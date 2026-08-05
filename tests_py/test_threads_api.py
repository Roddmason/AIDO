"""Tests del router HTTP de threads: crear hilo, enviar mensaje (coordinator responde o bloquea),
artifacts visibles en el hilo, guardas de escritura y presencia en el overview.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.shared.event_bus import EventBus
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Threads", path=tmp_path / "threads", template_id="other"
    )
    return project["id"]


def _token(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def _create_thread(client, headers, project_id: str, title: str = "First thread") -> dict:
    response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project_id,
            "ownerType": "workspace",
            "ownerId": "workspace-1",
            "title": title,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["thread"]


def test_create_thread_persists_and_lists(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, _token(runtime), project_id)
        assert thread["id"].startswith("thread-")
        assert thread["status"] == "open"

        listed = client.get("/api/v1/threads", params={"projectId": project_id})
        assert listed.status_code == 200
        ids = [item["id"] for item in listed.json()["threads"]]
        assert thread["id"] in ids
    finally:
        runtime.close()


def test_create_thread_rejects_blank_title(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        response = client.post(
            "/api/v1/threads",
            headers=_token(runtime),
            json={
                "projectId": project_id,
                "ownerType": "workspace",
                "ownerId": "workspace-1",
                "title": "   ",
            },
        )
        assert response.status_code == 422
    finally:
        runtime.close()


def test_create_thread_requires_write_token(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        response = client.post(
            "/api/v1/threads",
            json={
                "projectId": project_id,
                "ownerType": "workspace",
                "ownerId": "workspace-1",
                "title": "No token",
            },
        )
        assert response.status_code == 403
    finally:
        runtime.close()


def test_patch_thread_renames_title_and_records_audit(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id, title="Before rename")

        renamed = client.patch(
            f"/api/v1/threads/{thread['id']}",
            headers=headers,
            json={"title": "After rename"},
        )

        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["thread"]["title"] == "After rename"
        audit = EventBus(runtime.connection).list_audit_events(project_id)[0]
        assert audit["action"] == "thread.renamed"
        assert audit["target"] == thread["id"]
    finally:
        runtime.close()


def test_patch_thread_requires_write_token(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        response = client.patch(f"/api/v1/threads/{thread['id']}", json={"title": "No token"})

        assert response.status_code == 403
    finally:
        runtime.close()


def test_archive_unarchive_and_delete_lifecycle_filters_threads(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        archived_thread = _create_thread(client, headers, project_id, title="Archive target")
        deleted_thread = _create_thread(client, headers, project_id, title="Delete target")

        archived = client.post(
            f"/api/v1/threads/{archived_thread['id']}/archive",
            headers=headers,
            json={"reason": "Resolved", "actor": "operator"},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["thread"]["archivedAt"]
        assert archived.json()["auditEvent"]["action"] == "thread.archived"

        default_list = client.get("/api/v1/threads", params={"projectId": project_id})
        assert [item["id"] for item in default_list.json()["threads"]] == [deleted_thread["id"]]

        with_archived = client.get(
            "/api/v1/threads",
            params={"projectId": project_id, "includeArchived": True},
        )
        assert archived_thread["id"] in [item["id"] for item in with_archived.json()["threads"]]

        unarchived = client.post(
            f"/api/v1/threads/{archived_thread['id']}/unarchive",
            headers=headers,
            json={"reason": "Reopened", "actor": "operator"},
        )
        assert unarchived.status_code == 200, unarchived.text
        assert unarchived.json()["thread"]["archivedAt"] is None
        assert unarchived.json()["auditEvent"]["action"] == "thread.unarchived"

        deleted = client.request(
            "DELETE",
            f"/api/v1/threads/{deleted_thread['id']}",
            headers=headers,
            json={"reason": "Superseded", "actor": "operator"},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["thread"]["deletedAt"]
        assert deleted.json()["auditEvent"]["action"] == "thread.deleted"

        visible = client.get("/api/v1/threads", params={"projectId": project_id})
        assert [item["id"] for item in visible.json()["threads"]] == [archived_thread["id"]]

        with_deleted = client.get(
            "/api/v1/threads",
            params={"projectId": project_id, "includeDeleted": True},
        )
        assert deleted_thread["id"] in [item["id"] for item in with_deleted.json()["threads"]]
    finally:
        runtime.close()


def test_archive_requires_write_token(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        response = client.post(
            f"/api/v1/threads/{thread['id']}/archive",
            json={"reason": "No token", "actor": "operator"},
        )

        assert response.status_code == 403
    finally:
        runtime.close()


def test_running_thread_delete_returns_blocking_reason(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        ThreadsRepository(runtime.connection).set_status(thread["id"], "running")

        response = client.request(
            "DELETE",
            f"/api/v1/threads/{thread['id']}",
            headers=headers,
            json={"reason": "cleanup", "actor": "operator"},
        )

        assert response.status_code == 409
        assert "running" in response.json()["detail"]
    finally:
        runtime.close()


def test_post_message_queues_product_loop_job_and_returns_incremental_run(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": "Add a new dashboard endpoint to list active workspaces."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["blocked"] is False
        assert body["thread"]["status"] == "queued"
        assert body["run"]["status"] == "queued"
        assert body["run"]["jobId"].startswith("job-")
        assert [message["kind"] for message in body["messages"]] == ["user"]

        events = client.get(f"/api/v1/threads/{thread['id']}/events", params={"afterSeq": 0})
        assert events.status_code == 200
        event_body = events.json()
        assert event_body["threadStatus"] == "queued"
        assert event_body["running"] is True
        assert [event["type"] for event in event_body["events"]] == [
            "message_received",
            "classification_completed",
            "team_planned",
            "run_queued",
        ]
    finally:
        runtime.close()


def test_post_message_does_not_trust_internal_resource_approval_metadata(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={
                "content": "Implement a backend change with normal resource routing.",
                "metadata": {
                    "teamMode": "critical",
                    "risk": "high",
                    "approvedResourceSelections": [
                        {
                            "role": "backend_engineer",
                            "providerId": "nvidia_nim",
                            "model": "nvidia/nemotron-coder",
                            "runtime": "api",
                        }
                    ],
                    "retryOfLoopId": "product-loop-user-spoof",
                    "remediationActionId": "remediation-user-spoof",
                    "continueOfLoopId": "product-loop-user-continue-spoof",
                    "feedbackId": "feedback-user-spoof",
                },
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        job = JobsRepository(runtime.connection).get_job(body["run"]["jobId"])
        run_metadata = job["payload"]["runMetadata"]

        assert run_metadata["teamMode"] == "critical"
        assert run_metadata["risk"] == "high"
        assert run_metadata["userMode"] == "aido_decide"
        assert "approvedResourceSelections" not in run_metadata
        assert "retryOfLoopId" not in run_metadata
        assert "remediationActionId" not in run_metadata
        assert "continueOfLoopId" not in run_metadata
        assert "feedbackId" not in run_metadata
    finally:
        runtime.close()


def test_post_message_research_job_returns_public_queued_run_status(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": "Research official API documentation for runtime provider setup."},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["blocked"] is False
        assert body["thread"]["status"] == "queued"
        assert body["run"]["status"] == "queued"
        assert body["run"]["jobId"].startswith("job-")
        assert body["run"]["reason"] == "ResearchAgent job queued."

        events = client.get(f"/api/v1/threads/{thread['id']}/events", params={"afterSeq": 0})
        assert events.status_code == 200
        assert "research_running" in [event["type"] for event in events.json()["events"]]
    finally:
        runtime.close()


def test_post_message_blocks_on_ambiguous_prompt(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": "help"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["blocked"] is True
        assert body["thread"]["status"] == "waiting_decision"
        assert body["run"]["status"] == "blocked"
        assert body["run"]["jobId"] is None
        assert body["decision"]["status"] == "pending"
    finally:
        runtime.close()


def test_post_message_accepts_similarity_metadata_and_skips_duplicate_gate(tmp_path: Path) -> None:
    """El intake ya resolvió la deduplicación: el mensaje llega con `metadata.mode`, se persiste tal
    cual y el coordinator encola el run en vez de volver a bloquear por similitud."""
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        existing = _create_thread(client, headers, project_id, title="Dashboard endpoint work")
        seeded = client.post(
            f"/api/v1/threads/{existing['id']}/messages",
            headers=headers,
            json={"content": "Add a new dashboard endpoint to list active workspaces."},
        )
        assert seeded.status_code == 200, seeded.text

        fresh = _create_thread(client, headers, project_id, title="Duplicate dashboard request")
        response = client.post(
            f"/api/v1/threads/{fresh['id']}/messages",
            headers=headers,
            json={
                "content": "Add a new dashboard endpoint to list active workspaces.",
                "metadata": {"mode": "create_new_anyway", "similarThreadId": existing["id"]},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["blocked"] is False
        assert body["run"]["status"] == "queued"
        assert body["messages"][0]["metadata"] == {
            "mode": "create_new_anyway",
            "similarThreadId": existing["id"],
        }
    finally:
        runtime.close()


def test_thread_remediations_materialize_run_worker_once_when_worker_is_stopped(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        queued = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": "Add a new dashboard endpoint to list active workspaces."},
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["thread"]["status"] == "queued"

        response = client.get(f"/api/v1/threads/{thread['id']}/remediations")

        assert response.status_code == 200, response.text
        actions = {(item["blockerType"], item["actionType"]) for item in response.json()["remediations"]}
        assert ("worker_not_running", "run_worker_once") in actions

        persisted = runtime.connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM remediation_actions
            WHERE thread_id = ? AND blocker_type = 'worker_not_running' AND action_type = 'run_worker_once'
            """,
            (thread["id"],),
        ).fetchone()["total"]
        assert persisted == 1
    finally:
        runtime.close()


def test_thread_remediations_api_redacts_secrets_from_payload(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, _token(runtime), project_id)
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-secret-test",
                project_id,
                thread["id"],
                "product-loop-secret",
                "runtime",
                "provider_missing_credentials",
                "Configure provider credentials sk-title-secret1234567",
                "The provider token sk-supersecret1234567 is missing.",
                "open_settings_section",
                json.dumps(
                    {
                        "section": "providers",
                        "token": "sk-supersecret1234567",
                        "hint": "Bearer secret-token-123456",
                    }
                ),
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.get(f"/api/v1/threads/{thread['id']}/remediations")

        assert response.status_code == 200, response.text
        payload = json.dumps(response.json(), sort_keys=True)
        assert "sk-title-secret1234567" not in payload
        assert "sk-supersecret1234567" not in payload
        assert "secret-token-123456" not in payload
        assert "[redacted]" in payload
    finally:
        runtime.close()


def test_dismiss_remediation_marks_action_resolved(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-dismiss-test",
                project_id,
                thread["id"],
                "product-loop-dismiss",
                "runtime",
                "runtime_not_executable",
                "Validate runtime",
                "Validate the selected runtime.",
                "validate_runtime",
                "{}",
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post("/api/v1/remediations/remediation-dismiss-test/dismiss", headers=headers)

        assert response.status_code == 200, response.text
        remediation = response.json()["remediation"]
        assert remediation["status"] == "dismissed"
        assert remediation["resolvedAt"] is not None
    finally:
        runtime.close()


def test_execute_validate_runtime_requires_target_runtime_executable(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        monkeypatch.setattr(
            "local_control_center.remediations.service.RuntimeStatusService.list_provider_statuses",
            lambda _service, project_id=None: [
                {
                    "id": "codex_cli",
                    "displayName": "Codex CLI",
                    "executable": False,
                    "reason": "CLI runtime is not authenticated.",
                },
                {
                    "id": "openai_compatible",
                    "displayName": "OpenAI Compatible",
                    "executable": True,
                    "reason": "Provider is executable.",
                },
            ],
        )
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-validate-runtime-target-test",
                project_id,
                thread["id"],
                "product-loop-runtime-target",
                "runtime",
                "runtime_not_executable",
                "Validate runtime",
                "Validate the selected runtime.",
                "validate_runtime",
                json.dumps({"projectId": project_id, "runtimeId": "codex_cli"}),
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-validate-runtime-target-test/execute",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "blocked"
        assert body["execution"]["runtimeId"] == "codex_cli"
        assert body["remediation"]["status"] == "pending"
    finally:
        runtime.close()


def test_provider_remediations_validate_and_retry_blocked_provider_or_runtime(tmp_path: Path) -> None:
    runtime, _client_app = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(_client_app, _token(runtime), project_id)

        service = BlockerRemediationService(runtime.connection, root=tmp_path)
        missing_credentials = service.create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id="product-loop-provider-target",
            stage="provider",
            reason="Provider credentials are missing.",
            details={"providerId": "openrouter"},
        )
        unhealthy_provider = service.create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id="product-loop-provider-health",
            stage="provider",
            reason="Provider health is unhealthy.",
            details={"providerId": "openrouter"},
        )
        auth_missing = service.create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id="product-loop-runtime-auth",
            stage="runtime",
            reason="Runtime is not authenticated.",
            details={"runtimeId": "codex_cli"},
        )

        validate_action = next(
            action for action in missing_credentials if action["actionType"] == "validate_runtime"
        )
        assert validate_action["blockerType"] == "provider_missing_credentials"
        assert validate_action["payload"]["runtimeId"] == "openrouter"
        assert any(action["actionType"] == "retry_loop" for action in missing_credentials)
        assert any(action["actionType"] == "retry_loop" for action in unhealthy_provider)
        assert any(action["actionType"] == "retry_loop" for action in auth_missing)
    finally:
        runtime.close()


def test_git_branch_missing_remediation_can_retry_after_branch_recovery(tmp_path: Path) -> None:
    runtime, _client_app = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(_client_app, _token(runtime), project_id)

        actions = BlockerRemediationService(runtime.connection, root=tmp_path).create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id="product-loop-git-branch",
            stage="git",
            reason="The required execution branch is missing.",
            details={},
        )

        action_types = {action["actionType"] for action in actions}
        assert {"create_branch", "checkout_branch", "retry_loop"}.issubset(action_types)
    finally:
        runtime.close()


def test_resource_manager_mixed_blockers_keep_approval_and_configuration_actions(tmp_path: Path) -> None:
    runtime, _client_app = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(_client_app, _token(runtime), project_id)

        actions = BlockerRemediationService(runtime.connection, root=tmp_path).create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id="product-loop-resource-mixed",
            stage="resource_manager",
            reason="AIResourceManager could not select every required role.",
            details={
                "resourceBlockers": [
                    {
                        "role": "backend_engineer",
                        "taskId": "task-backend",
                        "reason": "No AI resource satisfied policy and capability filters.",
                        "decision": {
                            "selected": None,
                            "approvalRequired": False,
                            "decisionReason": "No AI resource satisfied policy and capability filters.",
                        },
                    },
                    {
                        "role": "aido_lead",
                        "taskId": "task-lead",
                        "reason": "AIResourceManager selected a resource that requires approval before execution.",
                        "decision": {
                            "selected": {
                                "providerId": "nvidia_nim",
                                "model": "nvidia/nemotron-coder",
                                "runtime": "api",
                            },
                            "approvalRequired": True,
                            "decisionReason": "Approval is required before execution.",
                        },
                    },
                ]
            },
        )

        action_types = {action["actionType"] for action in actions}
        approval_action = next(
            action for action in actions if action["actionType"] == "approve_resource_decision"
        )

        assert {"approve_resource_decision", "open_settings_section", "retry_loop"}.issubset(action_types)
        assert approval_action["blockerType"] == "resource_manager_unconfigured"
        assert approval_action["payload"]["resourceApprovals"][0]["role"] == "aido_lead"
        assert approval_action["payload"]["resourceApprovals"][0]["providerId"] == "nvidia_nim"
    finally:
        runtime.close()


@pytest.mark.skipif(not git_available(), reason="git CLI is required for git remediation execution")
def test_execute_git_init_remediation_initializes_repository_and_resolves_action(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        project_path = tmp_path / "threads"
        project_path.mkdir(parents=True, exist_ok=True)
        thread = _create_thread(client, headers, project_id)
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-git-init-api-test",
                project_id,
                thread["id"],
                "product-loop-git-init",
                "git",
                "git_not_initialized",
                "Initialize Git repository",
                "Create Git metadata in the project folder.",
                "git_init",
                "{}",
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-git-init-api-test/execute",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "completed"
        assert body["execution"]["action"] == "git_init"
        assert body["execution"]["currentBranch"] == "dev"
        assert body["execution"]["commitCreated"] is False
        assert body["remediation"]["status"] == "resolved"
        assert (project_path / ".git").exists()
        assert ".env*" in (project_path / ".gitignore").read_text(encoding="utf-8")
        assert run_git(["branch", "--show-current"], cwd=project_path).stdout.strip() == "dev"
        assert any(
            "git init" in call["payload"].get("command", "")
            for call in AgentsRepository(runtime.connection).list_agent_tool_calls()
        )
    finally:
        runtime.close()


def test_execute_switch_runtime_requires_executable_target(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        monkeypatch.setattr(
            "local_control_center.remediations.service.RuntimeStatusService.list_provider_statuses",
            lambda _service, project_id=None: [
                {
                    "id": "claude_code_cli",
                    "displayName": "Claude Code CLI",
                    "executable": False,
                    "reason": "CLI runtime is not authenticated.",
                },
            ],
        )
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-switch-runtime-target-test",
                project_id,
                thread["id"],
                "product-loop-runtime-switch",
                "runtime",
                "runtime_not_executable",
                "Switch runtime",
                "Switch to the selected runtime.",
                "switch_runtime",
                json.dumps({"projectId": project_id, "runtimeId": "claude_code_cli"}),
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-switch-runtime-target-test/execute",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "blocked"
        assert body["execution"]["runtimeId"] == "claude_code_cli"
        assert body["remediation"]["status"] == "pending"
        preference = runtime.connection.execute("SELECT default_runtime FROM runtime_preferences").fetchone()
        assert preference["default_runtime"] != "claude_code_cli"
    finally:
        runtime.close()


def test_execute_answer_question_rejects_answer_outside_options(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        decision = ThreadsRepository(runtime.connection).create_decision(
            thread_id=thread["id"],
            title="Choose delivery mode",
            prompt="Which delivery mode should AIDO use?",
            options=["Plan only", "Execute with QA"],
        )
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-answer-question-options-test",
                project_id,
                thread["id"],
                "product-loop-question-options",
                "product_owner",
                "po_needs_input",
                "Answer ProductOwnerAgent question",
                "Choose one of the offered options.",
                "answer_question",
                json.dumps({"threadId": thread["id"], "decisionId": decision["id"]}),
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-answer-question-options-test/execute",
            headers=headers,
            json={"payload": {"answer": "Do something else"}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "blocked"
        assert body["execution"]["options"] == ["Plan only", "Execute with QA"]
        assert body["remediation"]["status"] == "pending"
        assert ThreadsRepository(runtime.connection).get_decision(decision["id"])["status"] == "pending"
    finally:
        runtime.close()


def test_execute_answer_question_rejects_client_options_override(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        decision = ThreadsRepository(runtime.connection).create_decision(
            thread_id=thread["id"],
            title="Choose delivery mode",
            prompt="Which delivery mode should AIDO use?",
            options=[],
        )
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-answer-question-options-override-test",
                project_id,
                thread["id"],
                "product-loop-question-options-override",
                "product_owner",
                "po_needs_input",
                "Answer ProductOwnerAgent question",
                "Choose one of the offered options.",
                "answer_question",
                json.dumps(
                    {
                        "threadId": thread["id"],
                        "decisionId": decision["id"],
                        "options": ["Plan only", "Execute with QA"],
                    }
                ),
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-answer-question-options-override-test/execute",
            headers=headers,
            json={"payload": {"answer": "Untrusted path", "options": ["Untrusted path"]}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "blocked"
        assert body["execution"]["options"] == ["Plan only", "Execute with QA"]
        assert body["remediation"]["status"] == "pending"
        assert ThreadsRepository(runtime.connection).get_decision(decision["id"])["status"] == "pending"
    finally:
        runtime.close()


def test_execute_save_patch_persists_evidence_artifact_without_returning_patch(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        patch = (
            "diff --git a/service.py b/service.py\n"
            "--- a/service.py\n"
            "+++ b/service.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        monkeypatch.setattr(
            "local_control_center.remediations.service.GitWorkspaceService.diff",
            lambda _service, _project_id: {
                "status": "completed",
                "changedFiles": ["service.py"],
                "diff": patch,
            },
        )
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-save-patch-test",
                project_id,
                thread["id"],
                "product-loop-save-patch",
                "git",
                "git_dirty_tree",
                "Save dirty tree patch",
                "Save the current diff before changing branches.",
                "save_patch",
                "{}",
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-save-patch-test/execute",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        execution = body["execution"]
        assert execution["status"] == "completed"
        assert execution["artifactId"].startswith("artifact-")
        assert "diff --git" not in json.dumps(body)
        artifact = runtime.connection.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (execution["artifactId"],),
        ).fetchone()
        assert artifact is not None
        assert artifact["kind"] == "git_patch"
        assert Path(artifact["path"]).read_text(encoding="utf-8") == patch
        assert body["remediation"]["status"] == "pending"
    finally:
        runtime.close()


def test_execute_save_patch_blocks_when_diff_is_empty(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        monkeypatch.setattr(
            "local_control_center.remediations.service.GitWorkspaceService.diff",
            lambda _service, _project_id: {
                "status": "completed",
                "changedFiles": [],
                "diff": "",
            },
        )
        runtime.connection.execute(
            """
            INSERT INTO remediation_actions
                (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
                 action_type, payload_json, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                "remediation-empty-save-patch-test",
                project_id,
                thread["id"],
                "product-loop-empty-save-patch",
                "git",
                "git_dirty_tree",
                "Save empty patch",
                "Save should not succeed without a real diff.",
                "save_patch",
                "{}",
                "2026-01-01T00:00:00.000Z",
            ),
        )

        response = client.post(
            "/api/v1/remediations/remediation-empty-save-patch-test/execute",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "blocked"
        assert body["execution"]["action"] == "save_patch"
        assert "No diff" in body["execution"]["reason"]
        assert body["remediation"]["status"] == "pending"
        artifacts = runtime.connection.execute("SELECT * FROM artifacts").fetchall()
        assert artifacts == []
    finally:
        runtime.close()


def test_similarity_query_endpoint_returns_archived_candidate_and_masks_secret(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        repo = ThreadsRepository(runtime.connection)
        secret = "sk-" + ("test" * 5)
        existing = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="workspace-1",
            title="ThreadSimilarityService duplicate request detection",
            summary=f"Detect duplicate requests without leaking {secret}.",
        )
        repo.append_message(
            thread_id=existing["id"],
            kind="agent_summary",
            author="aido_lead",
            content="Use normalized lexical tokens and Jaccard overlap for similar threads.",
        )
        repo.archive_thread(existing["id"], reason="completed", actor="operator")

        response = client.get(
            "/api/v1/threads/similar",
            params={
                "projectId": project_id,
                "query": "Implement ThreadSimilarityService duplicate request detection.",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["candidates"][0]["threadId"] == existing["id"]
        assert body["candidates"][0]["status"] == "archived"
        assert secret not in response.text
        assert "[redacted]" in response.text
    finally:
        runtime.close()


def test_thread_specific_similarity_endpoint_excludes_deleted_unless_requested(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        repo = ThreadsRepository(runtime.connection)
        source = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="source",
            title="Duplicate detector source",
            summary="Find similar already worked requests.",
        )
        deleted = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="deleted",
            title="Duplicate detector deleted candidate",
            summary="Find similar already worked requests.",
        )
        repo.soft_delete_thread(deleted["id"], reason="obsolete", actor="operator")

        hidden = client.get(f"/api/v1/threads/{source['id']}/similar")
        visible = client.get(f"/api/v1/threads/{source['id']}/similar", params={"includeDeleted": True})

        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["candidates"] == []
        assert visible.status_code == 200, visible.text
        assert [item["threadId"] for item in visible.json()["candidates"]] == [deleted["id"]]
    finally:
        runtime.close()


def test_mark_similarity_endpoint_records_improve_existing_link(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        repo = ThreadsRepository(runtime.connection)
        source = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="source",
            title="New duplicate detector work",
        )
        candidate = repo.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id="candidate",
            title="Existing duplicate detector work",
        )

        response = client.post(
            f"/api/v1/threads/{source['id']}/similar/{candidate['id']}/mark",
            headers=headers,
            json={"action": "improve_existing", "score": 0.84, "reason": "Shared normalized goal."},
        )

        assert response.status_code == 200, response.text
        event = response.json()["event"]
        assert event["action"] == "improve_existing"
        assert event["sourceThreadId"] == source["id"]
        assert event["candidateThreadId"] == candidate["id"]
    finally:
        runtime.close()


def test_message_requires_write_token(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            json={"content": "Add a feature"},
        )
        assert response.status_code == 403
    finally:
        runtime.close()


def test_artifacts_appear_in_thread_detail(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": "Refactor the auth module and add tests."},
        )

        detail = client.get(f"/api/v1/threads/{thread['id']}")
        assert detail.status_code == 200
        artifacts = detail.json()["artifacts"]
        assert any(item["kind"] == "intake_classification" for item in artifacts)
        assert detail.json()["events"]
    finally:
        runtime.close()


def test_research_run_attaches_research_card_payload_to_thread(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        workspace = WorkspacesRepository(runtime.connection, root=runtime.cwd).allocate_workspace(
            project_id=project_id,
            task_id="thread-research",
            agent_id="research_agent",
            reason="thread research card test",
            isolation_type="directory",
        )
        thread = _create_thread(client, headers, project_id, title="Research thread")
        docs_url = "https://docs.python.org/3/library/asyncio-task.html"

        response = client.post(
            "/api/v1/agents/research/runs",
            headers=headers,
            json={
                "projectId": project_id,
                "workspaceId": workspace["id"],
                "taskId": "thread-research",
                "sources": [
                    {
                        "url": docs_url,
                        "publisher": "Python Software Foundation",
                        "content": "TaskGroup is official structured concurrency documentation.",
                        "fetchedAt": "2026-06-25T10:00:00.000Z",
                    }
                ],
                "conclusions": [
                    {
                        "statement": "Use TaskGroup for structured concurrency.",
                        "citations": [docs_url],
                        "webBased": True,
                    }
                ],
                "technicalDecisions": [
                    {
                        "title": "Structured concurrency API",
                        "decision": "Use asyncio.TaskGroup for concurrent subtasks.",
                        "sourceUrls": [docs_url],
                    }
                ],
                "metadata": {"threadId": thread["id"]},
            },
        )
        assert response.status_code == 202, response.text

        detail = client.get(f"/api/v1/threads/{thread['id']}")
        assert detail.status_code == 200
        research_cards = [
            artifact for artifact in detail.json()["artifacts"] if artifact["kind"] == "research_report"
        ]
        assert len(research_cards) == 1
        payload = research_cards[0]["metadata"]
        assert payload["status"] == "research_ready"
        assert payload["recommendation"]["decision"] == "Use asyncio.TaskGroup for concurrent subtasks."
        assert payload["sources"][0]["url"] == docs_url
        assert payload["sources"][0]["hash"]
        assert payload["sources"][0]["fetchedAt"] == "2026-06-25T10:00:00.000Z"
    finally:
        runtime.close()


def test_unknown_thread_returns_404(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/threads/thread-does-not-exist")
        assert response.status_code == 404
    finally:
        runtime.close()


def test_resolve_decision_queues_thread_execution(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)
        blocked = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": "help"},
        ).json()
        decision_id = blocked["decision"]["id"]

        resolved = client.post(
            f"/api/v1/threads/{thread['id']}/decisions/{decision_id}/resolve",
            headers=headers,
            json={"resolution": "implementation", "decidedBy": "user"},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["decision"]["status"] == "resolved"
        assert resolved.json()["thread"]["status"] == "queued"

        events = client.get(f"/api/v1/threads/{thread['id']}/events", params={"afterSeq": 0})
        assert events.status_code == 200
        event_types = [event["type"] for event in events.json()["events"]]
        assert "decision_resolved" in event_types
        assert event_types[-1] == "run_queued"
        assert events.json()["running"] is True
    finally:
        runtime.close()


def test_resolve_product_owner_decision_defers_run_until_batch_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id, title="Product decision batch")
        threads = ThreadsRepository(runtime.connection)
        source_message = threads.append_message(
            thread_id=thread["id"],
            kind="user",
            author="user",
            content="Upgrade Spring Boot and migrate the frontend.",
            metadata={"teamMode": "balanced", "risk": "medium"},
        )
        decision_specs = [
            ("JDK target", "Which JDK should the upgrade target?", ["current JDK", "latest LTS"]),
            (
                "Migration strategy",
                "Should the frontend migration be incremental?",
                ["incremental", "full rewrite"],
            ),
        ]
        pending_decisions = []
        decisions = []
        for title, prompt, options in decision_specs:
            decision = threads.create_decision(
                thread_id=thread["id"],
                title=title,
                prompt=prompt,
                options=options,
                metadata={
                    "sourceMessageId": source_message["id"],
                    "intents": ["migration"],
                    "risk": "medium",
                    "requiredRoles": ["product_owner", "technical_lead"],
                    "requiredGates": ["implementation_plan"],
                },
            )
            decisions.append(decision)
            pending_decisions.append(
                {
                    "decisionId": decision["id"],
                    "title": title,
                    "prompt": prompt,
                    "options": options,
                }
            )
        threads.set_status(thread["id"], "waiting_decision")
        coordinator = ProductLoopCoordinator(runtime.connection, root=tmp_path)
        loop = coordinator.start(
            project_id=project_id,
            title="Product decision batch",
            context={
                "durableRun": {
                    "status": "awaiting_user",
                    "message": source_message["content"],
                    "requestMeta": {"messageId": source_message["id"]},
                    "thread": {
                        "projectThreadId": thread["id"],
                        "messageId": source_message["id"],
                    },
                    "productOwner": {
                        "status": "needs_input",
                        "reason": "Product decisions are required.",
                        "pendingThreadDecisions": pending_decisions,
                    },
                }
            },
        )
        coordinator.transition(loop["id"], to_state="discovery")
        coordinator.transition(loop["id"], to_state="awaiting_user")
        remediation = BlockerRemediationService(runtime.connection, root=tmp_path)
        remediation.create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="product_owner",
            reason="Product decisions are required.",
            details={"status": "needs_input", "pendingDecisions": pending_decisions},
        )
        runtime.connection.execute(
            "DELETE FROM remediation_actions WHERE loop_id = ?",
            (loop["id"],),
        )
        original_create_for_blocked_run = BlockerRemediationService.create_for_blocked_run

        def crash_remediation_backfill(*args, **kwargs):
            raise RuntimeError("controlled ProductOwner remediation backfill failure")

        monkeypatch.setattr(
            BlockerRemediationService,
            "create_for_blocked_run",
            crash_remediation_backfill,
        )

        missing_remediation = client.post(
            f"/api/v1/threads/{thread['id']}/decisions/{decisions[0]['id']}/resolve",
            headers=headers,
            json={"resolution": "current JDK", "decidedBy": "user"},
        )

        assert missing_remediation.status_code == 422
        assert "remediation" in missing_remediation.text.lower()
        assert coordinator.get(loop["id"])["state"] == "awaiting_user"
        assert threads.get_thread(thread["id"])["status"] == "waiting_decision"
        assert all(threads.get_decision(decision["id"])["status"] == "pending" for decision in decisions)
        assert JobsRepository(runtime.connection).list_jobs(project_id) == []

        monkeypatch.setattr(
            BlockerRemediationService,
            "create_for_blocked_run",
            original_create_for_blocked_run,
        )
        remediation.create_for_blocked_run(
            project_id=project_id,
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="product_owner",
            reason="Product decisions are required.",
            details={"status": "needs_input", "pendingDecisions": pending_decisions},
        )
        first_action = next(
            action
            for action in remediation.repository.list_for_thread(thread["id"])
            if (action.get("payload") or {}).get("decisionId") == decisions[0]["id"]
        )
        remediation.repository.mark_status(first_action["id"], "dismissed")

        first = client.post(
            f"/api/v1/threads/{thread['id']}/decisions/{decisions[0]['id']}/resolve",
            headers=headers,
            json={"resolution": "current JDK", "decidedBy": "user"},
        )

        assert first.status_code == 200, first.text
        assert first.json()["decision"]["status"] == "resolved"
        assert first.json()["thread"]["status"] == "waiting_decision"
        assert coordinator.get(loop["id"])["state"] == "awaiting_user"
        assert JobsRepository(runtime.connection).list_jobs(project_id) == []
        answer_actions = [
            action
            for action in BlockerRemediationService(
                runtime.connection,
                root=tmp_path,
            ).repository.list_for_thread(thread["id"])
            if action["actionType"] == "answer_question"
        ]
        assert [threads.get_decision(decision["id"])["status"] for decision in decisions] == [
            "resolved",
            "pending",
        ]
        assert sorted(action["status"] for action in answer_actions) == ["pending", "resolved"]

        second = client.post(
            f"/api/v1/threads/{thread['id']}/decisions/{decisions[1]['id']}/resolve",
            headers=headers,
            json={"resolution": "incremental", "decidedBy": "user"},
        )

        jobs = JobsRepository(runtime.connection).list_jobs(project_id)
        assert second.status_code == 200, second.text
        assert second.json()["decision"]["status"] == "resolved"
        assert second.json()["thread"]["status"] == "queued"
        assert coordinator.get(loop["id"])["state"] == "cancelled"
        assert len([job for job in jobs if job["kind"] == "thread.product_loop.run"]) == 1
        assert all(
            action["status"] == "resolved"
            for action in BlockerRemediationService(
                runtime.connection,
                root=tmp_path,
            ).repository.list_for_thread(thread["id"])
            if action["actionType"] == "answer_question"
        )
    finally:
        runtime.close()


def test_overview_includes_threads(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _token(runtime)
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, headers, project_id)

        overview = client.get("/api/v1/overview")
        assert overview.status_code == 200
        body = overview.json()
        assert "threads" in body
        assert any(item["id"] == thread["id"] for item in body["threads"])
    finally:
        runtime.close()


def test_thread_detail_keeps_only_the_most_recent_tail(tmp_path, monkeypatch) -> None:
    """El detalle acota mensajes/eventos al tail reciente en orden ascendente; hilos chicos van completos."""
    from local_control_center.threads import api as threads_api

    monkeypatch.setattr(threads_api, "THREAD_DETAIL_MESSAGE_TAIL", 3)
    monkeypatch.setattr(threads_api, "THREAD_DETAIL_EVENT_TAIL", 3)

    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        headers = _token(runtime)
        thread = _create_thread(client, headers, project_id)
        repo = ThreadsRepository(runtime.connection)
        for position in range(6):
            repo.append_message(
                thread_id=thread["id"],
                kind="operator_note",
                author="operator",
                content=f"Message {position:02d}",
            )
            repo.record_event(
                thread_id=thread["id"],
                type="loop.progress",
                payload={"position": position},
            )

        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        contents = [item["content"] for item in detail["messages"]]
        assert contents == ["Message 03", "Message 04", "Message 05"]
        events = [item["payload"]["position"] for item in detail["events"] if item["type"] == "loop.progress"]
        assert events == [3, 4, 5]
    finally:
        runtime.close()
