"""Typed local coordination and joint budget, without model execution."""

import pytest
from pydantic import ValidationError

from tests_py.test_launcher_capture_http import offline_native_cli as offline_native_cli


@pytest.mark.parametrize("condition", ["owner", "cycle", "depth", "absent"])
def test_session_ancestry_is_bounded_and_stops_at_verified_owner(condition):
    from local_control_center.process_supervision import session_client

    calls = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def parent(self):
            calls.append(self.pid)
            if condition == "owner":
                assert self.pid != 2, "Never enumerate ancestors beyond the already verified owner"
                return Process(2)
            if condition == "cycle":
                return Process(1)
            if condition == "depth":
                return Process(self.pid + 1)
            return None

    assert session_client._has_verified_ancestor(Process(1), 2 if condition == "owner" else 1000) is (
        condition == "owner"
    )
    assert len(calls) <= 64


def test_joint_budget_keeps_global_cpu_and_memory_headroom():
    from local_control_center.process_supervision.launcher_session import SESSION_BUDGET

    assert SESSION_BUDGET["memoryBytes"] == 18 * 1024**3
    assert SESSION_BUDGET["cpuPercent"] == 65
    assert sum(part["memoryBytes"] for part in SESSION_BUDGET["parts"].values()) == 18 * 1024**3
    assert sum(part["cpuPercent"] for part in SESSION_BUDGET["parts"].values()) == 65


def test_acceptance_evidence_preserves_receipts_from_prior_runs(tmp_path, monkeypatch):
    import json

    from tests_py.operational_acceptance_support import evidence

    monkeypatch.setenv("AIDO_ACCEPTANCE_EVIDENCE", str(tmp_path))
    historical = tmp_path / "referenced.json"
    historical.write_text('{"status":"HISTORICAL"}', encoding="utf-8")
    evidence("referenced", {"status": "FAIL"})
    evidence("referenced", {"status": "PASS"})
    assert historical.read_text(encoding="utf-8") == '{"status":"HISTORICAL"}'
    current = list(tmp_path.glob("referenced-*.json"))
    assert len(current) == 2
    assert {json.loads(path.read_text())["status"] for path in current} == {"FAIL", "PASS"}


def test_canonical_launcher_help_documents_the_real_opt_in(monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "launcher_help_contract", Path("local-control-center/scripts/start_control_center.py")
    )
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(sys, "argv", ["start_control_center.py", "--help"])
    with pytest.raises(SystemExit) as result:
        launcher.parse_args()
    assert result.value.code == 0
    assert "--capture-session" in capsys.readouterr().out


def test_shared_control_scope_rejects_explicit_memory_above_its_part(tmp_path, monkeypatch):
    from local_control_center.process_supervision import session_client
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
    from local_control_center.process_supervision.service import ProcessSupervisorService, ResourceWaitError

    monkeypatch.setattr(session_client, "session_identity", lambda db: ({"aggregateId": "owned"}, None))
    context = ProcessExecutionContext(
        db_path=tmp_path / "never-opened.sqlite", aggregate_managed_process_id="owned", session_role="api"
    )
    with execution_scope(context), pytest.raises(ResourceWaitError, match="session_role_memory"):
        ProcessSupervisorService().start(
            argv=["must-not-start"],
            cwd=tmp_path,
            workload_class="control_plane",
            memory_limit_bytes=8 * 1024**3,
        )


@pytest.mark.parametrize("extra", [{"argv": ["arbitrary.exe"]}, {"pid": 123}, {"db": "original.sqlite"}])
def test_internal_request_does_not_accept_commands_pids_or_database_paths(extra):
    from local_control_center.process_supervision.launcher_session import SessionRequest

    with pytest.raises(ValidationError):
        SessionRequest.model_validate(
            {
                "action": "dispatch",
                "executionId": "execution-test",
                "attemptId": "attempt-test",
                "ownerId": "worker-test",
                "fencingToken": 1,
                **extra,
            }
        )


def test_joint_admission_preserves_headroom_and_does_not_create_independent_child_capacity(tmp_path):
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
    from local_control_center.host_resources.repository import ResourceRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "policy.sqlite")
    try:
        runtime.init()
        governor = HostResourceGovernor(runtime.connection)
        request = ResourceAdmissionRequest(
            execution_id="session", owner_id="launcher", workload_class="capture_session"
        )
        insufficient = governor.admit(
            request, snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=33 * 1024**3)
        )
        assert insufficient.reason_code == "aggregate_memory_budget" and insufficient.lease is None
        available = ResourceSnapshot.test_snapshot(available_memory_bytes=34 * 1024**3)
        admitted = governor.admit(request, snapshot=available)
        assert admitted.lease.memory_limit_bytes == 18 * 1024**3
        assert admitted.lease.cpu_limit_percent == 65
        assert governor.admit(request, snapshot=available).lease.id == admitted.lease.id
        separate = governor.admit(
            ResourceAdmissionRequest(execution_id="independent", owner_id="other", workload_class="qa_light"),
            snapshot=available,
        )
        assert separate.reason_code == "aggregate_cpu_budget"
        assert len(ResourceRepository(runtime.connection).active_leases()) == 1
    finally:
        runtime.close()


def test_native_offline_fixture_compiles_and_answers_only_readonly_probes(offline_native_cli):
    """Run no production session, no auth probe and no model command."""
    from local_control_center.process_supervision.service import run_probe_command

    for argv in ([str(offline_native_cli), "--version"], [str(offline_native_cli), "--help"]):
        result = run_probe_command(argv, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0
        assert "codex-cli" in result.stdout or "--sandbox" in result.stdout


def test_recovery_never_releases_a_lease_still_backing_a_live_aggregate(tmp_path, monkeypatch):
    """Unit OS-identity substitution; not evidence of native cleanup."""
    import os
    from dataclasses import replace

    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
    from local_control_center.host_resources.repository import ResourceRepository
    from local_control_center.process_supervision import recovery
    from local_control_center.process_supervision.models import ProcessLaunchSpec
    from local_control_center.process_supervision.repository import ManagedProcessRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "recovery.sqlite")
    try:
        runtime.init()
        connection = runtime.connection
        lease = (
            HostResourceGovernor(connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="aggregate", owner_id="launcher", workload_class="capture_session"
                ),
                snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=40 * 1024**3),
            )
            .lease
        )
        spec = ProcessLaunchSpec(
            "aggregate",
            "aggregate",
            [],
            str(tmp_path),
            "capture_session",
            "synthetic",
            18 * 1024**3,
            64,
            65,
            False,
        )
        repository = ManagedProcessRepository(connection)
        repository.start(spec, root_pid=os.getpid(), resource_lease_id=lease.id)
        repository.start(
            replace(spec, managed_process_id="child", execution_id="execution"),
            root_pid=os.getpid(),
            resource_lease_id=lease.id,
        )
        connection.execute(
            "UPDATE managed_processes SET owner_pid=100, owner_create_time=1, root_pid=101, root_create_time=1 WHERE managed_process_id='child'"
        )
        monkeypatch.setattr(recovery, "identity_alive", lambda pid, created: pid == os.getpid())
        assert recovery.recover_managed_processes(runtime.db_path) == ["child"]
        assert ResourceRepository(connection).get_lease(lease.id).released_at is None
    finally:
        runtime.close()
