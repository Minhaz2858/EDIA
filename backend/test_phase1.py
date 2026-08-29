from types import SimpleNamespace

import backend.src.engine.decision_graph as decision_graph
from backend.src.engine.prompt_assembler import PromptAssembler
from backend.src.nlu.intent_parser import _fast_intent
from backend.src.prompts.task_prompt import TaskType
from backend.src.nlu.types import TaskType as NluTaskType
from backend.src.service.types import DecisionReport
from backend.src.skills.response.action_clarity import ActionClaritySkill
from backend.src.tools.db_capability_registry import (
    looks_like_database_backed_question,
    resolve_database_capability,
    should_bypass_database_capability_discovery,
    should_use_database_capability_discovery,
)
from backend.src.tools import erp_tools


def test_direct_explanation_prompt_uses_follow_up_format():
    assembler = PromptAssembler()

    system_prompt = assembler.assemble_system_prompt(
        TaskType.MARKET_ANALYSIS_ONLY,
        language="en",
        direct_explanation=True,
    )

    assert "FOLLOW-UP EXPLANATION FORMAT" in system_prompt
    assert "do NOT produce the full 5-section report" in system_prompt


def test_boss_briefing_prompt_uses_executive_memo_format():
    assembler = PromptAssembler()

    system_prompt = assembler.assemble_system_prompt(
        TaskType.BOSS_BRIEFING,
        language="en",
        response_mode="full_report",
    )

    assert "professional executive memo" in system_prompt
    assert "## Executive View" in system_prompt
    assert "## Pricing Structure" in system_prompt


def test_chinese_erp_prompt_uses_localized_task_rules():
    assembler = PromptAssembler()

    system_prompt = assembler.assemble_system_prompt(
        TaskType.ERP_DATA_QUERY,
        language="zh",
        response_mode="structured",
    )

    assert "任务类型：内部业务数据查询" in system_prompt
    assert "FAMOUNT" in system_prompt
    assert "总销量×加权均价" in system_prompt
    assert "TASK TYPE: Internal Business Data Query" not in system_prompt


def test_fast_intent_keeps_what_if_action_query_out_of_boss_briefing():
    query = (
        "If Japanese Naphtha jumps sharply over 7 days, explain the supply-demand driver, "
        "the transmission risk, and what action we should take this week."
    )

    task_type, confidence = _fast_intent(query)

    assert task_type == NluTaskType.WHAT_IF_SCENARIO
    assert confidence >= 0.85


def test_fast_intent_still_detects_explicit_briefing_requests():
    query = (
        "Generate a weekly executive briefing covering supply, demand, inventories, "
        "and pricing structure for this week."
    )

    task_type, confidence = _fast_intent(query)

    assert task_type == NluTaskType.BOSS_BRIEFING
    assert confidence >= 0.9


def test_fast_intent_detects_price_move_action_query_as_decision():
    query = (
        "C5 Petroleum Resin moved +17.1% in 7 days. Explain the supply-demand driver, "
        "the pricing risk, and what action we should take this week."
    )

    task_type, confidence = _fast_intent(query)

    assert task_type == NluTaskType.MARKET_ANALYSIS_DECISION
    assert confidence >= 0.8


def test_chinese_ops_impact_query_prefers_decision_and_full_report_mode():
    query = "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。"

    task_type, confidence = _fast_intent(query)
    mode = decision_graph._infer_response_mode(
        query,
        NluTaskType.MARKET_ANALYSIS_DECISION,
        False,
    )

    assert task_type == NluTaskType.MARKET_ANALYSIS_DECISION
    assert confidence >= 0.8
    assert mode == "full_report"


def test_chinese_decision_report_missing_sections_are_recovered_deterministically():
    state = {
        "out_of_scope_detected": False,
        "response_mode": "full_report",
        "explanation_mode": False,
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。",
        "graph_product_id": "isoprene",
        "tool_info": (
            "INTERNAL DATABASE LIVE DATA:\n"
            "异戊二烯: 17,104 元/吨 (+3.20%) [src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-14]"
        ),
        "diagnosis": {
            "root_cause": "装置开停工扰动导致供应节奏变化",
            "chain_impact": ["乙烯负荷变化 → C5收率变化 → 异戊二烯现货偏紧"],
            "risks": ["下游接货不连续导致传导折损"],
        },
        "forecast": {
            "trend": "↗上涨",
            "confidence": "中",
            "scenarios": {
                "3d": {"base": {"low": 16900, "high": 17350}},
                "7d": {"base": {"low": 16750, "high": 17550}},
                "30d": {"base": {"low": 16400, "high": 17900}},
            },
        },
        "pricing_decision": {
            "recommended_price": 17180,
            "price_range": [17050, 17350],
            "action": "谨慎上调",
            "reasoning": "供应端扰动下建议先小步试探并观察成交反馈",
            "triggers": [
                {"condition": "连续两日成交放量", "action": "上调至17300"},
                {"condition": "下游拒单增多", "action": "回调至17100"},
            ],
        },
        "user_context": SimpleNamespace(product="异戊二烯", period="本周"),
    }

    fragmented = "当前主流价格与环比涨跌（已验证事实）\n市场诊断与关键驱动因素（基于数据推断）"
    recovered = decision_graph._apply_deterministic_guardrails(fragmented, state)

    assert "## 📊 01 · 市场现状" in recovered
    assert "## 🔍 02 · 关键驱动因素" in recovered
    assert "## 📈 03 · 价格预测" in recovered
    assert "## 💡 04 · 定价决策" in recovered
    assert "## ⚠️ 05 · 风险预警" in recovered
    assert "## ❓ 06 · 建议追问" in recovered


def test_chinese_decision_report_recovers_missing_canonical_section_06():
    state = {
        "out_of_scope_detected": False,
        "response_mode": "full_report",
        "explanation_mode": False,
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。",
        "graph_product_id": "isoprene",
        "tool_info": (
            "INTERNAL DATABASE LIVE DATA:\n"
            "异戊二烯: 17,104 元/吨 (+3.20%) [src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-14]"
        ),
        "diagnosis": {"root_cause": "装置开停工扰动导致供应节奏变化"},
        "forecast": {
            "trend": "↗上涨",
            "confidence": "中",
            "scenarios": {
                "3d": {"base": {"low": 16900, "high": 17350}},
                "7d": {"base": {"low": 16750, "high": 17550}},
                "30d": {"base": {"low": 16400, "high": 17900}},
            },
        },
        "pricing_decision": {
            "recommended_price": 17180,
            "price_range": [17050, 17350],
            "action": "谨慎上调",
        },
        "user_context": SimpleNamespace(product="异戊二烯", period="本周"),
    }

    almost_complete = "\n".join(
        [
            "## 📊 01 · 市场现状",
            "示例内容",
            "## 🔍 02 · 关键驱动因素",
            "示例内容",
            "## 📈 03 · 价格预测",
            "示例内容",
            "## 💡 04 · 定价决策",
            "示例内容",
            "## ⚠️ 05 · 风险预警",
            "示例内容",
        ]
    )

    recovered = decision_graph._apply_deterministic_guardrails(almost_complete, state)

    assert "## ❓ 06 · 建议追问" in recovered


def test_chinese_ops_impact_generic_report_is_rewritten_to_professional_structure():
    state = {
        "out_of_scope_detected": False,
        "response_mode": "full_report",
        "explanation_mode": False,
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。",
        "graph_product_id": "isoprene",
        "tool_info": (
            "INTERNAL DATABASE LIVE DATA:\n"
            "异戊二烯: 17,104 元/吨 (+3.20%) [src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-14]\n"
            "双环戊二烯: 8,960 元/吨 (+0.00%) [src=market_prices|product_id=dcpd|price_type=east_china|date=2026-04-14]"
        ),
        "diagnosis": {
            "root_cause": "乙烯/C5装置停开车扰动导致供应节奏变化",
            "chain_impact": [
                "乙烯负荷变化 → C5收率变化 → 异戊二烯现货偏紧",
                "C5副产物流变化 → 双环戊二烯供给扰动 → 下游采购节奏波动",
            ],
            "risks": ["若复产兑现快于预期，价格上行动能将被削弱"],
            "market_regime": "neutral",
        },
        "forecast": {
            "trend": "震荡偏强",
            "confidence": "中",
            "scenarios": {
                "3d": {"base": {"low": 16900, "high": 17350}},
                "7d": {"base": {"low": 16750, "high": 17550}},
                "30d": {"base": {"low": 16400, "high": 17900}},
            },
        },
        "pricing_decision": {
            "recommended_price": 17180,
            "price_range": [17050, 17350],
            "triggers": [
                {"condition": "复产延后且成交放量", "action": "上调报价重心"},
                {"condition": "下游拒单增多", "action": "回调至防守区间"},
            ],
        },
        "user_context": SimpleNamespace(product="异戊二烯", period="本周"),
    }

    generic = "\n".join(
        [
            "### 1. 当前主流价格与环比涨跌（已验证事实）",
            "### 2. 市场诊断与关键驱动因素（基于数据推断）",
            "### 4. 近期展望与风险（推断与情景）",
        ]
    )

    recovered = decision_graph._apply_deterministic_guardrails(generic, state)

    assert "## 📊 01 · 市场现状" in recovered
    assert "## 🔍 02 · 关键驱动因素" in recovered
    assert "## 📈 03 · 价格预测" in recovered
    assert "## 💡 04 · 定价决策" in recovered
    assert "### A. 库存动作" in recovered
    assert "### C. 生产/排产动作" in recovered
    assert "异戊二烯" in recovered
    assert "双环戊二烯" in recovered


def test_conversational_follow_up_with_history_routes_to_parallel_fetch():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.CONVERSATIONAL, confidence=0.9),
        "user_query": "explain me indetails",
        "conversation_history": [
            {
                "role": "user",
                "content": "Based on the China market database, what are the main price moves for Cracked C5 and isoprene this week?",
            },
            {
                "role": "assistant",
                "content": "China market database lookup: this week's mainstream prices and WoW changes",
            },
        ],
        "is_valid": True,
    }

    route = decision_graph._route_after_validation(state)
    assert route == "parallel_fetch"


def test_english_simple_lookup_comparison_returns_direct_market_answer():
    state = {
        "user_query": "Based on the China market database, what are the main price moves for Cracked C5 and isoprene this week, and what changed week over week?",
        "tool_info": (
            "INTERNAL DATABASE LIVE DATA:\n"
            "Cracked C5: 7,700 元/吨 (-2.53%) [src=market_prices|product_id=cracked_c5|price_type=east_china|date=2026-04-14]\n"
            "Isoprene: 17,104 元/吨 (+0.00%) [src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-14]"
        ),
    }

    report = decision_graph._build_simple_lookup_report(state)

    assert "Direct answer:" in report
    assert "What changed week over week:" in report
    assert "Market read:" in report
    assert "What this means (quick interpretation):" not in report


def test_reasoning_required_market_comparison_bypasses_simple_lookup_route():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.SIMPLE_LOOKUP, confidence=0.95),
        "user_query": "Based on the China market database, what are the main price moves for Cracked C5 and isoprene this week, and what changed week over week?",
        "user_context": SimpleNamespace(product="Cracked C5 and isoprene", period="this week"),
        "is_valid": True,
    }

    route = decision_graph._route_after_validation(state)

    assert route == "parallel_fetch"


def test_reasoning_required_market_comparison_skips_simple_lookup_short_circuit():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.SIMPLE_LOOKUP, confidence=0.95),
        "user_query": "Based on the China market database, what are the main price moves for Cracked C5 and isoprene this week, and what changed week over week?",
        "user_context": SimpleNamespace(product="Cracked C5 and isoprene", period="this week"),
        "is_valid": True,
        "resolved_chat_mode": "full_report",
        "mode_reason": "test",
    }

    out = decision_graph._node_short_circuit_check(state)

    assert out == {}


def test_streaming_pref_disables_fast_price_move_action_shortcut():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.WHAT_IF_SCENARIO, confidence=0.95),
        "user_query": "If Japanese Naphtha jumps sharply over 7 days, explain the supply-demand driver, the transmission risk, and what action we should take this week.",
        "user_prefs": {"prefer_token_streaming": True},
    }

    assert decision_graph._use_fast_price_move_action_path(state) is False


def test_briefing_query_without_live_rows_returns_fallback_briefing():
    state = {
        "user_query": "请基于本周中国市场数据库，生成一份包含价格变化、供需信号和下周建议动作的高层周报。",
        "tool_info": "INTERNAL DATABASE LIVE DATA:\nNo real MySQL price data available",
        "perception_snapshot": {
            "reference_date": "2026-04-14",
            "products": {
                "isoprene": {
                    "name_en": "Isoprene",
                    "name_cn": "异戊二烯",
                    "latest_price": 17104,
                    "unit": "元/吨",
                    "change_7d_pct": 0.0,
                    "latest_date": "2026-04-14",
                },
                "cracked_c5": {
                    "name_en": "Cracked C5",
                    "name_cn": "裂解C5",
                    "latest_price": 7700,
                    "unit": "元/吨",
                    "change_7d_pct": -2.53,
                    "latest_date": "2026-04-14",
                },
            },
        },
        "diagnosis": {
            "root_cause": "供应端扰动与下游接货节奏分化并存",
        },
        "forecast": {
            "trend": "震荡",
        },
        "pricing_decision": {
            "action": "稳价去库存",
            "triggers": [
                {
                    "condition": "下游补库放量",
                    "action": "小步上调报价",
                }
            ],
        },
        "rag_info": (
            "RECENT WEEKLY REPORTS:\n"
            "[Weekly 2026W15 | 2026-04-14 | forecast:sideways | action:hold]\n"
            "异戊二烯本周成交平稳，供应端检修扰动有限。\n\n"
            "[Weekly 2026W15 | 2026-04-14 | forecast:down | action:defensive]\n"
            "裂解C5价格重心下移，下游补库意愿一般。"
        ),
    }

    report = decision_graph._build_simple_lookup_report(state)

    assert "高层周报（降级模式）" in report
    assert "下周建议动作" in report
    assert "数据缺口与可信度" in report
    assert "周报证据摘录" in report
    assert "证据：diagnosis:root_cause" in report
    assert "证据台账" in report


def test_ops_impact_query_not_misclassified_as_multi_product_lookup():
    query = "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。"
    is_lookup = decision_graph._is_multi_product_price_delta_query(query, None)
    assert is_lookup is False


def test_direct_market_price_lookup_routes_to_tool_execution():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_ONLY, confidence=0.95),
        "user_query": "最近一周异戊二烯市场价格怎么样",
        "user_context": SimpleNamespace(product=None, period="最近一周"),
        "is_valid": True,
    }

    route = decision_graph._route_after_validation(state)

    assert route == "tool_execution"


def test_tool_execution_resolves_direct_market_product_from_raw_query(monkeypatch):
    captured = {}

    def _fake_get_db_price(query, change_days=1, language="zh"):
        captured["query"] = query
        captured["change_days"] = change_days
        return "异戊二烯 (Isoprene): 17104 元/吨 (7d:+0.00%) [src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-29]"

    monkeypatch.setattr(decision_graph, "get_db_price", _fake_get_db_price)

    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_ONLY, confidence=0.95),
        "user_query": "最近一周异戊二烯市场价格怎么样",
        "user_context": SimpleNamespace(product=None, period="最近一周"),
        "user_prefs": {"language": "zh"},
    }

    out = decision_graph._node_tool_execution(state)

    assert captured == {"query": "isoprene", "change_days": 7}
    assert "INTERNAL DATABASE LIVE DATA" in out["tool_info"]
    assert "异戊二烯" in out["tool_info"]


def test_direct_market_price_lookup_short_circuits_stream_report():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_ONLY, confidence=0.95),
        "user_query": "最近一周异戊二烯市场价格怎么样",
        "user_context": SimpleNamespace(product=None, period="最近一周"),
        "tool_info": (
            "INTERNAL DATABASE LIVE DATA:\n"
            "异戊二烯 (Isoprene): 17104 元/吨 (7d:+0.00%) "
            "[src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-29]"
        ),
        "resolved_chat_mode": "concise",
        "mode_reason": "test",
    }

    out = decision_graph._node_short_circuit_check(state)

    assert "根据内部数据库实时数据" in out["report"].raw_markdown
    assert "异戊二烯" in out["report"].raw_markdown
    assert "7日环比涨跌" in out["report"].raw_markdown


def test_short_circuit_does_not_emit_simple_lookup_for_ops_impact_decision_query():
    query = "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。"
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION, confidence=0.9),
        "user_query": query,
        "user_context": SimpleNamespace(product="异戊二烯, 双环戊二烯", period="本周"),
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
    }

    out = decision_graph._node_short_circuit_check(state)
    assert out == {}


def test_assembler():
    assembler = PromptAssembler()

    # Test System Prompt
    system_prompt = assembler.assemble_system_prompt(TaskType.MARKET_ANALYSIS_DECISION)
    print("--- SYSTEM PROMPT ---")
    print(system_prompt)
    print("\n")

    # Test User Prompt
    context = {
        "product": "C5",
        "period": "Q3 2024",
        "market_condition": "Volatile",
        "goal": "Maximize Profit",
    }
    user_query = "What should be our pricing strategy for Q3?"
    user_prompt = assembler.assemble_user_prompt(context, user_query)
    print("--- USER PROMPT ---")
    print(user_prompt)


def test_executive_weekly_request_detection_query_markers():
    assert decision_graph._is_executive_weekly_request(
        "请给管理层做本周经营周报，包含价格预测区间和建议。",
        task_type=NluTaskType.ERP_DATA_QUERY,
        response_mode="structured",
    )
    assert not decision_graph._is_executive_weekly_request(
        "What is the current isoprene price?",
        task_type=NluTaskType.SIMPLE_LOOKUP,
        response_mode="concise",
    )


def test_executive_weekly_request_detection_boss_task_type():
    assert decision_graph._is_executive_weekly_request(
        "quick summary",
        task_type=NluTaskType.BOSS_BRIEFING,
        response_mode="concise",
    )


def test_broad_market_research_prompt_stays_in_scope_and_portfolio_mode():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.BOSS_BRIEFING),
        "graph_product_ids": [],
        "user_query": "Produce a deep market research note covering supply, demand, inventories, and pricing structure.",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
    }

    assert decision_graph._should_short_circuit_out_of_scope(state) is False
    assert decision_graph._looks_like_portfolio_query(
        state["user_query"],
        state["user_context"],
    ) is True


def test_broad_market_research_prompt_classifies_as_report_or_deep_research():
    mode, reason = decision_graph._classify_chat_mode(
        "Produce a deep market research note covering supply, demand, inventories, and pricing structure.",
        user_prefs={},
        task_type=NluTaskType.BOSS_BRIEFING,
        explanation_mode=False,
    )

    assert mode in {"report", "deep_research"}
    assert reason in {"deep_research_markers", "report_markers", f"task_type:{NluTaskType.BOSS_BRIEFING.value}"}


def test_llm_brain_resolve_product_overrides_with_external_plan():
    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            return (
                '{"graph_product_ids":["isoprene","piperylene"],'
                '"primary_product_id":"isoprene",'
                '"portfolio_mode":true,'
                '"out_of_scope_detected":false,'
                '"response_mode":"full_report",'
                '"chat_mode":"deep_research",'
                '"reason":"broad portfolio market request"}'
            )

    node = decision_graph._make_node_resolve_product(FakeLLM())
    state = {
        "user_query": "Produce a deep market research note covering supply, demand, inventories, and pricing structure.",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_ONLY),
        "perception_snapshot": {},
        "user_prefs": {"llm_brain_mode": True},
    }

    out = node(state)

    assert out["graph_product_id"] == "isoprene"
    assert out["graph_product_ids"] == ["isoprene", "piperylene"]
    assert out["portfolio_mode"] is True
    assert out["out_of_scope_detected"] is False
    assert out["response_mode"] == "full_report"
    assert out["resolved_chat_mode"] == "deep_research"
    assert out["mode_reason"] == "llm_brain_router"


def test_erp_retrieval_uses_briefing_for_executive_weekly(monkeypatch):
    captured = {}

    def _fake_build_erp_context(product_id, period, query_type, user_query):
        captured["query_type"] = query_type
        return "ok"

    monkeypatch.setattr(decision_graph, "build_erp_context", _fake_build_erp_context)

    state = {
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "Generate a weekly executive briefing for this week with pricing recommendation.",
        "response_mode": "structured",
        "user_prefs": {"erp_precomputed_context_enabled": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert out["erp_data"] == "ok"
    assert captured["query_type"] == "briefing"


def test_strict_mode_routes_erp_query_to_erp_retrieval(monkeypatch):
    monkeypatch.setattr(decision_graph, "_strict_kimi_only_mode", lambda state=None: True)

    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周戊烷发泡剂的销售情况怎么样",
        "is_valid": True,
    }

    assert decision_graph._route_after_validation(state) == "erp_data_retrieval"


def test_erp_retrieval_resolves_product_from_raw_query(monkeypatch):
    captured = {}

    def _fake_build_erp_context(product_id, period, query_type, user_query):
        captured["product_id"] = product_id
        return "ok"

    monkeypatch.setattr(decision_graph, "build_erp_context", _fake_build_erp_context)

    state = {
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周戊烷发泡剂的销售情况怎么样",
        "response_mode": "concise",
        "user_prefs": {"erp_precomputed_context_enabled": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert out["erp_data"] == "ok"
    assert captured["product_id"] == "blowing_agent"


def test_erp_retrieval_queries_portfolio_for_other_products_excluding_focal(monkeypatch):
    captured = {}

    def _fake_build_erp_context(product_id, period, query_type, user_query):
        captured["product_id"] = product_id
        captured["query_type"] = query_type
        return "ok"

    monkeypatch.setattr(decision_graph, "build_erp_context", _fake_build_erp_context)

    state = {
        "user_context": SimpleNamespace(product="异戊二烯", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "除了异戊二烯外，还能和我说下其他产品的销售情况吗",
        "response_mode": "concise",
        "user_prefs": {"erp_precomputed_context_enabled": True},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert out["erp_data"] == "ok"
    assert captured["product_id"] is None
    assert captured["query_type"] == "auto"


def test_erp_retrieval_does_not_inherit_product_for_all_products_revenue(monkeypatch):
    captured = {}

    def _fake_build_erp_context(product_id, period, query_type, user_query):
        captured["product_id"] = product_id
        return "ok"

    monkeypatch.setattr(decision_graph, "build_erp_context", _fake_build_erp_context)

    state = {
        "user_context": SimpleNamespace(product="戊烷发泡剂", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "I would like to know the total sales revenue of the products for this week.",
        "response_mode": "concise",
        "user_prefs": {"erp_precomputed_context_enabled": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert out["erp_data"] == "ok"
    assert captured["product_id"] is None


def test_erp_retrieval_does_not_inherit_product_for_all_products_detail(monkeypatch):
    captured = {}

    def _fake_build_erp_context(product_id, period, query_type, user_query):
        captured["product_id"] = product_id
        return "ok"

    monkeypatch.setattr(decision_graph, "build_erp_context", _fake_build_erp_context)

    state = {
        "user_context": SimpleNamespace(product="戊烷发泡剂", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "请给我本周产品销售明细数据",
        "response_mode": "concise",
        "user_prefs": {"erp_precomputed_context_enabled": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert out["erp_data"] == "ok"
    assert captured["product_id"] is None


def test_other_products_excluding_query_is_portfolio_in_chinese_and_english():
    assert decision_graph._looks_like_portfolio_query(
        "除了异戊二烯外，还能和我说下其他产品的销售情况吗",
        SimpleNamespace(product="异戊二烯"),
    ) is True
    assert decision_graph._looks_like_portfolio_query(
        "Besides isoprene, could you also tell me the sales performance of the other products?",
        SimpleNamespace(product="isoprene"),
    ) is True
    assert decision_graph._looks_like_portfolio_query(
        "Besides blowing agent, is there sales data available for other products?",
        SimpleNamespace(product="blowing agent"),
    ) is True


def test_fast_intent_classifies_other_products_excluding_sales_as_erp():
    assert _fast_intent("除了异戊二烯外，还能和我说下其他产品的销售情况吗")[0] == NluTaskType.ERP_DATA_QUERY
    assert _fast_intent("Besides isoprene, could you also tell me the sales performance of the other products?")[0] == NluTaskType.ERP_DATA_QUERY


def test_fast_intent_classifies_total_sales_revenue_as_erp():
    assert _fast_intent("I would like to know the total sales revenue of the products for this week.")[0] == NluTaskType.ERP_DATA_QUERY
    assert _fast_intent("本周产品总销售额是多少？")[0] == NluTaskType.ERP_DATA_QUERY


def test_total_sales_revenue_resolves_to_sales_summary_capability():
    assert resolve_database_capability("本周产品总销售额是多少？").capability_id == "erp_sales_summary"
    assert resolve_database_capability("Show total sales revenue for this week").capability_id == "erp_sales_summary"


def test_fast_intent_does_not_treat_generic_income_as_erp_revenue():
    assert _fast_intent("请分析本周收入压力和市场风险")[0] != NluTaskType.ERP_DATA_QUERY


def test_revenue_queries_use_structured_response_mode_in_chinese_and_english():
    assert decision_graph._infer_response_mode(
        "本周产品总销售额是多少？",
        NluTaskType.ERP_DATA_QUERY,
        False,
    ) == "structured"
    assert decision_graph._infer_response_mode(
        "I would like to know the total sales revenue of the products for this week.",
        NluTaskType.ERP_DATA_QUERY,
        False,
        {"default_format": "concise"},
    ) == "structured"


def test_deterministic_erp_sales_report_uses_real_summary():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周戊烷发泡剂的销售情况怎么样",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-04 to 2026-05-08):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)\n"
            "----------------------------------------------------------------------\n"
            "戊烷发泡剂                         222.17          5           7,319.65\n"
            "----------------------------------------------------------------------\n"
            "TOTAL                             222.17"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "222.17 吨" in report
    assert "7,319.65 元/吨" in report
    assert "无有效数据" not in report


def test_deterministic_erp_revenue_report_uses_famount_summary():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "I would like to know the total sales revenue of the products for this week.",
        "user_prefs": {"language": "en"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
            "--------------------------------------------------------------------------------------------\n"
            "戊烷发泡剂                         136.48          3           7,379.48      1,007,169.43\n"
            "异戊二烯                            87.25          2          16,550.00      1,443,987.50\n"
            "--------------------------------------------------------------------------------------------\n"
            "TOTAL                             223.73                                      2,451,156.93"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "## ERP Sales Revenue" in report
    assert "2,451,156.93 CNY" in report
    assert "Revenue source field" in report


def test_deterministic_erp_sales_detail_report_lists_product_rows():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "请给我本周产品销售明细数据",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
            "--------------------------------------------------------------------------------------------\n"
            "戊烷发泡剂                         136.48          3           7,379.48      1,007,169.43\n"
            "异戊二烯                            87.25          2          16,550.00      1,443,987.50\n"
            "--------------------------------------------------------------------------------------------\n"
            "TOTAL                             223.73                                      2,451,156.93"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "## ERP销售明细" in report
    assert "| 戊烷发泡剂 |" in report
    assert "| 异戊二烯 |" in report
    assert "ERP销售额合计：2,451,156.93 元" in report


def test_deterministic_erp_sales_detail_report_triggered_by_list_keyword():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "上周销售情况，输出列表和图表，与上上周对比",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-17):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
            "--------------------------------------------------------------------------------------------\n"
            "戊烷发泡剂                         858.37          7           9,264.10     27,843,475.83\n"
            "异戊二烯                            87.25          2          16,550.00      1,443,987.50\n"
            "--------------------------------------------------------------------------------------------\n"
            "TOTAL                             945.62                                     29,287,463.33"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "## ERP销售明细" in report
    assert "| 戊烷发泡剂 |" in report
    assert "| 异戊二烯 |" in report
    assert "ERP销售额合计：29,287,463.33 元" in report


def test_deterministic_erp_sales_detail_report_triggered_by_chart_keyword():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "Show me last week's sales chart by product",
        "user_prefs": {"language": "en"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-17):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
            "--------------------------------------------------------------------------------------------\n"
            "Raffinate C5                       858.37          7           9,264.10     27,843,475.83\n"
            "Isoprene                            87.25          2          16,550.00      1,443,987.50\n"
            "--------------------------------------------------------------------------------------------\n"
            "TOTAL                             945.62                                     29,287,463.33"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "## ERP Sales Detail" in report
    assert "| Raffinate C5 |" in report
    assert "| Isoprene |" in report
    assert "Total revenue: 29,287,463.33 CNY" in report


def test_all_products_sales_detail_query_recognizes_list_chart_compare():
    """Regression: queries with 列表/图表/对比 but no explicit product should trigger all-products mode."""
    query = "上周销售情况，输出列表和图表，与上上周对比"
    assert decision_graph._looks_like_all_products_sales_detail_query(query) is True

    query_en = "Show me last week's sales list and chart compared to the week before"
    assert decision_graph._looks_like_all_products_sales_detail_query(query_en) is True

    # Explicit single-product query should NOT trigger all-products mode
    query_single = "戊烷发泡剂上周销售明细"
    assert decision_graph._looks_like_all_products_sales_detail_query(query_single) is False


def test_chinese_erp_sales_detail_short_circuits_in_report_mode():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "请给我本周产品销售明细数据",
        "response_mode": "deep_research",
        "resolved_chat_mode": "deep_research",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
            "--------------------------------------------------------------------------------------------\n"
            "戊烷发泡剂                         136.48          3           7,379.48      1,007,169.43\n"
            "异戊二烯                            87.25          2          16,550.00      1,443,987.50\n"
            "--------------------------------------------------------------------------------------------\n"
            "TOTAL                             223.73                                      2,451,156.93"
        ),
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["deterministic_erp_sales"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert "## ERP销售明细" in report.raw_markdown
    assert "ERP销售额合计：2,451,156.93 元" in report.raw_markdown


def test_chinese_order_detail_returns_sales_order_rows_not_guard():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "请给我本周订单明细",
        "response_mode": "deep_research",
        "resolved_chat_mode": "deep_research",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "erp_order_details": {
            "period_start": "2026-05-11",
            "period_end": "2026-05-13",
            "row_count": 1,
            "total_qty": 81.8,
            "total_revenue": 572389.38,
            "rows": [
                {
                    "order_no": "YSK-YXHT5-202605-0713",
                    "customer": "东莞新长桥塑料有限公司",
                    "plan_date": "2026-05-11",
                    "product": "戊烷发泡剂",
                    "qty_ton": 81.8,
                    "unit_price": 7700.0,
                    "revenue_cny": 572389.38,
                }
            ],
        },
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["deterministic_erp_order_details"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert "## ERP订单明细" in report.raw_markdown
    assert "YSK-YXHT5-202605-0713" in report.raw_markdown
    assert "东莞新长桥塑料有限公司" in report.raw_markdown
    assert "ERP明细数据可用性说明" not in report.raw_markdown
    assert report.enhancement_applied["database_capability"] == "erp_order_details"
    assert report.enhancement_applied["data_sensitivity"] == "erp_confidential"
    assert report.enhancement_applied["external_llm_blocked"] == "true"


def test_chinese_order_detail_synonym_returns_report_not_discovery():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="请提供 5 月 11 日至 5 月 12 日的订单详情。"),
        "user_query": "请提供 5 月 11 日至 5 月 12 日的订单详情。",
        "response_mode": "deep_research",
        "resolved_chat_mode": "deep_research",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "erp_order_details": {
            "period_start": "2026-05-11",
            "period_end": "2026-05-12",
            "row_count": 1,
            "total_qty": 54.68,
            "total_revenue": 341946.90,
            "rows": [
                {
                    "order_no": "YSK-YXHT5-202605-0712",
                    "customer": "东莞新长桥塑料有限公司",
                    "plan_date": "2026-05-11",
                    "product": "戊烷发泡剂",
                    "qty_ton": 54.68,
                    "unit_price": 6900.0,
                    "revenue_cny": 341946.90,
                }
            ],
        },
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["database_capability"] == "erp_order_details"
    assert report.enhancement_applied["deterministic_erp_order_details"] == "true"
    assert "## ERP订单明细" in report.raw_markdown
    assert "### 核心摘要" in report.raw_markdown
    assert "### 订单明细" in report.raw_markdown
    assert "### 口径说明" in report.raw_markdown
    assert "数据库能力发现" not in report.raw_markdown


def test_database_capability_registry_maps_order_detail_prompts():
    english = resolve_database_capability("Please provide this week's order details.")
    chinese = resolve_database_capability("请给我本周订单明细")
    chinese_synonym = resolve_database_capability("请提供 5 月 11 日至 5 月 12 日的订单详情。")

    assert english is not None
    assert english.capability_id == "erp_order_details"
    assert english.table == "erp_v_sale_orderentry"
    assert english.external_llm_allowed is False
    assert chinese is not None
    assert chinese.capability_id == "erp_order_details"
    assert chinese_synonym is not None
    assert chinese_synonym.capability_id == "erp_order_details"


def test_english_order_detail_returns_sales_order_rows_not_guard():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "Please provide this week's order details.",
        "response_mode": "deep_research",
        "resolved_chat_mode": "deep_research",
        "user_prefs": {"language": "en", "chat_mode": "deep_research"},
        "erp_order_details": {
            "period_start": "2026-05-11",
            "period_end": "2026-05-13",
            "row_count": 1,
            "total_qty": 87.25,
            "total_revenue": 11716814.16,
            "rows": [
                {
                    "order_no": "YSK-YXHT5-202604-0674",
                    "customer": "中国金山联合贸易有限责任公司",
                    "plan_date": "2026-05-13",
                    "product": "异戊二烯",
                    "qty_ton": 87.25,
                    "unit_price": 16550.0,
                    "revenue_cny": 11716814.16,
                }
            ],
        },
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["deterministic_erp_order_details"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert report.enhancement_applied["external_llm_blocked"] == "true"
    assert "## ERP Order Details" in report.raw_markdown
    assert "YSK-YXHT5-202604-0674" in report.raw_markdown
    assert "ERP Detail Data Availability" not in report.raw_markdown


def test_order_detail_explicit_month_day_range_overrides_current_week(monkeypatch):
    captured = {}

    def fake_query_sales_order_details(product_id=None, period=None, limit=200):
        captured["period"] = period
        return {
            "period_start": "2026-05-11",
            "period_end": "2026-05-12",
            "row_count": 1,
            "total_qty": 54.68,
            "total_revenue": 341946.90,
            "rows": [
                {
                    "order_no": "YSK-YXHT5-202605-0712",
                    "customer": "东莞新长桥塑料有限公司",
                    "plan_date": "2026-05-11",
                    "product": "戊烷发泡剂",
                    "qty_ton": 54.68,
                    "unit_price": 6900.0,
                    "revenue_cny": 341946.90,
                }
            ],
        }

    monkeypatch.setattr(decision_graph, "query_sales_order_details", fake_query_sales_order_details)
    state = {
        "user_context": SimpleNamespace(product=None, period=None),
        "user_query": "Please provide order details from 5-11 to 5-12.",
        "user_prefs": {"language": "en", "chat_mode": "deep_research"},
    }

    report = decision_graph._build_deterministic_erp_sales_order_detail_report(state)

    assert captured["period"] == "Please provide order details from 5-11 to 5-12."
    assert "2026-05-11 to 2026-05-12" in report
    assert "2026-05-14" not in report


def test_order_detail_uses_hermes_normalized_period_contract(monkeypatch):
    captured = {}

    def fake_query_sales_order_details(product_id=None, period=None, limit=200):
        captured["period"] = period
        return {
            "period_start": "2026-05-11",
            "period_end": "2026-05-12",
            "row_count": 1,
            "total_qty": 54.68,
            "total_revenue": 341946.90,
            "rows": [
                {
                    "order_no": "YSK-YXHT5-202605-0712",
                    "customer": "东莞新长桥塑料有限公司",
                    "plan_date": "2026-05-11",
                    "product": "戊烷发泡剂",
                    "qty_ton": 54.68,
                    "unit_price": 6900.0,
                    "revenue_cny": 341946.90,
                }
            ],
        }

    monkeypatch.setattr(decision_graph, "query_sales_order_details", fake_query_sales_order_details)
    state = {
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "Please provide order details for May 11th to May 12th.",
        "user_prefs": {"language": "en"},
        "hermes_plan": {
            "normalized_query": {
                "capability": "erp_order_details",
                "period_start": "2026-05-11",
                "period_end": "2026-05-12",
            }
        },
    }

    report = decision_graph._build_deterministic_erp_sales_order_detail_report(state)

    assert captured["period"] == "2026-05-11 to 2026-05-12"
    assert "2026-05-11 to 2026-05-12" in report


def test_erp_period_parser_supports_month_day_ranges(monkeypatch):
    from datetime import date

    monkeypatch.setattr(erp_tools, "_get_latest_erp_date", lambda: date(2026, 5, 14))

    start, end = erp_tools._resolve_period("Please provide order details from 5-11 to 5-12.")

    assert str(start) == "2026-05-11"
    assert str(end) == "2026-05-12"


def test_erp_period_parser_supports_english_month_name_ranges(monkeypatch):
    from datetime import date

    monkeypatch.setattr(erp_tools, "_get_latest_erp_date", lambda: date(2026, 5, 14))

    cases = [
        "Please provide order details for May 11th to May 12th.",
        "Please provide order details for May 11 to 12.",
        "Please provide order details from 11 May to 12 May.",
        "Please provide order details from May 11, 2026 to May 12, 2026.",
    ]

    for query in cases:
        start, end = erp_tools._resolve_period(query)
        assert str(start) == "2026-05-11", query
        assert str(end) == "2026-05-12", query


def test_erp_period_parser_supports_spaced_chinese_month_day_ranges(monkeypatch):
    from datetime import date

    monkeypatch.setattr(erp_tools, "_get_latest_erp_date", lambda: date(2026, 5, 14))

    start, end = erp_tools._resolve_period("请提供 5 月 11 日至 5 月 12 日的订单详情。")

    assert str(start) == "2026-05-11"
    assert str(end) == "2026-05-12"


def test_order_detail_no_data_reports_available_range():
    state = {
        "user_query": "Please provide this week's order details.",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_prefs": {"language": "en", "chat_mode": "deep_research"},
        "erp_order_details": {
            "period_start": "2027-01-04",
            "period_end": "2027-01-10",
            "row_count": 0,
            "total_qty": 0.0,
            "total_revenue": 0.0,
            "available_count": 8,
            "available_start": "2026-05-11",
            "available_end": "2026-05-13",
            "rows": [],
        },
    }

    report = decision_graph._build_deterministic_erp_sales_order_detail_report(state)

    assert "No ERP sales order rows were found for 2027-01-04 to 2027-01-10" in report
    assert "Available ERP sales-order data exists from 2026-05-11 to 2026-05-13 (8 rows)" in report


def test_unmapped_database_question_returns_capability_discovery():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "Show current inventory stock records by product",
        "response_mode": "deep_research",
        "resolved_chat_mode": "deep_research",
        "user_prefs": {"language": "en", "chat_mode": "deep_research"},
        "database_discovery": {
            "query_terms": ["inventory", "stock", "product"],
            "candidates": [
                {
                    "table": "erp_v_stk_inventory",
                    "matched_columns": ["material_name", "inventory_qty"],
                    "row_count": 123,
                    "date_column": "updated_at",
                    "min_date": "2026-05-01",
                    "max_date": "2026-05-14",
                }
            ],
        },
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["database_capability_discovery"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert report.enhancement_applied["external_llm_blocked"] == "true"
    assert "## Database Capability Discovery" in report.raw_markdown
    assert "erp_v_stk_inventory" in report.raw_markdown
    assert "will not invent a business result" in report.raw_markdown


def test_piperylene_commercial_ops_query_returns_hybrid_advisory(monkeypatch):
    query = (
        "Piperylene 7d decrease +0.0% (current 9,300 CNY/ton). "
        "How should current inventory be handled, do quotes need to follow downward, "
        "and how should shipment pace be adjusted?"
    )

    assert decision_graph._is_commercial_ops_advisory_query(query) is True

    monkeypatch.setattr(
        decision_graph,
        "_load_commercial_ops_facts",
        lambda _state: {
            "product_id": "piperylene",
            "product_meta": {
                "name_en": "Piperylene",
                "name_cn": "间戊二烯",
                "latest_price": 9300,
                "latest_date": "2026-05-18",
                "change_7d_pct": 0.0,
                "unit": "ton",
            },
            "pricing": {
                "product_id": "piperylene",
                "latest_market_price": 9300,
                "recommended_price": 9250,
                "price_range": [9150, 9350],
                "action": "hold_price",
            },
            "inventory": {
                "found": True,
                "total_tons": 1500,
                "inventory_days": 28,
            },
            "sales": {
                "ref_date": "2026-05-18",
                "sales_7d": {"shipment_tons": 120, "order_rows": 3},
                "sales_14d": {"shipment_tons": 240, "order_rows": 6},
                "shipment_change_pct": 0.0,
            },
        },
    )

    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "user_context": SimpleNamespace(product="piperylene", period="Current Week"),
        "user_query": query,
        "response_mode": "analysis",
        "resolved_chat_mode": "analysis",
        "user_prefs": {"language": "en", "chat_mode": "analysis"},
    }

    out = decision_graph._node_short_circuit_check(state)
    report = out.get("report")

    assert report is not None
    assert report.enhancement_applied["hybrid_ops_advisory"] == "true"
    assert report.enhancement_applied["llm_synthesis"] == "facts_first"
    assert "Database Capability Discovery" not in report.raw_markdown
    assert "Commercial Ops Advisory" in report.raw_markdown
    assert "Inventory:" in report.raw_markdown
    assert "Quote:" in report.raw_markdown
    assert "Shipment pace:" in report.raw_markdown


def test_piperylene_commercial_ops_query_is_not_deterministic_erp_stream():
    from backend.server import _is_deterministic_erp_stream_request
    from backend.src.hermes.hermes_agent import build_hermes_plan

    query = (
        "Piperylene 7d decrease +0.0% (current 9,300 CNY/ton). "
        "How should current inventory be handled, do quotes need to follow downward, "
        "and how should shipment pace be adjusted?"
    )

    plan = build_hermes_plan(query).to_dict()

    assert _is_deterministic_erp_stream_request(query) is False
    assert "market" in plan["domains"]
    assert "database" not in plan["domains"]
    assert plan["privacy"]["external_llm_blocked"] == "false"
    assert "commercial_ops_advisory" in plan["capabilities"]


def test_llm_agent_intent_routes_commercial_advisory_without_keyword_match():
    from backend.src.hermes.hermes_agent import build_hermes_plan

    class FakePlannerLLM:
        def generate_json(self, _system_prompt, _user_prompt, route_hint=None, sanitized=None):
            return {
                "agent_intent": {
                    "intent": "commercial_ops_advisory",
                    "confidence": 0.93,
                    "product_ids": ["piperylene"],
                    "domains": ["market"],
                    "capabilities": ["commercial_ops_advisory"],
                    "data_required": [
                        "market_price",
                        "erp_inventory",
                        "erp_shipments",
                        "pricing_service",
                    ],
                    "answer_style": "business_memo",
                    "should_not_use": ["database_capability_discovery"],
                    "privacy": {
                        "data_sensitivity": "market_internal_mixed",
                        "external_llm_blocked": "false",
                        "answer_mode": "hybrid_commercial_ops_advisory",
                    },
                    "route_reason": "The user asks for a commercial action plan.",
                }
            }

    query = "Piperylene softened again. What is the best commercial move this week?"

    assert decision_graph._is_commercial_ops_advisory_query(query) is False

    plan = build_hermes_plan(query, llm_service=FakePlannerLLM()).to_dict()

    assert plan["normalized_query"]["agent_intent"]["source"] == "llm"
    assert plan["normalized_query"]["agent_intent"]["intent"] == "commercial_ops_advisory"
    assert "commercial_ops_advisory" in plan["capabilities"]
    assert "database_capability_discovery" not in plan["tool_sequence"]
    assert plan["privacy"]["external_llm_blocked"] == "false"


def test_agent_intent_prevents_database_discovery_answer(monkeypatch):
    monkeypatch.setattr(
        decision_graph,
        "_load_commercial_ops_facts",
        lambda _state: {
            "product_id": "piperylene",
            "product_meta": {
                "name_en": "Piperylene",
                "latest_price": 9300,
                "latest_date": "2026-05-18",
                "change_7d_pct": -0.5,
                "unit": "ton",
            },
            "pricing": {
                "product_id": "piperylene",
                "latest_market_price": 9300,
                "recommended_price": 9250,
                "price_range": [9150, 9350],
                "action": "hold_price",
            },
            "inventory": {"found": True, "total_tons": 1500, "inventory_days": 28},
            "sales": {
                "ref_date": "2026-05-18",
                "sales_7d": {"shipment_tons": 120},
                "sales_14d": {"shipment_tons": 240},
                "shipment_change_pct": 0.0,
            },
        },
    )
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product="piperylene", period="Current Week"),
        "user_query": "Piperylene softened again. What is the best commercial move this week?",
        "response_mode": "analysis",
        "resolved_chat_mode": "analysis",
        "user_prefs": {"language": "en", "chat_mode": "analysis"},
        "hermes_plan": {
            "normalized_query": {
                "agent_intent": {
                    "intent": "commercial_ops_advisory",
                    "product_ids": ["piperylene"],
                    "source": "llm",
                }
            },
            "capabilities": ["commercial_ops_advisory"],
        },
    }

    out = decision_graph._node_short_circuit_check(state)
    report = out.get("report")

    assert report is not None
    assert "Database Capability Discovery" not in report.raw_markdown
    assert "Commercial Ops Advisory" in report.raw_markdown


def test_unmapped_database_question_is_detected_without_exact_capability():
    query = "Show current inventory stock records by product"

    assert resolve_database_capability(query) is None
    assert looks_like_database_backed_question(query) is True


def test_china_market_database_price_query_bypasses_capability_discovery():
    query = "请基于中国市场数据库，说明本周裂解C5和异戊二烯的主流价格变化，以及周环比方向。"

    assert resolve_database_capability(query) is None
    assert should_bypass_database_capability_discovery(query) is True
    assert should_use_database_capability_discovery(query) is False
    assert looks_like_database_backed_question(query) is False


def test_china_market_database_price_query_is_not_deterministic_erp_stream():
    from backend.server import _deterministic_erp_stream_enhancements, _is_deterministic_erp_stream_request
    from backend.src.hermes.hermes_agent import build_hermes_plan

    query = "请基于中国市场数据库，说明本周裂解C5和异戊二烯的主流价格变化，以及周环比方向。"
    plan = build_hermes_plan(query).to_dict()

    assert _is_deterministic_erp_stream_request(query, plan) is False
    assert "database_capability_discovery" not in plan["capabilities"]
    assert "database_capability_discovery" not in plan["tool_sequence"]
    assert "market" in plan["domains"]
    assert _deterministic_erp_stream_enhancements(query).get("database_capability_discovery") != "true"


def test_sinopec_listing_price_query_bypasses_capability_discovery():
    query = "Has SINOPEC's monthly listing price been released? And how has it changed?"

    assert should_bypass_database_capability_discovery(query) is True
    assert should_use_database_capability_discovery(query) is False


def test_chinese_sinopec_listing_query_is_direct_market_lookup():
    query = "SINOPEC的月度挂牌价是否已公布？其变动方向如何"

    assert decision_graph.looks_like_sinopec_listing_query(query) is True
    assert decision_graph._is_direct_market_price_lookup(query, None) is True


def test_chinese_sinopec_listing_intent_is_simple_lookup():
    from backend.src.nlu.intent_parser import _fast_intent

    task_type, confidence = _fast_intent("SINOPEC的月度挂牌价是否已公布？其变动方向如何")

    assert task_type == NluTaskType.SIMPLE_LOOKUP
    assert confidence >= 0.88


def test_sinopec_listing_tool_execution_uses_sinopec_sh_price_type(monkeypatch):
    captured = []

    def _fake_get_db_price_for_type(query, price_type, change_days=30, language="zh"):
        captured.append((query, price_type, change_days))
        return (
            f"异戊二烯 (Isoprene)（上海石化挂牌价）: 12000 元/吨 (30d:+1.00%) "
            "[src=market_prices|product_id=isoprene|price_type=sinopec_sh|date=2026-05-01]"
        )

    monkeypatch.setattr(decision_graph, "get_db_price_for_type", _fake_get_db_price_for_type)

    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.SIMPLE_LOOKUP, confidence=0.9),
        "user_query": "SINOPEC的月度挂牌价是否已公布？其变动方向如何",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_prefs": {"language": "zh"},
    }

    out = decision_graph._node_tool_execution(state)

    assert captured
    assert all(item[1] == "sinopec_sh" for item in captured)
    assert "INTERNAL DATABASE LIVE DATA" in out["tool_info"]
    assert "sinopec_sh" in out["tool_info"]


def test_database_catalog_question_still_uses_capability_discovery():
    query = "老板问数据库里销售、库存、合同哪些数据目前能直接回答？"

    assert should_bypass_database_capability_discovery(query) is False
    assert should_use_database_capability_discovery(query) is True


def test_stream_enhancements_prioritize_unmapped_database_discovery_over_sales_heuristic():
    from backend.server import _deterministic_erp_stream_enhancements

    enhancements = _deterministic_erp_stream_enhancements("Show current inventory stock records by product")

    assert enhancements["database_capability_discovery"] == "true"
    assert enhancements["skip_finalizer"] == "true"
    assert enhancements["external_llm_blocked"] == "true"
    assert "deterministic_erp_sales" not in enhancements


def test_chinese_contract_detail_still_uses_guard_not_order_rows():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "请给我本周合同明细",
        "response_mode": "deep_research",
        "resolved_chat_mode": "deep_research",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["deterministic_erp_row_level_guard"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert "不会编造" in report.raw_markdown
    assert "ERP订单明细" not in report.raw_markdown


def test_erp_sales_summary_parser_supports_revenue_column():
    parsed = decision_graph._extract_erp_sales_summary_rows(
        "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
        "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
        "--------------------------------------------------------------------------------------------\n"
        "戊烷发泡剂                         136.48          3           7,379.48      1,007,169.43\n"
        "--------------------------------------------------------------------------------------------\n"
        "TOTAL                             136.48                                      1,007,169.43"
    )

    assert parsed["rows"][0]["revenue_cny"] == 1007169.43
    assert parsed["total_revenue"] == 1007169.43


def test_erp_sales_revenue_sql_uses_famount_sum_only():
    assert erp_tools.ERP_ORDER_REVENUE_SUM_SQL == "SUM(COALESCE(FAMOUNT, 0))"
    assert erp_tools.ERP_ORDER_REVENUE_LINE_SQL == "COALESCE(FAMOUNT, 0)"
    assert "FALLAMOUNT" not in erp_tools.ERP_ORDER_REVENUE_SUM_SQL
    assert "×" in erp_tools.ERP_REVENUE_BASIS_NOTE_ZH


def test_deterministic_erp_revenue_report_states_famount_basis():
    state = {
        "user_query": "本月销售额是多少",
        "user_prefs": {"language": "zh"},
    }
    summary = {
        "period_start": "2026-05-01",
        "period_end": "2026-05-13",
        "total_revenue": 1_000_000.0,
        "rows": [
            {
                "product": "异戊二烯",
                "revenue_cny": 1_000_000.0,
                "volume_ton": 100.0,
                "orders": 5,
            }
        ],
    }
    report = decision_graph._build_deterministic_erp_revenue_report(state, summary)
    assert "1,000,000.00" in report
    assert "FAMOUNT" in report
    assert "总销量×加权均价" in report


def test_deterministic_erp_sales_report_filters_excluded_product():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "除了异戊二烯外，还能和我说下其他产品的销售情况吗",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)\n"
            "----------------------------------------------------------------------\n"
            "异戊二烯                          87.25          2          16,550.00\n"
            "间戊二烯                          11.50          1           6,200.00\n"
            "----------------------------------------------------------------------\n"
            "TOTAL                              98.75"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "不含异戊二烯" in report
    assert "有。" in report
    assert "间戊二烯" in report
    assert "11.50" in report
    assert "| 异戊二烯 |" not in report


def test_deterministic_erp_sales_report_does_not_answer_excluded_only_row():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "除了异戊二烯外，还能和我说下其他产品的销售情况吗",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)\n"
            "----------------------------------------------------------------------\n"
            "异戊二烯                          87.25          2          16,550.00\n"
            "----------------------------------------------------------------------\n"
            "TOTAL                              87.25\n"
            "\n"
            "Period-over-period change: +222.7%"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "剔除异戊二烯后" in report
    assert "没有。" in report
    assert "其他产品在该期间没有ERP销售交易记录" in report
    assert "异戊二烯销量为 87.25 吨" not in report


def test_deterministic_erp_sales_report_filters_english_blowing_agent():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "Besides blowing agent, is there sales data available for other products?",
        "user_prefs": {"language": "en"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)\n"
            "----------------------------------------------------------------------\n"
            "戊烷发泡剂                         136.48          3           7,379.48\n"
            "异戊二烯                            87.25          2          16,550.00\n"
            "----------------------------------------------------------------------\n"
            "TOTAL                             223.73"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "Yes." in report
    assert "excluding Pentane Blowing Agent" in report
    assert "| 异戊二烯 |" in report
    assert "| 戊烷发泡剂 |" not in report


def test_other_products_excluding_sales_short_circuits_stream_report():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product="异戊二烯", period="Current Week"),
        "user_query": "除了异戊二烯外，还能和我说下其他产品的销售情况吗",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)\n"
            "----------------------------------------------------------------------\n"
            "异戊二烯                          87.25          2          16,550.00\n"
            "戊烷发泡剂                         136.48          3           7,379.48\n"
            "----------------------------------------------------------------------\n"
            "TOTAL                             223.73"
        ),
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["deterministic_erp_exclusion_sales"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert "有。" in report.raw_markdown
    assert "| 戊烷发泡剂 |" in report.raw_markdown


def test_erp_revenue_short_circuits_stream_report():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "user_query": "I would like to know the total sales revenue of the products for this week.",
        "user_prefs": {"language": "en", "chat_mode": "chat"},
        "resolved_chat_mode": "chat",
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-11 to 2026-05-13):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)  Revenue(CNY)\n"
            "--------------------------------------------------------------------------------------------\n"
            "戊烷发泡剂                         136.48          3           7,379.48      1,007,169.43\n"
            "--------------------------------------------------------------------------------------------\n"
            "TOTAL                             136.48                                      1,007,169.43"
        ),
    }

    out = decision_graph._node_short_circuit_check(state)

    report = out.get("report")
    assert report is not None
    assert report.enhancement_applied["deterministic_erp_sales"] == "true"
    assert report.enhancement_applied["skip_finalizer"] == "true"
    assert "## ERP Sales Revenue" in report.raw_markdown


def test_deep_research_erp_sales_report_preserves_verified_detail():
    detailed = "\n".join(
        [
            "## 01 · 市场现状",
            "本周（2026-05-04 至 2026-05-08）戊烷发泡剂销售出现明显下滑，销量为222.17吨，订单4笔。",
            "## 02 · 关键驱动因素",
            "本周加权成交均价为7,319.65元/吨，库存覆盖天数偏低，需要关注补货节奏。",
            "## 03 · 价格预测",
            "库存压力与成交均价共同指向短期报价韧性。",
            "## 04 · 定价决策",
            "建议结合客户优先级和库存覆盖天数进行报价管理。",
            "## 05 · 风险预警",
            "若生产计划未能及时补充，存在断供风险。",
        ]
        * 8
    )
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周戊烷发泡剂的销售情况怎么样",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "resolved_chat_mode": "deep_research",
        "response_mode": "full_report",
        "erp_data": (
            "ERP SALES SUMMARY (Current Week):\n"
            "Period: 2026-05-04 to 2026-05-08\n"
            "- 戊烷发泡剂: 222.17 tons, 4 orders, avg price 7,319.65 CNY/ton\n"
            "Total sales volume: 222.17 tons\n"
            "Period change: -68.7%"
        ),
    }

    report = decision_graph._apply_deterministic_guardrails(detailed, state)

    assert report.startswith(detailed)
    assert "## ERP销售情况" not in report


def test_deterministic_erp_sales_report_with_action_request_returns_scenarios():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "请基于ERP数据分析最近一周戊烷发泡剂销售情况，并给未来1-2周看跌/基准/看涨动作与触发条件。",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "resolved_chat_mode": "deep_research",
        "response_mode": "full_report",
        "erp_data": (
            "ERP SALES SUMMARY (2026-05-04 to 2026-05-08):\n"
            "Product                   Volume(ton)     Orders     Avg Price(CNY/ton)\n"
            "----------------------------------------------------------------------\n"
            "戊烷发泡剂                     221.25          4            7318.90\n"
            "----------------------------------------------------------------------\n"
            "TOTAL                       221.25\n"
            "\n"
            "Period-over-period change: -68.9%"
        ),
    }

    report = decision_graph._build_deterministic_erp_sales_report(state)

    assert "## 03 · 未来1-2周情景判断" in report
    assert "看跌动作" in report
    assert "基准动作" in report
    assert "看涨动作" in report


def test_action_clarity_skips_erp_data_query():
    report = DecisionReport(
        raw_markdown="## ERP销售情况\n\n销量为 222.17 吨。",
        task_type=NluTaskType.ERP_DATA_QUERY,
        context=SimpleNamespace(),
        status="success",
    )

    out = ActionClaritySkill().run(report, {})

    assert "## Action Triggers" not in out.raw_markdown
    assert out.enhancement_applied["action_clarity"] == "skipped_erp_lookup"


def test_erp_no_records_message_includes_available_range(monkeypatch):
    from datetime import date

    monkeypatch.setattr(
        erp_tools,
        "_query_material_date_range",
        lambda table_name, date_column, material_names: (2, date(2023, 10, 7), date(2023, 10, 7)),
    )

    message = erp_tools._format_no_period_records_with_availability(
        record_label="sales",
        material_names=("SIS",),
        requested_start=date(2026, 5, 4),
        requested_end=date(2026, 5, 8),
    )

    assert "AVAILABLE ERP SALES DATE RANGE for SIS: 2023-10-07 to 2023-10-07 (2 rows)." in message
    assert "CLOSEST AVAILABLE 5-DAY WINDOW: 2023-10-07 to 2023-10-07." in message


def test_deterministic_erp_availability_report_mentions_duration():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周SIS的销售情况怎么样",
        "user_prefs": {"language": "zh"},
        "erp_data": (
            "No sales records found for requested period 2026-05-04 to 2026-05-08.\n"
            "AVAILABLE ERP SALES DATE RANGE for SIS: 2023-10-07 to 2023-10-07 (2 rows).\n"
            "CLOSEST AVAILABLE 5-DAY WINDOW: 2023-10-07 to 2023-10-07.\n"
            "Use the closest available window or choose a period inside the available date range."
        ),
    }

    report = decision_graph._build_deterministic_erp_availability_report(state)

    assert "请求区间 2026-05-04 至 2026-05-08" in report
    assert "可用ERP销售历史范围为 2023-10-07 至 2023-10-07" in report


def test_deep_research_erp_availability_preserves_detailed_answer():
    detailed = "\n".join(
        [
            "## 01 · 市场现状",
            "请求区间 2026-05-04 至 2026-05-08 内，SIS 没有任何ERP销售记录。",
            "可用ERP销售历史范围为 2023-10-07 至 2023-10-07，共2条记录。",
            "最接近的可用5天窗口是 2023-10-07 至 2023-10-07。",
            "## 02 · 关键驱动因素",
            "由于缺乏近一周交易数据，驱动判断以缺口识别和数据完整性为主。",
            "## 03 · 价格预测",
            "短期仅能给出区间化情景，不给出单点预测。",
            "## 04 · 定价决策",
            "建议按看跌/基准/看涨三套动作执行，并绑定触发条件。",
            "## 05 · 风险预警",
            "若ERP映射或采集延迟持续，将导致定价动作滞后。",
        ]
        * 8
    )
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "请基于ERP分析最近一周SIS销售情况；若无数据用最近可用窗口继续，并给未来1-2周看跌/基准/看涨动作与触发条件。",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "resolved_chat_mode": "deep_research",
        "response_mode": "full_report",
        "erp_data": (
            "No sales records found for requested period 2026-05-04 to 2026-05-08.\n"
            "AVAILABLE ERP SALES DATE RANGE for SIS: 2023-10-07 to 2023-10-07 (2 rows).\n"
            "CLOSEST AVAILABLE 5-DAY WINDOW: 2023-10-07 to 2023-10-07.\n"
            "Use the closest available window or choose a period inside the available date range."
        ),
    }

    report = decision_graph._apply_deterministic_guardrails(detailed, state)

    assert report.startswith(detailed)
    assert "我可以直接按这个最近可用窗口展示" not in report


def test_deterministic_erp_availability_with_action_request_returns_scenarios():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "请基于ERP分析最近一周SIS销售情况；若无数据用最近可用窗口继续，并给未来1-2周看跌/基准/看涨动作与触发条件。",
        "user_prefs": {"language": "zh", "chat_mode": "deep_research"},
        "resolved_chat_mode": "deep_research",
        "response_mode": "full_report",
        "erp_data": (
            "No sales records found for requested period 2026-05-04 to 2026-05-08.\n"
            "AVAILABLE ERP SALES DATE RANGE for SIS: 2023-10-07 to 2023-10-07 (2 rows).\n"
            "CLOSEST AVAILABLE 5-DAY WINDOW: 2023-10-07 to 2023-10-07.\n"
            "Use the closest available window or choose a period inside the available date range."
        ),
    }

    report = decision_graph._build_deterministic_erp_availability_report(state)

    assert "## 03 · 未来1-2周情景判断" in report
    assert "看跌动作" in report
    assert "基准动作" in report
    assert "看涨动作" in report


def test_erp_retrieval_resolves_catalog_only_product_from_raw_query(monkeypatch):
    captured = {}

    def _fake_build_erp_context(product_id, period, query_type, user_query):
        captured["product_id"] = product_id
        return "No ERP sales data mapping for product 'hex'."

    monkeypatch.setattr(decision_graph, "build_erp_context", _fake_build_erp_context)

    state = {
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周工业己烷的销售情况怎么样",
        "response_mode": "concise",
        "user_prefs": {"erp_precomputed_context_enabled": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert out["erp_data"] == "No ERP sales data mapping for product 'hex'."
    assert captured["product_id"] == "hex"


def test_deterministic_erp_mapping_report_prevents_product_substitution():
    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "最近一周工业己烷的销售情况怎么样",
        "user_prefs": {"language": "zh"},
        "erp_data": "No ERP sales data mapping for product 'hex'.\n\nNo ERP data mapping for product 'hex'.",
    }

    report = decision_graph._build_deterministic_erp_mapping_report(state)

    assert "`hex`" in report
    assert "不会用其他产品的销量代替回答" in report


def test_erp_retrieval_defaults_to_precomputed_bundle_when_enabled(monkeypatch):
    monkeypatch.setattr(
        decision_graph,
        "_build_precomputed_erp_block",
        lambda state, product_id=None: "PRECOMPUTED ERP INSIGHT BUNDLE (DB CACHE):\n- status: ready",
    )
    monkeypatch.setattr(
        decision_graph,
        "build_erp_context",
        lambda product_id, period, query_type, user_query: "LIVE ERP CONTEXT SHOULD NOT RUN",
    )

    state = {
        "user_context": SimpleNamespace(product="isoprene", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "Summarize our internal ERP status this week.",
        "response_mode": "structured",
        "user_prefs": {"erp_precomputed_context_enabled": True, "live_data_only": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert "PRECOMPUTED ERP INSIGHT BUNDLE" in out["erp_data"]
    assert "LIVE ERP CONTEXT SHOULD NOT RUN" not in out["erp_data"]


def test_erp_retrieval_runs_live_context_when_explicit_detail_requested(monkeypatch):
    monkeypatch.setattr(
        decision_graph,
        "_build_precomputed_erp_block",
        lambda state, product_id=None: "PRECOMPUTED ERP INSIGHT BUNDLE (DB CACHE):\n- status: ready",
    )
    monkeypatch.setattr(
        decision_graph,
        "build_erp_context",
        lambda product_id, period, query_type, user_query: "LIVE ERP CONTEXT EXECUTED",
    )

    state = {
        "user_context": SimpleNamespace(product="isoprene", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "user_query": "Show detailed customer detail and contract detail rows from ERP this week.",
        "response_mode": "structured",
        "user_prefs": {"erp_precomputed_context_enabled": True, "live_data_only": False},
    }

    out = decision_graph._node_erp_data_retrieval(state)

    assert "PRECOMPUTED ERP INSIGHT BUNDLE" in out["erp_data"]
    assert "LIVE ERP CONTEXT EXECUTED" in out["erp_data"]


def test_precomputed_insight_block_uses_combined_bundle(monkeypatch):
    monkeypatch.setattr(
        decision_graph.insight_snapshot_service,
        "get_combined_bundle_cached",
        lambda product_ids=None, market_max_age_seconds=None, erp_max_age_seconds=None, async_refresh_on_miss=True: {
            "ok": True,
            "market": {
                "snapshot_id": "mkt-1",
                "generated_at": "2026-04-17T08:00:00",
                "age_seconds": 120,
                "stale": False,
            },
            "erp": {
                "snapshot_id": "erp-1",
                "generated_at": "2026-04-17T07:55:00",
                "age_seconds": 420,
                "stale": False,
            },
            "by_product": {
                "isoprene": {
                    "product_id": "isoprene",
                    "market": {
                        "product_id": "isoprene",
                        "latest_price": 17104.0,
                        "weekly_change_pct": 33.62,
                        "unit": "RMB/ton",
                        "as_of_date": "2026-04-14",
                        "narrative": "Isoprene strengthened sharply this week.",
                    },
                    "erp": {
                        "product_id": "isoprene",
                        "sales_30d_ton": 559.4,
                        "inventory_days": 5.9,
                        "risk_level": "critical",
                        "narrative": "Inventory coverage is critically low.",
                    },
                }
            },
            "refresh_enqueued": {"market": False, "erp": True},
        },
    )

    state = {
        "graph_product_ids": ["isoprene"],
        "user_context": SimpleNamespace(product="isoprene", period="Current Week"),
        "user_prefs": {},
    }

    block = decision_graph._build_precomputed_insight_block(state)

    assert "PRECOMPUTED COMBINED INSIGHT BUNDLE" in block
    assert "market=isoprene" not in block
    assert "17104" in block
    assert "risk critical" in block
    assert "market_narrative: Isoprene strengthened sharply this week." in block
    assert "erp_narrative: Inventory coverage is critically low." in block
    assert "refresh_enqueued: market=False, erp=True" in block


def test_quote_range_request_detection():
    assert decision_graph._looks_like_quote_range_request(
        "Based on the China regional database, give the recommended quote range for Piperylene this week and next week, and explain the spread versus SINOPEC Shanghai listed price."
    )
    assert not decision_graph._looks_like_quote_range_request(
        "What is the current piperylene price in China?"
    )


def test_build_quote_range_report_contains_required_sections_and_spread():
    state = {
        "user_query": "Based on the China regional database, give the recommended quote range for Piperylene this week and next week, and explain the spread versus SINOPEC Shanghai listed price.",
        "user_prefs": {"language": "en"},
        "pricing_decision": {
            "product_id": "piperylene",
            "product_name_cn": "间戊二烯",
            "product_name_en": "Piperylene",
            "unit": "RMB/ton",
            "latest_market_price": 14933,
            "recommended_price": 14800,
            "price_range": [14700, 14900],
            "sinopec_anchor": 5700,
            "discount_to_sinopec": -9100,
            "confidence": 0.72,
            "action_cn": "偏弱 — 维持低位或小幅下调",
            "adjustment_steps": [],
            "triggers": [{"condition": "orders drop >10%", "action": "trim quotes"}],
            "watch_signals": ["naphtha", "downstream demand"],
            "dissenting_view": "Spot tightness could keep quotes firmer than the base case.",
        },
        "forecast": {
            "scenarios": {
                "7d": {
                    "base": {"low": 14850, "high": 15150}
                }
            }
        },
        "diagnosis": {
            "market_regime": "bullish",
            "primary_driver": {"name_en": "naphtha", "change_7d_pct": 4.2},
        },
        "tool_info": "INTERNAL DATABASE LIVE DATA:\nPiperylene: 14933 RMB/ton (7d: +4.32%) [src=market_prices|product_id=piperylene|date=2026-04-17]",
    }

    report = decision_graph._build_quote_range_report(state)

    assert "## 1. Market Overview" in report
    assert "## 4. Pricing Decision" in report
    assert "This week recommended quote range" in report
    assert "Next week indicative quote range" in report
    assert "Spread versus SINOPEC Shanghai listed price" in report
    assert "Missing Sections (Auto-filled)" not in report
    assert "premium of" in report or "discount of" in report


def test_quote_range_validator_rejects_preview_placeholder_text():
    bad_report = (
        "## Quick Take\n\n"
        "- Current status: grounding the answer in market, inventory, and pricing signals.\n"
        "- Full grounded analysis is still being generated."
    )

    assert decision_graph._quote_range_report_has_required_slots(bad_report, "en") is False


def test_quote_range_validator_accepts_complete_slot_filled_report():
    good_report = (
        "# Recommended Quote Range for Piperylene (China)\n\n"
        "## 4. Pricing Decision\n"
        "- This week recommended quote range: **14,700-14,900 CNY/ton**.\n"
        "- Next week indicative quote range: **14,850-15,150 CNY/ton**.\n"
        "- Spread versus SINOPEC Shanghai listed price: a discount of **-9,100 CNY/ton**."
    )

    assert decision_graph._quote_range_report_has_required_slots(good_report, "en") is True


def test_build_deterministic_boss_briefing_report_includes_db_values():
    state = {
        "user_prefs": {"language": "en"},
        "erp_data": (
            "ERP SALES SUMMARY (2026-04-13 to 2026-04-14):\n"
            "TOTAL                           559.43\n\n"
            "Period-over-period change: -40.7% (previous period: 2026-04-11 to 2026-04-12, 943.24 ton)\n\n"
            "SAFETY STOCK ALERTS:\n"
            "⚠️ CRITICAL: 异戊二烯 (Isoprene) — only 5.9 days of stock!\n"
            "⚠️ CRITICAL: 戊烷发泡剂 (Blowing Agent) — only 2.0 days of stock!\n\n"
            "LIVE MARKET DATA (MYSQL):\n"
            "- crude_oil: 99.36 on 2026-04-14 (7d -3.93%)\n"
            "- naphtha: 1,074.00 on 2026-04-14 (7d -7.45%)\n"
            "- isoprene: 17,104.00 on 2026-04-14 (7d +33.62%)\n"
            "- piperylene: 14,933.00 on 2026-04-14 (7d +52.92%)\n"
            "- blowing_agent: 7,699.12 on 2026-04-09 (7d +27.94%)\n\n"
            "FORECAST SNAPSHOT (STAGE 3):\n"
            "- isoprene: 3d 15,224-16,482 | 7d 14,813-16,735 | 30d 17,543-20,815\n"
            "- piperylene: 3d 12,887-14,395 | 7d 12,551-14,854 | 30d 14,811-18,795\n"
        ),
    }

    report = decision_graph._build_deterministic_boss_briefing_report(state)

    assert "## Market Overview" in report
    assert "559.43 tons" in report
    assert "Piperylene" in report
    assert "14,933.00" in report
    assert "Isoprene" in report
    assert "only 5.9 days of stock" in report
    assert "12,551-14,854" in report


def test_market_lookup_reports_closest_available_window_when_requested_range_is_sparse(monkeypatch):
    from backend.src.service.market_data_service import market_data

    history = [
        {"date": f"2026-04-{day:02d}", "price": 100.0 + day}
        for day in range(16, 30)
    ]

    monkeypatch.setattr(market_data, "get_price_history", lambda product_id, cutoff_date=None: history if product_id == "sis" else [])

    tool_info = erp_tools.query_live_market_forecast_context(
        user_query="截至2026-05-06，最近7天SIS行情",
        focal_product_id="sis",
        include_forecast=False,
    )

    state = {
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "截至2026-05-06，最近7天SIS行情",
        "user_prefs": {"language": "zh"},
        "tool_info": tool_info,
    }

    report = decision_graph._build_simple_lookup_report(state)

    assert "No market price records found for requested period 2026-04-30 to 2026-05-06." in tool_info
    assert "AVAILABLE MARKET PRICE RANGE for" in tool_info
    assert "CLOSEST AVAILABLE 7-DAY WINDOW" in tool_info
    assert "## 市场数据可用性" in report
    assert "请求区间 2026-04-30 至 2026-05-06" in report
    assert "最接近的可用 7 天窗口" in report
    assert "- sis:" in report or "- SIS:" in report


def test_live_market_grounding_covers_all_core_products(monkeypatch):
    from backend.src.service import forecasting_service as forecasting_service_module
    from backend.src.service import market_data_service as market_data_service_module

    core_product_ids = [
        "cracked_c5",
        "cracked_c9",
        "isoprene",
        "piperylene",
        "dcpd",
        "raffinate_c5",
        "sis",
        "blowing_agent",
        "c5_resin",
        "hydrogenated_c9",
        "trimethylbenzene",
        "styrene",
    ]

    class _FakeForecastingService:
        def forecast(self, product_id):
            return {
                "scenarios": {
                    "3d": {"base": {"low": 1, "high": 2}},
                    "7d": {"base": {"low": 3, "high": 4}},
                    "30d": {"base": {"low": 5, "high": 6}},
                }
            }

    def _fake_history(product_id, cutoff_date=None):
        return [
            {"date": "2026-05-01", "price": 100.0},
            {"date": "2026-05-02", "price": 101.0},
            {"date": "2026-05-03", "price": 102.0},
            {"date": "2026-05-04", "price": 103.0},
            {"date": "2026-05-05", "price": 104.0},
            {"date": "2026-05-06", "price": 105.0},
            {"date": "2026-05-07", "price": 106.0},
            {"date": "2026-05-08", "price": 107.0},
        ]

    monkeypatch.setattr(
        market_data_service_module.market_data,
        "get_price_history",
        _fake_history,
    )
    monkeypatch.setattr(
        forecasting_service_module.forecasting_service,
        "forecast",
        _FakeForecastingService().forecast,
    )

    for product_id in core_product_ids:
        block = erp_tools.query_live_market_forecast_context(
            user_query=f"分析一下{product_id}最新行情，包含7日涨跌幅、关键驱动和短期判断",
            focal_product_id=product_id,
            include_forecast=False,
        )

        assert "LIVE MARKET DATA (MYSQL):" in block
        assert f"- {product_id}:" in block
        assert "(7d" in block


def test_erp_context_passthrough_covers_all_core_products(monkeypatch):
    core_product_ids = [
        "cracked_c5",
        "cracked_c9",
        "isoprene",
        "piperylene",
        "dcpd",
        "raffinate_c5",
        "sis",
        "blowing_agent",
        "c5_resin",
        "hydrogenated_c9",
        "trimethylbenzene",
        "styrene",
    ]

    def _fake_sales_summary(product_id, period):
        return f"ERP SALES SUMMARY ({period}): {product_id} ERP sales block"

    def _fake_inventory_levels(product_id):
        return ""

    monkeypatch.setattr(erp_tools, "query_sales_summary", _fake_sales_summary)
    monkeypatch.setattr(erp_tools, "query_inventory_levels", _fake_inventory_levels)
    monkeypatch.setattr(erp_tools, "query_view_coverage_audit", lambda force_refresh=False: "")

    for product_id in core_product_ids:
        block = erp_tools.build_erp_context(
            product_id=product_id,
            period="Current Week",
            query_type="sales",
            user_query=f"最近一周{product_id}的销售情况怎么样",
        )

        assert f"ERP SALES SUMMARY (Current Week): {product_id} ERP sales block" in block


if __name__ == "__main__":
    test_assembler()
