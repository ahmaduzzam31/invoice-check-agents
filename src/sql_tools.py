"""Read-only lookups in the simulated ERP database.

These are the only way the agents can see the database: they are served by the MCP server
(mcp_server/server.py). Every query is parameterised (no SQL text comes from the model) and
the database is opened read-only, so nothing can be changed through these tools.
"""

import re
import sqlite3
from contextlib import closing

from src import config


def _connect() -> sqlite3.Connection:
    if not config.DB_PATH.exists():
        raise FileNotFoundError(f"{config.DB_PATH} not found. Run scripts/generate_data.py first.")
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows(sql: str, *params) -> list[dict]:
    with closing(_connect()) as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def normalise_po(po_number: str | None) -> str | None:
    """'PO45139', 'PO 45139', '45139' and 'PO-45139' all become 'PO-45139'. None if no digits."""
    digits = re.sub(r"[^0-9]", "", po_number or "")
    return f"PO-{digits}" if digits else None


def normalise_invoice_number(invoice_number: str) -> str:
    """'LHA-14682', 'LHA14682' and 'lha 14682' all become 'LHA14682'."""
    return re.sub(r"[^A-Z0-9]", "", (invoice_number or "").upper())


def find_supplier(name: str = "", vat_number: str = "") -> dict:
    """Find a supplier by VAT number (preferred) or by name. Returns its id, contract and payment terms."""
    if vat_number:
        rows = _rows("SELECT * FROM suppliers WHERE UPPER(vat_number) = UPPER(?)", vat_number.strip())
        if rows:
            return {"found": True, "matched_on": "vat_number", "suppliers": rows}
    if name:
        rows = _rows("SELECT * FROM suppliers WHERE LOWER(name) = LOWER(?)", name.strip())
        if not rows:
            rows = _rows("SELECT * FROM suppliers WHERE LOWER(name) LIKE LOWER(?)", f"%{name.strip()}%")
        if rows:
            return {"found": True, "matched_on": "name", "suppliers": rows}
    return {"found": False, "suppliers": []}


def get_purchase_order(po_number: str) -> dict:
    """Get a purchase order header and its lines (sku, qty_ordered, agreed unit_price)."""
    po = normalise_po(po_number)
    if not po:
        return {"found": False, "po_number": None, "reason": "no purchase order number given"}
    head = _rows("SELECT p.po_number, p.supplier_id, s.name AS supplier_name, p.order_date, p.status "
                 "FROM purchase_orders p JOIN suppliers s USING (supplier_id) WHERE p.po_number = ?", po)
    if not head:
        return {"found": False, "po_number": po, "reason": "purchase order does not exist"}
    lines = _rows("SELECT line_no, sku, description, unit, qty_ordered, unit_price FROM po_lines "
                  "WHERE po_number = ? ORDER BY line_no", po)
    return {"found": True, **head[0], "lines": lines}


def get_deliveries(po_number: str) -> dict:
    """Get every delivery recorded against a purchase order, and the total delivered per sku."""
    po = normalise_po(po_number)
    rows = _rows("SELECT delivery_id, sku, qty_delivered, delivery_date FROM deliveries "
                 "WHERE po_number = ? ORDER BY delivery_date, delivery_id", po) if po else []
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["sku"]] = totals.get(r["sku"], 0) + r["qty_delivered"]
    return {"po_number": po, "deliveries": rows, "delivered_by_sku": totals}


def find_possible_duplicates(supplier_id: str, invoice_number: str, po_number: str = "",
                             gross_total: float | None = None) -> dict:
    """Find earlier invoices from the same supplier with the same invoice number (ignoring spaces and
    hyphens), or with the same purchase order and the same total amount."""
    target_no = normalise_invoice_number(invoice_number)
    po = normalise_po(po_number)
    matches = []
    for r in _rows("SELECT * FROM invoices_received WHERE supplier_id = ?", supplier_id):
        reasons = []
        if target_no and normalise_invoice_number(r["invoice_number"]) == target_no:
            reasons.append("same invoice number")
        if po and r["po_number"] == po and gross_total is not None and abs(r["gross_total"] - gross_total) < 0.01:
            reasons.append("same purchase order and same total")
        if reasons:
            matches.append({**r, "reasons": reasons})
    return {"supplier_id": supplier_id, "checked_invoice_number": invoice_number, "matches": matches}


def get_stock(sku: str) -> dict:
    """Get the stock level for one item code (sku)."""
    rows = _rows("SELECT * FROM stock WHERE UPPER(sku) = UPPER(?)", (sku or "").strip())
    return {"found": bool(rows), **(rows[0] if rows else {"sku": sku})}
