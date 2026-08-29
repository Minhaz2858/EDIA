"""Semantic Consistency Checker – Response Enhancement Skill #8.

Verifies that the three core pipeline outputs — diagnosis, forecast, and
pricing recommendation — are directionally aligned within the final report.

Petrochemical context:  A diagnosis that says "bullish / cost-push" combined
with a forecast showing price decline and a pricing action of "hold" is
internally inconsistent.  This can happen when different pipeline stages
use slightly different data windows or when the LLM generation drifts from
the structured inputs.  The skill catches these mismatches and flags them
so the user is alerted.
"""

from __future__ import annotations

from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


_DIRECTION_MAP = {
    "bullish": 1,
    "bearish": -1,
    "neutral": 0,
    "mixed": 0,
}


class SemanticConsistencySkill(ResponseSkill):
    name = "semantic_consistency"

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        diagnosis = state.get("diagnosis") or {}
        forecast = state.get("forecast") or {}
        pricing = state.get("pricing_decision") or {}

        findings: list[str] = []

        # 1. Diagnosis direction vs forecast direction
        diag_regime = (diagnosis.get("market_regime") or "neutral").lower()
        diag_dir = _DIRECTION_MAP.get(diag_regime, 0)

        forecast_dir = self._infer_forecast_direction(forecast)

        if diag_dir != 0 and forecast_dir != 0 and diag_dir != forecast_dir:
            findings.append(
                f"diagnosis regime is '{diag_regime}' but 7d base forecast "
                f"points {'up' if forecast_dir > 0 else 'down'}"
            )

        # 2. Forecast direction vs pricing action
        action = (pricing.get("action") or "").lower()
        action_dir = self._action_direction(action)

        if forecast_dir != 0 and action_dir != 0 and forecast_dir != action_dir:
            findings.append(
                f"forecast direction is {'bullish' if forecast_dir > 0 else 'bearish'} "
                f"but pricing action is '{action}'"
            )

        # 3. Diagnosis direction vs pricing action
        if diag_dir != 0 and action_dir != 0 and diag_dir != action_dir:
            findings.append(
                f"diagnosis regime is '{diag_regime}' but pricing action is '{action}'"
            )

        # 4. Price recommendation vs forecast range sanity
        rec_price = pricing.get("recommended_price")
        scenarios = forecast.get("scenarios") or {}
        base_7d = (scenarios.get("7d") or {}).get("base") or {}
        if rec_price and base_7d:
            low = base_7d.get("low", 0)
            high = base_7d.get("high", 0)
            if low and high and rec_price:
                # Allow 8% tolerance around forecast band
                tolerance = (high - low) * 0.5 if high != low else high * 0.08
                if rec_price > high + tolerance:
                    findings.append(
                        f"recommended price {rec_price} is significantly above "
                        f"7d base forecast band [{low}–{high}]"
                    )
                elif rec_price < low - tolerance:
                    findings.append(
                        f"recommended price {rec_price} is significantly below "
                        f"7d base forecast band [{low}–{high}]"
                    )

        # 5. Evidence dominant view vs diagnosis
        evidence = state.get("evidence_bundle") or {}
        dominant_view = (evidence.get("dominant_view") or "").lower()
        ev_dir = _DIRECTION_MAP.get(dominant_view, 0)
        if ev_dir != 0 and diag_dir != 0 and ev_dir != diag_dir:
            findings.append(
                f"evidence dominant view is '{dominant_view}' "
                f"but diagnosis regime is '{diag_regime}'"
            )

        # Report
        if findings:
            detail = "; ".join(findings[:4])
            report.quality_flags.append(
                QualityFlag(
                    code="semantic_inconsistency",
                    severity="high",
                    message=detail,
                    source=self.name,
                )
            )
            report.alerts_prepended.append(
                "⚠️ Consistency check: potential misalignment detected between "
                "diagnosis, forecast, and pricing recommendation."
            )
            report.enhancement_applied[self.name] = "flagged"
        else:
            report.enhancement_applied[self.name] = "passed"

        return report

    # ------------------------------------------------------------------

    @staticmethod
    def _infer_forecast_direction(forecast: dict) -> int:
        """Infer directional bias from the 7-day base-case forecast."""
        scenarios = forecast.get("scenarios") or {}
        base_7d = (scenarios.get("7d") or {}).get("base") or {}
        latest = forecast.get("latest_price")
        if not latest or not base_7d:
            return 0
        mid = (base_7d.get("low", 0) + base_7d.get("high", 0)) / 2
        if mid == 0:
            return 0
        pct_change = (mid - latest) / latest
        if pct_change > 0.005:
            return 1   # bullish
        elif pct_change < -0.005:
            return -1  # bearish
        return 0       # neutral

    @staticmethod
    def _action_direction(action: str) -> int:
        """Map pricing action string to directional integer."""
        if action in ("raise", "tighten", "aggressive"):
            return 1
        elif action in ("lower", "flex", "discount", "defensive"):
            return -1
        elif action in ("hold", "maintain"):
            return 0
        return 0
