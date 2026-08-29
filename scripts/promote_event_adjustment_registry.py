"""Promote measured event-adjustment winners into the routing registry.

Reads a forecast quality report produced by scripts/measure_forecast_quality.py
and writes backend/data/event_adjustment_registry.json.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "backend" / "data" / "event_adjustment_registry.json"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_registry(
    report: dict[str, Any],
    *,
    min_backtests: int,
    min_event_applied: int,
    max_abs_adjustment_pct: float,
) -> dict[str, Any]:
    products = report.get("products") if isinstance(report.get("products"), dict) else {}
    registry: dict[str, dict[str, dict[str, Any]]] = {}

    for product_id, product_payload in products.items():
        horizons = product_payload.get("horizons") if isinstance(product_payload, dict) else {}
        if not isinstance(horizons, dict):
            continue
        for horizon_key, comparison in horizons.items():
            if not isinstance(comparison, dict):
                continue
            summaries = comparison.get("summaries") if isinstance(comparison.get("summaries"), dict) else {}
            baseline = summaries.get("champion") if isinstance(summaries.get("champion"), dict) else {}
            event = summaries.get("champion_event_adjusted") if isinstance(summaries.get("champion_event_adjusted"), dict) else {}
            base_mape = _safe_float(baseline.get("mape"))
            event_mape = _safe_float(event.get("mape"))
            base_direction = _safe_float(baseline.get("direction_accuracy"))
            event_direction = _safe_float(event.get("direction_accuracy"))
            total_backtests = _safe_int(event.get("total_backtests"))
            event_applied_count = _safe_int(event.get("event_feature_applied_count"))

            if base_mape is None or event_mape is None:
                continue
            if base_direction is None or event_direction is None:
                continue

            mape_delta = round(event_mape - base_mape, 4)
            direction_delta = round(event_direction - base_direction, 4)
            enabled = (
                total_backtests >= min_backtests
                and event_applied_count >= min_event_applied
                and mape_delta < 0.0
                and direction_delta >= 0.0
            )
            payload: dict[str, Any] = {
                "enabled": enabled,
                "mape_delta": mape_delta,
                "direction_delta": direction_delta,
                "event_applied_count": event_applied_count,
                "total_backtests": total_backtests,
                "max_abs_adjustment_pct": max_abs_adjustment_pct,
            }
            if not enabled:
                payload["reason"] = "event-adjusted forecast did not pass promotion thresholds"
            registry.setdefault(str(product_id), {})[str(horizon_key)] = payload

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": {
            "report": str(report.get("report_path") or ""),
            "start": report.get("start"),
            "end": report.get("end"),
            "step_days": report.get("step_days"),
            "horizons": report.get("horizons"),
            "model_modes": report.get("model_modes"),
            "rule": "enable only when event adjustment improves MAPE, does not reduce direction accuracy, and has enough event-applied samples",
            "min_backtests": min_backtests,
            "min_event_applied": min_event_applied,
        },
        "registry": registry,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="Path to quality report JSON")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Registry JSON output path")
    parser.add_argument("--min-backtests", type=int, default=3)
    parser.add_argument("--min-event-applied", type=int, default=2)
    parser.add_argument("--max-abs-adjustment-pct", type=float, default=3.0)
    args = parser.parse_args()

    with open(args.report, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    report["report_path"] = os.path.abspath(args.report)

    registry_payload = build_registry(
        report,
        min_backtests=args.min_backtests,
        min_event_applied=args.min_event_applied,
        max_abs_adjustment_pct=args.max_abs_adjustment_pct,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(registry_payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    enabled = [
        {"product": product, "horizon": horizon}
        for product, horizons in registry_payload["registry"].items()
        for horizon, payload in horizons.items()
        if payload.get("enabled")
    ]
    print(json.dumps({"out": str(out_path), "enabled": enabled}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
