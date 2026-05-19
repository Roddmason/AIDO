from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_sandbox_profile_edit_is_validated_and_audited(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    response = client.patch(
        "/api/v1/sandbox/profiles/default_docker",
        json={
            "reason": "Reduce default sandbox resources for local laptop smoke runs.",
            "allowedImages": ["python:3.13-slim"],
            "allowedNetworks": ["none"],
            "defaultNetwork": "none",
            "memory": "1g",
            "cpus": "1",
            "timeoutSeconds": 90,
            "status": "active",
        },
        headers=headers,
    )

    assert response.status_code == 202
    profile = response.json()["sandboxProfile"]
    assert profile["allowedImages"] == ["python:3.13-slim"]
    assert profile["memory"] == "1g"
    assert profile["timeoutSeconds"] == 90
    assert any(event["type"] == "sandbox.profile.updated" for event in store.events.list_events())
    assert any(event["action"] == "sandbox.profile.update" for event in store.events.list_audit_events())


def test_sandbox_profile_edit_rejects_network_escape_and_missing_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    missing_reason = client.patch(
        "/api/v1/sandbox/profiles/default_docker",
        json={"memory": "1g", "allowedNetworks": ["none"], "defaultNetwork": "none"},
        headers=headers,
    )
    assert missing_reason.status_code == 422

    network_escape = client.patch(
        "/api/v1/sandbox/profiles/default_docker",
        json={
            "reason": "Need network for install",
            "allowedNetworks": ["host"],
            "defaultNetwork": "host",
        },
        headers=headers,
    )
    assert network_escape.status_code == 422
    assert "network" in network_escape.json()["detail"].lower()
