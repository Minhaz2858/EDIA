"""Weather / Logistics Impact Predictor – Background Skill #11.

Predicts supply interruptions from external operational factors —
typhoon season logistics, winter heating demand spikes, port congestion,
and refinery turnaround schedules.

Petrochemical context:  C5/C9 products are shipped by tanker and road from
refineries (Yangzi, Maoming, Shanghai Petrochemical).  Weather events
(typhoons on East China Sea, winter ice in Bohai Bay) and scheduled
refinery turnarounds (春检/秋检) create predictable supply windows that
should be priced in *before* they hit spot markets.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# Known refinery turnaround windows (approximate, recurs annually)
# month_start, month_end, description, affected_products
_TURNAROUND_CALENDAR: list[dict[str, Any]] = [
    {
        "month_start": 3,
        "month_end": 4,
        "label": "Spring turnaround (春检)",
        "description": "Major crackers undergo spring maintenance, reducing C5/C9 supply",
        "affected_products": ["cracked_c5", "cracked_c9", "isoprene", "piperylene", "dcpd"],
        "supply_impact": "reduction",
        "magnitude": "medium",
    },
    {
        "month_start": 9,
        "month_end": 10,
        "label": "Autumn turnaround (秋检)",
        "description": "Secondary maintenance window, shorter than spring",
        "affected_products": ["cracked_c5", "cracked_c9"],
        "supply_impact": "reduction",
        "magnitude": "small",
    },
    {
        "month_start": 1,
        "month_end": 2,
        "label": "CNY shutdown window",
        "description": "Reduced logistics and downstream demand during Spring Festival",
        "affected_products": ["cracked_c5", "cracked_c9", "isoprene", "piperylene", "dcpd", "sis", "c5_resin"],
        "supply_impact": "demand_reduction",
        "magnitude": "large",
    },
]

# Typhoon season in East China (historically Jun–Oct)
_TYPHOON_SEASON_MONTHS = {6, 7, 8, 9, 10}

# Winter heating demand months (affects naphtha allocation)
_WINTER_HEATING_MONTHS = {11, 12, 1, 2}


class WeatherLogisticsPredictor(BackgroundSkill):
    name = "weather_logistics_predictor"
    interval_minutes = 360  # every 6 hours

    def run(self) -> SkillResult:
        now = datetime.now(timezone.utc)
        month = now.month
        alerts: list[str] = []
        active_events: list[dict[str, Any]] = []

        # 1. Check turnaround calendar
        for window in _TURNAROUND_CALENDAR:
            if window["month_start"] <= month <= window["month_end"]:
                event = {
                    "type": "refinery_turnaround",
                    "label": window["label"],
                    "description": window["description"],
                    "affected_products": window["affected_products"],
                    "supply_impact": window["supply_impact"],
                    "magnitude": window["magnitude"],
                    "active": True,
                }
                active_events.append(event)
                alerts.append(
                    f"[TURNAROUND] {window['label']} is active — "
                    f"{window['supply_impact']} expected for "
                    f"{', '.join(window['affected_products'][:3])}"
                )

            # Approaching window (within 30 days)
            elif month == window["month_start"] - 1 or (
                window["month_start"] == 1 and month == 12
            ):
                event = {
                    "type": "upcoming_turnaround",
                    "label": window["label"],
                    "description": f"Approaching: {window['description']}",
                    "affected_products": window["affected_products"],
                    "supply_impact": window["supply_impact"],
                    "magnitude": window["magnitude"],
                    "active": False,
                    "starts_in": "~30 days",
                }
                active_events.append(event)
                alerts.append(
                    f"[UPCOMING] {window['label']} approaching — "
                    "consider pre-positioning inventory"
                )

        # 2. Typhoon season logistics risk
        if month in _TYPHOON_SEASON_MONTHS:
            event = {
                "type": "typhoon_season",
                "label": "East China typhoon season",
                "description": (
                    "Logistics disruption risk for coastal/port movements. "
                    "Tanker delays possible for Shanghai/Ningbo/Zhoushan routes."
                ),
                "affected_products": ["cracked_c5", "cracked_c9"],
                "supply_impact": "logistics_delay",
                "magnitude": "medium" if month in {7, 8, 9} else "small",
                "active": True,
            }
            active_events.append(event)
            alerts.append(
                "[WEATHER] Typhoon season active — monitor shipping delays "
                "for coastal C5/C9 movements"
            )

        # 3. Winter heating demand (naphtha allocation competition)
        if month in _WINTER_HEATING_MONTHS:
            event = {
                "type": "winter_heating_season",
                "label": "Winter heating demand",
                "description": (
                    "Naphtha allocation shifts toward heating use, reducing availability "
                    "for steam crackers. C5/C9 feedstock tightening possible."
                ),
                "affected_products": ["cracked_c5", "cracked_c9"],
                "supply_impact": "feedstock_tightening",
                "magnitude": "medium",
                "active": True,
            }
            active_events.append(event)
            alerts.append(
                "[SEASONAL] Winter heating season — naphtha allocation competition "
                "may tighten C5/C9 feedstock supply"
            )

        # 4. Check intelligence events for weather-related items
        try:
            from backend.src.intelligence.event_registry import intelligence_event_registry
            events = intelligence_event_registry.list_recent_events(hours=48, limit=20)
            weather_keywords = ["typhoon", "台风", "暴雨", "flood", "洪水", "冰冻", "freeze", "port", "码头"]
            for evt in events:
                combined = f"{evt.get('headline', '')} {evt.get('event_type', '')}".lower()
                if any(kw in combined for kw in weather_keywords):
                    active_events.append({
                        "type": "intelligence_weather_event",
                        "label": evt.get("headline", "")[:80],
                        "source": "intelligence_registry",
                        "direction": evt.get("direction", ""),
                        "certainty": evt.get("certainty", ""),
                    })
                    alerts.append(
                        f"[WEATHER EVENT] {evt.get('headline', '')[:80]}"
                    )
        except Exception:
            pass

        return SkillResult(
            skill_name=self.name,
            summary=(
                f"{len(active_events)} active weather/logistics factor(s) for month {month}"
            ),
            data={
                "month": month,
                "active_events": active_events,
            },
            alerts=alerts,
        )
