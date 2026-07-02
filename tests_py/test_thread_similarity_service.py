"""Tests de ThreadSimilarityService: indice lexical, lifecycle y acciones de enlace.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import ThreadSimilarityService

SIMILARITY_TABLES = {"thread_memory_index", "thread_similarity_events"}


def _project(connection, tmp_path: Path) -> str:
    project = ProjectsRepository(connection).create_project(
        name="thread-similarity",
        path=tmp_path / "workspace",
        template_id="other",
        create_directory=True,
        source="runtime",
    )
    return project["id"]


def test_migration_creates_thread_similarity_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        names = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert names >= SIMILARITY_TABLES

        index_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(thread_memory_index)").fetchall()
        }
        assert {
            "id",
            "project_id",
            "thread_id",
            "title",
            "summary",
            "normalized_goal",
            "fingerprint",
            "keywords_json",
            "artifact_refs_json",
            "status",
            "updated_at",
        } <= index_columns


def test_query_similar_returns_archived_candidate(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="ThreadSimilarityService duplicate request detection",
            summary="Detect when the user asks for work already handled in another thread.",
        )
        repo.append_message(
            thread_id=existing["id"],
            kind="agent_summary",
            author="aido_lead",
            content="Use lexical normalization, keywords, Jaccard overlap and evidence refs.",
        )
        repo.archive_thread(existing["id"], reason="completed", actor="operator")

        results = ThreadSimilarityService(connection).find_similar(
            project_id=project_id,
            query="Implement ThreadSimilarityService for duplicate request detection with keyword overlap.",
        )

        assert results
        assert results[0]["threadId"] == existing["id"]
        assert results[0]["status"] == "archived"
        assert results[0]["score"] >= 0.5


def test_deleted_thread_is_excluded_unless_requested(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        deleted = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Deleted duplicate detector work",
            summary="This deleted thread should only appear when includeDeleted is true.",
        )
        repo.soft_delete_thread(deleted["id"], reason="obsolete", actor="operator")
        service = ThreadSimilarityService(connection)

        hidden = service.find_similar(
            project_id=project_id,
            query="Deleted duplicate detector work",
        )
        visible = service.find_similar(
            project_id=project_id,
            query="Deleted duplicate detector work",
            include_deleted=True,
        )

        assert hidden == []
        assert [item["threadId"] for item in visible] == [deleted["id"]]


def test_similarity_index_uses_artifacts_without_leaking_secrets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        secret = "sk-" + ("test" * 5)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Evidence-backed duplicate detector",
            summary=f"Compare thread requests without exposing {secret}.",
        )
        repo.attach_artifact(
            thread_id=thread["id"],
            kind="evidence_package",
            title="Similarity evidence package",
            artifact_id="evidence-similarity-1",
            payload={"brief": f"Jaccard evidence uses {secret} only after redaction."},
        )

        results = ThreadSimilarityService(connection).find_similar(
            project_id=project_id,
            query="duplicate detector similarity evidence package",
        )

        serialized = json_dumps(results)
        assert results[0]["artifactRefs"][0]["artifactId"] == "evidence-similarity-1"
        assert secret not in serialized
        assert "[redacted]" in serialized


def test_similarity_index_updates_from_evidence_events_without_leaking_secrets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        service = ThreadSimilarityService(connection)
        secret = "sk-" + ("event" * 5)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="Runtime follow-up",
        )

        before = service.get_index(thread["id"])
        assert "evidence" not in before["normalizedGoal"]

        repo.record_event(
            thread_id=thread["id"],
            type="evidence_created",
            payload={
                "reason": f"Duplicate detector evidence captured with {secret}",
                "evidencePackageId": "evidence-thread-similarity-event",
            },
            agent_role="qa",
        )

        index = service.get_index(thread["id"])
        serialized = json_dumps(index)
        assert "evidence" in index["normalizedGoal"]
        assert "duplicate" in index["normalizedGoal"]
        assert secret not in serialized
        assert "redacted" in index["normalizedGoal"]


def test_mark_improve_existing_records_similarity_event(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_id = _project(connection, tmp_path)
        repo = ThreadsRepository(connection)
        source = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="New duplicate request",
        )
        candidate = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-2",
            title="Existing duplicate request",
        )
        service = ThreadSimilarityService(connection)

        event = service.mark_similarity(
            project_id=project_id,
            source_thread_id=source["id"],
            candidate_thread_id=candidate["id"],
            score=0.86,
            reason="Shared normalized goal and keywords.",
            action="improve_existing",
        )

        assert event["action"] == "improve_existing"
        assert event["sourceThreadId"] == source["id"]
        assert event["candidateThreadId"] == candidate["id"]
        assert service.list_similarity_events(source_thread_id=source["id"])[0]["id"] == event["id"]
