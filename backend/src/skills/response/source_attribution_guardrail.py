from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class SourceAttributionGuardrailSkill(ResponseSkill):
    """Flag numeric market statements that lack explicit source attribution."""

    name = "source_attribution_guardrail"

    _NUMERIC_LINE_PATTERN = re.compile(
        r"(CNY|RMB|USD|yuan|price|价格|报价|元/吨|吨).*(\d[\d,]*(?:\.\d+)?)"
    )
    _SOURCE_HINT_PATTERN = re.compile(
        r"(\[T[1-5](?:-[A-Z]+)?\]|\[INTEL-\d+\]|来源|source|according to)",
        re.IGNORECASE,
    )

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        markdown = report.raw_markdown or ""
        lines = [ln.strip() for ln in markdown.splitlines() if ln.strip()]

        suspect_lines: list[str] = []
        for line in lines:
            if not self._NUMERIC_LINE_PATTERN.search(line):
                continue
            if self._SOURCE_HINT_PATTERN.search(line):
                continue
            suspect_lines.append(line)

        if suspect_lines:
            report.quality_flags.append(
                QualityFlag(
                    code="missing_source_attribution",
                    severity="high",
                    message=(
                        "numeric statements without clear source attribution: "
                        f"{len(suspect_lines)} line(s)"
                    ),
                    source=self.name,
                )
            )
            report.alerts_prepended.append(
                "⚠️ Attribution warning: some price/number statements are missing explicit source tags."
            )
            report.enhancement_applied[self.name] = "flagged"
        else:
            report.enhancement_applied[self.name] = "passed"

        return report
