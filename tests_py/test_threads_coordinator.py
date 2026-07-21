"""Tests del ThreadCoordinator: ejecuta el clasificador determinista sobre el mensaje de usuario y
responde (aido_lead) o bloquea (decision_request), dejando artifacts y eventos en el hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.settings.repository import SettingsRepository
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


def test_coordinator_queues_product_loop_with_message_run_metadata(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        result = coordinator.post_message(
            thread_id=thread["id"],
            content="Add a new dashboard endpoint to list active workspaces.",
            project_assessment=RUNTIME_AVAILABLE,
            metadata={
                "teamMode": "critical",
                "risk": "high",
                "allowUnknownCost": True,
                "allow_unknown_cost": True,
                "requireApprovalForUnknownCost": False,
                "require_approval_for_unknown_cost": False,
                "privacyLevel": "local_private",
                "autonomy": "guided",
                "userMode": "aido_decide",
            },
        )

        job = JobsRepository(connection).get_job(result["run"]["jobId"])
        assert job["kind"] == "thread.product_loop.run"
        assert job["payload"]["runMetadata"]["teamMode"] == "critical"
        assert job["payload"]["runMetadata"]["risk"] == "high"
        assert "allowUnknownCost" not in job["payload"]["runMetadata"]
        assert "allow_unknown_cost" not in job["payload"]["runMetadata"]
        assert "requireApprovalForUnknownCost" not in job["payload"]["runMetadata"]
        assert "require_approval_for_unknown_cost" not in job["payload"]["runMetadata"]
        assert job["payload"]["runMetadata"]["privacyLevel"] == "local_private"
        assert job["payload"]["runMetadata"]["autonomy"] == "guided"
        assert job["payload"]["runMetadata"]["userMode"] == "aido_decide"


def _queued_run_metadata(connection, coordinator: ThreadCoordinator, thread: dict, **kwargs) -> dict:
    result = coordinator.post_message(
        thread_id=thread["id"],
        content="Add a new dashboard endpoint to list active workspaces.",
        project_assessment=RUNTIME_AVAILABLE,
        **kwargs,
    )
    job = JobsRepository(connection).get_job(result["run"]["jobId"])
    return job["payload"]["runMetadata"]


def test_economy_mode_setting_governs_the_next_run(tmp_path: Path) -> None:
    """El modo elegido en Settings se sella en el run: deja de ser un control cosmético."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        SettingsRepository(connection).set_value(
            "project.loop.teamMode", "project", thread["projectId"], "economy"
        )

        metadata = _queued_run_metadata(connection, coordinator, thread)

        assert metadata["teamMode"] == "economy"
        # Force local sigue apagado: la privacidad no se toca sin que el operador lo pida.
        assert "privacyLevel" not in metadata


def test_message_metadata_may_override_the_mode_but_never_relaxes_force_local(tmp_path: Path) -> None:
    """El modo por mensaje es legítimo; force local es un control de privacidad y no se puede relajar."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        settings = SettingsRepository(connection)
        settings.set_value("project.loop.teamMode", "project", thread["projectId"], "economy")
        settings.set_value("project.routing.forceLocal", "project", thread["projectId"], True)

        metadata = _queued_run_metadata(
            connection,
            coordinator,
            thread,
            metadata={"teamMode": "critical", "privacyLevel": "remote_allowed"},
        )

        assert metadata["teamMode"] == "critical"
        assert metadata["privacyLevel"] == "local_private"


def test_invalid_message_mode_falls_back_to_the_project_setting(tmp_path: Path) -> None:
    """Un modo inválido por mensaje no gana: se usa la decisión persistida del operador."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        SettingsRepository(connection).set_value(
            "project.loop.teamMode", "project", thread["projectId"], "critical"
        )

        metadata = _queued_run_metadata(
            connection, coordinator, thread, metadata={"teamMode": "cheapest_possible"}
        )

        assert metadata["teamMode"] == "critical"


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
        assert result["decision"]["options"] == ["Continue in plan-only mode"]
        remediation_rows = connection.execute(
            """
            SELECT blocker_type, action_type, payload_json
            FROM remediation_actions
            WHERE thread_id = ?
            """,
            (thread["id"],),
        ).fetchall()
        remediation_actions = {(row["blocker_type"], row["action_type"]) for row in remediation_rows}
        assert ("thread_intake_decision_required", "answer_question") in remediation_actions
        assert ("runtime_not_executable", "open_settings_section") in remediation_actions
        assert ("runtime_not_executable", "validate_runtime") in remediation_actions
        assert ("runtime_not_executable", "switch_runtime") in remediation_actions
        runtime_validation = next(
            row
            for row in remediation_rows
            if row["blocker_type"] == "runtime_not_executable" and row["action_type"] == "validate_runtime"
        )
        validation_payload = json.loads(runtime_validation["payload_json"])
        assert validation_payload["runtimeId"] == "codex_cli"
        assert validation_payload["details"]["providers"][0]["id"] == "codex_cli"


def test_resolve_runtime_blocked_decision_queues_plan_only_run(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        blocked = coordinator.post_message(
            thread_id=thread["id"],
            content="Implement the onboarding dashboard.",
            project_assessment=RUNTIME_UNAVAILABLE,
        )

        resolved = coordinator.resolve_decision(
            thread_id=thread["id"],
            decision_id=blocked["decision"]["id"],
            resolution="Continue in plan-only mode",
            decided_by="user",
        )

        assert resolved["decision"]["status"] == "resolved"
        assert resolved["thread"]["status"] == "queued"
        jobs = JobsRepository(connection).list_jobs(thread["projectId"])
        queued_thread_jobs = [job for job in jobs if job["kind"] == "thread.product_loop.run"]
        assert len(queued_thread_jobs) == 1
        job_payload = queued_thread_jobs[0]["payload"]
        assert job_payload["planOnly"] is True
        assert job_payload["runMetadata"]["planOnly"] is True
        assert job_payload["runMetadata"]["userMode"] == "continue_in_plan_only_mode"
        assert job_payload["decision"]["resolution"] == "Continue in plan-only mode"


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
        assert result["decision"]["options"] == ["Diagnosis", "Implementation", "Research"]
        remediation = connection.execute(
            """
            SELECT *
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'thread_intake_decision_required'
              AND action_type = 'answer_question'
            """,
            (thread["id"],),
        ).fetchone()
        assert remediation is not None
        remediation_payload = json.loads(remediation["payload_json"])
        assert remediation_payload["decisionId"] == result["decision"]["id"]
        assert remediation_payload["options"] == result["decision"]["options"]

        resolved = BlockerRemediationService(connection, root=tmp_path).execute(
            remediation["id"],
            platform=object(),
            payload={"answer": "Implementation", "decidedBy": "user"},
        )
        jobs = JobsRepository(connection).list_jobs(thread["projectId"])

        assert resolved["execution"]["status"] == "completed"
        assert resolved["execution"]["thread"]["status"] == "queued"
        assert resolved["remediation"]["status"] == "resolved"
        assert len(jobs) == 1
        assert jobs[0]["kind"] == "thread.product_loop.run"


def test_coordinator_blocks_high_similarity_instead_of_queueing_duplicate(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="threads-similarity-coordinator",
            path=tmp_path / "workspace",
            template_id="other",
            create_directory=True,
            source="runtime",
        )
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="ThreadSimilarityService duplicate request detection",
            summary="Detect when a user asks for work that was already handled in another thread.",
        )
        repo.append_message(
            thread_id=existing["id"],
            kind="agent_summary",
            author="aido_lead",
            content="Use lexical normalization, keywords and token overlap before creating new work.",
        )
        source = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-new",
            title="New duplicate request",
        )
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        result = coordinator.post_message(
            thread_id=source["id"],
            content="Implement ThreadSimilarityService to detect duplicate requests with lexical overlap.",
            project_assessment=RUNTIME_AVAILABLE,
        )

        assert result["blocked"] is True
        assert result["thread"]["status"] == "waiting_decision"
        assert result["run"]["status"] == "blocked"
        assert result["run"]["jobId"] is None
        assert (
            "Esto parece relacionado con ThreadSimilarityService duplicate request detection"
            in result["messages"][1]["content"]
        )
        assert result["decision"]["options"] == [
            "continue_existing",
            "improve_existing",
            "performance_pass",
            "create_new_anyway",
        ]
        remediation = connection.execute(
            """
            SELECT *
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'thread_similarity_decision_required'
              AND action_type = 'answer_question'
            """,
            (source["id"],),
        ).fetchone()
        assert remediation is not None
        remediation_payload = json.loads(remediation["payload_json"])
        assert remediation_payload["decisionId"] == result["decision"]["id"]
        assert remediation_payload["options"] == result["decision"]["options"]
        assert remediation_payload["candidateThreadId"] == existing["id"]
        assert JobsRepository(connection).list_jobs(project["id"]) == []

        resolved = BlockerRemediationService(connection, root=tmp_path).execute(
            remediation["id"],
            platform=object(),
            payload={"answer": "improve_existing", "decidedBy": "user"},
        )
        assert resolved["execution"]["status"] == "completed"
        assert resolved["execution"]["thread"]["status"] == "resolved"
        assert resolved["remediation"]["status"] == "resolved"
        similarity_events = connection.execute(
            "SELECT * FROM thread_similarity_events WHERE source_thread_id = ?",
            (source["id"],),
        ).fetchall()
        assert len(similarity_events) == 1
        assert similarity_events[0]["candidate_thread_id"] == existing["id"]
        assert similarity_events[0]["action"] == "improve_existing"
        assert JobsRepository(connection).list_jobs(project["id"]) == []


def test_coordinator_metadata_mode_skips_similarity_gate_and_persists(tmp_path: Path) -> None:
    """Un `mode` de similitud en metadata registra la decisión ya tomada en el intake: el mensaje
    se persiste con esa metadata y el coordinator no vuelve a bloquear por duplicado."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="threads-similarity-metadata",
            path=tmp_path / "workspace",
            template_id="other",
            create_directory=True,
            source="runtime",
        )
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="ThreadSimilarityService duplicate request detection",
            summary="Detect when a user asks for work that was already handled in another thread.",
        )
        repo.append_message(
            thread_id=existing["id"],
            kind="agent_summary",
            author="aido_lead",
            content="Use lexical normalization, keywords and token overlap before creating new work.",
        )
        fresh = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-new",
            title="New duplicate request",
        )
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        duplicate_content = (
            "Implement ThreadSimilarityService to detect duplicate requests with lexical overlap."
        )

        created_new = coordinator.post_message(
            thread_id=fresh["id"],
            content=duplicate_content,
            project_assessment=RUNTIME_AVAILABLE,
            metadata={"mode": "create_new_anyway", "similarThreadId": existing["id"]},
        )

        assert created_new["blocked"] is False
        assert created_new["run"]["status"] == "queued"
        assert created_new["messages"][0]["metadata"] == {
            "mode": "create_new_anyway",
            "similarThreadId": existing["id"],
        }

        improved = coordinator.post_message(
            thread_id=existing["id"],
            content=duplicate_content,
            project_assessment=RUNTIME_AVAILABLE,
            metadata={"mode": "improve_existing"},
        )

        assert improved["blocked"] is False
        assert improved["run"]["status"] == "queued"
        assert improved["messages"][0]["metadata"] == {"mode": "improve_existing"}


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


def test_coordinator_queues_research_job_and_keeps_research_event_internal(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)
        repo = ThreadsRepository(connection)

        result = coordinator.post_message(
            thread_id=thread["id"],
            content="Research official Python documentation and cite sources before deciding.",
            project_assessment=RUNTIME_AVAILABLE,
        )

        jobs = JobsRepository(connection).list_jobs(thread["projectId"])
        assert [job["kind"] for job in jobs] == ["thread.research.run"]
        research_job = jobs[0]
        assert research_job["payload"]["threadId"] == thread["id"]
        assert research_job["payload"]["query"] == (
            "Research official Python documentation and cite sources before deciding."
        )
        assert result["blocked"] is False
        assert result["run"]["status"] == "queued"
        assert result["run"]["jobId"] == research_job["id"]

        research_artifacts = [
            artifact
            for artifact in repo.list_artifacts(thread["id"])
            if artifact["kind"] == "research_report"
        ]
        assert research_artifacts == []
        events = repo.list_events(thread["id"])
        assert "research_running" in [event["type"] for event in events]


def test_resolve_decision_queues_original_message_for_execution(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        blocked = coordinator.post_message(
            thread_id=thread["id"],
            content="help",
            project_assessment=RUNTIME_AVAILABLE,
            metadata={
                "teamMode": "critical",
                "risk": "high",
                "allowUnknownCost": True,
                "allow_unknown_cost": True,
                "requireApprovalForUnknownCost": False,
                "require_approval_for_unknown_cost": False,
                "privacyLevel": "local_private",
            },
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
        assert job["payload"]["runMetadata"]["teamMode"] == "critical"
        assert job["payload"]["runMetadata"]["risk"] == "high"
        assert "allowUnknownCost" not in job["payload"]["runMetadata"]
        assert "allow_unknown_cost" not in job["payload"]["runMetadata"]
        assert "requireApprovalForUnknownCost" not in job["payload"]["runMetadata"]
        assert "require_approval_for_unknown_cost" not in job["payload"]["runMetadata"]
        assert job["payload"]["runMetadata"]["privacyLevel"] == "local_private"
        assert job["payload"]["runMetadata"]["userMode"] == "implementation"


def test_resolving_functionality_blocker_decision_requeues_blocked_thread(tmp_path: Path) -> None:
    """Reproduce el loop bloqueado sin salida: el gate de funcionalidad existente deja el hilo en
    'blocked' (la transición del run pisa 'waiting_decision') y resolver la decisión debe reencolar
    el run con la elección del usuario en vez de dejar el hilo bloqueado para siempre."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        repo = ThreadsRepository(connection)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        repo.append_message(
            thread_id=thread["id"],
            kind="user",
            author="user",
            content="Refactoriza y actualiza Spring Boot",
            metadata={},
        )
        request = repo.append_message(
            thread_id=thread["id"],
            kind="decision_request",
            author="aido_lead",
            content="Existing functionality detected: choose how to proceed.",
            metadata={"source": "functionality_registry", "functionalityId": "functionality-x"},
        )
        decision = repo.create_decision(
            thread_id=thread["id"],
            message_id=request["id"],
            title="Existing functionality detected",
            prompt="Existing functionality detected: choose how to proceed.",
            options=["continue_existing", "improve_existing", "performance_pass", "create_new_anyway"],
            metadata={"source": "functionality_registry", "functionalityId": "functionality-x"},
        )
        repo.set_status(thread["id"], "blocked")

        resolved = coordinator.resolve_decision(
            thread_id=thread["id"],
            decision_id=decision["id"],
            resolution="continue_existing",
            decided_by="user",
        )

        assert resolved["decision"]["status"] == "resolved"
        assert resolved["thread"]["status"] == "queued"
        queued_thread_jobs = [
            job
            for job in JobsRepository(connection).list_jobs(thread["projectId"])
            if job["kind"] == "thread.product_loop.run"
        ]
        assert len(queued_thread_jobs) == 1
        payload = queued_thread_jobs[0]["payload"]
        assert payload["runMetadata"]["functionalityDecision"] == "continue_existing"


def test_resolve_decision_recovers_open_thread_with_source_message(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        repo = ThreadsRepository(connection)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        blocked = coordinator.post_message(
            thread_id=thread["id"],
            content="help",
            project_assessment=RUNTIME_AVAILABLE,
        )
        decision_id = blocked["decision"]["id"]
        repo.set_status(thread["id"], "open")

        resolved = coordinator.resolve_decision(
            thread_id=thread["id"],
            decision_id=decision_id,
            resolution="implementation",
            decided_by="user",
        )

        assert resolved["decision"]["status"] == "resolved"
        assert resolved["thread"]["status"] == "queued"
        queued_thread_jobs = [
            job
            for job in JobsRepository(connection).list_jobs(thread["projectId"])
            if job["kind"] == "thread.product_loop.run"
        ]
        assert len(queued_thread_jobs) == 1


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
