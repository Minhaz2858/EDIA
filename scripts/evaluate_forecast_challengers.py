"""Evaluate forecast challenger models from leakage-safe training rows.

The evaluator is intentionally offline: it trains challengers on historical
rows, compares them with realized naive performance, and writes reports. It
does not change live forecast routing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_forecast_training_dataset import DEFAULT_PRODUCTS

DEFAULT_DATASET = ROOT / "backend" / "data" / "forecast_training_dataset.csv"
DEFAULT_REPORT = ROOT / "backend" / "data" / "forecast_quality_reports" / "forecast_challenger_evaluation.json"
DEFAULT_REGISTRY_PREVIEW = ROOT / "backend" / "data" / "forecast_challenger_registry_preview.json"
DEFAULT_HORIZONS = [1, 3, 7, 14, 30]
PRODUCT_FAMILIES = {
    "upstream_external": {"crude_oil", "naphtha", "styrene"},
    "c5_chain": {"cracked_c5", "mixed_c5", "isoprene", "piperylene", "dcpd", "raffinate_c5"},
    "c9_aromatics": {"cracked_c9", "mixed_c9", "trimethylbenzene"},
    "erp_internal": {"blowing_agent", "c5_resin", "sis", "sis_rubber"},
}
TARGET_COLUMN = "target_change_pct"
DROP_COLUMNS = {
    "actual_price",
    "as_of_date",
    "target_date",
    "target_change_pct",
    "target_abs_change_pct",
    "target_direction",
    "naive_error_pct",
    "naive_forecast_price",
    "event_evidence_ids_json",
    "event_type_counts_json",
}
CATEGORICAL_COLUMNS = {
    "product_id",
    "cadence_class",
    "champion_model",
    "champion_challenger_model",
    "product_family",
}
FEATURE_SET_EXCLUDE_PREFIXES = {
    "all": (),
    "core": ("ts_", "upstream_"),
    "technical_only": ("event_", "feature_kimi_", "event_routing_"),
    "no_events": ("event_", "feature_kimi_", "event_routing_"),
}
LINEAR_FEATURE_PREFIXES = (
    "ts_return_",
    "ts_momentum_accel_",
    "ts_distance_ma_",
    "ts_position_",
    "ts_volatility_",
    "ts_avg_abs_return_",
    "feature_price_mom_",
    "feature_naphtha_mom_",
    "event_weighted_direction_score",
    "event_bullish_count",
    "event_bearish_count",
    "upstream_",
)


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


def _mape(actual: pd.Series, predicted: pd.Series) -> float:
    actual = actual.astype(float)
    predicted = predicted.astype(float)
    valid = actual.abs() > 1e-9
    if not bool(valid.any()):
        return 0.0
    return float(((actual[valid] - predicted[valid]).abs() / actual[valid].abs()).mean())


def _mae(actual: pd.Series, predicted: pd.Series) -> float:
    return float((actual.astype(float) - predicted.astype(float)).abs().mean())


def _rmse(actual: pd.Series, predicted: pd.Series) -> float:
    errors = actual.astype(float) - predicted.astype(float)
    return float(math.sqrt((errors * errors).mean()))


def _direction_accuracy(actual_change: pd.Series, predicted_change: pd.Series) -> float:
    actual_direction = actual_change.astype(float).apply(lambda value: 1 if value >= 0 else -1)
    predicted_direction = predicted_change.astype(float).apply(lambda value: 1 if value >= 0 else -1)
    if len(actual_direction) == 0:
        return 0.0
    return float((actual_direction == predicted_direction).mean())


def _spike_recall(actual_change: pd.Series, predicted_change: pd.Series, threshold_pct: float) -> float | None:
    actual_spikes = actual_change.astype(float).abs() >= threshold_pct
    if not bool(actual_spikes.any()):
        return None
    predicted_spikes = predicted_change.astype(float).abs() >= threshold_pct
    return float((predicted_spikes[actual_spikes]).mean())


def _metrics(frame: pd.DataFrame, predicted_price: pd.Series, predicted_change: pd.Series, spike_threshold_pct: float) -> dict[str, Any]:
    actual_price = frame["actual_price"].astype(float)
    actual_change = frame[TARGET_COLUMN].astype(float)
    return {
        "rows": int(len(frame)),
        "mape": round(_mape(actual_price, predicted_price), 6),
        "mae": round(_mae(actual_price, predicted_price), 6),
        "rmse": round(_rmse(actual_price, predicted_price), 6),
        "direction_accuracy": round(_direction_accuracy(actual_change, predicted_change), 6),
        "spike_recall": None
        if _spike_recall(actual_change, predicted_change, spike_threshold_pct) is None
        else round(float(_spike_recall(actual_change, predicted_change, spike_threshold_pct)), 6),
    }


def _load_dataset(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"product_id", "as_of_date", "horizon_days", "as_of_price", "actual_price", TARGET_COLUMN}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")
    frame = frame.copy()
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"])
    frame["horizon_days"] = frame["horizon_days"].astype(int)
    for column in ("as_of_price", "actual_price", TARGET_COLUMN):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["as_of_date", "as_of_price", "actual_price", TARGET_COLUMN])
    frame["product_family"] = frame["product_id"].astype(str).map(resolve_product_family)
    frame = frame.sort_values(["as_of_date", "product_id", "horizon_days"]).reset_index(drop=True)
    return frame


def resolve_product_family(product_id: str) -> str:
    normalized = str(product_id or "").strip()
    for family, products in PRODUCT_FAMILIES.items():
        if normalized in products:
            return family
    return "other"


def _build_dataset(args: argparse.Namespace, dataset_path: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "build_forecast_training_dataset.py"),
        "--out",
        str(dataset_path),
        "--products",
        *args.products,
        "--horizons",
        *[str(horizon) for horizon in args.horizons],
        "--start",
        args.start,
        "--end",
        args.end,
        "--step-days",
        str(args.step_days),
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))
    env.setdefault("DISABLE_RAG", "true")
    env.setdefault("ENABLE_XGBOOST_FORECAST", "false")
    env.setdefault("ENABLE_LSTM_FORECAST", "false")
    subprocess.run(command, cwd=str(ROOT), env=env, check=True)


def _feature_frame(frame: pd.DataFrame, feature_set: str) -> pd.DataFrame:
    features = frame.drop(columns=[column for column in DROP_COLUMNS if column in frame.columns]).copy()
    exclude_prefixes = FEATURE_SET_EXCLUDE_PREFIXES.get(feature_set, ())
    if exclude_prefixes:
        features = features.drop(
            columns=[column for column in features.columns if any(column.startswith(prefix) for prefix in exclude_prefixes)],
            errors="ignore",
        )
    for column in features.columns:
        if column in CATEGORICAL_COLUMNS:
            features[column] = features[column].fillna("unknown").astype(str)
        else:
            features[column] = pd.to_numeric(features[column], errors="coerce").fillna(0.0)
    return pd.get_dummies(features, columns=[column for column in CATEGORICAL_COLUMNS if column in features.columns])


def _train_xgboost(train_features: pd.DataFrame, train_target: pd.Series, seed: int) -> Any:
    from xgboost import XGBRegressor

    model = XGBRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.04,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        random_state=seed,
        n_jobs=1,
        reg_lambda=1.0,
    )
    model.fit(train_features, train_target.astype(float))
    return model


def _linear_feature_columns(features: pd.DataFrame) -> list[str]:
    columns = [
        column
        for column in features.columns
        if any(column.startswith(prefix) for prefix in LINEAR_FEATURE_PREFIXES)
    ]
    return columns or list(features.columns)


def _train_linear_momentum(train_features: pd.DataFrame, train_target: pd.Series) -> dict[str, Any]:
    columns = _linear_feature_columns(train_features)
    selected = train_features[columns].astype(float)
    means = selected.mean(axis=0)
    stds = selected.std(axis=0).replace(0.0, 1.0).fillna(1.0)
    x_values = ((selected - means) / stds).fillna(0.0).to_numpy(dtype=float)
    y_values = train_target.astype(float).to_numpy(dtype=float)
    x_design = np.column_stack([np.ones(len(x_values)), x_values])
    ridge_lambda = 10.0
    penalty = np.eye(x_design.shape[1]) * ridge_lambda
    penalty[0, 0] = 0.0
    try:
        coefficients = np.linalg.solve(x_design.T @ x_design + penalty, x_design.T @ y_values)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.pinv(x_design.T @ x_design + penalty) @ x_design.T @ y_values
    return {
        "columns": columns,
        "means": means.to_dict(),
        "stds": stds.to_dict(),
        "coefficients": coefficients.tolist(),
        "ridge_lambda": ridge_lambda,
    }


def _predict_linear_momentum(model: dict[str, Any], features: pd.DataFrame, index: pd.Index) -> pd.Series:
    columns = list(model.get("columns") or [])
    if not columns:
        return pd.Series(0.0, index=index)
    selected = features.reindex(columns=columns, fill_value=0.0).astype(float)
    means = pd.Series(model.get("means") or {}).reindex(columns).fillna(0.0)
    stds = pd.Series(model.get("stds") or {}).reindex(columns).replace(0.0, 1.0).fillna(1.0)
    x_values = ((selected - means) / stds).fillna(0.0).to_numpy(dtype=float)
    x_design = np.column_stack([np.ones(len(x_values)), x_values])
    coefficients = np.array(model.get("coefficients") or [0.0], dtype=float)
    if coefficients.shape[0] != x_design.shape[1]:
        return pd.Series(0.0, index=index)
    return pd.Series(x_design @ coefficients, index=index).clip(lower=-0.5, upper=0.5)


def _split_train_test(frame: pd.DataFrame, train_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = frame.sort_values(["as_of_date", "product_id"]).reset_index(drop=True)
    split_index = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction)))
    return ordered.iloc[:split_index].copy(), ordered.iloc[split_index:].copy()


def _candidate_payload(
    metrics: dict[str, Any],
    baseline: dict[str, Any],
    min_improvement_pct: float,
    model_key: str,
    model_label: str,
) -> dict[str, Any]:
    naive_mape = _safe_float(baseline.get("mape"))
    model_mape = _safe_float(metrics.get("mape"))
    improvement_pct = (naive_mape - model_mape) / naive_mape if naive_mape > 0 else 0.0
    return {
        "model_key": model_key,
        "model": model_label,
        "promotable": bool(improvement_pct >= min_improvement_pct),
        "improvement_pct": round(improvement_pct, 6),
        "required_improvement_pct": min_improvement_pct,
        "reason": f"{model_label} beats naive threshold" if improvement_pct >= min_improvement_pct else f"{model_label} did not beat naive threshold",
    }


def _best_candidate(result: dict[str, Any], min_improvement_pct: float) -> dict[str, Any]:
    baseline = result["baseline_naive"]
    candidates = [
        _candidate_payload(result["xgboost"], baseline, min_improvement_pct, "xgboost", "xgboost_change_pct_regressor"),
        _candidate_payload(result["linear_momentum"], baseline, min_improvement_pct, "linear_momentum", "linear_momentum_ridge"),
    ]
    return max(candidates, key=lambda item: item["improvement_pct"])


def _evaluate_group(
    group: pd.DataFrame,
    train_fraction: float,
    min_train_rows: int,
    min_test_rows: int,
    spike_threshold_pct: float,
    feature_set: str,
    seed: int,
) -> dict[str, Any]:
    if len(group) < min_train_rows + min_test_rows:
        return {"status": "skipped", "reason": "not enough rows", "rows": int(len(group))}

    train_frame, test_frame = _split_train_test(group, train_fraction)
    if len(train_frame) < min_train_rows or len(test_frame) < min_test_rows:
        return {
            "status": "skipped",
            "reason": "not enough train/test rows after time split",
            "rows": int(len(group)),
            "train_rows": int(len(train_frame)),
            "test_rows": int(len(test_frame)),
        }

    train_features = _feature_frame(train_frame, feature_set=feature_set)
    test_features = _feature_frame(test_frame, feature_set=feature_set).reindex(columns=train_features.columns, fill_value=0.0)
    xgboost_model = _train_xgboost(train_features, train_frame[TARGET_COLUMN], seed=seed)
    xgboost_change = pd.Series(xgboost_model.predict(test_features), index=test_frame.index).clip(lower=-0.5, upper=0.5)
    xgboost_price = test_frame["as_of_price"].astype(float) * (1.0 + xgboost_change)
    linear_model = _train_linear_momentum(train_features, train_frame[TARGET_COLUMN])
    linear_change = _predict_linear_momentum(linear_model, test_features, test_frame.index)
    linear_price = test_frame["as_of_price"].astype(float) * (1.0 + linear_change)
    naive_change = pd.Series(0.0, index=test_frame.index)
    naive_price = test_frame["as_of_price"].astype(float)

    return {
        "status": "ok",
        "rows": int(len(group)),
        "train_rows": int(len(train_frame)),
        "test_rows": int(len(test_frame)),
        "train_start": train_frame["as_of_date"].min().date().isoformat(),
        "train_end": train_frame["as_of_date"].max().date().isoformat(),
        "test_start": test_frame["as_of_date"].min().date().isoformat(),
        "test_end": test_frame["as_of_date"].max().date().isoformat(),
        "baseline_naive": _metrics(test_frame, naive_price, naive_change, spike_threshold_pct),
        "xgboost": _metrics(test_frame, xgboost_price, xgboost_change, spike_threshold_pct),
        "linear_momentum": _metrics(test_frame, linear_price, linear_change, spike_threshold_pct),
        "feature_count": int(len(train_features.columns)),
        "linear_feature_count": int(len(linear_model.get("columns") or [])),
        "feature_set": feature_set,
    }


def _product_breakdown(
    horizon_frame: pd.DataFrame,
    train_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    model: Any,
    linear_model: dict[str, Any],
    feature_columns: list[str],
    spike_threshold_pct: float,
    feature_set: str,
    min_product_train_rows: int,
    min_test_rows: int,
) -> dict[str, Any]:
    breakdown: dict[str, Any] = {}
    test_features = _feature_frame(test_frame, feature_set=feature_set).reindex(columns=feature_columns, fill_value=0.0)
    predicted_change = pd.Series(model.predict(test_features), index=test_frame.index).clip(lower=-0.5, upper=0.5)
    predicted_price = test_frame["as_of_price"].astype(float) * (1.0 + predicted_change)
    linear_change = _predict_linear_momentum(linear_model, test_features, test_frame.index)
    linear_price = test_frame["as_of_price"].astype(float) * (1.0 + linear_change)
    for product_id, product_test in test_frame.groupby("product_id"):
        if len(product_test) < min_test_rows:
            breakdown[str(product_id)] = {"status": "skipped", "reason": "not enough test rows", "test_rows": int(len(product_test))}
            continue
        product_index = product_test.index
        product_train_rows = int((train_frame["product_id"] == product_id).sum())
        if product_train_rows < min_product_train_rows:
            breakdown[str(product_id)] = {
                "status": "skipped",
                "reason": "not enough product train rows",
                "train_rows": product_train_rows,
                "test_rows": int(len(product_test)),
            }
            continue
        breakdown[str(product_id)] = {
            "status": "ok",
            "train_rows": product_train_rows,
            "test_rows": int(len(product_test)),
            "baseline_naive": _metrics(
                product_test,
                product_test["as_of_price"].astype(float),
                pd.Series(0.0, index=product_index),
                spike_threshold_pct,
            ),
            "xgboost": _metrics(
                product_test,
                predicted_price.loc[product_index],
                predicted_change.loc[product_index],
                spike_threshold_pct,
            ),
            "linear_momentum": _metrics(
                product_test,
                linear_price.loc[product_index],
                linear_change.loc[product_index],
                spike_threshold_pct,
            ),
        }
    return breakdown


def _evaluate_horizon_pool(
    frame: pd.DataFrame,
    horizon_days: int,
    train_fraction: float,
    min_train_rows: int,
    min_product_train_rows: int,
    min_test_rows: int,
    spike_threshold_pct: float,
    feature_set: str,
    seed: int,
) -> dict[str, Any]:
    horizon_frame = frame[frame["horizon_days"] == horizon_days].copy()
    result = _evaluate_group(
        horizon_frame,
        train_fraction=train_fraction,
        min_train_rows=min_train_rows,
        min_test_rows=min_test_rows,
        spike_threshold_pct=spike_threshold_pct,
        feature_set=feature_set,
        seed=seed,
    )
    if result.get("status") != "ok":
        return result

    train_frame, test_frame = _split_train_test(horizon_frame, train_fraction)
    train_features = _feature_frame(train_frame, feature_set=feature_set)
    model = _train_xgboost(train_features, train_frame[TARGET_COLUMN], seed=seed)
    linear_model = _train_linear_momentum(train_features, train_frame[TARGET_COLUMN])
    result["product_breakdown"] = _product_breakdown(
        horizon_frame=horizon_frame,
        train_frame=train_frame,
        test_frame=test_frame,
        model=model,
        linear_model=linear_model,
        feature_columns=list(train_features.columns),
        spike_threshold_pct=spike_threshold_pct,
        feature_set=feature_set,
        min_product_train_rows=min_product_train_rows,
        min_test_rows=min_test_rows,
    )
    return result


def _evaluate_product_horizon_models(
    frame: pd.DataFrame,
    horizons: list[int],
    train_fraction: float,
    min_product_train_rows: int,
    min_test_rows: int,
    spike_threshold_pct: float,
    min_improvement_pct: float,
    feature_set: str,
    seed: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for product_id in sorted(frame["product_id"].astype(str).unique().tolist()):
        product_payload: dict[str, Any] = {"horizons": {}}
        for horizon_days in horizons:
            group = frame[
                (frame["product_id"].astype(str) == product_id)
                & (frame["horizon_days"] == int(horizon_days))
            ].copy()
            result = _evaluate_group(
                group,
                train_fraction=train_fraction,
                min_train_rows=min_product_train_rows,
                min_test_rows=min_test_rows,
                spike_threshold_pct=spike_threshold_pct,
                feature_set=feature_set,
                seed=seed,
            )
            if result.get("status") == "ok":
                result["candidate"] = _best_candidate(result, min_improvement_pct)
                result["scope"] = "product_horizon"
                result["feature_set"] = feature_set
            product_payload["horizons"][str(horizon_days)] = result
        results[product_id] = product_payload
    return results


def _evaluate_family_horizon_models(
    frame: pd.DataFrame,
    horizons: list[int],
    train_fraction: float,
    min_train_rows: int,
    min_product_train_rows: int,
    min_test_rows: int,
    spike_threshold_pct: float,
    min_improvement_pct: float,
    feature_set: str,
    seed: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for family in sorted(frame["product_family"].astype(str).unique().tolist()):
        family_payload: dict[str, Any] = {"horizons": {}}
        for horizon_days in horizons:
            family_frame = frame[
                (frame["product_family"].astype(str) == family)
                & (frame["horizon_days"] == int(horizon_days))
            ].copy()
            result = _evaluate_group(
                family_frame,
                train_fraction=train_fraction,
                min_train_rows=min_train_rows,
                min_test_rows=min_test_rows,
                spike_threshold_pct=spike_threshold_pct,
                feature_set=feature_set,
                seed=seed,
            )
            if result.get("status") == "ok":
                result["candidate"] = _best_candidate(result, min_improvement_pct)
                result["scope"] = "product_family"
                result["feature_set"] = feature_set
                result["product_family"] = family

                train_frame, test_frame = _split_train_test(family_frame, train_fraction)
                train_features = _feature_frame(train_frame, feature_set=feature_set)
                xgboost_model = _train_xgboost(train_features, train_frame[TARGET_COLUMN], seed=seed)
                linear_model = _train_linear_momentum(train_features, train_frame[TARGET_COLUMN])
                result["product_breakdown"] = _product_breakdown(
                    horizon_frame=family_frame,
                    train_frame=train_frame,
                    test_frame=test_frame,
                    model=xgboost_model,
                    linear_model=linear_model,
                    feature_columns=list(train_features.columns),
                    spike_threshold_pct=spike_threshold_pct,
                    feature_set=feature_set,
                    min_product_train_rows=min_product_train_rows,
                    min_test_rows=min_test_rows,
                )
                for product_id, product_result in result["product_breakdown"].items():
                    if isinstance(product_result, dict) and product_result.get("status") == "ok":
                        product_result["candidate"] = _best_candidate(product_result, min_improvement_pct)
                        product_result["scope"] = "product_family"
                        product_result["product_family"] = family
                        product_result["feature_set"] = feature_set
            family_payload["horizons"][str(horizon_days)] = result
        results[family] = family_payload
    return results


def _add_preview_candidate(
    registry_preview: dict[str, Any],
    product_id: str,
    horizon_days: int,
    result: dict[str, Any],
    scope: str,
    feature_set: str,
    product_family: str | None = None,
) -> None:
    candidate = result.get("candidate") or {}
    if not candidate.get("promotable"):
        return
    horizon_key = str(horizon_days)
    models = registry_preview.setdefault("models", {})
    horizon_models = models.setdefault(horizon_key, {})
    existing = horizon_models.get(product_id)
    improvement_pct = _safe_float(candidate.get("improvement_pct"))
    if existing and _safe_float(existing.get("improvement_pct")) >= improvement_pct:
        return
    horizon_models[product_id] = {
        "enabled": False,
        "model": candidate.get("model") or "unknown_challenger",
        "model_key": candidate.get("model_key") or "unknown",
        "scope": scope,
        "feature_set": feature_set,
        "product_family": product_family,
        "horizon_days": int(horizon_days),
        "preview_only": True,
        "mape": result[str(candidate.get("model_key"))]["mape"],
        "baseline_naive_mape": result["baseline_naive"]["mape"],
        "improvement_pct": improvement_pct,
        "train_rows": result["train_rows"],
        "test_rows": result["test_rows"],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--registry-preview-out", default=str(DEFAULT_REGISTRY_PREVIEW))
    parser.add_argument("--build-dataset", action="store_true", help="Build the dataset before evaluating")
    parser.add_argument("--build-if-missing", action="store_true", help="Build the dataset only when --dataset does not exist")
    parser.add_argument("--start", default="2026-01-15")
    parser.add_argument("--end", default="2026-04-15")
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument("--horizons", nargs="*", type=int, default=DEFAULT_HORIZONS)
    parser.add_argument("--feature-sets", nargs="*", default=["all", "core", "technical_only", "no_events"])
    parser.add_argument("--train-fraction", type=float, default=0.7)
    parser.add_argument("--min-train-rows", type=int, default=30)
    parser.add_argument("--min-product-train-rows", type=int, default=8)
    parser.add_argument("--min-test-rows", type=int, default=4)
    parser.add_argument("--spike-threshold-pct", type=float, default=0.03)
    parser.add_argument("--min-improvement-pct", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    started_at = time.time()
    if args.build_dataset or (args.build_if_missing and not dataset_path.exists()):
        _build_dataset(args, dataset_path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}. Use --build-dataset first.")

    frame = _load_dataset(dataset_path)
    report: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "dataset": str(dataset_path),
        "rows": int(len(frame)),
        "products": sorted(frame["product_id"].astype(str).unique().tolist()),
        "product_families": {family: sorted(products) for family, products in PRODUCT_FAMILIES.items()},
        "horizons": [int(horizon) for horizon in args.horizons],
        "train_fraction": args.train_fraction,
        "min_train_rows": args.min_train_rows,
        "min_product_train_rows": args.min_product_train_rows,
        "min_test_rows": args.min_test_rows,
        "spike_threshold_pct": args.spike_threshold_pct,
        "min_improvement_pct": args.min_improvement_pct,
        "models": ["xgboost_change_pct_regressor", "linear_momentum_ridge"],
        "feature_sets_evaluated": args.feature_sets,
        "feature_sets": {},
    }
    registry_preview: dict[str, Any] = {
        "generated_at": report["generated_at"],
        "source_report": str(args.out),
        "status": "preview_only_not_live_routing",
        "min_improvement_pct": args.min_improvement_pct,
        "models": {},
    }

    valid_feature_sets = [feature_set for feature_set in args.feature_sets if feature_set in FEATURE_SET_EXCLUDE_PREFIXES]
    if not valid_feature_sets:
        raise ValueError(f"No valid feature sets requested. Valid sets: {sorted(FEATURE_SET_EXCLUDE_PREFIXES)}")

    total_product_horizon_promotable = 0
    total_family_horizon_promotable = 0
    for feature_set in valid_feature_sets:
        feature_set_report: dict[str, Any] = {"horizon_pooled": {}, "product_horizon": {}, "family_horizon": {}}
        for horizon_days in args.horizons:
            result = _evaluate_horizon_pool(
                frame=frame,
                horizon_days=int(horizon_days),
                train_fraction=args.train_fraction,
                min_train_rows=args.min_train_rows,
                min_product_train_rows=args.min_product_train_rows,
                min_test_rows=args.min_test_rows,
                spike_threshold_pct=args.spike_threshold_pct,
                feature_set=feature_set,
                seed=args.seed,
            )
            if result.get("status") == "ok":
                result["candidate"] = _best_candidate(result, args.min_improvement_pct)
                product_breakdown = result.get("product_breakdown") or {}
                for product_id, product_result in product_breakdown.items():
                    if not isinstance(product_result, dict) or product_result.get("status") != "ok":
                        continue
                    candidate = _best_candidate(product_result, args.min_improvement_pct)
                    product_result["candidate"] = candidate
                    if candidate["promotable"]:
                        _add_preview_candidate(
                            registry_preview,
                            product_id=str(product_id),
                            horizon_days=int(horizon_days),
                            result=product_result,
                            scope="horizon_pooled_product_slice",
                            feature_set=feature_set,
                        )
            feature_set_report["horizon_pooled"][str(horizon_days)] = result
            print(
                json.dumps(
                    {
                        "feature_set": feature_set,
                        "horizon_days": int(horizon_days),
                        "status": result.get("status"),
                        "naive_mape": (result.get("baseline_naive") or {}).get("mape") if isinstance(result, dict) else None,
                        "xgboost_mape": (result.get("xgboost") or {}).get("mape") if isinstance(result, dict) else None,
                        "linear_momentum_mape": (result.get("linear_momentum") or {}).get("mape") if isinstance(result, dict) else None,
                        "best_model": (result.get("candidate") or {}).get("model") if isinstance(result, dict) else None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        product_horizon = _evaluate_product_horizon_models(
            frame=frame,
            horizons=[int(horizon) for horizon in args.horizons],
            train_fraction=args.train_fraction,
            min_product_train_rows=args.min_product_train_rows,
            min_test_rows=args.min_test_rows,
            spike_threshold_pct=args.spike_threshold_pct,
            min_improvement_pct=args.min_improvement_pct,
            feature_set=feature_set,
            seed=args.seed,
        )
        feature_set_report["product_horizon"] = product_horizon
        product_horizon_promotable = 0
        for product_id, product_payload in product_horizon.items():
            horizons_payload = product_payload.get("horizons") or {}
            for horizon_key, product_result in horizons_payload.items():
                if not isinstance(product_result, dict) or product_result.get("status") != "ok":
                    continue
                if (product_result.get("candidate") or {}).get("promotable"):
                    product_horizon_promotable += 1
                    _add_preview_candidate(
                        registry_preview,
                        product_id=str(product_id),
                        horizon_days=int(horizon_key),
                        result=product_result,
                        scope="product_horizon",
                        feature_set=feature_set,
                    )
        feature_set_report["product_horizon_promotable_count"] = product_horizon_promotable
        total_product_horizon_promotable += product_horizon_promotable

        family_horizon = _evaluate_family_horizon_models(
            frame=frame,
            horizons=[int(horizon) for horizon in args.horizons],
            train_fraction=args.train_fraction,
            min_train_rows=args.min_train_rows,
            min_product_train_rows=args.min_product_train_rows,
            min_test_rows=args.min_test_rows,
            spike_threshold_pct=args.spike_threshold_pct,
            min_improvement_pct=args.min_improvement_pct,
            feature_set=feature_set,
            seed=args.seed,
        )
        feature_set_report["family_horizon"] = family_horizon
        family_horizon_promotable = 0
        for family, family_payload in family_horizon.items():
            horizons_payload = family_payload.get("horizons") or {}
            for horizon_key, family_result in horizons_payload.items():
                if not isinstance(family_result, dict) or family_result.get("status") != "ok":
                    continue
                product_breakdown = family_result.get("product_breakdown") or {}
                for product_id, product_result in product_breakdown.items():
                    if not isinstance(product_result, dict) or product_result.get("status") != "ok":
                        continue
                    if (product_result.get("candidate") or {}).get("promotable"):
                        family_horizon_promotable += 1
                        _add_preview_candidate(
                            registry_preview,
                            product_id=str(product_id),
                            horizon_days=int(horizon_key),
                            result=product_result,
                            scope="product_family",
                            feature_set=feature_set,
                            product_family=str(family),
                        )
        feature_set_report["family_horizon_promotable_count"] = family_horizon_promotable
        total_family_horizon_promotable += family_horizon_promotable
        report["feature_sets"][feature_set] = feature_set_report
        print(
            json.dumps(
                {
                    "feature_set": feature_set,
                    "product_horizon_promotable_count": product_horizon_promotable,
                    "family_horizon_promotable_count": family_horizon_promotable,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    report["product_horizon_promotable_count"] = total_product_horizon_promotable
    report["family_horizon_promotable_count"] = total_family_horizon_promotable

    report["elapsed_s"] = round(time.time() - started_at, 1)
    _write_json(Path(args.out), report)
    _write_json(Path(args.registry_preview_out), registry_preview)
    print(json.dumps({"out": args.out, "registry_preview_out": args.registry_preview_out, "elapsed_s": report["elapsed_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())