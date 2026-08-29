"""ERP Write-Back Service — stage pricing recommendations for ERP review.

Architecture:
  1. EDIA computes a pricing decision via the chat pipeline.
  2. User clicks "Submit to ERP" → POST /api/erp/prices
  3. Recommendation is written to the SQLite staging table with status=pending.
  4. An ERP admin reviews pending records via PUT /api/erp/prices/{id}/approve
     and the status transitions to approved / rejected.
  5. A separate ERP-side process polls or consumes committed records and
     writes them into the live ERP price tables.

This keeps EDIA read-only by default; human-in-the-loop gate for write-back.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import text

from backend.src.db.engine_factory import get_erp_engine, get_engine
from backend.src.db.sqlite_db import db

logger = logging.getLogger(__name__)

_VALID_STATUSES: set[str] = {"pending", "approved", "rejected", "committed"}
_VALID_PRODUCTS: set[str] = {
    "isoprene", "piperylene", "dcpd", "cracked_c5", "cracked_c9",
    "mixed_c5", "mixed_c9", "raffinate_c5", "sis", "sis_rubber",
    "c5_resin", "blowing_agent", "styrene", "hydrogenated_c9",
    "trimethylbenzene", "c9_resin", "mthpa_hardener",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Stage a new pricing recommendation
# ---------------------------------------------------------------------------


def stage_recommendation(
    product_id: str,
    recommended_price: float,
    valid_from: str,
    valid_until: str | None,
    rationale: str | None,
    submitted_by: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Insert a new pending recommendation into the staging table."""
    if product_id not in _VALID_PRODUCTS:
        raise ValueError(f"Unknown product_id: {product_id}")

    rec_id = str(uuid.uuid4())
    now = _now_iso()

    with db.engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO erp_price_recommendations
                    (id, product_id, recommended_price, valid_from, valid_until,
                     rationale, status, submitted_by, session_id, created_at, updated_at)
                VALUES
                    (:id, :product_id, :recommended_price, :valid_from, :valid_until,
                     :rationale, 'pending', :submitted_by, :session_id, :created_at, :updated_at)
            """),
            {
                "id": rec_id,
                "product_id": product_id,
                "recommended_price": recommended_price,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "rationale": rationale or "",
                "submitted_by": submitted_by,
                "session_id": session_id or "",
                "created_at": now,
                "updated_at": now,
            },
        )

    _write_audit_log(
        rec_id=rec_id,
        product_id=product_id,
        action="submit",
        user_id=submitted_by,
        session_id=session_id,
        extra={"recommended_price": recommended_price, "rationale": rationale},
    )

    return {
        "recommendation_id": rec_id,
        "status": "pending",
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Retrieve recommendations (optionally filtered)
# ---------------------------------------------------------------------------


def list_recommendations(
    product_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recommendations matching the given filters."""
    if status and status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    query = "SELECT * FROM erp_price_recommendations WHERE 1=1"
    params: dict[str, Any] = {"limit": limit}
    if product_id:
        query += " AND product_id = :product_id"
        params["product_id"] = product_id
    if status:
        query += " AND status = :status"
        params["status"] = status
    query += " ORDER BY created_at DESC LIMIT :limit"

    with db.engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_recommendation(rec_id: str) -> dict[str, Any] | None:
    with db.engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM erp_price_recommendations WHERE id = :id"),
            {"id": rec_id},
        ).fetchone()
        return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Admin actions — approve / reject / commit
# ---------------------------------------------------------------------------


def update_status(
    rec_id: str,
    new_status: Literal["approved", "rejected", "committed"],
    actor_user_id: str,
) -> dict[str, Any]:
    """Transition a recommendation to a new status (admin only)."""
    if new_status not in {"approved", "rejected", "committed"}:
        raise ValueError(f"Invalid status transition: {new_status}")

    rec = get_recommendation(rec_id)
    if not rec:
        raise ValueError(f"Recommendation not found: {rec_id}")

    now = _now_iso()
    update_fields: dict[str, Any] = {
        "status": new_status,
        "updated_at": now,
    }
    if new_status == "approved":
        update_fields["approved_by"] = actor_user_id
        update_fields["approved_at"] = now
    elif new_status == "committed":
        update_fields["committed_at"] = now
        # Push to ERP MySQL staging (real write-back — see _commit_to_erp)
        _commit_to_erp(rec)

    with db.engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE erp_price_recommendations
                SET status = :status,
                    approved_by = COALESCE(:approved_by, approved_by),
                    approved_at = COALESCE(:approved_at, approved_at),
                    committed_at = COALESCE(:committed_at, committed_at),
                    updated_at = :updated_at
                WHERE id = :id
            """),
            {
                "id": rec_id,
                "status": new_status,
                "approved_by": update_fields.get("approved_by") or "",
                "approved_at": update_fields.get("approved_at") or "",
                "committed_at": update_fields.get("committed_at") or "",
                "updated_at": now,
            },
        )

    _write_audit_log(
        rec_id=rec_id,
        product_id=rec["product_id"],
        action=f"status_change_to_{new_status}",
        user_id=actor_user_id,
        extra={"from_status": rec["status"], "to_status": new_status},
    )

    return get_recommendation(rec_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# ERP MySQL write-back (called only on commit)
# ---------------------------------------------------------------------------


def _commit_to_erp(rec: dict[str, Any]) -> None:
    """Write a committed recommendation to the ERP MySQL staging view.

    The actual table name is read from the ERP_*
    """
    try:
        engine = get_erp_engine()
        with engine.begin() as conn:
            # Write to a dedicated ERP-facing staging table.
            # ERP admins can then review and promote to live price tables.
            conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS erp_price_rec_staging (
                        rec_id       VARCHAR(36) PRIMARY KEY,
                        product_id   VARCHAR(64) NOT NULL,
                        price        DECIMAL(12, 2) NOT NULL,
                        valid_from   DATE NOT NULL,
                        valid_until  DATE NULL,
                        rationale    TEXT NULL,
                        submitted_by VARCHAR(64) NOT NULL,
                        created_at   VARCHAR(64) NOT NULL,
                        INDEX idx_product (product_id),
                        INDEX idx_valid (valid_from, valid_until)
                    )
                """),
            )
            conn.execute(
                text("""
                    INSERT INTO erp_price_rec_staging
                        (rec_id, product_id, price, valid_from, valid_until,
                         rationale, submitted_by, created_at)
                    VALUES
                        (:rec_id, :product_id, :price, :valid_from, :valid_until,
                         :rationale, :submitted_by, :created_at)
                    ON DUPLICATE KEY UPDATE
                        price = VALUES(price),
                        valid_from = VALUES(valid_from),
                        valid_until = VALUES(valid_until)
                """),
                {
                    "rec_id": rec["id"],
                    "product_id": rec["product_id"],
                    "price": float(rec["recommended_price"]),
                    "valid_from": rec["valid_from"],
                    "valid_until": rec["valid_until"] or "",
                    "rationale": rec["rationale"] or "",
                    "submitted_by": rec["submitted_by"],
                    "created_at": rec["created_at"],
                },
            )
        logger.info(
            "ERP write-back committed for rec_id=%s product=%s price=%s",
            rec["id"], rec["product_id"], rec["recommended_price"],
        )
    except Exception as exc:
        logger.error(
            "ERP write-back failed for rec_id=%s: %s", rec["id"], exc
        )
        raise


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


def _write_audit_log(
    rec_id: str,
    product_id: str,
    action: str,
    user_id: str,
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    try:
        db.write_decision_log(
            id=rec_id,
            product_id=product_id,
            user_id=user_id,
            session_id=session_id,
            ai_price_low=None,
            ai_price_high=None,
            ai_price_mid=None,
            price_unit="元/吨",
            ai_action=f"erp_writeback:{action}",
            ai_confidence=None,
            ai_reasoning=json.dumps(extra or {}),
            market_snapshot_json="",
        )
    except Exception:
        logger.debug("ERP write-back audit log write failed (non-fatal)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    return dict(row._mapping)  # SQLAlchemy 2.0 Row → dict
