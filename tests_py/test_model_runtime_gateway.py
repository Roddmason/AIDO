from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.cli_runtimes.base import RuntimeRequest, RuntimeResult
from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime
from local_control_center.agents.cli_runtimes.openhands import OpenHandsRuntime
from local_control_center.agents.cli_runtimes.swe_agent import SweAgentRuntime
from local_control_center.agents.cli_sessions import CliSessionStore
from local_control_center.agents.credential_preflight import run_credential_preflight
from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.model_benchmarks import ModelBenchmarkStore
from local_control_center.agents.model_gateway import ModelGateway, provider_instance
from local_control_center.agents.model_gateway_api import _run_provider_test_prompt
from local_control_center.agents.pricing_catalog import PricingCatalog
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.anthropic_api import AnthropicAPIProvider
from local_control_center.agents.providers.base import ModelInfo, ModelRequest, ProviderHealth
from local_control_center.agents.providers.nvidia_nim import NvidiaNimProvider
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.usage_ledger import UsageLedger
from local_control_center.app import create_app
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import _execute_atomic_statements, initialize_platform_schema
from local_control_center.shared.redaction import redact_secrets
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.evidence_helpers import real_qa_evidence_fields


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def enable_runtime_policy(
    connection,
    *,
    cli: bool = False,
    remote: bool = False,
    nvidia: bool = False,
) -> None:
    repo = RuntimeConfigRepository(connection)
    if cli:
        repo.set_runtime_setting("runtime.cli.enabled", True)
    if remote:
        repo.set_runtime_setting("runtime.remote.enabled", True)
    if nvidia:
        repo.set_runtime_setting("runtime.nvidia.enabled", True)


def create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    return TestClient(app)


def enable_provider(client: TestClient, headers: dict[str, str], provider_id: str, **extra: object) -> None:
    health_status = str(extra.pop("healthStatus", "healthy"))
    health_message = str(extra.pop("lastError", "") or "Test helper recorded provider health.")
    response = client.patch(
        f"/api/v1/model-gateway/providers/{provider_id}",
        json={
            "enabled": True,
            **extra,
        },
        headers=headers,
    )
    assert response.status_code == 200
    with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
        enable_runtime_policy(connection, remote=True, nvidia=provider_id == "nvidia_nim")
        ProviderAccountStore(connection).record_health_check(
            provider_id=provider_id,
            status="available" if health_status == "healthy" else health_status,
            payload={"healthStatus": health_status, "message": health_message},
        )


def register_workspace(connection, workspace_id: str, path: Path) -> None:
    enable_runtime_policy(connection, cli=True)
    connection.execute(
        """
        INSERT INTO workspaces
            (id, project_id, task_id, owner_agent_id, path, status, isolation_type, metadata,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            "project-cli",
            "task-cli",
            "agent-cli",
            str(path),
            "active",
            "filesystem",
            "{}",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )


class JsonGatewayHandler(BaseHTTPRequestHandler):
    response_payload: dict[str, object] = {}
    get_response_payloads: dict[str, dict[str, object]] = {}
    seen_requests: list[dict[str, object]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        type(self).seen_requests.append(
            {
                "method": "GET",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "x_api_key": self.headers.get("x-api-key"),
                "anthropic_version": self.headers.get("anthropic-version"),
                "body": {},
            }
        )
        if self.path in type(self).get_response_payloads:
            self._send_json(type(self).get_response_payloads[self.path])
            return
        if self.path == "/api/tags":
            self._send_json({"models": [{"name": "llama3:latest"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(length).decode("utf-8")
        body = json.loads(raw_body) if raw_body else {}
        type(self).seen_requests.append(
            {
                "method": "POST",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "x_api_key": self.headers.get("x-api-key"),
                "anthropic_version": self.headers.get("anthropic-version"),
                "body": body,
            }
        )
        self._send_json(type(self).response_payload)

    def _send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_json_gateway_server(payload: dict[str, object]) -> tuple[str, type[JsonGatewayHandler], HTTPServer]:
    handler = type(
        "GatewayTestHandler",
        (JsonGatewayHandler,),
        {"response_payload": payload, "get_response_payloads": {}, "seen_requests": []},
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", handler, server


def run_anthropic_gateway_server(
    *,
    message_payload: dict[str, object],
    models_payload: dict[str, object],
) -> tuple[str, type[JsonGatewayHandler], HTTPServer]:
    handler = type(
        "AnthropicGatewayTestHandler",
        (JsonGatewayHandler,),
        {
            "response_payload": message_payload,
            "get_response_payloads": {"/v1/models": models_payload},
            "seen_requests": [],
        },
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", handler, server


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
        model_call_columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(model_calls)").fetchall()
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
        "nvidia_nim",
        "ollama",
        "codex_cli",
        "claude_code_cli",
        "manual",
    } <= seeded_providers
    assert "metadata_json" in provider_columns
    assert "usage_source" in usage_columns
    assert model_call_columns["cost_usd"]["notnull"] == 0


def test_model_calls_cost_migration_preserves_existing_rows_and_accepts_unknown_cost(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        connection.executescript(
            """
            CREATE TABLE model_calls (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                agent_run_id TEXT,
                model_policy_id TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO model_calls
                (id, project_id, agent_run_id, model_policy_id, provider, model, status,
                 prompt_tokens, completion_tokens, cost_usd, metadata, created_at)
            VALUES
                ('model-call-existing', 'project-gateway', NULL, NULL, 'openai_compatible',
                 'configured_model', 'completed', 10, 5, 0.012, '{}', '2026-01-01T00:00:00Z'),
                ('model-call-unknown', 'project-gateway', NULL, NULL, 'nvidia_nim',
                 'unknown-model', 'completed', 0, 0, 0,
                 '{"tokenStatus":"unknown","costStatus":"unknown"}', '2026-01-01T00:00:01Z'),
                ('model-call-actual-zero', 'project-gateway', NULL, NULL, 'ollama',
                 'free-model', 'completed', 0, 0, 0,
                 '{"tokenStatus":"actual","costStatus":"actual"}', '2026-01-01T00:00:02Z'),
                ('model-call-failed', 'project-gateway', NULL, NULL, 'openai_compatible',
                 'failed-model', 'failed', 3, 2, 0.01, '{}', '2026-01-01T00:00:03Z'),
                ('model-call-invalid-metadata', 'project-gateway', NULL, NULL, 'openai_compatible',
                 'legacy-model', 'completed', 4, 2, 0.02, '{invalid', '2026-01-01T00:00:04Z');
            """
        )

        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(model_calls)").fetchall()
        }
        existing = connection.execute(
            """
            SELECT id, prompt_tokens, completion_tokens, cost_usd
            FROM model_calls WHERE id = 'model-call-existing'
            """
        ).fetchone()
        unknown = connection.execute(
            "SELECT prompt_tokens, completion_tokens, cost_usd FROM model_calls WHERE id = 'model-call-unknown'"
        ).fetchone()
        actual_zero = connection.execute(
            "SELECT prompt_tokens, completion_tokens, cost_usd FROM model_calls WHERE id = 'model-call-actual-zero'"
        ).fetchone()
        failed = connection.execute(
            "SELECT prompt_tokens, completion_tokens, cost_usd FROM model_calls WHERE id = 'model-call-failed'"
        ).fetchone()
        invalid_metadata = connection.execute(
            "SELECT prompt_tokens, completion_tokens, cost_usd FROM model_calls WHERE id = 'model-call-invalid-metadata'"
        ).fetchone()
        created = AgentsRepository(connection).record_model_call(
            project_id="project-gateway",
            provider="anthropic_api",
            model="claude-test-model",
            status="completed",
            prompt_tokens=11,
            completion_tokens=5,
            cost_usd=None,
        )

    assert columns["cost_usd"]["notnull"] == 0
    assert columns["prompt_tokens"]["notnull"] == 0
    assert columns["completion_tokens"]["notnull"] == 0
    assert existing["cost_usd"] == 0.012
    assert existing["prompt_tokens"] == 10
    assert existing["completion_tokens"] == 5
    assert tuple(unknown) == (None, None, None)
    assert tuple(actual_zero) == (0, 0, 0)
    assert tuple(failed) == (None, None, None)
    assert tuple(invalid_metadata) == (4, 2, 0.02)
    assert created["costUsd"] is None


def test_usage_token_migration_preserves_reported_values_and_nulls_unknown_zeros(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        connection.executescript(
            """
            CREATE TABLE usage_ledger (
                id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                model TEXT NOT NULL,
                runtime_type TEXT NOT NULL,
                agent_id TEXT,
                role TEXT,
                workflow_run_id TEXT,
                workflow_step_id TEXT,
                job_id TEXT,
                task_id TEXT,
                request_id TEXT,
                session_id TEXT,
                input_tokens INTEGER NOT NULL,
                cached_input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                reasoning_tokens INTEGER NOT NULL,
                tool_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                currency TEXT NOT NULL,
                latency_ms INTEGER,
                raw_usage_json TEXT NOT NULL,
                usage_source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO usage_ledger
                (id, provider_id, model, runtime_type, input_tokens, cached_input_tokens,
                 output_tokens, reasoning_tokens, tool_tokens, total_tokens, currency,
                 raw_usage_json, usage_source, created_at)
            VALUES
                ('usage-actual', 'ollama', 'reported', 'ollama', 10, 0, 5, 0, 0, 15,
                 'USD', '{"usage_source":"provider"}', 'actual', '2026-01-01T00:00:00Z'),
                ('usage-unknown', 'nvidia_nim', 'unreported', 'api', 0, 0, 0, 0, 0, 0,
                 'USD', '{"usage_source":"unknown"}', 'unknown', '2026-01-01T00:00:01Z');
            """
        )

        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(usage_ledger)").fetchall()
        }
        actual = connection.execute(
            "SELECT input_tokens, output_tokens, total_tokens FROM usage_ledger WHERE id = 'usage-actual'"
        ).fetchone()
        unknown = connection.execute(
            "SELECT input_tokens, output_tokens, total_tokens FROM usage_ledger WHERE id = 'usage-unknown'"
        ).fetchone()
        migration = connection.execute("SELECT 1 FROM schema_migrations WHERE version = 50").fetchone()
        index = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_usage_ledger_provider_created'"
        ).fetchone()

    assert all(columns[name]["notnull"] == 0 for name in ("input_tokens", "output_tokens", "total_tokens"))
    assert tuple(actual) == (10, 5, 15)
    assert tuple(unknown) == (None, None, None)
    assert migration is not None
    assert index is not None


def test_schema_rebuild_rolls_back_ddl_and_data_on_failure(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        connection.execute("CREATE TABLE source_rows (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO source_rows (id) VALUES ('preserved')")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            _execute_atomic_statements(
                connection,
                [
                    ("CREATE TABLE staged_rows AS SELECT * FROM source_rows", ()),
                    ("DROP TABLE source_rows", ()),
                    ("INSERT INTO missing_table (id) VALUES ('fail')", ()),
                ],
            )

        preserved = connection.execute("SELECT id FROM source_rows").fetchone()
        staged = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'staged_rows'"
        ).fetchone()

    assert preserved["id"] == "preserved"
    assert staged is None


def test_provider_test_prompt_keeps_missing_usage_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.model_gateway_api.provider_instance",
        lambda *_args, **_kwargs: SimpleNamespace(
            chat_completion=lambda _request: SimpleNamespace(
                content="ok",
                usage=SimpleNamespace(
                    total_tokens=0,
                    raw_usage={"usage_source": "provider"},
                ),
            )
        ),
    )

    result = _run_provider_test_prompt("ollama", "test-model", connection=object())

    assert result["ok"] is True
    assert result["totalTokens"] is None
    assert result["usageSource"] == "unknown"


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
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(model_benchmark_outcomes)").fetchall()
        }

    assert 13 in migrations
    assert "model_benchmark_outcomes" in tables
    assert "provenance" in columns


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
        json={"enabled": True, "metadata": {"lastErrorSample": "Authorization: Bearer sk-testsecret123456"}},
    )
    assert patched.status_code == 200
    assert patched.json()["provider"]["enabled"] is True
    assert "[redacted]" in patched.json()["provider"]["metadata"]["lastErrorSample"]

    listed = client.get("/api/v1/model-gateway/providers")
    assert listed.status_code == 200
    assert any(item["providerId"] == "test_compatible" for item in listed.json()["providers"])


def test_provider_account_requests_cannot_write_health_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "client_health_provider",
            "displayName": "Client Health Provider",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": "https://example.invalid/v1",
            "credentialRef": "CLIENT_HEALTH_PROVIDER_API_KEY",
            "enabled": True,
            "healthStatus": "healthy",
            "lastHealthCheckAt": "2026-01-01T00:00:00Z",
            "lastError": "client supplied health must not persist",
        },
    )
    assert created.status_code == 201
    provider = created.json()["provider"]
    assert provider["healthStatus"] == "unknown"
    assert provider["lastHealthCheckAt"] is None
    assert provider["lastError"] == ""

    patched = client.patch(
        "/api/v1/model-gateway/providers/client_health_provider",
        headers=headers,
        json={
            "healthStatus": "healthy",
            "lastHealthCheckAt": "2026-01-01T00:00:00Z",
            "lastError": "still client supplied",
        },
    )
    assert patched.status_code == 200
    provider = patched.json()["provider"]
    assert provider["healthStatus"] == "unknown"
    assert provider["lastHealthCheckAt"] is None
    assert provider["lastError"] == ""


def test_provider_account_config_change_resets_server_owned_health(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.patch_provider_account(
            "openai_compatible",
            {"enabled": True, "baseUrl": "https://old.example.invalid/v1", "credentialRef": "env:OPENAI_KEY"},
        )
        healthy = store.record_health_check(
            provider_id="openai_compatible",
            status="available",
            payload={"healthStatus": "healthy", "message": "health check passed"},
        )
        assert healthy["status"] == "available"

        provider = store.patch_provider_account(
            "openai_compatible",
            {"baseUrl": "https://new.example.invalid/v1"},
        )

    assert provider["healthStatus"] == "unknown"
    assert provider["lastHealthCheckAt"] is None
    assert provider["lastError"] == ""


def test_prepare_model_call_preserves_unknown_estimated_cost(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        policy = AgentsRepository(connection).upsert_model_policy(
            {
                "id": "unknown_cost_policy",
                "preferred": [{"provider": "openai_compatible", "model": "configured_model"}],
                "fallback": [],
            }
        )

        result = ModelGateway(connection).prepare_model_call(
            project_id="project-unknown-cost",
            model_policy_id=policy["id"],
        )

    assert result["status"] == "planned"
    metadata = result["modelCall"]["metadata"]
    assert metadata["costStatus"] == "unknown"
    assert metadata["estimatedCostUsd"] is None


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


def test_known_openai_compatible_provider_uses_default_base_url_without_manual_entry(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "deepseek",
                "displayName": "DeepSeek",
                "providerType": "api",
                "apiFormat": "openai_compatible",
                "baseUrl": "",
                "credentialRef": "env:DEEPSEEK_API_KEY",
                "enabled": True,
            }
        )
        store.upsert_provider_account(
            {
                "providerId": "custom_gateway",
                "displayName": "Custom gateway",
                "providerType": "api",
                "apiFormat": "openai_compatible",
                "baseUrl": "",
                "credentialRef": "env:CUSTOM_GATEWAY_API_KEY",
                "enabled": True,
            }
        )

        known_provider = provider_instance("deepseek", connection=connection)
        custom_provider = provider_instance("custom_gateway", connection=connection)

    assert known_provider.base_url == "https://api.deepseek.com"
    assert custom_provider.base_url == ""


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
    assert health.json()["health"]["healthStatus"] == "configuration_required"


def test_openai_compatible_provider_uses_credential_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIDO_PROVIDER_SECRET", "sk-testsecret123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")

    provider = OpenAICompatibleProvider(
        provider_id="test_resolver",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_PROVIDER_SECRET",
    )
    provider.base_url = ""

    assert provider._credential() == "sk-testsecret123456"
    health = provider.health_check()
    assert health.status == "misconfigured"
    assert "sk-testsecret" not in health.message


def test_model_gateway_blocks_unconfigured_openai_compatible_before_http_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_MISSING_KEY", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    base_url, handler, server = run_json_gateway_server({"unexpected": True})
    try:
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            initialize_platform_schema(connection)
            enable_runtime_policy(connection, remote=True)
            ProviderAccountStore(connection).upsert_provider_account(
                {
                    "providerId": "openai_compatible",
                    "providerType": "api",
                    "apiFormat": "openai_compatible",
                    "baseUrl": f"{base_url}/v1",
                    "credentialRef": "env:MODEL_GATEWAY_MISSING_KEY",
                    "enabled": True,
                    "healthStatus": "healthy",
                    "lastHealthCheckAt": "2026-01-01T00:00:00Z",
                }
            )

            plan = ModelGateway(connection).plan_model_call(
                project_id="project-gateway",
                provider="openai_compatible",
                model="configured_model",
                runtime_type="api",
                messages=[{"role": "user", "content": "hello"}],
            )
            result = ModelGateway(connection).execute_model_call(plan)

        assert result["status"] == "configuration_required"
        assert "credential" in result["reason"].lower()
        assert handler.seen_requests == []
    finally:
        server.shutdown()


def test_model_gateway_redacts_secrets_from_unavailable_provider_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-redactgateway123456"
    monkeypatch.setenv("MODEL_GATEWAY_SECRET_KEY", secret)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        enable_runtime_policy(connection, remote=True)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "openai_compatible",
                "providerType": "api",
                "apiFormat": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "credentialRef": "env:MODEL_GATEWAY_SECRET_KEY",
                "enabled": True,
                "healthStatus": "healthy",
                "lastHealthCheckAt": "2026-01-01T00:00:00Z",
            }
        )

        plan = ModelGateway(connection).plan_model_call(
            project_id="project-gateway",
            provider="openai_compatible",
            model="configured_model",
            runtime_type="api",
            messages=[{"role": "user", "content": "hello"}],
            metadata={"authorization": f"Bearer {secret}", "payload": {"apiKey": secret}},
        )
        result = ModelGateway(connection).execute_model_call(plan)

    serialized = json.dumps(result, sort_keys=True)
    assert result["status"] == "unavailable"
    assert secret not in serialized
    assert "[redacted]" in serialized


def test_model_gateway_budget_exceeded_blocks_before_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_chat_completion(self: OpenAICompatibleProvider, request: object) -> object:
        raise AssertionError("provider must not be called after a budget block")

    monkeypatch.setattr(OpenAICompatibleProvider, "chat_completion", fail_chat_completion)
    monkeypatch.setenv("MODEL_GATEWAY_BUDGET_KEY", "sk-budgetgateway123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "openai_compatible",
                "providerType": "api",
                "apiFormat": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "credentialRef": "env:MODEL_GATEWAY_BUDGET_KEY",
                "enabled": True,
                "healthStatus": "healthy",
                "lastHealthCheckAt": "2026-01-01T00:00:00Z",
            }
        )
        plan = ModelGateway(connection).plan_model_call(
            project_id="project-gateway",
            provider="openai_compatible",
            model="configured_model",
            runtime_type="api",
            messages=[{"role": "user", "content": "hello"}],
            budget_remaining_usd=0.01,
            estimated_cost_usd=0.02,
        )

        result = ModelGateway(connection).execute_model_call(plan)
        usage_count = connection.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0]

    assert result["status"] == "blocked_budget"
    assert result["reason"] == "budget_remaining_exceeded"
    assert usage_count == 0


def test_model_gateway_ollama_health_uses_configured_local_server(tmp_path: Path) -> None:
    base_url, _handler, server = run_json_gateway_server({})
    try:
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            initialize_platform_schema(connection)
            ProviderAccountStore(connection).upsert_provider_account(
                {
                    "providerId": "ollama",
                    "providerType": "local",
                    "apiFormat": "ollama",
                    "baseUrl": base_url,
                    "credentialRef": "",
                    "enabled": True,
                }
            )

            health = ModelGateway(connection).provider_health("ollama")

        assert health["status"] == "available"
        assert health["healthStatus"] == "healthy"
        assert health["models"] == ["llama3:latest"]
    finally:
        server.shutdown()


def test_model_gateway_endpoint_scoped_ollama_requires_explicit_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIDO_OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "ollama_remote",
                "providerType": "gateway",
                "apiFormat": "ollama",
                "baseUrl": "",
                "credentialRef": "",
                "enabled": True,
            }
        )

        provider = provider_instance("ollama_remote", connection=connection)
        health = ModelGateway(connection).provider_health("ollama_remote")

    assert provider.base_url == ""
    assert health["status"] == "configuration_required"
    assert "base URL" in health["message"]


def test_model_gateway_openai_compatible_executes_real_http_and_records_actual_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_REAL_KEY", "sk-realgateway123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    base_url, handler, server = run_json_gateway_server(
        {
            "id": "chatcmpl-test",
            "model": "configured_model",
            "choices": [{"message": {"role": "assistant", "content": "real provider response"}}],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        }
    )
    try:
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            initialize_platform_schema(connection)
            enable_runtime_policy(connection, remote=True)
            store = ProviderAccountStore(connection)
            store.upsert_provider_account(
                {
                    "providerId": "openai_compatible",
                    "providerType": "api",
                    "apiFormat": "openai_compatible",
                    "baseUrl": f"{base_url}/v1",
                    "credentialRef": "env:MODEL_GATEWAY_REAL_KEY",
                    "enabled": True,
                    "healthStatus": "healthy",
                    "lastHealthCheckAt": "2026-01-01T00:00:00Z",
                }
            )
            store.upsert_model(
                {
                    "providerId": "openai_compatible",
                    "model": "configured_model",
                    "enabled": True,
                    "inputPricePerMtok": 1.0,
                    "cachedInputPricePerMtok": 0.25,
                    "outputPricePerMtok": 2.0,
                    "reasoningPricePerMtok": 3.0,
                    "source": "unit_test_pricing",
                }
            )

            plan = ModelGateway(connection).plan_model_call(
                project_id="project-gateway",
                provider="openai_compatible",
                model="configured_model",
                runtime_type="api",
                messages=[{"role": "user", "content": "hello"}],
            )
            result = ModelGateway(connection).execute_model_call(plan)

        assert result["status"] == "completed"
        assert result["content"] == "real provider response"
        assert result["usage"]["inputTokens"] == 7
        assert result["usage"]["cachedInputTokens"] == 2
        assert result["usage"]["outputTokens"] == 5
        assert result["usage"]["totalTokens"] == 12
        assert result["usage"]["actualCostUsd"] == 0.000016
        assert result["usage"]["estimatedCostUsd"] is None
        assert result["usage"]["usageSource"] == "actual"
        assert result["usage"]["rawUsage"]["token_status"] == "actual"
        assert result["usage"]["rawUsage"]["cost_status"] == "actual"
        assert handler.seen_requests[0]["path"] == "/v1/chat/completions"
        assert handler.seen_requests[0]["authorization"] == "Bearer sk-realgateway123456"
    finally:
        server.shutdown()


def test_anthropic_provider_health_and_model_listing_use_real_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "sk-ant-health123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    base_url, handler, server = run_anthropic_gateway_server(
        message_payload={},
        models_payload={
            "data": [
                {
                    "id": "claude-test-model",
                    "display_name": "Claude Test Model",
                }
            ]
        },
    )
    try:
        provider = AnthropicAPIProvider(base_url=f"{base_url}/v1", credential_ref="env:ANTHROPIC_TEST_KEY")

        health = provider.health_check()
        models = provider.list_models()

        assert health.status == "available"
        assert health.health_status == "healthy"
        assert [model.model for model in models] == ["claude-test-model"]
        assert models[0].display_name == "Claude Test Model"
        assert handler.seen_requests[0]["path"] == "/v1/models"
        assert handler.seen_requests[0]["x_api_key"] == "sk-ant-health123456"
        assert handler.seen_requests[0]["anthropic_version"] == "2023-06-01"
    finally:
        server.shutdown()


def test_model_gateway_anthropic_executes_real_http_and_records_actual_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "sk-ant-gateway123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    base_url, handler, server = run_anthropic_gateway_server(
        message_payload={
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test-model",
            "content": [{"type": "text", "text": "real anthropic response"}],
            "usage": {
                "input_tokens": 11,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 3,
                "output_tokens": 5,
            },
        },
        models_payload={"data": []},
    )
    try:
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            initialize_platform_schema(connection)
            enable_runtime_policy(connection, remote=True)
            ProviderAccountStore(connection).upsert_provider_account(
                {
                    "providerId": "anthropic_api",
                    "providerType": "api",
                    "apiFormat": "anthropic",
                    "baseUrl": f"{base_url}/v1",
                    "credentialRef": "env:ANTHROPIC_TEST_KEY",
                    "enabled": True,
                    "healthStatus": "healthy",
                    "lastHealthCheckAt": "2026-01-01T00:00:00Z",
                }
            )

            plan = ModelGateway(connection).plan_model_call(
                project_id="project-anthropic",
                provider="anthropic_api",
                model="claude-test-model",
                runtime_type="api",
                messages=[
                    {"role": "system", "content": "Be concise."},
                    {"role": "user", "content": "hello"},
                ],
                max_tokens=128,
            )
            result = ModelGateway(connection).execute_model_call(plan)

        message_request = next(item for item in handler.seen_requests if item["method"] == "POST")
        assert result["status"] == "completed"
        assert result["content"] == "real anthropic response"
        assert result["usage"]["inputTokens"] == 11
        assert result["usage"]["cachedInputTokens"] == 5
        assert result["usage"]["outputTokens"] == 5
        assert result["usage"]["totalTokens"] == 21
        assert result["usage"]["estimatedCostUsd"] is None
        assert result["usage"]["actualCostUsd"] is None
        assert result["usage"]["usageSource"] == "actual"
        assert result["usage"]["rawUsage"]["usage_source"] == "provider"
        assert result["usage"]["rawUsage"]["token_status"] == "actual"
        assert result["usage"]["rawUsage"]["cost_status"] == "unknown"
        assert result["modelCall"]["promptTokens"] == 11
        assert result["modelCall"]["completionTokens"] == 5
        assert result["modelCall"]["costUsd"] is None
        assert message_request["path"] == "/v1/messages"
        assert message_request["x_api_key"] == "sk-ant-gateway123456"
        assert message_request["anthropic_version"] == "2023-06-01"
        assert message_request["body"]["model"] == "claude-test-model"
        assert message_request["body"]["max_tokens"] == 128
        assert message_request["body"]["system"] == "Be concise."
        assert message_request["body"]["messages"] == [{"role": "user", "content": "hello"}]
    finally:
        server.shutdown()


def test_anthropic_provider_without_usage_marks_tokens_unknown() -> None:
    provider = AnthropicAPIProvider(
        base_url="https://api.anthropic.com/v1", credential_ref="env:ANTHROPIC_TEST_KEY"
    )

    usage = provider.parse_usage({"content": [{"type": "text", "text": "text without usage"}]})

    assert usage.input_tokens == 0
    assert usage.cached_input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.total_tokens == 0
    assert usage.raw_usage == {
        "usage_source": "unknown",
        "reason": "provider_response_missing_usage",
    }


def test_nvidia_nim_without_provider_usage_does_not_invent_cost_or_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "sk-nvidiarealusage123456")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    base_url, _handler, server = run_json_gateway_server(
        {
            "id": "chatcmpl-nvidia-no-usage",
            "model": "auto_best_available",
            "choices": [{"message": {"role": "assistant", "content": "provider text without usage"}}],
        }
    )
    try:
        with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
            initialize_platform_schema(connection)
            enable_runtime_policy(connection, remote=True, nvidia=True)
            store = ProviderAccountStore(connection)
            store.upsert_provider_account(
                {
                    "providerId": "nvidia_nim",
                    "providerType": "api",
                    "apiFormat": "openai_compatible",
                    "baseUrl": f"{base_url}/v1",
                    "credentialRef": "env:NVIDIA_NIM_API_KEY",
                    "enabled": True,
                    "healthStatus": "healthy",
                    "lastHealthCheckAt": "2026-01-01T00:00:00Z",
                }
            )

            plan = ModelGateway(connection).plan_model_call(
                project_id="project-nvidia",
                provider="nvidia_nim",
                model="auto_best_available",
                runtime_type="api",
                messages=[{"role": "user", "content": "hello"}],
            )
            result = ModelGateway(connection).execute_model_call(plan)

        assert result["status"] == "completed"
        assert result["usage"]["inputTokens"] is None
        assert result["usage"]["outputTokens"] is None
        assert result["usage"]["totalTokens"] is None
        assert result["usage"]["estimatedCostUsd"] is None
        assert result["usage"]["actualCostUsd"] is None
        assert result["usage"]["usageSource"] == "unknown"
        assert result["usage"]["tokenStatus"] == "unknown"
        assert result["usage"]["costStatus"] == "unknown"
        assert result["usage"]["rawUsage"]["usage_source"] == "unknown"
        assert result["modelCall"]["promptTokens"] is None
        assert result["modelCall"]["completionTokens"] is None
        assert result["modelCall"]["costUsd"] is None
    finally:
        server.shutdown()


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
    assert analyst["allowUnknownCost"] is True
    assert analyst["requireApprovalForUnknownCost"] is True
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
    nvidia_candidate = next(
        item for item in response.json()["candidates"] if item["provider"] == "nvidia_nim"
    )
    assert nvidia_candidate["priceKnown"] is False
    assert nvidia_candidate["scoreBreakdown"]["costPenalty"] == 1.0
    assert response.json()["decisionReason"]
    assert response.json()["budgetResult"]["allowed"] is True
    assert response.json()["quotaResult"]["allowed"] is True
    assert response.json()["policyResult"]["requiresApproval"] is True
    assert response.json()["policyResult"]["unknownCostPolicy"] == {
        "action": "require_approval",
        "reason": "unknown_remote_cost_requires_approval",
        "provider": "nvidia_nim",
        "runtime": "api",
    }


def test_route_preview_rejects_remote_unknown_cost_when_role_policy_disallows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "nvidia_nim")
    enable_provider(client, headers, "ollama")

    patched_policy = client.patch(
        "/api/v1/model-gateway/role-policies/analyst",
        headers=headers,
        json={"allowUnknownCost": False, "requireApprovalForUnknownCost": True},
    )
    assert patched_policy.status_code == 200
    assert patched_policy.json()["rolePolicy"]["allowUnknownCost"] is False

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
    payload = response.json()
    assert payload["selected"]["provider"] == "ollama"
    assert payload["policyResult"]["requiresApproval"] is False
    assert any(
        item["provider"] == "nvidia_nim" and item["reason"] == "unknown_remote_cost_not_allowed"
        for item in payload["rejected"]
    )


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


def test_pricing_catalog_marks_nvidia_unknown_price_and_stale_prices(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            "UPDATE model_catalog SET source = ?, updated_at = ? WHERE provider_id = ? AND model = ?",
            ("manual_override", "2020-01-01T00:00:00Z", "codex_cli", "gpt-5.5"),
        )
        catalog = PricingCatalog(connection)

        nvidia_unknown = catalog.estimate(
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

    assert nvidia_unknown["estimatedCostUsd"] is None
    assert nvidia_unknown["freeTier"] is False
    assert nvidia_unknown["priceKnown"] is False
    assert nvidia_unknown["staleness"] == "unknown"
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


def test_discover_models_uses_sqlite_policy_when_env_flag_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "sk-test-discovery-disabled")
    enable_provider(client, headers, "nvidia_nim")

    response = client.post("/api/v1/model-gateway/providers/nvidia_nim/discover-models", headers=headers)

    assert response.status_code == 200
    assert "models" in response.json()


def test_router_rejects_enabled_seed_provider_without_real_healthcheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    response = client.patch(
        "/api/v1/model-gateway/providers/codex_cli",
        headers=headers,
        json={"enabled": True, "healthStatus": "healthy"},
    )
    assert response.status_code == 200

    preview = client.post(
        "/api/v1/model-gateway/route/preview",
        headers=headers,
        json={
            "role": "developer",
            "taskType": "implementation",
            "mode": "balanced_best_value",
            "contextTokensEstimate": 1000,
            "requiresCodeEdit": True,
            "requiresTools": True,
        },
    )

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["selected"] is None
    assert any(item["reason"] == "provider_healthcheck_required" for item in payload["rejected"])


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

    response = client.post(
        "/api/v1/model-gateway/providers/openai_compatible/discover-models", headers=headers
    )

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

    response = client.post(
        "/api/v1/model-gateway/providers/openai_compatible/discover-models", headers=headers
    )

    assert response.status_code == 200
    discovered = next(
        item for item in response.json()["models"] if item["model"] == "provider-discovered-model"
    )
    assert discovered["source"] == "provider"
    assert discovered["supportsTools"] is True
    with client:
        runtime = client.app.state.runtime  # type: ignore[attr-defined]
        audits = runtime.events.list_audit_events()
    discovery_audit = next(
        item for item in audits if item["action"] == "model_gateway.provider.models_discovered"
    )
    assert "mock" not in discovery_audit["payload"]
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
                "provenance": "automated_run",
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
                "provenance": "automated_run",
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


def test_operator_reported_benchmarks_are_separated_from_objective_routing_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_provider(client, headers, "codex_cli")
    enable_provider(client, headers, "openhands")
    for index in range(5):
        created = client.post(
            "/api/v1/model-gateway/benchmark-outcomes",
            headers=headers,
            json={
                "providerId": "openhands",
                "model": "auto",
                "runtimeType": "cli",
                "role": "developer",
                "taskId": f"operator-reported-{index}",
                "provenance": "operator_reported",
                "success": True,
                "qaPass": True,
                "rework": False,
                "estimatedCostUsd": 0.01,
                "latencyMs": 100,
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

    assert response.status_code == 200
    candidate = next(item for item in response.json()["candidates"] if item["provider"] == "openhands")
    breakdown = candidate["scoreBreakdown"]
    assert breakdown["benchmarkObjectiveSampleCount"] == 0.0
    assert breakdown["benchmarkOperatorReportedSampleCount"] == 5.0
    assert breakdown["benchmarkInsufficientData"] == 1.0
    assert breakdown["benchmarkScore"] == 0.0
    assert breakdown["benchmarkContribution"] == 0.0


def test_provider_health_real_mode_requires_explicit_env_and_uses_real_adapter_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def healthy_provider(self: OpenAICompatibleProvider) -> ProviderHealth:
        return ProviderHealth(
            providerId=self.provider_id,
            status="available",
            healthStatus="healthy",
            message="Test healthcheck reached configured provider.",
        )

    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("OPENAI_COMPATIBLE_TEST_KEY", "sk-test-health")
    monkeypatch.setattr(OpenAICompatibleProvider, "health_check", healthy_provider)
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
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "sk-test-nvidia-health")
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
            provider_id="openai_compatible",
            model="configured_model",
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
    assert estimated["inputTokens"] is None
    assert estimated["totalTokens"] is None
    assert actual["actualCostUsd"] == 0
    assert actual["totalTokens"] == 30
    assert actual["usageSource"] == "actual"


def test_usage_ledger_summary_preserves_unknown_actual_cost(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ledger = UsageLedger(connection)
        ledger.record_usage(
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

        unknown_summary = ledger.summary()

        ledger.record_usage(
            provider_id="openai_compatible",
            model="configured_model",
            runtime_type="api",
            role="qa",
            request_id="req-actual",
            input_tokens=20,
            output_tokens=10,
            estimated_cost_usd=0,
            actual_cost_usd=0,
            raw_usage={"usage_source": "provider"},
        )
        mixed_summary = ledger.summary()
    with open_sqlite_connection(tmp_path / "actual-only.sqlite") as connection:
        initialize_platform_schema(connection)
        ledger = UsageLedger(connection)
        ledger.record_usage(
            provider_id="openai_compatible",
            model="configured_model",
            runtime_type="api",
            role="qa",
            request_id="req-actual-only",
            input_tokens=20,
            output_tokens=10,
            estimated_cost_usd=0,
            actual_cost_usd=0,
            raw_usage={"usage_source": "provider"},
        )
        actual_summary = ledger.summary()

    assert unknown_summary["estimatedCostUsd"] == 0.001
    assert unknown_summary["actualCostUsd"] is None
    assert unknown_summary["totalTokens"] is None
    assert mixed_summary["estimatedCostUsd"] == 0.001
    assert mixed_summary["actualCostUsd"] is None
    assert mixed_summary["totalTokens"] is None
    assert actual_summary["estimatedCostUsd"] == 0
    assert actual_summary["actualCostUsd"] == 0
    assert actual_summary["totalTokens"] == 30


def test_usage_ledger_summary_does_not_present_known_cost_subtotals_as_complete(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ledger = UsageLedger(connection)
        ledger.record_usage(
            provider_id="ollama",
            model="known",
            runtime_type="ollama",
            input_tokens=4,
            output_tokens=2,
            estimated_cost_usd=0.01,
            actual_cost_usd=0.01,
            raw_usage={"usage_source": "actual"},
        )
        ledger.record_usage(
            provider_id="ollama",
            model="unknown",
            runtime_type="ollama",
            raw_usage={"usage_source": "unknown"},
            usage_source="unknown",
        )

        summary = ledger.summary()

    assert summary["estimatedCostUsd"] is None
    assert summary["actualCostUsd"] is None
    assert summary["byProvider"][0]["estimatedCostUsd"] is None


def test_route_execute_mock_endpoint_is_not_exposed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

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

    assert response.status_code == 404


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
        "provenance": "automated_run",
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
    assert benchmark["objectiveTasksAttempted"] == 1
    assert benchmark["operatorReportedTasks"] == 0
    assert benchmark["automatedRunTasks"] == 1
    assert benchmark["successRate"] == 1.0
    assert benchmark["qaPassRate"] == 1.0
    assert benchmark["reworkRate"] == 0.0
    assert benchmark["avgCost"] == 0.42
    assert benchmark["avgLatencyMs"] == 1200


def test_operator_reported_benchmark_outcomes_do_not_create_objective_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/benchmark-outcomes",
        headers=headers,
        json={
            "providerId": "codex_cli",
            "model": "gpt-5.5",
            "runtimeType": "cli",
            "role": "developer",
            "taskId": "operator-reported-benchmark",
            "success": True,
            "qaPass": True,
            "rework": False,
            "estimatedCostUsd": 0.42,
            "latencyMs": 1200,
            "metadata": {"source": "operator_console"},
        },
    )
    benchmarks = client.get("/api/v1/model-gateway/benchmarks")

    assert created.status_code == 201
    outcome = created.json()["outcome"]
    assert outcome["provenance"] == "operator_reported"
    benchmark = next(
        item
        for item in benchmarks.json()["benchmarks"]
        if item["providerId"] == "codex_cli" and item["model"] == "gpt-5.5"
    )
    assert benchmark["tasksAttempted"] == 1
    assert benchmark["objectiveTasksAttempted"] == 0
    assert benchmark["operatorReportedTasks"] == 1
    assert benchmark["automatedRunTasks"] == 0
    assert benchmark["releaseValidationTasks"] == 0
    assert benchmark["successRate"] is None
    assert benchmark["qaPassRate"] is None
    assert benchmark["reworkRate"] is None
    assert benchmark["avgCost"] is None
    assert benchmark["avgLatencyMs"] is None
    assert benchmark["insufficientData"] is True


def test_release_validation_benchmark_provenance_is_accepted_as_objective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/benchmark-outcomes",
        headers=headers,
        json={
            "providerId": "claude_code_cli",
            "model": "sonnet",
            "runtimeType": "cli",
            "role": "developer",
            "taskId": "release-validation-benchmark",
            "provenance": "release_validation",
            "success": True,
            "qaPass": True,
            "rework": False,
            "estimatedCostUsd": 0.12,
            "latencyMs": 900,
        },
    )
    benchmarks = client.get("/api/v1/model-gateway/benchmarks")

    assert created.status_code == 201
    assert created.json()["outcome"]["provenance"] == "release_validation"
    benchmark = next(
        item
        for item in benchmarks.json()["benchmarks"]
        if item["providerId"] == "claude_code_cli" and item["model"] == "sonnet"
    )
    assert benchmark["objectiveTasksAttempted"] == 1
    assert benchmark["releaseValidationTasks"] == 1
    assert benchmark["successRate"] == 1.0


def test_evidence_creation_ingests_benchmark_outcome_from_usage_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
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
        headers = auth_headers(client)

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
            "qaVerdict": "passed",
            **real_qa_evidence_fields(command="pytest", duration_ms=900),
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
    assert outcome["provenance"] == "automated_run"
    assert outcome["success"] is True
    assert outcome["qaPass"] is True
    assert outcome["rework"] is False
    assert outcome["estimatedCostUsd"] == 0.33
    assert outcome["latencyMs"] == 900


def test_failed_test_result_overrides_passed_verdict_in_benchmarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    with client:
        store = client.app.state.runtime  # type: ignore[attr-defined]
        store.create_project(
            name="Failed Benchmark Evidence", path=tmp_path / "failed-benchmark-evidence", template_id="other"
        )
        usage = UsageLedger(store.connection).record_usage(
            provider_id="codex_cli",
            model="gpt-5.5",
            runtime_type="cli",
            role="developer",
            workflow_run_id="workflow-run-failed-benchmark",
            workflow_step_id="workflow-step-failed-benchmark",
            agent_id="agent-dev",
            task_id="implementation",
            estimated_cost_usd=0.10,
            latency_ms=1200,
            raw_usage={"usage_source": "estimated"},
        )

    outcome = ModelBenchmarkStore(store.connection).record_evidence_outcome(
        evidence={
            "id": "evidence-contradictory-benchmark",
            "workflowRunId": "workflow-run-failed-benchmark",
            "agentId": "agent-dev",
            "taskId": "implementation",
            "qaVerdict": "passed",
        },
        payload={
            "usageLedgerId": usage["id"],
            "providerId": "codex_cli",
            "model": "gpt-5.5",
            "workflowStepId": "workflow-step-failed-benchmark",
        },
        test_results=[{"command": "pytest", "status": "failed", "durationMs": 1200}],
    )

    assert outcome is not None
    assert outcome["success"] is False
    assert outcome["qaPass"] is False
    assert outcome["rework"] is True


def test_nvidia_provider_parses_usage_and_handles_429(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        provider = NvidiaNimProvider(connection=connection)
        estimate = provider.estimate_cost(
            ModelRequest(model="auto_best_available", messages=[{"role": "user", "content": "hello"}]),
            "auto_best_available",
        )
        usage = provider.parse_usage(
            {
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 13,
                    "total_tokens": 34,
                }
            }
        )
        missing_usage = provider.parse_usage(
            {"choices": [{"message": {"content": "text without provider usage"}}]}
        )

        assert estimate.estimated_cost_usd is None
        assert estimate.source == "unknown:nvidia_nim:auto_best_available"
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0
        assert usage.raw_usage["usage_source"] == "provider"
        assert missing_usage.input_tokens == 0
        assert missing_usage.output_tokens == 0
        assert missing_usage.total_tokens == 0
        assert missing_usage.raw_usage == {
            "usage_source": "unknown",
            "reason": "provider_response_missing_usage",
        }

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
    runtime = CodexCliRuntime(executable="codex")

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


def test_cli_runtime_persists_real_session_and_usage_with_process_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    monkeypatch.setattr(
        "local_control_center.agents.cli_runtimes.base.evaluate_action",
        lambda payload: {"decision": "allow", "reason": "test policy allows isolated process"},
    )
    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        lambda *args, **kwargs: {
            "returnCode": 0,
            "stdout": '{"event":"token_usage","usage":{"prompt_tokens":7,"cached_input_tokens":2,"completion_tokens":5,"total_tokens":12}}\n',
            "stderr": "",
        },
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        register_workspace(connection, "workspace-cli-test", tmp_path)
        runtime = CodexCliRuntime(executable="codex", connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="Summarize status without secrets",
                profile="codex_gpt55_developer",
                model="gpt-5.5",
                envPolicy={"OPENAI_API_KEY": "sk-cli-secret123456", "network": False},
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
    assert ledger[0]["usage_source"] == "actual"
    assert ledger[0]["total_tokens"] == 12


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


def test_cli_runtime_links_stdout_and_logs_artifacts_for_process_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    monkeypatch.setattr(
        "local_control_center.agents.cli_runtimes.base.evaluate_action",
        lambda payload: {"decision": "allow", "reason": "test policy allows isolated process"},
    )
    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        lambda *args, **kwargs: {
            "returnCode": 0,
            "stdout": '{"event":"token_usage","usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n',
            "stderr": "",
        },
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        register_workspace(connection, "workspace-cli-test", tmp_path)
        runtime = CodexCliRuntime(executable="codex", connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="Summarize status without secrets",
                profile="codex_gpt55_developer",
                model="gpt-5.5",
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
        register_workspace(connection, "workspace-cli-test", tmp_path)
        runtime = CodexCliRuntime(executable="codex", connection=connection)

        result = runtime.run(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-cli-test",
                workspacePath=str(tmp_path),
                prompt="edit code",
                profile="codex_gpt55_developer",
                extraArgs=["--dangerously-bypass-approvals-and-sandbox"],
            )
        )
        session = connection.execute("SELECT * FROM cli_sessions").fetchone()
        ledger = connection.execute("SELECT * FROM usage_ledger").fetchone()

    assert result.status == "blocked"
    assert "dangerous" in (result.error or "")
    assert session["status"] == "blocked"
    assert session["logs_artifact_id"]
    assert ledger["usage_source"] == "unavailable"
    assert ledger["total_tokens"] is None


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

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute", fail_execute
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        register_workspace(connection, "workspace-cli-test", tmp_path)
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
                "runtimeMode": "hybrid",
                "permissionProfile": "dev_safe",
            },
        )
        assert response.status_code == 201
        assert response.json()["agentProfile"]["role"] == role


def test_cli_runtime_parses_usage_from_jsonl_output() -> None:
    runtime = CodexCliRuntime(executable="codex")
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
    codex = CodexCliRuntime(executable="codex").parse_usage(
        RuntimeResult(
            runtime="codex_cli",
            status="completed",
            stdout='{"type":"token_count","tokens":{"input":11,"cached_input":3,"output":7,"reasoning":5,"total":26}}\n',
            returnCode=0,
        )
    )
    claude = ClaudeCodeCliRuntime(executable="claude").parse_usage(
        RuntimeResult(
            runtime="claude_code_cli",
            status="completed",
            stdout='{"type":"result","message":{"usage":{"input_tokens":13,"output_tokens":8,"cache_read_input_tokens":2}}}\n',
            returnCode=0,
        )
    )
    openhands = OpenHandsRuntime(executable="openhands").parse_usage(
        RuntimeResult(
            runtime="openhands",
            status="completed",
            stdout='{"metrics":{"token_usage":{"prompt_tokens":17,"completion_tokens":9,"total_tokens":26}}}\n',
            returnCode=0,
        )
    )
    swe_agent = SweAgentRuntime(executable="sweagent").parse_usage(
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
    removed_mock_execute = client.post(
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
    assert discover.status_code == 403
    assert removed_mock_execute.status_code == 404


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
    assert "runtime.cli.enabled is false" in disabled.json()["detail"].lower()

    with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
        enable_runtime_policy(connection, cli=True)
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
