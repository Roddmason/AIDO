from __future__ import annotations

from typing import Any

import pytest

from local_control_center.agents.autonomy_profiles import (
    AutonomyEngine,
    AutonomyProfile,
    AutonomyValidationError,
    decision_record,
    resolve_action,
    validate_decision,
)


def decision(**overrides: Any) -> dict[str, Any]:
    base = {
        "category": "product",
        "decision": "Pick the onboarding flow",
        "options": ["Guided wizard", "Manual setup"],
        "chosen": "Guided wizard",
        "reason": "It reduces time-to-value for SMB users.",
        "confidence": "high",
        "reversibility": "reversible",
        "blocking": False,
    }
    base.update(overrides)
    return base


def test_profile_resolves_effective_level_with_category_overrides() -> None:
    profile = AutonomyProfile(level="autonomous", overrides={"security": "guided", "release": "recommended"})
    assert profile.effective_level("product") == "autonomous"
    assert profile.effective_level("security") == "guided"
    assert profile.effective_level("release") == "recommended"
    assert profile.to_dict() == {
        "level": "autonomous",
        "overrides": {"security": "guided", "release": "recommended"},
    }


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"level": "yolo"}, "level"),
        ({"level": "autonomous", "overrides": {"unknown": "guided"}}, "category"),
        ({"level": "autonomous", "overrides": {"security": "yolo"}}, "level"),
    ],
)
def test_profile_rejects_invalid_configuration(kwargs: dict[str, Any], fragment: str) -> None:
    with pytest.raises(AutonomyValidationError, match=fragment):
        AutonomyProfile(**kwargs)


def test_effective_level_rejects_unknown_category() -> None:
    with pytest.raises(AutonomyValidationError, match="category"):
        AutonomyProfile(level="autonomous").effective_level("marketing")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"category": "marketing"}, "category"),
        ({"confidence": "sure"}, "confidence"),
        ({"reversibility": "maybe"}, "reversibility"),
        ({"blocking": "yes"}, "blocking"),
        ({"options": ["only-one"]}, "options"),
        ({"chosen": "Desktop"}, "chosen"),
        ({"reason": ""}, "reason"),
    ],
)
def test_validate_decision_rejects_invalid_contracts(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(AutonomyValidationError, match=fragment):
        validate_decision(decision(**overrides))


@pytest.mark.parametrize(
    ("level", "blocking", "confidence", "reversibility", "expected"),
    [
        ("guided", False, "high", "reversible", "ask"),
        ("guided", False, "high", "irreversible", "ask"),
        ("recommended", False, "high", "reversible", "auto"),
        ("recommended", True, "high", "reversible", "ask"),
        ("recommended", False, "medium", "recoverable", "recommend"),
        ("autonomous", False, "high", "reversible", "auto"),
        ("autonomous", False, "medium", "recoverable", "auto"),
        ("autonomous", False, "high", "irreversible", "ask"),
        ("autonomous", True, "low", "reversible", "ask"),
        ("autonomous", False, "low", "reversible", "recommend"),
    ],
)
def test_resolve_action_matrix(
    level: str, blocking: bool, confidence: str, reversibility: str, expected: str
) -> None:
    assert (
        resolve_action(level, blocking=blocking, confidence=confidence, reversibility=reversibility)
        == expected
    )


def test_decision_record_captures_alternatives_reason_confidence_and_reversibility() -> None:
    record = decision_record(validate_decision(decision()), level="autonomous", action="auto")
    assert record["alternatives"] == ["Manual setup"]
    assert record["reason"]
    assert record["confidence"] == "high"
    assert record["reversibility"] == "reversible"
    assert record["automatic"] is True


def test_engine_partitions_batch_and_every_automatic_record_is_auditable() -> None:
    engine = AutonomyEngine(AutonomyProfile(level="autonomous", overrides={"security": "guided"}))
    decisions = [
        decision(category="product", confidence="high", reversibility="reversible"),  # auto
        decision(category="security", confidence="high", reversibility="reversible"),  # override -> ask
        decision(
            category="budget", confidence="low", reversibility="reversible", blocking=False
        ),  # recommend
        decision(category="release", confidence="high", reversibility="irreversible"),  # ask
    ]
    result = engine.resolve_all(decisions)
    assert result["counts"] == {"automatic": 1, "recommended": 1, "escalated": 2}
    assert result["automatic"][0]["category"] == "product"
    # Every required audit field is present on an automatic decision.
    for required in ("alternatives", "reason", "confidence", "reversibility"):
        assert result["automatic"][0][required] is not None
    assert {item["category"] for item in result["escalated"]} == {"security", "release"}
    assert result["recommended"][0]["category"] == "budget"
    assert result["recommended"][0]["automatic"] is False
