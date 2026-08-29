"""Decision Log Service — closed-loop AI recommendation tracking.

Full lifecycle:
  create()        → AI recommendation saved with frozen market snapshot
  mark_executed() → user records actual executed price (or ERP auto-match)
  mark_outcome()  → deal / no_deal / above / below + optional reason
  list()          → filtered timeline for the UI
  accuracy_stats()→ per-product hit rate, MAE, directional accuracy
"""
from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import db as _app_db

logger = logging.getLogger(__name__)

_ENTERPRISE_PRODUCTS = {"enterprise_c5", "enterprise_c9"}


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _engine():
    return _app_db.engine


def _normalize_price_unit(value: Optional[str]) -> Optional[str]:
    unit = str(value or "").strip()
    return unit[:32] or None


class DecisionLogService:

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        product_id: str,
        product_name: str,
        ai_price_low: float,
        ai_price_high: float,
        ai_action: str,
        ai_confidence: float,
        ai_reasoning: str,
        price_unit: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        market_snapshot: Optional[dict] = None,
        source: str = "chat",
    ) -> dict:
        """Record a new AI pricing recommendation."""
        entry_id = str(uuid.uuid4())
        ai_price_mid = round((ai_price_low + ai_price_high) / 2, 2)
        now = _now()
        snapshot_payload = self._build_market_snapshot(product_id, market_snapshot)
        normalized_price_unit = _normalize_price_unit(
            price_unit or snapshot_payload.get("unit") or snapshot_payload.get("price_unit")
        )

        with _engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO decision_log (
                        id, product_id, product_name, created_at, user_id, session_id,
                        ai_price_low, ai_price_high, ai_price_mid, price_unit,
                        ai_action, ai_confidence, ai_reasoning,
                        market_snapshot_json, status, source
                    ) VALUES (
                        :id, :product_id, :product_name, :created_at, :user_id, :session_id,
                        :ai_price_low, :ai_price_high, :ai_price_mid, :price_unit,
                        :ai_action, :ai_confidence, :ai_reasoning,
                        :market_snapshot_json, 'pending', :source
                    )
                    """
                ),
                {
                    "id": entry_id,
                    "product_id": product_id,
                    "product_name": product_name,
                    "created_at": now,
                    "user_id": user_id,
                    "session_id": session_id,
                    "ai_price_low": ai_price_low,
                    "ai_price_high": ai_price_high,
                    "ai_price_mid": ai_price_mid,
                    "price_unit": normalized_price_unit,
                    "ai_action": ai_action,
                    "ai_confidence": ai_confidence,
                    "ai_reasoning": ai_reasoning,
                    "market_snapshot_json": json.dumps(snapshot_payload),
                    "source": source,
                },
            )

        logger.info(
            "Decision log created: id=%s product=%s range=[%.0f–%.0f]",
            entry_id, product_id, ai_price_low, ai_price_high,
        )
        return self._get(entry_id)

    # ------------------------------------------------------------------
    # Mark executed
    # ------------------------------------------------------------------

    def mark_executed(
        self,
        entry_id: str,
        executed_price: float,
        executed_by: Optional[str] = None,
    ) -> dict:
        """Record the actual price at which the trade was executed."""
        entry = self._get(entry_id)
        if not entry:
            raise ValueError(f"Decision log entry not found: {entry_id}")

        price_error_cny = round(executed_price - entry["ai_price_mid"], 2)
        price_in_range = 1 if entry["ai_price_low"] <= executed_price <= entry["ai_price_high"] else 0

        with _engine().begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE decision_log
                    SET executed_price = :executed_price,
                        executed_at = :executed_at,
                        executed_by = :executed_by,
                        price_error_cny = :price_error_cny,
                        price_in_range = :price_in_range,
                        status = 'executed'
                    WHERE id = :id
                    """
                ),
                {
                    "executed_price": executed_price,
                    "executed_at": _now(),
                    "executed_by": executed_by,
                    "price_error_cny": price_error_cny,
                    "price_in_range": price_in_range,
                    "id": entry_id,
                },
            )
        return self._get(entry_id)

    # ------------------------------------------------------------------
    # Mark outcome
    # ------------------------------------------------------------------

    def mark_outcome(
        self,
        entry_id: str,
        outcome: str,  # 'deal' | 'no_deal' | 'above_range' | 'below_range'
        outcome_reason: Optional[str] = None,
        outcome_competitor_price: Optional[float] = None,
        outcome_note: Optional[str] = None,
    ) -> dict:
        """Record final outcome and close the loop."""
        entry = self._get(entry_id)
        if not entry:
            raise ValueError(f"Decision log entry not found: {entry_id}")

        with _engine().begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE decision_log
                    SET outcome = :outcome,
                        outcome_reason = :outcome_reason,
                        outcome_competitor_price = :outcome_competitor_price,
                        outcome_note = :outcome_note,
                        outcome_at = :outcome_at,
                        status = 'closed'
                    WHERE id = :id
                    """
                ),
                {
                    "outcome": outcome,
                    "outcome_reason": outcome_reason,
                    "outcome_competitor_price": outcome_competitor_price,
                    "outcome_note": outcome_note,
                    "outcome_at": _now(),
                    "id": entry_id,
                },
            )

        # Trigger learning loop calibration when we have executed price + outcome
        updated = self._get(entry_id)
        if updated.get("executed_price") and outcome in ("deal", "no_deal"):
            self._feed_learning_loop(updated)

        return updated

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list(
        self,
        product_id: Optional[str] = None,
        status: Optional[str] = None,
        days: int = 90,
        limit: int = 200,
    ) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conditions = ["created_at >= :cutoff"]
        params: dict[str, Any] = {"cutoff": cutoff, "limit": limit}

        if product_id:
            conditions.append("product_id = :product_id")
            params["product_id"] = product_id
        if status:
            conditions.append("status = :status")
            params["status"] = status

        where = " AND ".join(conditions)

        with _engine().connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT * FROM decision_log WHERE {where} ORDER BY created_at DESC LIMIT :limit"
                ),
                params,
            ).mappings().all()

        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Accuracy stats
    # ------------------------------------------------------------------

    def accuracy_stats(self, product_id: Optional[str] = None) -> dict:
        """Return per-product accuracy metrics from closed decisions."""
        where = "status = 'closed' AND executed_price IS NOT NULL"
        params: dict[str, Any] = {}
        if product_id:
            where += " AND product_id = :product_id"
            params["product_id"] = product_id

        with _engine().connect() as conn:
            rows = conn.execute(
                text(f"SELECT * FROM decision_log WHERE {where}"),
                params,
            ).mappings().all()

        if not rows:
            return {"products": [], "overall": {}}

        # Group by product
        by_product: dict[str, list] = {}
        for r in rows:
            pid = r["product_id"]
            by_product.setdefault(pid, []).append(dict(r))

        products = []
        all_errors = []
        all_in_range = []
        all_deals = []

        for pid, entries in by_product.items():
            errors = [abs(e["price_error_cny"]) for e in entries if e["price_error_cny"] is not None]
            in_range = [e["price_in_range"] for e in entries if e["price_in_range"] is not None]
            deals = [1 for e in entries if e["outcome"] == "deal"]

            mae = round(sum(errors) / len(errors), 1) if errors else None
            hit_rate = round(sum(in_range) / len(in_range) * 100, 1) if in_range else None
            deal_rate = round(len(deals) / len(entries) * 100, 1)

            products.append({
                "product_id": pid,
                "product_name": entries[0].get("product_name", pid),
                "total_decisions": len(entries),
                "hit_rate_pct": hit_rate,
                "mean_abs_error_cny": mae,
                "deal_rate_pct": deal_rate,
            })
            all_errors.extend(errors)
            all_in_range.extend(in_range)
            all_deals.extend(deals)

        overall_mae = round(sum(all_errors) / len(all_errors), 1) if all_errors else None
        overall_hit = round(sum(all_in_range) / len(all_in_range) * 100, 1) if all_in_range else None
        overall_deal = round(len(all_deals) / len(rows) * 100, 1) if rows else None

        return {
            "products": sorted(products, key=lambda x: x["total_decisions"], reverse=True),
            "overall": {
                "total_decisions": len(rows),
                "hit_rate_pct": overall_hit,
                "mean_abs_error_cny": overall_mae,
                "deal_rate_pct": overall_deal,
            },
        }

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, entry_id: str) -> None:
        """Hard-delete a decision log entry."""
        with _engine().begin() as conn:
            rowcount = conn.execute(
                text("DELETE FROM decision_log WHERE id = :id"),
                {"id": entry_id},
            ).rowcount
        if rowcount == 0:
            raise ValueError(f"Decision log entry not found: {entry_id}")

    # ------------------------------------------------------------------
    # Update (patch editable fields)
    # ------------------------------------------------------------------

    def update(
        self,
        entry_id: str,
        ai_price_low: Optional[float] = None,
        ai_price_high: Optional[float] = None,
        price_unit: Optional[str] = None,
        ai_action: Optional[str] = None,
        ai_reasoning: Optional[str] = None,
        outcome_note: Optional[str] = None,
        executed_price: Optional[float] = None,
    ) -> dict:
        """Patch editable fields on a decision log entry."""
        entry = self._get(entry_id)
        if not entry:
            raise ValueError(f"Decision log entry not found: {entry_id}")

        updates: dict = {}

        eff_low = ai_price_low if ai_price_low is not None else entry["ai_price_low"]
        eff_high = ai_price_high if ai_price_high is not None else entry["ai_price_high"]

        if ai_price_low is not None:
            updates["ai_price_low"] = ai_price_low
        if ai_price_high is not None:
            updates["ai_price_high"] = ai_price_high
        if ai_price_low is not None or ai_price_high is not None:
            updates["ai_price_mid"] = round((eff_low + eff_high) / 2, 2)
        if price_unit is not None:
            updates["price_unit"] = _normalize_price_unit(price_unit)
        if ai_action is not None:
            updates["ai_action"] = ai_action
        if ai_reasoning is not None:
            updates["ai_reasoning"] = ai_reasoning
        if outcome_note is not None:
            updates["outcome_note"] = outcome_note
        if executed_price is not None:
            eff_mid = updates.get("ai_price_mid", entry["ai_price_mid"])
            updates["executed_price"] = executed_price
            updates["price_error_cny"] = round(executed_price - eff_mid, 2)
            updates["price_in_range"] = 1 if eff_low <= executed_price <= eff_high else 0

        if not updates:
            return entry

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = entry_id
        with _engine().begin() as conn:
            conn.execute(
                text(f"UPDATE decision_log SET {set_clause} WHERE id = :id"),
                updates,
            )
        logger.info("Decision log updated: id=%s fields=%s", entry_id, list(updates.keys()))
        return self._get(entry_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_market_snapshot(
        self,
        product_id: str,
        market_snapshot: Optional[dict],
    ) -> dict:
        snapshot = dict(market_snapshot or {})
        if product_id in _ENTERPRISE_PRODUCTS:
            try:
                from backend.src.service.enterprise_data_service import enterprise_data

                summary = enterprise_data.get_summary(product_id)
                snapshot.setdefault(
                    "enterprise_coverage",
                    summary.get("coverage") or enterprise_data.get_coverage(product_id),
                )
                snapshot.setdefault(
                    "enterprise_semantics",
                    summary.get("semantics", {}),
                )
                snapshot.setdefault(
                    "canonical_product_id",
                    summary.get("canonical_product_id"),
                )
                snapshot.setdefault(
                    "gap_vs_market_abs",
                    summary.get("gap_vs_market_abs"),
                )
                snapshot.setdefault(
                    "gap_vs_market_pct",
                    summary.get("gap_vs_market_pct"),
                )
            except Exception as exc:
                logger.debug("Enterprise coverage snapshot skipped: %s", exc)
        return snapshot

    def _get(self, entry_id: str) -> Optional[dict]:
        with _engine().connect() as conn:
            row = conn.execute(
                text("SELECT * FROM decision_log WHERE id = :id"),
                {"id": entry_id},
            ).mappings().first()
        if not row:
            return None
        return self._row_to_dict(row)

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        if d.get("market_snapshot_json"):
            try:
                d["market_snapshot"] = json.loads(d["market_snapshot_json"])
            except Exception:
                d["market_snapshot"] = {}
        else:
            d["market_snapshot"] = {}
        d.setdefault("price_unit", None)
        return d

    def _feed_learning_loop(self, entry: dict) -> None:
        """Send outcome to IntelligenceLearningLoop for elasticity calibration."""
        try:
            from backend.src.intelligence.intelligence_learning_loop import (
                IntelligenceLearningLoop,
            )
            loop = IntelligenceLearningLoop()
            # Compute naphtha price at decision time vs now as a proxy event
            snapshot = entry.get("market_snapshot") or {}
            naphtha_at_decision = snapshot.get("naphtha_price")
            if not naphtha_at_decision:
                return

            predicted_pct = round(
                (entry["ai_price_mid"] - (naphtha_at_decision or entry["ai_price_mid"])) /
                max(naphtha_at_decision, 1) * 100,
                3,
            )
            actual_pct = round(
                (entry["executed_price"] - (naphtha_at_decision or entry["executed_price"])) /
                max(naphtha_at_decision, 1) * 100,
                3,
            )
            loop.record_event_outcome(
                event_id=f"decision_log_{entry['id'][:8]}",
                event_type="decision_log_feedback",
                predicted_naphtha_pct=predicted_pct,
                actual_naphtha_pct=actual_pct,
                resolution_days=1,
                notes=f"product={entry['product_id']} outcome={entry['outcome']}",
            )
        except Exception as exc:
            logger.debug("Learning loop feed skipped: %s", exc)


decision_log_service = DecisionLogService()
