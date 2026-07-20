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


@pytest.mark.parametrize(
    "emitted",
    ["web", "WEB", " Web ", "1. Web", "- Web", "Web.", '"Web"', "«Web»"],
)
def test_validate_impact_question_anchors_typographic_option_variants(emitted: str) -> None:
    normalized = validate_impact_question(question(recommendation=emitted, defaultDecision=emitted))
    assert normalized["recommendation"] == "Web"
    assert normalized["defaultDecision"] == "Web"
    assert normalized["defaultDecision"] in normalized["options"]


def test_validate_impact_question_rejects_invented_option_with_actionable_error() -> None:
    with pytest.raises(ImpactQuestionValidationError) as error:
        validate_impact_question(question(defaultDecision="Desktop"))
    message = str(error.value)
    assert "'Desktop'" in message
    assert "['Web', 'Mobile']" in message


def test_validate_impact_question_anchors_a_truncated_option_to_its_full_label() -> None:
    """Reproduce el bloqueo real: el modelo acorta la opcion a su prefijo y el loop quedaba trabado."""
    normalized = validate_impact_question(
        question(
            options=["Inspect codebase for frontend tech", "Assume Angular"],
            recommendation="Inspect codebase for frontend tech",
            defaultDecision="Inspect codebase",
        ),
        index=1,
    )
    assert normalized["defaultDecision"] == "Inspect codebase for frontend tech"
    assert normalized["defaultDecision"] in normalized["options"]


def test_validate_impact_question_rejects_a_prefix_shared_by_several_options() -> None:
    """Un prefijo ambiguo no es una truncacion recuperable: no se puede inferir la intencion."""
    with pytest.raises(ImpactQuestionValidationError, match="defaultDecision"):
        validate_impact_question(
            question(
                options=["Inspect codebase for frontend tech", "Inspect codebase for backend tech"],
                recommendation="Inspect codebase for frontend tech",
                defaultDecision="Inspect codebase",
            )
        )


def test_validate_impact_question_rejects_a_prefix_that_cuts_a_word_in_half() -> None:
    """Solo se ancla una truncacion en frontera de palabra; 'Insp' no identifica una opcion."""
    with pytest.raises(ImpactQuestionValidationError, match="defaultDecision"):
        validate_impact_question(
            question(
                options=["Inspect codebase", "Assume Angular"],
                recommendation="Inspect codebase",
                defaultDecision="Insp",
            )
        )


def test_validate_impact_question_does_not_anchor_a_superstring_of_an_option() -> None:
    """'Web y Mobile' no es una truncacion de 'Web': ampliar la opcion cambia la decision."""
    with pytest.raises(ImpactQuestionValidationError, match="defaultDecision"):
        validate_impact_question(question(defaultDecision="Web and Mobile"))


def test_validate_impact_question_rejects_ambiguous_option_match() -> None:
    with pytest.raises(ImpactQuestionValidationError, match="defaultDecision"):
        validate_impact_question(
            question(options=["Web", "web."], recommendation="Web", defaultDecision="WEB")
        )


def test_validate_impact_question_reports_non_string_default_decision_as_option_mismatch() -> None:
    with pytest.raises(ImpactQuestionValidationError, match="must be one of options"):
        validate_impact_question(question(defaultDecision=0))


def test_validate_impact_question_coerces_known_category_synonyms() -> None:
    assert validate_impact_question(question(category="Performance"))["category"] == "nonfunctional"
    assert validate_impact_question(question(category="usability"))["category"] == "ux"
    assert validate_impact_question(question(category="security"))["category"] == "risk"
    assert validate_impact_question(question(category="timeline"))["category"] == "delivery"


def test_validate_impact_question_defaults_unknown_category_and_audits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        normalized = validate_impact_question(question(category="totally-made-up"))
    assert normalized["category"] == "scope"
    assert any("totally-made-up" in record.getMessage() for record in caplog.records)


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


def test_engine_never_suppresses_a_blocking_question_by_bare_category() -> None:
    """Suprimir una bloqueante por categoria deja el turno vacio: 'waiting decision' sin nada que responder."""
    detected = detected_facts_from_assessment(brief={"scope": "Guided wizard."}, existing_questions=[])
    blocking_scope = question(category="scope", question="Java 17 or Java 21?", blocking=True)

    result = ImpactQuestionEngine().select([blocking_scope], detected_facts=detected)

    assert result["counts"]["suppressed"] == 0
    assert [item["question"] for item in result["turn"]] == ["Java 17 or Java 21?"]


def test_engine_still_suppresses_a_blocking_question_already_asked_verbatim() -> None:
    """El dedup exacto si es autoridad suficiente: la misma pregunta ya fue formulada."""
    asked = question(category="scope", question="Java 17 or Java 21?", blocking=True)
    detected = detected_facts_from_assessment(
        brief=None,
        existing_questions=[{"question": "Java 17 or Java 21?", "metadata": {"category": "scope"}}],
    )

    result = ImpactQuestionEngine().select([asked], detected_facts=detected)

    assert result["counts"]["suppressed"] == 1
    assert result["turn"] == []


def test_engine_ranks_a_covered_category_below_an_uncovered_one() -> None:
    """Una categoria ya cubierta degrada la prioridad, no elimina la pregunta."""
    detected = detected_facts_from_assessment(brief={"scope": "Guided wizard."}, existing_questions=[])
    candidates = [
        question(category="scope", question="Blocking scope?", blocking=True),
        question(category="data", question="Blocking data?", blocking=True),
    ]

    turn = ImpactQuestionEngine().select(candidates, detected_facts=detected)["turn"]

    assert [item["question"] for item in turn] == ["Blocking data?", "Blocking scope?"]


def test_engine_validation_propagates_for_malformed_candidates() -> None:
    with pytest.raises(ImpactQuestionValidationError, match=r"questions\[1\].confidence"):
        ImpactQuestionEngine().select([question(), question(confidence="bogus")])
