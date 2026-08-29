from __future__ import annotations

import re
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class CitationValidatorSkill(ResponseSkill):
    name = "citation_validator"
    _TAG_PATTERN = re.compile(r"\[(T[1-5](?:-[A-Z]+)?|INTEL-\d+|情报-\d+)\]")

    @staticmethod
    def _wants_source_tags(text: str) -> bool:
        q = (text or "").strip().lower()
        if not q:
            return False
        markers = [
            "show source tags",
            "show evidence tags",
            "provenance tags",
            "citation tags",
            "t1-erp",
            "证据标签",
            "来源标签",
            "溯源标签",
            "显示标签",
            "数据来源",
            "价格来源",
        ]
        return any(m in q for m in markers)

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        user_query = str(state.get("user_query") or "")
        if not self._wants_source_tags(user_query):
            report.enhancement_applied[self.name] = "skipped_non_provenance_mode"
            return report

        markdown = report.raw_markdown or ""
        tags = self._TAG_PATTERN.findall(markdown)

        source_presence = {
            "T1": bool((state.get("tool_info") or "").strip()),
            "T2": bool((state.get("rag_info") or "").strip()),
            "T3": bool((state.get("web_search_results") or "").strip()),
            "T4": bool((state.get("erp_data") or "").strip()),
            "T5": bool((state.get("rag_info") or "").strip()),
        }

        invalid_tags: list[str] = []
        for tag in tags:
            tier = tag.split("-")[0]
            if tier in source_presence and not source_presence[tier]:
                invalid_tags.append(tag)

        if invalid_tags:
            unique_invalid = sorted(set(invalid_tags))
            report.quality_flags.append(
                QualityFlag(
                    code="citation_source_missing",
                    severity="medium",
                    message=f"citation tags without source data: {', '.join(unique_invalid[:5])}",
                    source=self.name,
                )
            )
            report.alerts_prepended.append(
                "⚠️ Evidence warning: some citation tags could not be verified from available source data."
            )

        if len(markdown) > 600 and not tags:
            report.quality_flags.append(
                QualityFlag(
                    code="citation_missing",
                    severity="low",
                    message="long response contains no evidence tags",
                    source=self.name,
                )
            )

        report.enhancement_applied[self.name] = (
            "flagged" if invalid_tags else "passed"
        )
        return report
