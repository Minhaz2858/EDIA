from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class ConfidenceLanguageSkill(ResponseSkill):
    name = "confidence_language"

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        markdown = report.raw_markdown or ""
        overall = report.confidence.overall if report.confidence else None
        critic_level = (report.critic_level or "pass").lower()

        low_confidence = (
            (overall is not None and overall < 0.45)
            or critic_level == "high_risk"
            or bool(report.critic_flags)
        )

        if not low_confidence:
            report.enhancement_applied[self.name] = "passed"
            return report

        updated = markdown
        replacements = [
            (r"\bmust\b", "should"),
            (r"\bdefinitely\b", "likely"),
            (r"\bguaranteed\b", "probable"),
            (r"建议立即", "建议谨慎评估后"),
            (r"必须", "建议"),
        ]
        for pattern, repl in replacements:
            updated = re.sub(pattern, repl, updated, flags=re.IGNORECASE)

        if not updated.startswith("Confidence note:"):
            updated = (
                "Confidence note: current market evidence is mixed, so treat this recommendation as conditional until the next live market confirmation.\n\n"
                + updated
            )

        report.raw_markdown = updated
        report.quality_flags.append(
            QualityFlag(
                code="confidence_gated_language",
                severity="medium",
                message="language softened due to low confidence or critic warnings",
                source=self.name,
            )
        )
        report.alerts_prepended.append(
            "Confidence note: recommendation language was softened because current evidence is weaker than usual."
        )
        report.enhancement_applied[self.name] = "modified"
        return report
