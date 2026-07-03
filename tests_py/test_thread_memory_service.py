"""Tests de ThreadMemoryService: registry de funcionalidad, reindex y API publica.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import ThreadMemoryService

MEMORY_TABLES = {
    "thread_memory_index",
    "thread_similarity_events",
    "project_lessons",
    "functionality_registry",
}


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(connection, tmp_path: Path, name: str = "thread-memory") -> str:
    project = ProjectsRepository(connection).create_project(
        name=name,
        path=tmp_path / name,
        template_id="other",
        create_directory=True,
        source="runtime",
    )
    return project["id"]


def _token(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def test_migration_creates_thread_memory_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        names = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert names >= MEMORY_TABLES

        functionality_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(functionality_registry)").fetchall()
        }
        assert {
            "id",
            "project_id",
            "name",
            "summary",
            "normalized_name",
            "fingerprint",
            "source_thread_id",
            "status",
            "file_paths_json",
            "performance_notes_json",
            "metadata",
            "created_at",
            "updated_at",
        } <= functionality_columns

        event_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(thread_similarity_events)").fetchall()
        }
        assert "functionality_id" in event_columns


def test_reindex_thread_memory_persists_resolved_functionality(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.append_message(
            thread_id=thread["id"],
            kind="agent_summary",
            author="aido_lead",
            content="Delivered changes in local-control-center/web/src/pages/WorkspaceDashboard.tsx",
        )
        repo.record_event(
            thread_id=thread["id"],
            type="delivery_completed",
            payload={
                "deliveredChanges": ["Added workspace dashboard filter controls."],
                "filePaths": ["local-control-center/web/src/pages/WorkspaceDashboard.tsx"],
            },
            agent_role="release_manager",
        )
        repo.set_status(thread["id"], "resolved")

        service = ThreadMemoryService(connection)
        result = service.reindex_thread_memory(thread["id"])

        assert result["index"]["threadId"] == thread["id"]
        assert result["functionality"]["sourceThreadId"] == thread["id"]
        assert result["functionality"]["name"] == "Workspace dashboard filters"
        assert result["functionality"]["filePaths"] == [
            "local-control-center/web/src/pages/WorkspaceDashboard.tsx"
        ]


def test_performance_pass_links_similarity_event_to_functionality_registry(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        source = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-source",
            title="Optimize dashboard filters",
        )
        candidate = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-candidate",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.set_status(candidate["id"], "resolved")

        event = ThreadMemoryService(connection).mark_similarity(
            project_id=project_id,
            source_thread_id=source["id"],
            candidate_thread_id=candidate["id"],
            score=0.91,
            reason="Performance pass requested for existing dashboard filters.",
            action="performance_pass",
        )

        assert event["action"] == "performance_pass"
        assert event["functionalityId"].startswith("functionality-")
        registry_row = connection.execute(
            "SELECT * FROM functionality_registry WHERE id = ?",
            (event["functionalityId"],),
        ).fetchone()
        assert registry_row is not None
        notes = registry_row["performance_notes_json"]
        assert "Performance pass requested" in notes


def test_project_functionality_endpoint_lists_registry(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path, name="functionality-api")
        repo = ThreadsRepository(runtime.connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Runtime provider settings",
            summary="Delivered settings for runtime provider setup.",
        )
        repo.set_status(thread["id"], "resolved")
        ThreadMemoryService(runtime.connection).reindex_thread_memory(thread["id"])

        response = client.get(f"/api/v1/projects/{project_id}/functionality")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["sourceThreadId"] for item in body["functionality"]] == [thread["id"]]
        assert body["functionality"][0]["name"] == "Runtime provider settings"
    finally:
        runtime.close()


def test_reindex_endpoint_refreshes_thread_memory(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path, name="reindex-api")
        thread = ThreadsRepository(runtime.connection).create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Reindexable functionality",
            summary="Resolved functionality should be reindexed by API.",
        )
        ThreadsRepository(runtime.connection).set_status(thread["id"], "resolved")

        response = client.post(
            f"/api/v1/threads/{thread['id']}/memory/reindex",
            headers=_token(runtime),
        )

        assert response.status_code == 200, response.text
        assert response.json()["functionality"]["sourceThreadId"] == thread["id"]
    finally:
        runtime.close()
