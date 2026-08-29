"""Daily forecast-event quality cycle.

The Admin News Table already ingests news/events every 6 hours. This script is
for the daily forecasting loop that consumes those persisted events:
1. records event/feature snapshot status,
2. runs champion vs event-adjusted replay quality,
3. promotes measured winners into event_adjustment_registry.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "backend" / "data" / "forecast_quality_reports"
REGISTRY_PATH = ROOT / "backend" / "data" / "event_adjustment_registry.json"
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


def _utcnow() -> datetime:
    return datetime.utcnow()


def _event_summary() -> dict[str, Any]:
    from backend.src.db.sqlite_db import db

    with db.engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM intelligence_events")).scalar() or 0
        bounds = conn.execute(
            text("SELECT MIN(detected_at), MAX(detected_at) FROM intelligence_events")
        ).fetchone()
        recent_24h = conn.execute(
            text("SELECT COUNT(*) FROM intelligence_events WHERE detected_at >= :cutoff"),
            {"cutoff": (_utcnow() - timedelta(hours=24)).isoformat()},
        ).scalar() or 0
        recent_168h = conn.execute(
            text("SELECT COUNT(*) FROM intelligence_events WHERE detected_at >= :cutoff"),
            {"cutoff": (_utcnow() - timedelta(hours=168)).isoformat()},
        ).scalar() or 0
        statuses = [
            dict(row._mapping)
            for row in conn.execute(
                text(
                    "SELECT review_status, COUNT(*) AS total "
                    "FROM intelligence_events GROUP BY review_status ORDER BY total DESC"
                )
            )
        ]
    return {
        "total_events": int(total),
        "first_detected_at": bounds[0] if bounds else None,
        "latest_detected_at": bounds[1] if bounds else None,
        "recent_24h": int(recent_24h),
        "recent_168h": int(recent_168h),
        "review_statuses": statuses,
    }


def _feature_snapshot_status(refresh_features: bool) -> dict[str, Any]:
    from backend.src.service.kimi_forecast_feature_service import kimi_forecast_feature_service

    refreshed = None
    if refresh_features:
        if kimi_forecast_feature_service.llm is None:
            refreshed = {
                "skipped": True,
                "reason": "no LLM service configured in standalone script; server background loop can refresh every 6h",
            }
        else:
            refreshed = kimi_forecast_feature_service.refresh_snapshot()
    latest = kimi_forecast_feature_service.get_latest_snapshot(include_expired=True)
    if not latest:
        return {"available": False, "refresh": refreshed}
    features = latest.get("features") if isinstance(latest.get("features"), dict) else {}
    products = features.get("products") if isinstance(features.get("products"), dict) else {}
    return {
        "available": True,
        "snapshot_id": latest.get("snapshot_id"),
        "generated_at": latest.get("generated_at"),
        "expires_at": latest.get("expires_at"),
        "status": latest.get("status"),
        "model": latest.get("model"),
        "product_count": len(products),
        "refresh": refreshed,
    }


def _latest_price_date(products: list[str]) -> str | None:
    from backend.src.service.market_data_service import market_data

    latest_dates: list[str] = []
    for product_id in products:
        try:
            history = market_data.get_price_history(product_id)
        except Exception:
            history = []
        if history:
            latest = str(history[-1].get("date") or "").strip()
            if latest:
                latest_dates.append(latest[:10])
    return max(latest_dates) if latest_dates else None


def _default_start(event_summary: dict[str, Any], lookback_days: int) -> str:
    latest_event = str(event_summary.get("latest_detected_at") or "").strip()
    first_event = str(event_summary.get("first_detected_at") or "").strip()
    if latest_event:
        latest_dt = datetime.fromisoformat(latest_event[:19])
        first_candidate = latest_dt - timedelta(days=lookback_days)
        if first_event:
            first_dt = datetime.fromisoformat(first_event[:19])
            first_candidate = max(first_candidate, first_dt)
        return first_candidate.date().isoformat()
    return (_utcnow() - timedelta(days=lookback_days)).date().isoformat()


def _default_end(products: list[str], max_horizon_days: int) -> str:
    latest_price = _latest_price_date(products)
    if latest_price:
        return (datetime.fromisoformat(latest_price[:10]) - timedelta(days=max_horizon_days)).date().isoformat()
    return (_utcnow() - timedelta(days=max_horizon_days)).date().isoformat()


def _run_command(command: list[str], env: dict[str, str]) -> None:
    subprocess.run(command, cwd=str(ROOT), env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(REPORT_DIR))
    parser.add_argument("--registry-out", default=str(REGISTRY_PATH))
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--lookback-days", type=int, default=28)
    parser.add_argument("--step-days", type=int, default=6)
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument("--horizons", nargs="*", type=int, default=[7])
    parser.add_argument(
        "--model-modes",
        nargs="*",
        default=["champion", "champion_event_adjusted", "champion_event_routed"],
    )
    parser.add_argument("--min-backtests", type=int, default=3)
    parser.add_argument("--min-event-applied", type=int, default=2)
    parser.add_argument("--max-abs-adjustment-pct", type=float, default=3.0)
    parser.add_argument("--refresh-features", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    args = parser.parse_args()

    event_summary = _event_summary()
    feature_status = _feature_snapshot_status(args.refresh_features)

    products = [str(product) for product in args.products if str(product).strip()]
    horizons = [int(horizon) for horizon in args.horizons]
    max_horizon = max(horizons) if horizons else 7
    start = args.start or _default_start(event_summary, args.lookback_days)
    end = args.end or _default_end(products, max_horizon)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utcnow().strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"event_replay_quality_{stamp}.json"
    summary_path = out_dir / f"event_cycle_summary_{stamp}.json"

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))
    env.setdefault("DISABLE_RAG", "true")
    env.setdefault("ENABLE_XGBOOST_FORECAST", "false")
    env.setdefault("ENABLE_LSTM_FORECAST", "false")

    quality_ran = False
    promoted = False
    if not args.skip_quality:
        if datetime.fromisoformat(start) > datetime.fromisoformat(end):
            raise SystemExit(f"No mature forecast window: start {start} is after end {end}")
        quality_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "measure_forecast_quality.py"),
            "--out",
            str(report_path),
            "--start",
            start,
            "--end",
            end,
            "--step-days",
            str(args.step_days),
            "--products",
            *products,
            "--horizons",
            *[str(horizon) for horizon in horizons],
            "--model-modes",
            *args.model_modes,
        ]
        _run_command(quality_cmd, env)
        quality_ran = True

        promote_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "promote_event_adjustment_registry.py"),
            "--report",
            str(report_path),
            "--out",
            args.registry_out,
            "--min-backtests",
            str(args.min_backtests),
            "--min-event-applied",
            str(args.min_event_applied),
            "--max-abs-adjustment-pct",
            str(args.max_abs_adjustment_pct),
        ]
        _run_command(promote_cmd, env)
        promoted = True

    summary = {
        "generated_at": _utcnow().isoformat() + "Z",
        "event_collection": {
            "admin_news_table_cycle_hours": 6,
            **event_summary,
        },
        "feature_snapshot": feature_status,
        "quality": {
            "ran": quality_ran,
            "report_path": str(report_path) if quality_ran else None,
            "start": start,
            "end": end,
            "horizons": horizons,
            "model_modes": args.model_modes,
        },
        "promotion": {
            "ran": promoted,
            "registry_path": args.registry_out if promoted else None,
            "min_backtests": args.min_backtests,
            "min_event_applied": args.min_event_applied,
        },
    }
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps({"summary": str(summary_path), "quality_report": str(report_path) if quality_ran else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
