"""CLI preflight regression: fake transport only; real SQLite, policy and accounting."""

from __future__ import annotations

import json
import uuid
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents import runtime_preflight
from local_control_center.agents import runtime_preflight_cli as cli
from local_control_center.agents.ai_resource_manager import AIResourceRequest
from local_control_center.agents.codex_compatibility import CodexCompatibilityService
from local_control_center.agents.codex_smoke import SMOKE_MARKER, SMOKE_PROMPT
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.models import ProcessLaunchSpec, ProcessStats
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.process_supervision.service import command_fingerprint
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now
from local_control_center.workspaces_projects.repository import WorkspaceIsolationError, WorkspacesRepository

_REAL_CODEX_STATUS = CodexCompatibilityService.status


def _codex_output(text="ok", item_type="agent_message"):
    return "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": item_type, "text": text}},
            {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 1}},
        ]
    )


@pytest.fixture
def lane(tmp_path, monkeypatch):
    database = tmp_path / "cli-preflight.sqlite"
    with closing(open_sqlite_connection(database)) as connection:
        initialize_platform_schema(connection)
        project = tmp_path / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text(
            "This file must never reach the prompt workspace.", encoding="utf-8"
        )
        connection.execute(
            "INSERT INTO projects (id,name,path,template_id,source,status,metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("project", "fixture", str(project), "manual", "manual", "active", "{}", utc_now(), utc_now()),
        )
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.cli.enabled", True)
        store = ProviderAccountStore(connection)
        statuses = {}
        models = {}
        for provider, name in [("codex_cli", "codex.exe"), ("claude_code_cli", "claude.exe")]:
            binary = tmp_path / name
            binary.write_bytes(b"FAKE test fixture; never execute")
            repo.upsert_installation({"runtimeId": provider, "executablePath": str(binary), "enabled": True})
            connection.execute("UPDATE provider_accounts SET enabled=1 WHERE provider_id=?", (provider,))
            connection.execute("UPDATE runtime_accounts SET enabled=1 WHERE runtime_id=?", (provider,))
            connection.execute(
                "UPDATE runtime_capabilities SET enabled=1 WHERE runtime=? AND capability='chat'", (provider,)
            )
            models[provider] = store.upsert_model(
                {"providerId": provider, "model": "fixture-model", "enabled": True}
            )
            statuses[provider] = {"id": provider, "kind": "cli", "detectedCommand": str(binary)}
        monkeypatch.setattr(runtime_preflight, "_in_quality_environment", lambda: False)
        from local_control_center.agents import codex_compatibility, runtime_registry

        monkeypatch.setattr(
            codex_compatibility.CodexCompatibilityService, "status", lambda *a, **k: {"status": "compatible"}
        )
        home_root = tmp_path / "isolated-home"
        private_home = home_root / str(uuid.uuid4())
        private_home.mkdir(parents=True)
        monkeypatch.setattr(runtime_registry, "_product_owner_codex_home_root", lambda: home_root)

        @contextmanager
        def isolated_environment():
            # No test ever reads/copies the operator's native credential files.
            yield {"CODEX_HOME": str(private_home)}

        monkeypatch.setattr(cli, "isolated_product_owner_codex_environment", isolated_environment)
        state = SimpleNamespace(
            connection=connection,
            database=database,
            project=project,
            models=models,
            statuses=statuses,
            calls=[],
            output=None,
            stderr="",
            ledger=True,
            failure=None,
            timed_out=False,
            mutate_configuration=False,
            expected_prompt=cli.PREFLIGHT_PROMPT,
            after_transport=None,
        )

        def fake_execute(_self, **kwargs):
            assert not connection.in_transaction
            assert kwargs["timeout_seconds"] == 30 and kwargs["truncate_output"] is False
            cwd = Path(kwargs["cwd"])
            assert cwd != project and project not in cwd.parents and not list(cwd.iterdir())
            assert kwargs["argv"][-2:] == ["--", state.expected_prompt]
            assert kwargs["argv"][kwargs["argv"].index("--model") + 1] == "fixture-model"
            state.calls.append(kwargs)
            if state.failure:
                raise state.failure
            codex = Path(kwargs["argv"][0]).name == "codex.exe"
            output = state.output if state.output is not None else _codex_output() if codex else "ok"
            result = {
                "returnCode": 0,
                "stdout": output,
                "stderr": state.stderr,
                "remainingDescendantCount": 0,
                "stdoutCaptureTruncated": False,
                "timedOut": state.timed_out,
            }
            if not state.ledger:
                return result
            identity = f"fake-managed-{uuid.uuid4()}"
            governor = HostResourceGovernor(connection)
            admission = governor.admit(
                ResourceAdmissionRequest(
                    execution_id=identity, owner_id=identity, workload_class="agent_cli"
                ),
                snapshot=ResourceSnapshot.test_snapshot(),
            )
            assert admission.lease is not None
            lease = admission.lease
            processes = ManagedProcessRepository(connection)
            processes.start(
                ProcessLaunchSpec(
                    managed_process_id=identity,
                    execution_id="execution",
                    argv=kwargs["argv"],
                    cwd=str(cwd),
                    workload_class="agent_cli",
                    command_fingerprint=command_fingerprint(kwargs["argv"]),
                    memory_limit_bytes=lease.memory_limit_bytes,
                    process_limit=lease.process_limit,
                    cpu_limit_percent=lease.cpu_limit_percent,
                    below_normal_priority=False,
                ),
                root_pid=123456789,
                resource_lease_id=lease.id,
            )
            processes.finish(identity, stats=ProcessStats(exit_code=0, timed_out=state.timed_out))
            governor.release(lease.id, reason="fake_transport_finished")
            if state.mutate_configuration:
                connection.execute(
                    "UPDATE runtime_installations SET detected_version='changed' WHERE runtime_id=?",
                    ("codex_cli" if codex else "claude_code_cli",),
                )
            if state.after_transport:
                state.after_transport(identity)
            return {**result, "managedProcessId": identity}

        monkeypatch.setattr(cli.RestrictedSubprocessSandbox, "execute", fake_execute)
        state.context = ProcessExecutionContext(
            db_path=database,
            execution_id="execution",
            project_id="project",
            connection=connection,
            in_job_runner=True,
        )
        state.request = AIResourceRequest(
            task_type="product_owner.discovery",
            project_id="project",
            allow_unknown_cost=True,
            require_approval_for_unknown_cost=False,
        )
        yield state


def _run(lane, provider="claude_code_cli", request=None, context=None):
    with execution_scope(context or lane.context):
        return cli.validate_cli_candidate(
            lane.connection,
            model=lane.models[provider],
            runtime_status=lane.statuses[provider],
            request=request or lane.request,
        )


def test_weekly_cli_limit_is_preserved_and_excludes_account(lane):
    lane.output = "You've hit your weekly limit · resets Sep 22, 10pm (America/Santiago)"
    result = _run(lane)
    assert result["attempted"] and not result["success"]
    assert result["failureCause"] == "quota_exhausted"
    assert "weekly limit" in result["failureEvidence"]
    assert result["httpStatus"] is None
    assert cli.QuotaManager(lane.connection).providers_in_cooldown() == {"claude_code_cli"}
    observation = lane.connection.execute(
        "SELECT model,metadata_json FROM provider_limit_observations WHERE provider_id='claude_code_cli'"
    ).fetchone()
    assert observation["model"] == "*"
    assert json.loads(observation["metadata_json"])["statusCode"] is None
    assert (
        lane.connection.execute(
            "SELECT last_429_at FROM provider_limits WHERE provider_id='claude_code_cli' AND model='*'"
        ).fetchone()[0]
        is None
    )
    before = len(lane.calls)
    another = _run(lane)
    assert not another["attempted"] and len(lane.calls) == before


def test_valid_marker_with_quota_error_is_not_healthy(lane):
    lane.output = "ok"
    lane.stderr = "ERROR: You've hit your weekly limit"
    result = _run(lane)
    assert not result["success"]
    assert result["failureCause"] == "quota_exhausted"
    assert lane.connection.execute("SELECT success FROM model_execution_health").fetchone()[0] == 0


@pytest.mark.parametrize("provider", ["codex_cli", "claude_code_cli"])
def test_fixed_prompt_proves_completion_and_cleans_workspace(lane, provider):
    result = _run(lane, provider)
    assert result["success"] and result["status"] == "completed"
    assert result["attempted"] and result["managedProcessId"]
    assert result["estimatedCostUsd"] is None and result["costStatus"] == "unknown"
    assert result["usage"]["actualCostUsd"] is None
    assert not Path(lane.calls[0]["cwd"]).exists()
    assert (lane.project / "AGENTS.md").exists()
    assert lane.connection.execute("SELECT success FROM model_execution_health").fetchone()[0] == 1
    assert lane.connection.execute("SELECT COUNT(*) FROM cli_sessions").fetchone()[0] == 1
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
    assert lane.connection.execute("SELECT status FROM workspaces").fetchone()[0] == "archived"


@pytest.mark.parametrize("case", ["quality", "preview", "cost", "policy", "capability", "disabled_model"])
def test_fail_closed_before_transport(lane, monkeypatch, case):
    request = lane.request
    context = lane.context
    if case == "quality":
        monkeypatch.setattr(runtime_preflight, "_in_quality_environment", lambda: True)
    elif case == "preview":
        context = replace(context, in_job_runner=False)
    elif case == "cost":
        request = replace(request, require_approval_for_unknown_cost=True)
    elif case == "policy":
        RuntimeConfigRepository(lane.connection).set_runtime_setting("runtime.cli.enabled", False)
    elif case == "capability":
        lane.connection.execute("UPDATE runtime_capabilities SET enabled=0 WHERE capability='chat'")
    else:
        lane.connection.execute("UPDATE model_catalog SET enabled=0")
    result = _run(lane, request=request, context=context)
    assert not result["success"] and not result["attempted"] and not lane.calls


@pytest.mark.parametrize(
    "case", ["missing_ledger", "wrong_answer", "timeout", "configuration_changed", "transport_error"]
)
def test_transport_alone_never_certifies_model_and_always_archives(lane, case):
    if case == "missing_ledger":
        lane.ledger = False
    elif case == "wrong_answer":
        lane.output = "authenticated cli version 1.0"
    elif case == "timeout":
        lane.timed_out = True
    elif case == "configuration_changed":
        lane.mutate_configuration = True
    else:
        lane.failure = OSError("synthetic transport failure")
    result = _run(lane)
    assert not result["success"]
    assert not Path(lane.calls[0]["cwd"]).exists()
    assert (
        lane.connection.execute("SELECT COUNT(*) FROM model_execution_health WHERE success=1").fetchone()[0]
        == 0
    )
    assert (
        lane.connection.execute(
            "SELECT COUNT(*) FROM provider_execution_leases WHERE state IN ('active','dispatched')"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "output",
    [
        "ok",
        "not json",
        _codex_output("not ok"),
        _codex_output(item_type="command_execution"),
        _codex_output() + '\n{"type":"error"}',
    ],
)
def test_codex_output_requires_exact_answer_without_tools(output):
    assert not cli._output_is_ok("codex_cli", output)


def test_bootstrap_workspace_does_not_weaken_product_owner_source_requirement(lane):
    repository = WorkspacesRepository(lane.connection, root=lane.database.parent)
    for missing in [None, ""]:
        with pytest.raises(WorkspaceIsolationError):
            repository.allocate_prompt_workspace(
                project_id="project",
                task_id="test",
                agent_id="product_owner_agent",
                source_workspace_id=missing,
                reason="test",
            )


def test_legacy_false_is_not_runtime_policy(lane, monkeypatch):
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")
    assert _run(lane)["success"]


def _codex_without_smoke(lane, monkeypatch, *, initial="missing_smoke", missing_flag=False):
    """Real compatibility SQLite evidence; help/version and inference transports are fake."""
    from local_control_center.agents import codex_compatibility

    monkeypatch.setattr(CodexCompatibilityService, "status", _REAL_CODEX_STATUS)
    probes = []

    def fake_probe(argv, **_kwargs):
        assert argv[1:] in [["--version"], ["exec", "--help"], ["--help"]]
        probes.append(argv)
        flags = codex_compatibility.REQUIRED_FLAGS - ({"--strict-config"} if missing_flag else set())
        return SimpleNamespace(
            returncode=0,
            stdout="codex-cli 0.155.1" if argv[-1] == "--version" else " ".join(sorted(flags)),
        )

    monkeypatch.setattr(codex_compatibility, "run_probe_command", fake_probe)
    executable = lane.statuses["codex_cli"]["detectedCommand"]
    if initial != "missing_probe":
        CodexCompatibilityService(lane.connection).probe(executable)
        probes.clear()
    if initial == "changed_binary":
        Path(executable).write_bytes(b"CHANGED fake fixture; never execute")
    lane.expected_prompt = SMOKE_PROMPT
    lane.output = _codex_output(SMOKE_MARKER)
    return probes


@pytest.mark.parametrize("initial", ["missing_smoke", "missing_probe", "changed_binary"])
def test_codex_preflight_repairs_missing_smoke_with_one_selected_model_attempt(lane, monkeypatch, initial):
    from local_control_center.agents.cli_runtimes.base import RuntimeRequest
    from local_control_center.agents.codex_smoke import claim_smoke_approval
    from local_control_center.agents.runtime_registry import (
        RuntimeCommandUnavailableError,
        build_product_owner_agent_argv,
    )

    probes = _codex_without_smoke(lane, monkeypatch, initial=initial)
    # Normal ProductOwner execution remains closed until the real receipt exists.
    with pytest.raises(RuntimeCommandUnavailableError):
        build_product_owner_agent_argv(
            runtime=lane.statuses["codex_cli"],
            workspace_id="missing",
            workspace_path=str(lane.project),
            prompt="A project task is not a smoke",
            model="fixture-model",
            agent_id="product_owner_agent",
            connection=lane.connection,
        )
    result = _run(lane, "codex_cli")
    assert result["success"] and result["attempted"]
    assert len(lane.calls) == 1
    assert len(probes) == (0 if initial == "missing_smoke" else 3)
    receipt = json.loads(
        lane.connection.execute("SELECT receipt_json FROM codex_smoke_receipts").fetchone()[0]
    )
    assert receipt["status"] == "validated" and receipt["model"] == "fixture-model"
    assert receipt["managedProcessId"] == result["managedProcessId"]
    assert (
        CodexCompatibilityService(lane.connection).status(
            lane.statuses["codex_cli"]["detectedCommand"], verify_hash=True
        )["status"]
        == "compatible"
    )
    assert (
        lane.connection.execute(
            "SELECT success FROM model_execution_health WHERE provider_id='codex_cli' AND model='fixture-model'"
        ).fetchone()[0]
        == 1
    )
    approval = lane.connection.execute(
        "SELECT id,target,payload FROM audit_events WHERE action='runtime.codex.smoke_approved'"
    ).fetchone()
    assert approval is not None and receipt["approvalAuditId"] == approval["id"]
    assert json.loads(approval["payload"])["commandFingerprint"] == command_fingerprint(lane.calls[0]["argv"])
    assert (
        lane.connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='runtime.codex.smoke_claimed' AND target=?",
            (approval["id"],),
        ).fetchone()[0]
        == 1
    )
    replay = RuntimeRequest(
        runtime="codex_cli",
        workspaceId=approval["target"],
        workspacePath=lane.calls[0]["cwd"],
        prompt=SMOKE_PROMPT,
        model="fixture-model",
        role="product_owner",
        envPolicy={"permissionProfile": "plan", "network": False, "secrets": False},
    )
    assert not claim_smoke_approval(lane.connection, approval["id"], replay, lane.calls[0]["argv"])
    assert lane.connection.execute("SELECT COUNT(*) FROM cli_sessions").fetchone()[0] == 1
    assert not Path(lane.calls[0]["cwd"]).exists()


@pytest.mark.parametrize(
    "case",
    [
        "wrong_answer",
        "tool_output",
        "after_completion",
        "missing_ledger",
        "wrong_execution",
        "binary_changed",
        "configuration_changed",
    ],
)
def test_codex_bootstrap_never_certifies_invalid_execution(lane, monkeypatch, case):
    _codex_without_smoke(lane, monkeypatch)
    if case == "wrong_answer":
        lane.output = _codex_output("ok")
    elif case == "tool_output":
        lane.output = _codex_output(SMOKE_MARKER, item_type="command_execution")
    elif case == "after_completion":
        lane.output += '\n{"type":"thread.started"}'
    elif case == "missing_ledger":
        lane.ledger = False
    elif case == "wrong_execution":
        lane.after_transport = lambda identity: lane.connection.execute(
            "UPDATE managed_processes SET execution_id='unrelated' WHERE managed_process_id=?", (identity,)
        )
    elif case == "binary_changed":
        lane.after_transport = lambda _identity: Path(
            lane.statuses["codex_cli"]["detectedCommand"]
        ).write_bytes(b"changed during execution")
    else:
        lane.mutate_configuration = True
    result = _run(lane, "codex_cli")
    assert len(lane.calls) == 1 and not result["success"]
    assert not lane.connection.execute("SELECT 1 FROM model_execution_health WHERE success=1").fetchone()
    assert not lane.connection.execute(
        "SELECT 1 FROM codex_smoke_receipts WHERE json_extract(receipt_json,'$.status')='validated'"
    ).fetchone()


@pytest.mark.parametrize("case", ["quality", "preview", "cost", "policy", "disabled_model", "quota"])
def test_codex_bootstrap_does_not_probe_before_authorization(lane, monkeypatch, case):
    probes = _codex_without_smoke(lane, monkeypatch, initial="missing_probe")
    request, context = lane.request, lane.context
    if case == "quality":
        monkeypatch.setattr(runtime_preflight, "_in_quality_environment", lambda: True)
    elif case == "preview":
        context = replace(context, in_job_runner=False)
    elif case == "cost":
        request = replace(request, require_approval_for_unknown_cost=True)
    elif case == "policy":
        RuntimeConfigRepository(lane.connection).set_runtime_setting("runtime.cli.enabled", False)
    elif case == "disabled_model":
        lane.connection.execute("UPDATE model_catalog SET enabled=0 WHERE provider_id='codex_cli'")
    else:
        cli.QuotaManager(lane.connection).record_rate_limit(
            provider_id="codex_cli", model="*", error_class="runtime_usage_limit", status_code=None
        )
    result = _run(lane, "codex_cli", request=request, context=context)
    assert not result["attempted"] and not lane.calls and not probes
    assert not lane.connection.execute(
        "SELECT 1 FROM audit_events WHERE action='runtime.codex.smoke_approved'"
    ).fetchone()


def test_codex_bootstrap_cannot_repair_missing_isolation_flag(lane, monkeypatch):
    probes = _codex_without_smoke(lane, monkeypatch, initial="missing_probe", missing_flag=True)
    result = _run(lane, "codex_cli")
    assert len(probes) == 3
    assert not result["attempted"] and not lane.calls
    assert not lane.connection.execute("SELECT 1 FROM codex_smoke_receipts").fetchone()


def test_compatible_codex_preflight_does_not_add_smoke_or_capability_probe(lane, monkeypatch):
    from local_control_center.agents import codex_compatibility

    monkeypatch.setattr(
        codex_compatibility, "run_probe_command", lambda *_a, **_k: pytest.fail("Unexpected probe")
    )
    result = _run(lane, "codex_cli")
    assert result["success"] and len(lane.calls) == 1
    assert lane.calls[0]["argv"][-1] == cli.PREFLIGHT_PROMPT
    assert not lane.connection.execute("SELECT 1 FROM codex_smoke_receipts").fetchone()
