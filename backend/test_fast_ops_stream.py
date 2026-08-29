from __future__ import annotations

from backend.src.engine.decision_graph import (
    _is_ops_inventory_pricing_query,
    _route_after_orchestrator,
    _route_after_parallel_fetch,
)
from backend.src.nlu.types import TaskType
from types import SimpleNamespace


SAMPLE_QUERY = (
    "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，"
    "并给出运营建议。"
)


def test_ops_inventory_query_detection():
    assert _is_ops_inventory_pricing_query(SAMPLE_QUERY) is True


def test_route_after_parallel_fetch_skips_research_for_ops():
    state = {"user_query": SAMPLE_QUERY}
    assert _route_after_parallel_fetch(state) == "gather_context"


def test_route_after_orchestrator_uses_simple_lookup_for_ops():
    state = {
        "user_query": SAMPLE_QUERY,
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
    }
    assert _route_after_orchestrator(state) == "simple_lookup"
