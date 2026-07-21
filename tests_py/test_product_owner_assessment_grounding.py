from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.agents.product_owner_agent import (
    ProductOwnerAgent,
    ProductOwnerAgentRunner,
    ProductOwnerOutputValidationError,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_project_assessment import build_sample_project


def test_runner_creates_then_reuses_project_assessment_for_grounding(tmp_path: Path) -> None:
    project_root = tmp_path / "sample"
    build_sample_project(project_root)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = ProjectsRepository(connection)
        project = repository.create_project(name="Sample", path=project_root, template_id="other")
        runner = ProductOwnerAgentRunner(connection, root=tmp_path)

        # First call produces the assessment so the agent is grounded before asking the user.
        signals = runner._project_assessment_signals(project["id"])
        assert signals is not None
        assert "Python" in signals["stack"]
        assert signals["riskCount"] >= 1  # the committed .env in the sample project
        assert isinstance(signals["risks"], list)
        assert len(repository.list_project_assessments(project["id"])) == 1

        # A subsequent call reuses the latest assessment instead of re-walking the tree.
        runner._project_assessment_signals(project["id"])
        assert len(repository.list_project_assessments(project["id"])) == 1


def test_runner_returns_none_when_no_assessment_can_be_produced(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        runner = ProductOwnerAgentRunner(connection, root=tmp_path)
        # An unknown project cannot be assessed; the agent degrades gracefully (no grounding).
        assert runner._project_assessment_signals("project-does-not-exist") is None


def test_assessment_context_injects_codebase_signals_into_prompt() -> None:
    agent = ProductOwnerAgent()
    assessment = {
        "idea": "Add a self-serve onboarding flow.",
        "initiative": None,
        "projectAssessment": {
            "stack": ["Python", "TypeScript"],
            "hasTests": True,
            "risks": ["High technical-debt marker density"],
            "gaps": ["No security tooling detected"],
        },
    }

    messages = agent.model_messages(idea=assessment["idea"], assessment=assessment)
    user_payload = messages[1]["content"]

    assert "codebaseSignals" in user_payload
    assert "High technical-debt marker density" in user_payload
    assert "No security tooling detected" in user_payload


def test_prompt_includes_project_goal_section() -> None:
    agent = ProductOwnerAgent()
    assessment = {"idea": "Add onboarding.", "initiative": None, "projectAssessment": None}
    goal = "Plataforma de onboarding self-serve completa, de registro a activacion."

    prompt = agent.cli_prompt(idea=assessment["idea"], assessment=assessment, goal_statement=goal)
    messages = agent.model_messages(idea=assessment["idea"], assessment=assessment, goal_statement=goal)

    assert "projectGoal" in prompt
    assert goal in prompt
    assert "projectGoal" in messages[1]["content"]
    assert goal in messages[1]["content"]


def test_prompt_carries_resolved_facts_so_answered_questions_are_not_re_asked() -> None:
    """Sin esto el prompt es MENOS informativo tras responder: la respuesta borraba su propia evidencia."""
    agent = ProductOwnerAgent()
    assessment = {
        "idea": "Refactoriza y actualiza Spring Boot.",
        "initiative": None,
        "projectAssessment": None,
        "resolvedFacts": [
            {"question": "Which JDK version do we target?", "answer": "JDK 17", "answeredBy": "workspace"}
        ],
    }

    prompt = agent.cli_prompt(idea=assessment["idea"], assessment=assessment)
    messages = agent.model_messages(idea=assessment["idea"], assessment=assessment)

    for payload in (prompt, messages[1]["content"]):
        assert "resolvedFacts" in payload
        assert "Which JDK version do we target?" in payload
        assert "JDK 17" in payload


def test_prompt_carries_settled_decisions() -> None:
    agent = ProductOwnerAgent()
    assessment = {
        "idea": "Refactoriza y actualiza Spring Boot.",
        "initiative": None,
        "projectAssessment": None,
        "settledDecisions": [
            {
                "title": "AIDO decide: frontend stack",
                "decision": "Migrate to React",
                "rationale": "Team skill",
            }
        ],
    }

    payload = agent.model_messages(idea=assessment["idea"], assessment=assessment)[1]["content"]

    assert "settledDecisions" in payload
    assert "Migrate to React" in payload


def test_system_instruction_binds_the_agent_to_resolved_facts() -> None:
    instruction = ProductOwnerAgent()._system_instruction()
    assert "resolvedFacts" in instruction
    assert "settledDecisions" in instruction


def _valid_output(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "status": "backlog_ready",
        "summary": "Upgrade Spring Boot.",
        "confidence": "medium",
        "questions": [],
        "assumptions": [],
        "decisions": [],
        "productBriefPatch": {"title": "Upgrade"},
        "epics": [{"title": "Migration"}],
        "userStories": [
            {
                "epicTitle": "Migration",
                "title": "Run on Java 17",
                "asA": "developer",
                "iWant": "the app on Java 17",
                "soThat": "it stays supported",
                "acceptanceCriteria": ["Builds on Java 17"],
            }
        ],
        "risks": [],
        "recommendedNextAction": "Approve the backlog.",
    }
    base.update(overrides)
    return base


def test_advisory_enums_are_graded_down_instead_of_failing_the_whole_run() -> None:
    """Un enum advisory fuera de catalogo graduaba el brief entero a failed_validation."""
    output = ProductOwnerAgent().validate_output(
        _valid_output(
            confidence="very high",
            risks=[{"severity": "catastrophic", "description": "Breaking API change"}],
            userStories=[
                {
                    "epicTitle": "Migration",
                    "title": "Run on Java 17",
                    "asA": "developer",
                    "iWant": "the app on Java 17",
                    "soThat": "it stays supported",
                    "businessValue": "critical",
                    "acceptanceCriteria": ["Builds on Java 17"],
                }
            ],
        )
    )

    assert output["confidence"] == "medium"
    assert output["risks"][0]["severity"] == "medium"
    assert output["userStories"][0]["businessValue"] == "medium"


def test_an_unknown_reversibility_grades_to_the_conservative_side() -> None:
    """Degradar nunca puede abrir una puerta: reversibilidad desconocida escala, no auto-decide."""
    output = ProductOwnerAgent().validate_output(
        _valid_output(
            decisions=[{"title": "Pick a stack", "status": "proposed", "reversibility": "somewhat"}]
        )
    )
    assert output["decisions"][0]["reversibility"] == "irreversible"


def test_decision_status_stays_strict_because_it_routes_the_flow() -> None:
    """``status`` no es advisory: decide si el backlog se retiene, asi que un valor invalido debe fallar."""
    with pytest.raises(ProductOwnerOutputValidationError, match="status"):
        ProductOwnerAgent().validate_output(
            _valid_output(decisions=[{"title": "Pick a stack", "status": "kind-of-open"}])
        )


def test_system_instruction_declares_the_forbidden_technical_story_keys() -> None:
    instruction = ProductOwnerAgent()._system_instruction()
    for key in ("role", "agentRole", "technicalTask"):
        assert key in instruction


def test_user_story_schema_documents_the_forbidden_technical_keys() -> None:
    """La regla de no-tarea-técnica vive en el esquema, así ambas ramas del prompt la llevan."""
    from local_control_center.agents.product_owner_agent_contract import TECHNICAL_STORY_KEYS

    item = ProductOwnerAgent().contract()["outputSchema"]["properties"]["userStories"]["items"]
    for key in TECHNICAL_STORY_KEYS:
        assert key in item["description"]


def test_null_brief_list_field_is_tolerated_as_empty_not_a_hard_failure() -> None:
    """Un campo de lista en null lo trataba como error fatal; ahora baja la completitud, no bloquea."""
    output = ProductOwnerAgent().validate_output(
        _valid_output(productBriefPatch={"title": "T", "goals": None})
    )
    assert output["productBriefPatch"]["goals"] == []


def test_brief_list_field_drops_blank_entries_instead_of_rejecting_the_whole_output() -> None:
    output = ProductOwnerAgent().validate_output(
        _valid_output(productBriefPatch={"title": "T", "successMetrics": ["Real metric", "", "  "]})
    )
    assert output["productBriefPatch"]["successMetrics"] == ["Real metric"]


def test_acceptance_criteria_still_fails_closed_when_empty_after_dropping_blanks() -> None:
    story = {
        "epicTitle": "Migration",
        "title": "Run on Java 17",
        "asA": "developer",
        "iWant": "the app on Java 17",
        "soThat": "it stays supported",
        "acceptanceCriteria": [None, ""],
    }
    with pytest.raises(ProductOwnerOutputValidationError, match="acceptanceCriteria"):
        ProductOwnerAgent().validate_output(_valid_output(userStories=[story]))


def test_validate_output_no_longer_carries_the_unread_model_completeness_key() -> None:
    output = ProductOwnerAgent().validate_output(_valid_output(completeness={"score": 99}))
    assert "modelCompleteness" not in output


def test_prompt_unchanged_without_goal() -> None:
    agent = ProductOwnerAgent()
    assessment = {"idea": "Add onboarding.", "initiative": None, "projectAssessment": None}

    legacy_prompt = agent.cli_prompt(idea=assessment["idea"], assessment=assessment)
    explicit_none = agent.cli_prompt(idea=assessment["idea"], assessment=assessment, goal_statement=None)
    empty_string = agent.cli_prompt(idea=assessment["idea"], assessment=assessment, goal_statement="")

    assert explicit_none == legacy_prompt
    assert empty_string == legacy_prompt
    assert "projectGoal" not in legacy_prompt
