"""
Confidence scoring for decisions and forecasts.
Combines data quality, evidence strength, and model tuning to produce
a 0-1 confidence score with uncertainty breakdown.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import math


@dataclass
class ConfidenceScore:
    """Confidence and uncertainty metrics for a forecast or decision."""

    # Overall confidence (0-1)
    overall: float

    # Breakdown by source
    data_quality: float  # How much historical data? Data gaps?
    evidence_strength: float  # How aligned are competing signals?
    model_stability: float  # How consistent is model across different regimes?
    macro_uncertainty: float  # How volatile is macro environment?

    # Uncertainty range
    p25: float  # 25th percentile (more confident lower bound)
    p50: float  # Median / base case
    p75: float  # 75th percentile (more confident upper bound)

    # Human-readable
    uncertainty_source: (
        str  # "macro_volatility" | "supply_shock" | "data_gap" | "demand_uncertainty"
    )
    confidence_label: str  # "HIGH" | "MEDIUM" | "LOW"
    lookback_mape: Optional[float] = None  # Mean absolute percentage error on backtests


class ConfidenceScorer:
    """Compute confidence scores based on evidence, data, and model performance."""

    def score_forecast(
        self,
        product_id: str,
        evidence_bundle: Dict[str, Any],
        diagnosis: Dict[str, Any],
        forecast: Dict[str, Any],
        perception_snapshot: Dict[str, Any],
        macro_context: Dict[str, Any],
        lookback_mape: Optional[float] = None,
    ) -> ConfidenceScore:
        """
        Compute confidence score for a forecast.

        Args:
            product_id: Product being forecasted
            evidence_bundle: Bullish/bearish scores from EvidenceCompiler
            diagnosis: Market regime and analysis from DiagnosisService
            forecast: Forecast output with base/bull/bear scenarios
            perception_snapshot: Current market data snapshot
            macro_context: Macro signals and overrides
            lookback_mape: Historical forecast accuracy if available

        Returns:
            ConfidenceScore with breakdown and explanation
        """

        # 1. Data quality score
        data_quality = self._score_data_quality(perception_snapshot, product_id)

        # 2. Evidence strength score
        evidence_strength = self._score_evidence_strength(evidence_bundle)

        # 3. Model stability score
        model_stability = self._score_model_stability(diagnosis)

        # 4. Macro uncertainty score
        macro_uncertainty = self._score_macro_uncertainty(macro_context)

        # 5. Combine into overall confidence
        overall = (
            data_quality * 0.25
            + evidence_strength * 0.30
            + model_stability * 0.25
            + (1.0 - macro_uncertainty)
            * 0.20  # Inverse: higher macro uncertainty = lower confidence
        )

        # 6. Compute uncertainty range (±%)
        base_price = forecast.get("base", 5000)
        volatility_range = self._estimate_volatility_range(forecast, macro_uncertainty)

        p50 = base_price
        p25 = base_price * (1 - volatility_range / 2)
        p75 = base_price * (1 + volatility_range / 2)

        # 7. Determine primary uncertainty source
        uncertainty_source = self._identify_uncertainty_source(
            evidence_strength, macro_uncertainty, data_quality
        )

        # 8. Label
        if overall >= 0.75:
            confidence_label = "HIGH"
        elif overall >= 0.60:
            confidence_label = "MEDIUM"
        else:
            confidence_label = "LOW"

        return ConfidenceScore(
            overall=round(overall, 2),
            data_quality=round(data_quality, 2),
            evidence_strength=round(evidence_strength, 2),
            model_stability=round(model_stability, 2),
            macro_uncertainty=round(macro_uncertainty, 2),
            p25=round(p25, 0),
            p50=round(p50, 0),
            p75=round(p75, 0),
            uncertainty_source=uncertainty_source,
            confidence_label=confidence_label,
            lookback_mape=lookback_mape,
        )

    def _score_data_quality(
        self, perception_snapshot: Dict[str, Any], product_id: str
    ) -> float:
        """
        Score data quality based on:
        - How many data points do we have?
        - How recent is the data?
        - Are there gaps?
        """

        try:
            product_data = perception_snapshot.get("products", {}).get(product_id, {})

            # If data_source is "real" (from MySQL), high quality
            if product_data.get("data_source") == "real":
                return 0.95

            # If data_source is "simulated" or "inferred", lower quality
            if product_data.get("data_source") in {"simulated", "inferred"}:
                return 0.60

            # If no data source specified, assume medium
            return 0.70
        except Exception:
            return 0.65

    def _score_evidence_strength(self, evidence_bundle: Dict[str, Any]) -> float:
        """
        Score evidence strength based on:
        - How aligned are competing signals (low contradiction)?
        - How strong is dominant view?
        - Are there contradictions?
        """

        try:
            bullish_score = float(evidence_bundle.get("bullish_score", 0.0) or 0.0)
            bearish_score = float(evidence_bundle.get("bearish_score", 0.0) or 0.0)
            contradictions = evidence_bundle.get("contradictions") or []

            total_score = bullish_score + bearish_score

            # How "one-sided" is the evidence? (0.5 = perfectly balanced, 1.0 = unanimous)
            if total_score > 0:
                alignment = max(bullish_score, bearish_score) / total_score
            else:
                alignment = 0.5

            # Penalize for contradictions
            contradiction_penalty = len(contradictions) * 0.15

            evidence_strength = alignment - contradiction_penalty
            return max(0.0, min(1.0, evidence_strength))
        except Exception:
            return 0.70

    def _score_model_stability(self, diagnosis: Dict[str, Any]) -> float:
        """
        Score model stability based on:
        - How confident is the market regime classification?
        - Regime changes = lower stability
        """

        try:
            regime_confidence = float(diagnosis.get("regime_confidence", 0.7) or 0.7)
            return regime_confidence
        except Exception:
            return 0.70

    def _score_macro_uncertainty(self, macro_context: Dict[str, Any]) -> float:
        """
        Score macro uncertainty based on:
        - Active macro overrides (increase uncertainty)
        - Geopolitical risk level
        - Data consistency

        Returns: 0 = certain macro, 1 = very uncertain macro
        """

        try:
            # Active overrides = macro uncertainty
            active_overrides = macro_context.get("active_overrides", [])
            override_penalty = min(len(active_overrides) * 0.2, 0.6)

            # Risk level
            risk_level = str(macro_context.get("risk_level", "medium")).lower()
            risk_score_map = {
                "low": 0.1,
                "medium": 0.4,
                "high": 0.7,
                "critical": 0.95,
            }
            risk_uncertainty = risk_score_map.get(risk_level, 0.4)

            # Combine
            macro_uncertainty = override_penalty + risk_uncertainty * 0.4
            return min(1.0, macro_uncertainty)
        except Exception:
            return 0.4

    def _estimate_volatility_range(
        self, forecast: Dict[str, Any], macro_uncertainty: float
    ) -> float:
        """
        Estimate ±% volatility range around base forecast.

        Args:
            forecast: Dict with base/bull/bear
            macro_uncertainty: Macro uncertainty score (0-1)

        Returns:
            ±% range as decimal (e.g., 0.05 = ±5%)
        """

        try:
            base = forecast.get("base", 5000)
            bull = forecast.get("bull", 5200)
            bear = forecast.get("bear", 4800)

            # Distance from base to extremes
            bull_pct = abs(bull - base) / base if base else 0
            bear_pct = abs(base - bear) / base if base else 0
            max_range = max(bull_pct, bear_pct)

            # Amplify by macro uncertainty
            volatility_range = max_range * (1 + macro_uncertainty * 0.5)

            return min(volatility_range, 0.20)  # Cap at ±20%
        except Exception:
            return 0.05

    def _identify_uncertainty_source(
        self,
        evidence_strength: float,
        macro_uncertainty: float,
        data_quality: float,
    ) -> str:
        """Identify primary source of uncertainty."""

        if macro_uncertainty > 0.6:
            return "macro_volatility"
        elif evidence_strength < 0.6:
            return "conflicting_signals"
        elif data_quality < 0.65:
            return "data_gap"
        else:
            return "normal_market_dynamics"


# Singleton instance
confidence_scorer = ConfidenceScorer()
