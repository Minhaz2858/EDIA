"""Canonical forecast snapshot adapter.

This module is the bridge between EDIA's live forecasting service and surfaces
that need reproducible forecast numbers, such as weekly reports.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from sqlalchemy.engine import Engine

from backend.src.db.forecast_snapshots import (
    get_latest_approved_snapshot,
    persist_snapshot,
)
from backend.src.service.forecasting_service import forecasting_service
from backend.src.service.weekly_report_data import ForecastBand


_DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_FORECAST_PRODUCT_ALIASES = {
    "road_marking_resin": "c5_resin",
    "adhesive_resin": "c5_resin",
    "pentane_blowing_agent": "blowing_agent",
}


def resolve_forecast_product_id(product_id: str) -> str:
    normalized = str(product_id or "").strip()
    return _FORECAST_PRODUCT_ALIASES.get(normalized, normalized)


def _same_as_of_date(payload: Dict[str, Any], as_of_date: Optional[str]) -> bool:
    requested = str(as_of_date or "").strip()[:10]
    actual = str(payload.get("as_of_date") or "").strip()[:10]
    return actual == requested


def _confidence_score(label: str) -> int:
    normalized = str(label or "").strip().lower()
    if normalized in {"high", "高", "较高", "medium-high"}:
        return 4
    if normalized in {"medium", "中", "medium-low"}:
        return 3
    if normalized in {"low", "低"}:
        return 2
    return 3


def _localized_confidence(label: str, language: str) -> str:
    is_en = (language or "zh").lower().startswith("en")
    normalized = str(label or "").strip().lower()
    if is_en:
        if normalized in {"高", "较高", "high"}:
            return "High"
        if normalized in {"中", "medium"}:
            return "Medium"
        return "Low" if normalized in {"低", "low"} else "Medium"
    if normalized in {"high", "高"}:
        return "高"
    if normalized in {"medium-high", "较高"}:
        return "较高"
    if normalized in {"medium", "中"}:
        return "中"
    return "低" if normalized in {"low", "低"} else "中"


def _trend_verb(snapshot: Dict[str, Any], base_low: float, base_high: float, language: str) -> str:
    latest = snapshot.get("latest_price")
    try:
        latest_price = float(latest)
    except (TypeError, ValueError):
        latest_price = 0.0

    midpoint = (base_low + base_high) / 2.0
    pct = ((midpoint - latest_price) / latest_price * 100.0) if latest_price > 0 else 0.0
    is_en = (language or "zh").lower().startswith("en")
    if pct >= 1.0:
        return "move higher" if is_en else "稳中有涨"
    if pct <= -1.0:
        return "face downside pressure" if is_en else "承压运行"
    return "remain stable" if is_en else "走稳"


def _fmt_price(value: float) -> str:
    return f"{float(value):,.0f}"


def _scenario_band(snapshot: Dict[str, Any], horizon: str, scenario: str) -> Optional[Dict[str, float]]:
    bucket = ((snapshot.get("scenarios") or {}).get(horizon) or {}).get(scenario) or {}
    low = bucket.get("low")
    high = bucket.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None
    return {"low": float(low), "high": float(high)}


def get_or_create_forecast_snapshot(
    engine: Engine,
    product_id: str,
    *,
    as_of_date: Optional[str] = None,
    diagnosis: Optional[Dict[str, Any]] = None,
    intelligence_context: Optional[Dict[str, Any]] = None,
    hermes_plan: Optional[Dict[str, Any]] = None,
    run_reason: str = "canonical_forecast",
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
    policy_mode: str = "adaptive",
) -> Dict[str, Any]:
    """Return the canonical stored forecast payload for product/date.

    If no matching snapshot exists, this function generates one with the shared
    forecasting service, persists it, and returns the generated payload.
    """
    forecast_product_id = resolve_forecast_product_id(product_id)
    latest = get_latest_approved_snapshot(
        engine,
        forecast_product_id,
        max_age_seconds=max_age_seconds,
    )
    if latest and _same_as_of_date(latest, as_of_date):
        latest = copy.deepcopy(latest)
        latest["requested_product_id"] = product_id
        latest["forecast_product_id"] = forecast_product_id
        return latest

    forecast = forecasting_service.forecast(
        forecast_product_id,
        diagnosis=diagnosis,
        intelligence_context=intelligence_context,
        as_of_date=as_of_date,
        policy_mode=policy_mode,
        hermes_plan=hermes_plan,
    )
    payload = copy.deepcopy(forecast)
    payload["requested_product_id"] = product_id
    payload["forecast_product_id"] = forecast_product_id
    payload["forecast_snapshot"] = {
        **(payload.get("forecast_snapshot") if isinstance(payload.get("forecast_snapshot"), dict) else {}),
        "validation_status": "approved_forecasting_service",
        "source": "forecasting_service",
    }
    payload.setdefault("cause_summary", payload.get("method_notes") or "Canonical EDIA forecast.")
    snapshot_id = persist_snapshot(engine, payload, run_reason=run_reason)
    payload["snapshot_id"] = snapshot_id
    payload["forecast_snapshot"] = {
        **(payload.get("forecast_snapshot") if isinstance(payload.get("forecast_snapshot"), dict) else {}),
        "snapshot_id": snapshot_id,
        "source": "generated_snapshot",
        "run_reason": run_reason,
    }
    return payload


def forecast_snapshot_to_band(
    snapshot: Dict[str, Any],
    *,
    horizon: str = "7d",
    language: str = "zh",
) -> ForecastBand:
    """Convert a canonical forecast snapshot into weekly-report ForecastBand."""
    is_en = (language or "zh").lower().startswith("en")
    band = _scenario_band(snapshot, horizon, "base")
    if not band:
        return ForecastBand(
            period="next week" if is_en else "下周",
            trend_verb="remain stable" if is_en else "走稳",
            low=None,
            high=None,
            confidence_label="Low" if is_en else "低",
            confidence_score=2,
            model_version="canonical_unavailable",
            source="Forecasting Service",
        )

    supervisor = snapshot.get("forecast_supervisor") if isinstance(snapshot.get("forecast_supervisor"), dict) else {}
    confidence_label = _localized_confidence(str(supervisor.get("confidence_label") or "Medium"), language)
    snapshot_meta = snapshot.get("forecast_snapshot") if isinstance(snapshot.get("forecast_snapshot"), dict) else {}
    policy = snapshot.get("policy") if isinstance(snapshot.get("policy"), dict) else {}
    snapshot_id = str(snapshot_meta.get("snapshot_id") or snapshot.get("snapshot_id") or "").strip()
    model_version = str(snapshot.get("model_version") or "forecasting_service")
    if snapshot_id:
        model_version = f"{model_version}:{snapshot_id[:12]}"

    return ForecastBand(
        period="next week" if is_en else "下周",
        trend_verb=_trend_verb(snapshot, band["low"], band["high"], language),
        low=_fmt_price(band["low"]),
        high=_fmt_price(band["high"]),
        confidence_label=confidence_label,
        confidence_score=_confidence_score(confidence_label),
        model_version=model_version,
        source="Forecasting Service",
        snapshot_id=snapshot_id or None,
        as_of_date=str(snapshot.get("as_of_date") or "")[:10] or None,
        generated_at=str(snapshot_meta.get("generated_at") or "") or None,
        policy_id=str(policy.get("policy_id") or "") or None,
        horizon=horizon,
    )


def forecast_snapshot_to_report_context(snapshot: Dict[str, Any], *, horizon: str = "7d") -> Dict[str, Any]:
    snapshot_meta = snapshot.get("forecast_snapshot") if isinstance(snapshot.get("forecast_snapshot"), dict) else {}
    policy = snapshot.get("policy") if isinstance(snapshot.get("policy"), dict) else {}
    return {
        "forecast_snapshot_id": snapshot_meta.get("snapshot_id") or snapshot.get("snapshot_id"),
        "forecast_product_id": snapshot.get("forecast_product_id") or snapshot.get("product_id"),
        "requested_product_id": snapshot.get("requested_product_id"),
        "forecast_model_version": snapshot.get("model_version"),
        "policy_id": policy.get("policy_id"),
        "generated_at": snapshot_meta.get("generated_at"),
        "as_of_date": snapshot.get("as_of_date"),
        "horizon": horizon,
        "source": snapshot_meta.get("source") or "forecasting_service",
    }
