"""Tests del briefing de memoria: ensambla un bloque de contexto acotado a un presupuesto que no
se excede, con orden determinista y documentado, todo el texto redactado, y degradación honesta
cuando no hay un proveedor de embeddings real.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.memory_retrieval.briefing import (
    DEFAULT_BRIEFING_BUDGET_CHARS,
    MemoryBriefingService,
)
from local_control_center.memory_retrieval.index import RetrievalIndex
from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

EMBEDDING_PROVIDER = "openai_api"
EMBEDDING_MODEL = "text-embedding-3-small"


class StubbornEmbeddingProvider:
    """Proveedor de consulta determinista para los tests: devuelve siempre el mismo vector."""

    def __init__(self, vector: list[float]):
        self.vector = vector

    def status(self) -> dict[str, Any]:
        """Se declara disponible para ejercitar el camino no degradado."""
        return {"status": "available", "reason": ""}

    def embed_text(self, text: str) -> list[float]:
        """Ignora el texto: la relevancia la fija el corpus sembrado, no el query."""
        _ = text
        return list(self.vector)


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(connection, tmp_path: Path, name: str = "briefing") -> str:
    project = ProjectsRepository(connection).create_project(
        name=name, path=tmp_path / name, template_id="other"
    )
    return project["id"]


def _seed(
    memory: MemoryRepository,
    project_id: str,
    *,
    content: str,
    kind: str = "note",
    scope_id: str | None = None,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    item = memory.create_memory_item(
        project_id=project_id,
        scope="project",
        scope_id=scope_id or project_id,
        kind=kind,
        content=content,
        source_ref="test",
    )
    memory.upsert_memory_embedding(
        memory_item_id=item["id"],
        provider=EMBEDDING_PROVIDER,
        model=EMBEDDING_MODEL,
        embedding=embedding or [1.0, 0.0, 0.0],
    )
    return item


def _service(memory: MemoryRepository, tmp_path: Path) -> MemoryBriefingService:
    index = RetrievalIndex(
        memory=memory,
        index_dir=tmp_path / "faiss-index",
        embedding_provider=StubbornEmbeddingProvider([1.0, 0.0, 0.0]),
    )
    return MemoryBriefingService(memory, index)


def test_briefing_never_exceeds_the_budget_with_a_large_corpus(tmp_path: Path) -> None:
    """Con un corpus grande el bloque se corta: el presupuesto es un techo duro."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        for index in range(60):
            _seed(memory, project_id, content=f"memoria numero {index} " + ("contenido largo " * 20))

        budget = 1200
        result = _service(memory, tmp_path).build(
            project_id=project_id, scope="project", query="que sabemos", budget_chars=budget
        )

        assert result["status"] == "available", result
        assert len(result["context"]) <= budget
        assert result["includedCount"] < 60, "con este presupuesto no pueden entrar los 60"
        assert result["budgetChars"] == budget
        assert result["omittedCount"] == 60 - result["includedCount"]


def test_briefing_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Misma entrada y mismo estado producen exactamente la misma salida."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        for index in range(25):
            _seed(memory, project_id, content=f"memoria estable {index}")
        service = _service(memory, tmp_path)

        first = service.build(project_id=project_id, scope="project", query="estable")
        second = service.build(project_id=project_id, scope="project", query="estable")

        assert first["context"] == second["context"]
        assert [entry["id"] for entry in first["entries"]] == [entry["id"] for entry in second["entries"]]


def test_briefing_redacts_secrets_in_the_context(tmp_path: Path) -> None:
    """Un secreto inyectado en el contenido no sale al cliente."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        leaked = "sk-livekey1234567890abcdefGHIJ"
        _seed(memory, project_id, content=f"la credencial es {leaked} y no debe salir")

        result = _service(memory, tmp_path).build(project_id=project_id, scope="project", query="credencial")

        assert leaked not in result["context"]
        assert all(leaked not in entry["content"] for entry in result["entries"])


def test_lessons_outrank_notes_in_the_assembled_context(tmp_path: Path) -> None:
    """El orden es documentado y estable: primero lesson, después note."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed(memory, project_id, content="una nota cualquiera", kind="note")
        _seed(memory, project_id, content="una leccion aprendida", kind="lesson")

        result = _service(memory, tmp_path).build(
            project_id=project_id, scope="project", query="cualquier cosa"
        )

        assert [entry["kind"] for entry in result["entries"]] == ["lesson", "note"]


def test_briefing_is_scoped(tmp_path: Path) -> None:
    """El scope acota qué entra: memoria de otro scope no se mezcla."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        wanted = _seed(memory, project_id, content="memoria del scope pedido")
        other = memory.create_memory_item(
            project_id=project_id,
            scope="thread",
            scope_id="thread-otro",
            kind="note",
            content="memoria de otro scope",
            source_ref="test",
        )
        memory.upsert_memory_embedding(
            memory_item_id=other["id"],
            provider=EMBEDDING_PROVIDER,
            model=EMBEDDING_MODEL,
            embedding=[1.0, 0.0, 0.0],
        )

        result = _service(memory, tmp_path).build(project_id=project_id, scope="project", query="memoria")

        assert [entry["id"] for entry in result["entries"]] == [wanted["id"]]


def test_briefing_degrades_without_a_real_embedding_provider(tmp_path: Path) -> None:
    """Sin proveedor real no hay briefing parcial disfrazado de completo."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed(memory, project_id, content="hay memoria pero no hay proveedor")
        index = RetrievalIndex(memory=memory, index_dir=tmp_path / "faiss-index")

        result = MemoryBriefingService(memory, index).build(
            project_id=project_id, scope="project", query="lo que sea"
        )

        assert result["status"] == "configuration_required", result
        assert result["context"] == ""
        assert result["entries"] == []
        assert result["reason"]


def test_briefing_reports_when_the_project_has_no_embedded_memory(tmp_path: Path) -> None:
    """Proveedor disponible pero sin memoria embebida es un estado distinto, no un vacío mudo."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)

        result = _service(memory, tmp_path).build(
            project_id=project_id, scope="project", query="nada sembrado"
        )

        assert result["status"] == "empty_corpus", result
        assert result["context"] == ""
        assert result["reason"]


def test_a_single_oversized_item_does_not_break_the_budget(tmp_path: Path) -> None:
    """Si el primer item ya no cabe entero, no se trunca a media palabra: se omite."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed(memory, project_id, content="palabra " * 500)

        result = _service(memory, tmp_path).build(
            project_id=project_id, scope="project", query="palabra", budget_chars=50
        )

        assert len(result["context"]) <= 50
        assert result["includedCount"] == 0
        assert result["omittedCount"] == 1


def test_budget_is_measured_in_code_points_not_bytes(tmp_path: Path) -> None:
    """El presupuesto se mide en caracteres del string redactado, con contenido no ASCII."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        for index in range(20):
            _seed(memory, project_id, content=f"configuración número {index} con acentuación ñ")

        budget = 300
        result = _service(memory, tmp_path).build(
            project_id=project_id, scope="project", query="configuración", budget_chars=budget
        )

        assert len(result["context"]) <= budget
        assert len(result["context"].encode("utf-8")) > len(result["context"]), "hay no-ASCII"


def test_briefing_endpoint_returns_the_assembled_block(tmp_path: Path) -> None:
    """El endpoint expone el briefing y no exige require_write: es una lectura."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)
        _seed(MemoryRepository(runtime.connection), project_id, content="memoria para el briefing")

        response = client.post(
            "/api/v1/memory/briefing",
            json={"projectId": project_id, "scope": "project", "query": "briefing"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["budgetChars"] == DEFAULT_BRIEFING_BUDGET_CHARS
        assert body["status"] == "configuration_required", "sin proveedor real, degrada honesto"
    finally:
        runtime.close()
