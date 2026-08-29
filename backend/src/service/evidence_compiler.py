from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EvidenceItem:
    source: str
    signal: str
    direction: str  # bullish | bearish | neutral
    weight: float
    detail: str

    def as_line(self) -> str:
        arrow = (
            "+"
            if self.direction == "bullish"
            else ("-" if self.direction == "bearish" else "=")
        )
        return f"[{self.source}] {arrow} {self.signal} (w={self.weight:.2f}) — {self.detail}"


class EvidenceCompiler:
    """Compile competing evidence into a deterministic bundle for the LLM.

    This class keeps the reasoning contract structured and auditable while still
    allowing the LLM to produce natural language synthesis.
    """

    def compile(
        self,
        product_id: str,
        perception_snapshot: dict[str, Any] | None,
        diagnosis: dict[str, Any] | None,
        forecast: dict[str, Any] | None,
        pricing_decision: dict[str, Any] | None,
        intelligence_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        items: list[EvidenceItem] = []

        # Perception / market movement
        p = (perception_snapshot or {}).get("products", {}).get(product_id, {})
        chg_7d = float(p.get("change_7d_pct", 0.0) or 0.0)
        if chg_7d > 0.4:
            items.append(
                EvidenceItem(
                    source="perception",
                    signal="price_momentum_7d",
                    direction="bullish",
                    weight=min(1.0, abs(chg_7d) / 5.0),
                    detail=f"7d change {chg_7d:+.2f}%",
                )
            )
        elif chg_7d < -0.4:
            items.append(
                EvidenceItem(
                    source="perception",
                    signal="price_momentum_7d",
                    direction="bearish",
                    weight=min(1.0, abs(chg_7d) / 5.0),
                    detail=f"7d change {chg_7d:+.2f}%",
                )
            )

        # Diagnosis net signal
        d = diagnosis or {}
        net_signal = str(d.get("net_signal", "neutral") or "neutral").lower()
        if net_signal in {"bullish", "bearish"}:
            items.append(
                EvidenceItem(
                    source="diagnosis",
                    signal="graph_diagnosis",
                    direction=net_signal,
                    weight=0.9,
                    detail=f"market_regime={d.get('market_regime', 'unknown')}",
                )
            )

        # Forecast directional evidence
        f = forecast or {}
        pct_7d = 0.0
        try:
            s7 = f.get("scenarios", {}).get("7d", {}).get("base", {})
            low = float(s7.get("low", 0.0) or 0.0)
            high = float(s7.get("high", 0.0) or 0.0)
            latest = float(f.get("latest_market_price", 0.0) or 0.0)
            if latest > 0 and high > 0 and low > 0:
                pct_7d = (((low + high) / 2.0) - latest) / latest * 100
        except Exception:
            pct_7d = 0.0

        if pct_7d > 0.4:
            items.append(
                EvidenceItem(
                    source="forecast",
                    signal="base_7d_mid",
                    direction="bullish",
                    weight=min(1.0, abs(pct_7d) / 4.0),
                    detail=f"7d base midpoint {pct_7d:+.2f}% vs latest",
                )
            )
        elif pct_7d < -0.4:
            items.append(
                EvidenceItem(
                    source="forecast",
                    signal="base_7d_mid",
                    direction="bearish",
                    weight=min(1.0, abs(pct_7d) / 4.0),
                    detail=f"7d base midpoint {pct_7d:+.2f}% vs latest",
                )
            )

        # Pricing recommendation vs market
        pr = pricing_decision or {}
        rec = float(pr.get("recommended_price", 0.0) or 0.0)
        mkt = float(pr.get("latest_market_price", 0.0) or 0.0)
        if rec > 0 and mkt > 0:
            dlt_pct = (rec - mkt) / mkt * 100
            if dlt_pct > 0.25:
                items.append(
                    EvidenceItem(
                        source="pricing",
                        signal="recommended_vs_market",
                        direction="bullish",
                        weight=min(1.0, abs(dlt_pct) / 2.0),
                        detail=f"recommended {rec:.0f} vs market {mkt:.0f} ({dlt_pct:+.2f}%)",
                    )
                )
            elif dlt_pct < -0.25:
                items.append(
                    EvidenceItem(
                        source="pricing",
                        signal="recommended_vs_market",
                        direction="bearish",
                        weight=min(1.0, abs(dlt_pct) / 2.0),
                        detail=f"recommended {rec:.0f} vs market {mkt:.0f} ({dlt_pct:+.2f}%)",
                    )
                )

        # Intelligence contribution
        intel = intelligence_context or {}
        for analysis in (intel.get("causal_analyses") or [])[:3]:
            product_impacts = analysis.get("product_impacts") or {}
            impact = product_impacts.get(product_id) or {}
            pct_impact = float(impact.get("pct_impact", 0.0) or 0.0)
            if abs(pct_impact) < 0.15:
                continue
            direction = "bullish" if pct_impact > 0 else "bearish"
            items.append(
                EvidenceItem(
                    source="intelligence",
                    signal="live_event_causal",
                    direction=direction,
                    weight=min(1.0, abs(pct_impact) / 4.0),
                    detail=f"{analysis.get('event_headline', 'event')[:90]} ({pct_impact:+.2f}%)",
                )
            )

        bullish = [it for it in items if it.direction == "bullish"]
        bearish = [it for it in items if it.direction == "bearish"]
        neutral = [it for it in items if it.direction == "neutral"]

        bull_score = sum(it.weight for it in bullish)
        bear_score = sum(it.weight for it in bearish)

        if bull_score > bear_score + 0.2:
            dominant = "bullish"
        elif bear_score > bull_score + 0.2:
            dominant = "bearish"
        else:
            dominant = "mixed"

        contradictions = []
        if bullish and bearish:
            contradictions.append(
                "Evidence contains both upward and downward signals; recommendation depends on weighting."
            )

        total_score = bull_score + bear_score + sum(it.weight for it in neutral)
        residual_uncertainty = "high"
        if total_score >= 2.0 and not contradictions:
            residual_uncertainty = "low"
        elif total_score >= 1.2:
            residual_uncertainty = "medium"

        return {
            "product_id": product_id,
            "dominant_view": dominant,
            "bullish_score": round(bull_score, 3),
            "bearish_score": round(bear_score, 3),
            "residual_uncertainty": residual_uncertainty,
            "evidence_for_rise": [it.as_line() for it in bullish],
            "evidence_for_fall": [it.as_line() for it in bearish],
            "neutral_evidence": [it.as_line() for it in neutral],
            "contradictions": contradictions,
        }

    def to_prompt_text(self, evidence_bundle: dict[str, Any] | None) -> str:
        if not evidence_bundle:
            return ""

        rise = evidence_bundle.get("evidence_for_rise", [])
        fall = evidence_bundle.get("evidence_for_fall", [])
        neutral = evidence_bundle.get("neutral_evidence", [])
        contradictions = evidence_bundle.get("contradictions", [])

        lines = [
            "EVIDENCE COMPILER:",
            f"Dominant View: {evidence_bundle.get('dominant_view', 'mixed')}",
            f"Scores: bull={evidence_bundle.get('bullish_score', 0):.2f}, bear={evidence_bundle.get('bearish_score', 0):.2f}",
            f"Residual Uncertainty: {evidence_bundle.get('residual_uncertainty', 'high')}",
        ]

        if rise:
            lines.append("Evidence For Rise:")
            lines.extend(f"  - {line}" for line in rise[:5])
        if fall:
            lines.append("Evidence For Fall:")
            lines.extend(f"  - {line}" for line in fall[:5])
        if neutral:
            lines.append("Neutral Evidence:")
            lines.extend(f"  - {line}" for line in neutral[:3])
        if contradictions:
            lines.append("Contradictions:")
            lines.extend(f"  - {line}" for line in contradictions[:3])

        return "\n".join(lines)


evidence_compiler = EvidenceCompiler()
