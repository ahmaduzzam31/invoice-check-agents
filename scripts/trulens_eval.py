"""Trace and score the reviewer's exception notes with TruLens.

For every invoice the agent system flagged, the note written by the model is logged to TruLens as a
record (a "virtual app", because the runs already happened in scripts/evaluate.py), together with the
evidence it was given: the failed and unverified checks from the rules. TruLens then scores:

  groundedness  (TruLens feedback, local model as judge) is every sentence of the note supported by
                the evidence? Catches invented numbers or reasons. 0 to 1.
Plus one plain check, no model:
  coverage      does the note mention every problem the rules found? 0 to 1.

Run after scripts/evaluate.py:  .venv/bin/python scripts/trulens_eval.py [--runs results/runs_agent.jsonl]
"""

import argparse
import csv
import json
import os
import platform
import re
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

os.environ.setdefault("TRULENS_OTEL_TRACING", "0")  # virtual records use the classic TruLens record store
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

# Words a note must contain to count as mentioning each problem (plain coverage check).
MENTIONS = {
    "price_above_contract": r"price|contract",
    "qty_above_delivery": r"deliver",
    "duplicate": r"duplicate|already (been )?paid|earlier invoice",
    "missing_po": r"purchase order|\bPO\b",
    "total_mismatch": r"\bVAT\b|total|add up",
    "prompt_injection": r"instruction|automated|\bAI\b|hidden",
}


def evidence_text(rec: dict) -> str:
    lines = [f"Invoice {rec['invoice_id']}. Money at risk: EUR {rec['money_at_risk_net']:.2f} excluding VAT."]
    ex = rec.get("extraction") or {}
    if ex:
        lines.append(f"Supplier {ex.get('supplier_name')}, invoice number {ex.get('invoice_number')}.")
    for c in rec.get("checks", []):
        if c["status"] != "pass":
            lines.append(f"{c['check']} ({c['status']}): {c['detail']}")
    return "\n".join(lines)


def coverage(note: str, problems: list[str]) -> tuple[float, list[str]]:
    if not problems:
        return 1.0, []
    missing = [p for p in problems if not re.search(MENTIONS[p], note or "", re.IGNORECASE)]
    return round(1 - len(missing) / len(problems), 4), missing


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", default=str(config.RESULTS_DIR / "runs_agent.jsonl"))
    a = p.parse_args()
    runs = Path(a.runs)
    out_dir = runs.parent
    if platform.system() == "Darwin":
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

    from trulens.apps.virtual import TruVirtual, VirtualApp, VirtualRecord
    from trulens.core import Feedback, TruSession
    from trulens.core.schema.feedback import FeedbackMode
    from trulens.core.schema.select import Select
    from trulens.providers.litellm import LiteLLM

    recs = [json.loads(l) for l in runs.read_text().splitlines() if l.strip()]
    flagged = [r for r in recs if r.get("decision") == "flag" and r.get("note")]
    print(f"{len(flagged)} flagged invoices with notes to score", flush=True)

    TruSession(database_url=f"sqlite:///{out_dir / 'trulens.sqlite'}")
    # The groundedness function itself runs at temperature 0 (its default), so only the server is set here.
    judge = LiteLLM(model_engine=f"ollama/{config.LLM_MODEL}", completion_kwargs={"api_base": config.OLLAMA_BASE_URL})
    groundedness = (Feedback(judge.groundedness_measure_with_cot_reasons, name="Groundedness")
                    .on(Select.RecordInput).on(Select.RecordOutput))
    app = TruVirtual(app_name="invoice-check-agents", app_version="reviewer-notes",
                     app=VirtualApp({"llm": {"model": config.LLM_MODEL}, "role": "reviewer exception note"}),
                     feedbacks=[groundedness])

    rows = []
    for n, r in enumerate(flagged, start=1):
        t0 = time.perf_counter()
        record = VirtualRecord(main_input=evidence_text(r), main_output=r["note"], calls={},
                               meta={"invoice_id": r["invoice_id"]})
        record = app.add_record(record, feedback_mode=FeedbackMode.WITH_APP)
        score, error = None, ""
        try:
            for res in record.wait_for_feedback_results(feedback_timeout=600).values():
                score = res.result
                error = res.error or ""
        except Exception as e:  # a failed judgement is reported as failed, never guessed
            error = f"{type(e).__name__}: {e}"[:200]
        cov, missing = coverage(r["note"], r["problems"])
        rows.append({"invoice_id": r["invoice_id"], "problems": ";".join(r["problems"]),
                     "groundedness": round(float(score), 4) if score is not None else None,
                     "coverage": cov, "missing_mentions": ";".join(missing), "note": r["note"],
                     "error": error, "seconds": round(time.perf_counter() - t0, 2)})
        print(f"  {n}/{len(flagged)} {r['invoice_id']} groundedness {rows[-1]['groundedness']} coverage {cov}"
              f"{' ERROR ' + error if error else ''}", flush=True)

    scored = [x["groundedness"] for x in rows if x["groundedness"] is not None]
    summary = {"notes_scored": len(rows), "judge": f"ollama:{config.LLM_MODEL} via TruLens LiteLLM provider",
               "trulens_version": version("trulens-core"),
               "groundedness_mean": round(sum(scored) / len(scored), 4) if scored else None,
               "groundedness_failed": len(rows) - len(scored),
               "notes_fully_grounded": sum(1 for s in scored if s >= 0.999),
               "coverage_mean": round(sum(x["coverage"] for x in rows) / len(rows), 4) if rows else None,
               "notes_mentioning_every_problem": sum(1 for x in rows if x["coverage"] == 1.0)}
    (out_dir / "trulens_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (out_dir / "trulens_notes.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["invoice_id"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
