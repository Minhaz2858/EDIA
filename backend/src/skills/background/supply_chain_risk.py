"""Supply Chain Risk Monitor – Background Skill #2.

Monitors upstream feedstock supply risk indicators — naphtha/crude price
volatility, shipping disruptions, plant outage signals, and inventory
tightness — to provide early warning of potential supply chain stress.

Petrochemical context:  Ecisco's C5/C9 business is entirely derived from
steam-cracker naphtha feed.  Any disruption — refinery turnaround, tanker
delay, geopolitical crude shock — propagates through the chain within days.
Detecting supply risk early gives the pricing team a head-start on
adjusting inventory and quotes.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# Supply-chain risk thresholds calibrated for C5/C9 petrochemicals
_NAPHTHA_VOL_THRESHOLD = 3.0   # % 7-day realised vol → elevated risk
_CRUDE_VOL_THRESHOLD = 4.0     # % 7-day realised vol → elevated risk
_PRICE_SPIKE_PCT = 3.0         # single-day move triggering alert
_INVENTORY_LOW_DAYS = 14       # days-of-stock below which supply is tight


class SupplyChainRiskMonitor(BackgroundSkill):
    name = "supply_chain_risk_monitor"
    interval_minutes = 60  # every hour

    def run(self) -> SkillResult:
        try:
            from backend.src.service.forecasting_engine import get_price_series, get_naphtha_series
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"cannot import forecasting_engine: {exc}",
            )

        alerts: list[str] = []
        risk_factors: list[dict[str, Any]] = []
        overall_risk = "low"

        # 1. Naphtha volatility and spike detection
        try:
            naphtha = get_naphtha_series(days=30)
            if naphtha is not None and len(naphtha) >= 7:
                returns = naphtha.pct_change().dropna()
                vol_7d = float(returns.tail(7).std() * 100)
                last_return = float(returns.iloc[-1] * 100) if len(returns) > 0 else 0.0

                risk_factors.append({
                    "factor": "naphtha_volatility_7d",
                    "value": round(vol_7d, 2),
                    "threshold": _NAPHTHA_VOL_THRESHOLD,
                    "status": "elevated" if vol_7d > _NAPHTHA_VOL_THRESHOLD else "normal",
                })

                if vol_7d > _NAPHTHA_VOL_THRESHOLD:
                    alerts.append(
                        f"[SUPPLY RISK] Naphtha 7d realised vol {vol_7d:.1f}% > {_NAPHTHA_VOL_THRESHOLD}% threshold"
                    )
                    overall_risk = "medium"

                if abs(last_return) > _PRICE_SPIKE_PCT:
                    alerts.append(
                        f"[SUPPLY SPIKE] Naphtha moved {last_return:+.1f}% in last session"
                    )
                    overall_risk = "high"
        except Exception as exc:
            logger.debug("SupplyChainRisk: naphtha analysis failed: %s", exc)

        # 2. Crude oil volatility
        try:
            crude = get_price_series("crude_oil", "price", days=30)
            if crude is not None and len(crude) >= 7:
                returns = crude.pct_change().dropna()
                vol_7d = float(returns.tail(7).std() * 100)

                risk_factors.append({
                    "factor": "crude_volatility_7d",
                    "value": round(vol_7d, 2),
                    "threshold": _CRUDE_VOL_THRESHOLD,
                    "status": "elevated" if vol_7d > _CRUDE_VOL_THRESHOLD else "normal",
                })

                if vol_7d > _CRUDE_VOL_THRESHOLD:
                    alerts.append(
                        f"[SUPPLY RISK] Crude 7d realised vol {vol_7d:.1f}% > {_CRUDE_VOL_THRESHOLD}% threshold"
                    )
                    if overall_risk == "low":
                        overall_risk = "medium"
        except Exception as exc:
            logger.debug("SupplyChainRisk: crude analysis failed: %s", exc)

        # 3. Feedstock-to-product spread compression
        try:
            naphtha_s = get_naphtha_series(days=14)
            c5_s = get_price_series("cracked_c5", "price", days=14)
            if naphtha_s is not None and c5_s is not None and len(naphtha_s) >= 2 and len(c5_s) >= 2:
                latest_spread = float(c5_s.iloc[-1]) - float(naphtha_s.iloc[-1])
                avg_spread = float(c5_s.mean()) - float(naphtha_s.mean())
                if avg_spread != 0:
                    compression_pct = (latest_spread - avg_spread) / abs(avg_spread) * 100
                    risk_factors.append({
                        "factor": "c5_naphtha_spread_compression",
                        "value": round(compression_pct, 1),
                        "latest_spread": round(latest_spread, 0),
                        "avg_spread": round(avg_spread, 0),
                        "status": "compressed" if compression_pct < -20 else "normal",
                    })
                    if compression_pct < -20:
                        alerts.append(
                            f"[MARGIN RISK] C5–Naphtha spread compressed {compression_pct:.0f}% vs 14d avg"
                        )
        except Exception as exc:
            logger.debug("SupplyChainRisk: spread analysis failed: %s", exc)

        # 4. Intelligence events with supply-chain category
        try:
            from backend.src.intelligence.event_registry import intelligence_event_registry
            events = intelligence_event_registry.list_recent_events(hours=24, limit=20)
            supply_events = [
                e for e in events
                if any(kw in str(e.get("event_type", "")).lower()
                       for kw in ("outage", "disruption", "shipping", "plant", "supply"))
            ]
            if supply_events:
                for evt in supply_events[:3]:
                    alerts.append(
                        f"[SUPPLY EVENT] {evt.get('headline', 'unknown')[:80]}"
                    )
                    if overall_risk == "low":
                        overall_risk = "medium"
                risk_factors.append({
                    "factor": "active_supply_events",
                    "count": len(supply_events),
                    "status": "active",
                })
        except Exception:
            pass

        return SkillResult(
            skill_name=self.name,
            summary=f"supply chain risk: {overall_risk} | {len(risk_factors)} factors, {len(alerts)} alert(s)",
            data={
                "overall_risk": overall_risk,
                "risk_factors": risk_factors,
            },
            alerts=alerts,
        )
