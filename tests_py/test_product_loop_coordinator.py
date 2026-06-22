from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.product_loop.coordinator import (
    ProductLoopCoordinator,
    ProductLoopTransitionError,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

LOOP_TABLES = {"product_loops", "product_loop_transitions"}
HAPPY_PATH = [
    "discovery_running",
    "brief_ready",
    "awaiting_architecture_decision",
    "backlog_draft",
    "backlog_review",
    "ready_for_planning",
    "iteration_running",
    "quality_review",
    "completed",
]


def _project(connection, tmp_path: Path, name: str) -> dict:
    return ProjectsRepository(connection).create_project(name=name, path=tmp_path / name, template_id="other")


def test_product_loop_schema_adds_tables_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase19_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 19"
        ).fetchone()["total"]

    assert tables >= LOOP_TABLES
    assert phase19_rows == 1


def test_product_loop_walks_the_happy_path_to_completed(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "happy")
        coordinator = ProductLoopCoordinator(connection)

        loop = coordinator.start(project_id=project["id"], title="Onboarding")
        assert loop["state"] == "idea_received"
        assert loop["status"] == "active"
        assert loop["version"] == 1

        for state in HAPPY_PATH:
            loop = coordinator.transition(loop["id"], to_state=state)

        assert loop["state"] == "completed"
        assert loop["status"] == "completed"
        assert loop["version"] == 1 + len(HAPPY_PATH)
        resume = coordinator.resume(loop["id"])
        assert resume["resumable"] is False
        assert resume["allowedNextStates"] == []
        assert [t["toState"] for t in coordinator.list_transitions(loop["id"])] == [
            "idea_received",
            *HAPPY_PATH,
        ]


def test_product_loop_is_durable_and_resumes_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"

    # --- AIDO session 1: start the loop and advance it, then "shut down". ---
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "durable")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(
            project_id=project_id, title="Self-serve", context={"idea": "self-serve onboarding"}
        )
        coordinator.transition(loop["id"], to_state="discovery_running", trigger="discovery_started")
        coordinator.transition(loop["id"], to_state="awaiting_user", context_patch={"openQuestions": 3})
        loop_id = loop["id"]

    # --- Restart AIDO: a brand-new connection and coordinator, nothing kept in memory. ---
    with open_sqlite_connection(db_path) as connection:
        coordinator = ProductLoopCoordinator(connection)
        resumed = coordinator.resume(loop_id)
        assert resumed["loop"]["state"] == "awaiting_user"  # recovered straight from the database
        assert resumed["loop"]["version"] == 3
        assert resumed["loop"]["context"] == {"idea": "self-serve onboarding", "openQuestions": 3}
        assert resumed["resumable"] is True
        assert "discovery_running" in resumed["allowedNextStates"]

        # The loop continues exactly where it left off before the restart.
        coordinator.transition(loop_id, to_state="discovery_running", trigger="user_answered")
        loop = coordinator.transition(loop_id, to_state="brief_ready", trigger="discovery_completed")
        assert loop["state"] == "brief_ready"
        assert [t["toState"] for t in coordinator.list_transitions(loop_id)] == [
            "idea_received",
            "discovery_running",
            "awaiting_user",
            "discovery_running",
            "brief_ready",
        ]
        assert coordinator.list_loops(project_id)[0]["id"] == loop_id


def test_invalid_and_unknown_transitions_are_rejected(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "invalid")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.transition(loop["id"], to_state="completed")
        with pytest.raises(ProductLoopTransitionError, match="Unknown product loop state"):
            coordinator.transition(loop["id"], to_state="not_a_state")
        # The loop never moved off its initial state after the rejected transitions.
        assert coordinator.get(loop["id"])["state"] == "idea_received"
        assert coordinator.get(loop["id"])["version"] == 1


def test_optimistic_version_guard_blocks_stale_writes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "version")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        with pytest.raises(ProductLoopTransitionError, match="version mismatch"):
            coordinator.transition(loop["id"], to_state="discovery_running", expected_version=99)
        moved = coordinator.transition(loop["id"], to_state="discovery_running", expected_version=1)
        assert moved["version"] == 2


def test_block_and_unblock_returns_to_previous_state(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "block")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")
        coordinator.transition(loop["id"], to_state="discovery_running")

        blocked = coordinator.block(loop["id"], reason="Missing repository access")
        assert blocked["state"] == "blocked"
        assert blocked["status"] == "blocked"
        assert blocked["previousState"] == "discovery_running"

        resumed = coordinator.unblock(loop["id"])
        assert resumed["state"] == "discovery_running"  # resumes the pre-block state
        assert resumed["status"] == "active"

        # A completed (terminal) loop cannot be blocked.
        for state in HAPPY_PATH[1:]:
            loop = coordinator.transition(loop["id"], to_state=state)
        assert loop["state"] == "completed"
        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.block(loop["id"], reason="too late")
