"""Regulatory / Policy Monitor – Background Skill #8.

Scans intelligence events for regulatory, tariff, environmental-policy,
and sanctions-related signals that can rapidly shift C5/C9 pricing dynamics.

Petrochemical context:  Chinese chemical markets are heavily influenced by:
  - SINOPEC / PetroChina posted-price adjustments (quasi-regulatory)
  - Export tariff changes on C5/C9 fractions
  - Environmental inspection campaigns (限产)
  - Anti-dumping duties on imported styrene / resin
  - EU CBAM and cross-border carbon tariffs
  - Sanctions affecting Middle-East feedstock flows

These policy moves can change the market structure overnight.  The skill
monitors the intelligence event registry and flags relevant policy events.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# Event types and keywords that signal regulatory / policy risk
_POLICY_EVENT_TYPES = {
    "tariff_policy",
    "sanctions",
    "sinopec_price_change",
    "regulatory",
    "environmental",
    "policy",
}

_POLICY_KEYWORDS = [
    "tariff", "duty", "sanction", "ban", "quota",
    "限产", "环保", "关税", "制裁", "反倾销",
    "emission", "carbon", "CBAM", "inspection",
    "export restriction", "import ban",
    "挂牌价调整", "国有企业调价",
    "限电", "能耗双控",
]


def _event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return dict(event)

    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload

    return {
        "event_id": getattr(event, "event_id", ""),
        "event_type": getattr(event, "event_type", ""),
        "headline": getattr(event, "headline", ""),
        "full_text": getattr(event, "full_text", ""),
        "impact_magnitude": getattr(event, "impact_magnitude", 0.0),
        "certainty": getattr(event, "certainty", ""),
        "direction": getattr(event, "direction", ""),
        "affected_commodities": getattr(event, "affected_commodities", []),
        "time_horizon": getattr(event, "time_horizon", ""),
        "detected_at": getattr(event, "detected_at", ""),
    }


class RegulatoryPolicyMonitor(BackgroundSkill):
    name = "regulatory_policy_monitor"
    interval_minutes = 60  # every hour

    def run(self) -> SkillResult:
        try:
            from backend.src.intelligence.event_registry import intelligence_event_registry
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"cannot import event_registry: {exc}",
            )

        alerts: list[str] = []
        policy_events: list[dict[str, Any]] = []

        try:
            events = intelligence_event_registry.list_recent_events(
                hours=72, limit=50
            )
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"event query failed: {exc}",
            )

        for event in events:
            event_data = _event_payload(event)
            event_type = str(event_data.get("event_type", "")).lower()
            headline = str(event_data.get("headline", "")).lower()
            full_text = str(event_data.get("full_text", "")).lower()
            combined = f"{event_type} {headline} {full_text}"

            is_policy = (
                event_type in _POLICY_EVENT_TYPES
                or any(kw in combined for kw in _POLICY_KEYWORDS)
            )

            if not is_policy:
                continue

            # Assess severity
            impact = event_data.get("impact_magnitude", 0)
            if isinstance(impact, (int, float)):
                impact = float(impact)
            else:
                impact = 0.3

            certainty = str(event_data.get("certainty", "")).lower()
            direction = str(event_data.get("direction", "")).lower()

            severity = "HIGH" if impact > 0.6 else "MEDIUM" if impact > 0.35 else "LOW"

            policy_event = {
                "event_id": event_data.get("event_id", ""),
                "event_type": event_data.get("event_type", ""),
                "headline": event_data.get("headline", ""),
                "direction": direction,
                "certainty": certainty,
                "impact_magnitude": round(impact, 3),
                "severity": severity,
                "affected_commodities": event_data.get("affected_commodities", []),
                "time_horizon": event_data.get("time_horizon", ""),
                "detected_at": event_data.get("detected_at", ""),
            }
            policy_events.append(policy_event)

            if severity in ("HIGH", "MEDIUM"):
                emoji = "🔴" if severity == "HIGH" else "🟠"
                alerts.append(
                    f"[POLICY {emoji}] {event_data.get('headline', 'Unknown')[:80]} "
                    f"| certainty: {certainty} | direction: {direction} | impact: {impact:.2f}"
                )

        # Categorise by type
        by_type: dict[str, int] = {}
        for pe in policy_events:
            t = pe["event_type"]
            by_type[t] = by_type.get(t, 0) + 1

        return SkillResult(
            skill_name=self.name,
            summary=(
                f"{len(policy_events)} policy/regulatory event(s) in last 72h | "
                f"types: {dict(by_type)} | {len(alerts)} alert(s)"
            ),
            data={
                "policy_events": policy_events,
                "by_type": by_type,
            },
            alerts=alerts,
        )
