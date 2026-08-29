from __future__ import annotations

from types import SimpleNamespace

from backend.src.nlu.types import TaskType
from backend.src.service.types import DecisionReport
from backend.src.skills.response.executive_answer_quality import ExecutiveAnswerQualitySkill
from backend.src.skills.response.pipeline import apply_response_enhancement_skills


def _report(markdown: str, task_type: TaskType = TaskType.BOSS_BRIEFING) -> DecisionReport:
    return DecisionReport(
        raw_markdown=markdown,
        task_type=task_type,
        context=SimpleNamespace(to_dict=lambda: {}),
    )


def test_executive_answer_quality_flags_missing_source_and_ungrounded_action() -> None:
    report = _report(
        "背景说明\n\n"
        "建议继续关注市场，并尽快调整销售策略。\n"
    )

    audited = ExecutiveAnswerQualitySkill().run(
        report,
        {"user_query": "老板要看本周销售报告", "user_prefs": {"chat_mode": "report"}},
    )

    codes = {flag.code for flag in audited.quality_flags}
    assert "missing_so_what_opening" in codes
    assert "missing_date_range" in codes
    assert "missing_data_source" in codes
    assert "ungrounded_recommendation" in codes
    assert audited.enhancement_applied["executive_answer_quality"] == "score:0"


def test_executive_answer_quality_passes_grounded_management_answer() -> None:
    report = _report(
        "## 01 · 管理层结论\n"
        "建议本周对戊烷发泡剂维持报价，因为 ERP 显示 2026-05-11 至 2026-05-13 销量为 136.48 吨，订单 3 笔。\n\n"
        "## 02 · 关键数据\n"
        "数据源：ERP 业务库；周期：2026-05-11 至 2026-05-13。\n\n"
        "## 04 · 下一步动作\n"
        "- 建议销售团队优先跟进 3 笔订单客户，触发条件为销量低于 100 吨。\n"
    )

    audited = ExecutiveAnswerQualitySkill().run(
        report,
        {"user_query": "老板要看客户销售报告", "user_prefs": {"chat_mode": "report"}},
    )

    assert not [flag for flag in audited.quality_flags if flag.source == "executive_answer_quality"]
    assert audited.enhancement_applied["executive_answer_quality"] == "score:100"


def test_response_pipeline_runs_executive_answer_quality() -> None:
    report = _report(
        "## 01 · 管理层结论\n"
        "建议根据 ERP 业务库 2026-05-11 至 2026-05-13 的 136.48 吨销量维持报价。\n\n"
        "数据源：ERP 业务库。\n",
        TaskType.ERP_DATA_QUERY,
    )

    audited = apply_response_enhancement_skills(
        report,
        {"user_query": "最近一周戊烷发泡剂的销售情况怎么样", "user_prefs": {"chat_mode": "report"}},
    )

    assert "executive_answer_quality" in audited.enhancement_applied
