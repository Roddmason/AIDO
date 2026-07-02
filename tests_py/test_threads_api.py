"""Tests del router HTTP de threads: crear hilo, enviar mensaje (coordinator responde o bloquea),
artifacts visibles en el hilo, guardas de escritura y presencia en el overview.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.projects.repository import ProjectsRepository
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
