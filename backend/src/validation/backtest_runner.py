"""
Backtest validation system.
Runs historical forecasts against actuals to prove forecast accuracy.
"""

from typing import Dict, List, Any, Optional, Sequence
from datetime import datetime, timedelta
from dataclasses import dataclass
import json
import math


@dataclass
class BacktestResult:
    """Result of a single backtest run."""

    as_of_date: str  # When was this forecast made?
    product_id: str
    horizon_days: int  # 3-day or 7-day forecast?

    forecast_price: float
    actual_price: float
    as_of_price: float
    absolute_error: float
    signed_error_pct: float
    error_pct: float  # abs((forecast - actual) / actual)

    direction_correct: bool  # Did we get up/down right?
    actual_change_pct: float
    forecast_change_pct: float
    actual_spike: bool
    forecast_spike: bool
    spike_direction_correct: Optional[bool]
    bull_range: float  # Bull scenario
    bear_range: float  # Bear scenario
    range_contains_actual: bool  # Did [bear, bull] contain actual?

    forecast_confidence: Optional[float] = None
    model_mode: str = "adaptive"
    event_adjustment_pct: float = 0.0
    event_feature_applied: bool = False
    evidence_event_ids: Optional[List[str]] = None


class BacktestRunner:
    """
    Runs backtests to validate forecast accuracy.

    Usage:
        runner = BacktestRunner()
        results = runner.run_backtest(
            as_of_date="2026-03-01",
            product_id="Cracked C5",
            horizon_days=7
        )

    Note: Requires historical price data available in market_data service.
    """

    def __init__(self):
        # Will import from market_data service when called
        pass

    def run_backtest(
        self,
        as_of_date: str,
        product_id: str,
        horizon_days: int = 7,
        policy_mode: str = "adaptive",
        spike_threshold_pct: float = 0.03,
    ) -> Optional[BacktestResult]:
        """
        Run a single backtest: what did the system forecast, vs. what happened?

        Args:
            as_of_date: Date to "pretend" is today (YYYY-MM-DD)
            product_id: e.g., "Cracked C5"
            horizon_days: Forecast horizon (3 or 7)

        Returns:
            BacktestResult, or None if data unavailable
        """

        try:
            from backend.src.service.market_data_service import market_data

            get_price_on_date = getattr(market_data, "get_price_on_date", None)
            if not callable(get_price_on_date):
                return None

            # Get historical price as of as_of_date
            as_of_price = get_price_on_date(product_id, as_of_date)
            if not as_of_price:
                return None

            # Get actual price N days later
            target_date = (
                datetime.fromisoformat(as_of_date) + timedelta(days=horizon_days)
            ).isoformat()[:10]
            actual_price = get_price_on_date(product_id, target_date)
            if not actual_price:
                return None

            # Get forecast that would have been made on as_of_date
            # (This would require running the decision pipeline on historical data,
            #  which for now we'll simulate)
            forecast = self._get_historical_forecast(
                product_id, as_of_date, horizon_days, policy_mode=policy_mode
            )
            if not forecast:
                return None

            # Compute metrics
            forecast_price = forecast.get("base", as_of_price)
            error_pct = (
                abs(forecast_price - actual_price) / actual_price if actual_price else 0
            )
            signed_error_pct = (
                (forecast_price - actual_price) / actual_price if actual_price else 0
            )
            absolute_error = abs(forecast_price - actual_price)

            # Direction
            actual_direction = 1 if actual_price >= as_of_price else -1
            forecast_direction = 1 if forecast_price >= as_of_price else -1
            direction_correct = actual_direction == forecast_direction
            actual_change_pct = (
                (actual_price - as_of_price) / as_of_price if as_of_price else 0.0
            )
            forecast_change_pct = (
                (forecast_price - as_of_price) / as_of_price if as_of_price else 0.0
            )
            actual_spike = abs(actual_change_pct) >= spike_threshold_pct
            forecast_spike = abs(forecast_change_pct) >= spike_threshold_pct
            spike_direction_correct = (
                actual_spike
                and forecast_spike
                and (actual_change_pct >= 0) == (forecast_change_pct >= 0)
            ) if actual_spike or forecast_spike else None

            # Range
            bull = forecast.get("bull", forecast_price * 1.04)
            bear = forecast.get("bear", forecast_price * 0.96)
            range_contains_actual = bear <= actual_price <= bull

            return BacktestResult(
                as_of_date=as_of_date,
                product_id=product_id,
                horizon_days=horizon_days,
                forecast_price=round(forecast_price, 0),
                actual_price=round(actual_price, 0),
                as_of_price=round(as_of_price, 0),
                absolute_error=round(absolute_error, 2),
                signed_error_pct=round(signed_error_pct, 4),
                error_pct=round(error_pct, 4),
                direction_correct=direction_correct,
                actual_change_pct=round(actual_change_pct, 4),
                forecast_change_pct=round(forecast_change_pct, 4),
                actual_spike=actual_spike,
                forecast_spike=forecast_spike,
                spike_direction_correct=spike_direction_correct,
                bull_range=round(bull, 0),
                bear_range=round(bear, 0),
                range_contains_actual=range_contains_actual,
                forecast_confidence=forecast.get("confidence", 0.7),
                model_mode=policy_mode,
                event_adjustment_pct=round(float(forecast.get("event_adjustment_pct") or 0.0), 4),
                event_feature_applied=bool(forecast.get("event_feature_applied")),
                evidence_event_ids=list(forecast.get("evidence_event_ids") or []),
            )

        except Exception as e:
            print(f"Backtest error for {product_id} on {as_of_date}: {e}")
            return None

    def run_backtest_series(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        horizon_days: int = 7,
        step_days: int = 7,
        policy_mode: str = "adaptive",
        spike_threshold_pct: float = 0.03,
    ) -> Dict[str, Any]:
        """
        Run a series of backtests and compute aggregate statistics.

        Args:
            product_id: e.g., "Cracked C5"
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            horizon_days: Forecast horizon (3 or 7)
            step_days: How often to run backtest (weekly = 7)

        Returns:
            Dict with summary stats
        """

        results: List[BacktestResult] = []
        current = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)

        while current <= end:
            result = self.run_backtest(
                as_of_date=current.isoformat()[:10],
                product_id=product_id,
                horizon_days=horizon_days,
                policy_mode=policy_mode,
                spike_threshold_pct=spike_threshold_pct,
            )
            if result:
                results.append(result)
            current += timedelta(days=step_days)

        if not results:
            return {}

        # Compute aggregate stats
        errors = [r.error_pct for r in results if r.error_pct is not None]
        absolute_errors = [r.absolute_error for r in results if r.absolute_error is not None]
        signed_errors = [r.signed_error_pct for r in results if r.signed_error_pct is not None]
        directions = [
            r.direction_correct for r in results if r.direction_correct is not None
        ]
        ranges = [r.range_contains_actual for r in results]
        actual_spikes = [r for r in results if r.actual_spike]
        forecast_spikes = [r for r in results if r.forecast_spike]
        true_spikes = [r for r in forecast_spikes if r.actual_spike and r.spike_direction_correct]
        event_adjusted = [r for r in results if r.event_feature_applied]
        rmse = (
            math.sqrt(sum(err * err for err in absolute_errors) / len(absolute_errors))
            if absolute_errors
            else None
        )

        return {
            "product": product_id,
            "policy_mode": policy_mode,
            "period": f"{start_date} to {end_date}",
            "horizon_days": horizon_days,
            "total_backtests": len(results),
            # Accuracy metrics
            "mape": round(sum(errors) / len(errors), 4) if errors else None,
            "mae": round(sum(absolute_errors) / len(absolute_errors), 2) if absolute_errors else None,
            "rmse": round(rmse, 2) if rmse is not None else None,
            "bias_pct": round(sum(signed_errors) / len(signed_errors), 4) if signed_errors else None,
            "mape_label": self._mape_label(
                sum(errors) / len(errors) if errors else None
            ),
            "direction_accuracy": (
                round(sum(directions) / len(directions), 2) if directions else None
            ),
            "range_coverage": round(sum(ranges) / len(ranges), 2) if ranges else None,
            "spike_threshold_pct": spike_threshold_pct,
            "spike_precision": (
                round(len(true_spikes) / len(forecast_spikes), 2) if forecast_spikes else None
            ),
            "spike_recall": (
                round(len(true_spikes) / len(actual_spikes), 2) if actual_spikes else None
            ),
            "actual_spike_count": len(actual_spikes),
            "forecast_spike_count": len(forecast_spikes),
            "event_feature_applied_count": len(event_adjusted),
            "avg_event_adjustment_pct": (
                round(sum(r.event_adjustment_pct for r in results) / len(results), 4)
                if results
                else None
            ),
            # Extremes
            "best_forecast": (
                {
                    "date": results[0].as_of_date,
                    "error": results[0].error_pct,
                }
                if results
                else None
            ),
            "worst_forecast": (
                {
                    "date": max(results, key=lambda r: r.error_pct).as_of_date,
                    "error": max(r.error_pct for r in results),
                }
                if results
                else None
            ),
            # Monthly breakdown
            "monthly": self._breakdown_by_month(results),
            # Raw results for detail view
            "results": [
                {
                    "date": r.as_of_date,
                    "forecast": r.forecast_price,
                    "actual": r.actual_price,
                    "as_of_price": r.as_of_price,
                    "absolute_error": r.absolute_error,
                    "error_pct": r.error_pct,
                    "signed_error_pct": r.signed_error_pct,
                    "direction_correct": r.direction_correct,
                    "actual_change_pct": r.actual_change_pct,
                    "forecast_change_pct": r.forecast_change_pct,
                    "actual_spike": r.actual_spike,
                    "forecast_spike": r.forecast_spike,
                    "spike_direction_correct": r.spike_direction_correct,
                    "range_contains_actual": r.range_contains_actual,
                    "event_adjustment_pct": r.event_adjustment_pct,
                    "event_feature_applied": r.event_feature_applied,
                    "evidence_event_ids": list(r.evidence_event_ids or []),
                }
                for r in results
            ],
        }

    def compare_model_modes(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        horizon_days: int = 7,
        step_days: int = 7,
        model_modes: Sequence[str] = ("naive", "adaptive", "arima_residual_daily"),
        spike_threshold_pct: float = 0.03,
    ) -> Dict[str, Any]:
        """Run the same walk-forward schedule across model modes and rank them."""
        summaries: Dict[str, Dict[str, Any]] = {}
        for mode in model_modes:
            summaries[mode] = self.run_backtest_series(
                product_id=product_id,
                start_date=start_date,
                end_date=end_date,
                horizon_days=horizon_days,
                step_days=step_days,
                policy_mode=mode,
                spike_threshold_pct=spike_threshold_pct,
            )

        ranked = sorted(
            [
                {
                    "model_mode": mode,
                    "mape": summary.get("mape"),
                    "mae": summary.get("mae"),
                    "rmse": summary.get("rmse"),
                    "direction_accuracy": summary.get("direction_accuracy"),
                    "spike_recall": summary.get("spike_recall"),
                    "event_feature_applied_count": summary.get("event_feature_applied_count", 0),
                    "avg_event_adjustment_pct": summary.get("avg_event_adjustment_pct"),
                    "total_backtests": summary.get("total_backtests", 0),
                }
                for mode, summary in summaries.items()
                if isinstance(summary, dict) and summary.get("mape") is not None
            ],
            key=lambda row: (
                row["mape"],
                -(row["direction_accuracy"] or 0.0),
                row["rmse"] if row["rmse"] is not None else float("inf"),
            ),
        )
        champion = ranked[0]["model_mode"] if ranked else None
        return {
            "product": product_id,
            "period": f"{start_date} to {end_date}",
            "horizon_days": horizon_days,
            "step_days": step_days,
            "spike_threshold_pct": spike_threshold_pct,
            "champion_model": champion,
            "ranked_models": ranked,
            "summaries": summaries,
        }

    def compare_policy_modes(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        horizon_days: int = 7,
        step_days: int = 7,
    ) -> Dict[str, Any]:
        baseline = self.run_backtest_series(
            product_id=product_id,
            start_date=start_date,
            end_date=end_date,
            horizon_days=horizon_days,
            step_days=step_days,
            policy_mode="baseline",
        )
        adaptive = self.run_backtest_series(
            product_id=product_id,
            start_date=start_date,
            end_date=end_date,
            horizon_days=horizon_days,
            step_days=step_days,
            policy_mode="adaptive",
        )

        baseline_mape = baseline.get("mape")
        adaptive_mape = adaptive.get("mape")
        baseline_direction = baseline.get("direction_accuracy")
        adaptive_direction = adaptive.get("direction_accuracy")
        baseline_coverage = baseline.get("range_coverage")
        adaptive_coverage = adaptive.get("range_coverage")

        return {
            "product": product_id,
            "period": f"{start_date} to {end_date}",
            "horizon_days": horizon_days,
            "baseline": baseline,
            "adaptive": adaptive,
            "delta": {
                "mape": round((adaptive_mape - baseline_mape), 4)
                if adaptive_mape is not None and baseline_mape is not None
                else None,
                "direction_accuracy": round((adaptive_direction - baseline_direction), 4)
                if adaptive_direction is not None and baseline_direction is not None
                else None,
                "range_coverage": round((adaptive_coverage - baseline_coverage), 4)
                if adaptive_coverage is not None and baseline_coverage is not None
                else None,
            },
        }

    def _get_historical_forecast(
        self,
        product_id: str,
        as_of_date: str,
        horizon_days: int,
        policy_mode: str = "adaptive",
    ) -> Optional[Dict[str, float]]:
        """
        Retrieve the forecast that would have been made on as_of_date.

        Replays the current forecasting pipeline against history available up to
        `as_of_date`, then extracts the midpoint of the requested horizon.
        """
        try:
            if policy_mode == "champion":
                from backend.src.service.forecasting_engine import resolve_champion_model

                champion = resolve_champion_model(product_id, horizon_days)
                selected_model = str(champion.get("selected_model") or "arima_residual_daily")
                if selected_model == "champion":
                    selected_model = "arima_residual_daily"
                return self._get_historical_forecast(
                    product_id,
                    as_of_date,
                    horizon_days,
                    policy_mode=selected_model,
                )

            if policy_mode in {"champion_event_adjusted", "champion_kimi_event", "champion_event_routed"}:
                forecast = self._get_historical_forecast(
                    product_id,
                    as_of_date,
                    horizon_days,
                    policy_mode="champion",
                )
                if not forecast:
                    return None
                return self._apply_historical_event_feature_adjustment(
                    product_id,
                    as_of_date,
                    horizon_days,
                    forecast,
                    require_event_routing=(policy_mode == "champion_event_routed"),
                )

            if policy_mode == "naive":
                from backend.src.service.market_data_service import market_data

                as_of_price = market_data.get_price_on_date(product_id, as_of_date)
                if not as_of_price:
                    return None
                return {
                    "base": float(as_of_price),
                    "bull": float(as_of_price) * 1.04,
                    "bear": float(as_of_price) * 0.96,
                    "confidence": 0.5,
                }

            if policy_mode in {"arima_residual_daily", "arima_native"}:
                from backend.src.service.forecasting_engine import get_native_baseline_forecast_path

                native = get_native_baseline_forecast_path(
                    product_id,
                    horizon_days=horizon_days,
                    cutoff_date=as_of_date,
                    start_date=as_of_date,
                    use_champion_routing=False,
                )
                path = native.get("dense_path") if isinstance(native, dict) else []
                if not isinstance(path, list) or len(path) < horizon_days:
                    return None
                point = path[horizon_days - 1]
                base = float(point.get("base") or point.get("final_price") or 0.0)
                if base <= 0:
                    return None
                return {
                    "base": base,
                    "bull": float(point.get("bull") or base * 1.04),
                    "bear": float(point.get("bear") or base * 0.96),
                    "confidence": float(point.get("confidence") or 0.7),
                }

            from backend.src.service.forecasting_service import forecasting_service

            forecast = forecasting_service.forecast(
                product_id,
                as_of_date=as_of_date,
                policy_mode=policy_mode,
            )
            scenarios = forecast.get("scenarios", {}).get(f"{horizon_days}d", {})
            base = scenarios.get("base") or {}
            bull = scenarios.get("bull") or {}
            bear = scenarios.get("bear") or {}

            def midpoint(bounds: Dict[str, float], fallback: float = 0.0) -> float:
                low = bounds.get("low")
                high = bounds.get("high")
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    return (float(low) + float(high)) / 2.0
                return fallback

            base_mid = midpoint(base, float(forecast.get("latest_price") or 0.0))
            bull_mid = midpoint(bull, base_mid)
            bear_mid = midpoint(bear, base_mid)
            return {
                "base": base_mid,
                "bull": bull_mid,
                "bear": bear_mid,
                "confidence": 0.7,
            }
        except Exception as exc:
            print(
                f"Historical forecast replay error for {product_id} on {as_of_date} ({policy_mode}): {exc}"
            )
            return None

    def _horizon_feature_key(self, horizon_days: int) -> str:
        if horizon_days <= 1:
            return "1d"
        if horizon_days <= 3:
            return "3d"
        if horizon_days <= 7:
            return "7d"
        if horizon_days <= 14:
            return "14d"
        return "30d"

    def _historical_kimi_feature_bundle(
        self,
        product_id: str,
        as_of_date: str,
    ) -> Dict[str, Any]:
        """Return a Kimi feature snapshot valid at as_of_date, avoiding future leakage."""
        try:
            from sqlalchemy import text
            from backend.src.service.kimi_forecast_feature_service import (
                kimi_forecast_feature_service,
            )

            as_of_dt = datetime.fromisoformat(as_of_date[:10])
            stmt = text(
                """
                SELECT snapshot_id, generated_at, expires_at, model, status, feature_json
                FROM forecast_feature_snapshots
                WHERE generated_at <= :as_of
                  AND expires_at >= :as_of
                ORDER BY generated_at DESC
                LIMIT 1
                """
            )
            with kimi_forecast_feature_service.engine.connect() as conn:
                row = conn.execute(stmt, {"as_of": as_of_dt.isoformat()}).mappings().first()
            if not row:
                return {"status": "missing", "feature": {}}
            features = json.loads(row.get("feature_json") or "{}")
            products = features.get("products") if isinstance(features.get("products"), dict) else {}
            feature = products.get(product_id) if isinstance(products.get(product_id), dict) else {}
            return {
                "snapshot_id": row.get("snapshot_id"),
                "generated_at": row.get("generated_at"),
                "expires_at": row.get("expires_at"),
                "model": row.get("model"),
                "status": row.get("status"),
                "feature": feature,
            }
        except Exception:
            return {"status": "unavailable", "feature": {}}

    def _historical_kimi_application_weight(
        self,
        product_id: str,
        as_of_date: str,
        confidence: float,
    ) -> float:
        try:
            from backend.src.service.forecasting_engine import (
                get_cadence_info,
                get_price_series_quality,
            )

            cadence_info = get_cadence_info(product_id, cutoff_date=as_of_date)
            data_quality = get_price_series_quality(product_id, cutoff_date=as_of_date)
            cadence_class = str(cadence_info.get("cadence_class") or "unsupported")
            cadence_weight = {
                "daily-capable": 1.0,
                "multi-day": 0.85,
                "weekly": 0.7,
                "sparse": 0.55,
            }.get(cadence_class, 0.4)
            try:
                ffill_ratio = float(data_quality.get("ffill_ratio") or 0.0)
            except (TypeError, ValueError):
                ffill_ratio = 0.0
            quality_weight = max(0.35, 1.0 - max(0.0, min(1.0, ffill_ratio)) * 0.6)
            if data_quality.get("low_data"):
                quality_weight *= 0.8
            return round(max(0.0, min(1.0, confidence)) * cadence_weight * quality_weight, 4)
        except Exception:
            return round(max(0.0, min(1.0, confidence)) * 0.4, 4)

    def _historical_intelligence_override_pct(
        self,
        product_id: str,
        as_of_date: str,
        horizon_days: int,
    ) -> Dict[str, Any]:
        try:
            from backend.src.intelligence.causal_narrative_engine import (
                CausalNarrativeEngine,
                _APPROX_PRICES_CNY,
                _NAPHTHA_ELASTICITIES,
            )
            from backend.src.intelligence.event_registry import intelligence_event_registry
            from backend.src.intelligence.intelligence_orchestrator import (
                _event_allowed_by_review_policy,
                _is_noise_event,
            )
            from backend.src.intelligence.product_intelligence import summarize_product_intelligence
            from backend.src.intelligence.second_order_effects import (
                compute_total_second_order_pct,
                get_product_specific_effects,
            )

            events = intelligence_event_registry.list_events_as_of(
                as_of=as_of_date,
                lookback_hours=168,
                min_impact=0.50,
                review_status=None,
                min_usefulness_score=0,
                limit=80,
            )
            events = [
                event
                for event in events
                if _event_allowed_by_review_policy(event, "auto") and not _is_noise_event(event)
            ]
            if not events:
                return {"applied_pct": 0.0, "evidence_event_ids": [], "signal": {}}

            causal_engine = CausalNarrativeEngine(None)
            causal_analyses: list[dict[str, Any]] = []
            for event in events:
                try:
                    entry_commodity = causal_engine._map_event_to_entry(event)
                    primary_pct = causal_engine._quantify_primary_impact(event)
                    naphtha_total_pct = (
                        causal_engine._crude_to_naphtha_pct(primary_pct, event)
                        + compute_total_second_order_pct(event.event_type) * 100
                    )
                    product_impacts = {}
                    product_impacts["naphtha"] = {
                        "pct_impact": round(naphtha_total_pct, 2),
                        "cny_impact": round(1000 * (naphtha_total_pct / 100.0), 0),
                        "causal_path": f"event -> {entry_commodity} -> naphtha",
                        "elasticity_used": 1.0,
                        "second_order_pct": 0.0,
                    }
                    product_specific_effects = get_product_specific_effects(event.event_type)
                    for impacted_product_id, elasticity in _NAPHTHA_ELASTICITIES.items():
                        pct_impact = naphtha_total_pct * elasticity / 100
                        product_extra = product_specific_effects.get(impacted_product_id, 0.0)
                        total_pct = pct_impact + product_extra
                        approx_price = _APPROX_PRICES_CNY.get(impacted_product_id, 5000)
                        product_impacts[impacted_product_id] = {
                            "pct_impact": round(total_pct * 100, 2),
                            "cny_impact": round(approx_price * total_pct, 0),
                            "causal_path": f"event -> {entry_commodity} -> naphtha -> {impacted_product_id}",
                            "elasticity_used": round(elasticity, 3),
                            "second_order_pct": round(product_extra * 100, 2),
                        }
                except Exception:
                    continue
                causal_analyses.append(
                    {
                        "event_id": event.event_id,
                        "event_headline": event.headline,
                        "event_type": event.event_type,
                        "entry_commodity": entry_commodity,
                        "primary_impact_pct": round(primary_pct, 2),
                        "naphtha_total_pct": round(naphtha_total_pct, 2),
                        "product_impacts": product_impacts,
                        "narrative": event.causal_chain_hint or event.key_information or event.headline,
                        "confidence_by_horizon": causal_engine._compute_confidence_decay(event),
                        "recommended_override_type": causal_engine._suggest_override_type(event),
                        "detected_at": event.detected_at,
                        "review_status": event.review_status,
                        "source_url": event.source_url,
                        "raw_quote": event.raw_quote,
                    }
                )

            context = {
                "has_intelligence": bool(causal_analyses),
                "checked": True,
                "checked_hours": 168,
                "events": [event.to_dict() for event in events],
                "causal_analyses": causal_analyses,
                "scenarios": [],
                "override_decisions": [],
            }
            signal = summarize_product_intelligence(product_id, context)
            if not signal.get("active"):
                return {"applied_pct": 0.0, "evidence_event_ids": [], "signal": signal}

            weighted_pct = float(signal.get("net_price_pct") or 0.0) * float(signal.get("confidence") or 0.0)
            day3_pct = round(weighted_pct * 0.8, 4)
            day7_pct = round(weighted_pct, 4)
            day30_pct = round(weighted_pct * 0.6, 4)
            if horizon_days <= 3:
                applied_pct = day3_pct * (max(horizon_days, 1) / 3)
            elif horizon_days <= 7:
                applied_pct = day3_pct + (day7_pct - day3_pct) * ((horizon_days - 3) / 4)
            else:
                applied_pct = day7_pct + (day30_pct - day7_pct) * ((min(horizon_days, 30) - 7) / 23)

            evidence_event_ids = [
                str(event.get("event_id") or "")
                for event in signal.get("events", [])
                if isinstance(event, dict) and str(event.get("event_id") or "").strip()
            ]
            return {
                "applied_pct": round(applied_pct, 4),
                "evidence_event_ids": list(dict.fromkeys(evidence_event_ids)),
                "signal": signal,
            }
        except Exception:
            return {"applied_pct": 0.0, "evidence_event_ids": [], "signal": {}}

    def _apply_historical_event_feature_adjustment(
        self,
        product_id: str,
        as_of_date: str,
        horizon_days: int,
        forecast: Dict[str, float],
        require_event_routing: bool = False,
    ) -> Dict[str, float]:
        event_policy: dict[str, Any] = {}
        if require_event_routing:
            try:
                from backend.src.service.forecast_event_routing import resolve_event_adjustment_policy

                event_policy = resolve_event_adjustment_policy(product_id, horizon_days)
            except Exception:
                event_policy = {"enabled": False, "reason": "event routing lookup failed"}
            if not event_policy.get("enabled"):
                adjusted = dict(forecast)
                adjusted["event_adjustment_pct"] = 0.0
                adjusted["event_feature_applied"] = False
                adjusted["evidence_event_ids"] = []
                adjusted["event_adjustment_policy"] = event_policy
                adjusted["event_replay_adjustment_pct"] = 0.0
                adjusted["kimi_feature_adjustment_pct"] = 0.0
                return adjusted

        replay = self._historical_intelligence_override_pct(
            product_id,
            as_of_date,
            horizon_days,
        )
        try:
            replay_pct = float(replay.get("applied_pct") or 0.0)
        except (TypeError, ValueError):
            replay_pct = 0.0

        bundle = self._historical_kimi_feature_bundle(product_id, as_of_date)
        feature = bundle.get("feature") if isinstance(bundle.get("feature"), dict) else {}
        horizon_bias = feature.get("horizon_bias_pct") if isinstance(feature.get("horizon_bias_pct"), dict) else {}
        try:
            raw_bias_pct = float(horizon_bias.get(self._horizon_feature_key(horizon_days)) or 0.0)
        except (TypeError, ValueError):
            raw_bias_pct = 0.0
        try:
            confidence = float(feature.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        application_weight = self._historical_kimi_application_weight(
            product_id,
            as_of_date,
            confidence,
        ) if str(bundle.get("status") or "") == "success" else 0.0
        kimi_pct = round(raw_bias_pct * application_weight, 4)
        applied_pct = round(replay_pct + kimi_pct, 4)
        multiplier = 1.0 + applied_pct / 100.0
        adjusted = dict(forecast)
        for key in ("base", "bull", "bear"):
            try:
                adjusted[key] = float(adjusted.get(key) or 0.0) * multiplier
            except (TypeError, ValueError):
                pass
        adjusted["event_adjustment_pct"] = applied_pct
        adjusted["event_feature_applied"] = abs(applied_pct) > 1e-9
        evidence = feature.get("evidence_event_ids") if isinstance(feature.get("evidence_event_ids"), list) else []
        replay_evidence = replay.get("evidence_event_ids") if isinstance(replay.get("evidence_event_ids"), list) else []
        adjusted["evidence_event_ids"] = list(
            dict.fromkeys(
                [
                    *[str(item) for item in replay_evidence if str(item or "").strip()],
                    *[str(item) for item in evidence if str(item or "").strip()],
                ]
            )
        )
        adjusted["event_feature_snapshot_id"] = bundle.get("snapshot_id")
        adjusted["event_replay_adjustment_pct"] = round(replay_pct, 4)
        adjusted["kimi_feature_adjustment_pct"] = kimi_pct
        if require_event_routing:
            adjusted["event_adjustment_policy"] = event_policy
        return adjusted

    def _mape_label(self, mape: Optional[float]) -> str:
        """Convert MAPE to human label."""
        if not mape:
            return "unknown"
        if mape < 0.02:
            return "Excellent"
        if mape < 0.05:
            return "Good"
        if mape < 0.10:
            return "Fair"
        return "Poor"

    def _breakdown_by_month(
        self, results: List[BacktestResult]
    ) -> List[Dict[str, Any]]:
        """Break down accuracy by month."""

        by_month: Dict[str, List[BacktestResult]] = {}

        for result in results:
            month_key = result.as_of_date[:7]  # YYYY-MM
            if month_key not in by_month:
                by_month[month_key] = []
            by_month[month_key].append(result)

        breakdown = []
        for month in sorted(by_month.keys()):
            month_results = by_month[month]
            errors = [r.error_pct for r in month_results]
            directions = [r.direction_correct for r in month_results]

            breakdown.append(
                {
                    "month": month,
                    "count": len(month_results),
                    "mape": round(sum(errors) / len(errors), 4) if errors else None,
                    "direction_accuracy": (
                        round(sum(directions) / len(directions), 2)
                        if directions
                        else None
                    ),
                }
            )

        return breakdown


# Singleton
backtest_runner = BacktestRunner()
