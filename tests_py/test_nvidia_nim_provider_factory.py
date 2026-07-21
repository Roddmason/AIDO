from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.architect_agent import ArchitectAgentRunner
from local_control_center.agents.architect_agent_contract import architect_agent_readiness
from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.developer_agent_contract import developer_agent_readiness
from local_control_center.agents.model_gateway import ModelGateway, provider_instance
from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
from local_control_center.agents.product_owner_agent_contract import product_owner_agent_readiness
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.factory import (
    ProviderAccountDisabledError,
    ProviderAdapterFactory,
    ProviderBaseUrlRequiredError,
    UnsupportedProviderCapabilityError,
)
from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.agents.security_agent import SecurityAgentRunner
from local_control_center.agents.security_agent_contract import security_agent_status
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


def _upsert_nvidia_endpoint(connection, provider_id: str) -> dict[str, object]:
    return ProviderAccountStore(connection).upsert_provider_account(
        {
            "providerId": provider_id,
            "displayName": provider_id,
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_enterprise",
            "apiFamily": "chat_completions",
            "termsMode": "accepted",
            "pricingMode": "configured",
            "baseUrl": f"https://{provider_id}.example.invalid/v1",
            "credentialRef": f"env:{provider_id.upper().replace('-', '_')}_API_KEY",
            "enabled": True,
        }
    )


def _create_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    db_path = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(db_path))
    runtime = ControlPlaneFixture(cwd=tmp_path, db_path=db_path)
    runtime.init()
    return TestClient(
        create_app(runtime=runtime, static_dir=None),
        raise_server_exceptions=raise_server_exceptions,
    )


def _auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


@contextmanager
def _nvidia_http_server(*, redirect_url: str | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {
        "modelCalls": 0,
        "chatCalls": 0,
        "chatPayloads": [],
        "authorization": [],
        "requests": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return None

        def _write_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            state["modelCalls"] += 1
            state["authorization"].append(self.headers.get("Authorization"))
            state["requests"].append({"method": "GET", "path": self.path})
            if self.path.endswith("/health/ready"):
                self._write_json({"object": "health.response", "message": "ready", "ready": True})
                return
            self._write_json({"object": "list", "data": [{"id": "nvidia/test-model"}]})

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            state["chatCalls"] += 1
            state["chatPayloads"].append(payload)
            state["authorization"].append(self.headers.get("Authorization"))
            state["requests"].append({"method": "POST", "path": self.path})
            if redirect_url is not None:
                self.send_response(302)
                self.send_header("Location", redirect_url)
                self.end_headers()
                return
            self._write_json(
                {
                    "choices": [{"message": {"content": "endpoint response"}}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 2,
                        "total_tokens": 4,
                    },
                }
            )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _anthropic_http_server() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"modelCalls": 0, "messageCalls": 0, "xApiKey": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return None

        def _write_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            state["modelCalls"] += 1
            state["xApiKey"].append(self.headers.get("x-api-key"))
            self._write_json({"data": [{"id": "claude-test", "display_name": "Claude Test"}]})

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(content_length)
            state["messageCalls"] += 1
            state["xApiKey"].append(self.headers.get("x-api-key"))
            self._write_json(
                {
                    "content": [{"type": "text", "text": "endpoint response"}],
                    "usage": {"input_tokens": 2, "output_tokens": 2},
                }
            )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _execute_brokered_model_call(
    connection,
    *,
    tmp_path: Path,
    tool: str,
    provider_id: str,
    model: str,
) -> dict[str, Any]:
    project = ProjectsRepository(connection).create_project(
        name=f"{tool} endpoint binding",
        path=tmp_path,
        template_id="other",
    )
    profile = AgentsRepository(connection).upsert_agent_profile(
        {
            "id": "developer_agent",
            "name": f"{tool} Broker Agent",
            "role": "implementer",
            "runtimeMode": "api",
            "permissionProfile": "dev_safe",
            "allowedTools": [tool],
            "allowedProviders": [provider_id],
            "allowedRuntimes": [provider_id],
        }
    )
    task_id = f"{tool}-broker-task"
    workspace_id = f"workspace-{tool}"
    run = AgentsRepository(connection).create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id=task_id,
        input_payload={},
        output_payload={},
        status="running",
    )
    connection.execute(
        """
        INSERT INTO workspaces
            (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
             metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'active', 'filesystem', '{}',
                '2026-07-13T00:00:00Z', '2026-07-13T00:00:00Z')
        """,
        (workspace_id, project["id"], task_id, profile["id"], str(tmp_path)),
    )
    return ToolBroker(connection).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": tool,
            "operation": "developer_agent_model_call",
            "runtimeId": provider_id,
            "workspaceId": workspace_id,
            "workspacePath": str(tmp_path),
            "path": str(tmp_path),
            "execute": True,
            "input": {
                "providerId": provider_id,
                "model": model,
                "messages": [{"role": "user", "content": "Use the selected endpoint only."}],
            },
        },
    )


def test_nvidia_policy_blocks_noncanonical_endpoint_by_provider_family(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        account = _upsert_nvidia_endpoint(connection, "nvidia-team-a")
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", False)

        decision = repository.runtime_policy_decision(
            provider_id="nvidia-team-a",
            provider_family=str(account["providerFamily"]),
            kind="api",
        )

    assert decision["allowed"] is False
    assert decision["reason"] == "runtime.nvidia.enabled is false."


def test_provider_instance_resolves_real_nvidia_adapter_for_noncanonical_endpoint(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        account = _upsert_nvidia_endpoint(connection, "nvidia-team-a")
        second_account = _upsert_nvidia_endpoint(connection, "nvidia-team-b")

        provider = provider_instance("nvidia-team-a", connection=connection)
        second_provider = provider_instance("nvidia-team-b", connection=connection)

        assert isinstance(provider, NvidiaNimProvider)
        assert isinstance(second_provider, NvidiaNimProvider)
        assert provider.provider_id == "nvidia-team-a"
        assert provider.base_url == account["baseUrl"]
        assert provider.credential_ref == account["credentialRef"]
        assert provider.connection is connection
        assert second_provider.provider_id == "nvidia-team-b"
        assert second_provider.base_url == second_account["baseUrl"]
        assert second_provider.credential_ref == second_account["credentialRef"]
    assert second_provider.connection is connection


def test_descriptive_provider_construction_keeps_disabled_legacy_preset_introspectable(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        provider = provider_instance("nvidia_nim", connection=connection)
        with pytest.raises(ProviderAccountDisabledError):
            ProviderAdapterFactory(connection).resolve_for_execution("nvidia_nim")

    assert isinstance(provider, NvidiaNimProvider)
    assert provider.provider_id == "nvidia_nim"


def test_nonhosted_nvidia_endpoint_does_not_inherit_hosted_or_global_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIDO_NVIDIA_BASE_URL", "https://global-nvidia.example.invalid/v1")
    monkeypatch.setenv("AIDO_NVIDIA_API_KEY", "global-secret-must-not-be-used")
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", "https://generic-global.example.invalid/v1")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-self-hosted")
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia-self-hosted",
            {"baseUrl": "", "credentialRef": ""},
        )

        with pytest.raises(ProviderBaseUrlRequiredError) as captured:
            ProviderAdapterFactory(connection).resolve("nvidia-self-hosted")

    assert captured.value.code == "provider_base_url_required"
    assert captured.value.provider_id == "nvidia-self-hosted"


@pytest.mark.parametrize(
    ("provider_id", "provider_type", "api_format", "provider_family"),
    [
        ("openrouter-team-empty", "gateway", "openai_compatible", "openrouter"),
        ("anthropic-team-empty", "api", "anthropic", "anthropic_api"),
        ("compatible-team-empty", "api", "openai_compatible", "openai_compatible"),
    ],
)
def test_named_provider_keeps_explicit_empty_endpoint_fields_without_canonical_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    provider_type: str,
    api_format: str,
    provider_family: str,
) -> None:
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", "https://canonical-openai.example.invalid/v1")
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "canonical-openai-secret")
    monkeypatch.setenv("AIDO_OPENROUTER_BASE_URL", "https://canonical-openrouter.example.invalid/v1")
    monkeypatch.setenv("AIDO_OPENROUTER_API_KEY", "canonical-openrouter-secret")
    monkeypatch.setenv("AIDO_ANTHROPIC_BASE_URL", "https://canonical-anthropic.example.invalid/v1")
    monkeypatch.setenv("AIDO_ANTHROPIC_API_KEY", "canonical-anthropic-secret")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": provider_id,
                "displayName": provider_id,
                "providerType": provider_type,
                "apiFormat": api_format,
                "providerFamily": provider_family,
                "baseUrl": "",
                "credentialRef": "",
                "enabled": True,
            }
        )

        provider = ProviderAdapterFactory(connection).resolve(provider_id)

    assert provider.provider_id == provider_id
    assert provider.base_url == ""
    assert provider.credential_ref == ""


def test_self_hosted_nvidia_chat_executes_without_fabricated_bearer(
    tmp_path: Path,
) -> None:
    with (
        _nvidia_http_server() as (base_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-self-hosted-no-auth")
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia-self-hosted-no-auth",
            {"baseUrl": base_url, "credentialRef": ""},
        )
        provider = ProviderAdapterFactory(connection).resolve_for_execution("nvidia-self-hosted-no-auth")

        response = provider.chat_completion(
            ModelRequest(
                model="nvidia/test-model",
                messages=[{"role": "user", "content": "Use self-hosted NIM."}],
            )
        )

    assert response.content == "endpoint response"
    assert state["chatCalls"] == 1
    assert state["authorization"] == [None]


def test_legacy_nvidia_id_remains_compatible_with_factory(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia_nim",
            {
                "enabled": True,
                "baseUrl": "https://legacy-nvidia.example.invalid/v1",
                "credentialRef": "env:LEGACY_NVIDIA_API_KEY",
            },
        )

        provider = provider_instance("nvidia_nim", connection=connection)

    assert isinstance(provider, NvidiaNimProvider)
    assert provider.provider_id == "nvidia_nim"
    assert provider.base_url == "https://legacy-nvidia.example.invalid/v1"
    assert provider.credential_ref == "env:LEGACY_NVIDIA_API_KEY"


def test_embedding_nvidia_family_resolves_typed_provider_without_network(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-embeddings")
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia-embeddings", {"apiFamily": "embeddings"}
        )

        provider = ProviderAdapterFactory(connection).resolve("nvidia-embeddings")
        with pytest.raises(UnsupportedProviderCapabilityError):
            provider_instance("nvidia-embeddings", connection=connection)

    assert isinstance(provider, NvidiaNimProvider)
    assert provider.provider_id == "nvidia-embeddings"
    assert provider.api_family == "embeddings"


def test_selfhosted_embedding_api_uses_documented_discovery_and_passive_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_UNSUPPORTED_API_KEY", "unsupported-family-test-key")
    client = _create_client(tmp_path, monkeypatch, raise_server_exceptions=False)
    headers = _auth_headers(client)
    with _nvidia_http_server() as (base_url, state):
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            initialize_platform_schema(connection)
            _upsert_nvidia_endpoint(connection, "nvidia-embeddings-api")
            store = ProviderAccountStore(connection)
            store.patch_provider_account(
                "nvidia-embeddings-api",
                {
                    "apiFamily": "embeddings",
                    "baseUrl": base_url,
                    "credentialRef": "env:NVIDIA_UNSUPPORTED_API_KEY",
                },
            )
            store.upsert_model(
                {
                    "providerId": "nvidia-embeddings-api",
                    "model": "nvidia/embed-model",
                    "displayName": "NVIDIA Embed Model",
                    "enabled": True,
                }
            )
            repository = RuntimeConfigRepository(connection)
            repository.set_runtime_setting("runtime.remote.enabled", True)
            repository.set_runtime_setting("runtime.nvidia.enabled", True)

        health = client.post(
            "/api/v1/model-gateway/providers/nvidia-embeddings-api/health-check",
            headers=headers,
        )
        discovered = client.post(
            "/api/v1/model-gateway/providers/nvidia-embeddings-api/discover-models",
            headers=headers,
        )
        synced = client.post(
            "/api/v1/provider-accounts/nvidia-embeddings-api/sync-models",
            headers=headers,
        )
        prompt = client.post(
            "/api/v1/model-gateway/providers/nvidia-embeddings-api/test-prompt",
            headers=headers,
            json={"model": "nvidia/embed-model"},
        )

    assert health.status_code == 200, health.text
    assert health.json()["health"] == {
        "providerId": "nvidia-embeddings-api",
        "status": "available",
        "healthStatus": "healthy",
        "message": "Provider /health/ready responded ready",
        "lastError": None,
    }
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["models"][0]["apiFamily"] == "embeddings"
    assert discovered.json()["models"][0]["supportsEmbeddings"] is True
    assert synced.status_code == 200, synced.text
    assert synced.json()["models"][0]["apiFamily"] == "embeddings"
    assert synced.json()["models"][0]["supportsEmbeddings"] is True
    assert prompt.status_code == 200, prompt.text
    assert prompt.json()["test"]["ok"] is False
    assert prompt.json()["test"]["error"] == "unsupported_api_family"
    assert state["modelCalls"] == 4
    assert state["chatCalls"] == 0


def test_provider_account_sync_applies_family_policy_and_real_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_SYNC_TEAM_A_API_KEY", "sync-test-key")
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    with _nvidia_http_server() as (base_url, state):
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            account = _upsert_nvidia_endpoint(connection, "nvidia-sync-team-a")
            ProviderAccountStore(connection).patch_provider_account(
                "nvidia-sync-team-a",
                {
                    "baseUrl": base_url,
                    "credentialRef": "env:NVIDIA_SYNC_TEAM_A_API_KEY",
                },
            )
            repository = RuntimeConfigRepository(connection)
            repository.set_runtime_setting("runtime.remote.enabled", True)
            repository.set_runtime_setting("runtime.nvidia.enabled", False)

        blocked = client.post(
            "/api/v1/provider-accounts/nvidia-sync-team-a/sync-models",
            headers=headers,
        )

        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["detail"] == "runtime.nvidia.enabled is false."
        assert state["modelCalls"] == 0

        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            RuntimeConfigRepository(connection).set_runtime_setting("runtime.nvidia.enabled", True)

        synced = client.post(
            "/api/v1/provider-accounts/nvidia-sync-team-a/sync-models",
            headers=headers,
        )

    assert account["providerFamily"] == "nvidia_nim"
    assert synced.status_code == 200, synced.text
    assert state["modelCalls"] == 1
    assert len(synced.json()["models"]) == 1
    assert synced.json()["models"][0]["providerId"] == "nvidia-sync-team-a"
    assert synced.json()["models"][0]["model"] == "nvidia/test-model"


def test_runtime_status_surfaces_endpoint_identity_and_family_without_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_STATUS_TEAM_A_API_KEY", "status-test-key")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-status-team-a")
        store = ProviderAccountStore(connection)
        store.patch_provider_account(
            "nvidia-status-team-a",
            {"credentialRef": "env:NVIDIA_STATUS_TEAM_A_API_KEY"},
        )
        store.upsert_model(
            {
                "providerId": "nvidia-status-team-a",
                "model": "nvidia/test-model",
                "displayName": "NVIDIA test model",
                "enabled": True,
            }
        )
        store.record_health_check(
            provider_id="nvidia-status-team-a",
            status="available",
            payload={"healthStatus": "healthy", "message": "Persisted explicit health."},
        )
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", True)
        repository.upsert_installation({"runtimeId": "nvidia-status-team-a", "kind": "api", "enabled": True})

        status = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "nvidia-status-team-a"
        )

    assert status["providerFamily"] == "nvidia_nim"
    assert status["apiFamily"] == "chat_completions"
    assert status["healthStatus"] == "healthy"
    # La fase 56 siembra las capabilities de la familia desde el catálogo canónico; la
    # identidad del contrato es que jamás incluyan generación/edición de imágenes.
    assert status["capabilities"] == ["chat", "json", "streaming", "tools", "vision"]
    assert "image_generation" not in status["capabilities"]
    assert "image_editing" not in status["capabilities"]
    assert status["executable"] is True
    assert status["reason"] == "Provider is configured, health checked, and executable."


def test_nonchat_nvidia_runtime_status_never_inherits_chat_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_EMBEDDINGS_API_KEY", "status-test-key")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-embeddings-status")
        store = ProviderAccountStore(connection)
        store.patch_provider_account(
            "nvidia-embeddings-status",
            {
                "apiFamily": "embeddings",
                "credentialRef": "env:NVIDIA_EMBEDDINGS_API_KEY",
            },
        )
        store.upsert_model(
            {
                "providerId": "nvidia-embeddings-status",
                "model": "nvidia/embed-model",
                "displayName": "NVIDIA embedding model",
                "enabled": True,
            }
        )
        store.record_health_check(
            provider_id="nvidia-embeddings-status",
            status="available",
            payload={"healthStatus": "healthy", "message": "Persisted explicit health."},
        )
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", True)
        repository.upsert_installation(
            {"runtimeId": "nvidia-embeddings-status", "kind": "api", "enabled": True}
        )

        status = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "nvidia-embeddings-status"
        )

    assert status["providerFamily"] == "nvidia_nim"
    assert status["apiFamily"] == "embeddings"
    assert "chat" not in status["capabilities"]
    assert status["executable"] is False
    assert status["canRunPrompt"] is False
    assert "embeddings" in status["reason"]


def test_named_nvidia_endpoint_is_ready_for_all_model_agents_by_explicit_family() -> None:
    status = {
        "id": "nvidia-team-a",
        "providerFamily": "nvidia_nim",
        "executable": True,
        "configured": True,
        "capabilities": ["chat"],
    }

    readiness_results = [
        developer_agent_readiness([status], preferred_runtime="nvidia-team-a"),
        product_owner_agent_readiness([status], preferred_runtime="nvidia-team-a"),
        architect_agent_readiness([status], preferred_runtime="nvidia-team-a"),
        security_agent_status([status]),
    ]

    for readiness in readiness_results:
        assert readiness["executable"] is True
        assert readiness["selectedRuntimeId"] == "nvidia-team-a"
        assert readiness["candidateRuntimeIds"] == ["nvidia-team-a"]


def test_named_remote_endpoint_without_explicit_chat_capability_is_not_model_ready() -> None:
    status = {
        "id": "nvidia-team-a",
        "providerFamily": "nvidia_nim",
        "executable": True,
        "configured": True,
        "capabilities": [],
    }

    for readiness in (
        developer_agent_readiness([status], preferred_runtime="nvidia-team-a"),
        product_owner_agent_readiness([status], preferred_runtime="nvidia-team-a"),
        architect_agent_readiness([status], preferred_runtime="nvidia-team-a"),
    ):
        assert readiness["executable"] is False
        assert readiness["candidateRuntimeIds"] == []

    security_readiness = security_agent_status([status])
    assert security_readiness["selectedRuntimeId"] is None
    assert security_readiness["candidateRuntimeIds"] == []


def test_named_endpoint_profiles_and_security_selection_keep_endpoint_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {
        "id": "nvidia-team-a",
        "providerFamily": "nvidia_nim",
        "kind": "api",
        "executable": True,
        "configured": True,
        "capabilities": ["chat"],
    }
    monkeypatch.setattr(
        "local_control_center.agents.security_agent.RuntimeStatusService.list_provider_statuses",
        lambda _service: [runtime],
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        developer_profile = DeveloperAgentRunner(connection, root=tmp_path)._create_profile(runtime)
        product_owner_profile = ProductOwnerAgentRunner(connection, root=tmp_path)._ensure_profile(runtime)
        architect_profile = ArchitectAgentRunner(connection, root=tmp_path)._ensure_profile(runtime)
        security_runner = SecurityAgentRunner(connection, root=tmp_path)
        selected_security_runtime = security_runner._model_runtime("nvidia-team-a")
        security_profile = security_runner._ensure_profile(runtime)

    for profile in (
        developer_profile,
        product_owner_profile,
        architect_profile,
        security_profile,
    ):
        assert profile["allowedProviders"] == ["nvidia-team-a"]
        assert profile["allowedRuntimes"] == ["nvidia-team-a"]
        assert profile["allowApi"] is True
    assert selected_security_runtime == runtime


def test_named_endpoint_request_builders_emit_family_tool_and_endpoint_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {
        "id": "nvidia-team-a",
        "providerFamily": "nvidia_nim",
        "kind": "api",
        "executable": True,
        "configured": True,
        "capabilities": ["chat"],
    }
    payload = {
        "projectId": "project-endpoint",
        "instruction": "Implement the requested change.",
        "qaCommands": [],
        "model": "nvidia/test-model",
        "diffArtifactId": "artifact-diff",
        "workflowContext": {},
        "relevantDocs": [],
        "testResults": [],
        "riskRegister": [],
        "evidenceRefs": [],
        "runModelAnalysis": True,
    }
    workspace = {"id": "workspace-endpoint", "path": str(tmp_path)}
    agent_run = {"id": "run-endpoint"}
    job = {"id": "job-endpoint"}
    profile = {"id": "recording-profile"}

    class RecordingBroker:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def evaluate_tool_call(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "toolCall": {
                    "id": "tool-call-recorded",
                    "status": "denied",
                    "payload": {"decisionReason": "recording boundary"},
                }
            }

    developer_broker = RecordingBroker()
    DeveloperAgentRunner.__new__(DeveloperAgentRunner)._execute_model_runtime(
        payload=payload,
        runtime=runtime,
        workspace=workspace,
        agent_run=agent_run,
        job=job,
        profile=profile,
        broker=developer_broker,
    )
    product_owner_broker = RecordingBroker()
    ProductOwnerAgentRunner.__new__(ProductOwnerAgentRunner)._execute_model_runtime(
        payload=payload,
        runtime=runtime,
        workspace=workspace,
        agent_run=agent_run,
        job=job,
        profile=profile,
        broker=product_owner_broker,
        messages=[{"role": "user", "content": "Plan the request."}],
    )
    architect_broker = RecordingBroker()
    ArchitectAgentRunner.__new__(ArchitectAgentRunner)._execute_model_runtime(
        payload=payload,
        runtime=runtime,
        workspace=workspace,
        agent_run=agent_run,
        job=job,
        profile=profile,
        broker=architect_broker,
        diff_text="diff --git a/a.py b/a.py",
    )
    security_broker = RecordingBroker()
    security_runner = SecurityAgentRunner.__new__(SecurityAgentRunner)
    security_runner.connection = object()
    security_runner.root = tmp_path
    security_runner._model_runtime = lambda _preferred_runtime: runtime  # type: ignore[method-assign]
    monkeypatch.setattr(
        "local_control_center.agents.security_agent.ToolBroker",
        lambda *_args, **_kwargs: security_broker,
    )
    security_runner._execute_optional_model_analysis(
        project_id="project-endpoint",
        workspace=workspace,
        agent_run=agent_run,
        job=job,
        profile=profile,
        payload=payload,
        findings_payload={"findings": []},
    )

    for broker in (
        developer_broker,
        product_owner_broker,
        architect_broker,
        security_broker,
    ):
        tool_call = broker.calls[0]["tool_call"]
        assert tool_call["tool"] == "nvidia_nim"
        assert tool_call["runtimeId"] == "nvidia-team-a"
        assert tool_call["input"]["providerId"] == "nvidia-team-a"


def test_disabled_nvidia_policy_blocks_before_remote_secret_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-policy-first")
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia-policy-first",
            {"credentialRef": "openbao:secret/providers/nvidia#api_key"},
        )
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", False)
        original_resolve = CredentialResolver.resolve
        fetched_refs: list[str] = []

        def guard_remote_fetch(
            resolver: CredentialResolver,
            credential_ref: str | None,
            *,
            fetch: bool = True,
        ):
            if fetch:
                fetched_refs.append(str(credential_ref or ""))
                raise AssertionError("policy-denied execution must not fetch a remote secret")
            return original_resolve(resolver, credential_ref, fetch=fetch)

        monkeypatch.setattr(CredentialResolver, "resolve", guard_remote_fetch)

        configuration = ModelGateway(connection)._provider_configuration(
            "nvidia-policy-first",
            runtime_type="api",
        )

    assert configuration["status"] == "blocked"
    assert configuration["reason"] == "runtime.nvidia.enabled is false."
    assert fetched_refs == []


def test_disabled_nvidia_policy_blocks_health_before_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-health-policy-first")
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", False)

        def fail_provider_construction(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("policy-denied health must not construct a provider transport")

        monkeypatch.setattr(
            "local_control_center.agents.model_gateway.provider_instance",
            fail_provider_construction,
        )

        configuration = ModelGateway(connection)._provider_configuration(
            "nvidia-health-policy-first",
            runtime_type="api",
            for_health=True,
        )

    assert configuration["status"] == "blocked"
    assert configuration["reason"] == "runtime.nvidia.enabled is false."


def test_tool_broker_canonical_id_cannot_bypass_persisted_family_binding(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia_nim",
            {"enabled": True, "providerFamily": "openrouter"},
        )

        decision, provider_family = ToolBroker(connection)._model_provider_binding(
            tool_name="nvidia_nim",
            runtime_id="nvidia_nim",
            provider_id="nvidia_nim",
        )

    assert decision is not None
    assert decision["decision"] == "deny"
    assert provider_family is None


def test_runtime_adapter_and_broker_bind_selected_nvidia_endpoint_by_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_EXEC_TEAM_A_API_KEY", "execute-test-key")
    with (
        _nvidia_http_server() as (base_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        _upsert_nvidia_endpoint(connection, "nvidia-exec-team-a")
        ProviderAccountStore(connection).patch_provider_account(
            "nvidia-exec-team-a",
            {
                "baseUrl": base_url,
                "credentialRef": "env:NVIDIA_EXEC_TEAM_A_API_KEY",
            },
        )
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", True)
        project = ProjectsRepository(connection).create_project(
            name="NVIDIA broker execution",
            path=tmp_path,
            template_id="other",
        )
        profile = AgentsRepository(connection).upsert_agent_profile(
            {
                "id": "developer_agent",
                "name": "NVIDIA Broker Agent",
                "role": "implementer",
                "runtimeMode": "api",
                "permissionProfile": "dev_safe",
                "allowedTools": ["nvidia_nim"],
                "allowedProviders": ["nvidia-exec-team-a"],
                "allowedRuntimes": ["nvidia-exec-team-a"],
            }
        )
        run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="nvidia-broker-task",
            input_payload={},
            output_payload={},
            status="running",
        )
        connection.execute(
            """
            INSERT INTO workspaces
                (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', 'filesystem', '{}',
                    '2026-07-13T00:00:00Z', '2026-07-13T00:00:00Z')
            """,
            (
                "workspace-nvidia",
                project["id"],
                "nvidia-broker-task",
                profile["id"],
                str(tmp_path),
            ),
        )

        result = ToolBroker(connection).evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={
                "tool": "nvidia_nim",
                "operation": "developer_agent_model_call",
                "runtimeId": "nvidia-exec-team-a",
                "workspaceId": "workspace-nvidia",
                "workspacePath": str(tmp_path),
                "path": str(tmp_path),
                "execute": True,
                "input": {
                    "providerId": "nvidia-exec-team-a",
                    "model": "nvidia/test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            },
        )

    assert result["decision"]["decision"] == "allow"
    assert result["toolCall"]["status"] == "completed"
    assert result["toolCall"]["payload"]["execution"] == "runtime_adapter:nvidia_nim"
    assert state["chatCalls"] == 1
    assert state["chatPayloads"][0]["model"] == "nvidia/test-model"


def test_tool_broker_named_openrouter_uses_only_selected_endpoint_and_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_TEAM_A_API_KEY", "endpoint-a-secret")
    monkeypatch.setenv("AIDO_OPENROUTER_API_KEY", "canonical-b-secret")
    monkeypatch.setenv("AIDO_OPENROUTER_MODEL", "openrouter/test-model")
    with (
        _nvidia_http_server() as (endpoint_a_url, endpoint_a_state),
        _nvidia_http_server() as (canonical_b_url, canonical_b_state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        monkeypatch.setenv("AIDO_OPENROUTER_BASE_URL", canonical_b_url)
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "openrouter-team-a",
                "displayName": "OpenRouter Team A",
                "providerType": "gateway",
                "apiFormat": "openai_compatible",
                "providerFamily": "openrouter",
                "baseUrl": endpoint_a_url,
                "credentialRef": "env:OPENROUTER_TEAM_A_API_KEY",
                "enabled": True,
            }
        )
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        provider = ProviderAdapterFactory(connection).resolve("openrouter-team-a")
        result = _execute_brokered_model_call(
            connection,
            tmp_path=tmp_path,
            tool="openrouter",
            provider_id="openrouter-team-a",
            model="openrouter/test-model",
        )

    assert result["decision"]["decision"] == "allow"
    assert result["toolCall"]["status"] == "completed"
    assert provider.provider_id == "openrouter-team-a"
    assert provider.base_url == endpoint_a_url
    assert provider.credential_ref == "env:OPENROUTER_TEAM_A_API_KEY"
    assert endpoint_a_state["chatCalls"] == 1
    assert endpoint_a_state["authorization"] == ["Bearer endpoint-a-secret"]
    assert canonical_b_state["chatCalls"] == 0
    assert canonical_b_state["authorization"] == []


def test_tool_broker_named_anthropic_uses_only_selected_endpoint_and_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_TEAM_A_API_KEY", "endpoint-a-anthropic-secret")
    monkeypatch.setenv("AIDO_ANTHROPIC_API_KEY", "canonical-b-anthropic-secret")
    monkeypatch.setenv("AIDO_ANTHROPIC_MODEL", "claude-test")
    with (
        _anthropic_http_server() as (endpoint_a_url, endpoint_a_state),
        _anthropic_http_server() as (canonical_b_url, canonical_b_state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        monkeypatch.setenv("AIDO_ANTHROPIC_BASE_URL", canonical_b_url)
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "anthropic-team-a",
                "displayName": "Anthropic Team A",
                "providerType": "api",
                "apiFormat": "anthropic",
                "providerFamily": "anthropic_api",
                "baseUrl": endpoint_a_url,
                "credentialRef": "env:ANTHROPIC_TEAM_A_API_KEY",
                "enabled": True,
            }
        )
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        provider = ProviderAdapterFactory(connection).resolve("anthropic-team-a")
        result = _execute_brokered_model_call(
            connection,
            tmp_path=tmp_path,
            tool="anthropic_api",
            provider_id="anthropic-team-a",
            model="claude-test",
        )

    assert result["decision"]["decision"] == "allow"
    assert result["toolCall"]["status"] == "completed"
    assert provider.provider_id == "anthropic-team-a"
    assert provider.base_url == endpoint_a_url
    assert provider.credential_ref == "env:ANTHROPIC_TEAM_A_API_KEY"
    assert endpoint_a_state["messageCalls"] == 1
    assert endpoint_a_state["xApiKey"] == ["endpoint-a-anthropic-secret"]
    assert canonical_b_state["messageCalls"] == 0
    assert canonical_b_state["xApiKey"] == []


def test_tool_broker_openrouter_redirect_fails_closed_without_forwarding_bearer_or_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_REDIRECT_API_KEY", "redirect-origin-secret")
    with (
        _nvidia_http_server() as (target_url, target_state),
        _nvidia_http_server(redirect_url=target_url) as (origin_url, origin_state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "openrouter-redirect",
                "displayName": "OpenRouter Redirect",
                "providerType": "gateway",
                "apiFormat": "openai_compatible",
                "providerFamily": "openrouter",
                "baseUrl": origin_url,
                "credentialRef": "env:OPENROUTER_REDIRECT_API_KEY",
                "enabled": True,
            }
        )
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        result = _execute_brokered_model_call(
            connection,
            tmp_path=tmp_path,
            tool="openrouter",
            provider_id="openrouter-redirect",
            model="openrouter/test-model",
        )

    execution = result["toolCall"]["payload"]["executionResult"]
    assert result["decision"]["decision"] == "allow"
    assert result["toolCall"]["status"] == "unavailable"
    assert execution["status"] == "unavailable"
    assert origin_state["requests"] == [{"method": "POST", "path": "/v1/chat/completions"}]
    assert origin_state["authorization"] == ["Bearer redirect-origin-secret"]
    assert target_state["requests"] == []
    assert target_state["authorization"] == []
    public_result = json.dumps(execution)
    assert "redirect-origin-secret" not in public_result
    assert target_url not in public_result
    assert "Location" not in public_result
    assert "HTTPError" not in public_result


@pytest.mark.parametrize("provider_family", [None, "openrouter"])
def test_policy_engine_rejects_nvidia_endpoint_without_matching_explicit_family(
    provider_family: str | None,
) -> None:
    decision = evaluate_action(
        {
            "projectId": "project-nvidia",
            "workspaceId": "workspace-nvidia",
            "workspacePath": "C:/workspace-nvidia",
            "agentId": "developer_agent",
            "agentRunId": "run-nvidia",
            "role": "implementer",
            "permissionProfile": "dev_safe",
            "tool": "nvidia_nim",
            "operation": "developer_agent_model_call",
            "runtimeId": "nvidia-exec-team-a",
            "providerId": "nvidia-exec-team-a",
            "providerFamily": provider_family,
        }
    )

    assert decision["decision"] == "deny"
    assert "developer_agent_model_runtime_denied" in decision["categories"]


def test_search_bonus_uses_explicit_nvidia_family_not_endpoint_id_prefix(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        router = ModelRouter(connection)
        request = RoutingRequest(requiresSearch=True)
        model = {
            "model": "nvidia/test-model",
            "contextWindow": 0,
            "supportsTools": False,
            "supportsReasoning": False,
            "freeTier": False,
        }
        common = {
            "providerId": "nvidia-team-a",
            "providerType": "api",
            "healthStatus": "healthy",
        }

        nvidia_score = router._score(
            request,
            {"maxCostPerTaskUsd": 1},
            {**common, "providerFamily": "nvidia_nim"},
            model,
            {},
            0.0,
            {"freeTier": False},
            None,
        )
        mismatched_score = router._score(
            request,
            {"maxCostPerTaskUsd": 1},
            {**common, "providerFamily": "openrouter"},
            model,
            {},
            0.0,
            {"freeTier": False},
            None,
        )

    assert nvidia_score["capabilityScore"] == 1.15
    assert mismatched_score["capabilityScore"] == 1.0
