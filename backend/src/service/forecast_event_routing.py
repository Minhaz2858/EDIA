from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict


_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "event_adjustment_registry.json"

_EVENT_ROUTING_SOURCE = {
    "report": "/tmp/edia_forecast_quality_event_replay_full.json",
    "window": "2026-04-10..2026-04-28",
    "step_days": 6,
    "model_modes": ["champion", "champion_event_adjusted"],
    "rule": "enable only when event adjustment improved MAPE and did not reduce direction accuracy",
}

_EVENT_ADJUSTMENT_REGISTRY: dict[str, dict[int, dict[str, Any]]] = {
    "cracked_c9": {
        7: {
            "enabled": True,
            "mape_delta": -0.0004,
            "direction_delta": 0.0,
            "event_applied_count": 4,
            "total_backtests": 4,
            "max_abs_adjustment_pct": 3.0,
        },
    },
    "mixed_c9": {
        7: {
            "enabled": True,
            "mape_delta": -0.0004,
            "direction_delta": 0.0,
            "event_applied_count": 4,
            "total_backtests": 4,
            "max_abs_adjustment_pct": 3.0,
        },
    },
    "c5_resin": {
        7: {
            "enabled": True,
            "mape_delta": -0.0087,
            "direction_delta": 0.25,
            "event_applied_count": 4,
            "total_backtests": 4,
            "max_abs_adjustment_pct": 3.0,
        },
        30: {
            "enabled": False,
            "mape_delta": 0.0025,
            "direction_delta": 0.0,
            "event_applied_count": 1,
            "total_backtests": 1,
            "reason": "insufficient 30d validation and worse MAPE",
        },
    },
}


def _normalise_registry(raw_registry: Any) -> dict[str, dict[int, dict[str, Any]]]:
    if not isinstance(raw_registry, dict):
        return {}
    normalized: dict[str, dict[int, dict[str, Any]]] = {}
    for product_id, horizons in raw_registry.items():
        if not isinstance(horizons, dict):
            continue
        normalized_horizons: dict[int, dict[str, Any]] = {}
        for horizon, payload in horizons.items():
            if not isinstance(payload, dict):
                continue
            try:
                horizon_key = int(horizon)
            except (TypeError, ValueError):
                continue
            normalized_horizons[horizon_key] = dict(payload)
        if normalized_horizons:
            normalized[str(product_id)] = normalized_horizons
    return normalized


def _load_registry_payload() -> dict[str, Any]:
    try:
        with _REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {
        "source": copy.deepcopy(_EVENT_ROUTING_SOURCE),
        "registry": copy.deepcopy(_EVENT_ADJUSTMENT_REGISTRY),
    }


def _load_event_adjustment_registry() -> dict[str, dict[int, dict[str, Any]]]:
    payload = _load_registry_payload()
    registry = _normalise_registry(payload.get("registry"))
    return registry or copy.deepcopy(_EVENT_ADJUSTMENT_REGISTRY)


def _load_event_routing_source() -> dict[str, Any]:
    payload = _load_registry_payload()
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return source or copy.deepcopy(_EVENT_ROUTING_SOURCE)


def get_event_adjustment_registry() -> dict[str, dict[int, dict[str, Any]]]:
    registry = _load_event_adjustment_registry()
    return {
        product_id: {horizon: dict(payload) for horizon, payload in horizons.items()}
        for product_id, horizons in registry.items()
    }


def resolve_event_adjustment_policy(product_id: str, horizon_days: int) -> dict[str, Any]:
    selected_horizon = 7 if int(horizon_days or 0) <= 7 else 30
    source = _load_event_routing_source()
    registry = _load_event_adjustment_registry()
    payload = (registry.get(product_id) or {}).get(selected_horizon)
    if payload is None:
        return {
            "enabled": False,
            "registry_hit": False,
            "product_id": product_id,
            "requested_horizon_days": horizon_days,
            "selected_horizon_days": selected_horizon,
            "source": dict(source),
            "reason": "event adjustment has not beaten champion baseline for this product/horizon",
        }
    result = dict(payload)
    result.update(
        {
            "registry_hit": True,
            "product_id": product_id,
            "requested_horizon_days": horizon_days,
            "selected_horizon_days": selected_horizon,
            "source": dict(source),
        }
    )
    return result


def should_apply_event_adjustment(product_id: str, horizon_days: int) -> bool:
    return bool(resolve_event_adjustment_policy(product_id, horizon_days).get("enabled"))
