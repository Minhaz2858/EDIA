from backend.src.domain.product_resolver import (
    extract_product_ids_in_text,
    looks_like_sinopec_listing_query,
)
from backend.src.nlu.llm_service import LLMService
from backend.src.nlu.types import ParsedIntent, TaskType
import json
import re
import os


def _is_conversational_query(q: str, lower: str) -> bool:
    """Detect pure greeting / small-talk queries that need no data retrieval."""
    # Long messages almost certainly contain business content
    if len(q) > 150:
        return False
    # Greetings
    if re.match(r"^(hi|hello|hey|howdy|hiya|greetings|good\s+(?:morning|afternoon|evening|day|night))[!.,?\s]*$", lower):
        return True
    if re.match(r"^(你好|您好|早安|晚安|早上好|下午好|晚上好|嗨+)[!.,！？\s]*$", q):
        return True
    # How are you
    if re.match(r"^how\s+(are\s+you|r\s+u|is\s+it\s+going|do\s+you\s+do)[?!.,\s]*$", lower):
        return True
    if re.match(r"^(what'?s\s+up|wassup|sup)[?!.,\s]*$", lower):
        return True
    if re.match(r"^(你好吗|您好吗|最近怎么样|最近好吗|你怎么样)[?？\s]*$", q):
        return True
    # Identity / capability questions
    if re.match(r"^(who|what)\s+are\s+you[?!.,\s]*$", lower):
        return True
    if re.match(r"^what\s+(?:can|do)\s+you\s+(?:do|know|help(?:\s+(?:with|me))?)[?!.,\s]*$", lower):
        return True
    if re.match(r"^(?:tell\s+me\s+about\s+yourself|introduce\s+yourself|your\s+name)[?.!\s]*$", lower):
        return True
    if re.match(r"^(你是谁|你是什么|你叫什么|你能做什么|你能帮[什我]么)[?？\s]*$", q):
        return True
    # Thanks / acknowledgement
    if re.match(r"^(?:thank\s+(?:you|u)|thanks|thx|ty|cheers|great|awesome|perfect|got\s+it|understood)[!.,?\s]*$", lower):
        return True
    if re.match(r"^(谢谢|感谢|多谢|谢了|好的|明白了|收到|了解)[!.,！。\s]*$", q):
        return True
    # Goodbye
    if re.match(r"^(bye|goodbye|see\s+you|take\s+care|later|cya)[!.,?\s]*$", lower):
        return True
    if re.match(r"^(再见|拜拜|88|886)[!.,！\s]*$", q):
        return True
    return False


def _fast_intent(user_query: str) -> tuple[TaskType | None, float]:
    q = (user_query or "").strip()
    if not q:
        return None, 0.0

    lower = q.lower()

    # Conversational / greeting — no business data needed
    if _is_conversational_query(q, lower):
        return TaskType.CONVERSATIONAL, 0.97

    # External market context override: plant operation, market impact, and
    # pricing response questions should go through full market pipeline even if
    # they mention "库存" as one signal.
    external_market_markers_zh = [
        "市场",
        "行情",
        "价格",
        "定价",
        "装置",
        "停工",
        "停车",
        "重启",
        "开工",
        "检修",
        "中石化",
        "裂解",
        "乙烯",
        "影响",
    ]
    external_market_markers_en = [
        "market",
        "price",
        "pricing",
        "plant",
        "shutdown",
        "outage",
        "restart",
        "maintenance",
        "cracker",
        "sinopec",
        "impact",
    ]
    has_external_market_context = any(
        k in q for k in external_market_markers_zh
    ) or any(k in lower for k in external_market_markers_en)

    company_internal_markers_zh = ["我们的", "我司", "本公司", "我们公司", "内部"]
    company_internal_markers_en = ["our ", "our company", "internal", "company"]
    has_company_internal_context = any(
        k in q for k in company_internal_markers_zh
    ) or any(k in lower for k in company_internal_markers_en)

    customer_markers_zh = ["壳牌", "客户", "一期", "终端", "经销商"]
    customer_markers_en = ["shell", "customer", "client", "account"]
    quote_markers_zh = ["报价", "报多少", "合同价", "长约", "成交价"]
    quote_markers_en = ["quote", "pricing for", "contract price", "offer price"]

    is_customer_quote = (
        any(k in q for k in customer_markers_zh)
        or any(k in lower for k in customer_markers_en)
    ) and (
        any(k in q for k in quote_markers_zh)
        or any(k in lower for k in quote_markers_en)
    )

    if is_customer_quote:
        return TaskType.ERP_DATA_QUERY, 0.93

    revenue_markers_zh = ["销售额", "销售金额", "销售收入", "总销售额", "总销售收入", "营业额"]
    revenue_markers_en = ["sales revenue", "total revenue", "total sales", "sales amount", "sales value", "revenue total"]
    if any(k in q for k in revenue_markers_zh) or any(k in lower for k in revenue_markers_en):
        return TaskType.ERP_DATA_QUERY, 0.94

    erp_context_markers_zh = ("erp", "销售", "订单", "客户", "库存", "出库", "我司", "我们的", "本公司")
    erp_context_markers_en = ("erp", "sales", "order", "customer", "inventory", "shipment", "our ", "our company")
    erp_report_markers_zh = ("报表", "报告", "汇总", "概况", "经营状况", "业务概况")
    erp_report_markers_en = (
        "erp report",
        "erp summary",
        "monthly erp",
        "business report",
        "sales report",
        "erp dashboard",
        "status report",
    )
    erp_period_markers_zh = ("本月", "这个月", "当期", "本月度", "本季度", "这周", "本周")
    erp_period_markers_en = ("this month", "monthly", "current month", "mtd", "this week", "weekly")
    has_erp_context = "erp" in lower or any(
        marker in q for marker in erp_context_markers_zh
    ) or any(marker in lower for marker in erp_context_markers_en)
    has_erp_report = any(marker in q for marker in erp_report_markers_zh) or any(
        marker in lower for marker in erp_report_markers_en
    ) or ("report" in lower and has_erp_context)
    has_erp_period = any(marker in q for marker in erp_period_markers_zh) or any(
        marker in lower for marker in erp_period_markers_en
    )
    if has_erp_context and (has_erp_report or (has_erp_period and "erp" in lower)):
        return TaskType.ERP_DATA_QUERY, 0.93

    erp_detail_markers_zh = ("明细", "详情", "有没有明细", "明细呢", "有明细吗", "详细数据")
    erp_detail_markers_en = ("detail", "details", "breakdown", "more detail")
    if any(k in q for k in erp_detail_markers_zh) or any(k in lower for k in erp_detail_markers_en):
        return TaskType.ERP_DATA_QUERY, 0.93

    product_ids = extract_product_ids_in_text(q)
    situation_markers_zh = ("情况", "状况")
    situation_markers_en = ("situation", "status", "performance")
    if product_ids and (
        any(k in q for k in situation_markers_zh)
        or any(k in lower for k in situation_markers_en)
    ):
        return TaskType.ERP_DATA_QUERY, 0.91

    other_products_markers_zh = ["其他产品", "其它产品", "别的产品", "其余产品", "剩余产品"]
    other_products_markers_en = ["other products", "other product", "the other products", "others", "remaining products"]
    exclusion_markers_zh = ["除了", "除", "不包括", "排除", "剔除"]
    exclusion_markers_en = ["besides", "except", "excluding", "exclude", "other than", "apart from"]
    sales_markers_zh = ["销售", "销量", "成交", "卖了", "卖出"]
    sales_markers_en = ["sales", "sold", "sale volume", "revenue", "sales performance"]
    if (
        (any(k in q for k in other_products_markers_zh) or any(k in lower for k in other_products_markers_en))
        and (any(k in q for k in exclusion_markers_zh) or any(k in lower for k in exclusion_markers_en))
        and (any(k in q for k in sales_markers_zh) or any(k in lower for k in sales_markers_en))
    ):
        return TaskType.ERP_DATA_QUERY, 0.94

    competitor_markers_zh = [
        "金海",
        "晨光",
        "鲁华",
        "天利",
        "德荣",
        "恒河",
        "价差",
        "定位",
        "竞品",
        "竞争对手",
    ]
    competitor_markers_en = ["competitor", "spread", "benchmark", "positioning"]
    if any(k in q for k in competitor_markers_zh) or any(
        k in lower for k in competitor_markers_en
    ):
        return TaskType.MARKET_ANALYSIS_DECISION, 0.9

    inventory_action_markers_zh = [
        "库存",
        "够卖几天",
        "加速出货",
        "去库存",
        "补库",
        "需要",
    ]
    inventory_action_markers_en = [
        "inventory",
        "days of stock",
        "accelerate shipment",
        "destock",
        "restock",
    ]
    asks_action = any(k in q for k in ["需要", "应该", "要不要", "怎么做"]) or any(
        k in lower for k in ["should", "need to", "what should"]
    )
    if (
        any(k in q for k in inventory_action_markers_zh)
        or any(k in lower for k in inventory_action_markers_en)
    ) and asks_action:
        return TaskType.MARKET_ANALYSIS_DECISION, 0.88

    if ("原油" in q and "石脑油" in q and ("原因" in q or "影响" in q)) or (
        "crude" in lower
        and "naphtha" in lower
        and any(k in lower for k in ["why", "reason", "impact"])
    ):
        return TaskType.MARKET_ANALYSIS_DECISION, 0.89

    # Internal/ERP queries
    internal_report_markers_zh = ["销售报表", "销售报告", "区域销售", "团队销售", "目标", "实际", "差距", "达成", "团队"]
    internal_report_markers_en = [
        "sales report",
        "regional sales report",
        "target",
        "actual",
        "gap",
        "by team",
        "team performance",
        "regional performance",
        "vs target",
        "variance report",
    ]
    looks_like_internal_report = (
        ("sales" in lower or "report" in lower)
        and any(k in lower for k in internal_report_markers_en)
    ) or any(k in q for k in internal_report_markers_zh)

    if (
        (not has_external_market_context and looks_like_internal_report)
        or
        (not has_external_market_context or has_company_internal_context)
        and any(
            k in q
            for k in [
                "我们的",
                "我司",
                "客户",
                "合同",
                "订单",
                "出货",
                "发货",
                "入库",
                "收货",
                "到货",
                "在途",
                "采购",
                "库存",
                "销售",
                "回款",
                "生产",
                "排产",
                "交付",
            ]
        )
        or (not has_external_market_context or has_company_internal_context)
        and any(
            k in lower
            for k in [
                "our sales",
                "sales record",
                "order",
                "contract",
                "inventory",
                "shipment",
                "delivery",
                "inbound",
                "receipt",
                "receiving",
                "arrival",
                "purchase order",
                "eta",
                "production plan",
                "customer list",
            ]
        )
    ):
        return TaskType.ERP_DATA_QUERY, 0.92

    # What-if / scenario
    if any(k in q for k in ["假设", "如果", "若", "在…情况下", "情景"]) or any(
        k in lower for k in ["what if", "assuming", "if "]
    ):
        return TaskType.WHAT_IF_SCENARIO, 0.85

    # Boss briefing / status report / market executive report.
    # Require explicit briefing/report wording so broad market-analysis questions
    # like "explain the supply-demand driver" do not get forced into the
    # executive memo template.
    boss_briefing_markers_zh = [
        "简报",
        "经营状况",
        "公司情况",
        "总体情况",
        "市场简报",
        "市场报告",
        "周报",
        "行情报告",
        "管理层简报",
        "高管简报",
        "执行摘要",
    ]
    boss_briefing_markers_en = [
        "briefing",
        "business overview",
        "overall status",
        "status report",
        "executive report",
        "market report",
        "weekly report",
        "weekly summary",
        "weekly briefing",
        "executive summary",
        "market summary",
        "weekly overview",
        "this week report",
        "generate report",
        "market overview report",
        "research note",
        "market research note",
        "deep market research",
        "market note",
        "supply-demand note",
    ]
    if ("erp" not in lower) and (
        any(k in q for k in boss_briefing_markers_zh)
        or any(k in lower for k in boss_briefing_markers_en)
    ):
        return TaskType.BOSS_BRIEFING, 0.92

    # Decision vs analysis vs lookup vs forecast
    wants_decision = any(
        k in q
        for k in [
            "建议",
            "策略",
            "决策",
            "怎么做",
            "应该",
            "如何定价",
            "报价建议",
            "要不要",
            "行动",
        ]
    ) or any(
        k in lower
        for k in [
            "recommend",
            "recommendation",
            "what should",
            "should we",
            "what action",
            "action we should take",
            "what action should we take",
            "what should we do",
            "what to do",
            "pricing strategy",
            "decision",
        ]
    )

    wants_forecast = any(
        k in q for k in ["预测", "预估", "走势", "下周", "下月"]
    ) or any(k in lower for k in ["forecast", "predict", "next week", "next month"])

    # Price-only lookup heuristics: short + price markers.
    price_markers = [
        "主流价格",
        "挂牌价",
        "挂牌",
        "现价",
        "报价",
        "最新价",
        "listing price",
        "listed price",
        "price",
        "latest price",
        "current price",
    ]
    wants_price = any(m in q for m in price_markers) or any(
        m in lower for m in price_markers
    )

    if wants_forecast and not wants_decision and not ("原因" in q or "why" in lower):
        return TaskType.FORECAST_ONLY, 0.78

    if wants_decision:
        return TaskType.MARKET_ANALYSIS_DECISION, 0.8

    # Queries asking only for current prices plus a basic delta can still be handled
    # as direct lookup if they do not ask for causes, implications, or recommendations.
    change_markers_zh = [
        "环比",
        "同比",
        "涨跌",
        "涨幅",
        "跌幅",
        "变化",
        "变动",
        "方向",
        "波动",
        "驱动",
        "原因",
        "分析",
    ]
    change_markers_en = [
        "wow",
        "mom",
        "yoy",
        "change",
        "movement",
        "analysis",
        "driver",
        "reason",
    ]
    wants_change_analysis = any(k in q for k in change_markers_zh) or any(
        k in lower for k in change_markers_en
    )

    pure_lookup_only_zh = [
        "多少",
        "几多",
        "分别是多少",
        "主流价格",
        "现价",
        "最新价",
        "环比",
        "涨跌",
        "涨幅",
        "跌幅",
        "本周",
        "这周",
    ]
    pure_lookup_only_en = [
        "what is",
        "what are",
        "latest price",
        "current price",
        "price",
        "wow",
        "mom",
        "change",
        "this week",
        "weekly",
    ]
    deep_analysis_markers_zh = [
        "原因",
        "驱动",
        "分析",
        "展望",
        "预测",
        "建议",
        "定价",
        "策略",
        "库存",
        "风险",
        "影响",
    ]
    deep_analysis_markers_en = [
        "why",
        "reason",
        "driver",
        "analysis",
        "forecast",
        "outlook",
        "recommend",
        "strategy",
        "inventory",
        "risk",
        "impact",
    ]
    asks_only_for_price_delta = (
        wants_price
        and wants_change_analysis
        and not any(k in q for k in deep_analysis_markers_zh)
        and not any(k in lower for k in deep_analysis_markers_en)
        and (
            any(k in q for k in pure_lookup_only_zh)
            or any(k in lower for k in pure_lookup_only_en)
        )
    )

    detailed_lookup_period_markers_zh = ["本周", "这周", "本月", "本期"]
    detailed_lookup_period_markers_en = ["this week", "weekly", "this month", "monthly"]
    asks_multi_product = any(k in q for k in ["和", "及", "与", "分别"]) or bool(
        re.search(r"\b(and|,|/)\b", lower)
    )
    asks_detailed_lookup = (
        asks_only_for_price_delta
        and asks_multi_product
        and (
            any(k in q for k in detailed_lookup_period_markers_zh)
            or any(k in lower for k in detailed_lookup_period_markers_en)
        )
    )

    # For recurring business asks like "本周A和B主流价格+环比", force full pipeline so
    # diagnosis/pricing/risk sections are available and output is not overly short.
    if asks_detailed_lookup:
        return TaskType.MARKET_ANALYSIS_DECISION, 0.9

    if asks_only_for_price_delta:
        return TaskType.SIMPLE_LOOKUP, 0.86

    if looks_like_sinopec_listing_query(q) and wants_price:
        return TaskType.SIMPLE_LOOKUP, 0.9

    if wants_price and wants_change_analysis:
        return TaskType.MARKET_ANALYSIS_ONLY, 0.82

    if wants_price and len(q) <= 30:
        return TaskType.SIMPLE_LOOKUP, 0.75

    # Default market analysis — raise confidence for external market queries so LLM is not consulted
    if has_external_market_context:
        return TaskType.MARKET_ANALYSIS_ONLY, 0.78
    return TaskType.MARKET_ANALYSIS_ONLY, 0.6


class IntentParser:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    def parse(self, user_query: str) -> ParsedIntent:
        """
        Analyzes the user query to determine the intent (Task Type).
        """

        fast_mode = (os.getenv("FAST_NLU", "auto") or "auto").strip().lower()
        if fast_mode in {"1", "true", "on", "yes"}:
            fast_mode = "on"

        if fast_mode in {"auto", "on"}:
            task, conf = _fast_intent(user_query)
            # In auto mode, only trust fast intent if confidence is reasonably high.
            if task and (fast_mode == "on" or conf >= 0.75):
                return ParsedIntent(
                    original_query=user_query, task_type=task, confidence=conf
                )

        system_prompt = """TASK_CLASSIFICATION
You are the LLM brain of EDIA, a petrochemical business intelligence platform.
Your ONLY job is to read the user message and decide which of the 9 intent categories it belongs to.
Think carefully — some messages look similar but belong to different categories.

1. erp_data_query: User asks about INTERNAL company data — our own sales records, order history,
   delivery quantities, customer lists, contract details, production plans, inventory levels,
   shipment records, revenue, or any question using "our"/"my"/"we" about sales/products/customers.
   Examples: "show me last month sales", "how much did we sell", "who are our top customers",
   "sales of isoprene this quarter", "我们的销售数据", "上个月出货量", "客户列表",
   "合同情况", "库存多少", "production plan this week", "订单执行情况"

2. boss_briefing: User wants a comprehensive business overview or status report — overall company
   performance, business summary, daily/weekly briefing, or asks "how is business going".
   Examples: "give me a business briefing", "how is business today", "公司经营状况",
   "今日简报", "overall status", "business overview"

3. market_analysis_decision: User wants EXTERNAL market analysis AND specific recommendations/
   decisions (e.g., "what should I do", "pricing strategy", "give me a decision report").

4. market_analysis_only: User wants to understand the EXTERNAL market situation but did not ask
   for a decision (e.g., "what happened in the market", "why did price drop", "explain the market").

5. what_if_scenario: User proposes a hypothetical situation
   (e.g., "if oil price rises to $100", "assuming demand drops").

6. forecast_only: User ONLY wants price forecasts without full analysis
   (e.g., "predict isoprene price", "what will piperylene cost next week", "forecast C5").

7. simple_lookup: User wants a direct external market data lookup with no causal analysis.
   Can be one product or a small list if the user only asks for current price(s) + basic delta(s).
   If the user asks WHY, drivers, impact, outlook, strategy, forecast, risk → use market_analysis_* instead.

8. conversational: User is making small talk, greeting, saying goodbye, thanking you,
   asking how you are, or asking about your identity/capabilities.
   Examples: "hello", "how are you", "thanks", "what can you do", "bye",
   "who are you", "你好", "谢谢", "你能做什么", "introduce yourself"

9. out_of_domain: User is asking something COMPLETELY UNRELATED to petrochemicals, chemicals,
   commodity markets, business operations, ERP data, supply chains, or any related commercial topic.
   This includes: general knowledge, entertainment, personal advice, coding help, geography,
   politics, food/cooking, weather, math homework, jokes, creative writing, translations of
   unrelated content, and anything else outside the chemical/business domain.
   Examples: "what's the capital of France", "write me a poem", "who won the football game",
   "how do I cook pasta", "what is 2+2", "tell me a joke", "translate this sentence for me"

CRITICAL RULES:
- "our sales", "我们的", "my product sales", "how much did we sell", "客户", "合同",
  "库存", "订单", "出货", "production", "shipment" → erp_data_query
- Customer-specific quote questions (e.g., "壳牌一期报价", "Shell quote for ...") → erp_data_query
- Competitor spread/positioning questions → market_analysis_decision
- Inventory + action decision in same query → market_analysis_decision
- Plant shutdown/restart/maintenance market impact → market_analysis_* (NOT erp_data_query)
- MARKET prices, external trends, industry analysis → market_analysis_* or simple_lookup
- Greetings, thanks, small talk, asking about EDIA capabilities → conversational
- Anything with NO connection to the petrochemical/business domain → out_of_domain

Return ONLY a JSON object — no explanation, no extra text:
{
  "task_type": "string",
  "confidence": float
}"""
        try:
            result = self.llm.generate_json(system_prompt, user_query)

            # Map string to Enum
            task_type_str = result.get("task_type", "market_analysis_only")
            confidence = result.get("confidence", 0.0)

            try:
                task_type = TaskType(task_type_str)
            except ValueError:
                # LLM returned an unrecognised type — fall back to general market analysis
                task_type = TaskType.MARKET_ANALYSIS_ONLY

            return ParsedIntent(
                original_query=user_query, task_type=task_type, confidence=confidence
            )

        except Exception as e:
            # Fallback in case of error
            print(f"Error parsing intent: {e}")
            return ParsedIntent(
                original_query=user_query,
                task_type=TaskType.MARKET_ANALYSIS_ONLY,
                confidence=0.0,
            )
