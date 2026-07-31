from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.agents.product_owner_agent import (
    ProductOwnerAgent,
    ProductOwnerAgentRunner,
    ProductOwnerOutputValidationError,
    _supplied_assessment_signals,
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


def test_runner_shaped_supplied_assessment_is_compressed_to_signals() -> None:
    """El assessment_result completo del coordinator se reduce a señales antes de entrar al prompt.

    El ProjectAssessmentRunner devuelve {assessment, findings, artifact, decision, job, agentRun};
    al modelo solo deben llegar las señales decision-relevant de _codebase_signals, no los registros
    internos ni un finding por módulo/endpoint del repo.
    """
    supplied = {
        "status": "completed",
        "assessment": {
            "id": "assessment-1",
            "summary": {"stack": ["Python"], "hasTests": True, "riskCount": 1, "gapCount": 1},
        },
        "findings": [
            {"category": "risk", "title": "Committed .env"},
            {"category": "gap", "title": "No security tooling"},
            {"category": "module", "title": "local_control_center/product_loop"},
        ],
        "artifact": {"id": "artifact-1", "path": "/tmp/x.json", "hash": "abc"},
        "decision": {"id": "decision-1"},
        "job": {"id": "job-1"},
        "agentRun": {"id": "agent-run-1"},
    }

    signals = _supplied_assessment_signals(supplied)

    assert signals["assessmentId"] == "assessment-1"
    assert signals["stack"] == ["Python"]
    assert signals["risks"] == ["Committed .env"]
    assert signals["gaps"] == ["No security tooling"]
    # Los registros internos y el catálogo de módulos no viajan al prompt.
    serialized = str(signals)
    for noise in ("artifact-1", "job-1", "agent-run-1", "decision-1", "product_loop"):
        assert noise not in serialized


def test_flat_supplied_assessment_passes_through_unchanged() -> None:
    """Un assessment ya plano (stack/risks/gaps) entra tal cual: es la forma acotada histórica."""
    flat = {"stack": ["Python"], "hasTests": True, "risks": ["x"], "gaps": []}
    assert _supplied_assessment_signals(flat) is flat


def test_existing_initiative_and_brief_are_projected_to_content_fields() -> None:
    """El contexto del prompt no arrastra uuids/timestamps de initiative y brief."""
    agent = ProductOwnerAgent()
    assessment = {
        "initiative": {
            "id": "initiative-uuid-1234",
            "projectId": "project-uuid-5678",
            "title": "Onboarding",
            "summary": "Self-serve onboarding flow.",
            "status": "active",
            "priority": "high",
            "owner": "po",
            "version": 3,
            "metadata": {"threadId": "thread-uuid-9999"},
            "createdAt": "2026-07-01T00:00:00Z",
            "updatedAt": "2026-07-02T00:00:00Z",
        },
        "brief": {
            "id": "brief-uuid-4321",
            "projectId": "project-uuid-5678",
            "initiativeId": "initiative-uuid-1234",
            "title": "Onboarding brief",
            "summary": "Brief summary.",
            "problemStatement": "Users churn during setup.",
            "goals": ["Reduce churn"],
            "targetUsers": ["New users"],
            "successMetrics": ["Activation rate"],
            "scope": "Signup flow",
            "outOfScope": "Billing",
            "status": "draft",
            "version": 2,
            "createdAt": "2026-07-01T00:00:00Z",
            "updatedAt": "2026-07-02T00:00:00Z",
        },
    }

    context = agent._assessment_context(idea="Improve onboarding.", assessment=assessment)

    assert context["existingInitiative"] == {
        "title": "Onboarding",
        "summary": "Self-serve onboarding flow.",
        "status": "active",
    }
    brief = context["existingBrief"]
    assert brief["problemStatement"] == "Users churn during setup."
    assert brief["status"] == "draft"
    assert brief["version"] == 2
    serialized = str(context)
    for noise in (
        "initiative-uuid-1234",
        "brief-uuid-4321",
        "project-uuid-5678",
        "thread-uuid-9999",
        "createdAt",
    ):
        assert noise not in serialized


def test_context_budget_drops_oldest_but_never_the_most_recent_answer() -> None:
    """El presupuesto agregado corta lo antiguo; la respuesta más reciente jamás se descarta."""
    from local_control_center.agents.product_owner_agent import (
        PROMPT_CONTEXT_BUDGET_CHARS,
        _apply_context_budget,
    )

    facts = [
        {"question": f"q{index}", "answer": "a" * 2_000, "answeredBy": "operator"} for index in range(40)
    ]
    context = {
        "existingOpenQuestions": ["x"],
        "existingUnresolvedDecisions": [],
        "resolvedFacts": facts,
        "settledDecisions": [],
    }
    bounded = _apply_context_budget(dict(context))

    import json as _json

    total = sum(
        len(_json.dumps(bounded.get(name) or [], ensure_ascii=False))
        for name in (
            "existingOpenQuestions",
            "existingUnresolvedDecisions",
            "resolvedFacts",
            "settledDecisions",
        )
    )
    assert total <= PROMPT_CONTEXT_BUDGET_CHARS
    assert bounded["omittedResolvedFacts"] > 0
    # Drop-oldest: el fact más reciente (q39) sobrevive; el más antiguo (q0) se fue.
    surviving = [fact["question"] for fact in bounded["resolvedFacts"]]
    assert "q39" in surviving
    assert "q0" not in surviving

    small = {
        "existingOpenQuestions": ["una"],
        "existingUnresolvedDecisions": [],
        "resolvedFacts": [{"question": "q", "answer": "a", "answeredBy": "op"}],
        "settledDecisions": [],
    }
    assert _apply_context_budget(dict(small)) == small
