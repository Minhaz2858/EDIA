"""Fast-start preview markdown for streaming chat (shared by server and Hermes runtime)."""

from __future__ import annotations

from typing import Any, Dict, List


def build_fast_start_preview(prep_state: Dict[str, Any], request_message: str) -> str:
    user_context = prep_state.get("user_context")
    parsed = prep_state.get("parsed_intent")
    task_type = str(getattr(getattr(parsed, "task_type", None), "value", "") or "").strip().lower()
    product = str(getattr(user_context, "product", "") or "").strip()
    period = str(getattr(user_context, "period", "") or "").strip()
    diagnosis = prep_state.get("diagnosis") or {}
    pricing = prep_state.get("pricing_decision") or {}
    forecast = prep_state.get("forecast") or {}

    lead = str(
        diagnosis.get("root_cause")
        or pricing.get("reasoning")
        or forecast.get("trend")
        or ""
    ).strip()
    action = str(
        pricing.get("action")
        or pricing.get("recommended_action")
        or pricing.get("recommendation")
        or ""
    ).strip()
    recommended_price = pricing.get("recommended_price")
    price_range = pricing.get("price_range") or []

    is_chinese = any("\u4e00" <= ch <= "\u9fff" for ch in str(request_message or ""))

    if is_chinese:
        header = "## 快速结论"
        lines: List[str] = [header, ""]
        scope_bits = [bit for bit in [product, period] if bit]
        if scope_bits:
            lines.append(f"- 分析范围：{' / '.join(scope_bits)}")
        elif str(request_message or "").strip():
            lines.append(f"- 问题范围：{str(request_message).strip()[:80]}")
        if lead:
            lines.append(f"- 当前主线：{lead}")
        else:
            lines.append("- 当前状态：正在汇总市场、库存与定价相关信号。")
        if recommended_price not in (None, ""):
            if isinstance(price_range, (list, tuple)) and len(price_range) >= 2:
                lines.append(
                    f"- 建议价格：{recommended_price}，参考区间 {price_range[0]} - {price_range[1]}"
                )
            else:
                lines.append(f"- 建议价格：{recommended_price}")
        elif action:
            lines.append(f"- 当前动作：{action}")
        if task_type in {"analysis", "report", "market_analysis_decision", "boss_briefing"}:
            lines.append("- 正在继续生成完整分析、风险与建议。")
        return "\n".join(lines).strip() + "\n\n"

    header = "## Quick Take"
    lines = [header, ""]
    scope_bits = [bit for bit in [product, period] if bit]
    if scope_bits:
        lines.append(f"- Scope: {' / '.join(scope_bits)}")
    elif str(request_message or "").strip():
        lines.append(f"- Scope: {str(request_message).strip()[:80]}")
    if lead:
        lines.append(f"- Core signal: {lead}")
    else:
        lines.append("- Current status: grounding the answer in market, inventory, and pricing signals.")
    if recommended_price not in (None, ""):
        if isinstance(price_range, (list, tuple)) and len(price_range) >= 2:
            lines.append(
                f"- Recommended price: {recommended_price} within {price_range[0]} to {price_range[1]}"
            )
        else:
            lines.append(f"- Recommended price: {recommended_price}")
    elif action:
        lines.append(f"- Immediate action: {action}")
    if task_type in {"analysis", "report", "market_analysis_decision", "boss_briefing"}:
        lines.append("- Full grounded analysis is still being generated.")
    return "\n".join(lines).strip() + "\n\n"


def dedupe_fast_preview_markdown(markdown: str) -> str:
    """Remove consecutive duplicate Quick Take / 快速结论 blocks."""
    import re

    text = str(markdown or "")
    if not text.strip():
        return text

    pattern = re.compile(
        r"(?:##\s*Quick Take|##\s*快速结论)\s*[\s\S]*?(?=\n##\s+|\Z)",
        re.MULTILINE,
    )
    blocks = [match.group(0).strip() for match in pattern.finditer(text)]
    if len(blocks) <= 1:
        return text

    normalized = [re.sub(r"\s+", " ", block).strip() for block in blocks]
    if len(set(normalized)) != 1:
        return text

    first = blocks[0]
    first_index = text.find(first)
    if first_index < 0:
        return text
    remainder = text[first_index + len(first) :]
    second_index = remainder.find(first)
    if second_index < 0:
        return text
    return (text[: first_index + len(first)] + remainder[second_index + len(first) :]).strip()
