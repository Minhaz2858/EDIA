"""Dynamic chart skill — analyzes user queries and generates multi-metric comparison visualizations.

This skill is domain-agnostic: it works with ERP data, market data, or any registered
data source. It detects comparison/combination intents, fetches the relevant metrics,
recommends the best chart type, and builds a ChatVisualization-compatible payload.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Query Analyzer
# ──────────────────────────────────────────────────────────────────────────────

# Keywords that indicate a multi-metric comparison intent
_COMPARISON_MARKERS_ZH = (
    "对比",
    "比较",
    "综合",
    "一起",
    "同时",
    "同时展示",
    "综合分析",
    "多指标",
)
_COMPARISON_MARKERS_EN = (
    "compare",
    "comparison",
    "vs",
    "versus",
    "together",
    "combined",
    "multi-metric",
    "综合",
    "对比",
)

# Metric keywords — maps to ERP data field names
_METRIC_KEYWORDS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "revenue": (
        ("revenue", "销售额", "销售金额", "销售收入", "营收", "sales amount"),
        ("revenue_cny", "famount", "sales_amount", "revenue"),
    ),
    "volume": (
        ("volume", "销量", "销售量", "销售数量", "sales volume"),
        ("volume_ton", "quantity", "volume", "sales_quantity"),
    ),
    "orders": (
        ("orders", "订单", "订单数", "order count", "order count", "订单数量"),
        ("order_count", "orders", "fcnt"),
    ),
    "price": (
        ("price", "价格", "单价", "平均价", "avg price"),
        ("price", "avg_price"),
    ),
    "profit": (
        ("profit", "利润", "毛利", "gross profit", "margin"),
        ("profit", "gross_profit", "margin"),
    ),
}


def _extract_metrics_from_query(query: str) -> List[str]:
    """Return metric names detected in the query."""
    q = str(query or "").lower()
    detected: List[str] = []
    for metric_name, (keywords, _) in _METRIC_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            if metric_name not in detected:
                detected.append(metric_name)
    return detected


def _extract_time_range(query: str) -> Tuple[Optional[date], Optional[date]]:
    """Parse time range from natural-language query. Returns (start, end)."""
    today = date.today()
    end = today

    q = str(query or "").lower()

    # "last N days"
    m = re.search(r"last\s*(\d+)\s*days?", q)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n), end

    # "最近N天" / "近N天"
    m = re.search(r"(?:最近|近)(\d+)\s*天", q)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n), end

    # "last week"
    if "last week" in q:
        return today - timedelta(weeks=1), end

    # "this week"
    if "this week" in q or "本周" in q:
        return today - timedelta(days=today.weekday()), end

    # "last month"
    if "last month" in q or "上月" in q:
        return today - timedelta(days=30), end

    # "this month"
    if "this month" in q or "本月" in q:
        return today - timedelta(days=today.day - 1), end

    # Default: last 30 days
    return today - timedelta(days=30), end


def _is_dynamic_comparison_intent(query: str) -> bool:
    """Return True if the query looks like a multi-metric comparison request."""
    q = str(query or "").lower()
    has_comparison = any(
        kw in q
        for kw in (*_COMPARISON_MARKERS_ZH, *_COMPARISON_MARKERS_EN)
    )
    has_metrics = len(_extract_metrics_from_query(query)) >= 1
    return has_comparison and has_metrics


# ──────────────────────────────────────────────────────────────────────────────
# Data Resolver — generic interface
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_erp_multi_metric(
    product_id: Optional[str],
    metrics: List[str],
    start_date: date,
    end_date: date,
) -> List[Dict[str, Any]]:
    """Fetch multiple ERP metrics for a product over a date range.

    Returns a list of dicts, one per date, with keys matching metric names.
    """
    from sqlalchemy import text
    from backend.src.db.sqlite_db import Database
    from backend.src.tools.erp_tools import (
        _canonical_product_id,
        _PRODUCT_TO_MATERIAL_NAMES,
        _ALL_MATERIAL_NAMES,
        _material_name_params,
    )

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical) or []
    else:
        material_names = _ALL_MATERIAL_NAMES

    if not material_names:
        return []

    db = Database()
    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date

    # Build column list based on requested metrics
    revenue_col = (
        "SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) AS revenue_cny"
    )
    volume_col = "SUM(COALESCE(FDELIQTY, 0)) AS volume_ton"
    orders_col = "COUNT(DISTINCT FBILLNO) AS order_count"
    price_col = (
        "SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
        "/ NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS avg_price"
    )

    cols = []
    for m in metrics:
        if m == "revenue":
            cols.append(revenue_col)
        elif m == "volume":
            cols.append(volume_col)
        elif m == "orders":
            cols.append(orders_col)
        elif m == "price":
            cols.append(price_col)

    if not cols:
        return []

    # Query daily granularity
    query = f"""
        SELECT
            DATE(PLANDATE) AS d,
            {', '.join(cols)}
        FROM erp_v_sale_orderentry
        WHERE material_name IN ({placeholders})
          AND PLANDATE IS NOT NULL
          AND DATE(PLANDATE) >= :start_date
          AND DATE(PLANDATE) <= :end_date
        GROUP BY DATE(PLANDATE)
        ORDER BY d
    """

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
            result: List[Dict[str, Any]] = []
            for row in rows:
                d = str(row[0])
                entry: Dict[str, Any] = {"date": d}
                for i, m in enumerate(metrics):
                    val = row[i + 1]
                    if val is not None:
                        try:
                            entry[m] = float(val)
                        except (TypeError, ValueError):
                            pass
                if len(entry) > 1:  # has at least date + one metric
                    result.append(entry)
            return result
    except Exception:
        return []


def _resolve_market_multi_metric(
    product_ids: List[str],
    metrics: List[str],
    start_date: date,
    end_date: date,
) -> List[Dict[str, Any]]:
    """Fetch market price/volume data for multiple products over a date range."""
    from backend.src.service.market_data_service import market_data

    merged: Dict[str, Dict[str, Any]] = {}

    for pid in product_ids:
        history = market_data.get_price_history(pid)
        if not history:
            continue
        for point in history:
            dt = str(point.get("date") or "")[:10]
            if not dt or dt < start_date.strftime("%Y-%m-%d") or dt > end_date.strftime("%Y-%m-%d"):
                continue
            if dt not in merged:
                merged[dt] = {"date": dt}
            try:
                merged[dt][pid] = float(point.get("price") or 0)
            except (TypeError, ValueError):
                pass

    return sorted(merged.values(), key=lambda x: str(x.get("date", "")))


# ──────────────────────────────────────────────────────────────────────────────
# Chart Type Recommender
# ──────────────────────────────────────────────────────────────────────────────

ChartType = Literal["line", "bar", "multi_line", "composed", "multi_axis_composed", "pie", "scatter"]


def _recommend_chart_type(
    metrics: List[str],
    num_entities: int,
    has_different_units: bool,
    is_categorical: bool = False,
) -> ChartType:
    """Decide the best visualization based on data shape."""
    if is_categorical:
        return "bar" if len(metrics) == 1 else "composed"
    if len(metrics) == 1 and num_entities == 1:
        return "line"
    if len(metrics) == 1 and num_entities > 1:
        return "multi_line"
    if has_different_units or len(metrics) > 2:
        return "multi_axis_composed"
    return "composed"


# ──────────────────────────────────────────────────────────────────────────────
# Visualization Builder
# ──────────────────────────────────────────────────────────────────────────────

# Default color palette for series
_SERIES_COLORS = [
    "#3b82f6",  # blue
    "#22c55e",  # green
    "#f97316",  # orange
    "#a855f7",  # purple
    "#ec4899",  # pink
    "#14b8a6",  # teal
    "#eab308",  # yellow
    "#6366f1",  # indigo
]

_ERP_METRIC_UNITS: Dict[str, str] = {
    "revenue": "¥",
    "volume": "吨",
    "orders": "单",
    "price": "¥/吨",
    "profit": "¥",
}


def _build_multi_axis_composed_viz(
    data: List[Dict[str, Any]],
    metrics: List[str],
    entity_labels: List[str],
    title: str,
    subtitle: str,
    language: str,
) -> Dict[str, Any]:
    """Build a multi-axis composed chart payload (Revenue | Volume | Orders)."""
    date_key = "date"
    date_labels = [str(row.get(date_key, "")) for row in data]

    # Determine which metrics map to which Y axis
    # Convention: left = monetary/revenue, right = counts, farRight = ratios
    AXIS_ASSIGNMENTS: Dict[str, str] = {
        "revenue": "left",
        "profit": "left",
        "price": "left",
        "volume": "right",
        "orders": "right",
    }
    UNIT_MAP: Dict[str, str] = {
        "left": _ERP_METRIC_UNITS.get(metrics[0], ""),
        "right": _ERP_METRIC_UNITS.get(metrics[-1] if len(metrics) > 1 else metrics[0], ""),
    }

    # If only 2+ metrics, put first on left, second on right, rest on farRight
    far_right_metrics = metrics[2:] if len(metrics) > 2 else []
    if far_right_metrics and len(metrics) > 2:
        UNIT_MAP["farRight"] = _ERP_METRIC_UNITS.get(far_right_metrics[0], "")

    series = []
    for i, metric in enumerate(metrics):
        axis_pos = "farRight" if metric in far_right_metrics else AXIS_ASSIGNMENTS.get(metric, "right")
        series.append({
            "key": metric,
            "label": entity_labels[i] if i < len(entity_labels) else metric,
            "color": _SERIES_COLORS[i % len(_SERIES_COLORS)],
            "chart_type": "line",
            "axis": axis_pos,
            "unit": _ERP_METRIC_UNITS.get(metric, ""),
        })

    return {
        "kind": "multi_axis_composed",
        "title": title,
        "subtitle": subtitle,
        "unit": "",
        "x_key": date_key,
        "series": series,
        "data": data,
        "summary": {},
    }


def _build_multi_line_viz(
    data: List[Dict[str, Any]],
    series_keys: List[str],
    series_labels: List[str],
    title: str,
    subtitle: str,
    language: str,
) -> Dict[str, Any]:
    """Build a multi-line chart payload (same units, time-series)."""
    series = [
        {
            "key": key,
            "label": label,
            "color": _SERIES_COLORS[i % len(_SERIES_COLORS)],
            "chart_type": "line",
        }
        for i, (key, label) in enumerate(zip(series_keys, series_labels))
    ]
    return {
        "kind": "multi_line",
        "title": title,
        "subtitle": subtitle,
        "unit": "",
        "x_key": "date",
        "series": series,
        "data": data,
        "summary": {},
    }


def _build_composed_viz(
    data: List[Dict[str, Any]],
    series_keys: List[str],
    series_labels: List[str],
    title: str,
    subtitle: str,
    language: str,
) -> Dict[str, Any]:
    """Build a composed bar+line chart."""
    series = []
    for i, (key, label) in enumerate(zip(series_keys, series_labels)):
        chart_type: str = "bar" if i == 0 else "line"
        series.append({
            "key": key,
            "label": label,
            "color": _SERIES_COLORS[i % len(_SERIES_COLORS)],
            "chart_type": chart_type,
        })
    return {
        "kind": "composed",
        "title": title,
        "subtitle": subtitle,
        "unit": "",
        "x_key": "date",
        "series": series,
        "data": data,
        "summary": {},
    }


def _build_bar_viz(
    data: List[Dict[str, Any]],
    value_key: str,
    label_key: str,
    title: str,
    subtitle: str,
    language: str,
) -> Dict[str, Any]:
    """Build a simple bar chart."""
    return {
        "kind": "bar",
        "title": title,
        "subtitle": subtitle,
        "unit": "",
        "x_key": label_key,
        "series": [{"key": value_key, "label": value_key, "color": _SERIES_COLORS[0]}],
        "data": data,
        "summary": {},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main Skill Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def build_dynamic_comparison_visualization(
    query: str,
    product_ids: Optional[List[str]] = None,
    language: str = "zh",
    source: str = "erp",
    period: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Main entry point: analyze query, fetch data, build the best chart.

    Parameters
    ----------
    query        : Natural language user question.
    product_ids  : Product IDs to scope ERP data.
    language     : "zh" or "en".
    source       : "erp" | "market" | "auto". Auto detects from query.
    period       : Optional explicit period string (e.g. "last 30 days").

    Returns
    -------
    A ChatVisualization-compatible dict, or None if insufficient data.
    """
    product_ids = product_ids or []
    pid = product_ids[0] if product_ids else None

    # ── Step 1: Detect intent ──────────────────────────────────────────────────
    detected_metrics = _extract_metrics_from_query(query)
    if not detected_metrics:
        # Fallback: try revenue + volume as default ERP combo
        detected_metrics = ["revenue", "volume"]

    is_comparison = _is_dynamic_comparison_intent(query)

    # Auto-detect source
    q = str(query or "").lower()
    if source == "auto":
        erp_keywords = (
            "销售额", "销量", "订单", "erp", "销售", "库存", "营收",
            "revenue", "volume", "orders", "sales", "quantity",
            "profit", "margin", "price", "avg", "ton", "tons",
            "compare", "comparison", "vs", "versus", "together",
        )
        if any(kw in q for kw in erp_keywords):
            source = "erp"
        else:
            source = "market"

    # ── Step 2: Determine time range ────────────────────────────────────────
    if period:
        # Simple period parsing
        m = re.search(r"(\d+)\s*天", period)
        if m:
            days = int(m.group(1))
            end = date.today()
            start = end - timedelta(days=days)
        else:
            end = date.today()
            start = end - timedelta(days=30)
    else:
        start, end = _extract_time_range(query)

    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    # ── Step 3: Fetch data ───────────────────────────────────────────────────
    if source == "erp":
        raw_data = _resolve_erp_multi_metric(
            product_id=pid,
            metrics=detected_metrics,
            start_date=start,
            end_date=end,
        )
    else:
        raw_data = _resolve_market_multi_metric(
            product_ids=product_ids or [],
            metrics=detected_metrics,
            start_date=start,
            end_date=end,
        )

    if not raw_data:
        return None

    # Ensure all rows have a date key
    for row in raw_data:
        if "date" not in row:
            row["date"] = str(row.get("period", ""))

    # ── Step 4: Build title / subtitle ──────────────────────────────────────
    metric_labels_zh = {"revenue": "销售额", "volume": "销量", "orders": "订单数", "price": "价格", "profit": "利润"}
    metric_labels_en = {"revenue": "Revenue", "volume": "Volume", "orders": "Orders", "price": "Price", "profit": "Profit"}

    if language == "en":
        metric_str = " & ".join(metric_labels_en.get(m, m.title()) for m in detected_metrics)
        title = f"{metric_str} Comparison" if is_comparison else f"{metric_str} Trend"
        subtitle = f"{start_str} to {end_str}"
        date_range_str = f"{start_str} to {end_str}"
    else:
        metric_str = " & ".join(metric_labels_zh.get(m, m) for m in detected_metrics)
        title = f"{metric_str}对比" if is_comparison else f"{metric_str}走势"
        subtitle = f"{start_str} 至 {end_str}"
        date_range_str = f"{start_str} 至 {end_str}"

    # ── Step 5: Recommend chart type ────────────────────────────────────────
    has_diff_units = len(set(_ERP_METRIC_UNITS.get(m, "") for m in detected_metrics)) > 1
    chart_type = _recommend_chart_type(
        metrics=detected_metrics,
        num_entities=len(product_ids) if product_ids else 1,
        has_different_units=has_diff_units,
    )

    # ── Step 6: Build the visualization ──────────────────────────────────────
    entity_labels = [
        metric_labels_zh.get(m, metric_labels_en.get(m, m.title())) for m in detected_metrics
    ]

    if chart_type == "multi_axis_composed":
        return _build_multi_axis_composed_viz(
            data=raw_data,
            metrics=detected_metrics,
            entity_labels=entity_labels,
            title=title,
            subtitle=subtitle,
            language=language,
        )
    elif chart_type == "multi_line":
        return _build_multi_line_viz(
            data=raw_data,
            series_keys=detected_metrics,
            series_labels=entity_labels,
            title=title,
            subtitle=subtitle,
            language=language,
        )
    elif chart_type == "composed":
        return _build_composed_viz(
            data=raw_data,
            series_keys=detected_metrics,
            series_labels=entity_labels,
            title=title,
            subtitle=subtitle,
            language=language,
        )
    elif chart_type == "bar":
        val_key = detected_metrics[0]
        return _build_bar_viz(
            data=raw_data,
            value_key=val_key,
            label_key="date",
            title=title,
            subtitle=subtitle,
            language=language,
        )
    else:
        # Default to multi_axis_composed for ERP multi-metric
        return _build_multi_axis_composed_viz(
            data=raw_data,
            metrics=detected_metrics,
            entity_labels=entity_labels,
            title=title,
            subtitle=subtitle,
            language=language,
        )
