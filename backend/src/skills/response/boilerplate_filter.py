"""Boilerplate Filter – Response Enhancement Skill #7.

Detects and removes vague, generic, non-actionable filler language that
dilutes the quality of EDIA's market-analysis output.

Petrochemical context:  Decision-makers at Ecisco receive dozens of market
reports daily.  Phrases like "市场波动较大，建议密切关注" or "the market
remains uncertain" carry zero information.  This skill flags or strips
those phrases so the final response is dense with actionable reasoning.
"""

from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class BoilerplateFilterSkill(ResponseSkill):
    name = "boilerplate_filter"

    # Phrases that add no information in a C5/C9 decision context.
    # Each pattern is compiled as case-insensitive regex.
    _BOILERPLATE_PATTERNS: list[str] = [
        # Chinese boilerplate
        r"市场波动较大[，,]?\s*建议密切关注",
        r"需要?密切关注后续?市场[变化动态走势]+",
        r"综合以上分析[，,]?\s*我们认为",
        r"总[的而]言之",
        r"以上仅供参考",
        r"具体[操作决策]需根据实际情况",
        r"风险与机遇并存",
        r"建议.*保持谨慎[观望态度]*(?:\s*[。.])?",
        # English boilerplate
        r"(?:the )?market remains (?:highly )?uncertain",
        r"it is important to (?:closely )?monitor",
        r"in conclusion,?\s*(?:we believe|it can be said)",
        r"this (?:report|analysis) is for (?:reference|informational purposes) only",
        r"past performance (?:is|does) not (?:guarantee|indicate) future results",
        r"please consult.*(?:professional|advisor)",
        r"risks and opportunities coexist",
    ]

    _COMPILED = [re.compile(p, re.IGNORECASE) for p in _BOILERPLATE_PATTERNS]

    # Minimum report length to bother filtering (don't touch short look-ups)
    _MIN_LENGTH = 400

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        markdown = report.raw_markdown or ""

        if len(markdown) < self._MIN_LENGTH:
            report.enhancement_applied[self.name] = "skipped:short_response"
            return report

        removed: list[str] = []
        cleaned = markdown

        for pattern in self._COMPILED:
            match = pattern.search(cleaned)
            if match:
                removed.append(match.group().strip())
                # Remove the matched phrase (and trailing period / space)
                cleaned = pattern.sub("", cleaned)

        # Collapse multiple blank lines left behind by removal
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        if removed:
            report.raw_markdown = cleaned
            report.quality_flags.append(
                QualityFlag(
                    code="boilerplate_removed",
                    severity="low",
                    message=f"removed {len(removed)} generic phrase(s): {'; '.join(removed[:3])}",
                    source=self.name,
                )
            )
            report.enhancement_applied[self.name] = f"removed:{len(removed)}"
        else:
            report.enhancement_applied[self.name] = "passed"

        return report
