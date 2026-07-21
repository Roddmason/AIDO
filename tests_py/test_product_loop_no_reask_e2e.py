"""End-to-end: el loop no vuelve a preguntar algo que ya se respondió.

Ejerce el Runner REAL (`ProductOwnerAgentRunner`) a través del coordinator en DOS turnos del mismo
hilo, con solo la ejecución de red del runtime sustituida por JSON canónico. Todo lo demás —el
assessment que carga las preguntas ya formuladas, la supresión por `detected_facts` del motor de
impacto, la persistencia y la reconciliación del coordinator— corre de verdad. Si el fix del hilo
(estampar `threadId`) o el de doble-persistencia estuviera roto, el turno 2 repreguntaría el JDK.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.impact_question_engine import ImpactQuestionEngine
from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_product_loop_coordinator import (
    _AssessmentRunner,
    _GitGate,
    _seed_ai_resource,
    _workspace_project,
)


def _mock_ollama_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda *, base_url=None, credential_ref=None: {
            "provider": "ollama",
            "available": True,
            "models": ["qwen2.5-coder"],
            "reason": "Controlled Ollama daemon.",
        },
    )


JDK_QUESTION = {
    "category": "scope",
    "question": "Which JDK version is the target?",
    "whyItMatters": "It changes the migration plan and estimates.",
    "blocking": True,
    "options": ["Java 17", "Java 21"],
    "recommendation": "Java 17",
    "defaultDecision": "Java 17",
    "confidence": "low",
}
RETENTION_QUESTION = {
    "category": "data",
    "question": "Which records must be retained?",
    "whyItMatters": "It changes the data model and estimates.",
    "blocking": True,
    "options": ["All", "Recent only"],
    "recommendation": "All",
    "defaultDecision": "All",
    "confidence": "low",
}


def _needs_input(questions: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "status": "needs_input",
            "summary": "Spring Boot upgrade needs product facts before a backlog.",
            "confidence": "low",
            "questions": questions,
            "assumptions": [],
            "decisions": [],
            "productBriefPatch": {"title": "Spring Boot upgrade"},
            "epics": [],
            "userStories": [],
            "risks": [],
            "recommendedNextAction": "Answer the blocking questions before generating the backlog.",
        }
    )


class _CannedRuntimeProductOwnerRunner(ProductOwnerAgentRunner):
    """Runner REAL cuyo único stub es la ejecución de red: devuelve JSON canónico por turno.

    Conserva assessment + `detected_facts` + `ImpactQuestionEngine.select` + persistencia reales, que
    es justo donde vive la supresión de la repregunta.
    """

    def __init__(self, connection: Any, *, root: Path, responses: list[str]) -> None:
        super().__init__(connection, root=root)
        self._responses = list(responses)
        self._artifact_counter = 0

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        return {
            "executable": True,
            "status": "executable",
            "selectedRuntimeId": preferred_runtime or "controlled_product_owner_runtime",
            "reason": "Controlled ProductOwnerAgent runtime is executable.",
        }

    def _execute_once(
        self,
        *,
        payload,
        runtime,
        workspace,
        agent_run,
        job,
        profile,
        broker,
        assessment,
        detected_facts,
        epic,
        epic_expansion,
        repair,
    ):
        text = self._responses.pop(0)
        self._artifact_counter += 1
        artifact_id = f"artifact-canned-po-{uuid.uuid4()}"
        artifact_file = write_text_artifact(
            root=self.root, artifact_id=artifact_id, suffix=".json", content=text
        )
        self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=str(payload["projectId"]),
            evidence_package_id=None,
            kind="product_owner_runtime_output",
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={"source": "controlled", "mimeType": "application/json"},
        )
        output = self.agent.validate_output(self._json_object_from_text(text))
        if epic:
            self.agent.validate_epic_expansion_output(output, epic_title=epic["title"])
        selection = ImpactQuestionEngine().select(output["questions"], detected_facts=detected_facts)
        return {
            "kind": "ok",
            "output": output,
            "selection": selection,
            "runtimeResult": {"status": "completed", "reason": "controlled", "outputArtifactId": artifact_id},
            "outputArtifactId": artifact_id,
        }


def _run_turn(
    coordinator: ProductLoopCoordinator, *, project_id: str, message: str, runner: Any, thread_id: str | None
) -> dict[str, Any]:
    return coordinator.run_user_message(
        project_id=project_id,
        message=message,
        thread_id=thread_id,
        run_metadata={"userMode": "aido_decide", "autonomy": "guided"},
        git_service=_GitGate(),
        product_owner_runner=runner,
        assessment_runner=_AssessmentRunner(),
    )


def _thread_id_of(result: dict[str, Any]) -> str:
    return result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]


def test_the_loop_does_not_re_ask_an_answered_question_across_two_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_ollama_daemon(monkeypatch)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "spring-upgrade")
        discovery = ProductDiscoveryRepository(connection)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        # Turn 1: the PO asks the blocking JDK question and the loop blocks awaiting the answer.
        turn1 = _run_turn(
            coordinator,
            project_id=project["id"],
            message="Refactoriza y actualiza Spring Boot a la ultima version compatible con mi JDK.",
            runner=_CannedRuntimeProductOwnerRunner(
                connection, root=tmp_path, responses=[_needs_input([JDK_QUESTION])]
            ),
            thread_id=None,
        )
        thread_id = _thread_id_of(turn1)

        initiative = discovery.find_initiative_by_thread(project["id"], thread_id)
        assert initiative is not None, "turn 1 must create an initiative bound to the thread"
        assert (initiative.get("metadata") or {}).get("threadId") == thread_id
        questions_after_turn1 = discovery.list_clarification_questions(initiative_id=initiative["id"])
        jdk_rows = [q for q in questions_after_turn1 if "JDK" in q["question"]]
        assert len(jdk_rows) == 1, "the JDK question must be persisted exactly once (no duplicate)"
        jdk_question = jdk_rows[0]

        # The user answers it (mirrors aido_decide_product_loop).
        discovery.create_clarification_answer(
            {
                "projectId": project["id"],
                "questionId": jdk_question["id"],
                "initiativeId": initiative["id"],
                "answer": "Java 17",
                "status": "accepted",
                "answeredBy": "workspace",
            }
        )
        discovery.update_clarification_question(
            jdk_question["id"],
            {
                "status": "answered",
                "metadata": {**(jdk_question.get("metadata") or {}), "aidoDecision": "Java 17"},
            },
        )

        # Turn 2: the PO would emit the SAME JDK question plus a new one; the real engine must suppress
        # the JDK one because the initiative (found by threadId) now carries it as answered.
        turn2 = _run_turn(
            coordinator,
            project_id=project["id"],
            message="Sigamos con la migracion.",
            runner=_CannedRuntimeProductOwnerRunner(
                connection, root=tmp_path, responses=[_needs_input([JDK_QUESTION, RETENTION_QUESTION])]
            ),
            thread_id=thread_id,
        )

        # Same initiative reused across the two turns.
        initiative_after_turn2 = discovery.find_initiative_by_thread(project["id"], thread_id)
        assert initiative_after_turn2["id"] == initiative["id"]

        questions_after_turn2 = discovery.list_clarification_questions(initiative_id=initiative["id"])
        jdk_rows_after = [q for q in questions_after_turn2 if "JDK" in q["question"]]
        assert len(jdk_rows_after) == 1, "the answered JDK question must NOT be asked (or persisted) again"
        assert jdk_rows_after[0]["status"] == "answered"

        # The new question did surface, so the loop advanced rather than stalling.
        retention_rows = [q for q in questions_after_turn2 if "records" in q["question"]]
        assert len(retention_rows) == 1, "the genuinely new question must be surfaced on turn 2"

        # And the PO output for turn 2 confirms the JDK question was suppressed, not re-asked.
        turn2_output = turn2["productOwnerResult"]["output"] if "productOwnerResult" in turn2 else None
        if turn2_output is not None:
            asked_texts = {q["question"] for q in turn2_output.get("questions", [])}
            assert "Which JDK version is the target?" not in asked_texts
