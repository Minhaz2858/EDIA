import pytest

from backend.src.nlu.llm_service import DisabledLLMService
from backend.src.intelligence.product_intelligence import summarize_product_intelligence
from backend.src.nlu.context_generator import ContextGenerator


def test_disabled_llm_service_rejects_completion_requests():
    llm = DisabledLLMService()

    with pytest.raises(RuntimeError, match="real LLM backend"):
        llm.generate_completion("system", "user")


def test_disabled_llm_service_rejects_json_requests():
    llm = DisabledLLMService()

    with pytest.raises(RuntimeError, match="real LLM backend"):
        llm.generate_json("system", "user")


def test_context_generator_prefers_earliest_product_mention():
    generator = ContextGenerator(DisabledLLMService())

    context = generator.extract_context(
        "Analyze the current C5 market situation and compare it with SIS later."
    )

    # ContextGenerator can extract multiple products; ensure earliest mention wins as primary.
    assert str(context.product).startswith("Mixed C5")


def test_context_generator_uses_history_for_follow_up_questions():
    generator = ContextGenerator(DisabledLLMService())

    context = generator.extract_context(
        "describe why you make this price forecast",
        conversation_history=[
            {
                "role": "user",
                "content": "Analyze the current C5 product market situation.",
            },
            {
                "role": "assistant",
                "content": "product: C5\nPrice Forecast: 3-day range ...",
            },
        ],
    )

    assert context.product == "Mixed C5"


def test_context_generator_prefers_recent_user_product_for_analysis_follow_up():
    generator = ContextGenerator(DisabledLLMService())

    context = generator.extract_context(
        "i want full market analysis with drivers, outlook, and recommendations",
        conversation_history=[
            {
                "role": "user",
                "content": "give me last 7 days c5 product update",
            },
            {
                "role": "assistant",
                "content": "SIS forecast and other downstream products are listed in a summary table.",
            },
        ],
    )

    assert context.product == "Mixed C5"


def test_context_generator_preserves_english_month_name_date_range():
    generator = ContextGenerator(DisabledLLMService())

    query = "Please provide order details for May 11th to May 12th."
    context = generator.extract_context(query)

    assert context.period == query


def test_product_intelligence_summary_matches_aliases_and_returns_bullish_signal():
    intelligence_context = {
        "has_intelligence": True,
        "causal_analyses": [
            {
                "event_id": "evt-hormuz",
                "event_headline": "Hormuz closure",
                "event_type": "geopolitical_supply_disruption",
                "entry_commodity": "crude_oil",
                "naphtha_total_pct": 4.62,
                "product_impacts": {
                    "mixed_c5": {
                        "pct_impact": 3.79,
                        "cny_impact": 189,
                        "causal_path": "event -> crude_oil -> naphtha -> mixed_c5",
                    }
                },
                "confidence_by_horizon": {"7d": 0.8},
                "narrative": "Hormuz disruption tightens naphtha and lifts C5.",
                "detected_at": "2026-05-07T10:30:00",
                "review_status": "pending",
                "source_url": "https://example.com/hormuz",
                "raw_quote": "Traffic through Hormuz slowed sharply.",
            }
        ],
    }

    summary = summarize_product_intelligence("cracked_c5", intelligence_context)

    assert summary["active"] is True
    assert summary["event_count"] == 1
    assert summary["bias"] == "bullish"
    assert summary["net_price_pct"] == pytest.approx(3.79)
    assert summary["net_price_cny"] == pytest.approx(189)
    assert summary["dominant_event"]["event_id"] == "evt-hormuz"
    assert summary["events"][0]["review_status"] == "pending"
    assert summary["events"][0]["source_url"] == "https://example.com/hormuz"
