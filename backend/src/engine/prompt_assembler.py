from typing import Dict
import re
from backend.src.prompts.role_prompt import get_role_prompt
from backend.src.prompts.task_prompt import get_task_prompt, TaskType
from backend.src.prompts.context_prompt import get_context_prompt
from backend.src.prompts.reasoning_prompt import get_reasoning_prompt
from backend.src.prompts.output_prompt import (
    get_concise_output_prompt,
    get_conversational_output_prompt,
    get_erp_analysis_output_prompt,
    get_executive_brief_output_prompt,
    get_executive_memo_output_prompt,
    get_follow_up_explanation_output_prompt,
    get_output_prompt,
    get_structured_output_prompt,
)


def _detect_language(text: str) -> str:
    """Detect if user query is Chinese or English.

    Returns 'zh' if Chinese characters are dominant, 'en' otherwise.
    """
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "zh" if chinese_chars > 0 else "en"


def _build_user_instructions(language: str) -> str:
    if language == "en":
        claim_labels = "verified fact / event evidence / inference / recommendation"
        preamble = 'Okay, I will...'
        delta_label = 'Changes vs last query'
        ladder_instruction = (
            'If pricing decision data includes adjustment steps, render a "Pricing Ladder" additive '
            'table as a standalone Markdown table. Every row must be on its own separate line. '
            'Never output the table as inline text within a bullet point or paragraph.'
        )
        follow_up_label = 'You may also ask:'
        current_price_rule = (
            'If the requested current price or series is not present in MySQL context, explicitly say '
            'it is unavailable. Do not infer it from news, prior reports, or historical documents.'
        )
        no_event_rule = (
            'If no verified event evidence is present, do not invent a root-cause story; say that no '
            'recent verified event was detected and base the answer on internal market data only.'
        )
        language_purity_rule = None
    else:
        claim_labels = "已验证事实 / 事件证据 / 推断 / 建议"
        preamble = '好的，我将...'
        delta_label = '与上次查询'
        ladder_instruction = (
            'If pricing decision data includes adjustment steps, render a "定价推导过程" additive '
            'ladder as a standalone Markdown table. CRITICAL: every row (header, separator, and each '
            'data row) must be on its own separate line. Never output the table as inline text within '
            'a bullet point or paragraph.'
        )
        follow_up_label = '您可能还想问：'
        current_price_rule = (
            'If the requested current price or series is not present in MySQL context, explicitly say '
            'it is unavailable. Do not infer it from news, prior reports, or historical documents.'
        )
        no_event_rule = (
            'If no verified event evidence is present, do not invent a root-cause story; say that no '
            'recent verified event was detected and base the answer on internal market data only.'
        )
        language_purity_rule = (
            '中文模式下，所有可见自然语言必须使用简体中文。把英文分析标签、状态、动作、阶段名和表头翻译成中文；'
            '产品专有名词可保留必要英文缩写，但不要输出 Direct Answer、Supply-Demand Driver、Pricing Risk、'
            'Action、Stage-4、Weekly plan、normal、bearish、bullish、reduce_price 等英文标签。'
        )

    lines = [
        'IMPORTANT INSTRUCTIONS:',
        '- Analyze ALL the data provided above. Do not ignore any section.',
        '- Before drafting, identify the user\'s exact decision demand: what must be diagnosed, what must be forecast, and what concrete action must be recommended.',
        '- Reference specific numbers, dates, and data points from the context in your response.',
        f'- For each major claim, be explicit whether it is: {claim_labels}.',
        f'- Start the response directly from the required section heading. Do NOT add preambles like "{preamble}".',
        f'- If a block starting with "{delta_label}" is present and is relevant, place it at the top before the full report.' if language == 'en' else f'- If a block starting with "{delta_label}" is present, put this delta summary at the very top before the full report.',
        f'- {ladder_instruction}',
        f'- End with 2-3 natural follow-up suggestions under "{follow_up_label}" based on triggers, risks, or inventory hints in context.' if language == 'en' else f'- End with 2-3 natural follow-up suggestions under "{follow_up_label}" based on triggers/risks/inventory hints in context.',
        '- If the question asks where a price comes from, answer source-trace directly first (source table/feed + product + date + value), then add brief context.',
        '- Scope: focus on China market context only. If external/global info appears, use it only when it directly affects China pricing and explicitly state the China transmission path.',
        '- Treat INTERNAL DATABASE LIVE DATA and REAL MARKET DATA from MySQL as the only authoritative current-price sources.',
        '- Treat retrieved vector knowledge as supporting context, not as a substitute for missing current MySQL prices.',
        f'- {current_price_rule}',
        '- Prefer China region entities, China grades, and China trading windows when multiple interpretations are possible.',
        '- If pricing decision data is provided, cite the exact recommended price and express the Stage-4 output as pricing basis plus adjustment logic, not as a raw formula label.',
        '- If you explain a move using news/event intelligence, you must cite the corresponding event headline/source/date from the provided context and ensure it also appears in References.',
        f'- {no_event_rule}',
        '- Keep observed facts separate from forecasts: scenarios, possible reversals, and trigger conditions must be phrased as inference, not as established fact.',
        '- Do NOT generate generic or template-like content. Every statement must be grounded in the data above.',
        '- Your analysis should demonstrate that you have read and understood the full context.',
    ]
    if language_purity_rule:
        lines.insert(1, f'- {language_purity_rule}')
    return "\n".join(lines)


class PromptAssembler:
    """
    Assembles the complete prompt for the LLM based on the modular components.
    """

    def __init__(self):
        pass

    def assemble_system_prompt(
        self,
        task_type: TaskType,
        language: str = "zh",
        direct_explanation: bool = False,
        response_mode: str = "full_report",
        erp_hybrid_analysis: bool = False,
    ) -> str:
        """
        Constructs the system prompt (Role + Task + Reasoning + Output).
        """
        role = get_role_prompt(language=language)
        task = get_task_prompt(task_type, language=language)
        reasoning = get_reasoning_prompt()
        if direct_explanation:
            output = get_follow_up_explanation_output_prompt(language=language)
        elif response_mode == "conversational":
            output = get_conversational_output_prompt(language=language)
        elif response_mode == "concise":
            output = get_concise_output_prompt(language=language)
        elif response_mode == "structured":
            output = get_structured_output_prompt(language=language)
        elif response_mode == "executive_brief":
            output = get_executive_brief_output_prompt(language=language)
        elif task_type == TaskType.BOSS_BRIEFING:
            output = get_executive_memo_output_prompt(language=language)
        elif task_type == TaskType.ERP_DATA_QUERY and erp_hybrid_analysis:
            output = get_executive_brief_output_prompt(language=language)
        else:
            output = get_output_prompt(language=language)

        return f"{role}\n\n{task}\n\n{reasoning}\n\n{output}"

    def assemble_user_prompt(
        self, context_data: Dict, user_query: str, language: str = "zh"
    ) -> str:
        """
        Constructs the user prompt (Context + Specific Query).
        """
        context = get_context_prompt(context_data)
        instructions = _build_user_instructions(language)

        return f"""
{context}

USER QUERY:
"{user_query}"

{instructions}

Based on the provided context and your role, please analyze and provide the required decision report.
"""
