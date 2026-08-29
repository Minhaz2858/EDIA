"""Python handlers for executable EDIA skills (wired into skill_registry)."""

from __future__ import annotations

from typing import Any, Dict

from backend.src.hermes.skill_registry import get_skill


def _ok(summary: str, **payload: Any) -> Dict[str, Any]:
    return {"status": "ok", "summary": summary, **payload}


def _err(message: str) -> Dict[str, Any]:
    return {"status": "error", "summary": message, "error": message}


def handle_forecast_run(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    from backend.src.service.forecasting_service import ForecastingService

    product_id = str(context.get("product_id") or "").strip()
    if not product_id:
        product_ids = list(plan.get("product_ids") or [])
        product_id = str(product_ids[0]) if product_ids else "cracked_c5"

    service = ForecastingService()
    result = service.forecast(product_id, hermes_plan=plan)
    scenarios = result.get("scenarios") or {}
    return _ok(
        f"Forecast computed for {product_id}.",
        product_id=product_id,
        latest_price=result.get("latest_price"),
        scenarios=scenarios,
        method_notes=result.get("method_notes"),
        seasonal_adj_pct=result.get("seasonal_adj_pct"),
    )


def handle_chart_render(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    from backend.src.hermes.skills.chart_render import render_chat_chart

    query = str(context.get("user_query") or "").lower()
    chart_type = "price_trend"
    if any(m in query for m in ("销售", "销量", "sales", "revenue", "销售额")):
        chart_type = "revenue" if any(m in query for m in ("销售额", "revenue", "销售金额")) else "sales_volume"

    result = render_chat_chart(
        chart_type=chart_type,
        product_id=str(context.get("product_id") or ""),
        period=str(context.get("period") or ""),
        session_id=str(context.get("session_id") or ""),
        language=str(context.get("language") or "zh"),
    )
    if result.get("status") != "ok":
        return _err(str(result.get("error") or "chart render failed"))
    return _ok(
        f"Rendered {result.get('chart_type')} chart.",
        chart_type=result.get("chart_type"),
        attachment=result.get("attachment"),
        product_id=result.get("product_id"),
    )


def handle_background_insights(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from backend.src.skills.background import background_skill_scheduler

        results = background_skill_scheduler.get_all_latest()
    except Exception as exc:
        return _err(str(exc))

    alerts = []
    summaries = []
    for name, result in results.items():
        if result is None:
            continue
        if result.summary:
            summaries.append({"skill": name, "summary": result.summary, "status": result.status})
        for alert in result.alerts or []:
            alerts.append({"skill": name, "alert": alert, "ran_at": result.ran_at})

    return _ok(
        f"Collected {len(alerts)} background alerts from {len(summaries)} skills.",
        alerts=alerts[:20],
        summaries=summaries[:12],
        total_alerts=len(alerts),
    )


def handle_erp_customer_sales(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    from backend.src.tools.erp_tools import query_customer_sales_report

    result = query_customer_sales_report(
        product_id=str(context.get("product_id") or "") or None,
        period=str(context.get("period") or "") or None,
    )
    if isinstance(result, dict) and result.get("error"):
        return _err(str(result["error"]))
    return _ok(
        "Customer sales report fetched.",
        payload=result,
        total_revenue=result.get("total_revenue") if isinstance(result, dict) else None,
    )


def handle_erp_inventory(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    from backend.src.tools.erp_tools import query_inventory_levels

    text = query_inventory_levels(product_id=str(context.get("product_id") or "") or None)
    return _ok("Inventory levels fetched.", summary_text=text)


def handle_erp_daily_chart(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Handle erp-daily-chart skill: generate interactive daily sales chart."""
    from backend.src.hermes.skills.erp_daily_chart_skill import build_erp_daily_bar_visualization

    product_id = str(context.get("product_id") or "") or None
    period = str(context.get("period") or "") or None
    language = str(context.get("language") or "zh")

    viz = build_erp_daily_bar_visualization(
        product_id=product_id,
        period=period,
        language=language,
    )

    if viz is None:
        return _err("No daily sales data available for chart generation.")

    return _ok(
        "Daily ERP chart generated.",
        visualization=viz,
        chart_type=viz.get("kind", "bar"),
    )


def handle_dynamic_chart(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Handle dynamic-chart skill: build multi-metric comparison visualization."""
    from backend.src.hermes.skills.dynamic_chart_skill import build_dynamic_comparison_visualization

    user_query = str(context.get("user_query") or "")
    product_ids_raw = context.get("product_ids") or []
    if isinstance(product_ids_raw, str):
        product_ids = [product_ids_raw]
    elif isinstance(product_ids_raw, list):
        product_ids = [str(p) for p in product_ids_raw]
    else:
        product_ids = []
    language = str(context.get("language") or "zh")
    period = str(context.get("period") or "") or None

    viz = build_dynamic_comparison_visualization(
        query=user_query,
        product_ids=product_ids,
        language=language,
        source="auto",
        period=period,
    )
    if viz is None:
        return _err("No data available for dynamic chart generation.")
    return _ok(
        f"Dynamic chart generated for {len(viz.get('series', []))} metrics.",
        visualization=viz,
        chart_type=viz.get("kind", "multi_axis_composed"),
    )


def handle_db_capability_execute(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    from backend.src.tools.db_capability_registry import get_database_capability, resolve_database_capability
    from backend.src.tools.erp_tools import (
        build_erp_context,
        query_customer_sales_report,
        query_sales_order_details,
        query_sales_summary,
    )

    query = str(context.get("user_query") or "")
    capability_id = str(
        (plan.get("normalized_query") or {}).get("capability")
        or (plan.get("capabilities") or [""])[0]
        if plan.get("capabilities")
        else ""
    ).strip()
    cap = get_database_capability(capability_id) if capability_id else resolve_database_capability(query)
    if cap is None:
        return _err("No database capability matched.")

    product_id = str(context.get("product_id") or "") or None
    period = str(context.get("period") or "") or None

    if cap.capability_id == "erp_order_details":
        payload = query_sales_order_details(product_id=product_id, period=period, limit=50)
    elif cap.capability_id == "erp_customer_sales":
        payload = query_customer_sales_report(product_id=product_id, period=period)
    elif cap.capability_id == "erp_sales_summary":
        payload = query_sales_summary(product_id=product_id, period=period)
        if isinstance(payload, str):
            from backend.src.engine.decision_graph import _extract_erp_sales_summary_rows

            parsed = _extract_erp_sales_summary_rows(payload)
            return _ok(
                "ERP sales summary executed.",
                capability_id=cap.capability_id,
                summary_text=payload,
                total_revenue=parsed.get("total_revenue"),
                rows=parsed.get("rows"),
            )
    else:
        payload = build_erp_context(
            product_id=product_id,
            period=period,
            query_type="auto",
            user_query=query,
        )

    return _ok(
        f"Executed capability {cap.capability_id}.",
        capability_id=cap.capability_id,
        payload=payload if not isinstance(payload, str) else {"text": payload},
    )


def handle_ecisco_weekly_report(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ecisco-weekly-report skill: generate live C5/C9 weekly report on demand."""
    import asyncio
    import concurrent.futures

    language = str(context.get("language") or "zh")
    user_id = str(context.get("user_id") or "system")
    session_id = str(context.get("session_id") or "system")
    week_start_str = str(context.get("week_start") or "").strip() or None
    week_end_str = str(context.get("week_end") or "").strip() or None

    async def _run() -> Dict[str, Any]:
        from backend.src.db.sqlite_db import db as _db
        from backend.src.db.market_db import market_db as _market_db_inst
        from backend.src.service.weekly_report_generator import generate_weekly_report
        from backend.src.service.decision_service import decision_service

        _wr_llm = getattr(decision_service, "llm", None)
        if _wr_llm is None:
            raise RuntimeError("LLM not available for weekly report generation")

        result = await generate_weekly_report(
            db=_db,
            mysql_engine=_market_db_inst.engine,
            llm=_wr_llm,
            language=language,
            week_start_str=week_start_str,
            week_end_str=week_end_str,
            user_id=user_id,
            session_id=session_id,
            static_base_url="/api/reports-static",
            current_week=True,
        )
        return result

    try:
        result = asyncio.run(_run())
    except RuntimeError:
        # Already inside a running event loop (e.g. async server context)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(asyncio.run, _run()).result()

    products_count = int(result.get("products_count") or 0)
    week_start = str(result.get("week_start") or "")
    week_end = str(result.get("week_end") or "")
    freshness_banner = (
        f"Report covers {week_start} to {week_end} ({products_count} products, live data)."
        if language.startswith("en") else
        f"报告覆盖 {week_start} 至 {week_end}（{products_count} 个产品，实时数据）。"
    )

    # Action chips for post-report workflow
    action_chips = [
        {"label": "生成 PPTX" if language.startswith("zh") else "Generate PPTX", "skill_id": "pptx-maker", "intent": "generate_pptx"},
        {"label": "聚焦 异戊二烯" if language.startswith("zh") else "Focus Isoprene", "skill_id": "ecisco-weekly-report", "intent": "focus_product", "product_id": "isoprene"},
        {"label": "添加 ERP 销售数据" if language.startswith("zh") else "Add ERP Sales", "skill_id": "erp-customer-sales", "intent": "add_erp_sales"},
        {"label": "本周 vs 上周对比" if language.startswith("zh") else "WoW Comparison", "skill_id": "ecisco-weekly-report", "intent": "week_over_week"},
    ]

    return _ok(
        freshness_banner,
        markdown=result.get("markdown"),
        report_id=result.get("report_id"),
        week_start=week_start,
        week_end=week_end,
        products_count=products_count,
        preview_url=result.get("preview_url"),
        generated_at=result.get("generated_at"),
        duration_sec=result.get("duration_sec"),
        action_chips=action_chips,
        freshness_banner=freshness_banner,
    )


def handle_compare_erp_market(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-source skill: compare ERP actuals with market reference prices."""
    product_id = str(context.get("product_id") or "").strip() or "cracked_c5"
    try:
        from backend.src.service.erp_kpi_service import ErpKPIService
        from backend.src.service.market_data_service import market_data

        kpi = ErpKPIService().get_product_kpi(product_id)
        regional = market_data.get_regional_prices(product_id)
    except Exception as exc:
        return _err(str(exc))

    erp_price = getattr(kpi, "avg_price_30d", None) if kpi else None
    market_price = regional.get("east_china") if regional else None
    spread = None
    if erp_price and market_price:
        spread = round(erp_price - market_price, 2)

    return _ok(
        "ERP vs Market comparison fetched.",
        product_id=product_id,
        erp_price=erp_price,
        market_price=market_price,
        spread=spread,
        regional_prices=regional,
        kpi_summary={
            "sales_30d_ton": getattr(kpi, "sales_30d_ton", None),
            "inventory_tons": getattr(kpi, "inventory_tons", None),
            "inventory_days": getattr(kpi, "inventory_days", None),
        } if kpi else {},
    )


def handle_inventory_pricing_impact(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-source skill: analyze how inventory levels affect pricing strategy."""
    product_id = str(context.get("product_id") or "").strip() or "cracked_c5"
    try:
        from backend.src.service.erp_kpi_service import ErpKPIService
        from backend.src.service.market_data_service import market_data

        kpi = ErpKPIService().get_product_kpi(product_id)
        regional = market_data.get_regional_prices(product_id)
    except Exception as exc:
        return _err(str(exc))

    inventory_tons = getattr(kpi, "inventory_tons", None) if kpi else None
    inventory_days = getattr(kpi, "inventory_days", None) if kpi else None
    market_price = regional.get("east_china") if regional else None

    pressure = "neutral"
    if inventory_days is not None:
        if inventory_days > 30:
            pressure = "destock_pressure"
        elif inventory_days < 7:
            pressure = "shortage_risk"

    return _ok(
        "Inventory-pricing impact analysis fetched.",
        product_id=product_id,
        inventory_tons=inventory_tons,
        inventory_days=inventory_days,
        market_price=market_price,
        pricing_pressure=pressure,
        recommendation=(
            "Consider promotional pricing to reduce inventory."
            if pressure == "destock_pressure"
            else "Consider raising prices due to low stock."
            if pressure == "shortage_risk"
            else "Inventory level is healthy."
        ),
    )


def handle_customer_quote_recommendation(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-source skill: recommend customer quotes using ERP history + market prices."""
    product_id = str(context.get("product_id") or "").strip() or "cracked_c5"
    customer_id = str(context.get("customer_id") or "").strip() or None
    try:
        from backend.src.service.erp_kpi_service import ErpKPIService
        from backend.src.service.market_data_service import market_data
        from backend.src.tools.erp_tools import query_customer_sales_report

        kpi = ErpKPIService().get_product_kpi(product_id)
        regional = market_data.get_regional_prices(product_id)
        customer_report = query_customer_sales_report(product_id=product_id, period=None)
    except Exception as exc:
        return _err(str(exc))

    market_price = regional.get("east_china") if regional else None
    avg_erp_price = getattr(kpi, "avg_price_30d", None) if kpi else None

    suggested_floor = None
    suggested_ceiling = None
    if market_price:
        suggested_floor = round(market_price * 0.95, 0)
        suggested_ceiling = round(market_price * 1.08, 0)

    return _ok(
        "Customer quote recommendation generated.",
        product_id=product_id,
        customer_id=customer_id,
        market_reference=market_price,
        avg_erp_price=avg_erp_price,
        suggested_floor=suggested_floor,
        suggested_ceiling=suggested_ceiling,
        customer_report=customer_report if isinstance(customer_report, dict) else {},
    )


def attach_skill_handlers() -> None:
    """Bind Python handlers to registry entries (call once at import)."""
    from backend.src.hermes.skill_registry import register_handler

    register_handler("forecast-run", handle_forecast_run)
    register_handler("chart-render", handle_chart_render)
    register_handler("background-insights", handle_background_insights)
    register_handler("erp-customer-sales", handle_erp_customer_sales)
    register_handler("erp-inventory", handle_erp_inventory)
    register_handler("db-capability-execute", handle_db_capability_execute)
    register_handler("erp-daily-chart", handle_erp_daily_chart)
    register_handler("dynamic-chart", handle_dynamic_chart)
    register_handler("ecisco-weekly-report", handle_ecisco_weekly_report)
    register_handler("compare-erp-market", handle_compare_erp_market)
    register_handler("inventory-pricing-impact", handle_inventory_pricing_impact)
    register_handler("customer-quote-recommendation", handle_customer_quote_recommendation)


attach_skill_handlers()
