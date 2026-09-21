"""Bounded prevalidation uses real accounting/admission with fake provider transport."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from local_control_center.agents import runtime_preflight
from local_control_center.agents.ai_resource_manager import AIResourceRequest
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


@pytest.fixture
def lane(tmp_path, monkeypatch):
    with closing(open_sqlite_connection(tmp_path / "preflight.sqlite")) as connection:
        initialize_platform_schema(connection)
        monkeypatch.setenv("AIDO_PREFLIGHT_TEST_API_KEY", "synthetic-fixture")
        monkeypatch.setattr(runtime_preflight, "_in_quality_environment", lambda: False)
        monkeypatch.setattr(
            runtime_preflight.HostResourceProbe,
            "sample",
            lambda *_a, **_k: ResourceSnapshot.test_snapshot(),
        )
        store = ProviderAccountStore(connection)
        statuses = {}
        models = []
        for provider in ("preflight-a", "preflight-b", "preflight-c"):
            store.upsert_provider_account(
                {
                    "providerId": provider,
                    "name": provider,
                    "providerType": "api",
                    "providerFamily": "openai_compatible",
                    "apiFormat": "openai_compatible",
                    "baseUrl": "https://fixture.example.invalid/v1",
                    "credentialRef": "env:AIDO_PREFLIGHT_TEST_API_KEY",
                    "enabled": True,
                }
            )
            statuses[provider] = {
                "id": provider,
                "kind": "api",
                "configured": True,
                "healthStatus": "healthy",
                "healthCheckedAt": datetime.now(UTC).isoformat(),
            }
            for name in ("model-one", "model-two"):
                row = store.upsert_model(
                    {
                        "providerId": provider,
                        "model": name,
                        "enabled": True,
                        "inputPricePerMtok": 1,
                        "outputPricePerMtok": 1,
                    }
                )
                models.append({**row, "runtime": "api", "locality": "remote"})
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        calls = []
        failures = {}

        def provider_instance(provider_id, **_kwargs):
            def chat(request):
                assert not connection.in_transaction
                calls.append((provider_id, request.model, request.temperature))
                if failure := failures.get((provider_id, request.model)):
                    raise HTTPError("https://fixture.example.invalid", failure, "fixture", {}, None)
                return ModelResponse(
                    providerId=provider_id,
                    model=request.model,
                    content="OK",
                    usage=UsageRecord(
                        inputTokens=4,
                        outputTokens=1,
                        totalTokens=5,
                        rawUsage={"prompt_tokens": 4, "completion_tokens": 1},
                    ),
                )

            return SimpleNamespace(base_url="https://fixture.example.invalid/v1", chat_completion=chat)

        monkeypatch.setattr("local_control_center.agents.model_gateway.provider_instance", provider_instance)
        yield SimpleNamespace(
            connection=connection,
            models=models,
            statuses=statuses,
            calls=calls,
            failures=failures,
            request=AIResourceRequest(task_type="product_owner.discovery", project_id="preflight-project"),
        )


def _run(lane, models=None, request=None):
    return runtime_preflight.prevalidate_candidates(
        lane.connection,
        models=lane.models if models is None else models,
        runtime_statuses=lane.statuses,
        request=request or lane.request,
    )


def test_failure_does_not_abort_other_candidates_and_accounts_usage(lane):
    lane.failures[("preflight-a", "model-one")] = 404
    result = _run(lane)
    assert len(result["validated"]) == 2
    assert result["attempts"] == 3
    assert result["rejected"][0]["httpStatus"] == 404
    assert {row[0] for row in lane.calls} == {"preflight-a", "preflight-b", "preflight-c"}
    assert all(row[2] is None for row in lane.calls)
    assert lane.connection.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 2
    assert (
        lane.connection.execute(
            "SELECT COUNT(*) FROM provider_execution_leases WHERE state IN ('active','dispatched')"
        ).fetchone()[0]
        == 0
    )
    assert (
        lane.connection.execute("SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL").fetchone()[
            0
        ]
        == 0
    )


def test_cached_validation_needs_no_transport_or_lease(lane):
    for row in lane.models[:2]:
        record_model_execution(lane.connection, row["providerId"], row["model"], True, "test_prompt")
    result = _run(lane, lane.models[:2])
    assert len(result["validated"]) == 2
    assert all(item["cached"] for item in result["validated"])
    assert not lane.calls
    assert lane.connection.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0


def test_model_cache_with_stale_health_requires_real_probe(lane):
    row = lane.models[0]
    record_model_execution(lane.connection, row["providerId"], row["model"], True, "test_prompt")
    lane.statuses[row["providerId"]]["healthCheckedAt"] = (
        datetime.now(UTC) - timedelta(minutes=6)
    ).isoformat()
    result = _run(lane, [row])
    assert result["attempts"] == 1
    assert result["validated"][0]["cached"] is False
    assert len(lane.calls) == 1


def test_account_lock_deduplicates_concurrent_validation(lane):
    row = lane.models[0]
    fingerprint = runtime_preflight.provider_configuration_fingerprint(lane.connection, row["providerId"])
    with runtime_preflight._candidate_lock(lane.connection, row["providerId"], fingerprint) as acquired:
        assert acquired
        result = _run(lane, [row])
    assert result["deferred"][0]["reason"] == "preflight_in_progress"
    assert not lane.calls
    assert _run(lane, [row])["attempts"] == 1


def test_quota_denial_releases_host_lease_without_inference(lane):
    from local_control_center.agents.routing_profiles import RoutingProfileStore

    row = lane.models[0]
    RoutingProfileStore(lane.connection).create_provider_limit(
        {
            "providerId": row["providerId"],
            "model": row["model"],
            "maxCostPerRequestUsd": 0.000001,
        }
    )
    result = _run(lane, [row])
    assert result["attempts"] == 0
    assert not lane.calls
    assert (
        lane.connection.execute("SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL").fetchone()[
            0
        ]
        == 0
    )


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_excludes_account_across_roles_and_models(lane, status):
    lane.failures[("preflight-a", "model-one")] = status
    _run(lane, lane.models[:2])
    first_calls = list(lane.calls)
    result = _run(lane, lane.models[:2])
    assert lane.calls == first_calls
    assert first_calls == [("preflight-a", "model-one", None)]
    assert result["excludedProviderIds"] == ["preflight-a"]
    assert (
        ProviderAccountStore(lane.connection).get_provider_account("preflight-a")["healthStatus"]
        == "authentication_required"
    )


def test_changed_configuration_invalidates_cache_and_auth_cooldown(lane):
    row = lane.models[0]
    lane.failures[(row["providerId"], row["model"])] = 401
    _run(lane, [row])
    ProviderAccountStore(lane.connection).patch_provider_account(
        row["providerId"], {"baseUrl": "https://replacement.example.invalid/v1"}
    )
    lane.failures.clear()
    result = _run(lane, [row])
    assert result["attempts"] == 1
    assert result["validated"][0]["cached"] is False


def test_model_failure_cooldown_does_not_exclude_healthy_sibling(lane):
    lane.failures[("preflight-a", "model-one")] = 410
    first = _run(lane, lane.models[:2])
    assert [item["model"] for item in first["validated"]] == ["model-two"]
    before = list(lane.calls)
    _run(lane, lane.models[:2])
    assert lane.calls == before
    lane.connection.execute(
        "UPDATE model_execution_health SET started_at=? WHERE success=0",
        ((datetime.now(UTC) - timedelta(minutes=6)).isoformat(),),
    )
    lane.failures.clear()
    assert len(_run(lane, lane.models[:2])["validated"]) == 2


def test_disabled_candidate_is_never_probed_even_with_prior_success(lane):
    row = lane.models[0]
    record_model_execution(lane.connection, row["providerId"], row["model"], True, "test_prompt")
    lane.connection.execute(
        "UPDATE model_catalog SET enabled=0 WHERE provider_id=? AND model=?",
        (row["providerId"], row["model"]),
    )
    result = _run(lane, [row])
    assert not lane.calls
    assert result["rejected"][0]["reason"] == "model_disabled"


def test_preflight_respects_budget_privacy_and_unknown_price(lane):
    request = AIResourceRequest(task_type="reason", project_id="preflight-project", budget_remaining_usd=0)
    assert _run(lane, request=request)["attempts"] == 0
    request = AIResourceRequest(
        task_type="reason", project_id="preflight-project", privacy_level="local_only"
    )
    assert _run(lane, request=request)["attempts"] == 0
    lane.connection.execute("UPDATE model_catalog SET input_price_per_mtok=NULL, output_price_per_mtok=NULL")
    assert _run(lane)["attempts"] == 0
    assert not lane.calls


def test_global_policy_disable_and_capacity_never_reach_provider(lane, monkeypatch):
    RuntimeConfigRepository(lane.connection).set_runtime_setting("runtime.remote.enabled", False)
    assert _run(lane)["attempts"] == 0
    RuntimeConfigRepository(lane.connection).set_runtime_setting("runtime.remote.enabled", True)
    monkeypatch.setattr(
        runtime_preflight.HostResourceProbe,
        "sample",
        lambda *_a, **_k: ResourceSnapshot.test_snapshot(available_memory_bytes=0),
    )
    assert _run(lane)["attempts"] == 0
    assert not lane.calls


def test_probe_count_is_bounded_even_when_every_model_fails(lane):
    lane.failures.update({(row["providerId"], row["model"]): 404 for row in lane.models})
    result = _run(lane)
    assert result["attempts"] == 4
    assert len(lane.calls) == 4
    assert result["deferred"]


@pytest.mark.parametrize(
    "marker", ["AIDO_QUALITY_INVOCATION_ID", "AIDO_QUALITY_DB_PATH", "PYTEST_CURRENT_TEST"]
)
def test_quality_guard_is_absolute_and_does_not_use_legacy_call_flag(lane, monkeypatch, marker):
    monkeypatch.undo()
    monkeypatch.setenv(marker, "quality")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    transport = Mock(side_effect=AssertionError("QA must not reach a provider"))
    monkeypatch.setattr("local_control_center.agents.model_gateway.provider_instance", transport)
    result = _run(lane)
    assert result["attempts"] == 0
    transport.assert_not_called()


def test_legacy_false_does_not_override_sqlite_policy_with_fake_transport(lane, monkeypatch):
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")
    assert len(_run(lane)["validated"]) == 2


def test_cli_without_execution_evidence_is_deferred_without_fake_validation(lane):
    row = {**lane.models[0], "runtime": "cli"}
    lane.statuses[row["providerId"]]["kind"] = "cli"
    result = _run(lane, [row])
    assert result["deferred"][0]["reason"] == "preflight_unknown_cost_requires_approval"
    assert not lane.calls


def _cli_candidates(lane, monkeypatch, *, success=True, failed_providers=()):
    from local_control_center.agents import runtime_preflight_cli

    store = ProviderAccountStore(lane.connection)
    models = []
    RuntimeConfigRepository(lane.connection).set_runtime_setting("runtime.cli.enabled", True)
    for provider in ("codex_cli", "claude_code_cli"):
        store.patch_provider_account(provider, {"enabled": True})
        lane.statuses[provider] = {"id": provider, "kind": "cli", "configured": True}
        for name in ("cli-one", "cli-two"):
            models.append(
                {
                    **store.upsert_model({"providerId": provider, "model": name, "enabled": True}),
                    "runtime": "cli",
                    "locality": "local",
                }
            )
    calls = []

    def validate(connection, *, model, runtime_status, request):
        calls.append((model["providerId"], model["model"]))
        valid = success and model["providerId"] not in failed_providers
        record_model_execution(connection, model["providerId"], model["model"], valid, "test_prompt")
        return {
            "status": "completed" if valid else "unavailable",
            "attempted": True,
            "costStatus": "unknown",
            "usage": {"actualCostUsd": None, "totalTokens": None},
        }

    monkeypatch.setattr(runtime_preflight_cli, "validate_cli_candidate", validate)
    return models, calls


def test_cli_only_role_does_not_require_api_permission(lane, monkeypatch):
    models, cli_calls = _cli_candidates(lane, monkeypatch)
    request = AIResourceRequest(
        task_type="reason",
        project_id="preflight-project",
        allow_api=False,
        allow_local=False,
        allow_cli=True,
        allow_unknown_cost=True,
        require_approval_for_unknown_cost=False,
    )
    result = _run(lane, models, request)
    assert len(result["validated"]) == 2
    assert len(cli_calls) == result["cliAttempts"] == result["attempts"] == 2
    assert result["budgetCostUnknown"] is True
    assert result["budgetSpentUsd"] is None
    assert not lane.calls


def test_hybrid_attempt_limit_includes_unknown_cost_cli(lane, monkeypatch):
    cli_models, cli_calls = _cli_candidates(lane, monkeypatch, success=False)
    lane.failures.update({(row["providerId"], row["model"]): 404 for row in lane.models})
    request = AIResourceRequest(
        task_type="reason",
        project_id="preflight-project",
        allow_unknown_cost=True,
        require_approval_for_unknown_cost=False,
    )
    result = _run(lane, [*cli_models, *lane.models], request)
    assert result["attempts"] == 4
    assert result["cliAttempts"] == len(cli_calls) == 2
    assert len(lane.calls) == 2
    assert result["budgetCostUnknown"] is True
    assert result["budgetSpentUsd"] is None
    assert result["knownBudgetSpentUsd"] > 0


def test_first_cli_failure_allows_second_account_without_repeating_either(lane, monkeypatch):
    models, cli_calls = _cli_candidates(lane, monkeypatch, failed_providers={"codex_cli"})
    request = AIResourceRequest(
        task_type="reason",
        project_id="preflight-project",
        allow_unknown_cost=True,
        require_approval_for_unknown_cost=False,
    )
    result = _run(lane, models, request)
    assert cli_calls == [("codex_cli", "cli-one"), ("claude_code_cli", "cli-one")]
    assert [entry["providerId"] for entry in result["validated"]] == ["claude_code_cli"]
    assert result["attempts"] == result["cliAttempts"] == 2
    assert result["budgetSpentUsd"] is None
    assert result["budgetCostUnknown"] is True
    assert len(result["deferred"]) == 2


def test_cli_denial_does_not_consume_attempt_or_suppress_api(lane, monkeypatch):
    cli_models, cli_calls = _cli_candidates(lane, monkeypatch)
    request = AIResourceRequest(task_type="reason", project_id="preflight-project", allow_cli=False)
    result = _run(lane, [*cli_models, *lane.models], request)
    assert len(result["validated"]) == 2
    assert result["attempts"] == 2
    assert not cli_calls
    assert result["cliAttempts"] == 0
    assert result["budgetCostUnknown"] is False


def _unknown_price(lane, row, *, input_price=None):
    lane.connection.execute(
        "UPDATE model_catalog SET input_price_per_mtok=?, output_price_per_mtok=NULL "
        "WHERE provider_id=? AND model=?",
        (input_price, row["providerId"], row["model"]),
    )


@pytest.mark.parametrize("input_price", [None, 1.0], ids=["absent", "partial"])
def test_authorized_unknown_api_cost_preserves_unknown_and_real_quota(lane, monkeypatch, input_price):
    row = lane.models[0]
    _unknown_price(lane, row, input_price=input_price)
    estimates = []
    acquire = runtime_preflight.QuotaManager.acquire

    def record_acquire(manager, request):
        estimates.append(request.estimated_cost_usd)
        return acquire(manager, request)

    monkeypatch.setattr(runtime_preflight.QuotaManager, "acquire", record_acquire)
    request = replace(lane.request, allow_unknown_cost=True, require_approval_for_unknown_cost=False)
    result = _run(lane, [row], request)
    assert result["attempts"] == len(result["validated"]) == 1
    assert estimates == [None]
    assert result["budgetCostUnknown"] is True
    assert result["budgetSpentUsd"] is None
    assert lane.calls == [(row["providerId"], row["model"], None)]
    assert lane.connection.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 1


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"allow_unknown_cost": False}, "preflight_unknown_cost_requires_approval"),
        ({"require_approval_for_unknown_cost": True}, "preflight_unknown_cost_requires_approval"),
        ({"routing_policy": "economy"}, "unknown_remote_cost_rejected_by_policy"),
        ({"routing_policy": "free_first"}, "unknown_remote_cost_rejected_by_policy"),
        ({"budget_remaining_usd": 0}, "preflight_cost_budget"),
        ({"require_approval_over_usd": 0}, "preflight_cost_budget"),
    ],
)
def test_unknown_api_cost_obeys_role_policy_and_zero_budget(lane, overrides, reason):
    row = lane.models[0]
    _unknown_price(lane, row)
    request = replace(lane.request, allow_unknown_cost=True, require_approval_for_unknown_cost=False)
    result = _run(lane, [row], replace(request, **overrides))
    assert result["attempts"] == 0
    assert result["deferred"][0]["reason"] == reason
    assert not lane.calls
    assert lane.connection.execute("SELECT COUNT(*) FROM provider_execution_leases").fetchone()[0] == 0


@pytest.mark.parametrize("strategy,attempts", [("conservative", 0), ("allow", 1)])
def test_unknown_api_cost_keeps_provider_quota_authority(lane, strategy, attempts):
    from local_control_center.agents.routing_profiles import RoutingProfileStore

    row = lane.models[0]
    _unknown_price(lane, row)
    RoutingProfileStore(lane.connection).create_provider_limit(
        {
            "providerId": row["providerId"],
            "model": row["model"],
            "maxCostPerRequestUsd": 0.001,
            "unknownLimitStrategy": strategy,
        }
    )
    request = replace(lane.request, allow_unknown_cost=True, require_approval_for_unknown_cost=False)
    result = _run(lane, [row], request)
    assert result["attempts"] == len(lane.calls) == attempts
    if not attempts:
        assert result["deferred"][0]["reason"] == "unknown_cost_blocked"
    assert (
        lane.connection.execute("SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL").fetchone()[
            0
        ]
        == 0
    )


def test_unknown_api_failure_records_unknown_cost_and_continues_to_other_provider(lane):
    for row in lane.models:
        _unknown_price(lane, row)
    lane.failures[("preflight-a", "model-one")] = 404
    request = replace(lane.request, allow_unknown_cost=True, require_approval_for_unknown_cost=False)
    result = _run(lane, request=request)
    assert result["attempts"] == len(lane.calls) == 3
    assert len(result["validated"]) == 2
    assert result["rejected"][0]["httpStatus"] == 404
    assert result["budgetCostUnknown"] is True
    assert result["budgetSpentUsd"] is None


def test_known_api_estimate_keeps_preflight_cost_cap(lane):
    row = lane.models[0]
    lane.connection.execute(
        "UPDATE model_catalog SET input_price_per_mtok=1000,output_price_per_mtok=1000 "
        "WHERE provider_id=? AND model=?",
        (row["providerId"], row["model"]),
    )
    request = replace(lane.request, allow_unknown_cost=True, require_approval_for_unknown_cost=False)
    result = _run(lane, [row], request)
    assert result["attempts"] == 0
    assert result["deferred"][0]["reason"] == "preflight_cost_budget"
    assert not lane.calls


@pytest.mark.parametrize(
    "provider_type,api_format,metadata,attempts",
    [
        ("local", "ollama", {}, 1),
        ("gateway", "openai_compatible", {}, 0),
        ("local", "ollama", {"endpointKind": "remote"}, 0),
    ],
    ids=["local-ollama", "loopback-proxy", "remote-ollama"],
)
def test_local_runtime_price_exemption_does_not_treat_loopback_proxy_as_free(
    lane, provider_type, api_format, metadata, attempts
):
    store = ProviderAccountStore(lane.connection)
    provider = "preflight-local"
    store.upsert_provider_account(
        {
            "providerId": provider,
            "name": provider,
            "providerType": provider_type,
            "providerFamily": "ollama" if api_format == "ollama" else "openai_compatible",
            "apiFormat": api_format,
            "baseUrl": "http://localhost:11434",
            "credentialRef": "env:AIDO_PREFLIGHT_TEST_API_KEY",
            "metadata": metadata,
            "enabled": True,
        }
    )
    RuntimeConfigRepository(lane.connection).set_runtime_setting("runtime.ollama.enabled", True)
    row = {
        **store.upsert_model({"providerId": provider, "model": "local-model", "enabled": True}),
        "runtime": "api",
        "locality": "local",
    }
    lane.statuses[provider] = {"id": provider, "kind": provider_type, "configured": True}
    result = _run(lane, [row])
    assert result["attempts"] == len(lane.calls) == attempts
    if attempts:
        assert len(result["validated"]) == 1
        assert result["budgetCostUnknown"] is True
        assert result["budgetSpentUsd"] is None
    else:
        assert result["deferred"][0]["reason"] == "preflight_unknown_cost_requires_approval"


def test_preflight_borrows_job_reservation_without_extra_memory_or_release(lane, monkeypatch):
    from pathlib import Path

    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest
    from local_control_center.host_resources.repository import ResourceRepository
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    database = Path(lane.connection.execute("PRAGMA database_list").fetchone()[2])
    lease = (
        HostResourceGovernor(lane.connection)
        .admit(
            ResourceAdmissionRequest(execution_id="parent", owner_id="worker", workload_class="agent_cli"),
            snapshot=ResourceSnapshot.test_snapshot(),
        )
        .lease
    )
    monkeypatch.setattr(
        runtime_preflight.HostResourceProbe,
        "sample",
        lambda *_a, **_k: ResourceSnapshot.test_snapshot(available_memory_bytes=25 * 1024**3),
    )
    with execution_scope(
        ProcessExecutionContext(
            db_path=database,
            execution_id="parent",
            worker_id="worker",
            resource_lease_id=lease.id,
            connection=lane.connection,
        )
    ):
        result = _run(lane, lane.models[:1])
    assert result["attempts"] == len(result["validated"]) == 1
    assert len(lane.calls) == 1
    assert [item.id for item in ResourceRepository(lane.connection).active_leases()] == [lease.id]


def test_preflight_counts_all_deferred_reasons_with_bounded_examples(lane):
    store = ProviderAccountStore(lane.connection)
    models = []
    for index in range(35):
        models.append(
            {
                **store.upsert_model(
                    {"providerId": "preflight-a", "model": f"unknown-{index}", "enabled": True}
                ),
                "runtime": "api",
                "locality": "remote",
            }
        )
    result = _run(lane, models)
    assert result["attempts"] == 0
    assert result["deferredCount"] == 35
    assert len(result["deferred"]) == 32
    assert result["deferredReasonCounts"] == {"preflight_unknown_cost_requires_approval": 35}
    assert not lane.calls


def test_probe_boundary_keeps_original_connection_and_project(lane, monkeypatch):
    from local_control_center.process_supervision.context import CURRENT_EXECUTION, assert_external_boundary

    seen = []
    blocked = []
    row = lane.models[0]

    def chat(request):
        seen.append(CURRENT_EXECUTION.get())
        lane.connection.execute("BEGIN")
        try:
            try:
                assert_external_boundary()
            except RuntimeError:
                blocked.append(True)
            else:
                blocked.append(False)
        finally:
            lane.connection.rollback()
        return ModelResponse(
            providerId=row["providerId"], model=request.model, content="OK", usage=UsageRecord()
        )

    monkeypatch.setattr(
        "local_control_center.agents.model_gateway.provider_instance",
        lambda *_a, **_k: SimpleNamespace(
            base_url="https://fixture.example.invalid/v1", chat_completion=chat
        ),
    )
    result = _run(lane, [row])
    assert seen[0].connection is lane.connection
    assert seen[0].project_id == lane.request.project_id
    assert blocked == [True]
    assert len(result["validated"]) == 1


def test_invalid_parent_authority_is_deferred_without_counting_an_attempt(lane):
    from pathlib import Path

    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    database = Path(lane.connection.execute("PRAGMA database_list").fetchone()[2])
    with execution_scope(
        ProcessExecutionContext(
            db_path=database,
            execution_id="job",
            worker_id="worker",
            resource_lease_id="missing-parent-lease",
            connection=lane.connection,
        )
    ):
        result = _run(lane, lane.models[:1])
    assert result["attempts"] == 0
    assert result["deferred"][0]["reason"] == "resource_parent_lease_missing"
    assert not lane.calls
    assert not result["validated"]
    assert lane.connection.execute("SELECT COUNT(*) FROM model_execution_health").fetchone()[0] == 0


def test_api_spending_exact_remaining_budget_defers_following_cli(lane, monkeypatch):
    cli_models, cli_calls = _cli_candidates(lane, monkeypatch)
    request = replace(
        lane.request,
        budget_remaining_usd=0.000048,
        allow_unknown_cost=True,
        require_approval_for_unknown_cost=False,
    )
    result = _run(lane, [lane.models[0], *cli_models], request)
    assert lane.calls == [("preflight-a", "model-one", None)]
    assert cli_calls == []
    assert result["attempts"] == 1
    assert result["cliAttempts"] == 0
    assert result["knownBudgetSpentUsd"] == pytest.approx(0.000048)
    assert result["deferredReasonCounts"]["preflight_cost_budget"] == len(cli_models)
