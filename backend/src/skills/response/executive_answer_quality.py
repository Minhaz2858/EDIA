from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class ExecutiveAnswerQualitySkill(ResponseSkill):
    name = "executive_answer_quality"

    _EXECUTIVE_MARKERS = (
        "boss",
        "executive",
        "management",
        "board",
        "report",
        "老板",
        "管理层",
        "董事会",
        "报告",
        "结论",
    )
    _SOURCE_MARKERS = (
        "source",
        "data source",
        "erp",
        "mysql",
        "live db",
        "database",
        "来源",
        "数据源",
        "业务库",
        "数据库",
        "实时db",
    )
    _ACTION_MARKERS = (
        "recommend",
        "action",
        "trigger",
        "建议",
        "动作",
        "执行",
        "触发",
    )
    _DATA_SIGNAL_PATTERN = re.compile(
        r"(\d[\d,]*(?:\.\d+)?\s*(?:%|cny|rmb|元/吨|吨|orders?|笔|rows?|条)?)|"
        r"(erp|mysql|database|source|来源|数据|销量|销售额|订单|价格|库存)",
        re.IGNORECASE,
    )
    _DATE_PATTERN = re.compile(
        r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})|(\d{1,2}\s*月\s*\d{1,2}\s*日)|"
        r"(本周|上周|最近|today|yesterday|this week|last week|current week)",
        re.IGNORECASE,
    )

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        if not self._should_audit(report, state):
            report.enhancement_applied[self.name] = "skipped:not_executive"
            return report

        markdown = str(report.raw_markdown or "").strip()
        if not markdown:
            self._flag(report, "empty_answer", "high", "executive answer is empty")
            report.enhancement_applied[self.name] = "score:0"
            return report

        checks = {
            "so_what_first": self._first_paragraph_answers_so_what(markdown),
            "date_range_present": bool(self._DATE_PATTERN.search(markdown)),
            "source_present": self._has_source(markdown),
            "actions_grounded": self._actions_are_grounded(markdown),
        }

        if not checks["so_what_first"]:
            self._flag(
                report,
                "missing_so_what_opening",
                "medium",
                "first paragraph does not state a decision conclusion",
            )
        if not checks["date_range_present"]:
            self._flag(report, "missing_date_range", "medium", "answer does not mention the covered date range")
        if not checks["source_present"]:
            self._flag(report, "missing_data_source", "high", "answer does not mention ERP, market DB, or another data source")
        if not checks["actions_grounded"]:
            self._flag(
                report,
                "ungrounded_recommendation",
                "high",
                "one or more recommendation lines are not tied to a data signal",
            )

        score = int(round(sum(1 for passed in checks.values() if passed) / len(checks) * 100))
        report.enhancement_applied[self.name] = f"score:{score}"
        return report

    def _should_audit(self, report: DecisionReport, state: dict[str, Any]) -> bool:
        task_type = str(getattr(report.task_type, "value", report.task_type) or "").strip().lower()
        if task_type in {"conversational", "out_of_domain"}:
            return False
        mode_values = " ".join(
            str(value or "").strip().lower()
            for value in (
                state.get("user_query"),
                state.get("response_mode"),
                state.get("resolved_chat_mode"),
                (state.get("user_prefs") or {}).get("chat_mode") if isinstance(state.get("user_prefs"), dict) else "",
            )
        )
        return task_type in {"boss_briefing", "erp_data_query", "market_analysis_decision", "what_if_scenario", "forecast_only"} or any(
            marker in mode_values for marker in self._EXECUTIVE_MARKERS
        )

    def _first_paragraph_answers_so_what(self, markdown: str) -> bool:
        paragraphs = [part.strip(" #*-\t") for part in re.split(r"\n\s*\n", markdown) if part.strip()]
        if not paragraphs:
            return False
        first = paragraphs[0].lower()
        return any(
            marker in first
            for marker in (
                "结论",
                "建议",
                "需要",
                "风险",
                "机会",
                "action",
                "recommend",
                "risk",
                "opportunity",
                "decision",
            )
        )

    def _has_source(self, markdown: str) -> bool:
        lower_text = markdown.lower()
        return any(marker in lower_text for marker in self._SOURCE_MARKERS)

    def _actions_are_grounded(self, markdown: str) -> bool:
        action_lines = [line.strip() for line in markdown.splitlines() if self._line_has_action(line)]
        if not action_lines:
            return True
        return all(self._DATA_SIGNAL_PATTERN.search(line) for line in action_lines)

    def _line_has_action(self, line: str) -> bool:
        lower_line = line.lower()
        return any(marker in lower_line for marker in self._ACTION_MARKERS)

    def _flag(self, report: DecisionReport, code: str, severity: str, message: str) -> None:
        report.quality_flags.append(
            QualityFlag(
                code=code,
                severity=severity,
                message=message,
                source=self.name,
            )
        )