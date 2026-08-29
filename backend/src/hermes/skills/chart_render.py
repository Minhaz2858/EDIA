"""Render chat charts as interactive structured data for Hermes skill execution."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Dark theme colors matching DynamicComparisonChart
_DARK_BG = "#0B1929"
_DARK_GRID = "#1F2D3D"
_DARK_TEXT = "#9AA5B4"
_DARK_TITLE = "#E8E8E8"
_SERIES_COLORS = ["#00A6A6", "#D4A437", "#6366F1", "#F59E0B", "#10B981", "#EF4444"]


def _make_visualization(
    kind: str,
    title: str,
    x_key: str,
    series: List[Dict[str, Any]],
    data: List[Dict[str, Any]],
    subtitle: Optional[str] = None,
    unit: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a ChatVisualization-compatible dict."""
    latest = None
    if data and kind in ("line", "multi_line", "bar"):
        # Find the last non-null numeric value across all series keys
        for row in reversed(data):
            for s in series:
                val = row.get(s["key"])
                if isinstance(val, (int, float)):
                    latest = val
                    break
            if latest is not None:
                break

    result: Dict[str, Any] = {
        "kind": kind,
        "title": title,
        "x_key": x_key,
        "series": series,
        "data": data,
    }
    if subtitle:
        result["subtitle"] = subtitle
    if unit:
        result["unit"] = unit
    if latest is not None:
        result["summary"] = {"latest": latest}
    return result


def render_chat_chart(
    *,
    chart_type: str = "price_trend",
    product_id: str = "",
    period: str = "",
    session_id: str = "",
    language: str = "zh",
) -> Dict[str, Any]:
    """Render a verified chart. Returns ChatVisualization-compatible structured data."""
    chart_type = str(chart_type or "price_trend").strip().lower()
    product_id = str(product_id or "").strip()
    is_en = str(language or "zh").lower().startswith("en")

    try:
        if chart_type in ("price_trend", "price", "market"):
            return _render_price_trend_chart(
                product_id=product_id, session_id=session_id, is_en=is_en
            )
        if chart_type in ("sales_volume", "sales", "revenue"):
            return _render_sales_chart(
                product_id=product_id,
                period=period,
                session_id=session_id,
                is_en=is_en,
                metric=chart_type,
            )
        return {"status": "error", "error": f"Unsupported chart_type: {chart_type}"}
    except Exception as exc:
        logger.exception("Chart render failed")
        return {"status": "error", "error": str(exc)}


def _render_price_trend_chart(
    *,
    product_id: str,
    session_id: str,
    is_en: bool,
) -> Dict[str, Any]:
    from backend.src.service.market_data_service import market_data

    if not product_id:
        from backend.src.domain.product_resolver import extract_product_ids_in_text

        product_id = "cracked_c5"

    history = market_data.get_price_history(product_id) or []
    if not history:
        return {"status": "error", "error": f"No price history for product '{product_id}'."}

    points = history[-60:]
    dates = [str(p.get("date") or p.get("price_date") or "")[:10] for p in points]
    prices = [float(p.get("price") or 0) for p in points]

    data: List[Dict[str, Any]] = [
        {"date": d, "price": p} for d, p in zip(dates, prices)
    ]

    series: List[Dict[str, Any]] = [
        {
            "key": "price",
            "label": is_en and "Price" or "价格",
            "color": _SERIES_COLORS[0],
            "chart_type": "line",
        }
    ]

    title = f"{product_id} — {is_en and 'Price Trend' or '价格走势'}"
    visualization = _make_visualization(
        kind="line",
        title=title,
        x_key="date",
        series=series,
        data=data,
        subtitle=f"{len(points)} {is_en and 'data points' or '个数据点'}",
        unit="¥",
    )
    return {
        "status": "ok",
        "chart_type": "price_trend",
        "product_id": product_id,
        "visualization": visualization,
    }


def _render_sales_chart(
    *,
    product_id: str,
    period: str,
    session_id: str,
    is_en: bool,
    metric: str,
) -> Dict[str, Any]:
    from backend.src.tools.erp_tools import query_sales_summary
    from backend.src.engine.decision_graph import _extract_erp_sales_summary_rows

    raw = query_sales_summary(product_id=product_id or None, period=period or None)
    parsed = _extract_erp_sales_summary_rows(str(raw or ""))
    rows: List[Dict[str, Any]] = list(parsed.get("rows") or [])
    if not rows:
        return {"status": "error", "error": "No ERP sales rows for chart."}

    labels = [str(r.get("product") or "")[:12] for r in rows[:12]]
    if metric == "revenue":
        values = [float(r.get("revenue_cny") or 0) for r in rows[:12]]
        ylabel = is_en and "Revenue (CNY)" or "销售额(元)"
        unit = "¥"
        series_key = "revenue"
    else:
        values = [float(r.get("volume_ton") or 0) for r in rows[:12]]
        ylabel = is_en and "Volume (ton)" or "销量(吨)"
        unit = "吨"
        series_key = "volume"

    flat_data: List[Dict[str, Any]] = [
        {"product": l, series_key: v} for l, v in zip(labels, values)
    ]

    series: List[Dict[str, Any]] = [
        {
            "key": series_key,
            "label": ylabel,
            "color": _SERIES_COLORS[1],
            "chart_type": "bar",
        }
    ]

    title = is_en and "ERP Sales by Product" or "ERP 分产品销售"
    visualization = _make_visualization(
        kind="bar",
        title=title,
        x_key="product",
        series=series,
        data=flat_data,
        subtitle=f"{len(flat_data)} {is_en and 'products' or '个产品'}",
        unit=unit,
    )
    return {
        "status": "ok",
        "chart_type": metric,
        "visualization": visualization,
    }