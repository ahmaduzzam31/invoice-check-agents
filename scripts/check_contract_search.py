"""Measure whether the contract search finds the right section.

For every item in every contract (60 items), ask for its agreed price; for every supplier (12),
ask for the payment terms. Each question runs twice: limited to the supplier's own contract
(how the contract agent uses it) and across all contracts (to show why the limit matters).
Run:  .venv/bin/python scripts/check_contract_search.py
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.contracts import search_contract  # noqa: E402


def main() -> None:
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    suppliers = con.execute("SELECT supplier_id, payment_terms_days FROM suppliers").fetchall()
    items = json.loads(config.ANSWER_KEY.read_text())["invoices"]
    sku_desc = {l["sku"]: l["description"] for i in items for l in i["lines"]}
    prices = con.execute("SELECT DISTINCT p.sku, p.unit_price, o.supplier_id FROM po_lines p "
                         "JOIN purchase_orders o USING (po_number)").fetchall()

    def score(filtered: bool) -> dict:
        price_hits = price_total = terms_hits = 0
        misses = []
        for sku, price, sid in prices:
            q = f"Agreed unit price for {sku} {sku_desc.get(sku, '')}"
            top = search_contract(sid if filtered else None, q, k=1)[0]
            ok = top["supplier_id"] == sid and f"{sku} |" in top["text"] and f"| {price:.2f}" in top["text"]
            price_total += 1
            price_hits += ok
            if not ok:
                misses.append({"question": q, "expected_supplier": sid, "got": f"{top['supplier_id']} / {top['section']}"})
        for sid, days in suppliers:
            top = search_contract(sid if filtered else None, "What are the payment terms?", k=1)[0]
            ok = top["supplier_id"] == sid and f"within {days} days" in top["text"]
            terms_hits += ok
            if not ok:
                misses.append({"question": "payment terms", "expected_supplier": sid,
                               "got": f"{top['supplier_id']} / {top['section']}"})
        return {"price_questions": price_total, "price_top1_correct": price_hits,
                "terms_questions": len(suppliers), "terms_top1_correct": terms_hits, "misses": misses}

    result = {"note": "Top-1 result counts as correct only if it is from the right supplier AND contains the "
                      "exact item code and agreed price (or the right payment days).",
              "with_supplier_filter": score(True), "without_supplier_filter": score(False)}
    (config.RESULTS_DIR / "contract_search_check.json").write_text(json.dumps(result, indent=2) + "\n")
    for k in ("with_supplier_filter", "without_supplier_filter"):
        r = result[k]
        print(f"{k}: prices {r['price_top1_correct']}/{r['price_questions']}, "
              f"payment terms {r['terms_top1_correct']}/{r['terms_questions']}, misses {len(r['misses'])}")


if __name__ == "__main__":
    main()
