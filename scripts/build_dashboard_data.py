"""Turn the saved results into the files the write-ups use, so no number is typed by hand.

Reads only results/ and the answer key. Writes:
  docs/data.json             data for the GitHub Pages dashboard (docs/index.html)
  results/results_table.md   the results table copied into the README

Run after evaluate.py, trulens_eval.py and business_analysis.py:
    .venv/bin/python scripts/build_dashboard_data.py
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

R = config.RESULTS_DIR


def load(name: str):
    return json.loads((R / name).read_text())


def jsonl(name: str) -> dict:
    return {r["invoice_id"]: r for r in (json.loads(l) for l in (R / name).read_text().splitlines() if l.strip())}


def fmt_eur(x: float) -> str:
    return f"EUR {x:,.2f}"


def results_table(s: dict) -> str:
    a, b = s["systems"]["agent"], s["systems"]["baseline"]
    rows = [
        ("Problem invoices flagged", f"{a['problem_invoices_flagged']} of {a['invoices_with_problems']}",
         f"{b['problem_invoices_flagged']} of {b['invoices_with_problems']}"),
        ("Clean invoices flagged (false alarms)", f"{a['clean_invoices_flagged_false_alarms']} of {a['clean_invoices']}",
         f"{b['clean_invoices_flagged_false_alarms']} of {b['clean_invoices']}"),
        ("Recall", a["recall"], b["recall"]),
        ("Precision", a["precision"], b["precision"]),
        ("F1", a["f1"], b["f1"]),
        ("Decision accuracy", a["decision_accuracy"], b["decision_accuracy"]),
        ("Invoices with exactly the right problem list", f"{a['exact_problem_match']} of {a['invoices']}",
         f"{b['exact_problem_match']} of {b['invoices']}"),
        ("Money at risk caught (net)", f"{fmt_eur(a['money_at_risk_caught_net'])} of {fmt_eur(a['money_at_risk_planted_net'])}",
         f"{fmt_eur(b['money_at_risk_caught_net'])} of {fmt_eur(b['money_at_risk_planted_net'])}"),
        ("Problem invoices with the exact money at risk", f"{a['flagged_problem_invoices_with_exact_money']} of {a['invoices_with_problems']}",
         f"{b['flagged_problem_invoices_with_exact_money']} of {b['invoices_with_problems']}"),
        ("Approved with no person needed", f"{a['auto_approved']} ({a['auto_approved_correctly']} correctly)",
         f"{b['auto_approved']} ({b['auto_approved_correctly']} correctly)"),
        ("Mean seconds per invoice", a["seconds_mean"], b["seconds_mean"]),
        ("Median seconds per invoice", a["seconds_median"], b["seconds_median"]),
    ]
    out = ["| Measure (60 test invoices: 20 with problems, 40 clean) | Agent system | Single-call baseline |",
           "|---|---|---|"]
    out += [f"| {m} | {x} | {y} |" for m, x, y in rows]
    return "\n".join(out) + "\n"


def main() -> None:
    summary = load("summary.json")
    trulens = load("trulens_summary.json")
    business = load("business_analysis.json")
    with (R / "trulens_notes.csv").open() as f:
        notes = {r["invoice_id"]: r for r in csv.DictReader(f)}
    agent, base = jsonl("runs_agent.jsonl"), jsonl("runs_baseline.jsonl")
    key = {i["invoice_id"]: i for i in json.loads(config.ANSWER_KEY.read_text())["invoices"]}

    invoices = []
    for iid in sorted(key):
        k, a, b = key[iid], agent[iid], base[iid]
        n = notes.get(iid)
        invoices.append({
            "invoice_id": iid, "supplier": k["supplier_name"], "scenario": k["scenario"], "answer_key_note": k["note"],
            "true_problems": k["problems"], "true_money": k["money_at_risk_net"], "gross_total": k["gross_total"],
            "agent": {"decision": a["decision"], "problems": a["problems"], "money": a["money_at_risk_net"],
                      "note": a["note"], "seconds": a["seconds"], "unverified": a.get("unverified", []),
                      "steps": a["steps"],
                      "checks": [{k2: c[k2] for k2 in ("check", "status", "detail")} for c in a["checks"]],
                      "groundedness": float(n["groundedness"]) if n and n["groundedness"] else None,
                      "coverage": float(n["coverage"]) if n else None},
            "baseline": {"decision": b["decision"], "problems": b["problems"], "money": b["money_at_risk_net"],
                         "note": b["note"], "seconds": b["seconds"]},
        })

    data = {"summary": summary, "trulens": trulens, "business": business["queries"],
            "data_summary": load("data_summary.json"), "contract_search": load("contract_search_check.json"),
            "dev_extraction": {k: v for k, v in load("dev_extraction_check.json").items() if k != "invoices_detail"},
            "invoices": invoices}
    for k in ("with_supplier_filter", "without_supplier_filter"):
        data["contract_search"][k] = {x: y for x, y in data["contract_search"][k].items() if x != "misses"}
    docs = config.ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    (R / "results_table.md").write_text(results_table(summary))
    print(f"Wrote docs/data.json ({len(invoices)} invoices) and results/results_table.md")
    print(results_table(summary))


if __name__ == "__main__":
    main()
