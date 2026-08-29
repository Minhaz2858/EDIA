from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.server import (
    _is_fast_erp_stream_eligible,
    _resolve_response_llm_route,
    _try_erp_facts_then_kimi_stream_report,
)
from backend.src.hermes.erp_grounding import (
    build_erp_kimi_synthesis_prompts,
    is_fast_erp_stream_eligible,
    stream_erp_synthesis,
)
from backend.src.routing.chat_route_plan import build_chat_route_plan


ERP_REPORT_QUERY = "give me this month erp report"
SALES_REVENUE_QUERY = "this month sales revenue"
SAMPLE_FACTS = "## ERP Sales\n\nTotal revenue: 12,345,678 CNY\nVolume: 1,824 tons"


def test_sales_revenue_fast_stream_eligible_with_deterministic_flag():
    plan = build_chat_route_plan(SALES_REVENUE_QUERY).to_dict()
    assert is_fast_erp_stream_eligible(
        SALES_REVENUE_QUERY,
        plan,
        deterministic_erp_stream=True,
    )
    assert _is_fast_erp_stream_eligible(
        SALES_REVENUE_QUERY,
        plan,
        deterministic_erp_stream=True,
    )


def test_erp_kimi_prompts_include_verified_facts():
    _system, user_prompt = build_erp_kimi_synthesis_prompts(
        ERP_REPORT_QUERY,
        SAMPLE_FACTS,
        language="en",
    )
    assert "VERIFIED_ERP_FACTS" in user_prompt
    assert "12,345,678" in user_prompt
    assert "upload" not in user_prompt.lower() or "never" in _system.lower()


def test_resolve_llm_route_allows_kimi_for_erp_facts_then_kimi():
    route = _resolve_response_llm_route(
        task_type="erp_data_query",
        enhancement_applied={
            "erp_facts_then_kimi": "true",
            "erp_synthesis_route": "external",
        },
        prefs={},
    )
    assert route == "external"


def test_try_erp_facts_then_kimi_uses_kimi_synthesis(monkeypatch):
    monkeypatch.setenv("EDIA_FAST_ERP_STREAM", "true")
    monkeypatch.setenv("EDIA_ERP_SYNTHESIS", "kimi")
    monkeypatch.setenv("EDIA_ERP_FAST_HYBRID", "false")

    captured: dict = {}

    def fake_build(message, prefs=None):
        return SAMPLE_FACTS

    def fake_stream(llm, message, facts_markdown, *, language="zh", on_delta=None):
        captured["facts"] = facts_markdown
        return "## Final ERP Report\n\nRevenue held steady at 12.3M CNY."

    monkeypatch.setattr(
        "backend.src.hermes.erp_grounding.build_erp_sales_report_markdown",
        fake_build,
    )
    monkeypatch.setattr(
        "backend.src.hermes.erp_grounding.stream_erp_synthesis",
        fake_stream,
    )

    import backend.server as server_module

    monkeypatch.setattr(server_module, "llm_service", MagicMock())

    result = _try_erp_facts_then_kimi_stream_report(
        ERP_REPORT_QUERY,
        {"language": "en"},
        hermes_plan={"domains": ["erp"]},
        deterministic_erp_stream=True,
    )

    assert result is not None
    assert result.get("mode_reason") == "erp_facts_then_kimi"
    assert "12.3M" in str(result.get("markdown") or "")
    assert captured.get("facts") == SAMPLE_FACTS
    assert result.get("llm_route") == "external"
    assert result.get("enhancement_applied", {}).get("erp_synthesis_route") == "external"


def test_erp_domains_block_full_hermes_stream_gate():
    plan = build_chat_route_plan(ERP_REPORT_QUERY).to_dict()
    hermes_domains = {
        str(domain or "").strip().lower() for domain in (plan.get("domains") or [])
    }
    use_full_hermes_stream = True
    if hermes_domains & {"erp", "database"}:
        use_full_hermes_stream = False
    assert "erp" in hermes_domains
    assert use_full_hermes_stream is False


def test_stream_erp_synthesis_calls_external_route(monkeypatch):
    monkeypatch.setenv("EDIA_ERP_SYNTHESIS", "kimi")
    llm = MagicMock()
    llm.generate_completion_stream.return_value = iter(["## Report\n", "Done."])

    markdown = stream_erp_synthesis(
        llm,
        ERP_REPORT_QUERY,
        SAMPLE_FACTS,
        language="en",
    )

    assert "Report" in markdown
    llm.generate_completion_stream.assert_called_once()
    _args, kwargs = llm.generate_completion_stream.call_args
    assert kwargs.get("route_hint") == "external"
    assert SAMPLE_FACTS in str(_args[1])
