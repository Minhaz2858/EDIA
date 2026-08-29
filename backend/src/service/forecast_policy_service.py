from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict

from backend.src.db.daily_calibration import get_forecast_accuracy_history
from backend.src.db.sqlite_db import db as _app_db
from backend.src.service.market_data_service import market_data


@dataclass
class ForecastPolicyMetrics:
    sample_count: int
    recent_mape: float | None
    mean_signed_error_pct: float
    recent_volatility_pct: float
    trailing_14d_change_pct: float
    regime: str


def _safe_pct_change(current_value: float, previous_value: float) -> float:
    if previous_value == 0:
        return 0.0
    return (current_value - previous_value) / previous_value * 100.0


def _compute_recent_metrics(product_id: str, as_of_date: str | None = None) -> ForecastPolicyMetrics:
    records = get_forecast_accuracy_history(
        _app_db.engine,
        product_id,
        days_back=45,
        end_date=as_of_date,
    )
    if as_of_date:
        as_of = date.fromisoformat(as_of_date[:10])
        records = [
            record
            for record in records
            if str(record.get("target_date") or "")[:10] < as_of.isoformat()
        ]
    else:
        as_of = None
    evaluated = [
        record
        for record in records
        if record.get("status") == "evaluated"
        and isinstance(record.get("predicted_price"), (int, float))
        and isinstance(record.get("actual_price"), (int, float))
    ]

    recent_records = evaluated[:14]
    signed_errors: list[float] = []
    abs_errors: list[float] = []
    for record in recent_records:
        actual = float(record["actual_price"])
        predicted = float(record["predicted_price"])
        if actual == 0:
            continue
        signed_error = (predicted - actual) / actual * 100.0
        signed_errors.append(signed_error)
        abs_errors.append(abs(signed_error))

    history = market_data.get_price_history(product_id, cutoff_date=as_of.isoformat() if as_of else None)[-30:]
    recent_prices = [float(point.get("price") or 0.0) for point in history if point.get("price") is not None]
    daily_changes = [
        _safe_pct_change(recent_prices[idx], recent_prices[idx - 1])
        for idx in range(1, len(recent_prices))
        if recent_prices[idx - 1] != 0
    ]
    if daily_changes:
        mean_change = sum(daily_changes) / len(daily_changes)
        variance = sum((value - mean_change) ** 2 for value in daily_changes) / len(daily_changes)
        recent_volatility = variance ** 0.5
    else:
        recent_volatility = 0.0

    trailing_change = 0.0
    if len(recent_prices) >= 14 and recent_prices[-14] != 0:
        trailing_change = _safe_pct_change(recent_prices[-1], recent_prices[-14])

    if recent_volatility >= 3.5:
        regime = "high_volatility"
    elif abs(trailing_change) >= 8.0:
        regime = "strong_trend"
    else:
        regime = "steady_state"

    return ForecastPolicyMetrics(
        sample_count=len(signed_errors),
        recent_mape=round(sum(abs_errors) / len(abs_errors), 2) if abs_errors else None,
        mean_signed_error_pct=round(sum(signed_errors) / len(signed_errors), 4) if signed_errors else 0.0,
        recent_volatility_pct=round(recent_volatility, 4),
        trailing_14d_change_pct=round(trailing_change, 4),
        regime=regime,
    )


class ForecastPolicyService:
    def apply(
        self,
        product_id: str,
        horizons: Dict[str, Dict[str, Any]],
        diagnosis: Dict[str, Any] | None = None,
        as_of_date: str | None = None,
    ) -> Dict[str, Any]:
        metrics = _compute_recent_metrics(product_id, as_of_date=as_of_date)

        bias_correction = 0.0
        if metrics.sample_count >= 3:
            bias_correction = max(-2.5, min(2.5, -metrics.mean_signed_error_pct * 0.35))

        volatility_multiplier = 1.0
        if metrics.regime == "high_volatility":
            volatility_multiplier = 1.25
        elif metrics.regime == "strong_trend":
            volatility_multiplier = 1.1

        if metrics.recent_mape is not None and metrics.recent_mape >= 10.0:
            volatility_multiplier = max(volatility_multiplier, 1.2)

        net_signal = str((diagnosis or {}).get("net_signal") or "").lower()
        signal_bias = 0.0
        if net_signal == "bullish":
            signal_bias = 0.2
        elif net_signal == "bearish":
            signal_bias = -0.2

        horizon_scale = {"3d": 1.0, "7d": 0.8, "30d": 0.55}
        adjusted_horizons: Dict[str, Dict[str, Any]] = {}
        for horizon_name, payload in horizons.items():
            scale = horizon_scale.get(horizon_name, 0.5)
            adjusted_payload = dict(payload)
            adjusted_payload["base_pct"] = round(
                float(payload.get("base_pct") or 0.0) + bias_correction * scale + signal_bias,
                4,
            )
            adjusted_payload["vol_pct"] = round(
                max(0.1, float(payload.get("vol_pct") or 0.0) * volatility_multiplier),
                4,
            )
            adjusted_horizons[horizon_name] = adjusted_payload

        policy_id = f"adaptive_{metrics.regime}"
        return {
            "policy_id": policy_id,
            "model_version": "v1_policy",
            "metrics": {
                "sample_count": metrics.sample_count,
                "recent_mape": metrics.recent_mape,
                "mean_signed_error_pct": metrics.mean_signed_error_pct,
                "recent_volatility_pct": metrics.recent_volatility_pct,
                "trailing_14d_change_pct": metrics.trailing_14d_change_pct,
                "regime": metrics.regime,
                "bias_correction_pct": round(bias_correction, 4),
                "volatility_multiplier": round(volatility_multiplier, 4),
            },
            "horizons": adjusted_horizons,
        }


forecast_policy_service = ForecastPolicyService()