from __future__ import annotations

from datetime import date as _date
from datetime import timedelta as _timedelta

from backend.src.service.market_data_service import market_data
from backend.src.service.market_data_service import REAL_DATA_MAP
from backend.src.domain.knowledge_graph import domain_graph
from backend.src.domain.product_resolver import (
    get_product_node_by_id,
    resolve_product_id,
)


def _parse_date(d: str) -> _date | None:
    try:
        # market_data stores ISO-like strings
        return _date.fromisoformat(str(d)[:10])
    except Exception:
        return None


def _price_at_or_before(history: list[dict], target: _date) -> float | None:
    """Return the last known price at or before target date."""
    for row in reversed(history):
        rd = _parse_date(row.get("date"))
        if rd and rd <= target:
            try:
                return float(row.get("price"))
            except Exception:
                return None
    return None


def get_db_price(query: str, change_days: int = 1, language: str = "zh") -> str:
    """
    Retrieves real-time price from the MySQL-backed market database.
    Query should be a product name (e.g., "Isoprene", "Crude Oil").
    """
    # 1. Resolve Product ID
    query_lower = query.lower()
    resolved_id = resolve_product_id(query)

    # Ethylene is not a directly tracked pricing series in this EDIA market scope.
    # Return a deterministic, domain-aware message instead of a generic query error.
    if resolved_id == "ethylene" or query_lower in {"ethylene", "乙烯", "ethene"}:
        if (language or "zh").lower().startswith("en"):
            return (
                "Ethylene is not a directly monitored/traded price series in this EDIA scope. "
                "Use cracked C5 and downstream products (isoprene/piperylene) as the operational transmission path."
            )
        return (
            "乙烯不在当前EDAI直接监控的价格口径内。"
            "请通过裂解C5及其下游异戊二烯/间戊二烯价格来观察传导影响。"
        )

    target_node = get_product_node_by_id(resolved_id) if resolved_id else None

    if not target_node:
        # Compatibility fallback for free-form input that did not normalize cleanly.
        for node in domain_graph.nodes:
            if query_lower in node.name_en.lower() or query_lower in node.name_cn.lower():
                target_node = node
                break

    if not target_node:
        return f"Error: Product '{query}' not found in database."

    if not market_data.is_real_data(target_node.id):
        return (
            f"No real MySQL price data available for {target_node.name_cn} "
            f"({target_node.name_en})."
        )

    # 2. Get Price
    price = market_data.get_latest_price(target_node.id)

    # 3. Get Trend (Change from N days ago, by last-known price)
    history = market_data.get_price_history(target_node.id)
    change_str = ""

    if history and len(history) > 1:
        latest_date = _parse_date(history[-1].get("date"))
        if latest_date and change_days > 0:
            prev_target = latest_date - _timedelta(days=int(change_days))
            prev_price = _price_at_or_before(history, prev_target)
            if prev_price and prev_price != 0:
                pct = (price - prev_price) / prev_price * 100
                if int(change_days) == 1:
                    change_str = f"({pct:+.2f}%)"
                else:
                    change_str = f"({int(change_days)}d:{pct:+.2f}%)"

    latest_row_date = ""
    if history:
        latest_row_date = str(history[-1].get("date") or "")[:10]

    source_product_id = "unknown"
    source_price_type = "unknown"
    if target_node.id in REAL_DATA_MAP:
        try:
            source_product_id, source_price_type = REAL_DATA_MAP[target_node.id]
        except Exception:
            pass

    lineage = (
        "[src=market_prices"
        f"|product_id={source_product_id}"
        f"|price_type={source_price_type}"
        f"|date={latest_row_date or 'unknown'}]"
    )

    if (language or "zh").lower().startswith("en"):
        return (
            f"{target_node.name_en}: {price} {target_node.unit} {change_str} "
            f"{lineage}"
        )

    return (
        f"{target_node.name_cn} ({target_node.name_en}): {price} "
        f"{target_node.unit} {change_str} {lineage}"
    )


def get_db_price_for_type(
    query: str,
    price_type: str,
    change_days: int = 30,
    language: str = "zh",
) -> str:
    """
    Look up a specific market_prices price_type (e.g. sinopec_sh) for a product.
    Used for 中石化/上海石化挂牌价 questions that differ from the default series.
    """
    resolved_id = resolve_product_id(query)
    target_node = get_product_node_by_id(resolved_id) if resolved_id else None
    if not target_node:
        return f"Error: Product '{query}' not found in database."

    mp_product = resolved_id or target_node.id
    history = market_data._load_history_from_market_prices(
        product_id=mp_product,
        mp_product=mp_product,
        mp_price_type=str(price_type or "").strip() or None,
    )
    if not history:
        if (language or "zh").lower().startswith("en"):
            return (
                f"No {price_type} listing rows in market_prices for "
                f"{target_node.name_en}."
            )
        return (
            f"market_prices 中暂无 {target_node.name_cn} 的 {price_type} 挂牌价记录。"
        )

    price = float(history[-1]["price"])
    change_str = ""
    if len(history) > 1 and change_days > 0:
        latest_date = _parse_date(history[-1].get("date"))
        if latest_date:
            prev_target = latest_date - _timedelta(days=int(change_days))
            prev_price = _price_at_or_before(history, prev_target)
            if prev_price and prev_price != 0:
                pct = (price - prev_price) / prev_price * 100
                if int(change_days) == 1:
                    change_str = f"({pct:+.2f}%)"
                else:
                    change_str = f"({int(change_days)}d:{pct:+.2f}%)"

    latest_row_date = str(history[-1].get("date") or "")[:10]
    lineage = (
        "[src=market_prices"
        f"|product_id={mp_product}"
        f"|price_type={price_type}"
        f"|date={latest_row_date or 'unknown'}]"
    )
    label_suffix = " (SINOPEC Shanghai listing)" if price_type == "sinopec_sh" else ""

    if (language or "zh").lower().startswith("en"):
        return (
            f"{target_node.name_en}{label_suffix}: {price} {target_node.unit} "
            f"{change_str} {lineage}"
        )

    listing_label = "（上海石化挂牌价）" if price_type == "sinopec_sh" else ""
    return (
        f"{target_node.name_cn} ({target_node.name_en}){listing_label}: {price} "
        f"{target_node.unit} {change_str} {lineage}"
    )
