"""Run disabled forecast challenger shadow mode.

This script does not change live routing. It reads Kimi-approved disabled
challengers, generates their predictions beside the current champion forecast,
stores pending observations, and evaluates older pending rows once actual prices
are available.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_forecast_training_dataset import (  # noqa: E402
    _event_features,
    _latest_feature_row,
    _quality_features,
    _series_features,
)
from scripts.evaluate_forecast_challengers import (  # noqa: E402
    TARGET_COLUMN,
    _feature_frame,
    _load_dataset,
    _predict_linear_momentum,
    _train_linear_momentum,
    _train_xgboost,
)

DEFAULT_DATASET = ROOT / "backend" / "data" / "forecast_training_dataset.csv"
DEFAULT_REGISTRY = ROOT / "backend" / "data" / "forecast_challenger_registry_approved.json"
DEFAULT_REPORT = ROOT / "backend" / "data" / "forecast_quality_reports" / "forecast_shadow_mode_latest.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _error_pct(predicted: float | None, actual: float | None) -> float | None:
    if predicted is None or actual in (None, 0.0):
        return None
    return round(abs(float(predicted) - float(actual)) / abs(float(actual)), 6)


def _direction(as_of_price: float | None, predicted: float | None) -> int | None:
    if as_of_price is None or predicted is None:
        return None
    return 1 if float(predicted) - float(as_of_price) >= 0 else -1


def _latest_price_point(product_id: str) -> dict[str, Any] | None:
    from backend.src.service.market_data_service import market_data

    history = market_data.get_price_history(product_id)
    if not history:
        return None
    ordered = sorted(history, key=lambda item: str(item.get("date") or ""))
    latest = ordered[-1]
    price = _safe_float(latest.get("price"))
    date = str(latest.get("date") or "")[:10]
    if not date or price is None or price <= 0:
        return None
    return {"date": date, "price": price}


def _load_approved_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates: list[dict[str, Any]] = []
    for horizon_key, products in (payload.get("models") or {}).items():
        if not isinstance(products, dict):
            continue
        for product_id, candidate in products.items():
            if not isinstance(candidate, dict):
                continue
            if candidate.get("stability_status") != "kimi_approved":
                continue
            item = dict(candidate)
            item["product_id"] = str(item.get("product_id") or product_id)
            item["horizon_days"] = int(item.get("horizon_days") or horizon_key)
            candidates.append(item)
    return candidates


def _shadow_feature_row(
    product_id: str,
    as_of_date: str,
    horizon_days: int,
    as_of_price: float,
    event_lookback_hours: int,
    event_limit: int,
) -> dict[str, Any]:
    target_date = (datetime.fromisoformat(as_of_date[:10]) + timedelta(days=horizon_days)).date().isoformat()
    row: dict[str, Any] = {
        "product_id": product_id,
        "as_of_date": as_of_date,
        "target_date": target_date,
        "horizon_days": int(horizon_days),
        "as_of_price": round(float(as_of_price), 6),
        "actual_price": 0.0,
        "target_change_pct": 0.0,
        "target_abs_change_pct": 0.0,
        "target_direction": 1,
        "naive_forecast_price": round(float(as_of_price), 6),
        "naive_error_pct": 0.0,
    }
    row.update(_quality_features(product_id, as_of_date, horizon_days))
    row.update(_latest_feature_row(product_id, as_of_date))
    row.update(_series_features(product_id, as_of_date, float(as_of_price)))
    row.update(_event_features(product_id, as_of_date, event_lookback_hours, event_limit))
    return row


def _train_and_predict_challenger(
    dataset: pd.DataFrame,
    candidate: dict[str, Any],
    live_row: dict[str, Any],
    seed: int,
) -> tuple[float | None, float | None, dict[str, Any]]:
    product_id = str(candidate["product_id"])
    horizon_days = int(candidate["horizon_days"])
    feature_set = str(candidate.get("feature_set") or "all")
    model_key = str(candidate.get("model_key") or "")
    scope = str(candidate.get("scope") or "product_horizon")

    train_frame = dataset[dataset["horizon_days"] == horizon_days].copy()
    if scope == "product_horizon":
        train_frame = train_frame[train_frame["product_id"].astype(str) == product_id].copy()
    elif scope == "product_family" and candidate.get("product_family"):
        train_frame = train_frame[train_frame["product_family"].astype(str) == str(candidate["product_family"])].copy()

    train_frame = train_frame.sort_values(["as_of_date", "product_id"]).reset_index(drop=True)
    if train_frame.empty:
        return None, None, {"status": "skipped", "reason": "no training rows"}

    train_features = _feature_frame(train_frame, feature_set=feature_set)
    live_frame = pd.DataFrame([live_row])
    live_features = _feature_frame(live_frame, feature_set=feature_set).reindex(columns=train_features.columns, fill_value=0.0)

    if model_key == "linear_momentum":
        model = _train_linear_momentum(train_features, train_frame[TARGET_COLUMN])
        predicted_change = float(_predict_linear_momentum(model, live_features, live_frame.index).iloc[0])
        model_meta = {
            "status": "ok",
            "trained_rows": int(len(train_frame)),
            "feature_count": int(len(train_features.columns)),
            "linear_feature_count": int(len(model.get("columns") or [])),
        }
    elif model_key == "xgboost":
        model = _train_xgboost(train_features, train_frame[TARGET_COLUMN], seed=seed)
        predicted_change = float(pd.Series(model.predict(live_features)).clip(lower=-0.5, upper=0.5).iloc[0])
        model_meta = {
            "status": "ok",
            "trained_rows": int(len(train_frame)),
            "feature_count": int(len(train_features.columns)),
        }
    else:
        return None, None, {"status": "skipped", "reason": f"unsupported model_key {model_key}"}

    as_of_price = float(live_row["as_of_price"])
    predicted_price = round(as_of_price * (1.0 + predicted_change), 6)
    return predicted_price, round(predicted_change, 6), model_meta


def _champion_prediction(
    product_id: str,
    horizon_days: int,
    as_of_date: str,
    as_of_price: float,
) -> tuple[float | None, dict[str, Any]]:
    from backend.src.service.forecasting_engine import get_native_baseline_forecast_path

    path = get_native_baseline_forecast_path(
        product_id,
        horizon_days=horizon_days,
        cutoff_date=as_of_date,
        start_date=as_of_date,
        use_champion_routing=True,
    )
    points = path.get("dense_path") if isinstance(path.get("dense_path"), list) else []
    target_point = next((point for point in points if int(point.get("step_number") or 0) == horizon_days), None)
    if target_point is None and points:
        target_point = points[-1]
    predicted = _safe_float((target_point or {}).get("final_price"))
    fallback_applied = False
    if predicted is None:
        predicted = round(float(as_of_price), 6)
        fallback_applied = True
    return predicted, {
        "status": path.get("status"),
        "source": path.get("source"),
        "message": path.get("message"),
        "model_components": path.get("model_components"),
        "shadow_fallback_applied": fallback_applied,
        "shadow_fallback_reason": "Champion path unavailable; using current price as conservative naive comparator."
        if fallback_applied
        else None,
    }


def _ensure_table(engine: Any) -> None:
    ddl = text(
        """
        CREATE TABLE IF NOT EXISTS forecast_shadow_predictions (
            id VARCHAR(64) PRIMARY KEY,
            created_at VARCHAR(32) NOT NULL,
            evaluated_at VARCHAR(32) NULL,
            status VARCHAR(32) NOT NULL,
            product_id VARCHAR(64) NOT NULL,
            horizon_days INTEGER NOT NULL,
            as_of_date VARCHAR(16) NOT NULL,
            target_date VARCHAR(16) NOT NULL,
            as_of_price REAL NOT NULL,
            champion_model VARCHAR(120),
            champion_predicted_price REAL,
            challenger_model VARCHAR(120) NOT NULL,
            challenger_model_key VARCHAR(64) NOT NULL,
            challenger_feature_set VARCHAR(64),
            challenger_predicted_price REAL,
            challenger_predicted_change_pct REAL,
            actual_price REAL NULL,
            champion_error_pct REAL NULL,
            challenger_error_pct REAL NULL,
            challenger_improvement_pct REAL NULL,
            champion_direction INTEGER NULL,
            challenger_direction INTEGER NULL,
            actual_direction INTEGER NULL,
            payload_json TEXT
        )
        """
    )
    with engine.begin() as conn:
        conn.execute(ddl)
        for stmt in (
            "CREATE INDEX idx_forecast_shadow_status ON forecast_shadow_predictions (status)",
            "CREATE INDEX idx_forecast_shadow_target ON forecast_shadow_predictions (target_date)",
            "CREATE INDEX idx_forecast_shadow_product ON forecast_shadow_predictions (product_id, horizon_days)",
        ):
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


def _upsert_shadow_row(engine: Any, row: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM forecast_shadow_predictions
                WHERE product_id = :product_id
                  AND horizon_days = :horizon_days
                  AND as_of_date = :as_of_date
                  AND challenger_model_key = :challenger_model_key
                  AND challenger_feature_set = :challenger_feature_set
                """
            ),
            row,
        )
        conn.execute(
            text(
                """
                INSERT INTO forecast_shadow_predictions (
                    id, created_at, evaluated_at, status, product_id, horizon_days,
                    as_of_date, target_date, as_of_price, champion_model,
                    champion_predicted_price, challenger_model, challenger_model_key,
                    challenger_feature_set, challenger_predicted_price,
                    challenger_predicted_change_pct, actual_price, champion_error_pct,
                    challenger_error_pct, challenger_improvement_pct, champion_direction,
                    challenger_direction, actual_direction, payload_json
                ) VALUES (
                    :id, :created_at, :evaluated_at, :status, :product_id, :horizon_days,
                    :as_of_date, :target_date, :as_of_price, :champion_model,
                    :champion_predicted_price, :challenger_model, :challenger_model_key,
                    :challenger_feature_set, :challenger_predicted_price,
                    :challenger_predicted_change_pct, :actual_price, :champion_error_pct,
                    :challenger_error_pct, :challenger_improvement_pct, :champion_direction,
                    :challenger_direction, :actual_direction, :payload_json
                )
                """
            ),
            row,
        )


def _evaluate_pending(engine: Any, limit: int) -> dict[str, Any]:
    from backend.src.service.market_data_service import market_data

    with engine.connect() as conn:
        pending = conn.execute(
            text(
                """
                SELECT * FROM forecast_shadow_predictions
                WHERE status = 'pending'
                ORDER BY target_date ASC
                LIMIT :limit
                """
            ),
            {"limit": int(limit)},
        ).mappings().all()

    evaluated = 0
    still_pending = 0
    for row in pending:
        actual_price = _safe_float(market_data.get_price_on_date(str(row["product_id"]), str(row["target_date"])))
        if actual_price is None or actual_price <= 0:
            still_pending += 1
            continue

        as_of_price = _safe_float(row.get("as_of_price"))
        champion_predicted = _safe_float(row.get("champion_predicted_price"))
        challenger_predicted = _safe_float(row.get("challenger_predicted_price"))
        champion_error = _error_pct(champion_predicted, actual_price)
        challenger_error = _error_pct(challenger_predicted, actual_price)
        improvement = None
        if champion_error not in (None, 0.0) and challenger_error is not None:
            improvement = round((champion_error - challenger_error) / champion_error, 6)

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE forecast_shadow_predictions
                    SET status = 'evaluated',
                        evaluated_at = :evaluated_at,
                        actual_price = :actual_price,
                        champion_error_pct = :champion_error_pct,
                        challenger_error_pct = :challenger_error_pct,
                        challenger_improvement_pct = :challenger_improvement_pct,
                        champion_direction = :champion_direction,
                        challenger_direction = :challenger_direction,
                        actual_direction = :actual_direction
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "evaluated_at": _utcnow(),
                    "actual_price": round(actual_price, 6),
                    "champion_error_pct": champion_error,
                    "challenger_error_pct": challenger_error,
                    "challenger_improvement_pct": improvement,
                    "champion_direction": _direction(as_of_price, champion_predicted),
                    "challenger_direction": _direction(as_of_price, challenger_predicted),
                    "actual_direction": _direction(as_of_price, actual_price),
                },
            )
        evaluated += 1

    return {"checked": len(pending), "evaluated": evaluated, "still_pending": still_pending}


def _summary(engine: Any) -> dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT status, COUNT(*) AS count
                FROM forecast_shadow_predictions
                GROUP BY status
                """
            )
        ).mappings().all()
        evaluated = conn.execute(
            text(
                """
                SELECT product_id, horizon_days,
                       COUNT(*) AS evaluated_count,
                       AVG(challenger_improvement_pct) AS avg_improvement_pct,
                       AVG(CASE WHEN challenger_error_pct < champion_error_pct THEN 1 ELSE 0 END) AS win_rate
                FROM forecast_shadow_predictions
                WHERE status = 'evaluated'
                GROUP BY product_id, horizon_days
                """
            )
        ).mappings().all()
    return {
        "status_counts": {str(row["status"]): int(row["count"]) for row in rows},
        "evaluated_rollup": [dict(row) for row in evaluated],
    }


def run_shadow_mode(
    *,
    dataset_path: Path | str = DEFAULT_DATASET,
    registry_path: Path | str = DEFAULT_REGISTRY,
    report_out: Path | str = DEFAULT_REPORT,
    event_lookback_hours: int = 168,
    event_limit: int = 80,
    evaluate_pending_limit: int = 200,
    seed: int = 42,
    dry_run: bool = False,
) -> dict[str, Any]:
    os.environ.setdefault("DISABLE_RAG", "true")
    from backend.src.db.sqlite_db import db

    engine = db.engine
    _ensure_table(engine)
    dataset_path = Path(dataset_path)
    registry_path = Path(registry_path)
    report_out = Path(report_out)
    dataset = _load_dataset(dataset_path)
    candidates = _load_approved_candidates(registry_path)

    evaluation = _evaluate_pending(engine, limit=evaluate_pending_limit)
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for candidate in candidates:
        product_id = str(candidate["product_id"])
        horizon_days = int(candidate["horizon_days"])
        latest = _latest_price_point(product_id)
        if latest is None:
            skipped.append({"product_id": product_id, "horizon_days": horizon_days, "reason": "no latest price"})
            continue

        as_of_date = latest["date"]
        as_of_price = float(latest["price"])
        target_date = (datetime.fromisoformat(as_of_date) + timedelta(days=horizon_days)).date().isoformat()
        live_row = _shadow_feature_row(
            product_id=product_id,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            as_of_price=as_of_price,
            event_lookback_hours=event_lookback_hours,
            event_limit=event_limit,
        )
        champion_price, champion_meta = _champion_prediction(product_id, horizon_days, as_of_date, as_of_price)
        challenger_price, challenger_change, model_meta = _train_and_predict_challenger(
            dataset=dataset,
            candidate=candidate,
            live_row=live_row,
            seed=seed,
        )
        if challenger_price is None:
            skipped.append({"product_id": product_id, "horizon_days": horizon_days, "reason": model_meta.get("reason")})
            continue

        payload = {
            "candidate": candidate,
            "champion": champion_meta,
            "challenger_training": model_meta,
            "live_features": {
                "as_of_date": as_of_date,
                "target_date": target_date,
                "as_of_price": as_of_price,
            },
        }
        shadow_row = {
            "id": uuid.uuid4().hex,
            "created_at": _utcnow(),
            "evaluated_at": None,
            "status": "pending",
            "product_id": product_id,
            "horizon_days": horizon_days,
            "as_of_date": as_of_date,
            "target_date": target_date,
            "as_of_price": round(as_of_price, 6),
            "champion_model": str((champion_meta.get("model_components") or {}).get("champion_routing", {}).get("selected_model") or champion_meta.get("source") or "champion"),
            "champion_predicted_price": champion_price,
            "challenger_model": str(candidate.get("model") or "unknown"),
            "challenger_model_key": str(candidate.get("model_key") or "unknown"),
            "challenger_feature_set": str(candidate.get("feature_set") or "all"),
            "challenger_predicted_price": challenger_price,
            "challenger_predicted_change_pct": challenger_change,
            "actual_price": None,
            "champion_error_pct": None,
            "challenger_error_pct": None,
            "challenger_improvement_pct": None,
            "champion_direction": _direction(as_of_price, champion_price),
            "challenger_direction": _direction(as_of_price, challenger_price),
            "actual_direction": None,
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        }
        if not dry_run:
            _upsert_shadow_row(engine, shadow_row)
        generated.append(
            {
                "product_id": product_id,
                "horizon_days": horizon_days,
                "as_of_date": as_of_date,
                "target_date": target_date,
                "as_of_price": round(as_of_price, 6),
                "champion_predicted_price": champion_price,
                "challenger_predicted_price": challenger_price,
                "challenger_predicted_change_pct": challenger_change,
                "challenger_model": shadow_row["challenger_model"],
                "status": "dry_run" if dry_run else "pending",
            }
        )

    report = {
        "generated_at": _utcnow(),
        "dry_run": bool(dry_run),
        "registry": str(registry_path.resolve()),
        "dataset": str(dataset_path.resolve()),
        "candidate_count": len(candidates),
        "generated_count": len(generated),
        "skipped_count": len(skipped),
        "pending_evaluation": evaluation,
        "generated": generated,
        "skipped": skipped,
        "storage_summary": _summary(engine),
    }
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--report-out", default=str(DEFAULT_REPORT))
    parser.add_argument("--event-lookback-hours", type=int, default=168)
    parser.add_argument("--event-limit", type=int, default=80)
    parser.add_argument("--evaluate-pending-limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = run_shadow_mode(
        dataset_path=args.dataset,
        registry_path=args.registry,
        report_out=args.report_out,
        event_lookback_hours=args.event_lookback_hours,
        event_limit=args.event_limit,
        evaluate_pending_limit=args.evaluate_pending_limit,
        seed=args.seed,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())