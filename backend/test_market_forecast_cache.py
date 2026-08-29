import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import backend.server as server


def setup_function():
    with server._market_forecast_cache_lock:
        server._market_forecast_cache.clear()


def test_market_forecast_cache_hit_with_matching_fingerprint(monkeypatch):
    fingerprint = {"market_prices": {"rows": 10, "latest_ts": "2026-04-08"}}
    payload = {"product_id": "naphtha", "forecast": [{"date": "2026-04-09", "base": 1.0}]}

    monkeypatch.setattr(server.time, "time", lambda: 100.0)
    server._store_market_forecast_cache("naphtha", payload, fingerprint)

    monkeypatch.setattr(server.time, "time", lambda: 120.0)
    cached = server._get_cached_market_forecast("naphtha", fingerprint)

    assert cached == payload
    assert cached is not payload


def test_market_forecast_cache_expires_after_ttl(monkeypatch):
    fingerprint = {"market_prices": {"rows": 10, "latest_ts": "2026-04-08"}}
    payload = {"product_id": "naphtha", "forecast": []}

    monkeypatch.setattr(server.time, "time", lambda: 100.0)
    server._store_market_forecast_cache("naphtha", payload, fingerprint)

    monkeypatch.setattr(server.time, "time", lambda: 191.0)
    cached = server._get_cached_market_forecast("naphtha", fingerprint)

    assert cached is None
    assert "naphtha" not in server._market_forecast_cache


def test_market_forecast_cache_invalidates_when_fingerprint_changes(monkeypatch):
    cached_fingerprint = {"market_prices": {"rows": 10, "latest_ts": "2026-04-08"}}
    current_fingerprint = {"market_prices": {"rows": 11, "latest_ts": "2026-04-09"}}
    payload = {"product_id": "naphtha", "forecast": []}

    monkeypatch.setattr(server.time, "time", lambda: 100.0)
    server._store_market_forecast_cache("naphtha", payload, cached_fingerprint)

    monkeypatch.setattr(server.time, "time", lambda: 110.0)
    cached = server._get_cached_market_forecast("naphtha", current_fingerprint)

    assert cached is None
    assert "naphtha" not in server._market_forecast_cache


def test_market_forecast_route_refreshes_runtime_before_cache(monkeypatch):
    calls: list[bool] = []
    payload = {"product_id": "naphtha", "forecast": []}

    def fake_ensure_market_runtime_fresh(force: bool = False):
        calls.append(force)

    monkeypatch.setattr(server, "_ensure_market_runtime_fresh", fake_ensure_market_runtime_fresh)
    monkeypatch.setattr(server, "_current_market_fingerprint", lambda: {"market_prices": {"rows": 10}})
    monkeypatch.setattr(server, "_build_market_forecast_intelligence_context", lambda product_id: {"has_intelligence": False, "events": []})
    monkeypatch.setattr(server, "_build_market_forecast_intelligence_fingerprint", lambda product_id, ctx: {"active": False, "event_ids": []})
    monkeypatch.setattr(server, "_get_cached_market_forecast", lambda product_id, market_fingerprint: payload)

    result = asyncio.run(server.get_market_forecast("naphtha"))

    assert result == payload
    assert calls == [False]


def test_market_forecast_route_returns_stable_output_within_cache_ttl(monkeypatch):
    calls: list[str] = []
    now = {"value": 100.0}
    fingerprint = {"market_prices": {"rows": 10, "latest_ts": "2026-04-29"}}
    payload = {
        "product_id": "naphtha",
        "forecast": [{"date": "2026-04-30", "base": 5100.0, "bull": 5200.0, "bear": 5000.0}],
    }

    def fake_compute_market_forecast(product_id: str, intelligence_context=None, intelligence_fingerprint=None, market_fingerprint=None):
        calls.append(product_id)
        return payload

    monkeypatch.setattr(server.time, "time", lambda: now["value"])
    monkeypatch.setattr(server, "_ensure_product_allowed", lambda product_id: None)
    monkeypatch.setattr(server, "_ensure_market_runtime_fresh", lambda: None)
    monkeypatch.setattr(server, "_current_market_fingerprint", lambda: fingerprint)
    monkeypatch.setattr(server, "_build_market_forecast_intelligence_context", lambda product_id: {"has_intelligence": False, "events": []})
    monkeypatch.setattr(server, "_build_market_forecast_intelligence_fingerprint", lambda product_id, ctx: {"active": False, "event_ids": []})
    monkeypatch.setattr(server, "_compute_market_forecast", fake_compute_market_forecast)
    monkeypatch.setattr(server, "_log_market_timing", lambda *args, **kwargs: None)

    first = asyncio.run(server.get_market_forecast("naphtha"))
    now["value"] = 120.0
    second = asyncio.run(server.get_market_forecast("naphtha"))

    assert first == second
    assert calls == ["naphtha"]


def test_market_forecast_cache_invalidates_when_intelligence_fingerprint_changes(monkeypatch):
    market = {"market_prices": {"rows": 10, "latest_ts": "2026-04-08"}}
    cached_fingerprint = server._compose_market_forecast_fingerprint(
        market,
        {"active": True, "event_ids": ["evt-1"], "event_keys": ["evt-1"]},
    )
    current_fingerprint = server._compose_market_forecast_fingerprint(
        market,
        {"active": True, "event_ids": ["evt-2"], "event_keys": ["evt-2"]},
    )
    payload = {"product_id": "naphtha", "forecast": []}

    monkeypatch.setattr(server.time, "time", lambda: 100.0)
    server._store_market_forecast_cache("naphtha", payload, cached_fingerprint)

    monkeypatch.setattr(server.time, "time", lambda: 110.0)
    cached = server._get_cached_market_forecast("naphtha", current_fingerprint)

    assert cached is None
    assert "naphtha" not in server._market_forecast_cache


def test_snapshot_market_fingerprint_rejects_stale_forecast_start():
    stale_snapshot = {
        "history": [{"date": "2026-05-11", "price": 100.0}],
        "forecast_path": [{"date": "2026-05-07", "base": 101.0}],
        "forecast_snapshot": {"intelligence_fingerprint": {"active": False}},
    }
    fresh_snapshot = {
        "history": [{"date": "2026-05-11", "price": 100.0}],
        "forecast_path": [{"date": "2026-05-12", "base": 101.0}],
        "forecast_snapshot": {"intelligence_fingerprint": {"active": False}},
    }
    fingerprint_snapshot = {
        "history": [{"date": "2026-05-01", "price": 100.0}],
        "forecast_path": [{"date": "2026-05-02", "base": 101.0}],
        "forecast_snapshot": {"market_fingerprint": {"market_prices": {"latest_ts": "2026-05-11"}}},
    }

    assert server._snapshot_matches_market_fingerprint(stale_snapshot, {}) is False
    assert server._snapshot_matches_market_fingerprint(fresh_snapshot, {}) is True
    assert server._snapshot_matches_market_fingerprint(
        fingerprint_snapshot,
        {"market_prices": {"latest_ts": "2026-05-11"}},
    ) is True
    assert server._snapshot_matches_market_fingerprint(
        fingerprint_snapshot,
        {"market_prices": {"latest_ts": "2026-05-12"}},
    ) is False


def test_compute_market_forecast_applies_event_intelligence_metadata(monkeypatch):
    import backend.src.db.daily_calibration as daily_calibration
    import backend.src.db.forecast_snapshots as forecast_snapshots
    import backend.src.service.forecast_event_routing as forecast_event_routing
    import backend.src.service.forecasting_engine as forecasting_engine

    history = []
    start = date(2026, 3, 1)
    for idx in range(80):
        history.append(
            {
                "date": (start + timedelta(days=idx)).isoformat(),
                "price": 100.0 + idx,
            }
        )

    event = {
        "event_id": "evt-1",
        "headline": "Hormuz closure",
        "detected_at": "2026-05-07T12:00:00",
        "source_url": "https://example.com/hormuz",
        "review_status": "pending",
        "pct_impact": 4.0,
        "cny_impact": 200,
    }

    monkeypatch.setattr(server.market_data, "get_price_history", lambda product_id: history)
    monkeypatch.setattr(
        server.forecasting_service,
        "forecast",
        lambda product_id, intelligence_context=None: {
            "scenarios": {
                "7d": {
                    "base": {"low": 190.0, "high": 194.0},
                    "bull": {"low": 197.0, "high": 201.0},
                    "bear": {"low": 184.0, "high": 188.0},
                },
                "30d": {
                    "base": {"low": 215.0, "high": 219.0},
                    "bull": {"low": 225.0, "high": 230.0},
                    "bear": {"low": 205.0, "high": 210.0},
                },
            },
            "seasonal_adj_pct": 0.0,
            "policy": {},
            "model_version": "test-v1",
            "intelligence_signal": {
                "active": True,
                "bias": "bullish",
                "confidence": 0.8,
                "event_count": 1,
                "net_price_pct": 4.0,
                "net_price_cny": 200,
                "dominant_event": event,
                "events": [event],
            },
            "intelligence_adj_pct": {"3d": 2.4, "7d": 3.2, "30d": 2.4},
        },
    )
    monkeypatch.setattr(
        forecasting_engine,
        "get_price_series_quality",
        lambda product_id: {
            "raw_observations": 80,
            "model_window_days": 80,
            "ffill_ratio": 0.0,
            "low_data": False,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        forecasting_engine,
        "get_cadence_info",
        lambda product_id: {
            "cadence_class": "daily-capable",
            "change_frequency": 0.8,
            "native_step_days": 1,
            "daily_forecast_advised": True,
            "display_preferred_series": "forecast",
        },
    )
    monkeypatch.setattr(
        forecasting_engine,
        "get_native_baseline_forecast_path",
        lambda product_id, horizon_days, start_date: {
            "status": "ok",
            "source": "arima_native",
            "message": "ok",
            "dense_path": [
                {
                    "date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "forecast_date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "step_number": idx + 1,
                    "baseline_price": 180.0 + idx,
                    "proposed_override_pct": 0.0,
                    "applied_override_pct": 0.0,
                    "final_price": 180.0 + idx,
                    "base": 180.0 + idx,
                    "bull": 180.0 + idx,
                    "bear": 180.0 + idx,
                    "confidence": None,
                    "cause_summary": "Local ARIMA baseline; no event override applied.",
                    "evidence_event_ids": [],
                    "source": "arima_native",
                }
                for idx in range(30)
            ],
            "native_path": [
                {
                    "date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "forecast_date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "step_number": idx + 1,
                    "baseline_price": 180.0 + idx,
                    "proposed_override_pct": 0.0,
                    "applied_override_pct": 0.0,
                    "final_price": 180.0 + idx,
                    "base": 180.0 + idx,
                    "bull": 180.0 + idx,
                    "bear": 180.0 + idx,
                    "confidence": None,
                    "cause_summary": "Local ARIMA baseline; no event override applied.",
                    "evidence_event_ids": [],
                    "source": "arima_native",
                }
                for idx in range(30)
            ],
        },
    )
    monkeypatch.setattr(forecasting_engine, "_BELOW_NAIVE_PRODUCTS", frozenset())
    monkeypatch.setattr(
        forecast_event_routing,
        "resolve_event_adjustment_policy",
        lambda product_id, horizon_days: {
            "enabled": True,
            "registry_hit": True,
            "product_id": product_id,
            "requested_horizon_days": horizon_days,
            "selected_horizon_days": 7,
            "max_abs_adjustment_pct": 3.0,
        },
    )
    monkeypatch.setattr(forecast_snapshots, "get_latest_approved_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(forecast_snapshots, "persist_snapshot", lambda *args, **kwargs: "snap-1")
    monkeypatch.setattr(daily_calibration, "persist_forecast_snapshot", lambda *args, **kwargs: None)

    payload = server._compute_market_forecast(
        "c5_resin",
        intelligence_context={"has_intelligence": True, "events": [event]},
        intelligence_fingerprint={
            "mode": "automatic_non_rejected",
            "hours": 168,
            "event_keys": ["evt-1"],
            "event_ids": ["evt-1"],
            "event_count": 1,
            "latest_detected_at": "2026-05-07T12:00:00",
            "active": True,
        },
    )

    assert payload["forecast_adjustment"]["source"] == "automatic_event_intelligence"
    assert payload["forecast_adjustment"]["evidence_event_ids"] == ["evt-1"]
    assert payload["evidence_event_ids"] == ["evt-1"]
    assert payload["evidence_events"][0]["event_id"] == "evt-1"
    assert payload["forecast_path"][0]["source"] == "automatic_event_intelligence"
    assert payload["forecast_path"][0]["evidence_event_ids"] == ["evt-1"]
    assert payload["forecast_path"][0]["event_signal_present"] is True
    assert payload["forecast_path"][0]["event_routing_applied"] is True
    assert payload["forecast_path"][0]["event_routing_blocked"] is False
    assert payload["forecast_path"][0]["proposed_override_pct"] >= payload["forecast_path"][0]["applied_override_pct"]
    assert payload["forecast_path"][0]["applied_override_pct"] > 0
    assert payload["forecast_path"][0]["base"] > payload["forecast_path"][0]["baseline_price"]
    assert payload["forecast_adjustment"]["raw_horizon_adjustment_pct"]["7d"] > 0
    assert payload["forecast_adjustment"]["event_adjustment_policy"]["enabled"] is True
    assert payload["forecast_snapshot"]["intelligence_fingerprint"]["event_ids"] == ["evt-1"]
    assert "Automatic event analyses" in payload["cause_summary"]


def test_compute_market_forecast_applies_kimi_numeric_adjustment(monkeypatch):
    import backend.src.db.daily_calibration as daily_calibration
    import backend.src.db.forecast_snapshots as forecast_snapshots
    import backend.src.service.forecast_event_routing as forecast_event_routing
    import backend.src.service.forecasting_engine as forecasting_engine

    history = []
    start = date(2026, 3, 1)
    for idx in range(80):
        history.append(
            {
                "date": (start + timedelta(days=idx)).isoformat(),
                "price": 100.0 + idx,
            }
        )

    monkeypatch.setattr(server.market_data, "get_price_history", lambda product_id: history)
    monkeypatch.setattr(
        server.forecasting_service,
        "forecast",
        lambda product_id, intelligence_context=None: {
            "scenarios": {
                "7d": {
                    "base": {"low": 190.0, "high": 194.0},
                    "bull": {"low": 197.0, "high": 201.0},
                    "bear": {"low": 184.0, "high": 188.0},
                },
                "30d": {
                    "base": {"low": 215.0, "high": 219.0},
                    "bull": {"low": 225.0, "high": 230.0},
                    "bear": {"low": 205.0, "high": 210.0},
                },
            },
            "seasonal_adj_pct": 0.0,
            "policy": {},
            "model_version": "test-v1",
            "intelligence_signal": {
                "active": False,
                "bias": "neutral",
                "confidence": None,
                "event_count": 0,
                "net_price_pct": 0.0,
                "net_price_cny": 0,
                "dominant_event": None,
                "events": [],
            },
            "intelligence_adj_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
        },
    )
    monkeypatch.setattr(
        forecasting_engine,
        "get_price_series_quality",
        lambda product_id: {
            "raw_observations": 80,
            "model_window_days": 80,
            "ffill_ratio": 0.0,
            "low_data": False,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        forecasting_engine,
        "get_cadence_info",
        lambda product_id: {
            "cadence_class": "daily-capable",
            "change_frequency": 0.8,
            "native_step_days": 1,
            "daily_forecast_advised": True,
            "display_preferred_series": "forecast",
        },
    )
    monkeypatch.setattr(
        forecasting_engine,
        "get_native_baseline_forecast_path",
        lambda product_id, horizon_days, start_date: {
            "status": "ok",
            "source": "arima_native",
            "message": "ok",
            "dense_path": [
                {
                    "date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "forecast_date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "step_number": idx + 1,
                    "baseline_price": 180.0 + idx,
                    "proposed_override_pct": 0.0,
                    "applied_override_pct": 0.0,
                    "final_price": 180.0 + idx,
                    "base": 180.0 + idx,
                    "bull": 180.0 + idx,
                    "bear": 180.0 + idx,
                    "confidence": None,
                    "cause_summary": "Local ARIMA baseline; no event override applied.",
                    "evidence_event_ids": [],
                    "source": "arima_native",
                }
                for idx in range(30)
            ],
            "native_path": [
                {
                    "date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "forecast_date": (date(2026, 5, 20) + timedelta(days=idx)).isoformat(),
                    "step_number": idx + 1,
                    "baseline_price": 180.0 + idx,
                    "proposed_override_pct": 0.0,
                    "applied_override_pct": 0.0,
                    "final_price": 180.0 + idx,
                    "base": 180.0 + idx,
                    "bull": 180.0 + idx,
                    "bear": 180.0 + idx,
                    "confidence": None,
                    "cause_summary": "Local ARIMA baseline; no event override applied.",
                    "evidence_event_ids": [],
                    "source": "arima_native",
                }
                for idx in range(30)
            ],
        },
    )
    monkeypatch.setattr(
        server.kimi_forecast_feature_service,
        "get_product_features",
        lambda product_id: {
            "snapshot_id": "fs-1",
            "status": "success",
            "generated_at": "2026-05-07T12:00:00",
            "expires_at": "2099-05-07T18:00:00",
            "model": "kimi-test",
            "feature": {
                "confidence": 0.8,
                "reason_codes": ["feedstock_pressure", "cross_node_strength"],
                "evidence_event_ids": ["evt-deep-1"],
                "horizon_bias_pct": {"1d": 4.0, "3d": 4.0, "7d": 2.0, "14d": 1.0, "30d": 0.5},
            },
        },
    )
    monkeypatch.setattr(forecasting_engine, "_BELOW_NAIVE_PRODUCTS", frozenset())
    monkeypatch.setattr(
        forecast_event_routing,
        "resolve_event_adjustment_policy",
        lambda product_id, horizon_days: {
            "enabled": True,
            "registry_hit": True,
            "product_id": product_id,
            "requested_horizon_days": horizon_days,
            "selected_horizon_days": 7,
            "max_abs_adjustment_pct": 3.0,
        },
    )
    monkeypatch.setattr(forecast_snapshots, "get_latest_approved_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(forecast_snapshots, "persist_snapshot", lambda *args, **kwargs: "snap-1")
    monkeypatch.setattr(daily_calibration, "persist_forecast_snapshot", lambda *args, **kwargs: None)

    payload = server._compute_market_forecast(
        "c5_resin",
        intelligence_context={"has_intelligence": False, "events": []},
        intelligence_fingerprint={
            "mode": "automatic_non_rejected",
            "hours": 168,
            "event_keys": [],
            "event_ids": [],
            "event_count": 0,
            "latest_detected_at": None,
            "active": False,
        },
    )

    first_point = payload["forecast_path"][0]
    assert payload["kimi_feature_signal"]["applied_to_numeric_forecast"] is True
    assert payload["kimi_feature_signal"]["integration_stage"] == "deterministic_exogenous_adjustment_active"
    assert payload["forecast_adjustment"]["source"] == "kimi_feature_deterministic_adjustment"
    assert payload["forecast_adjustment"]["kimi_applied_to_numeric_forecast"] is True
    assert payload["forecast_adjustment"]["combined_applied_override_pct"] > 0
    assert payload["evidence_event_ids"] == ["evt-deep-1"]
    assert first_point["kimi_feature_applied"] is True
    assert first_point["kimi_feature_adjustment_pct"] > 0
    assert first_point["final_price"] > first_point["baseline_price"]
    assert payload["forecast_dense"][0]["base"] == first_point["base"]
    assert payload["forecast_generation"]["path_source"].endswith("_kimi_feature_adjusted")


def test_apply_llm_route_defaults_prefers_external_for_analysis(monkeypatch):
    monkeypatch.setenv("DISABLE_LOCAL_LLM", "false")
    monkeypatch.setenv("STRICT_KIMI_ONLY", "false")
    monkeypatch.setenv("CHAT_EXTERNAL_REASONING_DEFAULT", "false")

    resolved = server._apply_llm_route_defaults({}, "analysis")

    assert resolved["llm_route"] == "external"
    assert resolved["final_decision_llm"] == "external"
    assert resolved["kimi_mixed_synthesis"] is True
    assert resolved["external_first_online"] is False
    assert resolved["planner_streaming_synthesis"] is False


def test_pick_stream_display_markdown_prefers_final_markdown():
    preview = "## Quick Take\n\n- Full grounded analysis is still being generated."
    final = "# Final Answer\n\n- This week recommended quote range: **1-2**"

    assert server._pick_stream_display_markdown(preview, final) == final


def test_pick_stream_display_markdown_keeps_detailed_erp_sparse_analysis():
    raw = "\n".join(
        [
            "## 01 · 市场现状",
            "请求区间 2026-05-04 至 2026-05-08 内，SIS 没有ERP销售记录。",
            "该产品可用ERP销售历史范围为 2023-10-07 至 2023-10-07，共 2 条记录。",
            "最接近的可用 5 天窗口是 2023-10-07 至 2023-10-07。",
            "## 02 · 关键驱动因素",
            "当前重点是数据连续性与可验证性，不做无根据单点结论。",
            "## 03 · 价格预测",
            "未来1-2周按看跌/基准/看涨三情景管理，并明确触发条件。",
            "## 04 · 定价决策",
            "建议按库存覆盖与订单恢复节奏分层执行动作。",
            "## 05 · 风险预警",
            "若数据缺口持续，决策滞后风险上升。",
            "## 06 · 建议追问",
            "请确认SIS最近补录时间和渠道覆盖。",
        ]
        * 6
    )
    final = (
        "## ERP数据可用性\n\n"
        "请求区间 2026-05-04 至 2026-05-08 内，SIS 没有ERP销售记录。\n\n"
        "该产品可用ERP销售历史范围为 2023-10-07 至 2023-10-07，共 2 条记录。"
        "最接近的可用 5 天窗口是 2023-10-07 至 2023-10-07。\n\n"
        "我可以直接按这个最近可用窗口展示，也可以按你指定的可用区间重新分析。"
    )

    assert server._pick_stream_display_markdown(raw, final) == raw


def test_resolve_final_llm_route_hint_prefers_local_for_reports(monkeypatch):
    monkeypatch.setenv("DISABLE_LOCAL_LLM", "false")

    route = server._resolve_final_llm_route_hint({"chat_mode": "report"})

    assert route == "local"


def test_build_fast_start_preview_uses_grounded_local_summary(monkeypatch):
    monkeypatch.setenv("STRICT_KIMI_ONLY", "false")
    prep_state = {
        "user_context": SimpleNamespace(product="Isoprene", period="this week"),
        "parsed_intent": SimpleNamespace(task_type=SimpleNamespace(value="analysis")),
        "diagnosis": {"root_cause": "Supply tightened after the cracker turnaround slipped."},
        "pricing_decision": {
            "recommended_price": 17180,
            "price_range": [17050, 17350],
        },
    }

    preview = server._build_fast_start_preview(prep_state, "Explain isoprene outlook in detail")

    assert "## Quick Take" in preview
    assert "Supply tightened after the cracker turnaround slipped." in preview
    assert "17180" in preview
    assert "Full grounded analysis is still being generated." in preview