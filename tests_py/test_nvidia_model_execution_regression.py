from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.capabilities import ProviderHttpResponse
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.providers.nvidia_nim import NvidiaNimCapabilityError, NvidiaNimProvider
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_adapters.models import RuntimeExecutionRequest
from local_control_center.agents.runtime_adapters.provider_factory import ProviderFactoryAdapter
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _model(model: str = "auto_best_available") -> dict[str, object]:
    return {
        "providerId": "nvidia-custom",
        "model": model,
        "enabled": True,
        "contextWindow": 128000,
        "supportsVision": False,
        "supportsJson": True,
        "supportsReasoning": True,
        "supportsTools": True,
    }


def _provider(provider_family: str = "nvidia_nim") -> dict[str, object]:
    return {
        "providerId": "nvidia-custom",
        "providerFamily": provider_family,
        "providerType": "api",
        "enabled": True,
        "healthStatus": "healthy",
        "lastHealthCheckAt": "2026-09-19T00:00:00Z",
    }


@pytest.mark.parametrize("provider_id", ["nvidia_nim", "nvidia-custom"])
def test_routers_reject_nvidia_auto_selection_sentinel_for_canonical_and_custom_endpoint(
    provider_id: str,
) -> None:
    model = {**_model(), "providerId": provider_id}
    runtime_status = {
        "id": provider_id,
        "providerFamily": "nvidia_nim",
        "executable": True,
        "kind": "api",
    }

    manager_reason = AIResourceManager._runtime_executable_reject_reason(
        model=model,
        runtime_statuses={provider_id: runtime_status},
        request=AIResourceRequest(task_type="product_owner.discovery", required_capabilities=["chat"]),
    )
    router_reason = ModelRouter._hard_reject_reason(
        object.__new__(ModelRouter),
        RoutingRequest(taskType="product_owner.discovery"),
        {},
        {**_provider(), "providerId": provider_id},
        model,
    )

    assert manager_reason == "nvidia_model_selection_required"
    assert router_reason == "nvidia_model_selection_required"


def test_routers_keep_auto_selection_available_for_non_nvidia_provider() -> None:
    model = _model()
    runtime_status = {
        "id": "nvidia-custom",
        "providerFamily": "openai_compatible",
        "executable": True,
        "kind": "api",
    }

    manager_reason = AIResourceManager._runtime_executable_reject_reason(
        model=model,
        runtime_statuses={"nvidia-custom": runtime_status},
        request=AIResourceRequest(task_type="product_owner.discovery", required_capabilities=["chat"]),
    )
    router_reason = ModelRouter._hard_reject_reason(
        object.__new__(ModelRouter),
        RoutingRequest(taskType="product_owner.discovery"),
        {},
        _provider("openai_compatible"),
        model,
    )

    assert manager_reason is None
    assert router_reason is None


def test_select_resource_rejects_catalog_sentinel_and_selects_known_nvidia_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        lambda _service, *, project_id=None: [
            {
                "id": "nvidia_nim",
                "providerFamily": "nvidia_nim",
                "kind": "api",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat"],
            }
        ],
    )

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE model_catalog SET enabled = 0")
        _configure_nvidia_account(connection)
        store = ProviderAccountStore(connection)
        for model in ("auto_best_available", "meta/llama-3.3-70b-instruct"):
            store.upsert_model(
                {
                    "providerId": "nvidia_nim",
                    "model": model,
                    "displayName": model,
                    "modelFamily": "nvidia",
                    "contextWindow": 128000,
                    "maxOutputTokens": 4096,
                    "supportsTools": False,
                    "supportsJson": False,
                    "supportsStreaming": True,
                    "supportsVision": False,
                    "supportsReasoning": False,
                    "freeTier": True,
                    "inputPricePerMtok": 0.0,
                    "outputPricePerMtok": 0.0,
                    "enabled": True,
                    "source": "test",
                }
            )
        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )

    assert decision["selected"]["model"] == "meta/llama-3.3-70b-instruct"
    sentinel = next(item for item in decision["rejected"] if item["model"] == "auto_best_available")
    assert sentinel["reason"] == "nvidia_model_selection_required"


class _RecordingProvider:
    base_url = "https://nvidia.example.invalid/v1"
    credential_ref = ""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self.error = error

    def chat_completion(self, _request: ModelRequest):
        self.calls += 1
        if self.error is not None:
            raise self.error
        raise AssertionError("the sentinel must block before chat execution")


def _execution_request(*, model: str | None = "auto_best_available") -> RuntimeExecutionRequest:
    input_payload: dict[str, object] = {
        "providerId": "nvidia_nim",
        "messages": [{"role": "user", "content": "return JSON"}],
    }
    if model is not None:
        input_payload["model"] = model
    return RuntimeExecutionRequest(
        projectId="project-nvidia-regression",
        workspaceId="workspace-nvidia-regression",
        workspacePath=".",
        capability="chat",
        input=input_payload,
    )


def _configure_nvidia_account(connection, *, policy_enabled: bool = True) -> None:
    ProviderAccountStore(connection).upsert_provider_account(
        {
            "providerId": "nvidia_nim",
            "displayName": "NVIDIA NIM",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_enterprise",
            "apiFamily": "chat_completions",
            "adapterProfile": "nvidia_openai_chat",
            "termsMode": "accepted",
            "pricingMode": "configured",
            "baseUrl": "https://nvidia.example.invalid/v1",
            "credentialRef": "",
            "enabled": True,
        }
    )
    runtime = RuntimeConfigRepository(connection)
    runtime.set_runtime_setting("runtime.remote.enabled", True)
    runtime.set_runtime_setting("runtime.nvidia.enabled", policy_enabled)


@pytest.mark.parametrize("request_model", ["auto_best_available", None])
def test_nvidia_adapter_blocks_explicit_and_env_sentinel_before_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_model: str | None,
) -> None:
    monkeypatch.setenv("AIDO_NVIDIA_MODEL", "auto_best_available")
    provider = _RecordingProvider()
    monkeypatch.setattr(ProviderAdapterFactory, "resolve_for_execution", lambda *_args: provider)

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _configure_nvidia_account(connection)
        result = ProviderFactoryAdapter(
            provider_family="nvidia_nim",
            display_name="NVIDIA NIM",
            connection=connection,
        ).execute(_execution_request(model=request_model))

    assert result.status == "blocked"
    assert result.reason == "nvidia_model_selection_required"
    assert provider.calls == 0


def test_nvidia_adapter_keeps_disabled_policy_before_sentinel_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(ProviderAdapterFactory, "resolve_for_execution", lambda *_args: provider)

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _configure_nvidia_account(connection, policy_enabled=False)
        result = ProviderFactoryAdapter(
            provider_family="nvidia_nim",
            display_name="NVIDIA NIM",
            connection=connection,
        ).execute(_execution_request())

    assert result.status == "blocked"
    assert result.reason == "runtime.nvidia.enabled is false."
    assert provider.calls == 0


@pytest.mark.parametrize("http_status", [401, 410, 503])
def test_nvidia_adapter_reports_only_stable_error_code_and_http_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
) -> None:
    error = NvidiaNimCapabilityError("provider_request_failed")
    error.status_code = http_status
    provider = _RecordingProvider(error)
    monkeypatch.setattr(ProviderAdapterFactory, "resolve_for_execution", lambda *_args: provider)

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _configure_nvidia_account(connection)
        result = ProviderFactoryAdapter(
            provider_family="nvidia_nim",
            display_name="NVIDIA NIM",
            connection=connection,
        ).execute(_execution_request(model="nvidia/selected-model"))

    assert result.status == "unavailable"
    assert (
        result.reason == f"NVIDIA NIM execution failed: provider_request_failed (http_status={http_status})"
    )
    assert result.http_status == http_status
    assert result.redacted is True


def test_nvidia_capability_errors_keep_http_status_timeout_and_429_cooldown(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)

        def unauthorized(_request):
            return ProviderHttpResponse(
                statusCode=401,
                jsonBody={"error": {"message": "secret body must not escape"}},
            )

        unauthorized_provider = NvidiaNimProvider(
            provider_id="nvidia-custom",
            connection=connection,
            base_url="https://nvidia.example.invalid/v1",
            credential_ref="",
            deployment_mode="self_hosted_enterprise",
            api_family="chat_completions",
            adapter_profile="nvidia_openai_chat",
            transport=unauthorized,
        )
        with pytest.raises(NvidiaNimCapabilityError) as unauthorized_error:
            unauthorized_provider.chat_completion(
                ModelRequest(model="nvidia/selected-model", messages=[{"role": "user", "content": "hello"}])
            )

        def timed_out(_request):
            raise TimeoutError("https://secret.example.invalid timed out")

        timeout_provider = NvidiaNimProvider(
            provider_id="nvidia-custom",
            base_url="https://nvidia.example.invalid/v1",
            credential_ref="",
            deployment_mode="self_hosted_enterprise",
            api_family="chat_completions",
            adapter_profile="nvidia_openai_chat",
            transport=timed_out,
        )
        with pytest.raises(NvidiaNimCapabilityError) as timeout_error:
            timeout_provider.chat_completion(
                ModelRequest(model="nvidia/selected-model", messages=[{"role": "user", "content": "hello"}])
            )

        def rate_limited(_request):
            return ProviderHttpResponse(
                statusCode=429,
                headers={"retry-after": "60"},
                jsonBody={"error": {"message": "secret rate limit body"}},
            )

        rate_limited_provider = NvidiaNimProvider(
            provider_id="nvidia-custom",
            connection=connection,
            base_url="https://nvidia.example.invalid/v1",
            credential_ref="",
            deployment_mode="self_hosted_enterprise",
            api_family="chat_completions",
            adapter_profile="nvidia_openai_chat",
            transport=rate_limited,
        )
        with pytest.raises(NvidiaNimCapabilityError) as rate_limit_error:
            rate_limited_provider.chat_completion(
                ModelRequest(model="nvidia/selected-model", messages=[{"role": "user", "content": "hello"}])
            )
        cooldown = QuotaManager(connection).check(
            provider_id="nvidia-custom",
            model="nvidia/selected-model",
            request_tokens=1,
        )

    assert unauthorized_error.value.code == "provider_request_failed"
    assert unauthorized_error.value.status_code == 401
    assert timeout_error.value.code == "provider_request_timeout"
    assert "secret.example.invalid" not in str(timeout_error.value)
    assert rate_limit_error.value.code == "provider_rate_limited"
    assert rate_limit_error.value.status_code == 429
    assert cooldown.reason == "provider_in_cooldown"


def test_nvidia_provider_blocks_auto_selection_sentinel_before_http() -> None:
    calls: list[object] = []

    def transport(request):
        calls.append(request)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={"choices": [{"message": {"content": "unexpected"}}]},
        )

    provider = NvidiaNimProvider(
        provider_id="nvidia-custom",
        base_url="https://nvidia.example.invalid/v1",
        credential_ref="",
        deployment_mode="self_hosted_enterprise",
        api_family="chat_completions",
        adapter_profile="nvidia_openai_chat",
        transport=transport,
    )

    with pytest.raises(NvidiaNimCapabilityError) as error:
        provider.chat_completion(
            ModelRequest(model="auto_best_available", messages=[{"role": "user", "content": "hello"}])
        )

    assert error.value.code == "nvidia_model_selection_required"
    assert calls == []
