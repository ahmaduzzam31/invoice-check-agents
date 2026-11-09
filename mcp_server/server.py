"""MCP server that gives the agents read-only access to the simulated ERP database.

The agents never see the database directly. They can only call these five tools, each marked
read-only. Any MCP-capable app could use the same server.

Run on its own (stdio):  .venv/bin/python -m mcp_server.server
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from src import sql_tools  # noqa: E402

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

mcp = FastMCP("erp-simulated",
              instructions="Read-only lookups in a SIMULATED ERP database: suppliers, purchase orders, "
                           "deliveries, earlier invoices and stock.")


@mcp.tool(annotations=READ_ONLY)
def find_supplier(name: str = "", vat_number: str = "") -> dict:
    """Find a supplier by VAT number (preferred) or by name. Returns supplier_id, contract_id and payment terms."""
    return sql_tools.find_supplier(name, vat_number)


@mcp.tool(annotations=READ_ONLY)
def get_purchase_order(po_number: str) -> dict:
    """Get a purchase order and its lines (sku, qty_ordered, agreed unit_price). Accepts PO numbers
    written in any format, for example 'PO-45139', 'PO45139' or '45139'."""
    return sql_tools.get_purchase_order(po_number)


@mcp.tool(annotations=READ_ONLY)
def get_deliveries(po_number: str) -> dict:
    """Get every delivery recorded against a purchase order and the total quantity delivered per sku."""
    return sql_tools.get_deliveries(po_number)


@mcp.tool(annotations=READ_ONLY)
def find_possible_duplicates(supplier_id: str, invoice_number: str, po_number: str = "",
                             gross_total: float | None = None) -> dict:
    """Find earlier invoices from the same supplier with the same invoice number (ignoring spaces and
    hyphens), or with the same purchase order and the same total."""
    return sql_tools.find_possible_duplicates(supplier_id, invoice_number, po_number, gross_total)


@mcp.tool(annotations=READ_ONLY)
def get_stock(sku: str) -> dict:
    """Get the current stock level for one item code (sku)."""
    return sql_tools.get_stock(sku)


if __name__ == "__main__":
    mcp.run()  # stdio transport
