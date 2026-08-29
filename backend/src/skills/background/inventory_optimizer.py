"""Inventory Level Optimizer – Background Skill #9.

Converts market outlook and forecast data into inventory and cash-flow
posture recommendations using ERP KPIs.

Petrochemical context:  Ecisco holds physical inventory of C5/C9 products.
Carrying too much during a declining market erodes margins; holding too
little during a rising market means lost sales.  This skill bridges the
gap between market intelligence and operational inventory decisions by
combining forecast direction with real-time ERP stock levels.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# Target days-of-stock by product category (domain calibrated)
_TARGET_INVENTORY_DAYS = {
    "cracked_c5": 20,
    "cracked_c9": 20,
    "isoprene": 18,
    "piperylene": 18,
    "dcpd": 22,
    "sis": 25,
    "c5_resin": 28,
    "blowing_agent": 22,
}


class InventoryLevelOptimizer(BackgroundSkill):
    name = "inventory_level_optimizer"
    interval_minutes = 240  # every 4 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.tools.erp_tools import query_inventory_levels
            from backend.src.service.forecasting_engine import get_price_series
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        recommendations: list[dict[str, Any]] = []
        alerts: list[str] = []

        for product_id, target_days in _TARGET_INVENTORY_DAYS.items():
            try:
                # Get inventory from ERP
                inv_text = query_inventory_levels(product_id)
                inv_qty = self._parse_inventory_qty(inv_text)

                # Get daily avg sales (from recent data)
                daily_sales = self._estimate_daily_sales(inv_text)

                # Get market trend from price data
                trend = "flat"
                try:
                    series = get_price_series(product_id, "price", days=14)
                    if series is not None and len(series) >= 5:
                        start = float(series.iloc[0])
                        end = float(series.iloc[-1])
                        if start > 0:
                            pct = (end - start) / start * 100
                            if pct > 2.0:
                                trend = "rising"
                            elif pct < -2.0:
                                trend = "falling"
                except Exception:
                    pass

                # Calculate days-of-stock
                dos = inv_qty / daily_sales if daily_sales > 0 else 999

                # Generate recommendation
                rec = self._recommend(product_id, dos, target_days, trend, inv_qty)
                recommendations.append(rec)

                if rec["urgency"] in ("high", "critical"):
                    alerts.append(
                        f"[INVENTORY] {product_id}: {rec['action']} — "
                        f"DoS={dos:.0f} (target={target_days}), market {trend}"
                    )

            except Exception as exc:
                logger.debug("InventoryOptimizer: %s failed: %s", product_id, exc)

        return SkillResult(
            skill_name=self.name,
            summary=f"optimised inventory for {len(recommendations)} products, {len(alerts)} action(s)",
            data={"recommendations": recommendations},
            alerts=alerts,
        )

    def _recommend(
        self,
        product_id: str,
        dos: float,
        target: int,
        trend: str,
        qty: float,
    ) -> dict[str, Any]:
        """Generate inventory action based on days-of-stock vs target and market trend."""

        ratio = dos / target if target > 0 else 1.0

        if dos < target * 0.5 and trend == "rising":
            action = "urgent_restock"
            rationale = (
                f"Only {dos:.0f} days of stock vs {target}d target while market rising — "
                "accelerate procurement immediately"
            )
            urgency = "critical"
        elif dos < target * 0.7:
            action = "increase_procurement"
            rationale = f"Below target: {dos:.0f}d vs {target}d — replenish proactively"
            urgency = "high"
        elif dos > target * 1.8 and trend == "falling":
            action = "aggressive_destocking"
            rationale = (
                f"Excessive stock ({dos:.0f}d vs {target}d target) in falling market — "
                "prioritise sell-down to reduce holding losses"
            )
            urgency = "critical"
        elif dos > target * 1.5:
            action = "moderate_destocking"
            rationale = f"Above target: {dos:.0f}d vs {target}d — increase sales push"
            urgency = "medium"
        elif dos > target * 1.3 and trend == "falling":
            action = "mild_destocking"
            rationale = f"Slightly heavy ({dos:.0f}d) in falling market — trim positions"
            urgency = "medium"
        else:
            action = "maintain_current"
            rationale = f"Stock level ({dos:.0f}d) near target ({target}d) — no action needed"
            urgency = "low"

        return {
            "product_id": product_id,
            "days_of_stock": round(dos, 1),
            "target_days": target,
            "inventory_qty": round(qty, 0),
            "market_trend": trend,
            "action": action,
            "rationale": rationale,
            "urgency": urgency,
        }

    @staticmethod
    def _parse_inventory_qty(text: str) -> float:
        """Extract numeric inventory quantity from ERP query result text."""
        import re
        # Look for patterns like "库存: 1500" or "FBASEQTY: 2000" or "总量: 800"
        patterns = [
            r"(?:库存|FBASEQTY|总量|数量|stock)\s*[:：]\s*([\d,.]+)",
            r"([\d,]+)\s*(?:吨|ton|MT)",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        return 0.0

    @staticmethod
    def _estimate_daily_sales(text: str) -> float:
        """Rough daily sales estimate from ERP data."""
        import re
        patterns = [
            r"(?:日均|daily|平均)\s*[:：]\s*([\d,.]+)",
            r"(?:月销|monthly)\s*[:：]\s*([\d,.]+)",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                try:
                    v = float(m.group(1).replace(",", ""))
                    if "月" in p or "monthly" in p:
                        return v / 30.0
                    return v
                except ValueError:
                    pass
        # Fallback: assume ~50 tons/day for a mid-sized product line
        return 50.0
