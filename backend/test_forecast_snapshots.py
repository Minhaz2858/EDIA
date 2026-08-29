from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
import unittest
from unittest.mock import patch
import os

from backend.src.db.forecast_snapshots import (
    get_latest_approved_snapshot,
    init_tables,
    persist_snapshot,
)
from backend.src.service import forecasting_engine as fe


ARIMA_CHAMPION = {
    "enabled": True,
    "registry_hit": True,
    "product_id": "test",
    "requested_horizon_days": 7,
    "selected_horizon_days": 7,
    "selected_model": "arima_residual_daily",
    "mape": 0.01,
    "challenger_model": "naive",
    "challenger_mape": 0.02,
    "source_window": "unit-test",
    "step_days": 14,
}


class TestForecastSnapshots(unittest.TestCase):
    def test_forecast_snapshot_round_trips_payload_and_path_points(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        init_tables(engine)

        payload = {
            "product_id": "isoprene",
            "model_version": "test-v1",
            "cadence_info": {
                "cadence_class": "daily-capable",
                "native_step_days": 1,
                "daily_forecast_advised": True,
                "display_preferred_series": "forecast",
            },
            "forecast_path": [
                {
                    "date": "2026-05-08",
                    "forecast_date": "2026-05-08",
                    "step_number": 1,
                    "baseline_price": 100.0,
                    "final_price": 101.0,
                    "base": 101.0,
                    "bull": 102.0,
                    "bear": 99.0,
                    "applied_override_pct": 1.0,
                    "cause_summary": "Test event adjustment.",
                    "evidence_event_ids": ["evt-1"],
                }
            ],
            "forecast_adjustment": {
                "source": "test",
                "proposed_override_pct": 2.0,
                "applied_override_pct": 1.0,
                "evidence_event_ids": ["evt-1"],
            },
            "cause_summary": "Test event adjustment.",
            "evidence_event_ids": ["evt-1"],
        }

        snapshot_id = persist_snapshot(engine, payload, run_reason="unit_test")
        latest = get_latest_approved_snapshot(engine, "isoprene")

        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["snapshot_id"], snapshot_id)
        self.assertEqual(latest["product_id"], "isoprene")
        self.assertEqual(latest["forecast_path"][0]["final_price"], 101.0)
        self.assertEqual(latest["forecast_snapshot"]["source"], "stored_snapshot")

    def test_latest_snapshot_can_require_minimum_path_points(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        init_tables(engine)
        payload = {
            "product_id": "cracked_c5",
            "forecast_path": [
                {"date": "2026-05-08", "step_number": 1, "base": 100.0},
                {"date": "2026-05-09", "step_number": 2, "base": 101.0},
            ],
            "cadence_info": {"cadence_class": "weekly", "native_step_days": 7},
        }

        persist_snapshot(engine, payload, run_reason="unit_test")

        self.assertIsNone(get_latest_approved_snapshot(engine, "cracked_c5", min_path_points=30))
        self.assertIsNotNone(get_latest_approved_snapshot(engine, "cracked_c5", min_path_points=2))

    def test_latest_snapshot_can_skip_legacy_fallback(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        init_tables(engine)

        legacy_payload = {
            "product_id": "crude_oil",
            "forecast_path": [
                {"date": "2026-05-08", "step_number": 1, "base": 100.0},
                {"date": "2026-05-09", "step_number": 2, "base": 101.0},
            ],
            "forecast_snapshot": {"validation_status": "legacy_interpolated_fallback"},
        }
        native_payload = {
            "product_id": "crude_oil",
            "forecast_path": [
                {"date": "2026-05-08", "step_number": 1, "base": 102.0},
                {"date": "2026-05-09", "step_number": 2, "base": 103.0},
            ],
            "forecast_snapshot": {"validation_status": "approved_arima_native_baseline"},
        }

        persist_snapshot(engine, legacy_payload, run_reason="unit_test")
        persist_snapshot(engine, native_payload, run_reason="unit_test")

        latest = get_latest_approved_snapshot(
            engine,
            "crude_oil",
            min_path_points=2,
            exclude_validation_statuses=["legacy_interpolated_fallback"],
        )

        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(
            latest["forecast_snapshot"]["validation_status"],
            "approved_arima_native_baseline",
        )


class TestNativeBaselineForecastPath(unittest.TestCase):
    def test_build_feature_matrix_overrides_latest_row_with_live_kimi_regressors(self) -> None:
        series = pd.Series(
            [100.0 + idx for idx in range(80)],
            index=pd.date_range("2026-02-01", periods=80, freq="D"),
            dtype=float,
        )
        naphtha = pd.Series(
            [80.0 + idx * 0.5 for idx in range(80)],
            index=pd.date_range("2026-02-01", periods=80, freq="D"),
            dtype=float,
        )

        with patch.object(fe, "_series_for_product", return_value=series), \
             patch.object(fe, "get_naphtha_series", return_value=naphtha), \
             patch.object(
                 fe,
                 "_get_live_kimi_feature_bundle",
                 return_value={
                     "snapshot_id": "fs-1",
                     "status": "success",
                     "feature": {
                         "confidence": 0.8,
                         "supply_disruption_score": 0.6,
                         "feedstock_pressure_score": 0.5,
                         "recent_event_impact": 0.3,
                         "horizon_bias_pct": {"1d": 4.0, "7d": 2.0, "30d": 1.0},
                     },
                 },
             ):
            df = fe.build_feature_matrix("isoprene")

        self.assertFalse(df.empty)
        for column in (
            "kimi_bias_1d",
            "kimi_bias_7d",
            "kimi_bias_30d",
            "kimi_confidence",
            "kimi_supply_score",
            "kimi_feedstock_pressure",
            "kimi_event_impact",
        ):
            with self.subTest(column=column):
                self.assertIn(column, df.columns)

        self.assertAlmostEqual(float(df["kimi_bias_1d"].iloc[-1]), 4.0)
        self.assertAlmostEqual(float(df["kimi_bias_7d"].iloc[-1]), 2.0)
        self.assertAlmostEqual(float(df["kimi_confidence"].iloc[-1]), 0.8)
        self.assertAlmostEqual(float(df["kimi_supply_score"].iloc[-1]), 0.6)

    def test_uses_arima_steps_for_daily_product(self) -> None:
        series = pd.Series(
            [100.0 + idx for idx in range(50)],
            index=pd.date_range("2026-03-01", periods=50, freq="D"),
            dtype=float,
        )
        cadence = {
            "cadence_class": "daily-capable",
            "change_frequency": 0.75,
            "native_step_days": 1,
            "daily_forecast_advised": True,
            "display_preferred_series": "forecast",
        }

        with patch.object(fe, "resolve_champion_model", return_value=ARIMA_CHAMPION), \
             patch.object(fe, "get_cadence_info", return_value=cadence), \
             patch.object(fe, "_series_for_product", return_value=series), \
             patch.object(
                 fe,
                 "_fit_arima_forecast",
                 return_value=np.array([200.0 + step for step in range(7)], dtype=float),
             ):
            result = fe.get_native_baseline_forecast_path("isoprene", horizon_days=7, start_date="2026-05-07")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["dense_path"]), 7)
        self.assertEqual(len(result["native_path"]), 7)
        self.assertEqual(result["native_path"][0]["date"], "2026-05-08")
        self.assertEqual(result["native_path"][0]["base"], 200.0)
        self.assertEqual(result["native_path"][-1]["base"], 206.0)

    def test_uses_naive_path_when_champion_routing_selects_naive(self) -> None:
        series = pd.Series(
            [100.0, 102.0, 105.0],
            index=pd.date_range("2026-03-01", periods=3, freq="D"),
            dtype=float,
        )
        cadence = {
            "cadence_class": "daily-capable",
            "change_frequency": 0.75,
            "native_step_days": 1,
            "daily_forecast_advised": True,
            "display_preferred_series": "forecast",
        }

        with patch.object(fe, "get_cadence_info", return_value=cadence), \
             patch.object(fe, "_series_for_product", return_value=series), \
             patch.object(fe, "_fit_arima_forecast") as arima_mock:
            result = fe.get_native_baseline_forecast_path(
                "naphtha",
                horizon_days=7,
                start_date="2026-05-07",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "naive_champion")
        self.assertEqual(result["model_components"]["champion_routing"]["selected_model"], "naive")
        self.assertEqual([point["base"] for point in result["dense_path"]], [105.0] * 7)
        self.assertEqual(result["dense_path"][0]["source"], "naive_champion")
        self.assertFalse(result["dense_path"][0]["daily_calibration"]["active"])
        arima_mock.assert_not_called()

    def test_calibrates_daily_path_with_recent_real_volatility(self) -> None:
        history_values = [
            100.0,
            104.0,
            99.0,
            107.0,
            101.0,
            110.0,
            105.0,
            113.0,
            108.0,
            116.0,
        ] * 6
        series = pd.Series(
            history_values,
            index=pd.date_range("2026-03-01", periods=len(history_values), freq="D"),
            dtype=float,
        )
        cadence = {
            "cadence_class": "daily-capable",
            "change_frequency": 0.9,
            "native_step_days": 1,
            "daily_forecast_advised": True,
            "display_preferred_series": "forecast",
        }
        smooth_arima = np.array([200.0 + step for step in range(10)], dtype=float)

        with patch.object(fe, "resolve_champion_model", return_value=ARIMA_CHAMPION), \
                 patch.object(fe, "get_cadence_info", return_value=cadence), \
                 patch.object(fe, "_series_for_product", return_value=series), \
                 patch.object(fe, "_fit_arima_forecast", return_value=smooth_arima):
            result = fe.get_native_baseline_forecast_path("isoprene", horizon_days=10, start_date="2026-05-07")

        bases = [point["base"] for point in result["dense_path"]]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(bases), 10)
        self.assertTrue(result["model_components"]["residual_daily_calibration"]["active"])
        self.assertEqual(result["model_components"]["residual_daily_calibration"]["random_noise"], False)
        self.assertEqual(result["dense_path"][0]["source"], "arima_native_residual_daily")
        self.assertNotEqual(bases[:5], [200.0 + step for step in range(5)])
        self.assertAlmostEqual(bases[-1], 209.0, delta=0.02)

    def test_blends_xgboost_native_path_when_kimi_exogenous_features_are_available(self) -> None:
        series = pd.Series(
            [100.0 + idx for idx in range(50)],
            index=pd.date_range("2026-03-01", periods=50, freq="D"),
            dtype=float,
        )
        cadence = {
            "cadence_class": "daily-capable",
            "change_frequency": 0.75,
            "native_step_days": 1,
            "daily_forecast_advised": True,
            "display_preferred_series": "forecast",
        }

        with patch.dict(os.environ, {"ENABLE_XGBOOST_FORECAST": "true"}), \
             patch.object(fe, "resolve_champion_model", return_value=ARIMA_CHAMPION), \
             patch.object(fe, "get_cadence_info", return_value=cadence), \
             patch.object(
                 fe,
                 "get_price_series_quality",
                 return_value={
                     "raw_observations": 50,
                     "model_window_days": 50,
                     "ffill_ratio": 0.0,
                     "low_data": False,
                     "reason": "",
                 },
             ), \
             patch.object(fe, "_series_for_product", return_value=series), \
             patch.object(
                 fe,
                 "_fit_arima_forecast",
                 return_value=np.array([200.0 + step for step in range(7)], dtype=float),
             ), \
             patch.object(fe, "build_feature_matrix", return_value=pd.DataFrame({"price": [100.0]})), \
             patch.object(
                 fe,
                 "_fit_xgboost_forecast",
                 return_value=np.array([220.0 + step for step in range(7)], dtype=float),
             ), \
             patch.object(
                 fe,
                 "_get_live_kimi_feature_bundle",
                 return_value={
                     "snapshot_id": "fs-1",
                     "status": "success",
                     "feature": {
                         "confidence": 0.8,
                         "supply_disruption_score": 0.6,
                         "feedstock_pressure_score": 0.5,
                         "recent_event_impact": 0.3,
                         "horizon_bias_pct": {"1d": 4.0, "7d": 2.0, "30d": 1.0},
                     },
                 },
             ):
            result = fe.get_native_baseline_forecast_path(
                "isoprene",
                horizon_days=7,
                start_date="2026-05-07",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "arima_xgboost_kimi_exogenous")
        self.assertTrue(result["model_components"]["kimi_exogenous_active"])
        self.assertGreater(result["model_components"]["xgboost_weight"], 0.0)
        self.assertEqual(result["model_components"]["kimi_feature_snapshot_id"], "fs-1")
        self.assertGreater(result["dense_path"][0]["base"], 200.0)
        self.assertLess(result["dense_path"][0]["base"], 220.0)

    def test_samples_lower_cadence_products(self) -> None:
        series = pd.Series(
            [100.0 + idx for idx in range(50)],
            index=pd.date_range("2026-03-01", periods=50, freq="D"),
            dtype=float,
        )
        cadence = {
            "cadence_class": "weekly",
            "change_frequency": 0.10,
            "native_step_days": 7,
            "daily_forecast_advised": False,
            "display_preferred_series": "forecast_native",
        }

        with patch.object(fe, "resolve_champion_model", return_value=ARIMA_CHAMPION), \
             patch.object(fe, "get_cadence_info", return_value=cadence), \
             patch.object(fe, "_series_for_product", return_value=series), \
             patch.object(
                 fe,
                 "_fit_arima_forecast",
                 return_value=np.array([300.0 + step for step in range(14)], dtype=float),
             ):
            result = fe.get_native_baseline_forecast_path("cracked_c5", horizon_days=14, start_date="2026-05-07")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["dense_path"]), 14)
        self.assertEqual([point["step_number"] for point in result["native_path"]], [7, 14])
        self.assertEqual([point["base"] for point in result["native_path"]], [306.0, 313.0])


if __name__ == "__main__":
    unittest.main()