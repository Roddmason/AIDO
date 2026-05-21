from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.agents.cli_runtimes.base import RuntimeRequest, RuntimeResult
from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime
from local_control_center.agents.cli_runtimes.openhands import OpenHandsRuntime
from local_control_center.agents.cli_runtimes.swe_agent import SweAgentRuntime
from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.usage_ledger import UsageLedger
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    return TestClient(app)


def enable_provider(client: TestClient, headers: dict[str, str], provider_id: str, **extra: object) -> None:
    response = client.patch(
        f"/api/v1/model-gateway/providers/{provider_id}",
        json={"enabled": True, "healthStatus": "healthy", **extra},
        headers=headers,
    )
    assert response.status_code == 200


def test_phase12_schema_adds_unified_model_runtime_gateway_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        seeded_roles = {
            row["role"] for row in connection.execute("SELECT role FROM role_model_policies").fetchall()
        }
        seeded_profiles = {
            row["name"] for row in connection.execute("SELECT name FROM routing_profiles").fetchall()
        }
        seeded_providers = {
            row["provider_id"]
            for row in connection.execute("SELECT provider_id FROM provider_accounts").fetchall()
        }

    assert 12 in migrations
    assert {
        "provider_accounts",
        "model_catalog",
        "routing_profiles",
        "role_model_policies",
        "usage_ledger",
        "provider_limits",
        "routing_decisions",
        "cli_sessions",
        "runtime_capabilities",
        "provider_health_checks",
        "budget_rules",
        "model_benchmarks",
    } <= tables
    assert {
        "analyst",
        "product_owner",
        "technical_lead",
        "developer",
        "qa",
        "security_reviewer",
        "release_manager",
    } <= seeded_roles
    assert {
        "free_first",
        "cost_controlled",
        "balanced_best_value",
        "max_performance",
        "manual_by_profile",
        "local_private",
    } <= seeded_profiles
    assert {
        "internal_mock",
        "nvidia_nim",
        "ollama",
        "codex_cli",
        "claude_code_cli",
        "manual",
    } <= seeded_providers


def test_phase13_schema_adds_benchmark_outcomes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 13 in migrations
    assert "model_benchmark_outcomes" in tables


def test_provider_accounts_crud_endpoints_do_not_expose_raw_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "test_compatible",
            "displayName": "Test Compatible",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "TEST_COMPATIBLE_API_KEY",
            "enabled": False,
        },
    )
    assert created.status_code == 201
    payload = created.json()["provider"]
    assert payload["credentialRef"] == "env:TEST_COMPATIBLE_API_KEY"
    assert "sk-" not in str(payload)

    patched = client.patch(
        "/api/v1/model-gateway/providers/test_compatible",
        headers=headers,
        json={"enabled": True, "lastError": "Authorization: Bearer sk-testsecret123456"},
    )
    assert patched.status_code == 200
    assert patched.json()["provider"]["enabled"] is True
    assert "[redacted]" in patched.json()["provider"]["lastError"]

    listed = client.get("/api/v1/model-gateway/providers")
    assert listed.status_code == 200
    assert any(item["providerId"] == "test_compatible" for item in listed.json()["providers"])


def test_provider_accounts_reject_raw_credential_refs_and_support_env_scheme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_TEST_COMPATIBLE_API_KEY", "sk-testsecret123456")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    rejected = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "raw_secret_provider",
            "displayName": "Raw Secret Provider",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "sk-testsecret123456",
            "enabled": False,
        },
    )
    assert rejected.status_code == 400
    assert "credential_ref" in rejected.json()["detail"]

    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "env_scheme_provider",
            "displayName": "Env Scheme Provider",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "env:AIDO_TEST_COMPATIBLE_API_KEY",
            "enabled": False,
        },
    )

    assert created.status_code == 201
    provider = created.json()["provider"]
    assert provider["credentialRef"] == "env:AIDO_TEST_COMPATIBLE_API_KEY"
    assert provider["credentialStatus"] == "configured"
    assert "sk-testsecret" not in str(provider)

    uppercase_secret = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "uppercase_secret_provider",
            "displayName": "Uppercase Secret Provider",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "NOT_A_VALID_REF",
            "enabled": False,
        },
    )
    assert uppercase_secret.status_code == 400


def test_credential_resolver_supports_env_keyring_fallbacks_and_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_PROVIDER_SECRET", "sk-testsecret123456")
    resolver = CredentialResolver()

    env_result = resolver.resolve("env:AIDO_PROVIDER_SECRET")
    legacy_env_result = resolver.resolve("AIDO_PROVIDER_SECRET")
    missing_result = resolver.resolve("env:AIDO_MISSING_SECRET")
    raw_result = resolver.resolve("sk-testsecret123456")
    keyring_result = resolver.resolve("keyring:aido/nvidia_nim")

    assert env_result.status == "configured"
    assert env_result.source == "env"
    assert env_result.value == "sk-testsecret123456"
    assert legacy_env_result.status == "configured"
    assert missing_result.status == "missing"
    assert raw_result.status == "invalid"
    assert raw_result.value is None
    assert keyring_result.status in {"configured", "missing", "unsupported"}
    assert "sk-testsecret" not in repr(env_result)
    assert "sk-testsecret" not in str(env_result.to_public_dict())


def test_credential_resolver_fetches_remote_openbao_kv2_secret_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    def fake_http_json_get(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        requests.append((url, headers))
        return {"data": {"data": {"api_key": "sk-remote-secret123456"}}}

    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("AIDO_SECRET_VAULT_TOKEN", "vault-session-token")
    resolver = CredentialResolver(http_json_get=fake_http_json_get)

    status = resolver.status("openbao:secret/providers/nvidia_nim#api_key")
    result = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    assert status == "unverified"
    assert result.status == "configured"
    assert result.source == "openbao"
    assert result.value == "sk-remote-secret123456"
    assert requests == [
        (
            "https://vault.example/v1/secret/data/providers/nvidia_nim",
            {"X-Vault-Token": "vault-session-token", "Accept": "application/json"},
        )
    ]
    assert "remote-secret" not in repr(result)
    assert "vault-session-token" not in repr(result)


def test_remote_vault_refs_validate_format_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = CredentialResolver(http_json_get=lambda url, headers, timeout: {})

    missing = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")
    invalid = resolver.resolve("openbao:secret/providers/nvidia_nim")

    assert missing.status == "missing"
    assert "not configured" in missing.message
    assert invalid.status == "invalid"
    assert "field" in invalid.message


def test_remote_vault_requires_secure_transport_or_explicit_loopback_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_SECRET_VAULT_TOKEN", "vault-session-token")
    resolver = CredentialResolver(
        http_json_get=lambda url, headers, timeout: {"data": {"data": {"api_key": "secret"}}}
    )

    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "http://vault.example")
    insecure_remote = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "http://127.0.0.1:8200")
    insecure_loopback = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    monkeypatch.setenv("AIDO_ALLOW_INSECURE_LOCAL_VAULT", "true")
    local_dev = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    assert insecure_remote.status == "invalid"
    assert insecure_loopback.status == "invalid"
    assert local_dev.status == "configured"


def test_remote_vault_requires_kv2_payload_and_normalizes_decode_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("AIDO_SECRET_VAULT_TOKEN", "vault-session-token")
    kv1_resolver = CredentialResolver(
        http_json_get=lambda url, headers, timeout: {"data": {"api_key": "secret"}}
    )
    bad_decode_resolver = CredentialResolver(
        http_json_get=lambda url, headers, timeout: (_ for _ in ()).throw(
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        )
    )

    kv1 = kv1_resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")
    bad_decode = bad_decode_resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    assert kv1.status == "missing"
    assert "KV v2" in kv1.message
    assert bad_decode.status == "unavailable"


def test_remote_vault_can_use_approle_bootstrap_without_static_vault_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[tuple[str, dict[str, str], dict[str, str]]] = []
    gets: list[tuple[str, dict[str, str]]] = []

    def fake_http_json_post(
        url: str, headers: dict[str, str], payload: dict[str, str], timeout: float
    ) -> dict[str, object]:
        posts.append((url, headers, payload))
        return {"auth": {"client_token": "vault-session-token"}}

    def fake_http_json_get(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        gets.append((url, headers))
        return {"data": {"data": {"api_key": "provider-secret"}}}

    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("AIDO_SECRET_VAULT_AUTH_METHOD", "approle")
    monkeypatch.setenv("AIDO_SECRET_VAULT_ROLE_ID_REF", "env:AIDO_OPENBAO_ROLE_ID")
    monkeypatch.setenv("AIDO_SECRET_VAULT_SECRET_ID_REF", "env:AIDO_OPENBAO_SECRET_ID")
    monkeypatch.setenv("AIDO_OPENBAO_ROLE_ID", "role-id")
    monkeypatch.setenv("AIDO_OPENBAO_SECRET_ID", "secret-id")
    resolver = CredentialResolver(http_json_get=fake_http_json_get, http_json_post=fake_http_json_post)

    status = resolver.status("openbao:secret/providers/nvidia_nim#api_key")
    result = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    assert status == "unverified"
    assert result.status == "configured"
    assert posts == [
        (
            "https://vault.example/v1/auth/approle/login",
            {"Content-Type": "application/json", "Accept": "application/json"},
            {"role_id": "role-id", "secret_id": "secret-id"},
        )
    ]
    assert gets == [
        (
            "https://vault.example/v1/secret/data/providers/nvidia_nim",
            {"X-Vault-Token": "vault-session-token", "Accept": "application/json"},
        )
    ]


def test_remote_vault_propagates_invalid_auth_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("AIDO_SECRET_VAULT_AUTH_METHOD", "approle")
    monkeypatch.setenv("AIDO_SECRET_VAULT_ROLE_ID_REF", "openbao:secret/bootstrap#role_id")
    monkeypatch.setenv("AIDO_SECRET_VAULT_SECRET_ID_REF", "env:AIDO_OPENBAO_SECRET_ID")
    monkeypatch.setenv("AIDO_OPENBAO_SECRET_ID", "secret-id")
    resolver = CredentialResolver(http_json_get=lambda url, headers, timeout: {})

    result = resolver.resolve("openbao:secret/providers/nvidia_nim#api_key")

    assert result.status == "invalid"
    assert "recursively" in result.message


def test_provider_health_check_does_not_mark_missing_remote_vault_ref_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "remote_secret_provider",
            "displayName": "Remote Secret Provider",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "openbao:secret/providers/remote_secret_provider#api_key",
            "enabled": True,
        },
    )
    health = client.post(
        "/api/v1/model-gateway/providers/remote_secret_provider/health-check", headers=headers
    )

    assert created.status_code == 201
    assert created.json()["provider"]["credentialStatus"] == "missing"
    assert health.status_code == 200
    assert health.json()["health"]["healthStatus"] == "misconfigured"


def test_openai_compatible_provider_uses_credential_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIDO_PROVIDER_SECRET", "sk-testsecret123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")

    provider = OpenAICompatibleProvider(
        provider_id="test_resolver",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_PROVIDER_SECRET",
        mock=False,
    )

    assert provider._credential() == "sk-testsecret123456"
    health = provider.health_check()
    assert health.status == "disabled"
    assert "sk-testsecret" not in health.message


def test_model_catalog_crud_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/models",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "model": "nvidia/test-model",
            "displayName": "NVIDIA Test",
            "modelFamily": "nim",
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "supportsJson": True,
            "inputPricePerMtok": 0,
            "outputPricePerMtok": 0,
            "freeTier": True,
            "enabled": True,
            "source": "test",
        },
    )
    assert created.status_code == 201
    model_id = created.json()["model"]["id"]

    patched = client.patch(
        f"/api/v1/model-gateway/models/{model_id}", headers=headers, json={"enabled": False}
    )
    assert patched.status_code == 200
    assert patched.json()["model"]["enabled"] is False

    listed = client.get("/api/v1/model-gateway/models")
    assert listed.status_code == 200
    assert any(item["model"] == "nvidia/test-model" for item in listed.json()["models"])


def test_routing_profiles_and_role_policy_seeds_are_exposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    profiles = client.get("/api/v1/model-gateway/routing-profiles")
    policies = client.get("/api/v1/model-gateway/role-policies")

    assert profiles.status_code == 200
    assert policies.status_code == 200
    assert any(item["name"] == "balanced_best_value" for item in profiles.json()["routingProfiles"])
    analyst = next(item for item in policies.json()["rolePolicies"] if item["role"] == "analyst")
    assert analyst["maxCostPerTaskUsd"] == 0.10
    assert analyst["allowApi"] is True


def test_agent_profile_stores_routing_runtime_and_budget_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    response = client.post(
        "/api/v1/agent-profiles",
        headers=headers,
        json={
            "id": "agent-routing-controls",
            "name": "Agent Routing Controls",
            "role": "developer",
            "runtimeMode": "hybrid",
            "modelPolicyId": "implementation_default",
            "routingProfileId": "balanced_best_value",
            "roleModelPolicyId": "developer",
            "allowedProviders": ["codex_cli"],
            "allowedRuntimes": ["cli"],
            "allowedTools": ["shell"],
            "permissionProfile": "dev_safe",
            "maxTokensPerRun": 120000,
            "allowRemote": True,
            "allowCli": True,
            "allowApi": False,
            "requiresApprovalOverUsd": 1.25,
        },
    )

    assert response.status_code == 201
    profile = response.json()["agentProfile"]
    assert profile["routingProfileId"] == "balanced_best_value"
    assert profile["roleModelPolicyId"] == "developer"
    assert profile["allowedProviders"] == ["codex_cli"]
    assert profile["allowedRuntimes"] == ["cli"]
    assert profile["maxTokensPerRun"] == 120000
    assert profile["allowApi"] is False
    assert profile["requiresApprovalOverUsd"] == 1.25


def test_route_preview_free_first_chooses_nvidia_when_enabled_healthy_and_in_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "nvidia_nim")

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "analyst",
            "taskType": "research_brief",
            "mode": "free_first",
            "riskLevel": "low",
            "contextTokensEstimate": 4000,
            "requiresCodeEdit": False,
            "requiresTools": False,
            "requiresSearch": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 0.5,
        },
    )

    assert response.status_code == 200
    selected = response.json()["selected"]
    assert selected["provider"] == "nvidia_nim"
    assert selected["runtime"] == "api"
    assert response.json()["decisionReason"]


def test_route_preview_local_private_blocks_remote_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "nvidia_nim")
    enable_provider(client, headers, "ollama")

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "analyst",
            "taskType": "doc_summary",
            "mode": "local_private",
            "riskLevel": "medium",
            "contextTokensEstimate": 2000,
            "privacyLevel": "local_private",
            "budgetRemainingUsd": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"]["provider"] == "ollama"
    assert all(item["provider"] != "nvidia_nim" or item["reason"] for item in payload["rejected"])


def test_route_preview_developer_prefers_cli_runtime_over_nvidia_for_code_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "nvidia_nim")
    enable_provider(client, headers, "codex_cli")

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "riskLevel": "medium",
            "contextTokensEstimate": 45000,
            "requiresCodeEdit": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 4.2,
        },
    )

    assert response.status_code == 200
    selected = response.json()["selected"]
    assert selected["provider"] == "codex_cli"
    assert selected["runtime"] == "cli"


def test_route_preview_technical_lead_selects_premium_xhigh_when_budget_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "technical_lead",
            "taskType": "architecture_decision",
            "mode": "max_performance",
            "riskLevel": "high",
            "contextTokensEstimate": 60000,
            "requiresReasoning": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 8,
        },
    )

    assert response.status_code == 200
    selected = response.json()["selected"]
    assert selected["provider"] == "codex_cli"
    assert selected["effort"] == "xhigh"


def test_route_preview_requires_approval_when_estimated_cost_exceeds_role_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "analyst",
            "taskType": "research_brief",
            "mode": "max_performance",
            "riskLevel": "high",
            "contextTokensEstimate": 60000,
            "requiresReasoning": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["policyResult"]["requiresApproval"] is True


def test_quota_manager_blocks_provider_in_cooldown(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager = QuotaManager(connection)
        manager.record_rate_limit(
            provider_id="nvidia_nim", model="auto_best_available", retry_after_seconds=120
        )

        result = manager.check(provider_id="nvidia_nim", model="auto_best_available", request_tokens=100)

    assert result.allowed is False
    assert result.reason == "provider_in_cooldown"


def test_usage_ledger_records_estimated_and_actual_usage(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ledger = UsageLedger(connection)
        estimated = ledger.record_usage(
            provider_id="nvidia_nim",
            model="auto_best_available",
            runtime_type="api",
            role="analyst",
            request_id="req-estimated",
            input_tokens=10,
            output_tokens=5,
            estimated_cost_usd=0.001,
            raw_usage={"usage_source": "estimated"},
        )
        actual = ledger.record_usage(
            provider_id="internal_mock",
            model="mock",
            runtime_type="api",
            role="qa",
            request_id="req-actual",
            input_tokens=20,
            output_tokens=10,
            estimated_cost_usd=0,
            actual_cost_usd=0,
            raw_usage={"usage_source": "provider"},
        )

    assert estimated["actualCostUsd"] is None
    assert estimated["rawUsage"]["usage_source"] == "estimated"
    assert actual["actualCostUsd"] == 0
    assert actual["totalTokens"] == 30


def test_route_execute_mock_links_usage_and_decision_to_workflow_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "nvidia_nim")

    response = client.post(
        "/api/v1/model-gateway/route/execute-mock",
        headers=headers,
        json={
            "role": "analyst",
            "taskType": "doc_summary",
            "mode": "free_first",
            "contextTokensEstimate": 500,
            "privacyLevel": "remote_allowed",
            "workflowRunId": "workflow-run-test",
            "workflowStepId": "workflow-step-test",
            "taskId": "doc_summary",
        },
    )

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["workflowRunId"] == "workflow-run-test"
    assert usage["workflowStepId"] == "workflow-step-test"
    decisions = client.get("/api/v1/model-gateway/routing-decisions").json()["routingDecisions"]
    assert decisions[0]["workflowRunId"] == "workflow-run-test"
    assert decisions[0]["workflowStepId"] == "workflow-step-test"


def test_benchmarks_derive_attempt_cost_and_latency_from_usage_without_inventing_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    with client:
        UsageLedger(client.app.state.runtime.connection).record_usage(  # type: ignore[attr-defined]
            provider_id="nvidia_nim",
            model="auto_best_available",
            runtime_type="api",
            role="analyst",
            input_tokens=100,
            output_tokens=20,
            estimated_cost_usd=0.01,
            latency_ms=150,
            raw_usage={"usage_source": "estimated"},
        )

    response = client.get("/api/v1/model-gateway/benchmarks")

    assert response.status_code == 200
    benchmark = next(item for item in response.json()["benchmarks"] if item["providerId"] == "nvidia_nim")
    assert benchmark["tasksAttempted"] == 1
    assert benchmark["avgCost"] == 0.01
    assert benchmark["avgLatencyMs"] == 150
    assert benchmark["successRate"] is None
    assert benchmark["insufficientData"] is True


def test_benchmark_outcome_endpoint_records_success_qa_and_rework_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    outcome_payload = {
        "providerId": "codex_cli",
        "model": "gpt-5.5",
        "runtimeType": "cli",
        "role": "developer",
        "taskId": "implementation",
        "success": True,
        "qaPass": True,
        "rework": False,
        "estimatedCostUsd": 0.42,
        "latencyMs": 1200,
        "metadata": {"source": "test"},
    }

    created = client.post("/api/v1/model-gateway/benchmark-outcomes", headers=headers, json=outcome_payload)
    outcomes = client.get("/api/v1/model-gateway/benchmark-outcomes")
    benchmarks = client.get("/api/v1/model-gateway/benchmarks")

    assert created.status_code == 201
    assert outcomes.status_code == 200
    assert any(item["providerId"] == "codex_cli" for item in outcomes.json()["outcomes"])
    benchmark = next(
        item
        for item in benchmarks.json()["benchmarks"]
        if item["providerId"] == "codex_cli" and item["model"] == "gpt-5.5"
    )
    assert benchmark["tasksAttempted"] == 1
    assert benchmark["successRate"] == 1.0
    assert benchmark["qaPassRate"] == 1.0
    assert benchmark["reworkRate"] == 0.0
    assert benchmark["avgCost"] == 0.42
    assert benchmark["avgLatencyMs"] == 1200


def test_evidence_creation_ingests_benchmark_outcome_from_usage_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with client:
        store = client.app.state.runtime  # type: ignore[attr-defined]
        project = store.create_project(
            name="Benchmark Evidence", path=tmp_path / "benchmark-evidence", template_id="other"
        )
        usage = UsageLedger(store.connection).record_usage(
            provider_id="codex_cli",
            model="gpt-5.5",
            runtime_type="cli",
            role="developer",
            workflow_run_id="workflow-run-benchmark",
            workflow_step_id="workflow-step-benchmark",
            agent_id="agent-dev",
            task_id="implementation",
            estimated_cost_usd=0.33,
            latency_ms=900,
            raw_usage={"usage_source": "estimated"},
        )

    created = client.post(
        "/api/v1/evidence",
        headers=headers,
        json={
            "projectId": project["id"],
            "workflowRunId": "workflow-run-benchmark",
            "workflowStepId": "workflow-step-benchmark",
            "agentId": "agent-dev",
            "taskId": "implementation",
            "usageLedgerId": usage["id"],
            "testPlan": "Verify routed implementation",
            "testResults": [{"command": "pytest", "status": "passed", "durationMs": 900}],
            "qaVerdict": "passed",
        },
    )
    outcomes = client.get("/api/v1/model-gateway/benchmark-outcomes")

    assert created.status_code == 201
    assert outcomes.status_code == 200
    outcome = next(item for item in outcomes.json()["outcomes"] if item["usageLedgerId"] == usage["id"])
    assert outcome["providerId"] == "codex_cli"
    assert outcome["model"] == "gpt-5.5"
    assert outcome["runtimeType"] == "cli"
    assert outcome["workflowStepId"] == "workflow-step-benchmark"
    assert outcome["success"] is True
    assert outcome["qaPass"] is True
    assert outcome["rework"] is False
    assert outcome["estimatedCostUsd"] == 0.33
    assert outcome["latencyMs"] == 900


def test_nvidia_provider_mock_parses_usage_and_handles_429(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        provider = NvidiaNimProvider(connection=connection, mock=True)
        response = provider.chat_completion(
            ModelRequest(model="auto_best_available", messages=[{"role": "user", "content": "summarize"}])
        )
        usage = provider.parse_usage(response.raw_response)

        assert response.model == "auto_best_available"
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0

        limited = provider.handle_error(status_code=429, message="rate limit", model="auto_best_available")
        assert limited.health_status == "degraded"
        quota = QuotaManager(connection).check(
            provider_id="nvidia_nim", model="auto_best_available", request_tokens=1
        )
        assert quota.allowed is False


def test_cli_detection_missing_binary_returns_not_installed() -> None:
    missing = "__definitely_missing_aido_cli_binary__"

    codex = CodexCliRuntime(executable=missing).detect()
    claude = ClaudeCodeCliRuntime(executable=missing).detect()

    assert codex.status == "not_installed"
    assert codex.message == "Codex CLI not detected"
    assert claude.status == "not_installed"
    assert claude.message == "Claude Code CLI not detected"


def test_cli_runtime_blocks_dangerous_flags(tmp_path: Path) -> None:
    runtime = CodexCliRuntime(executable="codex", mock=True)

    with pytest.raises(ValueError, match="dangerous"):
        runtime.build_command(
            RuntimeRequest(
                runtime="codex_cli",
                workspace_id="workspace-test",
                workspace_path=str(tmp_path),
                prompt="edit code",
                profile="codex_gpt55_developer",
                extra_args=["--dangerously-bypass-approvals-and-sandbox"],
            )
        )


def test_cli_runtime_parses_usage_from_jsonl_output() -> None:
    runtime = CodexCliRuntime(executable="codex", mock=True)
    result = RuntimeResult(
        runtime="codex_cli",
        status="completed",
        stdout='{"event":"token_usage","usage":{"prompt_tokens":10,"completion_tokens":5,"reasoning_tokens":2,"total_tokens":17}}\n',
        returnCode=0,
    )

    usage = runtime.parse_usage(result)

    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.reasoning_tokens == 2
    assert usage.total_tokens == 17
    assert usage.raw_usage["usage_source"] == "cli_output"


def test_cli_runtimes_parse_runtime_specific_usage_aliases() -> None:
    codex = CodexCliRuntime(executable="codex", mock=True).parse_usage(
        RuntimeResult(
            runtime="codex_cli",
            status="completed",
            stdout='{"type":"token_count","tokens":{"input":11,"cached_input":3,"output":7,"reasoning":5,"total":26}}\n',
            returnCode=0,
        )
    )
    claude = ClaudeCodeCliRuntime(executable="claude", mock=True).parse_usage(
        RuntimeResult(
            runtime="claude_code_cli",
            status="completed",
            stdout='{"type":"result","message":{"usage":{"input_tokens":13,"output_tokens":8,"cache_read_input_tokens":2}}}\n',
            returnCode=0,
        )
    )
    openhands = OpenHandsRuntime(executable="openhands", mock=True).parse_usage(
        RuntimeResult(
            runtime="openhands",
            status="completed",
            stdout='{"metrics":{"token_usage":{"prompt_tokens":17,"completion_tokens":9,"total_tokens":26}}}\n',
            returnCode=0,
        )
    )
    swe_agent = SweAgentRuntime(executable="sweagent", mock=True).parse_usage(
        RuntimeResult(
            runtime="swe_agent",
            status="completed",
            stdout='{"llm_metrics":{"input_tokens":19,"output_tokens":10,"total_tokens":29}}\n',
            returnCode=0,
        )
    )

    assert (
        codex is not None
        and codex.input_tokens == 11
        and codex.cached_input_tokens == 3
        and codex.reasoning_tokens == 5
    )
    assert (
        claude is not None
        and claude.input_tokens == 13
        and claude.cached_input_tokens == 2
        and claude.output_tokens == 8
    )
    assert openhands is not None and openhands.total_tokens == 26
    assert swe_agent is not None and swe_agent.output_tokens == 10


def test_secrets_are_redacted_from_logs() -> None:
    redacted = redact_secrets(
        {
            "Authorization": "Bearer sk-testsecret123456",
            "message": "token sk-anothersecret123456 should not appear",
            "nested": {"api_key": "sk-nestedsecret123456"},
        }
    )

    assert "sk-testsecret" not in str(redacted)
    assert redacted["Authorization"] == "[redacted]"
    assert redacted["nested"]["api_key"] == "[redacted]"


def test_model_gateway_endpoints_return_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    endpoints = [
        "/api/v1/model-gateway/overview",
        "/api/v1/model-gateway/providers",
        "/api/v1/model-gateway/providers/nvidia_nim",
        "/api/v1/model-gateway/models",
        "/api/v1/model-gateway/routing-profiles",
        "/api/v1/model-gateway/role-policies",
        "/api/v1/model-gateway/usage-ledger",
        "/api/v1/model-gateway/usage-ledger/summary",
        "/api/v1/model-gateway/routing-decisions",
        "/api/v1/model-gateway/benchmarks",
        "/api/v1/model-gateway/benchmark-outcomes",
        "/api/v1/model-gateway/provider-limits",
        "/api/v1/model-gateway/budget-rules",
        "/api/v1/model-gateway/cli-runtimes",
        "/api/v1/model-gateway/cli-sessions",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, endpoint
        assert isinstance(response.json(), dict), endpoint

    detect = client.post("/api/v1/model-gateway/cli-runtimes/codex_cli/detect", headers=headers)
    health = client.post("/api/v1/model-gateway/providers/nvidia_nim/health-check", headers=headers)
    discover = client.post("/api/v1/model-gateway/providers/nvidia_nim/discover-models", headers=headers)
    mock_execute = client.post(
        "/api/v1/model-gateway/route/execute-mock",
        headers=headers,
        json={
            "role": "analyst",
            "taskType": "doc_summary",
            "mode": "free_first",
            "privacyLevel": "remote_allowed",
        },
    )

    assert detect.status_code == 200
    assert health.status_code == 200
    assert discover.status_code == 200
    assert mock_execute.status_code == 200


def test_route_execute_real_is_blocked_by_default_and_requires_approval_when_costly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")

    disabled = client.post(
        "/api/v1/model-gateway/route/execute",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "contextTokensEstimate": 1000,
            "requiresCodeEdit": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 4,
        },
    )
    assert disabled.status_code == 403
    assert "disabled" in disabled.json()["detail"].lower()

    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    approval = client.post(
        "/api/v1/model-gateway/route/execute",
        headers=headers,
        json={
            "role": "technical_lead",
            "taskType": "architecture_decision",
            "mode": "max_performance",
            "contextTokensEstimate": 60000,
            "requiresReasoning": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 8,
        },
    )
    assert approval.status_code == 409
    assert "approval" in approval.json()["detail"].lower()
    action_requests = client.get("/api/v1/approvals").json()["actionRequests"]
    model_route_actions = [item for item in action_requests if item["actionType"] == "model.route.execute"]
    assert len(model_route_actions) == 1
    assert model_route_actions[0]["status"] == "pending"
    assert model_route_actions[0]["payload"]["routing"]["selected"]["provider"] == "codex_cli"
    overview = client.get("/api/v1/model-gateway/overview").json()["overview"]
    assert overview["pendingModelApprovals"] == 1


def test_workflow_start_records_model_router_decisions_for_each_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Routing Workflow", path=tmp_path / "routing-workflow", template_id="other"
    )
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    workflow = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"projectId": project["id"], "kind": "idea_to_pr", "title": "Route workflow steps"},
    ).json()["workflow"]

    started = client.post(
        f"/api/v1/workflows/{workflow['id']}/start", headers=headers, json={"reason": "route"}
    )
    decisions = client.get("/api/v1/model-gateway/routing-decisions").json()["routingDecisions"]

    assert started.status_code == 202
    assert len(decisions) == len(started.json()["workflowSteps"])
    assert {item["workflowRunId"] for item in decisions} == {started.json()["workflowRun"]["id"]}
    implementation = next(item for item in decisions if item["taskType"] == "implementation")
    assert implementation["workflowStepId"].startswith("workflow-step-")
