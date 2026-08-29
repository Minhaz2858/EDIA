from dataclasses import dataclass, field

from backend.src.nlu.llm_service import LLMService
from backend.src.nlu.types import UserContext
from backend.src.domain.product_resolver import (
    extract_product_ids_in_text,
    product_id_to_context_label,
    resolve_product_id,
)
import os
import re


@dataclass
class SessionSlots:
    last_metric_id: str | None = None
    last_product_ids: list[str] = field(default_factory=list)
    last_capability: str | None = None
    awaiting_product: bool = False

# Industry keywords
_INDUSTRY_KEYWORDS = ["石化", "化工", "c5", "c9", "petrochemical", "chemical"]


def _looks_like_broad_market_analysis_query(text: str) -> bool:
    q = str(text or "").strip()
    lower = q.lower()
    if not q:
        return False

    analysis_markers_zh = [
        "市场",
        "行情",
        "价格",
        "成本",
        "定价",
        "策略",
        "供需",
        "供给",
        "需求",
        "库存",
        "风险",
        "影响",
        "驱动",
        "原因",
        "本周",
        "下周",
    ]
    analysis_markers_en = [
        "market",
        "price",
        "pricing",
        "cost",
        "strategy",
        "supply",
        "demand",
        "inventory",
        "risk",
        "impact",
        "driver",
        "reason",
        "this week",
        "next week",
    ]
    return any(k in q for k in analysis_markers_zh) or any(k in lower for k in analysis_markers_en)


def _looks_like_product_only_reply(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if _looks_like_assistant_clarification_prompt(t):
        return False
    if extract_product_ids_in_text(t):
        return True
    if resolve_product_id(t):
        return True
    lower = t.lower()
    product_only_en = ["isoprene", "piperylene", "dcpd", "styrene", "cracked c5", "c5"]
    product_only_zh = ["异戊二烯", "间戊二烯", "双环", "苯乙烯", "裂解c5", "碳五"]
    return any(m in lower for m in product_only_en) or any(m in t for m in product_only_zh)


def _extract_session_slots(conversation_history: list[dict]) -> SessionSlots:
    slots = SessionSlots()
    for message in reversed(conversation_history or []):
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            if _looks_like_assistant_clarification_prompt(content):
                slots.awaiting_product = True
            from backend.src.domain.edia_ontology import resolve_metric

            mid = resolve_metric(content)
            if mid:
                slots.last_metric_id = mid
            continue
        if role == "user":
            pids = extract_product_ids_in_text(content)
            if pids:
                slots.last_product_ids = pids
            from backend.src.domain.edia_ontology import resolve_metric

            mid = resolve_metric(content)
            if mid:
                slots.last_metric_id = mid
            break
    return slots


def _looks_like_assistant_clarification_prompt(text: str) -> bool:
    """User pasted EDIA's own follow-up question instead of answering it."""
    t = (text or "").strip()
    if not t:
        return False
    lower = t.lower()
    markers = [
        "which specific sinopec product",
        "are you interested in for the monthly listing price",
        "is there an official sinopec announcement or channel source",
        "您关注的是哪一个具体的中石化产品",
        "是否有中石化官方公告或渠道来源",
    ]
    return any(marker in lower for marker in markers) or (
        "which specific sinopec product" in lower and "listing price" in lower
    )


def _looks_like_follow_up(text: str) -> bool:
    lower = text.lower()
    follow_up_markers = [
        "why",
        "describe why",
        "explain",
        "this forecast",
        "this price forecast",
        "why you make",
        "how did you get",
        "based on this",
        "这个预测",
        "为什么",
        "解释一下",
        "说明一下",
        "这个判断",
        "这个价格预测",
        "where does this price come from",
        "where is this price from",
        "source of this price",
        "price source",
        "数据来源",
        "价格来源",
        "来源是什么",
        "怎么来的",
        "i want full market analysis",
        "full market analysis",
        "drivers, outlook, and recommendations",
        "drivers outlook recommendations",
        "give me full analysis",
        "continue with full analysis",
        "go deeper",
        "完整分析",
        "完整市场分析",
        "全量分析",
        "继续分析",
        "给我完整分析",
        "有没有明细",
        "明细呢",
        "详细数据",
        "有明细吗",
        "breakdown",
        "more detail",
        "show details",
    ]
    return any(marker in lower for marker in follow_up_markers) or any(
        marker in text for marker in ("有没有明细", "明细呢", "详细数据", "有明细吗")
    )


def _product_mentions(text: str) -> list[tuple[int, int, str]]:
    product_ids = extract_product_ids_in_text(text)
    return [(idx, -1, product_id_to_context_label(pid)) for idx, pid in enumerate(product_ids)]


def _keyword_extract_products(text: str) -> list[str]:
    """Extract all product mentions in query order, de-duplicated."""
    mentions = _product_mentions(text)
    if not mentions:
        return []
    products: list[str] = []
    seen: set[str] = set()
    for _pos, _neg_len, product in mentions:
        if product in seen:
            continue
        products.append(product)
        seen.add(product)
    return products


def _extract_recent_product_from_history(
    conversation_history: list[dict],
) -> str | None:
    # Prefer the latest explicit USER product mention; assistant responses often
    # include many products in summaries/charts that are not the requested focus.
    for message in reversed(conversation_history):
        role = str(message.get("role") or "").strip().lower()
        if role != "user":
            continue
        content = (message.get("content") or "").strip()
        if not content:
            continue
        products = _keyword_extract_products(content)
        if products:
            return products[0]

    # Fallback for sessions without user-role markers.
    for message in reversed(conversation_history):
        content = (message.get("content") or "").strip()
        if not content:
            continue
        products = _keyword_extract_products(content)
        if products:
            return products[0]
    return None


def _keyword_extract_industry(text: str) -> str | None:
    lower = text.lower()
    for kw in _INDUSTRY_KEYWORDS:
        if kw.lower() in lower:
            return "Petrochemical"
    return None


def _keyword_extract_period(text: str) -> str | None:
    """Very light heuristics for period extraction."""
    lower = text.lower()
    explicit_numeric_range = re.search(
        r"\b\d{1,4}\s*(?:[-/年月]|\s+month\s*)\s*\d{1,2}\s*(?:日)?\s*(?:to|through|until|till|至|到|--|~|—)\s*\d{1,4}\s*(?:[-/年月]|\s+month\s*)\s*\d{1,2}\b",
        text,
        re.IGNORECASE,
    )
    explicit_month_name_range = re.search(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\s*(?:to|through|until|till|至|到|--|~|—)\s*(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+)?\d{1,2}(?:st|nd|rd|th)?\b",
        text,
        re.IGNORECASE,
    )
    if explicit_numeric_range or explicit_month_name_range:
        return text
    if "本周" in text or "this week" in lower:
        return "Current Week"
    if "本月" in text or "this month" in lower:
        return "Current Month"
    if "下周" in text or "next week" in lower:
        return "Next Week"
    if "下月" in text or "next month" in lower:
        return "Next Month"
    if "今天" in text or "today" in lower:
        return "Today"
    if "昨天" in text or "yesterday" in lower:
        return "Yesterday"
    if "上个月" in text or "上月" in text or "last month" in lower:
        return "Last Month"
    if "上周" in text or "last week" in lower:
        return "Last Week"
    if "本季度" in text or "this quarter" in lower:
        return "This Quarter"
    if "上季度" in text or "last quarter" in lower:
        return "Last Quarter"
    if "今年" in text or "本年" in text or "this year" in lower:
        return "This Year"
    return None


class ContextGenerator:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    def extract_context(
        self,
        user_query: str,
        conversation_history: list[dict] | None = None,
        user_prefs: dict | None = None,
    ) -> UserContext:
        """
        Extracts structured business entities from the natural language query.
        First tries LLM-based extraction; falls back to keyword matching so that
        a simple product mention in Chinese never causes a "partial" response.
        """
        system_prompt = """
ENTITY_EXTRACTION
You are a context extraction engine.
Extract the following entities from the user's business query into a JSON object.

IMPORTANT:
- Be flexible with "period". If user says "Q3", assume current year if not specified.
- Be flexible with "product". If user says "ethylene" (English) or "乙烯" (Chinese), map it to "Ethylene".
- If a field is not explicitly stated but strongly implied, infer it.
- If a field is completely missing, return null for that field.

Fields to extract:
- product: Specific product name (e.g., C5, Ethylene, Polypropylene)
- industry: The industry sector (e.g., Petrochemical, Energy) - infer if not explicit
- period: The time period for analysis (e.g., Q3 2024, Next Week, Next Month)
- goal: The business objective (e.g., Maximize Profit, Increase Market Share, Inventory Optimization)
- region: Geographic region (e.g., China, North America)
- user_role: The likely role of the user (e.g., Sales Manager, Analyst, Executive) - infer from tone

Return JSON only.
"""
        prefs = user_prefs or {}

        pinned_products = prefs.get("preferred_products")
        if isinstance(pinned_products, str):
            pinned_list = [p.strip() for p in pinned_products.split(",") if p.strip()]
        elif isinstance(pinned_products, list):
            pinned_list = [str(p).strip() for p in pinned_products if str(p).strip()]
        else:
            pinned_list = []

        conversation_history = conversation_history or []
        history_product = _extract_recent_product_from_history(conversation_history)
        query_products = _keyword_extract_products(user_query)
        session_slots = _extract_session_slots(conversation_history)

        try:
            from backend.src.routing.query_router import route_query

            routed = route_query(user_query, conversation_history=conversation_history)
            if routed.product_ids and not query_products:
                query_products = [
                    product_id_to_context_label(pid) for pid in routed.product_ids
                ]
        except Exception:
            routed = None

        # Fast-path: keyword heuristics are already robust for products/period/industry.
        # Only call LLM when the query is ambiguous or explicitly asks for regions/goals.
        fast_mode = (os.getenv("FAST_NLU", "auto") or "auto").strip().lower()
        if fast_mode in {"1", "true", "on", "yes"}:
            fast_mode = "on"

        lower = (user_query or "").lower()
        asks_region_or_goal = any(
            k in user_query
            for k in [
                "华东",
                "华南",
                "华北",
                "东北",
                "西北",
                "地区",
                "区域",
                "目标",
                "利润",
                "市场份额",
            ]
        ) or any(
            k in lower
            for k in [
                "region",
                "north america",
                "europe",
                "global",
                "goal",
                "maximize",
                "profit",
                "market share",
            ]
        )

        can_skip_llm = (
            bool(query_products or pinned_list or history_product)
            and not asks_region_or_goal
        )
        if not can_skip_llm and not asks_region_or_goal and _looks_like_broad_market_analysis_query(user_query):
            can_skip_llm = True

        llm_result: dict = {}
        llm_product = llm_industry = llm_period = None
        if (
            fast_mode == "off"
            or (fast_mode == "auto" and not can_skip_llm)
            or fast_mode == "on"
        ):
            # In FAST_NLU=on, still call LLM only if region/goal markers exist.
            if fast_mode != "on" or asks_region_or_goal:
                try:
                    llm_result = self.llm.generate_json(system_prompt, user_query)
                    llm_product = llm_result.get("product")
                    llm_industry = llm_result.get("industry")
                    llm_period = llm_result.get("period")
                except Exception as e:
                    print(
                        f"LLM context extraction failed ({e}), falling back to keyword matching."
                    )
                    llm_product = llm_industry = llm_period = None

        # Deterministic rule:
        # - If the user explicitly mentions products in the current query, trust those (LLM extraction can drift).
        # - If it's a follow-up with no explicit product mention, inherit the most recent product from history.
        if query_products:
            product = ", ".join(query_products)
        elif _looks_like_product_only_reply(user_query) and session_slots.last_metric_id == "sinopec_monthly_listing":
            pid = resolve_product_id(user_query)
            if pid:
                product = product_id_to_context_label(pid)
            elif session_slots.last_product_ids:
                product = ", ".join(
                    product_id_to_context_label(p) for p in session_slots.last_product_ids
                )
        elif _looks_like_product_only_reply(user_query) and history_product:
            product = history_product
        elif _looks_like_assistant_clarification_prompt(user_query) and history_product:
            product = history_product
        elif _looks_like_assistant_clarification_prompt(user_query):
            from backend.src.domain.product_resolver import default_sinopec_listing_product_ids

            product = ", ".join(
                product_id_to_context_label(p) for p in default_sinopec_listing_product_ids()
            )
        elif session_slots.awaiting_product and session_slots.last_metric_id == "sinopec_monthly_listing":
            from backend.src.domain.product_resolver import default_sinopec_listing_product_ids

            product = ", ".join(
                product_id_to_context_label(p) for p in default_sinopec_listing_product_ids()
            )
        elif pinned_list and not _looks_like_follow_up(user_query):
            product = ", ".join(pinned_list)
        elif _looks_like_follow_up(user_query) and history_product:
            product = llm_product or history_product
        else:
            product = llm_product or history_product

        # Always merge keyword fallback for any field the LLM left blank.
        # This ensures Chinese product names never cause a "partial" wall.
        industry = (
            llm_industry or _keyword_extract_industry(user_query) or "Petrochemical"
        )
        period = llm_period or _keyword_extract_period(user_query) or "Current Week"

        # Product scope for this deployment is China market only.
        # Keep region deterministic so downstream prompt/routing does not drift.
        return UserContext(
            product=product,
            industry=industry,
            period=period,
            goal=llm_result.get("goal"),
            region="China",
            user_role=llm_result.get("user_role"),
        )
