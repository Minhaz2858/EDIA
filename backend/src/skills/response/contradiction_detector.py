from __future__ import annotations

from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class ContradictionDetectorSkill(ResponseSkill):
    name = "contradiction_detector"

    _CONTRADICTORY_PAIRS = [
        ("price will rise", "price will fall"),
        ("prices will rise", "prices will fall"),
        ("bullish", "bearish"),
        ("建议提价", "建议降价"),
        ("看涨", "看跌"),
    ]

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        markdown = report.raw_markdown or ""
        lower_text = markdown.lower()
        findings: list[str] = []

        for left, right in self._CONTRADICTORY_PAIRS:
            if left in lower_text and right in lower_text:
                findings.append(f"contains both '{left}' and '{right}'")

        if report.confidence and report.confidence.p25 > report.confidence.p75:
            findings.append("confidence interval invalid: p25 > p75")

        if findings:
            detail = "; ".join(findings[:3])
            report.quality_flags.append(
                QualityFlag(
                    code="contradiction_detected",
                    severity="high",
                    message=detail,
                    source=self.name,
                )
            )
            report.alerts_prepended.append(
                "⚠️ Consistency warning: contradictory signals were detected in this response."
            )

        report.enhancement_applied[self.name] = (
            "flagged" if findings else "passed"
        )
        return report
