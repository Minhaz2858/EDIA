from __future__ import annotations

from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class ActionClaritySkill(ResponseSkill):
    name = "action_clarity"

    _ACTION_MARKERS = [
        "if",
        "then",
        "trigger",
        "建议",
        "若",
        "如果",
        "执行",
        "action",
        "recommend",
    ]

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        task_type = str(getattr(report.task_type, "value", report.task_type) or "").strip().lower()
        if task_type == "erp_data_query":
            report.enhancement_applied[self.name] = "skipped_erp_lookup"
            return report

        markdown = report.raw_markdown or ""
        lower_text = markdown.lower()
        has_action_logic = any(marker in lower_text for marker in self._ACTION_MARKERS)

        if has_action_logic:
            report.enhancement_applied[self.name] = "passed"
            return report

        addendum = (
            "\n\n## Action Triggers\n"
            "- If spot prices break above the upper forecast band, tighten discount policy and review daily.\n"
            "- If spot prices break below the lower band, pause aggressive offers and re-check inventory pressure.\n"
            "- If volatility expands beyond recent norms, switch to shorter validity windows for quotes.\n"
        )
        report.raw_markdown = markdown + addendum
        report.quality_flags.append(
            QualityFlag(
                code="action_triggers_added",
                severity="low",
                message="response lacked explicit action triggers; default trigger block appended",
                source=self.name,
            )
        )
        report.enhancement_applied[self.name] = "modified"
        return report
