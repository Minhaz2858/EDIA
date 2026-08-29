from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class LanguageConsistencyGuardrailSkill(ResponseSkill):
    """Detect heavy Chinese-English mixing in a single response."""

    name = "language_consistency_guardrail"

    _ZH_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
    _EN_WORD_PATTERN = re.compile(r"\b[a-zA-Z]{3,}\b")

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        markdown = report.raw_markdown or ""
        if not markdown.strip():
            report.enhancement_applied[self.name] = "skipped:empty"
            return report

        zh_chars = len(self._ZH_CHAR_PATTERN.findall(markdown))
        en_words = len(self._EN_WORD_PATTERN.findall(markdown))

        # Trigger only when Chinese is dominant but English wording is still heavy.
        if zh_chars >= 80 and en_words >= 25:
            report.quality_flags.append(
                QualityFlag(
                    code="mixed_language_output",
                    severity="medium",
                    message=(
                        "response contains substantial Chinese-English mixing; "
                        "consider single-language output"
                    ),
                    source=self.name,
                )
            )
            report.alerts_prepended.append(
                "⚠️ Language warning: response contains mixed Chinese and English phrasing."
            )
            report.enhancement_applied[self.name] = "flagged"
        else:
            report.enhancement_applied[self.name] = "passed"

        return report
