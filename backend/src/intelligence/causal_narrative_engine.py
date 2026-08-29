"""Causal Narrative Engine — multi-hop causal chain reasoning from events to product impacts.

Given any event (Strait closure, tariff, plant shutdown), traces the exact
causal path from event → upstream commodity → C5/C9 products with:
1. Numerical impact estimates per product (CNY/ton)
2. Human-readable causal narrative
3. Confidence decay over time horizons
4. Historical precedent comparison
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from backend.src.intelligence.event_types import ExtractedEvent
from backend.src.intelligence.event_impact_db import EventImpactDB, event_impact_db
from backend.src.intelligence.second_order_effects import (
    compute_total_second_order_pct,
    get_product_specific_effects,
    get_second_order_effects,
)

logger = logging.getLogger(__name__)

# ── Causal chain elasticities (from knowledge graph) ──────────────────────

# Naphtha → product elasticities (effective, after tier dampening)
_NAPHTHA_ELASTICITIES: dict[str, float] = {
    "mixed_c5": 0.82,
    "isoprene": 0.82 * 0.88 * 0.85,  # via mixed_c5, tier-2 dampening
    "piperylene": 0.82 * 0.85 * 0.85,
    "dcpd": 0.82 * 0.78 * 0.85,
    "c5_resin": 0.82 * 0.85 * 0.70 * 0.72,  # tier-3
    "sis_rubber": 0.82 * 0.88 * 0.65 * 0.72,
    "mixed_c9": 0.70,
    "c9_resin": 0.70 * 0.75 * 0.85,
}

# Crude → naphtha conversion factor (USD/bbl → CNY/ton)
_CRUDE_TO_NAPHTHA_FACTOR = 1.35  # $1/bbl crude ≈ $1.35/ton naphtha impact

# Average naphtha price for CNY conversion
_NAPHTHA_BASE_USD = 252.0  # ~$252/ton Asian naphtha benchmark
_USD_CNY = 7.25

# Approx current price levels for CNY impact calculation
_APPROX_PRICES_CNY: dict[str, float] = {
    "mixed_c5": 5000,
    "isoprene": 9700,
    "piperylene": 8700,
    "dcpd": 6500,
    "c5_resin": 8200,
    "sis_rubber": 13000,
    "mixed_c9": 4500,
    "c9_resin": 7500,
}


@dataclass
class ProductImpact:
    product_id: str
    pct_impact: float  # % change expected
    cny_impact: float  # absolute CNY/ton change
    causal_path: str  # "crude → naphtha → C5 → isoprene"
    elasticity_used: float
    second_order_pct: float  # additional % from non-linear effects


@dataclass
class CausalChainResult:
    event: dict  # the triggering event (serializable)
    entry_commodity: str  # e.g. "crude_oil"
    primary_impact_pct: float  # e.g. crude +10%
    naphtha_total_pct: float  # with second-order effects
    product_impacts: dict[str, dict]  # product_id → impact details
    causal_narrative: str  # LLM-generated human-readable narrative
    confidence_by_horizon: dict  # "3d": 0.85, "7d": 0.75, "30d": 0.55
    historical_precedents: dict  # from EventImpactDB
    recommended_override_type: str  # event_type for macro_override
    second_order_summary: dict  # second-order effects applied

    def to_dict(self) -> dict:
        return asdict(self)


class CausalNarrativeEngine:
    """Traces causal chains from events through the supply chain to product-level impacts."""

    def __init__(self, llm_service, impact_db: Optional[EventImpactDB] = None):
        self.llm = llm_service
        self.impact_db = impact_db or event_impact_db

    def reason_through_event(self, event: ExtractedEvent) -> CausalChainResult:
        """Full multi-hop causal reasoning from event to product impacts."""

        # Step 1: Map event to entry commodity
        entry_commodity = self._map_event_to_entry(event)

        # Step 2: Quantify primary commodity impact
        primary_pct = self._quantify_primary_impact(event)

        # Step 3: Compute naphtha impact (including second-order effects)
        naphtha_base_pct = self._crude_to_naphtha_pct(primary_pct, event)
        second_order_extra = compute_total_second_order_pct(event.event_type)
        naphtha_total_pct = naphtha_base_pct + second_order_extra * 100

        # Step 4: Look up historical precedents
        precedents = self.impact_db.find_similar_events(
            event_type=event.event_type,
            magnitude=event.magnitude_estimate,
            geographic_scope=event.geographic_scope,
            k=3,
        )

        # Step 5: Propagate through supply chain
        product_impacts = {}
        product_specific_effects = get_product_specific_effects(event.event_type)

        for product_id, elasticity in _NAPHTHA_ELASTICITIES.items():
            pct_impact = naphtha_total_pct * elasticity / 100  # as fraction
            # Add product-specific second-order effects
            product_extra = product_specific_effects.get(product_id, 0.0)
            total_pct = pct_impact + product_extra

            approx_price = _APPROX_PRICES_CNY.get(product_id, 5000)
            cny_impact = round(approx_price * total_pct, 0)

            product_impacts[product_id] = {
                "pct_impact": round(total_pct * 100, 2),
                "cny_impact": cny_impact,
                "causal_path": f"event → {entry_commodity} → naphtha → {product_id}",
                "elasticity_used": round(elasticity, 3),
                "second_order_pct": round(product_extra * 100, 2),
            }

        # Step 6: Generate causal narrative via LLM
        narrative = self._generate_narrative(
            event, primary_pct, naphtha_total_pct, product_impacts, precedents
        )

        # Step 7: Compute confidence decay over horizons
        confidence_by_horizon = self._compute_confidence_decay(event)

        # Step 8: Determine recommended override type
        override_type = self._suggest_override_type(event)

        return CausalChainResult(
            event=event.to_dict(),
            entry_commodity=entry_commodity,
            primary_impact_pct=round(primary_pct, 2),
            naphtha_total_pct=round(naphtha_total_pct, 2),
            product_impacts=product_impacts,
            causal_narrative=narrative,
            confidence_by_horizon=confidence_by_horizon,
            historical_precedents=precedents,
            recommended_override_type=override_type,
            second_order_summary=get_second_order_effects(event.event_type),
        )

    # ── Internal methods ──────────────────────────────────────────────────

    def _map_event_to_entry(self, event: ExtractedEvent) -> str:
        """Map event to the primary commodity it enters the supply chain through."""
        type_to_entry = {
            "geopolitical_supply_disruption": "crude_oil",
            "shipping_disruption": "naphtha",
            "plant_outage": "mixed_c5",
            "sinopec_price_change": "mixed_c5",
            "tariff_policy": "mixed_c5",
            "demand_signal": "downstream",
            "opec_decision": "crude_oil",
            "weather_event": "naphtha",
            "currency_move": "naphtha",
            "sanctions": "crude_oil",
        }
        return type_to_entry.get(event.event_type, "crude_oil")

    def _quantify_primary_impact(self, event: ExtractedEvent) -> float:
        """Estimate crude/naphtha % change from the event magnitude."""
        magnitude_to_pct = {
            "small": 1.5,
            "medium": 4.0,
            "large": 8.0,
            "extreme": 12.0,
        }
        base_pct = magnitude_to_pct.get(event.magnitude_estimate, 3.0)
        if event.direction == "bearish":
            base_pct = -base_pct
        elif event.direction in ("neutral", "uncertain"):
            base_pct *= 0.3

        # Adjust for certainty
        certainty_factor = {
            "confirmed": 1.0,
            "likely": 0.80,
            "rumored": 0.50,
            "speculative": 0.30,
        }
        base_pct *= certainty_factor.get(event.certainty, 0.5)

        return base_pct

    def _crude_to_naphtha_pct(self, crude_pct: float, event: ExtractedEvent) -> float:
        """Convert crude % move to naphtha % move."""
        entry = self._map_event_to_entry(event)
        if entry == "crude_oil":
            # Crude → naphtha via conversion factor
            return crude_pct * _CRUDE_TO_NAPHTHA_FACTOR
        elif entry in ("naphtha", "mixed_c5"):
            return crude_pct  # Direct impact
        else:
            return crude_pct * 0.5  # Indirect (demand-side)

    def _compute_confidence_decay(self, event: ExtractedEvent) -> dict:
        """Confidence decays over longer time horizons."""
        base = {"confirmed": 0.90, "likely": 0.75, "rumored": 0.55, "speculative": 0.35}
        initial = base.get(event.certainty, 0.6)

        # Faster decay for shorter-lived events
        horizon_to_decay = {
            "immediate": 0.02,
            "short": 0.05,
            "medium": 0.08,
        }
        decay_per_week = horizon_to_decay.get(event.time_horizon, 0.06)

        return {
            "3d": round(initial, 2),
            "7d": round(initial - decay_per_week, 2),
            "14d": round(initial - decay_per_week * 2, 2),
            "30d": round(max(initial - decay_per_week * 4, 0.20), 2),
        }

    def _suggest_override_type(self, event: ExtractedEvent) -> str:
        """Map event to the appropriate macro_override event_type."""
        mapping = {
            "geopolitical_supply_disruption": "crude_spike",
            "shipping_disruption": "crude_spike",
            "opec_decision": "crude_spike",
            "tariff_policy": "tariff_shock",
            "sanctions": "tariff_shock",
            "plant_outage": "force_majeure",
            "weather_event": "force_majeure",
            "sinopec_price_change": "manual",
            "demand_signal": "manual",
            "currency_move": "manual",
        }
        return mapping.get(event.event_type, "manual")

    def _generate_narrative(
        self,
        event: ExtractedEvent,
        primary_pct: float,
        naphtha_pct: float,
        product_impacts: dict,
        precedents: dict,
    ) -> str:
        """Generate human-readable causal narrative using LLM."""
        # Build impact table
        impact_lines = []
        for pid, imp in sorted(
            product_impacts.items(), key=lambda x: abs(x[1]["cny_impact"]), reverse=True
        ):
            impact_lines.append(
                f"  {pid}: {imp['cny_impact']:+.0f} CNY/ton ({imp['pct_impact']:+.1f}%) via {imp['causal_path']}"
            )
        impact_table = "\n".join(impact_lines)

        # Format precedents
        prec_lines = []
        for p in precedents.get("similar_events", [])[:3]:
            prec_lines.append(
                f"  - {p['date']} {p['headline']}: crude {p.get('crude_impact_pct', 0):+.1f}%, "
                f"C5 {p.get('c5_impact_cny', 0):+.0f} CNY, resolved in {p.get('duration_days', '?')} days. "
                f"Lesson: {p.get('key_lesson', 'N/A')}"
            )
        prec_text = (
            "\n".join(prec_lines) if prec_lines else "  No close precedents found."
        )

        prompt = f"""You are a senior C5/C9 petrochemical market strategist at Ecisco.
A market event has just occurred. Write a concise but thorough market intelligence briefing.

EVENT: {event.headline}
Type: {event.event_type} | Direction: {event.direction} | Magnitude: {event.magnitude_estimate} | Certainty: {event.certainty}
Primary commodity impact: {primary_pct:+.1f}%
Naphtha total impact (incl. second-order): {naphtha_pct:+.1f}%

PRODUCT IMPACTS:
{impact_table}

HISTORICAL PRECEDENTS:
{prec_text}

Write a 3-paragraph briefing:

Paragraph 1 — WHAT HAPPENED AND WHY IT MATTERS:
Explain the event in supply chain terms. Be specific about the pathway from event to product prices.

Paragraph 2 — QUANTIFIED IMPACT PER PRODUCT:
State expected price changes in CNY/ton with reasoning. Cite the causal chain. Reference the closest historical precedent.

Paragraph 3 — WHAT ECISCO SHOULD DO RIGHT NOW:
Specific, actionable recommendations. Which products to adjust, by how much, what triggers would change the recommendation.

Write in professional market analysis style. Use specific CNY/ton numbers. Be decisive, not vague."""

        try:
            narrative = self.llm.generate_completion(
                "You are a senior petrochemical market strategist providing actionable intelligence briefings.",
                prompt,
            )
            return narrative
        except Exception as e:
            logger.warning("Narrative generation failed: %s", e)
            # Fallback: return structured summary
            return (
                f"EVENT: {event.headline}\n"
                f"Impact: Naphtha {naphtha_pct:+.1f}%\n"
                f"Product impacts:\n{impact_table}\n"
                f"Historical precedents:\n{prec_text}"
            )


# Module-level factory (initialized with LLM when server starts)
_engine: Optional[CausalNarrativeEngine] = None


def get_causal_engine() -> Optional[CausalNarrativeEngine]:
    return _engine


def init_causal_engine(llm_service) -> CausalNarrativeEngine:
    global _engine
    _engine = CausalNarrativeEngine(llm_service)
    return _engine
