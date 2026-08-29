from __future__ import annotations

from pathlib import Path


def test_response_quality_context_marks_needs_review_for_high_flags() -> None:
    from backend.server import _attach_response_quality_to_context

    context = _attach_response_quality_to_context(
        {"llm_route": "deterministic"},
        [
            {
                "code": "missing_data_source",
                "severity": "high",
                "message": "answer does not mention a source",
                "source": "executive_answer_quality",
            }
        ],
        [],
        {"executive_answer_quality": "score:75"},
    )

    assert context["llm_route"] == "deterministic"
    assert context["quality_flags"][0]["code"] == "missing_data_source"
    assert context["response_quality"]["status"] == "needs_review"
    assert context["response_quality"]["executive_answer_quality"] == "score:75"
    assert context["response_quality"]["flag_count"] == 1


def test_response_quality_context_marks_passed_when_score_only() -> None:
    from backend.server import _attach_response_quality_to_context

    context = _attach_response_quality_to_context(
        {},
        [],
        [],
        {"executive_answer_quality": "score:100"},
    )

    assert context["response_quality"]["status"] == "passed"
    assert context["response_quality"]["executive_answer_quality"] == "score:100"
    assert context["response_quality"]["flag_count"] == 0
    assert "quality_flags" not in context


def test_chat_quality_ui_is_visible_and_restored_from_history() -> None:
    root = Path(__file__).resolve().parents[1]
    chat_message = (root / "frontend/src/components/ChatMessage.tsx").read_text(encoding="utf-8")
    app = (root / "frontend/src/App.tsx").read_text(encoding="utf-8")

    assert "qualityNeedsReview" in chat_message
    assert "qualityChecked" in chat_message
    assert "response_quality" in app
    assert "context?.quality_flags" in app
    assert "false && !isUser && hasQualityMeta" not in chat_message
    assert "false && !isUser && qualityFlags.length" not in chat_message
