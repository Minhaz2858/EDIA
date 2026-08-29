from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from backend.src.nlu.types import UserContext, TaskType


@dataclass
class RiskAlert:
    """A single risk alert."""

    severity: str  # "🔴 CRITICAL", "🟠 HIGH", "🟡 MEDIUM", "🟢 LOW"
    category: str  # "macro", "supply", "demand", "regulatory", "competitor"
    description: str
    impact_on_price: Optional[float] = None  # e.g., +200 or -300 CNY/ton
    probability: float = 0.5
    evidence: str = ""


@dataclass
class ConfidenceScoreDetail:
    """Confidence metrics for a forecast."""

    overall: float  # 0-1
    data_quality: float
    evidence_strength: float
    model_stability: float
    macro_uncertainty: float
    p25: float  # 25th percentile
    p50: float  # Median
    p75: float  # 75th percentile
    uncertainty_source: str  # Primary source of uncertainty
    confidence_label: str  # "HIGH", "MEDIUM", "LOW"
    lookback_mape: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualityFlag:
    """Quality issue or enhancement note produced by response skills."""

    code: str
    severity: str  # high | medium | low
    message: str
    source: str


@dataclass
class DecisionReport:
    """
    The final output object returned to the user.
    Enhanced with confidence and risk information for Phase 1.
    """

    raw_markdown: str
    task_type: TaskType
    context: UserContext
    status: str = "success"  # success, error, partial
    error_message: Optional[str] = None

    # Phase 1 enhancements
    confidence: Optional[ConfidenceScoreDetail] = None
    risks: List[RiskAlert] = field(default_factory=list)
    critic_flags: List[str] = field(default_factory=list)  # From decision_critic
    critic_level: str = "pass"  # pass, warn, high_risk
    quality_flags: List[QualityFlag] = field(default_factory=list)
    alerts_prepended: List[str] = field(default_factory=list)
    enhancement_applied: Dict[str, str] = field(default_factory=dict)
    resolved_chat_mode: Optional[str] = None
    mode_reason: Optional[str] = None
    citations: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "raw_markdown": self.raw_markdown,
            "task_type": self.task_type.value,
            "context": self.context.to_dict(),
            "status": self.status,
            "error_message": self.error_message,
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "risks": [
                {
                    "severity": r.severity,
                    "category": r.category,
                    "description": r.description,
                    "impact_on_price": r.impact_on_price,
                    "probability": r.probability,
                    "evidence": r.evidence,
                }
                for r in self.risks
            ],
            "critic_flags": self.critic_flags,
            "critic_level": self.critic_level,
            "quality_flags": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "source": f.source,
                }
                for f in self.quality_flags
            ],
            "alerts_prepended": self.alerts_prepended,
            "enhancement_applied": self.enhancement_applied,
            "resolved_chat_mode": self.resolved_chat_mode,
            "mode_reason": self.mode_reason,
            "citations": self.citations,
        }
