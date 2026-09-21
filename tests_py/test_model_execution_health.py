"""Model validation requires real execution evidence, never catalog or provider health.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.model_execution_health import (
    model_validation_rejection,
    provider_configuration_fingerprint,
    record_model_execution,
)
from local_control_center.agents.model_gateway import ModelGateway
from local_control_center.agents.model_gateway_api import _run_provider_test_prompt
from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ALLOWED_TOOLS
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelRequest, ModelResponse, UsageRecord
from local_control_center.agents.providers.capabilities import ProviderHttpResponse
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.providers.nvidia_nim import NvidiaNimCapabilityError, NvidiaNimProvider
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_adapters.registry import RuntimeAdapterBrokerAdapter
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared import migrations
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture

PROVIDER = "openai_compatible"
MODEL = "validated-model"


@pytest.fixture
def connection(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "health.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def _record(connection, success=True, **kwargs):
    record_model_execution(connection, PROVIDER, MODEL, success, "test_prompt", **kwargs)


def test_health_only_and_catalog_replay_never_validate_a_model(connection):
    store = ProviderAccountStore(connection)
    store.record_health_check(provider_id=PROVIDER, status="healthy", payload={"healthStatus": "healthy"})
    store.upsert_model({"providerId": PROVIDER, "model": MODEL, "enabled": True})
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_required"
    assert connection.execute("SELECT COUNT(*) FROM model_execution_health").fetchone()[0] == 0


@pytest.mark.parametrize("http_status", [None, 404, 410])
def test_failure_invalidates_success_and_catalog_cannot_clear_it(connection, http_status):
    _record(connection)
    assert model_validation_rejection(connection, PROVIDER, MODEL) is None
    _record(connection, False, http_status=http_status)
    ProviderAccountStore(connection).upsert_model(
        {"providerId": PROVIDER, "model": MODEL, "enabled": True}, preserve_operator_enabled=True
    )
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_failed"
    _record(connection)
    assert model_validation_rejection(connection, PROVIDER, MODEL) is None


@pytest.mark.parametrize("hours", [24, 25, -1])
def test_success_must_be_fresh_and_not_from_future(connection, hours):
    _record(connection)
    connection.execute(
        "UPDATE model_execution_health SET started_at = ?",
        ((datetime.now(UTC) - timedelta(hours=hours)).isoformat(),),
    )
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_expired"


def test_fingerprint_is_stable_for_health_and_invalidated_by_endpoint_change(connection):
    before = provider_configuration_fingerprint(connection, PROVIDER)
    _record(connection)
    store = ProviderAccountStore(connection)
    store.record_health_check(provider_id=PROVIDER, status="healthy", payload={"healthStatus": "healthy"})
    assert provider_configuration_fingerprint(connection, PROVIDER) == before
    store.patch_provider_account(PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"})
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_configuration_changed"


def test_execution_observation_keeps_pre_call_fingerprint(connection):
    before = provider_configuration_fingerprint(connection, PROVIDER)
    ProviderAccountStore(connection).patch_provider_account(
        PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
    )
    _record(connection, configuration_fingerprint=before)
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_configuration_changed"


def test_fingerprint_canonicalizes_legacy_ref_but_detects_different_credential_reference(connection):
    connection.execute(
        "UPDATE provider_accounts SET credential_ref = 'LEGACY_VALIDATION_API_KEY' WHERE provider_id = ?",
        (PROVIDER,),
    )
    _record(connection)
    store = ProviderAccountStore(connection)
    store.patch_provider_account(PROVIDER, {"credentialRef": "env:LEGACY_VALIDATION_API_KEY"})
    assert model_validation_rejection(connection, PROVIDER, MODEL) is None
    store.patch_provider_account(PROVIDER, {"credentialRef": "env:OTHER_VALIDATION_KEY"})
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_configuration_changed"


def test_late_success_from_older_attempt_cannot_clear_newer_failure(connection):
    now = datetime.now(UTC)
    _record(connection, False, http_status=410, started_at=now.isoformat())
    _record(connection, started_at=(now - timedelta(seconds=1)).isoformat())
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_failed"


@pytest.mark.parametrize("http_status", [401, 403])
def test_authentication_failure_excludes_previously_validated_sibling_model(connection, http_status):
    _record(connection)
    record_model_execution(connection, PROVIDER, "sibling", False, "model_gateway", http_status=http_status)
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "provider_authentication_cooldown"
    # A new successful execution proves that the account was repaired; catalog reads do not.
    record_model_execution(connection, PROVIDER, "sibling", True, "model_gateway")
    assert model_validation_rejection(connection, PROVIDER, MODEL) is None


@pytest.mark.parametrize("content,model", [("", MODEL), ("   ", MODEL), ("ok", "wrong-model")])
def test_test_prompt_rejects_empty_or_wrong_identity_response(connection, content, model):
    provider = Mock()
    provider.chat_completion.return_value = ModelResponse(
        providerId=PROVIDER, model=model, content=content, usage=UsageRecord()
    )
    result = _run_provider_test_prompt(PROVIDER, MODEL, connection=connection, provider=provider)
    assert result["ok"] is False
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_failed"


@pytest.mark.parametrize("http_status", [None, 404, 410])
def test_test_prompt_records_real_result_without_content_or_credentials(connection, http_status):
    provider = Mock()
    if http_status:
        provider.chat_completion.side_effect = HTTPError(
            "https://example.invalid", http_status, "gone", {}, None
        )
    else:
        provider.chat_completion.return_value = ModelResponse(
            providerId=PROVIDER, model=MODEL, content="private response", usage=UsageRecord()
        )
    result = _run_provider_test_prompt(PROVIDER, MODEL, connection=connection, provider=provider)
    assert result["ok"] is (http_status is None)
    row = connection.execute("SELECT * FROM model_execution_health").fetchone()
    assert bool(row["success"]) is (http_status is None)
    assert row["http_status"] == http_status
    assert "private response" not in str(tuple(row))
    assert "https://" not in str(tuple(row))


def test_test_prompt_fingerprint_is_captured_before_transport(connection):
    def complete(_request):
        ProviderAccountStore(connection).patch_provider_account(
            PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
        )
        return ModelResponse(providerId=PROVIDER, model=MODEL, content="ok", usage=UsageRecord())

    _run_provider_test_prompt(
        PROVIDER, MODEL, connection=connection, provider=SimpleNamespace(chat_completion=complete)
    )
    assert model_validation_rejection(connection, PROVIDER, MODEL) == "model_validation_configuration_changed"


@pytest.fixture
def broker_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    project_path = tmp_path / "project"
    project_path.mkdir()
    with closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")) as store:
        store.init()
        project = store.create_project(name="Model validation", path=project_path, template_id="other")
        connection = store.connection
        ProviderAccountStore(connection).patch_provider_account(PROVIDER, {"enabled": True})
        workspace = WorkspacesRepository(connection, root=tmp_path).allocate_workspace(
            project_id=project["id"],
            branch_name="health",
            isolation_type="directory",
            task_id="health-task",
            agent_id="product_owner_agent",
        )
        agents = AgentsRepository(connection)
        profile = agents.upsert_agent_profile(
            {
                "id": "product_owner_agent",
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": "hybrid",
                "permissionProfile": "plan",
                "allowedTools": PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [PROVIDER],
                "allowedRuntimes": [PROVIDER],
            }
        )
        run = agents.create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="health-task",
            input_payload={},
            output_payload={},
            workflow_run_id="health-loop",
            status="running",
        )
        decision = {
            "routingDecisionId": "health-routing",
            "usageStatus": "not_executed",
            "approvalRequired": False,
            "selected": {"providerId": PROVIDER, "model": MODEL, "runtime": "api"},
            "policyResult": {
                "decisionEngine": {
                    "mode": "runtime_selection",
                    "selectionValidation": {
                        "providerId": PROVIDER,
                        "model": MODEL,
                        "configurationFingerprint": provider_configuration_fingerprint(connection, PROVIDER),
                    },
                }
            },
        }
        AIResourceManager(connection)._record_routing_decision(
            request=AIResourceRequest(
                project_id=project["id"],
                workflow_run_id="health-loop",
                agent_id=profile["id"],
                task_id="health-task",
                task_type="product_owner.discovery",
            ),
            decision=decision,
        )
        adapter = Mock()
        adapter.execute.return_value = {"executed": True, "status": "completed", "returnCode": 0}
        broker = ToolBroker(connection, runtime_adapters={PROVIDER: adapter})
        call = {
            "tool": PROVIDER,
            "operation": "product_owner_model_call",
            "execute": True,
            "runtimeId": PROVIDER,
            "workspaceId": workspace["id"],
            "workspacePath": workspace["path"],
            "path": workspace["path"],
            "providerTransportRequired": True,
            "input": {
                "providerId": PROVIDER,
                "model": MODEL,
                "messages": [{"role": "user", "content": "secret"}],
            },
            "resourceDecisionId": decision["routingDecisionId"],
        }
        yield SimpleNamespace(
            connection=connection,
            adapter=adapter,
            broker=broker,
            kwargs={
                "project_id": project["id"],
                "agent_run_id": run["id"],
                "agent_profile": profile,
                "tool_call": call,
                "trusted_operation": "product_owner_model_call",
                "trusted_resource_decision": decision,
            },
        )


def test_broker_revalidates_persisted_jev_mode_and_denial_creates_no_evidence(broker_lane):
    lane = broker_lane
    lane.kwargs["trusted_resource_decision"]["policyResult"] = {}
    result = lane.broker.evaluate_tool_call(**lane.kwargs)
    assert result["decision"]["decision"] == "deny"
    assert result["decision"]["reason"] == "model_validation_required"
    lane.adapter.execute.assert_not_called()
    assert lane.connection.execute("SELECT COUNT(*) FROM model_execution_health").fetchone()[0] == 0


@pytest.mark.parametrize("http_status", [None, 404, 410])
def test_broker_records_executed_model_results(broker_lane, http_status):
    lane = broker_lane
    _record(lane.connection)
    if http_status:
        lane.adapter.execute.return_value = {
            "executed": False,
            "blocked": True,
            "status": "unavailable",
            "httpStatus": http_status,
        }
    lane.broker.evaluate_tool_call(**lane.kwargs)
    lane.adapter.execute.assert_called_once()
    row = lane.connection.execute("SELECT * FROM model_execution_health ORDER BY id DESC LIMIT 1").fetchone()
    assert row["source"] == "tool_broker"
    assert bool(row["success"]) is (http_status is None)
    assert row["http_status"] == http_status


def test_broker_does_not_record_configuration_failure_as_model_execution(broker_lane):
    lane = broker_lane
    _record(lane.connection)
    lane.adapter.execute.return_value = {
        "executed": False,
        "blocked": True,
        "status": "configuration_required",
    }
    lane.broker.evaluate_tool_call(**lane.kwargs)
    assert lane.connection.execute("SELECT COUNT(*) FROM model_execution_health").fetchone()[0] == 1


def test_broker_rejects_model_disabled_after_selection(broker_lane):
    lane = broker_lane
    _record(lane.connection)
    ProviderAccountStore(lane.connection).upsert_model(
        {"providerId": PROVIDER, "model": MODEL, "enabled": False}
    )
    result = lane.broker.evaluate_tool_call(**lane.kwargs)
    assert result["decision"]["reason"] == "model_disabled"
    lane.adapter.execute.assert_not_called()


def test_broker_rejects_role_policy_revision_changed_after_selection(broker_lane):
    lane = broker_lane
    _record(lane.connection)
    lane.connection.execute(
        """UPDATE ai_routing_decisions SET policy_result_json = json_set(
            policy_result_json, '$.roleExecutionPolicy.rolePolicyId', 'product_owner',
            '$.decisionEngine.rolePolicyRevision', 'old-revision') WHERE id = 'health-routing'"""
    )
    result = lane.broker.evaluate_tool_call(**lane.kwargs)
    assert result["decision"]["reason"] == "role_policy_changed"
    lane.adapter.execute.assert_not_called()


def test_broker_success_keeps_fingerprint_from_before_execution(broker_lane):
    lane = broker_lane
    _record(lane.connection)

    def execute(**_kwargs):
        ProviderAccountStore(lane.connection).patch_provider_account(
            PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
        )
        return {"executed": True, "status": "completed", "returnCode": 0}

    lane.adapter.execute.side_effect = execute
    lane.broker.evaluate_tool_call(**lane.kwargs)
    assert (
        model_validation_rejection(lane.connection, PROVIDER, MODEL)
        == "model_validation_configuration_changed"
    )


@pytest.mark.parametrize("fail", [False, True])
def test_phase76_preserves_v75_evidence_and_rolls_back_atomically(tmp_path, monkeypatch, fail):
    with closing(open_sqlite_connection(tmp_path / "v75.sqlite")) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
        migrations.init_phase75_schema(connection)
        connection.execute("""INSERT INTO model_execution_health VALUES
            (9, 'provider', 'model', 'fingerprint', 0, 'tool_broker', 410, 'start', 'observed')""")
        before = tuple(connection.execute("SELECT * FROM model_execution_health").fetchone())
        if fail:
            original = migrations._execute_atomic_statements

            def inject_failure(database, statements):
                original(database, [*statements, ("INSERT INTO nonexistent_table VALUES (1)", ())])

            monkeypatch.setattr(migrations, "_execute_atomic_statements", inject_failure)
            with pytest.raises(sqlite3.OperationalError):
                migrations.init_phase76_schema(connection)
        else:
            migrations.init_phase76_schema(connection)
            migrations.init_phase76_schema(connection)
        assert tuple(connection.execute("SELECT * FROM model_execution_health").fetchone()) == before
        assert connection.execute("SELECT COUNT(*) FROM model_execution_health").fetchone()[0] == 1
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_model_execution_health_latest'"
        ).fetchone()
        assert bool(connection.execute("SELECT 1 FROM schema_migrations WHERE version = 76").fetchone()) is (
            not fail
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='model_execution_health_v76'"
            ).fetchone()
            is None
        )
        insert = """INSERT INTO model_execution_health
            (provider_id, model, configuration_fingerprint, success, source, started_at, observed_at)
            VALUES ('provider', 'next', 'fingerprint', 1, 'model_gateway', 'start', 'observed')"""
        if fail:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert)
        else:
            connection.execute(insert)
            assert connection.execute("SELECT MAX(id) FROM model_execution_health").fetchone()[0] > 9


@pytest.fixture
def gateway_lane(connection, monkeypatch):
    monkeypatch.setenv("AIDO_VALIDATION_GATEWAY_KEY", "test-validation-gateway-credential")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    ProviderAccountStore(connection).patch_provider_account(
        PROVIDER,
        {
            "enabled": True,
            "baseUrl": "https://provider.example.invalid/v1",
            "credentialRef": "env:AIDO_VALIDATION_GATEWAY_KEY",
        },
    )
    RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
    transport = Mock(
        return_value=ModelResponse(providerId=PROVIDER, model=MODEL, content="ok", usage=UsageRecord())
    )
    provider = SimpleNamespace(base_url="https://provider.example.invalid/v1", chat_completion=transport)
    monkeypatch.setattr(
        "local_control_center.agents.model_gateway.provider_instance", lambda *_a, **_k: provider
    )
    gateway = ModelGateway(connection)
    plan = gateway.plan_model_call(
        project_id="validation-project",
        provider=PROVIDER,
        model=MODEL,
        runtime_type="api",
        messages=[{"role": "user", "content": "private"}],
    )
    return SimpleNamespace(connection=connection, gateway=gateway, plan=plan, transport=transport)


@pytest.mark.parametrize("http_status", [None, 404, 410])
def test_gateway_records_executed_outcome_and_http_status(gateway_lane, http_status):
    lane = gateway_lane
    if http_status:
        _record(lane.connection)
        lane.transport.side_effect = HTTPError(
            "https://provider.example.invalid", http_status, "gone", {}, None
        )
    result = lane.gateway.execute_model_call(lane.plan)
    assert result["status"] == ("completed" if http_status is None else "unavailable")
    row = lane.connection.execute("SELECT * FROM model_execution_health ORDER BY id DESC LIMIT 1").fetchone()
    assert row["source"] == "model_gateway"
    assert row["http_status"] == http_status
    assert bool(row["success"]) is (http_status is None)
    if http_status:
        assert result["httpStatus"] == http_status
        assert model_validation_rejection(lane.connection, PROVIDER, MODEL) == "model_validation_failed"


@pytest.mark.parametrize("content,model", [("", MODEL), ("ok", "wrong-model")])
def test_gateway_does_not_validate_empty_or_mismatched_response(gateway_lane, content, model):
    lane = gateway_lane
    lane.transport.return_value = ModelResponse(
        providerId=PROVIDER, model=model, content=content, usage=UsageRecord()
    )
    result = lane.gateway.execute_model_call(lane.plan)
    assert result["status"] == "unavailable"
    assert model_validation_rejection(lane.connection, PROVIDER, MODEL) == "model_validation_failed"
    assert lane.connection.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 1


def test_gateway_block_does_not_create_execution_evidence(gateway_lane):
    lane = gateway_lane
    ProviderAccountStore(lane.connection).patch_provider_account(PROVIDER, {"enabled": False})
    assert lane.gateway.execute_model_call(lane.plan)["status"] == "blocked"
    lane.transport.assert_not_called()
    assert lane.connection.execute("SELECT COUNT(*) FROM model_execution_health").fetchone()[0] == 0


def test_gateway_transport_timeout_invalidates_previous_success(gateway_lane):
    lane = gateway_lane
    _record(lane.connection)
    lane.transport.side_effect = TimeoutError()
    lane.gateway.execute_model_call(lane.plan)
    assert model_validation_rejection(lane.connection, PROVIDER, MODEL) == "model_validation_failed"


def test_gateway_records_pre_call_configuration(gateway_lane):
    lane = gateway_lane

    def complete(_request):
        ProviderAccountStore(lane.connection).patch_provider_account(
            PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
        )
        return ModelResponse(providerId=PROVIDER, model=MODEL, content="ok", usage=UsageRecord())

    lane.transport.side_effect = complete
    lane.gateway.execute_model_call(lane.plan)
    assert (
        model_validation_rejection(lane.connection, PROVIDER, MODEL)
        == "model_validation_configuration_changed"
    )


def _nvidia_provider(connection, monkeypatch, *, failure="timeout"):
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    store = ProviderAccountStore(connection)
    store.patch_provider_account(
        PROVIDER,
        {
            "enabled": True,
            "providerFamily": "nvidia_nim",
            "deploymentMode": "self_hosted_enterprise",
            "apiFamily": "chat_completions",
            "adapterProfile": "nvidia_openai_chat",
            "baseUrl": "https://nvidia.example.invalid/v1",
            "credentialRef": "",
            "termsMode": "accepted",
            "pricingMode": "configured",
        },
    )
    runtime = RuntimeConfigRepository(connection)
    runtime.set_runtime_setting("runtime.remote.enabled", True)
    runtime.set_runtime_setting("runtime.nvidia.enabled", True)

    def transport(_request):
        if failure == "timeout":
            raise TimeoutError()
        if failure == "urlerror":
            raise URLError("transport unavailable")
        if failure == "json":
            raise json.JSONDecodeError("invalid", "", 0)
        return ProviderHttpResponse(
            statusCode=200,
            jsonBody={
                "model": "wrong-model" if failure == "wrong-model" else MODEL,
                "choices": (
                    [None]
                    if failure == "malformed-choice"
                    else [{"message": {"content": "" if failure == "empty" else "ok"}}]
                ),
            },
        )

    return NvidiaNimProvider(
        provider_id=PROVIDER,
        connection=connection,
        base_url="https://nvidia.example.invalid/v1",
        credential_ref="",
        deployment_mode="self_hosted_enterprise",
        api_family="chat_completions",
        adapter_profile="nvidia_openai_chat",
        transport=transport,
    )


@pytest.mark.parametrize(
    "failure", ["timeout", "urlerror", "json", "empty", "wrong-model", "malformed-choice"]
)
def test_real_nvidia_adapter_broker_invalidates_prior_success_on_attempted_failure(
    broker_lane, monkeypatch, failure
):
    lane = broker_lane
    provider = _nvidia_provider(lane.connection, monkeypatch, failure=failure)
    monkeypatch.setattr(ProviderAdapterFactory, "resolve_for_execution", lambda *_args: provider)
    lane.broker.runtime_adapters = {
        "nvidia_nim": RuntimeAdapterBrokerAdapter(adapter_id="nvidia_nim", connection=lane.connection)
    }
    lane.kwargs["tool_call"]["tool"] = "nvidia_nim"
    lane.connection.execute(
        """UPDATE ai_routing_decisions SET policy_result_json = json_set(policy_result_json,
           '$.decisionEngine.selectionValidation.configurationFingerprint', ?) WHERE id='health-routing'""",
        (provider_configuration_fingerprint(lane.connection, PROVIDER),),
    )
    _record(lane.connection)
    result = lane.broker.evaluate_tool_call(**lane.kwargs)
    assert result["toolCall"]["status"] == "unavailable"
    assert result["toolCall"]["payload"]["executionResult"]["providerAttempted"] is True
    assert model_validation_rejection(lane.connection, PROVIDER, MODEL) == "model_validation_failed"


@pytest.mark.parametrize("source", ["test_prompt", "gateway"])
def test_nvidia_timeout_invalidates_prior_success_in_both_consumers(gateway_lane, monkeypatch, source):
    lane = gateway_lane
    provider = _nvidia_provider(lane.connection, monkeypatch)
    _record(lane.connection)
    if source == "test_prompt":
        result = _run_provider_test_prompt(PROVIDER, MODEL, connection=lane.connection, provider=provider)
        assert result["ok"] is False
    else:
        monkeypatch.setattr(
            "local_control_center.agents.model_gateway.provider_instance", lambda *_a, **_k: provider
        )
        assert lane.gateway.execute_model_call(lane.plan)["status"] == "unavailable"
    assert model_validation_rejection(lane.connection, PROVIDER, MODEL) == "model_validation_failed"


def test_nvidia_missing_credential_is_not_an_attempt(connection, monkeypatch):
    provider = _nvidia_provider(connection, monkeypatch)
    provider.credential_ref = "env:AIDO_ABSENT_MODEL_HEALTH_KEY"
    monkeypatch.delenv("AIDO_ABSENT_MODEL_HEALTH_KEY", raising=False)
    transport = Mock()
    provider.transport = transport
    with pytest.raises(NvidiaNimCapabilityError) as failure:
        provider.chat_completion(ModelRequest(model=MODEL, messages=[{"role": "user", "content": "ok"}]))
    assert failure.value.code == "credential_missing"
    assert failure.value.request_attempted is False
    transport.assert_not_called()
