from __future__ import annotations

from backend.src.engine.decision_graph import (
    _build_naphtha_passthrough_report,
    _is_naphtha_cost_passthrough_query,
    _is_price_move_action_query,
    _route_after_orchestrator,
    _route_after_parallel_fetch,
)
from backend.src.nlu.types import TaskType
from types import SimpleNamespace


NAPHTHA_QUERY = (
    "Naphtha 7d increase +8.0% (current 993.25 CNY/ton). "
    "What is the cost pass-through impact on our C5/C9 product line and pricing strategy? "
    "Should we adjust quotes now?"
)


def test_naphtha_passthrough_query_detection():
    assert _is_naphtha_cost_passthrough_query(NAPHTHA_QUERY) is True


def test_price_move_query_includes_passthrough_markers():
    assert _is_price_move_action_query(NAPHTHA_QUERY) is True


def test_route_after_parallel_fetch_skips_research_for_naphtha():
    state = {"user_query": NAPHTHA_QUERY}
    assert _route_after_parallel_fetch(state) == "gather_context"


def test_route_after_orchestrator_simple_lookup_for_naphtha():
    state = {
        "user_query": NAPHTHA_QUERY,
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
    }
    assert _route_after_orchestrator(state) == "simple_lookup"


def test_build_naphtha_passthrough_report_contains_core_sections():
    state = {
        "user_query": NAPHTHA_QUERY,
        "perception_snapshot": {"products": {}},
    }
    markdown = _build_naphtha_passthrough_report(state, language="en")
    assert "Executive Summary" in markdown
    assert "Quote Recommendation" in markdown
    assert "isoprene" in markdown.lower()
    assert "dcpd" in markdown.lower()
