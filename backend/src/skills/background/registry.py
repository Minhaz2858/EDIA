"""Background skill registration — instantiates and registers all 12 skills.

Import this module and call ``register_all_background_skills()`` at
server startup to populate the ``background_skill_scheduler`` singleton.
"""

from __future__ import annotations

import logging

from .base import background_skill_scheduler

logger = logging.getLogger(__name__)


def register_all_background_skills() -> None:
    """Instantiate every background skill and register it with the scheduler."""

    # Phase-1 core skills (highest priority)
    from .competitor_tracker import CompetitorIntelligenceTracker
    from .supply_chain_risk import SupplyChainRiskMonitor
    from .decision_quality_auditor import DecisionQualityAuditor
    from .scenario_confidence import ScenarioConfidenceEngine

    # Phase-2 analytical skills
    from .causal_impact_attribution import CausalImpactAttribution
    from .anomaly_pattern_detector import AnomalyPatternDetector
    from .hedging_strategy import HedgingStrategyRecommender
    from .regulatory_monitor import RegulatoryPolicyMonitor

    # Phase-3 operational skills
    from .inventory_optimizer import InventoryLevelOptimizer
    from .cross_product_correlation import CrossProductCorrelationEngine
    from .weather_logistics import WeatherLogisticsPredictor
    from .sentiment_reversal import SentimentReversalDetector
    from .ontology_integrity_monitor import OntologyIntegrityMonitor
    from .automated_report_pipeline import AutomatedWeeklyReportPipeline

    # Phase-4 personalisation
    from .user_behavior_profiler import UserBehaviorProfiler

    skills = [
        # --- Phase 1: core ---
        CompetitorIntelligenceTracker(),
        SupplyChainRiskMonitor(),
        DecisionQualityAuditor(),
        ScenarioConfidenceEngine(),
        # --- Phase 2: analytical ---
        CausalImpactAttribution(),
        AnomalyPatternDetector(),
        HedgingStrategyRecommender(),
        RegulatoryPolicyMonitor(),
        # --- Phase 3: operational ---
        InventoryLevelOptimizer(),
        CrossProductCorrelationEngine(),
        WeatherLogisticsPredictor(),
        SentimentReversalDetector(),
        OntologyIntegrityMonitor(),
        AutomatedWeeklyReportPipeline(),
        # --- Phase 4: personalisation ---
        UserBehaviorProfiler(),
    ]

    for skill in skills:
        background_skill_scheduler.register(skill)
        logger.info(
            "Registered background skill: %s (every %d min)",
            skill.name,
            skill.interval_minutes,
        )

    logger.info("All %d background intelligence skills registered.", len(skills))
