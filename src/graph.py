"""The LangGraph graph: a supervisor routes each invoice through the specialist agents.

    START -> supervisor -> extractor | matcher | contract_agent | reviewer -> supervisor ...
    supervisor (FINISH) -> END                      if the rules approved the invoice
    supervisor (FINISH) -> human_review -> END      if the invoice was flagged

human_review pauses the run with LangGraph's interrupt(). The paused run is saved in a SQLite
checkpoint, so a person can approve or reject it later (from the app or the API) and the run resumes
exactly where it stopped. The pause is fixed in the graph: no model decides whether a person is asked.
"""

import operator
import time
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src import agents


class InvoiceState(TypedDict, total=False):
    invoice_id: str
    pdf_path: str
    extraction: dict | None
    extraction_error: str | None
    supplier: dict | None
    po: dict | None
    deliveries: dict | None
    duplicates: dict | None
    matcher_tool_calls: int
    matcher_fallbacks: list
    contract: dict
    result: dict
    note: str
    final_status: str
    human: dict
    next: str
    hops: int
    supervisor_overrides: int
    done: Annotated[list, operator.add]
    steps: Annotated[list, operator.add]


def human_review(state: dict) -> dict:
    t0 = time.perf_counter()
    answer = interrupt({"invoice_id": state.get("invoice_id"),
                        "invoice_number": (state.get("extraction") or {}).get("invoice_number"),
                        "problems": state["result"]["problems"], "unverified": state["result"]["unverified"],
                        "money_at_risk_net": state["result"]["money_at_risk_net"], "note": state.get("note")})
    decision = answer.get("decision")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    human = {"decision": decision, "by": answer.get("by") or "unknown", "comment": answer.get("comment") or "",
             "decided_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    status = "approved_by_person" if decision == "approve" else "rejected_by_person"
    detail = f"{human['by']} chose to {decision}" + (f": {human['comment']}" if human["comment"] else "")
    return {"human": human, "final_status": status,
            "steps": [{"agent": "human_review", "detail": detail, "seconds": round(time.perf_counter() - t0, 2)}]}


def route(state: dict) -> str:
    nxt = state["next"]
    if nxt != "FINISH":
        return nxt
    return "human_review" if state["result"]["decision"] == "flag" and "human" not in state else END


def build_graph(checkpointer=None):
    g = StateGraph(InvoiceState)
    g.add_node("supervisor", agents.supervisor)
    g.add_node("extractor", agents.extractor)
    g.add_node("matcher", agents.matcher)
    g.add_node("contract_agent", agents.contract_agent)
    g.add_node("reviewer", agents.reviewer)
    g.add_node("human_review", human_review)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route, ["extractor", "matcher", "contract_agent", "reviewer",
                                                  "human_review", END])
    for worker in ("extractor", "matcher", "contract_agent", "reviewer"):
        g.add_edge(worker, "supervisor")
    g.add_edge("human_review", END)
    return g.compile(checkpointer=checkpointer)


async def check_invoice(graph, invoice_id: str, pdf_path: str, thread_id: str) -> dict:
    """Run one invoice. Returns the final state plus paused=True if it waits for a person."""
    t0 = time.perf_counter()
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    out = await graph.ainvoke({"invoice_id": invoice_id, "pdf_path": str(pdf_path), "done": [], "steps": []}, cfg)
    return {**out, "paused": "__interrupt__" in out, "thread_id": thread_id,
            "seconds": round(time.perf_counter() - t0, 2)}


async def decide(graph, thread_id: str, decision: str, by: str, comment: str = "") -> dict:
    """Resume a paused invoice with a person's decision."""
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    out = await graph.ainvoke(Command(resume={"decision": decision, "by": by, "comment": comment}), cfg)
    return {**out, "paused": "__interrupt__" in out, "thread_id": thread_id}
