from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse


@dataclass
class ResearchBrief:
    decision: str
    target_customer: str
    geography: str
    category_or_substitute_set: str
    time_horizon: str
    must_answer_questions: list[str] = field(default_factory=list)
    evidence_bar: str = "medium"


@dataclass
class EvidenceItem:
    title: str
    url: str
    source_type: str = "news"  # official | market_data | filings | news | blog | forum
    published_date: str = ""
    corroboration_count: int = 1


def validate_research_brief(brief: ResearchBrief) -> list[str]:
    issues: list[str] = []

    if len((brief.decision or "").strip()) < 12:
        issues.append("decision is too vague; define the exact decision to make")
    if len((brief.target_customer or "").strip()) < 3:
        issues.append("target_customer is missing or too short")
    if len((brief.geography or "").strip()) < 2:
        issues.append("geography is missing")
    if len((brief.category_or_substitute_set or "").strip()) < 3:
        issues.append("category_or_substitute_set is missing")
    if len((brief.time_horizon or "").strip()) < 2:
        issues.append("time_horizon is missing")
    if len(brief.must_answer_questions) < 3:
        issues.append("at least 3 must-answer questions are recommended")

    evidence_bar = (brief.evidence_bar or "medium").lower()
    if evidence_bar not in {"low", "medium", "high"}:
        issues.append("evidence_bar must be one of: low | medium | high")

    return issues


def grade_evidence(items: list[EvidenceItem]) -> dict[str, Any]:
    """Decision-oriented evidence score inspired by market-research/data-analysis rigor.

    Output fields:
    - score: 0-100
    - confidence_label: LOW | MEDIUM | HIGH
    - source_diversity: number of unique source types
    - freshness_days_median: median age by available dates (or None)
    """
    if not items:
        return {
            "score": 0,
            "confidence_label": "LOW",
            "source_diversity": 0,
            "freshness_days_median": None,
            "notes": ["no evidence items provided"],
        }

    source_weights = {
        "official": 1.0,
        "market_data": 0.95,
        "filings": 0.9,
        "news": 0.7,
        "blog": 0.45,
        "forum": 0.3,
    }

    now = datetime.now(timezone.utc)
    weighted_scores: list[float] = []
    ages: list[int] = []
    source_types: set[str] = set()
    domains: set[str] = set()

    for item in items:
        st = (item.source_type or "news").strip().lower()
        source_types.add(st)
        domain = _domain(item.url)
        if domain:
            domains.add(domain)

        base = source_weights.get(st, 0.55)
        corroboration_bonus = min(0.25, max(0.0, (item.corroboration_count - 1) * 0.08))

        freshness_penalty = 0.0
        age = _age_days(item.published_date, now)
        if age is not None:
            ages.append(age)
            if age > 30:
                freshness_penalty = 0.1
            if age > 90:
                freshness_penalty = 0.2
            if age > 180:
                freshness_penalty = 0.35

        weighted_scores.append(max(0.0, base + corroboration_bonus - freshness_penalty))

    avg = sum(weighted_scores) / max(1, len(weighted_scores))
    diversity_boost = min(0.15, len(source_types) * 0.03) + min(0.1, len(domains) * 0.01)
    raw = min(1.0, avg + diversity_boost)
    score = int(round(raw * 100))

    if score >= 75:
        label = "HIGH"
    elif score >= 45:
        label = "MEDIUM"
    else:
        label = "LOW"

    median_age: Optional[int] = None
    if ages:
        sorted_ages = sorted(ages)
        median_age = sorted_ages[len(sorted_ages) // 2]

    notes: list[str] = []
    if len(source_types) < 2:
        notes.append("low source-type diversity")
    if len(domains) < 3:
        notes.append("low domain diversity")
    if median_age is not None and median_age > 30:
        notes.append("evidence may be stale for fast-moving market decisions")

    return {
        "score": score,
        "confidence_label": label,
        "source_diversity": len(source_types),
        "domain_diversity": len(domains),
        "freshness_days_median": median_age,
        "notes": notes,
    }


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().strip()
    except Exception:
        return ""


def _age_days(raw_date: str, now: datetime) -> Optional[int]:
    text = (raw_date or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (now.date() - dt.date()).days)
        except Exception:
            continue
    return None
