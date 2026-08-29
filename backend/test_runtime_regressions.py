from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from backend.src.intelligence.event_types import ExtractedEvent
from backend.src.service.kimi_forecast_feature_service import (
    FORECAST_FEATURE_NODE_IDS,
    KimiForecastFeatureService,
)
from backend.src.service.alert_service import AlertDB, _detect_arbitrage_alerts
from backend.src.service.fx_rate_service import build_fx_rate_payload
from backend.src.skills.background.regulatory_monitor import RegulatoryPolicyMonitor
from backend.src.hermes.erp_grounding import (
    erp_hybrid_synthesis_enabled,
    looks_like_erp_access_denial,
)


def test_erp_access_denial_patterns_detect_refusal_text() -> None:
    assert looks_like_erp_access_denial(
        "I don't have access to your ERP system or any internal database live data."
    )
    assert not looks_like_erp_access_denial("ERP sales summary for May 2026: total revenue 12.3M CNY.")
    assert erp_hybrid_synthesis_enabled() is True


def test_erp_kimi_synthesis_replaces_denial_with_facts(monkeypatch) -> None:
    from backend.src.hermes.erp_grounding import build_erp_kimi_synthesis_prompts, guard_erp_markdown_against_denial

    facts = "Total revenue: 12,345,678 CNY for May 2026."
    denial = "I don't have access to your ERP system."
    guarded = guard_erp_markdown_against_denial(denial, facts, has_erp_data=True)
    assert guarded == facts
    _system, user = build_erp_kimi_synthesis_prompts("erp report", facts, language="en")
    assert facts in user
    assert "don't have access" not in _system.lower()


def test_alert_record_sanitizes_missing_numeric_fields() -> None:
    record = AlertDB._sanitize_alert_record(
        {
            "id": "alert-1",
            "product": "isoprene",
            "product_name": "isoprene",
            "alert_type": "regional_arbitrage",
            "severity": "high",
            "change_7d_pct": None,
            "price": "16000",
            "message": "spread alert",
            "triggered_at": "2026-05-07T14:59:41.816909",
        }
    )

    assert record["change_7d_pct"] == 0.0
    assert record["price"] == 16000.0


def test_detect_arbitrage_alerts_emits_non_null_change_pct(monkeypatch) -> None:
    import backend.src.service.market_data_service as market_data_module

    captured: list[dict] = []

    class StubAlertDB:
        def recent_alert_exists(self, product: str, alert_type: str, window_days: int = 7) -> bool:
            return False

        def insert_alert(self, alert: dict) -> None:
            captured.append(dict(alert))

    monkeypatch.setattr(
        market_data_module.market_data,
        "get_regional_prices",
        lambda product_id: {
            "east_china": 13875.0,
            "south_china": 16000.0,
        },
    )

    alerts = _detect_arbitrage_alerts(StubAlertDB())

    assert alerts
    assert captured
    assert all(alert["change_7d_pct"] == 0.0 for alert in alerts)
    assert all(alert["change_7d_pct"] == 0.0 for alert in captured)


def test_regulatory_monitor_accepts_extracted_event_instances(monkeypatch) -> None:
    import backend.src.intelligence.event_registry as event_registry_module

    event = ExtractedEvent(
        event_type="tariff_policy",
        headline="China adjusts export tariff on C5 streams",
        affected_commodities=["cracked_c5", "piperylene"],
        direction="bullish",
        magnitude_estimate="large",
        certainty="confirmed",
        time_horizon="short",
        geographic_scope="china",
        relevance_to_c5_c9="direct",
        full_text="Tariff adjustment raises the cost of outbound C5 flows.",
    )

    monkeypatch.setattr(
        event_registry_module.intelligence_event_registry,
        "list_recent_events",
        lambda hours=72, limit=50: [event],
    )

    result = RegulatoryPolicyMonitor().run()

    assert result.status == "success"
    assert result.data["policy_events"]
    assert result.data["policy_events"][0]["event_id"] == event.event_id
    assert result.data["policy_events"][0]["headline"] == event.headline
    assert result.alerts
    assert "tariff" in result.alerts[0].lower()


def test_build_fx_rate_payload_marks_missing_rate_as_unavailable() -> None:
    payload = build_fx_rate_payload(None)

    assert payload["price"] is None
    assert payload["as_of_date"] is None
    assert payload["available"] is False
    assert payload["unit"] == "USD/CNY"


def test_build_fx_rate_payload_formats_present_rate() -> None:
    payload = build_fx_rate_payload(("2026-05-07", "usd_cny", "spot", 7.23))

    assert payload["price"] == 7.23
    assert payload["as_of_date"] == "2026-05-07"
    assert payload["series_product_id"] == "usd_cny"
    assert payload["series_price_type"] == "spot"
    assert payload["available"] is True


def test_kimi_feature_normaliser_fills_all_forecast_nodes() -> None:
    service = KimiForecastFeatureService(llm_service=None)
    features = service.normalise_features(
        {
            "global_market_regime": "mixed",
            "global_risk_score": 1.5,
            "products": {
                "isoprene": {
                    "supply_disruption_score": 0.7,
                    "directional_bias_pct": 12.0,
                    "horizon_bias_pct": {"1d": 0.4, "7d": -9.0, "30d": 0.6},
                    "confidence": 0.74,
                    "evidence_event_ids": ["evt-1"],
                    "reason_codes": ["feedstock_pressure"],
                }
            },
        }
    )

    assert set(features["products"]) == set(FORECAST_FEATURE_NODE_IDS)
    assert features["global_risk_score"] == 1.0
    assert features["products"]["isoprene"]["directional_bias_pct"] == 8.0
    assert features["products"]["isoprene"]["horizon_bias_pct"]["7d"] == -8.0
    assert features["products"]["isoprene"]["confidence"] == 0.74
    assert features["products"]["naphtha"]["confidence"] == 0.0


def test_kimi_feature_refresh_falls_back_when_llm_unavailable() -> None:
    class BrokenLLM:
        model = "broken"
        external_model = "kimi-test"

        def generate_json(self, *args, **kwargs):
            raise RuntimeError("offline")

    service = KimiForecastFeatureService(llm_service=BrokenLLM())
    snapshot = service.refresh_snapshot()

    assert snapshot["status"] == "fallback"
    assert snapshot["features"]["products"]["isoprene"]["confidence"] == 0.0
    assert "kimi_unavailable" in snapshot["features"]["products"]["isoprene"]["reason_codes"]


NAPHTHA_QUERY = (
    "Naphtha 7d increase +8.0% (current 993.25 CNY/ton). "
    "What is the cost pass-through impact on our C5/C9 product line and pricing strategy? "
    "Should we adjust quotes now?"
)


def test_chat_stream_emits_at_most_one_quick_take_preview(monkeypatch) -> None:
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)
    preview = "## Quick Take\n\n- Scope: test\n- Current status: grounding\n\n"

    monkeypatch.setattr(server, "_build_fast_start_preview", lambda *args, **kwargs: preview)
    monkeypatch.setattr(server, "_local_fast_start_target_seconds", lambda: 0.01)
    monkeypatch.setattr(server, "_try_fast_naphtha_passthrough_stream_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_try_fast_ops_inventory_stream_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_chat_stream_prepare_force_synthesis_seconds", lambda: 600.0)
    monkeypatch.setattr(server, "_chat_stream_prepare_timeout_seconds", lambda: 600.0)
    monkeypatch.setattr(server, "_chat_stream_prepare_hard_timeout_seconds", lambda: 600.0)

    def _fast_prepare_request(*args, **kwargs):
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "resolved_chat_mode": "analysis",
            "mode_reason": "unit_test",
            "grounding_source": "",
        }

    def _slow_stream(*args, **kwargs):
        time.sleep(0.15)
        yield "Final grounded answer with pricing table."

    monkeypatch.setattr(server.decision_service, "prepare_request", _fast_prepare_request)
    monkeypatch.setattr(
        server.decision_service.llm,
        "generate_completion_stream",
        _slow_stream,
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": NAPHTHA_QUERY,
                "prefs": {"language": "en"},
                "chat_mode": "analysis",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.text
    delta_quick_takes = [
        line
        for line in body.split("\n")
        if line.startswith("data:") and "## Quick Take" in line and '"delta"' in line
    ]
    assert len(delta_quick_takes) <= 1
    assert "Final grounded answer with pricing table." in body
    assert "event: done" in body


def test_chat_stream_forces_synthesis_without_preview(monkeypatch) -> None:
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)

    monkeypatch.setattr(server, "_build_fast_start_preview", lambda *args, **kwargs: "")
    monkeypatch.setattr(server, "_local_fast_start_target_seconds", lambda: 0.01)
    monkeypatch.setattr(server, "_chat_stream_prepare_force_synthesis_seconds", lambda: 0.05)
    monkeypatch.setattr(server, "_chat_stream_prepare_timeout_seconds", lambda: 5.0)
    monkeypatch.setattr(server, "_chat_stream_prepare_hard_timeout_seconds", lambda: 6.0)

    def _slow_prepare_request(*args, **kwargs):
        time.sleep(1.2)
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "resolved_chat_mode": "analysis",
            "mode_reason": "forced_synthesis_after_prepare_dwell",
            "grounding_source": "",
        }

    monkeypatch.setattr(server.decision_service, "prepare_request", _slow_prepare_request)
    monkeypatch.setattr(
        server.decision_service.llm,
        "generate_completion_stream",
        lambda *args, **kwargs: iter(["最终答案：这是强制综合后的回复。"]),
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": "请基于本周中国市场数据库，生成一份高层周报。",
                "prefs": {"language": "zh"},
                "chat_mode": "analysis",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.text
    assert "event: done" in body
    assert "最终答案：这是强制综合后的回复。" in body
    assert "Preparing analysis is taking longer than expected." not in body


def test_chat_stream_emits_keepalive_during_slow_local_stream(monkeypatch) -> None:
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)
    monkeypatch.setenv("EDIA_HERMES_LITE_STREAM", "false")
    monkeypatch.setattr(server, "_try_erp_facts_then_kimi_stream_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_is_deterministic_erp_stream_request", lambda *args, **kwargs: False)

    monkeypatch.setattr(server, "_build_fast_start_preview", lambda *args, **kwargs: "预览：仍在生成。")
    monkeypatch.setattr(server, "_local_fast_start_target_seconds", lambda: 0.01)
    monkeypatch.setattr(server, "_chat_stream_keepalive_seconds", lambda: 0.05)

    def _fast_prepare_request(*args, **kwargs):
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "resolved_chat_mode": "analysis",
            "mode_reason": "unit_test",
            "grounding_source": "",
        }

    def _slow_stream(*args, **kwargs):
        time.sleep(0.35)
        yield "最终答案：慢流式回复。"

    monkeypatch.setattr(server.decision_service, "prepare_request", _fast_prepare_request)
    monkeypatch.setattr(
        server.decision_service.llm,
        "generate_completion_stream",
        _slow_stream,
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": "请详细分析最近一周SIS销售情况。",
                "prefs": {"language": "zh"},
                "chat_mode": "analysis",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.text
    assert ": keep-alive" in body
    assert "最终答案：慢流式回复。" in body
    assert "event: done" in body


def test_chat_stream_returns_partial_before_total_budget(monkeypatch) -> None:
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)

    monkeypatch.setattr(server, "_build_fast_start_preview", lambda *args, **kwargs: "预览：系统仍在整理分析。")
    monkeypatch.setattr(server, "_local_fast_start_target_seconds", lambda: 0.01)
    monkeypatch.setattr(server, "_chat_stream_keepalive_seconds", lambda: 0.05)
    monkeypatch.setattr(server, "_chat_stream_total_budget_seconds", lambda: 0.15)

    def _fast_prepare_request(*args, **kwargs):
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "resolved_chat_mode": "analysis",
            "mode_reason": "unit_test",
            "grounding_source": "",
            "parsed_intent": SimpleNamespace(task_type=SimpleNamespace(value="analysis")),
        }

    def _very_slow_stream(*args, **kwargs):
        time.sleep(0.4)
        yield "最终答案：这条内容不应在预算内返回。"

    monkeypatch.setattr(server.decision_service, "prepare_request", _fast_prepare_request)
    monkeypatch.setattr(
        server.decision_service.llm,
        "generate_completion_stream",
        _very_slow_stream,
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "message": "请做一个很慢的测试分析。",
                "prefs": {"language": "zh"},
                "chat_mode": "analysis",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    body = response.text
    assert "event: done" in body
    assert "stream_budget_partial" in body
    assert "预览：系统仍在整理分析。" in body