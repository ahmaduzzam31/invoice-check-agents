"""Tests for the read-only SQL tools and the MCP server, checked against the simulated answer key.

Needs the simulated data: run scripts/generate_data.py first.
"""

import asyncio
import json
import sqlite3
import sys

import pytest

from src import config, sql_tools

if not config.DB_PATH.exists():
    pytest.skip("simulated database missing: run scripts/generate_data.py", allow_module_level=True)

INVOICES = json.loads(config.ANSWER_KEY.read_text())["invoices"]


def by_scenario(*names):
    return [i for i in INVOICES if i["scenario"] in names]


@pytest.mark.parametrize("raw", ["PO-45139", "PO45139", "PO 45139", "45139", " po-45139 "])
def test_po_formats_are_normalised(raw):
    assert sql_tools.normalise_po(raw) == "PO-45139"


def test_missing_po_number_gives_none():
    assert sql_tools.normalise_po(None) is None
    assert sql_tools.normalise_po("") is None
    assert sql_tools.get_purchase_order("")["found"] is False


def test_every_real_po_is_found_for_the_right_supplier():
    for inv in INVOICES:
        if inv["true_po_number"]:
            po = sql_tools.get_purchase_order(inv["po_number"])
            assert po["found"], inv["invoice_id"]
            assert po["supplier_id"] == inv["supplier_id"], inv["invoice_id"]


def test_missing_or_unknown_po_is_not_found():
    for inv in by_scenario("missing_po"):
        assert sql_tools.get_purchase_order(inv["po_number"] or "")["found"] is False, inv["invoice_id"]


def test_duplicates_are_found():
    for inv in by_scenario("duplicate"):
        res = sql_tools.find_possible_duplicates(inv["supplier_id"], inv["invoice_number"],
                                                 inv["po_number"], inv["gross_total"])
        assert res["matches"], inv["invoice_id"]


def test_clean_invoices_have_no_duplicates():
    """Includes the tricky cases: second delivery on the same PO and same amount from another supplier."""
    for inv in INVOICES:
        if not inv["problems"]:
            res = sql_tools.find_possible_duplicates(inv["supplier_id"], inv["invoice_number"],
                                                     inv["po_number"] or "", inv["gross_total"])
            assert res["matches"] == [], inv["invoice_id"]


def over_delivered(inv) -> bool:
    delivered = sql_tools.get_deliveries(inv["po_number"])["delivered_by_sku"]
    return any(line["qty"] > delivered.get(line["sku"], 0) for line in inv["lines"])


def test_quantity_above_delivery_is_visible():
    for inv in by_scenario("qty_above_delivery"):
        assert over_delivered(inv), inv["invoice_id"]


def test_clean_invoices_are_within_delivered_quantities():
    for inv in INVOICES:
        if not inv["problems"]:
            assert not over_delivered(inv), inv["invoice_id"]


def test_find_supplier_by_vat_and_name():
    inv = INVOICES[0]
    assert sql_tools.find_supplier(vat_number=inv["supplier_vat_number"])["suppliers"][0]["supplier_id"] == \
        inv["supplier_id"]
    assert sql_tools.find_supplier(name=inv["supplier_name"])["suppliers"][0]["supplier_id"] == inv["supplier_id"]
    assert sql_tools.find_supplier(name="No Such Supplier Ltd")["found"] is False


def test_stock_lookup():
    sku = INVOICES[0]["lines"][0]["sku"]
    assert sql_tools.get_stock(sku)["found"] is True
    assert sql_tools.get_stock("NOT-A-SKU")["found"] is False


def test_database_is_read_only():
    con = sql_tools._connect()
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM suppliers")
    con.close()


def test_mcp_server_offers_five_tools_and_answers():
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({"erp": {"command": sys.executable, "args": ["-m", "mcp_server.server"],
                                           "transport": "stdio", "cwd": str(config.ROOT)}})

    async def run():
        tools = await client.get_tools()
        by_name = {t.name: t for t in tools}
        po = next(i for i in INVOICES if i["scenario"] == "clean_po_format_variant")
        result = await by_name["get_purchase_order"].ainvoke({"po_number": po["po_number"]})
        return by_name, result, po

    by_name, result, po = asyncio.run(run())
    assert set(by_name) == {"find_supplier", "get_purchase_order", "get_deliveries",
                            "find_possible_duplicates", "get_stock"}
    # MCP returns a list of content blocks; the tool's dict is the JSON text inside the first one.
    payload = json.loads(result[0]["text"]) if isinstance(result, list) else json.loads(result)
    assert payload["found"] is True
    assert payload["po_number"] == po["true_po_number"]
    assert payload["supplier_id"] == po["supplier_id"]
