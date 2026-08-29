"""
weekly_report_data.py
---------------------
Data-access layer for the C5/C9 weekly report generator.

All numeric values are computed here — the LLM receives them as pre-rendered
JSON and may only narrate them.  Never let the LLM recompute or round these
values (see spec §7 — the pre-renderer pattern).

⚠️  Critical SQL rules (non-negotiable):
    1. LZ views: filter by  price REGEXP '^[0-9]+(\\.[0-9]+)?$'
       Raw rows contain strings like '询价中' which break AVG().
    2. ERP weighted avg: SUM(FPRICE * FQTY_ORIGIN) / NULLIF(SUM(FQTY_ORIGIN), 0)
       Plain AVG(FPRICE) ignores quantity weighting → materially wrong.
    3. ERP and LZ are never substituted for each other.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _table_columns(engine: Engine, table_name: str) -> set[str]:
    """Return lowercase column names for a table/view; empty set on failure."""
    try:
        sql = """
            SELECT LOWER(COLUMN_NAME)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"table_name": table_name}).fetchall()
        return {str(r[0]).lower() for r in rows}
    except Exception:
        return set()


def _pick_column(columns: set[str], *candidates: str) -> Optional[str]:
    # 1) exact match
    for cand in candidates:
        if cand.lower() in columns:
            return cand

    # 2) tolerate aliased columns such as "biz_date(业务日期)"
    for cand in candidates:
        cand_l = cand.lower()
        for col in columns:
            if col.startswith(cand_l + "("):
                return col

    # 3) final loose containment fallback
    for cand in candidates:
        cand_l = cand.lower()
        for col in columns:
            if cand_l in col:
                return col
    return None


def _qcol(column_name: str) -> str:
    """Quote an identifier column name with backticks for MySQL."""
    return "`" + str(column_name).replace("`", "``") + "`"


# ---------------------------------------------------------------------------
# Dataclasses (spec §6.1 and §7.1)
# ---------------------------------------------------------------------------

@dataclass
class ProductSpec:
    slug: str                        # "isoprene"
    name_zh: str                     # "异戊二烯"
    name_en: str                     # "Isoprene"
    category: str                    # "feedstock" | "liquid_product" | "resin"
    in_scope: bool
    lz_view: Optional[str]           # "lz_v_异戊二烯_data"
    erp_sales_view: Optional[str]    # "sale_erp_v_异戊二烯_data"
    forecast_key: Optional[str] = None      # key into forecast_price.product
    report_profile: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MacroContext:
    brent_price: Optional[str]       # "84.20"
    brent_wow_pct: Optional[str]     # "上涨2.42%"
    naphtha_price: Optional[str]     # "630"
    naphtha_wow_pct: Optional[str]   # "下跌0.5%"
    events: List[Dict[str, str]] = field(default_factory=list)
    # [{"date":"2026-04-22","title":"OPEC+维持产量","impact":"原油承压"}]


@dataclass
class PriceTimeseries:
    ym_list: List[str]               # ["2025-04", "2025-05", ...]
    lz_avg: List[Optional[float]]    # monthly LZ prevailing mean (None = no data)
    erp_wavg: List[Optional[float]]  # monthly ERP quantity-weighted avg


@dataclass
class MonthlyDailySeries:
    date_list: List[str]             # ["2026-04-01", "2026-04-02", ...]
    lz_daily: List[Optional[float]]  # LZ prevailing daily avg (None = no data)
    erp_daily: List[Optional[float]] # ERP quantity-weighted daily avg (None = no data)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def serialize_product_chart_payload(
    product: "ProductSpec",
    timeseries: "PriceTimeseries",
    monthly_series: "MonthlyDailySeries",
    forecast: Optional["ForecastBand"],
    week_start: str,
    week_end: str,
    language: str = "zh",
) -> Dict[str, Any]:
    """Build a JSON-safe snapshot payload for dynamic weekly report charts."""
    is_en = (language or "zh").lower().startswith("en")

    yearly_points = [
        {
            "period": ym,
            "label": ym,
            "lz_value": lz_val,
            "erp_value": erp_val,
        }
        for ym, lz_val, erp_val in zip(
            timeseries.ym_list,
            timeseries.lz_avg,
            timeseries.erp_wavg,
        )
    ]

    monthly_points = [
        {
            "period": day,
            "label": str(day)[5:10].replace("-", "/") if len(str(day)) >= 10 else str(day),
            "lz_value": lz_val,
            "erp_value": erp_val,
        }
        for day, lz_val, erp_val in zip(
            monthly_series.date_list,
            monthly_series.lz_daily,
            monthly_series.erp_daily,
        )
    ]

    anchor_month = (
        str(monthly_series.date_list[0])[:7]
        if monthly_series.date_list
        else str(week_end)[:7]
    )

    return {
        "product_slug": product.slug,
        "product_name_zh": product.name_zh,
        "product_name_en": product.name_en,
        "yearly": {
            "chart_type": "yearly",
            "title": "12-Month Price Trend" if is_en else "近12月价格走势",
            "unit": "CNY/t" if is_en else "元/吨",
            "series": [
                {
                    "key": "lz_value",
                    "label": "LZ Market Avg" if is_en else "隆众市场均价",
                    "color": "#00A6A6",
                },
                {
                    "key": "erp_value",
                    "label": "ERP Weighted Avg" if is_en else "ERP 加权均价",
                    "color": "#D4A437",
                },
            ],
            "points": yearly_points,
            "forecast_band": {
                "low": _safe_float(forecast.low) if forecast else None,
                "high": _safe_float(forecast.high) if forecast else None,
                "period": forecast.period if forecast else None,
                "confidence_label": forecast.confidence_label if forecast else None,
                "model_version": forecast.model_version if forecast else None,
                "source": forecast.source if forecast else None,
                "snapshot_id": forecast.snapshot_id if forecast else None,
                "as_of_date": forecast.as_of_date if forecast else None,
                "generated_at": forecast.generated_at if forecast else None,
                "policy_id": forecast.policy_id if forecast else None,
                "horizon": forecast.horizon if forecast else None,
            },
            "report_period": str(week_end)[:7],
        },
        "monthly": {
            "chart_type": "monthly",
            "title": (
                f"Daily Prices ({anchor_month})"
                if is_en else
                f"{anchor_month}日价格走势"
            ),
            "unit": "CNY/t" if is_en else "元/吨",
            "series": [
                {
                    "key": "lz_value",
                    "label": "LZ Market Avg" if is_en else "隆众市场均价",
                    "color": "#00A6A6",
                },
                {
                    "key": "erp_value",
                    "label": "ERP Weighted Avg" if is_en else "ERP 加权均价",
                    "color": "#D4A437",
                },
            ],
            "points": monthly_points,
            "anchor_month": anchor_month,
            "highlight": {
                "start": week_start,
                "end": week_end,
            },
        },
    }


@dataclass
class PriceSnapshot:
    sinopec_listed: Optional[str]    # "10000"
    sinopec_wow_delta: Optional[str] # "持平" / "下跌300元"
    region_prices: List[Dict]        # [{"region":"华东","range":"9800-10200"}]
    erp_weighted_avg: Optional[str]  # "9860"
    erp_wow_pct: Optional[str]       # "下跌1.4%"
    erp_weekly_volume: Optional[str] # formatted for context
    snapshot_lines: List[str] = field(default_factory=list)


@dataclass
class ForecastBand:
    period: str                      # "5月初至5月上旬"
    trend_verb: str                  # "走稳"
    low: Optional[str]               # "9300"
    high: Optional[str]              # "9800"
    confidence_label: str            # "中"
    confidence_score: int            # 3
    model_version: str               # "fc_v2.3_20260420"
    source: str = "Forecasting Tier"
    snapshot_id: Optional[str] = None
    as_of_date: Optional[str] = None
    generated_at: Optional[str] = None
    policy_id: Optional[str] = None
    horizon: Optional[str] = None


@dataclass
class PreRenderedContext:
    # Identity
    product_name_zh: str
    product_name_en: str
    week_start: str                        # "2026-04-21"
    week_end: str                          # "2026-04-27"
    prev_week_start: str
    prev_week_end: str

    # Price snapshot — pre-computed, pre-formatted
    sinopec_listed_price: Optional[str]
    sinopec_listed_wow_delta: Optional[str]
    region_prices: List[Dict]
    erp_weighted_avg: Optional[str]
    erp_wow_pct: Optional[str]

    # Macro pass-through
    brent_price: Optional[str]
    brent_wow_pct: Optional[str]
    naphtha_price: Optional[str]
    naphtha_wow_pct: Optional[str]

    snapshot_lines: List[str] = field(default_factory=list)

    # Events for THIS product
    events: List[Dict] = field(default_factory=list)

    # Related product moves (pre-computed)
    related_moves: List[Dict] = field(default_factory=list)

    # Forecast — injected, NOT generated by LLM
    forecast_period: str = "下周"
    forecast_trend_verb: str = "走稳"
    forecast_low: Optional[str] = None
    forecast_high: Optional[str] = None
    confidence_label: str = "低"
    confidence_score: int = 2
    forecast_model_version: str = "fc_fallback"
    forecast_source: str = "Forecasting Tier"
    forecast_snapshot_id: Optional[str] = None
    forecast_as_of_date: Optional[str] = None
    forecast_generated_at: Optional[str] = None
    forecast_policy_id: Optional[str] = None
    forecast_horizon: Optional[str] = None


# ---------------------------------------------------------------------------
# Week resolution
# ---------------------------------------------------------------------------

def resolve_week_dates(
    week_start_str: Optional[str] = None,
    week_end_str: Optional[str] = None,
    current_week: bool = False,
) -> tuple[str, str, str, str]:
    """
    Returns (week_start, week_end, prev_week_start, prev_week_end) as ISO strings.
    Defaults to the most recently completed Mon–Sun if not provided.
    When current_week=True, returns Mon -> yesterday (partial current week).
    """
    today = date.today()
    if week_start_str and week_end_str:
        week_start = date.fromisoformat(week_start_str)
        week_end = date.fromisoformat(week_end_str)
    elif current_week:
        # Current partial week: Monday through yesterday
        week_end = today - timedelta(days=1)
        week_start = today - timedelta(days=today.weekday())
    else:
        # Most recently completed Mon-Sun week.
        # Tue -> last Sunday (2 days ago), Mon -> yesterday (Sunday).
        week_end = today - timedelta(days=today.weekday() + 1)
        week_start = week_end - timedelta(days=6)

    prev_week_end = week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)

    return (
        week_start.isoformat(),
        week_end.isoformat(),
        prev_week_start.isoformat(),
        prev_week_end.isoformat(),
    )


# ---------------------------------------------------------------------------
# Product catalog
# ---------------------------------------------------------------------------

def load_product_catalog(db) -> List[ProductSpec]:
    """Load in-scope products from report_product_catalog table."""
    rows = db.list_report_products()
    if not rows:
        raise ValueError(
            "report_product_catalog is empty — cannot generate weekly report. "
            "This indicates config corruption."
        )
    return [
        ProductSpec(
            slug=r["slug"],
            name_zh=r["name_zh"],
            name_en=r["name_en"],
            category=r.get("category", "liquid_product"),
            in_scope=bool(r.get("in_scope", 1)),
            lz_view=r.get("lz_view"),
            erp_sales_view=r.get("erp_sales_view"),
            report_profile=r.get("report_profile") or {},
            forecast_key=r.get("forecast_key"),
        )
        for r in rows
        if r.get("in_scope", 1)
    ]


# ---------------------------------------------------------------------------
# Macro context
# ---------------------------------------------------------------------------

_BRENT_LZ_VIEWS = ["lz_v_布伦特原油_data", "lz_v_brent_data"]
_NAPHTHA_LZ_VIEWS = ["lz_v_日本石脑油_data", "lz_v_naphtha_data"]


def _try_lz_price(engine: Engine, view: str, price_date_col: str, price_col: str, where_extra: str = "") -> Optional[float]:
    """Attempt to fetch the latest price from a LZ view. Returns None on failure."""
    try:
        sql = f"""
            SELECT CAST({price_col} AS DECIMAL(12,2)) AS p
            FROM `{view}`
            WHERE {price_col} REGEXP '^[0-9]+(\\\\.[0-9]+)?$'
            {where_extra}
            ORDER BY {price_date_col} DESC
            LIMIT 1
        """
        with engine.connect() as conn:
            row = conn.execute(text(sql)).first()
        if row:
            return float(row[0])
    except Exception:
        pass
    return None


def _format_wow_pct(current: Optional[float], prev: Optional[float]) -> Optional[str]:
    """Return a signed percentage string usable in both Chinese and English reports, e.g. '+2.42%'."""
    if current is None or prev is None or prev == 0:
        return None
    pct = (current - prev) / abs(prev) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


_PROFILE_FRESHNESS_DAYS = 21


def _parse_iso_day(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text_value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text_value, fmt).date()
        except Exception:
            continue
    return None


def _is_stale(latest_day: Optional[str], week_end: str, max_age_days: int = _PROFILE_FRESHNESS_DAYS) -> bool:
    latest_dt = _parse_iso_day(latest_day)
    week_end_dt = _parse_iso_day(week_end)
    if latest_dt is None or week_end_dt is None:
        return True
    return (week_end_dt - latest_dt).days > max_age_days


def _format_price_value(value: Optional[float], unit: str) -> Optional[str]:
    if value is None:
        return None
    unit_l = str(unit or "").lower()
    decimals = 3 if "usd" in unit_l or "美元" in unit else 0
    return f"{value:,.{decimals}f}"


def _format_delta_label(current: Optional[float], prev: Optional[float], unit: str, is_en: bool) -> Optional[str]:
    if current is None or prev is None:
        return None
    delta = current - prev
    if abs(delta) < 1e-9:
        return "flat" if is_en else "持平"
    unit_suffix = f" {unit}" if is_en else unit
    magnitude = f"{abs(delta):,.3f}" if "usd" in str(unit or "").lower() or "美元" in unit else f"{abs(delta):,.0f}"
    if is_en:
        direction = "up" if delta > 0 else "down"
        return f"{direction} {magnitude}{unit_suffix}"
    direction = "上涨" if delta > 0 else "下跌"
    return f"{direction}{magnitude}{unit_suffix}"


def _format_pct_label(current: Optional[float], prev: Optional[float], is_en: bool) -> Optional[str]:
    pct = _format_wow_pct(current, prev)
    if not pct:
        return None
    if is_en:
        return f"up {pct[1:]}" if pct.startswith("+") else f"down {pct[1:]}"
    return f"上涨{pct[1:]}" if pct.startswith("+") else f"下跌{pct[1:]}"


def _fetch_market_latest_day(engine: Engine, product_id: str, price_type: str, as_of: str) -> Optional[str]:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT DATE_FORMAT(date, '%Y-%m-%d')
                    FROM market_prices
                    WHERE product_id = :pid AND price_type = :pt AND date <= :d
                    ORDER BY date DESC
                    LIMIT 1
                    """
                ),
                {"pid": product_id, "pt": price_type, "d": as_of},
            ).first()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def _fetch_market_grouped_series(
    engine: Engine,
    product_id: str,
    price_type: str,
    week_end: str,
    *,
    granularity: str,
    month_start: Optional[str] = None,
) -> Dict[str, float]:
    try:
        if granularity == "month":
            sql = text(
                """
                SELECT DATE_FORMAT(date, '%Y-%m') AS period, AVG(price) AS value
                FROM market_prices
                WHERE product_id = :pid
                  AND price_type = :pt
                  AND date >= DATE_SUB(:week_end, INTERVAL 13 MONTH)
                  AND date <= :week_end
                GROUP BY period
                ORDER BY period
                """
            )
            params = {"pid": product_id, "pt": price_type, "week_end": week_end}
        else:
            sql = text(
                """
                SELECT DATE_FORMAT(date, '%Y-%m-%d') AS period, AVG(price) AS value
                FROM market_prices
                WHERE product_id = :pid
                  AND price_type = :pt
                  AND date >= :month_start
                  AND date <= :week_end
                GROUP BY period
                ORDER BY period
                """
            )
            params = {
                "pid": product_id,
                "pt": price_type,
                "month_start": month_start,
                "week_end": week_end,
            }
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {
            str(row[0]): float(row[1])
            for row in rows
            if row[0] and row[1] is not None
        }
    except Exception:
        return {}


def _fetch_erp_weighted_value(engine: Engine, product: ProductSpec, start: str, end: str) -> Optional[float]:
    erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
    for view in erp_views:
        cols = _table_columns(engine, view)
        erp_date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
        if not erp_date_col:
            continue
        erp_date_col_q = _qcol(erp_date_col)
        sql = text(
            f"""
            SELECT SUM(e.FPRICE * e.FQTY_ORIGIN) / NULLIF(SUM(e.FQTY_ORIGIN), 0) AS erp_wavg
            FROM `{view}` e
            WHERE e.{erp_date_col_q} BETWEEN :start AND :end
              AND e.FQTY_ORIGIN > 0
              AND e.FPRICE > 0
            """
        )
        try:
            with engine.connect() as conn:
                row = conn.execute(sql, {"start": start, "end": end}).first()
            if row and row[0] is not None:
                return float(row[0])
        except Exception:
            continue
    return None


def _fetch_erp_latest_day(engine: Engine, product: ProductSpec, as_of: str) -> Optional[str]:
    erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
    for view in erp_views:
        cols = _table_columns(engine, view)
        erp_date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
        if not erp_date_col:
            continue
        erp_date_col_q = _qcol(erp_date_col)
        sql = text(
            f"""
            SELECT DATE_FORMAT(e.{erp_date_col_q}, '%Y-%m-%d')
            FROM `{view}` e
            WHERE e.{erp_date_col_q} <= :week_end
              AND e.FQTY_ORIGIN > 0
              AND e.FPRICE > 0
            ORDER BY e.{erp_date_col_q} DESC
            LIMIT 1
            """
        )
        try:
            with engine.connect() as conn:
                row = conn.execute(sql, {"week_end": as_of}).first()
            if row and row[0]:
                return str(row[0])
        except Exception:
            continue
    return None


def _fetch_erp_grouped_series(
    engine: Engine,
    product: ProductSpec,
    week_end: str,
    *,
    granularity: str,
    month_start: Optional[str] = None,
) -> Dict[str, float]:
    erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
    for view in erp_views:
        cols = _table_columns(engine, view)
        erp_date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
        if not erp_date_col:
            continue
        erp_date_col_q = _qcol(erp_date_col)
        if granularity == "month":
            sql = text(
                f"""
                SELECT DATE_FORMAT(e.{erp_date_col_q}, '%Y-%m') AS period,
                       SUM(e.FPRICE * e.FQTY_ORIGIN) / NULLIF(SUM(e.FQTY_ORIGIN), 0) AS erp_wavg
                FROM `{view}` e
                WHERE e.{erp_date_col_q} >= DATE_SUB(:week_end, INTERVAL 13 MONTH)
                  AND e.{erp_date_col_q} <= :week_end
                  AND e.FQTY_ORIGIN > 0
                  AND e.FPRICE > 0
                GROUP BY period
                ORDER BY period
                """
            )
            params = {"week_end": week_end}
        else:
            sql = text(
                f"""
                SELECT DATE_FORMAT(e.{erp_date_col_q}, '%Y-%m-%d') AS period,
                       SUM(e.FPRICE * e.FQTY_ORIGIN) / NULLIF(SUM(e.FQTY_ORIGIN), 0) AS erp_wavg
                FROM `{view}` e
                WHERE e.{erp_date_col_q} >= :month_start
                  AND e.{erp_date_col_q} <= :week_end
                  AND e.FQTY_ORIGIN > 0
                  AND e.FPRICE > 0
                GROUP BY period
                ORDER BY period
                """
            )
            params = {"month_start": month_start, "week_end": week_end}
        try:
            with engine.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            series = {
                str(row[0]): float(row[1])
                for row in rows
                if row[0] and row[1] is not None
            }
            if series:
                return series
        except Exception:
            continue
    return {}


def _profile_chart_defs(product: ProductSpec, chart_type: str) -> List[Dict[str, Any]]:
    return list((((product.report_profile or {}).get("chart_profiles") or {}).get(chart_type) or {}).get("series") or [])


def _profile_snapshot_defs(product: ProductSpec) -> List[Dict[str, Any]]:
    return list((((product.report_profile or {}).get("snapshot_profile") or {}).get("lines") or []))


def _build_profile_snapshot_lines(
    engine: Engine,
    product: ProductSpec,
    week_end: str,
    prev_week_end: str,
    erp_cur: Optional[float],
    erp_prev: Optional[float],
    language: str,
) -> tuple[List[str], List[Dict[str, str]]]:
    is_en = (language or "zh").lower().startswith("en")
    lines: List[str] = []
    region_prices: List[Dict[str, str]] = []
    for line_def in _profile_snapshot_defs(product):
        source = str(line_def.get("source") or "market")
        unit = str(line_def.get("unit") or ("CNY/t" if is_en else "元/吨"))
        label = str(line_def.get("label") or "价格")
        wow_mode = str(line_def.get("wow") or "none")
        current: Optional[float] = None
        previous: Optional[float] = None
        if source == "market":
            product_id = str(line_def.get("product_id") or product.slug)
            price_type = str(line_def.get("price_type") or "")
            if not price_type:
                continue
            latest_day = _fetch_market_latest_day(engine, product_id, price_type, week_end)
            if _is_stale(latest_day, week_end):
                continue
            current = _fetch_market_price(engine, product_id, price_type, week_end)
            previous = _fetch_market_price(engine, product_id, price_type, prev_week_end)
            if current is not None and ("市场" in label or price_type in {"east_china", "south_china", "wuhan", "maoming", "yangzi", "bulk_sh", "baling_1105", "guangzhou"}):
                region_prices.append({"region": label, "range": _format_price_value(current, unit) or ""})
        elif source == "erp":
            latest_day = _fetch_erp_latest_day(engine, product, week_end)
            if _is_stale(latest_day, week_end):
                continue
            current = erp_cur
            previous = erp_prev
        if current is None:
            continue
        value_str = _format_price_value(current, unit)
        if not value_str:
            continue
        wow_label: Optional[str] = None
        if wow_mode == "pct":
            wow_label = _format_pct_label(current, previous, is_en)
        elif wow_mode == "delta":
            wow_label = _format_delta_label(current, previous, unit, is_en)
        if is_en:
            suffix = f", WoW {wow_label}" if wow_label else ""
            lines.append(f"- {label}: **{value_str}** {unit}{suffix}")
        else:
            suffix = f"，环比{wow_label}" if wow_label else ""
            lines.append(f"- {label}报 **{value_str}** {unit}{suffix}")
    return lines, region_prices


def _fetch_market_price(engine: Engine, product_id: str, price_type: str, as_of: str) -> Optional[float]:
    """Read the most recent price from market_prices on or before *as_of*."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT price FROM market_prices
                    WHERE product_id = :pid AND price_type = :pt AND date <= :d
                    ORDER BY date DESC LIMIT 1
                    """
                ),
                {"pid": product_id, "pt": price_type, "d": as_of},
            ).first()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def fetch_macro_context(engine: Engine, week_end: str, prev_week_end: str) -> MacroContext:
    """
    Fetch Brent crude + Japan naphtha prices and WoW change.
    Primary source: market_prices table (product_id='crude_oil'/'naphtha').
    Fallback: LZ views when market_prices has no data.
    """
    brent_cur: Optional[float] = None
    brent_prev: Optional[float] = None
    naphtha_cur: Optional[float] = None
    naphtha_prev: Optional[float] = None

    # -- Primary: market_prices table --
    brent_cur = _fetch_market_price(engine, "crude_oil", "brent", week_end)
    if brent_cur is not None:
        brent_prev = _fetch_market_price(engine, "crude_oil", "brent", prev_week_end)

    naphtha_cur = _fetch_market_price(engine, "naphtha", "japan_cif", week_end)
    if naphtha_cur is not None:
        naphtha_prev = _fetch_market_price(engine, "naphtha", "japan_cif", prev_week_end)

    # -- Fallback: LZ views (only when market_prices returned nothing) --
    if brent_cur is None:
        for view in _BRENT_LZ_VIEWS:
            try:
                cols = _table_columns(engine, view)
                date_col = _pick_column(cols, "price_date", "biz_date", "FDATE", "date")
                price_col = _pick_column(cols, "price", "tax_price", "FTAXPRICE")
                if not date_col or not price_col:
                    continue
                date_col_q = _qcol(date_col)
                price_col_q = _qcol(price_col)
                with engine.connect() as conn:
                    conn.execute(text(f"SELECT 1 FROM `{view}` LIMIT 1"))
                brent_cur = _try_lz_price(
                    engine, view, date_col_q, price_col_q,
                    f"AND {date_col_q} <= '{week_end}'"
                )
                if brent_cur is not None:
                    brent_prev = _try_lz_price(
                        engine, view, date_col_q, price_col_q,
                        f"AND {date_col_q} <= '{prev_week_end}'"
                    )
                    break
            except Exception:
                continue

    if naphtha_cur is None:
        for view in _NAPHTHA_LZ_VIEWS:
            try:
                cols = _table_columns(engine, view)
                date_col = _pick_column(cols, "price_date", "biz_date", "FDATE", "date")
                price_col = _pick_column(cols, "price", "tax_price", "FTAXPRICE")
                if not date_col or not price_col:
                    continue
                date_col_q = _qcol(date_col)
                price_col_q = _qcol(price_col)
                with engine.connect() as conn:
                    conn.execute(text(f"SELECT 1 FROM `{view}` LIMIT 1"))
                naphtha_cur = _try_lz_price(
                    engine, view, date_col_q, price_col_q,
                    f"AND {date_col_q} <= '{week_end}'"
                )
                if naphtha_cur is not None:
                    naphtha_prev = _try_lz_price(
                        engine, view, date_col_q, price_col_q,
                        f"AND {date_col_q} <= '{prev_week_end}'"
                    )
                    break
            except Exception:
                continue

    events: List[Dict[str, str]] = []
    try:
        events_start = (date.fromisoformat(week_end) - timedelta(days=6)).isoformat()
        with engine.connect() as conn:
            result = conn.execute(text(
                """
                SELECT event_date, title, impact_summary
                FROM intelligence_events
                WHERE event_date BETWEEN :events_start AND :week_end
                ORDER BY event_date DESC
                LIMIT 5
                """
            ), {"events_start": events_start, "week_end": week_end})
            for row in result:
                events.append({
                    "date": str(row[0]),
                    "title": str(row[1])[:50],
                    "impact": str(row[2] or "")[:30],
                })
    except Exception:
        pass  # Table may not exist

    return MacroContext(
        brent_price=f"{brent_cur:.2f}" if brent_cur is not None else None,
        brent_wow_pct=_format_wow_pct(brent_cur, brent_prev),
        naphtha_price=f"{naphtha_cur:.0f}" if naphtha_cur is not None else None,
        naphtha_wow_pct=_format_wow_pct(naphtha_cur, naphtha_prev),
        events=events,
    )


# ---------------------------------------------------------------------------
# Per-product: price snapshot
# ---------------------------------------------------------------------------

def fetch_price_snapshot(
    engine: Engine,
    product: ProductSpec,
    week_start: str,
    week_end: str,
    prev_week_start: str,
    prev_week_end: str,
    language: str = "zh",
) -> PriceSnapshot:
    """
    Fetch ERP weighted-avg price + LZ prevailing price for the report week
    and compute WoW change.

    CRITICAL: ERP weighted avg = SUM(FPRICE * FQTY_ORIGIN) / SUM(FQTY_ORIGIN)
    Never use AVG(FPRICE).
    """
    erp_cur: Optional[float] = None
    erp_prev: Optional[float] = None
    lz_cur: Optional[float] = None
    lz_prev: Optional[float] = None

    # -- ERP --
    if product.erp_sales_view:
        # For resin products, need material_model filter; use generic query for now
        erp_sql_template = """
            SELECT
                SUM(e.FPRICE * e.FQTY_ORIGIN) / NULLIF(SUM(e.FQTY_ORIGIN), 0) AS wavg
            FROM `{view}` e
            WHERE e.{date_col} BETWEEN :start AND :end
              AND e.FQTY_ORIGIN > 0
              AND e.FPRICE > 0
        """
        # Some products have multiple ERP views (e.g. pentane_blowing_agent)
        erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
        for view in erp_views:
            try:
                cols = _table_columns(engine, view)
                date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
                if not date_col:
                    continue
                date_col_q = _qcol(date_col)
                with engine.connect() as conn:
                    row = conn.execute(
                        text(erp_sql_template.format(view=view, date_col=date_col_q)),
                        {"start": week_start, "end": week_end},
                    ).first()
                    if row and row[0] is not None:
                        erp_cur = float(row[0])

                    row_prev = conn.execute(
                        text(erp_sql_template.format(view=view, date_col=date_col_q)),
                        {"start": prev_week_start, "end": prev_week_end},
                    ).first()
                    if row_prev and row_prev[0] is not None:
                        erp_prev = float(row_prev[0])
                break  # use first view that returns data
            except Exception as e:
                logger.debug("ERP snapshot query failed for %s/%s: %s", product.slug, view, e)

    # -- LZ --
    if product.lz_view:
        cols = _table_columns(engine, product.lz_view)
        lz_date_col = _pick_column(cols, "price_date", "biz_date", "FDATE", "date")
        lz_price_col = _pick_column(cols, "price", "tax_price", "FTAXPRICE")
        if lz_date_col and lz_price_col:
            lz_date_col_q = _qcol(lz_date_col)
            lz_price_col_q = _qcol(lz_price_col)
            lz_sql = """
            SELECT AVG(CAST({price_col} AS DECIMAL(12, 2))) AS lz_avg
            FROM `{view}`
            WHERE {date_col} BETWEEN :start AND :end
              AND {price_col} REGEXP '^[0-9]+(\\\\.[0-9]+)?$'
        """
            try:
                with engine.connect() as conn:
                    row = conn.execute(
                        text(lz_sql.format(view=product.lz_view, date_col=lz_date_col_q, price_col=lz_price_col_q)),
                        {"start": week_start, "end": week_end},
                    ).first()
                    if row and row[0] is not None:
                        lz_cur = float(row[0])

                    row_prev = conn.execute(
                        text(lz_sql.format(view=product.lz_view, date_col=lz_date_col_q, price_col=lz_price_col_q)),
                        {"start": prev_week_start, "end": prev_week_end},
                    ).first()
                    if row_prev and row_prev[0] is not None:
                        lz_prev = float(row_prev[0])
            except Exception as e:
                logger.debug("LZ snapshot query failed for %s: %s", product.slug, e)

    # -- Format --
    def fmt(v: Optional[float]) -> Optional[str]:
        if v is None:
            return None
        return f"{v:,.0f}"

    erp_wow_pct: Optional[str] = None
    if erp_cur is not None and erp_prev is not None and erp_prev != 0:
        pct = (erp_cur - erp_prev) / abs(erp_prev) * 100
        direction = "上涨" if pct >= 0 else "下跌"
        erp_wow_pct = f"{direction}{abs(pct):.1f}%"

    # LZ as the market price reference for region_prices
    region_prices: List[Dict] = []
    if lz_cur is not None:
        region_prices.append({"region": "市场均价", "range": fmt(lz_cur) or ""})
    if lz_prev is not None and lz_cur is not None:
        delta = lz_cur - lz_prev
        direction_str = "上涨" if delta >= 0 else "下跌"
        sinopec_wow = f"{direction_str}{abs(delta):,.0f}元" if delta != 0 else "持平"
    else:
        sinopec_wow = "持平"

    snapshot_lines, profile_region_prices = _build_profile_snapshot_lines(
        engine=engine,
        product=product,
        week_end=week_end,
        prev_week_end=prev_week_end,
        erp_cur=erp_cur,
        erp_prev=erp_prev,
        language=language,
    )

    return PriceSnapshot(
        sinopec_listed=None,           # Would come from a dedicated Sinopec price table
        sinopec_wow_delta=None,
        region_prices=profile_region_prices or region_prices,
        erp_weighted_avg=fmt(erp_cur),
        erp_wow_pct=erp_wow_pct,
        erp_weekly_volume=None,
        snapshot_lines=snapshot_lines,
    )


# ---------------------------------------------------------------------------
# Per-product: 12-month timeseries
# ---------------------------------------------------------------------------

def fetch_12mo_timeseries(
    engine: Engine,
    product: ProductSpec,
    week_end: str,
) -> PriceTimeseries:
    """
    Fetch 13-month monthly series (12 complete months + current partial) for
    both LZ prevailing and ERP weighted avg.

    ⚠️  LZ: numeric regex guard mandatory — raw data contains '询价中' strings.
    ⚠️  ERP: quantity-weighted average, never plain AVG.
    """
    lz_monthly: Dict[str, float] = {}
    erp_monthly: Dict[str, float] = {}

    if product.lz_view:
        cols = _table_columns(engine, product.lz_view)
        lz_date_col = _pick_column(cols, "price_date", "biz_date", "FDATE", "date")
        lz_price_col = _pick_column(cols, "price", "tax_price", "FTAXPRICE")
        lz_sql = """
            SELECT
                DATE_FORMAT({date_col}, '%Y-%m') AS ym,
                AVG(CAST({price_col} AS DECIMAL(12, 2))) AS lz_avg
            FROM `{view}`
            WHERE {date_col} >= DATE_SUB(:week_end, INTERVAL 13 MONTH)
              AND {price_col} REGEXP '^[0-9]+(\\\\.[0-9]+)?$'
            GROUP BY ym
            ORDER BY ym
        """
        if lz_date_col and lz_price_col:
            lz_date_col_q = _qcol(lz_date_col)
            lz_price_col_q = _qcol(lz_price_col)
            try:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text(lz_sql.format(view=product.lz_view, date_col=lz_date_col_q, price_col=lz_price_col_q)),
                        {"week_end": week_end},
                    ).fetchall()
                    for row in rows:
                        if row[0] and row[1] is not None:
                            lz_monthly[str(row[0])] = float(row[1])
            except Exception as e:
                logger.debug("LZ timeseries failed for %s: %s", product.slug, e)

    erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
    for view in erp_views:
        cols = _table_columns(engine, view)
        erp_date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
        if not erp_date_col:
            continue
        erp_date_col_q = _qcol(erp_date_col)
        erp_sql = """
            SELECT
                DATE_FORMAT(e.{date_col}, '%Y-%m') AS ym,
                SUM(e.FPRICE * e.FQTY_ORIGIN) / NULLIF(SUM(e.FQTY_ORIGIN), 0) AS erp_wavg
            FROM `{view}` e
            WHERE e.{date_col} >= DATE_SUB(:week_end, INTERVAL 13 MONTH)
              AND e.FQTY_ORIGIN > 0
              AND e.FPRICE > 0
            GROUP BY ym
            ORDER BY ym
        """
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(erp_sql.format(view=view, date_col=erp_date_col_q)),
                    {"week_end": week_end},
                ).fetchall()
                for row in rows:
                    if row[0] and row[1] is not None:
                        erp_monthly[str(row[0])] = float(row[1])
            if erp_monthly:
                break
        except Exception as e:
            logger.debug("ERP timeseries failed for %s/%s: %s", product.slug, view, e)

    # Build unified ym list (union of keys, sorted)
    all_yms = sorted(set(list(lz_monthly.keys()) + list(erp_monthly.keys())))

    return PriceTimeseries(
        ym_list=all_yms,
        lz_avg=[lz_monthly.get(ym) for ym in all_yms],
        erp_wavg=[erp_monthly.get(ym) for ym in all_yms],
    )


# ---------------------------------------------------------------------------
# Per-product: monthly daily series (current month up to week_end)
# ---------------------------------------------------------------------------

def fetch_monthly_daily_series(
    engine: Engine,
    product: "ProductSpec",
    week_end: str,
) -> "MonthlyDailySeries":
    """
    Fetch daily price rows for the latest available month up to week_end.

    Returns a MonthlyDailySeries with one entry per calendar day that has data.
    Same column-detection logic as fetch_12mo_timeseries().
    """
    from datetime import date as _date

    def _month_start_for(day_str: str) -> str:
        try:
            return _date.fromisoformat(day_str).replace(day=1).isoformat()
        except Exception:
            return day_str[:7] + "-01"

    def _latest_lz_day() -> Optional[str]:
        if not product.lz_view:
            return None
        cols = _table_columns(engine, product.lz_view)
        lz_date_col = _pick_column(cols, "price_date", "biz_date", "FDATE", "date")
        lz_price_col = _pick_column(cols, "price", "tax_price", "FTAXPRICE")
        if not lz_date_col or not lz_price_col:
            return None
        lz_date_col_q = _qcol(lz_date_col)
        lz_price_col_q = _qcol(lz_price_col)
        sql = """
            SELECT DATE_FORMAT({date_col}, '%Y-%m-%d') AS day
            FROM `{view}`
            WHERE {date_col} <= :week_end
              AND {price_col} REGEXP '^[0-9]+(\\\\.[0-9]+)?$'
            ORDER BY {date_col} DESC
            LIMIT 1
        """
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(sql.format(
                        view=product.lz_view,
                        date_col=lz_date_col_q,
                        price_col=lz_price_col_q,
                    )),
                    {"week_end": week_end},
                ).first()
            return str(row[0]) if row and row[0] else None
        except Exception:
            return None

    def _latest_erp_day() -> Optional[str]:
        erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
        for view in erp_views:
            cols = _table_columns(engine, view)
            erp_date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
            if not erp_date_col:
                continue
            erp_date_col_q = _qcol(erp_date_col)
            sql = """
                SELECT DATE_FORMAT(e.{date_col}, '%Y-%m-%d') AS day
                FROM `{view}` e
                WHERE e.{date_col} <= :week_end
                  AND e.FQTY_ORIGIN > 0
                  AND e.FPRICE > 0
                ORDER BY e.{date_col} DESC
                LIMIT 1
            """
            try:
                with engine.connect() as conn:
                    row = conn.execute(
                        text(sql.format(view=view, date_col=erp_date_col_q)),
                        {"week_end": week_end},
                    ).first()
                if row and row[0]:
                    return str(row[0])
            except Exception:
                continue
        return None

    try:
        week_end_dt = _date.fromisoformat(week_end)
        month_start = week_end_dt.replace(day=1).isoformat()
    except Exception:
        month_start = week_end[:7] + "-01"

    latest_available_day = max(
        [d for d in [_latest_lz_day(), _latest_erp_day()] if d],
        default=None,
    )
    anchor_end = latest_available_day or week_end
    month_start = _month_start_for(anchor_end)

    lz_daily: Dict[str, float] = {}
    erp_daily: Dict[str, float] = {}

    if product.lz_view:
        cols = _table_columns(engine, product.lz_view)
        lz_date_col = _pick_column(cols, "price_date", "biz_date", "FDATE", "date")
        lz_price_col = _pick_column(cols, "price", "tax_price", "FTAXPRICE")
        if lz_date_col and lz_price_col:
            lz_date_col_q = _qcol(lz_date_col)
            lz_price_col_q = _qcol(lz_price_col)
            lz_sql = """
                SELECT
                    DATE_FORMAT({date_col}, '%Y-%m-%d') AS day,
                    AVG(CAST({price_col} AS DECIMAL(12, 2))) AS lz_avg
                FROM `{view}`
                WHERE {date_col} >= :month_start
                  AND {date_col} <= :week_end
                  AND {price_col} REGEXP '^[0-9]+(\\\\.[0-9]+)?$'
                GROUP BY day
                ORDER BY day
            """
            try:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text(lz_sql.format(
                            view=product.lz_view,
                            date_col=lz_date_col_q,
                            price_col=lz_price_col_q,
                        )),
                        {"month_start": month_start, "week_end": anchor_end},
                    ).fetchall()
                    for row in rows:
                        if row[0] and row[1] is not None:
                            lz_daily[str(row[0])] = float(row[1])
            except Exception as e:
                logger.debug("LZ monthly-daily failed for %s: %s", product.slug, e)

    erp_views = [v.strip() for v in (product.erp_sales_view or "").split("+") if v.strip()]
    for view in erp_views:
        cols = _table_columns(engine, view)
        erp_date_col = _pick_column(cols, "delivery_date", "PLANDATE", "FDATE", "biz_date", "date")
        if not erp_date_col:
            continue
        erp_date_col_q = _qcol(erp_date_col)
        erp_sql = """
            SELECT
                DATE_FORMAT(e.{date_col}, '%Y-%m-%d') AS day,
                SUM(e.FPRICE * e.FQTY_ORIGIN) / NULLIF(SUM(e.FQTY_ORIGIN), 0) AS erp_wavg
            FROM `{view}` e
            WHERE e.{date_col} >= :month_start
              AND e.{date_col} <= :week_end
              AND e.FQTY_ORIGIN > 0
              AND e.FPRICE > 0
            GROUP BY day
            ORDER BY day
        """
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(erp_sql.format(view=view, date_col=erp_date_col_q)),
                    {"month_start": month_start, "week_end": anchor_end},
                ).fetchall()
                for row in rows:
                    if row[0] and row[1] is not None:
                        erp_daily[str(row[0])] = float(row[1])
            if erp_daily:
                break
        except Exception as e:
            logger.debug("ERP monthly-daily failed for %s/%s: %s", product.slug, view, e)

    all_days = sorted(set(list(lz_daily.keys()) + list(erp_daily.keys())))

    return MonthlyDailySeries(
        date_list=all_days,
        lz_daily=[lz_daily.get(d) for d in all_days],
        erp_daily=[erp_daily.get(d) for d in all_days],
    )


def build_product_chart_payload(
    engine: Engine,
    product: ProductSpec,
    forecast: Optional[ForecastBand],
    week_start: str,
    week_end: str,
    language: str = "zh",
) -> Dict[str, Any]:
    yearly_defs = _profile_chart_defs(product, "yearly")
    monthly_defs = _profile_chart_defs(product, "monthly")
    if not yearly_defs and not monthly_defs:
        return serialize_product_chart_payload(
            product=product,
            timeseries=fetch_12mo_timeseries(engine, product, week_end),
            monthly_series=fetch_monthly_daily_series(engine, product, week_end),
            forecast=forecast,
            week_start=week_start,
            week_end=week_end,
            language=language,
        )

    def _build_series_payload(
        chart_type: str,
        series_defs: List[Dict[str, Any]],
        anchor_end: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        active_series: List[Dict[str, Any]] = []
        series_data: Dict[str, Dict[str, float]] = {}
        periods: set[str] = set()
        for series_def in series_defs:
            source = str(series_def.get("source") or "market")
            key = str(series_def.get("key") or "")
            if not key:
                continue
            latest_day: Optional[str] = None
            data: Dict[str, float] = {}
            if source == "market":
                product_id = str(series_def.get("product_id") or product.slug)
                price_type = str(series_def.get("price_type") or "")
                if not price_type:
                    continue
                latest_day = _fetch_market_latest_day(engine, product_id, price_type, week_end)
                if _is_stale(latest_day, week_end):
                    continue
                data = _fetch_market_grouped_series(
                    engine,
                    product_id,
                    price_type,
                    anchor_end or week_end,
                    granularity=("month" if chart_type == "yearly" else "day"),
                    month_start=(None if chart_type == "yearly" else (anchor_end or week_end)[:7] + "-01"),
                )
            elif source == "erp":
                latest_day = _fetch_erp_latest_day(engine, product, week_end)
                if _is_stale(latest_day, week_end):
                    continue
                data = _fetch_erp_grouped_series(
                    engine,
                    product,
                    anchor_end or week_end,
                    granularity=("month" if chart_type == "yearly" else "day"),
                    month_start=(None if chart_type == "yearly" else (anchor_end or week_end)[:7] + "-01"),
                )
            if not data:
                continue
            active_series.append(
                {
                    "key": key,
                    "label": str(series_def.get("label") or key),
                    "color": str(series_def.get("color") or "#1890ff"),
                    "axis": str(series_def.get("axis") or "left"),
                    "unit": str(series_def.get("unit") or ("CNY/t" if (language or "zh").lower().startswith("en") else "元/吨")),
                }
            )
            series_data[key] = data
            periods.update(data.keys())
        ordered_periods = sorted(periods)
        points: List[Dict[str, Any]] = []
        for period in ordered_periods:
            point: Dict[str, Any] = {
                "period": period,
                "label": period if chart_type == "yearly" else str(period)[5:10].replace("-", "/"),
            }
            for series_meta in active_series:
                point[series_meta["key"]] = series_data.get(series_meta["key"], {}).get(period)
            points.append(point)
        left_unit = next((series["unit"] for series in active_series if series.get("axis") != "right"), active_series[0]["unit"] if active_series else ("CNY/t" if (language or "zh").lower().startswith("en") else "元/吨"))
        right_unit = next((series["unit"] for series in active_series if series.get("axis") == "right"), None)
        return active_series, points, left_unit if right_unit is None else left_unit

    latest_days: List[str] = []
    for series_def in monthly_defs:
        source = str(series_def.get("source") or "market")
        if source == "market":
            price_type = str(series_def.get("price_type") or "")
            if price_type:
                latest_day = _fetch_market_latest_day(engine, str(series_def.get("product_id") or product.slug), price_type, week_end)
                if latest_day and not _is_stale(latest_day, week_end):
                    latest_days.append(latest_day)
        elif source == "erp":
            latest_day = _fetch_erp_latest_day(engine, product, week_end)
            if latest_day and not _is_stale(latest_day, week_end):
                latest_days.append(latest_day)
    anchor_end = max(latest_days) if latest_days else week_end
    anchor_month = anchor_end[:7]

    yearly_series, yearly_points, yearly_unit = _build_series_payload("yearly", yearly_defs)
    monthly_series, monthly_points, monthly_unit = _build_series_payload("monthly", monthly_defs, anchor_end=anchor_end)
    if not yearly_points and not monthly_points:
        return serialize_product_chart_payload(
            product=product,
            timeseries=fetch_12mo_timeseries(engine, product, week_end),
            monthly_series=fetch_monthly_daily_series(engine, product, week_end),
            forecast=forecast,
            week_start=week_start,
            week_end=week_end,
            language=language,
        )

    is_en = (language or "zh").lower().startswith("en")
    return {
        "product_slug": product.slug,
        "product_name_zh": product.name_zh,
        "product_name_en": product.name_en,
        "yearly": {
            "chart_type": "yearly",
            "title": "12-Month Price Trend" if is_en else "近12月价格走势",
            "unit": yearly_unit,
            "series": yearly_series,
            "points": yearly_points,
            "forecast_band": {
                "low": _safe_float(forecast.low) if forecast else None,
                "high": _safe_float(forecast.high) if forecast else None,
                "period": forecast.period if forecast else None,
                "confidence_label": forecast.confidence_label if forecast else None,
                "model_version": forecast.model_version if forecast else None,
                "source": forecast.source if forecast else None,
                "snapshot_id": forecast.snapshot_id if forecast else None,
                "as_of_date": forecast.as_of_date if forecast else None,
                "generated_at": forecast.generated_at if forecast else None,
                "policy_id": forecast.policy_id if forecast else None,
                "horizon": forecast.horizon if forecast else None,
            },
            "report_period": str(week_end)[:7],
            "left_axis_unit": next((series.get("unit") for series in yearly_series if series.get("axis") != "right"), yearly_unit),
            "right_axis_unit": next((series.get("unit") for series in yearly_series if series.get("axis") == "right"), None),
        },
        "monthly": {
            "chart_type": "monthly",
            "title": (f"Daily Prices ({anchor_month})" if is_en else f"{anchor_month}日价格走势"),
            "unit": monthly_unit,
            "series": monthly_series,
            "points": monthly_points,
            "anchor_month": anchor_month,
            "highlight": {"start": week_start, "end": week_end},
            "left_axis_unit": next((series.get("unit") for series in monthly_series if series.get("axis") != "right"), monthly_unit),
            "right_axis_unit": next((series.get("unit") for series in monthly_series if series.get("axis") == "right"), None),
        },
    }


# ---------------------------------------------------------------------------
# Per-product: forecast band
# ---------------------------------------------------------------------------

_TREND_VERB_MAP = {
    "STABLE": "走稳",
    "WEAK_DOWN": "易跌难涨",
    "DOWN_PRESSURE": "承压下行",
    "OSCILLATE_DOWN": "维持震荡下行走势",
    "MILD_UP": "稳中有涨",
    "NARROW_RANGE": "窄幅调整",
}


def fetch_forecast_band(engine: Engine, product: ProductSpec, week_end: str) -> ForecastBand:
    """
    Try to pull a forecast from the forecast_price table.
    Falls back to a confidence=2/低 placeholder if not available (spec §11).
    """
    fallback = ForecastBand(
        period="下周",
        trend_verb="走稳",
        low=None,
        high=None,
        confidence_label="低",
        confidence_score=2,
        model_version="fc_fallback",
    )

    key = product.forecast_key or product.slug
    try:
        sql = """
            SELECT price_low, price_high, trend_code, confidence_score, model_version, forecast_period
            FROM forecast_price
            WHERE product = :product
              AND forecast_date > :week_end
            ORDER BY forecast_date ASC
            LIMIT 1
        """
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"product": key, "week_end": week_end}).first()
        if not row:
            return fallback

        trend_verb = _TREND_VERB_MAP.get(str(row[2] or ""), "走稳")
        score = int(row[3]) if row[3] is not None else 2
        score = max(1, min(5, score))
        labels = {5: "高", 4: "较高", 3: "中", 2: "低", 1: "低"}

        return ForecastBand(
            period=str(row[5] or "下周"),
            trend_verb=trend_verb,
            low=f"{float(row[0]):,.0f}" if row[0] is not None else None,
            high=f"{float(row[1]):,.0f}" if row[1] is not None else None,
            confidence_label=labels.get(score, "低"),
            confidence_score=score,
            model_version=str(row[4] or "fc_fallback"),
        )
    except Exception as e:
        logger.debug("Forecast query failed for %s: %s", product.slug, e)
        return fallback


# ---------------------------------------------------------------------------
# Per-product: related events
# ---------------------------------------------------------------------------

def fetch_product_events(
    engine: Engine,
    product: ProductSpec,
    week_start: str,
    week_end: str,
) -> List[Dict[str, str]]:
    """
    Fetch intelligence events tagged to this product for the week.
    Returns [] if the table doesn't exist or no events found.
    """
    try:
        sql = """
            SELECT event_date, title, impact_summary
            FROM intelligence_events
            WHERE event_date BETWEEN :start AND :end
              AND (product_tags LIKE :slug_pat OR product_tags IS NULL)
            ORDER BY event_date DESC
            LIMIT 5
        """
        with engine.connect() as conn:
            rows = conn.execute(
                text(sql),
                {
                    "start": week_start,
                    "end": week_end,
                    "slug_pat": f"%{product.slug}%",
                },
            ).fetchall()
        return [
            {
                "date": str(r[0]),
                "title": str(r[1] or "")[:50],
                "impact": str(r[2] or "")[:30],
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Pre-rendered context assembly
# ---------------------------------------------------------------------------

def build_prerendered_ctx(
    product: ProductSpec,
    snapshot: PriceSnapshot,
    macro: MacroContext,
    events: List[Dict],
    forecast: ForecastBand,
    week_start: str,
    week_end: str,
    prev_week_start: str,
    prev_week_end: str,
    related_moves: Optional[List[Dict]] = None,
) -> PreRenderedContext:
    return PreRenderedContext(
        product_name_zh=product.name_zh,
        product_name_en=product.name_en,
        week_start=week_start,
        week_end=week_end,
        prev_week_start=prev_week_start,
        prev_week_end=prev_week_end,
        sinopec_listed_price=snapshot.sinopec_listed,
        sinopec_listed_wow_delta=snapshot.sinopec_wow_delta,
        region_prices=snapshot.region_prices,
        erp_weighted_avg=snapshot.erp_weighted_avg,
        erp_wow_pct=snapshot.erp_wow_pct,
        snapshot_lines=snapshot.snapshot_lines,
        brent_price=macro.brent_price,
        brent_wow_pct=macro.brent_wow_pct,
        naphtha_price=macro.naphtha_price,
        naphtha_wow_pct=macro.naphtha_wow_pct,
        events=events,
        related_moves=related_moves or [],
        forecast_period=forecast.period,
        forecast_trend_verb=forecast.trend_verb,
        forecast_low=forecast.low,
        forecast_high=forecast.high,
        confidence_label=forecast.confidence_label,
        confidence_score=forecast.confidence_score,
        forecast_model_version=forecast.model_version,
        forecast_source=forecast.source,
        forecast_snapshot_id=forecast.snapshot_id,
        forecast_as_of_date=forecast.as_of_date,
        forecast_generated_at=forecast.generated_at,
        forecast_policy_id=forecast.policy_id,
        forecast_horizon=forecast.horizon,
    )


# ---------------------------------------------------------------------------
# Guardrail: collect all allowed numeric tokens from a PreRenderedContext
# ---------------------------------------------------------------------------

def collect_all_numbers(ctx: PreRenderedContext) -> set[str]:
    """Flatten every numeric token present in the pre-rendered context."""
    allowed: set[str] = set()
    _RE_NUM = re.compile(r'\d{2,}(?:\.\d+)?')

    def _add(val: Any) -> None:
        if isinstance(val, (int, float)):
            for m in _RE_NUM.findall(str(val)):
                allowed.add(m)
        elif isinstance(val, str):
            for m in _RE_NUM.findall(val):
                allowed.add(m)
        elif isinstance(val, (list, tuple)):
            for item in val:
                _add(item)
        elif isinstance(val, dict):
            for v in val.values():
                _add(v)

    import dataclasses
    for f in dataclasses.fields(ctx):
        _add(getattr(ctx, f.name))

    return allowed
