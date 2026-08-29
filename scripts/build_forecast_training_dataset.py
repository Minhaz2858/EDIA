"""Build leakage-safe forecast learning rows for challenger model training.

This script does not change live forecasts. It creates historical supervised
rows that can be used to evaluate XGBoost and other challengers before any
promotion into production routing.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PRODUCTS = [
    "crude_oil",
    "naphtha",
    "cracked_c5",
    "mixed_c5",
    "cracked_c9",
    "mixed_c9",
    "isoprene",
    "piperylene",
    "dcpd",
    "raffinate_c5",
    "sis",
    "sis_rubber",
    "c5_resin",
    "blowing_agent",
    "styrene",
    "trimethylbenzene",
]

EVENT_DIRECTIONS = ("bullish", "bearish", "neutral", "uncertain")
SOURCE_TIERS = ("tier1", "tier2", "tier3")


def _date_range(start: str, end: str, step_days: int) -> Iterable[str]:
    current = datetime.fromisoformat(start[:10])
    end_dt = datetime.fromisoformat(end[:10])
    step = timedelta(days=max(1, int(step_days)))
    while current <= end_dt:
        yield current.date().isoformat()
        current += step


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _normalise_token(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _product_aliases(product_id: str) -> set[str]:
    try:
        from backend.src.intelligence.product_intelligence import _PRODUCT_ALIASES

        aliases = _PRODUCT_ALIASES.get(product_id, {product_id})
        normalized = {_normalise_token(alias) for alias in aliases if str(alias).strip()}
    except Exception:
        normalized = {_normalise_token(product_id)}
    normalized.add(_normalise_token(product_id))
    if "c5" in product_id:
        normalized.add("c5")
    if "c9" in product_id:
        normalized.add("c9")
    return normalized


def _event_matches_product(event: Any, product_id: str) -> bool:
    aliases = _product_aliases(product_id)
    commodities = getattr(event, "affected_commodities", []) or []
    tokens = {_normalise_token(item) for item in commodities if str(item).strip()}
    if aliases & tokens:
        return True
    text = " ".join(
        _normalise_token(value)
        for value in (
            getattr(event, "headline", ""),
            getattr(event, "causal_chain_hint", ""),
            getattr(event, "key_information", ""),
        )
    )
    return any(alias and alias in text for alias in aliases)


def _event_direction_sign(direction: str) -> float:
    normalized = _normalise_token(direction)
    if normalized == "bullish":
        return 1.0
    if normalized == "bearish":
        return -1.0
    return 0.0


def _event_features(product_id: str, as_of_date: str, lookback_hours: int, limit: int) -> dict[str, Any]:
    try:
        from backend.src.intelligence.event_registry import intelligence_event_registry

        events = intelligence_event_registry.list_events_as_of(
            as_of=as_of_date,
            lookback_hours=lookback_hours,
            min_impact=None,
            review_status=None,
            min_usefulness_score=0,
            limit=limit,
        )
    except Exception:
        events = []

    matched = [event for event in events if _event_matches_product(event, product_id)]
    direction_counts = Counter(_normalise_token(getattr(event, "direction", "uncertain")) for event in matched)
    source_counts = Counter(_normalise_token(getattr(event, "source_credibility", "")) for event in matched)
    type_counts = Counter(_normalise_token(getattr(event, "event_type", "unknown")) for event in matched)
    impact_values = [_safe_float(getattr(event, "impact_magnitude", 0.0)) for event in matched]
    usefulness_values = [_safe_float(getattr(event, "usefulness_score", 0.0)) for event in matched]
    weighted_direction_score = sum(
        _event_direction_sign(getattr(event, "direction", ""))
        * _safe_float(getattr(event, "impact_magnitude", 0.0))
        for event in matched
    )

    features: dict[str, Any] = {
        "event_total_count": len(events),
        "event_matched_count": len(matched),
        "event_avg_impact": round(sum(impact_values) / len(impact_values), 4) if impact_values else 0.0,
        "event_max_impact": round(max(impact_values), 4) if impact_values else 0.0,
        "event_high_impact_count": sum(1 for value in impact_values if value >= 0.6),
        "event_weighted_direction_score": round(weighted_direction_score, 4),
        "event_avg_usefulness": round(sum(usefulness_values) / len(usefulness_values), 4) if usefulness_values else 0.0,
        "event_max_usefulness": round(max(usefulness_values), 4) if usefulness_values else 0.0,
        "event_type_counts_json": json.dumps(dict(type_counts), sort_keys=True),
        "event_evidence_ids_json": json.dumps(
            [str(getattr(event, "event_id", "")) for event in matched if str(getattr(event, "event_id", "")).strip()][:20],
            sort_keys=True,
        ),
    }
    for direction in EVENT_DIRECTIONS:
        features[f"event_{direction}_count"] = int(direction_counts.get(direction, 0))
    for tier in SOURCE_TIERS:
        features[f"event_source_{tier}_count"] = int(source_counts.get(tier, 0))
    return features


def _latest_feature_row(product_id: str, as_of_date: str) -> dict[str, Any]:
    try:
        from backend.src.service.forecasting_engine import build_feature_matrix

        frame = build_feature_matrix(product_id, cutoff_date=as_of_date)
        if frame.empty:
            return {"engine_feature_available": 0}
        row = frame.iloc[-1].to_dict()
        features = {"engine_feature_available": 1}
        for key, value in row.items():
            if key == "price":
                continue
            features[f"feature_{key}"] = round(_safe_float(value), 6)
        return features
    except Exception:
        return {"engine_feature_available": 0}


def _quality_features(product_id: str, as_of_date: str, horizon_days: int) -> dict[str, Any]:
    from backend.src.service.forecast_event_routing import resolve_event_adjustment_policy
    from backend.src.service.forecasting_engine import (
        get_cadence_info,
        get_price_series_quality,
        resolve_champion_model,
    )

    quality = get_price_series_quality(product_id, cutoff_date=as_of_date)
    cadence = get_cadence_info(product_id, cutoff_date=as_of_date)
    champion = resolve_champion_model(product_id, horizon_days)
    event_policy = resolve_event_adjustment_policy(product_id, horizon_days)
    return {
        "raw_observations": int(quality.get("raw_observations") or 0),
        "model_window_days": int(quality.get("model_window_days") or 0),
        "ffill_ratio": _safe_float(quality.get("ffill_ratio"), 1.0),
        "low_data": int(bool(quality.get("low_data"))),
        "cadence_class": str(cadence.get("cadence_class") or "unsupported"),
        "change_frequency": _safe_float(cadence.get("change_frequency"), 0.0),
        "native_step_days": int(cadence.get("native_step_days") or 0),
        "daily_forecast_advised": int(bool(cadence.get("daily_forecast_advised"))),
        "champion_model": str(champion.get("selected_model") or ""),
        "champion_mape": _safe_float(champion.get("mape"), 0.0),
        "champion_challenger_model": str(champion.get("challenger_model") or ""),
        "champion_challenger_mape": _safe_float(champion.get("challenger_mape"), 0.0),
        "event_routing_enabled": int(bool(event_policy.get("enabled"))),
        "event_routing_registry_hit": int(bool(event_policy.get("registry_hit"))),
        "event_routing_mape_delta": _safe_float(event_policy.get("mape_delta"), 0.0),
        "event_routing_direction_delta": _safe_float(event_policy.get("direction_delta"), 0.0),
    }


def _history_points(product_id: str, as_of_date: str) -> list[dict[str, Any]]:
    from backend.src.service.market_data_service import market_data

    points = []
    for point in market_data.get_price_history(product_id, cutoff_date=as_of_date):
        price = _safe_float(point.get("price"))
        date_text = str(point.get("date") or "")[:10]
        if price > 0 and date_text:
            points.append({"date": date_text, "price": price})
    points.sort(key=lambda item: item["date"])
    return points


def _price_at_or_before(points: list[dict[str, Any]], date_text: str) -> float | None:
    for point in reversed(points):
        if point["date"] <= date_text:
            return _safe_float(point.get("price"))
    return None


def _return_since(points: list[dict[str, Any]], as_of_date: str, days: int, current_price: float) -> float:
    anchor_date = (datetime.fromisoformat(as_of_date[:10]) - timedelta(days=days)).date().isoformat()
    anchor_price = _price_at_or_before(points, anchor_date)
    if not anchor_price or anchor_price <= 0:
        return 0.0
    return (current_price - anchor_price) / anchor_price


def _recent_prices(points: list[dict[str, Any]], window: int) -> list[float]:
    return [_safe_float(point.get("price")) for point in points[-window:] if _safe_float(point.get("price")) > 0]


def _pct_returns(prices: list[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(prices, prices[1:]):
        if previous > 0:
            returns.append((current - previous) / previous)
    return returns


def _series_features(product_id: str, as_of_date: str, as_of_price: float) -> dict[str, Any]:
    from backend.src.service.market_data_service import market_data

    points = _history_points(product_id, as_of_date)
    features: dict[str, Any] = {
        "ts_history_points": len(points),
    }
    for days in (7, 14, 30, 60, 90):
        features[f"ts_return_{days}d"] = round(_return_since(points, as_of_date, days, as_of_price), 6)

    for window in (7, 14, 30, 60):
        prices = _recent_prices(points, window)
        returns = _pct_returns(prices)
        min_price = min(prices) if prices else 0.0
        max_price = max(prices) if prices else 0.0
        mean_price = _mean(prices)
        range_pct = (max_price - min_price) / mean_price if mean_price > 0 else 0.0
        position = (as_of_price - min_price) / (max_price - min_price) if max_price > min_price else 0.5
        volatility = math.sqrt(_mean([(value - _mean(returns)) ** 2 for value in returns])) if len(returns) > 1 else 0.0
        features[f"ts_obs_{window}"] = len(prices)
        features[f"ts_ma_{window}"] = round(mean_price, 6)
        features[f"ts_distance_ma_{window}_pct"] = round((as_of_price - mean_price) / mean_price, 6) if mean_price > 0 else 0.0
        features[f"ts_range_{window}_pct"] = round(range_pct, 6)
        features[f"ts_position_{window}"] = round(max(0.0, min(1.0, position)), 6)
        features[f"ts_volatility_{window}"] = round(volatility, 6)
        features[f"ts_avg_abs_return_{window}"] = round(_mean([abs(value) for value in returns]), 6)

    features["ts_momentum_accel_7_30"] = round(features["ts_return_7d"] - (features["ts_return_30d"] / 4.0), 6)
    features["ts_momentum_accel_14_60"] = round(features["ts_return_14d"] - (features["ts_return_60d"] / 4.0), 6)
    for upstream_id in ("crude_oil", "naphtha"):
        upstream_price = market_data.get_price_on_date(upstream_id, as_of_date)
        upstream_price = _safe_float(upstream_price)
        if upstream_price > 0:
            features[f"upstream_{upstream_id}_ratio"] = round(as_of_price / upstream_price, 6)
            features[f"upstream_{upstream_id}_spread_pct"] = round((as_of_price - upstream_price) / upstream_price, 6)
        else:
            features[f"upstream_{upstream_id}_ratio"] = 0.0
            features[f"upstream_{upstream_id}_spread_pct"] = 0.0
    return features


def _build_row(
    product_id: str,
    as_of_date: str,
    horizon_days: int,
    event_lookback_hours: int,
    event_limit: int,
) -> dict[str, Any] | None:
    from backend.src.service.market_data_service import market_data

    as_of_price = market_data.get_price_on_date(product_id, as_of_date)
    if not as_of_price or as_of_price <= 0:
        return None

    target_date = (datetime.fromisoformat(as_of_date[:10]) + timedelta(days=horizon_days)).date().isoformat()
    actual_price = market_data.get_price_on_date(product_id, target_date)
    if not actual_price or actual_price <= 0:
        return None

    as_of_price = float(as_of_price)
    actual_price = float(actual_price)
    target_change_pct = (actual_price - as_of_price) / as_of_price if as_of_price else 0.0
    row: dict[str, Any] = {
        "product_id": product_id,
        "as_of_date": as_of_date,
        "target_date": target_date,
        "horizon_days": int(horizon_days),
        "as_of_price": round(as_of_price, 6),
        "actual_price": round(actual_price, 6),
        "target_change_pct": round(target_change_pct, 6),
        "target_abs_change_pct": round(abs(target_change_pct), 6),
        "target_direction": 1 if target_change_pct >= 0 else -1,
        "naive_forecast_price": round(as_of_price, 6),
        "naive_error_pct": round(abs(as_of_price - actual_price) / actual_price, 6),
    }
    row.update(_quality_features(product_id, as_of_date, horizon_days))
    row.update(_latest_feature_row(product_id, as_of_date))
    row.update(_series_features(product_id, as_of_date, as_of_price))
    row.update(_event_features(product_id, as_of_date, event_lookback_hours, event_limit))
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row.keys()}) if rows else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not fields:
            return
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Output CSV or JSONL path")
    parser.add_argument("--format", choices=["csv", "jsonl"], default="csv")
    parser.add_argument("--start", default="2026-01-15")
    parser.add_argument("--end", default="2026-04-15")
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument("--horizons", nargs="*", type=int, default=[1, 3, 7, 14, 30])
    parser.add_argument("--event-lookback-hours", type=int, default=168)
    parser.add_argument("--event-limit", type=int, default=80)
    args = parser.parse_args()

    os.environ.setdefault("DISABLE_RAG", "true")
    rows: list[dict[str, Any]] = []
    skipped = 0
    products = [str(product).strip() for product in args.products if str(product).strip()]
    horizons = [int(horizon) for horizon in args.horizons]
    for product_id in products:
        for as_of_date in _date_range(args.start, args.end, args.step_days):
            for horizon_days in horizons:
                row = _build_row(
                    product_id=product_id,
                    as_of_date=as_of_date,
                    horizon_days=horizon_days,
                    event_lookback_hours=int(args.event_lookback_hours),
                    event_limit=int(args.event_limit),
                )
                if row is None:
                    skipped += 1
                    continue
                rows.append(row)

    out_path = Path(args.out)
    if args.format == "jsonl":
        _write_jsonl(out_path, rows)
    else:
        _write_csv(out_path, rows)

    print(
        json.dumps(
            {
                "out": str(out_path),
                "format": args.format,
                "rows": len(rows),
                "skipped": skipped,
                "products": products,
                "horizons": horizons,
                "start": args.start,
                "end": args.end,
                "step_days": args.step_days,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())