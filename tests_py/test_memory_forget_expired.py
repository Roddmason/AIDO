"""Tests del olvido real de memoria: el job ``memory.forget.expired`` borra lógicamente los items
vencidos, los retira del índice vectorial, emite un evento por item, es idempotente en una segunda
corrida, y no toca timestamps no canónicos que la comparación lexicográfica juzgaría mal.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.jobs_approvals.worker import ConcurrentWorker
from local_control_center.memory_retrieval.index import RetrievalIndex
from local_control_center.memory_retrieval.models import MEMORY_FORGET_JOB_KIND
from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema

EXPIRED = "2000-01-01T00:00:00.000Z"
EMBEDDING_PROVIDER = "openai_api"
EMBEDDING_MODEL = "text-embedding-3-small"


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _token(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def _project(connection, tmp_path: Path, name: str = "forget") -> str:
    project = ProjectsRepository(connection).create_project(
        name=name, path=tmp_path / name, template_id="other"
    )
    return project["id"]


def _run_forget(db_path: Path, connection, project_id: str) -> dict:
    """Encola el job de olvido y lo ejecuta con el worker real, devolviendo el run cerrado."""
    JobsRepository(connection).create_job(
        project_id=project_id,
        kind=MEMORY_FORGET_JOB_KIND,
        payload={"projectId": project_id},
    )
    return ConcurrentWorker(db_path=db_path).run_once(worker_id="worker-forget")


def test_expired_item_disappears_from_list_but_survives_in_the_database(tmp_path: Path) -> None:
    """El item vencido sale de list, la fila queda con deleted_at poblado para auditoría."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        expired = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="memoria vencida",
            source_ref="test",
            expires_at=EXPIRED,
        )
        alive = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="memoria vigente",
            source_ref="test",
        )

        run = _run_forget(db_path, connection, project_id)

        assert run["run"]["status"] == "completed", run
        assert run["run"]["metadata"]["forgotten"] == 1
        visible = [item["id"] for item in memory.list_memory_items(project_id=project_id)]
        assert visible == [alive["id"]]
        row = connection.execute(
            "SELECT deleted_at, content FROM memory_items WHERE id = ?", (expired["id"],)
        ).fetchone()
        assert row["deleted_at"], "el borrado debe ser logico, la fila se conserva"
        assert row["content"] == "memoria vencida", "el contenido se conserva para auditoria"


def test_second_run_changes_nothing_and_emits_no_new_events(tmp_path: Path) -> None:
    """Idempotencia: la segunda corrida no vuelve a borrar ni a emitir eventos."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="memoria vencida",
            source_ref="test",
            expires_at=EXPIRED,
        )

        first = _run_forget(db_path, connection, project_id)
        snapshot = [
            tuple(row)
            for row in connection.execute(
                "SELECT id, deleted_at, metadata, updated_at FROM memory_items ORDER BY id"
            ).fetchall()
        ]
        forget_events = len(
            [
                event
                for event in EventBus(connection).list_events(project_id=project_id)
                if event["type"] == "memory.expired"
            ]
        )

        second = _run_forget(db_path, connection, project_id)

        assert first["run"]["metadata"]["forgotten"] == 1
        assert second["run"]["metadata"]["forgotten"] == 0
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT id, deleted_at, metadata, updated_at FROM memory_items ORDER BY id"
            ).fetchall()
        ] == snapshot
        assert (
            len(
                [
                    event
                    for event in EventBus(connection).list_events(project_id=project_id)
                    if event["type"] == "memory.expired"
                ]
            )
            == forget_events
        )


def test_expired_item_leaves_the_vector_index(tmp_path: Path) -> None:
    """Tras el olvido el retrieval no devuelve el item vencido."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        item = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="indexada y a punto de vencer",
            source_ref="test",
        )
        memory.upsert_memory_embedding(
            memory_item_id=item["id"],
            provider=EMBEDDING_PROVIDER,
            model=EMBEDDING_MODEL,
            embedding=[1.0, 0.0, 0.0],
        )
        index = RetrievalIndex(memory=memory, index_dir=db_path.parent / "faiss-index")
        assert index.rebuild(project_id=project_id)["indexed"] == 1
        connection.execute("UPDATE memory_items SET expires_at = ? WHERE id = ?", (EXPIRED, item["id"]))

        _run_forget(db_path, connection, project_id)

        assert index.search_embedding(project_id=project_id, embedding=[1.0, 0.0, 0.0]) == []


def test_search_survives_an_item_that_expired_without_a_reindex(tmp_path: Path) -> None:
    """Regresión: un item indexado que vence no puede hacer reventar la búsqueda con KeyError."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        item = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="vector vivo",
            source_ref="test",
        )
        memory.upsert_memory_embedding(
            memory_item_id=item["id"],
            provider=EMBEDDING_PROVIDER,
            model=EMBEDDING_MODEL,
            embedding=[1.0, 0.0, 0.0],
        )
        index = RetrievalIndex(memory=memory, index_dir=db_path.parent / "faiss-index")
        index.rebuild(project_id=project_id)
        connection.execute("UPDATE memory_items SET expires_at = ? WHERE id = ?", (EXPIRED, item["id"]))

        results = index.search_embedding(project_id=project_id, embedding=[1.0, 0.0, 0.0])

        assert results == []


def test_non_canonical_expiry_is_skipped_instead_of_deleted(tmp_path: Path) -> None:
    """Un expires_at con offset no se borra: la comparación lexicográfica lo juzgaría mal."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        offset_item = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="expira con offset, no en Z",
            source_ref="test",
            expires_at="2000-01-01T00:00:00+00:00",
        )

        run = _run_forget(db_path, connection, project_id)

        assert run["run"]["metadata"]["forgotten"] == 0
        assert run["run"]["metadata"]["skippedNonCanonicalExpiry"] == 1
        row = connection.execute(
            "SELECT deleted_at FROM memory_items WHERE id = ?", (offset_item["id"],)
        ).fetchone()
        assert row["deleted_at"] is None, "no se borra lo que el predicado no puede juzgar"


def test_forget_emits_one_event_per_expired_item(tmp_path: Path) -> None:
    """Cada item olvidado deja su propio evento con el id, más la traza del total procesado."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        expected = {
            memory.create_memory_item(
                project_id=project_id,
                scope="project",
                scope_id=project_id,
                kind="note",
                content=f"vencida {index}",
                source_ref="test",
                expires_at=EXPIRED,
            )["id"]
            for index in range(3)
        }

        run = _run_forget(db_path, connection, project_id)

        assert run["run"]["metadata"]["forgotten"] == 3
        emitted = {
            event["payload"]["memoryItemId"]
            for event in EventBus(connection).list_events(project_id=project_id)
            if event["type"] == "memory.expired"
        }
        assert emitted == expected


def test_forget_endpoint_enqueues_the_job_and_requires_write(tmp_path: Path) -> None:
    """El endpoint encola el job bajo require_write; sin token no encola nada."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)

        denied = client.post("/api/v1/memory/forget", json={"projectId": project_id})
        assert denied.status_code == 403, denied.text
        assert JobsRepository(runtime.connection).list_jobs(project_id=project_id) == []

        accepted = client.post(
            "/api/v1/memory/forget",
            json={"projectId": project_id},
            headers=_token(runtime),
        )
        assert accepted.status_code == 202, accepted.text
        jobs = JobsRepository(runtime.connection).list_jobs(project_id=project_id)
        assert [job["kind"] for job in jobs] == [MEMORY_FORGET_JOB_KIND]
        assert accepted.json()["job"]["status"] == "queued"
    finally:
        runtime.close()


def test_forget_leaves_live_items_untouched(tmp_path: Path) -> None:
    """Sin nada vencido el job cierra en completed sin tocar una sola fila."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="vigente sin expiracion",
            source_ref="test",
        )
        before = [
            tuple(row)
            for row in connection.execute(
                "SELECT id, deleted_at, updated_at FROM memory_items ORDER BY id"
            ).fetchall()
        ]

        run = _run_forget(db_path, connection, project_id)

        assert run["run"]["status"] == "completed"
        assert run["run"]["metadata"]["forgotten"] == 0
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT id, deleted_at, updated_at FROM memory_items ORDER BY id"
            ).fetchall()
        ] == before
