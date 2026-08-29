"""Validate disabled forecast challenger candidates across rolling splits.

This script is a safety gate between an interesting preview candidate and any
manual live-routing consideration. It only writes a report; it never enables a
candidate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=ROOT / ".env")
except Exception:
    pass

from scripts.evaluate_forecast_challengers import (  # noqa: E402
    DEFAULT_DATASET,
    DEFAULT_REGISTRY_PREVIEW,
    TARGET_COLUMN,
    _feature_frame,
    _load_dataset,
    _metrics,
    _predict_linear_momentum,
    _safe_float,
    _train_linear_momentum,
    _train_xgboost,
)

DEFAULT_OUT = ROOT / "backend" / "data" / "forecast_quality_reports" / "forecast_candidate_stability.json"


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Candidate registry preview not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_candidates(registry: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    models = registry.get("models") or {}
    for horizon_key, products in models.items():
        if not isinstance(products, dict):
            continue
        for product_id, payload in products.items():
            if not isinstance(payload, dict):
                continue
            candidate = dict(payload)
            candidate["product_id"] = str(product_id)
            candidate["horizon_days"] = int(candidate.get("horizon_days") or horizon_key)
            candidates.append(candidate)
    return candidates


def _predict_candidate(
    model_key: str,
    train_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    feature_set: str,
    seed: int,
) -> pd.Series:
    train_features = _feature_frame(train_frame, feature_set=feature_set)
    test_features = _feature_frame(test_frame, feature_set=feature_set).reindex(columns=train_features.columns, fill_value=0.0)
    if model_key == "linear_momentum":
        model = _train_linear_momentum(train_features, train_frame[TARGET_COLUMN])
        return _predict_linear_momentum(model, test_features, test_frame.index)
    if model_key == "xgboost":
        model = _train_xgboost(train_features, train_frame[TARGET_COLUMN], seed=seed)
        return pd.Series(model.predict(test_features), index=test_frame.index).clip(lower=-0.5, upper=0.5)
    raise ValueError(f"Unsupported candidate model_key: {model_key}")


def _split_metrics(
    train_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    candidate: dict[str, Any],
    spike_threshold_pct: float,
    seed: int,
) -> dict[str, Any]:
    predicted_change = _predict_candidate(
        model_key=str(candidate.get("model_key") or ""),
        train_frame=train_frame,
        test_frame=test_frame,
        feature_set=str(candidate.get("feature_set") or "core"),
        seed=seed,
    )
    predicted_price = test_frame["as_of_price"].astype(float) * (1.0 + predicted_change)
    naive_change = pd.Series(0.0, index=test_frame.index)
    naive_price = test_frame["as_of_price"].astype(float)
    baseline = _metrics(test_frame, naive_price, naive_change, spike_threshold_pct)
    challenger = _metrics(test_frame, predicted_price, predicted_change, spike_threshold_pct)
    naive_mape = _safe_float(baseline.get("mape"))
    challenger_mape = _safe_float(challenger.get("mape"))
    improvement_pct = (naive_mape - challenger_mape) / naive_mape if naive_mape > 0 else 0.0
    return {
        "train_rows": int(len(train_frame)),
        "test_rows": int(len(test_frame)),
        "train_start": train_frame["as_of_date"].min().date().isoformat(),
        "train_end": train_frame["as_of_date"].max().date().isoformat(),
        "test_start": test_frame["as_of_date"].min().date().isoformat(),
        "test_end": test_frame["as_of_date"].max().date().isoformat(),
        "baseline_naive": baseline,
        "candidate": challenger,
        "improvement_pct": round(improvement_pct, 6),
        "direction_delta": round(_safe_float(challenger.get("direction_accuracy")) - _safe_float(baseline.get("direction_accuracy")), 6),
    }


def _candidate_frames(frame: pd.DataFrame, candidate: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    product_id = str(candidate.get("product_id") or "")
    horizon_days = int(candidate.get("horizon_days") or 0)
    product_family = str(candidate.get("product_family") or "")
    horizon_frame = frame[frame["horizon_days"] == horizon_days].copy()
    family_frame = horizon_frame[horizon_frame.get("product_family", "").astype(str) == product_family].copy() if product_family else horizon_frame.copy()
    product_frame = horizon_frame[horizon_frame["product_id"].astype(str) == product_id].copy()
    horizon_frame = horizon_frame.sort_values(["as_of_date", "product_id"]).reset_index(drop=True)
    family_frame = family_frame.sort_values(["as_of_date", "product_id"]).reset_index(drop=True)
    product_frame = product_frame.sort_values(["as_of_date", "product_id"]).reset_index(drop=True)
    scope_frame = family_frame if str(candidate.get("scope")) == "product_family" else horizon_frame
    return scope_frame, product_frame


def _rolling_splits(
    frame: pd.DataFrame,
    candidate: dict[str, Any],
    min_train_rows: int,
    min_test_rows: int,
    max_splits: int,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    horizon_frame, product_frame = _candidate_frames(frame, candidate)
    if len(product_frame) < min_train_rows + min_test_rows:
        return []

    splits: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for split_index in range(min_train_rows, len(product_frame) - min_test_rows + 1):
        product_train = product_frame.iloc[:split_index].copy()
        product_test = product_frame.iloc[split_index : split_index + min_test_rows].copy()
        if str(candidate.get("scope")) in {"horizon_pooled_product_slice", "product_family"}:
            train_end = product_train["as_of_date"].max()
            train_frame = horizon_frame[horizon_frame["as_of_date"] <= train_end].copy()
        else:
            train_frame = product_train
        splits.append((train_frame, product_test))

    if max_splits > 0 and len(splits) > max_splits:
        stride = max(1, len(splits) // max_splits)
        splits = splits[::stride][:max_splits]
    return splits


def _status(summary: dict[str, Any], args: argparse.Namespace) -> str:
    if summary["split_count"] < args.min_splits:
        return "reject"
    if (
        summary["pass_rate"] >= args.min_pass_rate
        and summary["mean_improvement_pct"] >= args.min_improvement_pct
        and summary["worst_improvement_pct"] >= args.max_worst_drawdown_pct
        and summary["mean_direction_delta"] >= args.min_direction_delta
    ):
        return "ready_for_kimi_review"
    if summary["positive_rate"] >= 0.5 and summary["mean_improvement_pct"] > 0:
        return "watch"
    return "reject"


def _status_reasons(summary: dict[str, Any], args: argparse.Namespace) -> list[str]:
    reasons: list[str] = []
    if summary["split_count"] < args.min_splits:
        reasons.append("not enough rolling splits")
    if summary["pass_rate"] < args.min_pass_rate:
        reasons.append("pass rate below threshold")
    if summary["mean_improvement_pct"] < args.min_improvement_pct:
        reasons.append("mean improvement below threshold")
    if summary["worst_improvement_pct"] < args.max_worst_drawdown_pct:
        reasons.append("worst split drawdown below threshold")
    if summary["mean_direction_delta"] < args.min_direction_delta:
        reasons.append("direction accuracy delta below threshold")
    if not reasons:
        reasons.append("all stability thresholds passed; ready for Kimi automated review")
    return reasons


def _kimi_review_payload(result: dict[str, Any]) -> dict[str, Any]:
    candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else {}
    splits = result.get("splits") if isinstance(result.get("splits"), list) else []
    compact_splits = [
        {
            "test_start": split.get("test_start"),
            "test_end": split.get("test_end"),
            "baseline_mape": (split.get("baseline_naive") or {}).get("mape"),
            "candidate_mape": (split.get("candidate") or {}).get("mape"),
            "improvement_pct": split.get("improvement_pct"),
            "direction_delta": split.get("direction_delta"),
        }
        for split in splits[-8:]
        if isinstance(split, dict)
    ]
    return {
        "product_id": candidate.get("product_id"),
        "horizon_days": candidate.get("horizon_days"),
        "scope": candidate.get("scope"),
        "product_family": candidate.get("product_family"),
        "model": candidate.get("model"),
        "model_key": candidate.get("model_key"),
        "feature_set": candidate.get("feature_set"),
        "numeric_status": result.get("numeric_status") or result.get("status"),
        "status_reasons": result.get("status_reasons"),
        "split_count": result.get("split_count"),
        "pass_rate": result.get("pass_rate"),
        "positive_rate": result.get("positive_rate"),
        "mean_improvement_pct": result.get("mean_improvement_pct"),
        "median_improvement_pct": result.get("median_improvement_pct"),
        "worst_improvement_pct": result.get("worst_improvement_pct"),
        "best_improvement_pct": result.get("best_improvement_pct"),
        "mean_direction_delta": result.get("mean_direction_delta"),
        "split_summaries": compact_splits,
    }


def _kimi_system_prompt() -> str:
    return """
SANITIZED_FOR_EXTERNAL_LLM=YES
You are Kimi acting as EDIA's automated forecast-model governance reviewer.
You decide whether a disabled challenger candidate is safe enough for automated promotion review.

Hard rules:
- You do not enable live routing.
- You must return JSON only.
- You may approve only when numeric_status is ready_for_kimi_review.
- If any split has severe negative improvement or unstable direction accuracy, choose watch or reject.
- Prefer watch when evidence is promising but sample size is small.
- Prefer reject when the model is worse than naive, unstable, or data is too sparse.

Return schema:
{
  "decision": "approve|watch|reject",
  "confidence": 0.0,
  "reason": "short reason",
  "required_next_action": "short operational next action"
}
"""


def _run_kimi_review(result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_kimi_review:
        return {
            "status": "skipped",
            "decision": "watch" if result.get("numeric_status") != "reject" else "reject",
            "reason": "Kimi review skipped by CLI flag",
        }
    try:
        from backend.src.nlu.llm_service import RealLLMService

        llm = RealLLMService()
        if not getattr(llm, "is_ready", False):
            return {
                "status": "unavailable",
                "decision": "watch" if result.get("numeric_status") != "reject" else "reject",
                "reason": "RealLLMService is not ready; configure Kimi LLM environment",
            }
        payload = _kimi_review_payload(result)
        response = llm.generate_json(
            _kimi_system_prompt(),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            route_hint=os.getenv("FORECAST_CANDIDATE_REVIEW_LLM_ROUTE", "external"),
            sanitized=True,
        )
        if not isinstance(response, dict) or not response:
            return {
                "status": "unavailable",
                "decision": "watch" if result.get("numeric_status") != "reject" else "reject",
                "reason": "Kimi returned no structured JSON review",
            }
        decision = str(response.get("decision") or "watch").strip().lower()
        if decision not in {"approve", "watch", "reject"}:
            decision = "watch"
        if result.get("numeric_status") != "ready_for_kimi_review" and decision == "approve":
            decision = "watch"
        return {
            "status": "completed",
            "decision": decision,
            "confidence": _safe_float(response.get("confidence"), 0.0),
            "reason": str(response.get("reason") or "").strip(),
            "required_next_action": str(response.get("required_next_action") or "").strip(),
            "raw": response,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "decision": "watch" if result.get("numeric_status") != "reject" else "reject",
            "reason": f"Kimi review failed: {exc}",
        }


def _apply_kimi_status(result: dict[str, Any], review: dict[str, Any]) -> str:
    numeric_status = str(result.get("numeric_status") or result.get("status") or "")
    decision = str(review.get("decision") or "watch").strip().lower()
    if numeric_status == "reject" or decision == "reject":
        return "kimi_rejected"
    if review.get("status") in {"unavailable", "error"}:
        return "kimi_review_unavailable"
    if decision == "approve" and numeric_status == "ready_for_kimi_review":
        return "kimi_approved"
    return "kimi_watch"


def _validate_candidate(frame: pd.DataFrame, candidate: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    splits = _rolling_splits(
        frame=frame,
        candidate=candidate,
        min_train_rows=args.min_train_rows,
        min_test_rows=args.min_test_rows,
        max_splits=args.max_splits,
    )
    result: dict[str, Any] = {
        "candidate": candidate,
        "split_count": len(splits),
        "min_train_rows": args.min_train_rows,
        "min_test_rows": args.min_test_rows,
        "splits": [],
    }
    if not splits:
        result.update(
            {
                "numeric_status": "reject",
                "status": "kimi_rejected",
                "reason": "not enough rows for rolling stability validation",
                "mean_improvement_pct": 0.0,
                "pass_rate": 0.0,
                "positive_rate": 0.0,
                "status_reasons": ["not enough rows for rolling stability validation"],
                "kimi_review": {
                    "status": "not_run",
                    "decision": "reject",
                    "reason": "numeric validation rejected before Kimi review",
                },
            }
        )
        return result

    split_payloads: list[dict[str, Any]] = []
    for train_frame, test_frame in splits:
        split_payloads.append(_split_metrics(train_frame, test_frame, candidate, args.spike_threshold_pct, args.seed))

    improvements = [_safe_float(item.get("improvement_pct")) for item in split_payloads]
    direction_deltas = [_safe_float(item.get("direction_delta")) for item in split_payloads]
    pass_count = sum(1 for value in improvements if value >= args.min_improvement_pct)
    positive_count = sum(1 for value in improvements if value > 0)
    summary = {
        "split_count": len(split_payloads),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(split_payloads), 6),
        "positive_count": positive_count,
        "positive_rate": round(positive_count / len(split_payloads), 6),
        "mean_improvement_pct": round(sum(improvements) / len(improvements), 6),
        "median_improvement_pct": round(float(median(improvements)), 6),
        "worst_improvement_pct": round(min(improvements), 6),
        "best_improvement_pct": round(max(improvements), 6),
        "mean_direction_delta": round(sum(direction_deltas) / len(direction_deltas), 6),
    }
    result.update(summary)
    result["numeric_status"] = _status(summary, args)
    result["status_reasons"] = _status_reasons(summary, args)
    result["splits"] = split_payloads
    review = _run_kimi_review(result, args)
    result["kimi_review"] = review
    result["status"] = _apply_kimi_status(result, review)
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--registry-preview", default=str(DEFAULT_REGISTRY_PREVIEW))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--min-train-rows", type=int, default=8)
    parser.add_argument("--min-test-rows", type=int, default=2)
    parser.add_argument("--min-splits", type=int, default=2)
    parser.add_argument("--max-splits", type=int, default=8)
    parser.add_argument("--min-improvement-pct", type=float, default=0.05)
    parser.add_argument("--min-pass-rate", type=float, default=0.67)
    parser.add_argument("--max-worst-drawdown-pct", type=float, default=-0.02)
    parser.add_argument("--min-direction-delta", type=float, default=-0.05)
    parser.add_argument("--spike-threshold-pct", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-kimi-review", action="store_true", help="Run numeric stability only and skip Kimi governance review")
    args = parser.parse_args()

    started_at = time.time()
    frame = _load_dataset(Path(args.dataset))
    registry = _load_registry(Path(args.registry_preview))
    candidates = _iter_candidates(registry)
    report: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "dataset": args.dataset,
        "registry_preview": args.registry_preview,
        "source_registry_status": registry.get("status"),
        "status_meanings": {
            "ready_for_kimi_review": "numeric thresholds passed and candidate should be reviewed by Kimi",
            "kimi_approved": "Kimi approved the stable candidate for automated promotion review, still not live-enabled",
            "kimi_watch": "Kimi kept the candidate under watch",
            "kimi_rejected": "Kimi or numeric stability rejected the candidate",
            "kimi_review_unavailable": "numeric validation ran but Kimi review was unavailable",
            "watch": "some positive evidence, needs more data or stricter validation",
            "reject": "not stable enough to consider",
        },
        "thresholds": {
            "min_train_rows": args.min_train_rows,
            "min_test_rows": args.min_test_rows,
            "min_splits": args.min_splits,
            "min_improvement_pct": args.min_improvement_pct,
            "min_pass_rate": args.min_pass_rate,
            "max_worst_drawdown_pct": args.max_worst_drawdown_pct,
            "min_direction_delta": args.min_direction_delta,
        },
        "candidate_count": len(candidates),
        "candidates": [],
    }

    for candidate in candidates:
        result = _validate_candidate(frame, candidate, args)
        report["candidates"].append(result)
        print(
            json.dumps(
                {
                    "product_id": candidate.get("product_id"),
                    "horizon_days": candidate.get("horizon_days"),
                    "model": candidate.get("model"),
                    "feature_set": candidate.get("feature_set"),
                    "numeric_status": result.get("numeric_status"),
                    "status": result.get("status"),
                    "kimi_decision": (result.get("kimi_review") or {}).get("decision"),
                    "split_count": result.get("split_count"),
                    "mean_improvement_pct": result.get("mean_improvement_pct"),
                    "pass_rate": result.get("pass_rate"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    counts: dict[str, int] = {}
    for result in report["candidates"]:
        counts[str(result.get("status"))] = counts.get(str(result.get("status")), 0) + 1
    report["status_counts"] = counts
    report["elapsed_s"] = round(time.time() - started_at, 1)
    _write_json(Path(args.out), report)
    print(json.dumps({"out": args.out, "status_counts": counts, "elapsed_s": report["elapsed_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())