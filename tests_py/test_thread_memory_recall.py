"""Tests del recall de memoria de threads: GET /api/v1/threads/{id}/memory agrega las seis
categorías del panel (similares, decisiones, evidencia, lecciones, performance y funcionalidad
ya implementada) desde datos reales sembrados por repositorio, sin tablas nuevas.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import ThreadSimilarityService


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Recall", path=tmp_path / "recall", template_id="other"
    )
    return project["id"]


def _thread(runtime, project_id: str, title: str, summary: str = "") -> dict:
    return ThreadsRepository(runtime.connection).create_thread(
        project_id=project_id,
        owner_type="workspace",
        owner_id="workspace-1",
        title=title,
        summary=summary,
    )


def test_memory_recall_returns_all_six_categories(tmp_path: Path) -> None:
    """El endpoint agrega similares, decisiones, evidencia, lecciones, performance y resueltos."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        repo = ThreadsRepository(runtime.connection)

        source = _thread(runtime, project_id, "Implement OAuth login flow for dashboard")
        previous = _thread(runtime, project_id, "Implement OAuth login flow", "Login OAuth done")
        repo.append_message(
            thread_id=previous["id"],
            kind="user",
            author="user",
            content="Implement OAuth login flow with refresh tokens for the dashboard",
        )
        decision = repo.create_decision(
            thread_id=previous["id"],
            title="Token storage",
            prompt="Where should refresh tokens live?",
            options=["cookie", "vault"],
        )
        repo.resolve_decision(
            thread_id=previous["id"],
            decision_id=decision["id"],
            resolution="vault",
            decided_by="operator",
        )
        repo.attach_artifact(
            thread_id=previous["id"],
            artifact_id="artifact-oauth-report",
            kind="research_report",
            title="OAuth provider comparison",
        )
        repo.set_status(previous["id"], "resolved")

        MemoryRepository(runtime.connection).create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="lesson",
            content="OAuth login redirects fail when the dashboard callback URL is not whitelisted",
            source_ref=f"thread:{previous['id']}",
        )
        ThreadSimilarityService(runtime.connection).mark_similarity(
            project_id=project_id,
            source_thread_id=source["id"],
            candidate_thread_id=previous["id"],
            score=0.8,
            reason="OAuth login overlap",
            action="performance_pass",
        )

        response = client.get(f"/api/v1/threads/{source['id']}/memory")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["sourceThreadId"] == source["id"]
        similar_ids = [item["threadId"] for item in payload["similarThreads"]]
        assert previous["id"] in similar_ids

        decisions = payload["previousDecisions"]
        assert [item["resolution"] for item in decisions] == ["vault"]
        assert decisions[0]["threadTitle"] == previous["title"]

        evidence = payload["relatedEvidence"]
        assert [item["artifactId"] for item in evidence] == ["artifact-oauth-report"]
        assert evidence[0]["kind"] == "research_report"

        lessons = payload["lessonsLearned"]
        assert len(lessons) == 1
        assert "callback" in lessons[0]["content"]
        assert lessons[0]["matchedKeywords"], "la lección comparte tokens con el objetivo del hilo"

        performance = payload["performanceIssues"]
        assert any(item["source"] == "similarity_event" for item in performance)
        assert performance[0]["threadId"] == previous["id"]

        implemented = payload["implementedFunctionality"]
        assert [item["threadId"] for item in implemented] == [previous["id"]]
    finally:
        runtime.close()


def test_memory_recall_is_empty_but_valid_without_history(tmp_path: Path) -> None:
    """Sin historial similar, cada categoría vuelve vacía (recall honesto, nunca inventado)."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        source = _thread(runtime, project_id, "Completely unique work item")

        response = client.get(f"/api/v1/threads/{source['id']}/memory")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["similarThreads"] == []
        assert payload["previousDecisions"] == []
        assert payload["relatedEvidence"] == []
        assert payload["lessonsLearned"] == []
        assert payload["performanceIssues"] == []
        assert payload["implementedFunctionality"] == []
    finally:
        runtime.close()


def test_memory_recall_404_for_unknown_thread(tmp_path: Path) -> None:
    """Un hilo inexistente responde 404, no 500."""
    runtime, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/threads/thread-missing/memory")
        assert response.status_code == 404
    finally:
        runtime.close()


def test_memory_recall_excludes_expired_and_deleted_lessons(tmp_path: Path) -> None:
    """Las lecciones expiradas o borradas no vuelven; '' en expires_at cuenta como sin expiración."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        source = _thread(runtime, project_id, "Improve report caching layer")
        memory = MemoryRepository(runtime.connection)
        memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="lesson",
            content="Caching layer invalidation must be explicit",
            expires_at="2000-01-01T00:00:00+00:00",
        )
        kept = memory.create_memory_item(
            project_id=project_id,
            scope="project",
            scope_id=project_id,
            kind="lesson",
            content="Report caching needs cache busting on deploy",
            expires_at="",
        )

        response = client.get(f"/api/v1/threads/{source['id']}/memory")
        assert response.status_code == 200, response.text
        lessons = response.json()["lessonsLearned"]
        assert [item["id"] for item in lessons] == [kept["id"]]
    finally:
        runtime.close()
