"""Causal Impact Attribution – Background Skill #5.

Explains *what actually drove* recent price movements for each tracked
product by decomposing returns into upstream (feedstock), macro, seasonal,
and idiosyncratic components.

Petrochemical context:  When isoprene rises 3% the sales team needs to
know whether that's naphtha cost-push, downstream SIS restocking, or a
SINOPEC posted-price adjustment.  Different drivers imply different
pricing responses.  This skill attributes retrospectively so every future
diagnosis starts with a grounded causal baseline.
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

# Approximate feedstock elasticities (from the forecasting engine design)
_FEEDSTOCK_ELASTICITY = {
    "cracked_c5": 0.85,
    "isoprene": 0.55,
    "piperylene": 0.50,
    "dcpd": 0.60,
    "cracked_c9": 0.70,
    "sis": 0.30,
    "c5_resin": 0.35,
}


class CausalImpactAttribution(BackgroundSkill):
    name = "causal_impact_attribution"
    interval_minutes = 180  # every 3 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.forecasting_engine import get_price_series, get_naphtha_series
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        attributions: list[dict[str, Any]] = []
        alerts: list[str] = []

        naphtha_series = get_naphtha_series(days=14)
        naphtha_change_7d = 0.0
        if naphtha_series is not None and len(naphtha_series) >= 8:
            naphtha_now = float(naphtha_series.iloc[-1])
            naphtha_7d = float(naphtha_series.iloc[-8])
            if naphtha_7d > 0:
                naphtha_change_7d = (naphtha_now - naphtha_7d) / naphtha_7d * 100

        for pid in _PRODUCTS:
            try:
                series = get_price_series(pid, "price", days=14)
                if series is None or len(series) < 8:
                    continue

                price_now = float(series.iloc[-1])
                price_7d = float(series.iloc[-8])
                if price_7d == 0:
                    continue
                total_change_pct = (price_now - price_7d) / price_7d * 100

                # Decompose
                elasticity = _FEEDSTOCK_ELASTICITY.get(pid, 0.5)
                feedstock_component = naphtha_change_7d * elasticity
                residual = total_change_pct - feedstock_component

                # Simple seasonal detection (check if month matches known season)
                seasonal_component = self._seasonal_estimate(pid)
                idiosyncratic = residual - seasonal_component

                # Determine primary driver
                components = {
                    "feedstock_cost_push": abs(feedstock_component),
                    "seasonal_pattern": abs(seasonal_component),
                    "idiosyncratic_demand": abs(idiosyncratic),
                }
                primary_driver = max(components, key=components.get)

                attr = {
                    "product_id": pid,
                    "total_change_7d_pct": round(total_change_pct, 2),
                    "feedstock_component_pct": round(feedstock_component, 2),
                    "seasonal_component_pct": round(seasonal_component, 2),
                    "idiosyncratic_pct": round(idiosyncratic, 2),
                    "primary_driver": primary_driver,
                    "naphtha_change_7d": round(naphtha_change_7d, 2),
                    "elasticity_used": elasticity,
                }
                attributions.append(attr)

                # Alert on large unexplained moves
                if abs(idiosyncratic) > 2.0:
                    alerts.append(
                        f"[CAUSAL] {pid} has {idiosyncratic:+.1f}% unexplained 7d move "
                        f"(total {total_change_pct:+.1f}%, feedstock {feedstock_component:+.1f}%) "
                        f"— investigate demand-side or competitive shifts"
                    )

            except Exception as exc:
                logger.debug("CausalAttribution: %s failed: %s", pid, exc)

        return SkillResult(
            skill_name=self.name,
            summary=f"attributed 7d returns for {len(attributions)} products, {len(alerts)} anomalous moves",
            data={"attributions": attributions},
            alerts=alerts,
        )

    @staticmethod
    def _seasonal_estimate(product_id: str) -> float:
        """Return approximate seasonal return component for current month.

        Based on domain knowledge encoded in the forecasting engine's
        seasonal rules layer.
        """
        from datetime import datetime
        month = datetime.now().month

        # Simplified seasonal calendar for C5/C9 products
        seasonal_rules: dict[str, dict[int, float]] = {
            "c5_resin": {
                1: -0.5, 2: -0.8, 3: 0.3, 4: 0.8, 5: 1.0, 6: 0.5,
                7: 0.3, 8: 0.2, 9: 0.5, 10: 0.8, 11: -0.3, 12: -0.8,
            },
            "dcpd": {
                1: -0.3, 2: -0.5, 3: 0.5, 4: 0.8, 5: 0.5, 6: 0.3,
                7: 0.2, 8: 0.0, 9: 0.5, 10: 0.3, 11: 0.0, 12: -0.3,
            },
            "isoprene": {
                1: -0.3, 2: -0.5, 3: 0.3, 4: 0.5, 5: 0.5, 6: 0.3,
                7: 0.5, 8: 0.8, 9: 0.5, 10: 0.3, 11: -0.2, 12: -0.5,
            },
        }
        rules = seasonal_rules.get(product_id, {})
        return rules.get(month, 0.0)
