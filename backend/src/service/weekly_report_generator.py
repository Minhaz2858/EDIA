"""
weekly_report_generator.py
--------------------------
Orchestrator for the C5/C9 weekly report.

Pipeline (spec §4):
    1. RESOLVE_WEEK
    2. LOAD_PRODUCT_CATALOG
    3. FETCH_MACRO_CONTEXT
    4. PER_PRODUCT_LOOP (→ chart + LLM narrative + Markdown block)
    5. BUILD_SUMMARY_RECOMMENDATIONS
    6. ASSEMBLE_REPORT_MD
    7. PERSIST
    8. RETURN

Critical constraint (spec §7, §12):
    The LLM NEVER generates numbers.  All prices, percentages, ranges and
    forecast values are computed by the pre-renderer and injected as JSON.
    The LLM writes only the Chinese narrative prose.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.engine import Engine

from backend.src.service.weekly_report_data import (
    build_product_chart_payload,
    ForecastBand,
    MacroContext,
    PreRenderedContext,
    ProductSpec,
    build_prerendered_ctx,
    collect_all_numbers,
    fetch_macro_context,
    fetch_price_snapshot,
    fetch_product_events,
    load_product_catalog,
    resolve_week_dates,
)
from backend.src.service.forecast_snapshot_adapter import (
    forecast_snapshot_to_band,
    forecast_snapshot_to_report_context,
    get_or_create_forecast_snapshot,
)
from backend.src.service.weekly_report_charts import render_chart_from_snapshot_payload

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _weekly_reports_root() -> Path:
    """Returns <repo_root>/exports/weekly_reports/, creating it if needed."""
    root = Path(__file__).resolve().parents[3] / "exports" / "weekly_reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# LLM narrative generation (spec §8)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是惠州伊斯科（Ecisco）的石化市场分析助手。你的任务是根据提供的结构化数据，撰写产品的"市场分析"段落，用于公司周报。

严格规则：
1. 只能使用 INPUT_JSON 中已提供的数字和事实。禁止自行添加任何数字、价格、百分比、日期或公司名称。
2. 禁止在"市场分析"段落中给出价格预测或预测区间。预测内容由其他模块生成。
3. 输出必须是简体中文，不混入英文或拼音。
4. 输出必须是 3 个自然段，结构如下：
   - 第 1 段：供应端（装置动态、库存、产量）
   - 第 2 段：需求端 / 相关产品联动
   - 第 3 段：宏观或政策影响（如适用；若数据未涉及则省略此段）
5. 每段 60–100 字。整体不超过 280 字。
6. 语气：专业、客观、贴近隆众/卓创等行业资讯的写作习惯。

不得输出任何前言、标题或解释，只输出三段正文。"""

_SYSTEM_PROMPT_EN = """You are a petrochemical market analyst for Ecisco. Based only on the provided structured data, write the product's market analysis section for a weekly report.

Strict rules:
1. Use only numbers and facts in INPUT_JSON. Do not invent any prices, percentages, dates, or company names.
2. Do not include price forecasts or forecast ranges in this analysis section.
3. Output must be in English.
4. Use 2-3 short paragraphs with this structure:
    - Supply side (plant status, inventory, output)
    - Demand side / linked products
    - Macro or policy impact (optional when data supports it)
5. Keep total length under 220 words.
6. Tone: professional, objective, and concise.

Do not output headings or explanations, only the analysis paragraphs."""

_FEW_SHOT_EXAMPLE = """
【示例输入】
{"product_name_zh":"异戊二烯","product_name_en":"Isoprene","week_start":"2024-12-24","week_end":"2024-12-30","erp_weighted_avg":"9860","erp_wow_pct":"下跌1.4%","region_prices":[{"region":"华东","range":"13800"}],"brent_price":"74.50","brent_wow_pct":"上涨1.20%","naphtha_price":"628","naphtha_wow_pct":"上涨0.50%","events":[{"date":"2024-12-26","title":"中韩乙烯C5分离装置推迟复产","impact":"供应偏紧"}],"related_moves":[{"product":"SIS","move":"持稳","relevance":"下游需求稳定"}]}

【示例输出】
本周异戊二烯供应端整体偏紧。中韩乙烯C5分离装置复产时间推迟，目前暂无产出，主营厂家供货保持正常节奏，库存维持低位。

下游需求方面，SIS整体开工率平稳，主营厂商产销正常，对异戊二烯采购维持刚需。橡胶市场走稳，价格变化不大，对原料采购支撑有限。

宏观方面，原油与日本石脑油环比小幅上涨，成本端略有抬升，但对异戊二烯价格的传导有限，市场情绪整体偏谨慎。"""

_FEW_SHOT_EXAMPLE_EN = """
[Sample Input]
{"product_name_zh":"异戊二烯","product_name_en":"Isoprene","week_start":"2024-12-24","week_end":"2024-12-30","erp_weighted_avg":"9860","erp_wow_pct":"down 1.4%","region_prices":[{"region":"East China","range":"13800"}],"brent_price":"74.50","brent_wow_pct":"up 1.20%","naphtha_price":"628","naphtha_wow_pct":"up 0.50%","events":[{"date":"2024-12-26","title":"C5 separation unit restart delayed","impact":"tighter supply"}],"related_moves":[{"product":"SIS","move":"stable","relevance":"steady downstream demand"}]}

[Sample Output]
Supply remained relatively tight this week. The delayed restart of a C5 separation unit reduced near-term spot availability, while major producers maintained normal shipment rhythm and kept inventories at low levels.

On the demand side, SIS operating rates stayed stable and downstream buyers continued rigid-demand procurement. End-market sentiment in elastomers was steady, providing limited but consistent support to raw-material consumption.

From a macro perspective, Brent and Japanese naphtha moved higher week over week, implying a slightly firmer cost floor. However, cost pass-through to isoprene remained moderate and market sentiment stayed cautious overall."""


def _call_llm_for_narrative(
    llm,
    ctx: PreRenderedContext,
    retry: int = 0,
    language: str = "zh",
) -> str:
    """
    Call Deepseek to generate the 3-paragraph market narrative.
    Params: temp=0.3, max_tokens=400, num_ctx=16384 (spec §8.1).
    """
    ctx_dict = dataclasses.asdict(ctx)
    is_en = (language or "zh").lower().startswith("en")
    user_prompt = (
        (_FEW_SHOT_EXAMPLE_EN if is_en else _FEW_SHOT_EXAMPLE)
        + "\n\nINPUT_JSON:\n"
        + json.dumps(ctx_dict, ensure_ascii=False, indent=2)
        + ('\n\nPlease draft the market analysis section based on the data above.' if is_en else '\n\n请根据以上数据撰写"市场分析"段落。')
    )

    stricter_prefix = "" if retry == 0 else (
        "IMPORTANT: Your previous output was rejected. Do not add numbers not present in INPUT_JSON, and do not include any forecast statements.\n\n"
        if is_en else
        "【重要提示】你的上一次输出因包含不允许的数字或预测语句被拒绝。本次请严格遵守：不添加任何原JSON中没有的数字，不写价格区间预测。\n\n"
    )

    try:
        narrative = llm.generate_completion(
            (_SYSTEM_PROMPT_EN if is_en else _SYSTEM_PROMPT),
            stricter_prefix + user_prompt,
            max_tokens=400,
            temperature=0.3,
        )
        return narrative.strip() if narrative else ""
    except TypeError:
        # Fallback: some LLM interfaces don't accept extra kwargs
        try:
            narrative = llm.generate_completion((_SYSTEM_PROMPT_EN if is_en else _SYSTEM_PROMPT), stricter_prefix + user_prompt)
            return narrative.strip() if narrative else ""
        except Exception as e:
            logger.error("LLM narrative call failed: %s", e)
            return ""


# ---------------------------------------------------------------------------
# Guardrail (spec §7.3)
# ---------------------------------------------------------------------------

def _validate_narrative(narrative: str, ctx: PreRenderedContext, language: str = "zh") -> Tuple[bool, str]:
    """
    Returns (ok, reason).
    Rejects if: (a) LLM added rogue numbers not in ctx, or (b) wrote a forecast.
    """
    nums_in_narrative = re.findall(r'\d{2,}(?:\.\d+)?', narrative)
    allowed = collect_all_numbers(ctx)

    rogue = [n for n in nums_in_narrative if n not in allowed]
    if rogue:
        return False, f"LLM hallucinated numbers: {rogue}"

    forecast_pattern = r'预计.*元/吨|主流价格.*元/吨|expected.*(cny|yuan|usd|price)|price\s+range'
    if re.search(forecast_pattern, narrative, flags=re.IGNORECASE):
        return False, "LLM wrote a forecast statement in the analysis block"

    return True, ""


def _deterministic_narrative_fallback(ctx: PreRenderedContext, language: str = "zh") -> str:
    """
    Template-only narrative when the LLM fails guardrail twice.
    Always correct but lower quality (spec §7.3).
    """
    is_en = (language or "zh").lower().startswith("en")
    snapshot = " ".join(ctx.snapshot_lines or [])
    supply_line = (
        f"Supply-side pricing signals were mixed this week. {snapshot} Main producers maintained normal shipment rhythm, while cost pass-through still needed confirmation."
        if is_en and snapshot else
        "No major supply-side disruptions were observed this week, and main producers maintained stable operating rates."
        if is_en else
        f"供应端价格信号分化。{snapshot} 主营厂家出货节奏保持正常，成本向现货价格的传导仍需继续观察。"
        if snapshot else
        "本周供应端暂无重大变动，主营厂家产能运行平稳。"
    )
    if ctx.events:
        supply_line = (
            f"This week, {ctx.events[0]['title']} led to {ctx.events[0].get('impact', 'supply-side fluctuation')}."
            if is_en else
            f"本周{ctx.events[0]['title']}，供应端{ctx.events[0].get('impact','出现波动')}。"
        )

    demand_line = (
        f"Downstream buyers stayed cautious. ERP weighted average transaction price was {ctx.erp_weighted_avg} CNY/t"
        + (f", {ctx.erp_wow_pct} week over week" if ctx.erp_wow_pct else "")
        + ", so order mix should be reviewed separately from spot price direction."
        if is_en and ctx.erp_weighted_avg else
        "Downstream demand stayed stable, and procurement pace showed no material change."
        if is_en else
        (
            f"下游采购仍偏谨慎。我司ERP周均成交价为{ctx.erp_weighted_avg}元/吨"
            + (f"，环比{ctx.erp_wow_pct}" if ctx.erp_wow_pct else "")
            + "，需将订单结构变化与现货价格方向分开判断。"
        )
        if ctx.erp_weighted_avg else
        "下游需求维持平稳，整体采购节奏未见明显变化。"
    )
    if ctx.related_moves:
        m = ctx.related_moves[0]
        demand_line = (
            f"In downstream markets, {m['product']} was {m['move']}, and {m.get('relevance', 'impact on raw-material demand was limited')}."
            if is_en else
            f"下游{m['product']}市场{m['move']}，{m.get('relevance','对原料需求影响有限')}。"
        )

    macro_line = ""
    if ctx.brent_price:
        macro_line = (
            (
                f"\n\nOn the macro side, Brent settled at {ctx.brent_price} USD/bbl"
                + (f", {ctx.brent_wow_pct} week over week" if ctx.brent_wow_pct else "")
                + ", with limited cost pass-through and a broadly wait-and-see sentiment."
            )
            if is_en else
            (
                f"\n\n宏观方面，布伦特原油报{ctx.brent_price}美元/桶"
                + (f"，环比{ctx.brent_wow_pct}" if ctx.brent_wow_pct else "")
                + "，成本端传导有限，市场整体维持观望。"
            )
        )

    return supply_line + "\n\n" + demand_line + macro_line


def _to_float_token(value: Any) -> Optional[float]:
    if value is None:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _derive_forecast_range(ctx: PreRenderedContext) -> Tuple[Optional[str], Optional[str], bool]:
    """Build a conservative fallback range from pre-rendered CNY/t price facts."""
    candidates: list[float] = []

    for line in ctx.snapshot_lines or []:
        if "元/吨" not in str(line) and "CNY/t" not in str(line):
            continue
        value = _to_float_token(line)
        if value is not None:
            candidates.append(value)

    for price in [ctx.sinopec_listed_price, ctx.erp_weighted_avg]:
        value = _to_float_token(price)
        if value is not None:
            candidates.append(value)

    for region_price in ctx.region_prices:
        value = _to_float_token(region_price.get("range"))
        if value is not None:
            candidates.append(value)

    # Ignore tiny values and obvious non-price tokens that slipped through labels.
    candidates = [value for value in candidates if value >= 1000]
    if not candidates:
        return None, None, False

    anchor = sorted(candidates)[len(candidates) // 2]
    low = round(anchor * 0.97 / 100) * 100
    high = round(anchor * 1.03 / 100) * 100
    if low == high:
        high = low + 100
    return f"{low:,.0f}", f"{high:,.0f}", True


def _generate_and_validate_narrative(llm, ctx: PreRenderedContext, language: str = "zh") -> str:
    """
    Call LLM → validate → retry up to 2× → fallback (spec §7.3).
    """
    for attempt in range(3):
        raw = _call_llm_for_narrative(llm, ctx, retry=attempt, language=language)
        if not raw:
            continue
        ok, reason = _validate_narrative(raw, ctx, language=language)
        if ok:
            return raw
        logger.warning(
            "Narrative guardrail rejected attempt %d for %s: %s",
            attempt + 1, ctx.product_name_zh, reason,
        )

    logger.error(
        "All LLM attempts failed for %s — using deterministic fallback.",
        ctx.product_name_zh,
    )
    return _deterministic_narrative_fallback(ctx, language=language)


# ---------------------------------------------------------------------------
# Markdown assemblers
# ---------------------------------------------------------------------------

def _product_block_md(
    idx: int,
    ctx: PreRenderedContext,
    narrative: str,
    chart_url: Optional[str],
    report_id: str,
    monthly_chart_url: Optional[str] = None,
    language: str = "zh",
) -> str:
    """Assemble the Markdown block for a single product (spec §5 + §13)."""

    # Charts — yearly 12-month + monthly daily (stacked vertically)
    is_en = (language or "zh").lower().startswith("en")
    chart_parts = []
    if chart_url:
        chart_parts.append(
            f"![{ctx.product_name_en} - 12-Month Price Trend]({chart_url})"
            if is_en else
            f"![{ctx.product_name_zh} - 近12月价格走势]({chart_url})"
        )
    else:
        chart_parts.append("_(12-month chart generation failed. Please check alerts_log.)_" if is_en else "_（年度走势图生成失败，请查阅 alerts_log）_")
    if monthly_chart_url:
        chart_parts.append(
            f"![{ctx.product_name_en} - Daily Trend This Month]({monthly_chart_url})"
            if is_en else
            f"![{ctx.product_name_zh} - 本月日价格走势]({monthly_chart_url})"
        )
    chart_ref = "\n\n".join(chart_parts)

    # Price snapshot bullets
    snapshot_lines = list(ctx.snapshot_lines or [])
    if not snapshot_lines:
        if ctx.sinopec_listed_price:
            wow = ctx.sinopec_listed_wow_delta or ("flat" if is_en else "持平")
            snapshot_lines.append(
                f"- Sinopec listed price for {ctx.product_name_en}: **{ctx.sinopec_listed_price}** CNY/t, WoW {wow}"
                if is_en else
                f"- 上海石化 {ctx.product_name_zh} 挂牌价报 **{ctx.sinopec_listed_price}** 元/吨，环比{wow}"
            )
        for rp in ctx.region_prices:
            snapshot_lines.append(
                f"- {rp.get('region','Market')} mainstream market price: **{rp.get('range','')}** CNY/t"
                if is_en else
                f"- {rp.get('region','市场')} 主流市场价 **{rp.get('range','')}** 元/吨"
            )
        if ctx.erp_weighted_avg:
            wow = ctx.erp_wow_pct or ("flat" if is_en else "持平")
            snapshot_lines.append(
                f"- ERP weighted weekly average transaction price: **{ctx.erp_weighted_avg}** CNY/t, WoW {wow}"
                if is_en else
                f"- 我司 ERP 周均成交价（加权）**{ctx.erp_weighted_avg}** 元/吨，环比{wow}"
            )
    if not snapshot_lines:
        snapshot_lines.append("- No price data available this week" if is_en else "- 本周暂无价格数据")

    snapshot_md = "\n".join(snapshot_lines)

    # Forecast block. If the formal forecast table has no range, derive a
    # conservative operational range from pre-rendered price facts so reports
    # are still complete and editable instead of showing an empty placeholder.
    forecast_low = ctx.forecast_low
    forecast_high = ctx.forecast_high
    forecast_model_version = ctx.forecast_model_version
    forecast_source = ctx.forecast_source or "Forecasting Tier"
    confidence_label = ctx.confidence_label
    confidence_score = ctx.confidence_score
    if not (forecast_low and forecast_high):
        derived_low, derived_high, derived = _derive_forecast_range(ctx)
        if derived:
            forecast_low = derived_low
            forecast_high = derived_high
            forecast_model_version = "derived_from_weekly_snapshot"
            confidence_label = "Medium" if is_en else "中"
            confidence_score = max(confidence_score, 3)

    if forecast_low and forecast_high:
        price_range = f"**{forecast_low}–{forecast_high} CNY/t**" if is_en else f"**{forecast_low}–{forecast_high} 元/吨**"
    else:
        price_range = "near the latest observed trading range" if is_en else "围绕本周最新成交区间"

    forecast_md = (
        (
            f"**Forecast:**\n"
            f"For {ctx.forecast_period}, {ctx.product_name_en} is expected to {ctx.forecast_trend_verb}, with mainstream prices in {price_range}.\n"
            f"> Confidence: {confidence_label} ({confidence_score}/5)\n"
            f"> Source: {forecast_source} ({forecast_model_version})"
        )
        if is_en else
        (
            f"**预测:**\n"
            f"预计 {ctx.forecast_period} {ctx.product_name_zh}**{ctx.forecast_trend_verb}**，主流价格在 {price_range}\n"
            f"> 置信度: {confidence_label} ({confidence_score}/5)\n"
            f"> 预测来源: {forecast_source} ({forecast_model_version})"
        )
    )

    return (
        (
            f"### {idx}. {ctx.product_name_en} ({ctx.product_name_zh})\n\n"
            f"{chart_ref}\n\n"
            f"**Price Snapshot:**\n{snapshot_md}\n\n"
            f"**Market Analysis:**\n{narrative}\n\n"
            f"{forecast_md}\n\n"
            f"---\n"
        )
        if is_en else
        (
            f"### {idx}. {ctx.product_name_zh} ({ctx.product_name_en})\n\n"
            f"{chart_ref}\n\n"
            f"**价格快照:**\n{snapshot_md}\n\n"
            f"**市场分析:**\n{narrative}\n\n"
            f"{forecast_md}\n\n"
            f"---\n"
        )
    )


def _macro_section_md(macro: MacroContext, events: List[Dict], language: str = "zh") -> str:
    is_en = (language or "zh").lower().startswith("en")
    lines = []
    if macro.brent_price:
        wow = macro.brent_wow_pct or ("data unavailable" if is_en else "数据暂缺")
        lines.append(
            f"- **Brent Crude**: {macro.brent_price} USD/bbl, WoW {wow}"
            if is_en else
            f"- **布伦特原油**: {macro.brent_price} 美元/桶，环比{wow}"
        )
    else:
        lines.append("- **Brent Crude**: data unavailable this week" if is_en else "- **布伦特原油**: 本周数据暂缺")

    if macro.naphtha_price:
        wow = macro.naphtha_wow_pct or ("data unavailable" if is_en else "数据暂缺")
        lines.append(
            f"- **Japan Naphtha**: {macro.naphtha_price} USD/t, WoW {wow}"
            if is_en else
            f"- **日本石脑油**: {macro.naphtha_price} 美元/吨，环比{wow}"
        )
    else:
        lines.append("- **Japan Naphtha**: data unavailable this week" if is_en else "- **日本石脑油**: 本周数据暂缺")

    macro_events = events or macro.events
    if macro_events:
        lines.append("- **Key Events This Week**:" if is_en else "- **本周关键事件**:")
        for ev in macro_events[:5]:
            lines.append(f"  - {ev.get('title','')} — {ev.get('impact','')}")

    return ("## 1. Macro Overview\n\n" if is_en else "## 一、宏观背景\n\n") + "\n".join(lines)


def _summary_section_md(
    all_contexts: List[PreRenderedContext],
    llm,
    language: str = "zh",
) -> str:
    """
    Build the cross-product summary + recommendations (spec §9).
    Clusters products by shared upstream events and generates recommendations.
    """
    is_en = (language or "zh").lower().startswith("en")

    # Simple cluster: group products that have events with similar keywords
    event_product_map: Dict[str, List[str]] = defaultdict(list)
    for ctx in all_contexts:
        for ev in ctx.events:
            key = ev.get("title", "")[:30]
            if key:
                event_product_map[key].append(ctx.product_name_zh)

    # Build recommendation blocks
    action_enum = [
        "Align production and shipment plans in advance",
        "Lock in key customer orders earlier",
        "Secure orders ahead of production planning cycles",
        "Adjust sales strategy dynamically to avoid inventory pressure",
    ]
    if not is_en:
        action_enum = [
            "我司做好排产及出货计划",
            "我司提早锁定客户订单",
            "根据我司排产计划提前锁定客户订单",
            "我司灵活调整销售策略，避免库存压力和税务风险",
        ]

    rec_blocks = []
    used_events: set[str] = set()

    # Per-event cluster
    for i, (event_key, products) in enumerate(event_product_map.items(), start=1):
        if event_key in used_events:
            continue
        used_events.add(event_key)
        product_str = "、".join(dict.fromkeys(products))  # deduplicated
        action = action_enum[i % len(action_enum)]
        rec_blocks.append(
            (
                f"**({i}) {product_str} — Production Plan**\n"
                f"{event_key} is likely to affect pricing for {product_str}; monitor follow-up supply-demand shifts closely.\n"
                f"> **Recommendation**: {action}"
            )
            if is_en else
            (
                f"**（{i}）{product_str} — 排产计划**\n"
                f"{event_key}，对{product_str}价格形成影响，请关注后续供需变化。\n"
                f"> **建议**：{action}"
            )
        )

    # If no events, add one generic block per product with forecast bias
    if not rec_blocks:
        for i, ctx in enumerate(all_contexts[:4], start=1):
            action = action_enum[(i - 1) % len(action_enum)]
            verb = ctx.forecast_trend_verb
            rec_blocks.append(
                (
                    f"**({i}) {ctx.product_name_en} — Sales Strategy**\n"
                    f"{ctx.product_name_en} is expected to {verb}; adjust commercial strategy in line with market momentum.\n"
                    f"> **Recommendation**: {action}"
                )
                if is_en else
                (
                    f"**（{i}）{ctx.product_name_zh} — 销售策略**\n"
                    f"预计{ctx.product_name_zh}{verb}，建议根据市场走势灵活调整。\n"
                    f"> **建议**：{action}"
                )
            )

    recs_md = "\n\n".join(rec_blocks)
    n = len(rec_blocks)

    # LLM-generated macro summary sentence (optional enrichment)
    macro_narrative = ""
    if llm and all_contexts:
        try:
            key_events = []
            for ctx in all_contexts:
                key_events.extend(ctx.events)
            if key_events:
                event_titles = "、".join(ev["title"] for ev in key_events[:3])
                macro_narrative_raw = llm.generate_completion(
                    "You are a petrochemical analyst. Summarize the impact of the following key events on the overall market in 1-2 sentences, without introducing new data."
                    if is_en else
                    "你是石化行业分析师，请用1-2句话（不超过100字）总结以下关键事件对整体市场的影响，不得引入新数据。",
                    f"Key events this week: {event_titles}" if is_en else f"本周关键事件：{event_titles}",
                )
                if macro_narrative_raw:
                    macro_narrative = macro_narrative_raw.strip()
        except Exception:
            pass

    macro_line = (
        (f"**Core Macro Event:** {macro_narrative}" if is_en else f"**核心宏观事件**: {macro_narrative}")
        if macro_narrative else ""
    )

    return (
        (
            f"## 3. Summary and Recommendations\n\n"
            f"{macro_line}\n\n"
            f"### Recommendations ({n})\n\n"
            f"{recs_md}\n\n"
            f"---"
        )
        if is_en else
        (
            f"## 三、总结预测及建议\n\n"
            f"{macro_line}\n\n"
            f"### 建议（共 {n} 条）\n\n"
            f"{recs_md}\n\n"
            f"---"
        )
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def generate_weekly_report(
    db,
    mysql_engine: Engine,
    llm,
    language: str = "zh",
    week_start_str: Optional[str] = None,
    week_end_str: Optional[str] = None,
    user_id: str = "system",
    session_id: str = "system",
    progress_callback: Optional[Callable[[str], None]] = None,
    static_base_url: str = "/api/reports-static",
    current_week: bool = False,
) -> Dict[str, Any]:
    """
    Full pipeline (spec §4).  Returns the response payload dict (spec §2).

    progress_callback(message: str) — called after each product is processed,
    suitable for streaming stage events to the frontend.
    """
    t_start = time.perf_counter()
    is_en = (language or "zh").lower().startswith("en")

    def _progress(msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Step 1: Resolve week dates
    # -----------------------------------------------------------------------
    _progress("Resolving report period..." if is_en else "正在解析报告周期…")
    week_start, week_end, prev_week_start, prev_week_end = resolve_week_dates(
        week_start_str, week_end_str, current_week=current_week
    )

    # -----------------------------------------------------------------------
    # Step 2: Load product catalog
    # -----------------------------------------------------------------------
    _progress("Loading product catalog..." if is_en else "正在加载产品目录…")
    products = load_product_catalog(db)  # raises ValueError if empty

    # -----------------------------------------------------------------------
    # Step 3: Fetch macro context
    # -----------------------------------------------------------------------
    _progress("Fetching macro data..." if is_en else "正在获取宏观数据…")
    macro: MacroContext
    try:
        macro = fetch_macro_context(mysql_engine, week_end, prev_week_end)
    except Exception as e:
        logger.warning("Macro context fetch failed: %s", e)
        from backend.src.service.weekly_report_data import MacroContext
        macro = MacroContext(
            brent_price=None, brent_wow_pct=None,
            naphtha_price=None, naphtha_wow_pct=None,
            events=[],
        )

    # -----------------------------------------------------------------------
    # Step 4: Per-product loop
    # -----------------------------------------------------------------------
    report_id = f"wr_{week_end[:4]}w{_iso_week(week_end)}_{uuid.uuid4().hex[:6]}"
    charts_dir = _weekly_reports_root() / report_id / "charts"

    product_blocks_md: List[str] = []
    all_contexts: List[PreRenderedContext] = []
    chart_payload_by_product: Dict[str, Dict[str, Any]] = {}
    forecast_snapshot_context_by_product: Dict[str, Dict[str, Any]] = {}
    snapshot_engine = getattr(db, "engine", mysql_engine)

    for idx, product in enumerate(products, start=1):
        _progress(
            f"Processing product {idx}/{len(products)}: {product.name_en}..."
            if is_en else
            f"处理产品 {idx}/{len(products)}: {product.name_zh}…"
        )

        # 4a. Price snapshot
        try:
            snapshot = fetch_price_snapshot(
                mysql_engine, product,
                week_start, week_end, prev_week_start, prev_week_end,
                language=("en" if is_en else "zh"),
            )
        except Exception as e:
            logger.warning("Price snapshot failed for %s: %s", product.slug, e)
            from backend.src.service.weekly_report_data import PriceSnapshot
            snapshot = PriceSnapshot(
                sinopec_listed=None, sinopec_wow_delta=None,
                region_prices=[], erp_weighted_avg=None, erp_wow_pct=None,
                erp_weekly_volume=None, snapshot_lines=[],
            )

        # 4b. Product-level events
        try:
            events = fetch_product_events(mysql_engine, product, week_start, week_end)
        except Exception:
            events = []

        # 4c. Canonical forecast band from the shared forecasting service snapshot
        try:
            forecast_snapshot = get_or_create_forecast_snapshot(
                snapshot_engine,
                product.slug,
                as_of_date=week_end,
                run_reason="weekly_report",
            )
            forecast = forecast_snapshot_to_band(
                forecast_snapshot,
                horizon="7d",
                language=("en" if is_en else "zh"),
            )
            forecast_snapshot_context_by_product[product.slug] = forecast_snapshot_to_report_context(
                forecast_snapshot,
                horizon="7d",
            )
            logger.info(
                "Weekly report forecast for %s uses canonical snapshot %s",
                product.slug,
                forecast.snapshot_id or "generated_without_id",
            )
        except Exception as e:
            logger.warning("Canonical forecast snapshot failed for %s: %s", product.slug, e)
            forecast = ForecastBand(
                period="next week" if is_en else "下周",
                trend_verb="remain stable" if is_en else "走稳",
                low=None, high=None,
                confidence_label="Low" if is_en else "低",
                confidence_score=2,
                model_version="fc_fallback",
                source="Forecasting Service",
            )
            forecast_snapshot_context_by_product[product.slug] = {
                "forecast_model_version": "fc_fallback",
                "source": "canonical_snapshot_unavailable",
                "as_of_date": week_end,
                "horizon": "7d",
            }

        # 4d. Build chart payload + render PNGs from the same payload used for persistence
        product_chart_payload = build_product_chart_payload(
            engine=mysql_engine,
            product=product,
            forecast=forecast,
            week_start=week_start,
            week_end=week_end,
            language=("en" if is_en else "zh"),
        )
        chart_payload_by_product[product.slug] = product_chart_payload

        chart_path: Optional[Path] = None
        try:
            chart_path = render_chart_from_snapshot_payload(
                product_payload=product_chart_payload,
                chart_type="yearly",
                report_id=report_id,
                charts_dir=charts_dir,
                language=("en" if is_en else "zh"),
            )
        except Exception as e:
            logger.warning("Chart render failed for %s: %s", product.slug, e)

        # Chart URL for Markdown (served via StaticFiles mount)
        chart_url: Optional[str] = None
        if chart_path and chart_path.exists():
            chart_url = f"{static_base_url}/{report_id}/charts/{product.slug}_12mo.png"

        # 4e. Render monthly chart
        monthly_chart_path: Optional[Path] = None
        try:
            monthly_chart_path = render_chart_from_snapshot_payload(
                product_payload=product_chart_payload,
                chart_type="monthly",
                report_id=report_id,
                charts_dir=charts_dir,
                language=("en" if is_en else "zh"),
            )
        except Exception as e:
            logger.warning("Monthly chart render failed for %s: %s", product.slug, e)

        monthly_chart_url: Optional[str] = None
        if monthly_chart_path and monthly_chart_path.exists():
            monthly_chart_url = f"{static_base_url}/{report_id}/charts/{product.slug}_monthly.png"

        # 4f. Build pre-rendered context
        ctx = build_prerendered_ctx(
            product=product,
            snapshot=snapshot,
            macro=macro,
            events=events,
            forecast=forecast,
            week_start=week_start,
            week_end=week_end,
            prev_week_start=prev_week_start,
            prev_week_end=prev_week_end,
        )
        all_contexts.append(ctx)
        # 4g + 4h. LLM narrative + guardrail validation
        narrative = _generate_and_validate_narrative(llm, ctx, language=("en" if is_en else "zh"))

        # 4i. Assemble product block
        block_md = _product_block_md(
            idx,
            ctx,
            narrative,
            chart_url,
            report_id,
            monthly_chart_url,
            language=("en" if is_en else "zh"),
        )
        product_blocks_md.append(block_md)

    # -----------------------------------------------------------------------
    # Step 5: Build summary + recommendations
    # -----------------------------------------------------------------------
    _progress("Generating summary and recommendations..." if is_en else "正在生成总结与建议…")
    try:
        summary_md = _summary_section_md(all_contexts, llm, language=("en" if is_en else "zh"))
    except Exception as e:
        logger.error("Summary generation failed: %s", e)
        summary_md = (
            "## 3. Summary and Recommendations\n\n_Summary generation failed. Please check alerts_log._\n"
            if is_en else
            "## 三、总结预测及建议\n\n_总结生成失败，请查阅 alerts_log。_\n"
        )

    # -----------------------------------------------------------------------
    # Step 6: Assemble full report Markdown
    # -----------------------------------------------------------------------
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    product_count = len(products)

    header_md = (
        (
            f"# C5C9 Weekly Summary and Forecast — {week_start} to {week_end}\n\n"
            f"> Generated at: {generated_at}\n"
            f"> Data range: {week_start} to {week_end} "
            f"(WoW baseline: {prev_week_start} to {prev_week_end})\n"
            f"> Products covered: {product_count}\n\n"
        )
        if is_en else
        (
            f"# C5C9 周度总结及预测 — {week_start} 至 {week_end}\n\n"
            f"> 报告生成时间: {generated_at}\n"
            f"> 数据范围: {week_start} 至 {week_end} "
            f"(环比基准: {prev_week_start} 至 {prev_week_end})\n"
            f"> 覆盖产品: {product_count} 个\n\n"
        )
    )

    # All product events for macro section
    all_events: List[Dict] = []
    for ctx in all_contexts:
        all_events.extend(ctx.events)

    macro_md = _macro_section_md(macro, all_events, language=("en" if is_en else "zh"))
    products_header_md = "\n\n## 2. Product Analysis\n\n" if is_en else "\n\n## 二、产品分析\n\n"

    full_md = (
        header_md
        + macro_md
        + products_header_md
        + "\n".join(product_blocks_md)
        + "\n"
        + summary_md
    )

    # -----------------------------------------------------------------------
    # Step 7: Persist
    # -----------------------------------------------------------------------
    _progress("Saving report..." if is_en else "正在保存报告…")
    report_dir = _weekly_reports_root() / report_id
    report_dir.mkdir(parents=True, exist_ok=True)

    # Write markdown file
    md_path = report_dir / "report.md"
    try:
        md_path.write_text(full_md, encoding="utf-8")
    except Exception as e:
        logger.error("Failed to write report.md: %s", e)

    # Write meta.json
    meta = {
        "report_id": report_id,
        "week_start": week_start,
        "week_end": week_end,
        "prev_week_start": prev_week_start,
        "prev_week_end": prev_week_end,
        "products": [p.slug for p in products],
        "products_count": product_count,
        "generated_at": generated_at,
        "model_version": "deepseek-chat",
        "forecast_model_version": "canonical_forecasting_service",
        "forecast_snapshots": forecast_snapshot_context_by_product,
        "language": ("en" if is_en else "zh"),
        "weekly_chart_payload": {
            "version": 1,
            "report_id": report_id,
            "week_start": week_start,
            "week_end": week_end,
            "language": ("en" if is_en else "zh"),
            "products": chart_payload_by_product,
        },
    }
    try:
        (report_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    # Persist to generated_reports table
    title = f"C5C9 Weekly Report — {week_start} to {week_end}" if is_en else f"C5C9 周报 — {week_start} 至 {week_end}"
    generated_report_row: Optional[Dict[str, Any]] = None
    try:
        generated_report_row = db.create_generated_report(
            user_id=user_id,
            session_id=session_id,
            title=title,
            summary=(
                f"C5/C9 weekly summary and forecast, {product_count} products, report ID: {report_id}"
                if is_en else
                f"C5/C9 周度总结及预测，共 {product_count} 个产品，报告ID: {report_id}"
            ),
            markdown=full_md,
            blocks=None,
            context=meta,
            report_type="weekly",
            status="draft",
        )
    except Exception as e:
        logger.error("Failed to persist report to DB: %s", e)

    # -----------------------------------------------------------------------
    # Step 8: Return
    # -----------------------------------------------------------------------
    duration = time.perf_counter() - t_start
    preview_url = f"/api/reports/weekly/{report_id}"

    return {
        "id": str((generated_report_row or {}).get("id") or ""),
        "report_id": report_id,
        "title": title,
        "markdown": full_md,
        "preview_url": preview_url,
        "generated_at": generated_at,
        "duration_sec": round(duration, 1),
        "products_count": product_count,
        "week_start": week_start,
        "week_end": week_end,
        "context": meta,
    }


def _iso_week(date_str: str) -> str:
    """Return ISO week number string (zero-padded) from a YYYY-MM-DD string."""
    try:
        from datetime import date
        d = date.fromisoformat(date_str)
        return f"{d.isocalendar()[1]:02d}"
    except Exception:
        return "00"
