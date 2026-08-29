"""Regression tests for ERP chatbot response quality fixes."""

from __future__ import annotations

from types import SimpleNamespace

from backend.src.nlu.intent_parser import _fast_intent
from backend.src.prompts.output_prompt import get_erp_analysis_output_prompt
from backend.src.prompts.task_prompt import TaskType
from backend.src.tools.db_capability_registry import resolve_database_capability


def test_detail_followup_routes_to_deterministic_erp_stream():
    from backend import server

    assert server._is_deterministic_erp_stream_request("有没有明细") is True
    assert resolve_database_capability("有没有明细") is not None


def test_piperylene_situation_intent_is_erp():
    task_type, confidence = _fast_intent("上周间戊二烯情况，输出柱图")
    assert task_type == TaskType.ERP_DATA_QUERY
    assert confidence >= 0.9


def test_classify_chart_intent_includes_bar_for_zhugitu():
    from backend import server

    directives = server._classify_chart_intent(
        "上周间戊二烯情况，输出柱图",
        task_type="erp_data_query",
        resolved_chat_mode="chat",
        product_ids=["piperylene"],
    )
    assert "bar" in directives


def test_build_erp_sales_bar_visualization():
    from backend import server

    summary = {
        "period_start": "2026-05-04",
        "period_end": "2026-05-10",
        "period_change": -5.2,
        "rows": [
            {
                "product": "间戊二烯",
                "volume_ton": 367.57,
                "orders": 3,
                "avg_price": 10282.98,
                "revenue_cny": 3780000.0,
            }
        ],
    }
    viz = server._build_erp_sales_bar_visualization(summary, "piperylene", "zh")
    assert viz is not None
    assert viz["kind"] == "bar"
    assert len(viz["data"]) == 1
    assert viz["data"][0]["volume"] == 367.57


def test_erp_hybrid_analysis_mode_detection():
    from backend.src.engine.decision_graph import _is_erp_hybrid_analysis_mode

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "上周间戊二烯销售情况",
        "resolved_chat_mode": "analysis",
        "user_prefs": {},
    }
    assert _is_erp_hybrid_analysis_mode(state) is True

    detail_state = dict(state)
    detail_state["user_query"] = "有没有明细"
    assert _is_erp_hybrid_analysis_mode(detail_state) is False


def test_edia_fragment_detected_as_placeholder_skeleton():
    from backend.src.engine.decision_graph import _looks_like_placeholder_skeleton_report

    assert _looks_like_placeholder_skeleton_report("! EDIA。? :\n\n**** ()\n**** (、)") is True


def test_erp_analysis_output_prompt_forbids_placeholders():
    prompt = get_erp_analysis_output_prompt("zh")
    assert "****" in prompt
    assert "禁止" in prompt
    assert "## 结论" in prompt


def test_guardrail_prefers_deterministic_erp_over_garbled_llm_draft():
    from backend.src.engine.decision_graph import _apply_deterministic_guardrails

    erp_data = """
ERP SALES SUMMARY (2026-05-04 to 2026-05-10):
Product Volume_ton Orders Avg_price
间戊二烯 367.57 3 10282.98
TOTAL 367.57
"""
    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "上周销售情况",
        "user_prefs": {"language": "zh", "chat_mode": "chat"},
        "resolved_chat_mode": "chat",
        "erp_data": erp_data,
    }
    garbled = "! EDIA。? :\n\n**** (、 7、 )\n**** (、、)"
    guarded = _apply_deterministic_guardrails(garbled, state)
    assert "****" not in guarded
    assert "ERP销售" in guarded or "销量" in guarded


def test_reasoning_strip_keeps_body_when_no_heading():
    from backend.src.nlu.llm_service import RealLLMService

    service = RealLLMService.__new__(RealLLMService)
    draft = (
        "Thinking Process:\n"
        "1. Compare ERP volume with prior week.\n"
        "2. Check average price.\n\n"
        "## 结论\n"
        "上周间戊二烯销量367.57吨，订单3单。"
    )
    cleaned = service._strip_reasoning_blocks(draft)
    assert "## 结论" in cleaned
    assert "367.57" in cleaned
