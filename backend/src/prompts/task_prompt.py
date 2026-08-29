from enum import Enum


class TaskType(Enum):
    MARKET_ANALYSIS_DECISION = "market_analysis_decision"
    MARKET_ANALYSIS_ONLY = "market_analysis_only"
    WHAT_IF_SCENARIO = "what_if_scenario"
    FORECAST_ONLY = "forecast_only"
    SIMPLE_LOOKUP = "simple_lookup"
    ERP_DATA_QUERY = "erp_data_query"
    BOSS_BRIEFING = "boss_briefing"
    CONVERSATIONAL = "conversational"
    OUT_OF_DOMAIN = "out_of_domain"


def get_task_prompt(task_type: TaskType, language: str = "en") -> str:
    """
    Returns the task definition prompt based on the selected task type.
    """
    if str(language or "").strip().lower() in {"zh", "cn", "chinese"}:
        zh_prompt = _get_task_prompt_zh(task_type)
        if zh_prompt:
            return zh_prompt

    if task_type == TaskType.MARKET_ANALYSIS_DECISION:
        return """
TASK TYPE: Market Analysis & Comprehensive Decision

Your objective is to:
1. Diagnose the current market situation based on provided context.
2. Forecast future trends (price, volume, demand).
3. Provide specific, actionable business recommendations (e.g., pricing, production volume).
4. Highlight risks and uncertainties.

MANDATORY QUALITY RULES:
- Use a professional executive tone: concise, precise, and business-action oriented.
- Separate facts from inference explicitly. Label key statements as one of: Verified Fact / Inference / Recommendation.
- Never claim a cause (e.g., product grade difference, segment premium, supply shock) unless the context provides direct evidence.
- If evidence is insufficient, say so directly and provide the minimum data needed to confirm the conclusion.
- Avoid run-on paragraphs; use clean headings, short paragraphs, and valid Markdown tables.

STRUCTURE EXPECTATION:
- Organize as: Market Overview, Key Drivers, Forecast, Pricing Decision, Risk Alerts.
- Include concrete trigger conditions and actions (if X happens, do Y).

This is the most complete decision support mode.
"""
    elif task_type == TaskType.MARKET_ANALYSIS_ONLY:
        return """
TASK TYPE: Market Analysis Only

Your objective is to:
1. State the current price(s) for the requested product(s) with exact figures, units, and reference date.
2. Show week-over-week (环比) and month-over-month (同比) changes where available.
3. Diagnose the current market situation: identify the key drivers (upstream feedstock costs, supply/demand balance, macro factors, plant shutdowns/restarts).
4. Forecast near-term direction (next 1-2 weeks) — directional view with key assumptions.
5. Flag any risk factors or notable anomalies.

Structure: use headers or numbered sections. Be specific with numbers. Aim for a comprehensive but readable response.
Do NOT provide specific business decisions, pricing recommendations, or action items in this mode.
"""
    elif task_type == TaskType.WHAT_IF_SCENARIO:
        return """
TASK TYPE: What-If Scenario Analysis

Your objective is to:
1. Analyze the impact of the specific hypothetical condition stated in the user's query.
2. Provide clear quantitative estimates: if X happens, price Y moves by Z amount/percentage.
3. Derive step-by-step pricing or action recommendations under that hypothetical.
4. Compare the hypothetical scenario outcome to the current baseline.
5. Identify risks, limits, and counter-scenarios.

FORMAT RULES (mandatory):
- Use the full 6-section report structure (01 市场现状 → 02 驱动因素 → 03 价格预测 → 04 定价决策 → 05 风险预警 → 06 建议追问).
- For the pricing derivation in section 04, ALWAYS render a Markdown table with columns: 步骤 | 变化 (元/吨) | 说明 | 累计价格. Each row on a separate line. Do NOT embed the table inline in a paragraph.
- Use bullet points for lists, not run-on sentences.
- No preamble like "好的，我将…". Start directly from section 01.
"""
    elif task_type == TaskType.FORECAST_ONLY:
        return """
TASK TYPE: Forecast Only

Your objective is to:
1. Provide price forecasts for the requested product(s) at 3-day, 7-day, and 30-day horizons.
2. Include confidence intervals and scenario analysis (bull/base/bear).
3. Cite the model ensemble components and their weights.

Do NOT provide detailed diagnosis, pricing decisions, or operational recommendations.
"""
    elif task_type == TaskType.SIMPLE_LOOKUP:
        return """
TASK TYPE: Simple Data Lookup

Your objective is to:
1. Return the requested data point(s) with the actual number, unit, and date.
2. Show the week-over-week or period change if available in the data.
3. For each product mentioned, add 1-2 sentences of context: what is driving the price level, any notable upstream or supply-side factors.
4. Include the data source and reference date.

Keep the response focused but informative — not a one-liner. Aim for a clear, well-structured answer with brief market context.
"""
    elif task_type == TaskType.ERP_DATA_QUERY:
        return """
TASK TYPE: Internal Business Data Query (ERP)

You are answering a question about the company's INTERNAL business operations data.
The ERP_DATA section below contains real data queried from the company's ERP/MySQL database.

Your objective is to:
1. Present the requested internal data (sales volumes, inventory levels, customer details, contracts, production plans) clearly and accurately.
2. Use tables, bullet points, or charts as appropriate to organize the data.
3. Highlight key numbers: totals, top performers, trends, and noteworthy changes.
4. If period comparisons are available, show growth/decline percentages.
5. Add brief business insights based on the data patterns (e.g., "Sales are trending up 15% month-over-month").
6. If the requested data is not available, say so clearly and suggest what data IS available.

Keep the response data-driven and actionable. This is internal business intelligence for executives.
"""
    elif task_type == TaskType.BOSS_BRIEFING:
        return """
TASK TYPE: Executive Business Briefing

You are providing a comprehensive business status briefing for company leadership.
The data below contains real operational metrics from the company's ERP/MySQL database.

Your objective is to:
1. Provide a high-level summary of business health across key dimensions:
   - Sales performance (volume, revenue trends)
   - Inventory status (days of stock, safety stock alerts)
   - Production utilization
   - Key market price movements
2. Flag any items that need immediate attention (low inventory, big price moves, contract expirations).
3. Compare current performance to recent history (month-over-month, week-over-week).
4. Keep the tone executive-friendly: concise, data-backed, action-oriented.
5. End with 2-3 recommended actions or focus areas.

MANDATORY QUALITY RULES:
- Output must be board-ready: crisp, structured, no filler text.
- Use explicit section headers and keep each section focused on decision relevance.
- Every recommendation must include rationale and a trigger/condition.
- If key data is missing, state it clearly and describe business impact.
- Do not output placeholders or meta notes about generation quality.

This is a "boss dashboard in words" — the user is a senior decision-maker who needs the full picture quickly.
"""
    elif task_type == TaskType.CONVERSATIONAL:
        return """
TASK TYPE: Conversational Assistant

Your objective is to:
1. Provide a direct, helpful answer to the user's question in natural language.
2. Keep the response concise unless the user asks for more depth.
3. Use available context when relevant, but do not force heavy analytical structure.
4. If the request is ambiguous, ask one focused clarifying question.

Do not fabricate data. If specific business or market numbers are unavailable, clearly say what is known and unknown.
"""
    elif task_type == TaskType.OUT_OF_DOMAIN:
        return """
TASK TYPE: Out-of-Domain Query

Your objective is to:
1. Politely explain that the request is outside this assistant's primary business/market scope.
2. Still provide a brief, helpful high-level answer when possible.
3. Offer the closest in-scope alternative the assistant can handle.

Do not invent internal company data or unsupported market facts.
"""
    else:
        raise ValueError(f"Unknown task type: {task_type}")


def _get_task_prompt_zh(task_type: TaskType) -> str:
    if task_type == TaskType.MARKET_ANALYSIS_DECISION:
        return """
任务类型：市场分析与综合决策

你的目标是：
1. 基于已提供上下文诊断当前市场状态。
2. 预测未来价格、销量和需求趋势。
3. 给出具体、可执行的业务建议，例如定价、产量或客户动作。
4. 明确提示风险和不确定性。

强制质量规则：
- 使用专业、简洁、面向决策的管理层表达。
- 将事实、推断和建议分开说明，并标注“已验证事实 / 推断 / 建议”。
- 没有直接证据时，不要编造原因或因果链。
- 证据不足时，直接说明缺口，并指出还需要哪些最小数据。
- 使用清晰标题、短段落和合法 Markdown 表格。

结构要求：
- 按“市场现状、关键驱动、价格预测、定价决策、风险预警”组织。
- 给出触发条件和对应动作，例如“如果 X 发生，则执行 Y”。
"""
    if task_type == TaskType.ERP_DATA_QUERY:
        return """
任务类型：内部业务数据查询（ERP）

你正在回答公司内部经营数据问题。下方 ERP_DATA 区块包含从公司 ERP/MySQL 数据库查询到的真实数据。

你的目标是：
1. 准确呈现用户请求的内部数据，包括销量、销售额、库存、客户、合同或生产计划。
2. 使用表格、要点或图表组织数据，避免大段堆砌。
3. 突出关键数字：合计、排名、趋势和异常变化。
4. 如有周期对比，展示增长或下降比例。
5. 基于数据模式给出简短业务洞察。
6. 如果请求数据不可用，要明确说明，并指出当前可用的数据范围。

销售额类问题必须说明口径：对每条销售订单明细的 ERP FAMOUNT（不含税销售金额，单位元）求和；禁止用总销量×加权均价推算销售额。
答案必须以数据为核心、可供管理层直接使用。
"""
    if task_type == TaskType.BOSS_BRIEFING:
        return """
任务类型：管理层经营简报

你正在为公司管理层提供综合经营状态简报。下方数据包含来自 ERP/MySQL 的真实经营指标。

你的目标是：
1. 从销售表现、销售额趋势、库存状态、生产利用率和市场价格变化等维度总结业务健康度。
2. 标记需要立即关注的事项，例如低库存、大幅价格波动或合同风险。
3. 对比近期历史表现，例如环比或同比变化。
4. 使用管理层友好的表达：简洁、数据支撑、行动导向。
5. 结尾给出 2-3 条推荐动作或重点关注方向。

强制质量规则：
- 输出要可直接用于管理层阅读，结构清晰，不写套话。
- 每条建议都必须包含依据和触发条件。
- 关键数据缺失时，明确说明缺口及其业务影响。
- 不输出占位符或关于生成质量的元说明。
"""
    if task_type == TaskType.MARKET_ANALYSIS_ONLY:
        return """
任务类型：市场分析

你的目标是：
1. 给出用户请求产品的当前价格、单位和参考日期。
2. 在数据可用时展示环比、同比或周期变化。
3. 诊断关键驱动因素，包括上游成本、供需平衡、宏观因素和装置开停工。
4. 给出未来 1-2 周方向性判断和关键假设。
5. 标记风险因素或异常信号。

不要给出具体商业决策、定价建议或操作动作。
"""
    if task_type == TaskType.WHAT_IF_SCENARIO:
        return """
任务类型：情景推演

你的目标是：
1. 分析用户提出的假设条件会带来什么影响。
2. 给出清晰的量化估计：如果 X 发生，价格或销量可能变化多少。
3. 推导该情景下的定价或行动建议。
4. 将情景结果与当前基准进行对比。
5. 说明风险、边界条件和反向情景。

必须使用完整 6 段式结构，并在定价推导处使用 Markdown 表格。
"""
    if task_type == TaskType.FORECAST_ONLY:
        return """
任务类型：价格预测

你的目标是提供 3 天、7 天和 30 天价格预测，包含置信区间、牛/基准/熊情景和模型依据。不要输出详细诊断或操作建议。
"""
    if task_type == TaskType.SIMPLE_LOOKUP:
        return """
任务类型：简单数据查询

你的目标是返回用户请求的数据点、单位、日期和数据来源；如有变化幅度，也要展示。回答保持聚焦，但应包含必要市场背景。
"""
    if task_type == TaskType.CONVERSATIONAL:
        return """
任务类型：自然对话

直接、自然地回答用户问题。除非用户要求深度分析，否则保持简洁；如果问题含糊，只问一个关键澄清问题。
"""
    if task_type == TaskType.OUT_OF_DOMAIN:
        return """
任务类型：范围外问题

礼貌说明请求不在本助手主要业务/市场范围内；如可以，提供简短高层回答，并给出最接近的可处理方向。不要编造公司内部数据或无依据市场事实。
"""
    raise ValueError(f"Unknown task type: {task_type}")
