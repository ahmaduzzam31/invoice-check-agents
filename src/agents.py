"""The supervisor and the four specialist agents.

Every agent reads the shared state, does one job, and returns its results plus a step log entry
(shown in the app and used in the evaluation). Plain code checks each agent's work:
  supervisor      the model chooses the next agent; code corrects a choice that skips needed work
  extractor       self-check with one retry (src/extractor.py)
  matcher         the model calls the MCP tools; code checks every needed lookup was made with the
                  invoice's own values and makes any missing call itself (counted as a fallback)
  contract_agent  every price the model copies must appear in the contract text, or it is dropped
  reviewer        plain rules decide (src/rules.py); the model only writes the note
"""

import asyncio
import json
import math
import re
import time
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from src import rules
from src.contracts import search_contract
from src.extractor import extract_invoice, read_pdf_text
from src.mcp_client import call, get_tools, parse
from src.models import get_llm
from src.prompts import CONTRACT_SYSTEM, MATCHER_SYSTEM, REVIEWER_SYSTEM, SUPERVISOR_SYSTEM
from src.sql_tools import normalise_invoice_number, normalise_po

MAX_HOPS = 10           # the supervisor can never loop forever
MAX_MATCHER_ROUNDS = 6  # tool-calling rounds for the matcher


def _step(agent: str, detail: str, t0: float, **extra) -> dict:
    return {"agent": agent, "detail": detail, "seconds": round(time.perf_counter() - t0, 2), **extra}


# ---------------------------------------------------------------- supervisor

class Route(BaseModel):
    next: Literal["extractor", "matcher", "contract_agent", "reviewer", "FINISH"]
    reason: str = Field(description="One short sentence")


def needed_next(state: dict) -> str:
    """The agent whose input is ready and whose work is not done yet."""
    done = set(state.get("done", []))
    if "extractor" not in done:
        return "extractor"
    if state.get("extraction") is None:  # unreadable invoice: go straight to the reviewer
        return "FINISH" if "reviewer" in done else "reviewer"
    for agent in ("matcher", "contract_agent", "reviewer"):
        if agent not in done:
            return agent
    return "FINISH"


def status_text(state: dict) -> str:
    done = set(state.get("done", []))
    lines = []
    for agent in ("extractor", "matcher", "contract_agent", "reviewer"):
        lines.append(f"- {agent}: {'done' if agent in done else 'not done'}")
    if "extractor" in done:
        lines.append("Invoice read: " + ("yes" if state.get("extraction") else "NO, extraction failed"))
    if "matcher" in done:
        lines.append(f"Supplier found: {'yes' if state.get('supplier') else 'no'}")
    return "Progress:\n" + "\n".join(lines)


async def supervisor(state: dict) -> dict:
    t0 = time.perf_counter()
    hops = state.get("hops", 0) + 1
    expected = needed_next(state)
    chosen, reason = expected, ""
    if hops > MAX_HOPS:
        reason = "step limit reached"
    else:
        try:
            r = await get_llm().with_structured_output(Route).ainvoke(
                [SystemMessage(SUPERVISOR_SYSTEM), HumanMessage(status_text(state))])
            chosen, reason = r.next, r.reason
        except Exception as e:  # unreadable answer: follow the required order
            chosen, reason = expected, f"answer unreadable ({type(e).__name__})"
    overridden = chosen != expected
    detail = f"Chose {chosen}" + (f": {reason}" if reason else "")
    if overridden:
        detail += f" | Corrected to {expected}, because that work has to come first"
    return {"next": expected, "hops": hops, "supervisor_overrides": state.get("supervisor_overrides", 0) + overridden,
            "steps": [_step("supervisor", detail, t0, chosen=chosen, routed_to=expected, overridden=overridden)]}


# ---------------------------------------------------------------- extractor

async def extractor(state: dict) -> dict:
    t0 = time.perf_counter()
    text = read_pdf_text(state["pdf_path"])
    r = await asyncio.to_thread(extract_invoice, text)
    e = r["extraction"]
    if e:
        detail = (f"Read invoice {e['invoice_number']} from {e['supplier_name']}: {len(e['lines'])} line(s), "
                  f"gross {e['gross_total']:,.2f} {e['currency']}, PO {e['po_number'] or 'none'}")
        if r["retried"]:
            detail += " | Self-check found a value not in the document; corrected on a second try"
        if e.get("instructions_found"):
            detail += " | Found text aimed at automated systems (not followed)"
    else:
        detail = f"Could not read the invoice: {r['error']}"
    return {"extraction": e, "extraction_error": r["error"], "done": ["extractor"],
            "steps": [_step("extractor", detail, t0, retried=r["retried"])]}


# ---------------------------------------------------------------- matcher

def _last(calls: list[dict], name: str, ok) -> dict | None:
    for c in reversed(calls):
        if c["tool"] == name and "error" not in c["result"]:
            try:
                if ok(c):
                    return c["result"]
            except (TypeError, ValueError, KeyError):
                continue
    return None


def _same_money(a, b) -> bool:
    try:
        return a is not None and b is not None and math.isclose(float(a), float(b), abs_tol=0.005)
    except (TypeError, ValueError):
        return False


async def matcher(state: dict) -> dict:
    t0 = time.perf_counter()
    inv = state["extraction"]
    tools = await get_tools()
    llm = get_llm().bind_tools(list(tools.values()))
    facts = {k: inv.get(k) for k in ("supplier_name", "supplier_vat_number", "invoice_number", "po_number",
                                     "gross_total")}
    msgs = [SystemMessage(MATCHER_SYSTEM), HumanMessage("Invoice details:\n" + json.dumps(facts))]
    calls = []
    for _ in range(MAX_MATCHER_ROUNDS):
        try:
            ai = await llm.ainvoke(msgs)
        except Exception as e:
            calls.append({"tool": "(model error)", "args": {}, "result": {"error": str(e)[:200]}})
            break
        msgs.append(ai)
        if not ai.tool_calls:
            break
        for tc in ai.tool_calls:
            try:
                res = parse(await tools[tc["name"]].ainvoke(tc["args"])) if tc["name"] in tools \
                    else {"error": f"unknown tool {tc['name']}"}
            except Exception as e:
                res = {"error": f"{type(e).__name__}: {e}"[:200]}
            calls.append({"tool": tc["name"], "args": tc["args"], "result": res})
            msgs.append(ToolMessage(json.dumps(res, default=str)[:4000], tool_call_id=tc["id"]))

    # Check the agent's evidence; make any missing or wrong lookup directly (a "fallback").
    fallbacks = []
    vat = (inv.get("supplier_vat_number") or "").upper()
    sup_res = _last(calls, "find_supplier", lambda c: c["result"]["found"] and
                    (not vat or c["result"]["suppliers"][0]["vat_number"].upper() == vat))
    if sup_res is None:
        fallbacks.append("find_supplier")
        sup_res = await call("find_supplier", name=inv.get("supplier_name") or "", vat_number=inv.get("supplier_vat_number") or "")
    supplier = sup_res["suppliers"][0] if sup_res.get("found") else None

    po_no = inv.get("po_number")
    po, deliveries = {"found": False, "po_number": None, "reason": "no purchase order number on the invoice"}, None
    if po_no:
        po = _last(calls, "get_purchase_order", lambda c: normalise_po(c["args"]["po_number"]) == normalise_po(po_no))
        if po is None:
            fallbacks.append("get_purchase_order")
            po = await call("get_purchase_order", po_number=po_no)
        if po.get("found"):
            deliveries = _last(calls, "get_deliveries", lambda c: normalise_po(c["args"]["po_number"]) == po["po_number"])
            if deliveries is None:
                fallbacks.append("get_deliveries")
                deliveries = await call("get_deliveries", po_number=po["po_number"])

    duplicates = None
    if supplier:
        duplicates = _last(calls, "find_possible_duplicates", lambda c:
                           c["args"]["supplier_id"] == supplier["supplier_id"]
                           and normalise_invoice_number(c["args"]["invoice_number"]) == normalise_invoice_number(inv["invoice_number"])
                           and normalise_po(c["args"].get("po_number")) == normalise_po(po_no)
                           and _same_money(c["args"].get("gross_total"), inv["gross_total"]))
        if duplicates is None:
            fallbacks.append("find_possible_duplicates")
            duplicates = await call("find_possible_duplicates", supplier_id=supplier["supplier_id"],
                                    invoice_number=inv["invoice_number"], po_number=po_no or "",
                                    gross_total=inv["gross_total"])

    agent_tools = ", ".join(c["tool"] for c in calls) or "none"
    detail = (f"Agent made {len(calls)} tool call(s): {agent_tools} | "
              f"Fallback calls by code: {', '.join(fallbacks) or 'none'} | "
              f"Supplier: {supplier['supplier_id'] + ' ' + supplier['name'] if supplier else 'NOT FOUND'} | "
              f"PO: {po.get('po_number') or 'none'} {'found' if po.get('found') else 'not found'} | "
              f"Earlier invoices matching: {len((duplicates or {}).get('matches', []))}")
    return {"supplier": supplier, "po": po, "deliveries": deliveries, "duplicates": duplicates,
            "matcher_tool_calls": len(calls), "matcher_fallbacks": fallbacks, "done": ["matcher"],
            "steps": [_step("matcher", detail, t0, tool_calls=calls, fallbacks=fallbacks)]}


# ---------------------------------------------------------------- contract agent

class ContractPrice(BaseModel):
    sku: str = Field(description="Item code exactly as in the schedule")
    agreed_unit_price: float = Field(description="Agreed unit price as a plain number")


class ContractTerms(BaseModel):
    contract_id: str = Field(description="Contract number, e.g. CTR-2026-01")
    payment_terms_days: int | None = Field(default=None, description="Days allowed for payment")
    prices: list[ContractPrice] = Field(description="Agreed prices for the requested item codes")


def _price_in_text(text: str, sku: str, price: float) -> bool:
    """True if the schedule row for this item code shows exactly this price."""
    for line in text.splitlines():
        if line.startswith(f"{sku} |") and re.search(rf"\|\s*{price:.2f}\s*$", line):
            return True
    return False


async def contract_agent(state: dict) -> dict:
    t0 = time.perf_counter()
    inv, sup = state["extraction"], state.get("supplier")
    skus = [l["sku"] for l in inv["lines"]]
    if not sup:
        contract = {"contract_id": None, "payment_terms_days": None, "prices": {}, "missing": skus, "sections": []}
        return {"contract": contract, "done": ["contract_agent"],
                "steps": [_step("contract_agent", "No supplier found, so no contract could be searched", t0)]}

    sid = sup["supplier_id"]
    hits = await asyncio.to_thread(search_contract, sid, "Schedule 1 agreed unit prices for " + ", ".join(skus), 2)
    hits += await asyncio.to_thread(search_contract, sid, "Payment terms: number of days to pay invoices", 1)
    seen, sections = set(), []
    for h in hits:
        if h["section"] not in seen:
            seen.add(h["section"])
            sections.append(h)
    text = "\n\n".join(h["text"] for h in sections)

    verified, dropped, days, cid, error = {}, [], None, None, None
    try:
        form = await get_llm().with_structured_output(ContractTerms).ainvoke(
            [SystemMessage(CONTRACT_SYSTEM), HumanMessage(f"Item codes: {', '.join(skus)}\n\nContract sections:\n{text}")])
        for p in form.prices:
            if p.sku in skus and _price_in_text(text, p.sku, p.agreed_unit_price):
                verified[p.sku] = p.agreed_unit_price
            else:
                dropped.append(f"{p.sku} {p.agreed_unit_price}")
        if form.payment_terms_days and f"within {form.payment_terms_days} days" in text:
            days = form.payment_terms_days
        cid = form.contract_id if form.contract_id in text else sections[0]["contract_id"] if sections else None
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:200]
    missing = [s for s in skus if s not in verified]
    contract = {"contract_id": cid, "payment_terms_days": days, "prices": verified, "missing": missing,
                "sections": [h["section"] for h in sections]}
    detail = (f"Searched {sup['name']}'s contract only: {len(sections)} section(s) "
              f"({'; '.join(h['section'] for h in sections)}) | Agreed prices found: "
              + (", ".join(f"{k} {v:.2f}" for k, v in verified.items()) or "none")
              + f" | Payment terms: {days or 'not found'} days")
    if dropped:
        detail += f" | Dropped (not in the contract text): {', '.join(dropped)}"
    if missing:
        detail += f" | No verified price for: {', '.join(missing)}"
    if error:
        detail += f" | Model answer unreadable: {error}"
    return {"contract": contract, "done": ["contract_agent"], "steps": [_step("contract_agent", detail, t0)]}


# ---------------------------------------------------------------- reviewer

def _template_note(result: dict) -> str:
    bad = [c["detail"] for c in result["checks"] if c["status"] != "pass"]
    return ("Flagged by the automatic checks: " + "; ".join(bad)
            + f". Money at risk: EUR {result['money_at_risk_net']:,.2f} excluding VAT. Please review before payment.")


async def reviewer(state: dict) -> dict:
    t0 = time.perf_counter()
    inv = state.get("extraction")
    if not inv:
        result = {"checks": [], "problems": [], "unverified": ["extraction"], "decision": "flag",
                  "money_at_risk_net": 0.0}
        note = "The invoice could not be read automatically, so nothing could be checked. Please check it by hand."
    else:
        result = rules.run_checks(inv, state.get("supplier"), state.get("po"), state.get("deliveries"),
                                  state.get("duplicates"), state.get("contract", {}).get("prices", {}))
        if result["decision"] == "approve":
            c = state.get("contract", {})
            note = f"All {len(result['checks'])} checks passed. Approved for payment"
            note += (f" within {c['payment_terms_days']} days under contract {c['contract_id']}."
                     if c.get("payment_terms_days") else ".")
        else:
            facts = {"supplier": inv["supplier_name"], "invoice_number": inv["invoice_number"],
                     "problems_found": [c["detail"] for c in result["checks"] if c["status"] == "fail"],
                     "checks_that_could_not_be_done": [c["detail"] for c in result["checks"] if c["status"] == "unverified"],
                     "money_at_risk_eur_excl_vat": result["money_at_risk_net"]}
            try:
                note = (await get_llm().ainvoke([SystemMessage(REVIEWER_SYSTEM),
                                                 HumanMessage(json.dumps(facts, indent=1))])).content.strip()
            except Exception:
                note = _template_note(result)
    status = "approved_automatically" if result["decision"] == "approve" else "waiting_for_person"
    summary = ", ".join(f"{c['check']} {c['status']}" for c in result["checks"])
    detail = (f"Decision by the rules: {result['decision'].upper()} | Problems: {', '.join(result['problems']) or 'none'}"
              f" | Money at risk: EUR {result['money_at_risk_net']:,.2f} | Checks: {summary}")
    return {"result": result, "note": note, "final_status": status, "done": ["reviewer"],
            "steps": [_step("reviewer", detail, t0)]}
