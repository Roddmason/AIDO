from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.agents.cli_runtimes.base import RuntimeRequest, RuntimeResult
from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime
from local_control_center.agents.cli_runtimes.openhands import OpenHandsRuntime
from local_control_center.agents.cli_runtimes.swe_agent import SweAgentRuntime
from local_control_center.agents.cli_sessions import CliSessionStore
from local_control_center.agents.credential_preflight import run_credential_preflight
from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.agents.providers.base import ModelInfo, ModelRequest, ProviderHealth
from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.agents.pricing_catalog import PricingCatalog
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
        provider_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(provider_accounts)").fetchall()
        }
        usage_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(usage_ledger)").fetchall()
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
        "technical_lead_shadow",
        "backend_engineer",
        "frontend_engineer",
        "developer",
        "implementer",
        "qa",
        "qa_reviewer",
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
    assert "metadata_json" in provider_columns
    assert "usage_source" in usage_columns


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
            "metadata": {"region": "test", "apiKey": "sk-providersecret123456"},
        },
    )
    assert created.status_code == 201
    payload = created.json()["provider"]
    assert payload["credentialRef"] == "env:TEST_COMPATIBLE_API_KEY"
    assert payload["metadata"]["region"] == "test"
    assert payload["metadata"]["apiKey"] == "[redacted]"
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


def test_credential_preflight_status_redacts_values_and_avoids_remote_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        raise AssertionError("status preflight must not fetch remote secrets")

    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "sk-testsecret123456")
    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("AIDO_SECRET_VAULT_TOKEN", "vault-session-token")
    resolver = CredentialResolver(http_json_get=fail_if_called)

    report = run_credential_preflight(
        [
            "env:NVIDIA_NIM_API_KEY",
            "openbao:secret/providers/nvidia_nim#api_key",
        ],
        resolver=resolver,
        fetch=False,
    )

    assert report["ok"] is True
    assert report["mode"] == "status"
    assert [item["status"] for item in report["credentials"]] == ["configured", "unverified"]
    assert "sk-testsecret" not in str(report)
    assert "vault-session-token" not in str(report)


def test_credential_preflight_fetch_reports_failures_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROKEN_API_KEY", "sk-testsecret123456")
    resolver = CredentialResolver(http_json_get=lambda url, headers, timeout: {"data": {"api_key": "secret"}})

    report = run_credential_preflight(
        [
            "env:BROKEN_API_KEY",
            "openbao:secret/providers/nvidia_nim#api_key",
        ],
        resolver=resolver,
        fetch=True,
    )

    assert report["ok"] is False
    assert report["mode"] == "fetch"
    assert [item["status"] for item in report["credentials"]] == ["configured", "missing"]
    assert "sk-testsecret" not in str(report)


def test_credential_preflight_script_accepts_pnpm_argument_separator() -> None:
    env = os.environ.copy()
    env["NVIDIA_NIM_API_KEY"] = "redacted-test-value"

    completed = subprocess.run(
        [
            sys.executable,
            "local-control-center/scripts/check-credentials.py",
            "--",
            "--ref",
            "env:NVIDIA_NIM_API_KEY",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(completed.stdout)

    assert report["ok"] is True
    assert report["credentials"][0]["status"] == "configured"
    assert "redacted-test-value" not in completed.stdout


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
    seeded_roles = {item["role"] for item in policies.json()["rolePolicies"]}
    assert {
        "technical_lead_shadow",
        "backend_engineer",
        "frontend_engineer",
        "implementer",
        "qa_reviewer",
    } <= seeded_roles


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
    assert response.json()["budgetResult"]["allowed"] is True
    assert response.json()["quotaResult"]["allowed"] is True


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


def test_budget_rule_denies_route_preview_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    created = client.post(
        "/api/v1/model-gateway/budget-rules",
        headers=headers,
        json={
            "id": "deny-developer-task",
            "scopeType": "role",
            "scopeId": "developer",
            "maxCostUsd": 0.001,
            "maxTokens": 1000,
            "period": "task",
            "actionOnExceed": "deny",
            "enabled": True,
        },
    )
    assert created.status_code == 201

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "contextTokensEstimate": 45000,
            "requiresCodeEdit": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"] is None
    assert payload["budgetResult"]["allowed"] is False
    assert payload["budgetResult"]["action"] == "deny"
    assert any(item["reason"] == "budget_denied" for item in payload["rejected"])


def test_budget_rule_requires_approval_in_route_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    created = client.post(
        "/api/v1/model-gateway/budget-rules",
        headers=headers,
        json={
            "id": "approval-lead-task",
            "scopeType": "role",
            "scopeId": "technical_lead",
            "maxCostUsd": 0.001,
            "period": "task",
            "actionOnExceed": "require_approval",
            "enabled": True,
        },
    )
    assert created.status_code == 201

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "technical_lead",
            "taskType": "architecture_decision",
            "mode": "max_performance",
            "contextTokensEstimate": 60000,
            "requiresReasoning": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 8,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"]["provider"] == "codex_cli"
    assert payload["budgetResult"]["allowed"] is True
    assert payload["budgetResult"]["requiresApproval"] is True
    assert payload["policyResult"]["requiresApproval"] is True


def test_budget_rule_fallback_rejects_candidate_and_selects_next_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    enable_provider(client, headers, "claude_code_cli")
    created = client.post(
        "/api/v1/model-gateway/budget-rules",
        headers=headers,
        json={
            "id": "fallback-codex-cost",
            "scopeType": "provider",
            "scopeId": "codex_cli",
            "maxCostUsd": 0.001,
            "period": "task",
            "actionOnExceed": "fallback",
            "enabled": True,
        },
    )
    assert created.status_code == 201

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "contextTokensEstimate": 45000,
            "requiresCodeEdit": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"]["provider"] == "claude_code_cli"
    assert any(
        item["provider"] == "codex_cli" and item["reason"] == "budget_fallback"
        for item in payload["rejected"]
    )


def test_budget_rule_warn_keeps_selection_and_surfaces_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    created = client.post(
        "/api/v1/model-gateway/budget-rules",
        headers=headers,
        json={
            "id": "warn-codex-cost",
            "scopeType": "provider",
            "scopeId": "codex_cli",
            "maxCostUsd": 0.001,
            "period": "task",
            "actionOnExceed": "warn",
            "enabled": True,
        },
    )
    assert created.status_code == 201

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "contextTokensEstimate": 45000,
            "requiresCodeEdit": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected"]["provider"] == "codex_cli"
    assert payload["budgetResult"]["action"] == "warn"
    assert payload["budgetResult"]["warnings"] == ["budget_rule_exceeded"]


def test_pricing_catalog_marks_unknown_free_and_stale_prices(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            "UPDATE model_catalog SET source = ?, updated_at = ? WHERE provider_id = ? AND model = ?",
            ("manual_override", "2020-01-01T00:00:00Z", "codex_cli", "gpt-5.5"),
        )
        catalog = PricingCatalog(connection)

        free = catalog.estimate(
            provider_id="nvidia_nim",
            model="auto_best_available",
            input_tokens=1000,
            output_tokens=500,
        )
        unknown = catalog.estimate(
            provider_id="litellm",
            model="configured_model",
            input_tokens=1000,
            output_tokens=500,
        )
        stale = catalog.estimate(
            provider_id="codex_cli",
            model="gpt-5.5",
            input_tokens=1000,
            output_tokens=500,
        )

    assert free["estimatedCostUsd"] == 0.0
    assert free["freeTier"] is True
    assert free["priceKnown"] is True
    assert unknown["estimatedCostUsd"] is None
    assert unknown["freeTier"] is False
    assert unknown["priceKnown"] is False
    assert unknown["staleness"] == "unknown"
    assert stale["estimatedCostUsd"] is not None
    assert stale["staleness"] == "stale"


def test_route_preview_exposes_pricing_metadata_and_penalizes_unknown_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    enable_provider(client, headers, "openhands")

    response = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "contextTokensEstimate": 45000,
            "requiresCodeEdit": True,
            "requiresTools": True,
            "privacyLevel": "remote_allowed",
            "budgetRemainingUsd": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    openhands = next(item for item in payload["candidates"] if item["provider"] == "openhands")
    codex = next(item for item in payload["candidates"] if item["provider"] == "codex_cli")
    assert openhands["pricingStaleness"] == "unknown"
    assert openhands["pricingSource"] == "unknown_price"
    assert openhands["scoreBreakdown"]["costPenalty"] > 0
    assert codex["pricingStaleness"] in {"fresh", "stale", "unknown"}
    assert codex["pricingSource"]


def test_discover_models_uses_mock_by_default_and_records_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)

    response = client.post("/api/v1/model-gateway/providers/nvidia_nim/discover-models", headers=headers)

    assert response.status_code == 200
    models = response.json()["models"]
    assert any(item["providerId"] == "nvidia_nim" for item in models)
    with client:
        runtime = client.app.state.runtime  # type: ignore[attr-defined]
        audits = runtime.events.list_audit_events()
    discovery_audit = next(
        item for item in audits if item["action"] == "model_gateway.provider.models_discovered"
    )
    assert discovery_audit["payload"]["mock"] is True
    assert discovery_audit["payload"]["source"] == "mock"


def test_discover_models_rejects_real_api_provider_without_configured_credential_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_list_models(self: OpenAICompatibleProvider) -> list[ModelInfo]:
        raise AssertionError("network-backed discovery should not run without credential")

    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fail_list_models)
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(
        client,
        headers,
        "openai_compatible",
        baseUrl="https://example.invalid/v1",
        credentialRef="env:AIDO_MISSING_PROVIDER_KEY",
    )

    response = client.post("/api/v1/model-gateway/providers/openai_compatible/discover-models", headers=headers)

    assert response.status_code == 400
    assert "Credential ref env:AIDO_MISSING_PROVIDER_KEY is missing" in response.json()["detail"]


def test_discover_models_real_mode_stores_provider_sourced_models_without_real_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_list_models(self: OpenAICompatibleProvider) -> list[ModelInfo]:
        return [
            ModelInfo(
                providerId="openai_compatible",
                model="provider-discovered-model",
                displayName="Provider discovered model",
                contextWindow=64000,
                maxOutputTokens=4096,
                supportsTools=True,
                supportsJson=True,
                supportsStreaming=True,
                supportsReasoning=True,
                source="provider",
            )
        ]

    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("AIDO_TEST_PROVIDER_KEY", "sk-test-discovery123456")
    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fake_list_models)
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(
        client,
        headers,
        "openai_compatible",
        baseUrl="https://example.invalid/v1",
        credentialRef="env:AIDO_TEST_PROVIDER_KEY",
    )

    response = client.post("/api/v1/model-gateway/providers/openai_compatible/discover-models", headers=headers)

    assert response.status_code == 200
    discovered = next(item for item in response.json()["models"] if item["model"] == "provider-discovered-model")
    assert discovered["source"] == "provider"
    assert discovered["supportsTools"] is True
    with client:
        runtime = client.app.state.runtime  # type: ignore[attr-defined]
        audits = runtime.events.list_audit_events()
    discovery_audit = next(
        item for item in audits if item["action"] == "model_gateway.provider.models_discovered"
    )
    assert discovery_audit["payload"]["mock"] is False
    assert discovery_audit["payload"]["source"] == "provider"


def test_pricing_snapshot_api_records_redacted_append_only_snapshot_and_updates_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/pricing-snapshots",
        headers=headers,
        json={
            "providerId": "openai_compatible",
            "model": "configured_model",
            "inputPricePerMtok": 0.42,
            "cachedInputPricePerMtok": 0.11,
            "outputPricePerMtok": 1.23,
            "reasoningPricePerMtok": 2.34,
            "freeTier": False,
            "sourceRef": "manual-import Bearer sk-pricing-secret123456",
            "effectiveAt": "2026-05-24T00:00:00Z",
            "metadata": {"operatorToken": "Bearer sk-pricing-secret123456", "source": "vendor-sheet"},
            "applyToCatalog": True,
        },
    )
    listed = client.get("/api/v1/model-gateway/pricing-snapshots")
    catalog = client.get("/api/v1/model-gateway/models")

    assert created.status_code == 201
    snapshot = created.json()["pricingSnapshot"]
    assert snapshot["providerId"] == "openai_compatible"
    assert snapshot["inputPricePerMtok"] == 0.42
    assert snapshot["sourceRef"] == "manual-import [redacted]"
    assert snapshot["metadata"]["operatorToken"] == "[redacted]"
    assert "sk-pricing-secret" not in str(snapshot)
    assert listed.status_code == 200
    assert any(item["id"] == snapshot["id"] for item in listed.json()["pricingSnapshots"])
    updated_model = next(
        item
        for item in catalog.json()["models"]
        if item["providerId"] == "openai_compatible" and item["model"] == "configured_model"
    )
    assert updated_model["inputPricePerMtok"] == 0.42
    assert updated_model["outputPricePerMtok"] == 1.23
    assert updated_model["source"].startswith("pricing_snapshot:")


def test_phase14_schema_adds_pricing_snapshots(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 14 in migrations
    assert "pricing_snapshots" in tables


def test_benchmark_routing_ignores_insufficient_data_in_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    enable_provider(client, headers, "openhands")
    for index in range(2):
        created = client.post(
            "/api/v1/model-gateway/benchmark-outcomes",
            headers=headers,
            json={
                "providerId": "openhands",
                "model": "auto",
                "runtimeType": "cli",
                "role": "developer",
                "taskId": f"bench-small-{index}",
                "success": True,
                "qaPass": True,
                "rework": False,
                "estimatedCostUsd": 0.05,
                "latencyMs": 500,
            },
        )
        assert created.status_code == 201

    response = client.post(
        "/api/v1/model-gateway/route/preview",
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

    candidate = next(item for item in response.json()["candidates"] if item["provider"] == "openhands")
    assert candidate["scoreBreakdown"]["benchmarkSampleCount"] == 2
    assert candidate["scoreBreakdown"]["benchmarkInsufficientData"] == 1.0
    assert candidate["scoreBreakdown"]["benchmarkScore"] == 0.0


def test_benchmark_routing_uses_sufficient_data_without_overriding_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    enable_provider(client, headers, "openhands")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        manager = QuotaManager(connection)
        manager.record_rate_limit(provider_id="openhands", model="auto", retry_after_seconds=120)

    for index in range(5):
        created = client.post(
            "/api/v1/model-gateway/benchmark-outcomes",
            headers=headers,
            json={
                "providerId": "openhands",
                "model": "auto",
                "runtimeType": "cli",
                "role": "developer",
                "taskId": f"bench-enough-{index}",
                "success": True,
                "qaPass": True,
                "rework": False,
                "estimatedCostUsd": 0.05,
                "latencyMs": 500,
            },
        )
        assert created.status_code == 201

    response = client.post(
        "/api/v1/model-gateway/route/preview",
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
    payload = response.json()
    rejected = next(item for item in payload["rejected"] if item["provider"] == "openhands")

    assert rejected["reason"] == "provider_in_cooldown"
    assert payload["selected"]["provider"] != "openhands"


def test_provider_health_real_mode_requires_explicit_env_and_uses_real_adapter_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("OPENAI_COMPATIBLE_TEST_KEY", "sk-test-health")
    response = client.patch(
        "/api/v1/model-gateway/providers/openai_compatible",
        headers=headers,
        json={
            "enabled": True,
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "env:OPENAI_COMPATIBLE_TEST_KEY",
        },
    )
    assert response.status_code == 200

    health = client.post(
        "/api/v1/model-gateway/providers/openai_compatible/health-check",
        headers=headers,
    )

    assert health.status_code == 200
    assert health.json()["health"]["status"] == "available"
    assert health.json()["health"]["healthStatus"] == "healthy"


def test_provider_health_429_records_cooldown_and_redacts_last_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "nvidia_nim")

    def rate_limited_health(self: object) -> ProviderHealth:
        return ProviderHealth(
            providerId="nvidia_nim",
            status="rate_limited",
            healthStatus="degraded",
            message="provider returned 429",
            lastError="Bearer sk-test-provider-secret",
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.nvidia_nim.NvidiaNimProvider.health_check",
        rate_limited_health,
    )

    health = client.post("/api/v1/model-gateway/providers/nvidia_nim/health-check", headers=headers)
    provider = client.get("/api/v1/model-gateway/providers/nvidia_nim").json()["provider"]
    limits = client.get("/api/v1/model-gateway/provider-limits").json()["providerLimits"]
    nvidia_limit = next(
        item
        for item in limits
        if item["providerId"] == "nvidia_nim" and item["model"] == "auto_best_available"
    )

    assert health.status_code == 200
    assert health.json()["health"]["healthStatus"] == "degraded"
    assert "sk-test" not in provider["lastError"]
    assert "Bearer" not in provider["lastError"]
    assert nvidia_limit["cooldownUntil"]


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
    assert result.as_dict()["cooldownUntil"]


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
    assert estimated["usageSource"] == "estimated"
    assert actual["actualCostUsd"] == 0
    assert actual["totalTokens"] == 30
    assert actual["usageSource"] == "actual"


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


def test_cli_runtime_persists_mock_session_and_usage(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        runtime = CodexCliRuntime(executable="codex", mock=True, connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="Summarize status without secrets",
                profile="codex_gpt55_developer",
                model="gpt-5.5",
                envPolicy={"OPENAI_API_KEY": "sk-cli-secret123456", "network": False},
                mock=True,
            )
        )
        sessions = connection.execute("SELECT * FROM cli_sessions").fetchall()
        ledger = connection.execute("SELECT * FROM usage_ledger").fetchall()

    assert result.status == "completed"
    assert len(sessions) == 1
    assert sessions[0]["runtime"] == "codex_cli"
    assert "sk-cli-secret" not in sessions[0]["env_policy_json"]
    assert sessions[0]["usage_ledger_id"] is not None
    assert len(ledger) == 1
    assert ledger[0]["runtime_type"] == "cli"
    assert ledger[0]["usage_source"] == "estimated"


def test_cli_session_store_writes_redacted_stdout_stderr_and_log_artifacts(tmp_path: Path) -> None:
    secret = "Bearer sk-test1234567890abcdef"
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        session = CliSessionStore(connection).record_result(
            runtime="codex_cli",
            executable="codex",
            workspace_id="workspace-cli-test",
            command=["codex", "exec", secret],
            env_policy={"Authorization": secret, "network": False},
            status="failed",
            stdout=f"stdout contains {secret}",
            stderr=f"stderr contains {secret}",
            error=f"failed with {secret}",
        )
        artifacts = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id, kind, path, hash, metadata FROM artifacts ORDER BY created_at ASC"
            ).fetchall()
        }

    assert session["stdout_artifact_id"] in artifacts
    assert session["stderr_artifact_id"] in artifacts
    assert session["logs_artifact_id"] in artifacts
    assert artifacts[session["stdout_artifact_id"]]["hash"]
    assert artifacts[session["stderr_artifact_id"]]["hash"]
    assert artifacts[session["logs_artifact_id"]]["hash"]
    for artifact in artifacts.values():
        content = Path(artifact["path"]).read_text(encoding="utf-8")
        metadata = artifact["metadata"]
        assert "sk-test" not in content
        assert "Bearer" not in content
        assert "sk-test" not in metadata
        assert "Bearer" not in metadata
        assert str(tmp_path / ".tmp" / "evidence-artifacts") in artifact["path"]
        assert '"runtime": "codex_cli"' in metadata


def test_cli_mock_runtime_links_stdout_and_logs_artifacts(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        runtime = CodexCliRuntime(executable="codex", mock=True, connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="Summarize status without secrets",
                profile="codex_gpt55_developer",
                model="gpt-5.5",
                mock=True,
            )
        )
        session = connection.execute("SELECT * FROM cli_sessions").fetchone()
        stdout_artifact = connection.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (session["stdout_artifact_id"],),
        ).fetchone()
        logs_artifact = connection.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (session["logs_artifact_id"],),
        ).fetchone()

    assert result.status == "completed"
    assert session["stdout_artifact_id"]
    assert session["logs_artifact_id"]
    assert stdout_artifact is not None
    assert logs_artifact is not None


def test_cli_runtime_records_dangerous_flags_rejection_without_execution(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        runtime = CodexCliRuntime(executable="codex", mock=True, connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="edit code",
                profile="codex_gpt55_developer",
                extraArgs=["--dangerously-bypass-approvals-and-sandbox"],
                mock=True,
            )
        )
        session = connection.execute("SELECT * FROM cli_sessions").fetchone()
        ledger = connection.execute("SELECT * FROM usage_ledger").fetchone()

    assert result.status == "blocked"
    assert "dangerous" in (result.error or "")
    assert session["status"] == "blocked"
    assert session["logs_artifact_id"]
    assert ledger["usage_source"] == "estimated"


def test_cli_runtime_records_invalid_workspace_without_execution(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        runtime = CodexCliRuntime(executable="codex", connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path / "missing-workspace"),
                prompt="edit code",
                profile="codex_gpt55_developer",
            )
        )
        session = connection.execute("SELECT * FROM cli_sessions").fetchone()

    assert result.status == "blocked"
    assert "workspace_path" in (result.error or "")
    assert session["status"] == "blocked"
    assert session["logs_artifact_id"]


def test_cli_runtime_records_policy_denied_without_sandbox_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")

    def fail_execute(*args: object, **kwargs: object) -> None:
        raise AssertionError("sandbox should not execute when policy denies")

    monkeypatch.setattr("local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute", fail_execute)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            INSERT INTO workspaces
                (id, project_id, task_id, owner_agent_id, path, status, isolation_type, metadata,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "workspace-cli-test",
                "project-cli",
                "task-cli",
                "agent-cli",
                str(tmp_path),
                "active",
                "filesystem",
                "{}",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        runtime = CodexCliRuntime(executable="codex", connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="edit code",
                profile="codex_gpt55_developer",
                envPolicy={"permissionProfile": "plan"},
            )
        )
        session = connection.execute("SELECT * FROM cli_sessions").fetchone()

    assert result.status == "blocked"
    assert "Plan profile" in (result.error or "")
    assert session["status"] == "blocked"
    assert "policyResult" in session["env_policy_json"]


def test_agent_profiles_accept_expanded_executable_role_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    for role in ("technical_lead_shadow", "backend_engineer", "frontend_engineer"):
        response = client.post(
            "/api/v1/agent-profiles",
            headers=headers,
            json={
                "id": f"agent-{role}",
                "name": role,
                "role": role,
                "runtimeMode": "internal_mock",
                "permissionProfile": "dev_safe",
            },
        )
        assert response.status_code == 201
        assert response.json()["agentProfile"]["role"] == role


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
