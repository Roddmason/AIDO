from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.app import create_app
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import init_phase53_schema, initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(database))
    runtime = ControlPlaneFixture(cwd=tmp_path, db_path=database)
    runtime.init()
    return TestClient(create_app(runtime=runtime, static_dir=None))


def enable_nvidia_remote_policy(database: Path) -> None:
    with open_sqlite_connection(database) as connection:
        repository = RuntimeConfigRepository(connection)
        repository.set_runtime_setting("runtime.remote.enabled", True)
        repository.set_runtime_setting("runtime.nvidia.enabled", True)


def register_capability_model(
    database: Path,
    *,
    provider_id: str,
    model: str,
    api_family: str,
) -> None:
    """Persist one operator-confirmed model manifest for typed execution tests."""
    with open_sqlite_connection(database) as connection:
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": provider_id,
                "model": model,
                "apiFamily": api_family,
                "supportsEmbeddings": api_family == "embeddings",
                "supportsRerank": api_family == "rerank",
                "enabled": True,
                "source": "operator_manifest",
            }
        )


@contextmanager
def provider_server(
    responses: dict[str, dict[str, object]],
) -> Iterator[tuple[str, list[dict[str, object]]]]:
    calls: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b""
            calls.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(raw_body.decode("utf-8")) if raw_body else None,
                }
            )
            payload = dict(responses.get(self.path, {"error": "unexpected path"}))
            status_code = int(payload.pop("__status__", 200 if self.path in responses else 404))
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = _respond
        do_POST = _respond

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_phase53_adds_adapter_profile_and_backfills_auto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.shared import migrations

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        monkeypatch.setattr(migrations, "init_phase53_schema", lambda _connection: None)
        initialize_platform_schema(connection)

        phase52_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(provider_accounts)").fetchall()
        }
        phase52_migration = connection.execute(
            "SELECT version FROM schema_migrations WHERE version = 52"
        ).fetchone()
        missing_phase53 = connection.execute(
            "SELECT version FROM schema_migrations WHERE version = 53"
        ).fetchone()
        existing_account = connection.execute(
            "SELECT provider_id FROM provider_accounts WHERE provider_id = 'nvidia_nim'"
        ).fetchone()

        init_phase53_schema(connection)

        columns = {
            str(row["name"]): row
            for row in connection.execute("PRAGMA table_info(provider_accounts)").fetchall()
        }
        migration = connection.execute("SELECT version FROM schema_migrations WHERE version = 53").fetchone()
        store = ProviderAccountStore(connection)
        account = store.get_provider_account("nvidia_nim")
        store.patch_provider_account("nvidia_nim", {"adapterProfile": "nvidia_openai_chat"})
        init_phase53_schema(connection)
        reentrant_account = store.get_provider_account("nvidia_nim")

    assert columns["adapter_profile"]["notnull"] == 1
    assert str(columns["adapter_profile"]["dflt_value"]).strip("'") == "auto"
    assert "adapter_profile" not in phase52_columns
    assert phase52_migration is not None
    assert missing_phase53 is None
    assert existing_account is not None
    assert migration is not None
    assert account["adapterProfile"] == "auto"
    assert reentrant_account["adapterProfile"] == "nvidia_openai_chat"


def test_provider_account_round_trips_explicit_adapter_profile(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)

        created = store.upsert_provider_account(
            {
                "providerId": "nvidia-embedding-hosted",
                "displayName": "NVIDIA hosted embeddings",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "hosted_trial",
                "apiFamily": "embeddings",
                "adapterProfile": "nvidia_openai_embeddings",
                "termsMode": "evaluation",
                "pricingMode": "unknown",
                "baseUrl": "https://integrate.api.nvidia.com/v1",
                "credentialRef": "env:NVIDIA_EMBEDDING_TEST_KEY",
                "enabled": True,
            }
        )
        store.record_health_check(
            provider_id="nvidia-embedding-hosted",
            status="healthy",
            payload={"status": "available"},
        )
        patched = store.patch_provider_account("nvidia-embedding-hosted", {"adapterProfile": "auto"})

    assert created["adapterProfile"] == "nvidia_openai_embeddings"
    assert patched["adapterProfile"] == "auto"
    assert patched["healthStatus"] == "unknown"
    assert patched["lastHealthCheckAt"] is None


def test_runtime_checkable_capability_ports_match_only_implemented_methods() -> None:
    from local_control_center.agents.providers.capabilities import (
        ChatCompletionProvider,
        EmbeddingProvider,
        ImageEditingProvider,
        ImageGenerationProvider,
        RerankProvider,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    provider = NvidiaNimProvider(
        provider_id="nvidia-capability-probe",
        base_url="http://127.0.0.1:9999/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="embeddings",
    )

    assert isinstance(provider, ChatCompletionProvider)
    assert isinstance(provider, EmbeddingProvider)
    assert isinstance(provider, RerankProvider)
    assert not isinstance(provider, ImageGenerationProvider)
    assert not isinstance(provider, ImageEditingProvider)


def test_hosted_embedding_uses_versioned_root_body_and_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        EmbeddingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            json_body={
                "object": "list",
                "model": "nvidia/nv-embedqa-e5-v5",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.25, -0.5]},
                    {"object": "embedding", "index": 1, "embedding": [0.5, -0.25]},
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    monkeypatch.setenv("NVIDIA_EMBEDDING_TEST_KEY", "hosted-embedding-secret")
    provider = NvidiaNimProvider(
        provider_id="nvidia-embedding-hosted",
        base_url="https://integrate.api.nvidia.com/v1",
        credential_ref="env:NVIDIA_EMBEDDING_TEST_KEY",
        deployment_mode="hosted_trial",
        api_family="embeddings",
        adapter_profile="nvidia_openai_embeddings",
        transport=transport,
    )

    result = provider.embed(
        EmbeddingRequest(
            model="nvidia/nv-embedqa-e5-v5",
            input=["first", "second"],
            inputType="query",
            encodingFormat="float",
            truncate="NONE",
        )
    )

    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].url == "https://integrate.api.nvidia.com/v1/embeddings"
    assert calls[0].headers["Authorization"] == "Bearer hosted-embedding-secret"
    assert calls[0].json_body == {
        "model": "nvidia/nv-embedqa-e5-v5",
        "input": ["first", "second"],
        "input_type": "query",
        "encoding_format": "float",
        "truncate": "NONE",
    }
    assert result.provider_id == "nvidia-embedding-hosted"
    assert result.data[0].embedding == [0.25, -0.5]
    assert result.usage is not None
    assert result.usage.total_tokens == 2


def test_self_hosted_embedding_uses_v1_root_without_authorization() -> None:
    from local_control_center.agents.providers.capabilities import (
        EmbeddingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "nvidia/local-embed",
                "data": [{"index": 0, "embedding": [0.75]}],
            },
        )

    provider = NvidiaNimProvider(
        provider_id="nvidia-embedding-local",
        base_url="http://127.0.0.1:9100/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="embeddings",
        adapter_profile="nvidia_openai_embeddings",
        transport=transport,
    )

    result = provider.embed(EmbeddingRequest(model="nvidia/local-embed", input="hello"))

    assert calls[0].url == "http://127.0.0.1:9100/v1/embeddings"
    assert "Authorization" not in calls[0].headers
    assert result.usage is None


def test_hosted_rerank_uses_model_root_and_reranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
        RerankRequest,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "nvidia/llama-nemotron-rerank-1b-v2",
                "rankings": [
                    {"index": 1, "logit": 0.91},
                    {"index": 0, "logit": 0.25},
                ],
            },
        )

    monkeypatch.setenv("NVIDIA_RERANK_TEST_KEY", "hosted-rerank-secret")
    provider = NvidiaNimProvider(
        provider_id="nvidia-rerank-hosted",
        base_url=("https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-1b-v2"),
        credential_ref="env:NVIDIA_RERANK_TEST_KEY",
        deployment_mode="hosted_trial",
        api_family="rerank",
        adapter_profile="nvidia_hosted_rerank",
        transport=transport,
    )

    result = provider.rerank(
        RerankRequest(
            model="nvidia/llama-nemotron-rerank-1b-v2",
            query="best passage",
            passages=[{"text": "first"}, {"text": "second"}],
            truncate="END",
        )
    )

    assert calls[0].url.endswith("/v1/retrieval/nvidia/llama-nemotron-rerank-1b-v2/reranking")
    assert calls[0].headers["Authorization"] == "Bearer hosted-rerank-secret"
    assert calls[0].json_body == {
        "model": "nvidia/llama-nemotron-rerank-1b-v2",
        "query": {"text": "best passage"},
        "passages": [{"text": "first"}, {"text": "second"}],
        "truncate": "END",
    }
    assert result.rankings[0].index == 1
    assert result.rankings[0].relevance_score == pytest.approx(0.91)


def test_self_hosted_rerank_uses_v1_ranking_without_authorization() -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
        RerankRequest,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"rankings": [{"index": 0, "logit": 0.5}]},
        )

    provider = NvidiaNimProvider(
        provider_id="nvidia-rerank-local",
        base_url="http://127.0.0.1:9200/v1",
        credential_ref="",
        deployment_mode="self_hosted_enterprise",
        api_family="rerank",
        adapter_profile="nvidia_nim_ranking",
        transport=transport,
    )

    provider.rerank(
        RerankRequest(
            model="nvidia/local-rerank",
            query="query",
            passages=[{"text": "passage"}],
        )
    )

    assert calls[0].url == "http://127.0.0.1:9200/v1/ranking"
    assert "Authorization" not in calls[0].headers


@pytest.mark.parametrize(
    ("api_family", "adapter_profile"),
    [
        ("embeddings", "nvidia_openai_embeddings"),
        ("rerank", "nvidia_nim_ranking"),
    ],
)
def test_self_hosted_retrieval_lists_models_and_uses_ready_health(
    api_family: str,
    adapter_profile: str,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        if request.url.endswith("/models"):
            return ProviderHttpResponse(
                statusCode=200,
                jsonBody={"object": "list", "data": [{"id": f"nvidia/local-{api_family}"}]},
            )
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"object": "health.response", "message": "ready", "ready": True},
        )

    provider = NvidiaNimProvider(
        provider_id=f"nvidia-local-{api_family}",
        base_url="http://127.0.0.1:9201/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family=api_family,
        adapter_profile=adapter_profile,
        transport=transport,
    )

    models = provider.list_models()
    health = provider.health_check()

    assert [item.model for item in models] == [f"nvidia/local-{api_family}"]
    assert health.health_status == "healthy"
    assert [call.url for call in calls] == [
        "http://127.0.0.1:9201/v1/models",
        "http://127.0.0.1:9201/v1/health/ready",
    ]


@pytest.mark.parametrize(
    ("deployment_mode", "credential_ref", "expected_authorization"),
    [
        ("hosted_trial", "env:NVIDIA_CHAT_TEST_KEY", "Bearer hosted-chat-secret"),
        ("self_hosted_development", "", None),
    ],
)
def test_chat_uses_injected_transport_with_deployment_aware_auth(
    deployment_mode: str,
    credential_ref: str,
    expected_authorization: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.base import ModelRequest
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "nvidia/chat-model",
                "choices": [{"message": {"content": "ok"}}],
            },
        )

    monkeypatch.setenv("NVIDIA_CHAT_TEST_KEY", "hosted-chat-secret")
    provider = NvidiaNimProvider(
        provider_id=f"nvidia-chat-{deployment_mode}",
        base_url="http://127.0.0.1:9300/v1",
        credential_ref=credential_ref,
        deployment_mode=deployment_mode,
        api_family="chat_completions",
        adapter_profile="auto",
        transport=transport,
    )

    response = provider.chat_completion(
        ModelRequest(model="nvidia/chat-model", messages=[{"role": "user", "content": "hi"}])
    )

    assert calls[0].url == "http://127.0.0.1:9300/v1/chat/completions"
    assert calls[0].headers.get("Authorization") == expected_authorization
    assert response.content == "ok"


def test_hosted_embedding_without_credential_fails_before_transport() -> None:
    from local_control_center.agents.providers.capabilities import (
        EmbeddingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimProvider,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        raise AssertionError("transport must not run")

    provider = NvidiaNimProvider(
        provider_id="nvidia-embedding-missing-key",
        base_url="https://integrate.api.nvidia.com/v1",
        credential_ref="",
        deployment_mode="hosted_trial",
        api_family="embeddings",
        adapter_profile="nvidia_openai_embeddings",
        transport=transport,
    )

    with pytest.raises(NvidiaNimCapabilityError, match="credential_missing"):
        provider.embed(EmbeddingRequest(model="nvidia/embed", input="hello"))

    assert calls == []


def test_model_gateway_embedding_endpoint_executes_typed_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_EMBED_KEY", "api-embedding-secret")
    with provider_server(
        {
            "/v1/embeddings": {
                "model": "nvidia/api-embed",
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            }
        }
    ) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-api-embed",
                "displayName": "NVIDIA API Embed",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "embeddings",
                "adapterProfile": "nvidia_openai_embeddings",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "env:NVIDIA_API_EMBED_KEY",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))
        register_capability_model(
            Path(os.environ["LOCAL_CONTROL_CENTER_DB"]),
            provider_id="nvidia-api-embed",
            model="nvidia/api-embed",
            api_family="embeddings",
        )

        response = client.post(
            "/api/v1/model-gateway/providers/nvidia-api-embed/embeddings",
            headers=headers,
            json={"model": "nvidia/api-embed", "input": ["hello"]},
        )

    assert created.status_code == 201
    assert created.json()["provider"]["adapterProfile"] == "nvidia_openai_embeddings"
    assert response.status_code == 200, response.text
    assert response.json()["embedding"]["providerId"] == "nvidia-api-embed"
    assert calls == [
        {
            "method": "POST",
            "path": "/v1/embeddings",
            "authorization": "Bearer api-embedding-secret",
            "body": {"model": "nvidia/api-embed", "input": ["hello"]},
        }
    ]
    assert "api-embedding-secret" not in response.text


def test_model_gateway_rejects_malformed_embedding_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_EMBED_KEY", "api-embedding-secret")
    with provider_server({}) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-api-embed-invalid",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "embeddings",
                "adapterProfile": "nvidia_openai_embeddings",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "env:NVIDIA_API_EMBED_KEY",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

        response = client.post(
            "/api/v1/model-gateway/providers/nvidia-api-embed-invalid/embeddings",
            headers=headers,
            json={"model": "nvidia/api-embed", "input": []},
        )

    assert created.status_code == 201
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    "invalid_option",
    [
        {"inputType": "invalid"},
        {"encodingFormat": "base64"},
        {"truncate": "SIDEWAYS"},
    ],
)
def test_model_gateway_rejects_unsupported_embedding_options_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_option: dict[str, str],
) -> None:
    with provider_server({}) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-embedding-option-invalid",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "embeddings",
                "adapterProfile": "nvidia_openai_embeddings",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "",
                "enabled": True,
            },
        )

        response = client.post(
            "/api/v1/model-gateway/providers/nvidia-embedding-option-invalid/embeddings",
            headers=headers,
            json={
                "model": "nvidia/embed",
                "input": ["hello"],
                **invalid_option,
            },
        )

    assert created.status_code == 201
    assert response.status_code == 422
    assert calls == []


def test_model_gateway_requires_matching_manifest_capability_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with provider_server({"/v1/embeddings": {"data": [{"index": 0, "embedding": [0.1]}]}}) as (
        base_url,
        calls,
    ):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-embedding-capability-disabled",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "embeddings",
                "adapterProfile": "nvidia_openai_embeddings",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))
        with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
            ProviderAccountStore(connection).upsert_model(
                {
                    "providerId": "nvidia-embedding-capability-disabled",
                    "model": "nvidia/embed",
                    "apiFamily": "embeddings",
                    "supportsEmbeddings": False,
                    "enabled": True,
                    "source": "operator_manifest",
                }
            )

        response = client.post(
            "/api/v1/model-gateway/providers/nvidia-embedding-capability-disabled/embeddings",
            headers=headers,
            json={"model": "nvidia/embed", "input": ["hello"]},
        )

    assert created.status_code == 201
    assert response.status_code == 409
    assert response.json()["detail"] == "model_capability_mismatch"
    assert calls == []


def test_model_gateway_self_hosted_rerank_executes_without_bearer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with provider_server({"/v1/ranking": {"rankings": [{"index": 0, "logit": 0.8}]}}) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-local-rerank-api",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "rerank",
                "adapterProfile": "nvidia_nim_ranking",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

        without_manifest = client.post(
            "/api/v1/model-gateway/providers/nvidia-local-rerank-api/rerank",
            headers=headers,
            json={
                "model": "nvidia/local-rerank",
                "query": "query",
                "passages": [{"text": "passage"}],
            },
        )
        register_capability_model(
            Path(os.environ["LOCAL_CONTROL_CENTER_DB"]),
            provider_id="nvidia-local-rerank-api",
            model="nvidia/local-rerank",
            api_family="rerank",
        )
        response = client.post(
            "/api/v1/model-gateway/providers/nvidia-local-rerank-api/rerank",
            headers=headers,
            json={
                "model": "nvidia/local-rerank",
                "query": "query",
                "passages": [{"text": "passage"}],
            },
        )

    assert created.status_code == 201, created.text
    assert without_manifest.status_code == 409
    assert without_manifest.json()["detail"] == "model_manifest_required"
    assert response.status_code == 200, response.text
    assert response.json()["rerank"]["rankings"][0]["relevanceScore"] == pytest.approx(0.8)
    assert calls[0]["path"] == "/v1/ranking"
    assert calls[0]["authorization"] is None


def test_rerank_discovery_requires_explicit_manifest_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with provider_server({}) as (_base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-rerank-manifest",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "hosted_trial",
                "apiFamily": "rerank",
                "adapterProfile": "nvidia_hosted_rerank",
                "termsMode": "evaluation",
                "pricingMode": "unknown",
                "baseUrl": ("https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-1b-v2"),
                "credentialRef": "env:NVIDIA_RERANK_MANIFEST_KEY",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

        discovery = client.post(
            "/api/v1/model-gateway/providers/nvidia-rerank-manifest/discover-models",
            headers=headers,
        )
        sync = client.post(
            "/api/v1/provider-accounts/nvidia-rerank-manifest/sync-models",
            headers=headers,
        )

    assert created.status_code == 201
    assert discovery.status_code == 409
    assert discovery.json()["detail"] == "explicit_model_manifest_required"
    assert sync.status_code == 409
    assert sync.json()["detail"] == "explicit_model_manifest_required"
    assert calls == []


def test_self_hosted_rerank_passive_health_uses_documented_ready_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with provider_server(
        {
            "/v1/health/ready": {
                "object": "health.response",
                "message": "ready",
                "ready": True,
            },
            "/v1/models": {
                "object": "list",
                "data": [{"id": "nvidia/local-rerank"}],
            },
        }
    ) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-rerank-passive-health",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "rerank",
                "adapterProfile": "nvidia_nim_ranking",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

        health = client.post(
            "/api/v1/model-gateway/providers/nvidia-rerank-passive-health/health-check",
            headers=headers,
        )

    assert created.status_code == 201
    assert health.status_code == 200, health.text
    assert health.json()["health"]["status"] == "available"
    assert health.json()["health"]["healthStatus"] == "healthy"
    assert [call["path"] for call in calls] == ["/v1/health/ready", "/v1/models"]


def test_visual_auto_profile_fails_closed_before_network(tmp_path: Path) -> None:
    from local_control_center.agents.providers.factory import (
        AdapterProfileRequiredError,
        ProviderAdapterFactory,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "nvidia-image-auto",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "image_editing",
                "adapterProfile": "auto",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": "http://127.0.0.1:9999/v1",
                "credentialRef": "",
                "enabled": True,
            }
        )

        with pytest.raises(AdapterProfileRequiredError, match="adapter_profile_required"):
            ProviderAdapterFactory(connection).resolve_for_execution("nvidia-image-auto")


def test_catalog_exposes_adapter_profile_and_allows_self_hosted_without_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    catalog = client.get("/api/v1/providers/catalog")
    nvidia = next(item for item in catalog.json()["providers"] if item["id"] == "nvidia_nim")
    created = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": "nvidia-selfhost-embed-catalog",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "accepted",
            "pricingMode": "free",
            "baseUrl": "http://127.0.0.1:9400/v1",
            "enabled": False,
        },
    )
    hosted_rerank_without_root = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": "nvidia-hosted-rerank-no-root",
            "deploymentMode": "hosted_trial",
            "apiFamily": "rerank",
            "adapterProfile": "nvidia_hosted_rerank",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "credentialRef": "env:NVIDIA_UNUSED_RERANK_KEY",
        },
    )

    assert catalog.status_code == 200
    assert nvidia["adapterProfile"] == "auto"
    assert created.status_code == 201, created.text
    assert created.json()["provider"]["credentialRef"] == ""
    assert created.json()["provider"]["adapterProfile"] == "nvidia_openai_embeddings"
    assert hosted_rerank_without_root.status_code == 422
    assert "baseUrl is required" in hosted_rerank_without_root.json()["detail"]


def test_legacy_patch_omitting_adapter_profile_preserves_explicit_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-profile-preserved",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "accepted",
            "pricingMode": "free",
            "baseUrl": "http://127.0.0.1:9500/v1",
            "enabled": False,
        },
    )

    patched = client.patch(
        "/api/v1/model-gateway/providers/nvidia-profile-preserved",
        headers=headers,
        json={"displayName": "Renamed only"},
    )
    malformed = client.patch(
        "/api/v1/model-gateway/providers/nvidia-profile-preserved",
        headers=headers,
        json={"adapterProfile": "bad profile"},
    )

    assert created.status_code == 201
    assert patched.status_code == 200
    assert patched.json()["provider"]["adapterProfile"] == "nvidia_openai_embeddings"
    assert malformed.status_code == 422


@pytest.mark.parametrize(
    ("credential_ref", "adapter_profile", "expected_detail"),
    [
        ("", "nvidia_openai_embeddings", "credential_missing"),
        ("env:NVIDIA_PROFILE_TEST_KEY", "unknown_well_formed_profile", "unsupported_adapter_profile"),
    ],
)
def test_embedding_conflicts_fail_before_network_with_stable_409(
    credential_ref: str,
    adapter_profile: str,
    expected_detail: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_PROFILE_TEST_KEY", "profile-test-secret")
    with provider_server({}) as (_base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": f"nvidia-conflict-{expected_detail}",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "hosted_trial",
                "apiFamily": "embeddings",
                "adapterProfile": adapter_profile,
                "termsMode": "evaluation",
                "pricingMode": "unknown",
                "baseUrl": "https://integrate.api.nvidia.com/v1",
                "credentialRef": credential_ref,
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))
        response = client.post(
            f"/api/v1/model-gateway/providers/nvidia-conflict-{expected_detail}/embeddings",
            headers=headers,
            json={"model": "nvidia/embed", "input": "hello"},
        )

    assert created.status_code == 201
    assert response.status_code == 409
    assert response.json()["detail"] == expected_detail
    assert calls == []


def test_hosted_rerank_missing_model_root_fails_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"rankings": [{"index": 0, "logit": 0.9}]},
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim._stdlib_transport",
        transport,
    )
    monkeypatch.setenv("NVIDIA_MISSING_ROOT_KEY", "configured-but-unused")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-hosted-rerank-missing-root",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "rerank",
            "adapterProfile": "nvidia_hosted_rerank",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "credentialRef": "env:NVIDIA_MISSING_ROOT_KEY",
            "enabled": True,
        },
    )
    enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

    response = client.post(
        "/api/v1/model-gateway/providers/nvidia-hosted-rerank-missing-root/rerank",
        headers=headers,
        json={
            "model": "nvidia/rerank",
            "query": "query",
            "passages": [{"text": "passage"}],
        },
    )

    assert created.status_code == 201
    assert response.status_code == 409
    assert response.json()["detail"] == "provider_base_url_required"
    assert calls == []


def test_unknown_chat_profile_fails_before_secret_or_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.base import ModelRequest
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimProvider,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={"choices": []})

    monkeypatch.delenv("NVIDIA_UNKNOWN_CHAT_KEY", raising=False)
    provider = NvidiaNimProvider(
        provider_id="nvidia-chat-unknown-profile",
        base_url="https://integrate.api.nvidia.com/v1",
        credential_ref="env:NVIDIA_UNKNOWN_CHAT_KEY",
        deployment_mode="hosted_trial",
        api_family="chat_completions",
        adapter_profile="unknown_chat_profile",
        transport=transport,
    )

    with pytest.raises(NvidiaNimCapabilityError, match="unsupported_adapter_profile"):
        provider.chat_completion(
            ModelRequest(model="nvidia/model", messages=[{"role": "user", "content": "hello"}])
        )

    assert calls == []


def test_unknown_profile_health_is_controlled_and_does_not_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with provider_server({}) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-unknown-profile-health",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "embeddings",
                "adapterProfile": "unknown_well_formed_profile",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

        health = client.post(
            "/api/v1/model-gateway/providers/nvidia-unknown-profile-health/health-check",
            headers=headers,
        )

    assert created.status_code == 201
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "blocked"
    assert health.json()["health"]["healthStatus"] == "unsupported"
    assert health.json()["health"]["message"] == "unsupported_adapter_profile"
    assert calls == []


def test_nvidia_provider_type_cannot_bypass_runtime_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "nvidia/embed",
                "data": [{"index": 0, "embedding": [0.5]}],
            },
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim._stdlib_transport",
        transport,
    )
    monkeypatch.setenv("NVIDIA_POLICY_SPOOF_KEY", "must-never-be-sent")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    rejected = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-policy-type-rejected",
            "providerType": "local",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "baseUrl": "https://integrate.api.nvidia.com/v1",
            "credentialRef": "env:NVIDIA_POLICY_SPOOF_KEY",
            "enabled": True,
        },
    )
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-policy-type-corrupted",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "baseUrl": "https://integrate.api.nvidia.com/v1",
            "credentialRef": "env:NVIDIA_POLICY_SPOOF_KEY",
            "enabled": True,
        },
    )
    with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
        connection.execute(
            "UPDATE provider_accounts SET provider_type = 'local' WHERE provider_id = ?",
            ("nvidia-policy-type-corrupted",),
        )
        # Los transportes vienen habilitados por defecto. Para verificar que el providerType
        # spoofeado no evade la policy se apaga el transporte remoto: la familia nvidia_nim se fuerza
        # a kind 'api', así que debe seguir denegada aunque el registro figure como 'local'.
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", False)

    blocked = client.post(
        "/api/v1/model-gateway/providers/nvidia-policy-type-corrupted/embeddings",
        headers=headers,
        json={"model": "nvidia/embed", "input": "hello"},
    )

    assert rejected.status_code == 400
    assert "providerType" in rejected.json()["detail"]
    assert created.status_code == 201
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "runtime_policy_denied"
    assert calls == []


def test_self_hosted_explicit_missing_credential_fails_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=200, jsonBody={"data": []})

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim._stdlib_transport",
        transport,
    )
    monkeypatch.delenv("NVIDIA_SELFHOST_PROXY_KEY", raising=False)
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-selfhost-missing-explicit-ref",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "accepted",
            "pricingMode": "free",
            "baseUrl": "http://127.0.0.1:9800/v1",
            "credentialRef": "env:NVIDIA_SELFHOST_PROXY_KEY",
            "enabled": True,
        },
    )
    enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

    response = client.post(
        "/api/v1/model-gateway/providers/nvidia-selfhost-missing-explicit-ref/embeddings",
        headers=headers,
        json={"model": "nvidia/embed", "input": "hello"},
    )

    assert created.status_code == 201
    assert response.status_code == 409
    assert response.json()["detail"] == "credential_missing"
    assert calls == []


def test_hosted_discovery_provider_failure_is_not_a_successful_empty_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        return ProviderHttpResponse(statusCode=500, jsonBody={"error": "raw provider body"})

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim._stdlib_transport",
        transport,
    )
    monkeypatch.setenv("NVIDIA_DISCOVERY_FAILURE_KEY", "discovery-secret")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-discovery-failure",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "baseUrl": "https://integrate.api.nvidia.com/v1",
            "credentialRef": "env:NVIDIA_DISCOVERY_FAILURE_KEY",
            "enabled": True,
        },
    )
    enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

    response = client.post(
        "/api/v1/model-gateway/providers/nvidia-discovery-failure/discover-models",
        headers=headers,
    )

    assert created.status_code == 201
    assert response.status_code == 502
    assert response.json()["detail"] == "provider_request_failed"
    assert [(call.method, call.url) for call in calls] == [
        ("GET", "https://integrate.api.nvidia.com/v1/models")
    ]


def test_hosted_embedding_discovery_persists_family_manifest_for_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
    )

    calls: list[ProviderHttpRequest] = []

    def transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
        calls.append(request)
        if request.method == "GET":
            return ProviderHttpResponse(
                statusCode=200,
                jsonBody={"data": [{"id": "nvidia/discovered-embed"}]},
            )
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "nvidia/discovered-embed",
                "data": [{"index": 0, "embedding": [0.2]}],
            },
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim._stdlib_transport",
        transport,
    )
    monkeypatch.setenv("NVIDIA_DISCOVERY_SUCCESS_KEY", "discovery-success-secret")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-discovered-embedding",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "baseUrl": "https://integrate.api.nvidia.com/v1",
            "credentialRef": "env:NVIDIA_DISCOVERY_SUCCESS_KEY",
            "enabled": True,
        },
    )
    enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))

    discovered = client.post(
        "/api/v1/model-gateway/providers/nvidia-discovered-embedding/discover-models",
        headers=headers,
    )
    executed = client.post(
        "/api/v1/model-gateway/providers/nvidia-discovered-embedding/embeddings",
        headers=headers,
        json={"model": "nvidia/discovered-embed", "input": "hello"},
    )

    assert created.status_code == 201
    assert discovered.status_code == 200
    assert discovered.json()["models"][0]["apiFamily"] == "embeddings"
    assert executed.status_code == 200, executed.text
    assert [call.method for call in calls] == ["GET", "POST"]


@pytest.mark.parametrize(
    "base_url",
    [
        "relative/v1",
        "ftp://provider.example/v1",
        "https://provider.example/v1?token=must-not-persist",
        "https://provider.example/v1#fragment",
        "http://provider.example/v1",
        "https://provider.example/v1/embeddings",
    ],
)
def test_nvidia_hosted_rejects_unsafe_base_urls(
    base_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/model-gateway/providers",
        headers=auth_headers(client),
        json={
            "providerId": "nvidia-unsafe-root",
            "providerType": "api",
            "apiFormat": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "embeddings",
            "adapterProfile": "nvidia_openai_embeddings",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "baseUrl": base_url,
            "credentialRef": "env:NVIDIA_UNUSED_UNSAFE_ROOT_KEY",
            "enabled": False,
        },
    )

    assert response.status_code == 400
    assert "baseUrl" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("embeddings", {"model": " ", "input": "hello"}),
        ("embeddings", {"model": "nvidia/embed", "input": "   "}),
        (
            "rerank",
            {"model": "nvidia/rerank", "query": " ", "passages": [{"text": "passage"}]},
        ),
        (
            "rerank",
            {"model": "nvidia/rerank", "query": "query", "passages": [{"text": "  "}]},
        ),
    ],
)
def test_semantically_blank_capability_input_returns_422_without_transport(
    path: str,
    payload: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = "rerank" if path == "rerank" else "embeddings"
    profile = "nvidia_nim_ranking" if family == "rerank" else "nvidia_openai_embeddings"
    with provider_server({}) as (base_url, calls):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": f"nvidia-blank-{family}",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": family,
                "adapterProfile": profile,
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))
        response = client.post(
            f"/api/v1/model-gateway/providers/nvidia-blank-{family}/{path}",
            headers=headers,
            json=payload,
        )

    assert created.status_code == 201
    assert response.status_code == 422
    assert calls == []


def test_provider_response_numeric_strings_are_rejected() -> None:
    from local_control_center.agents.providers.capabilities import (
        EmbeddingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimProvider,
    )

    def transport(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "nvidia/embed",
                "data": [{"index": "0", "embedding": ["1.25"]}],
                "usage": {"prompt_tokens": "1", "total_tokens": "1"},
            },
        )

    provider = NvidiaNimProvider(
        provider_id="nvidia-strict-response",
        base_url="http://127.0.0.1:9801/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="embeddings",
        adapter_profile="nvidia_openai_embeddings",
        transport=transport,
    )

    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        provider.embed(EmbeddingRequest(model="nvidia/embed", input="hello"))


def test_incomplete_embedding_and_rerank_responses_are_rejected() -> None:
    from local_control_center.agents.providers.capabilities import (
        EmbeddingRequest,
        ProviderHttpRequest,
        ProviderHttpResponse,
        RerankRequest,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimProvider,
    )

    def incomplete_embedding(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"data": [{"index": 0, "embedding": [0.1]}]},
        )

    embedding_provider = NvidiaNimProvider(
        provider_id="nvidia-incomplete-embedding",
        base_url="http://127.0.0.1:9802/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="embeddings",
        adapter_profile="nvidia_openai_embeddings",
        transport=incomplete_embedding,
    )
    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        embedding_provider.embed(EmbeddingRequest(model="nvidia/embed", input=["first", "second"]))

    def incomplete_rerank(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"rankings": [{"index": 0, "logit": 0.8}]},
        )

    rerank_provider = NvidiaNimProvider(
        provider_id="nvidia-incomplete-rerank",
        base_url="http://127.0.0.1:9803/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="rerank",
        adapter_profile="nvidia_nim_ranking",
        transport=incomplete_rerank,
    )
    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        rerank_provider.rerank(
            RerankRequest(
                model="nvidia/rerank",
                query="query",
                passages=[{"text": "first"}, {"text": "second"}],
            )
        )


def test_empty_chat_content_and_invalid_rerank_indices_are_not_success() -> None:
    from local_control_center.agents.providers.base import ModelRequest
    from local_control_center.agents.providers.capabilities import (
        ProviderHttpRequest,
        ProviderHttpResponse,
        RerankPassage,
        RerankRequest,
    )
    from local_control_center.agents.providers.nvidia_nim import (
        NvidiaNimCapabilityError,
        NvidiaNimProvider,
    )

    def empty_chat(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"model": "nvidia/chat", "choices": [{"message": {}}]},
        )

    chat_provider = NvidiaNimProvider(
        provider_id="nvidia-empty-chat",
        base_url="http://127.0.0.1:9802/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="chat_completions",
        adapter_profile="auto",
        transport=empty_chat,
    )
    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        chat_provider.chat_completion(
            ModelRequest(model="nvidia/chat", messages=[{"role": "user", "content": "hello"}])
        )

    def invalid_ranking(_request: ProviderHttpRequest) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"rankings": [{"index": 3, "logit": 0.8}]},
        )

    rerank_provider = NvidiaNimProvider(
        provider_id="nvidia-invalid-ranking",
        base_url="http://127.0.0.1:9803/v1",
        credential_ref="",
        deployment_mode="self_hosted_development",
        api_family="rerank",
        adapter_profile="nvidia_nim_ranking",
        transport=invalid_ranking,
    )
    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_invalid"):
        rerank_provider.rerank(
            RerankRequest(
                model="nvidia/rerank",
                query="query",
                passages=[RerankPassage(text="passage")],
            )
        )


def test_productive_transport_bounds_provider_json_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.agents.providers.capabilities import ProviderHttpRequest
    from local_control_center.agents.providers.nvidia_nim import (
        MAX_PROVIDER_JSON_BYTES,
        NvidiaNimCapabilityError,
        _stdlib_transport,
    )

    class OversizedResponse:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> OversizedResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            assert size == MAX_PROVIDER_JSON_BYTES + 1
            return b"x" * size

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim.urlopen_fail_closed",
        lambda *_args, **_kwargs: OversizedResponse(),
    )

    with pytest.raises(NvidiaNimCapabilityError, match="provider_response_too_large"):
        _stdlib_transport(
            ProviderHttpRequest(
                method="POST",
                url="https://provider.invalid/v1/embeddings",
                jsonBody={"model": "nvidia/embed", "input": "hello"},
            )
        )


def test_provider_error_body_and_bearer_are_not_returned_or_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "provider-secret-never-return"
    monkeypatch.setenv("NVIDIA_REDACTION_TEST_KEY", secret)
    with provider_server({"/v1/embeddings": {"__status__": 500, "error": f"raw body {secret}"}}) as (
        base_url,
        calls,
    ):
        client = create_client(tmp_path, monkeypatch)
        headers = auth_headers(client)
        created = client.post(
            "/api/v1/model-gateway/providers",
            headers=headers,
            json={
                "providerId": "nvidia-redacted-error",
                "providerType": "api",
                "apiFormat": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "deploymentMode": "self_hosted_development",
                "apiFamily": "embeddings",
                "adapterProfile": "nvidia_openai_embeddings",
                "termsMode": "accepted",
                "pricingMode": "free",
                "baseUrl": f"{base_url}/v1",
                "credentialRef": "env:NVIDIA_REDACTION_TEST_KEY",
                "enabled": True,
            },
        )
        enable_nvidia_remote_policy(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))
        register_capability_model(
            Path(os.environ["LOCAL_CONTROL_CENTER_DB"]),
            provider_id="nvidia-redacted-error",
            model="nvidia/embed",
            api_family="embeddings",
        )

        response = client.post(
            "/api/v1/model-gateway/providers/nvidia-redacted-error/embeddings",
            headers=headers,
            json={"model": "nvidia/embed", "input": "hello"},
        )

    assert created.status_code == 201
    assert response.status_code == 502
    assert response.json()["detail"] == "provider_request_failed"
    assert secret not in response.text
    assert "raw body" not in response.text
    assert calls[0]["authorization"] == f"Bearer {secret}"
    with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
        audit_text = "\n".join(
            str(row[0])
            for row in connection.execute(
                "SELECT payload FROM audit_events ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        )
    assert secret not in audit_text
    assert "raw body" not in audit_text
