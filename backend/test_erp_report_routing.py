from __future__ import annotations

from backend.server import (
    _deterministic_erp_stream_enhancements,
    _is_deterministic_erp_stream_request,
    _is_erp_report_stream_query,
    _is_fast_erp_stream_eligible,
)
from backend.src.engine.decision_graph import _looks_like_erp_report_query
from backend.src.hermes.erp_grounding import (
    guard_erp_markdown_against_denial,
    looks_like_erp_access_denial,
)
from backend.src.routing.chat_route_plan import build_chat_route_plan
from backend.src.nlu.intent_parser import _fast_intent
from backend.src.prompts.task_prompt import TaskType
from backend.src.routing.query_router import is_deterministic_erp_stream, route_query
from backend.src.tools.db_capability_registry import resolve_database_capability


ERP_REPORT_QUERY = "give me this month erp report"
SALES_REVENUE_QUERY = "this month sales revenue"


def test_erp_report_intent_is_erp_data_query():
    task_type, confidence = _fast_intent(ERP_REPORT_QUERY)
    assert task_type == TaskType.ERP_DATA_QUERY
    assert confidence >= 0.9


def test_erp_report_query_router_is_deterministic_erp():
    routed = route_query(ERP_REPORT_QUERY)
    assert routed.path == "deterministic_erp"
    assert is_deterministic_erp_stream(routed)


def test_erp_report_capability_resolution():
    capability = resolve_database_capability(ERP_REPORT_QUERY)
    assert capability is not None
    assert capability.capability_id == "erp_sales_summary"


def test_erp_report_stream_gate_enabled():
    plan = build_chat_route_plan(ERP_REPORT_QUERY).to_dict()
    assert _looks_like_erp_report_query(ERP_REPORT_QUERY)
    assert _is_erp_report_stream_query(ERP_REPORT_QUERY)
    assert _is_deterministic_erp_stream_request(ERP_REPORT_QUERY, plan) is True


def test_sales_revenue_not_blocked_by_mysql_skill():
    plan = build_chat_route_plan(SALES_REVENUE_QUERY).to_dict()
    assert "mysql" in set(plan.get("agent_skills") or [])
    assert _is_deterministic_erp_stream_request(SALES_REVENUE_QUERY, plan) is True
    assert _is_fast_erp_stream_eligible(
        SALES_REVENUE_QUERY,
        plan,
        deterministic_erp_stream=True,
    )


def test_deterministic_erp_enhancements_for_monthly_report():
    applied = _deterministic_erp_stream_enhancements(ERP_REPORT_QUERY)
    assert applied.get("deterministic_erp_stream") == "true"
    assert applied.get("deterministic_erp_sales") == "true"
    assert applied.get("database_capability") == "erp_sales_summary"


def test_erp_access_denial_guard_replaces_hallucinated_refusal():
    denial = "I don't have access to your ERP system or any internal database live data."
    facts = "## ERP Sales Summary\n\nTotal revenue: 12,345,678 CNY"
    guarded = guard_erp_markdown_against_denial(
        denial,
        facts,
        has_erp_data=True,
    )
    assert guarded == facts
    assert looks_like_erp_access_denial(denial) is True
    assert looks_like_erp_access_denial(facts) is False
