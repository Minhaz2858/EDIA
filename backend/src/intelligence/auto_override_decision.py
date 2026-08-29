"""Auto-Override Decision — autonomous event-to-override pipeline.

When the intelligence layer detects a high-impact event, this module decides:
  1. injection_confidence ≥ 0.75 → auto-inject override (no human needed)
  2. 0.50 ≤ injection_confidence < 0.75 → flag for human review
  3. injection_confidence < 0.50 → log only, no override

The injection confidence is computed from:
  - Event certainty (confirmed > likely > possible > speculative)
  - Source credibility (tier_1 > tier_2 > social_media)
  - Historical accuracy of this event type
  - Magnitude of estimated price impact
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from backend.src.intelligence.event_types import ExtractedEvent
from backend.src.intelligence.causal_narrative_engine import CausalChainResult

logger = logging.getLogger(__name__)

AUTO_INJECT_THRESHOLD = 0.75
HUMAN_REVIEW_THRESHOLD = 0.50

_CERTAINTY_SCORES = {
    "confirmed": 0.95,
    "likely": 0.70,
    "possible": 0.45,
    "speculative": 0.20,
}

_CREDIBILITY_SCORES = {
    "tier_1": 0.95,
    "tier_2": 0.70,
    "social_media": 0.30,
}

# Historical accuracy by event type (what % of detected events materially affected prices)
_HISTORICAL_ACCURACY = {
    "geopolitical_supply_disruption": 0.80,
    "shipping_disruption": 0.75,
    "plant_outage": 0.85,
    "tariff_policy": 0.70,
    "demand_signal": 0.55,
    "opec_decision": 0.90,
    "sinopec_price_change": 0.95,
    "weather_event": 0.60,
    "currency_move": 0.50,
    "sanctions": 0.75,
}


@dataclass
class OverrideDecision:
    """Result of the auto-override evaluation."""

    action: str  # "auto_inject" | "human_review" | "log_only"
    injection_confidence: float  # 0.0 - 1.0
    event_id: str
    event_headline: str
    naphtha_shock_pct: float
    event_type: str
    description: str
    reasoning: str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


class AutoOverrideDecisionEngine:
    """Evaluates whether an event warrants automatic macro override injection."""

    def __init__(self):
        self._pending_reviews: list[OverrideDecision] = []
        self._auto_injected: list[OverrideDecision] = []

    def decide(
        self,
        event: ExtractedEvent,
        causal_chain: CausalChainResult,
    ) -> OverrideDecision:
        """Evaluate whether this event should auto-inject an override."""
        for existing in self._pending_reviews:
            if existing.event_id == event.event_id:
                return existing
        for existing in self._auto_injected:
            if existing.event_id == event.event_id:
                return existing

        confidence = self._compute_injection_confidence(event, causal_chain)
        naphtha_pct = causal_chain.naphtha_total_pct

        if confidence >= AUTO_INJECT_THRESHOLD:
            action = "auto_inject"
            reasoning = (
                f"High confidence ({confidence:.0%}): "
                f"certainty={event.certainty}, source={event.source_credibility}, "
                f"event_type accuracy={_HISTORICAL_ACCURACY.get(event.event_type, 0.5):.0%}. "
                f"Auto-injecting naphtha shock of {naphtha_pct:+.2f}%."
            )
        elif confidence >= HUMAN_REVIEW_THRESHOLD:
            action = "human_review"
            reasoning = (
                f"Medium confidence ({confidence:.0%}): requires human review. "
                f"certainty={event.certainty}, source={event.source_credibility}. "
                f"Proposed naphtha shock: {naphtha_pct:+.2f}%."
            )
        else:
            action = "log_only"
            reasoning = (
                f"Low confidence ({confidence:.0%}): logging only. "
                f"certainty={event.certainty}, source={event.source_credibility}."
            )

        decision = OverrideDecision(
            action=action,
            injection_confidence=round(confidence, 3),
            event_id=event.event_id,
            event_headline=event.headline,
            naphtha_shock_pct=round(naphtha_pct, 2),
            event_type=event.event_type,
            description=f"[{event.event_type}] {event.headline}",
            reasoning=reasoning,
            timestamp=datetime.utcnow().isoformat(),
        )

        if action == "auto_inject":
            self._execute_auto_inject(decision)
        elif action == "human_review":
            self._pending_reviews.append(decision)

        return decision

    def _compute_injection_confidence(
        self, event: ExtractedEvent, causal_chain: CausalChainResult
    ) -> float:
        """Blend multiple confidence signals into a single injection score."""
        certainty_score = _CERTAINTY_SCORES.get(event.certainty, 0.3)
        credibility_score = _CREDIBILITY_SCORES.get(event.source_credibility, 0.5)
        historical_accuracy = _HISTORICAL_ACCURACY.get(event.event_type, 0.5)

        # Impact magnitude: larger impacts need HIGHER confidence to auto-inject
        abs_impact = abs(causal_chain.naphtha_total_pct)
        impact_penalty = min(0.15, abs_impact * 0.03)  # up to -15% for huge impacts

        raw = (
            certainty_score * 0.35
            + credibility_score * 0.25
            + historical_accuracy * 0.25
            + event.impact_magnitude * 0.15
            - impact_penalty
        )
        return max(0.0, min(1.0, raw))

    def _execute_auto_inject(self, decision: OverrideDecision) -> None:
        """Auto-inject the macro override."""
        try:
            from backend.src.service.macro_override_service import set_override

            set_override(
                naphtha_shock_pct=decision.naphtha_shock_pct,
                event_type=decision.event_type,
                description=decision.description,
                ttl_hours=24,
            )
            self._auto_injected.append(decision)
            logger.info(
                "Auto-injected override: %s (conf=%.2f, shock=%+.2f%%)",
                decision.event_headline,
                decision.injection_confidence,
                decision.naphtha_shock_pct,
            )
        except Exception as e:
            logger.error("Failed to auto-inject override: %s", e)

    def get_pending_reviews(self) -> list[dict]:
        """Return events pending human review."""
        return [d.to_dict() for d in self._pending_reviews]

    def get_auto_injected(self) -> list[dict]:
        """Return events that were auto-injected."""
        return [d.to_dict() for d in self._auto_injected]

    def approve_pending(self, event_id: str) -> Optional[OverrideDecision]:
        """Human approves a pending review → inject the override."""
        for i, d in enumerate(self._pending_reviews):
            if d.event_id == event_id:
                d.action = "approved_and_injected"
                self._execute_auto_inject(d)
                self._pending_reviews.pop(i)
                return d
        return None

    def reject_pending(self, event_id: str) -> Optional[OverrideDecision]:
        """Human rejects a pending review → discard."""
        for i, d in enumerate(self._pending_reviews):
            if d.event_id == event_id:
                d.action = "rejected"
                self._pending_reviews.pop(i)
                return d
        return None


# Module-level singleton
auto_override_engine = AutoOverrideDecisionEngine()
