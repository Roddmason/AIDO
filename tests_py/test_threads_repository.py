"""Tests del ThreadsRepository: header polimórfico, timeline con secuencia monótona,
artifacts enlazados, bitácora de eventos y solicitudes de decisión.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

THREAD_TABLES = {
    "project_threads",
    "thread_messages",
    "thread_artifacts",
    "thread_agent_events",
    "thread_decisions",
}


def _project(connection, tmp_path: Path) -> str:
    project = ProjectsRepository(connection).create_project(
        name="threads-fixture",
        path=tmp_path / "workspace",
        template_id="other",
        create_directory=True,
        source="runtime",
    )
    return project["id"]


def test_migration_creates_thread_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        names = {row["name"] for row in rows}
        assert names >= THREAD_TABLES
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(project_threads)").fetchall()
        }
        assert {
            "archived_at",
            "archived_by",
            "deleted_at",
            "deleted_by",
            "lifecycle_reason",
        } <= columns


def test_create_and_list_threads_filters_by_owner(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)

        workspace_thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Workspace thread",
        )
        repo.create_thread(
            project_id=project_id,
            owner_type="loop",
            owner_id="product-loop-1",
            title="Loop thread",
        )

        assert workspace_thread["id"].startswith("thread-")
        assert workspace_thread["status"] == "open"
        assert workspace_thread["ownerType"] == "workspace"

        all_threads = repo.list_threads(project_id=project_id)
        assert len(all_threads) == 2

        only_workspace = repo.list_threads(
            project_id=project_id, owner_type="workspace", owner_id="workspace-1"
        )
        assert [thread["id"] for thread in only_workspace] == [workspace_thread["id"]]


def test_rename_thread_updates_title_and_audits(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Original title",
        )

        renamed = repo.rename_thread(thread["id"], "Renamed thread", actor="operator")

        assert renamed["title"] == "Renamed thread"
        assert repo.get_thread(thread["id"])["title"] == "Renamed thread"
        audits = EventBus(connection).list_audit_events(project_id)
        assert audits[0]["action"] == "thread.renamed"
        assert audits[0]["target"] == thread["id"]
        assert audits[0]["actor"] == "operator"


def test_archive_hides_thread_by_default_and_include_archived_restores_it(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Archive me",
        )

        archived = repo.archive_thread(thread["id"], reason="Work completed", actor="operator")

        assert archived["status"] == "archived"
        assert archived["archivedAt"]
        assert archived["archivedBy"] == "operator"
        assert archived["lifecycleReason"] == "Work completed"
        assert [item["id"] for item in repo.list_threads(project_id=project_id)] == []
        assert [item["id"] for item in repo.list_threads(project_id=project_id, include_archived=True)] == [
            thread["id"]
        ]
        assert EventBus(connection).list_audit_events(project_id)[0]["action"] == "thread.archived"


def test_soft_delete_hides_thread_but_keeps_messages_and_artifacts(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Delete me",
        )
        message = repo.append_message(thread_id=thread["id"], kind="user", author="user", content="keep me")
        artifact = repo.attach_artifact(
            thread_id=thread["id"],
            kind="result",
            title="Kept artifact",
            artifact_id="artifact-1",
            message_id=message["id"],
        )

        deleted = repo.soft_delete_thread(thread["id"], reason="No longer needed", actor="operator")

        assert deleted["status"] == "deleted"
        assert deleted["deletedAt"]
        assert deleted["deletedBy"] == "operator"
        assert [item["id"] for item in repo.list_threads(project_id=project_id)] == []
        assert [item["id"] for item in repo.list_threads(project_id=project_id, include_deleted=True)] == [
            thread["id"]
        ]
        assert repo.list_messages(thread["id"])[0]["id"] == message["id"]
        assert repo.list_artifacts(thread["id"])[0]["id"] == artifact["id"]
        assert EventBus(connection).list_audit_events(project_id)[0]["action"] == "thread.deleted"


def test_running_thread_cannot_be_soft_deleted(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Running thread",
        )
        repo.set_status(thread["id"], "running")

        with pytest.raises(ValueError, match="running"):
            repo.soft_delete_thread(thread["id"], reason="cleanup", actor="operator")


def test_owner_type_must_be_known(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        with pytest.raises(ValueError):
            repo.create_thread(
                project_id=project_id,
                owner_type="not_a_real_owner",
                owner_id="x",
                title="bad",
            )


def test_append_message_assigns_monotonic_sequence(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Thread",
        )

        first = repo.append_message(thread_id=thread["id"], kind="user", author="user", content="hello")
        second = repo.append_message(
            thread_id=thread["id"], kind="aido_lead", author="aido_lead", content="hi"
        )

        assert first["sequence"] == 1
        assert second["sequence"] == 2
        messages = repo.list_messages(thread["id"])
        assert [message["kind"] for message in messages] == ["user", "aido_lead"]
        assert messages[0]["projectId"] == project_id


def test_message_kind_must_be_known(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="review",
            owner_id="review-1",
            title="Thread",
        )
        with pytest.raises(ValueError):
            repo.append_message(thread_id=thread["id"], kind="not_a_kind", author="x", content="y")


def test_attach_artifact_links_to_thread(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="story",
            owner_id="story-1",
            title="Thread",
        )

        artifact = repo.attach_artifact(
            thread_id=thread["id"],
            kind="intake_classification",
            title="Intake decision",
            artifact_id="thread-intake-1",
            payload={"planMode": "execute"},
        )

        assert artifact["threadId"] == thread["id"]
        listed = repo.list_artifacts(thread["id"])
        assert [item["artifactId"] for item in listed] == ["thread-intake-1"]
        assert listed[0]["metadata"]["planMode"] == "execute"


def test_record_event_is_append_only_with_sequence(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="agent_task",
            owner_id="task-1",
            title="Thread",
        )

        repo.record_event(
            thread_id=thread["id"],
            type="coordinator_run",
            agent_role="aido_lead",
            payload={"planMode": "ask"},
        )
        repo.record_event(
            thread_id=thread["id"],
            type="status_change",
            payload={"status": "waiting_decision"},
        )

        events = repo.list_events(thread["id"])
        assert [event["sequence"] for event in events] == [1, 2]
        assert events[0]["type"] == "coordinator_run"


def test_list_events_after_returns_incremental_window(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="agent_task",
            owner_id="task-1",
            title="Thread",
        )

        for event_type in ["message_received", "classification_completed", "run_queued"]:
            repo.record_event(thread_id=thread["id"], type=event_type)

        events = repo.list_events_after(thread["id"], after_sequence=1, limit=1)

        assert [event["sequence"] for event in events] == [2]
        assert events[0]["type"] == "classification_completed"


def test_decision_lifecycle(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Thread",
        )
        message = repo.append_message(
            thread_id=thread["id"],
            kind="decision_request",
            author="aido_lead",
            content="Need a decision",
        )

        decision = repo.create_decision(
            thread_id=thread["id"],
            message_id=message["id"],
            title="Pick scope",
            prompt="What outcome should AIDO optimize for?",
            options=["diagnosis", "implementation"],
        )
        assert decision["status"] == "pending"

        resolved = repo.resolve_decision(
            thread_id=thread["id"],
            decision_id=decision["id"],
            resolution="implementation",
            decided_by="user",
        )
        assert resolved["status"] == "resolved"
        assert resolved["resolution"] == "implementation"
        assert resolved["decidedAt"]
        assert repo.list_decisions(thread["id"])[0]["status"] == "resolved"


def test_set_status_updates_header(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Thread",
        )
        updated = repo.set_status(thread["id"], "waiting_decision")
        assert updated["status"] == "waiting_decision"
        with pytest.raises(ValueError):
            repo.set_status(thread["id"], "not_a_status")
