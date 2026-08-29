"""Machine-readable database capabilities for database-first chatbot routing."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.domain.product_resolver import extract_product_ids_in_text
from backend.src.prompts.task_prompt import TaskType


@dataclass(frozen=True)
class DatabaseCapability:
    capability_id: str
    domain: str
    table: str
    date_column: str | None
    fields: Dict[str, str]
    markers_zh: Tuple[str, ...]
    markers_en: Tuple[str, ...]
    privacy_level: str
    answer_mode: str
    external_llm_allowed: bool

    def matches(self, query: str) -> bool:
        q = str(query or "")
        ql = q.lower()
        return any(marker in q for marker in self.markers_zh) or any(
            marker in ql for marker in self.markers_en
        )


DATABASE_CAPABILITIES: Tuple[DatabaseCapability, ...] = (
    DatabaseCapability(
        capability_id="erp_order_details",
        domain="erp_sales",
        table="erp_v_sale_orderentry",
        date_column="PLANDATE",
        fields={
            "order_no": "FBILLNO",
            "customer": "CUST_NAME",
            "product": "material_name",
            "quantity": "FDELIQTY",
            "unit_price": "FTAXPRICE",
            "revenue": "FAMOUNT",
        },
        markers_zh=("订单明细", "订单详情", "销售订单明细", "销售订单详情"),
        markers_en=("order detail", "order details", "sales order detail", "sales order details"),
        privacy_level="erp_confidential",
        answer_mode="deterministic",
        external_llm_allowed=False,
    ),
    DatabaseCapability(
        capability_id="erp_sales_summary",
        domain="erp_sales",
        table="erp_v_sale_orderentry",
        date_column="PLANDATE",
        fields={
            "product": "material_name",
            "quantity": "FDELIQTY",
            "order_count": "COUNT(*)",
            "unit_price": "FTAXPRICE",
            "revenue": "FAMOUNT",
        },
        markers_zh=(
            "总销售额",
            "总销售收入",
            "销售收入",
            "销售金额",
            "销售情况",
            "销售数据",
            "销售明细",
            "销售额",
            "销量",
            "销售结构",
            "销售占比",
            "erp报表",
            "erp报告",
            "销售报表",
            "销售报告",
            "明细",
            "详情",
            "有没有明细",
            "明细呢",
        ),
        markers_en=(
            "total sales revenue",
            "sales revenue total",
            "sales revenue",
            "total revenue",
            "sales amount",
            "sales",
            "sales data",
            "sales detail",
            "revenue",
            "volume",
            "erp report",
            "erp summary",
            "monthly erp",
            "business report",
        ),
        privacy_level="erp_confidential",
        answer_mode="deterministic_first",
        external_llm_allowed=False,
    ),
    DatabaseCapability(
        capability_id="erp_customer_sales",
        domain="erp_sales",
        table="erp_v_sale_orderentry",
        date_column="PLANDATE",
        fields={
            "customer": "CUST_NAME",
            "product": "material_name",
            "quantity": "FDELIQTY",
            "order_count": "COUNT(*)",
        },
        markers_zh=("客户销售", "客户明细", "客户销量", "客户销售报告", "增长点", "流失风险", "复购", "客户排名", "客户成交量", "前五名客户", "前几名客户", "客户成交量排名"),
        markers_en=("customer sales", "customer detail", "customer volume", "customer report", "growth points", "churn risk", "lost customer", "repeat purchase"),
        privacy_level="erp_confidential",
        answer_mode="deterministic_first",
        external_llm_allowed=False,
    ),
)


def iter_database_capabilities() -> Iterable[DatabaseCapability]:
    return DATABASE_CAPABILITIES


def get_database_capability(capability_id: str) -> DatabaseCapability | None:
    wanted = str(capability_id or "").strip().lower()
    return next((cap for cap in DATABASE_CAPABILITIES if cap.capability_id == wanted), None)


def resolve_database_capability(query: str) -> DatabaseCapability | None:
    matches = [cap for cap in DATABASE_CAPABILITIES if cap.matches(query)]
    if not matches:
        return None
    customer_match = next(
        (cap for cap in matches if cap.capability_id == "erp_customer_sales"),
        None,
    )
    if customer_match is not None:
        return customer_match
    return max(matches, key=lambda cap: max(len(m) for m in cap.markers_zh + cap.markers_en))


def capability_privacy_metadata(capability: DatabaseCapability | None) -> Dict[str, str]:
    if capability is None:
        return {}
    return {
        "database_capability": capability.capability_id,
        "data_sensitivity": capability.privacy_level,
        "answer_mode": capability.answer_mode,
        "external_llm_blocked": "false" if capability.external_llm_allowed else "true",
    }


_DISCOVERY_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_DISCOVERY_CACHE_TTL_SECONDS = 1800
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_TABLE_PREFIXES = ("erp_v_", "sale_erp_v_", "v_", "lz_v_", "market_", "forecast_")
_DATE_COLUMN_CANDIDATES = (
    "PLANDATE",
    "FDATE",
    "FDELIVERYDATE",
    "date",
    "trade_date",
    "created_at",
    "updated_at",
)
# Avoid bare "数据" — it matches inside "数据库" and market phrases like "中国市场数据库".
_DATABASE_INTENT_MARKERS_ZH = (
    "明细",
    "记录",
    "数据表",
    "数据库",
    "库存",
    "出库",
    "发货",
    "入库",
    "合同",
    "订单",
    "客户",
)
_MARKET_DATA_SOURCE_LABELS_ZH = (
    "中国市场数据库",
    "市场数据库",
    "内部数据库实时",
    "内部市场价",
    "market_prices",
)
_MARKET_DATA_SOURCE_LABELS_EN = (
    "china market database",
    "internal market database",
    "internal database live",
    "market_prices",
    "live market data",
)
_MARKET_PRICE_MARKERS_ZH = (
    "主流价格",
    "现价",
    "报价",
    "最新价",
    "挂牌价",
    "价格变化",
    "涨跌",
    "环比",
    "同比",
    "涨幅",
    "跌幅",
    "走势",
    "行情",
)
_MARKET_PRICE_MARKERS_EN = (
    "listing price",
    "mainstream price",
    "current price",
    "latest price",
    "price change",
    "week over week",
    "wow",
    "mom",
    "yoy",
    "how has it changed",
    "how has it moved",
)
_PIPELINE_TASK_TYPES = frozenset(
    {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.SIMPLE_LOOKUP,
        TaskType.FORECAST_ONLY,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.BOSS_BRIEFING,
        TaskType.CONVERSATIONAL,
    }
)
_DATABASE_INTENT_MARKERS_EN = (
    "data",
    "detail",
    "details",
    "record",
    "records",
    "table",
    "database",
    "inventory",
    "stock",
    "shipment",
    "delivery",
    "outbound",
    "inbound",
    "receipt",
    "contract",
    "order",
    "customer",
    "sales",
    "forecast",
)


def _safe_identifier(value: str) -> str | None:
    name = str(value or "").strip()
    return name if _SAFE_IDENTIFIER.match(name) else None


def _database_query_terms(query: str) -> List[str]:
    q = str(query or "")
    ql = q.lower()
    groups: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (("库存",), ("inventory", "stock", "stk", "onhand")),
        (("出库", "发货", "发运"), ("outbound", "shipment", "shipping", "delivery", "deliver")),
        (("入库", "到货", "收货"), ("inbound", "receipt", "receiving", "arrival")),
        (("合同",), ("contract", "execution", "fulfill")),
        (("订单",), ("order", "bill", "fbillno")),
        (("客户",), ("customer", "cust")),
        (("销售", "销量", "销售额"), ("sales", "sale", "amount", "revenue", "qty")),
        (("价格", "报价"), ("price", "quote", "taxprice")),
        (("预测", "预报"), ("forecast", "prediction")),
        (("产品", "物料"), ("product", "material")),
    ]
    terms: list[str] = []
    for zh_markers, en_terms in groups:
        if any(marker in q for marker in zh_markers) or any(term in ql for term in en_terms):
            terms.extend(en_terms)
    if any(marker in q for marker in _DATABASE_INTENT_MARKERS_ZH) or any(
        marker in ql for marker in _DATABASE_INTENT_MARKERS_EN
    ):
        terms.extend(("date", "name"))
    deduped: list[str] = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped


def _normalize_task_type(task_type: Any) -> TaskType | None:
    if task_type is None:
        return None
    if isinstance(task_type, TaskType):
        return task_type
    value = str(getattr(task_type, "value", task_type) or "").strip().lower()
    if not value:
        return None
    for candidate in TaskType:
        if candidate.value == value:
            return candidate
    return None


def is_market_data_source_label(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    return any(label in q for label in _MARKET_DATA_SOURCE_LABELS_ZH) or any(
        label in ql for label in _MARKET_DATA_SOURCE_LABELS_EN
    )


def looks_like_market_price_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    if looks_like_database_catalog_question(q):
        return False
    record_inventory_markers_zh = ("哪些记录", "有什么记录", "哪些数据记录", "数据有哪些记录")
    record_inventory_markers_en = ("what records", "which records", "what data records")
    if any(marker in q for marker in record_inventory_markers_zh) or any(
        marker in ql for marker in record_inventory_markers_en
    ):
        return False
    if is_market_data_source_label(q):
        return True
    has_price_signal = any(marker in q for marker in _MARKET_PRICE_MARKERS_ZH) or any(
        marker in ql for marker in _MARKET_PRICE_MARKERS_EN
    )
    if not has_price_signal:
        return False
    if extract_product_ids_in_text(q):
        return True
    market_context_zh = ("市场", "行情", "裂解", "中石化", "隆众", "装置", "挂牌")
    market_context_en = ("market", "sinopec", "listing", "cracker", "plant")
    return any(marker in q for marker in market_context_zh) or any(
        marker in ql for marker in market_context_en
    )


def looks_like_database_catalog_question(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    catalog_markers_zh = (
        "哪些数据",
        "什么数据",
        "哪些表",
        "哪个表",
        "在哪张表",
        "哪些表里",
        "能直接回答",
        "可以回答",
        "数据覆盖",
        "能力注册",
        "数据源",
    )
    catalog_markers_en = (
        "what data",
        "which data",
        "what records",
        "which records",
        "records exist",
        "which tables",
        "what tables",
        "can answer",
        "coverage",
        "capability",
        "data source",
    )
    table_catalog_markers_zh = ("哪些表", "哪个表", "在哪张表", "哪些表里", "什么表")
    table_catalog_markers_en = ("which table", "what table", "which tables", "what tables")
    if any(marker in q for marker in table_catalog_markers_zh) or any(
        marker in ql for marker in table_catalog_markers_en
    ):
        return True
    has_catalog = any(marker in q for marker in catalog_markers_zh) or any(
        marker in ql for marker in catalog_markers_en
    )
    if has_catalog:
        return True
    return False


def looks_like_operational_database_records_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    if looks_like_database_catalog_question(q):
        return True
    record_markers_zh = ("记录", "明细", "表", "字段", "行数")
    record_markers_en = ("record", "records", "row", "rows", "table", "tables", "field", "fields")
    has_records = any(marker in q for marker in record_markers_zh) or any(
        marker in ql for marker in record_markers_en
    )
    operational_zh = ("库存", "订单", "合同", "客户", "出库", "入库", "发货", "销售明细", "收货", "物料")
    operational_en = (
        "inventory",
        "stock",
        "order",
        "contract",
        "customer",
        "shipment",
        "inbound",
        "outbound",
        "receipt",
        "receiving",
        "material",
        "sales detail",
    )
    has_operational = any(marker in q for marker in operational_zh) or any(
        marker in ql for marker in operational_en
    )
    market_context_zh = ("市场", "行情", "价格", "报价")
    market_context_en = ("market", "price", "pricing", "quote")
    has_market_context = any(marker in q for marker in market_context_zh) or any(
        marker in ql for marker in market_context_en
    )
    if has_records and (has_operational or has_market_context):
        return True
    if has_operational and has_records:
        return True
    data_pull_markers_zh = ("数据", "明细", "分布", "给我看", "列出来", "查询")
    data_pull_markers_en = ("data", "breakdown", "distribution", "show me", "list", "query")
    asks_data_pull = any(marker in q for marker in data_pull_markers_zh) or any(
        marker in ql for marker in data_pull_markers_en
    )
    return bool(has_operational and asks_data_pull)


def should_bypass_database_capability_discovery(
    query: str,
    *,
    task_type: Any = None,
    hermes_domains: Iterable[str] | None = None,
    capabilities: Iterable[str] | None = None,
    agent_intent: Any = None,
) -> bool:
    """Return True when the user should get the normal EDIA pipeline, not ERP table discovery."""
    q = str(query or "").strip()
    if not q:
        return True

    if resolve_database_capability(q) is not None:
        return False

    normalized_task = _normalize_task_type(task_type)
    if normalized_task in _PIPELINE_TASK_TYPES:
        if looks_like_database_catalog_question(q) or looks_like_operational_database_records_query(q):
            return False
        return True

    domain_set = {str(domain or "").strip().lower() for domain in (hermes_domains or []) if domain}
    capability_set = {str(cap or "").strip().lower() for cap in (capabilities or []) if cap}

    if "commercial_ops_advisory" in capability_set:
        return True
    if agent_intent is not None:
        intent_name = str(getattr(agent_intent, "intent", "") or "").strip().lower()
        if intent_name == "commercial_ops_advisory":
            return True
        blocked = getattr(agent_intent, "should_not_use", None) or []
        if "database_capability_discovery" in {str(item).strip().lower() for item in blocked}:
            return True

    if looks_like_market_price_query(q):
        return True

    if domain_set & {"document", "presentation", "frontend"}:
        return True
    if "forecast" in domain_set:
        if looks_like_database_catalog_question(q) or looks_like_operational_database_records_query(q):
            return False
        return True
    if "executive" in domain_set and normalized_task == TaskType.BOSS_BRIEFING:
        return True

    return False


def should_use_database_capability_discovery(
    query: str,
    *,
    task_type: Any = None,
    hermes_domains: Iterable[str] | None = None,
    capabilities: Iterable[str] | None = None,
    agent_intent: Any = None,
) -> bool:
    """True only for genuine unmapped ERP/catalog questions — never for market or general analysis."""
    q = str(query or "").strip()
    if not q or resolve_database_capability(q) is not None:
        return False
    if should_bypass_database_capability_discovery(
        q,
        task_type=task_type,
        hermes_domains=hermes_domains,
        capabilities=capabilities,
        agent_intent=agent_intent,
    ):
        return False
    if looks_like_database_catalog_question(q):
        return True
    normalized_task = _normalize_task_type(task_type)
    if normalized_task == TaskType.ERP_DATA_QUERY and looks_like_operational_database_records_query(q):
        return True
    return looks_like_operational_database_records_query(q) and looks_like_database_backed_question(q)


def looks_like_database_backed_question(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    if should_bypass_database_capability_discovery(q):
        return False
    if looks_like_operational_database_records_query(q) or looks_like_database_catalog_question(q):
        return True
    return any(marker in q for marker in _DATABASE_INTENT_MARKERS_ZH) or any(
        marker in ql for marker in _DATABASE_INTENT_MARKERS_EN
    )


def discover_database_candidates(query: str, *, limit: int = 6) -> Dict[str, Any]:
    """Find likely database sources when no registered capability matches.

    This is deliberately an availability/candidate scan, not a business-answer tool.
    It prevents false "no data" answers while keeping unverified calculations out of chat.
    """
    terms = _database_query_terms(query)
    cache_key = "|".join(terms) or str(query or "").strip().lower()[:120]
    cached = _DISCOVERY_CACHE.get(cache_key)
    now = time.time()
    if cached and (now - cached[0]) < _DISCOVERY_CACHE_TTL_SECONDS:
        return cached[1]

    result: Dict[str, Any] = {"query_terms": terms, "candidates": []}
    if not terms:
        _DISCOVERY_CACHE[cache_key] = (now, result)
        return result

    try:
        with db.engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name "
                    "FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() "
                    "ORDER BY table_name"
                )
            ).fetchall()
            scored: list[dict[str, Any]] = []
            for table_row in tables:
                table_name = _safe_identifier(str(table_row[0] or ""))
                if not table_name or not table_name.startswith(_TABLE_PREFIXES):
                    continue
                columns = conn.execute(
                    text(
                        "SELECT column_name "
                        "FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = :table_name "
                        "ORDER BY ordinal_position"
                    ),
                    {"table_name": table_name},
                ).fetchall()
                column_names = [str(col[0] or "") for col in columns if col and col[0]]
                haystack = " ".join([table_name] + column_names).lower()
                matched_terms = [term for term in terms if term.lower() in haystack]
                if not matched_terms:
                    continue
                scored.append(
                    {
                        "table": table_name,
                        "score": len(set(matched_terms)) + sum(1 for term in matched_terms if term in table_name.lower()),
                        "matched_terms": sorted(set(matched_terms)),
                        "matched_columns": [
                            column
                            for column in column_names
                            if any(term.lower() in column.lower() for term in matched_terms)
                        ][:12],
                        "columns": column_names,
                    }
                )

            scored.sort(key=lambda item: (-int(item["score"]), str(item["table"])))
            candidates: list[dict[str, Any]] = []
            for item in scored[: max(1, limit)]:
                table_name = item["table"]
                columns = item.pop("columns", [])
                date_column = next((col for col in _DATE_COLUMN_CANDIDATES if col in columns), None)
                summary: dict[str, Any] = {
                    "table": table_name,
                    "matched_terms": item["matched_terms"],
                    "matched_columns": item["matched_columns"],
                    "row_count": None,
                    "date_column": date_column,
                    "min_date": None,
                    "max_date": None,
                }
                try:
                    if date_column and _safe_identifier(date_column):
                        row = conn.execute(
                            text(
                                f"SELECT COUNT(*) AS row_count, MIN(DATE(`{date_column}`)) AS min_date, "
                                f"MAX(DATE(`{date_column}`)) AS max_date FROM `{table_name}`"
                            )
                        ).fetchone()
                        if row:
                            summary["row_count"] = int(row[0] or 0)
                            summary["min_date"] = str(row[1]) if row[1] else None
                            summary["max_date"] = str(row[2]) if row[2] else None
                    else:
                        row_count = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()
                        summary["row_count"] = int(row_count or 0)
                except Exception as exc:
                    summary["error"] = str(exc)
                candidates.append(summary)
            result["candidates"] = candidates
    except Exception as exc:
        result["error"] = str(exc)

    _DISCOVERY_CACHE[cache_key] = (now, result)
    return result