from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.app import create_app
from local_control_center.credentials.backends import KeyringBackend
from local_control_center.credentials.repository import CredentialRepository
from tests_py.control_plane_fixture import ControlPlaneFixture

SECRET = "credential-api-secret-material-0123456789"
ROTATED_SECRET = "credential-api-rotated-material-9876543210"


class _FakeKeyring:
    """Minimal keyring facade used by the API tests instead of the OS keychain."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def _client(
    tmp_path: Path, monkeypatch
) -> tuple[TestClient, dict[str, str], ControlPlaneFixture, _FakeKeyring]:
    fake_keyring = _FakeKeyring()
    monkeypatch.setattr(KeyringBackend, "_keyring", staticmethod(lambda: fake_keyring))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    token = client.get("/api/v1/security/handshake").json()["token"]
    return client, {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}, store, fake_keyring


def test_credentials_api_lifecycle_never_returns_secret_values(tmp_path: Path, monkeypatch) -> None:
    client, headers, store, fake_keyring = _client(tmp_path, monkeypatch)

    initial = client.get("/api/v1/credentials")
    assert initial.status_code == 200
    backends = {backend["kind"]: backend for backend in initial.json()["backends"]}
    assert {"keyring", "openbao", "vault", "dpapi_sqlite", "environment_override"} <= set(backends)
    assert backends["keyring"]["default"] is True

    denied = client.post(
        "/api/v1/credentials",
        json={"name": "codex/personal", "locator": "AIDO/codex", "value": SECRET},
    )
    assert denied.status_code == 403

    created_response = client.post(
        "/api/v1/credentials",
        json={
            "name": "codex/personal",
            "locator": "AIDO/codex",
            "value": SECRET,
            "authMode": "subscription",
            "metadata": {"runtimeId": "codex_cli"},
        },
        headers=headers,
    )
    assert created_response.status_code == 201
    created = created_response.json()["credential"]
    credential_id = created["id"]
    assert created["backendKind"] == "keyring"
    assert "value" not in created and "fingerprint" not in created and "salt" not in created
    assert SECRET not in json.dumps(created_response.json())
    assert fake_keyring.get_password("AIDO", "codex") == SECRET

    listed = client.get("/api/v1/credentials").json()["credentials"]
    assert [item["name"] for item in listed] == ["codex/personal"]
    assert SECRET not in json.dumps(listed)

    validation_response = client.post(f"/api/v1/credentials/{credential_id}/validate", headers=headers)
    assert validation_response.status_code == 200
    assert validation_response.json()["validation"] == {
        "name": "codex/personal",
        "present": True,
        "valid": True,
        "fingerprintMatches": True,
    }
    assert SECRET not in json.dumps(validation_response.json())

    rotated_response = client.post(
        f"/api/v1/credentials/{credential_id}/rotate",
        json={"value": ROTATED_SECRET},
        headers=headers,
    )
    assert rotated_response.status_code == 200
    assert ROTATED_SECRET not in json.dumps(rotated_response.json())
    assert fake_keyring.get_password("AIDO", "codex") == ROTATED_SECRET

    deleted_response = client.delete(f"/api/v1/credentials/{credential_id}", headers=headers)
    assert deleted_response.status_code == 200
    assert deleted_response.json()["deleted"] == {"name": "codex/personal", "deleted": True}
    assert fake_keyring.get_password("AIDO", "codex") is None

    audit = client.get("/api/v1/credentials/audit").json()["audit"]
    assert [entry["action"] for entry in audit] == ["create", "validate", "rotate", "delete"]
    assert SECRET not in json.dumps(audit) and ROTATED_SECRET not in json.dumps(audit)
    assert CredentialRepository(store.connection).find_credential_by_name("codex/personal") is None
    sqlite_text = " ".join(
        str(tuple(row)) for row in store.connection.execute("SELECT * FROM credential_audit")
    )
    assert SECRET not in sqlite_text and ROTATED_SECRET not in sqlite_text


def test_credentials_api_accepts_product_contract_aliases_and_reports_provider_usage(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers, store, fake_keyring = _client(tmp_path, monkeypatch)

    rejected = client.post(
        "/api/v1/credentials",
        json={
            "label": "bad raw ref",
            "credentialRef": "sk-aaaaaaaa",
            "value": SECRET,
        },
        headers=headers,
    )
    assert rejected.status_code == 400
    assert "raw secret" in rejected.json()["detail"]
    assert "sk-aaaa" not in rejected.json()["detail"]

    created_response = client.post(
        "/api/v1/credentials",
        json={
            "label": "Codex personal",
            "source": "keyring",
            "credentialRef": "AIDO/codex-personal",
            "value": SECRET,
        },
        headers=headers,
    )

    assert created_response.status_code == 201
    created = created_response.json()["credential"]
    assert created["label"] == "Codex personal"
    assert created["name"] == "Codex personal"
    assert created["source"] == "keyring"
    assert created["backendKind"] == "keyring"
    assert created["credentialRef"] == "keyring:AIDO/codex-personal"
    assert created["locator"] == "AIDO/codex-personal"
    assert created["lastRotatedAt"] is None
    assert created["providerUsages"] == []
    assert fake_keyring.get_password("AIDO", "codex-personal") == SECRET
    assert SECRET not in json.dumps(created_response.json())

    ProviderAccountStore(store.connection).patch_provider_account(
        "openai_compatible",
        {
            "displayName": "OpenAI Compatible",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "keyring:AIDO/codex-personal",
            "enabled": True,
        },
    )

    listed = client.get("/api/v1/credentials").json()["credentials"]
    credential = next(item for item in listed if item["id"] == created["id"])
    assert credential["providerUsages"] == [
        {
            "providerId": "openai_compatible",
            "displayName": "OpenAI Compatible",
            "credentialRef": "keyring:AIDO/codex-personal",
            "enabled": True,
        }
    ]
    assert SECRET not in json.dumps(credential)

    rotated_response = client.post(
        f"/api/v1/credentials/{created['id']}/rotate",
        json={"value": ROTATED_SECRET},
        headers=headers,
    )
    assert rotated_response.status_code == 200
    rotated = rotated_response.json()["credential"]
    assert rotated["lastRotatedAt"]
    assert rotated["rotatedAt"] == rotated["lastRotatedAt"]
    assert ROTATED_SECRET not in json.dumps(rotated_response.json())


def test_credentials_api_migrates_environment_overrides_without_copying_values(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIDO_GITHUB_TOKEN", SECRET)
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", ROTATED_SECRET)
    client, headers, store, _fake_keyring = _client(tmp_path, monkeypatch)

    response = client.post("/api/v1/credentials/migrate", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["source"] == "environment_override"
    assert SECRET not in json.dumps(payload) and ROTATED_SECRET not in json.dumps(payload)
    github = CredentialRepository(store.connection).get_credential_by_name("github/token")
    assert github["backendKind"] == "environment_override"
    assert github["locator"] == "AIDO_GITHUB_TOKEN"
    assert github["metadata"]["source"] == "environment_override"
    assert all(
        SECRET not in str(tuple(row)) and ROTATED_SECRET not in str(tuple(row))
        for row in store.connection.execute("SELECT * FROM credential_refs")
    )
