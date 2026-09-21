"""PO selection must remain inside the existing model-execution policy.

These tests never execute a provider, probe a runtime or widen the broker allowlist.
@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.product_owner_agent_contract import (
    PRODUCT_OWNER_AGENT_MODEL_RUNTIMES,
    is_product_owner_runtime,
    product_owner_agent_readiness,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService, _api_provider_status
from local_control_center.security_policy.policy_engine import MODEL_RUNTIME_TOOLS, evaluate_action
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now


def _policy(family: str, *, provider_id: str | None = None) -> dict:
    provider_id = provider_id or family
    return evaluate_action(
        {
            "operation": "product_owner_model_call",
            "agentId": "product_owner_agent",
            "permissionProfile": "plan",
            "tool": family,
            "runtimeId": provider_id,
            "providerId": provider_id,
            "providerFamily": family,
            "workspaceId": "fixture-workspace",
            "workspacePath": ".",
            "agentRunId": "fixture-run",
            "execute": True,
        }
    )


def _configured_api(connection, family: str, model: str) -> dict:
    store = ProviderAccountStore(connection)
    account = store.upsert_provider_account(
        {
            **store.get_provider_account(family),
            "enabled": True,
            "healthStatus": "healthy",
            "lastHealthCheckAt": utc_now(),
            "credentialRef": "env:AIDO_PO_ELIGIBILITY_TEST_KEY",
            "apiFamily": "chat_completions",
        }
    )
    store.upsert_model(
        {
            "providerId": family,
            "model": model,
            "displayName": model,
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "inputPricePerMtok": 0.0,
            "outputPricePerMtok": 0.0,
            "freeTier": True,
            "enabled": True,
            "source": "test",
        }
    )
    return _api_provider_status(
        connection,
        account,
        {"enabled": True},
        ["chat"],
        {"allowed": True},
    )


def test_product_owner_contract_uses_only_existing_execution_adapters():
    assert PRODUCT_OWNER_AGENT_MODEL_RUNTIMES == MODEL_RUNTIME_TOOLS
    assert _policy("gemini")["decision"] == "deny"
    assert "product_owner_model_runtime_denied" in _policy("gemini")["categories"]


@pytest.mark.parametrize("provider", ["ollama", "omniroute"])
def test_resource_pressure_keeps_its_cause_when_product_owner_is_not_executable(provider):
    reason = AIResourceManager._runtime_executable_reject_reason(
        model={"providerId": provider, "model": "configured-model"},
        runtime_statuses={
            provider: {
                "providerFamily": provider,
                "executable": False,
                "productOwnerExecutable": False,
                "reason": "aggregate_memory_budget",
            }
        },
        request=AIResourceRequest(task_type="product_owner.discovery", agent_id="product_owner_agent"),
    )
    assert reason == "runtime_not_executable: aggregate_memory_budget"


@pytest.mark.parametrize(
    "family", ["ollama", "openai_compatible", "openrouter", "nvidia_nim", "anthropic_api"]
)
def test_existing_model_adapters_remain_eligible_and_authorized(family):
    runtime = {
        "id": f"configured-{family}",
        "providerFamily": family,
        "executable": True,
        "productOwnerExecutable": True,
        "capabilities": ["chat"],
        "models": ["fixture-model"],
    }
    assert is_product_owner_runtime(runtime) is True
    assert _policy(family, provider_id=runtime["id"])["decision"] == "allow"


def test_generic_api_health_does_not_claim_gemini_is_product_owner_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDO_PO_ELIGIBILITY_TEST_KEY", "synthetic-fixture-key")
    with closing(open_sqlite_connection(tmp_path / "eligibility.sqlite")) as connection:
        initialize_platform_schema(connection)
        gemini = _configured_api(connection, "gemini", "gemini-2.5-flash-lite")
        nvidia = _configured_api(connection, "nvidia_nim", "fixture-nvidia-model")
    assert gemini["executable"] is True
    assert gemini["productOwnerExecutable"] is False
    assert nvidia["productOwnerExecutable"] is True
    automatic = product_owner_agent_readiness([gemini, nvidia])
    assert automatic["selectedRuntimeId"] == "nvidia_nim"
    assert "gemini" not in automatic["candidateRuntimeIds"]
    explicit = product_owner_agent_readiness([gemini, nvidia], preferred_runtime="gemini")
    assert explicit["executable"] is False
    assert explicit["selectedRuntimeId"] == "gemini"
    assert "limited to" in explicit["reason"]


@pytest.mark.parametrize("explicit_gemini_only", [False, True])
def test_resource_manager_never_selects_gemini_for_product_owner(
    tmp_path,
    monkeypatch,
    explicit_gemini_only,
):
    monkeypatch.setenv("AIDO_PO_ELIGIBILITY_TEST_KEY", "synthetic-fixture-key")
    with closing(open_sqlite_connection(tmp_path / "routing.sqlite")) as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE model_catalog SET enabled = 0")
        statuses = [
            _configured_api(connection, "gemini", "gemini-2.5-flash-lite"),
            _configured_api(connection, "nvidia_nim", "fixture-nvidia-model"),
        ]
        monkeypatch.setattr(
            RuntimeStatusService,
            "list_provider_statuses",
            lambda _self, *, project_id=None: statuses,
        )
        request = AIResourceRequest(
            task_type="product_owner.discovery",
            agent_id="product_owner_agent",
            required_capabilities=["chat"],
            allow_unknown_cost=True,
            preferred_resources=[{"provider": "gemini", "model": "gemini-2.5-flash-lite"}],
            allowed_provider_ids=["gemini"] if explicit_gemini_only else None,
        )
        preference = list(request.preferred_resources)
        decision = AIResourceManager(connection).select_resource(request, record=False)
    assert request.preferred_resources == preference
    assert all(item["providerId"] != "gemini" for item in decision["candidates"])
    rejection = next(item for item in decision["rejected"] if item["providerId"] == "gemini")
    assert rejection["reason"].startswith("runtime_not_executable: ProductOwnerAgent")
    if explicit_gemini_only:
        assert decision["selected"] is None
    else:
        assert decision["selected"]["providerId"] == "nvidia_nim"
