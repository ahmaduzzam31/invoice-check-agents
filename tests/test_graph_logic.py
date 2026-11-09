"""Tests for the routing logic of the agent graph (no model needed)."""

from langgraph.graph import END

from src.agents import needed_next
from src.graph import build_graph, route


def test_agents_run_in_dependency_order():
    assert needed_next({"done": []}) == "extractor"
    assert needed_next({"done": ["extractor"], "extraction": {"x": 1}}) == "matcher"
    assert needed_next({"done": ["extractor", "matcher"], "extraction": {"x": 1}}) == "contract_agent"
    assert needed_next({"done": ["extractor", "matcher", "contract_agent"], "extraction": {"x": 1}}) == "reviewer"
    assert needed_next({"done": ["extractor", "matcher", "contract_agent", "reviewer"],
                        "extraction": {"x": 1}}) == "FINISH"


def test_unreadable_invoice_goes_straight_to_reviewer():
    assert needed_next({"done": ["extractor"], "extraction": None}) == "reviewer"
    assert needed_next({"done": ["extractor", "reviewer"], "extraction": None}) == "FINISH"


def test_flagged_invoices_always_go_to_a_person():
    assert route({"next": "FINISH", "result": {"decision": "flag"}}) == "human_review"
    assert route({"next": "FINISH", "result": {"decision": "approve"}}) == END
    assert route({"next": "FINISH", "result": {"decision": "flag"}, "human": {"decision": "reject"}}) == END
    assert route({"next": "matcher"}) == "matcher"


def test_graph_has_all_agents():
    nodes = set(build_graph().get_graph().nodes)
    assert {"supervisor", "extractor", "matcher", "contract_agent", "reviewer", "human_review"} <= nodes
