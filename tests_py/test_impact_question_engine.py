from __future__ import annotations

from typing import Any

import pytest

from local_control_center.agents.impact_question_engine import (
    ImpactQuestionEngine,
    ImpactQuestionValidationError,
    detected_facts_from_assessment,
    impact_score,
    question_dedup_key,
    validate_impact_question,
)


def question(**overrides: Any) -> dict[str, Any]:
    base = {
        "category": "scope",
        "question": "Which platform do we target first?",
        "whyItMatters": "It changes the delivery plan and estimates.",
        "blocking": False,
        "options": ["Web", "Mobile"],
        "recommendation": "Web",
        "defaultDecision": "Web",
        "confidence": "medium",
    }
    base.update(overrides)
    return base


def test_validate_impact_question_normalizes_the_eight_fields() -> None:
    normalized = validate_impact_question(question(category="DATA", confidence="High"))
    assert set(normalized) == {
        "category",
        "question",
        "whyItMatters",
        "blocking",
        "options",
        "recommendation",
        "defaultDecision",
        "confidence",
    }
    assert normalized["category"] == "data"
    assert normalized["confidence"] == "high"
    assert normalized["blocking"] is False


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"category": "unknown"}, "category"),
        ({"confidence": "certain"}, "confidence"),
        ({"blocking": "yes"}, "blocking"),
        ({"options": ["only-one"]}, "options"),
        ({"whyItMatters": ""}, "whyItMatters"),
        ({"recommendation": "Desktop"}, "recommendation"),
        ({"defaultDecision": "Desktop"}, "defaultDecision"),
    ],
)
def test_validate_impact_question_rejects_invalid_contracts(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ImpactQuestionValidationError, match=fragment):
        validate_impact_question(question(**overrides))


def test_impact_score_orders_blocking_then_low_confidence_then_category() -> None:
    blocking = question(blocking=True, confidence="high", category="ux")
    low_conf = question(blocking=False, confidence="low", category="ux")
    high_conf = question(blocking=False, confidence="high", category="ux")
    assert impact_score(blocking) > impact_score(low_conf) > impact_score(high_conf)
    # Same blocking/confidence, higher-weight category wins.
    assert impact_score(question(category="compliance")) > impact_score(question(category="ux"))


def test_engine_groups_at_most_five_per_turn_and_defers_the_rest() -> None:
    candidates = [
        question(question=f"Q{index}", category="ux", confidence="high", blocking=False) for index in range(6)
    ]
    # Make one clearly highest-impact and one clearly lowest so ordering is deterministic.
    candidates[0] = question(question="Q0-blocking", blocking=True, confidence="low", category="compliance")
    result = ImpactQuestionEngine().select(candidates)
    assert result["counts"] == {"candidates": 6, "asked": 5, "deferred": 1, "suppressed": 0}
    assert len(result["turn"]) == 5
    assert result["turn"][0]["question"] == "Q0-blocking"
    assert len(result["deferred"]) == 1


def test_engine_does_not_ask_data_already_detected_in_the_repository() -> None:
    repo_question = question(category="users", question="Who are the primary users?")
    detected = detected_facts_from_assessment(
        brief={"scope": "Guided wizard."},  # scope category already covered
        existing_questions=[{"question": "Who are the primary users?", "metadata": {"category": "users"}}],
    )
    assert "scope" in detected
    assert question_dedup_key(repo_question) in detected

    candidates = [
        repo_question,  # suppressed: exact key already asked
        question(category="scope", question="What is in scope for v1?"),  # suppressed: category detected
        question(category="data", question="Which records must be retained?", confidence="low"),  # kept
    ]
    result = ImpactQuestionEngine().select(candidates, detected_facts=detected)
    assert result["counts"]["suppressed"] == 2
    assert [q["category"] for q in result["turn"]] == ["data"]


def test_engine_validation_propagates_for_malformed_candidates() -> None:
    with pytest.raises(ImpactQuestionValidationError, match=r"questions\[1\].category"):
        ImpactQuestionEngine().select([question(), question(category="bogus")])
