"""Real forecasting engine: ARIMA(2,1,2) + optional XGBoost/LSTM + monthly STL.

Layer architecture (aligned with EDIA Build Instructions §8):
  Short-term (3d / 7d):
    • ARIMA(2,1,2)  — weight 0.30
    • XGBoost       — optional, env-gated (lag/MA/naphtha features)
    • LSTM          — optional, env-gated (sequence component)
    • Mean-reversion component — weight 0.30
  Monthly (30d):
    1. STL trend      — weight 0.40
    2. Causal chain   — weight 0.35  (naphtha → C5 → downstream)
    3. Seasonal rules — overlay     (additive %, from spec §8.2)
    4. Weekly nudge   — ±0.8% cap   (from latest weekly-report RAG signal)

Current dev-environment ensemble state on macOS:
    • ARIMA and STL are enabled when statsmodels is importable.
    • XGBoost defaults disabled unless ENABLE_XGBOOST_FORECAST is truthy.
    • LSTM defaults disabled unless ENABLE_LSTM_FORECAST is truthy.

Production enablement is controlled only by environment flags and installed
packages. This module logs the effective state once at import time; it does not
change any config flags.
"""

from __future__ import annotations

import datetime
import importlib
import json
import os
import sys
import warnings
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def _feature_flag(name: str, default: str) -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _xgboost_enabled() -> bool:
    default = "false" if sys.platform == "darwin" else "true"
    return _feature_flag("ENABLE_XGBOOST_FORECAST", default)


def _lstm_enabled() -> bool:
    default = "false" if sys.platform == "darwin" else "true"
    return _feature_flag("ENABLE_LSTM_FORECAST", default)


def _env_status(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return f"<unset>; default={default}"
    return value


def _package_status(import_name: str, dist_name: str | None = None) -> str:
    try:
        module = importlib.import_module(import_name)
    except Exception as exc:  # noqa: BLE001 - status log should never break startup
        return f"unavailable ({exc.__class__.__name__}: {exc})"

    version = getattr(module, "__version__", None)
    if not version:
        try:
            version = metadata.version(dist_name or import_name.split(".")[0])
        except Exception:  # noqa: BLE001
            version = "unknown"
    return f"available v{version}"


def _log_active_model_state() -> None:
    xgb_default = "false" if sys.platform == "darwin" else "true"
    lstm_default = "false" if sys.platform == "darwin" else "true"
    xgb_enabled = _xgboost_enabled()
    lstm_enabled = _lstm_enabled()

    lines = [
        "[forecasting_engine] EDIA Forecasting Engine - active models:",
        f"  ARIMA:   enabled (statsmodels={_package_status('statsmodels')})",
        "  XGBoost: "
        f"{'enabled' if xgb_enabled else 'disabled'} "
        f"(ENABLE_XGBOOST_FORECAST={_env_status('ENABLE_XGBOOST_FORECAST', xgb_default)}; "
        f"package={_package_status('xgboost')})",
        "  LSTM:    "
        f"{'enabled' if lstm_enabled else 'disabled'} "
        f"(ENABLE_LSTM_FORECAST={_env_status('ENABLE_LSTM_FORECAST', lstm_default)}; "
        f"package={_package_status('torch')})",
        f"  STL:     enabled (statsmodels.tsa.seasonal.STL={_package_status('statsmodels.tsa.seasonal', 'statsmodels')})",
    ]
    print("\n".join(lines))


_log_active_model_state()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Two-hop tier-dampened causal-chain elasticities (§6.3)
# Tier 1 (naphtha → C5): ×1.00   Tier 2 (C5 → downstream): ×0.85
# Tier 3 (downstream → end-use): ×0.72
_RAW_ELASTICITIES: Dict[str, Tuple[float, float]] = {
    # product_id: (raw_elasticity, tier_dampening_factor)
    "naphtha": (1.00, 1.00),
    "cracked_c5": (0.82, 1.00),  # Tier 1
    "mixed_c5": (0.82, 1.00),  # Tier 1
    "cracked_c9": (0.65, 0.85),
    "isoprene": (0.88, 0.85),  # Tier 2: 0.88 × 0.85 = 0.75
    "piperylene": (0.85, 0.85),  # Tier 2: 0.85 × 0.85 = 0.72
    "dcpd": (0.78, 0.85),  # Tier 2: 0.78 × 0.85 = 0.66
    "raffinate_c5": (0.70, 0.85),
    "styrene": (0.55, 0.85),  # Tier 2: 0.55 × 0.85 = 0.47
    "sis": (0.71, 0.72),  # Tier 3: 0.71 × 0.72 = 0.51
    "sis_rubber": (0.71, 0.72),  # Tier 3
    "blowing_agent": (0.70, 0.85),
    "butadiene": (0.65, 0.85),
    "sbs": (0.60, 0.72),  # Tier 3
    "c5_resin": (0.62, 0.72),  # Tier 3
    "mixed_c9": (0.65, 0.85),
    "hydrogenated_c9": (0.60, 0.72),
    "trimethylbenzene": (0.60, 0.72),
    "c9_resin": (0.60, 0.72),  # legacy alias
}

# Pre-computed effective coefficients = raw × dampening
_ELASTICITIES: Dict[str, float] = {
    k: round(raw * damp, 4) for k, (raw, damp) in _RAW_ELASTICITIES.items()
}

# Seasonal rules (product_id, month) → pct adjustment (additive)
_SEASONAL_RULES: Dict[Tuple[str, int], float] = {
    ("c5_resin", 11): -2.5,
    ("c5_resin", 12): -2.5,
    ("c5_resin", 1): -2.5,
    ("c5_resin", 2): -2.5,
    ("c5_resin", 3): 1.0,
    ("c5_resin", 4): 1.0,
    ("c5_resin", 5): 1.0,
    ("c5_resin", 6): 1.0,
    ("c5_resin", 7): 1.0,
    ("c5_resin", 8): 1.0,
    ("c5_resin", 9): 1.0,
    ("dcpd", 11): -3.0,
    ("dcpd", 12): -3.0,
    ("dcpd", 1): -3.0,
    ("dcpd", 2): -3.0,
    ("dcpd", 3): 1.5,
    ("dcpd", 4): 1.5,
    ("dcpd", 5): 1.5,
    ("dcpd", 6): 1.5,
    ("dcpd", 7): 1.5,
    ("dcpd", 8): 1.5,
    ("isoprene", 2): 0.8,
    ("isoprene", 3): 0.8,
    ("cracked_c5", 1): -1.0,
    ("cracked_c5", 2): -1.0,
    ("cracked_c5", 3): 0.5,
    ("cracked_c5", 4): 0.5,
    ("cracked_c5", 5): 0.5,
    ("piperylene", 2): 1.2,
    ("piperylene", 3): 1.2,
    ("sis", 2): 0.5,
    ("sis", 3): 0.8,
    ("sis", 9): 0.5,
}

# Map product node ID → (product_id in market_prices, primary price_type)
#
# Phase 1.2 audit notes (2026-04-29) — keep in mind when editing:
# - Several entries below could be re-mapped to a richer `erp_sale_order_weighted`
#   series (deferred to Phase 1.2b pending Ecisco decision; see
#   PHASE_1_2B_REMAP_CANDIDATES.md for measurement data).
# - `crude_oil` and `mthpa_hardener` are intentionally absent from this map
#   (chart-only). `butadiene` and `sbs` are intentionally model-only
#   (no dashboard counterpart). See trailing comments below for rationale.
_PRODUCT_MAP: Dict[str, Tuple[str, str]] = {
    "crude_oil": ("crude_oil", "brent"),
    "naphtha": ("naphtha", "japan_cif"),
    "cracked_c5": ("cracked_c5", "yangzi"),
    "mixed_c5": ("cracked_c5", "yangzi"),
    # Phase 1.2c: re-mapped from sparse "erp_forecast_price" (36 rows) to
    # "yangzi_basf" (544 rows). `yangzi_basf` is an LZ market-reference quote
    # from supplier 扬子巴斯夫, so this is a broken mapping fix, not an ERP
    # semantic shift.
    "cracked_c9": ("cracked_c9", "yangzi_basf"),
    "mixed_c9": ("cracked_c9", "yangzi_basf"),
    "isoprene": ("isoprene", "east_china"),
    "piperylene": ("piperylene", "east_china"),
    "dcpd": ("dcpd", "east_china"),
    "raffinate_c5": ("raffinate_c5", "erp_sale_order_weighted"),
    "sis": ("sis", "baling_1105"),
    "sis_rubber": ("sis", "baling_1105"),
    # Phase 1.2 (Tier A): re-mapped from "east_china" (261 rows) to
    # "erp_sale_order_weighted" (2196 rows) to match REAL_DATA_MAP. The
    # dashboard chart already used the ERP series; the model was pointing at
    # a sparser LZ price by accident. No semantic shift.
    "blowing_agent": ("blowing_agent", "erp_sale_order_weighted"),
    "c5_resin": ("c5_resin", "erp_sale_order_weighted"),
    "hydrogenated_c9": ("hydrogenated_c9", "partner_forecast_price"),
    "trimethylbenzene": ("trimethylbenzene", "partner_forecast_price"),
    "c9_resin": ("hydrogenated_c9", "partner_forecast_price"),
    "styrene": ("styrene", "sinopec_sh"),
    # Model-only entries (no REAL_DATA_MAP counterpart). Likely legacy.
    # Verify usage and consider removal in Phase 2.
    "butadiene": ("butadiene", "east_china_jiangyin"),
    "sbs": ("sbs", "east_china_791h"),
    # NOTE: `mthpa_hardener` is intentionally NOT in this map. Chart-only by
    # design. MTHPA is out of forecasting scope per Ecisco product catalog.
}

# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------


def _get_engine():
    """Return the SQLAlchemy engine from the app's db module."""
    from backend.src.db.sqlite_db import db as _app_db  # type: ignore

    return _app_db.engine


def get_price_series(
    product_id: str,
    price_type: str,
    days: int = 500,
    cutoff_date: str | None = None,
) -> pd.Series:
    """Fetch real price history from market_prices → pd.Series indexed by date.

    Applies calendar-day reindex + forward-fill to produce a gapless daily series.
    Returns an empty Series on any error.
    """
    from sqlalchemy import text  # local import keeps module light

    try:
        engine = _get_engine()
        with engine.connect() as conn:
            # Phase 1.1 fix: select the LATEST :lim observations (descending),
            # not the oldest. Prior version used "ORDER BY date ASC LIMIT :lim"
            # which truncated long ERP histories to their earliest rows
            # (e.g. c5_resin model series ended in 2022 despite raw data through
            # 2026). We sort ascending in-pandas below before reindex/ffill so
            # downstream behavior is unchanged.
            rows = conn.execute(
                text(
                    "SELECT date, price FROM market_prices "
                    "WHERE product_id = :pid AND price_type = :pt "
                    "AND (:cutoff IS NULL OR date <= :cutoff) "
                    "AND price IS NOT NULL "
                    "ORDER BY date DESC "
                    "LIMIT :lim"
                ),
                {"pid": product_id, "pt": price_type, "cutoff": cutoff_date, "lim": days},
            ).fetchall()

        if not rows:
            return pd.Series(dtype=float)

        s = pd.Series(
            {r[0]: float(r[1]) for r in rows},
            dtype=float,
        )
        s.index = pd.to_datetime(s.index)
        s = s.sort_index()
        # Reindex to daily calendar, forward-fill up to 7 d, then back-fill start
        full_idx = pd.date_range(s.index[0], s.index[-1], freq="D")
        s = s.reindex(full_idx).ffill(limit=7).bfill()
        return s

    except Exception as e:
        print(f"[forecasting_engine] get_price_series({product_id},{price_type}): {e}")
        return pd.Series(dtype=float)


def get_naphtha_series(days: int = 500, cutoff_date: str | None = None) -> pd.Series:
    return get_price_series("naphtha", "japan_cif", days=days, cutoff_date=cutoff_date)


# ---------------------------------------------------------------------------
# Model-quality constants (Phase 1 win validation, 2026-04-29)
# ---------------------------------------------------------------------------

# Products where MAPE_BASELINE.md reports a positive `skill_vs_naive` on the
# official Phase 1 backtest window (2026-01-15 → 2026-04-15, 7-day horizon),
# meaning the current model performs worse than a naive "tomorrow = today"
# forecast for that product.
#
# This set drives `model_quality.below_naive_baseline` in the forecast API
# payload. Any product with documented `skill_vs_naive > 0` belongs here.
# Current documented members:
#   - cracked_c5: skill_vs_naive +0.0061
#   - cracked_c9: skill_vs_naive +0.0369
#   - mixed_c9: skill_vs_naive +0.0344
#   - styrene: skill_vs_naive +0.0087
#
# Do NOT remove entries from this set without a new official backtest showing
# model <= naive for the affected product.
_BELOW_NAIVE_PRODUCTS: frozenset[str] = frozenset(
    {"cracked_c5", "cracked_c9", "mixed_c9", "styrene"}
)


# ---------------------------------------------------------------------------
# Champion model routing (old-data walk-forward backtest, 2026-05-08)
# ---------------------------------------------------------------------------

_CHAMPION_ROUTING_WINDOW = "2026-01-15 to 2026-04-15"
_CHAMPION_ROUTING_STEP_DAYS = 14
_LIVE_CHALLENGER_REGISTRY_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "forecast_challenger_registry_approved.json"
)
_CHAMPION_MODEL_REGISTRY: Dict[str, Dict[int, Dict[str, Any]]] = {
    "crude_oil": {
        7: {"selected_model": "naive", "mape": 0.0672, "challenger_model": "arima_residual_daily", "challenger_mape": 0.0723},
        30: {"selected_model": "arima_residual_daily", "mape": 0.1230, "challenger_model": "naive", "challenger_mape": 0.1253},
    },
    "naphtha": {
        7: {"selected_model": "naive", "mape": 0.0744, "challenger_model": "arima_residual_daily", "challenger_mape": 0.0879},
        30: {"selected_model": "naive", "mape": 0.1227, "challenger_model": "arima_residual_daily", "challenger_mape": 0.1397},
    },
    "cracked_c5": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0359, "challenger_model": "naive", "challenger_mape": 0.0395},
        30: {"selected_model": "naive", "mape": 0.1639, "challenger_model": "arima_residual_daily", "challenger_mape": 0.1766},
    },
    "mixed_c5": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0359, "challenger_model": "naive", "challenger_mape": 0.0395},
        30: {"selected_model": "naive", "mape": 0.1639, "challenger_model": "arima_residual_daily", "challenger_mape": 0.1766},
    },
    "cracked_c9": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0588, "challenger_model": "naive", "challenger_mape": 0.0739},
        30: {"selected_model": "arima_residual_daily", "mape": 0.1741, "challenger_model": "naive", "challenger_mape": 0.1991},
    },
    "mixed_c9": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0588, "challenger_model": "naive", "challenger_mape": 0.0739},
        30: {"selected_model": "arima_residual_daily", "mape": 0.1741, "challenger_model": "naive", "challenger_mape": 0.1991},
    },
    "isoprene": {
        7: {"selected_model": "naive", "mape": 0.0183, "challenger_model": "arima_residual_daily", "challenger_mape": 0.0297},
        30: {"selected_model": "naive", "mape": 0.1367, "challenger_model": "arima_residual_daily", "challenger_mape": 0.1546},
    },
    "piperylene": {
        7: {"selected_model": "naive", "mape": 0.0519, "challenger_model": "arima_residual_daily", "challenger_mape": 0.0674},
        30: {"selected_model": "naive", "mape": 0.2440, "challenger_model": "arima_residual_daily", "challenger_mape": 0.3094},
    },
    "dcpd": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0249, "challenger_model": "naive", "challenger_mape": 0.0251},
        30: {"selected_model": "arima_residual_daily", "mape": 0.1114, "challenger_model": "naive", "challenger_mape": 0.1204},
    },
    "c5_resin": {
        7: {"selected_model": "naive", "mape": 0.1203, "challenger_model": "arima_residual_daily", "challenger_mape": 0.1209},
        30: {"selected_model": "arima_residual_daily", "mape": 0.2259, "challenger_model": "naive", "challenger_mape": 0.2707},
    },
    "blowing_agent": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0381, "challenger_model": "naive", "challenger_mape": 0.0407},
        30: {"selected_model": "naive", "mape": 0.1598, "challenger_model": "arima_residual_daily", "challenger_mape": 0.1639},
    },
    "styrene": {
        7: {"selected_model": "arima_residual_daily", "mape": 0.0448, "challenger_model": "naive", "challenger_mape": 0.0500},
        30: {"selected_model": "arima_residual_daily", "mape": 0.0706, "challenger_model": "naive", "challenger_mape": 0.0745},
    },
}


def get_champion_forecast_registry() -> Dict[str, Dict[int, Dict[str, Any]]]:
    return {
        product_id: {horizon: dict(payload) for horizon, payload in horizons.items()}
        for product_id, horizons in _CHAMPION_MODEL_REGISTRY.items()
    }


def resolve_champion_model(product_id: str, horizon_days: int) -> Dict[str, Any]:
    selected_horizon = 7 if int(horizon_days or 0) <= 7 else 30
    product_registry = _CHAMPION_MODEL_REGISTRY.get(product_id) or {}
    payload = product_registry.get(selected_horizon)
    if payload is None:
        return {
            "enabled": True,
            "registry_hit": False,
            "product_id": product_id,
            "requested_horizon_days": horizon_days,
            "selected_horizon_days": selected_horizon,
            "selected_model": "arima_residual_daily",
            "source_window": _CHAMPION_ROUTING_WINDOW,
            "step_days": _CHAMPION_ROUTING_STEP_DAYS,
            "message": "No measured champion entry; using ARIMA residual daily default.",
        }
    selected = dict(payload)
    selected.update(
        {
            "enabled": True,
            "registry_hit": True,
            "product_id": product_id,
            "requested_horizon_days": horizon_days,
            "selected_horizon_days": selected_horizon,
            "source_window": _CHAMPION_ROUTING_WINDOW,
            "step_days": _CHAMPION_ROUTING_STEP_DAYS,
        }
    )
    selected["mape_delta_vs_challenger"] = (
        round(float(selected["mape"]) - float(selected["challenger_mape"]), 4)
        if selected.get("mape") is not None and selected.get("challenger_mape") is not None
        else None
    )
    return selected


def _live_challenger_candidate(product_id: str, horizon_days: int) -> Dict[str, Any] | None:
    if not _LIVE_CHALLENGER_REGISTRY_PATH.exists():
        return None
    try:
        payload = json.loads(_LIVE_CHALLENGER_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    policy = payload.get("promotion_policy") if isinstance(payload, dict) else {}
    if not isinstance(policy, dict) or not bool(policy.get("live_routing_enabled")):
        return None
    models = payload.get("models") if isinstance(payload, dict) else {}
    if not isinstance(models, dict):
        return None
    candidate = (models.get(str(int(horizon_days))) or {}).get(product_id)
    if not isinstance(candidate, dict):
        return None
    if not bool(candidate.get("enabled")):
        return None
    if candidate.get("stability_status") != "deepseek_approved":
        return None
    if candidate.get("model_key") not in {"linear_momentum", "xgboost"}:
        return None
    item = dict(candidate)
    item["product_id"] = str(item.get("product_id") or product_id)
    item["horizon_days"] = int(item.get("horizon_days") or horizon_days)
    return item


def _live_challenger_target_prediction(
    product_id: str,
    candidate: Dict[str, Any],
    as_of_date: str,
    as_of_price: float,
) -> Dict[str, Any] | None:
    try:
        from scripts.run_forecast_shadow_mode import (
            DEFAULT_DATASET,
            _load_dataset,
            _shadow_feature_row,
            _train_and_predict_challenger,
        )

        horizon_days = int(candidate["horizon_days"])
        dataset = _load_dataset(DEFAULT_DATASET)
        live_row = _shadow_feature_row(
            product_id=product_id,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            as_of_price=as_of_price,
            event_lookback_hours=168,
            event_limit=80,
        )
        predicted_price, predicted_change, model_meta = _train_and_predict_challenger(
            dataset=dataset,
            candidate=candidate,
            live_row=live_row,
            seed=42,
        )
    except Exception as exc:
        print(f"[forecasting_engine] live challenger promotion skipped for {product_id}: {exc}")
        return None

    if predicted_price is None:
        return None
    return {
        "candidate": candidate,
        "predicted_price": round(float(predicted_price), 6),
        "predicted_change_pct": predicted_change,
        "training": model_meta,
    }


def _apply_live_challenger_target_overlay(
    node_id: str,
    dense_path: list[dict[str, Any]],
    champion_routing: dict[str, Any],
    series: pd.Series,
    cutoff_date: str | None,
    start_date: str | datetime.date | pd.Timestamp | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    if cutoff_date is not None or not dense_path:
        return dense_path, champion_routing, None

    latest_price = float(series.iloc[-1])
    as_of_date = (
        pd.Timestamp(start_date).strftime("%Y-%m-%d")
        if start_date is not None
        else pd.Timestamp(series.index[-1]).strftime("%Y-%m-%d")
    )
    promoted_points: list[dict[str, Any]] = []
    promoted_payloads: list[dict[str, Any]] = []

    for point in dense_path:
        step_number = int(point.get("step_number") or 0)
        candidate = _live_challenger_candidate(node_id, step_number)
        if candidate is None:
            promoted_points.append(point)
            continue

        prediction = _live_challenger_target_prediction(node_id, candidate, as_of_date, latest_price)
        if prediction is None:
            promoted_points.append(point)
            continue

        promoted_price = round(float(prediction["predicted_price"]), 2)
        promoted_point = {
            **point,
            "baseline_price": promoted_price,
            "final_price": promoted_price,
            "base": promoted_price,
            "bull": promoted_price,
            "bear": promoted_price,
            "source": "deepseek_approved_live_challenger",
            "cause_summary": (
                "DeepSeek-approved challenger promoted to live routing for "
                f"{node_id} {step_number}d. Model: {candidate.get('model')}; "
                f"mean rolling improvement: {candidate.get('mean_improvement_pct')}."
            ),
            "live_challenger_promotion": {
                "active": True,
                "model": candidate.get("model"),
                "model_key": candidate.get("model_key"),
                "feature_set": candidate.get("feature_set"),
                "horizon_days": step_number,
                "deepseek_decision": candidate.get("deepseek_decision"),
                "mean_improvement_pct": candidate.get("mean_improvement_pct"),
                "pass_rate": candidate.get("pass_rate"),
                "split_count": candidate.get("split_count"),
                "predicted_change_pct": prediction.get("predicted_change_pct"),
            },
        }
        promoted_points.append(promoted_point)
        promoted_payloads.append(promoted_point["live_challenger_promotion"])

    if not promoted_payloads:
        return dense_path, champion_routing, None

    updated_routing = {
        **champion_routing,
        "selected_model": "deepseek_approved_live_challenger",
        "live_challenger_promotions": promoted_payloads,
        "message": "DeepSeek-approved challenger target forecast promoted into live output.",
    }
    return promoted_points, updated_routing, "deepseek_approved_live_challenger"


# ---------------------------------------------------------------------------
# Per-product ARIMA order table (Phase 2.2)
# ---------------------------------------------------------------------------
# Orders selected by AIC grid search (p∈{0-3}, d∈{0-1}, q∈{0-3}) and validated
# via mini-backtest on the Phase 1 window. Only products that improved MAPE by
# ≥5% relative are stored here. Missing products fall back to (2,1,2).
# File is written by backend/scripts/refit_arima_orders.py; do not hand-edit.

_ARIMA_ORDERS_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "data" / "arima_orders.json"
)


def _load_arima_orders() -> Dict[str, Tuple[int, int, int]]:
    """Load per-product ARIMA orders from JSON.  Returns {} on any error (silent fallback)."""
    try:
        with open(_ARIMA_ORDERS_PATH, encoding="utf-8") as _f:
            _raw = json.load(_f)
        return {
            k: (int(v[0]), int(v[1]), int(v[2]))
            for k, v in _raw.items()
            if not k.startswith("_") and isinstance(v, list) and len(v) == 3
        }
    except Exception:  # noqa: BLE001
        return {}


_ARIMA_ORDERS: Dict[str, Tuple[int, int, int]] = _load_arima_orders()


def _arima_order_for(node_id: str) -> Tuple[int, int, int]:
    """Return the per-product ARIMA order, falling back to the default (2,1,2)."""
    return _ARIMA_ORDERS.get(node_id, (2, 1, 2))


# ---------------------------------------------------------------------------
# Data-quality helper (Phase 2.1)
# ---------------------------------------------------------------------------

_FFILL_LOW_DATA_THRESHOLD = 0.30  # ffill_ratio >= this → low_data: true
_CADENCE_LOOKBACK_DAYS = 90
_CADENCE_NATIVE_STEP_DAYS: Dict[str, int] = {
    "daily-capable": 1,
    "multi-day": 3,
    "weekly": 7,
    "sparse": 14,
    "unsupported": 14,
}


def get_price_series_quality(
    node_id: str,
    days: int = 500,
    cutoff_date: str | None = None,
) -> dict:
    """Return data-quality metadata for the canonical series of *node_id*.

    Metrics:
      raw_observations  — count of non-null price rows in the DB window
      model_window_days — calendar-day span of that window
      ffill_ratio       — (window_days - raw_observations) / window_days
      low_data          — True when ffill_ratio >= _FFILL_LOW_DATA_THRESHOLD
      reason            — human-readable explanation when low_data is True, else ""
    """
    from sqlalchemy import text  # local import keeps module light

    _NULL = {
        "raw_observations": 0,
        "model_window_days": 0,
        "ffill_ratio": 1.0,
        "low_data": True,
        "reason": "no_data",
    }

    if node_id not in _PRODUCT_MAP:
        return _NULL

    pid, pt = _PRODUCT_MAP[node_id]

    try:
        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COUNT(*), MIN(date), MAX(date) "
                    "FROM ("
                    "  SELECT date FROM market_prices "
                    "  WHERE product_id = :pid AND price_type = :pt "
                    "  AND (:cutoff IS NULL OR date <= :cutoff) "
                    "  AND price IS NOT NULL "
                    "  ORDER BY date DESC LIMIT :lim"
                    ") sub"
                ),
                {"pid": pid, "pt": pt, "cutoff": cutoff_date, "lim": days},
            ).fetchone()
    except Exception as e:
        print(f"[forecasting_engine] get_price_series_quality({node_id}): {e}")
        return _NULL

    if not row or row[0] == 0:
        return _NULL

    raw_count = int(row[0])
    min_date = pd.Timestamp(row[1])
    max_date = pd.Timestamp(row[2])
    calendar_span = int((max_date - min_date).days) + 1
    ffill_ratio = (
        round((calendar_span - raw_count) / calendar_span, 4)
        if calendar_span > 0
        else 1.0
    )
    low_data = ffill_ratio >= _FFILL_LOW_DATA_THRESHOLD
    reason = f"ffill_ratio>={_FFILL_LOW_DATA_THRESHOLD}" if low_data else ""

    return {
        "raw_observations": raw_count,
        "model_window_days": calendar_span,
        "ffill_ratio": ffill_ratio,
        "low_data": low_data,
        "reason": reason,
    }


def _series_for_product(node_id: str, days: int = 500, cutoff_date: str | None = None) -> pd.Series:
    """Look up the canonical price series for a product node."""
    if node_id in _PRODUCT_MAP:
        pid, pt = _PRODUCT_MAP[node_id]
        s = get_price_series(pid, pt, days=days, cutoff_date=cutoff_date)
        if not s.empty:
            return s
    # fallback: search any price_type for this product
    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT date, price FROM market_prices "
                    "WHERE product_id = :pid AND price IS NOT NULL "
                    "  AND (:cutoff IS NULL OR date <= :cutoff) "
                    "ORDER BY date DESC LIMIT 1"
                ),
                {"pid": node_id, "cutoff": cutoff_date},
            ).fetchone()
        if row:
            return pd.Series([float(row[1])], index=[pd.Timestamp(row[0])], dtype=float)
    except Exception:
        pass
    return pd.Series(dtype=float)


def _classify_change_frequency(change_frequency: float) -> str:
    if change_frequency > 0.50:
        return "daily-capable"
    if change_frequency > 0.20:
        return "multi-day"
    if change_frequency > 0.05:
        return "weekly"
    return "sparse"


def get_cadence_info(
    node_id: str,
    days: int = _CADENCE_LOOKBACK_DAYS,
    cutoff_date: str | None = None,
) -> dict:
    """Return cadence metadata for the canonical price series of *node_id*."""

    def _unsupported() -> dict:
        return {
            "cadence_class": "unsupported",
            "change_frequency": 0.0,
            "native_step_days": _CADENCE_NATIVE_STEP_DAYS["unsupported"],
            "daily_forecast_advised": False,
            "display_preferred_series": "forecast_native",
        }

    if node_id not in _PRODUCT_MAP:
        return _unsupported()

    series = _series_for_product(node_id, days=days, cutoff_date=cutoff_date)
    if series.empty:
        return _unsupported()

    window = series.sort_index()
    if len(window) > days:
        window = window.iloc[-days:]

    if len(window) <= 1:
        cadence_class = "sparse"
        change_frequency = 0.0
    else:
        change_days = int(window.diff().fillna(0.0).ne(0.0).sum())
        change_frequency = round(change_days / max(len(window) - 1, 1), 4)
        cadence_class = _classify_change_frequency(change_frequency)

    daily_forecast_advised = cadence_class == "daily-capable"
    return {
        "cadence_class": cadence_class,
        "change_frequency": change_frequency,
        "native_step_days": _CADENCE_NATIVE_STEP_DAYS[cadence_class],
        "daily_forecast_advised": daily_forecast_advised,
        "display_preferred_series": (
            "forecast" if daily_forecast_advised else "forecast_native"
        ),
    }


def _naive_champion_forecast_path(
    node_id: str,
    series: pd.Series,
    cadence_info: dict,
    champion_routing: dict,
    horizon_days: int,
    start_date: str | datetime.date | pd.Timestamp | None = None,
    cutoff_date: str | None = None,
) -> dict:
    native_step_days = cadence_info.get("native_step_days")
    if not isinstance(native_step_days, int) or native_step_days < 1:
        native_step_days = 1

    if start_date is None:
        anchor_date = pd.Timestamp(series.index[-1])
    else:
        anchor_date = pd.Timestamp(start_date)

    latest_price = round(float(series.iloc[-1]), 2)
    baseline_message = (
        "Naive champion forecast selected from old-data walk-forward validation; "
        "baseline holds the latest observed price constant."
    )
    dense_path: list[dict[str, Any]] = []
    for step_number in range(1, horizon_days + 1):
        forecast_date = (anchor_date + pd.Timedelta(days=step_number)).strftime("%Y-%m-%d")
        dense_path.append(
            {
                "date": forecast_date,
                "forecast_date": forecast_date,
                "step_number": step_number,
                "baseline_price": latest_price,
                "proposed_override_pct": 0.0,
                "applied_override_pct": 0.0,
                "final_price": latest_price,
                "base": latest_price,
                "bull": latest_price,
                "bear": latest_price,
                "confidence": 0.5,
                "cause_summary": baseline_message,
                "evidence_event_ids": [],
                "source": "naive_champion",
                "daily_calibration": {
                    "active": False,
                    "method": "naive_champion_constant_price",
                    "random_noise": False,
                },
            }
        )

    dense_path, champion_routing, promoted_source = _apply_live_challenger_target_overlay(
        node_id,
        dense_path,
        champion_routing,
        series,
        cutoff_date=cutoff_date,
        start_date=start_date,
    )
    if promoted_source:
        baseline_message = "DeepSeek-approved challenger target forecast promoted into live output."

    if cadence_info.get("daily_forecast_advised"):
        native_path = list(dense_path)
    else:
        native_path = [
            point
            for point in dense_path
            if point["step_number"] == horizon_days or point["step_number"] % native_step_days == 0
        ]

    return {
        "status": "ok",
        "source": promoted_source or "naive_champion",
        "cadence_info": cadence_info,
        "model_components": {
            "arima_weight": 0.0,
            "xgboost_weight": 0.0,
            "kimi_exogenous_active": False,
            "kimi_feature_snapshot_id": None,
            "residual_daily_calibration": dense_path[0]["daily_calibration"] if dense_path else {"active": False},
            "champion_routing": champion_routing,
        },
        "dense_path": dense_path,
        "native_path": native_path,
        "message": baseline_message,
    }


def get_native_baseline_forecast_path(
    node_id: str,
    horizon_days: int = 30,
    cutoff_date: str | None = None,
    start_date: str | datetime.date | pd.Timestamp | None = None,
    use_champion_routing: bool = True,
) -> dict:
    """Return native baseline forecast points from real per-step ARIMA output.

    Daily-capable products return every step.  Lower-cadence products return a
    cadence-matched subset while preserving the dense ARIMA path for diagnostics.
    No synthetic volatility, random walk, or interpolation is introduced here.
    """
    cadence_info = get_cadence_info(node_id, cutoff_date=cutoff_date)
    champion_routing = resolve_champion_model(node_id, horizon_days)
    if not use_champion_routing:
        champion_routing = {
            **champion_routing,
            "enabled": False,
            "selected_model": "arima_residual_daily",
            "message": "Champion routing bypassed for challenger measurement.",
        }
    native_step_days = cadence_info.get("native_step_days")
    if not isinstance(native_step_days, int) or native_step_days < 1:
        native_step_days = 1

    series = _series_for_product(node_id, cutoff_date=cutoff_date)
    if series.empty:
        return {
            "status": "no_data",
            "source": "arima_native",
            "cadence_info": cadence_info,
            "model_components": {"champion_routing": champion_routing},
            "dense_path": [],
            "native_path": [],
            "message": "No canonical price series available for baseline forecast.",
        }

    if champion_routing.get("selected_model") == "naive":
        return _naive_champion_forecast_path(
            node_id,
            series,
            cadence_info,
            champion_routing,
            horizon_days,
            start_date=start_date,
            cutoff_date=cutoff_date,
        )

    arima_fc = _fit_arima_forecast(
        series,
        horizon_days,
        order=_arima_order_for(node_id),
    )
    if arima_fc is None or len(arima_fc) == 0:
        if cutoff_date is None and any(
            _live_challenger_candidate(node_id, step_number) is not None
            for step_number in range(1, horizon_days + 1)
        ):
            return _naive_champion_forecast_path(
                node_id,
                series,
                cadence_info,
                {
                    **champion_routing,
                    "selected_model": "deepseek_approved_live_challenger",
                    "message": "ARIMA unavailable; using constant path with DeepSeek-approved challenger target promotion.",
                },
                horizon_days,
                start_date=start_date,
                cutoff_date=cutoff_date,
            )
        return {
            "status": "model_unavailable",
            "source": "arima_native",
            "cadence_info": cadence_info,
            "model_components": {"champion_routing": champion_routing},
            "dense_path": [],
            "native_path": [],
            "message": "ARIMA did not produce a baseline forecast path.",
        }

    xgb_fc = None
    blend_components = {
        "arima_weight": 1.0,
        "xgboost_weight": 0.0,
        "kimi_exogenous_active": False,
        "kimi_feature_snapshot_id": None,
        "champion_routing": champion_routing,
    }
    baseline_source = "arima_native"
    baseline_message = "Real per-step ARIMA baseline forecast path generated."
    if _xgboost_enabled():
        try:
            df_feat = build_feature_matrix(node_id, cutoff_date=cutoff_date)
            xgb_fc = _fit_xgboost_forecast(
                df_feat,
                horizon_days,
                node_id=node_id,
                cutoff_date=cutoff_date,
            )
        except Exception as exc:
            print(f"[forecasting_engine] native baseline XGBoost path skipped: {exc}")

    forecast_values = np.asarray(arima_fc[:horizon_days], dtype=float)
    if xgb_fc is not None and len(xgb_fc) >= horizon_days:
        blend_components = _native_baseline_blend_components(
            node_id,
            cadence_info,
            cutoff_date=cutoff_date,
        )
        blend_components = {**blend_components, "champion_routing": champion_routing}
        baseline_source = str(blend_components.get("source") or "arima_xgboost_native")
        baseline_message = str(
            blend_components.get("message")
            or "Real per-step baseline forecast path generated with ARIMA + XGBoost."
        )
        forecast_values = (
            np.asarray(arima_fc[:horizon_days], dtype=float)
            * float(blend_components.get("arima_weight") or 0.0)
            + np.asarray(xgb_fc[:horizon_days], dtype=float)
            * float(blend_components.get("xgboost_weight") or 0.0)
        )

    forecast_values, residual_calibration = _historical_residual_daily_calibration(
        series,
        forecast_values,
        cadence_info,
    )
    if residual_calibration.get("active"):
        blend_components = {
            **blend_components,
            "residual_daily_calibration": residual_calibration,
        }
        baseline_source = f"{baseline_source}_residual_daily"
        baseline_message = (
            f"{baseline_message} Daily path calibrated with recent historical residual volatility."
        )
    else:
        blend_components = {
            **blend_components,
            "residual_daily_calibration": residual_calibration,
        }

    if start_date is None:
        anchor_date = pd.Timestamp(series.index[-1])
    else:
        anchor_date = pd.Timestamp(start_date)

    dense_path: list[dict[str, Any]] = []
    for step_number, price in enumerate(forecast_values[:horizon_days], start=1):
        forecast_date = (anchor_date + pd.Timedelta(days=step_number)).strftime("%Y-%m-%d")
        baseline_price = round(float(price), 2)
        dense_path.append(
            {
                "date": forecast_date,
                "forecast_date": forecast_date,
                "step_number": step_number,
                "baseline_price": baseline_price,
                "proposed_override_pct": 0.0,
                "applied_override_pct": 0.0,
                "final_price": baseline_price,
                "base": baseline_price,
                "bull": baseline_price,
                "bear": baseline_price,
                "confidence": None,
                "cause_summary": baseline_message,
                "evidence_event_ids": [],
                "source": baseline_source,
                "daily_calibration": residual_calibration,
            }
        )

    dense_path, champion_routing, promoted_source = _apply_live_challenger_target_overlay(
        node_id,
        dense_path,
        champion_routing,
        series,
        cutoff_date,
        start_date,
    )
    if promoted_source:
        blend_components = {**blend_components, "champion_routing": champion_routing}
        baseline_source = promoted_source
        baseline_message = "DeepSeek-approved challenger target forecast promoted into live output."

    if cadence_info.get("daily_forecast_advised"):
        native_path = list(dense_path)
    else:
        native_path = [
            point
            for point in dense_path
            if point["step_number"] == horizon_days or point["step_number"] % native_step_days == 0
        ]

    return {
        "status": "ok",
        "source": baseline_source,
        "cadence_info": cadence_info,
        "model_components": blend_components,
        "dense_path": dense_path,
        "native_path": native_path,
        "message": baseline_message,
    }


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def build_feature_matrix(node_id: str, days: int = 500, cutoff_date: str | None = None) -> pd.DataFrame:
    """Build XGBoost feature matrix for *node_id*.

    Features:
      price_lag_{1,2,3,5,10} — own lagged prices
      ma_5, ma_10, ma_20     — moving averages
      naphtha_lag_1          — naphtha price lag-1 (key upstream signal)
    naphtha_mom_5d         — naphtha 5-day momentum
    price_mom_5d, price_mom_10d
    deepseek_*             — proxy exogenous regressors, overridden by live
                     DeepSeek snapshot on the latest row for live runs
      month                  — calendar month (1-12)
            is_q1                  — Q1 indicator (Jan–Mar, post-holiday)
      dow                    — day of week (0=Mon)
    """
    s = _series_for_product(node_id, days=days, cutoff_date=cutoff_date)
    if s.empty or len(s) < 30:
        return pd.DataFrame()

    df = pd.DataFrame({"price": s})
    df.index = pd.to_datetime(df.index)

    for lag in (1, 2, 3, 5, 10):
        df[f"price_lag_{lag}"] = df["price"].shift(lag)

    for win in (5, 10, 20):
        df[f"ma_{win}"] = df["price"].rolling(win).mean()

    nap = get_naphtha_series(days=days, cutoff_date=cutoff_date)
    if not nap.empty:
        nap_aligned = nap.reindex(df.index)
        if nap_aligned.notna().any():
            nap_aligned = nap_aligned.ffill().bfill()
            df["naphtha_lag_1"] = nap_aligned.shift(1)
            df["naphtha_mom_5d"] = nap_aligned.pct_change(5) * 100
        else:
            # Some ERP-derived product series are historical windows that do not
            # overlap the current naphtha series. Keep upstream features neutral
            # so self-history features can still drive the short-term model.
            df["naphtha_lag_1"] = float(nap.iloc[-1])
            df["naphtha_mom_5d"] = 0.0
    else:
        df["naphtha_lag_1"] = np.nan
        df["naphtha_mom_5d"] = 0.0

    df["price_mom_5d"] = df["price"].pct_change(5) * 100
    df["price_mom_10d"] = df["price"].pct_change(10) * 100
    df["month"] = df.index.month
    df["is_q1"] = (df.index.month <= 3).astype(int)
    df["dow"] = df.index.dayofweek
    df = _add_kimi_regressor_features(node_id, df, cutoff_date=cutoff_date)

    df = df.dropna()
    return df


# ---------------------------------------------------------------------------
# ARIMA(2,1,2) component
# ---------------------------------------------------------------------------


def _fit_arima_forecast(
    series: pd.Series, horizon: int, order: Tuple[int, int, int] = (2, 1, 2)
) -> Optional[np.ndarray]:
    """Fit ARIMA with *order* and return point-forecast array of length *horizon*.

    The order defaults to (2,1,2) and is overridden per-product by the
    _ARIMA_ORDERS table loaded from backend/data/arima_orders.json at import time.
    """
    if len(series) < 40:
        return None
    try:
        from statsmodels.tsa.arima.model import ARIMA

        model = ARIMA(series.values, order=order)
        res = model.fit(method_kwargs={"warn_convergence": False})
        fc = res.forecast(steps=horizon)
        return np.asarray(fc, dtype=float)
    except Exception as e:
        print(f"[forecasting_engine] ARIMA fit error: {e}")
        return None


def _historical_residual_daily_calibration(
    series: pd.Series,
    forecast_values: np.ndarray,
    cadence_info: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Shape the daily forecast path with recent real daily market movement.

    ARIMA multi-step means are often intentionally smooth. For dashboard use we
    still need a day-by-day path that carries realistic volatility. This layer is
    deterministic: it reuses recent observed return deviations, dampens them by
    source cadence, and re-anchors the final horizon to the model mean. It does
    not add random noise.
    """
    values = np.asarray(forecast_values, dtype=float)
    if values.size < 3 or series.empty:
        return values, {"active": False, "reason": "insufficient_forecast"}

    cleaned_series = pd.Series(series, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    cleaned_series = cleaned_series[cleaned_series > 0]
    if len(cleaned_series) < 20:
        return values, {"active": False, "reason": "insufficient_history"}

    returns = cleaned_series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if returns.empty:
        return values, {"active": False, "reason": "no_returns"}

    recent_returns = returns.tail(90)
    nonzero_returns = recent_returns[recent_returns.abs() > 1e-6]
    if len(nonzero_returns) < 6:
        return values, {"active": False, "reason": "low_recent_movement"}

    recent_volatility = float(nonzero_returns.std(ddof=0) or 0.0)
    if not np.isfinite(recent_volatility) or recent_volatility <= 1e-6:
        return values, {"active": False, "reason": "zero_volatility"}
    if recent_volatility < 0.0015:
        return values, {
            "active": False,
            "reason": "below_daily_volatility_threshold",
            "recent_volatility_pct": round(recent_volatility * 100.0, 4),
        }

    cadence_class = str(cadence_info.get("cadence_class") or "unsupported")
    cadence_weight = {
        "daily-capable": 1.0,
        "multi-day": 0.85,
        "weekly": 0.65,
        "sparse": 0.45,
        "unsupported": 0.35,
    }.get(cadence_class, 0.35)

    pattern_source = nonzero_returns.tail(max(10, min(30, len(nonzero_returns))))
    centered_pattern = pattern_source - float(pattern_source.median())
    if centered_pattern.abs().sum() <= 1e-9:
        centered_pattern = pattern_source - float(pattern_source.mean())
    if centered_pattern.abs().sum() <= 1e-9:
        return values, {"active": False, "reason": "flat_pattern"}

    horizon = int(values.size)
    repeated_pattern = np.resize(centered_pattern.to_numpy(dtype=float), horizon)
    cap = min(max(recent_volatility * 2.5, 0.0025), 0.08)
    step_numbers = np.arange(1, horizon + 1, dtype=float)
    dampening = np.linspace(1.0, 0.45, horizon)
    innovations = np.clip(repeated_pattern, -cap, cap) * cadence_weight * dampening

    log_deviation = np.cumsum(np.log1p(np.clip(innovations, -0.30, 0.30)))
    if horizon > 1:
        # Preserve the model's 30-day anchor while retaining intra-horizon shape.
        log_deviation = log_deviation - (step_numbers / horizon) * log_deviation[-1]

    adjusted_values = values * np.exp(log_deviation)
    adjusted_values = np.where(np.isfinite(adjusted_values), adjusted_values, values)
    adjusted_values = np.maximum(adjusted_values, 0.01)

    max_pct_deviation = float(np.max(np.abs((adjusted_values - values) / np.maximum(values, 1e-9))) * 100.0)
    return adjusted_values, {
        "active": True,
        "source": "historical_residual_daily_calibration",
        "cadence_class": cadence_class,
        "cadence_weight": round(cadence_weight, 4),
        "pattern_days": int(len(pattern_source)),
        "recent_volatility_pct": round(recent_volatility * 100.0, 4),
        "max_path_adjustment_pct": round(max_pct_deviation, 4),
        "random_noise": False,
    }


_KIMI_FEATURE_COLS = [
    "kimi_bias_1d",
    "kimi_bias_7d",
    "kimi_bias_30d",
    "kimi_confidence",
    "kimi_supply_score",
    "kimi_feedstock_pressure",
    "kimi_event_impact",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, *, low: float, high: float, default: float = 0.0) -> float:
    numeric = _safe_float(value, default)
    return round(max(low, min(high, numeric)), 4)


def _neutral_kimi_regressor_values() -> dict[str, float]:
    return {
        "kimi_bias_1d": 0.0,
        "kimi_bias_7d": 0.0,
        "kimi_bias_30d": 0.0,
        "kimi_confidence": 0.0,
        "kimi_supply_score": 0.0,
        "kimi_feedstock_pressure": 0.0,
        "kimi_event_impact": 0.0,
    }


def _get_live_kimi_feature_bundle(
    node_id: str,
    *,
    include_expired: bool = False,
) -> dict[str, Any]:
    try:
        from backend.src.service.kimi_forecast_feature_service import (
            kimi_forecast_feature_service,
        )

        bundle = kimi_forecast_feature_service.get_product_features(
            node_id,
            include_expired=include_expired,
        )
        return bundle if isinstance(bundle, dict) else {}
    except Exception as exc:
        print(f"[forecasting_engine] Kimi feature lookup failed for {node_id}: {exc}")
        return {}


def _get_live_kimi_regressor_state(
    node_id: str,
    *,
    include_expired: bool = False,
) -> dict[str, Any]:
    bundle = _get_live_kimi_feature_bundle(node_id, include_expired=include_expired)
    feature = bundle.get("feature") if isinstance(bundle.get("feature"), dict) else {}
    horizon = (
        feature.get("horizon_bias_pct")
        if isinstance(feature.get("horizon_bias_pct"), dict)
        else {}
    )
    values = {
        "kimi_bias_1d": _clamp_float(horizon.get("1d"), low=-8.0, high=8.0),
        "kimi_bias_7d": _clamp_float(horizon.get("7d"), low=-8.0, high=8.0),
        "kimi_bias_30d": _clamp_float(horizon.get("30d"), low=-8.0, high=8.0),
        "kimi_confidence": _clamp_float(feature.get("confidence"), low=0.0, high=1.0),
        "kimi_supply_score": _clamp_float(
            feature.get("supply_disruption_score"),
            low=-1.0,
            high=1.0,
        ),
        "kimi_feedstock_pressure": _clamp_float(
            feature.get("feedstock_pressure_score"),
            low=-1.0,
            high=1.0,
        ),
        "kimi_event_impact": _clamp_float(
            feature.get("recent_event_impact"),
            low=0.0,
            high=1.0,
        ),
    }
    return {
        "snapshot_id": bundle.get("snapshot_id"),
        "status": str(bundle.get("status") or "missing"),
        "values": values,
    }


def _add_kimi_regressor_features(
    node_id: str,
    df: pd.DataFrame,
    *,
    cutoff_date: str | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df

    elasticity = float(_ELASTICITIES.get(node_id, 0.55))
    price_mom_5 = df["price_mom_5d"].fillna(0.0)
    price_mom_10 = df["price_mom_10d"].fillna(0.0)
    price_mom_20 = df["price"].pct_change(20).fillna(0.0) * 100.0
    naphtha_mom = df["naphtha_mom_5d"].fillna(0.0)
    ma_5 = df["ma_5"].replace(0.0, np.nan).fillna(df["price"])
    deviation_pct = ((df["price"] - ma_5) / ma_5).replace([np.inf, -np.inf], 0.0).fillna(0.0) * 100.0

    df["kimi_bias_1d"] = (price_mom_5 * 0.45 + naphtha_mom * elasticity * 0.20).clip(-8.0, 8.0)
    df["kimi_bias_7d"] = (price_mom_10 * 0.30 + naphtha_mom * elasticity * 0.25).clip(-8.0, 8.0)
    df["kimi_bias_30d"] = (
        price_mom_20 * 0.20 + price_mom_10 * 0.25 + naphtha_mom * elasticity * 0.15
    ).clip(-8.0, 8.0)
    df["kimi_confidence"] = (
        0.75 - ((price_mom_5.abs() + naphtha_mom.abs()) / 60.0)
    ).clip(0.15, 0.9)
    df["kimi_supply_score"] = (deviation_pct / 6.0).clip(-1.0, 1.0)
    df["kimi_feedstock_pressure"] = (naphtha_mom * elasticity / 8.0).clip(-1.0, 1.0)
    df["kimi_event_impact"] = (
        (price_mom_5.abs() * 0.35 + naphtha_mom.abs() * 0.25) / 8.0
    ).clip(0.0, 1.0)

    if cutoff_date is None:
        live_state = _get_live_kimi_regressor_state(node_id)
        if live_state.get("status") == "success":
            for col, value in (live_state.get("values") or {}).items():
                if col in df.columns:
                    df.at[df.index[-1], col] = value

    return df


def _native_baseline_blend_components(
    node_id: str,
    cadence_info: dict[str, Any],
    *,
    cutoff_date: str | None = None,
) -> dict[str, Any]:
    xgboost_weight = 0.22
    data_quality = get_price_series_quality(node_id, cutoff_date=cutoff_date)
    cadence_class = str(cadence_info.get("cadence_class") or "unsupported")
    if cadence_class in {"multi-day", "weekly", "sparse"}:
        xgboost_weight += 0.08
    if data_quality.get("low_data"):
        xgboost_weight += 0.05

    kimi_state = (
        _get_live_kimi_regressor_state(node_id)
        if cutoff_date is None
        else {"status": "historical", "snapshot_id": None, "values": _neutral_kimi_regressor_values()}
    )
    if kimi_state.get("status") == "success":
        kimi_confidence = float(
            (kimi_state.get("values") or {}).get("kimi_confidence") or 0.0
        )
        xgboost_weight += min(0.15, kimi_confidence * 0.20)

    xgboost_weight = round(min(0.55, max(0.15, xgboost_weight)), 4)
    arima_weight = round(1.0 - xgboost_weight, 4)
    kimi_active = kimi_state.get("status") == "success"
    return {
        "arima_weight": arima_weight,
        "xgboost_weight": xgboost_weight,
        "kimi_exogenous_active": kimi_active,
        "kimi_feature_snapshot_id": kimi_state.get("snapshot_id"),
        "source": (
            "arima_xgboost_kimi_exogenous"
            if kimi_active
            else "arima_xgboost_native"
        ),
        "message": (
            "Real per-step baseline forecast path generated with ARIMA + XGBoost + Kimi exogenous features."
            if kimi_active
            else "Real per-step baseline forecast path generated with ARIMA + XGBoost."
        ),
    }


# ---------------------------------------------------------------------------
# XGBoost component
# ---------------------------------------------------------------------------

_FEATURE_COLS = [
    "price_lag_1",
    "price_lag_2",
    "price_lag_3",
    "price_lag_5",
    "price_lag_10",
    "ma_5",
    "ma_10",
    "ma_20",
    "naphtha_lag_1",
    "naphtha_mom_5d",
    "price_mom_5d",
    "price_mom_10d",
    "kimi_bias_1d",
    "kimi_bias_7d",
    "kimi_bias_30d",
    "kimi_confidence",
    "kimi_supply_score",
    "kimi_feedstock_pressure",
    "kimi_event_impact",
    "month",
    "is_q1",
    "dow",
]


def _fit_xgboost_forecast(
    df: pd.DataFrame,
    horizon: int,
    node_id: str | None = None,
    cutoff_date: str | None = None,
) -> Optional[np.ndarray]:
    """Train XGBoost on *df* and predict next *horizon* steps iteratively."""
    if not _xgboost_enabled():
        return None

    if df.empty or len(df) < 50:
        return None

    feat_cols = [c for c in _FEATURE_COLS if c in df.columns]
    if len(feat_cols) < 5:
        return None

    try:
        from xgboost import XGBRegressor

        X = df[feat_cols].values
        y = df["price"].values

        # simple rolling train/test to get residual scale
        split = int(len(X) * 0.85)
        model = XGBRegressor(
            n_estimators=150,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="reg:squarederror",
            verbosity=0,
            n_jobs=1,
        )
        model.fit(
            X[:split], y[:split], eval_set=[(X[split:], y[split:])], verbose=False
        )

        # Iterative multi-step forecast
        last_row = df.iloc[-1].copy()
        price_hist = list(df["price"].values[-20:])  # ring buffer for MA recalc
        forecasts = []

        nap_level = (
            float(df["naphtha_lag_1"].iloc[-1]) if "naphtha_lag_1" in df else np.nan
        )
        nap_drift = (
            float(df["naphtha_mom_5d"].iloc[-1]) / 5.0
            if "naphtha_mom_5d" in df
            else 0.0
        )
        kimi_future = {
            col: float(df[col].iloc[-1]) if col in df.columns else 0.0
            for col in _KIMI_FEATURE_COLS
        }
        if node_id and cutoff_date is None:
            live_kimi = _get_live_kimi_regressor_state(node_id)
            if live_kimi.get("status") == "success":
                kimi_future.update(live_kimi.get("values") or {})
        next_date = df.index[-1]

        for step in range(horizon):
            next_date = next_date + pd.Timedelta(days=1)
            x_row = np.zeros(len(feat_cols))

            # Lag features from forecast buffer
            ph = price_hist[-10:]  # last 10 known/forecasted prices
            for i, lag in enumerate([1, 2, 3, 5, 10]):
                col = f"price_lag_{lag}"
                if col in feat_cols:
                    idx_f = feat_cols.index(col)
                    x_row[idx_f] = ph[-lag] if len(ph) >= lag else ph[0]

            # MA features
            for win in (5, 10, 20):
                col = f"ma_{win}"
                if col in feat_cols:
                    idx_f = feat_cols.index(col)
                    recent = price_hist[-win:]
                    x_row[idx_f] = float(np.mean(recent)) if recent else ph[-1]

            # Naphtha
            nap_level += nap_drift
            if "naphtha_lag_1" in feat_cols:
                x_row[feat_cols.index("naphtha_lag_1")] = nap_level
            if "naphtha_mom_5d" in feat_cols:
                x_row[feat_cols.index("naphtha_mom_5d")] = nap_drift * 5

            # Momentum of forecasted series
            if "price_mom_5d" in feat_cols and len(price_hist) >= 5:
                x_row[feat_cols.index("price_mom_5d")] = (
                    (price_hist[-1] - price_hist[-5]) / price_hist[-5] * 100
                )
            if "price_mom_10d" in feat_cols and len(price_hist) >= 10:
                x_row[feat_cols.index("price_mom_10d")] = (
                    (price_hist[-1] - price_hist[-10]) / price_hist[-10] * 100
                )

            if kimi_future:
                short_decay = max(0.35, 1.0 - (step / max(horizon - 1, 1)) * 0.55)
                medium_decay = max(0.55, 1.0 - (step / max(horizon - 1, 1)) * 0.25)
                step_deepseek = dict(kimi_future)
                step_deepseek["kimi_bias_1d"] = float(step_deepseek.get("kimi_bias_1d", 0.0)) * short_decay
                step_deepseek["kimi_bias_7d"] = float(step_deepseek.get("kimi_bias_7d", 0.0)) * medium_decay
                step_deepseek["kimi_confidence"] = float(step_deepseek.get("kimi_confidence", 0.0)) * medium_decay
                step_deepseek["kimi_event_impact"] = float(step_deepseek.get("kimi_event_impact", 0.0)) * medium_decay
                for col, value in step_deepseek.items():
                    if col in feat_cols:
                        x_row[feat_cols.index(col)] = value

            # Calendar
            if "month" in feat_cols:
                x_row[feat_cols.index("month")] = next_date.month
            if "is_q1" in feat_cols:
                x_row[feat_cols.index("is_q1")] = int(next_date.month <= 3)
            if "dow" in feat_cols:
                x_row[feat_cols.index("dow")] = next_date.dayofweek

            pred = float(model.predict(x_row.reshape(1, -1))[0])
            forecasts.append(pred)
            price_hist.append(pred)

        return np.array(forecasts, dtype=float)

    except Exception as e:
        print(f"[forecasting_engine] XGBoost error: {e}")
        return None


# ---------------------------------------------------------------------------
# Conformal-style uncertainty bands
# ---------------------------------------------------------------------------


def _rolling_coverage_std(series: pd.Series, window: int = 60) -> float:
    """Estimate 1-sigma spread from recent rolling residuals (conformal proxy)."""
    if len(series) < window + 1:
        window = max(10, len(series) - 1)
    if window < 2:
        return float(series.std()) if not series.empty else 50.0

    pct = series.pct_change().dropna().tail(window) * 100
    return float(pct.std()) if len(pct) > 1 else 1.0


# ---------------------------------------------------------------------------
# LSTM component (2-layer PyTorch)
# ---------------------------------------------------------------------------

_LSTM_LOOKBACK = 30  # input window length
_LSTM_HIDDEN = 32
_LSTM_EPOCHS = 40
_LSTM_LR = 0.005


def _fit_lstm_forecast(series: pd.Series, horizon: int) -> Optional[np.ndarray]:
    """Train a lightweight 2-layer LSTM on *series* and predict *horizon* steps.

    Returns forecasted price array or None on failure/insufficient data.
    """
    if not _lstm_enabled():
        return None

    try:
        import torch
        import torch.nn as nn
    except ImportError:
        return None

    if len(series) < _LSTM_LOOKBACK + 20:
        return None

    values = series.values.astype(np.float64)
    # Normalise to [0, 1]
    v_min, v_max = values.min(), values.max()
    if v_max == v_min:
        return None
    normed = (values - v_min) / (v_max - v_min)

    # Build windowed sequences
    X_list, y_list = [], []
    for i in range(_LSTM_LOOKBACK, len(normed)):
        X_list.append(normed[i - _LSTM_LOOKBACK : i])
        y_list.append(normed[i])

    X_arr = np.array(X_list, dtype=np.float32).reshape(-1, _LSTM_LOOKBACK, 1)
    y_arr = np.array(y_list, dtype=np.float32)

    X_t = torch.from_numpy(X_arr)
    y_t = torch.from_numpy(y_arr)

    class _SmallLSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=1, hidden_size=_LSTM_HIDDEN, num_layers=2, batch_first=True
            )
            self.fc = nn.Linear(_LSTM_HIDDEN, 1)

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :]).squeeze(-1)

    model = _SmallLSTM()
    optimiser = torch.optim.Adam(model.parameters(), lr=_LSTM_LR)
    loss_fn = nn.MSELoss()

    model.train()
    for _ in range(_LSTM_EPOCHS):
        pred = model(X_t)
        loss = loss_fn(pred, y_t)
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()

    # Iterative multi-step forecast
    model.eval()
    window = list(normed[-_LSTM_LOOKBACK:])
    forecasts = []
    with torch.no_grad():
        for _ in range(horizon):
            inp = torch.tensor(window[-_LSTM_LOOKBACK:], dtype=torch.float32).reshape(
                1, _LSTM_LOOKBACK, 1
            )
            p = float(model(inp))
            p = max(0.0, min(1.0, p))
            forecasts.append(p)
            window.append(p)

    # Denormalise
    result = np.array(forecasts) * (v_max - v_min) + v_min
    return result.astype(float)


# ---------------------------------------------------------------------------
# Short-term ensemble forecast (3d / 7d)
# ---------------------------------------------------------------------------


def ensemble_forecast(
    node_id: str,
    horizon_days: int,
    diagnosis: Optional[Dict[str, Any]] = None,
    cutoff_date: str | None = None,
) -> Dict[str, Any]:
    """Weighted ARIMA + XGBoost ensemble for *horizon_days* ahead.

    Returns:
        {
          "base_pct": float,          # expected % change
          "vol_pct":  float,          # 1-sigma volatility for the horizon
          "arima_ok": bool,
          "xgb_ok":   bool,
          "latest":   float,
        }
    """
    series = _series_for_product(node_id, cutoff_date=cutoff_date)
    if series.empty:
        return {
            "base_pct": 0.0,
            "vol_pct": 2.0,
            "arima_ok": False,
            "xgb_ok": False,
            "latest": 0.0,
        }

    latest = float(series.iloc[-1])
    vol_1d = _rolling_coverage_std(series, window=60)  # % per day

    # ── ARIMA component
    arima_fc = _fit_arima_forecast(series, horizon_days, order=_arima_order_for(node_id))
    if arima_fc is not None and len(arima_fc) > 0:
        arima_pct = (arima_fc[-1] - latest) / latest * 100
        arima_ok = True
    else:
        arima_pct = 0.0
        arima_ok = False

    # ── XGBoost component
    df_feat = build_feature_matrix(node_id, cutoff_date=cutoff_date)
    xgb_fc = _fit_xgboost_forecast(
        df_feat,
        horizon_days,
        node_id=node_id,
        cutoff_date=cutoff_date,
    )
    if xgb_fc is not None and len(xgb_fc) > 0:
        xgb_pct = (xgb_fc[-1] - latest) / latest * 100
        xgb_ok = True
    else:
        xgb_pct = 0.0
        xgb_ok = False

    deepseek_regressor_state = (
        _get_live_kimi_regressor_state(node_id)
        if cutoff_date is None
        else {"status": "historical", "values": _neutral_kimi_regressor_values()}
    )

    # ── Mean-reversion component
    mean_30 = float(series.tail(30).mean()) if len(series) >= 30 else latest
    mr_pct = (mean_30 - latest) / latest * 100 * 0.3

    # ── LSTM component
    lstm_fc = _fit_lstm_forecast(series, horizon_days)
    if lstm_fc is not None and len(lstm_fc) > 0:
        lstm_pct = (lstm_fc[-1] - latest) / latest * 100
        lstm_ok = True
    else:
        lstm_pct = 0.0
        lstm_ok = False

    # ── Dynamic weights (4-model ensemble) — §8.1 weight adjustment rules
    w_arima, w_xgb, w_lstm, w_mr = 0.25, 0.30, 0.25, 0.20
    if diagnosis:
        # Rule 1: naphtha_cost_push → xgb +0.10
        pd_hyp = (diagnosis.get("primary_driver") or {}).get("hypothesis", "")
        if pd_hyp == "naphtha_cost_push":
            w_xgb += 0.10
            w_arima -= 0.05
            w_mr -= 0.05

        # Rule 2: post_holiday_restart → lstm +0.10 (regime transition)
        macro_phase = diagnosis.get("market_phase", "")
        if not macro_phase:
            # fallback: check current month
            _cur_month = datetime.date.today().month
            if _cur_month in (2, 3):
                macro_phase = "post_holiday_restart"
        if macro_phase == "post_holiday_restart":
            w_lstm += 0.10
            w_arima -= 0.10

        # Rule 3: downstream supportive signals → xgb +0.05
        downstream = diagnosis.get("downstream_propagated_signals", {})
        supportive_count = sum(
            1
            for v in downstream.values()
            if "supportive" in str(v).lower() or "bullish" in str(v).lower()
        )
        if supportive_count >= 1:
            w_xgb += 0.05
            w_mr -= 0.05

    # Rule 4: few data points → xgb +0.15 (tree models handle small data better)
    if len(series) < 180:
        w_xgb += 0.15
        w_arima -= 0.15

    if xgb_ok and deepseek_regressor_state.get("status") == "success":
        kimi_confidence = float(
            (deepseek_regressor_state.get("values") or {}).get("kimi_confidence") or 0.0
        )
        xgb_boost = min(0.08, 0.03 + kimi_confidence * 0.05)
        w_xgb += xgb_boost
        w_mr = max(0.05, w_mr - xgb_boost)

    if not arima_ok:
        w_mr += w_arima
        w_arima = 0.0
    if not xgb_ok:
        w_mr += w_xgb
        w_xgb = 0.0
    if not lstm_ok:
        w_mr += w_lstm
        w_lstm = 0.0

    total_w = w_arima + w_xgb + w_lstm + w_mr
    if total_w == 0:
        total_w = 1.0
    base_pct = (
        arima_pct * w_arima + xgb_pct * w_xgb + lstm_pct * w_lstm + mr_pct * w_mr
    ) / total_w

    # Scale horizon volatility (daily vol × sqrt(days) approximation)
    vol_horizon = vol_1d * (horizon_days**0.5)

    return {
        "base_pct": round(base_pct, 4),
        "vol_pct": round(vol_horizon, 4),
        "arima_ok": arima_ok,
        "xgb_ok": xgb_ok,
        "lstm_ok": lstm_ok,
        "kimi_exogenous_active": deepseek_regressor_state.get("status") == "success" and xgb_ok,
        "latest": round(latest, 2),
    }


# ---------------------------------------------------------------------------
# Monthly 4-layer STL forecaster
# ---------------------------------------------------------------------------


def monthly_stl_forecast(
    node_id: str,
    diagnosis: Optional[Dict[str, Any]] = None,
    reference_month: Optional[int] = None,
    cutoff_date: str | None = None,
) -> Dict[str, Any]:
    """4-layer monthly forecast: STL + causal chain + seasonal rules + weekly nudge.

    Returns:
        {
          "base_pct":        float,    # total expected % change for the month
          "vol_pct":         float,    # 1-sigma spread
          "stl_pct":         float,    # contribution from STL trend
          "causal_pct":      float,    # contribution from naphtha causal chain
          "seasonal_pct":    float,    # overlay from seasonal rules
          "nudge_pct":       float,    # weekly report nudge (capped ±0.8%)
          "layers_used":     list[str],
          "latest":          float,
        }
    """
    series = _series_for_product(node_id, cutoff_date=cutoff_date)
    if series.empty:
        return {
            "base_pct": 0.0,
            "vol_pct": 3.0,
            "stl_pct": 0.0,
            "causal_pct": 0.0,
            "seasonal_pct": 0.0,
            "nudge_pct": 0.0,
            "layers_used": [],
            "latest": 0.0,
        }

    latest = float(series.iloc[-1])
    vol_1d = _rolling_coverage_std(series, window=90)
    vol_30d = vol_1d * (30**0.5)
    layers = []

    # ── Layer 1: STL trend component (40%)
    stl_pct = 0.0
    if len(series) >= 2 * 7:  # need at least 2 periods
        try:
            from statsmodels.tsa.seasonal import STL

            # Weekly seasonality on daily series
            stl_res = STL(series, period=7, robust=True).fit()
            trend = stl_res.trend

            # Estimate monthly trend continuation: slope over last 30 trend values
            recent_trend = trend[-30:] if len(trend) >= 30 else trend
            if len(recent_trend) >= 2:
                slope = float(np.polyfit(range(len(recent_trend)), recent_trend, 1)[0])
                # Monthly projection: slope per day × 30 days, as % of latest
                stl_pct = slope * 30 / latest * 100
                stl_pct = float(np.clip(stl_pct, -15.0, 15.0))
                layers.append("stl_trend")
        except Exception as e:
            print(f"[forecasting_engine] STL error for {node_id}: {e}")

    # ── Layer 2: Causal chain from naphtha via ARIMA(2,1,1) (35%) — §8.2
    nap_series = get_naphtha_series(days=500, cutoff_date=cutoff_date)
    causal_pct = 0.0
    naphtha_mom_pct = 0.0
    override_applied = False
    override_notes = None
    if not nap_series.empty and len(nap_series) >= 60:
        # Resample to monthly averages for ARIMA fit
        nap_monthly = nap_series.resample("MS").mean().dropna()
        if len(nap_monthly) >= 12:
            try:
                from statsmodels.tsa.arima.model import ARIMA as _ARIMA_Model

                _nap_arima = _ARIMA_Model(nap_monthly, order=(2, 1, 1)).fit(
                    method_kwargs={"maxiter": 200}
                )
                nap_fc = _nap_arima.forecast(steps=1)
                nap_last = float(nap_monthly.iloc[-1])
                naphtha_mom_pct = (float(nap_fc.iloc[0]) - nap_last) / nap_last * 100
            except Exception:
                # Fallback to observed MoM if ARIMA fails
                nap_last = float(nap_series.tail(30).iloc[-1])
                nap_first = float(nap_series.tail(30).iloc[0])
                naphtha_mom_pct = (nap_last - nap_first) / nap_first * 100
        else:
            # Not enough monthly data, use observed MoM
            nap_last = float(nap_series.tail(30).iloc[-1])
            nap_first = float(nap_series.tail(30).iloc[0])
            naphtha_mom_pct = (nap_last - nap_first) / nap_first * 100

        # Hard cap ±8% MoM on naphtha (§8.2)
        naphtha_mom_pct = float(np.clip(naphtha_mom_pct, -8.0, 8.0))

        # Macro override integration (§8.2): adjust naphtha MoM before propagation
        try:
            from backend.src.service.macro_override_service import (
                get_active_override,
                apply_macro_shock_to_forecast,
            )

            _override = get_active_override()
            if _override:
                shock_pct = float(_override.get("naphtha_shock_pct", 0))
                shock_pct = float(np.clip(shock_pct, -4.0, 4.0))
                naphtha_mom_pct += shock_pct
                naphtha_mom_pct = float(np.clip(naphtha_mom_pct, -8.0, 8.0))
                override_applied = True
                override_notes = (
                    f"Macro override: naphtha shock {shock_pct:+.1f}%, "
                    f"phase={_override.get('market_phase', 'unknown')}"
                )
        except ImportError:
            pass
        except Exception:
            pass

        # Propagate through tier-dampened elasticities
        elasticity = _ELASTICITIES.get(node_id, 0.60)
        causal_pct = naphtha_mom_pct * elasticity
        layers.append("causal_chain")

    # ── Layer 3: Seasonal rules (additive overlay)
    month = reference_month or datetime.date.today().month
    seasonal_pct = _SEASONAL_RULES.get((node_id, month), 0.0)

    # Post-holiday override: +1.2% for key products in Feb–Mar (§8.2)
    if month in (2, 3):
        _post_holiday_products = {
            "piperylene",
            "c5_resin",
            "cracked_c5",
            "isoprene",
            "raffinate_c5",
        }
        if node_id in _post_holiday_products:
            # Check if market_phase is post_holiday_restart
            _m_phase = ""
            if diagnosis:
                _m_phase = diagnosis.get("market_phase", "")
            if not _m_phase:
                try:
                    from backend.src.service.macro_override_service import (
                        get_active_override,
                    )

                    _ov = get_active_override()
                    if _ov:
                        _m_phase = _ov.get("market_phase", "")
                except Exception:
                    pass
            if _m_phase == "post_holiday_restart" or month in (2, 3):
                seasonal_pct += 1.2

    if seasonal_pct != 0.0:
        layers.append("seasonal_rules")

    # ── Layer 4: Weekly nudge from diagnosis net_signal (±0.8% cap) — §8.2
    nudge_pct = 0.0
    if diagnosis:
        net_signal = diagnosis.get("net_signal", "")
        if net_signal == "bullish":
            nudge_pct = 0.8
        elif net_signal == "bearish":
            nudge_pct = -0.8
        elif net_signal == "neutral":
            nudge_pct = 0.0
        if nudge_pct != 0.0:
            layers.append("weekly_nudge")

    # ── Weighted combination (§8.2 blending formula)
    w_stl, w_causal = 0.40, 0.35

    base_pct = (
        (stl_pct * w_stl + causal_pct * w_causal) / 0.75
        + seasonal_pct  # additive overlay
        + nudge_pct  # nudge at face value
    )

    # Hard cap ±15%
    base_pct = float(np.clip(base_pct, -15.0, 15.0))

    # ── Week-by-week 4-week interpolated breakdown (§8.2)
    blended_price = latest * (1 + base_pct / 100)
    weekly_breakdown = []
    for wk in range(1, 5):
        frac = wk / 4.0
        wk_price = latest + (blended_price - latest) * frac
        weekly_breakdown.append(
            {
                "week": wk,
                "price": round(wk_price, 2),
                "pct_from_current": round((wk_price - latest) / latest * 100, 2),
            }
        )

    return {
        "base_pct": round(base_pct, 4),
        "vol_pct": round(vol_30d, 4),
        "stl_pct": round(stl_pct, 4),
        "causal_pct": round(causal_pct, 4),
        "naphtha_mom_pct": round(naphtha_mom_pct, 4),
        "seasonal_pct": round(seasonal_pct, 4),
        "nudge_pct": round(nudge_pct, 4),
        "layers_used": layers,
        "latest": round(latest, 2),
        "blended_price": round(blended_price, 2),
        "weekly_breakdown": weekly_breakdown,
        "override_applied": override_applied,
        "override_notes": override_notes,
    }
