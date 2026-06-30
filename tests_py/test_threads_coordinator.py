"""Tests del ThreadCoordinator: ejecuta el clasificador determinista sobre el mensaje de usuario y
responde (aido_lead) o bloquea (decision_request), dejando artifacts y eventos en el hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.coordinator import ThreadCoordinator
from local_control_center.threads.repository import ThreadsRepository

RUNTIME_AVAILABLE = {
    "runtimeStatus": {
        "providers": [{"id": "codex_cli", "executable": True, "available": True, "canEditWorkspace": True}]
    }
}
RUNTIME_UNAVAILABLE = {
    "runtimeStatus": {
        "providers": [{"id": "codex_cli", "executable": False, "available": False, "canEditWorkspace": False}]
    }
}


def _thread(connection, tmp_path: Path) -> dict:
    project = ProjectsRepository(connection).create_project(
        name="threads-coordinator",
        path=tmp_path / "workspace",
        template_id="other",
        create_directory=True,
        source="runtime",
    )
    return ThreadsRepository(connection).create_thread(
        project_id=project["id"],
        owner_type="workspace",
        owner_id="workspace-1",
        title="Coordinator thread",
    )


def test_coordinator_queues_clear_intent_for_async_product_loop(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        result = coordinator.post_message(
            thread_id=thread["id"],
            content="Add a new dashboard endpoint to list active workspaces.",
            project_assessment=RUNTIME_AVAILABLE,
        )

        assert result["blocked"] is False
        assert result["thread"]["status"] == "queued"
        assert result["run"]["status"] == "queued"
        assert result["run"]["jobId"].startswith("job-")
        kinds = [message["kind"] for message in result["messages"]]
        assert kinds == ["user"]
        assert result["decision"] is None
        job = JobsRepository(connection).get_job(result["run"]["jobId"])
        assert job["kind"] == "thread.product_loop.run"
        assert job["payload"]["threadId"] == thread["id"]


def test_coordinator_blocks_when_runtime_is_unavailable(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection)

        result = coordinator.post_message(
            thread_id=thread["id"],
            content="Implement the onboarding dashboard.",
            project_assessment=RUNTIME_UNAVAILABLE,
        )

        assert result["blocked"] is True
        assert result["thread"]["status"] == "waiting_decision"
        kinds = [message["kind"] for message in result["messages"]]
        assert kinds == ["user", "decision_request"]
        assert result["decision"] is not None
        assert result["decision"]["status"] == "pending"
        assert result["decision"]["options"]


def test_coordinator_blocks_on_low_confidence_prompt(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection)

        result = coordinator.post_message(
            thread_id=thread["id"],
            content="help",
            project_assessment=RUNTIME_AVAILABLE,
        )

        assert result["blocked"] is True
        assert result["thread"]["status"] == "waiting_decision"


def test_coordinator_attaches_intake_artifact_and_event(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        repo = ThreadsRepository(connection)

        coordinator.post_message(
            thread_id=thread["id"],
            content="Refactor the auth module and add tests.",
            project_assessment=RUNTIME_AVAILABLE,
        )

        artifacts = repo.list_artifacts(thread["id"])
        assert any(item["kind"] == "intake_classification" for item in artifacts)
        events = repo.list_events(thread["id"])
        event_types = [event["type"] for event in events]
        assert "message_received" in event_types
        assert "classification_completed" in event_types
        assert "team_planned" in event_types
        assert "run_queued" in event_types


def test_coordinator_marks_research_required_when_intent_needs_sources(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        repo = ThreadsRepository(connection)

        coordinator.post_message(
            thread_id=thread["id"],
            content="Research official Python documentation and cite sources before deciding.",
            project_assessment=RUNTIME_AVAILABLE,
        )

        research_artifacts = [
            artifact for artifact in repo.list_artifacts(thread["id"]) if artifact["kind"] == "research_report"
        ]
        assert len(research_artifacts) == 1
        payload = research_artifacts[0]["metadata"]
        assert payload["status"] == "research_required"
        assert payload["sources"] == []
        assert payload["recommendation"]["decision"] == "Official documentation is required before a technical decision."


def test_resolve_decision_queues_original_message_for_execution(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        blocked = coordinator.post_message(
            thread_id=thread["id"],
            content="help",
            project_assessment=RUNTIME_AVAILABLE,
        )
        decision_id = blocked["decision"]["id"]

        resolved = coordinator.resolve_decision(
            thread_id=thread["id"],
            decision_id=decision_id,
            resolution="implementation",
            decided_by="user",
        )

        assert resolved["decision"]["status"] == "resolved"
        assert resolved["thread"]["status"] == "queued"

        events = ThreadsRepository(connection).list_events(thread["id"])
        event_types = [event["type"] for event in events]
        assert event_types[-2:] == ["decision_resolved", "run_queued"]

        jobs = JobsRepository(connection).list_jobs(thread["projectId"])
        queued_thread_jobs = [job for job in jobs if job["kind"] == "thread.product_loop.run"]
        assert len(queued_thread_jobs) == 1
        job = queued_thread_jobs[0]
        assert job["payload"]["threadId"] == thread["id"]
        assert job["payload"]["message"] == "help"
        assert job["payload"]["decision"]["planMode"] == "execute"
        assert job["payload"]["decision"]["resolution"] == "implementation"


def test_coordinator_redacts_secrets_in_thread_summary(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        # Built from parts so no literal secret lands in source; matches the sk- value pattern.
        secret = "sk-" + ("test" * 5)

        coordinator.post_message(
            thread_id=thread["id"],
            content=f"Use {secret} to call the dashboard API.",
            project_assessment=RUNTIME_AVAILABLE,
        )

        refreshed = ThreadsRepository(connection).get_thread(thread["id"])
        assert secret not in refreshed["summary"]
        assert "[redacted]" in refreshed["summary"]
