"""Tests for the money-check rules.

With PERFECT inputs (the answer key's printed fields and the real database), the rules alone must
find exactly the planted problems on all 60 invoices, flag nothing else, and compute the same money
at risk as the answer key. Any error in the full system then comes from the AI steps, not the rules.
"""

import json
import sqlite3

import pytest

from src import config, rules, sql_tools

if not config.DB_PATH.exists():
    pytest.skip("simulated database missing: run scripts/generate_data.py", allow_module_level=True)

INVOICES = json.loads(config.ANSWER_KEY.read_text())["invoices"]
with sqlite3.connect(config.DB_PATH) as _con:
    CONTRACT_PRICES = dict(_con.execute("SELECT DISTINCT sku, unit_price FROM po_lines").fetchall())


def perfect_run(inv: dict) -> dict:
    extraction = {**inv, "instructions_found": inv["injection_text"]}
    supplier = sql_tools.find_supplier(vat_number=inv["supplier_vat_number"])["suppliers"][0]
    po = sql_tools.get_purchase_order(inv["po_number"] or "")
    deliveries = sql_tools.get_deliveries(inv["po_number"]) if po["found"] else None
    dups = sql_tools.find_possible_duplicates(supplier["supplier_id"], inv["invoice_number"],
                                              inv["po_number"] or "", inv["gross_total"])
    prices = {l["sku"]: CONTRACT_PRICES[l["sku"]] for l in inv["lines"]}
    return rules.run_checks(extraction, supplier, po, deliveries, dups, prices)


@pytest.mark.parametrize("inv", INVOICES, ids=[i["invoice_id"] for i in INVOICES])
def test_rules_find_exactly_the_planted_problems(inv):
    out = perfect_run(inv)
    assert sorted(out["problems"]) == sorted(inv["problems"]), out["checks"]
    assert out["decision"] == inv["expected_decision"]
    assert out["money_at_risk_net"] == pytest.approx(inv["money_at_risk_net"], abs=0.01)


LINE = {"sku": "X-1", "description": "Test item", "qty": 10, "unit_price": 5.0, "line_total": 50.0}
CLEAN = {"po_number": "PO-1", "lines": [LINE], "net_total": 50.0, "vat_amount": 11.5, "gross_total": 61.5}


def test_price_below_contract_passes():
    assert rules.check_prices(CLEAN, {"X-1": 5.50})["status"] == "pass"


def test_price_one_cent_above_contract_fails():
    out = rules.check_prices({**CLEAN, "lines": [{**LINE, "unit_price": 5.01, "line_total": 50.1}]}, {"X-1": 5.0})
    assert out["status"] == "fail" and out["money_at_risk_net"] == pytest.approx(0.10)


def test_missing_contract_price_is_unverified_and_flags():
    assert rules.check_prices(CLEAN, {})["status"] == "unverified"
    supplier = {"supplier_id": "SUP01", "name": "Test"}
    po = {"found": True, "po_number": "PO-1", "supplier_id": "SUP01"}
    out = rules.run_checks(CLEAN, supplier, po, {"delivered_by_sku": {"X-1": 10}}, {"matches": []}, {})
    assert out["decision"] == "flag" and out["problems"] == [] and "contract_price" in out["unverified"]


def test_wrong_vat_rate_fails_totals():
    out = rules.check_totals({**CLEAN, "vat_amount": 12.5, "gross_total": 62.5})
    assert out["status"] == "fail" and out["money_at_risk_net"] == pytest.approx(1.0)


def test_po_of_another_supplier_fails():
    out = rules.check_po(CLEAN, {"supplier_id": "SUP01"}, {"found": True, "po_number": "PO-1", "supplier_id": "SUP02"})
    assert out["status"] == "fail" and out["problem"] == "missing_po"
