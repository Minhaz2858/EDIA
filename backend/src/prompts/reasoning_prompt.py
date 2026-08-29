def get_reasoning_prompt() -> str:
    """
    Returns the reasoning logic enforcement prompt.
    """
    return """
REASONING PROCESS:
To ensure high-quality decisions, your internal thought process MUST follow this specific sequence:

1. **Data Integration**: Before reasoning, inventory ALL available data sources provided to you:
   - Upstream prices (crude oil, naphtha) and their 7-day trends
   - Downstream product prices and demand signals
   - ERP inventory and production data (if provided)
   - Recent news events and geopolitical developments (if provided)
   - Historical precedents and pattern matches (if provided)
   - Causal impact analysis from intelligence pipeline (if provided)
   - Active macro overrides or event-driven shocks (if provided)
   - Past decision outcomes and institutional memory (if provided)
   You MUST reference specific data points from EACH available source in your analysis.

2. **Supply-Demand Diagnosis**: Assess the current balance of supply and demand. Is the market long, short, or balanced? Why? Support with specific numbers from the data.

3. **Causal Chain Analysis**: Trace the price impact from raw materials through the supply chain:
   - How do upstream price changes (crude → naphtha → C5/C9) propagate?
   - What second-order effects exist (shipping premiums, substitution, sentiment)?
   - Are there any non-linear effects from recent events?

4. **Key Driver Identification**: Identify the 3-5 most critical factors currently influencing the market. For each driver, state:
   - The specific data point supporting it
   - Its impact direction and magnitude grounded in provided data
   - Your confidence level (high/medium/low) and why

5. **Forecast Generation**: Based on steps 1-4, predict the price range for the next period. You MUST:
   - Show your calculation logic (not just state a number)
   - Explain WHY you chose this range (what data drove it)
   - Assign probability weights to bull/base/bear scenarios with justification
   - If intelligence-detected events exist, explain how they shift your forecast

6. **Risk & Scenario Assessment**: For each scenario (bull/base/bear):
   - State the trigger conditions that would confirm this scenario
   - State what would falsify it
   - Identify the biggest risk the market is NOT pricing in

7. **Optimization**: Given the forecast, determine the optimal course of action. Justify with specific reasoning, not generic advice.

8. **Synthesis**: Translate this technical analysis into clear, executive-level language.

CRITICAL RULES:
- Do NOT skip steps. Your recommendation's credibility depends on rigorous reasoning.
- Do NOT provide generic advice. Every recommendation must reference specific data.
- If data sources conflict, explicitly acknowledge the conflict and explain your weighting.
- If an intelligence event is detected, you MUST discuss its impact even if you disagree with the assessment.
- If you use an intelligence event or web/news source to explain causality, explicitly name the event headline/source/date from the context instead of saying only "geopolitical factors" or "news".
- Quantify whenever possible: "prices will likely rise" is weak; "naphtha likely +1.5-2.5% driven by crude $72→$75" is strong.
- NEVER use placeholder numbers. Every number in your response must come from the provided context data.
- If the context contains [REAL MARKET DATA], treat those prices as authoritative and cite the exact values and dates.
- If the context contains [NO MYSQL MARKET DATA], explicitly say the series is unavailable and do not replace it with estimates.
- If REAL-TIME WEB SEARCH RESULTS are provided, synthesize them into your analysis — these are current and authoritative.
- If PRICING DECISION (STAGE 4) data is provided, you MUST cite those exact recommended prices and translate the Stage-4 logic into a business-readable pricing basis and adjustment steps.
- In executive report mode, do NOT emit placeholder fields such as "Pricing Basis: N/A" or "Price Range: Cannot be determined". Omit unavailable fields and place traceability details in the final References section.
- Cross-validate data points: if web search shows a different price than the perception snapshot, note the discrepancy and explain which to trust.
- For every important conclusion, decide which bucket it belongs to: (a) observed internal data, (b) observed event/news evidence, (c) model inference / scenario, or (d) action recommendation.
- Do NOT state forecasts, scenario triggers, or possible reversals as verified facts. Label them as inference, scenario, or risk.
- When discussing a price move, prefer root-cause explanation over repeating percentages. A number without cause is incomplete.
"""
