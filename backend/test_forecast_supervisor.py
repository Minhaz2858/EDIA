from __future__ import annotations

from unittest.mock import patch

from backend.src.service.forecast_supervisor import forecast_supervisor
from backend.src.service.forecasting_service import ForecastingService


def _scenario(base: float = 100.0) -> dict:
    return {
        "bear": {"low": base - 6.0, "high": base - 4.0},
        "base": {"low": base - 1.0, "high": base + 1.0},
        "bull": {"low": base + 4.0, "high": base + 6.0},
    }


def _forecast_payload(recent_mape: float | None = 4.0) -> dict:
    return {
        "product_id": "isoprene",
        "latest_price": 100.0,
        "scenarios": {
            "3d": _scenario(101.0),
            "7d": _scenario(102.0),
            "30d": _scenario(103.0),
        },
        "policy": {
            "metrics": {
                "sample_count": 8,
                "recent_mape": recent_mape,
                "regime": "steady_state",
            }
        },
    }


def test_forecast_supervisor_marks_clean_forecast_publishable() -> None:
    result = forecast_supervisor.supervise(_forecast_payload(4.5))

    assert result["status"] == "publishable"
    assert result["confidence_label"] == "high"
    assert result["quality_gates"]["scenario_band_sanity"] == "pass"
    assert result["quality_gates"]["recent_mape_available_or_disclosed"] == "pass"
    assert "Recent MAPE: 4.50%" in result["required_disclosures"][0]


def test_forecast_supervisor_requires_caveat_when_mape_is_missing() -> None:
    payload = _forecast_payload(None)
    payload["policy"]["metrics"]["sample_count"] = 0

    result = forecast_supervisor.supervise(payload)

    assert result["status"] == "needs_caveat"
    assert result["confidence_label"] == "low"
    assert result["quality_gates"]["recent_mape_available_or_disclosed"] == "warn"
    assert any(issue["code"] == "recent_mape_unavailable" for issue in result["issues"])


def test_forecast_supervisor_blocks_invalid_scenario_bands() -> None:
    payload = _forecast_payload(5.0)
    payload["scenarios"]["7d"] = {
        "bear": {"low": 105.0, "high": 106.0},
        "base": {"low": 100.0, "high": 101.0},
        "bull": {"low": 102.0, "high": 103.0},
    }

    result = forecast_supervisor.supervise(payload)

    assert result["status"] == "insufficient_data"
    assert result["quality_gates"]["scenario_band_sanity"] == "fail"
    assert any(issue["code"] == "invalid_scenario_bands" for issue in result["issues"])


def test_forecast_service_attaches_supervisor_and_uses_hermes_horizons() -> None:
    service = ForecastingService()
    hermes_plan = {
        "forecast_policy": {
            "horizons": ["7d"],
        }
    }

    with patch("backend.src.service.forecasting_service.ensemble_forecast") as ensemble_mock, \
         patch("backend.src.service.forecasting_service.monthly_stl_forecast") as monthly_mock, \
         patch("backend.src.service.forecasting_service.compute_kpi_forecast_adjustment", return_value={"active": False, "adjustment_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0}}), \
         patch("backend.src.service.forecasting_service.erp_kpi_service.get_product_kpi", return_value={}), \
         patch("backend.src.service.forecasting_service.summarize_product_intelligence", return_value={"active": False, "bias": "neutral", "net_price_pct": 0.0, "confidence": 0.0}), \
         patch("backend.src.service.forecasting_service.forecast_policy_service.apply") as policy_mock:
        ensemble_mock.side_effect = [
            {"latest": 100.0, "base_pct": 0.5, "vol_pct": 1.0, "arima_ok": True, "xgb_ok": False, "lstm_ok": False},
            {"latest": 100.0, "base_pct": 1.0, "vol_pct": 1.2, "arima_ok": True, "xgb_ok": False, "lstm_ok": False},
        ]
        monthly_mock.return_value = {
            "base_pct": 2.0,
            "vol_pct": 1.5,
            "seasonal_pct": 0.0,
            "stl_pct": 1.0,
            "causal_pct": 1.0,
            "layers_used": ["stl"],
        }
        policy_mock.return_value = {
            "policy_id": "adaptive_steady_state",
            "model_version": "v1_policy",
            "metrics": {
                "sample_count": 5,
                "recent_mape": 6.0,
                "mean_signed_error_pct": 0.0,
                "recent_volatility_pct": 1.0,
                "trailing_14d_change_pct": 0.0,
                "regime": "steady_state",
                "bias_correction_pct": 0.0,
                "volatility_multiplier": 1.0,
            },
            "horizons": {
                "3d": {"base_pct": 0.5, "vol_pct": 1.0},
                "7d": {"base_pct": 1.0, "vol_pct": 1.2},
                "30d": {"base_pct": 2.0, "vol_pct": 1.5},
            },
        }

        result = service.forecast("isoprene", hermes_plan=hermes_plan)

    supervisor = result["forecast_supervisor"]
    assert supervisor["status"] == "publishable"
    assert supervisor["required_horizons"] == ["7d"]
    assert supervisor["quality_gates"]["forecast_horizons_present"] == "pass"
    assert "Supervisor:" in service.to_prompt_text(result)