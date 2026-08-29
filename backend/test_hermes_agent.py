from __future__ import annotations

import json

from backend.src.hermes.hermes_agent import build_hermes_plan
from backend.src.tools.erp_tools import format_customer_sales_report


def _sse_event_payload(body: str, event_name: str) -> dict:
    for frame in str(body or "").split("\n\n"):
        lines = [line.strip() for line in frame.splitlines() if line.strip()]
        event = "message"
        data = ""
        for line in lines:
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        if event == event_name and data:
            return json.loads(data)
    raise AssertionError(f"SSE event not found: {event_name}")


def test_hermes_routes_private_erp_order_details_to_internal_tools():
    plan = build_hermes_plan("请提供 5 月 11 日至 5 月 12 日的订单详情。")

    assert "erp" in plan.domains
    assert "erp_order_details" in plan.capabilities
    assert plan.privacy["external_llm_blocked"] == "true"
    assert plan.presentation_mode == "erp_detail_table"
    assert "erp_external_finalizer_blocked" in plan.validation_gates
    assert plan.normalized_query["capability"] == "erp_order_details"
    assert plan.normalized_query["period_start"] == "2026-05-11"
    assert plan.normalized_query["period_end"] == "2026-05-12"
    assert plan.normalized_query["period_source"] == "explicit_range"


def test_hermes_normalizes_english_month_name_order_details():
    plan = build_hermes_plan("Please provide order details for May 11th to May 12th.")

    assert "erp_order_details" in plan.capabilities
    assert plan.normalized_query["capability"] == "erp_order_details"
    assert plan.normalized_query["period_start"] == "2026-05-11"
    assert plan.normalized_query["period_end"] == "2026-05-12"
    assert plan.normalized_query["period_source"] == "explicit_range"


def test_hermes_forecast_plan_requires_policy_and_quality_gates():
    plan = build_hermes_plan("老板想看戊烷发泡剂下周价格预测和销售风险。")

    assert "forecast" in plan.domains
    assert "executive" in plan.domains
    assert "blowing_agent" in plan.product_ids
    assert "forecast_orchestration" in plan.capabilities
    assert "forecasting_service" in plan.tool_sequence
    assert "forecast_policy_service" in plan.tool_sequence
    assert plan.forecast_policy["policy_mode"] == "adaptive"
    assert "7d" in plan.forecast_policy["horizons"]
    assert "forecast_numbers_locked_before_llm" in plan.forecast_policy["quality_gates"]
    assert "confidence_matches_recent_accuracy" in plan.validation_gates
    assert plan.presentation_mode == "executive_forecast_memo"


def test_hermes_discovers_unmapped_database_capability_safely():
    plan = build_hermes_plan("Which contract detail records exist for cracked C5 this month?")

    assert "database" in plan.domains
    assert "database_capability_discovery" in plan.capabilities
    assert plan.privacy["external_llm_blocked"] == "true"
    assert plan.presentation_mode == "capability_discovery"


def test_hermes_routes_customer_sales_report_to_customer_presenter():
    plan = build_hermes_plan("输出客户销售报告，识别增长点、流失风险和下一步动作。")

    assert "erp" in plan.domains
    assert "erp_customer_sales" in plan.capabilities
    assert plan.privacy["external_llm_blocked"] == "true"
    assert plan.presentation_mode == "erp_business_summary"


def test_hermes_privacy_enforcement_blocks_external_finalizer():
    from backend import server

    private_plan = build_hermes_plan("输出客户销售报告，识别增长点、流失风险和下一步动作。").to_dict()
    enhancement = server._apply_hermes_privacy_to_enhancement({}, private_plan)

    assert enhancement["external_llm_blocked"] == "true"
    assert enhancement["skip_finalizer"] == "true"
    assert enhancement["hermes_privacy_enforced"] == "true"
    assert server._finalizer_allowed_by_privacy(True, {}, private_plan) is False

    market_plan = {
        "domains": ["market", "forecast"],
        "privacy": {"external_llm_blocked": "false"},
    }

    assert server._finalizer_allowed_by_privacy(True, {}, market_plan) is True


def test_hermes_mixed_market_forecast_uses_forecast_scenario_presenter():
    plan = build_hermes_plan("裂解C5未来30天市场价格预测怎么看？")

    assert "market" in plan.domains
    assert "forecast" in plan.domains
    assert "cracked_c5" in plan.product_ids
    assert "30d" in plan.forecast_policy["horizons"]
    assert plan.presentation_mode == "forecast_scenarios"


def test_hermes_routes_docx_and_presentation_export_skills():
    plan = build_hermes_plan("Create a DOCX and PPTX executive report for pentane blowing agent sales.")

    assert "document" in plan.domains
    assert "presentation" in plan.domains
    assert "report_export_docx" in plan.capabilities
    assert "report_export_presentation" in plan.capabilities
    assert plan.presentation_mode == "multi_format_report_export"
    assert "docx" in plan.agent_skills
    assert "minimax-docx" in plan.agent_skills
    assert "presentation-creator" in plan.agent_skills
    assert "html-ppt" in plan.agent_skills
    assert "pptx-generator" in plan.agent_skills
    assert "export_format_declared" in plan.validation_gates


def test_hermes_routes_frontend_and_mysql_agent_skills():
    ui_plan = build_hermes_plan("Improve the dashboard UI frontend design for executive users.")
    db_plan = build_hermes_plan("Review MySQL indexes and EXPLAIN for slow ERP order queries.")

    assert "frontend" in ui_plan.domains
    assert ui_plan.presentation_mode == "frontend_design_brief"
    assert "frontend-design" in ui_plan.agent_skills
    assert "mysql" in db_plan.agent_skills


def test_hermes_execution_brief_instructs_chat_prompt_to_use_agents_and_skills():
    from backend.src.engine.decision_graph import format_hermes_execution_brief

    plan = build_hermes_plan("Create a DOCX and PPTX executive report for pentane blowing agent sales.")
    brief = format_hermes_execution_brief(plan.to_dict())

    assert "HERMES EXECUTION BRIEF" in brief
    for skill in (
        "docx",
        "minimax-docx",
        "presentation-creator",
        "html-ppt",
        "pptx-generator",
    ):
        assert skill in brief
    assert "multi_format_report_export" in brief
    assert "export-ready report blocks" in brief
    assert "do not invent chart values" in brief


def test_hermes_routes_new_minimax_and_converter_skills():
    markdown_plan = build_hermes_plan("Convert this executive report to Markdown and preserve tables.")
    vision_plan = build_hermes_plan("Analyze this screenshot and explain the dashboard issue.")
    research_plan = build_hermes_plan("Write an industry research report on C5 resin market demand.")

    assert "markdown-converter" in markdown_plan.agent_skills
    assert "vision-analysis" in vision_plan.agent_skills
    assert "edia-market-research" in research_plan.agent_skills


def test_hermes_agent_loop_executes_safe_observations_for_prompt():
    from backend.src.hermes.execution import execute_hermes_plan, format_hermes_observations_for_prompt

    plan = build_hermes_plan("Create a DOCX and PPTX executive report for pentane blowing agent sales.").to_dict()
    result = execute_hermes_plan(plan, user_query=plan["user_query"])
    prompt_block = format_hermes_observations_for_prompt(result)

    assert result["enabled"] is True
    assert result["observation_count"] >= 1
    assert "agent_skill_router" in prompt_block
    assert "docx" in prompt_block
    assert "presentation-creator" in prompt_block
    assert "HERMES TOOL OBSERVATIONS" in prompt_block


def test_chat_prompt_includes_hermes_tool_observations_for_agentic_context():
    from types import SimpleNamespace

    from backend.src.engine import decision_graph
    from backend.src.engine.prompt_assembler import PromptAssembler
    from backend.src.nlu.types import TaskType

    class _UserContext(SimpleNamespace):
        def to_dict(self):
            return dict(self.__dict__)

    plan = build_hermes_plan("Create a DOCX and PPTX executive report for pentane blowing agent sales.").to_dict()
    node = decision_graph._make_assemble_prompt(PromptAssembler())
    result = node(
        {
            "user_query": plan["user_query"],
            "conversation_history": [],
            "user_context": _UserContext(product=None, period=None, additional_info=""),
            "parsed_intent": SimpleNamespace(task_type=TaskType.CONVERSATIONAL),
            "user_prefs": {"language": "en"},
            "response_mode": "conversational",
            "hermes_plan": plan,
            "tool_info": "",
        }
    )

    assert "HERMES TOOL OBSERVATIONS" in result["user_prompt"]
    assert "agent_skill_router" in result["user_prompt"]
    assert result["prompt_stats"]["hermes_observation_count"] >= 1
    assert "agent_skill_router" in result["prompt_stats"]["hermes_observation_tools"]


def test_hermes_plan_endpoint_returns_structured_forecast_plan(monkeypatch):
    import pytest

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/hermes/plan",
            json={"message": "老板想看戊烷发泡剂下周价格预测和销售风险。"},
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    payload = response.json()
    assert "forecast" in payload["domains"]
    assert "forecast_orchestration" in payload["capabilities"]
    assert payload["forecast_policy"]["policy_mode"] == "adaptive"
    assert "forecast_numbers_locked_before_llm" in payload["forecast_policy"]["quality_gates"]


def test_chat_stream_done_payload_includes_hermes_plan_metadata(monkeypatch):
    import pytest

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)
    captured_prepare_kwargs = {}

    def _prepare_request(*args, **kwargs):
        captured_prepare_kwargs.update(kwargs)
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "resolved_chat_mode": "analysis",
            "mode_reason": "unit_test",
            "grounding_source": "",
        }

    monkeypatch.setattr(server.decision_service, "prepare_request", _prepare_request)
    monkeypatch.setattr(
        server.decision_service.llm,
        "generate_completion_stream",
        lambda *args, **kwargs: iter(["最终答案：Hermes metadata smoke test。"]),
    )

    message = "Boss wants pentane blowing agent next week outlook and market risk."
    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": message,
                "prefs": {"language": "zh"},
                "chat_mode": "analysis",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    payload = _sse_event_payload(response.text, "done")
    hermes_plan = payload["context"]["_stream"]["hermes_plan"]

    assert hermes_plan["user_query"] == message
    assert "forecast" in hermes_plan["domains"]
    assert "executive" in hermes_plan["domains"]
    assert "blowing_agent" in hermes_plan["product_ids"]
    assert "forecast_orchestration" in hermes_plan["capabilities"]
    assert hermes_plan["forecast_policy"]["policy_mode"] == "adaptive"
    assert "forecast_numbers_locked_before_llm" in hermes_plan["forecast_policy"]["quality_gates"]
    assert captured_prepare_kwargs["hermes_plan"] == hermes_plan


def test_chat_stream_skill_trace_persists_to_session_history(monkeypatch):
    import pytest

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)
    monkeypatch.setenv("EDIA_HERMES_LITE_STREAM", "false")
    monkeypatch.setattr(server, "_try_erp_facts_then_kimi_stream_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_is_deterministic_erp_stream_request", lambda *args, **kwargs: False)
    monkeypatch.setattr(server, "_harvest_forced_synthesis_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_chat_stream_prepare_force_synthesis_seconds", lambda: 9999.0)
    monkeypatch.setattr(server, "_build_fast_start_preview", lambda *args, **kwargs: "")

    monkeypatch.setattr(
        server.decision_service,
        "prepare_request",
        lambda *args, **kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "resolved_chat_mode": "analysis",
            "mode_reason": "unit_test",
            "grounding_source": "",
        },
    )
    monkeypatch.setattr(
        server.decision_service.llm,
        "generate_completion_stream",
        lambda *args, **kwargs: iter(["MySQL index and EXPLAIN review ready."]),
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": "Review MySQL indexes and EXPLAIN for slow ERP order queries.",
                "prefs": {"language": "en"},
                "chat_mode": "analysis",
            },
            headers=_AUTH_HEADERS,
        )

        assert response.status_code == 200
        payload = _sse_event_payload(response.text, "done")
        skill_trace = payload["context"]["skill_trace"]

        assert skill_trace["route"] == "edia"
        assert "mysql" in skill_trace["selected_skills"]
        assert "database" in skill_trace["domains"]
        assert "mysql" in payload["context"]["_stream"]["hermes_plan"]["agent_skills"]

        history = client.get(
            f"/api/sessions/{payload['session_id']}",
            headers=_AUTH_HEADERS,
        )

    assert history.status_code == 200
    assistant_messages = [
        message for message in history.json() if message.get("role") == "assistant"
    ]
    assert "mysql" in assistant_messages[-1]["context"]["skill_trace"]["selected_skills"]


def test_chat_stream_private_erp_order_details_reports_deterministic_route(monkeypatch):
    import pytest

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": "请提供 5 月 11 日至 5 月 12 日的订单详情。",
                "prefs": {"language": "zh"},
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    payload = _sse_event_payload(response.text, "done")

    assert payload["task_type"] == "erp_data_query"
    assert payload["llm_route"] == "deterministic"
    assert payload["enhancement_applied"]["external_llm_blocked"] == "true"
    assert payload["enhancement_applied"]["skip_finalizer"] == "true"
    assert payload["context"]["_stream"]["hermes_plan"]["privacy"]["external_llm_blocked"] == "true"


def test_deterministic_erp_shortcut_does_not_capture_forecast_or_export_requests():
    from backend import server

    assert server._is_deterministic_erp_stream_request("请提供 5 月 11 日至 5 月 12 日的订单详情。") is True
    assert server._is_deterministic_erp_stream_request("老板想看戊烷发泡剂下周价格预测和销售风险。") is False
    assert (
        server._is_deterministic_erp_stream_request(
            "Create a DOCX and PPTX executive report for pentane blowing agent sales."
        )
        is False
    )
    assert (
        server._is_deterministic_erp_stream_request(
            "Convert this executive report to Markdown and preserve tables."
        )
        is False
    )
    assert (
        server._is_deterministic_erp_stream_request(
            "Review MySQL indexes and EXPLAIN for slow ERP order queries."
        )
        is False
    )
    assert (
        server._is_deterministic_erp_stream_request(
            "Based on the China market database, what changed week over week for Cracked C5 and isoprene?"
        )
        is False
    )
    assert (
        server._is_deterministic_erp_stream_request(
            "请基于中国市场数据库，说明本周裂解C5和异戊二烯的主流价格变化，以及周环比方向。"
        )
        is False
    )


def test_unmapped_database_fallback_respects_hermes_agent_skills():
    from backend.src.engine.decision_graph import _build_unmapped_database_capability_report

    assert (
        _build_unmapped_database_capability_report(
            {
                "user_query": "Review MySQL indexes and EXPLAIN for slow ERP order queries.",
                "hermes_plan": {
                    "domains": ["database", "erp"],
                    "agent_skills": ["mysql"],
                },
            }
        )
        == ""
    )


def test_customer_sales_report_formatter_is_boss_grade():
    markdown = format_customer_sales_report(
        {
            "period_start": "2026-05-11",
            "period_end": "2026-05-14",
            "previous_start": "2026-05-07",
            "previous_end": "2026-05-10",
            "period_days": 4,
            "product_label": "全部产品",
            "total_qty": 120.0,
            "total_revenue": 900000.0,
            "previous_total_qty": 150.0,
            "previous_total_revenue": 1000000.0,
            "rows": [
                {
                    "customer": "客户A",
                    "qty_ton": 80.0,
                    "orders": 2,
                    "revenue_cny": 600000.0,
                    "previous_qty_ton": 60.0,
                    "previous_revenue_cny": 430000.0,
                    "qty_change_pct": 33.3,
                }
            ],
            "growth": [
                {
                    "customer": "客户A",
                    "qty_ton": 80.0,
                    "revenue_cny": 600000.0,
                    "qty_change_pct": 33.3,
                }
            ],
            "risk": [
                {
                    "customer": "客户B",
                    "previous_qty_ton": 40.0,
                    "previous_revenue_cny": 300000.0,
                    "qty_change_pct": -100.0,
                    "is_missing_repeat": True,
                }
            ],
        }
    )

    assert "## 01 · 管理层结论" in markdown
    assert "## 03 · 客户增长点" in markdown
    assert "## 04 · 流失/下滑风险" in markdown
    assert "客户A" in markdown
    assert "客户B" in markdown
    assert "短期预警" in markdown


def test_chat_stream_customer_sales_uses_deterministic_customer_report(monkeypatch):
    import pytest

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs
    import backend.src.tools.erp_tools as erp_tools

    server = _import_server_with_stubs(monkeypatch)

    def _fail_process_request(*args, **kwargs):
        raise AssertionError("customer sales should use deterministic stream branch")

    monkeypatch.setattr(server.decision_service, "process_request", _fail_process_request)
    monkeypatch.setattr(
        erp_tools,
        "build_customer_sales_report_markdown",
        lambda *args, **kwargs: "## 01 · 管理层结论\n\n客户销售报告已生成。\n\n## 04 · 流失/下滑风险\n\n客户B需要跟进。",
    )

    def _fail_finalizer(*args, **kwargs):
        raise AssertionError("Hermes-private ERP answer must not use external finalizer")

    monkeypatch.setattr(server, "_finalize_with_kimi_sync", _fail_finalizer)
    monkeypatch.setattr(server, "_finalize_with_kimi_stream", _fail_finalizer)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": "输出客户销售报告，识别增长点、流失风险和下一步动作。",
                "prefs": {"language": "zh", "final_kimi_hook": True},
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    payload = _sse_event_payload(response.text, "done")

    assert payload["task_type"] == "erp_data_query"
    assert payload["llm_route"] == "deterministic"
    assert payload["enhancement_applied"]["database_capability"] == "erp_customer_sales"
    assert payload["enhancement_applied"]["deterministic_erp_customer_sales"] == "true"
    assert payload["enhancement_applied"]["hermes_privacy_enforced"] == "true"
    assert payload["context"]["_stream"]["display_markdown_source"] == "deterministic_erp_customer_sales"
    assert payload["context"]["_stream"]["hermes_plan"]["privacy"]["external_llm_blocked"] == "true"
    assert "客户销售报告已生成" in payload["markdown"]