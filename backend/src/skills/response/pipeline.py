from __future__ import annotations

from typing import Any

from backend.src.service.types import DecisionReport

from .action_clarity import ActionClaritySkill
from .alternative_perspective import AlternativePerspectiveSkill
from .boilerplate_filter import BoilerplateFilterSkill
from .confidence_language import ConfidenceLanguageSkill
from .contradiction_detector import ContradictionDetectorSkill
from .citation_validator import CitationValidatorSkill
from .evidence_freshness import EvidenceFreshnessSkill
from .executive_answer_quality import ExecutiveAnswerQualitySkill
from .language_consistency_guardrail import LanguageConsistencyGuardrailSkill
from .semantic_consistency import SemanticConsistencySkill
from .source_attribution_guardrail import SourceAttributionGuardrailSkill

try:
    from backend.src.hermes.skills.erp_revenue_skill import ErpRevenueGuardrailSkill
except ImportError:
    ErpRevenueGuardrailSkill = None  # type: ignore


_RESPONSE_SKILLS = [
    # --- quality gates (detect issues) ---
    ContradictionDetectorSkill(),
    CitationValidatorSkill(),
    EvidenceFreshnessSkill(),
    SourceAttributionGuardrailSkill(),
    LanguageConsistencyGuardrailSkill(),
    SemanticConsistencySkill(),
    ExecutiveAnswerQualitySkill(),
    # --- content modifiers (rewrite / enrich) ---
    ConfidenceLanguageSkill(),
    BoilerplateFilterSkill(),
    AlternativePerspectiveSkill(),
    ActionClaritySkill(),
]
if ErpRevenueGuardrailSkill is not None:
    _RESPONSE_SKILLS.append(ErpRevenueGuardrailSkill())


def apply_response_enhancement_skills(
    report: DecisionReport, state: dict[str, Any]
) -> DecisionReport:
    """Run response skills sequentially and keep failures non-fatal."""
    for skill in _RESPONSE_SKILLS:
        try:
            report = skill.run(report, state)
        except Exception as exc:
            report.enhancement_applied[skill.name] = f"error:{type(exc).__name__}"
    return report
