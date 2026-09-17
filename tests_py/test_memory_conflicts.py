"""Tests de detección de contradicciones en memoria: el detector marca pares del mismo scope cuya
similitud coseno es un outlier del corpus del proyecto, no toca jamás el contenido existente, y
degrada de forma explícita cuando no hay embeddings reales o el corpus es demasiado chico.

@author Rodrigo Mason
"""

from __future__ import annotations

import itertools
import sys
from contextlib import closing
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from local_control_center.memory_retrieval.conflicts import MemoryConflictDetector
from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema

DIMENSIONS = 128
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


def _project(connection, tmp_path: Path, name: str = "conflicts") -> str:
    project = ProjectsRepository(connection).create_project(
        name=name, path=tmp_path / name, template_id="other"
    )
    return project["id"]


def _unit(vector: np.ndarray) -> list[float]:
    return (vector / np.linalg.norm(vector)).tolist()


def _seed_corpus(
    memory: MemoryRepository,
    project_id: str,
    *,
    with_conflict: bool,
    seed: int = 20260917,
    scope_id: str | None = None,
    kind: str = "note",
) -> dict[str, str]:
    """Siembra 7 memorias no relacionadas más un par que sólo es contradictorio si se pide.

    Devuelve el mapa etiqueta -> id. Las dimensiones y la separación de este corpus están
    verificadas: el par contradictorio queda sobre la valla y ninguno de los otros 35 pares.
    """
    rng = np.random.default_rng(seed)
    vectors = [(f"m{index}", _unit(rng.normal(size=DIMENSIONS))) for index in range(7)]
    base = rng.normal(size=DIMENSIONS)
    vectors.append(("c_a", _unit(base)))
    partner = base + 0.05 * rng.normal(size=DIMENSIONS) if with_conflict else rng.normal(size=DIMENSIONS)
    vectors.append(("c_b", _unit(partner)))

    identifiers: dict[str, str] = {}
    for label, embedding in vectors:
        item = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=scope_id or project_id,
            kind=kind,
            content=f"memoria {label}",
            source_ref="test",
        )
        memory.upsert_memory_embedding(
            memory_item_id=item["id"],
            provider=EMBEDDING_PROVIDER,
            model=EMBEDDING_MODEL,
            embedding=embedding,
        )
        identifiers[label] = item["id"]
    return identifiers


def _snapshot(connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT id, content, hash, supersedes_id, kind FROM memory_items ORDER BY id"
        ).fetchall()
    ]


def test_detects_the_contradictory_pair_with_both_ids(tmp_path: Path) -> None:
    """Dos memorias contradictorias del mismo scope producen un conflicto con ambos ids."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        identifiers = _seed_corpus(memory, project_id, with_conflict=True)

        result = MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert result["status"] == "available", result
        assert len(result["conflicts"]) == 1, result["conflicts"]
        conflict = result["conflicts"][0]
        assert {conflict["leftMemoryItemId"], conflict["rightMemoryItemId"]} == {
            identifiers["c_a"],
            identifiers["c_b"],
        }
        assert conflict["score"] > result["threshold"]


def test_unrelated_memories_produce_no_false_positive(tmp_path: Path) -> None:
    """Un corpus sin contradicciones no marca nada: el detector puede decir 'no hay conflictos'."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed_corpus(memory, project_id, with_conflict=False)

        result = MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert result["status"] == "available", result
        assert result["conflicts"] == []


def test_detection_never_mutates_existing_memory(tmp_path: Path) -> None:
    """Detectar no sobrescribe contenido ni marca supersedes_id: la supersesión sigue siendo humana."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed_corpus(memory, project_id, with_conflict=True)
        before = _snapshot(connection)

        MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert _snapshot(connection) == before
        superseded = connection.execute(
            "SELECT COUNT(*) FROM memory_items WHERE supersedes_id IS NOT NULL"
        ).fetchone()[0]
        assert superseded == 0


def test_identical_content_is_a_duplicate_not_a_conflict(tmp_path: Path) -> None:
    """Mismo hash no es contradicción: dos copias del mismo texto no entran como candidatas."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed_corpus(memory, project_id, with_conflict=False)
        rng = np.random.default_rng(5)
        shared = _unit(rng.normal(size=DIMENSIONS))
        for _ in range(2):
            duplicate = memory.create_memory_item(
                project_id=project_id,
                scope="project",
                scope_id=project_id,
                kind="note",
                content="exactamente el mismo texto",
                source_ref="test",
            )
            memory.upsert_memory_embedding(
                memory_item_id=duplicate["id"],
                provider=EMBEDDING_PROVIDER,
                model=EMBEDDING_MODEL,
                embedding=shared,
            )

        result = MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert result["conflicts"] == [], result["conflicts"]


def test_other_scope_or_kind_is_not_a_candidate(tmp_path: Path) -> None:
    """Un par muy similar pero de distinto kind no es conflicto: el grupo acota qué puede serlo."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed_corpus(memory, project_id, with_conflict=False)
        rng = np.random.default_rng(11)
        base = rng.normal(size=DIMENSIONS)
        for index, kind in enumerate(("note", "lesson")):
            item = memory.create_memory_item(
                project_id=project_id,
                scope="project",
                scope_id=project_id,
                kind=kind,
                content=f"contenido casi igual {index}",
                source_ref="test",
            )
            memory.upsert_memory_embedding(
                memory_item_id=item["id"],
                provider=EMBEDDING_PROVIDER,
                model=EMBEDDING_MODEL,
                embedding=_unit(base + 0.02 * rng.normal(size=DIMENSIONS)),
            )

        result = MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert result["conflicts"] == [], result["conflicts"]


def test_without_real_embeddings_the_detector_degrades(tmp_path: Path) -> None:
    """Sin embeddings reales devuelve configuration_required, no una lista vacía silenciosa."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="note",
            content="sin embedding",
            source_ref="test",
        )

        result = MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert result["status"] == "configuration_required", result
        assert result["conflicts"] == []
        assert result["reason"]


def test_small_corpus_degrades_instead_of_guessing_a_threshold(tmp_path: Path) -> None:
    """Bajo el mínimo de pares la distribución no significa nada: se degrada, no se inventa umbral."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        rng = np.random.default_rng(3)
        base = rng.normal(size=DIMENSIONS)
        for index in range(2):
            item = memory.create_memory_item(
                project_id=project_id,
                scope="project",
                scope_id=project_id,
                kind="note",
                content=f"apenas dos memorias {index}",
                source_ref="test",
            )
            memory.upsert_memory_embedding(
                memory_item_id=item["id"],
                provider=EMBEDDING_PROVIDER,
                model=EMBEDDING_MODEL,
                embedding=_unit(base + 0.03 * rng.normal(size=DIMENSIONS)),
            )

        result = MemoryConflictDetector(memory, EventBus(connection)).detect(project_id=project_id)

        assert result["status"] == "insufficient_corpus", result
        assert result["conflicts"] == []


def test_detection_is_deterministic_across_runs(tmp_path: Path) -> None:
    """Mismo estado, misma salida: umbral y orden de conflictos no dependen de la corrida."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed_corpus(memory, project_id, with_conflict=True)
        detector = MemoryConflictDetector(memory, EventBus(connection))

        first = detector.detect(project_id=project_id)
        second = detector.detect(project_id=project_id)

        assert first["threshold"] == second["threshold"]
        assert [
            (item["leftMemoryItemId"], item["rightMemoryItemId"], item["score"])
            for item in first["conflicts"]
        ] == [
            (item["leftMemoryItemId"], item["rightMemoryItemId"], item["score"])
            for item in second["conflicts"]
        ]


def test_conflicts_endpoint_persists_records_and_emits_event(tmp_path: Path) -> None:
    """El endpoint deja el conflicto consultable y emite memory.conflict_detected."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)
        identifiers = _seed_corpus(MemoryRepository(runtime.connection), project_id, with_conflict=True)

        detect = client.post(
            "/api/v1/memory/conflicts/detect",
            json={"projectId": project_id},
            headers=_token(runtime),
        )
        assert detect.status_code == 200, detect.text
        assert len(detect.json()["conflicts"]) == 1

        listed = client.get("/api/v1/memory/conflicts", params={"projectId": project_id})
        assert listed.status_code == 200, listed.text
        records = listed.json()["conflicts"]
        assert {records[0]["leftMemoryItemId"], records[0]["rightMemoryItemId"]} == {
            identifiers["c_a"],
            identifiers["c_b"],
        }

        events = EventBus(runtime.connection).list_events(project_id=project_id)
        assert any(event["type"] == "memory.conflict_detected" for event in events)
    finally:
        runtime.close()


def test_conflict_detection_requires_write_access(tmp_path: Path) -> None:
    """La detección muta (escribe registros y eventos), así que pasa por require_write."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime.connection, tmp_path)

        response = client.post("/api/v1/memory/conflicts/detect", json={"projectId": project_id})

        assert response.status_code == 403, response.text
    finally:
        runtime.close()


def test_repeated_detection_does_not_duplicate_the_same_conflict(tmp_path: Path) -> None:
    """Correr la detección dos veces deja un solo registro por par, no dos."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        memory = MemoryRepository(connection)
        _seed_corpus(memory, project_id, with_conflict=True)
        detector = MemoryConflictDetector(memory, EventBus(connection))

        detector.detect(project_id=project_id)
        detector.detect(project_id=project_id)

        persisted = connection.execute("SELECT COUNT(*) FROM memory_conflicts").fetchone()[0]
        assert persisted == 1


def test_pair_enumeration_covers_every_combination(tmp_path: Path) -> None:
    """Guardia del fixture: 9 items dan 36 pares, sobre el mínimo exigido por el detector."""
    assert len(list(itertools.combinations(range(9), 2))) == 36
