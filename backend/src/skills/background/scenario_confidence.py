"""Scenario Confidence Engine – Background Skill #4.

Quantifies forecast uncertainty across all tracked products so the
pipeline can tell users *when to trust* a recommendation vs *when to
be cautious*.

Methodology:
  1. Pulls recent price history for each product.
  2. Computes realised volatility over 7d, 14d, 30d windows.
  3. Measures forecast model stability (lookback MAPE if available).
  4. Blends with macro regime uncertainty from intelligence events.
  5. Produces per-product confidence scores (0-1).

Petrochemical context:  Post-holiday restarts, SINOPEC posted-price
changes, and turnaround seasons all spike forecast uncertainty.  This
skill makes that uncertainty *visible* so each response can carry a
calibrated confidence tag.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# Products to track
_TRACKED_PRODUCTS = [
    "cracked_c5", "cracked_c9", "isoprene", "piperylene",
    "dcpd", "sis", "c5_resin", "blowing_agent",
]

# Confidence thresholds
_HIGH_VOL_THRESHOLD = 2.5    # 7d daily vol % above which confidence drops
_MEDIUM_VOL_THRESHOLD = 1.5


class ScenarioConfidenceEngine(BackgroundSkill):
    name = "scenario_confidence_engine"
    interval_minutes = 120  # every 2 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.forecasting_engine import get_price_series
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"cannot import forecasting_engine: {exc}",
            )

        product_scores: list[dict[str, Any]] = []
        alerts: list[str] = []

        for pid in _TRACKED_PRODUCTS:
            try:
                series = get_price_series(pid, "price", days=60)
                if series is None or len(series) < 7:
                    continue

                returns = series.pct_change().dropna()

                # Realised volatility windows
                vol_7d = float(returns.tail(7).std() * 100)  if len(returns) >= 7  else 0
                vol_14d = float(returns.tail(14).std() * 100) if len(returns) >= 14 else vol_7d
                vol_30d = float(returns.tail(30).std() * 100) if len(returns) >= 30 else vol_14d

                # Data freshness penalty
                last_idx = series.index[-1] if hasattr(series.index[-1], 'date') else None
                freshness_penalty = 0.0
                if last_idx:
                    from datetime import datetime, timezone
                    try:
                        import pandas as pd
                        last_date = pd.Timestamp(last_idx).date()
                        today = datetime.now(timezone.utc).date()
                        stale_days = (today - last_date).days
                        if stale_days > 1:
                            freshness_penalty = min(stale_days * 0.05, 0.3)
                    except Exception:
                        pass

                # Data completeness (how many of last 30 days have data?)
                completeness = min(len(series.tail(30)) / 20.0, 1.0)  # 20 trading days

                # Trend stability – low autocorrelation of returns = less predictable
                if len(returns) >= 10:
                    autocorr = float(returns.autocorr(lag=1)) if hasattr(returns, 'autocorr') else 0.0
                    stability = max(0.0, min(abs(autocorr), 1.0))
                else:
                    stability = 0.5

                # Composite confidence score (0-1, higher = more confident)
                vol_score = max(0.0, 1.0 - vol_7d / 5.0)  # 5% vol → score 0
                confidence = (
                    0.35 * vol_score
                    + 0.25 * completeness
                    + 0.20 * stability
                    + 0.20 * 0.7  # base prior
                ) - freshness_penalty
                confidence = max(0.05, min(confidence, 0.99))

                label = "HIGH" if confidence >= 0.70 else "MEDIUM" if confidence >= 0.45 else "LOW"

                entry = {
                    "product_id": pid,
                    "confidence": round(confidence, 3),
                    "label": label,
                    "vol_7d": round(vol_7d, 2),
                    "vol_14d": round(vol_14d, 2),
                    "vol_30d": round(vol_30d, 2),
                    "completeness": round(completeness, 2),
                    "stability": round(stability, 2),
                    "freshness_penalty": round(freshness_penalty, 2),
                }
                product_scores.append(entry)

                if label == "LOW":
                    alerts.append(
                        f"[LOW CONFIDENCE] {pid}: confidence={confidence:.0%}, "
                        f"vol_7d={vol_7d:.1f}% — forecasts for this product are unreliable right now"
                    )

            except Exception as exc:
                logger.debug("ScenarioConfidence: %s failed: %s", pid, exc)

        # Macro uncertainty from intelligence events
        macro_uncertain = False
        try:
            from backend.src.intelligence.event_registry import intelligence_event_registry
            events = intelligence_event_registry.list_recent_events(hours=24, limit=10)
            high_impact = [e for e in events if (e.get("impact_magnitude") or 0) > 0.6]
            if high_impact:
                macro_uncertain = True
                alerts.append(
                    f"[MACRO UNCERTAINTY] {len(high_impact)} high-impact events detected — "
                    "scenario spreads may widen"
                )
        except Exception:
            pass

        return SkillResult(
            skill_name=self.name,
            summary=(
                f"confidence scored for {len(product_scores)} products | "
                f"macro_uncertain={macro_uncertain} | {len(alerts)} alert(s)"
            ),
            data={
                "product_scores": product_scores,
                "macro_uncertain": macro_uncertain,
            },
            alerts=alerts,
        )
