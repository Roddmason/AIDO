"""El coordinator reconcilia lo que el ProductOwnerAgentRunner real ya persistió, sin duplicar.

En producción el worker llama al coordinator SIN runner inyectado, así que se construye el Runner real,
que persiste iniciativa, preguntas y decisiones. El coordinator debe reusar esos registros (por id) en vez
de volver a insertarlos, y estampar el ``threadId`` en la iniciativa reusada para que el hilo la recupere
en el turno siguiente. Sin esto: filas duplicadas cada turno, decisiones de hilo huérfanas y una iniciativa
sin ``threadId`` que rompe la persistencia por hilo y hace que el PO vuelva a preguntar todo.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository


def _model_question() -> dict[str, object]:
    """La forma id-less que produce validate_output (lo que llega en output['questions'])."""
    return {
        "category": "scope",
        "question": "Which JDK version is the target?",
        "whyItMatters": "It changes the migration plan.",
        "blocking": True,
        "options": ["Java 17", "Java 21"],
        "recommendation": "Java 17",
        "defaultDecision": "Java 17",
        "confidence": "low",
    }


def test_ensure_product_initiative_stamps_thread_id_on_a_reused_runner_initiative(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        discovery = ProductDiscoveryRepository(connection)
        project = projects.create_project(name="P", path=tmp_path / "p", template_id="other")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        # The real Runner creates the initiative WITHOUT a threadId.
        runner_initiative = discovery.create_initiative(
            {"projectId": project["id"], "title": "Upgrade", "owner": PRODUCT_OWNER_AGENT_ID}
        )
        assert (runner_initiative.get("metadata") or {}).get("threadId") is None

        ensured = coordinator._ensure_product_initiative(
            project_id=project["id"],
            message="m",
            title="Upgrade",
            result={"initiative": runner_initiative},
            output={"summary": "x"},
            thread_id="thread-xyz",
        )

        assert ensured["id"] == runner_initiative["id"], "must reuse, not create a new initiative"
        assert (ensured.get("metadata") or {}).get("threadId") == "thread-xyz"
        # The whole point: the next turn can find it by thread and reuse discovery state.
        found = discovery.find_initiative_by_thread(project["id"], "thread-xyz")
        assert found is not None and found["id"] == runner_initiative["id"]


def _project_thread_initiative(connection, tmp_path: Path):
    projects = ProjectsRepository(connection)
    discovery = ProductDiscoveryRepository(connection)
    threads = ThreadsRepository(connection)
    project = projects.create_project(name="P", path=tmp_path / "p", template_id="other")
    thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-1", title="Upgrade Spring Boot"
    )
    initiative = discovery.create_initiative(
        {"projectId": project["id"], "title": "Upgrade", "owner": PRODUCT_OWNER_AGENT_ID}
    )
    return project, thread, initiative, discovery, threads


def test_persist_clarification_questions_reuses_runner_records_without_duplicating(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread, initiative, discovery, threads = _project_thread_initiative(connection, tmp_path)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        # The Runner already persisted the clarification question (id-bearing, options in metadata).
        runner_q = discovery.create_clarification_question(
            {
                "projectId": project["id"],
                "initiativeId": initiative["id"],
                "question": "Which JDK version is the target?",
                "askedBy": PRODUCT_OWNER_AGENT_ID,
                "metadata": {"options": ["Java 17", "Java 21"], "category": "scope", "blocking": True},
            }
        )

        records = coordinator._persist_clarification_questions(
            project_id=project["id"],
            initiative_id=initiative["id"],
            output={"questions": [_model_question()]},
            thread_id=thread["id"],
            result={"questions": [runner_q]},
        )

        stored = discovery.list_clarification_questions(initiative_id=initiative["id"])
        assert len(stored) == 1, "must not re-insert the question the Runner already wrote"
        assert records[0]["id"] == runner_q["id"]
        decisions = threads.list_decisions(thread["id"])
        linked = [
            d for d in decisions if (d.get("metadata") or {}).get("clarificationQuestionId") == runner_q["id"]
        ]
        assert len(linked) == 1, "the reused question must still surface as exactly one thread decision"
        assert linked[0]["options"] == ["Java 17", "Java 21"]


def test_persist_clarification_questions_still_creates_when_runner_did_not_persist(tmp_path: Path) -> None:
    """Camino stub/tests: sin registros persistidos, el coordinator sigue creando desde output."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread, initiative, discovery, threads = _project_thread_initiative(connection, tmp_path)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        records = coordinator._persist_clarification_questions(
            project_id=project["id"],
            initiative_id=initiative["id"],
            output={"questions": [_model_question()]},
            thread_id=thread["id"],
            result=None,
        )

        stored = discovery.list_clarification_questions(initiative_id=initiative["id"])
        assert len(stored) == 1
        assert records[0]["id"] == stored[0]["id"]
        assert threads.list_decisions(thread["id"])[0]["options"] == ["Java 17", "Java 21"]


def test_persist_product_decisions_reuses_runner_records_without_duplicating(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread, initiative, discovery, threads = _project_thread_initiative(connection, tmp_path)
        brief = discovery.upsert_product_brief(
            {"projectId": project["id"], "initiativeId": initiative["id"], "title": "Upgrade"}
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        # The Runner persisted the escalated (proposed) decision with its options in metadata.
        runner_d = discovery.create_product_decision(
            {
                "projectId": project["id"],
                "initiativeId": initiative["id"],
                "briefId": brief["id"],
                "title": "Pick the frontend stack",
                "status": "proposed",
                "metadata": {"options": ["React", "Angular"], "blocking": True},
            }
        )

        records = coordinator._persist_product_decisions(
            project_id=project["id"],
            initiative_id=initiative["id"],
            brief_id=brief["id"],
            output={
                "decisions": [
                    {
                        "title": "Pick the frontend stack",
                        "status": "proposed",
                        "options": ["React", "Angular"],
                        "blocking": True,
                    }
                ]
            },
            thread_id=thread["id"],
            result={"blockingDecisions": [runner_d]},
        )

        stored = discovery.list_product_decisions(initiative_id=initiative["id"])
        assert len(stored) == 1, "must not re-insert the decision the Runner already wrote"
        assert records[0]["id"] == runner_d["id"]
        decisions = threads.list_decisions(thread["id"])
        linked = [
            d for d in decisions if (d.get("metadata") or {}).get("productDecisionId") == runner_d["id"]
        ]
        assert len(linked) == 1
        assert linked[0]["options"] == ["React", "Angular"]
