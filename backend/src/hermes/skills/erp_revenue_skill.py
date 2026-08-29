"""Post-synthesis guardrail: enforce FAMOUNT-sum revenue, block volume×avg narration."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.src.service.types import DecisionReport


_VOLUME_AVG_PATTERNS = [
    re.compile(
        r"(总销量|销量合计|合计销量).{0,40}?(加权均价|均价).{0,40}?(销售额|销售收入|营业额)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(total\s+volume|sales\s+volume).{0,60}?(weighted\s+average|avg\s+price).{0,60}?(revenue|sales\s+amount)",
        re.IGNORECASE,
    ),
    re.compile(r"×\s*[\d,]+\.?\d*\s*元", re.IGNORECASE),
    re.compile(r"\*\s*[\d,]+\.?\d*\s*(CNY|元)", re.IGNORECASE),
]


def _extract_verified_revenue(state: dict[str, Any]) -> Optional[float]:
    hermes_execution = state.get("hermes_execution")
    if isinstance(hermes_execution, dict):
        for obs in hermes_execution.get("observations") or []:
            if not isinstance(obs, dict):
                continue
            payload = obs.get("payload") if isinstance(obs.get("payload"), dict) else {}
            if payload.get("total_revenue") is not None:
                return float(payload["total_revenue"])
            if isinstance(payload.get("summary_text"), str):
                from backend.src.engine.decision_graph import _extract_erp_sales_summary_rows

                parsed = _extract_erp_sales_summary_rows(payload["summary_text"])
                if parsed.get("total_revenue") is not None:
                    return float(parsed["total_revenue"])

    erp_data = str(state.get("erp_data") or "")
    if erp_data:
        from backend.src.engine.decision_graph import _extract_erp_sales_summary_rows

        parsed = _extract_erp_sales_summary_rows(erp_data)
        if parsed.get("total_revenue") is not None:
            return float(parsed["total_revenue"])

    ctx = state.get("context")
    if isinstance(ctx, dict):
        summary = ctx.get("erp_summary")
        if isinstance(summary, dict) and summary.get("total_revenue") is not None:
            return float(summary["total_revenue"])
    return None


def _looks_like_revenue_query(state: dict[str, Any]) -> bool:
    query = str(state.get("user_query") or "").lower()
    markers = (
        "销售额",
        "销售金额",
        "销售收入",
        "营业额",
        "sales revenue",
        "total revenue",
        "sales amount",
    )
    return any(marker in query for marker in markers)


class ErpRevenueGuardrailSkill:
    name = "erp_revenue_guardrail"

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        if not _looks_like_revenue_query(state):
            report.enhancement_applied[self.name] = "skipped:not_revenue_query"
            return report

        markdown = str(report.raw_markdown or "")
        if not markdown.strip():
            report.enhancement_applied[self.name] = "skipped:empty"
            return report

        flagged = False
        for pattern in _VOLUME_AVG_PATTERNS:
            if pattern.search(markdown):
                flagged = True
                break

        verified = _extract_verified_revenue(state)
        if flagged and verified is not None:
            note_zh = (
                f"\n\n> **销售额口径说明**：本期 ERP 销售额合计为 **{verified:,.2f} 元**"
                "（对每条订单明细的 `FAMOUNT` 求和）。不得使用「总销量×加权均价」推算销售额。"
            )
            note_en = (
                f"\n\n> **Revenue basis**: Total ERP sales revenue is **{verified:,.2f} CNY** "
                "(sum of per-order-line `FAMOUNT`). Do not derive revenue as volume × average price."
            )
            lang = str((state.get("user_prefs") or {}).get("language") or "zh")
            markdown = markdown.rstrip() + (note_en if lang == "en" else note_zh)
            report.raw_markdown = markdown
            report.enhancement_applied[self.name] = "corrected:volume_avg_blocked"
            return report

        if verified is not None:
            report.enhancement_applied[self.name] = f"verified:{verified:.2f}"
        elif flagged:
            report.enhancement_applied[self.name] = "flagged:volume_avg_pattern"
        else:
            report.enhancement_applied[self.name] = "passed"
        return report
