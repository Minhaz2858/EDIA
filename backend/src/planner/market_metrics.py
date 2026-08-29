"""Fetch structured market_prices metrics for deterministic chatbot answers."""

from __future__ import annotations

from typing import List, Optional

from backend.src.domain.edia_ontology import (
    change_days_for_metric,
    default_products_for_metric,
    metric_spec,
    resolve_metric,
    resolve_price_type,
    resolve_products,
)
from backend.src.domain.product_resolver import looks_like_sinopec_listing_query
from backend.src.tools.db_tools import get_db_price, get_db_price_for_type


def fetch_market_metric(
    product_id: str,
    price_type: str,
    change_days: int = 7,
    language: str = "zh",
) -> str:
    if price_type and price_type != "east_china":
        return get_db_price_for_type(
            product_id,
            price_type=price_type,
            change_days=change_days,
            language=language,
        )
    return get_db_price(product_id, change_days=change_days, language=language)


def fetch_routed_market_block(
    user_query: str,
    *,
    product_ids: Optional[List[str]] = None,
    price_types: Optional[List[str]] = None,
    metric_id: Optional[str] = None,
    change_days: Optional[int] = None,
    language: str = "zh",
) -> str:
    """Build INTERNAL DATABASE LIVE DATA block for routed market queries."""
    mid = metric_id or resolve_metric(user_query)
    products = list(product_ids or [])
    if not products:
        products = resolve_products(user_query)
    if not products and mid:
        products = default_products_for_metric(mid)
    if not products and looks_like_sinopec_listing_query(user_query):
        mid = mid or "sinopec_monthly_listing"
        products = default_products_for_metric(mid)

    if not products:
        return ""

    ptype = (price_types or [None])[0] if price_types else resolve_price_type(user_query, mid)
    if not ptype and mid == "sinopec_monthly_listing":
        ptype = "sinopec_sh"
    if not ptype:
        ptype = "east_china"

    days = change_days if change_days is not None else change_days_for_metric(mid, user_query)

    ok_rows: List[str] = []
    failed: List[str] = []
    for pid in products:
        row = fetch_market_metric(pid, ptype, change_days=days, language=language)
        if (
            "Error" not in row
            and "No real MySQL" not in row
            and "暂无" not in row
            and "No sinopec_sh listing rows" not in row
        ):
            ok_rows.append(row)
        else:
            failed.append(f"{pid}: {row}")

    parts: List[str] = []
    if ok_rows:
        parts.append("INTERNAL DATABASE LIVE DATA:\n" + "\n".join(ok_rows))
    if failed:
        parts.append("DB LOOKUP MISSING:\n" + "\n".join(failed))
    return "\n\n".join(parts).strip()


def fetch_metric_plan_products(
    user_query: str,
    products: List[str],
    metric_id: str,
    language: str = "zh",
) -> str:
    spec = metric_spec(metric_id)
    ptype = (spec.price_type if spec else None) or "east_china"
    days = change_days_for_metric(metric_id, user_query)
    return fetch_routed_market_block(
        user_query,
        product_ids=products,
        price_types=[ptype],
        metric_id=metric_id,
        change_days=days,
        language=language,
    )
