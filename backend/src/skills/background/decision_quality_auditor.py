"""Decision Quality Auditor – Background Skill #3.

Retrospectively evaluates past EDIA pricing recommendations against
actual market outcomes.  Measures hit-rate, directional accuracy, and
bias patterns so the system can self-improve.

Petrochemical context:  Ecisco's pricing team needs to know *whether*
EDIA's advice is actually working.  If recommendations systematically
overshoot on the bullish side for DCPD, the team should be told so they
can apply mental discounts.  This auditor quantifies that.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)


class DecisionQualityAuditor(BackgroundSkill):
    name = "decision_quality_auditor"
    interval_minutes = 360  # every 6 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.market_data_service import market_data
            from backend.src.db.sqlite_db import db
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        alerts: list[str] = []
        audits: list[dict[str, Any]] = []
        products_audited = 0
        total_accurate = 0
        total_evaluated = 0

        # Retrieve recent sessions with pricing recommendations
        try:
            recent_sessions = db.list_recent_sessions(limit=50)
        except Exception:
            recent_sessions = []

        for session in recent_sessions:
            session_id = session.get("id") or session.get("session_id")
            if not session_id:
                continue

            try:
                messages = db.get_messages(str(session_id))
            except Exception:
                continue

            for msg in messages:
                if msg.get("role") != "assistant":
                    continue
                metadata = msg.get("metadata") or {}
                pricing = metadata.get("pricing_decision") or {}
                forecast = metadata.get("forecast") or {}

                rec_price = pricing.get("recommended_price")
                action = pricing.get("action", "")
                product_id = forecast.get("product_id") or metadata.get("product_id")
                msg_time = msg.get("created_at") or msg.get("timestamp")

                if not rec_price or not product_id or not msg_time:
                    continue

                # Check if enough time has passed to evaluate (7 days)
                try:
                    if isinstance(msg_time, str):
                        msg_dt = datetime.fromisoformat(msg_time.replace("Z", "+00:00"))
                    else:
                        msg_dt = msg_time
                    if not msg_dt.tzinfo:
                        msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                    days_ago = (datetime.now(timezone.utc) - msg_dt).days
                    if days_ago < 7:
                        continue  # too recent to evaluate
                except Exception:
                    continue

                # Get actual price 7 days after recommendation
                try:
                    history = market_data.get_price_history(product_id)
                    if not history:
                        continue

                    # Find price closest to 7 days after recommendation
                    target_date = (msg_dt + timedelta(days=7)).strftime("%Y-%m-%d")
                    actual_price = None
                    for row in history:
                        row_date = str(row.get("date", ""))
                        if row_date <= target_date:
                            actual_price = row.get("price") or row.get("latest_price")
                            break

                    if actual_price is None:
                        continue

                    actual_price = float(actual_price)
                    rec_price = float(rec_price)
                except Exception:
                    continue

                # Evaluate
                error_pct = (actual_price - rec_price) / rec_price * 100
                direction_correct = (
                    (action in ("raise", "tighten") and actual_price > rec_price * 0.99)
                    or (action in ("lower", "flex") and actual_price < rec_price * 1.01)
                    or (action in ("hold", "maintain") and abs(error_pct) < 2.0)
                )

                audit = {
                    "product_id": product_id,
                    "recommended_price": rec_price,
                    "actual_price": actual_price,
                    "error_pct": round(error_pct, 2),
                    "action": action,
                    "direction_correct": direction_correct,
                    "days_ago": days_ago,
                }
                audits.append(audit)
                products_audited += 1
                total_evaluated += 1
                if direction_correct:
                    total_accurate += 1

        # Compute aggregate metrics
        accuracy_rate = (total_accurate / total_evaluated * 100) if total_evaluated > 0 else 0
        avg_error = (
            sum(abs(a["error_pct"]) for a in audits) / len(audits)
            if audits else 0
        )

        # Detect systematic bias
        bias_by_product: dict[str, list[float]] = {}
        for a in audits:
            bias_by_product.setdefault(a["product_id"], []).append(a["error_pct"])

        biased_products: list[dict] = []
        for pid, errors in bias_by_product.items():
            mean_err = sum(errors) / len(errors)
            if abs(mean_err) > 1.5 and len(errors) >= 3:
                direction = "over-optimistic" if mean_err > 0 else "over-pessimistic"
                biased_products.append({
                    "product_id": pid,
                    "mean_error_pct": round(mean_err, 2),
                    "direction": direction,
                    "sample_size": len(errors),
                })
                alerts.append(
                    f"[BIAS] EDIA is {direction} on {pid}: mean error {mean_err:+.1f}% "
                    f"over {len(errors)} recommendations"
                )

        if total_evaluated > 0 and accuracy_rate < 55:
            alerts.append(
                f"[QUALITY] Overall directional accuracy {accuracy_rate:.0f}% is below 55% threshold"
            )

        return SkillResult(
            skill_name=self.name,
            summary=(
                f"audited {total_evaluated} past decisions: "
                f"accuracy {accuracy_rate:.0f}%, avg error {avg_error:.1f}%, "
                f"{len(biased_products)} biased product(s)"
            ),
            data={
                "total_evaluated": total_evaluated,
                "accuracy_rate": round(accuracy_rate, 1),
                "avg_error_pct": round(avg_error, 2),
                "biased_products": biased_products,
                "recent_audits": audits[-20:],
            },
            alerts=alerts,
        )
