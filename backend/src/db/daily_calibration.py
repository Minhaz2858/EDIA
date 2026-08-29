"""Daily forecast calibration pipeline.

Runs every day (via cron or manual trigger) to:
1. Compare T-7 forecasts vs today's actual prices
2. Compute MAPE per product
3. Update evidence_outcomes accuracy scores
4. Flag products with MAPE > 10%

Usage:
    python -m backend.src.db.daily_calibration
    # or via cron: 0 8 * * * cd /path/to/ecisco && .venv/bin/python -m backend.src.db.daily_calibration
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_calibration")


# SQL to create required tables if they don't already exist
_SCHEMA_SQL = [
    """
CREATE TABLE IF NOT EXISTS forecast_accuracy_log (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    forecast_date   DATE NOT NULL COMMENT 'Date the forecast was made',
    target_date     DATE NOT NULL COMMENT 'Date the forecast was for',
    product_id      VARCHAR(60) NOT NULL,
    predicted_price FLOAT COMMENT 'Predicted price (¥/ton)',
    actual_price    FLOAT COMMENT 'Actual observed price on target_date',
    error_pct       FLOAT COMMENT 'Absolute % error: |predicted - actual| / actual * 100',
    model_version   VARCHAR(20) DEFAULT 'v1',
    evidence_types  TEXT COMMENT 'JSON list of evidence tiers used (T1-T5)',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_prod_date (product_id, forecast_date),
    INDEX idx_target   (target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Tracks forecast accuracy over time';
""",
    """
CREATE TABLE IF NOT EXISTS evidence_outcomes (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    evidence_tier VARCHAR(10) NOT NULL COMMENT 'T1-ERP / T2-MKT / T3-COST / T4-SEASONAL / T5-RAG',
    product_id    VARCHAR(60) NOT NULL DEFAULT '__all__',
    correct       INT NOT NULL DEFAULT 0,
    total         INT NOT NULL DEFAULT 0,
    accuracy_rate FLOAT COMMENT 'correct / total * 100',
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_tier_product (evidence_tier, product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Self-learning evidence tier scoring';
""",
]


def init_tables(engine) -> None:
    """Create tables if they don't exist."""
    with engine.connect() as conn:
        for sql in _SCHEMA_SQL:
            conn.execute(text(sql.strip()))
        conn.commit()
    logger.info("Tables forecast_accuracy_log + evidence_outcomes ready.")


def log_forecast(
    engine,
    forecast_date: date,
    target_date: date,
    product_id: str,
    predicted_price: float,
    evidence_types: Optional[List[str]] = None,
    model_version: str = "v1",
) -> int:
    """Insert a forecast record (before we know the actual price).

    Returns the row id.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "INSERT INTO forecast_accuracy_log "
                "(forecast_date, target_date, product_id, predicted_price, "
                " evidence_types, model_version) "
                "VALUES (:fd, :td, :pid, :pp, :ev, :mv)"
            ),
            {
                "fd": forecast_date,
                "td": target_date,
                "pid": product_id,
                "pp": predicted_price,
                "ev": json.dumps(evidence_types or []),
                "mv": model_version,
            },
        )
        conn.commit()
        return result.lastrowid


def persist_forecast_snapshot(
    engine,
    product_id: str,
    forecast_points: List[Dict[str, Any]],
    model_version: str = "v1",
) -> int:
    """Batch-persist today's forecast points, skipping duplicates.

    Each forecast_point must have keys: date, base, bull, bear.
    We store the *base* scenario as the primary predicted_price.
    Returns the count of newly inserted rows.
    """
    today = date.today()
    inserted = 0
    with engine.connect() as conn:
        for pt in forecast_points:
            target = pt["date"]  # 'YYYY-MM-DD'
            base_price = pt.get("base")
            if base_price is None:
                continue
            # Dedup: skip if we already logged a forecast for this combo today
            existing = conn.execute(
                text(
                    "SELECT id FROM forecast_accuracy_log "
                    "WHERE forecast_date=:fd AND target_date=:td AND product_id=:pid "
                    "LIMIT 1"
                ),
                {"fd": str(today), "td": target, "pid": product_id},
            ).fetchone()
            if existing:
                continue
            # Store base as predicted_price; store all 3 scenarios in evidence_types JSON
            scenario_detail = json.dumps({
                "base": pt.get("base"),
                "bull": pt.get("bull"),
                "bear": pt.get("bear"),
            })
            conn.execute(
                text(
                    "INSERT INTO forecast_accuracy_log "
                    "(forecast_date, target_date, product_id, predicted_price, "
                    " evidence_types, model_version) "
                    "VALUES (:fd, :td, :pid, :pp, :ev, :mv)"
                ),
                {
                    "fd": str(today),
                    "td": target,
                    "pid": product_id,
                    "pp": round(base_price, 2),
                    "ev": scenario_detail,
                    "mv": model_version,
                },
            )
            inserted += 1
        conn.commit()
    if inserted:
        logger.info(f"Persisted {inserted} forecast points for {product_id} (forecast_date={today})")
    return inserted


def get_forecast_accuracy_history(
    engine,
    product_id: str,
    days_back: int = 30,
    end_date: Optional[date | str] = None,
) -> List[Dict[str, Any]]:
    """Return evaluated forecast records for a product over last N days.

    Only returns rows where actual_price has been filled in (evaluated).
    """
    if end_date is None:
        effective_end_date = date.today()
    elif isinstance(end_date, str):
        effective_end_date = date.fromisoformat(end_date[:10])
    else:
        effective_end_date = end_date

    cutoff = str(effective_end_date - timedelta(days=days_back))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT forecast_date, target_date, predicted_price, actual_price, "
                "       error_pct, evidence_types "
                "FROM forecast_accuracy_log "
                "WHERE product_id=:pid AND forecast_date >= :cutoff AND forecast_date <= :end_date "
                "ORDER BY target_date DESC"
            ),
            {"pid": product_id, "cutoff": cutoff, "end_date": str(effective_end_date)},
        ).fetchall()
    results: List[Dict[str, Any]] = []
    for r in rows:
        rec: Dict[str, Any] = {
            "forecast_date": str(r[0]),
            "target_date": str(r[1]),
            "predicted_price": float(r[2]) if r[2] is not None else None,
            "actual_price": float(r[3]) if r[3] is not None else None,
            "error_pct": float(r[4]) if r[4] is not None else None,
            "status": "evaluated" if r[3] is not None else "pending",
        }
        # Parse scenario detail from evidence_types
        if r[5]:
            try:
                scenarios = json.loads(r[5])
                if isinstance(scenarios, dict) and "base" in scenarios:
                    rec["scenarios"] = scenarios
            except Exception:
                pass
        results.append(rec)
    return results


def _get_actual_price(engine, product_id: str, target_date: date) -> Optional[float]:
    """Fetch the market price for product_id on or near target_date."""
    from backend.src.service.market_data_service import REAL_DATA_MAP
    mp_product, mp_price_type = REAL_DATA_MAP.get(product_id, (product_id, None))
    if not mp_price_type:
        return None

    date_str = str(target_date)
    with engine.connect() as conn:
        # Try exact date first, then ±3 days
        row = conn.execute(
            text(
                "SELECT price FROM market_prices "
                "WHERE product_id=:pid AND price_type=:pt "
                "  AND date BETWEEN DATE_SUB(:d, INTERVAL 3 DAY) AND DATE_ADD(:d, INTERVAL 3 DAY) "
                "  AND price IS NOT NULL "
                "ORDER BY ABS(DATEDIFF(date, :d)) ASC LIMIT 1"
            ),
            {"pid": mp_product, "pt": mp_price_type, "d": date_str},
        ).fetchone()
    return float(row[0]) if row else None


def evaluate_pending_forecasts(engine, target_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """Evaluate all unscored forecasts whose target_date <= today.

    Returns list of evaluated records with error_pct.
    """
    if target_date is None:
        target_date = date.today()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, product_id, target_date, predicted_price "
                "FROM forecast_accuracy_log "
                "WHERE target_date <= :td AND actual_price IS NULL "
                "ORDER BY target_date ASC"
            ),
            {"td": str(target_date)},
        ).fetchall()

    evaluated: List[Dict[str, Any]] = []
    with engine.connect() as conn:
        for row in rows:
            row_id, product_id, td, predicted = row
            actual = _get_actual_price(engine, product_id, td)
            if actual is None or actual == 0:
                continue
            predicted = float(predicted)
            error_pct = abs(predicted - actual) / actual * 100.0

            conn.execute(
                text(
                    "UPDATE forecast_accuracy_log "
                    "SET actual_price=:ap, error_pct=:ep "
                    "WHERE id=:rid"
                ),
                {"ap": actual, "ep": round(error_pct, 2), "rid": row_id},
            )
            evaluated.append({
                "id": row_id,
                "product_id": product_id,
                "target_date": str(td),
                "predicted_price": predicted,
                "actual_price": actual,
                "error_pct": round(error_pct, 2),
            })
        conn.commit()

    logger.info(f"Evaluated {len(evaluated)} pending forecasts.")
    return evaluated


def compute_product_mape(engine, days_back: int = 30) -> Dict[str, float]:
    """Compute per-product MAPE over the last N days of evaluated forecasts."""
    cutoff = str(date.today() - timedelta(days=days_back))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT product_id, AVG(error_pct) as mape, COUNT(*) as n "
                "FROM forecast_accuracy_log "
                "WHERE actual_price IS NOT NULL "
                "  AND forecast_date >= :cutoff "
                "GROUP BY product_id"
            ),
            {"cutoff": cutoff},
        ).fetchall()
    return {r[0]: round(float(r[1]), 2) for r in rows if r[1] is not None}


def update_evidence_outcome_from_log(engine, product_id: str, error_pct: float, evidence_types: List[str]):
    """If a forecast error < 5% → count as 'correct' for each evidence tier used."""
    is_correct = 1 if error_pct < 5.0 else 0
    with engine.connect() as conn:
        for tier in evidence_types:
            conn.execute(
                text(
                    "INSERT INTO evidence_outcomes (evidence_tier, product_id, correct, total, accuracy_rate) "
                    "VALUES (:tier, :pid, :correct, 1, :acc) "
                    "ON DUPLICATE KEY UPDATE "
                    "  correct = correct + :correct, "
                    "  total = total + 1, "
                    "  accuracy_rate = (correct + :correct) / (total + 1) * 100"
                ),
                {
                    "tier": tier,
                    "pid": product_id,
                    "correct": is_correct,
                    "acc": is_correct * 100.0,
                },
            )
        conn.commit()


def run_daily_calibration() -> Dict[str, Any]:
    """Main entry point. Evaluates pending forecasts and reports status."""
    from backend.src.db.sqlite_db import db as _app_db
    engine = _app_db.engine

    init_tables(engine)

    evaluated = evaluate_pending_forecasts(engine)
    mape_by_product = compute_product_mape(engine, days_back=30)

    # Flag products with high MAPE
    flagged = {pid: mape for pid, mape in mape_by_product.items() if mape > 10.0}
    if flagged:
        logger.warning(f"HIGH MAPE products (>10%%): {flagged}")

    # Update evidence outcomes from newly evaluated records
    with engine.connect() as conn:
        for rec in evaluated:
            # Load evidence_types from DB
            ev_row = conn.execute(
                text("SELECT evidence_types FROM forecast_accuracy_log WHERE id=:rid"),
                {"rid": rec["id"]},
            ).fetchone()
            if ev_row and ev_row[0]:
                try:
                    tiers = json.loads(ev_row[0])
                except Exception:
                    tiers = []
                update_evidence_outcome_from_log(engine, rec["product_id"], rec["error_pct"], tiers)

    summary = {
        "evaluated_count": len(evaluated),
        "mape_by_product": mape_by_product,
        "flagged_high_mape": flagged,
        "status": "ok",
    }
    logger.info(f"Daily calibration complete: {summary}")
    return summary


if __name__ == "__main__":
    result = run_daily_calibration()
    print(json.dumps(result, indent=2, ensure_ascii=False))
