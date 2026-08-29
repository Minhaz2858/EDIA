from __future__ import annotations

from typing import Any, Dict, List, Optional


_ERP_BLOCK_TYPES = {
    "erp_sales_summary",
    "erp_customer_breakdown",
    "erp_monthly_trend",
    "erp_delivery_status",
    "erp_vs_market",
}


def _lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _block_type(block: Dict[str, Any]) -> str:
    return str(block.get("type") or "").strip()


def _template_score(template: Dict[str, Any], objective: str) -> int:
    haystack = " ".join(
        [
            str(template.get("id") or ""),
            str(template.get("name") or ""),
            str(template.get("name_en") or ""),
            str(template.get("description") or ""),
            str(template.get("category") or ""),
            " ".join(str(tag) for tag in template.get("tags") or []),
        ]
    ).lower()
    score = 0
    objective_lower = _lower_text(objective)
    keyword_groups = {
        "customer_sales_report": ("customer", "sales", "客户", "销售", "流失", "增长"),
        "inventory_alert_report": ("inventory", "stock", "库存", "预警", "low stock"),
        "executive_dashboard": ("executive", "boss", "board", "老板", "高管", "董事会"),
        "weekly_market_report": ("market", "price", "weekly", "行情", "价格", "周报"),
    }
    for template_id, markers in keyword_groups.items():
        if str(template.get("id") or "") == template_id and any(marker in objective_lower for marker in markers):
            score += 20
    if str(template.get("id") or "") == "customer_sales_report" and any(marker in objective_lower for marker in ("客户", "customer")):
        score += 16
    if str(template.get("id") or "") == "inventory_alert_report" and any(marker in objective_lower for marker in ("库存", "inventory", "stock")):
        score += 16
    for token in objective_lower.replace("/", " ").replace(",", " ").split():
        if token and token in haystack:
            score += 2
    if any(marker in objective_lower for marker in ("erp", "客户", "销售", "订单", "库存")):
        required = {str(item) for item in template.get("required_data") or []}
        if "customer_sales" in required or any(_block_type(block) in _ERP_BLOCK_TYPES for block in template.get("blocks") or []):
            score += 8
    return score


class ReportBuilderCopilot:
    """Plan report structure from existing templates, blocks, and data-source contracts."""

    def build_plan(
        self,
        *,
        objective: str,
        current_blocks: Optional[List[Dict[str, Any]]] = None,
        templates: Optional[List[Dict[str, Any]]] = None,
        data_sources: Optional[List[Dict[str, Any]]] = None,
        language: str = "zh",
    ) -> Dict[str, Any]:
        blocks = list(current_blocks or [])
        gallery = list(templates or [])
        sources = list(data_sources or [])
        source_ids = {str(item.get("id") or "") for item in sources if isinstance(item, dict)}
        selected_template = self._select_template(gallery, objective)
        recommended_blocks = self._recommended_blocks(selected_template, blocks, objective)
        required_data = self._required_data(selected_template, recommended_blocks, objective)
        missing_data = [item for item in required_data if source_ids and item not in source_ids]
        readiness = self._readiness(blocks, recommended_blocks, missing_data)
        missing_sections = self._missing_sections(blocks, objective)

        return {
            "ok": True,
            "contract": "report_builder_copilot_v1",
            "language": "en" if str(language).lower().startswith("en") else "zh",
            "objective": objective,
            "recommended_template": {
                "id": selected_template.get("id") if selected_template else None,
                "name": selected_template.get("name") if selected_template else None,
                "name_en": selected_template.get("name_en") if selected_template else None,
                "reason": self._template_reason(selected_template, objective),
            },
            "readiness": readiness,
            "required_data": required_data,
            "missing_data": missing_data,
            "missing_sections": missing_sections,
            "recommended_blocks": recommended_blocks,
            "next_actions": self._next_actions(readiness, missing_sections, missing_data),
            "quality_gates": {
                "template_selected": "pass" if selected_template else "warn",
                "has_data_blocks": "pass" if any(_block_type(block) != "text" for block in blocks + recommended_blocks) else "warn",
                "has_narrative": "pass" if any(_block_type(block) == "text" for block in blocks + recommended_blocks) else "warn",
                "data_sources_available": "pass" if not missing_data else "warn",
            },
        }

    def _select_template(self, templates: List[Dict[str, Any]], objective: str) -> Dict[str, Any]:
        if not templates:
            return {}
        return max(templates, key=lambda template: (_template_score(template, objective), str(template.get("id") or "")))

    def _recommended_blocks(
        self,
        selected_template: Dict[str, Any],
        current_blocks: List[Dict[str, Any]],
        objective: str,
    ) -> List[Dict[str, Any]]:
        existing_types = {_block_type(block) for block in current_blocks}
        template_blocks = [dict(block) for block in (selected_template.get("blocks") or []) if isinstance(block, dict)]
        recommendations: list[dict[str, Any]] = []
        for block in template_blocks:
            block_type = _block_type(block)
            if block_type and block_type not in existing_types:
                recommendations.append(
                    {
                        "type": block_type,
                        "title": block.get("title") or block_type,
                        "config": dict(block.get("config") or {}),
                        "reason": "template_required_block",
                    }
                )
            if len(recommendations) >= 4:
                break

        objective_lower = _lower_text(objective)
        wants_erp = any(marker in objective_lower for marker in ("erp", "客户", "销售", "订单", "库存", "customer", "sales", "inventory"))
        if wants_erp and not any(item["type"] in _ERP_BLOCK_TYPES for item in recommendations) and not (existing_types & _ERP_BLOCK_TYPES):
            recommendations.insert(
                0,
                {
                    "type": "erp_customer_breakdown" if any(marker in objective_lower for marker in ("客户", "customer")) else "erp_sales_summary",
                    "title": "客户销售明细" if any(marker in objective_lower for marker in ("客户", "customer")) else "ERP销售汇总",
                    "config": {"period_days": 30},
                    "reason": "erp_objective_requires_internal_data",
                },
            )
        if not any(item["type"] == "text" for item in recommendations) and "text" not in existing_types:
            recommendations.append(
                {
                    "type": "text",
                    "title": "管理层结论",
                    "config": {"content": "Summarize verified data, risks, and next actions."},
                    "reason": "boss_ready_narrative_required",
                }
            )
        return recommendations[:5]

    @staticmethod
    def _required_data(selected_template: Dict[str, Any], blocks: List[Dict[str, Any]], objective: str) -> List[str]:
        required = {str(item) for item in selected_template.get("required_data") or [] if str(item)}
        block_types = {_block_type(block) for block in blocks}
        if block_types & _ERP_BLOCK_TYPES or any(marker in _lower_text(objective) for marker in ("erp", "客户", "销售", "订单")):
            required.add("customer_sales")
        if any(block_type in {"chart", "data_table", "kpi_cards"} for block_type in block_types):
            required.add("market_prices")
        return sorted(required)

    @staticmethod
    def _missing_sections(blocks: List[Dict[str, Any]], objective: str) -> List[str]:
        block_types = {_block_type(block) for block in blocks}
        missing: list[str] = []
        if not block_types:
            return ["data_block", "narrative", "action_section"]
        if not any(block_type in {"kpi_cards", "data_table", "chart"} or block_type in _ERP_BLOCK_TYPES for block_type in block_types):
            missing.append("data_block")
        if "text" not in block_types:
            missing.append("narrative")
        if any(marker in _lower_text(objective) for marker in ("老板", "boss", "action", "建议", "下一步")) and "text" not in block_types:
            missing.append("action_section")
        return missing

    @staticmethod
    def _readiness(blocks: List[Dict[str, Any]], recommendations: List[Dict[str, Any]], missing_data: List[str]) -> Dict[str, Any]:
        score = 100
        if not blocks:
            score -= 35
        score -= min(30, len(recommendations) * 6)
        score -= min(25, len(missing_data) * 8)
        score = max(0, score)
        if score >= 80:
            status = "ready"
        elif score >= 50:
            status = "needs_work"
        else:
            status = "not_ready"
        return {"status": status, "score": score}

    @staticmethod
    def _template_reason(selected_template: Dict[str, Any], objective: str) -> str:
        if not selected_template:
            return "No template gallery was available."
        return f"Best match for objective using template/category/tags: {selected_template.get('id')}."

    @staticmethod
    def _next_actions(readiness: Dict[str, Any], missing_sections: List[str], missing_data: List[str]) -> List[str]:
        actions: list[str] = []
        if missing_data:
            actions.append("Confirm required data sources before export: " + ", ".join(missing_data))
        if "data_block" in missing_sections:
            actions.append("Add at least one verified data block from ERP or market data.")
        if "narrative" in missing_sections:
            actions.append("Add a short executive narrative explaining implications and actions.")
        if not actions and readiness.get("status") == "ready":
            actions.append("Refresh live data, preview the report, then export.")
        return actions[:5]


report_builder_copilot = ReportBuilderCopilot()