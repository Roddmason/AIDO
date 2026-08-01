"""Re-entry durable del product loop: marcador runActive y supersede de runs interrumpidos.

Un worker que muere a mitad de un run deja el loop en un estado transitorio con ``runActive=True``
huérfano; el próximo run del mismo hilo lo cancela como superseded (lo que además resuelve sus
remediaciones al llegar a terminal). Los cierres controlados apagan el marcador vía ``_run_result``,
así que los estados de espera legítimos (awaiting_user, blocked, brief_ready) jamás se tocan.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _project(connection, tmp_path: Path, name: str):
    return ProjectsRepository(connection).create_project(name=name, path=tmp_path / name, template_id="other")


def _loop_with_run_active(
    coordinator: ProductLoopCoordinator, project_id: str, thread_id: str, *, run_active: bool
):
    return coordinator.start(
        project_id=project_id,
        title="Run",
        context={"durableRun": {"status": "goal_received", "runActive": run_active}},
        correlation_id=thread_id,
    )


def test_interrupted_loop_is_cancelled_when_the_thread_runs_again(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "reentry")
        coordinator = ProductLoopCoordinator(connection)

        zombie = _loop_with_run_active(coordinator, project["id"], "thread-1", run_active=True)
        coordinator.transition(zombie["id"], to_state="workspace_check")
        coordinator.transition(zombie["id"], to_state="git_check")

        coordinator._supersede_interrupted_loops(
            project_id=project["id"], thread_id="thread-1", actor="operator"
        )

        superseded = coordinator.get(zombie["id"])
        assert superseded["state"] == "cancelled"
        transitions = coordinator.list_transitions(zombie["id"])
        assert transitions[-1]["trigger"] == "superseded_interrupted"


def test_waiting_and_foreign_loops_are_never_superseded(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "waiting")
        coordinator = ProductLoopCoordinator(connection)

        # Cierre controlado: runActive quedó apagado => espera legítima, no se toca.
        waiting = _loop_with_run_active(coordinator, project["id"], "thread-1", run_active=False)
        coordinator.transition(waiting["id"], to_state="discovering")
        coordinator.transition(waiting["id"], to_state="awaiting_user")

        # Otro hilo del mismo proyecto: fuera de alcance aunque tenga runActive.
        foreign = _loop_with_run_active(coordinator, project["id"], "thread-2", run_active=True)

        coordinator._supersede_interrupted_loops(
            project_id=project["id"], thread_id="thread-1", actor="operator"
        )

        assert coordinator.get(waiting["id"])["state"] == "awaiting_user"
        assert coordinator.get(foreign["id"])["state"] == "goal_received"


def test_operator_wait_states_survive_supersede_even_with_a_hung_run_active(tmp_path: Path) -> None:
    """Defensa en profundidad: un runActive colgado en awaiting_approval no se cancela como zombi.

    Si _clear_run_active best-effort falla (p. ej. 'database is locked'), el loop queda en un estado
    de espera del operador con runActive=True. El guard por estado debe protegerlo: cancelarlo
    descartaría trabajo entregado esperando aprobación.
    """
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "wait")
        coordinator = ProductLoopCoordinator(connection)

        for state in ("awaiting_user", "awaiting_approval", "awaiting_feedback", "blocked"):
            loop = _loop_with_run_active(coordinator, project["id"], f"thread-{state}", run_active=True)
            # Camina hasta el estado de espera correspondiente (marcador runActive queda colgado True).
            if state == "awaiting_user":
                coordinator.transition(loop["id"], to_state="discovering")
                coordinator.transition(loop["id"], to_state="awaiting_user")
            elif state == "blocked":
                coordinator.transition(loop["id"], to_state="blocked")
            elif state == "awaiting_feedback":
                for target in (
                    "discovering",
                    "brief_ready",
                    "architecture_review",
                    "backlog_ready",
                    "iteration_planning",
                    "branch_ready",
                    "executing",
                    "qa_running",
                    "security_running",
                    "quality_review",
                    "review_ready",
                    "awaiting_approval",
                    "awaiting_feedback",
                ):
                    coordinator.transition(loop["id"], to_state=target)
            else:  # awaiting_approval
                for target in (
                    "discovering",
                    "brief_ready",
                    "architecture_review",
                    "backlog_ready",
                    "iteration_planning",
                    "branch_ready",
                    "executing",
                    "qa_running",
                    "security_running",
                    "quality_review",
                    "review_ready",
                    "awaiting_approval",
                ):
                    coordinator.transition(loop["id"], to_state=target)

            coordinator._supersede_interrupted_loops(
                project_id=project["id"], thread_id=f"thread-{state}", actor="operator"
            )
            assert coordinator.get(loop["id"])["state"] == state, f"{state} fue supersedido por error"


def test_run_result_clears_the_run_active_marker(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "clear")
        coordinator = ProductLoopCoordinator(connection)

        loop = _loop_with_run_active(coordinator, project["id"], "thread-1", run_active=True)
        result = coordinator._run_result(loop, status="brief_ready", reason="done")

        durable = coordinator.get(loop["id"])["context"]["durableRun"]
        assert durable["runActive"] is False
        assert result["status"] == "brief_ready"

        # Con el marcador apagado, un run nuevo del hilo ya no lo supersede.
        coordinator._supersede_interrupted_loops(
            project_id=project["id"], thread_id="thread-1", actor="operator"
        )
        assert coordinator.get(loop["id"])["state"] == "goal_received"
