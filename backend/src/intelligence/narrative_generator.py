"""Narrative Generator — LLM prompt templates for different event types.

Each event type has a specialized prompt that guides the LLM to produce
a narrative appropriate for that class of market event.
"""

from __future__ import annotations


NARRATIVE_PROMPT_TEMPLATES: dict[str, str] = {
    "geopolitical_supply_disruption": """You are a senior Ecisco market strategist. A geopolitical supply disruption has just occurred.

Event: {event_headline}
Crude impact: {crude_pct:+.1f}%
Naphtha impact: {naphtha_pct:+.1f}% (including shipping and second-order effects)
Product impacts:
{product_impact_table}

Historical precedent: {closest_precedent}

Write a 4-part forecast narrative:

PART 1 — WHY THIS MATTERS FOR OUR SUPPLY CHAIN (3-4 sentences):
Trace the exact causal path from the geopolitical event to our products.
Be specific about which geographic chokepoints, shipping routes, and upstream commodities are affected.

PART 2 — QUANTIFIED IMPACT (one line per product):
State: "[Product]: Expected [+/- CNY] impact because [one-sentence reason]."
Always include the specific causal mechanism, not just the number.

PART 3 — SCENARIO ANALYSIS:
Bull/Base/Bear with probability, price target, and the specific condition that determines which scenario plays out.

PART 4 — IMMEDIATE ACTIONS (numbered list):
Specific steps Ecisco should take in the next 24-48 hours.
Include: which prices to change, by how much, and what to watch as confirmation.

Write with the confidence of an expert. Be specific. Use CNY/ton numbers.""",
    "tariff_policy": """A tariff policy change has been announced affecting the chemical industry.

Event: {event_headline}
Affected products: {affected_products}
Impact assessment: {product_impact_table}
Historical tariff precedents: {closest_precedent}

Write a forecast narrative covering:
- Which products face demand destruction vs. benefit from import substitution
- How tariff cost flows through the supply chain
- The time profile: immediate panic vs. structural adjustment
- Ecisco-specific recommendation: which customer segments to prioritize

Be frank about uncertainty. Tariff impacts are harder to model than physical supply shocks.""",
    "plant_outage": """A significant plant shutdown or production outage has been reported.

Event: {event_headline}
Products affected: {affected_products}
Impact: {product_impact_table}
Precedent: {closest_precedent}

Write a narrative explaining:
- How much this tightens physical supply in the relevant market
- Whether Ecisco can capture the supply gap (inventory/capacity question)
- Price ceiling this creates vs. competing supply sources
- How long before the premium fades (plant restoration timeline)""",
    "sinopec_price_change": """SINOPEC has announced a pricing change.

Event: {event_headline}
Impact: {product_impact_table}
Market follow probability: 92% historical

Write a narrative explaining:
- How this changes Ecisco's positioning vs. SINOPEC listing
- Whether our current discount should narrow or widen
- What this signals about SINOPEC's view of the market
- Recommended price adjustments and timing""",
    "demand_signal": """A significant demand signal has been detected.

Event: {event_headline}
Downstream impact: {product_impact_table}
Precedent: {closest_precedent}

Write a narrative explaining:
- Which downstream sectors are driving the signal
- Time lag between demand signal and price response
- Whether this is seasonal/structural/one-off
- Ecisco inventory and production implications""",
    "default": """A market event has been detected.

Event: {event_headline}
Impact assessment: {product_impact_table}
Historical comparison: {closest_precedent}

Provide a brief market intelligence briefing covering:
1. What happened and why it matters for C5/C9 markets
2. Expected price impact per product (CNY/ton)
3. Recommended actions for Ecisco
4. Key risks and what to watch""",
}


def get_narrative_template(event_type: str) -> str:
    """Return the appropriate narrative template for an event type."""
    return NARRATIVE_PROMPT_TEMPLATES.get(
        event_type, NARRATIVE_PROMPT_TEMPLATES["default"]
    )
