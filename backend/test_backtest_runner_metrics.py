from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.src.validation.backtest_runner import BacktestRunner
from backend.src.service.forecast_event_routing import resolve_event_adjustment_policy


class _FakeMarketData:
    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = prices

    def get_price_on_date(self, product_id: str, target_date: str) -> float | None:
        return self.prices.get(target_date[:10])


class TestBacktestRunnerMetrics(unittest.TestCase):
    def test_event_routing_policy_enables_only_measured_winners(self) -> None:
        c5_policy = resolve_event_adjustment_policy("c5_resin", 7)
        naphtha_policy = resolve_event_adjustment_policy("naphtha", 7)

        self.assertTrue(c5_policy["enabled"])
        self.assertTrue(c5_policy["registry_hit"])
        self.assertFalse(naphtha_policy["enabled"])
        self.assertTrue(naphtha_policy["registry_hit"])

    def test_backtest_result_includes_rich_error_and_spike_metrics(self) -> None:
        runner = BacktestRunner()
        fake_market = _FakeMarketData(
            {
                "2026-01-01": 100.0,
                "2026-01-08": 110.0,
            }
        )

        with patch("backend.src.service.market_data_service.market_data", fake_market), \
             patch.object(
                 runner,
                 "_get_historical_forecast",
                 return_value={"base": 108.0, "bull": 112.0, "bear": 104.0, "confidence": 0.8},
             ):
            result = runner.run_backtest(
                as_of_date="2026-01-01",
                product_id="naphtha",
                horizon_days=7,
                policy_mode="arima_residual_daily",
                spike_threshold_pct=0.03,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.model_mode, "arima_residual_daily")
        self.assertEqual(result.as_of_price, 100.0)
        self.assertEqual(result.absolute_error, 2.0)
        self.assertAlmostEqual(result.error_pct, 0.0182)
        self.assertTrue(result.direction_correct)
        self.assertTrue(result.actual_spike)
        self.assertTrue(result.forecast_spike)
        self.assertTrue(result.spike_direction_correct)
        self.assertTrue(result.range_contains_actual)

    def test_backtest_result_records_event_feature_adjustment(self) -> None:
        runner = BacktestRunner()
        fake_market = _FakeMarketData(
            {
                "2026-01-01": 100.0,
                "2026-01-08": 104.0,
            }
        )

        with patch("backend.src.service.market_data_service.market_data", fake_market), \
             patch.object(
                 runner,
                 "_get_historical_forecast",
                 return_value={
                     "base": 102.0,
                     "bull": 106.0,
                     "bear": 98.0,
                     "confidence": 0.8,
                     "event_adjustment_pct": 2.0,
                     "event_feature_applied": True,
                     "evidence_event_ids": ["evt-1"],
                 },
             ):
            result = runner.run_backtest(
                as_of_date="2026-01-01",
                product_id="naphtha",
                horizon_days=7,
                policy_mode="champion_event_adjusted",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.event_adjustment_pct, 2.0)
        self.assertTrue(result.event_feature_applied)
        self.assertEqual(result.evidence_event_ids, ["evt-1"])

    def test_series_summary_ranks_model_modes_by_mape_then_direction(self) -> None:
        runner = BacktestRunner()

        def fake_series(product_id: str, start_date: str, end_date: str, horizon_days: int, step_days: int, policy_mode: str, spike_threshold_pct: float = 0.03):
            payload = {
                "naive": {"mape": 0.05, "mae": 5.0, "rmse": 6.0, "direction_accuracy": 0.5, "total_backtests": 4},
                "adaptive": {"mape": 0.04, "mae": 4.0, "rmse": 5.0, "direction_accuracy": 0.5, "total_backtests": 4},
                "arima_residual_daily": {"mape": 0.04, "mae": 3.8, "rmse": 4.8, "direction_accuracy": 0.75, "total_backtests": 4},
            }
            return payload[policy_mode]

        with patch.object(runner, "run_backtest_series", side_effect=fake_series):
            comparison = runner.compare_model_modes(
                product_id="naphtha",
                start_date="2026-01-01",
                end_date="2026-02-01",
                horizon_days=7,
                model_modes=("naive", "adaptive", "arima_residual_daily"),
            )

        self.assertEqual(comparison["champion_model"], "arima_residual_daily")
        self.assertEqual(comparison["ranked_models"][0]["model_mode"], "arima_residual_daily")
        self.assertEqual(comparison["ranked_models"][-1]["model_mode"], "naive")

    def test_arima_residual_daily_mode_uses_native_dense_path(self) -> None:
        runner = BacktestRunner()
        native_payload = {
            "dense_path": [
                {"base": 101.0},
                {"base": 103.0, "bull": 106.0, "bear": 100.0, "confidence": 0.7},
            ]
        }

        with patch(
            "backend.src.service.forecasting_engine.get_native_baseline_forecast_path",
            return_value=native_payload,
        ) as native_mock:
            forecast = runner._get_historical_forecast(
                "naphtha",
                "2026-01-01",
                2,
                policy_mode="arima_residual_daily",
            )

        self.assertEqual(forecast, {"base": 103.0, "bull": 106.0, "bear": 100.0, "confidence": 0.7})
        native_mock.assert_called_once_with(
            "naphtha",
            horizon_days=2,
            cutoff_date="2026-01-01",
            start_date="2026-01-01",
            use_champion_routing=False,
        )

    def test_champion_mode_delegates_to_resolved_model(self) -> None:
        runner = BacktestRunner()
        fake_market = _FakeMarketData({"2026-01-01": 100.0})

        with patch(
            "backend.src.service.forecasting_engine.resolve_champion_model",
            return_value={"selected_model": "naive"},
        ), patch("backend.src.service.market_data_service.market_data", fake_market):
            forecast = runner._get_historical_forecast(
                "naphtha",
                "2026-01-01",
                7,
                policy_mode="champion",
            )

        self.assertEqual(
            forecast,
            {"base": 100.0, "bull": 104.0, "bear": 96.0, "confidence": 0.5},
        )

    def test_champion_event_adjusted_mode_applies_historical_feature_bias(self) -> None:
        runner = BacktestRunner()

        with patch.object(
            runner,
            "_historical_intelligence_override_pct",
            return_value={"applied_pct": 0.0, "evidence_event_ids": [], "signal": {}},
        ), patch.object(
            runner,
            "_historical_kimi_feature_bundle",
            return_value={
                "snapshot_id": "fs-old",
                "status": "success",
                "feature": {
                    "confidence": 0.8,
                    "horizon_bias_pct": {"7d": 4.0},
                    "evidence_event_ids": ["evt-old"],
                },
            },
        ), patch.object(
            runner,
            "_historical_kimi_application_weight",
            return_value=0.5,
        ):
            adjusted = runner._apply_historical_event_feature_adjustment(
                "naphtha",
                "2026-01-01",
                7,
                {"base": 100.0, "bull": 104.0, "bear": 96.0, "confidence": 0.7},
            )

        self.assertAlmostEqual(adjusted["base"], 102.0)
        self.assertAlmostEqual(adjusted["bull"], 106.08)
        self.assertAlmostEqual(adjusted["bear"], 97.92)
        self.assertEqual(adjusted["event_adjustment_pct"], 2.0)
        self.assertTrue(adjusted["event_feature_applied"])
        self.assertEqual(adjusted["evidence_event_ids"], ["evt-old"])

    def test_champion_event_adjusted_mode_combines_replayed_event_bias(self) -> None:
        runner = BacktestRunner()

        with patch.object(
            runner,
            "_historical_intelligence_override_pct",
            return_value={"applied_pct": -1.5, "evidence_event_ids": ["evt-replay"], "signal": {"active": True}},
        ), patch.object(
            runner,
            "_historical_kimi_feature_bundle",
            return_value={"status": "missing", "feature": {}},
        ):
            adjusted = runner._apply_historical_event_feature_adjustment(
                "naphtha",
                "2026-04-15",
                7,
                {"base": 100.0, "bull": 104.0, "bear": 96.0, "confidence": 0.7},
            )

        self.assertAlmostEqual(adjusted["base"], 98.5)
        self.assertEqual(adjusted["event_adjustment_pct"], -1.5)
        self.assertEqual(adjusted["event_replay_adjustment_pct"], -1.5)
        self.assertTrue(adjusted["event_feature_applied"])
        self.assertEqual(adjusted["evidence_event_ids"], ["evt-replay"])

    def test_champion_event_adjusted_mode_is_neutral_without_historical_snapshot(self) -> None:
        runner = BacktestRunner()

        with patch.object(
            runner,
            "_historical_intelligence_override_pct",
            return_value={"applied_pct": 0.0, "evidence_event_ids": [], "signal": {}},
        ), patch.object(
            runner,
            "_historical_kimi_feature_bundle",
            return_value={"status": "missing", "feature": {}},
        ):
            adjusted = runner._apply_historical_event_feature_adjustment(
                "naphtha",
                "2026-01-01",
                7,
                {"base": 100.0, "bull": 104.0, "bear": 96.0, "confidence": 0.7},
            )

        self.assertEqual(adjusted["base"], 100.0)
        self.assertEqual(adjusted["event_adjustment_pct"], 0.0)
        self.assertFalse(adjusted["event_feature_applied"])

    def test_champion_event_routed_mode_skips_unapproved_products(self) -> None:
        runner = BacktestRunner()

        with patch.object(
            runner,
            "_historical_intelligence_override_pct",
            return_value={"applied_pct": 2.0, "evidence_event_ids": ["evt-replay"], "signal": {"active": True}},
        ), patch.object(
            runner,
            "_historical_kimi_feature_bundle",
            return_value={"status": "missing", "feature": {}},
        ):
            adjusted = runner._apply_historical_event_feature_adjustment(
                "naphtha",
                "2026-04-15",
                7,
                {"base": 100.0, "bull": 104.0, "bear": 96.0, "confidence": 0.7},
                require_event_routing=True,
            )

        self.assertEqual(adjusted["base"], 100.0)
        self.assertEqual(adjusted["event_adjustment_pct"], 0.0)
        self.assertFalse(adjusted["event_feature_applied"])
        self.assertFalse(adjusted["event_adjustment_policy"]["enabled"])


if __name__ == "__main__":
    unittest.main()
