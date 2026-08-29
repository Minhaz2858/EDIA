"""Alternative Perspective Enricher – Response Enhancement Skill #6.

Ensures every market-analysis response contains balanced bull/base/bear
framing so the user always sees both sides of the trade.

Petrochemical context:  C5/C9 pricing decisions are rarely one-directional.
A buyer looking at isoprene must weigh feedstock cost-push (bullish) against
downstream SIS demand softness (bearish) simultaneously.  If the report only
presents one side the user may anchor on an incomplete picture.

This skill scans the generated markdown for the presence of at least two
scenario directions.  When only one perspective is found it appends a
concise counter-perspective block derived from existing state data.
"""

from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class AlternativePerspectiveSkill(ResponseSkill):
    name = "alternative_perspective"

    # Markers indicating a bullish / bearish viewpoint is already present
    _BULLISH_MARKERS = [
        "bullish", "upside", "上行", "看涨", "涨价", "成本推动",
        "price will rise", "prices may rise", "建议提价",
    ]
    _BEARISH_MARKERS = [
        "bearish", "downside", "下行", "看跌", "降价", "需求疲软",
        "price will fall", "prices may fall", "建议降价",
    ]

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        markdown = report.raw_markdown or ""
        lower = markdown.lower()

        has_bull = any(m in lower for m in self._BULLISH_MARKERS)
        has_bear = any(m in lower for m in self._BEARISH_MARKERS)

        # If both perspectives already exist, nothing to do
        if has_bull and has_bear:
            report.enhancement_applied[self.name] = "passed"
            return report

        # Build a counter-perspective from available state
        counter_lines: list[str] = []
        diagnosis = state.get("diagnosis") or {}
        forecast = state.get("forecast") or {}
        evidence = state.get("evidence_bundle") or {}

        if has_bull and not has_bear:
            # Need bearish counter-perspective
            counter_lines.append("## ⚖️ Alternative Perspective — Downside Risks")
            counter_lines.extend(self._bearish_counter(diagnosis, forecast, evidence))
        elif has_bear and not has_bull:
            # Need bullish counter-perspective
            counter_lines.append("## ⚖️ Alternative Perspective — Upside Factors")
            counter_lines.extend(self._bullish_counter(diagnosis, forecast, evidence))
        else:
            # Neither direction found (generic / lookup response) — skip
            report.enhancement_applied[self.name] = "skipped:no_directional_content"
            return report

        if counter_lines and len(counter_lines) > 1:
            report.raw_markdown = markdown + "\n\n" + "\n".join(counter_lines) + "\n"
            report.quality_flags.append(
                QualityFlag(
                    code="alternative_perspective_added",
                    severity="low",
                    message="response lacked balanced bull/bear framing; counter-perspective appended",
                    source=self.name,
                )
            )
            report.enhancement_applied[self.name] = "modified"
        else:
            report.enhancement_applied[self.name] = "skipped:insufficient_state"

        return report

    # ------------------------------------------------------------------
    # Counter-perspective builders using real pipeline state
    # ------------------------------------------------------------------

    def _bearish_counter(
        self, diagnosis: dict, forecast: dict, evidence: dict
    ) -> list[str]:
        lines: list[str] = []

        # Evidence for fall from evidence compiler
        for_fall = evidence.get("evidence_for_fall") or []
        if for_fall:
            lines.append("- Bearish evidence from analysis:")
            for item in for_fall[:3]:
                lines.append(f"  - {item}")

        # Demand weakness from diagnosis
        demand = diagnosis.get("demand_status") or {}
        if demand.get("effective_demand_status") in ("weak", "very_weak"):
            lines.append(
                f"- Downstream demand rated **{demand['effective_demand_status']}** "
                f"(seasonal phase: {demand.get('seasonal_phase', 'n/a')})"
            )

        # Bear scenario from forecast
        scenarios = forecast.get("scenarios") or {}
        bear_7d = (scenarios.get("7d") or {}).get("bear") or {}
        if bear_7d:
            lines.append(
                f"- 7-day bear scenario range: **{bear_7d.get('low', '?')}–{bear_7d.get('high', '?')} CNY/ton**"
            )

        if not lines:
            lines.append(
                "- Consider downstream demand softness and inventory accumulation "
                "risk as potential price headwinds."
            )

        return lines

    def _bullish_counter(
        self, diagnosis: dict, forecast: dict, evidence: dict
    ) -> list[str]:
        lines: list[str] = []

        # Evidence for rise from evidence compiler
        for_rise = evidence.get("evidence_for_rise") or []
        if for_rise:
            lines.append("- Bullish evidence from analysis:")
            for item in for_rise[:3]:
                lines.append(f"  - {item}")

        # Naphtha / crude cost push from diagnosis
        chain_impact = diagnosis.get("chain_impact") or []
        for c in chain_impact[:2]:
            if any(kw in str(c).lower() for kw in ("上升", "成本", "push", "upstream")):
                lines.append(f"- Upstream pressure: {c}")

        # Bull scenario from forecast
        scenarios = forecast.get("scenarios") or {}
        bull_7d = (scenarios.get("7d") or {}).get("bull") or {}
        if bull_7d:
            lines.append(
                f"- 7-day bull scenario range: **{bull_7d.get('low', '?')}–{bull_7d.get('high', '?')} CNY/ton**"
            )

        if not lines:
            lines.append(
                "- Consider upstream feedstock cost pressure and low-inventory "
                "restocking as potential price supports."
            )

        return lines
