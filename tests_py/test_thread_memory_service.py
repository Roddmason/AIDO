"""Tests de ThreadMemoryService: registry de funcionalidad, reindex y API publica.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
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
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)

        names = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert names >= MEMORY_TABLES

        functionality_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(functionality_registry)").fetchall()
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
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
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

        _delivered_loop(connection, project_id, thread["id"])
        service = ThreadMemoryService(connection)
        result = service.reindex_thread_memory(thread["id"])

        assert result["index"]["threadId"] == thread["id"]
        assert result["functionality"]["sourceThreadId"] == thread["id"]
        assert result["functionality"]["name"] == "Workspace dashboard filters"
        assert result["functionality"]["filePaths"] == [
            "local-control-center/web/src/pages/WorkspaceDashboard.tsx"
        ]


def test_reindex_existing_functionality_updates_changed_fingerprint_without_id_collision(
    tmp_path: Path,
) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Runtime provider routing",
            summary="Delivered deterministic runtime routing.",
        )
        repo.set_status(thread["id"], "resolved")
        _delivered_loop(connection, project_id, thread["id"])
        service = ThreadMemoryService(connection)
        first = service.reindex_thread_memory(thread["id"])["functionality"]

        repo.append_message(
            thread_id=thread["id"],
            kind="agent_summary",
            author="aido_lead",
            content="Added durable provider selection evidence after the first memory index.",
        )
        second = service.reindex_thread_memory(thread["id"])["functionality"]
        rows = connection.execute(
            "SELECT id, fingerprint FROM functionality_registry WHERE source_thread_id = ?",
            (thread["id"],),
        ).fetchall()

        assert second["id"] == first["id"]
        assert second["fingerprint"] != first["fingerprint"]
        assert [(row["id"], row["fingerprint"]) for row in rows] == [(second["id"], second["fingerprint"])]


def test_reindex_thread_memory_extracts_file_paths_from_artifacts_and_decisions(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Thread memory API",
            summary="Delivered API memory routes.",
        )
        repo.attach_artifact(
            thread_id=thread["id"],
            kind="git_patch",
            title="Similarity patch",
            artifact_id="artifact-memory-file-paths",
            payload={"path": "local_control_center/threads/similarity.py"},
        )
        decision = repo.create_decision(
            thread_id=thread["id"],
            title="API route decision",
            prompt="Keep memory routes under the existing threads router.",
            options=["approve", "reject"],
            metadata={"filePath": "local_control_center/threads/api.py"},
        )
        repo.resolve_decision(
            thread_id=thread["id"],
            decision_id=decision["id"],
            resolution="approve existing threads router",
            decided_by="operator",
        )
        repo.set_status(thread["id"], "resolved")

        _delivered_loop(connection, project_id, thread["id"])
        result = ThreadMemoryService(connection).reindex_thread_memory(thread["id"])

        assert set(result["functionality"]["filePaths"]) >= {
            "local_control_center/threads/similarity.py",
            "local_control_center/threads/api.py",
        }


def test_performance_pass_links_similarity_event_to_functionality_registry(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
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


def test_mark_similarity_confirmation_makes_the_candidate_findable_without_delivery(
    tmp_path: Path,
) -> None:
    """Spec: una decisión explícita del operador es evidencia más fuerte que la heurística de entrega."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
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
            summary="Never delivered, but the operator confirmed it as existing.",
        )
        repo.set_status(candidate["id"], "resolved")
        service = ThreadMemoryService(connection)

        assert service.has_delivery_evidence(candidate["id"], project_id) is False
        service.mark_similarity(
            project_id=project_id,
            source_thread_id=source["id"],
            candidate_thread_id=candidate["id"],
            score=0.91,
            reason="Operator confirmed the workspace dashboard filters already exist.",
            action="improve_existing",
        )

        assert service.has_delivery_evidence(candidate["id"], project_id) is True
        matches = service.find_existing_functionality(
            project_id=project_id, query="Improve the workspace dashboard filters"
        )

    assert [match["sourceThreadId"] for match in matches] == [candidate["id"]]


def test_delivery_evidence_never_borrows_a_loop_from_another_project(tmp_path: Path) -> None:
    """Spec: la evidencia de entrega está acotada al proyecto del hilo, nunca a otro proyecto."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        other_project = ProjectsRepository(connection).create_project(
            name="Other project", path=tmp_path / "other-project", template_id="other"
        )
        _delivered_loop(connection, other_project["id"], thread["id"])

        service = ThreadMemoryService(connection)
        assert service.has_delivery_evidence(thread["id"], project["id"]) is False
        assert service.ensure_project_functionality(project["id"]) == 0
        matches = service.find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert matches == []


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
        _delivered_loop(runtime.connection, project_id, thread["id"])
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

        _delivered_loop(runtime.connection, project_id, thread["id"])
        response = client.post(
            f"/api/v1/threads/{thread['id']}/memory/reindex",
            headers=_token(runtime),
        )

        assert response.status_code == 200, response.text
        assert response.json()["functionality"]["sourceThreadId"] == thread["id"]
    finally:
        runtime.close()


def _delivered_loop(
    connection, project_id: str, thread_id: str, *, state: str = "delivered", initiative_id=None
) -> None:
    connection.execute(
        """
        INSERT INTO product_loops
            (id, project_id, initiative_id, title, state, previous_state, status, context, version,
             created_at, updated_at)
        VALUES (?, ?, ?, 'Seeded loop', ?, NULL, 'active', ?, 1,
                '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """,
        (
            f"loop-{state}-{thread_id}",
            project_id,
            initiative_id,
            state,
            json.dumps({"durableRun": {"thread": {"projectThreadId": thread_id}}}),
        ),
    )


def _archived_functionality_thread(connection, tmp_path) -> tuple[dict, dict]:
    project = ProjectsRepository(connection).create_project(
        name="Functionality evidence", path=tmp_path / "project", template_id="other"
    )
    repo = ThreadsRepository(connection)
    thread = repo.create_thread(
        project_id=project["id"],
        owner_type="workspace",
        owner_id="workspace-evidence",
        title="Workspace dashboard filters",
        summary="Filters for the active workspace dashboard.",
    )
    repo.set_status(thread["id"], "archived")
    ThreadMemoryService(connection).reindex_thread_memory(thread["id"])
    return project, thread


def test_functionality_from_a_thread_that_never_delivered_is_not_a_match(tmp_path) -> None:
    """Caso vivo: un hilo archivado sin ejecución quedó registrado y bloqueó al siguiente (0.627)."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        _delivered_loop(connection, project["id"], thread["id"], state="cancelled")

        matches = ThreadMemoryService(connection).find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert matches == []


def test_functionality_from_a_delivered_thread_still_matches(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        _delivered_loop(connection, project["id"], thread["id"])

        matches = ThreadMemoryService(connection).find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert [match["sourceThreadId"] for match in matches] == [thread["id"]]


def test_functionality_with_an_approved_brief_matches(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        _delivered_loop(
            connection, project["id"], thread["id"], state="planning", initiative_id="initiative-approved"
        )
        connection.execute(
            """
            INSERT INTO product_briefs
                (id, project_id, initiative_id, title, status, summary, problem_statement, goals,
                 target_users, success_metrics, scope, out_of_scope, version, created_at, updated_at)
            VALUES ('brief-approved', ?, 'initiative-approved', 'Brief', 'approved', '', '', '[]', '[]',
                    '[]', '[]', '[]', 1, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
            """,
            (project["id"],),
        )

        service = ThreadMemoryService(connection)
        assert service.has_delivery_evidence(thread["id"], project["id"]) is True
        matches = service.find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert [match["sourceThreadId"] for match in matches] == [thread["id"]]


def test_thread_without_delivery_evidence_neither_creates_nor_confirms_functionality(tmp_path) -> None:
    """Spec §2.6: el registro se crea o se confirma sólo con entrega; lo heredado no bloquea."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        service = ThreadMemoryService(connection)

        assert connection.execute("SELECT COUNT(*) FROM functionality_registry").fetchone()[0] == 0
        assert service.reindex_thread_memory(thread["id"])["functionality"] is None
        assert service.ensure_project_functionality(project["id"]) == 0

        connection.execute(
            """
            INSERT INTO functionality_registry
                (id, project_id, name, summary, normalized_name, fingerprint, source_thread_id, status,
                 file_paths_json, performance_notes_json, metadata, created_at, updated_at)
            VALUES ('functionality-legacy', ?, 'Workspace dashboard filters', '', 'workspace dashboard filters',
                    'legacy-fingerprint', ?, 'archived', '[]', '[]', '{}',
                    '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
            """,
            (project["id"], thread["id"]),
        )
        service.reindex_project_memory(project["id"])
        service.ensure_project_functionality(project["id"])
        legacy_updated_at = connection.execute(
            "SELECT updated_at FROM functionality_registry WHERE id = 'functionality-legacy'"
        ).fetchone()[0]
        matches = service.find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert legacy_updated_at == "2026-01-01T00:00:00.000Z"
    assert matches == []


def test_delivery_evidence_never_parses_the_context_of_loops_without_delivery(tmp_path) -> None:
    """Regresión perf: el JSON de ``context`` (hasta decenas de MB) sólo se lee en loops candidatos.

    Un loop que no está ``delivered`` ni pertenece a una iniciativa con brief aprobado no puede
    aportar evidencia, así que su ``context`` no debe parsearse ni en cada intake
    (``has_delivery_evidence``) ni al materializar (``ensure_project_functionality``).
    """
    with (
        closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection,
        connection,
        closing(sqlite3.connect(":memory:")) as delegate,
    ):
        context_reads = _count_thread_link_reads(connection, delegate)
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        for index in range(30):
            _delivered_loop(
                connection,
                project["id"],
                f"thread-noise-{index}",
                state="cancelled",
                initiative_id=f"initiative-noise-{index}",
            )
        _delivered_loop(connection, project["id"], thread["id"], state="blocked")
        _approved_brief(connection, project["id"], "initiative-noise-0")
        context_reads[0] = 0
        service = ThreadMemoryService(connection)

        assert service.has_delivery_evidence(thread["id"], project["id"]) is False
        reads_per_check = context_reads[0]
        assert service.ensure_project_functionality(project["id"]) == 0

    assert reads_per_check == 1, "sólo el loop de la iniciativa con brief aprobado"
    assert context_reads == [2]


def _approved_brief(connection, project_id: str, initiative_id: str) -> None:
    connection.execute(
        """
        INSERT INTO product_briefs
            (id, project_id, initiative_id, title, status, summary, problem_statement, goals,
             target_users, success_metrics, scope, out_of_scope, version, created_at, updated_at)
        VALUES (?, ?, ?, 'Brief', 'approved', '', '', '[]', '[]',
                '[]', '[]', '[]', 1, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """,
        (f"brief-{initiative_id}", project_id, initiative_id),
    )


def _count_thread_link_reads(connection, delegate) -> list[int]:
    """Envuelve ``json_extract`` para contar lecturas del enlace loop→hilo sin cambiar su resultado.

    Se registra antes de preparar cualquier sentencia: las ya cacheadas conservan la función nativa.
    """
    reads = [0]

    def counting_json_extract(document, path):
        if path == "$.durableRun.thread.projectThreadId":
            reads[0] += 1
        return delegate.execute("SELECT json_extract(?, ?)", (document, path)).fetchone()[0]

    connection.create_function("json_extract", 2, counting_json_extract, deterministic=True)
    return reads
