from __future__ import annotations

from typing import Any, Dict, List


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _period_from_normalized(normalized_query: Dict[str, Any], fallback_query: str) -> str:
    period_start = str(normalized_query.get("period_start") or "").strip()
    period_end = str(normalized_query.get("period_end") or "").strip()
    if period_start and period_end:
        return f"{period_start} to {period_end}"
    return fallback_query


def _product_ids_from_plan(plan: Dict[str, Any], normalized_query: Dict[str, Any]) -> List[str]:
    product_ids = [str(item) for item in _as_list(normalized_query.get("product_ids")) if item]
    if not product_ids:
        product_ids = [str(item) for item in _as_list(plan.get("product_ids")) if item]
    return product_ids


def _sales_order_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    rows = _as_list(result.get("rows"))
    sample_rows = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        sample_rows.append(
            {
                "order_no": row.get("order_no"),
                "customer": row.get("customer"),
                "plan_date": row.get("plan_date"),
                "product": row.get("product"),
                "qty_ton": row.get("qty_ton"),
                "unit_price": row.get("unit_price"),
                "revenue_cny": row.get("revenue_cny"),
            }
        )
    return {
        "period_start": result.get("period_start"),
        "period_end": result.get("period_end"),
        "row_count": result.get("row_count"),
        "total_qty": result.get("total_qty"),
        "total_revenue": result.get("total_revenue"),
        "available_start": result.get("available_start"),
        "available_end": result.get("available_end"),
        "sample_rows": sample_rows,
        "error": result.get("error"),
    }


def execute_hermes_plan(hermes_plan: Any, *, user_query: str = "") -> Dict[str, Any]:
    """Run safe, deterministic observations requested by a Hermes plan."""
    # Ensure skill handlers are registered
    import backend.src.hermes.skill_handlers  # noqa: F401

    plan = _as_dict(hermes_plan)
    if not plan:
        return {"enabled": False, "observations": []}

    normalized_query = _as_dict(plan.get("normalized_query"))
    capabilities = [str(item) for item in _as_list(plan.get("capabilities")) if item]
    tool_sequence = [str(item) for item in _as_list(plan.get("tool_sequence")) if item]
    agent_skills = [str(item) for item in _as_list(plan.get("agent_skills")) if item]
    observations: List[Dict[str, Any]] = []
    query = user_query or str(plan.get("user_query") or "")
    period = _period_from_normalized(normalized_query, query)
    product_ids = _product_ids_from_plan(plan, normalized_query)
    product_id = product_ids[0] if product_ids else None

    capability = str(normalized_query.get("capability") or "").strip()
    if not capability and capabilities:
        capability = capabilities[0]

    if capability == "erp_order_details" or "capability:erp_order_details" in tool_sequence:
        try:
            from backend.src.tools.erp_tools import query_sales_order_details

            result = query_sales_order_details(
                product_id=product_id,
                period=period,
                limit=50,
            )
            observations.append(
                {
                    "tool": "erp_sales_order_details",
                    "status": "ok" if not result.get("error") else "error",
                    "summary": "Fetched verified ERP sales order rows from erp_v_sale_orderentry.",
                    "payload": _sales_order_summary(result),
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "erp_sales_order_details",
                    "status": "error",
                    "summary": f"ERP order detail observation failed: {exc}",
                    "payload": {},
                }
            )

    if capability in {"erp_sales_summary", "erp_customer_sales"} or any(
        cap in capabilities for cap in ("erp_sales_summary", "erp_customer_sales")
    ):
        try:
            if capability == "erp_customer_sales" or "erp_customer_sales" in capabilities:
                from backend.src.tools.erp_tools import query_customer_sales_report

                result = query_customer_sales_report(product_id=product_id, period=period)
                observations.append(
                    {
                        "tool": "erp_customer_sales",
                        "status": "ok" if not result.get("error") else "error",
                        "summary": "Fetched customer-level ERP sales (FAMOUNT sums).",
                        "payload": result if isinstance(result, dict) else {"text": str(result)},
                    }
                )
            else:
                from backend.src.engine.decision_graph import _extract_erp_sales_summary_rows
                from backend.src.tools.erp_tools import query_sales_summary

                summary_text = query_sales_summary(product_id=product_id, period=period)
                parsed = _extract_erp_sales_summary_rows(str(summary_text or ""))
                observations.append(
                    {
                        "tool": "erp_sales_summary",
                        "status": "ok",
                        "summary": "Fetched ERP sales summary; revenue = SUM(FAMOUNT) per order line.",
                        "payload": {
                            "summary_text": summary_text,
                            "total_revenue": parsed.get("total_revenue"),
                            "rows": parsed.get("rows"),
                            "period_start": parsed.get("period_start"),
                            "period_end": parsed.get("period_end"),
                        },
                    }
                )
        except Exception as exc:
            observations.append(
                {
                    "tool": "erp_sales_summary",
                    "status": "error",
                    "summary": f"ERP sales summary observation failed: {exc}",
                    "payload": {},
                }
            )

    if "erp_inventory" in tool_sequence or any(
        marker in query for marker in ("库存", "inventory", "stock")
    ):
        try:
            from backend.src.tools.erp_tools import query_inventory_levels

            inv_text = query_inventory_levels(product_id=product_id)
            observations.append(
                {
                    "tool": "erp_inventory",
                    "status": "ok",
                    "summary": "Fetched ERP inventory levels.",
                    "payload": {"summary_text": inv_text},
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "erp_inventory",
                    "status": "error",
                    "summary": f"ERP inventory observation failed: {exc}",
                    "payload": {},
                }
            )

    if "database_capability_discovery" in capabilities or "database_capability_discovery" in tool_sequence:
        try:
            from backend.src.tools.db_capability_registry import discover_database_candidates

            result = discover_database_candidates(query, limit=5)
            observations.append(
                {
                    "tool": "database_capability_discovery",
                    "status": "ok" if not result.get("error") else "error",
                    "summary": "Scanned registered MySQL tables/columns for likely data sources.",
                    "payload": {
                        "query_terms": result.get("query_terms") or [],
                        "candidates": _as_list(result.get("candidates"))[:5],
                        "error": result.get("error"),
                    },
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "database_capability_discovery",
                    "status": "error",
                    "summary": f"Database capability discovery failed: {exc}",
                    "payload": {},
                }
            )

    if capability and capability.startswith("erp_") and capability not in {
        "erp_order_details",
        "erp_sales_summary",
        "erp_customer_sales",
    }:
        try:
            from backend.src.hermes.skill_handlers import handle_db_capability_execute

            ctx = {
                "user_query": query,
                "product_id": product_id or "",
                "period": period,
            }
            skill_result = handle_db_capability_execute(ctx, plan)
            observations.append(
                {
                    "tool": "edia_execute_db_capability",
                    "status": skill_result.get("status", "ok"),
                    "summary": str(skill_result.get("summary") or "Executed database capability."),
                    "payload": skill_result,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "edia_execute_db_capability",
                    "status": "error",
                    "summary": str(exc),
                    "payload": {},
                }
            )

    if plan.get("forecast_policy"):
        observations.append(
            {
                "tool": "forecast_policy_service",
                "status": "ok",
                "summary": "Forecast policy gates are locked before narration.",
                "payload": plan.get("forecast_policy") or {},
            }
        )

    if "forecast" in _as_list(plan.get("domains")) and any(
        marker in query
        for marker in ("预测", "forecast", "走势", "outlook", "下周", "未来", "预判")
    ):
        try:
            from backend.src.hermes.skill_handlers import handle_forecast_run

            ctx = {
                "user_query": query,
                "product_id": product_id or "",
                "period": period,
            }
            fc = handle_forecast_run(ctx, plan)
            observations.append(
                {
                    "tool": "edia_run_forecast",
                    "status": fc.get("status", "ok"),
                    "summary": str(fc.get("summary") or "Forecast computed."),
                    "payload": fc,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "edia_run_forecast",
                    "status": "error",
                    "summary": str(exc),
                    "payload": {},
                }
            )

    # Cross-source skill: compare ERP vs Market
    if any(marker in query for marker in ("对比", "差距", "价差", "vs market", "compare", "spread", "actual vs market")):
        try:
            from backend.src.hermes.skill_handlers import handle_compare_erp_market

            ctx = {"product_id": product_id or "", "user_query": query}
            result = handle_compare_erp_market(ctx, plan)
            observations.append(
                {
                    "tool": "edia_compare_erp_market",
                    "status": result.get("status", "ok"),
                    "summary": str(result.get("summary") or "ERP vs Market comparison fetched."),
                    "payload": result,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "edia_compare_erp_market",
                    "status": "error",
                    "summary": str(exc),
                    "payload": {},
                }
            )

    # Cross-source skill: inventory pricing impact
    if any(marker in query for marker in ("库存影响", "stock pricing", "inventory strategy", "days on hand", "inventory impact")):
        try:
            from backend.src.hermes.skill_handlers import handle_inventory_pricing_impact

            ctx = {"product_id": product_id or "", "user_query": query}
            result = handle_inventory_pricing_impact(ctx, plan)
            observations.append(
                {
                    "tool": "edia_inventory_pricing_impact",
                    "status": result.get("status", "ok"),
                    "summary": str(result.get("summary") or "Inventory-pricing impact analyzed."),
                    "payload": result,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "edia_inventory_pricing_impact",
                    "status": "error",
                    "summary": str(exc),
                    "payload": {},
                }
            )

    # Cross-source skill: customer quote recommendation
    if any(marker in query for marker in ("报价建议", "customer quote", "pricing suggestion", "offer price", "recommended price")):
        try:
            from backend.src.hermes.skill_handlers import handle_customer_quote_recommendation

            ctx = {"product_id": product_id or "", "user_query": query}
            result = handle_customer_quote_recommendation(ctx, plan)
            observations.append(
                {
                    "tool": "edia_customer_quote_recommendation",
                    "status": result.get("status", "ok"),
                    "summary": str(result.get("summary") or "Quote recommendation generated."),
                    "payload": result,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "tool": "edia_customer_quote_recommendation",
                    "status": "error",
                    "summary": str(exc),
                    "payload": {},
                }
            )

    skill_execution: Dict[str, Any] = {}
    try:
        from backend.src.hermes.skill_executor import execute_routed_skills

        skill_execution = execute_routed_skills(plan, user_query=query, skill_ids=agent_skills or None)
        for obs in _as_list(skill_execution.get("observations")):
            if isinstance(obs, dict):
                observations.append(
                    {
                        "tool": obs.get("tool") or obs.get("skill_id"),
                        "status": obs.get("status"),
                        "summary": obs.get("summary"),
                        "payload": obs.get("payload") or {},
                        "skill_id": obs.get("skill_id"),
                    }
                )
        observations.append(
            {
                "tool": "agent_skill_router",
                "status": "ok",
                "summary": "Executed routed EDIA skills.",
                "payload": {
                    "agent_skills": agent_skills,
                    "routed_skills": skill_execution.get("routed_skills"),
                    "executed_skills": skill_execution.get("executed_skills"),
                    "presentation_mode": plan.get("presentation_mode"),
                    "instruction_blocks": skill_execution.get("instruction_blocks"),
                },
            }
        )
    except Exception as exc:
        observations.append(
            {
                "tool": "agent_skill_router",
                "status": "error",
                "summary": f"Skill execution failed: {exc}",
                "payload": {"agent_skills": agent_skills},
            }
        )

    chart_attachments = []
    visualizations: List[Dict[str, Any]] = []
    for obs in observations:
        payload = _as_dict(obs.get("payload"))
        attachment = _as_dict(payload.get("attachment"))
        if attachment.get("base64") or attachment.get("url"):
            chart_attachments.append(
                {
                    "title": payload.get("chart_type") or obs.get("tool"),
                    "url": attachment.get("url"),
                    "base64": attachment.get("base64"),
                    "mime_type": attachment.get("mime_type", "image/png"),
                }
            )
        # Collect structured visualization objects from skill execution
        viz = _as_dict(payload.get("visualization"))
        if viz.get("kind") and viz.get("data") and viz.get("series"):
            visualizations.append(viz)

    return {
        "enabled": True,
        "plan_user_query": plan.get("user_query") or user_query,
        "observation_count": len(observations),
        "observations": observations,
        "skill_execution": skill_execution,
        "chart_attachments": chart_attachments,
        "visualizations": visualizations,
    }


def format_hermes_observations_for_prompt(execution_result: Any) -> str:
    result = _as_dict(execution_result)
    observations = [item for item in _as_list(result.get("observations")) if isinstance(item, dict)]
    if not observations:
        return ""

    lines = [
        "HERMES TOOL OBSERVATIONS:",
        "Use these plan-act-observe results as verified execution context. Do not invent missing rows or chart values.",
        "Sales revenue = sum of ERP FAMOUNT per order line; never volume × weighted average price.",
    ]
    for index, observation in enumerate(observations, start=1):
        lines.append(
            f"{index}. tool={observation.get('tool')}; status={observation.get('status')}; "
            f"summary={observation.get('summary')}"
        )
        payload = _as_dict(observation.get("payload"))
        if payload:
            compact_payload = {key: value for key, value in payload.items() if value not in (None, "", [])}
            if compact_payload:
                lines.append(f"   payload={compact_payload}")

    skill_text = ""
    try:
        from backend.src.hermes.skill_executor import format_skill_observations_for_prompt

        skill_text = format_skill_observations_for_prompt(_as_dict(result.get("skill_execution")))
    except Exception:
        pass
    if skill_text:
        lines.append("")
        lines.append(skill_text)
    return "\n".join(lines)


def _citation_type_for_tool(tool: str) -> str:
    t = tool.lower()
    if "erp" in t:
        return "erp"
    if "forecast" in t:
        return "forecast"
    if "market" in t or "price" in t:
        return "market_data"
    if "rag" in t or "intel" in t or "search" in t or "web" in t:
        return "intelligence"
    if "db" in t or "mysql" in t or "sql" in t:
        return "database"
    return "tool"


def build_citations_from_hermes_execution(hermes_execution: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build structured citation objects from Hermes execution observations."""
    observations: List[Dict[str, Any]] = []
    if isinstance(hermes_execution, dict):
        observations.extend(_as_list(hermes_execution.get("observations")))
        skill_execution = _as_dict(hermes_execution.get("skill_execution"))
        observations.extend(_as_list(skill_execution.get("observations")))

    citations: List[Dict[str, str]] = []
    seen: set[str] = set()
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        tool = str(obs.get("tool") or obs.get("skill_id") or "").strip()
        if not tool:
            continue
        if tool in seen:
            continue
        seen.add(tool)
        status = str(obs.get("status") or "").strip()
        if status == "error":
            continue
        summary = str(obs.get("summary") or "").strip()
        citations.append({
            "id": f"cite_{len(citations) + 1}",
            "source": tool,
            "type": _citation_type_for_tool(tool),
            "description": summary,
        })
    return citations
