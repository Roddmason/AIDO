"""Tests de la taxonomía cerrada de ``memory_items.kind``: el contrato acepta sólo el conjunto
declarado, rechaza cualquier otro valor en el borde HTTP, y la migración normaliza filas legacy
conservando el valor original sin perder legibilidad.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import init_phase68_schema, initialize_platform_schema
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _token(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def _project(connection, tmp_path: Path, name: str = "kind-taxonomy") -> str:
    project = ProjectsRepository(connection).create_project(
        name=name, path=tmp_path / name, template_id="other"
    )
    return project["id"]


def test_create_memory_rejects_kind_outside_the_closed_set(tmp_path: Path) -> None:
    """Un kind fuera del conjunto declarado falla con 422 en el borde, no llega a la base."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)

        response = client.post(
            "/api/v1/memory",
            json={"projectId": project_id, "kind": "bogus", "content": "no debe persistir"},
            headers=_token(runtime),
        )

        assert response.status_code == 422, response.text
        assert MemoryRepository(runtime.connection).list_memory_items(project_id=project_id) == []
    finally:
        runtime.close()


def test_create_memory_accepts_every_declared_kind(tmp_path: Path) -> None:
    """Los dos valores del contrato (note y lesson) siguen siendo aceptados."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)

        for kind in ("note", "lesson"):
            response = client.post(
                "/api/v1/memory",
                json={"projectId": project_id, "kind": kind, "content": f"contenido {kind}"},
                headers=_token(runtime),
            )
            assert response.status_code == 201, response.text
            assert response.json()["memoryItem"]["kind"] == kind
    finally:
        runtime.close()


def _seed_legacy_row(connection, project_id: str, *, memory_id: str, kind: str) -> None:
    """Inserta por SQL directo una fila con un kind fuera del conjunto, como haría una base vieja."""
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO memory_items
            (id, project_id, scope, scope_id, kind, content, source_ref, version, hash,
             supersedes_id, created_by_run_id, valid_from, expires_at, deleted_at, metadata,
             created_at, updated_at)
        VALUES (?, ?, 'project', ?, ?, ?, 'legacy', 1, 'hash-legacy', NULL, NULL, ?, NULL, NULL,
                ?, ?, ?)
        """,
        (
            memory_id,
            project_id,
            project_id,
            kind,
            f"contenido legacy {kind}",
            timestamp,
            json_dumps({"origin": "legacy"}),
            timestamp,
            timestamp,
        ),
    )


def test_phase68_normalizes_legacy_kinds_and_preserves_the_original_value(tmp_path: Path) -> None:
    """Las filas preexistentes siguen legibles: el kind se normaliza y el original queda en metadata."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        connection.execute("DELETE FROM schema_migrations WHERE version = 68")
        _seed_legacy_row(connection, project_id, memory_id="memory-legacy", kind="decision")
        _seed_legacy_row(connection, project_id, memory_id="memory-lesson", kind="lesson")

        init_phase68_schema(connection)

        rows = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, kind, metadata FROM memory_items ORDER BY rowid"
            ).fetchall()
        }

        legacy = rows["memory-legacy"]
        assert legacy["kind"] == "note"
        metadata = json_loads(legacy["metadata"])
        assert metadata["legacyKind"] == "decision"
        assert metadata["origin"] == "legacy", "la migración no puede perder metadata previa"

        preserved = rows["memory-lesson"]
        assert preserved["kind"] == "lesson", "un kind válido no se toca"
        assert "legacyKind" not in json_loads(preserved["metadata"])


def test_phase68_is_idempotent(tmp_path: Path) -> None:
    """Correr la fase dos veces no cambia nada la segunda vez."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        connection.execute("DELETE FROM schema_migrations WHERE version = 68")
        _seed_legacy_row(connection, project_id, memory_id="memory-legacy", kind="decision")

        init_phase68_schema(connection)
        after_first = connection.execute(
            "SELECT id, kind, metadata, updated_at FROM memory_items ORDER BY rowid"
        ).fetchall()

        init_phase68_schema(connection)
        after_second = connection.execute(
            "SELECT id, kind, metadata, updated_at FROM memory_items ORDER BY rowid"
        ).fetchall()

        assert [tuple(row) for row in after_first] == [tuple(row) for row in after_second]
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 68").fetchone()[
            0
        ]
        assert applied == 1


def test_legacy_rows_remain_readable_through_the_contract(tmp_path: Path) -> None:
    """Tras migrar, una fila legacy se sirve por la API sin romper el modelo de salida."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)
        runtime.connection.execute("DELETE FROM schema_migrations WHERE version = 68")
        _seed_legacy_row(runtime.connection, project_id, memory_id="memory-legacy", kind="decision")
        init_phase68_schema(runtime.connection)

        response = client.get("/api/v1/memory", params={"projectId": project_id})

        assert response.status_code == 200, response.text
        items = response.json()["memoryItems"]
        assert [item["kind"] for item in items] == ["note"]
        assert items[0]["metadata"]["legacyKind"] == "decision"
    finally:
        runtime.close()
