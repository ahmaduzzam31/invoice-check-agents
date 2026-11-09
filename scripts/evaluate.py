"""Evaluate the agent system and the single-call baseline against the answer key.

Stage 1: run each system on every invoice; each result is saved straight away, so a stopped run
         resumes where it left off. Flagged invoices pause for a person: in the evaluation the pause
         is recorded (the invoice counts as flagged) and nobody decides.
Stage 2: compute the metrics and save everything.

Full run (test set, 60 invoices):  .venv/bin/python scripts/evaluate.py
Trial (dev set):  DATA_SET=dev .venv/bin/python scripts/evaluate.py --ids INV001 INV004 INV026 --out data/dev/eval_trial
"""

import argparse
import asyncio
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from src import config  # noqa: E402
from src.baseline import check_invoice_single_call  # noqa: E402
from src.graph import build_graph, check_invoice  # noqa: E402
from src.scoring import field_matches, summarise  # noqa: E402

SYSTEMS = ["agent", "baseline"]
PROBLEM_TYPES = ["price_above_contract", "qty_above_delivery", "duplicate", "missing_po", "total_mismatch",
                 "prompt_injection"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_done(path: Path) -> dict:
    if not path.exists():
        return {}
    return {r["invoice_id"]: r for r in (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


async def run_agent(graph, inv: dict, stamp: str) -> dict:
    out = await check_invoice(graph, inv["invoice_id"], str(config.INVOICE_DIR / inv["file"]),
                              f"eval-{config.DATA_SET}-{inv['invoice_id']}-{stamp}")
    r = out.get("result", {})
    steps = out.get("steps", [])
    sup_steps = [s for s in steps if s["agent"] == "supervisor"]
    matcher = next((s for s in steps if s["agent"] == "matcher"), {})
    extractor = next((s for s in steps if s["agent"] == "extractor"), {})
    return {"decision": r.get("decision"), "problems": sorted(r.get("problems", [])),
            "unverified": r.get("unverified", []), "money_at_risk_net": r.get("money_at_risk_net"),
            "final_status": out.get("final_status"), "note": out.get("note"), "checks": r.get("checks", []),
            "extraction": out.get("extraction"), "extraction_error": out.get("extraction_error"),
            "extraction_retried": extractor.get("retried", False),
            "field_matches": field_matches(out.get("extraction"), inv),
            "supervisor_steps": len(sup_steps), "supervisor_overrides": sum(s.get("overridden", False) for s in sup_steps),
            "matcher_tool_calls": out.get("matcher_tool_calls"), "matcher_fallbacks": out.get("matcher_fallbacks", []),
            "matcher_calls": [{"tool": c["tool"], "args": c["args"]} for c in matcher.get("tool_calls", [])],
            "contract_missing": (out.get("contract") or {}).get("missing", []),
            "steps": [{k: s[k] for k in ("agent", "detail", "seconds")} for s in steps],
            "seconds": out["seconds"], "error": None}


async def run_system(system: str, invoices: list[dict], out_dir: Path) -> None:
    path = out_dir / f"runs_{system}.jsonl"
    done = load_done(path)
    todo = [i for i in invoices if i["invoice_id"] not in done]
    print(f"[{system}] {len(done)} already done, {len(todo)} to run", flush=True)
    if not todo:
        return
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    config.CHECKPOINT_DB.parent.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(config.ROOT / "data" / "eval_checkpoints.db")) as cp:
        graph = build_graph(cp)
        with path.open("a", encoding="utf-8") as f:
            for n, inv in enumerate(todo, start=1):
                t0 = time.perf_counter()
                try:
                    if system == "agent":
                        res = await run_agent(graph, inv, stamp)
                    else:
                        res = await asyncio.to_thread(check_invoice_single_call, config.INVOICE_DIR / inv["file"])
                except Exception as e:  # record the failure and carry on
                    res = {"decision": None, "problems": [], "money_at_risk_net": None, "note": None,
                           "error": f"{type(e).__name__}: {e}"[:300], "seconds": round(time.perf_counter() - t0, 2)}
                rec = {"invoice_id": inv["invoice_id"], "scenario": inv["scenario"], "true_problems": sorted(inv["problems"]),
                       "true_decision": inv["expected_decision"], "true_money_at_risk_net": inv["money_at_risk_net"],
                       "system": system, "run_utc": now(), **res}
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                f.flush()
                status = "ERROR" if rec["error"] else f"{rec['decision']} {rec['problems']}"
                print(f"  [{system}] {n}/{len(todo)} {inv['invoice_id']} {inv['scenario']}: {status} "
                      f"({rec['seconds']} s)", flush=True)


def metrics(recs: list[dict]) -> dict:
    n = len(recs)
    flagged = [r["decision"] == "flag" for r in recs]
    truly_bad = [bool(r["true_problems"]) for r in recs]
    tp = sum(f and b for f, b in zip(flagged, truly_bad))
    fp = sum(f and not b for f, b in zip(flagged, truly_bad))
    fn = sum((not f) and b for f, b in zip(flagged, truly_bad))
    tn = sum((not f) and (not b) for f, b in zip(flagged, truly_bad))
    bad, clean = sum(truly_bad), n - sum(truly_bad)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / bad if bad else None
    per_type = {}
    for p in PROBLEM_TYPES:
        planted = [r for r in recs if p in r["true_problems"]]
        per_type[p] = {"planted": len(planted), "detected": sum(p in r["problems"] for r in planted),
                       "reported_where_not_planted": sum(p in r["problems"] for r in recs if p not in r["true_problems"])}
    money_planted = round(sum(r["true_money_at_risk_net"] for r in recs), 2)
    money_caught = round(sum(r["true_money_at_risk_net"] for r, f in zip(recs, flagged) if f and r["true_problems"]), 2)
    money_exact = sum(1 for r, f in zip(recs, flagged) if f and r["true_problems"] and r["money_at_risk_net"] is not None
                      and abs(r["money_at_risk_net"] - r["true_money_at_risk_net"]) <= 0.01)
    auto_approved = [r for r in recs if r["decision"] == "approve"]
    secs = [r["seconds"] for r in recs if r.get("seconds") is not None]
    return {
        "invoices": n, "errors": sum(1 for r in recs if r.get("error")),
        "invoices_with_problems": bad, "clean_invoices": clean,
        "problem_invoices_flagged": tp, "problem_invoices_missed": fn,
        "clean_invoices_flagged_false_alarms": fp, "clean_invoices_approved": tn,
        "recall": round(recall, 4) if recall is not None else None,
        "false_alarm_rate": round(fp / clean, 4) if clean else None,
        "precision": round(precision, 4) if precision is not None else None,
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision and recall else None,
        "decision_accuracy": round((tp + tn) / n, 4),
        "exact_problem_match": sum(r["problems"] == r["true_problems"] for r in recs),
        "per_problem_type": per_type,
        "money_at_risk_planted_net": money_planted, "money_at_risk_caught_net": money_caught,
        "money_caught_share": round(money_caught / money_planted, 4) if money_planted else None,
        "flagged_problem_invoices_with_exact_money": money_exact,
        "auto_approved": len(auto_approved),
        "auto_approved_correctly": sum(1 for r in auto_approved if not r["true_problems"]),
        "straight_through_rate": round(len(auto_approved) / n, 4),
        "seconds_mean": round(statistics.mean(secs), 2) if secs else None,
        "seconds_median": round(statistics.median(secs), 2) if secs else None,
        "seconds_max": round(max(secs), 2) if secs else None,
        "seconds_total": round(sum(secs), 1) if secs else None,
    }


def agent_extras(recs: list[dict]) -> dict:
    sup_steps = sum(r.get("supervisor_steps", 0) for r in recs)
    overrides = sum(r.get("supervisor_overrides", 0) for r in recs)
    return {
        "extraction": summarise([r["field_matches"] for r in recs if "field_matches" in r]),
        "extraction_failures": sum(1 for r in recs if r.get("extraction") is None),
        "extraction_self_check_retries": sum(1 for r in recs if r.get("extraction_retried")),
        "supervisor_routing_steps": sup_steps, "supervisor_choices_corrected_by_code": overrides,
        "supervisor_choice_agreement": round(1 - overrides / sup_steps, 4) if sup_steps else None,
        "matcher_invoices": sum(1 for r in recs if r.get("matcher_tool_calls") is not None),
        "matcher_invoices_without_fallback": sum(1 for r in recs if r.get("matcher_tool_calls") is not None
                                                 and not r.get("matcher_fallbacks")),
        "matcher_fallback_calls_total": sum(len(r.get("matcher_fallbacks") or []) for r in recs),
        "matcher_fallbacks_by_tool": {t: sum(t in (r.get("matcher_fallbacks") or []) for r in recs)
                                      for t in ("find_supplier", "get_purchase_order", "get_deliveries",
                                                "find_possible_duplicates")},
        "invoices_with_unverified_contract_price": sum(1 for r in recs if r.get("contract_missing")),
        "invoices_with_any_unverified_check": sum(1 for r in recs if r.get("unverified")),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--systems", nargs="*", default=SYSTEMS, choices=SYSTEMS)
    p.add_argument("--ids", nargs="*")
    p.add_argument("--out", default=str(config.RESULTS_DIR))
    a = p.parse_args()
    out_dir = Path(a.out)
    if config.DATA_SET != "test" and out_dir.resolve() == config.RESULTS_DIR.resolve():
        sys.exit("Dev runs must not write to results/. Use --out data/dev/eval_trial")
    out_dir.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Darwin":
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

    invoices = json.loads(config.ANSWER_KEY.read_text())["invoices"]
    if a.ids:
        invoices = [i for i in invoices if i["invoice_id"] in a.ids]
    started, t_all = now(), time.perf_counter()
    print(f"{len(invoices)} invoices ({config.DATA_SET} set), systems: {', '.join(a.systems)}, output: {out_dir}")
    for system in a.systems:
        asyncio.run(run_system(system, invoices, out_dir))

    ids = {i["invoice_id"] for i in invoices}
    summary = {"run_started_utc": started, "run_finished_utc": now(),
               "this_session_seconds": round(time.perf_counter() - t_all, 1), "data_set": config.DATA_SET,
               "settings": {"model": f"{config.LLM_PROVIDER}:{config.LLM_MODEL}", "temperature": config.LLM_TEMPERATURE,
                            "embedding_model": config.EMBED_MODEL, "contract_store": f"pinecone:{config.PINECONE_INDEX}"},
               "systems": {}}
    rows = []
    for system in SYSTEMS:
        recs = [r for r in load_done(out_dir / f"runs_{system}.jsonl").values() if r["invoice_id"] in ids]
        if not recs:
            continue
        recs.sort(key=lambda r: r["invoice_id"])
        summary["systems"][system] = metrics(recs)
        if system == "agent":
            summary["systems"][system]["agent_details"] = agent_extras(recs)
        for r in recs:
            rows.append({"system": system, "invoice_id": r["invoice_id"], "scenario": r["scenario"],
                         "true_problems": ";".join(r["true_problems"]), "predicted_problems": ";".join(r["problems"]),
                         "decision": r["decision"], "correct_decision": r["decision"] == r["true_decision"],
                         "exact_problems": r["problems"] == r["true_problems"],
                         "true_money_at_risk_net": r["true_money_at_risk_net"],
                         "predicted_money_at_risk_net": r["money_at_risk_net"], "seconds": r["seconds"],
                         "error": r.get("error") or ""})
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (out_dir / "per_invoice.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
