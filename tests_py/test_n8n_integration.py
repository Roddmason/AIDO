from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.threads.repository import ThreadsRepository
from tests_py.control_plane_fixture import ControlPlaneFixture

N8N_TOKEN = "sk-n8nsecret123456"


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, str], ControlPlaneFixture]:
    monkeypatch.setenv("AIDO_N8N_TOKEN", N8N_TOKEN)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    token = client.get("/api/v1/security/handshake").json()["token"]
    return client, {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}, store


def _project(store: ControlPlaneFixture, tmp_path: Path) -> dict[str, Any]:
    return store.create_project(name="n8n integration", path=tmp_path / "project", template_id="other")


def _target(client: TestClient, headers: dict[str, str], project_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/integrations/n8n/webhook-targets",
        headers=headers,
        json={
            "projectId": project_id,
            "url": "https://n8n.example.invalid/webhook/aido",
            "credentialRef": "env:AIDO_N8N_TOKEN",
            "enabled": True,
            "allowedEventTypes": ["thread.created", "qa.failed"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["target"]


def test_n8n_status_and_configure_use_requested_api_contract(tmp_path: Path, monkeypatch) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)

    initial = client.get(
        "/api/v1/integrations/n8n/status",
        headers=headers,
        params={"projectId": project["id"]},
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["status"]["configured"] is False
    assert initial.json()["status"]["enabledTargetCount"] == 0

    configured = client.post(
        "/api/v1/integrations/n8n/configure",
        headers=headers,
        json={
            "projectId": project["id"],
            "url": "https://n8n.example.invalid/webhook/aido",
            "credentialRef": "env:AIDO_N8N_TOKEN",
            "enabled": True,
            "allowedEventTypes": ["thread.created", "approval.required"],
            "metadata": {"apiKey": N8N_TOKEN},
        },
    )
    assert configured.status_code == 201, configured.text
    assert configured.json()["target"]["credentialRef"] == "env:AIDO_N8N_TOKEN"
    assert N8N_TOKEN not in configured.text

    current = client.get(
        "/api/v1/integrations/n8n/status",
        headers=headers,
        params={"projectId": project["id"]},
    )
    assert current.status_code == 200, current.text
    status = current.json()["status"]
    assert status["configured"] is True
    assert status["targetCount"] == 1
    assert status["enabledTargetCount"] == 1
    assert status["allowedEventTypes"] == ["thread.created", "approval.required"]
    assert status["targets"][0]["url"] == "https://n8n.example.invalid/webhook/aido"
    assert N8N_TOKEN not in current.text


def test_n8n_emit_uses_requested_endpoint_with_mocked_outbound_webhook(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    target = _target(client, headers, project["id"])
    sent: list[dict[str, Any]] = []

    def fake_http_post(
        url: str, request_headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        sent.append({"url": url, "headers": request_headers, "payload": payload, "timeout": timeout_seconds})
        return {"statusCode": 200, "body": {"ok": True}}

    store.n8n_http_post = fake_http_post

    response = client.post(
        "/api/v1/integrations/n8n/emit",
        headers=headers,
        json={
            "projectId": project["id"],
            "targetId": target["id"],
            "eventType": "thread.created",
            "subjectId": "thread-1",
            "payload": {"title": "Created from AIDO"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["delivery"]["status"] == "delivered"
    assert sent[0]["payload"]["eventType"] == "thread.created"
    assert sent[0]["headers"]["Authorization"] == f"Bearer {N8N_TOKEN}"


def test_n8n_inbound_token_adds_message_and_returns_thread_status_without_execution(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    _target(client, headers, project["id"])

    created = client.post(
        f"/api/v1/integrations/n8n/inbound/{N8N_TOKEN}",
        json={
            "projectId": project["id"],
            "action": "create_thread",
            "payload": {
                "title": "Thread from inbound path",
                "ownerType": "workspace",
                "ownerId": "n8n",
            },
        },
    )
    assert created.status_code == 201, created.text
    thread_id = created.json()["thread"]["id"]

    message = client.post(
        f"/api/v1/integrations/n8n/inbound/{N8N_TOKEN}",
        json={
            "projectId": project["id"],
            "action": "add_message",
            "payload": {
                "threadId": thread_id,
                "content": "External automation added context.",
                "metadata": {"apiKey": N8N_TOKEN},
            },
        },
    )
    assert message.status_code == 201, message.text
    assert message.json()["message"]["content"] == "External automation added context."
    assert message.json()["thread"]["status"] == "open"

    status = client.post(
        f"/api/v1/integrations/n8n/inbound/{N8N_TOKEN}",
        json={
            "projectId": project["id"],
            "action": "get_status",
            "payload": {"threadId": thread_id},
        },
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"]["threadStatus"] == "open"
    assert status.json()["status"]["messageCount"] == 1
    assert store.jobs.list_jobs(project["id"]) == []
    assert N8N_TOKEN not in message.text


def test_n8n_inbound_rate_limit_and_critical_delivery_approval_are_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    _target(client, headers, project["id"])
    store.n8n_inbound_rate_limit = {"maxRequests": 2, "windowSeconds": 60}

    blocked_approval = client.post(
        f"/api/v1/integrations/n8n/inbound/{N8N_TOKEN}",
        json={
            "projectId": project["id"],
            "action": "approve_delivery",
            "payload": {"deliveryId": "delivery-1", "approved": True},
        },
    )
    assert blocked_approval.status_code == 403

    first = client.post(
        f"/api/v1/integrations/n8n/inbound/{N8N_TOKEN}",
        json={
            "projectId": project["id"],
            "action": "get_status",
            "payload": {},
        },
    )
    assert first.status_code == 200, first.text

    limited = client.post(
        f"/api/v1/integrations/n8n/inbound/{N8N_TOKEN}",
        json={
            "projectId": project["id"],
            "action": "get_status",
            "payload": {},
        },
    )
    assert limited.status_code == 429


def test_n8n_emit_event_delivers_mocked_outbound_event_and_redacts_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    target = _target(client, headers, project["id"])
    sent: list[dict[str, Any]] = []

    def fake_http_post(
        url: str, request_headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        sent.append(
            {
                "url": url,
                "headers": request_headers,
                "payload": payload,
                "timeout": timeout_seconds,
            }
        )
        return {"statusCode": 202, "body": {"accepted": True, "echo": N8N_TOKEN}}

    store.n8n_http_post = fake_http_post

    response = client.post(
        "/api/v1/integrations/n8n/emit-event",
        headers=headers,
        json={
            "projectId": project["id"],
            "targetId": target["id"],
            "eventType": "thread.created",
            "subjectId": "thread-1",
            "payload": {
                "title": "Created from AIDO",
                "apiKey": N8N_TOKEN,
                "message": f"do not leak {N8N_TOKEN}",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivery"]["status"] == "delivered"
    assert body["delivery"]["statusCode"] == 202
    assert sent[0]["url"] == "https://n8n.example.invalid/webhook/aido"
    assert sent[0]["headers"]["Authorization"] == f"Bearer {N8N_TOKEN}"
    assert sent[0]["payload"]["eventType"] == "thread.created"
    assert sent[0]["payload"]["payload"]["apiKey"] == "[redacted]"
    assert N8N_TOKEN not in response.text
    sqlite_dump = "\n".join(store.connection.iterdump())
    assert N8N_TOKEN not in sqlite_dump
    assert "[redacted]" in sqlite_dump


def test_n8n_test_endpoint_uses_configured_target(tmp_path: Path, monkeypatch) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    target = _target(client, headers, project["id"])
    sent: list[dict[str, Any]] = []

    def fake_http_post(
        url: str, request_headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        sent.append({"url": url, "headers": request_headers, "payload": payload})
        return {"statusCode": 200, "body": {"ok": True}}

    store.n8n_http_post = fake_http_post

    response = client.post(
        "/api/v1/integrations/n8n/test",
        headers=headers,
        json={
            "projectId": project["id"],
            "targetId": target["id"],
            "eventType": "thread.created",
            "payload": {"sample": "ok"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["delivery"]["status"] == "delivered"
    assert sent[0]["payload"]["subjectId"] == "n8n-test"
    assert sent[0]["payload"]["payload"] == {"test": True, "sample": "ok"}


def test_n8n_inbound_webhook_creates_thread_with_scoped_token(tmp_path: Path, monkeypatch) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    _target(client, headers, project["id"])

    denied = client.post(
        "/api/v1/integrations/n8n/webhook",
        json={
            "projectId": project["id"],
            "action": "create_thread",
            "payload": {"title": "No token", "ownerType": "workspace", "ownerId": "n8n"},
        },
    )
    assert denied.status_code == 403

    created = client.post(
        "/api/v1/integrations/n8n/webhook",
        headers={"X-AIDO-N8N-Token": N8N_TOKEN},
        json={
            "projectId": project["id"],
            "action": "create_thread",
            "payload": {
                "title": "Thread from n8n",
                "ownerType": "workspace",
                "ownerId": "n8n",
                "message": "Create a QA checklist.",
            },
        },
    )

    assert created.status_code == 201, created.text
    thread = created.json()["thread"]
    assert thread["projectId"] == project["id"]
    assert thread["ownerType"] == "workspace"
    assert thread["ownerId"] == "n8n"
    assert thread["title"] == "Thread from n8n"
    messages = ThreadsRepository(store.connection).list_messages(thread["id"])
    assert [message["content"] for message in messages] == ["Create a QA checklist."]


def test_n8n_inbound_webhook_creates_loop_without_executing_commands(tmp_path: Path, monkeypatch) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    _target(client, headers, project["id"])

    blocked_command = client.post(
        "/api/v1/integrations/n8n/webhook",
        headers={"X-AIDO-N8N-Token": N8N_TOKEN},
        json={
            "projectId": project["id"],
            "action": "execute_command",
            "payload": {"command": "echo unsafe"},
        },
    )
    assert blocked_command.status_code == 403

    blocked_approval = client.post(
        "/api/v1/integrations/n8n/webhook",
        headers={"X-AIDO-N8N-Token": N8N_TOKEN},
        json={
            "projectId": project["id"],
            "action": "approve_critical_action",
            "payload": {"approval": "ship"},
        },
    )
    assert blocked_approval.status_code == 403

    created = client.post(
        "/api/v1/integrations/n8n/webhook",
        headers={"X-AIDO-N8N-Token": N8N_TOKEN},
        json={
            "projectId": project["id"],
            "action": "create_loop",
            "payload": {
                "title": "Loop from n8n",
                "initiativeId": "initiative-n8n",
                "context": {"source": "n8n", "apiKey": N8N_TOKEN},
            },
        },
    )

    assert created.status_code == 201, created.text
    loop = created.json()["loop"]
    assert loop["projectId"] == project["id"]
    assert loop["state"] == "goal_received"
    assert loop["status"] == "active"
    assert loop["context"]["source"] == "n8n"
    assert loop["context"]["apiKey"] == "[redacted]"
    assert store.jobs.list_jobs(project["id"]) == []
    assert N8N_TOKEN not in json.dumps(created.json())


def test_n8n_emit_event_blocks_event_not_allowed_for_project_target(tmp_path: Path, monkeypatch) -> None:
    client, headers, store = _client(tmp_path, monkeypatch)
    project = _project(store, tmp_path)
    target = _target(client, headers, project["id"])
    sent: list[dict[str, Any]] = []
    store.n8n_http_post = lambda *args, **kwargs: sent.append({"args": args, "kwargs": kwargs})

    response = client.post(
        "/api/v1/integrations/n8n/emit-event",
        headers=headers,
        json={
            "projectId": project["id"],
            "targetId": target["id"],
            "eventType": "delivery.ready",
            "subjectId": "delivery-1",
            "payload": {"status": "ready"},
        },
    )

    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]
    assert sent == []
