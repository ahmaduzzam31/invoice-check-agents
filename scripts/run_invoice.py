"""Check one invoice from the command line and print every agent step.

    .venv/bin/python scripts/run_invoice.py INV013                       (test set)
    DATA_SET=dev .venv/bin/python scripts/run_invoice.py INV013          (dev set)
    .venv/bin/python scripts/run_invoice.py --thread <id> --decide reject --by "Ahmad" --comment "..."
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from src import config  # noqa: E402
from src.graph import build_graph, check_invoice, decide  # noqa: E402


def show(out: dict) -> None:
    for i, s in enumerate(out.get("steps", []), start=1):
        print(f"{i:>2}. [{s['agent']}] {s['seconds']} s")
        for part in s["detail"].split(" | "):
            print(f"      {part}")
    r = out.get("result", {})
    print(f"\nDecision: {r.get('decision', '?').upper()}   Status: {out.get('final_status')}   "
          f"Problems: {r.get('problems')}   Money at risk: EUR {r.get('money_at_risk_net')}")
    print(f"Note: {out.get('note')}")
    if out.get("paused"):
        print(f"\nPAUSED for a person. Resume with: --thread {out['thread_id']} --decide approve|reject --by NAME")
    if "seconds" in out:
        print(f"Total time: {out['seconds']} s")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("invoice_id", nargs="?")
    p.add_argument("--thread")
    p.add_argument("--decide", choices=["approve", "reject"])
    p.add_argument("--by", default="")
    p.add_argument("--comment", default="")
    a = p.parse_args()
    config.CHECKPOINT_DB.parent.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as cp:
        graph = build_graph(cp)
        if a.decide:
            show(await decide(graph, a.thread, a.decide, a.by, a.comment))
            return
        inv = next(i for i in json.loads(config.ANSWER_KEY.read_text())["invoices"] if i["invoice_id"] == a.invoice_id)
        thread = f"{config.DATA_SET}-{a.invoice_id}-{uuid.uuid4().hex[:6]}"
        out = await check_invoice(graph, a.invoice_id, config.INVOICE_DIR / inv["file"], thread)
        show(out)
        print(f"\nAnswer key ({config.DATA_SET} set): {inv['scenario']}, problems {inv['problems']}, "
              f"expected {inv['expected_decision']}, money at risk EUR {inv['money_at_risk_net']}")


if __name__ == "__main__":
    asyncio.run(main())
