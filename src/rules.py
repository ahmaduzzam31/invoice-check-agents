"""The money checks. Plain Python, no AI: exact, repeatable and easy to audit.

The agents gather the evidence (what the invoice says, what the PO, deliveries, earlier invoices and
contract say). These rules then compare the numbers. A small language model is unreliable at
arithmetic, and money checks must be exact, so the model never makes these decisions.

Each check returns a status:
  pass        the invoice is fine on this point
  fail        a problem was found (the invoice is flagged)
  unverified  the evidence was missing, so the check could not be done (the invoice is flagged
              for a person, because "could not check" must never mean "approved")
"""

VAT_RATE = 0.23
TOLERANCE = 0.01  # one cent, for rounding


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def _check(check: str, status: str, problem: str | None, detail: str, money: float = 0.0) -> dict:
    return {"check": check, "status": status, "problem": problem if status == "fail" else None,
            "detail": detail, "money_at_risk_net": _money(money) if status == "fail" else 0.0}


def check_supplier(supplier: dict | None) -> dict:
    if supplier:
        return _check("supplier", "pass", None, f"Known supplier {supplier['supplier_id']} ({supplier['name']})")
    return _check("supplier", "unverified", None, "Supplier not found in the supplier list")


def check_po(inv: dict, supplier: dict | None, po: dict | None) -> dict:
    net = inv["net_total"]
    if not inv.get("po_number"):
        return _check("purchase_order", "fail", "missing_po", "The invoice has no purchase order number", net)
    if not po or not po.get("found"):
        return _check("purchase_order", "fail", "missing_po",
                      f"Purchase order {inv['po_number']} does not exist in the system", net)
    if supplier and po["supplier_id"] != supplier["supplier_id"]:
        return _check("purchase_order", "fail", "missing_po",
                      f"Purchase order {po['po_number']} belongs to another supplier ({po['supplier_id']})", net)
    return _check("purchase_order", "pass", None, f"Purchase order {po['po_number']} found for this supplier")


def check_prices(inv: dict, contract_prices: dict) -> dict:
    over, unknown, details = 0.0, [], []
    for line in inv["lines"]:
        agreed = contract_prices.get(line["sku"])
        if agreed is None:
            unknown.append(line["sku"])
        elif line["unit_price"] > agreed + TOLERANCE / 2:
            over += (line["unit_price"] - agreed) * line["qty"]
            details.append(f"{line['sku']}: billed {line['unit_price']:.2f}, contract price {agreed:.2f}")
    if details:
        return _check("contract_price", "fail", "price_above_contract", "; ".join(details), over)
    if unknown:
        return _check("contract_price", "unverified", None,
                      f"No contract price found for {', '.join(unknown)}")
    return _check("contract_price", "pass", None, "Every unit price is at or below the contract price")


def check_quantities(inv: dict, po: dict | None, deliveries: dict | None, contract_prices: dict) -> dict:
    if not po or not po.get("found") or deliveries is None:
        return _check("delivered_quantity", "unverified", None, "No valid purchase order, so deliveries cannot be checked")
    delivered = deliveries.get("delivered_by_sku", {})
    extra, details = 0.0, []
    for line in inv["lines"]:
        got = delivered.get(line["sku"], 0)
        if line["qty"] > got:
            price = contract_prices.get(line["sku"], line["unit_price"])
            extra += (line["qty"] - got) * min(price, line["unit_price"])
            details.append(f"{line['sku']}: {line['qty']:g} billed, {got:g} delivered")
    if details:
        return _check("delivered_quantity", "fail", "qty_above_delivery", "; ".join(details), extra)
    return _check("delivered_quantity", "pass", None, "No line bills more than was delivered")


def check_duplicates(inv: dict, duplicates: dict | None) -> dict:
    matches = (duplicates or {}).get("matches", [])
    if matches:
        m = matches[0]
        return _check("duplicate", "fail", "duplicate",
                      f"Matches earlier invoice {m['invoice_number']} paid on {m['invoice_date']} "
                      f"({', '.join(m['reasons'])})", inv["net_total"])
    return _check("duplicate", "pass", None, "No earlier invoice with the same number, or same PO and amount")


def check_totals(inv: dict) -> dict:
    lines_sum = _money(sum(l["line_total"] for l in inv["lines"]))
    expected_vat = _money(inv["net_total"] * VAT_RATE)
    problems = []
    for l in inv["lines"]:
        if abs(_money(l["qty"] * l["unit_price"]) - l["line_total"]) > TOLERANCE:
            problems.append(f"{l['sku']}: {l['qty']:g} x {l['unit_price']:.2f} is not {l['line_total']:.2f}")
    if abs(lines_sum - inv["net_total"]) > TOLERANCE:
        problems.append(f"lines add up to {lines_sum:.2f} but the net total says {inv['net_total']:.2f}")
    if abs(expected_vat - inv["vat_amount"]) > TOLERANCE:
        problems.append(f"VAT should be {expected_vat:.2f} (23%) but is {inv['vat_amount']:.2f}")
    if abs(inv["net_total"] + inv["vat_amount"] - inv["gross_total"]) > TOLERANCE:
        problems.append("net plus VAT does not equal the gross total")
    if problems:
        correct_gross = _money(lines_sum + _money(lines_sum * VAT_RATE))
        return _check("totals_and_vat", "fail", "total_mismatch", "; ".join(problems),
                      abs(inv["gross_total"] - correct_gross))
    return _check("totals_and_vat", "pass", None, "Lines, net total, VAT at 23% and gross total all agree")


def check_instructions(inv: dict) -> dict:
    text = (inv.get("instructions_found") or "").strip()
    if text:
        return _check("hidden_instructions", "fail", "prompt_injection",
                      f"The invoice contains instructions aimed at an automated system: \"{text[:160]}\"")
    return _check("hidden_instructions", "pass", None, "No instructions aimed at an automated system")


def run_checks(inv: dict, supplier: dict | None, po: dict | None, deliveries: dict | None,
               duplicates: dict | None, contract_prices: dict) -> dict:
    """Run every check and decide. Any fail or unverified check means the invoice is flagged."""
    checks = [check_supplier(supplier), check_po(inv, supplier, po), check_prices(inv, contract_prices),
              check_quantities(inv, po, deliveries, contract_prices), check_duplicates(inv, duplicates),
              check_totals(inv), check_instructions(inv)]
    problems = [c["problem"] for c in checks if c["status"] == "fail"]
    unverified = [c["check"] for c in checks if c["status"] == "unverified"]
    # If the whole invoice should not be paid (no valid PO, or already paid), the whole net is at risk;
    # otherwise the over-charges add up.
    whole = [c["money_at_risk_net"] for c in checks if c["problem"] in ("missing_po", "duplicate")]
    money = max(whole) if whole else sum(c["money_at_risk_net"] for c in checks)
    return {"checks": checks, "problems": problems, "unverified": unverified,
            "decision": "flag" if problems or unverified else "approve", "money_at_risk_net": _money(money)}
