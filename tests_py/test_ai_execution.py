"""Contracts for fail-closed one-or-many chat execution."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from local_control_center.agents.ai_execution import AIExecutionService
from local_control_center.agents.ai_execution_models import AIExecutionPlan
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.app import create_app
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


class CallTracker:
    """Thread-safe proof that provider calls overlap only when configured to do so."""

    def __init__(self, *, delay_seconds: float = 0.05) -> None:
        self.delay_seconds = delay_seconds
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.calls: list[tuple[str, str]] = []

    def enter(self, provider_id: str, model: str) -> None:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.calls.append((provider_id, model))

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class FakeChatProvider:
    """Test-only synchronous chat adapter; productive code never receives fake success."""

    def __init__(
        self,
        provider_id: str,
        tracker: CallTracker,
        *,
        fail: bool = False,
        usage_known: bool = True,
    ) -> None:
        self.provider_id = provider_id
        self.tracker = tracker
        self.fail = fail
        self.usage_known = usage_known

    def chat_completion(self, request: Any) -> ModelResponse:
        self.tracker.enter(self.provider_id, request.model)
        try:
            time.sleep(self.tracker.delay_seconds)
            if self.fail:
                raise RuntimeError("Bearer test-secret-must-not-persist")
            usage = (
                UsageRecord(
                    inputTokens=4,
                    outputTokens=2,
                    totalTokens=6,
                    rawUsage={
                        "prompt_tokens": 4,
                        "completion_tokens": 2,
                        "total_tokens": 6,
                        "usage_source": "provider",
                    },
                )
                if self.usage_known
                else UsageRecord(
                    rawUsage={
                        "usage_source": "unknown",
                        "reason": "provider_response_missing_usage",
                    }
                )
            )
            return ModelResponse(
                providerId=self.provider_id,
                model=request.model,
                content=f"result:{self.provider_id}:{request.model}",
                usage=usage,
                rawResponse={"authorization": "Bearer test-secret-must-not-persist"},
            )
        finally:
            self.tracker.leave()


def _configure_chat_endpoint(
    connection: Any,
    *,
    provider_id: str,
    models: tuple[str, ...],
    free_tier: bool = True,
) -> None:
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": provider_id,
            "displayName": provider_id,
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_development",
            "apiFamily": "chat_completions",
            "adapterProfile": "nvidia_openai_chat",
            "termsMode": "accepted",
            "pricingMode": "free" if free_tier else "configured",
            "baseUrl": f"http://127.0.0.1:{8100 + len(provider_id)}/v1",
            "credentialRef": "",
            "enabled": True,
        }
    )
    for model in models:
        store.upsert_model(
            {
                "providerId": provider_id,
                "model": model,
                "apiFamily": "chat_completions",
                "contextWindow": 16_384,
                "maxOutputTokens": 2_048,
                "freeTier": free_tier,
                "inputPricePerMtok": None if free_tier else 1.0,
                "outputPricePerMtok": None if free_tier else 2.0,
                "enabled": True,
                "source": "test_operator_manifest",
            }
        )


def _execution_context(tmp_path: Path, endpoint_models: dict[str, tuple[str, ...]]):
    database = tmp_path / "platform.sqlite"
    connection = open_sqlite_connection(database)
    initialize_platform_schema(connection)
    project = ProjectsRepository(connection).create_project(
        name="parallel-chat",
        path=tmp_path / "project",
    )
    runtime = RuntimeConfigRepository(connection)
    runtime.set_runtime_setting("runtime.remote.enabled", True)
    runtime.set_runtime_setting("runtime.nvidia.enabled", True)
    for provider_id, models in endpoint_models.items():
        _configure_chat_endpoint(connection, provider_id=provider_id, models=models)
    return connection, project


def _plan(
    project_id: str,
    branches: list[dict[str, Any]],
    *,
    strategy: str = "parallel_compare",
    max_parallelism: int = 2,
    min_successful: int = 1,
) -> AIExecutionPlan:
    return AIExecutionPlan.model_validate(
        {
            "projectId": project_id,
            "strategy": strategy,
            "messages": [{"role": "user", "content": "prompt-marker-must-not-persist"}],
            "branches": branches,
            "maxParallelism": max_parallelism,
            "minSuccessful": min_successful,
            "temperature": 0,
        }
    )


def test_execution_plan_rejects_invalid_strategy_cardinality() -> None:
    branch = {"providerId": "a", "model": "m", "maxTokens": 64}
    with pytest.raises(ValidationError):
        _plan("project", [branch, {**branch, "providerId": "b"}], strategy="single")
    with pytest.raises(ValidationError):
        _plan("project", [branch], strategy="quorum", min_successful=2)


def test_phase55_migrates_from_phase54_and_reenters_without_overwriting_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.shared import migrations

    database = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database) as connection:
        monkeypatch.setattr(migrations, "init_phase55_schema", lambda _connection: None)
        initialize_platform_schema(connection)
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version = 54").fetchone()
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version = 55").fetchone() is None

        monkeypatch.undo()
        migrations.init_phase55_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="migration",
            path=tmp_path / "migration-project",
        )
        now = "2026-07-14T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO ai_executions
                (id, project_id, capability, strategy, status, min_successful,
                 max_parallelism, branch_count, successful_branches, failed_branches,
                 created_at, started_at, completed_at, updated_at)
            VALUES ('preserved-execution', ?, 'chat_completions', 'single', 'planned',
                    1, 1, 1, 0, 0, ?, NULL, NULL, ?)
            """,
            (project["id"], now, now),
        )
        migrations.init_phase55_schema(connection)
        tables = {
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        migration_count = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 55"
        ).fetchone()["total"]
        preserved = connection.execute(
            "SELECT status FROM ai_executions WHERE id = 'preserved-execution'"
        ).fetchone()

    assert {"ai_executions", "ai_execution_branches"} <= tables
    assert migration_count == 1
    assert preserved["status"] == "planned"


def test_parallel_compare_overlaps_calls_and_persists_no_request_or_raw_provider_payload(
    tmp_path: Path,
) -> None:
    connection, project = _execution_context(
        tmp_path,
        {"nim-a": ("model-a",), "nim-b": ("model-b",)},
    )
    tracker = CallTracker()
    adapters = {provider_id: FakeChatProvider(provider_id, tracker) for provider_id in ("nim-a", "nim-b")}
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda provider_id: adapters[provider_id],
        ).execute(
            _plan(
                project["id"],
                [
                    {"providerId": "nim-a", "model": "model-a", "maxTokens": 64},
                    {"providerId": "nim-b", "model": "model-b", "maxTokens": 64},
                ],
            )
        )
        payload = result.model_dump(by_alias=True)
        persisted = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM ai_execution_branches WHERE execution_id = ? ORDER BY ordinal",
                (payload["executionId"],),
            ).fetchall()
        ]
        serialized = json.dumps(persisted)
    finally:
        connection.close()

    assert payload["status"] == "completed"
    assert payload["successfulBranches"] == 2
    assert tracker.maximum_active == 2
    assert [branch["content"] for branch in payload["branches"]] == [
        "result:nim-a:model-a",
        "result:nim-b:model-b",
    ]
    assert "prompt-marker-must-not-persist" not in serialized
    assert "test-secret-must-not-persist" not in serialized
    assert all(row["status"] == "completed" for row in persisted)


def test_max_parallelism_one_serializes_provider_calls(tmp_path: Path) -> None:
    connection, project = _execution_context(
        tmp_path,
        {"nim-a": ("model-a",), "nim-b": ("model-b",)},
    )
    tracker = CallTracker()
    adapters = {provider_id: FakeChatProvider(provider_id, tracker) for provider_id in ("nim-a", "nim-b")}
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda provider_id: adapters[provider_id],
        ).execute(
            _plan(
                project["id"],
                [
                    {"providerId": "nim-a", "model": "model-a", "maxTokens": 64},
                    {"providerId": "nim-b", "model": "model-b", "maxTokens": 64},
                ],
                max_parallelism=1,
            )
        )
    finally:
        connection.close()

    assert result.status == "completed"
    assert tracker.maximum_active == 1


def test_provider_max_concurrency_blocks_second_branch_before_transport(tmp_path: Path) -> None:
    connection, project = _execution_context(
        tmp_path,
        {"nim-shared": ("model-a", "model-b")},
    )
    now = "2026-07-14T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO provider_limits
            (id, provider_id, model, current_window_json, unknown_limit_strategy,
             max_concurrency, window_timezone, enabled, fallback_retry_after_seconds,
             created_at, updated_at)
        VALUES ('nim-shared:*', 'nim-shared', '*', '{}', 'conservative',
                1, 'UTC', 1, 300, ?, ?)
        """,
        (now, now),
    )
    tracker = CallTracker()
    adapter = FakeChatProvider("nim-shared", tracker)
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda _provider_id: adapter,
        ).execute(
            _plan(
                project["id"],
                [
                    {"providerId": "nim-shared", "model": "model-a", "maxTokens": 64},
                    {"providerId": "nim-shared", "model": "model-b", "maxTokens": 64},
                ],
            )
        )
        payload = result.model_dump(by_alias=True)
    finally:
        connection.close()

    assert payload["status"] == "partial"
    assert len(tracker.calls) == 1
    assert {branch["status"] for branch in payload["branches"]} == {"completed", "blocked"}
    blocked = next(branch for branch in payload["branches"] if branch["status"] == "blocked")
    assert blocked["errorCode"] == "blocked_concurrency"


@pytest.mark.parametrize(
    ("strategy", "minimum", "expected_status", "expected_success"),
    [
        ("parallel_compare", 1, "partial", True),
        ("quorum", 2, "failed", False),
    ],
)
def test_partial_and_quorum_results_keep_each_branch_independent(
    tmp_path: Path,
    strategy: str,
    minimum: int,
    expected_status: str,
    expected_success: bool,
) -> None:
    connection, project = _execution_context(
        tmp_path,
        {"nim-ok": ("model-ok",), "nim-fail": ("model-fail",)},
    )
    tracker = CallTracker()
    adapters = {
        "nim-ok": FakeChatProvider("nim-ok", tracker),
        "nim-fail": FakeChatProvider("nim-fail", tracker, fail=True),
    }
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda provider_id: adapters[provider_id],
        ).execute(
            _plan(
                project["id"],
                [
                    {"providerId": "nim-ok", "model": "model-ok", "maxTokens": 64},
                    {"providerId": "nim-fail", "model": "model-fail", "maxTokens": 64},
                ],
                strategy=strategy,
                min_successful=minimum,
            )
        )
        payload = result.model_dump(by_alias=True)
        persisted = json.dumps(
            [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM ai_execution_branches WHERE execution_id = ?",
                    (payload["executionId"],),
                ).fetchall()
            ]
        )
    finally:
        connection.close()

    assert payload["status"] == expected_status
    assert payload["succeeded"] is expected_success
    assert {branch["status"] for branch in payload["branches"]} == {"completed", "failed"}
    failed = next(branch for branch in payload["branches"] if branch["status"] == "failed")
    assert failed["errorCode"] == "provider_request_failed"
    assert "test-secret-must-not-persist" not in persisted


def test_quorum_succeeds_with_two_of_three_without_hiding_failed_branch(tmp_path: Path) -> None:
    connection, project = _execution_context(
        tmp_path,
        {
            "nim-a": ("model-a",),
            "nim-b": ("model-b",),
            "nim-c": ("model-c",),
        },
    )
    tracker = CallTracker()
    adapters = {
        "nim-a": FakeChatProvider("nim-a", tracker),
        "nim-b": FakeChatProvider("nim-b", tracker),
        "nim-c": FakeChatProvider("nim-c", tracker, fail=True),
    }
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda provider_id: adapters[provider_id],
        ).execute(
            _plan(
                project["id"],
                [
                    {"providerId": "nim-a", "model": "model-a", "maxTokens": 64},
                    {"providerId": "nim-b", "model": "model-b", "maxTokens": 64},
                    {"providerId": "nim-c", "model": "model-c", "maxTokens": 64},
                ],
                strategy="quorum",
                max_parallelism=3,
                min_successful=2,
            )
        )
        payload = result.model_dump(by_alias=True)
    finally:
        connection.close()

    assert payload["status"] == "partial"
    assert payload["succeeded"] is True
    assert payload["successfulBranches"] == 2
    assert payload["failedBranches"] == 1


def test_single_provider_failure_is_terminal_and_redacted(tmp_path: Path) -> None:
    connection, project = _execution_context(tmp_path, {"nim-fail": ("model-fail",)})
    tracker = CallTracker()
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda _provider_id: FakeChatProvider("nim-fail", tracker, fail=True),
        ).execute(
            _plan(
                project["id"],
                [{"providerId": "nim-fail", "model": "model-fail", "maxTokens": 64}],
                strategy="single",
                max_parallelism=1,
            )
        )
        payload = result.model_dump(by_alias=True)
        stored = json.dumps(
            dict(
                connection.execute(
                    "SELECT * FROM ai_execution_branches WHERE execution_id = ?",
                    (payload["executionId"],),
                ).fetchone()
            )
        )
    finally:
        connection.close()

    assert payload["status"] == "failed"
    assert payload["succeeded"] is False
    assert payload["branches"][0]["errorCode"] == "provider_request_failed"
    assert "test-secret-must-not-persist" not in stored


def test_local_settlement_failure_rolls_back_commit_then_releases_attempt(tmp_path: Path) -> None:
    connection, project = _execution_context(tmp_path, {"nim-ok": ("model-ok",)})
    tracker = CallTracker()
    service = AIExecutionService(
        connection,
        provider_resolver=lambda _provider_id: FakeChatProvider("nim-ok", tracker),
    )

    def fail_usage_persistence(**_kwargs: object) -> dict[str, object]:
        raise sqlite3.OperationalError("simulated local ledger failure")

    service.usage.record_usage = fail_usage_persistence  # type: ignore[method-assign]
    try:
        result = service.execute(
            _plan(
                project["id"],
                [{"providerId": "nim-ok", "model": "model-ok", "maxTokens": 64}],
                strategy="single",
                max_parallelism=1,
            )
        )
        payload = result.model_dump(by_alias=True)
        lease = connection.execute(
            "SELECT state, actual_tokens, actual_cost_usd FROM provider_execution_leases"
        ).fetchone()
        ledger_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM usage_ledger WHERE request_id = ?",
            (payload["executionId"],),
        ).fetchone()["total"]
    finally:
        connection.close()

    assert payload["status"] == "failed"
    assert payload["branches"][0]["errorCode"] == "execution_settlement_failed"
    assert dict(lease) == {
        "state": "released",
        "actual_tokens": None,
        "actual_cost_usd": None,
    }
    assert ledger_rows == 0


def test_unknown_provider_usage_remains_null_and_unverified_in_quota_windows(
    tmp_path: Path,
) -> None:
    connection, project = _execution_context(tmp_path, {"nim-unknown": ("model-u",)})
    now = "2026-07-14T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO provider_limits
            (id, provider_id, model, rpm, tpm, daily_requests, daily_tokens,
             monthly_requests, monthly_tokens, monthly_budget_usd, current_window_json,
             cooldown_until, last_429_at, last_limit_error_at, unknown_limit_strategy,
             max_concurrency, window_timezone, enabled, fallback_retry_after_seconds,
             max_cost_per_request_usd, created_at, updated_at)
        VALUES ('nim-unknown:*', 'nim-unknown', '*', 100, 100000, 1000, 1000000,
                10000, 10000000, NULL, '{}', NULL, NULL, NULL, 'conservative',
                2, 'UTC', 1, 300, NULL, ?, ?)
        """,
        (now, now),
    )
    tracker = CallTracker()
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda _provider_id: FakeChatProvider(
                "nim-unknown", tracker, usage_known=False
            ),
        ).execute(
            _plan(
                project["id"],
                [{"providerId": "nim-unknown", "model": "model-u", "maxTokens": 64}],
                strategy="single",
                max_parallelism=1,
            )
        )
        payload = result.model_dump(by_alias=True)
        lease = connection.execute(
            "SELECT * FROM provider_execution_leases WHERE execution_id = ?",
            (payload["executionId"],),
        ).fetchone()
        minute = connection.execute(
            """
            SELECT committed_tokens, unverified_tokens, reserved_tokens, unknown_usage_count
            FROM provider_limit_windows
            WHERE limit_id = 'nim-unknown:*' AND window_kind = 'minute'
            """
        ).fetchone()
    finally:
        connection.close()

    branch = payload["branches"][0]
    assert branch["status"] == "completed"
    assert branch["totalTokens"] is None
    assert branch["usageStatus"] == "unknown"
    assert lease["state"] == "committed"
    assert lease["actual_tokens"] is None
    assert minute["committed_tokens"] == 0
    assert minute["unverified_tokens"] == branch["reservedTokens"]
    assert minute["reserved_tokens"] == 0
    assert minute["unknown_usage_count"] == 1


def test_capability_mismatch_blocks_before_quota_or_provider_transport(tmp_path: Path) -> None:
    connection, project = _execution_context(tmp_path, {"nim-a": ("model-a",)})
    connection.execute("UPDATE model_catalog SET api_family = 'embeddings' WHERE provider_id = 'nim-a'")
    tracker = CallTracker()
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda provider_id: FakeChatProvider(provider_id, tracker),
        ).execute(
            _plan(
                project["id"],
                [{"providerId": "nim-a", "model": "model-a", "maxTokens": 64}],
                strategy="single",
                max_parallelism=1,
            )
        )
        payload = result.model_dump(by_alias=True)
        leases = connection.execute(
            "SELECT COUNT(*) AS total FROM provider_execution_leases WHERE execution_id = ?",
            (payload["executionId"],),
        ).fetchone()["total"]
    finally:
        connection.close()

    assert payload["status"] == "blocked"
    assert payload["branches"][0]["errorCode"] == "model_api_family_mismatch"
    assert tracker.calls == []
    assert leases == 0


def test_hosted_endpoint_without_resolved_credential_blocks_before_transport(tmp_path: Path) -> None:
    connection, project = _execution_context(tmp_path, {"nim-hosted": ("model-h",)})
    connection.execute(
        """
        UPDATE provider_accounts
        SET deployment_mode = 'hosted_trial', credential_ref = '',
            base_url = 'https://integrate.api.nvidia.com/v1',
            terms_mode = 'evaluation', pricing_mode = 'unknown'
        WHERE provider_id = 'nim-hosted'
        """
    )
    tracker = CallTracker()
    try:
        result = AIExecutionService(
            connection,
            provider_resolver=lambda provider_id: FakeChatProvider(provider_id, tracker),
        ).execute(
            _plan(
                project["id"],
                [{"providerId": "nim-hosted", "model": "model-h", "maxTokens": 64}],
                strategy="single",
                max_parallelism=1,
            )
        )
        payload = result.model_dump(by_alias=True)
        leases = connection.execute(
            "SELECT COUNT(*) AS total FROM provider_execution_leases WHERE execution_id = ?",
            (payload["executionId"],),
        ).fetchone()["total"]
    finally:
        connection.close()

    assert payload["status"] == "blocked"
    assert payload["branches"][0]["errorCode"] == "credential_missing"
    assert tracker.calls == []
    assert leases == 0


def test_ai_execution_http_contract_is_typed_and_audit_contains_no_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(database))
    fixture = ControlPlaneFixture(cwd=tmp_path, db_path=database)
    fixture.init()
    project = fixture.projects.create_project(name="api", path=tmp_path / "api-project")
    RuntimeConfigRepository(fixture.connection).set_runtime_setting("runtime.remote.enabled", True)
    RuntimeConfigRepository(fixture.connection).set_runtime_setting("runtime.nvidia.enabled", True)
    _configure_chat_endpoint(
        fixture.connection,
        provider_id="nim-api",
        models=("model-api",),
    )
    fixture.connection.execute(
        "UPDATE model_catalog SET api_family = 'embeddings' WHERE provider_id = 'nim-api'"
    )
    client = TestClient(create_app(runtime=fixture, static_dir=None))
    token = client.get("/api/v1/security/handshake").json()["token"]
    headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}

    response = client.post(
        "/api/v1/model-gateway/ai-executions",
        headers=headers,
        json={
            "projectId": project["id"],
            "strategy": "single",
            "messages": [{"role": "user", "content": "HTTP-PROMPT-MUST-NOT-PERSIST"}],
            "branches": [{"providerId": "nim-api", "model": "model-api", "maxTokens": 64}],
            "maxParallelism": 1,
            "minSuccessful": 1,
        },
    )
    audit_row = fixture.connection.execute(
        """
        SELECT payload FROM audit_events
        WHERE action = 'model_gateway.ai_execution.completed'
        ORDER BY rowid DESC LIMIT 1
        """
    ).fetchone()
    fixture.close()

    assert response.status_code == 200, response.text
    execution = response.json()["execution"]
    assert execution["status"] == "blocked"
    assert execution["branches"][0]["errorCode"] == "model_api_family_mismatch"
    assert "HTTP-PROMPT-MUST-NOT-PERSIST" not in audit_row["payload"]
