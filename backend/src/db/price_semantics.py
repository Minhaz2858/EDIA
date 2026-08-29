from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class LZMarketSpec:
    product_id: str
    price_type: str
    table_name: str
    average_supplier: str


@dataclass(frozen=True)
class ERPSaleSpec:
    product_id: str
    price_type: str
    table_name: str


@dataclass(frozen=True)
class ProductPairSpec:
    canonical_product_id: str
    enterprise_product_id: str
    display_name: str
    lz_source_name: str
    erp_source_name: str
    lz_spec: LZMarketSpec
    erp_spec: ERPSaleSpec


LZ_MARKET_SPECS: dict[str, LZMarketSpec] = {
    "enterprise_c5": LZMarketSpec(
        product_id="cracked_c5",
        price_type="average",
        table_name="lz_v_裂解c5_data",
        average_supplier="裂解C5均价",
    ),
    "enterprise_c9": LZMarketSpec(
        product_id="cracked_c9",
        price_type="average",
        table_name="lz_v_裂解c9_data",
        average_supplier="裂解C9均价",
    ),
}


ERP_SALE_SPECS: dict[str, ERPSaleSpec] = {
    "enterprise_c5": ERPSaleSpec(
        product_id="cracked_c5",
        price_type="erp_sale_order_weighted",
        table_name="sale_erp_v_工业用裂解碳五_data",
    ),
    "enterprise_c9": ERPSaleSpec(
        product_id="cracked_c9",
        price_type="erp_sale_order_weighted",
        table_name="sale_erp_v_工业用裂解碳九_data",
    ),
}


PRODUCT_PAIR_SPECS: dict[str, ProductPairSpec] = {
    "enterprise_c5": ProductPairSpec(
        canonical_product_id="cracked_c5",
        enterprise_product_id="enterprise_c5",
        display_name="Cracked C5",
        lz_source_name="裂解C5",
        erp_source_name="工业用裂解碳五",
        lz_spec=LZ_MARKET_SPECS["enterprise_c5"],
        erp_spec=ERP_SALE_SPECS["enterprise_c5"],
    ),
    "enterprise_c9": ProductPairSpec(
        canonical_product_id="cracked_c9",
        enterprise_product_id="enterprise_c9",
        display_name="Cracked C9",
        lz_source_name="裂解C9",
        erp_source_name="工业用裂解碳九",
        lz_spec=LZ_MARKET_SPECS["enterprise_c9"],
        erp_spec=ERP_SALE_SPECS["enterprise_c9"],
    ),
}


def compute_series_coverage(
    erp_series: Iterable[dict[str, Any]],
    lz_series: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    erp_list = list(erp_series)
    lz_list = list(lz_series)
    erp_dates = {row["date"] for row in erp_list if row.get("date")}
    lz_dates = {row["date"] for row in lz_list if row.get("date")}
    overlap_dates = sorted(erp_dates & lz_dates)

    if overlap_dates:
        status = "READY_FOR_GAP"
    elif erp_dates or lz_dates:
        status = "PARTIAL_COVERAGE"
    else:
        status = "NOT_COMPARABLE"

    total_distinct_dates = len(erp_dates | lz_dates)
    overlap_ratio = (
        round(len(overlap_dates) / total_distinct_dates, 4)
        if total_distinct_dates
        else 0.0
    )

    return {
        "alignment_mode": "same_day",
        "status": status,
        "erp_date_count": len(erp_dates),
        "lz_date_count": len(lz_dates),
        "overlap_date_count": len(overlap_dates),
        "overlap_ratio": overlap_ratio,
        "latest_erp_date": max(erp_dates) if erp_dates else None,
        "latest_lz_date": max(lz_dates) if lz_dates else None,
        "latest_overlap_date": overlap_dates[-1] if overlap_dates else None,
    }


def _numeric_price_guard_sql(column_name: str = "tax_price(含税单价)") -> str:
    # Excludes non-numeric values such as '询价中'.
    return f"`{column_name}` REGEXP '^-?[0-9]+(\\\\.[0-9]+)?$'"


def _normalize_date_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    normalized = str(value).strip()
    if not normalized:
        return None
    normalized = normalized.replace("/", "-")
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return normalized


def lz_average_series_sql(table_name: str) -> str:
    return (
        "SELECT "
        "  `biz_date(业务日期)` AS biz_date, "
        "  ROUND(AVG(CAST(`tax_price(含税单价)` AS DECIMAL(18,4))), 2) AS market_price, "
        "  COUNT(*) AS row_count "
        f"FROM `{table_name}` "
        "WHERE `supplier_name(厂商名称)` = :supplier_name "
        f"  AND {_numeric_price_guard_sql()} "
        "GROUP BY `biz_date(业务日期)` "
        "ORDER BY `biz_date(业务日期)` ASC"
    )


def erp_weighted_sale_series_sql(table_name: str) -> str:
    return (
        "SELECT "
        "  DATE(PLANDATE) AS biz_date, "
        "  ROUND(SUM(FPRICE * FQTY_ORIGIN) / NULLIF(SUM(FQTY_ORIGIN), 0), 2) AS ecisco_price, "
        "  SUM(FQTY_ORIGIN) AS total_qty, "
        "  COUNT(*) AS row_count "
        f"FROM `{table_name}` "
        "WHERE PLANDATE IS NOT NULL "
        "  AND FPRICE IS NOT NULL "
        "  AND FPRICE > 0 "
        "  AND FQTY_ORIGIN IS NOT NULL "
        "  AND FQTY_ORIGIN > 0 "
        "GROUP BY DATE(PLANDATE) "
        "ORDER BY DATE(PLANDATE) ASC"
    )


def query_lz_average_series(
    conn: Connection,
    spec: LZMarketSpec,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(lz_average_series_sql(spec.table_name)),
        {"supplier_name": spec.average_supplier},
    ).fetchall()
    return [
        {
            "date": _normalize_date_value(biz_date),
            "market_price": float(market_price),
            "row_count": int(row_count or 0),
        }
        for biz_date, market_price, row_count in rows
        if _normalize_date_value(biz_date) is not None and market_price is not None
    ]


def query_erp_weighted_sale_series(
    conn: Connection,
    spec: ERPSaleSpec,
) -> list[dict[str, Any]]:
    rows = conn.execute(text(erp_weighted_sale_series_sql(spec.table_name))).fetchall()
    return [
        {
            "date": _normalize_date_value(biz_date),
            "ecisco_price": float(ecisco_price),
            "total_qty": float(total_qty or 0),
            "row_count": int(row_count or 0),
        }
        for biz_date, ecisco_price, total_qty, row_count in rows
        if _normalize_date_value(biz_date) is not None and ecisco_price is not None
    ]


def merge_price_gap_series(
    erp_series: Iterable[dict[str, Any]],
    lz_series: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    erp_list = list(erp_series)
    lz_list = list(lz_series)
    coverage = compute_series_coverage(erp_list, lz_list)
    lz_by_date = {row["date"]: row for row in lz_list}
    merged: list[dict[str, Any]] = []

    for erp_row in erp_list:
        date_value = erp_row["date"]
        market_row = lz_by_date.get(date_value)
        if not market_row:
            continue

        ecisco_price = float(erp_row["ecisco_price"])
        market_price = float(market_row["market_price"])
        gap_abs = round(ecisco_price - market_price, 2)
        gap_pct = round((gap_abs / market_price) * 100, 2) if market_price else 0.0

        merged.append(
            {
                "date": date_value,
                "ecisco_price": round(ecisco_price, 2),
                "market_price": round(market_price, 2),
                "gap_abs": gap_abs,
                "gap_pct": gap_pct,
                "erp_total_qty": round(float(erp_row.get("total_qty") or 0), 2),
                "erp_row_count": int(erp_row.get("row_count") or 0),
                "lz_row_count": int(market_row.get("row_count") or 0),
                "alignment_mode": coverage["alignment_mode"],
                "coverage_status": coverage["status"],
            }
        )

    merged.sort(key=lambda row: row["date"])
    return merged
