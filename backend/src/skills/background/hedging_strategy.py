"""Hedging Strategy Recommender – Background Skill #7.

Converts forecast risk into protective action suggestions for Ecisco's
procurement and sales teams.

Petrochemical context:  Ecisco sells C5/C9 derivatives on term contracts
and spot.  When forecast uncertainty is high or directional conviction is
strong, the business can lock in forward purchases, pre-sell inventory,
or adjust quote validity windows.  This skill turns abstract risk metrics
into concrete hedging / position recommendations.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

_PRODUCTS = [
    "cracked_c5", "isoprene", "piperylene", "dcpd",
    "cracked_c9", "sis", "c5_resin",
]


class HedgingStrategyRecommender(BackgroundSkill):
    name = "hedging_strategy_recommender"
    interval_minutes = 240  # every 4 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.forecasting_engine import get_price_series, get_naphtha_series
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        recommendations: list[dict[str, Any]] = []
        alerts: list[str] = []

        # Get macro backdrop
        naphtha_trend = self._compute_trend(get_naphtha_series(days=14))

        for pid in _PRODUCTS:
            try:
                series = get_price_series(pid, "price", days=30)
                if series is None or len(series) < 14:
                    continue

                returns = series.pct_change().dropna()
                vol_7d = float(returns.tail(7).std() * 100) if len(returns) >= 7 else 0
                trend = self._compute_trend(series.tail(14))

                latest_price = float(series.iloc[-1])

                rec = self._generate_recommendation(
                    pid, trend, vol_7d, naphtha_trend, latest_price
                )
                recommendations.append(rec)

                if rec["urgency"] in ("high", "critical"):
                    alerts.append(
                        f"[HEDGE] {pid}: {rec['action']} — {rec['rationale']}"
                    )

            except Exception as exc:
                logger.debug("HedgingRecommender: %s failed: %s", pid, exc)

        return SkillResult(
            skill_name=self.name,
            summary=f"generated hedging recommendations for {len(recommendations)} products",
            data={"recommendations": recommendations},
            alerts=alerts,
        )

    def _generate_recommendation(
        self,
        product_id: str,
        product_trend: str,
        vol_7d: float,
        naphtha_trend: str,
        latest_price: float,
    ) -> dict[str, Any]:
        """Generate a hedging recommendation based on trend, vol, and macro."""

        # Decision matrix
        if product_trend == "strong_up" and naphtha_trend in ("up", "strong_up"):
            action = "accelerate_procurement"
            rationale = (
                f"Both {product_id} and feedstock trending up — lock in current "
                f"prices ({latest_price:.0f}) before further rises"
            )
            urgency = "high"
            validity_window = "24h"
        elif product_trend == "strong_down" and vol_7d > 2.0:
            action = "defer_procurement_pre_sell"
            rationale = (
                f"{product_id} in accelerating decline with high vol ({vol_7d:.1f}%) "
                "— pre-sell inventory at current levels, defer new purchases"
            )
            urgency = "high"
            validity_window = "24h"
        elif vol_7d > 3.0:
            action = "shorten_quote_validity"
            rationale = (
                f"Extreme vol ({vol_7d:.1f}%) on {product_id} — "
                f"use intra-day quotes, avoid multi-day commitments"
            )
            urgency = "critical"
            validity_window = "4h"
        elif product_trend == "up" and naphtha_trend == "down":
            action = "hold_and_monitor"
            rationale = (
                f"{product_id} rising against falling feedstock — "
                "likely demand-driven, hold position but watch for reversion"
            )
            urgency = "medium"
            validity_window = "48h"
        elif product_trend == "down" and naphtha_trend == "up":
            action = "tighten_offers"
            rationale = (
                f"{product_id} falling while feedstock rises — margin squeeze risk, "
                "tighten offer terms and reduce discount flexibility"
            )
            urgency = "high"
            validity_window = "24h"
        else:
            action = "maintain_standard"
            rationale = (
                f"No significant directional signal on {product_id} — "
                "maintain standard pricing and procurement cadence"
            )
            urgency = "low"
            validity_window = "72h"

        return {
            "product_id": product_id,
            "action": action,
            "rationale": rationale,
            "urgency": urgency,
            "recommended_validity_window": validity_window,
            "latest_price": round(latest_price, 0),
            "vol_7d": round(vol_7d, 2),
            "product_trend": product_trend,
            "naphtha_trend": naphtha_trend,
        }

    @staticmethod
    def _compute_trend(series) -> str:
        """Classify a price series into a trend label."""
        if series is None or len(series) < 5:
            return "flat"
        start = float(series.iloc[0])
        end = float(series.iloc[-1])
        if start == 0:
            return "flat"
        pct = (end - start) / start * 100
        if pct > 3.0:
            return "strong_up"
        elif pct > 1.0:
            return "up"
        elif pct < -3.0:
            return "strong_down"
        elif pct < -1.0:
            return "down"
        return "flat"
