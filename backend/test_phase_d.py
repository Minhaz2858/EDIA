"""Phase D end-to-end test — Auto-Override Integration.

Verifies the full pipeline for a simulated Tier-1 confirmed geopolitical supply shock:
  1. News ingestion agent detects the event
  2. Auto-override engine evaluates and auto-injects (confidence >= 0.75)
  3. Perception service surfaces the active event in the snapshot
  4. Medium-confidence events go to the human-review queue

Milestone: "Zero manual intervention required for a Tier-1 confirmed geopolitical
supply shock. System detects, computes, injects, and alerts — all within 15 minutes."
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.src.intelligence.event_types import ExtractedEvent
from backend.src.intelligence.auto_override_decision import (
    AutoOverrideDecisionEngine,
    AUTO_INJECT_THRESHOLD,
    HUMAN_REVIEW_THRESHOLD,
)
from backend.src.intelligence.causal_narrative_engine import CausalChainResult
from backend.src.service.perception_service import PerceptionService


class FakeVectorStore:
    """Minimal vector-store stub for NewsIngestionAgent."""

    def add_news_event(self, payload: dict) -> None:
        pass


class FakeLLM:
    """Minimal LLM stub."""

    pass


class PhaseDAutoOverrideTest(unittest.TestCase):
    """End-to-end auto-override pipeline verification."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.override_path = Path(cls.temp_dir.name) / "macro_override.json"
        # Patch the macro override path used by the service
        import backend.src.service.macro_override_service as mos

        cls._original_override_path = mos._OVERRIDE_PATH
        mos._OVERRIDE_PATH = cls.override_path

    @classmethod
    def tearDownClass(cls):
        import backend.src.service.macro_override_service as mos

        mos._OVERRIDE_PATH = cls._original_override_path
        cls.temp_dir.cleanup()

    def setUp(self):
        self.override_engine = AutoOverrideDecisionEngine()
        self.perception = PerceptionService()
        if self.override_path.exists():
            self.override_path.unlink()

    # ------------------------------------------------------------------
    # Helper factories
    # ------------------------------------------------------------------

    def _make_tier1_confirmed_geopolitical_event(self) -> ExtractedEvent:
        """Simulate a Tier-1 confirmed geopolitical supply shock."""
        return ExtractedEvent(
            event_type="geopolitical_supply_disruption",
            headline="Hormuz Strait tanker attack confirmed — 2M bpd at risk",
            affected_commodities=["crude_oil", "naphtha"],
            direction="bullish",
            magnitude_estimate="extreme",
            certainty="confirmed",
            time_horizon="short",
            geographic_scope="middle_east",
            relevance_to_c5_c9="direct",
            source_credibility="tier_1",
            causal_chain_hint="Confirmed attack on tanker convoy in Strait of Hormuz.",
            raw_quote="Hormuz Strait tanker attack confirmed — 2M bpd at risk",
        )

    def _make_medium_confidence_event(self) -> ExtractedEvent:
        """Simulate an event that should trigger human review (0.50-0.75)."""
        return ExtractedEvent(
            event_type="demand_signal",
            headline="Rumored Chinese stimulus package for petrochemical sector",
            affected_commodities=["naphtha", "c5"],
            direction="bullish",
            magnitude_estimate="medium",
            certainty="likely",
            time_horizon="short",
            geographic_scope="china",
            relevance_to_c5_c9="direct",
            source_credibility="tier_2",
            causal_chain_hint="Unconfirmed reports of stimulus targeting downstream demand.",
            raw_quote="Rumored Chinese stimulus package for petrochemical sector",
        )

    def _make_low_confidence_event(self) -> ExtractedEvent:
        """Simulate an event that should only be logged (< 0.50)."""
        return ExtractedEvent(
            event_type="weather_event",
            headline="Typhoon forecast for South China Sea next week",
            affected_commodities=["naphtha"],
            direction="bearish",
            magnitude_estimate="small",
            certainty="speculative",
            time_horizon="medium",
            geographic_scope="asia_pacific",
            relevance_to_c5_c9="minimal",
            source_credibility="social_media",
            causal_chain_hint="Social media weather forecast with no confirmed impact on ports.",
            raw_quote="Typhoon forecast for South China Sea next week",
        )

    def _build_causal_chain(self, event: ExtractedEvent, naphtha_total_pct: float = 10.0) -> CausalChainResult:
        """Build a minimal CausalChainResult for override testing."""
        return CausalChainResult(
            event=event.to_dict(),
            entry_commodity="crude_oil",
            primary_impact_pct=10.0,
            naphtha_total_pct=naphtha_total_pct,
            product_impacts={
                "naphtha": {
                    "pct_impact": naphtha_total_pct * 100,
                    "cny_impact": 500,
                    "causal_path": "event → crude_oil → naphtha → naphtha",
                    "elasticity_used": 1.0,
                    "second_order_pct": 0.0,
                }
            },
            causal_narrative=event.causal_chain_hint,
            confidence_by_horizon={"3d": 0.90, "7d": 0.80, "30d": 0.60},
            historical_precedents={},
            recommended_override_type=event.event_type,
            second_order_summary={},
        )

    # ------------------------------------------------------------------
    # D1 — Auto-Override Engine Tests
    # ------------------------------------------------------------------

    def test_tier1_confirmed_event_auto_injects(self):
        """Tier-1 confirmed geopolitical event with large impact → auto_inject."""
        event = self._make_tier1_confirmed_geopolitical_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=10.0)

        decision = self.override_engine.decide(event, chain)

        self.assertEqual(decision.action, "auto_inject")
        self.assertGreaterEqual(
            decision.injection_confidence,
            AUTO_INJECT_THRESHOLD,
            f"Expected confidence >= {AUTO_INJECT_THRESHOLD}, got {decision.injection_confidence}",
        )
        self.assertEqual(decision.event_id, event.event_id)

    def test_auto_inject_writes_macro_override_file(self):
        """Auto-inject decision must write macro_override.json to disk."""
        event = self._make_tier1_confirmed_geopolitical_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=10.0)

        self.override_engine.decide(event, chain)

        self.assertTrue(
            self.override_path.exists(),
            "macro_override.json was not written after auto-inject",
        )
        data = json.loads(self.override_path.read_text(encoding="utf-8"))
        self.assertTrue(data.get("active"), "Override should be active")
        self.assertIn("naphtha_shock_pct", data)
        self.assertGreater(abs(data["naphtha_shock_pct"]), 0)

    def test_medium_confidence_event_goes_to_human_review(self):
        """Medium-confidence event (0.50-0.75) → human_review queue."""
        event = self._make_medium_confidence_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=5.0)

        decision = self.override_engine.decide(event, chain)

        self.assertEqual(decision.action, "human_review")
        self.assertGreaterEqual(decision.injection_confidence, HUMAN_REVIEW_THRESHOLD)
        self.assertLess(decision.injection_confidence, AUTO_INJECT_THRESHOLD)

        pending = self.override_engine.get_pending_reviews()
        event_ids = [d["event_id"] for d in pending]
        self.assertIn(event.event_id, event_ids)

    def test_low_confidence_event_logs_only(self):
        """Low-confidence event (< 0.50) → log_only, no file write."""
        event = self._make_low_confidence_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=1.0)

        decision = self.override_engine.decide(event, chain)

        self.assertEqual(decision.action, "log_only")
        self.assertLess(decision.injection_confidence, HUMAN_REVIEW_THRESHOLD)
        # Override file should not be created/modified by a log_only decision
        if self.override_path.exists():
            data = json.loads(self.override_path.read_text(encoding="utf-8"))
            # File may exist from previous test; ensure it wasn't updated by this event
            self.assertNotEqual(data.get("event_type"), event.event_type)

    def test_human_approve_pending_injects_override(self):
        """Approving a pending review injects the override."""
        event = self._make_medium_confidence_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=5.0)
        self.override_engine.decide(event, chain)

        # Before approval, no override file (medium confidence)
        if self.override_path.exists():
            self.override_path.unlink()

        approved = self.override_engine.approve_pending(event.event_id)
        self.assertIsNotNone(approved)
        self.assertEqual(approved.action, "approved_and_injected")

        self.assertTrue(self.override_path.exists())

    def test_human_reject_pending_does_not_inject(self):
        """Rejecting a pending review discards it without injection."""
        event = self._make_medium_confidence_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=5.0)
        self.override_engine.decide(event, chain)

        rejected = self.override_engine.reject_pending(event.event_id)
        self.assertIsNotNone(rejected)
        self.assertEqual(rejected.action, "rejected")

        pending = self.override_engine.get_pending_reviews()
        event_ids = [d["event_id"] for d in pending]
        self.assertNotIn(event.event_id, event_ids)

    # ------------------------------------------------------------------
    # D2 — News Ingestion Agent Wiring Test
    # ------------------------------------------------------------------

    def test_news_agent_accepts_override_engine(self):
        """NewsIngestionAgent must accept override_engine in constructor."""
        from backend.src.intelligence.news_ingestion_agent import NewsIngestionAgent

        fake_kb = FakeVectorStore()
        fake_llm = FakeLLM()
        agent = NewsIngestionAgent(fake_llm, fake_kb, override_engine=self.override_engine)
        self.assertIs(agent.override_engine, self.override_engine)

    def test_manual_ingest_triggers_override_evaluation(self):
        """Manual event ingestion must trigger auto-override if engine is wired."""
        from backend.src.intelligence.news_ingestion_agent import NewsIngestionAgent

        fake_kb = FakeVectorStore()
        fake_llm = FakeLLM()
        agent = NewsIngestionAgent(fake_llm, fake_kb, override_engine=self.override_engine)

        if self.override_path.exists():
            self.override_path.unlink()

        event_data = {
            "event_type": "geopolitical_supply_disruption",
            "headline": "Major pipeline explosion in Middle East confirmed",
            "affected_commodities": ["crude_oil", "naphtha"],
            "direction": "bullish",
            "magnitude_estimate": "extreme",
            "certainty": "confirmed",
            "time_horizon": "short",
            "geographic_scope": "middle_east",
            "relevance_to_c5_c9": "direct",
            "source_credibility": "tier_1",
            "causal_chain_hint": "Confirmed pipeline explosion reducing 1.5M bpd.",
            "raw_quote": "Major pipeline explosion in Middle East confirmed",
        }
        agent.ingest_manual_event(event_data)

        # The override should have been auto-injected
        self.assertTrue(
            self.override_path.exists(),
            "Auto-override was not triggered during manual ingestion",
        )
        data = json.loads(self.override_path.read_text(encoding="utf-8"))
        self.assertTrue(data.get("active"))

    # ------------------------------------------------------------------
    # D3 — Perception Service Integration Test
    # ------------------------------------------------------------------

    def test_perception_snapshot_includes_intelligence_events(self):
        """PerceptionService.build_snapshot must include intelligence_events when active."""
        snapshot = self.perception.build_snapshot()
        # Should always have alert_level
        self.assertIn("alert_level", snapshot)
        # intelligence_events may or may not be present depending on DB state;
        # the key test is that the method does not crash and the key exists when present.
        self.assertIn("products", snapshot)
        self.assertIn("market_phase", snapshot)

    def test_alert_level_elevation_with_intelligence_events(self):
        """If intelligence events are active, alert_level must be ALERT."""
        # We can't easily inject fake DB events in this unit test, but we can
        # verify the helper logic directly by monkey-patching _query_recent_intelligence_events.
        fake_event = {
            "event_id": "evt_test_123",
            "headline": "Test event",
            "event_type": "geopolitical_supply_disruption",
            "certainty": "confirmed",
            "source_credibility": "tier_1",
            "direction": "bullish",
            "impact_magnitude": 0.85,
            "detected_at": datetime.utcnow().isoformat(),
            "source_url": "",
            "raw_quote": "Test",
        }
        self.perception._query_recent_intelligence_events = lambda hours=4: [fake_event]

        snapshot = self.perception.build_snapshot()
        self.assertEqual(snapshot["alert_level"], "ALERT")
        self.assertIn("intelligence_events", snapshot)
        self.assertEqual(len(snapshot["intelligence_events"]), 1)
        self.assertEqual(snapshot["intelligence_events"][0]["event_id"], "evt_test_123")

    # ------------------------------------------------------------------
    # D4 — Pipeline Timing / Idempotency
    # ------------------------------------------------------------------

    def test_duplicate_event_decision_is_idempotent(self):
        """Calling decide() twice on the same event returns the cached decision."""
        event = self._make_tier1_confirmed_geopolitical_event()
        chain = self._build_causal_chain(event, naphtha_total_pct=10.0)

        d1 = self.override_engine.decide(event, chain)
        d2 = self.override_engine.decide(event, chain)

        self.assertEqual(d1.action, d2.action)
        self.assertEqual(d1.injection_confidence, d2.injection_confidence)
        self.assertEqual(d1.timestamp, d2.timestamp)


if __name__ == "__main__":
    unittest.main()
