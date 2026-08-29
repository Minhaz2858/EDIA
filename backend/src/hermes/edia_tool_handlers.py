"""EDIA domain tool handlers invoked by Hermes agent tools."""

from __future__ import annotations

import json
from typing import Any, Dict

from backend.src.hermes.edia_context import get_edia_context, require_edia_context
from backend.src.hermes.execution import execute_hermes_plan
from backend.src.hermes.hermes_agent import build_hermes_plan, resolve_followup_standalone_query


def _json_result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def handle_edia_build_plan(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    query = str(args.get("query") or ctx.user_query or "").strip()
    plan = build_hermes_plan(
        query,
        llm_service=ctx.llm_service,
        conversation_history=ctx.conversation_history,
        prior_turn_state=ctx.turn_state,
    )
    plan_payload = plan.to_dict()
    if not ctx.hermes_plan or ctx.turn_state.get("is_follow_up"):
        ctx.hermes_plan = plan_payload
    return _json_result({"status": "ok", "plan": plan_payload})


def handle_edia_resolve_followup(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    query = str(args.get("query") or ctx.user_query or "").strip()
    resolved = resolve_followup_standalone_query(
        query,
        conversation_history=ctx.conversation_history,
        prior_turn_state=ctx.turn_state,
    )
    ctx.turn_state.update(resolved)
    if resolved.get("is_follow_up"):
        ctx.user_query = str(resolved.get("standalone_query") or ctx.user_query)
    return _json_result({"status": "ok", "followup": resolved})


def handle_edia_execute_plan_observations(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    plan = args.get("plan") if isinstance(args.get("plan"), dict) else ctx.hermes_plan
    if not plan:
        plan = build_hermes_plan(
            ctx.user_query,
            llm_service=ctx.llm_service,
            conversation_history=ctx.conversation_history,
            prior_turn_state=ctx.turn_state,
        ).to_dict()
        ctx.hermes_plan = plan
    result = execute_hermes_plan(plan, user_query=ctx.user_query)
    return _json_result({"status": "ok", "execution": result})


def handle_edia_prepare_analysis(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    if ctx.decision_service is None:
        return _json_result({"status": "error", "error": "decision_service unavailable"})

    query = str(args.get("query") or ctx.user_query or "").strip()
    hermes_plan = ctx.hermes_plan
    if not hermes_plan:
        hermes_plan = build_hermes_plan(
            query,
            llm_service=ctx.llm_service,
            conversation_history=ctx.conversation_history,
            prior_turn_state=ctx.turn_state,
        ).to_dict()
        ctx.hermes_plan = hermes_plan

    try:
        prep = ctx.decision_service.prepare_request(
            query,
            conversation_history=ctx.conversation_history,
            user_prefs=ctx.user_prefs,
            user_id=ctx.user_id or None,
            session_id=ctx.session_id or None,
            hermes_plan=hermes_plan,
        )
        return _json_result(
            {
                "status": "ok",
                "partial_context": prep.get("partial_context") or {},
                "assembled_prompt": prep.get("assembled_prompt") or "",
                "hermes_execution": prep.get("hermes_execution") or {},
                "task_type": str((prep.get("task_type") or "")),
            }
        )
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_stream_synthesis(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    if ctx.llm_service is None:
        return _json_result({"status": "error", "error": "llm_service unavailable"})

    system_prompt = str(args.get("system_prompt") or "").strip()
    user_prompt = str(
        args.get("user_prompt")
        or args.get("assembled_prompt")
        or ctx.user_query
        or ""
    ).strip()
    if not system_prompt:
        system_prompt = (
            "You are EDIA assistant. Provide a grounded business answer using only "
            "verified observations from EDIA tools."
        )

    parts: list[str] = []
    try:
        stream_fn = getattr(ctx.llm_service, "generate_completion_stream", None)
        if not callable(stream_fn):
            full = ctx.llm_service.generate_completion(
                system_prompt,
                user_prompt,
                route_hint="external",
            )
            text = str(full or "")
            if text and ctx.stream_delta_callback:
                ctx.stream_delta_callback(text)
            parts.append(text)
        else:
            for delta in stream_fn(system_prompt, user_prompt, route_hint="external"):
                piece = str(delta or "")
                if not piece:
                    continue
                parts.append(piece)
                if ctx.stream_delta_callback:
                    ctx.stream_delta_callback(piece)
        markdown = "".join(parts).strip()
        return _json_result(
            {
                "status": "success" if markdown else "error",
                "markdown": markdown,
                "task_type": "conversational",
                "source": "edia_stream_synthesis",
            }
        )
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_generate_report(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    if ctx.decision_service is None:
        return _json_result({"status": "error", "error": "decision_service unavailable"})

    query = str(args.get("query") or ctx.user_query or "").strip()
    hermes_plan = ctx.hermes_plan
    if not hermes_plan:
        hermes_plan = build_hermes_plan(
            query,
            llm_service=ctx.llm_service,
            conversation_history=ctx.conversation_history,
            prior_turn_state=ctx.turn_state,
        ).to_dict()
        ctx.hermes_plan = hermes_plan

    try:
        report = ctx.decision_service.process_request(
            query,
            conversation_history=ctx.conversation_history,
            user_prefs=ctx.user_prefs,
            user_id=ctx.user_id or None,
            session_id=ctx.session_id or None,
            hermes_plan=hermes_plan,
        )
        return _json_result(
            {
                "status": report.status,
                "markdown": report.raw_markdown,
                "task_type": report.task_type.value,
                "context": report.context.to_dict() if hasattr(report.context, "to_dict") else {},
                "enhancement_applied": dict(report.enhancement_applied or {}),
                "quality_flags": [
                    {
                        "code": flag.code,
                        "severity": flag.severity,
                        "message": flag.message,
                        "source": flag.source,
                    }
                    for flag in (report.quality_flags or [])
                ],
                "resolved_chat_mode": report.resolved_chat_mode,
                "error_message": report.error_message,
            }
        )
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_query_erp_orders(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    plan = ctx.hermes_plan or build_hermes_plan(
        ctx.user_query,
        llm_service=ctx.llm_service,
        conversation_history=ctx.conversation_history,
        prior_turn_state=ctx.turn_state,
    ).to_dict()
    normalized = plan.get("normalized_query") if isinstance(plan.get("normalized_query"), dict) else {}
    product_id = str(args.get("product_id") or "").strip()
    if not product_id:
        product_ids = normalized.get("product_ids") or plan.get("product_ids") or []
        if product_ids:
            product_id = str(product_ids[0])
    period = str(args.get("period") or ctx.turn_state.get("period") or ctx.user_query or "").strip()
    limit = int(args.get("limit") or 50)

    try:
        from backend.src.tools.erp_tools import query_sales_order_details

        result = query_sales_order_details(
            product_id=product_id or None,
            period=period,
            limit=limit,
        )
        return _json_result({"status": "ok", "payload": result})
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_query_erp_sales_summary(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    product_id = str(args.get("product_id") or "").strip()
    period = str(args.get("period") or ctx.turn_state.get("period") or ctx.user_query or "").strip()
    try:
        from backend.src.tools.erp_tools import query_sales_summary

        result = query_sales_summary(product_id=product_id or None, period=period)
        return _json_result(
            {
                "status": "ok",
                "payload": result,
                "period": period,
                "product_id": product_id or None,
                "source": "erp_v_sale_orderentry",
            }
        )
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_discover_database_capability(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    query = str(args.get("query") or ctx.user_query or "").strip()
    limit = int(args.get("limit") or 5)
    try:
        from backend.src.tools.db_capability_registry import discover_database_candidates

        result = discover_database_candidates(query, limit=limit)
        return _json_result({"status": "ok", "payload": result})
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_run_forecast(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    plan = ctx.hermes_plan or {}
    product_id = str(args.get("product_id") or "").strip()
    if not product_id:
        normalized = plan.get("normalized_query") if isinstance(plan.get("normalized_query"), dict) else {}
        product_ids = list(normalized.get("product_ids") or plan.get("product_ids") or [])
        product_id = str(product_ids[0]) if product_ids else "cracked_c5"
    try:
        from backend.src.hermes.skill_handlers import handle_forecast_run

        result = handle_forecast_run(
            {
                "user_query": ctx.user_query,
                "product_id": product_id,
                "period": str(args.get("period") or ctx.user_query or ""),
                "session_id": ctx.session_id or "",
                "language": str((ctx.user_prefs or {}).get("language") or "zh"),
            },
            plan,
        )
        return _json_result(result)
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_render_chart(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    plan = ctx.hermes_plan or {}
    product_id = str(args.get("product_id") or "").strip()
    if not product_id:
        normalized = plan.get("normalized_query") if isinstance(plan.get("normalized_query"), dict) else {}
        product_ids = list(normalized.get("product_ids") or plan.get("product_ids") or [])
        product_id = str(product_ids[0]) if product_ids else ""
    try:
        from backend.src.hermes.skills.chart_render import render_chat_chart

        result = render_chat_chart(
            chart_type=str(args.get("chart_type") or "price_trend"),
            product_id=product_id,
            period=str(args.get("period") or ctx.user_query or ""),
            session_id=str(ctx.session_id or ""),
            language=str((ctx.user_prefs or {}).get("language") or "zh"),
        )
        if result.get("status") == "ok" and ctx.hermes_plan is not None:
            viz = result.get("visualization")
            if viz:
                visualizations = list((ctx.hermes_plan or {}).get("visualizations") or [])
                visualizations.append(viz)
                ctx.hermes_plan["visualizations"] = visualizations
        return _json_result(result)
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_query_erp_customer_sales(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    product_id = str(args.get("product_id") or "").strip()
    period = str(args.get("period") or ctx.user_query or "").strip()
    try:
        from backend.src.tools.erp_tools import query_customer_sales_report

        result = query_customer_sales_report(product_id=product_id or None, period=period)
        return _json_result({"status": "ok", "payload": result})
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_query_erp_inventory(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    product_id = str(args.get("product_id") or "").strip()
    try:
        from backend.src.tools.erp_tools import query_inventory_levels

        text = query_inventory_levels(product_id=product_id or None)
        return _json_result({"status": "ok", "payload": text})
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_execute_db_capability(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    plan = ctx.hermes_plan
    if not plan:
        plan = build_hermes_plan(
            ctx.user_query,
            llm_service=ctx.llm_service,
            conversation_history=ctx.conversation_history,
            prior_turn_state=ctx.turn_state,
        ).to_dict()
        ctx.hermes_plan = plan
    capability_id = str(args.get("capability_id") or "").strip()
    if capability_id:
        normalized = dict(plan.get("normalized_query") or {})
        normalized["capability"] = capability_id
        plan["normalized_query"] = normalized
    try:
        from backend.src.hermes.skill_handlers import handle_db_capability_execute

        normalized = plan.get("normalized_query") if isinstance(plan.get("normalized_query"), dict) else {}
        product_ids = list(normalized.get("product_ids") or plan.get("product_ids") or [])
        result = handle_db_capability_execute(
            {
                "user_query": ctx.user_query,
                "product_id": str(product_ids[0]) if product_ids else "",
                "period": str(args.get("period") or ctx.user_query or ""),
            },
            plan,
        )
        return _json_result(result)
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_get_background_insights(args: Dict[str, Any], **_: Any) -> str:
    try:
        from backend.src.hermes.skill_handlers import handle_background_insights

        result = handle_background_insights({"user_query": require_edia_context().user_query}, {})
        return _json_result(result)
    except Exception as exc:
        return _json_result({"status": "error", "error": str(exc)})


def handle_edia_forecast_policy(args: Dict[str, Any], **_: Any) -> str:
    ctx = require_edia_context()
    query = str(args.get("query") or ctx.user_query or "").strip()
    plan = ctx.hermes_plan
    if not plan:
        plan = build_hermes_plan(
            query,
            llm_service=ctx.llm_service,
            conversation_history=ctx.conversation_history,
            prior_turn_state=ctx.turn_state,
        ).to_dict()
        ctx.hermes_plan = plan
    policy = plan.get("forecast_policy") if isinstance(plan.get("forecast_policy"), dict) else {}
    return _json_result(
        {
            "status": "ok",
            "domains": list(plan.get("domains") or []),
            "product_ids": list(plan.get("product_ids") or []),
            "forecast_policy": policy,
            "validation_gates": list(plan.get("validation_gates") or []),
        }
    )


def current_hermes_plan() -> Dict[str, Any]:
    ctx = get_edia_context()
    if ctx and ctx.hermes_plan:
        return dict(ctx.hermes_plan)
    return {}
