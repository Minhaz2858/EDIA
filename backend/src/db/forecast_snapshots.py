"""Canonical forecast decision snapshot storage.

This module stores the approved forecast path plus the reason/evidence that led
to it.  It is separate from daily_calibration.py: calibration tracks accuracy
after the fact, while these tables record what EDIA decided at forecast time.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text


_SNAPSHOT_TABLE = "forecast_decision_snapshots"
_POINT_TABLE = "forecast_decision_points"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_load(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _utc_now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _mysql_schema() -> list[str]:
    return [
        f"""
CREATE TABLE IF NOT EXISTS {_SNAPSHOT_TABLE} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    snapshot_id VARCHAR(64) NOT NULL UNIQUE,
    product_id VARCHAR(60) NOT NULL,
    generated_at DATETIME NOT NULL,
    expires_at DATETIME,
    status VARCHAR(32) NOT NULL DEFAULT 'approved',
    validation_status VARCHAR(64) NOT NULL,
    run_reason VARCHAR(64) NOT NULL DEFAULT 'manual',
    model_version VARCHAR(64) DEFAULT 'v1',
    policy_version VARCHAR(64) DEFAULT 'baseline',
    cadence_class VARCHAR(32),
    native_step_days INT,
    daily_forecast_advised TINYINT DEFAULT 0,
    cause_summary TEXT,
    deepseek_reasoning TEXT,
    evidence_event_ids TEXT,
    baseline_json LONGTEXT,
    adjustment_json LONGTEXT,
    payload_json LONGTEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_forecast_snapshot_product_time (product_id, generated_at),
    INDEX idx_forecast_snapshot_status (status, validation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Canonical EDIA forecast decision snapshots';
""",
        f"""
CREATE TABLE IF NOT EXISTS {_POINT_TABLE} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    snapshot_id VARCHAR(64) NOT NULL,
    product_id VARCHAR(60) NOT NULL,
    forecast_date DATE NOT NULL,
    step_number INT NOT NULL,
    baseline_price FLOAT,
    proposed_override_pct FLOAT DEFAULT 0,
    applied_override_pct FLOAT DEFAULT 0,
    final_price FLOAT,
    bull_price FLOAT,
    bear_price FLOAT,
    confidence FLOAT,
    cause_summary TEXT,
    evidence_event_ids TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_forecast_point (snapshot_id, step_number),
    INDEX idx_forecast_point_product_date (product_id, forecast_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Forecast path points for canonical EDIA forecast snapshots';
""",
    ]


def _sqlite_schema() -> list[str]:
    return [
        f"""
CREATE TABLE IF NOT EXISTS {_SNAPSHOT_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'approved',
    validation_status TEXT NOT NULL,
    run_reason TEXT NOT NULL DEFAULT 'manual',
    model_version TEXT DEFAULT 'v1',
    policy_version TEXT DEFAULT 'baseline',
    cadence_class TEXT,
    native_step_days INTEGER,
    daily_forecast_advised INTEGER DEFAULT 0,
    cause_summary TEXT,
    deepseek_reasoning TEXT,
    evidence_event_ids TEXT,
    baseline_json TEXT,
    adjustment_json TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""",
        f"CREATE INDEX IF NOT EXISTS idx_forecast_snapshot_product_time ON {_SNAPSHOT_TABLE} (product_id, generated_at);",
        f"CREATE INDEX IF NOT EXISTS idx_forecast_snapshot_status ON {_SNAPSHOT_TABLE} (status, validation_status);",
        f"""
CREATE TABLE IF NOT EXISTS {_POINT_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    baseline_price REAL,
    proposed_override_pct REAL DEFAULT 0,
    applied_override_pct REAL DEFAULT 0,
    final_price REAL,
    bull_price REAL,
    bear_price REAL,
    confidence REAL,
    cause_summary TEXT,
    evidence_event_ids TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (snapshot_id, step_number)
);
""",
        f"CREATE INDEX IF NOT EXISTS idx_forecast_point_product_date ON {_POINT_TABLE} (product_id, forecast_date);",
    ]


def init_tables(engine) -> None:
    """Create forecast snapshot tables if they do not exist."""
    schema = _sqlite_schema() if engine.dialect.name == "sqlite" else _mysql_schema()
    with engine.begin() as conn:
        for sql in schema:
            conn.execute(text(sql.strip()))


def _point_rows_from_payload(
    snapshot_id: str,
    product_id: str,
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    raw_points = payload.get("forecast_path") or payload.get("forecast_native") or payload.get("forecast") or []
    rows: list[dict[str, Any]] = []
    if not isinstance(raw_points, list):
        return rows

    for idx, point in enumerate(raw_points, start=1):
        if not isinstance(point, dict):
            continue
        forecast_date = point.get("forecast_date") or point.get("date")
        if not forecast_date:
            continue
        baseline_price = point.get("baseline_price", point.get("base"))
        final_price = point.get("final_price", point.get("base", baseline_price))
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "product_id": product_id,
                "forecast_date": str(forecast_date)[:10],
                "step_number": int(point.get("step_number") or idx),
                "baseline_price": baseline_price,
                "proposed_override_pct": point.get("proposed_override_pct", 0.0),
                "applied_override_pct": point.get("applied_override_pct", 0.0),
                "final_price": final_price,
                "bull_price": point.get("bull_price", point.get("bull", final_price)),
                "bear_price": point.get("bear_price", point.get("bear", final_price)),
                "confidence": point.get("confidence"),
                "cause_summary": point.get("cause_summary") or payload.get("cause_summary") or "",
                "evidence_event_ids": _json_dump(point.get("evidence_event_ids") or payload.get("evidence_event_ids") or []),
            }
        )
    return rows


def persist_snapshot(
    engine,
    payload: Dict[str, Any],
    *,
    run_reason: str = "manual",
    expires_in_seconds: int = 21600,
) -> str:
    """Persist an approved forecast payload and its path points.

    Returns the generated snapshot_id.
    """
    if not isinstance(payload, dict):
        raise TypeError("forecast snapshot payload must be a dict")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise ValueError("forecast snapshot payload missing product_id")

    init_tables(engine)
    generated_at = _utc_now()
    expires_at = generated_at + timedelta(seconds=max(60, int(expires_in_seconds)))
    snapshot_id = str(payload.get("snapshot_id") or uuid.uuid4().hex)

    cadence_info = payload.get("cadence_info") if isinstance(payload.get("cadence_info"), dict) else {}
    snapshot_meta = payload.get("forecast_snapshot") if isinstance(payload.get("forecast_snapshot"), dict) else {}
    baseline_json = payload.get("baseline_forecast") or payload.get("forecast_path") or payload.get("forecast_native") or []
    adjustment_json = payload.get("forecast_adjustment") or {
        "proposed_override_pct": 0.0,
        "applied_override_pct": 0.0,
        "source": "baseline_only",
    }
    evidence_event_ids = payload.get("evidence_event_ids") or adjustment_json.get("evidence_event_ids") or []

    payload_to_store = copy.deepcopy(payload)
    payload_to_store.setdefault("snapshot_id", snapshot_id)
    payload_to_store["forecast_snapshot"] = {
        **snapshot_meta,
        "snapshot_id": snapshot_id,
        "status": "approved",
        "validation_status": snapshot_meta.get("validation_status", "approved_baseline_only"),
        "generated_at": generated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "run_reason": run_reason,
    }

    header = {
        "snapshot_id": snapshot_id,
        "product_id": product_id,
        "generated_at": generated_at.isoformat(sep=" "),
        "expires_at": expires_at.isoformat(sep=" "),
        "status": "approved",
        "validation_status": payload_to_store["forecast_snapshot"]["validation_status"],
        "run_reason": run_reason,
        "model_version": str(payload.get("model_version") or "v1"),
        "policy_version": str(payload.get("policy", {}).get("policy_id") if isinstance(payload.get("policy"), dict) else "baseline"),
        "cadence_class": cadence_info.get("cadence_class"),
        "native_step_days": cadence_info.get("native_step_days"),
        "daily_forecast_advised": 1 if cadence_info.get("daily_forecast_advised") else 0,
        "cause_summary": payload.get("cause_summary") or "Local model baseline; no event override applied.",
        "deepseek_reasoning": payload.get("kimi_reasoning") or payload.get("deepseek_reasoning") or "",
        "evidence_event_ids": _json_dump(evidence_event_ids),
        "baseline_json": _json_dump(baseline_json),
        "adjustment_json": _json_dump(adjustment_json),
        "payload_json": _json_dump(payload_to_store),
    }
    point_rows = _point_rows_from_payload(snapshot_id, product_id, payload_to_store)

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {_SNAPSHOT_TABLE}
                (snapshot_id, product_id, generated_at, expires_at, status,
                 validation_status, run_reason, model_version, policy_version,
                 cadence_class, native_step_days, daily_forecast_advised,
                 cause_summary, deepseek_reasoning, evidence_event_ids,
                 baseline_json, adjustment_json, payload_json)
                VALUES
                (:snapshot_id, :product_id, :generated_at, :expires_at, :status,
                 :validation_status, :run_reason, :model_version, :policy_version,
                 :cadence_class, :native_step_days, :daily_forecast_advised,
                 :cause_summary, :deepseek_reasoning, :evidence_event_ids,
                 :baseline_json, :adjustment_json, :payload_json)
                """
            ),
            header,
        )
        for row in point_rows:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {_POINT_TABLE}
                    (snapshot_id, product_id, forecast_date, step_number,
                     baseline_price, proposed_override_pct, applied_override_pct,
                     final_price, bull_price, bear_price, confidence,
                     cause_summary, evidence_event_ids)
                    VALUES
                    (:snapshot_id, :product_id, :forecast_date, :step_number,
                     :baseline_price, :proposed_override_pct, :applied_override_pct,
                     :final_price, :bull_price, :bear_price, :confidence,
                     :cause_summary, :evidence_event_ids)
                    """
                ),
                row,
            )

    return snapshot_id


def get_latest_approved_snapshot(
    engine,
    product_id: str,
    *,
    max_age_seconds: int = 21600,
    min_path_points: int = 0,
    exclude_validation_statuses: Optional[list[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the latest approved, non-expired forecast payload for a product."""
    init_tables(engine)
    now = _utc_now()
    min_generated_at = now - timedelta(seconds=max(60, int(max_age_seconds)))
    excluded_statuses = {
        str(status).strip()
        for status in (exclude_validation_statuses or [])
        if str(status).strip()
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT snapshot_id, payload_json, generated_at, expires_at, validation_status
                FROM {_SNAPSHOT_TABLE}
                WHERE product_id = :product_id
                  AND status = 'approved'
                  AND generated_at >= :min_generated_at
                  AND (expires_at IS NULL OR expires_at >= :now_value)
                ORDER BY generated_at DESC, id DESC
                """
            ),
            {
                "product_id": product_id,
                "min_generated_at": min_generated_at.isoformat(sep=" "),
                "now_value": now.isoformat(sep=" "),
            },
        ).fetchall()
    for row in rows:
        validation_status = str(row[4] or "")
        if validation_status in excluded_statuses:
            continue
        payload = _json_load(row[1], {})
        if not isinstance(payload, dict):
            continue
        if min_path_points > 0:
            forecast_path = payload.get("forecast_path")
            if not isinstance(forecast_path, list) or len(forecast_path) < min_path_points:
                continue
        payload.setdefault("snapshot_id", row[0])
        snapshot_meta = payload.get("forecast_snapshot") if isinstance(payload.get("forecast_snapshot"), dict) else {}
        payload["forecast_snapshot"] = {
            **snapshot_meta,
            "snapshot_id": row[0],
            "status": "approved",
            "validation_status": validation_status,
            "generated_at": str(row[2]),
            "expires_at": str(row[3]) if row[3] is not None else None,
            "source": "stored_snapshot",
        }
        return payload
    return None