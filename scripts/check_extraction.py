"""Development check: how well does the extractor read invoices? Runs on the DEV set (seed 7) only,
so the 60 test invoices stay unseen while the prompt is being tuned.

Run:  .venv/bin/python scripts/check_extraction.py --limit 20
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.extractor import extract_invoice, read_pdf_text  # noqa: E402
from src.scoring import field_matches, summarise  # noqa: E402

DEV = config.ROOT / "data" / "dev"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if platform.system() == "Darwin":
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

    invoices = json.loads((DEV / "answer_key.json").read_text())["invoices"][:args.limit]
    rows, matches = [], []
    for inv in invoices:
        r = extract_invoice(read_pdf_text(DEV / "invoices" / inv["file"]))
        m = field_matches(r["extraction"], inv)
        found = bool((r["extraction"] or {}).get("instructions_found"))
        matches.append(m)
        rows.append({"invoice_id": inv["invoice_id"], "layout": inv["layout"], "seconds": r["seconds"],
                     "retried": r.get("retried", False),
                     "error": r["error"], "wrong_fields": [f for f, ok in m.items() if not ok],
                     "injection_planted": bool(inv["injection_text"]), "injection_reported": found})
        print(f"{inv['invoice_id']} layout {inv['layout']} {r['seconds']} s wrong: {rows[-1]['wrong_fields']}"
              f"{' ERROR ' + r['error'] if r['error'] else ''}", flush=True)

    summary = summarise(matches)
    summary.update({
        "set": "dev (seed 7)", "model": f"{config.LLM_PROVIDER}:{config.LLM_MODEL}",
        "extraction_errors": sum(1 for r in rows if r["error"]),
        "self_check_retries": sum(1 for r in rows if r["retried"]),
        "mean_seconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
        "injections_planted": sum(r["injection_planted"] for r in rows),
        "injections_reported_correctly": sum(r["injection_planted"] and r["injection_reported"] for r in rows),
        "false_injection_reports": sum(r["injection_reported"] and not r["injection_planted"] for r in rows),
        "invoices_detail": rows})
    (config.RESULTS_DIR / "dev_extraction_check.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "invoices_detail"}, indent=2))


if __name__ == "__main__":
    main()
