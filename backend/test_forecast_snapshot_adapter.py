from __future__ import annotations

from sqlalchemy import create_engine

from backend.src.service import forecast_snapshot_adapter as adapter


def _fake_forecast(product_id: str) -> dict:
    return {
        "product_id": product_id,
        "latest_price": 5000.0,
        "as_of_date": "2026-05-03",
        "scenarios": {
            "7d": {
                "bear": {"low": 4750.0, "high": 4850.0},
                "base": {"low": 4950.0, "high": 5050.0},
                "bull": {"low": 5150.0, "high": 5250.0},
            }
        },
        "policy": {"policy_id": "unit_policy"},
        "model_version": "unit-model-v1",
        "method_notes": "Unit-test canonical forecast.",
        "forecast_supervisor": {"confidence_label": "Medium"},
    }


def test_get_or_create_forecast_snapshot_persists_and_reuses_canonical_payload(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    calls = {"count": 0}

    def fake_forecast(product_id, **kwargs):
        calls["count"] += 1
        return _fake_forecast(product_id)

    monkeypatch.setattr(adapter.forecasting_service, "forecast", fake_forecast)

    first = adapter.get_or_create_forecast_snapshot(
        engine,
        "cracked_c5",
        as_of_date="2026-05-03",
        run_reason="unit_test",
    )
    second = adapter.get_or_create_forecast_snapshot(
        engine,
        "cracked_c5",
        as_of_date="2026-05-03",
        run_reason="unit_test",
    )

    assert calls["count"] == 1
    assert first["snapshot_id"] == second["snapshot_id"]
    assert second["forecast_snapshot"]["source"] == "stored_snapshot"
    assert second["scenarios"]["7d"]["base"] == {"low": 4950.0, "high": 5050.0}


def test_weekly_report_band_uses_same_canonical_7d_base_range(monkeypatch):
    engine = create_engine("sqlite:///:memory:")

    monkeypatch.setattr(
        adapter.forecasting_service,
        "forecast",
        lambda product_id, **kwargs: _fake_forecast(product_id),
    )

    snapshot = adapter.get_or_create_forecast_snapshot(
        engine,
        "cracked_c5",
        as_of_date="2026-05-03",
        run_reason="unit_test",
    )
    band = adapter.forecast_snapshot_to_band(snapshot, horizon="7d", language="zh")

    assert band.low == "4,950"
    assert band.high == "5,050"
    assert band.source == "Forecasting Service"
    assert band.snapshot_id == snapshot["snapshot_id"]
    assert band.policy_id == "unit_policy"
    assert band.model_version != "fc_fallback"


def test_weekly_report_alias_uses_canonical_forecast_product(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    seen = {}

    def fake_forecast(product_id, **kwargs):
        seen["product_id"] = product_id
        return _fake_forecast(product_id)

    monkeypatch.setattr(adapter.forecasting_service, "forecast", fake_forecast)

    snapshot = adapter.get_or_create_forecast_snapshot(
        engine,
        "pentane_blowing_agent",
        as_of_date="2026-05-03",
        run_reason="unit_test",
    )

    assert seen["product_id"] == "blowing_agent"
    assert snapshot["product_id"] == "blowing_agent"
    assert snapshot["requested_product_id"] == "pentane_blowing_agent"
    assert snapshot["forecast_product_id"] == "blowing_agent"
