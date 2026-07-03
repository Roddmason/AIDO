from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


class OllamaTagsHandler(BaseHTTPRequestHandler):
    models: list[str] = []
    status_code: int = 200
    seen_requests: list[dict[str, str | None]] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        type(self).seen_requests.append(
            {
                "method": "GET",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )
        if self.path != "/api/tags":
            self.send_error(404)
            return
        if type(self).status_code != 200:
            self.send_error(type(self).status_code)
            return
        payload = {"models": [{"name": model} for model in type(self).models]}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_ollama_tags_server(
    *, models: list[str] | None = None, status_code: int = 200
) -> tuple[str, type[OllamaTagsHandler], HTTPServer]:
    handler = type(
        "OllamaTagsTestHandler",
        (OllamaTagsHandler,),
        {"models": list(models or []), "status_code": status_code, "seen_requests": []},
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", handler, server


def client_with_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str], ControlPlaneFixture]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    token = client.get("/api/v1/security/handshake").json()["token"]
    return client, {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}, store


def endpoint_payload(endpoint_id: str, base_url: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": endpoint_id,
        "displayName": endpoint_id,
        "baseUrl": base_url,
        "enabled": True,
        **extra,
    }


def test_ollama_endpoints_create_local_health_and_catalog_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, _handler, server = run_ollama_tags_server(models=["llama3:latest", "nomic-embed-text"])
    try:
        client, headers, store = client_with_store(tmp_path, monkeypatch)

        created = client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json=endpoint_payload("ollama-local", base_url, kind="local"),
        )
        health = client.post("/api/v1/ollama/endpoints/ollama-local/health", headers=headers)
        synced = client.post("/api/v1/ollama/endpoints/ollama-local/sync-models", headers=headers)
        listed = client.get("/api/v1/ollama/endpoints")

        provider_rows = store.connection.execute(
            "SELECT provider_id, provider_type, api_format, base_url FROM provider_accounts "
            "WHERE provider_id = 'ollama-local'"
        ).fetchall()
        runtime_rows = store.connection.execute(
            "SELECT runtime_id, account_label, credential_ref FROM runtime_accounts "
            "WHERE runtime_id = 'ollama-local'"
        ).fetchall()
    finally:
        server.shutdown()

    assert created.status_code == 201
    assert created.json()["endpoint"]["id"] == "ollama-local"
    assert created.json()["endpoint"]["baseUrl"] == base_url
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "available"
    assert health.json()["health"]["models"] == ["llama3:latest", "nomic-embed-text"]
    assert synced.status_code == 200
    assert [item["model"] for item in synced.json()["models"]] == ["llama3:latest", "nomic-embed-text"]
    assert listed.status_code == 200
    assert any(item["id"] == "ollama-local" for item in listed.json()["endpoints"])
    assert [tuple(row) for row in provider_rows] == [("ollama-local", "local", "ollama", base_url)]
    assert [(row["runtime_id"], row["account_label"], row["credential_ref"]) for row in runtime_rows] == [
        ("ollama-local", "ollama-local", None)
    ]


def test_ollama_endpoint_accepts_remote_lan_http_without_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers, store = client_with_store(tmp_path, monkeypatch)

    created = client.post(
        "/api/v1/ollama/endpoints",
        headers=headers,
        json=endpoint_payload("ollama-remote-lan", "http://192.168.1.50:11434", kind="remote"),
    )
    listed = client.get("/api/v1/ollama/endpoints")
    provider = store.connection.execute(
        "SELECT provider_id, provider_type, api_format, credential_ref FROM provider_accounts "
        "WHERE provider_id = 'ollama-remote-lan'"
    ).fetchone()
    account = store.connection.execute(
        "SELECT runtime_id, credential_store_kind, credential_ref FROM runtime_accounts "
        "WHERE runtime_id = 'ollama-remote-lan'"
    ).fetchone()

    assert created.status_code == 201
    assert created.json()["endpoint"]["kind"] == "remote"
    assert created.json()["endpoint"]["credentialRef"] is None
    assert any(item["id"] == "ollama-remote-lan" for item in listed.json()["endpoints"])
    assert tuple(provider) == ("ollama-remote-lan", "gateway", "ollama", "")
    assert tuple(account) == ("ollama-remote-lan", "none", None)


def test_ollama_remote_endpoint_uses_optional_credential_ref_as_bearer_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_OLLAMA_REMOTE_KEY", "sk-ollamaendpoint123456")
    base_url, handler, server = run_ollama_tags_server(models=["qwen2.5-coder:14b"])
    try:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json=endpoint_payload(
                "ollama-remote-auth",
                base_url,
                kind="remote",
                credentialRef="env:AIDO_OLLAMA_REMOTE_KEY",
            ),
        )
        health = client.post("/api/v1/ollama/endpoints/ollama-remote-auth/health", headers=headers)
    finally:
        server.shutdown()

    assert created.status_code == 201
    assert created.json()["endpoint"]["credentialStatus"] == "configured"
    assert "sk-ollamaendpoint" not in json.dumps(created.json())
    assert health.status_code == 200
    assert health.json()["health"]["models"] == ["qwen2.5-coder:14b"]
    assert any(
        item["authorization"] == "Bearer sk-ollamaendpoint123456" for item in handler.seen_requests
    )
    assert "sk-ollamaendpoint" not in json.dumps(health.json())


def test_ollama_endpoint_health_marks_down_endpoint_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, _handler, server = run_ollama_tags_server(status_code=503)
    try:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json=endpoint_payload("ollama-down", base_url, kind="remote"),
        )
        health = client.post("/api/v1/ollama/endpoints/ollama-down/health", headers=headers)
    finally:
        server.shutdown()

    assert created.status_code == 201
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "unavailable"
    assert health.json()["health"]["healthStatus"] == "offline"
    assert health.json()["health"]["models"] == []


def test_routing_selects_remote_ollama_endpoint_when_local_has_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_url, _local_handler, local_server = run_ollama_tags_server(models=[])
    remote_url, _remote_handler, remote_server = run_ollama_tags_server(models=["llama3:latest"])
    try:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        store.connection.execute("UPDATE provider_accounts SET enabled = 0")
        store.connection.execute("UPDATE model_catalog SET enabled = 0")

        local_created = client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json=endpoint_payload("ollama-local", local_url, kind="local"),
        )
        remote_created = client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json=endpoint_payload("ollama-remote-lan", remote_url, kind="remote"),
        )
        local_health = client.post("/api/v1/ollama/endpoints/ollama-local/health", headers=headers)
        remote_synced = client.post(
            "/api/v1/ollama/endpoints/ollama-remote-lan/sync-models", headers=headers
        )
        response = client.post(
            "/api/v1/model-gateway/route/preview",
            headers=headers,
            json={
                "role": "analyst",
                "taskType": "doc_summary",
                "mode": "balanced_best_value",
                "contextTokensEstimate": 1200,
                "privacyLevel": "remote_allowed",
                "budgetRemainingUsd": 1,
            },
        )
    finally:
        local_server.shutdown()
        remote_server.shutdown()

    assert local_created.status_code == 201
    assert remote_created.status_code == 201
    assert local_health.status_code == 200
    assert remote_synced.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"]["provider"] == "ollama-remote-lan"
    assert payload["selected"]["model"] == "llama3:latest"
    assert all(item["provider"] != "ollama-local" for item in payload["candidates"])
