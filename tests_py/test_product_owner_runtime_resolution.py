"""AIDO-55: resolution evidence in disposable SQLite, never runtime execution."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
from contextlib import closing

import pytest

from local_control_center.agents import codex_compatibility, runtime_provider_config
from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
from local_control_center.agents.product_owner_agent_contract import product_owner_agent_readiness
from local_control_center.agents.runtime_registry import RuntimeRegistry
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.runtime_integrations.config import RUNTIME_COMMAND_ENV
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now


@pytest.fixture(autouse=True)
def no_runtime_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("AIDO-55 must not launch processes, probe runtimes or contact providers")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    # Windows implements socketpair via one loopback connection for the event loop.
    # Permit only that implementation, not arbitrary loopback provider requests.
    pair_scope = threading.local()
    original_pair, original_connect = socket.socketpair, socket.socket.connect

    def internal_socketpair(*args, **kwargs):
        pair_scope.active = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            pair_scope.active = False

    def guarded_connect(self, address):
        if not getattr(pair_scope, "active", False):
            forbidden()
        return original_connect(self, address)

    monkeypatch.setattr(socket, "socketpair", internal_socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(RuntimeRegistry, "detect", forbidden)
    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", forbidden)
    monkeypatch.setattr(codex_compatibility, "run_probe_command", forbidden)
    for spec in runtime_provider_config.RUNTIME_PROVIDER_CONFIG_SPECS:
        for variable in spec.variables:
            for name in (variable.name, *variable.aliases):
                monkeypatch.delenv(name, raising=False)
    for name in RUNTIME_COMMAND_ENV.values():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def po_fixture(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "po-fixture.sqlite")) as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.cli.enabled", True)
        repo.upsert_preferences({"defaultRuntime": "claude_code_cli", "runtimeOrder": ["claude_code_cli"]})
        for runtime_id, name in (("codex_cli", "codex.exe"), ("claude_code_cli", "claude.exe")):
            binary = tmp_path / name
            binary.write_bytes(b"synthetic non-executable fixture: " + runtime_id.encode())
            repo.upsert_installation(
                {
                    "runtimeId": runtime_id,
                    "executablePath": str(binary),
                    "enabled": True,
                    "detectedVersion": "codex-cli 0.153.4" if runtime_id == "codex_cli" else "2.1.218",
                }
            )
            repo.create_runtime_account(
                {
                    "runtimeId": runtime_id,
                    "accountLabel": "fixture",
                    "enabled": True,
                    "isDefault": True,
                    "healthStatus": "healthy",
                    "lastValidationAt": utc_now(),
                    "configurationSource": "test_fixture",
                }
            )
        binary = tmp_path / "codex.exe"
        fingerprint = hashlib.sha256(binary.read_bytes()).hexdigest()
        connection.execute(
            "INSERT INTO codex_capability_probes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(binary.resolve()),
                fingerprint,
                binary.stat().st_size,
                binary.stat().st_mtime_ns,
                "codex-cli 0.153.4",
                " ".join(codex_compatibility.REQUIRED_FLAGS),
                utc_now(),
            ),
        )
        ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())
        yield connection, ProductOwnerAgentRunner(connection, root=tmp_path), binary, fingerprint


def _receipt(connection, fingerprint, *, contract=None):
    contract = contract or codex_compatibility.contract_fingerprint()
    receipt = {
        "status": "validated",
        "version": "codex-cli 0.153.4",
        "binaryFingerprint": fingerprint,
        "contractFingerprint": contract,
    }
    connection.execute(
        """INSERT INTO managed_processes
        (managed_process_id, execution_id, root_pid, workload_class, command_fingerprint,
         started_at, finished_at, exit_code, released_at)
        VALUES ('fixture-receipt', 'fixture-execution', 0, 'agent_cli', 'fixture-only', ?, ?, 0, ?)""",
        (utc_now(), utc_now(), utc_now()),
    )
    connection.execute(
        "INSERT INTO codex_smoke_receipts VALUES (?, ?, ?, ?, ?)",
        ("fixture-receipt", fingerprint, contract, json_dumps(receipt), utc_now()),
    )


@pytest.mark.parametrize(
    "preferred,selected,executable",
    [
        (None, "codex_cli", True),
        ("claude_code_cli", "claude_code_cli", True),
        ("ollama", "ollama", False),
        ("uncatalogued", None, False),
    ],
)
def test_request_preference_never_silently_falls_back(preferred, selected, executable):
    statuses = [
        {"id": "claude_code_cli", "configured": True, "canRunPrompt": True, "capabilities": ["chat"]},
        {"id": "codex_cli", "configured": True, "canRunPrompt": True, "capabilities": ["chat"]},
        {
            "id": "ollama",
            "configured": True,
            "executable": False,
            "capabilities": ["chat"],
            "reason": "offline",
        },
    ]
    result = product_owner_agent_readiness(statuses, preferred_runtime=preferred)
    assert (result["selectedRuntimeId"], result["executable"]) == (selected, executable)
    assert result["candidateRuntimeIds"] == ["codex_cli", "claude_code_cli"]
    if not executable:
        assert result["reason"] and result["status"] == "runtime_unavailable"


def test_no_eligible_candidate_has_no_selection():
    result = product_owner_agent_readiness([{"id": "codex_cli", "executable": False}])
    assert not result["executable"]
    assert result["selectedRuntimeId"] is None
    assert result["candidateRuntimeIds"] == []


def test_runner_explains_selected_identity_without_using_global_preference(po_fixture):
    connection, runner, binary, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    before = connection.total_changes
    result = runner.status()
    assert result["selectedRuntimeId"] == "codex_cli", result
    resolution = result["resolution"]
    assert resolution["source"] == "eligible_cost_order"
    candidate = next(item for item in resolution["candidates"] if item["runtimeId"] == "codex_cli")
    assert candidate["detectedCommand"] == str(binary)
    assert candidate["executableSource"] == "persisted"
    assert candidate["version"] == "codex-cli 0.153.4"
    assert candidate["binaryFingerprint"] == fingerprint
    assert candidate["contractFingerprint"] == codex_compatibility.contract_fingerprint()
    assert candidate["versionVerified"] is False
    assert resolution["permissions"]["permissionProfile"] == "plan"
    assert resolution["permissions"]["executionAuthorized"] is False
    assert resolution["observedEffort"] is None
    assert resolution["remainingQuota"] is None
    assert connection.total_changes == before
    assert "credentialRef" not in json.dumps(result)


def test_project_policy_is_used_by_request_readiness(po_fixture):
    connection, runner, _, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    RuntimeConfigRepository(connection).set_runtime_setting(
        "project.runtime.cli.enabled", False, scope="project", scope_id="fixture-project"
    )
    result = runner.status(preferred_runtime="codex_cli", project_id="fixture-project")
    assert not result["executable"]
    assert result["selectedRuntimeId"] == "codex_cli"
    assert "project.runtime.cli.enabled" in result["reason"]


@pytest.mark.parametrize("receipt_case", ["match", "different-binary", "different-contract", "absent"])
def test_receipt_pair_controls_readiness_and_real_builder(po_fixture, receipt_case):
    from local_control_center.agents.runtime_registry import (
        RuntimeCommandUnavailableError,
        build_product_owner_agent_argv,
        validate_product_owner_runtime_argv,
    )

    connection, runner, binary, fingerprint = po_fixture
    from tests_py.test_model_runtime_gateway import register_workspace

    register_workspace(connection, "fixture-workspace", binary.parent)
    if receipt_case != "absent":
        _receipt(
            connection,
            "0" * 64 if receipt_case == "different-binary" else fingerprint,
            contract="1" * 64 if receipt_case == "different-contract" else None,
        )
    result = runner.status(preferred_runtime="codex_cli")
    expected = receipt_case == "match"
    assert result["executable"] is expected
    assert result["selectedRuntimeId"] == "codex_cli"
    # Claude remains eligible but must not silently replace the explicit choice.
    assert "claude_code_cli" in result["candidateRuntimeIds"]
    kwargs = {
        "runtime": {"id": "codex_cli", "detectedCommand": str(binary)},
        "workspace_id": "fixture-workspace",
        "workspace_path": str(binary.parent),
        "prompt": "synthetic fixture, never sent",
        "model": "fixture-model",
        "agent_id": "product_owner_agent",
        "connection": connection,
    }
    if expected:
        argv = build_product_owner_agent_argv(**kwargs)
        assert argv[0] == str(binary)
        assert argv[argv.index("--model") + 1] == "fixture-model"
        assert "model_reasoning_effort" not in " ".join(argv)
        assert (
            validate_product_owner_runtime_argv(
                runtime_id="codex_cli", argv=argv, workspace_path=str(binary.parent)
            )
            is None
        )
    else:
        assert "validated_smoke_missing" in result["reason"]
        with pytest.raises(RuntimeCommandUnavailableError, match="validated_smoke_missing"):
            build_product_owner_agent_argv(**kwargs)


def test_missing_capability_evidence_is_not_hidden_by_authenticated_state(po_fixture):
    connection, runner, _, _ = po_fixture
    connection.execute("DELETE FROM codex_capability_probes")
    result = runner.status(preferred_runtime="codex_cli")
    assert not result["executable"]
    assert "capability_probe_required" in result["reason"]
    candidate = next(item for item in result["resolution"]["candidates"] if item["runtimeId"] == "codex_cli")
    assert candidate["authenticated"] is True
    assert candidate["binaryFingerprint"] is None
    assert candidate["contractFingerprint"] == codex_compatibility.contract_fingerprint()


def test_environment_path_override_does_not_promote_receipt_or_mutate_preference(po_fixture, monkeypatch):
    connection, runner, binary, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    override = binary.parent / "ruta con espacios ñ"
    override.mkdir()
    target = override / "codex.exe"
    target.write_bytes(b"another synthetic file, not a model")
    monkeypatch.setenv("AIDO_CODEX_COMMAND", str(target))
    before = connection.total_changes
    result = runner.status(preferred_runtime="codex_cli")
    candidate = next(item for item in result["resolution"]["candidates"] if item["runtimeId"] == "codex_cli")
    assert not result["executable"]
    assert candidate["detectedCommand"] == str(target)
    assert candidate["executableSource"] == "environment_override"
    assert candidate["binaryFingerprint"] is None
    assert RuntimeConfigRepository(connection).get_installation("codex_cli")["executablePath"] == str(binary)
    assert RuntimeConfigRepository(connection).get_preferences()["defaultRuntime"] == "claude_code_cli"
    assert connection.total_changes == before


@pytest.mark.parametrize(
    "free_enabled,declared_free,preferred,expected",
    [
        (True, True, None, "codex_cli"),
        (True, False, None, "codex_cli"),
        (False, True, None, "codex_cli"),
        (True, True, "codex_cli", "codex_cli"),
    ],
)
def test_free_gemini_cannot_outrank_an_executor_eligible_runtime(
    free_enabled, declared_free, preferred, expected
):
    result = product_owner_agent_readiness(
        [
            {"id": "codex_cli", "canRunPrompt": True, "capabilities": ["chat"]},
            {
                "id": "gemini",
                "providerFamily": "gemini",
                "executable": free_enabled,
                "capabilities": ["chat"],
                "pricingMode": "free",
                "freeTierDeclaredByOperator": declared_free,
            },
        ],
        preferred_runtime=preferred,
    )
    assert result["selectedRuntimeId"] == expected


def test_no_limit_and_requested_effort_are_not_observations(po_fixture):
    from local_control_center.agents.quota_manager import QuotaManager

    connection, runner, _, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    quota = QuotaManager(connection).check(provider_id="codex_cli", model="fixture-model", request_tokens=1)
    assert quota.allowed and quota.reason == "no_limit"
    RuntimeConfigRepository(connection).upsert_preferences(
        {
            "defaultRuntime": "claude_code_cli",
            "defaultProfiles": {"reasoningEffort": "high"},
        }
    )
    resolution = runner.status(preferred_runtime="codex_cli")["resolution"]
    assert resolution["observedEffort"] is None
    assert resolution["remainingQuota"] is None
    assert "unlimited" not in json.dumps(resolution).lower()


def test_blocking_reason_and_explanation_redact_secrets():
    marker = "Bearer " + "synthetic_sensitive_value"
    result = product_owner_agent_readiness(
        [
            {
                "id": "codex_cli",
                "configured": True,
                "productOwnerExecutable": False,
                "reason": "authentication_required " + marker,
                "credentialRef": marker,
                "metadata": {"private": marker},
                "capabilities": ["chat"],
            },
        ],
        preferred_runtime="codex_cli",
    )
    assert "synthetic_sensitive_value" not in json.dumps(result)
    assert "authentication_required" in result["reason"]


def test_http_request_scope_and_resolution_survive_response_contract(po_fixture):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from local_control_center.agents.api import create_router

    connection, _, binary, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    RuntimeConfigRepository(connection).set_runtime_setting(
        "project.runtime.cli.enabled", False, scope="project", scope_id="fixture-project"
    )
    app = FastAPI()
    app.include_router(
        create_router(
            platform=SimpleNamespace(connection=connection, cwd=binary.parent),
            require_write=lambda request: pytest.fail("Readiness must not request write authority"),
        )
    )
    before = connection.total_changes
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agents/product-owner/status",
            params={
                "preferredRuntime": "codex_cli",
                "projectId": "fixture-project",
            },
        )
        assert response.status_code == 200
        result = response.json()["productOwnerAgent"]
        assert not result["executable"]
        assert result["resolution"]["source"] == "request.preferredRuntime"
        assert result["selectedRuntimeId"] == "codex_cli"
        assert "project.runtime.cli.enabled" in result["reason"]
    assert connection.total_changes == before


def test_builder_rechecks_bytes_even_if_cached_metadata_is_unchanged(po_fixture):
    import os

    from local_control_center.agents.runtime_registry import (
        RuntimeCommandUnavailableError,
        build_product_owner_agent_argv,
    )

    connection, runner, binary, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    previous = binary.stat()
    binary.write_bytes(b"X" * previous.st_size)
    os.utime(binary, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    # GET is a cache-based preview, not a fresh binary measurement. The builder is authoritative.
    assert runner.status(preferred_runtime="codex_cli")["executable"]
    with pytest.raises(RuntimeCommandUnavailableError, match="executable_changed"):
        build_product_owner_agent_argv(
            runtime={"id": "codex_cli", "detectedCommand": str(binary)},
            workspace_id="unused",
            workspace_path=str(binary.parent),
            prompt="fixture never sent",
            agent_id="product_owner_agent",
            connection=connection,
        )


@pytest.mark.parametrize("operation", [None, "product_owner_runtime"])
def test_readiness_does_not_grant_tool_broker_authority(po_fixture, operation):
    from local_control_center.agents.repository import AgentsRepository
    from local_control_center.agents.tool_broker import ToolBroker
    from tests_py.test_model_runtime_gateway import register_workspace

    connection, runner, binary, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    register_workspace(connection, "fixture-workspace", binary.parent)
    assert runner.status(preferred_runtime="codex_cli")["executable"]
    profile = runner._ensure_profile({"id": "codex_cli"})
    run = AgentsRepository(connection).create_agent_run(
        project_id="project-cli",
        agent_profile_id=profile["id"],
        task_id="fixture-only",
        input_payload={},
        output_payload={},
        status="running",
    )
    result = ToolBroker(connection, artifact_root=binary.parent).evaluate_tool_call(
        project_id="project-cli",
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "shell",
            "argv": [str(binary)],
            "execute": True,
            "workspaceId": "fixture-workspace",
            "workspacePath": str(binary.parent),
            "path": str(binary.parent),
            "operation": operation,
            "runtimeId": "codex_cli",
            "providerTransportRequired": True,
        },
    )
    assert result["toolCall"]["status"] == "denied"
    assert result["decision"]["decision"] == "deny"
    expected = (
        "product_owner_internal_operation_denied" if operation else "product_owner_generic_tool_call_denied"
    )
    assert expected in result["decision"]["payload"]["categories"]
    assert connection.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0


def test_run_passes_project_policy_before_any_runtime_attempt(po_fixture):
    from tests_py.test_model_runtime_gateway import register_workspace

    connection, runner, binary, fingerprint = po_fixture
    _receipt(connection, fingerprint)
    register_workspace(connection, "fixture-workspace", binary.parent)
    RuntimeConfigRepository(connection).set_runtime_setting(
        "project.runtime.cli.enabled", False, scope="project", scope_id="project-cli"
    )
    result = runner.run(
        {
            "projectId": "project-cli",
            "workspaceId": "fixture-workspace",
            "taskId": "fixture-only",
            "idea": "fixture that must be blocked before execution",
            "preferredRuntime": "codex_cli",
        }
    )
    assert result["status"] == "runtime_unavailable"
    assert "project.runtime.cli.enabled" in result["reason"]
    assert result["runtimeResult"]["execution"] == "not_executed"
    assert result["runtime"]["policyAllowed"] is False
    assert connection.execute("SELECT COUNT(*) FROM agent_tool_calls").fetchone()[0] == 0
