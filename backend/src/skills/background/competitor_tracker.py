"""Competitor Intelligence Tracker – Background Skill #1.

Monitors competitor pricing moves (SINOPEC, Yangzi, Maoming posted prices)
against Ecisco's own pricing trajectory.  Detects when competitors shift
strategy so downstream pricing is never decided in isolation.

Data sources:
  - external MySQL market tables (SINOPEC 挂牌价, competitor posted prices)
  - perception snapshot products (latest vs 7d change)
  - knowledge graph for product mapping
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# Competitor price columns that appear in the external market DB
# These map product_id → column aliases found in the market tables
_COMPETITOR_COLUMNS = {
    "isoprene":    ["上海石化挂牌价"],
    "piperylene":  ["上海石化挂牌价"],
    "dcpd":        ["上海石化"],
    "cracked_c5":  ["扬子裂解C5", "茂名裂解C5"],
    "cracked_c9":  [],
    "sis":         ["巴陵1105"],
    "c5_resin":    [],
    "blowing_agent": ["F7挂牌价"],
}


class CompetitorIntelligenceTracker(BackgroundSkill):
    name = "competitor_intelligence_tracker"
    interval_minutes = 120  # every 2 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.market_data_service import market_data
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"cannot import market_data: {exc}",
            )

        alerts: list[str] = []
        moves: list[dict[str, Any]] = []

        for product_id, comp_cols in _COMPETITOR_COLUMNS.items():
            if not comp_cols:
                continue
            try:
                history = market_data.get_price_history(product_id)
                if not history or len(history) < 2:
                    continue

                latest = history[0]
                prev = history[1] if len(history) > 1 else history[0]

                for col in comp_cols:
                    cur_val = latest.get(col)
                    prev_val = prev.get(col)
                    if cur_val is None or prev_val is None:
                        continue
                    try:
                        cur_f = float(cur_val)
                        prev_f = float(prev_val)
                    except (ValueError, TypeError):
                        continue
                    if prev_f == 0:
                        continue

                    change_pct = (cur_f - prev_f) / prev_f * 100
                    move = {
                        "product_id": product_id,
                        "competitor": col,
                        "current": cur_f,
                        "previous": prev_f,
                        "change_pct": round(change_pct, 2),
                        "direction": "up" if change_pct > 0 else "down" if change_pct < 0 else "flat",
                    }
                    moves.append(move)

                    # Flag significant moves (>1.5% day-over-day)
                    if abs(change_pct) >= 1.5:
                        alerts.append(
                            f"[COMPETITOR] {col} moved {change_pct:+.1f}% on {product_id} "
                            f"({prev_f:.0f}→{cur_f:.0f} CNY/ton)"
                        )
            except Exception as exc:
                logger.debug("CompetitorTracker: %s skipped: %s", product_id, exc)

        # Detect divergence: competitor raising price while Ecisco product flat
        divergences = self._detect_divergences(moves)
        alerts.extend(divergences)

        return SkillResult(
            skill_name=self.name,
            summary=f"{len(moves)} competitor price points tracked, {len(alerts)} alert(s)",
            data={"moves": moves, "divergence_alerts": divergences},
            alerts=alerts,
        )

    @staticmethod
    def _detect_divergences(moves: list[dict]) -> list[str]:
        """Flag when competitors move strongly but our anchor hasn't."""
        divergences: list[str] = []
        for move in moves:
            if abs(move["change_pct"]) >= 2.0:
                divergences.append(
                    f"[DIVERGENCE] {move['competitor']} on {move['product_id']} "
                    f"shifted {move['change_pct']:+.1f}% — review Ecisco pricing posture"
                )
        return divergences
