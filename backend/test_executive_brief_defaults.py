"""Tests for analysis-first executive brief defaults."""

from __future__ import annotations

from types import SimpleNamespace

from backend.src.engine.decision_graph import (
    _build_deterministic_erp_sales_report,
    _classify_chat_mode,
    _infer_response_mode,
    _is_business_data_task,
    _looks_like_incomplete_executive_brief,
    _should_use_executive_brief_format,
)
from backend.src.nlu.intent_parser import _fast_intent
from backend.src.prompts.output_prompt import get_executive_brief_output_prompt
from backend.src.prompts.task_prompt import TaskType


def test_greeting_is_not_business_data_task():
    task_type, _ = _fast_intent("你好")
    assert task_type == TaskType.CONVERSATIONAL
    assert _is_business_data_task(task_type, "你好") is False


def test_sales_query_defaults_to_analysis_and_executive_brief():
    query = "上周销售情况"
    task_type, _ = _fast_intent(query)
    assert task_type == TaskType.ERP_DATA_QUERY
    assert _is_business_data_task(task_type, query) is True

    mode, reason = _classify_chat_mode(query, task_type=task_type)
    assert mode == "analysis"
    assert reason == "business_data_default_analysis"

    response_mode = _infer_response_mode(query, task_type, explanation_mode=False)
    assert response_mode == "executive_brief"


def test_deterministic_erp_sales_report_uses_executive_brief_sections():
    erp_data = """
ERP SALES SUMMARY (2026-05-04 to 2026-05-10):
Product Volume_ton Orders Avg_price
间戊二烯 367.57 3 10282.98
TOTAL 367.57
Period-over-period sales volume change: -5.0%
"""
    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "上周销售情况",
        "user_prefs": {"language": "zh"},
        "resolved_chat_mode": "analysis",
        "response_mode": "executive_brief",
        "erp_data": erp_data,
    }
    report = _build_deterministic_erp_sales_report(state)
    assert "## 结论" in report
    assert "## 数据依据" in report
    assert "## 影响判断" in report
    assert "## 建议动作" in report
    assert "## 风险关注" in report
    assert "367.57" in report


def test_detail_followup_has_boss_sections():
    erp_data = """
ERP SALES SUMMARY (2026-05-04 to 2026-05-10):
Product Volume_ton Orders Avg_price
间戊二烯 367.57 3 10282.98
双环戊二烯 120.00 2 9800.00
TOTAL 487.57
"""
    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "有没有明细",
        "user_prefs": {"language": "zh"},
        "resolved_chat_mode": "analysis",
        "response_mode": "executive_brief",
        "erp_data": erp_data,
    }
    report = _build_deterministic_erp_sales_report(state)
    assert "## 结论" in report
    assert "## ERP销售明细" in report
    assert "## 风险关注" in report


def test_price_lookup_defaults_to_executive_brief_not_concise():
    query = "异戊二烯今天价格"
    task_type, _ = _fast_intent(query)
    assert _is_business_data_task(task_type, query) is True
    assert _infer_response_mode(query, task_type, explanation_mode=False) == "executive_brief"


def test_executive_brief_prompt_requires_sections():
    prompt = get_executive_brief_output_prompt("zh")
    for section in ("## 结论", "## 数据依据", "## 影响判断", "## 建议动作", "## 风险关注"):
        assert section in prompt


def test_incomplete_executive_brief_detection():
    assert _looks_like_incomplete_executive_brief("上周销量367吨。") is True
    assert _looks_like_incomplete_executive_brief(
        "## 结论\n\nok\n\n## 数据依据\n\n- x\n\n## 影响判断\n\n- y"
    ) is True


def test_should_use_executive_brief_format_respects_manual_chat_mode():
    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "上周销售情况",
        "response_mode": "executive_brief",
        "user_prefs": {"chat_mode": "chat"},
    }
    assert _should_use_executive_brief_format(state) is False
