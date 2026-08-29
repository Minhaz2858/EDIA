from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skip(reason="Hermes agent runtime removed from EDIA")

from backend.src.hermes.edia_context import EdiaRequestContext, get_edia_context, set_edia_context
from backend.src.hermes.edia_tool_handlers import (
    handle_edia_build_plan,
    handle_edia_forecast_policy,
    handle_edia_resolve_followup,
)
from backend.src.hermes.hermes_agent import build_hermes_plan, resolve_followup_standalone_query
from backend.src.hermes.hermes_runtime import (
    HermesRuntime,
    hermes_lite_stream_eligible,
    is_history_dependent_followup,
    localized_hermes_tool_stage_label,
    _normalize_moonshot_base_url,
    _register_edia_tools,
    hermes_main_enabled,
)


def test_normalize_moonshot_base_url_fixes_typo():
    assert _normalize_moonshot_base_url("https://api.moonshot.cn/v1z") == "https://api.moonshot.cn/v1"


def test_hermes_main_enabled_flag():
    with patch.dict("os.environ", {"EDIA_HERMES_MAIN": "true"}):
        assert hermes_main_enabled() is True
    with patch.dict("os.environ", {"EDIA_HERMES_MAIN": "false"}):
        assert hermes_main_enabled() is False


def test_sales_followup_rewrites_to_standalone_query():
    history = [
        {"role": "user", "content": "give me this week total sales"},
        {
            "role": "assistant",
            "content": "For 2026-05-11 to 2026-05-17, ERP records show total sales revenue.",
        },
    ]
    resolved = resolve_followup_standalone_query("i need more details", history)

    assert resolved["is_follow_up"] is True
    assert resolved["period"] == "2026-05-11 to 2026-05-17"
    assert "ERP sales breakdown" in resolved["standalone_query"]


def test_build_hermes_plan_inherits_sales_followup_context():
    history = [
        {"role": "user", "content": "give me this week total sales"},
        {
            "role": "assistant",
            "content": "For 2026-05-11 to 2026-05-17, ERP records show total sales revenue.",
        },
    ]
    plan = build_hermes_plan("i need more details", conversation_history=history)

    assert "erp" in plan.domains
    assert plan.normalized_query["followup"]["period"] == "2026-05-11 to 2026-05-17"
    assert plan.normalized_query["period_source"] == "conversation_history"


def test_edia_build_plan_tool_uses_context(monkeypatch):
    class _FakePlan:
        def to_dict(self):
            return {"domains": ["erp"], "capabilities": ["erp_order_details"]}

    monkeypatch.setattr(
        "backend.src.hermes.edia_tool_handlers.build_hermes_plan",
        lambda query, llm_service=None, **kwargs: _FakePlan(),
    )
    ctx = EdiaRequestContext(user_query="订单详情")
    token = set_edia_context(ctx)
    try:
        raw = handle_edia_build_plan({"query": "订单详情"})
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert payload["plan"]["domains"] == ["erp"]
        assert get_edia_context().hermes_plan["capabilities"] == ["erp_order_details"]
    finally:
        from backend.src.hermes.edia_context import reset_edia_context

        reset_edia_context(token)


def test_edia_forecast_policy_tool():
    ctx = EdiaRequestContext(
        user_query="下周价格预测",
        hermes_plan={
            "domains": ["forecast", "market"],
            "product_ids": ["blowing_agent"],
            "forecast_policy": {"horizons": ["7d"], "policy_mode": "adaptive"},
            "validation_gates": ["forecast_numbers_locked_before_llm"],
        },
    )
    token = set_edia_context(ctx)
    try:
        raw = handle_edia_forecast_policy({})
        payload = json.loads(raw)
        assert payload["status"] == "ok"
        assert "forecast" in payload["domains"]
        assert payload["forecast_policy"]["policy_mode"] == "adaptive"
    finally:
        from backend.src.hermes.edia_context import reset_edia_context

        reset_edia_context(token)


def test_edia_resolve_followup_tool_updates_context():
    ctx = EdiaRequestContext(
        user_query="i need more details",
        conversation_history=[
            {"role": "user", "content": "give me this week total sales"},
            {
                "role": "assistant",
                "content": "For 2026-05-11 to 2026-05-17, ERP records show total sales revenue.",
            },
        ],
    )
    token = set_edia_context(ctx)
    try:
        payload = json.loads(handle_edia_resolve_followup({"query": "i need more details"}))
        assert payload["status"] == "ok"
        assert payload["followup"]["is_follow_up"] is True
        assert ctx.turn_state["period"] == "2026-05-11 to 2026-05-17"
    finally:
        from backend.src.hermes.edia_context import reset_edia_context

        reset_edia_context(token)


def test_hermes_runtime_process_chat_with_fake_agent(monkeypatch):
    fake_agent = MagicMock()
    fake_agent.run_conversation.return_value = {
        "final_response": "Hello from Hermes",
        "api_calls": 1,
        "messages": [],
    }

    runtime = HermesRuntime(decision_service=MagicMock(), llm_service=MagicMock())
    monkeypatch.setattr(
        "backend.src.hermes.hermes_runtime._get_or_create_agent",
        lambda session_id: fake_agent,
    )
    monkeypatch.setattr(
        runtime,
        "build_plan_metadata",
        lambda message: {"domains": ["general"]},
    )

    result = runtime.process_chat(
        message="hi",
        session_id="sess-1",
        user_id="user-1",
        prior_messages=[],
        user_prefs={},
        hermes_plan={"domains": ["general"]},
    )
    assert result["status"] == "success"
    assert "Hello from Hermes" in result["markdown"]
    assert result["llm_route"] == "hermes"
    fake_agent.run_conversation.assert_called_once()


def test_register_edia_tools_imports_registry():
    _register_edia_tools()
    from tools.registry import registry

    names = {entry.name for entry in registry._snapshot_entries()}
    assert "edia_build_plan" in names
    assert "edia_generate_report" in names
    assert "edia_resolve_followup" in names
    assert "edia_query_erp_sales_summary" in names
    assert "edia_run_forecast" in names
    assert "edia_render_chart" in names
    assert "edia_query_erp_customer_sales" in names
    assert "edia_execute_db_capability" in names
    assert "edia_get_background_insights" in names
    assert "edia_stream_synthesis" in names


def test_history_dependent_followup_routes_to_hermes():
    prior = [
        {"role": "user", "content": "give me this week total sales"},
        {
            "role": "assistant",
            "content": "For 2026-05-11 to 2026-05-17, ERP records show total sales revenue.",
        },
    ]

    assert is_history_dependent_followup("i need more details", prior) is True


def test_hermes_main_active_helper(monkeypatch):
    with patch("backend.src.hermes.hermes_runtime.hermes_main_enabled", return_value=True):
        from backend.src.hermes import hermes_runtime as runtime_module

        assert runtime_module.hermes_main_enabled() is True


def test_localized_hermes_tool_stage_label_zh():
    label = localized_hermes_tool_stage_label("edia_query_erp_sales_summary", "zh")
    assert "ERP" in label or "销售" in label


def test_hermes_lite_stream_eligible_with_operational_skills():
    plan = build_hermes_plan("本月销售额是多少").to_dict()
    plan["agent_skills"] = ["forecast-run", "erp-revenue-guardrail", "chart-render"]
    assert hermes_lite_stream_eligible(plan, hermes_followup=False) is True


def test_hermes_lite_stream_blocked_for_export_skills():
    plan = build_hermes_plan("导出word报告").to_dict()
    plan["domains"] = ["document"]
    plan["agent_skills"] = ["docx", "minimax-docx"]
    assert hermes_lite_stream_eligible(plan, hermes_followup=False) is False


def test_hermes_lite_stream_eligible_for_market_plan():
    plan = {
        "domains": ["market", "pricing", "database"],
        "capabilities": ["price_recommendation"],
        "agent_skills": [],
    }
    assert hermes_lite_stream_eligible(plan, hermes_followup=False) is True


def test_hermes_lite_stream_not_eligible_for_followup():
    plan = {"domains": ["erp"], "capabilities": ["erp_sales_summary"], "agent_skills": []}
    assert hermes_lite_stream_eligible(plan, hermes_followup=True) is False


def test_process_chat_stream_yields_delta_before_done(monkeypatch):
    import time

    class _SlowAgent:
        tool_progress_callback = None

        def run_conversation(self, message, conversation_history=None, stream_callback=None):
            if self.tool_progress_callback:
                self.tool_progress_callback("start", "edia_build_plan", "planning", {})
            time.sleep(0.05)
            if stream_callback:
                stream_callback("Hello ")
                time.sleep(0.05)
                stream_callback("world")
            return {"final_response": "Hello world", "messages": []}

    runtime = HermesRuntime(decision_service=MagicMock(), llm_service=MagicMock())
    monkeypatch.setattr(
        "backend.src.hermes.hermes_runtime._get_or_create_agent",
        lambda session_id: _SlowAgent(),
    )
    monkeypatch.setattr(
        runtime,
        "build_plan_metadata",
        lambda *args, **kwargs: {"domains": ["general"]},
    )
    monkeypatch.setattr(
        runtime.decision_service,
        "prepare_request",
        lambda *args, **kwargs: {"partial_context": {}},
    )

    events = list(
        runtime.process_chat_stream(
            message="hi",
            session_id="sess-stream",
            user_id="user-1",
            prior_messages=[],
            user_prefs={},
            hermes_plan={"domains": ["general"]},
        )
    )
    types = [event.get("type") for event in events]
    delta_index = types.index("delta")
    done_index = types.index("done")
    assert delta_index < done_index
    assert any(event.get("delta") == "Hello " for event in events if event.get("type") == "delta")


def test_process_chat_stream_fast_start_preview(monkeypatch):
    import time

    class _QuietAgent:
        tool_progress_callback = None

        def run_conversation(self, message, conversation_history=None, stream_callback=None):
            time.sleep(0.6)
            return {"final_response": "Done", "messages": []}

    runtime = HermesRuntime(decision_service=MagicMock(), llm_service=MagicMock())
    monkeypatch.setattr(
        "backend.src.hermes.hermes_runtime._get_or_create_agent",
        lambda session_id: _QuietAgent(),
    )
    monkeypatch.setattr(
        runtime,
        "build_plan_metadata",
        lambda *args, **kwargs: {"domains": ["market", "pricing"]},
    )
    monkeypatch.setenv("LOCAL_LLM_FAST_START_SECONDS", "0.05")
    monkeypatch.setattr(
        runtime.decision_service,
        "prepare_request",
        lambda *args, **kwargs: {
            "user_context": type(
                "Ctx",
                (),
                {"product": "C5", "period": "本周"},
            )(),
            "parsed_intent": type("Intent", (), {"task_type": type("T", (), {"value": "analysis"})()})(),
            "diagnosis": {"root_cause": "库存偏紧"},
        },
    )
    monkeypatch.setattr(runtime, "_guarded_business_report", lambda *args, **kwargs: None)

    events = list(
        runtime.process_chat_stream(
            message="本周报价",
            session_id="sess-preview",
            user_id="user-1",
            prior_messages=[],
            user_prefs={},
            hermes_plan={"domains": ["market", "pricing"]},
        )
    )
    preview_deltas = [
        event
        for event in events
        if event.get("type") == "delta"
        and (
            event.get("preview")
            or "快速结论" in str(event.get("delta") or "")
        )
    ]
    assert preview_deltas, "expected fast-start preview delta"
