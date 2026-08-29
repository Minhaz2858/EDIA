from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from backend.server import (
    _generate_weekly_report_chat_payload,
    _is_deterministic_erp_stream_request,
    _is_fast_erp_stream_eligible,
    _is_weekly_report_chat_request,
    _should_bypass_erp_fast_path,
    _try_erp_facts_then_kimi_stream_report,
)
from backend.src.hermes.erp_grounding import is_fast_erp_stream_eligible, should_bypass_erp_fast_path
from backend.src.routing.chat_route_plan import build_chat_route_plan


MARKET_WEEKLY_QUERY = "make market weekly report"
DEEP_MARKET_RESEARCH_QUERY = (
    "形成一版深度市场研究报告，包含短期预测、风险和经营启示。"
)
ERP_REPORT_QUERY = "give me this month erp report"


def test_is_weekly_report_chat_request_market_weekly():
    assert _is_weekly_report_chat_request(MARKET_WEEKLY_QUERY) is True
    assert _is_weekly_report_chat_request("weekly report for C5") is True
    assert _is_weekly_report_chat_request("market weekly") is True


def test_is_weekly_report_chat_request_not_erp_sales():
    assert _is_weekly_report_chat_request(ERP_REPORT_QUERY) is False
    assert _is_weekly_report_chat_request("this month sales revenue") is False


def test_market_weekly_not_fast_erp_eligible():
    plan = build_chat_route_plan(MARKET_WEEKLY_QUERY).to_dict()
    assert is_fast_erp_stream_eligible(MARKET_WEEKLY_QUERY, plan) is False
    assert _is_fast_erp_stream_eligible(MARKET_WEEKLY_QUERY, plan) is False
    assert (
        is_fast_erp_stream_eligible(
            MARKET_WEEKLY_QUERY,
            plan,
            deterministic_erp_stream=True,
        )
        is False
    )


def test_market_weekly_not_deterministic_erp_stream():
    plan = build_chat_route_plan(MARKET_WEEKLY_QUERY).to_dict()
    assert _is_deterministic_erp_stream_request(MARKET_WEEKLY_QUERY, plan) is False


def test_deep_market_research_bypasses_erp_fast_path():
    plan = build_chat_route_plan(DEEP_MARKET_RESEARCH_QUERY).to_dict()
    assert should_bypass_erp_fast_path(DEEP_MARKET_RESEARCH_QUERY, plan) is True
    assert _should_bypass_erp_fast_path(DEEP_MARKET_RESEARCH_QUERY, plan) is True
    assert is_fast_erp_stream_eligible(DEEP_MARKET_RESEARCH_QUERY, plan) is False
    assert (
        is_fast_erp_stream_eligible(
            DEEP_MARKET_RESEARCH_QUERY,
            plan,
            deterministic_erp_stream=True,
        )
        is False
    )
    assert _is_deterministic_erp_stream_request(DEEP_MARKET_RESEARCH_QUERY, plan) is False


def test_deep_market_research_erp_try_returns_none_without_facts(monkeypatch):
    import os

    monkeypatch.setenv("EDIA_FAST_ERP_STREAM", "true")
    os.environ["EDIA_FAST_ERP_STREAM"] = "true"

    with patch(
        "backend.src.hermes.erp_grounding.build_erp_sales_report_markdown",
        lambda message, prefs=None: None,
    ):
        result = _try_erp_facts_then_kimi_stream_report(
            DEEP_MARKET_RESEARCH_QUERY,
            {"language": "zh"},
            hermes_plan=build_chat_route_plan(DEEP_MARKET_RESEARCH_QUERY).to_dict(),
            deterministic_erp_stream=True,
        )
    assert result is None


def test_erp_facts_then_kimi_returns_none_for_weekly_when_no_facts(monkeypatch):
    import os

    monkeypatch.setenv("EDIA_FAST_ERP_STREAM", "true")
    os.environ["EDIA_FAST_ERP_STREAM"] = "true"

    with patch(
        "backend.src.hermes.erp_grounding.build_erp_sales_report_markdown",
        lambda message, prefs=None: None,
    ):
        result = _try_erp_facts_then_kimi_stream_report(
            MARKET_WEEKLY_QUERY,
            {"language": "en"},
            hermes_plan=build_chat_route_plan(MARKET_WEEKLY_QUERY).to_dict(),
            deterministic_erp_stream=False,
        )
    assert result is None


def test_erp_report_still_fast_eligible_with_deterministic_flag():
    plan = build_chat_route_plan(ERP_REPORT_QUERY).to_dict()
    assert (
        is_fast_erp_stream_eligible(
            ERP_REPORT_QUERY,
            plan,
            deterministic_erp_stream=True,
        )
        is True
    )


def test_generate_weekly_report_chat_payload():
    fake_result = {
        "markdown": "## Weekly Market\n\nProduct A up 2%.",
        "report_id": "wr_test_001",
        "week_start": "2026-05-12",
        "week_end": "2026-05-18",
        "products_count": 12,
    }

    async def fake_generate_weekly_report(**_kwargs):
        return fake_result

    with patch(
        "backend.src.service.weekly_report_generator.generate_weekly_report",
        fake_generate_weekly_report,
    ), patch(
        "backend.src.db.market_db.market_db",
        MagicMock(engine=MagicMock()),
    ), patch("backend.server.decision_service", MagicMock(llm=MagicMock())), patch(
        "backend.server.real_llm",
        MagicMock(),
    ):
        payload = asyncio.run(
            _generate_weekly_report_chat_payload(
                user_id="user-1",
                session_id="sess-1",
                language="en",
            )
        )

    assert payload["task_type"] == "weekly_report"
    assert payload["mode_reason"] == "weekly_report_generator"
    assert "Weekly Market" in payload["markdown"]
    assert payload["context"]["weekly_report_id"] == "wr_test_001"
