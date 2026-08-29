"""EDIA skill registry — maps skill IDs to handlers, markers, and optional SKILL.md paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

SkillCategory = Literal[
    "database",
    "forecast",
    "chart",
    "report",
    "analysis",
    "ontology",
    "automation",
    "guardrail",
]

SkillHandler = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_SKILLS = _PROJECT_ROOT / ".agents" / "skills"
_GITHUB_SKILLS = _PROJECT_ROOT / ".github" / "skills"


def _skill_md(*parts: str) -> Optional[Path]:
    path = Path(*parts)
    return path if path.is_file() else None


@dataclass(frozen=True)
class EdiaSkill:
    skill_id: str
    category: SkillCategory
    description: str
    handler: SkillHandler | None = None
    skill_md_path: Path | None = None
    markers_zh: Tuple[str, ...] = ()
    markers_en: Tuple[str, ...] = ()
    domain_triggers: Tuple[str, ...] = ()
    privacy_level: str = "internal"
    external_llm_allowed: bool = True
    tool_name: str | None = None
    instruction_excerpt: str = ""

    def matches_query(self, query: str, domains: List[str]) -> bool:
        q = str(query or "")
        ql = q.lower()
        if self.domain_triggers and any(domain in domains for domain in self.domain_triggers):
            return True
        if any(marker in q for marker in self.markers_zh):
            return True
        if any(marker in ql for marker in self.markers_en):
            return True
        return False

    def load_instruction_excerpt(self, max_lines: int = 40) -> str:
        if self.instruction_excerpt:
            return self.instruction_excerpt
        if not self.skill_md_path or not self.skill_md_path.is_file():
            return ""
        try:
            lines = self.skill_md_path.read_text(encoding="utf-8").splitlines()
            return "\n".join(lines[:max_lines]).strip()
        except OSError:
            return ""


def _contains_any(query: str, lower_query: str, zh: Tuple[str, ...], en: Tuple[str, ...]) -> bool:
    return any(marker in query for marker in zh) or any(marker in lower_query for marker in en)


# Lazy-populated registry
_SKILLS: Dict[str, EdiaSkill] = {}
_HANDLERS: Dict[str, SkillHandler] = {}


def _register(skill: EdiaSkill) -> None:
    _SKILLS[skill.skill_id] = skill


def _build_registry() -> None:
    if _SKILLS:
        return

    _register(
        EdiaSkill(
            skill_id="erp-sales-summary",
            category="database",
            description="ERP sales summary; revenue = SUM(FAMOUNT) per order line.",
            tool_name="edia_query_erp_sales_summary",
            domain_triggers=("erp",),
            markers_zh=("销售额", "销售金额", "销售收入", "销量", "销售情况"),
            markers_en=("sales revenue", "sales amount", "revenue", "sales volume"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="erp-order-details",
            category="database",
            description="Row-level ERP sales orders with per-line FAMOUNT.",
            tool_name="edia_query_erp_orders",
            domain_triggers=("erp",),
            markers_zh=("订单明细", "订单详情", "销售订单"),
            markers_en=("order detail", "order details"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="erp-customer-sales",
            category="database",
            description="Customer-level sales revenue and growth/risk.",
            tool_name="edia_query_erp_customer_sales",
            domain_triggers=("erp",),
            markers_zh=("客户销售", "客户排名", "流失风险", "增长点", "前五名客户", "前几名客户", "客户成交量", "客户成交量排名"),
            markers_en=("customer sales", "customer report", "churn risk", "top customer", "top customers"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="erp-inventory",
            category="database",
            description="ERP inventory levels by product.",
            tool_name="edia_query_erp_inventory",
            domain_triggers=("erp",),
            markers_zh=("库存", "存货", "库存量"),
            markers_en=("inventory", "stock level", "stock"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="db-capability-execute",
            category="database",
            description="Execute a registered database capability with verified SQL.",
            tool_name="edia_execute_db_capability",
            domain_triggers=("database",),
            markers_zh=("数据库", "明细", "查询"),
            markers_en=("database", "capability", "query data"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="db-capability-discover",
            category="database",
            description="Discover MySQL tables/columns relevant to a query.",
            tool_name="edia_discover_database_capability",
            domain_triggers=("database",),
            markers_zh=("有哪些表", "数据表", "字段"),
            markers_en=("which table", "schema", "columns"),
            privacy_level="internal_metadata",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="forecast-run",
            category="forecast",
            description="Run numeric price forecast scenarios from ForecastingService.",
            tool_name="edia_run_forecast",
            domain_triggers=("forecast",),
            markers_zh=("预测", "走势", "下周价格", "未来价格"),
            markers_en=("forecast", "price outlook", "predict"),
            privacy_level="market_internal_mixed",
            external_llm_allowed=True,
        )
    )
    _register(
        EdiaSkill(
            skill_id="forecast-policy",
            category="forecast",
            description="Forecast policy gates and horizons.",
            tool_name="edia_forecast_policy",
            domain_triggers=("forecast",),
            markers_zh=(),
            markers_en=(),
            privacy_level="market_internal_mixed",
            external_llm_allowed=True,
        )
    )
    _register(
        EdiaSkill(
            skill_id="chart-render",
            category="chart",
            description="Render verified price/sales charts (PNG base64).",
            tool_name="edia_render_chart",
            domain_triggers=("market", "erp"),
            markers_zh=("图表", "走势图", "趋势图", "可视化", "画图"),
            markers_en=("chart", "plot", "graph", "visualize", "trend chart"),
            privacy_level="internal",
            external_llm_allowed=True,
        )
    )
    _register(
        EdiaSkill(
            skill_id="erp-daily-chart",
            category="chart",
            description="Generate interactive daily ERP sales bar/line chart visualization from query_sales_daily().",
            tool_name="edia_erp_daily_chart",
            domain_triggers=("erp",),
            markers_zh=("周度", "按日", "日销售", "日销量", "每日销量", "日度", "每天"),
            markers_en=("daily sales", "daily volume", "daily bar", "weekly chart", "by day", "per day"),
            privacy_level="internal",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="background-insights",
            category="analysis",
            description="Active alerts from background intelligence skills.",
            tool_name="edia_get_background_insights",
            domain_triggers=("executive", "market", "erp"),
            markers_zh=("风险", "异常", "预警", "监控"),
            markers_en=("alert", "risk", "anomaly", "monitor"),
            privacy_level="internal",
            external_llm_allowed=True,
        )
    )
    _register(
        EdiaSkill(
            skill_id="erp-revenue-guardrail",
            category="guardrail",
            description="Block volume×avg revenue narration; enforce FAMOUNT sums.",
            privacy_level="erp_confidential",
            external_llm_allowed=False,
            instruction_excerpt=(
                "Sales revenue must equal the sum of ERP FAMOUNT per order line. "
                "Never compute revenue as total volume × weighted average price."
            ),
        )
    )
    _register(
        EdiaSkill(
            skill_id="edia-market-research",
            category="analysis",
            description="Source-ranked external market research.",
            skill_md_path=_skill_md(_GITHUB_SKILLS, "edia-market-research", "SKILL.md"),
            markers_zh=("调研", "研究", "外部市场"),
            markers_en=("market research", "research report"),
            domain_triggers=("market",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="docx",
            category="report",
            description="DOCX report export guidance.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "docx", "SKILL.md"),
            domain_triggers=("document",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="minimax-docx",
            category="report",
            description="Professional DOCX formatting.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "minimax-docx", "SKILL.md"),
            domain_triggers=("document",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="presentation-creator",
            category="report",
            description="Presentation deck structure.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "presentation-creator", "SKILL.md"),
            domain_triggers=("presentation",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="html-ppt",
            category="report",
            description="HTML slide deck.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "html-ppt", "SKILL.md"),
            domain_triggers=("presentation",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="pptx-generator",
            category="report",
            description="PowerPoint generation.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "pptx-generator", "SKILL.md"),
            domain_triggers=("presentation",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="pptx-maker",
            category="report",
            description="PPTD-format PowerPoint generation with validation.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "pptx-maker", "SKILL.md"),
            domain_triggers=("presentation",),
        )
    )
    _register(
        EdiaSkill(
            skill_id="ecisco-weekly-report",
            category="report",
            description="Ecisco C5/C9 weekly summary and forecast report.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "ecisco-weekly-report", "SKILL.md"),
            domain_triggers=("presentation",),
            markers_zh=("周度总结", "周报", "C5C9", "ecisco周报"),
            markers_en=("weekly report", "ecisco weekly", "C5/C9 weekly"),
        )
    )
    _register(
        EdiaSkill(
            skill_id="beautiful-mermaid",
            category="chart",
            description="Mermaid diagram rendering.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "beautiful-mermaid", "SKILL.md"),
            markers_zh=("流程图", "架构图", "mermaid"),
            markers_en=("mermaid", "diagram", "flowchart"),
        )
    )
    _register(
        EdiaSkill(
            skill_id="mysql",
            category="database",
            description="MySQL engineering and EXPLAIN guidance.",
            skill_md_path=_skill_md(_AGENTS_SKILLS, "mysql", "SKILL.md"),
            domain_triggers=("database",),
            markers_zh=("explain", "索引", "慢查询"),
            markers_en=("explain", "index", "slow query"),
        )
    )
    _register(
        EdiaSkill(
            skill_id="ontology-context",
            category="ontology",
            description="EDIA ontology metric definitions and integrity.",
            domain_triggers=("erp", "market", "database"),
            instruction_excerpt="Use edia_ontology.yaml metrics; revenue field is FAMOUNT on order lines.",
        )
    )
    _register(
        EdiaSkill(
            skill_id="dynamic-chart",
            category="chart",
            description="Dynamic multi-metric comparison visualization: Revenue + Volume + Orders vs time.",
            tool_name="edia_dynamic_chart",
            domain_triggers=("erp", "market"),
            markers_zh=("综合对比", "多指标", "销售额与销量", "营收销量", "对比图表"),
            markers_en=("multi-metric", "compare revenue", "revenue vs volume", "综合对比"),
            privacy_level="internal",
            external_llm_allowed=True,
        )
    )
    _register(
        EdiaSkill(
            skill_id="compare-erp-market",
            category="analysis",
            description="Cross-source analysis comparing ERP internal actual prices/transactions with external market reference prices.",
            tool_name="edia_compare_erp_market",
            domain_triggers=("erp", "market"),
            markers_zh=("对比", "差距", "价差", "内部外部", "实际价格", "市场对比", "利润分析"),
            markers_en=("compare", "gap", "spread", "vs market", "actual vs market", "margin analysis", "price difference"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="inventory-pricing-impact",
            category="analysis",
            description="Analyze how inventory levels and days-on-hand affect pricing strategy and margin pressure.",
            tool_name="edia_inventory_pricing_impact",
            domain_triggers=("erp", "market"),
            markers_zh=("库存影响", "库龄定价", "库存价格", "存货策略", "去库存"),
            markers_en=("inventory impact", "stock pricing", "inventory strategy", "days on hand", "destock"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )
    _register(
        EdiaSkill(
            skill_id="customer-quote-recommendation",
            category="analysis",
            description="Recommend customer quotes by blending ERP contract history with current market spot and forecast prices.",
            tool_name="edia_customer_quote_recommendation",
            domain_triggers=("erp", "market"),
            markers_zh=("报价建议", "客户报价", "定价建议", "合同报价", "推荐价格"),
            markers_en=("quote recommendation", "customer quote", "pricing suggestion", "offer price", "recommended price"),
            privacy_level="erp_confidential",
            external_llm_allowed=False,
        )
    )


def iter_skills() -> List[EdiaSkill]:
    _build_registry()
    return list(_SKILLS.values())


def register_handler(skill_id: str, handler: SkillHandler) -> None:
    _build_registry()
    _HANDLERS[str(skill_id)] = handler


def get_skill(skill_id: str) -> EdiaSkill | None:
    _build_registry()
    skill = _SKILLS.get(str(skill_id or "").strip())
    if skill is None:
        return None
    handler = _HANDLERS.get(skill.skill_id)
    if handler is not None and skill.handler is None:
        return EdiaSkill(
            skill_id=skill.skill_id,
            category=skill.category,
            description=skill.description,
            handler=handler,
            skill_md_path=skill.skill_md_path,
            markers_zh=skill.markers_zh,
            markers_en=skill.markers_en,
            domain_triggers=skill.domain_triggers,
            privacy_level=skill.privacy_level,
            external_llm_allowed=skill.external_llm_allowed,
            tool_name=skill.tool_name,
            instruction_excerpt=skill.instruction_excerpt,
        )
    return skill


def route_skills(query: str, domains: List[str]) -> List[str]:
    """Return ordered skill IDs matched by query markers and domains."""
    _build_registry()
    lower_query = str(query or "").lower()
    matched: list[tuple[int, str]] = []

    for skill in _SKILLS.values():
        score = 0
        if skill.domain_triggers and any(d in domains for d in skill.domain_triggers):
            score += 2
        if skill.markers_zh and any(m in query for m in skill.markers_zh):
            score += 3
        if skill.markers_en and any(m in lower_query for m in skill.markers_en):
            score += 3
        if score > 0:
            matched.append((score, skill.skill_id))

    matched.sort(key=lambda item: (-item[0], item[1]))
    ordered: list[str] = []
    for _, skill_id in matched:
        if skill_id not in ordered:
            ordered.append(skill_id)
    return ordered


def legacy_agent_skill_ids(query: str, domains: List[str]) -> List[str]:
    """Map registry routes to legacy agent_skills string IDs used in prompts."""
    routes = route_skills(query, domains)
    legacy: list[str] = []
    mapping = {
        "erp-sales-summary": [],
        "erp-order-details": [],
        "erp-customer-sales": [],
        "erp-inventory": [],
        "db-capability-execute": [],
        "db-capability-discover": [],
        "forecast-run": [],
        "forecast-policy": [],
        "chart-render": [],
        "background-insights": [],
        "erp-revenue-guardrail": [],
        "edia-market-research": ["edia-market-research"],
        "docx": ["docx"],
        "minimax-docx": ["minimax-docx"],
        "presentation-creator": ["presentation-creator"],
        "html-ppt": ["html-ppt"],
        "pptx-generator": ["pptx-generator"],
        "beautiful-mermaid": ["beautiful-mermaid"],
        "mysql": ["mysql"],
        "ontology-context": [],
        "dynamic-chart": ["dynamic-chart"],
        "compare-erp-market": ["compare-erp-market"],
        "inventory-pricing-impact": ["inventory-pricing-impact"],
        "customer-quote-recommendation": ["customer-quote-recommendation"],
    }
    for route_id in routes:
        for item in mapping.get(route_id, [route_id]):
            if item and item not in legacy:
                legacy.append(item)
    return legacy
