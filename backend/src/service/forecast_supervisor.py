from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


DEFAULT_HORIZONS = ("3d", "7d", "30d")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _midpoint(bucket: Dict[str, Any]) -> Optional[float]:
    low = _safe_float(bucket.get("low"))
    high = _safe_float(bucket.get("high"))
    if low is None or high is None or low > high:
        return None
    return (low + high) / 2.0


def _required_horizons(hermes_plan: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(hermes_plan, dict):
        return list(DEFAULT_HORIZONS)
    forecast_policy = hermes_plan.get("forecast_policy") if isinstance(hermes_plan.get("forecast_policy"), dict) else {}
    horizons = forecast_policy.get("horizons") if isinstance(forecast_policy, dict) else None
    if not isinstance(horizons, list) or not horizons:
        return list(DEFAULT_HORIZONS)
    resolved = [str(item).strip().lower() for item in horizons if str(item).strip()]
    return resolved or list(DEFAULT_HORIZONS)


class ForecastSupervisor:
    """Validate whether a computed forecast is ready for user-facing narration."""

    def supervise(
        self,
        forecast: Dict[str, Any],
        *,
        hermes_plan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        required_horizons = _required_horizons(hermes_plan)
        scenarios = forecast.get("scenarios") if isinstance(forecast.get("scenarios"), dict) else {}
        policy = forecast.get("policy") if isinstance(forecast.get("policy"), dict) else {}
        metrics = policy.get("metrics") if isinstance(policy.get("metrics"), dict) else {}

        issues: list[dict[str, str]] = []
        disclosures: list[str] = []
        gates: dict[str, str] = {}

        latest_price = _safe_float(forecast.get("latest_price"))
        if latest_price is None or latest_price <= 0:
            gates["latest_price_available"] = "fail"
            issues.append({"severity": "blocker", "code": "latest_price_unavailable", "message": "Forecast has no usable latest price."})
        else:
            gates["latest_price_available"] = "pass"

        missing_horizons = [horizon for horizon in required_horizons if horizon not in scenarios]
        if missing_horizons:
            gates["forecast_horizons_present"] = "fail"
            issues.append({"severity": "blocker", "code": "missing_horizons", "message": f"Missing forecast horizons: {', '.join(missing_horizons)}."})
        else:
            gates["forecast_horizons_present"] = "pass"

        invalid_bands = self._invalid_band_horizons(scenarios, required_horizons)
        if invalid_bands:
            gates["scenario_band_sanity"] = "fail"
            issues.append({"severity": "blocker", "code": "invalid_scenario_bands", "message": f"Invalid scenario band ordering for: {', '.join(invalid_bands)}."})
        else:
            gates["scenario_band_sanity"] = "pass"

        recent_mape = _safe_float(metrics.get("recent_mape"))
        sample_count = int(_safe_float(metrics.get("sample_count")) or 0)
        if recent_mape is None:
            gates["recent_mape_available_or_disclosed"] = "warn"
            disclosures.append("Recent MAPE is unavailable; confidence is capped until enough evaluated forecasts accumulate.")
            issues.append({"severity": "warning", "code": "recent_mape_unavailable", "message": "Recent MAPE is not available."})
        else:
            gates["recent_mape_available_or_disclosed"] = "pass"
            disclosures.append(f"Recent MAPE: {recent_mape:.2f}% from {sample_count} evaluated sample(s).")
            if recent_mape >= 18.0:
                issues.append({"severity": "blocker", "code": "recent_mape_very_high", "message": f"Recent MAPE is very high at {recent_mape:.2f}%."})
            elif recent_mape >= 10.0:
                issues.append({"severity": "warning", "code": "recent_mape_high", "message": f"Recent MAPE is high at {recent_mape:.2f}%."})

        confidence = self._confidence_from_mape(recent_mape, sample_count)
        gates["confidence_matches_recent_accuracy"] = "pass" if confidence >= 0.6 else "warn"

        if any(issue["severity"] == "blocker" for issue in issues):
            status = "insufficient_data"
            narrative_policy = "Do not present point guidance; explain missing or invalid forecast evidence first."
        elif any(issue["severity"] == "warning" for issue in issues):
            status = "needs_caveat"
            narrative_policy = "Present scenario ranges with uncertainty language and required disclosures."
        else:
            status = "publishable"
            narrative_policy = "Present forecast ranges as model inference, separate from verified history."

        return {
            "status": status,
            "confidence": round(confidence, 2),
            "confidence_label": self._confidence_label(confidence),
            "quality_gates": gates,
            "issues": issues,
            "required_disclosures": disclosures,
            "required_horizons": required_horizons,
            "narrative_policy": narrative_policy,
        }

    @staticmethod
    def _invalid_band_horizons(scenarios: Dict[str, Any], horizons: Iterable[str]) -> List[str]:
        invalid: list[str] = []
        for horizon in horizons:
            scenario = scenarios.get(horizon) if isinstance(scenarios.get(horizon), dict) else {}
            bear = _midpoint(scenario.get("bear") or {})
            base = _midpoint(scenario.get("base") or {})
            bull = _midpoint(scenario.get("bull") or {})
            if bear is None or base is None or bull is None or not (bear <= base <= bull):
                invalid.append(horizon)
        return invalid

    @staticmethod
    def _confidence_from_mape(recent_mape: Optional[float], sample_count: int) -> float:
        if recent_mape is None:
            return 0.55 if sample_count == 0 else 0.6
        if recent_mape <= 5.0:
            return 0.82
        if recent_mape <= 10.0:
            return 0.68
        if recent_mape <= 18.0:
            return 0.52
        return 0.35

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        if confidence >= 0.75:
            return "high"
        if confidence >= 0.6:
            return "medium"
        if confidence >= 0.45:
            return "low"
        return "very_low"


forecast_supervisor = ForecastSupervisor()


# ── P0: Forecast Trust Tier ────────────────────────────────────────────────────

# Products with documented genuine ARIMA skill (skill_vs_naive < 0 in backtest).
# These get the "high" trust tier.
_HIGH_SKILL_PRODUCTS: frozenset[str] = frozenset({
    "isoprene",
    "piperylene",
    "c5_resin",
    "dcpd",
})

# Products that trade weekly or less frequently; daily forecast is misleading.
# These get the "directional" tier even if they have some model signal.
_WEEKLY_CADENCE_PRODUCTS: frozenset[str] = frozenset({
    "cracked_c9",
    "mixed_c9",
    "trimethylbenzene",
    "raffinate_c5",
})


def compute_forecast_trust_tier(
    product_id: str,
    *,
    below_naive: Optional[bool] = None,
    cadence_class: Optional[str] = None,
    mape: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute a user-facing trust tier for a product's forecast.

    Returns
    -------
    dict with keys:
        tier: "high" | "medium" | "directional" | "low"
        below_naive: bool
        cadence_class: str
        mape: float | None
        skill_vs_naive: float | None  (negative = beats naive, positive = worse than naive)
        reason: str  (human-readable explanation)
        badge_color: str  (CSS color class name for frontend)
        badge_label_zh: str
        badge_label_en: str
    """
    from backend.src.service.forecasting_engine import (
        _BELOW_NAIVE_PRODUCTS,
        get_cadence_info,
    )

    # Resolve inputs
    if below_naive is None:
        below_naive = product_id in _BELOW_NAIVE_PRODUCTS

    if cadence_class is None:
        try:
            cadence_class = get_cadence_info(product_id).get("cadence_class", "unknown")
        except Exception:
            cadence_class = "unknown"

    # ── Tier logic ────────────────────────────────────────────────────────────

    if cadence_class in ("sparse", "unsupported", "unknown"):
        tier = "low"
        badge_color = "red"
        badge_label_zh = "数据不足"
        badge_label_en = "Insufficient Data"
        reason_zh = "历史数据稀疏或不支持预测，建议查看市场定性分析。"
        reason_en = "Historical data is too sparse or unsupported for forecasting. Refer to qualitative market analysis."

    elif below_naive or product_id in _BELOW_NAIVE_PRODUCTS:
        tier = "directional"
        badge_color = "orange"
        badge_label_zh = "方向性参考"
        badge_label_en = "Directional Only"
        if product_id in _WEEKLY_CADENCE_PRODUCTS:
            reason_zh = (
                "该产品周频交易，日预测精度有限；当前模型弱于简单基准。仅供参考方向。"
            )
            reason_en = (
                "This product trades weekly; daily forecast precision is limited and "
                "the current model underperforms a naive baseline. Use for direction only."
            )
        else:
            reason_zh = (
                "当前模型预测误差高于简单基准（持平最新价格），建议参考市场定性判断。"
            )
            reason_en = (
                "Current model MAPE exceeds a naive baseline (last-price hold); "
                "use qualitative market judgment alongside."
            )

    elif cadence_class == "weekly" and product_id not in _HIGH_SKILL_PRODUCTS:
        tier = "medium"
        badge_color = "yellow"
        badge_label_zh = "周参考"
        badge_label_en = "Weekly Reference"
        reason_zh = "周频产品，日预测仅供参考；建议以周均价为主要参考。"
        reason_en = (
            "Weekly-trading product; daily forecast is indicative only. "
            "Use weekly average price as the primary reference."
        )

    elif product_id in _HIGH_SKILL_PRODUCTS and not below_naive:
        tier = "high"
        badge_color = "green"
        badge_label_zh = "高置信"
        badge_label_en = "High Confidence"
        mape_str = f"（MAPE {mape:.1f}%）" if mape is not None else ""
        reason_zh = f"模型在历史回测中持续跑赢简单基准，置信度较高{mape_str}。"
        reason_en = f"Model consistently beats naive baseline in backtesting; high confidence.{mape_str if mape is not None else ''}"

    else:
        # Default: medium
        tier = "medium"
        badge_color = "yellow"
        badge_label_zh = "中置信"
        badge_label_en = "Medium Confidence"
        reason_zh = "模型有一定预测能力，但需结合市场判断使用。"
        reason_en = "Model has some predictive skill; use alongside qualitative market judgment."

    reason = reason_zh if True else reason_en  # language decision deferred to caller

    return {
        "tier": tier,
        "below_naive": below_naive,
        "cadence_class": cadence_class,
        "mape": mape,
        "skill_vs_naive": float(mape) if mape is not None else None,
        "reason_zh": reason_zh,
        "reason_en": reason_en,
        "badge_color": badge_color,
        "badge_label_zh": badge_label_zh,
        "badge_label_en": badge_label_en,
    }