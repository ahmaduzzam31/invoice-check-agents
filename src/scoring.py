"""Compare an extracted invoice with the answer key, field by field.

Used by the dev check now and by the final evaluation later, so both score extraction the same way.
"""

FIELDS = ["supplier_name", "supplier_vat_number", "invoice_number", "invoice_date", "po_number",
          "net_total", "vat_amount", "gross_total", "lines"]
MONEY_FIELDS = {"net_total", "vat_amount", "gross_total"}


def _text(v) -> str | None:
    return None if v is None or str(v).strip() == "" else " ".join(str(v).split()).casefold()


def _lines_equal(got: list[dict], want: list[dict]) -> bool:
    if len(got or []) != len(want):
        return False
    by_sku = {(_text(l.get("sku")) or ""): l for l in got}
    for w in want:
        g = by_sku.get(_text(w["sku"]))
        if g is None or abs(float(g["qty"]) - w["qty"]) > 1e-9 \
                or abs(float(g["unit_price"]) - w["unit_price"]) > 0.005 \
                or abs(float(g["line_total"]) - w["line_total"]) > 0.005:
            return False
    return True


def field_matches(extraction: dict | None, truth: dict) -> dict[str, bool]:
    """True/False per field. A failed extraction (None) scores False on every field."""
    if not extraction:
        return {f: False for f in FIELDS}
    out = {}
    for f in FIELDS:
        if f == "lines":
            out[f] = _lines_equal(extraction.get("lines"), truth["lines"])
        elif f in MONEY_FIELDS:
            try:
                out[f] = abs(float(extraction.get(f)) - truth[f]) <= 0.005
            except (TypeError, ValueError):
                out[f] = False
        else:
            out[f] = _text(extraction.get(f)) == _text(truth[f])
    return out


def summarise(per_invoice: list[dict[str, bool]]) -> dict:
    n = len(per_invoice)
    per_field = {f: round(sum(m[f] for m in per_invoice) / n, 4) for f in FIELDS} if n else {}
    all_fields = sum(all(m.values()) for m in per_invoice)
    return {"invoices": n, "per_field_accuracy": per_field,
            "mean_field_accuracy": round(sum(per_field.values()) / len(per_field), 4) if n else None,
            "invoices_fully_correct": all_fields}
