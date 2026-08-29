"""ERP data query tools for the chatbot pipeline.

Provides functions that query internal business data (sales, inventory,
customers, contracts, production plans) from MySQL to feed the LLM.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.domain.product_resolver import extract_product_ids_in_text, resolve_product_id
from backend.src.service.erp_kpi_service import (
    ErpKPIService,
    _PRODUCT_TO_MATERIAL_NAMES,
    _canonical_product_id,
    _material_name_params,
)

_erp_kpi = ErpKPIService()

# Per-order sales amount: sum ERP FAMOUNT only. Never reconstruct as volume × weighted avg price.
ERP_ORDER_REVENUE_SUM_SQL = "SUM(COALESCE(FAMOUNT, 0))"
ERP_ORDER_REVENUE_LINE_SQL = "COALESCE(FAMOUNT, 0)"
ERP_REVENUE_BASIS_NOTE_ZH = (
    "销售额口径：对每条销售订单明细的 ERP FAMOUNT（不含税销售金额）求和后得到；"
    "不得用「总销量×加权均价」推算销售额。"
)
ERP_REVENUE_BASIS_NOTE_EN = (
    "Revenue basis: sum of ERP FAMOUNT per order line; "
    "do NOT compute revenue as total volume × weighted average price."
)

# ── Cached latest date in ERP sales data ──
_latest_erp_date_cache: date | None = None
_view_audit_cache_text: str | None = None
_view_audit_cache_ts: float = 0.0
_VIEW_AUDIT_CACHE_TTL_SECONDS = max(
    60, int(os.getenv("ERP_VIEW_AUDIT_CACHE_TTL_SECONDS", "1800"))
)

_ENGLISH_MONTHS: Dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_ENGLISH_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)
_EXPLICIT_DATE_RANGE_SEPARATOR = r"(?:\s*(?:to|through|until|till|至|到|--|~|—)\s*)"


def invalidate_erp_tools_cache() -> None:
    """Clear module-level cached ERP reference date used by chatbot tools."""
    global _latest_erp_date_cache, _view_audit_cache_text, _view_audit_cache_ts
    _latest_erp_date_cache = None
    _view_audit_cache_text = None
    _view_audit_cache_ts = 0.0


def _get_latest_erp_date() -> date:
    """Get the latest PLANDATE in the ERP sales table."""
    global _latest_erp_date_cache
    if _latest_erp_date_cache is not None:
        return _latest_erp_date_cache
    try:
        with db.engine.connect() as conn:
            val = conn.execute(
                text(
                    "SELECT MAX(DATE(PLANDATE)) FROM erp_v_sale_orderentry "
                    "WHERE PLANDATE IS NOT NULL"
                )
            ).scalar()
            if val:
                _latest_erp_date_cache = (
                    val if isinstance(val, date) else date.fromisoformat(str(val)[:10])
                )
                return _latest_erp_date_cache
    except Exception:
        pass
    return date.today()


def _parse_explicit_period_range(period: str | None, reference_date: date) -> tuple[date, date] | None:
    text_value = str(period or "")
    if not text_value.strip():
        return None

    english_range = _parse_english_month_name_range(text_value, reference_date)
    if english_range:
        return english_range

    patterns = [
        r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?\s*(?:to|through|until|至|到|--|-|~|—)\s*(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?",
        r"(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?\s*(?:to|through|until|至|到|--|-|~|—)\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_value, re.IGNORECASE)
        if not match:
            continue
        groups = [int(value) for value in match.groups()]
        try:
            if len(groups) == 6:
                start = date(groups[0], groups[1], groups[2])
                end = date(groups[3], groups[4], groups[5])
            else:
                start = date(reference_date.year, groups[0], groups[1])
                end = date(reference_date.year, groups[2], groups[3])
                if end < start and start.month == 12 and end.month == 1:
                    end = date(reference_date.year + 1, groups[2], groups[3])
            if end < start:
                start, end = end, start
            return start, end
        except ValueError:
            return None

    return None


def _strip_english_ordinal_suffixes(value: str) -> str:
    return re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)


def _english_month_number(month_name: str | None) -> int | None:
    normalized = str(month_name or "").strip().lower().rstrip(".")
    return _ENGLISH_MONTHS.get(normalized)


def _coerce_explicit_date(
    *,
    year_before: str | None = None,
    year_after: str | None = None,
    month_name: str | None = None,
    day: str | None = None,
    fallback_year: int,
) -> date | None:
    month = _english_month_number(month_name)
    if month is None or not day:
        return None
    try:
        year = int(year_before or year_after or fallback_year)
        return date(year, month, int(day))
    except ValueError:
        return None


def _normalize_explicit_range(start: date, end: date) -> tuple[date, date]:
    if end < start and start.month == 12 and end.month == 1:
        end = date(start.year + 1, end.month, end.day)
    if end < start:
        start, end = end, start
    return start, end


def _parse_english_month_name_range(
    period: str,
    reference_date: date,
) -> tuple[date, date] | None:
    normalized = _strip_english_ordinal_suffixes(period)
    normalized = re.sub(r"[,]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    month = _ENGLISH_MONTH_PATTERN
    separator = _EXPLICIT_DATE_RANGE_SEPARATOR

    month_day_pattern = re.compile(
        rf"\b(?:(\d{{4}})\s+)?({month})\s+(\d{{1,2}})(?:\s+(\d{{4}}))?"
        rf"{separator}"
        rf"(?:(\d{{4}})\s+)?(?:({month})\s+)?(\d{{1,2}})(?:\s+(\d{{4}}))?\b",
        re.IGNORECASE,
    )
    match = month_day_pattern.search(normalized)
    if match:
        (
            start_year_before,
            start_month,
            start_day,
            start_year_after,
            end_year_before,
            end_month,
            end_day,
            end_year_after,
        ) = match.groups()
        resolved_start_year = start_year_before or start_year_after or str(reference_date.year)
        start = _coerce_explicit_date(
            year_before=start_year_before,
            year_after=start_year_after,
            month_name=start_month,
            day=start_day,
            fallback_year=reference_date.year,
        )
        end = _coerce_explicit_date(
            year_before=end_year_before,
            year_after=end_year_after,
            month_name=end_month or start_month,
            day=end_day,
            fallback_year=int(end_year_before or end_year_after or resolved_start_year),
        )
        if start and end:
            return _normalize_explicit_range(start, end)

    day_month_pattern = re.compile(
        rf"\b(\d{{1,2}})\s+({month})(?:\s+(\d{{4}}))?"
        rf"{separator}"
        rf"(\d{{1,2}})\s+({month})(?:\s+(\d{{4}}))?\b",
        re.IGNORECASE,
    )
    match = day_month_pattern.search(normalized)
    if match:
        start_day, start_month, start_year, end_day, end_month, end_year = match.groups()
        resolved_start_year = start_year or str(reference_date.year)
        start = _coerce_explicit_date(
            year_after=start_year,
            month_name=start_month,
            day=start_day,
            fallback_year=reference_date.year,
        )
        end = _coerce_explicit_date(
            year_after=end_year,
            month_name=end_month,
            day=end_day,
            fallback_year=int(end_year or resolved_start_year),
        )
        if start and end:
            return _normalize_explicit_range(start, end)

    return None


# ── All known material names (for queries that don't specify a product) ──
_ALL_MATERIAL_NAMES: tuple[str, ...] = tuple(
    name for names in _PRODUCT_TO_MATERIAL_NAMES.values() for name in names
)

# ── Product display names ──
_PRODUCT_DISPLAY: Dict[str, str] = {
    "isoprene": "异戊二烯 (Isoprene)",
    "piperylene": "间戊二烯 (Piperylene)",
    "dcpd": "双环戊二烯 (DCPD)",
    "cracked_c5": "工业用裂解碳五 (Cracked C5)",
    "cracked_c9": "工业用裂解碳九 (Cracked C9)",
    "raffinate_c5": "抽余碳五 (Raffinate C5)",
    "sis": "SIS橡胶 (SIS Rubber)",
    "c5_resin": "碳五石油树脂 (C5 Resin)",
    "blowing_agent": "戊烷发泡剂 (Blowing Agent)",
    "styrene": "苯乙烯 (Styrene)",
    "hydrogenated_c9": "加氢碳九 (Hydrogenated C9)",
    "trimethylbenzene": "混三甲苯 (Trimethylbenzene)",
}


def _resolve_period_with_source(period: str | None) -> tuple[date, date, str]:
    """Convert a human-readable period to dates plus the normalization source.

    Uses the latest available ERP data date as reference instead of today(),
    so queries always return data even if ERP data lags behind real date.
    """
    ref_date = _get_latest_erp_date()
    explicit_range = _parse_explicit_period_range(period, ref_date)
    if explicit_range:
        return explicit_range[0], explicit_range[1], "explicit_range"

    if not period:
        # Default: last 30 days relative to latest data
        return (ref_date - timedelta(days=30), ref_date, "default_latest_30_days")

    p = period.lower().strip()

    if "last month" in p or "上个月" in p or "上月" in p:
        first_this_month = ref_date.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return (last_month_start, last_month_end, "relative_last_month")

    if "this month" in p or "本月" in p or "current month" in p:
        return (ref_date.replace(day=1), ref_date, "relative_this_month")

    if "last week" in p or "上周" in p:
        days_since_monday = ref_date.weekday()
        this_monday = ref_date - timedelta(days=days_since_monday)
        last_monday = this_monday - timedelta(days=7)
        last_sunday = this_monday - timedelta(days=1)
        return (last_monday, last_sunday, "relative_last_week")

    if "this week" in p or "本周" in p or "current week" in p:
        days_since_monday = ref_date.weekday()
        this_monday = ref_date - timedelta(days=days_since_monday)
        return (this_monday, ref_date, "relative_this_week")

    if "today" in p or "今天" in p or "今日" in p:
        return (ref_date, ref_date, "relative_today")

    if "yesterday" in p or "昨天" in p or "昨日" in p:
        yesterday = ref_date - timedelta(days=1)
        return (yesterday, yesterday, "relative_yesterday")

    if "last 7" in p or "past week" in p or "近7天" in p or "近一周" in p:
        return (ref_date - timedelta(days=7), ref_date, "relative_last_7_days")

    if "last 90" in p or "past 3 months" in p or "近3个月" in p or "近三个月" in p:
        return (ref_date - timedelta(days=90), ref_date, "relative_last_90_days")

    if "this quarter" in p or "本季度" in p:
        quarter_month = ((ref_date.month - 1) // 3) * 3 + 1
        return (ref_date.replace(month=quarter_month, day=1), ref_date, "relative_this_quarter")

    if "last quarter" in p or "上季度" in p:
        quarter_month = ((ref_date.month - 1) // 3) * 3 + 1
        q_start = ref_date.replace(month=quarter_month, day=1)
        last_q_end = q_start - timedelta(days=1)
        last_q_start_month = ((last_q_end.month - 1) // 3) * 3 + 1
        last_q_start = last_q_end.replace(month=last_q_start_month, day=1)
        return (last_q_start, last_q_end, "relative_last_quarter")

    if "this year" in p or "今年" in p or "本年" in p:
        return (ref_date.replace(month=1, day=1), ref_date, "relative_this_year")

    # Default: last 30 days
    return (ref_date - timedelta(days=30), ref_date, "default_latest_30_days")


def _resolve_period(period: str | None) -> tuple[date, date]:
    start_date, end_date, _source = _resolve_period_with_source(period)
    return start_date, end_date


def resolve_period_metadata(period: str | None) -> Dict[str, str]:
    start_date, end_date, source = _resolve_period_with_source(period)
    return {
        "period_start": str(start_date),
        "period_end": str(end_date),
        "period_source": source,
    }


def _material_label(material_names: tuple[str, ...]) -> str:
    return ", ".join(str(name) for name in material_names if str(name).strip()) or "selected product"


def _query_material_date_range(
    table_name: str,
    date_column: str,
    material_names: tuple[str, ...],
) -> tuple[int, date | None, date | None]:
    if not material_names:
        return 0, None, None

    placeholders, params = _material_name_params(material_names)
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT COUNT(*), MIN(DATE({date_column})), MAX(DATE({date_column})) "
                    f"FROM {table_name} "
                    f"WHERE material_name IN ({placeholders}) "
                    f"AND {date_column} IS NOT NULL"
                ),
                params,
            ).fetchone()
    except Exception:
        return 0, None, None

    if not row:
        return 0, None, None

    count = int(row[0] or 0)

    def _coerce(value) -> date | None:
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return None

    return count, _coerce(row[1]), _coerce(row[2])


def _format_no_period_records_with_availability(
    *,
    record_label: str,
    material_names: tuple[str, ...],
    requested_start: date,
    requested_end: date,
    table_name: str = "erp_v_sale_orderentry",
    date_column: str = "PLANDATE",
    extra_note: str = "",
) -> str:
    count, available_start, available_end = _query_material_date_range(
        table_name,
        date_column,
        material_names,
    )
    requested_days = max(1, (requested_end - requested_start).days + 1)
    label = _material_label(material_names)
    lines = [
        f"No {record_label} records found for requested period {requested_start} to {requested_end}."
    ]
    if count > 0 and available_start and available_end:
        closest_start = max(available_start, available_end - timedelta(days=requested_days - 1))
        lines.append(
            f"AVAILABLE ERP {record_label.upper()} DATE RANGE for {label}: "
            f"{available_start} to {available_end} ({count} rows)."
        )
        lines.append(
            f"CLOSEST AVAILABLE {requested_days}-DAY WINDOW: {closest_start} to {available_end}."
        )
        lines.append(
            "Use the closest available window or choose a period inside the available date range."
        )
    else:
        lines.append(
            f"No historical ERP {record_label} records are available for {label}."
        )
    if extra_note:
        lines.append(extra_note)
    return "\n".join(lines)


def query_sales_summary(
    product_id: str | None = None,
    period: str | None = None,
) -> str:
    """Query sales summary from ERP — volumes, revenue, by product and period."""
    start_date, end_date = _resolve_period(period)

    # Determine which material names to query
    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return f"No ERP sales data mapping for product '{product_id}'."
    else:
        material_names = _ALL_MATERIAL_NAMES

    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date

    try:
        with db.engine.connect() as conn:
            # Aggregate by material
            rows = conn.execute(
                text(
                    "SELECT material_name, "
                    "  SUM(COALESCE(FDELIQTY, 0)) AS total_qty, "
                    "  COUNT(*) AS order_count, "
                    "  SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
                    "    / NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS avg_price, "
                    f"  {ERP_ORDER_REVENUE_SUM_SQL} AS revenue_cny "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    "GROUP BY material_name "
                    "ORDER BY total_qty DESC"
                ),
                params,
            ).fetchall()

            if not rows:
                return _format_no_period_records_with_availability(
                    record_label="sales",
                    material_names=material_names,
                    requested_start=start_date,
                    requested_end=end_date,
                )

            lines = [
                f"ERP SALES SUMMARY ({start_date} to {end_date}):",
                f"{'Product':<25} {'Volume(ton)':<15} {'Orders':<10} {'Avg Price(CNY/ton)':<20} {'Revenue(CNY)':<18}",
                "-" * 92,
            ]
            total_vol = 0.0
            total_revenue = 0.0
            for row in rows:
                name = row[0]
                vol = float(row[1] or 0)
                orders = int(row[2] or 0)
                price = float(row[3] or 0)
                revenue = float(row[4] or 0)
                total_vol += vol
                total_revenue += revenue
                lines.append(
                    f"{name:<25} {vol:>12,.2f}   {orders:>8}   {price:>16,.2f}   {revenue:>15,.2f}"
                )
            lines.append("-" * 92)
            lines.append(f"{'TOTAL':<25} {total_vol:>12,.2f}   {'':>8}   {'':>16}   {total_revenue:>15,.2f}")

            # Also get previous period for comparison
            period_days = (end_date - start_date).days + 1
            prev_start = start_date - timedelta(days=period_days)
            prev_end = start_date - timedelta(days=1)
            params2 = dict(params)
            params2["start_date"] = prev_start
            params2["end_date"] = prev_end

            prev_total = conn.execute(
                text(
                    "SELECT SUM(COALESCE(FDELIQTY, 0)) "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date"
                ),
                params2,
            ).scalar()

            if prev_total and float(prev_total) > 0:
                growth = (total_vol - float(prev_total)) / float(prev_total) * 100
                lines.append(
                    f"\nPeriod-over-period change: {growth:+.1f}% "
                    f"(previous period: {prev_start} to {prev_end}, {float(prev_total):,.2f} ton)"
                )

            lines.append("")
            lines.append(ERP_REVENUE_BASIS_NOTE_EN)
            return "\n".join(lines)

    except Exception as e:
        return f"Error querying ERP sales: {e}"


def query_sales_order_details(
    product_id: str | None = None,
    period: str | None = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Return row-level ERP sales order details from the verified sales-order view."""
    start_date, end_date = _resolve_period(period)

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return {
                "period_start": str(start_date),
                "period_end": str(end_date),
                "rows": [],
                "total_qty": 0.0,
                "total_revenue": 0.0,
                "row_count": 0,
                "error": f"No ERP data mapping for product '{product_id}'.",
            }
    else:
        material_names = _ALL_MATERIAL_NAMES

    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date
    params["limit"] = max(1, min(int(limit or 200), 500))

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT FBILLNO, CUST_NAME, DATE(PLANDATE) AS plan_date, "
                    "  material_name, FDELIQTY, COALESCE(FTAXPRICE, FPRICE, 0) AS unit_price, "
                    f"  {ERP_ORDER_REVENUE_LINE_SQL} AS revenue_cny "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    "ORDER BY DATE(PLANDATE), FBILLNO, FENTRYID "
                    "LIMIT :limit"
                ),
                params,
            ).fetchall()

            totals = conn.execute(
                text(
                    "SELECT COUNT(*), SUM(COALESCE(FDELIQTY, 0)), "
                    f"  {ERP_ORDER_REVENUE_SUM_SQL} "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date"
                ),
                params,
            ).fetchone()

        detail_rows: List[Dict[str, Any]] = []
        for row in rows:
            detail_rows.append(
                {
                    "order_no": str(row[0] or ""),
                    "customer": str(row[1] or ""),
                    "plan_date": str(row[2] or "")[:10],
                    "product": str(row[3] or ""),
                    "qty_ton": float(row[4] or 0),
                    "unit_price": float(row[5] or 0),
                    "revenue_cny": float(row[6] or 0),
                }
            )

        available_count = 0
        available_start = None
        available_end = None
        if not detail_rows:
            available_count, available_start, available_end = _query_material_date_range(
                "erp_v_sale_orderentry",
                "PLANDATE",
                material_names,
            )

        return {
            "period_start": str(start_date),
            "period_end": str(end_date),
            "rows": detail_rows,
            "total_qty": float((totals or [0, 0, 0])[1] or 0),
            "total_revenue": float((totals or [0, 0, 0])[2] or 0),
            "row_count": int((totals or [0, 0, 0])[0] or 0),
            "available_count": available_count,
            "available_start": str(available_start) if available_start else "",
            "available_end": str(available_end) if available_end else "",
        }
    except Exception as e:
        return {
            "period_start": str(start_date),
            "period_end": str(end_date),
            "rows": [],
            "total_qty": 0.0,
            "total_revenue": 0.0,
            "row_count": 0,
            "error": f"Error querying ERP sales order details: {e}",
        }


def query_sales_daily(
    product_id: str | None = None,
    period: str | None = None,
) -> str:
    """Query daily sales breakdown from ERP."""
    start_date, end_date = _resolve_period(period)

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return f"No ERP data mapping for product '{product_id}'."
    else:
        material_names = _ALL_MATERIAL_NAMES

    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DATE(PLANDATE) AS d, "
                    "  SUM(COALESCE(FDELIQTY, 0)) AS vol, "
                    "  SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
                    "    / NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS avg_price "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    "GROUP BY DATE(PLANDATE) ORDER BY d"
                ),
                params,
            ).fetchall()

            if not rows:
                return _format_no_period_records_with_availability(
                    record_label="daily sales",
                    material_names=material_names,
                    requested_start=start_date,
                    requested_end=end_date,
                )

            lines = [
                f"DAILY SALES ({start_date} to {end_date}):",
                f"{'Date':<12} {'Volume(ton)':<15} {'Avg Price(CNY/ton)':<20}",
                "-" * 47,
            ]
            for row in rows:
                d = str(row[0])
                vol = float(row[1] or 0)
                price = float(row[2] or 0)
                lines.append(f"{d:<12} {vol:>12,.2f}   {price:>16,.2f}")

            return "\n".join(lines)

    except Exception as e:
        return f"Error querying daily sales: {e}"


def query_inventory_levels(product_id: str | None = None) -> str:
    """Query current inventory levels from ERP."""
    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return f"No ERP data mapping for product '{product_id}'."
        names_to_query = {canonical: material_names}
    else:
        names_to_query = {
            pid: names for pid, names in _PRODUCT_TO_MATERIAL_NAMES.items()
        }

    lines = ["CURRENT INVENTORY LEVELS:"]
    lines.append(
        f"{'Product':<25} {'Inventory(ton)':<18} {'Days of Stock':<15} {'30d Sales(ton)':<15}"
    )
    lines.append("-" * 73)

    any_data = False
    for pid, mat_names in names_to_query.items():
        kpi = _erp_kpi.get_product_kpi(pid)
        if kpi is None:
            continue
        any_data = True
        display = _PRODUCT_DISPLAY.get(pid, pid)
        inv = f"{kpi.inventory_tons:,.2f}" if kpi.inventory_tons is not None else "N/A"
        days = f"{kpi.inventory_days:.1f}" if kpi.inventory_days is not None else "N/A"
        sales = f"{kpi.sales_30d_ton:,.2f}" if kpi.sales_30d_ton is not None else "N/A"
        lines.append(f"{display:<25} {inv:>16}   {days:>13}   {sales:>13}")

    if not any_data:
        return "No inventory data available in ERP."

    # Safety stock alerts
    alerts = []
    for pid, mat_names in names_to_query.items():
        kpi = _erp_kpi.get_product_kpi(pid)
        if kpi and kpi.inventory_days is not None:
            display = _PRODUCT_DISPLAY.get(pid, pid)
            if kpi.inventory_days <= 7:
                alerts.append(
                    f"⚠️ CRITICAL: {display} — only {kpi.inventory_days:.1f} days of stock!"
                )
            elif kpi.inventory_days <= 14:
                alerts.append(
                    f"⚠ LOW: {display} — {kpi.inventory_days:.1f} days of stock"
                )

    if alerts:
        lines.append("\nSAFETY STOCK ALERTS:")
        lines.extend(alerts)

    return "\n".join(lines)


def query_sales_by_customer(
    product_id: str | None = None,
    period: str | None = None,
    top_n: int = 15,
) -> str:
    """Query top customers by sales volume from ERP."""
    start_date, end_date = _resolve_period(period)

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return f"No ERP data mapping for product '{product_id}'."
    else:
        material_names = _ALL_MATERIAL_NAMES

    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date

    try:
        with db.engine.connect() as conn:
            # Try org_name as customer proxy (from erp_v_sale_orderentry)
            rows = conn.execute(
                text(
                    "SELECT org_name, "
                    "  SUM(COALESCE(FDELIQTY, 0)) AS total_qty, "
                    "  COUNT(*) AS order_count "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    "  AND org_name IS NOT NULL AND org_name != '' "
                    "GROUP BY org_name "
                    "ORDER BY total_qty DESC "
                    f"LIMIT {int(top_n)}"
                ),
                params,
            ).fetchall()

            if not rows:
                return _format_no_period_records_with_availability(
                    record_label="customer sales",
                    material_names=material_names,
                    requested_start=start_date,
                    requested_end=end_date,
                )

            lines = [
                f"TOP CUSTOMERS BY SALES VOLUME ({start_date} to {end_date}):",
                f"{'Rank':<6} {'Customer/Org':<35} {'Volume(ton)':<15} {'Orders':<10}",
                "-" * 66,
            ]
            for i, row in enumerate(rows, 1):
                name = str(row[0])[:33]
                vol = float(row[1] or 0)
                orders = int(row[2] or 0)
                lines.append(f"{i:<6} {name:<35} {vol:>12,.2f}   {orders:>8}")

            return "\n".join(lines)

    except Exception as e:
        return f"Error querying customer sales: {e}"


def query_customer_sales_report(
    product_id: str | None = None,
    period: str | None = None,
    top_n: int = 10,
) -> Dict[str, Any]:
    """Return customer-level ERP sales, growth, decline risk, and actions."""
    start_date, end_date = _resolve_period(period)

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return {
                "period_start": str(start_date),
                "period_end": str(end_date),
                "rows": [],
                "growth": [],
                "risk": [],
                "error": f"No ERP data mapping for product '{product_id}'.",
            }
        product_label = _PRODUCT_DISPLAY.get(canonical, canonical)
    else:
        material_names = _ALL_MATERIAL_NAMES
        product_label = "全部产品"

    period_days = (end_date - start_date).days + 1
    prev_start = start_date - timedelta(days=period_days)
    prev_end = start_date - timedelta(days=1)
    placeholders, params = _material_name_params(material_names)
    customer_expr = "COALESCE(NULLIF(CUST_NAME, ''), NULLIF(org_name, ''), '未命名客户')"

    def _fetch_window(window_start: date, window_end: date) -> Dict[str, Dict[str, Any]]:
        query_params = dict(params)
        query_params["start_date"] = window_start
        query_params["end_date"] = window_end
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {customer_expr} AS customer_name, "
                    "  SUM(COALESCE(FDELIQTY, 0)) AS total_qty, "
                    "  COUNT(*) AS order_count, "
                    f"  {ERP_ORDER_REVENUE_SUM_SQL} AS revenue_cny, "
                    "  SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
                    "    / NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS avg_price "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    f"GROUP BY {customer_expr} "
                    "ORDER BY revenue_cny DESC, total_qty DESC"
                ),
                query_params,
            ).fetchall()
        return {
            str(row[0] or "未命名客户"): {
                "customer": str(row[0] or "未命名客户"),
                "qty_ton": float(row[1] or 0),
                "orders": int(row[2] or 0),
                "revenue_cny": float(row[3] or 0),
                "avg_price": float(row[4] or 0),
            }
            for row in rows
        }

    try:
        current = _fetch_window(start_date, end_date)
        previous = _fetch_window(prev_start, prev_end)
    except Exception as e:
        return {
            "period_start": str(start_date),
            "period_end": str(end_date),
            "rows": [],
            "growth": [],
            "risk": [],
            "error": f"Error querying ERP customer sales report: {e}",
        }

    if not current:
        available_count, available_start, available_end = _query_material_date_range(
            "erp_v_sale_orderentry",
            "PLANDATE",
            material_names,
        )
        return {
            "period_start": str(start_date),
            "period_end": str(end_date),
            "previous_start": str(prev_start),
            "previous_end": str(prev_end),
            "product_label": product_label,
            "rows": [],
            "growth": [],
            "risk": [],
            "available_count": available_count,
            "available_start": str(available_start) if available_start else "",
            "available_end": str(available_end) if available_end else "",
        }

    combined: list[Dict[str, Any]] = []
    for customer, row in current.items():
        prev = previous.get(customer, {})
        prev_qty = float(prev.get("qty_ton") or 0)
        prev_revenue = float(prev.get("revenue_cny") or 0)
        qty_change_pct = ((row["qty_ton"] - prev_qty) / prev_qty * 100.0) if prev_qty > 0 else None
        revenue_change_pct = ((row["revenue_cny"] - prev_revenue) / prev_revenue * 100.0) if prev_revenue > 0 else None
        combined.append(
            {
                **row,
                "previous_qty_ton": prev_qty,
                "previous_revenue_cny": prev_revenue,
                "qty_change_pct": qty_change_pct,
                "revenue_change_pct": revenue_change_pct,
                "is_new_or_recovered": prev_qty <= 0 and row["qty_ton"] > 0,
            }
        )

    missing_repeat: list[Dict[str, Any]] = []
    for customer, prev in previous.items():
        if customer in current:
            continue
        missing_repeat.append(
            {
                "customer": customer,
                "qty_ton": 0.0,
                "orders": 0,
                "revenue_cny": 0.0,
                "avg_price": 0.0,
                "previous_qty_ton": float(prev.get("qty_ton") or 0),
                "previous_revenue_cny": float(prev.get("revenue_cny") or 0),
                "qty_change_pct": -100.0,
                "revenue_change_pct": -100.0,
                "is_missing_repeat": True,
            }
        )

    combined.sort(key=lambda item: (float(item.get("revenue_cny") or 0), float(item.get("qty_ton") or 0)), reverse=True)
    growth = sorted(
        [row for row in combined if row.get("is_new_or_recovered") or float(row.get("qty_change_pct") or 0) > 10],
        key=lambda item: (float(item.get("revenue_cny") or 0), float(item.get("qty_ton") or 0)),
        reverse=True,
    )
    risk = sorted(
        [row for row in combined if row.get("qty_change_pct") is not None and float(row.get("qty_change_pct") or 0) <= -20]
        + missing_repeat,
        key=lambda item: (float(item.get("previous_revenue_cny") or 0), float(item.get("previous_qty_ton") or 0)),
        reverse=True,
    )

    return {
        "period_start": str(start_date),
        "period_end": str(end_date),
        "previous_start": str(prev_start),
        "previous_end": str(prev_end),
        "period_days": period_days,
        "product_label": product_label,
        "rows": combined[: max(1, int(top_n or 10))],
        "growth": growth[:5],
        "risk": risk[:5],
        "total_qty": sum(float(row.get("qty_ton") or 0) for row in current.values()),
        "total_revenue": sum(float(row.get("revenue_cny") or 0) for row in current.values()),
        "previous_total_qty": sum(float(row.get("qty_ton") or 0) for row in previous.values()),
        "previous_total_revenue": sum(float(row.get("revenue_cny") or 0) for row in previous.values()),
    }


def format_customer_sales_report(report: Dict[str, Any], *, language: str = "zh") -> str:
    """Format customer sales analysis for a management audience."""
    if report.get("error"):
        return str(report.get("error") or "")

    period_start = report.get("period_start") or ""
    period_end = report.get("period_end") or ""
    previous_start = report.get("previous_start") or ""
    previous_end = report.get("previous_end") or ""
    rows = list(report.get("rows") or [])
    if not rows:
        available = ""
        if report.get("available_start") and report.get("available_end"):
            available = f"ERP可用日期范围：{report.get('available_start')} 至 {report.get('available_end')}，共 {int(report.get('available_count') or 0)} 行。"
        return (
            "## ERP客户销售报告\n\n"
            f"{period_start} 至 {period_end} 没有查到匹配客户销售记录。{available}\n\n"
            "数据来源：内部ERP销售订单客户聚合数据。"
        ).strip()

    total_qty = float(report.get("total_qty") or 0)
    total_revenue = float(report.get("total_revenue") or 0)
    prev_qty = float(report.get("previous_total_qty") or 0)
    qty_change = ((total_qty - prev_qty) / prev_qty * 100.0) if prev_qty > 0 else None
    qty_change_text = f"{qty_change:+.1f}%" if qty_change is not None else "上一可比周期无基数"
    product_label = str(report.get("product_label") or "全部产品")
    short_period_note = "由于统计周期少于7天，流失判断为短期预警，需要结合30/60/90天复购历史确认。" if int(report.get("period_days") or 0) < 7 else ""
    growth_rows = list(report.get("growth") or [])
    risk_rows = list(report.get("risk") or [])

    lines = ["## 01 · 管理层结论", ""]
    top_customer = rows[0]
    lines.append(
        f"{period_start} 至 {period_end}，{product_label}客户销售额 {total_revenue:,.2f} 元，销量 {total_qty:,.2f} 吨，"
        f"较上一可比周期销量变化 {qty_change_text}。当前第一优先级客户是 {top_customer.get('customer')}，"
        f"本期贡献 {float(top_customer.get('revenue_cny') or 0):,.2f} 元 / {float(top_customer.get('qty_ton') or 0):,.2f} 吨。"
    )
    if risk_rows:
        risk_revenue = sum(float(row.get("previous_revenue_cny") or 0) for row in risk_rows)
        lines.append(f"需要重点处理的下滑/未复购客户 {len(risk_rows)} 个，对应上一周期销售额约 {risk_revenue:,.2f} 元。{short_period_note}")
    else:
        lines.append(f"本期未发现上一可比周期高贡献客户完全未复购的明确信号。{short_period_note}")

    lines.extend(["", "## 02 · 客户销售排名", ""])
    lines.append("| 客户 | 销量(吨) | 销售额(元) | 订单 | 较上期销量变化 |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in rows[:10]:
        change = row.get("qty_change_pct")
        change_text = "新增/恢复" if row.get("is_new_or_recovered") else (f"{float(change):+.1f}%" if change is not None else "无基数")
        lines.append(
            f"| {row.get('customer')} | {float(row.get('qty_ton') or 0):,.2f} | "
            f"{float(row.get('revenue_cny') or 0):,.2f} | {int(row.get('orders') or 0)} | {change_text} |"
        )

    lines.extend(["", "## 03 · 客户增长点", ""])
    if growth_rows:
        lines.append("| 客户 | 增长信号 | 本期销售额(元) | 本期销量(吨) |")
        lines.append("|---|---|---:|---:|")
        for row in growth_rows:
            signal = "新增/恢复采购" if row.get("is_new_or_recovered") else f"销量增长 {float(row.get('qty_change_pct') or 0):+.1f}%"
            lines.append(
                f"| {row.get('customer')} | {signal} | {float(row.get('revenue_cny') or 0):,.2f} | {float(row.get('qty_ton') or 0):,.2f} |"
            )
    else:
        lines.append("本期没有明显新增/恢复采购或销量增长超过10%的客户。")

    lines.extend(["", "## 04 · 流失/下滑风险", ""])
    if risk_rows:
        lines.append("| 客户 | 风险信号 | 上期销售额(元) | 上期销量(吨) |")
        lines.append("|---|---|---:|---:|")
        for row in risk_rows:
            signal = "本期未复购" if row.get("is_missing_repeat") else f"销量下降 {float(row.get('qty_change_pct') or 0):.1f}%"
            lines.append(
                f"| {row.get('customer')} | {signal} | {float(row.get('previous_revenue_cny') or 0):,.2f} | {float(row.get('previous_qty_ton') or 0):,.2f} |"
            )
    else:
        lines.append("未发现销量下降超过20%或上一周期有采购、本期未复购的客户。")

    lines.extend(["", "## 05 · 下一步动作", ""])
    if risk_rows:
        lines.append(f"- 今日优先跟进：{risk_rows[0].get('customer')}，确认未复购或下滑原因、下周期采购计划和价格敏感点。")
    if growth_rows:
        lines.append(f"- 本周放大增长：对 {growth_rows[0].get('customer')} 锁定复购节奏，优先保障交付和账期条件。")
    lines.append("- 管理触发条件：如果未来7天客户订单数或销量继续低于上一可比周期20%以上，升级为重点客户流失预警。")
    lines.append("- 报价策略：对增长客户控制无效折让；对风险客户用阶梯量价或交付保障换取复购确认。")

    lines.extend(["", "## 06 · 数据说明", ""])
    lines.append(
        f"数据来源：内部ERP销售订单客户聚合数据。统计周期：{period_start} 至 {period_end}；"
        f"上一可比周期：{previous_start} 至 {previous_end}。销售额口径优先使用ERP FAMOUNT。"
    )
    return "\n".join(lines).strip()


def build_customer_sales_report_markdown(
    product_id: str | None = None,
    period: str | None = None,
    *,
    language: str = "zh",
) -> str:
    return format_customer_sales_report(
        query_customer_sales_report(product_id=product_id, period=period),
        language=language,
    )


def query_production_summary(period: str | None = None) -> str:
    """Query production plan vs actual from ERP."""
    start_date, end_date = _resolve_period(period)

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT material_name, "
                    "  SUM(COALESCE(plan_qty, 0)) AS planned, "
                    "  SUM(COALESCE(produce_qty, 0)) AS produced "
                    "FROM erp_v_sale_orderentry "
                    "WHERE PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    "  AND (plan_qty IS NOT NULL OR produce_qty IS NOT NULL) "
                    "GROUP BY material_name "
                    "ORDER BY planned DESC"
                ),
                {"start_date": start_date, "end_date": end_date},
            ).fetchall()

            if not rows:
                return f"No production data found for {start_date} to {end_date}."

            lines = [
                f"PRODUCTION SUMMARY ({start_date} to {end_date}):",
                f"{'Product':<25} {'Planned(ton)':<15} {'Produced(ton)':<16} {'Utilization%':<12}",
                "-" * 68,
            ]
            for row in rows:
                name = str(row[0])
                planned = float(row[1] or 0)
                produced = float(row[2] or 0)
                util = f"{produced / planned * 100:.1f}%" if planned > 0 else "N/A"
                lines.append(
                    f"{name:<25} {planned:>12,.2f}   {produced:>13,.2f}   {util:>10}"
                )

            return "\n".join(lines)

    except Exception as e:
        return f"Error querying production data: {e}"


def query_inbound_receipts(
    product_id: str | None = None,
    period: str | None = None,
) -> str:
    """Query inbound goods receipts (入库) from ERP raw-material receiving view.

    Returns date, planned quantity, actual received quantity, and variance.
    Covers both purchase-order-backed receipts and production replenishment.
    """
    start_date, end_date = _resolve_period(period)

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return f"No ERP data mapping for product '{product_id}'."
    else:
        material_names = _ALL_MATERIAL_NAMES

    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT FDATE AS receipt_date, material_name, "
                    "  COALESCE(PLAN_QTY, 0) AS planned_qty, "
                    "  COALESCE(FREALQTY, 0) AS received_qty "
                    "FROM erp_v_raw_material_receiving "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND FDATE IS NOT NULL "
                    "  AND DATE(FDATE) >= :start_date "
                    "  AND DATE(FDATE) <= :end_date "
                    "ORDER BY FDATE DESC "
                    "LIMIT 200"
                ),
                params,
            ).fetchall()

            if not rows:
                return (
                    f"No inbound receipt records found for period {start_date} to {end_date}. "
                    "This may indicate no receipts were logged in ERP for this period, or "
                    "the raw-material receiving view is not yet populated."
                )

            lines = [
                f"INBOUND RECEIPTS 入库记录 ({start_date} to {end_date}):",
                f"{'Date':<14} {'Product':<25} {'Planned(ton)':<15} {'Received(ton)':<15} {'Variance':<12}",
                "-" * 81,
            ]
            total_planned = 0.0
            total_received = 0.0
            for row in rows:
                d = str(row[0])[:10]
                name = str(row[1])
                planned = float(row[2] or 0)
                received = float(row[3] or 0)
                variance = received - planned
                total_planned += planned
                total_received += received
                lines.append(
                    f"{d:<14} {name:<25} {planned:>12,.2f}   {received:>12,.2f}   "
                    f"{variance:>+10,.2f}"
                )

            lines.append("-" * 81)
            total_variance = total_received - total_planned
            lines.append(
                f"{'TOTAL':<40} {total_planned:>12,.2f}   {total_received:>12,.2f}   "
                f"{total_variance:>+10,.2f}"
            )
            if total_planned > 0:
                fulfill_pct = total_received / total_planned * 100
                lines.append(f"\nReceipt fulfillment rate: {fulfill_pct:.1f}%")

            return "\n".join(lines)

    except Exception as e:
        return f"Error querying inbound receipts: {e}"


def query_outbound_shipments(
    product_id: str | None = None,
    period: str | None = None,
) -> str:
    """Query outbound shipments (出库/发货) from ERP sales order view.

    Returns shipped quantities, unit prices, and remaining open-order volume.
    """
    start_date, end_date = _resolve_period(period)

    if product_id:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return f"No ERP data mapping for product '{product_id}'."
    else:
        material_names = _ALL_MATERIAL_NAMES

    placeholders, params = _material_name_params(material_names)
    params["start_date"] = start_date
    params["end_date"] = end_date

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DATE(PLANDATE) AS ship_date, material_name, "
                    "  SUM(COALESCE(FDELIQTY, 0)) AS shipped_qty, "
                    "  SUM(COALESCE(FREMAINOUTQTY, 0)) AS remaining_qty, "
                    "  SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
                    "    / NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS avg_price "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date "
                    "GROUP BY DATE(PLANDATE), material_name "
                    "ORDER BY PLANDATE DESC "
                    "LIMIT 200"
                ),
                params,
            ).fetchall()

            if not rows:
                return f"No outbound shipment records found for {start_date} to {end_date}."

            lines = [
                f"OUTBOUND SHIPMENTS 出库记录 ({start_date} to {end_date}):",
                f"{'Date':<14} {'Product':<25} {'Shipped(ton)':<15} {'Remaining(ton)':<16} {'Avg Price':<12}",
                "-" * 82,
            ]
            total_shipped = 0.0
            total_remaining = 0.0
            for row in rows:
                d = str(row[0])[:10]
                name = str(row[1])
                shipped = float(row[2] or 0)
                remaining = float(row[3] or 0)
                avg_price = float(row[4] or 0)
                total_shipped += shipped
                total_remaining += remaining
                lines.append(
                    f"{d:<14} {name:<25} {shipped:>12,.2f}   {remaining:>13,.2f}   {avg_price:>10,.2f}"
                )

            lines.append("-" * 82)
            lines.append(
                f"{'TOTAL':<40} {total_shipped:>12,.2f}   {total_remaining:>13,.2f}"
            )
            if total_remaining > 0:
                lines.append(
                    f"\nOpen order backlog (remaining to ship): {total_remaining:,.2f} ton"
                )

            return "\n".join(lines)

    except Exception as e:
        return f"Error querying outbound shipments: {e}"


def query_replenishment_status(product_id: str | None = None) -> str:
    """Combine inventory, inbound receipts, and outbound rate to assess replenishment risk.

    Provides a single context-ready summary for the chat engine to reason about
    whether inventory will cover demand until the next inbound arrives.
    """
    try:
        inv_text = query_inventory_levels(product_id)
        inbound_text = query_inbound_receipts(product_id, period="last 30 days")
        outbound_text = query_outbound_shipments(product_id, period="last 7 days")

        return (
            "=== REPLENISHMENT STATUS 补货状态 ===\n\n"
            f"[CURRENT INVENTORY 当前库存]\n{inv_text}\n\n"
            f"[RECENT INBOUND RECEIPTS 近30天入库]\n{inbound_text}\n\n"
            f"[RECENT OUTBOUND SHIPMENTS 近7天出库]\n{outbound_text}"
        )
    except Exception as e:
        return f"Error assembling replenishment status: {e}"


def query_contract_summary(period: str | None = None) -> str:
    """Query contract information from ERP (if contract tables exist)."""
    start_date, end_date = _resolve_period(period)

    try:
        with db.engine.connect() as conn:
            # Check if contract table exists
            try:
                rows = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'erp_v_contract_execution'"
                    )
                ).scalar()
                if not rows:
                    return "Contract execution table not available in current database."
            except Exception:
                return "Unable to check contract tables."

            rows = conn.execute(
                text(
                    "SELECT material_name, "
                    "  COUNT(*) AS contract_count, "
                    "  SUM(COALESCE(contract_fqty, contract_qty, ORDERQTY, 0)) AS total_qty, "
                    "  SUM(COALESCE(out_fqty, delivered_qty, plan_fqty, 0)) AS delivered "
                    "FROM erp_v_contract_execution "
                    "WHERE COALESCE(FDATE, contract_date, FDELIVERYDATE) IS NOT NULL "
                    "  AND DATE(COALESCE(FDATE, contract_date, FDELIVERYDATE)) >= :start_date "
                    "  AND DATE(COALESCE(FDATE, contract_date, FDELIVERYDATE)) <= :end_date "
                    "GROUP BY material_name "
                    "ORDER BY total_qty DESC"
                ),
                {"start_date": start_date, "end_date": end_date},
            ).fetchall()

            if not rows:
                return f"No contract records for {start_date} to {end_date}."

            lines = [
                f"CONTRACT EXECUTION SUMMARY ({start_date} to {end_date}):",
                f"{'Product':<25} {'Contracts':<12} {'Qty(ton)':<15} {'Delivered(ton)':<16} {'Fulfill%':<10}",
                "-" * 78,
            ]
            for row in rows:
                name = str(row[0])
                count = int(row[1] or 0)
                qty = float(row[2] or 0)
                delivered = float(row[3] or 0)
                fulfill = f"{delivered / qty * 100:.1f}%" if qty > 0 else "N/A"
                lines.append(
                    f"{name:<25} {count:>10}   {qty:>12,.2f}   {delivered:>13,.2f}   {fulfill:>8}"
                )

            return "\n".join(lines)

    except Exception as e:
        return f"Error querying contracts: {e}"


def query_live_market_forecast_context(
    user_query: str | None = None,
    focal_product_id: str | None = None,
    include_forecast: bool = True,
) -> str:
    """Build compact live MySQL market panel and optional forecast ranges."""
    try:
        from backend.src.service.market_data_service import market_data
        from backend.src.service.forecasting_service import forecasting_service
        from backend.src.engine.date_utils import detect_historical_date_scope

        q = (user_query or "").lower()
        date_scope = detect_historical_date_scope(user_query or "")
        requested_end = date.today()
        if date_scope and date_scope.get("cutoff_date"):
            try:
                requested_end = date.fromisoformat(str(date_scope["cutoff_date"]))
            except Exception:
                requested_end = date.today()

        def _resolve_requested_window_days(text: str) -> Optional[int]:
            normalized = (text or "").lower()
            if any(marker in text for marker in ("最近一周", "近一周", "本周", "一周", "7天", "7日")):
                return 7
            if any(marker in text for marker in ("最近两周", "近两周", "两周", "14天", "14日")):
                return 14
            if any(marker in normalized for marker in ("last 7 days", "past 7 days", "recent 7 days", "weekly")):
                return 7
            if any(marker in normalized for marker in ("last 14 days", "past 14 days", "recent 14 days")):
                return 14
            if any(marker in normalized for marker in ("last 30 days", "past 30 days", "recent 30 days", "monthly")):
                return 30
            match = re.search(
                r"(?:last|past|recent|近|最近|过去)\s*(\d{1,2})\s*(?:day|days|天|日)",
                normalized,
                re.IGNORECASE,
            )
            if match:
                try:
                    return max(1, int(match.group(1)))
                except Exception:
                    return None
            return None

        requested_days = _resolve_requested_window_days(user_query or "")
        wants_forecast = include_forecast or any(
            k in q
            for k in [
                "forecast",
                "outlook",
                "weekly",
                "report",
                "price",
                "预测",
                "周报",
                "报告",
                "价格",
                "走势",
            ]
        )

        extracted_ids = extract_product_ids_in_text(user_query or "")
        if focal_product_id:
            product_ids = [focal_product_id]
        elif extracted_ids:
            product_ids = extracted_ids
        else:
            product_ids = [
                "crude_oil",
                "naphtha",
                "isoprene",
                "piperylene",
                "dcpd",
                "sis",
                "c5_resin",
                "blowing_agent",
                "styrene",
            ]

        def _product_label(pid: str) -> str:
            node_id = resolve_product_id(pid) or pid
            try:
                from backend.src.domain.knowledge_graph import domain_graph

                node = next(
                    (
                        n
                        for n in domain_graph.nodes
                        if str(getattr(n, "id", "") or "").strip().lower()
                        == str(node_id or "").strip().lower()
                    ),
                    None,
                )
                if node:
                    return str(getattr(node, "name_cn", None) or getattr(node, "name_en", None) or node_id)
            except Exception:
                pass
            return str(node_id or pid)

        seen: set[str] = set()
        ordered_ids: list[str] = []
        for pid in product_ids:
            if pid in seen:
                continue
            seen.add(pid)
            ordered_ids.append(pid)

        lines: list[str] = ["LIVE MARKET DATA (MYSQL):"]
        usable_ids: list[str] = []

        for pid in ordered_ids[:8]:
            hist = market_data.get_price_history(pid)
            if not hist:
                continue
            if requested_days:
                try:
                    requested_start = requested_end - timedelta(days=requested_days - 1)
                    window_points = [
                        point
                        for point in hist
                        if requested_start
                        <= date.fromisoformat(str(point.get("date") or "")[:10])
                        <= requested_end
                    ]
                    if len(window_points) < requested_days:
                        available_start = date.fromisoformat(str(hist[0].get("date") or "")[:10])
                        available_end = date.fromisoformat(str(hist[-1].get("date") or "")[:10])
                        closest_start = max(available_start, available_end - timedelta(days=requested_days - 1))
                        label = _product_label(pid)
                        lines.append(
                            f"No market price records found for requested period {requested_start} to {requested_end}."
                        )
                        lines.append(
                            f"AVAILABLE MARKET PRICE RANGE for {label}: {available_start} to {available_end} ({len(hist)} rows)."
                        )
                        lines.append(
                            f"CLOSEST AVAILABLE {requested_days}-DAY WINDOW: {closest_start} to {available_end}."
                        )
                        lines.append(
                            "Use the closest available window or choose a period inside the available date range."
                        )
                except Exception:
                    pass
            latest = hist[-1]
            latest_price = float(latest.get("price", 0) or 0)
            latest_date = str(latest.get("date") or "")[:10]
            tail = hist[-8:] if len(hist) >= 8 else hist
            base_price = float(tail[0].get("price", latest_price) or latest_price)
            change_7d = (
                ((latest_price - base_price) / base_price) * 100
                if base_price
                else 0.0
            )
            lines.append(
                f"- {pid}: {latest_price:,.2f} on {latest_date} (7d {change_7d:+.2f}%)"
            )
            usable_ids.append(pid)

        if len(lines) == 1:
            return ""

        if wants_forecast and usable_ids:
            lines.append("\nFORECAST SNAPSHOT (STAGE 3):")
            for pid in usable_ids[:4]:
                try:
                    fc = forecasting_service.forecast(pid)
                    scenarios = fc.get("scenarios", {})

                    def _fmt(h: str) -> str:
                        base = (scenarios.get(h) or {}).get("base") or {}
                        low = base.get("low")
                        high = base.get("high")
                        if low is None or high is None:
                            return "Unavailable"
                        return f"{float(low):,.0f}-{float(high):,.0f}"

                    lines.append(
                        f"- {pid}: 3d {_fmt('3d')} | 7d {_fmt('7d')} | 30d {_fmt('30d')}"
                    )
                except Exception:
                    continue

        return "\n".join(lines)
    except Exception:
        return ""


def build_db_grounding_context(
    user_query: str | None = None,
    focal_product_id: str | None = None,
    include_forecast: bool = True,
    include_view_audit: bool = True,
) -> str:
    """Shared DB grounding block for report/analysis prompts."""
    blocks: list[str] = []
    market_block = query_live_market_forecast_context(
        user_query=user_query,
        focal_product_id=focal_product_id,
        include_forecast=include_forecast,
    )
    if market_block:
        blocks.append(market_block)
    if include_view_audit:
        blocks.append(query_view_coverage_audit())
    return "\n\n".join(blocks).strip()


def query_view_coverage_audit(force_refresh: bool = False) -> str:
    """Build a compact audit over all key DB views using full-table aggregates.

    This verifies chatbot context is grounded on complete view contents rather than
    paginated UI results.
    """
    view_prefixes = ("v_", "lz_v_", "erp_v_", "sale_erp_v_")
    date_candidates = [
        "biz_date",
        "date",
        "PLANDATE",
        "FDATE",
        "FDELIVERYDATE",
        "contract_date",
        "created_at",
        "updated_at",
    ]

    global _view_audit_cache_text, _view_audit_cache_ts
    now = time.time()
    if (
        not force_refresh
        and _view_audit_cache_text
        and (now - _view_audit_cache_ts) < _VIEW_AUDIT_CACHE_TTL_SECONDS
    ):
        return _view_audit_cache_text

    try:
        with db.engine.connect() as conn:
            views = conn.execute(
                text(
                    "SELECT table_name "
                    "FROM information_schema.views "
                    "WHERE table_schema = DATABASE() "
                    "ORDER BY table_name"
                )
            ).fetchall()

            view_names = [
                str(row[0])
                for row in views
                if row and row[0] and str(row[0]).startswith(view_prefixes)
            ]

            if not view_names:
                return "VIEW COVERAGE AUDIT: No matching views found."

            lines: list[str] = [
                "VIEW COVERAGE AUDIT (FULL TABLE AGGREGATES):",
                f"- Matched views: {len(view_names)}",
            ]

            family_counts = {
                "v_": 0,
                "lz_v_": 0,
                "erp_v_": 0,
                "sale_erp_v_": 0,
            }
            total_rows = 0

            for v in view_names:
                for prefix in family_counts:
                    if v.startswith(prefix):
                        family_counts[prefix] += 1
                        break

                # Detect a useful date column for min/max coverage reporting.
                cols = conn.execute(
                    text(
                        "SELECT column_name "
                        "FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() "
                        "  AND table_name = :t"
                    ),
                    {"t": v},
                ).fetchall()
                col_set = {str(c[0]) for c in cols if c and c[0]}

                date_col = next((c for c in date_candidates if c in col_set), None)

                try:
                    if date_col:
                        row = conn.execute(
                            text(
                                f"SELECT COUNT(*) AS cnt, "
                                f"MIN(DATE(`{date_col}`)) AS min_d, "
                                f"MAX(DATE(`{date_col}`)) AS max_d "
                                f"FROM `{v}`"
                            )
                        ).fetchone()
                        cnt = int(row[0] or 0)
                        min_d = str(row[1]) if row[1] is not None else "-"
                        max_d = str(row[2]) if row[2] is not None else "-"
                        lines.append(f"- {v}: rows={cnt}, date_range={min_d}..{max_d}")
                    else:
                        cnt = int(
                            conn.execute(text(f"SELECT COUNT(*) FROM `{v}`")).scalar()
                            or 0
                        )
                        lines.append(f"- {v}: rows={cnt}")
                    total_rows += cnt
                except Exception as exc:
                    err = str(exc).split("[SQL:")[0].strip()
                    if len(err) > 180:
                        err = err[:177] + "..."
                    lines.append(f"- {v}: audit_failed={err}")

            lines.insert(2, f"- Total rows across matched views: {total_rows}")
            lines.insert(
                3,
                "- View families: "
                + ", ".join(f"{k}{v}" for k, v in family_counts.items()),
            )

            result = "\n".join(lines)
            _view_audit_cache_text = result
            _view_audit_cache_ts = now
            return result
    except Exception as e:
        return f"VIEW COVERAGE AUDIT failed: {e}"


def build_erp_context(
    product_id: str | None = None,
    period: str | None = None,
    query_type: str = "auto",
    user_query: str | None = None,
) -> str:
    """Build comprehensive ERP data context for the chatbot.

    query_type can be:
      - "sales": sales summary + daily + customer breakdown
      - "inventory": inventory levels
      - "production": production plan vs actual
      - "contracts": contract execution
      - "inbound": inbound goods receipts (入库)
      - "outbound": outbound shipments (出库/发货)
      - "replenishment": inventory + inbound + outbound coverage status
      - "briefing": everything (for boss_briefing)
      - "report_light": lightweight briefing for latency-sensitive report mode
      - "auto": sales + inventory (default for erp_data_query)
    """
    parts: list[str] = []

    if query_type in ("sales", "auto", "briefing", "report_light"):
        parts.append(query_sales_summary(product_id, period))

    if query_type in ("inventory", "auto", "briefing", "report_light"):
        parts.append(query_inventory_levels(product_id))

    if query_type in ("sales", "briefing"):
        parts.append(query_sales_by_customer(product_id, period))

    if query_type in ("production", "briefing"):
        parts.append(query_production_summary(period))

    if query_type in ("contracts", "briefing"):
        parts.append(query_contract_summary(period))

    if query_type in ("inbound", "replenishment", "briefing"):
        parts.append(query_inbound_receipts(product_id, period))

    if query_type in ("outbound", "replenishment", "briefing"):
        parts.append(query_outbound_shipments(product_id, period))

    if query_type == "replenishment":
        parts.append(query_replenishment_status(product_id))

    # Add shared DB grounding blocks so report prompts are grounded on complete
    # view coverage (all rows/pages), not a paginated UI slice.
    if query_type == "briefing":
        grounding = build_db_grounding_context(
            user_query=user_query,
            focal_product_id=product_id,
            include_forecast=True,
            include_view_audit=True,
        )
        if grounding:
            parts.append(grounding)
    elif query_type == "report_light":
        # Lightweight report profile: keep only compact grounding, skip full view-audit.
        grounding = build_db_grounding_context(
            user_query=user_query,
            focal_product_id=product_id,
            include_forecast=True,
            include_view_audit=False,
        )
        if grounding:
            parts.append(grounding)

    if not parts:
        return "No ERP data available."

    return "\n\n".join(parts)

