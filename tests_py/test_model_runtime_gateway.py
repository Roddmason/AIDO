from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.agents.cli_runtimes.base import RuntimeRequest
from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider
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
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        migrations = {row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()}
        seeded_roles = {row["role"] for row in connection.execute("SELECT role FROM role_model_policies").fetchall()}
        seeded_profiles = {row["name"] for row in connection.execute("SELECT name FROM routing_profiles").fetchall()}
        seeded_providers = {row["provider_id"] for row in connection.execute("SELECT provider_id FROM provider_accounts").fetchall()}

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
    assert {"free_first", "cost_controlled", "balanced_best_value", "max_performance", "manual_by_profile", "local_private"} <= seeded_profiles
    assert {"internal_mock", "nvidia_nim", "ollama", "codex_cli", "claude_code_cli", "manual"} <= seeded_providers


def test_provider_accounts_crud_endpoints_do_not_expose_raw_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert payload["credentialRef"] == "TEST_COMPATIBLE_API_KEY"
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

    patched = client.patch(f"/api/v1/model-gateway/models/{model_id}", headers=headers, json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["model"]["enabled"] is False

    listed = client.get("/api/v1/model-gateway/models")
    assert listed.status_code == 200
    assert any(item["model"] == "nvidia/test-model" for item in listed.json()["models"])


def test_routing_profiles_and_role_policy_seeds_are_exposed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_client(tmp_path, monkeypatch)
    profiles = client.get("/api/v1/model-gateway/routing-profiles")
    policies = client.get("/api/v1/model-gateway/role-policies")

    assert profiles.status_code == 200
    assert policies.status_code == 200
    assert any(item["name"] == "balanced_best_value" for item in profiles.json()["routingProfiles"])
    analyst = next(item for item in policies.json()["rolePolicies"] if item["role"] == "analyst")
    assert analyst["maxCostPerTaskUsd"] == 0.10
    assert analyst["allowApi"] is True


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


def test_route_preview_local_private_blocks_remote_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        manager.record_rate_limit(provider_id="nvidia_nim", model="auto_best_available", retry_after_seconds=120)

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
        quota = QuotaManager(connection).check(provider_id="nvidia_nim", model="auto_best_available", request_tokens=1)
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
        json={"role": "analyst", "taskType": "doc_summary", "mode": "free_first", "privacyLevel": "remote_allowed"},
    )

    assert detect.status_code == 200
    assert health.status_code == 200
    assert discover.status_code == 200
    assert mock_execute.status_code == 200
