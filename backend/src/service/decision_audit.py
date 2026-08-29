"""
Decision audit trail system.
Tracks all decisions over time, enabling users to see what changed and why.
"""

from datetime import datetime
from typing import Dict, List, Any, Optional
import json
from backend.src.db.sqlite_db import db


class DecisionAudit:
    """Audit trail for a single decision."""

    def __init__(
        self,
        product_id: str,
        date: str,
        recommendation_price: float,
        confidence: float,
        reasoning_hash: str,
        factors: Dict[str, Any],
        scenario: Optional[Dict[str, Any]] = None,
    ):
        self.product_id = product_id
        self.date = date
        self.recommendation_price = recommendation_price
        self.confidence = confidence
        self.reasoning_hash = reasoning_hash
        self.factors = factors  # What drove the decision?
        self.scenario = scenario or {}  # Bull/Base/Bear probability

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "date": self.date,
            "recommendation_price": self.recommendation_price,
            "confidence": self.confidence,
            "reasoning_hash": self.reasoning_hash,
            "factors": self.factors,
            "scenario": self.scenario,
        }


class DecisionHistory:
    """
    Tracks decision history and enables audit trail generation.

    Stores in SQLite `decision_history` table:
    - product_id
    - date
    - recommendation_price
    - confidence
    - reasoning_hash (MD5 of reasoning for comparison)
    - factors_json (what drove it)
    - scenario_json (bull/base/bear)
    """

    # In-memory cache for speed (real system would also persist to DB)
    _cache: Dict[str, List[DecisionAudit]] = {}

    @classmethod
    def record_decision(
        cls,
        product_id: str,
        recommendation_price: float,
        confidence: float,
        factors: Dict[str, Any],
        scenario: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a decision for audit trail.

        Args:
            product_id: e.g., "Cracked C5"
            recommendation_price: e.g., 5000
            confidence: 0-1 score
            factors: Dict of what influenced decision
            scenario: Bull/Base/Bear scenarios
        """

        date = datetime.now().isoformat()
        reasoning_hash = cls._hash_factors(factors)

        audit = DecisionAudit(
            product_id=product_id,
            date=date,
            recommendation_price=recommendation_price,
            confidence=confidence,
            reasoning_hash=reasoning_hash,
            factors=factors,
            scenario=scenario,
        )

        # Store in cache
        if product_id not in cls._cache:
            cls._cache[product_id] = []
        cls._cache[product_id].append(audit)

        # Persist to DB (SQL omitted for now, but would be INSERT)
        try:
            db.execute(
                """
                INSERT INTO decision_history 
                (product_id, date, recommendation_price, confidence, reasoning_hash, factors_json, scenario_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    product_id,
                    date,
                    recommendation_price,
                    confidence,
                    reasoning_hash,
                    json.dumps(factors),
                    json.dumps(scenario or {}),
                ),
            )
        except Exception as e:
            print(f"Warning: Could not persist decision to DB: {e}")

    @classmethod
    def get_audit_trail(
        cls,
        product_id: str,
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """
        Get decision evolution over N days.

        Returns: List of decisions with what changed between consecutive days.
        """

        # Get from cache or DB
        history = cls._cache.get(product_id, [])

        if not history:
            # Try to load from DB
            try:
                rows = db.query(
                    """
                    SELECT * FROM decision_history
                    WHERE product_id = ?
                    ORDER BY date DESC
                    LIMIT ?
                """,
                    (product_id, days * 4),
                )  # Overestimate to account for multiple per day

                history = [
                    DecisionAudit(
                        product_id=row[0],
                        date=row[1],
                        recommendation_price=row[2],
                        confidence=row[3],
                        reasoning_hash=row[4],
                        factors=json.loads(row[5]) if row[5] else {},
                        scenario=json.loads(row[6]) if row[6] else {},
                    )
                    for row in rows
                ]
            except Exception:
                history = []

        # Reverse to chronological order
        history = sorted(history, key=lambda x: x.date)

        # Build evolution narrative
        audit_trail = []
        for i, audit in enumerate(history):
            item = {
                "date": audit.date,
                "recommendation_price": audit.recommendation_price,
                "confidence": audit.confidence,
                "factors": audit.factors,
                "scenario": audit.scenario,
                "changes": {},
                "change_summary": "",
            }

            # Compare to previous decision
            if i > 0:
                prev = history[i - 1]
                item["changes"] = cls._compute_changes(prev, audit)
                item["change_summary"] = cls._summarize_changes(
                    prev, audit, item["changes"]
                )

            audit_trail.append(item)

        return audit_trail

    @classmethod
    def _compute_changes(
        cls, prev: DecisionAudit, curr: DecisionAudit
    ) -> Dict[str, Any]:
        """Identify what changed between two decisions."""

        changes = {}

        # Price change
        price_delta = curr.recommendation_price - prev.recommendation_price
        if abs(price_delta) > 1:  # Ignore tiny changes
            changes["price"] = {
                "from": prev.recommendation_price,
                "to": curr.recommendation_price,
                "delta": price_delta,
                "pct": (
                    round(price_delta / prev.recommendation_price * 100, 2)
                    if prev.recommendation_price
                    else 0
                ),
            }

        # Confidence change
        conf_delta = curr.confidence - prev.confidence
        if abs(conf_delta) > 0.05:
            changes["confidence"] = {
                "from": prev.confidence,
                "to": curr.confidence,
                "delta": round(conf_delta, 2),
            }

        # Factor changes
        prev_factors = prev.factors or {}
        curr_factors = curr.factors or {}

        for key in set(list(prev_factors.keys()) + list(curr_factors.keys())):
            prev_val = prev_factors.get(key)
            curr_val = curr_factors.get(key)

            if prev_val != curr_val:
                changes[key] = {
                    "from": prev_val,
                    "to": curr_val,
                }

        return changes

    @classmethod
    def _summarize_changes(
        cls,
        prev: DecisionAudit,
        curr: DecisionAudit,
        changes: Dict[str, Any],
    ) -> str:
        """Convert changes dict to human-readable summary."""

        if not changes:
            return "No significant changes"

        parts = []

        # Price change
        if "price" in changes:
            p = changes["price"]
            direction = "↑" if p["delta"] > 0 else "↓"
            parts.append(
                f"{direction} Price {p['from']} → {p['to']} ({p['pct']:+.1f}%)"
            )

        # Other key factors
        key_factors = [
            "naphtha_price",
            "crude_price",
            "inventory_signal",
            "demand_signal",
            "supply_signal",
            "competitor_price",
        ]

        for factor in key_factors:
            if factor in changes:
                parts.append(
                    f"  ↑ {factor.replace('_', ' ')}: {changes[factor]['from']} → {changes[factor]['to']}"
                )

        return " | ".join(parts)

    @classmethod
    def _hash_factors(cls, factors: Dict[str, Any]) -> str:
        """Create a hash of factors for comparison."""
        import hashlib

        factors_str = json.dumps(factors, sort_keys=True)
        return hashlib.md5(factors_str.encode()).hexdigest()[:16]


# Singleton
decision_history = DecisionHistory()
