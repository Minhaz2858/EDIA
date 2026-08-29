"""Query planner for intent-driven focused data fetch."""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Optional, TypedDict

from backend.src.planner.llm_router import route_planner_call
from backend.src.planner.schema_registry import (
    INTENTS,
    PRODUCTS,
    build_source_operations,
    get_intent_keys,
    get_product_keys,
    get_source_keys,
    normalize_product_key,
    normalize_source_operation,
    resolve_product,
)


PLANNER_ENABLED = os.getenv("PLANNER_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
USE_LLM_PLANNER = os.getenv("USE_LLM_PLANNER", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# ── In-memory LRU cache for plan_query results ─────────────────────────────────
# Key: (normalized_query_lower, date_iso) → (QueryPlan, timestamp)
# Evicts entries older than _PLANNER_CACHE_TTL_SECONDS per entry.
_PLANNER_CACHE: dict[tuple[str, str], tuple[QueryPlan, float]] = {}
_PLANNER_CACHE_LOCK = __import__("threading").Lock()
_PLANNER_CACHE_TTL_SECONDS = max(30, int(os.getenv("PLANNER_CACHE_TTL_SECONDS", "120")))


def _cache_key(user_query: str) -> tuple[str, str]:
    import hashlib

    normalized = str(user_query or "").strip().lower()
    today = date.today().isoformat()
    # Use first 128 chars of query + date as cache key to avoid unbounded length
    short = normalized[:128]
    return (short, today)


def _cache_get(key: tuple[str, str]) -> Optional[QueryPlan]:
    with _PLANNER_CACHE_LOCK:
        entry = _PLANNER_CACHE.get(key)
        if entry is None:
            return None
        plan, ts = entry
        import time

        if time.monotonic() - ts > _PLANNER_CACHE_TTL_SECONDS:
            _PLANNER_CACHE.pop(key, None)
            return None
        return plan


def _cache_set(key: tuple[str, str], plan: QueryPlan) -> None:
    import time

    with _PLANNER_CACHE_LOCK:
        # Simple size cap to avoid unbounded growth
        if len(_PLANNER_CACHE) >= 500:
            # Evict ~20% oldest entries
            cutoff = time.monotonic() - _PLANNER_CACHE_TTL_SECONDS
            to_remove = [k for k, (_, ts) in list(_PLANNER_CACHE.items()) if ts < cutoff]
            for k in to_remove[:100]:
                _PLANNER_CACHE.pop(k, None)
        _PLANNER_CACHE[key] = (plan, time.monotonic())


class SourceOperation(TypedDict):
    source: str
    intent: str
    product: str
    operation: str
    metric: str
    group_by: str
    reason: str


class QueryPlan(TypedDict):
    products: list[str]
    intents: list[str]
    source_operations: list[SourceOperation]
    time_range: dict
    needs_forecast: bool
    confidence: str
    raw_query: str


def build_planner_prompt(user_query: str, today: date) -> str:
    product_list = ", ".join(get_product_keys())
    intent_list = ", ".join(get_intent_keys())
    source_list = ", ".join(get_source_keys())
    return f"""You are a query planner for a petrochemical pricing system.
Today's date is {today.isoformat()}.

Extract the user intent into JSON with exactly these fields:
- products: list of product keys from [{product_list}]
- intents: list of intent keys from [{intent_list}]
- source_operations: list of objects, each with source from [{source_list}], product, operation, metric, group_by, reason. Use multiple source_operations when the question needs ERP, market, forecast, logistics, production, contract, or inventory evidence together. Operation must be one of raw|latest|average|sum|top_n.
- time_range: {{\"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\"}}
- needs_forecast: true if user asks about future prices or prediction
- confidence: high|medium|low

CRITICAL: When the user query blends internal company data (sales, inventory, contracts, production) with external market data (prices, trends, competitors, forecasts), you MUST emit source_operations for BOTH erp/mysql sources AND market/mysql sources. This is a cross-source query.

Examples of cross-source queries:
- "Why is our isoprene sales margin lower than the market price trend?" → needs actual_price (ERP) + market_reference + market_spot_east_china
- "How does current inventory affect our pricing strategy?" → needs inventory (ERP) + actual_price + forecast_price
- "Compare our contract prices with market spot prices" → needs contract (ERP) + market_reference

Return only JSON object.

User query: {user_query}"""


def parse_planner_output(raw: str, user_query: str) -> Optional[QueryPlan]:
    try:
        cleaned = re.sub(r"```(?:json)?\\s*|\\s*```", "", str(raw or "")).strip()
        data = json.loads(cleaned)
    except Exception:
        return None

    products = []
    for raw_product in list(data.get("products") or []):
        product = normalize_product_key(str(raw_product))
        if product and product not in products:
            products.append(product)
    if not products:
        return None

    intents = [i for i in list(data.get("intents") or []) if i in INTENTS]
    if not intents:
        intents = ["actual_price", "market_reference"]

    source_operations: list[SourceOperation] = []
    for raw_op in list(data.get("source_operations") or []):
        if not isinstance(raw_op, dict):
            continue
        default_product = products[0] if len(products) == 1 else None
        op = normalize_source_operation(raw_op, default_product=default_product)
        if op:
            source_operations.append(SourceOperation(**op))

    if not source_operations:
        source_operations = [SourceOperation(**op) for op in build_source_operations(products, intents)]

    tr = data.get("time_range") or {}
    start = str(tr.get("start") or "").strip()
    end = str(tr.get("end") or "").strip()
    if not start or not end:
        today = date.today()
        start = (today - timedelta(days=7)).isoformat()
        end = today.isoformat()

    return QueryPlan(
        products=products,
        intents=intents,
        source_operations=source_operations,
        time_range={"start": start, "end": end},
        needs_forecast=bool(data.get("needs_forecast", False)),
        confidence=str(data.get("confidence") or "medium"),
        raw_query=user_query,
    )


def _detect_blended_intents(q: str, ql: str) -> list[str]:
    """Detect if query blends internal (ERP) and external (market) signals."""
    blended: list[str] = []

    # Internal signals
    has_erp = any(k in q for k in ["销售", "订单", "合同", "客户", "库存", "库龄", "生产", "产量", "排产"]) or any(
        k in ql for k in ["sales", "order", "contract", "customer", "inventory", "stock", "production", "output"]
    )
    # External signals
    has_market = any(k in q for k in ["市场", "行情", "报价", "价格", "走势", "竞品", "竞争"]) or any(
        k in ql for k in ["market", "price", "trend", "competitor", "competition", "quote"]
    )
    # Margin / profitability blending
    has_margin = any(k in q for k in ["利润", "毛利", "margin", "盈利", "收益"]) or any(
        k in ql for k in ["margin", "profit", "profitability", "spread"]
    )

    if has_margin and (has_erp or has_market):
        # Margin questions almost always need both sides
        blended.append("cross_source_margin_analysis")

    if has_erp and has_market:
        blended.append("cross_source_erp_market")

    return blended


def keyword_fallback(user_query: str) -> Optional[QueryPlan]:
    from backend.src.domain.edia_ontology import (
        default_products_for_metric,
        resolve_metric,
        resolve_products,
    )
    from backend.src.domain.product_resolver import (
        default_sinopec_listing_product_ids,
        looks_like_sinopec_listing_query,
    )

    q = str(user_query or "")
    metric_id = resolve_metric(q)
    product_keys = resolve_products(q)

    if metric_id == "sinopec_monthly_listing" or looks_like_sinopec_listing_query(q):
        product_keys = product_keys or default_sinopec_listing_product_ids()
        metric_id = "sinopec_monthly_listing"
    elif metric_id and not product_keys:
        product_keys = default_products_for_metric(metric_id)

    if len(product_keys) > 1:
        today = date.today()
        planner_products: list[str] = []
        for pid in product_keys:
            pk = normalize_product_key(pid)
            if not pk:
                from backend.src.planner.schema_registry import DOMAIN_PRODUCT_TO_PLANNER_KEY

                pk = DOMAIN_PRODUCT_TO_PLANNER_KEY.get(pid, pid)
            if pk and pk not in planner_products:
                planner_products.append(pk)
        intents = ["market_multi_product_wow", "market_reference"]
        if any(k in q for k in ["挂牌", "中石化", "sinopec"]) or "listing" in q.lower():
            intents = ["market_listing_sinopec"]
        source_operations = [
            SourceOperation(**op)
            for op in build_source_operations(planner_products, intents)
        ]
        return QueryPlan(
            products=planner_products,
            intents=intents,
            source_operations=source_operations,
            time_range={
                "start": (today - timedelta(days=7)).isoformat(),
                "end": today.isoformat(),
            },
            needs_forecast=False,
            confidence="fallback",
            raw_query=user_query,
        )

    product_key = product_keys[0] if product_keys else resolve_product(user_query)
    if not product_key:
        return None

    today = date.today()
    needs_forecast = any(k in q.lower() for k in ["forecast", "predict", "future", "next week"]) or any(
        k in q for k in ["预测", "未来", "下周"]
    )
    intents = ["actual_price", "market_reference"]

    ql = q.lower()
    if any(k in q for k in ["库存", "库龄", "安全库存"]) or "inventory" in ql:
        intents.append("inventory")

    if any(k in q for k in ["销售", "订单", "合同", "客户"]) or any(
        k in ql for k in ["sales", "order", "contract", "customer"]
    ):
        intents.append("sales")

    logistics_inbound_markers_zh = ["入库", "收货", "到货", "来货", "在途", "采购"]
    logistics_inbound_markers_en = ["inbound", "receipt", "receiving", "arrival", "eta", "purchase order"]
    if any(k in q for k in logistics_inbound_markers_zh) or any(k in ql for k in logistics_inbound_markers_en):
        intents.extend(["inbound_receipt", "purchase_order"])

    logistics_outbound_markers_zh = ["出库", "出货", "发货", "交付"]
    logistics_outbound_markers_en = ["outbound", "shipment", "delivery", "ship out"]
    if any(k in q for k in logistics_outbound_markers_zh) or any(k in ql for k in logistics_outbound_markers_en):
        intents.append("outbound_shipment")

    logistics_production_markers_zh = ["生产入库", "生产出库", "排产", "产量"]
    logistics_production_markers_en = ["production in", "production out", "production plan", "output"]
    if any(k in q for k in logistics_production_markers_zh) or any(k in ql for k in logistics_production_markers_en):
        intents.extend(["production_in", "production_out"])

    if any(k in q for k in ["调拨", "移库", "转仓"]) or any(k in ql for k in ["transfer", "stock move"]):
        intents.append("internal_transfer")

    if any(k in q for k in ["生产", "排产", "产能", "产量"]) or any(
        k in ql for k in ["production", "capacity", "output"]
    ):
        intents.append("production")

    if any(k in q for k in ["合同", "执行", "履约"]) or any(
        k in ql for k in ["contract", "execution", "fulfillment"]
    ):
        intents.append("contract")

    if any(k in q for k in ["挂牌价", "挂牌", "中石化", "上海石化"]) or any(
        k in ql for k in ["listing price", "sinopec", "listed price"]
    ):
        intents.append("market_listing_sinopec")
    elif any(k in q for k in ["市场", "行情", "报价", "主流价格"]) or any(
        k in ql for k in ["market", "quote", "reference", "mainstream price"]
    ):
        intents.append("market_spot_east_china")
        intents.append("market_reference")

    # Cross-source blended intent detection
    blended = _detect_blended_intents(q, ql)
    if "cross_source_margin_analysis" in blended:
        # Margin analysis needs both ERP actuals and market reference
        if "sales" not in intents:
            intents.append("sales")
        if "actual_price" not in intents:
            intents.append("actual_price")
        if "market_reference" not in intents:
            intents.append("market_reference")
        if "market_spot_east_china" not in intents:
            intents.append("market_spot_east_china")

    # Preserve ordering while removing duplicates.
    intents = list(dict.fromkeys(intents))

    if needs_forecast:
        intents.append("forecast_price")

    intents = list(dict.fromkeys(intents))

    end = (today + timedelta(days=7)).isoformat() if needs_forecast else today.isoformat()
    source_operations = [SourceOperation(**op) for op in build_source_operations([product_key], intents)]
    return QueryPlan(
        products=[product_key],
        intents=intents,
        source_operations=source_operations,
        time_range={
            "start": (today - timedelta(days=7)).isoformat(),
            "end": end,
        },
        needs_forecast=needs_forecast,
        confidence="fallback",
        raw_query=user_query,
    )


def plan_query(user_query: str) -> Optional[QueryPlan]:
    if not PLANNER_ENABLED:
        return None

    cache_key = _cache_key(user_query)

    # Fast path: return cached result if available and fresh
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result: Optional[QueryPlan] = None

    if USE_LLM_PLANNER:
        try:
            raw = route_planner_call(build_planner_prompt(user_query, date.today()))
            parsed = parse_planner_output(raw, user_query)
            if parsed:
                result = parsed
        except Exception:
            pass

    # Fallback to keyword-only path if LLM planner failed or is disabled
    if result is None:
        result = keyword_fallback(user_query)

    # Cache the result (even fallback results are worth caching)
    if result is not None:
        _cache_set(cache_key, result)

    return result


def build_planner_fast_preview(plan: QueryPlan, user_query: str) -> str:
    """
    Build a fast-start preview markdown directly from a QueryPlan — no LLM needed.
    This is used to emit an immediate preview SSE event before the full graph runs.

    Args:
        plan: Parsed QueryPlan from plan_query()
        user_query: Original user query (used for language detection)

    Returns:
        Markdown string for the fast-preview SSE event.
    """
    from backend.src.planner.schema_registry import PRODUCTS

    is_chinese = any("\u4e00" <= ch <= "\u9fff" for ch in str(user_query or ""))

    products = plan.get("products") or []
    intents = plan.get("intents") or []
    time_range = plan.get("time_range") or {}
    needs_forecast = plan.get("needs_forecast", False)
    source_ops = plan.get("source_operations") or []

    # Build human-readable product labels
    product_labels: list[str] = []
    for pk in products:
        spec = PRODUCTS.get(pk, {})
        label = spec.get("chinese_name") or spec.get("english_name") or pk
        product_labels.append(label)

    # Build intent summary
    intent_labels: list[str] = []
    for intent in intents:
        label_map = {
            "actual_price": "实际价格",
            "forecast_price": "预测价格",
            "market_reference": "市场参考价",
            "inventory": "库存",
            "sales": "销售订单",
            "outbound_shipment": "出库发货",
            "inbound_receipt": "入库收货",
            "purchase_order": "采购订单",
            "production": "生产",
            "production_in": "生产入库",
            "production_out": "生产出库",
            "contract": "合同",
            "market_listing_sinopec": "中石化挂牌价",
            "market_spot_east_china": "华东市场价",
            "market_multi_product_wow": "多产品周环比",
        }
        intent_labels.append(label_map.get(intent, intent))

    # Build data source summary
    source_labels: list[str] = []
    for op in source_ops[:5]:
        src = op.get("source", "")
        prod = op.get("product", "")
        source_labels.append(f"{prod}/{src}")

    start = time_range.get("start", "")
    end = time_range.get("end", "")
    time_str = f"{start} ~ {end}" if start and end else ""

    if is_chinese:
        lines: list[str] = ["## 快速结论", ""]
        if product_labels:
            lines.append(f"- **产品**：{' / '.join(product_labels)}")
        if intent_labels:
            lines.append(f"- **意图**：{' / '.join(intent_labels)}")
        if time_str:
            lines.append(f"- **时间范围**：{time_str}")
        if needs_forecast:
            lines.append("- **包含预测**：是")
        if source_labels:
            unique_sources = list(dict.fromkeys(source_labels))
            lines.append(f"- **数据来源**（前{len(unique_sources)}项）：{', '.join(unique_sources[:4])}")
        lines.append("")
        lines.append("*正在并行获取数据并计算分析，请稍候...*")
        return "\n".join(lines) + "\n"
    else:
        lines: list[str] = ["## Quick Take", ""]
        if product_labels:
            lines.append(f"- **Products**: {' / '.join(product_labels)}")
        if intent_labels:
            lines.append(f"- **Intents**: {' / '.join(intent_labels)}")
        if time_str:
            lines.append(f"- **Time range**: {time_str}")
        if needs_forecast:
            lines.append("- **Includes forecast**: Yes")
        if source_labels:
            unique_sources = list(dict.fromkeys(source_labels))
            lines.append(f"- **Data sources** (first {len(unique_sources)}): {', '.join(unique_sources[:4])}")
        lines.append("")
        lines.append("*Fetching data and computing analysis in parallel...*")
        return "\n".join(lines) + "\n"
