from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.models import (
    ProcessLaunchSpec,
    ProcessStats,
    SupervisedProcess,
)
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.process_supervision.service import ExecutionCancelled, ProcessSupervisorService
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


class FakeProcess:
    def __init__(self, pid: int = 4321, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class FakeSupervisor:
    def __init__(self) -> None:
        self.started: list[ProcessLaunchSpec] = []
        self.terminated: list[str] = []
        self.released: list[str] = []

    def start(self, spec: ProcessLaunchSpec, **popen_kwargs: Any) -> SupervisedProcess:
        self.started.append(spec)
        return SupervisedProcess(
            managed_process_id=spec.managed_process_id,
            execution_id=spec.execution_id,
            process=FakeProcess(),
            native_handle="fake-job",
        )

    def terminate_tree(
        self, process: SupervisedProcess, *, grace_seconds: float, reason: str
    ) -> ProcessStats:
        self.terminated.append(process.managed_process_id)
        process.process.returncode = -1
        return ProcessStats(exit_code=-1, cancelled=True, termination_reason=reason)

    def stats(self, process: SupervisedProcess) -> ProcessStats:
        return ProcessStats(exit_code=process.process.returncode, peak_memory_bytes=2048)

    def release(self, process: SupervisedProcess) -> None:
        self.released.append(process.managed_process_id)


def test_live_metrics_write_contention_does_not_cancel_healthy_process(tmp_path, monkeypatch):
    import threading
    import time

    backend = FakeSupervisor()
    sampled = threading.Event()
    original = backend.stats

    def stats(process):
        sampled.set()
        return original(process)

    monkeypatch.setattr(backend, "stats", stats)
    service = ProcessSupervisorService(
        db_path=tmp_path / "runtime.sqlite",
        backend=backend,
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    managed = service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
    with open_sqlite_connection(service.db_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            assert sampled.wait(2)
            time.sleep(0.5)
            assert backend.terminated == []
        finally:
            connection.rollback()
            service.complete(managed, exit_code=0)


def test_capture_initialization_failure_releases_the_tree_and_lease(tmp_path, monkeypatch):
    import io

    import local_control_center.process_supervision.service as module

    backend = FakeSupervisor()
    original_start = backend.start

    def start(*args, **kwargs):
        child = original_start(*args, **kwargs)
        child.process.stdout = io.BytesIO()
        return child

    def fail_capture(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backend, "start", start)
    monkeypatch.setattr(module, "ArtifactCapture", fail_capture)
    db_path = tmp_path / "platform.sqlite"
    service = ProcessSupervisorService(
        db_path=db_path, backend=backend, resource_snapshot=ResourceSnapshot.test_snapshot()
    )
    with pytest.raises(OSError, match="disk full"):
        service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
    assert len(backend.terminated) == len(backend.released) == 1
    with open_sqlite_connection(db_path) as connection:
        assert ResourceRepository(connection).active_leases() == []


def test_evidence_failure_still_releases_native_container(tmp_path, monkeypatch):
    backend = FakeSupervisor()
    service = ProcessSupervisorService(
        db_path=tmp_path / "platform.sqlite",
        backend=backend,
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    child = service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
    child.process.returncode = 0

    def fail_evidence(*args):
        raise OSError("evidence unavailable")

    monkeypatch.setattr(service, "_finish_captures", fail_evidence)
    with pytest.raises(OSError, match="evidence unavailable"):
        service.complete(child, exit_code=0)
    assert backend.released == [child.managed_process_id]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["pnpm", "build"], "build_heavy"),
        (["corepack", "pnpm", "exec", "playwright", "test"], "browser_test"),
        (["uv", "run", "semgrep", "scan"], "qa_light"),
        (["python", "-m", "pytest"], "qa_light"),
    ],
)
def test_wrapped_workload_classification(argv, expected):
    from local_control_center.process_supervision.service import classify_workload

    assert classify_workload(argv) == expected


@pytest.mark.parametrize(
    "changes",
    [{"image": "--privileged"}, {"memory": "32g"}, {"cpus": "64"}, {"cpus": "nan"}, {"network": "host"}],
)
def test_docker_budget_and_option_injection_fail_closed(tmp_path, changes):
    from local_control_center.security_policy.sandbox import DockerSandbox

    args = {"image": "test-image", "argv": ["echo", "ok"], "workspace_path": tmp_path, **changes}
    with pytest.raises(ValueError):
        DockerSandbox(docker_executable="docker").build_run_args(**args)


def test_expired_resource_lease_stays_reserved_until_process_cleanup(tmp_path):
    from local_control_center.shared.time import iso_after_seconds, utc_now

    backend = FakeSupervisor()
    db_path = tmp_path / "runtime.sqlite"
    service = ProcessSupervisorService(
        db_path=db_path, backend=backend, resource_snapshot=ResourceSnapshot.test_snapshot()
    )
    child = service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
    with open_sqlite_connection(db_path) as connection:
        future = iso_after_seconds(utc_now(), 600)
        repository = ResourceRepository(connection)
        assert repository.recover_expired(now_iso=future) == []
        assert len(repository.active_leases(now_iso=future)) == 1
    child.process.returncode = 0
    service.complete(child, exit_code=0)
    with open_sqlite_connection(db_path) as connection:
        assert ResourceRepository(connection).active_leases() == []


def test_docker_cleanup_is_durable_and_retains_lease_on_failure(tmp_path, monkeypatch):
    from local_control_center.process_supervision import docker

    db_path = tmp_path / "runtime.sqlite"
    calls = []
    monkeypatch.setattr(docker.HostResourceProbe, "sample", lambda *a, **kw: ResourceSnapshot.test_snapshot())

    def capture(argv, **kwargs):
        calls.append(argv)
        return {
            "timedOut": False,
            "returnCode": 1 if argv[1] == "rm" else 0,
            "stdout": "",
            "stderr": "daemon unavailable",
        }

    monkeypatch.setattr(docker, "run_supervised_capture", capture)
    with (
        execution_scope(ProcessExecutionContext(db_path=db_path)),
        pytest.raises(RuntimeError, match="cleanup"),
    ):
        docker.run_docker_capture(
            ["docker", "run", "--rm", "image", "echo", "ok"], cwd=tmp_path, timeout_seconds=5
        )
    assert calls[0][3] == "--name"
    assert calls[1][1:3] == ["rm", "--force"]
    assert calls[1][3] == calls[0][4]
    with open_sqlite_connection(db_path) as connection:
        assert len(ResourceRepository(connection).active_leases()) == 1
        assert connection.execute("SELECT released_at FROM managed_containers").fetchone()[0] is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object crash integration")
def test_worker_crash_kills_job_and_recovers_partial_evidence(tmp_path):
    import os

    import psutil

    from local_control_center.process_supervision.recovery import recover_managed_processes

    db_path = tmp_path / "runtime.sqlite"
    program = "\n".join(
        [
            "import os,sys,subprocess",
            "from local_control_center.process_supervision.service import ProcessSupervisorService",
            "from local_control_center.host_resources.models import ResourceSnapshot",
            "s=ProcessSupervisorService(db_path=sys.argv[1],resource_snapshot=ResourceSnapshot.test_snapshot())",
            "p=s.start(argv=[sys.executable,'-c',\"import time; print('partial',flush=True); time.sleep(60)\"],cwd=sys.argv[2],stdout=subprocess.PIPE)",
            "p.process.stdout.readline()",
            "os._exit(17)",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(db_path), str(tmp_path)],
        env=os.environ.copy(),
        timeout=15,
        capture_output=True,
    )
    assert result.returncode == 17, result.stderr.decode()
    recovered = recover_managed_processes(db_path)
    assert len(recovered) == 1
    with open_sqlite_connection(db_path) as connection:
        record = ManagedProcessRepository(connection).get(recovered[0])
        assert record.cancelled and record.finished_at
        assert not psutil.pid_exists(record.root_pid)
        assert ResourceRepository(connection).active_leases() == []
        artifact = connection.execute(
            "SELECT hash, metadata, path FROM artifacts WHERE id = ?", (record.stdout_artifact_id,)
        ).fetchone()
        assert artifact[0] and '"partial"' in artifact[1]
        assert Path(artifact[2]).read_text() == "partial\n"


def test_child_completion_preserves_the_parent_job_resource_lease(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        lease = (
            HostResourceGovernor(connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="parent-job", workload_class="agent_cli", owner_id="worker"
                ),
                snapshot=ResourceSnapshot.test_snapshot(),
            )
            .lease
        )
    with execution_scope(
        ProcessExecutionContext(db_path=db_path, execution_id="parent-job", resource_lease_id=lease.id)
    ):
        service = ProcessSupervisorService(backend=FakeSupervisor())
        child = service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
        child.process.returncode = 0
        service.complete(child, exit_code=0)
    with open_sqlite_connection(db_path) as connection:
        assert ResourceRepository(connection).get_lease(lease.id).released_at is None


def test_inherited_lease_reserves_root_before_native_spawn_finishes(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from local_control_center.process_supervision.service import ResourceWaitError

    db = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db) as connection:
        initialize_platform_schema(connection)
        lease = (
            HostResourceGovernor(connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="parent", owner_id="worker", workload_class="agent_cli"
                ),
                snapshot=ResourceSnapshot.test_snapshot(),
            )
            .lease
        )
    entered, proceed = threading.Event(), threading.Event()
    backend = FakeSupervisor()
    original = backend.start

    def paused_start(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return original(*args, **kwargs)

    backend.start = paused_start
    with execution_scope(
        ProcessExecutionContext(db_path=db, execution_id="parent", resource_lease_id=lease.id)
    ):
        first = ProcessSupervisorService(backend=backend)
        second = ProcessSupervisorService(backend=FakeSupervisor())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.start, argv=[sys.executable, "--version"], cwd=tmp_path)
        try:
            assert entered.wait(5)
            with pytest.raises(ResourceWaitError, match="native_root_capacity"):
                other = second.start(argv=[sys.executable, "--version"], cwd=tmp_path)
                second.complete(other, exit_code=0)
        finally:
            proceed.set()
            child = future.result(timeout=5)
            child.process.returncode = 0
            first.complete(child, exit_code=0)


def test_durable_cancel_is_seen_by_running_process_and_blocks_next_stage(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    service = ProcessSupervisorService(db_path=db_path, resource_snapshot=ResourceSnapshot.test_snapshot())
    managed = service.start(
        argv=[
            sys.executable,
            "-c",
            "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)",
        ],
        cwd=tmp_path,
        execution_id="cancel-me",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        import psutil

        child_pid = int(managed.process.stdout.readline().strip())
        with open_sqlite_connection(db_path) as connection:
            ManagedProcessRepository(connection).request_execution_cancel("cancel-me", reason="operator stop")
        managed.process.wait(timeout=6)
        managed.process.stdout.read()
        managed.process.stderr.read()
        record = service.complete(managed, exit_code=managed.process.returncode)
        assert record.cancelled
        assert not psutil.pid_exists(child_pid)
        assert record.termination_reason == "operator stop"
        assert record.stdout_artifact_id and record.stderr_artifact_id
        with pytest.raises(ExecutionCancelled, match="operator stop"):
            service.start(argv=[sys.executable, "--version"], cwd=tmp_path, execution_id="cancel-me")
    finally:
        if not managed.released:
            service.cancel(managed.managed_process_id, reason="test cleanup")


def test_hard_memory_floor_cancels_active_process_and_prevents_next_stage(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from local_control_center.process_supervision import service as module

    backend = FakeSupervisor()
    service = ProcessSupervisorService(
        db_path=tmp_path / "runtime.sqlite",
        backend=backend,
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    child = service.start(argv=[sys.executable, "--version"], cwd=tmp_path, execution_id="memory-test")
    monkeypatch.setattr(module.psutil, "virtual_memory", lambda: SimpleNamespace(available=1))
    try:
        deadline = time.monotonic() + 3
        while child.terminal_stats is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child.terminal_stats and child.terminal_stats.cancelled
        assert child.terminal_stats.termination_reason == "hard_memory_floor"
        with pytest.raises(ExecutionCancelled, match="hard_memory_floor"):
            service.start(argv=[sys.executable, "--version"], cwd=tmp_path, execution_id="memory-test")
    finally:
        service.complete(child, exit_code=child.process.poll())


def test_complete_large_output_is_spilled_and_hashed(tmp_path: Path, controlled_domain_host) -> None:
    count = 1_200_000
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "-c", f"import sys;sys.stdout.write('x'*{count})"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=10,
    )
    assert result["returnCode"] == 0
    assert len(result["stdout"].encode()) <= 4000
    from local_control_center.shared.settings import default_db_path

    with open_sqlite_connection(default_db_path()) as connection:
        artifact = connection.execute(
            "SELECT * FROM artifacts WHERE id = ?", (result["stdoutArtifactId"],)
        ).fetchone()
    data = Path(artifact["path"]).read_bytes()
    assert data == b"x" * count
    assert hashlib.sha256(data).hexdigest() == artifact["hash"]
    assert result["stdoutSha256"] == artifact["hash"]
    import json

    from local_control_center.agents.cli_sessions import CliSessionStore

    with open_sqlite_connection(default_db_path()) as connection:
        session = CliSessionStore(connection).record_result(
            runtime="test",
            executable=sys.executable,
            workspace_id="test",
            command=[sys.executable],
            env_policy={},
            status="completed",
            stdout=result["stdout"],
            process_evidence=result,
        )
        logs = connection.execute(
            "SELECT path FROM artifacts WHERE id=?", (session["logs_artifact_id"],)
        ).fetchone()
        recorded = json.loads(Path(logs["path"]).read_text(encoding="utf-8"))["processEvidence"]
        assert recorded["managedProcessId"] == result["managedProcessId"]
        assert recorded["durationMs"] >= 0
        assert recorded["returnCode"] == 0
        assert session["stdout_artifact_id"] == result["stdoutArtifactId"]


def test_cli_session_command_logs_never_store_the_prompt(tmp_path):
    from local_control_center.agents.cli_sessions import CliSessionStore

    with open_sqlite_connection(tmp_path / "runtime.sqlite") as connection:
        initialize_platform_schema(connection)
        CliSessionStore(connection).record_result(
            runtime="codex_cli",
            executable="codex",
            workspace_id="workspace-test",
            command=["codex", "exec", "--", "private operator request"],
            env_policy={},
            status="blocked",
        )
        row = connection.execute("SELECT command_json FROM cli_sessions").fetchone()
        assert "private operator request" not in row[0]


def test_external_execution_under_transaction_is_rejected_before_spawn(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    backend = FakeSupervisor()
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        with (
            execution_scope(ProcessExecutionContext(db_path=db_path, connection=connection)),
            pytest.raises(RuntimeError, match="transacción"),
        ):
            ProcessSupervisorService(backend=backend).start(argv=[sys.executable, "--version"], cwd=tmp_path)
        connection.rollback()
    assert backend.started == []


def test_emergency_api_requires_token_and_reason_and_records_durable_requests(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from local_control_center.api import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    runtime.init()
    with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
        token = {"X-Local-Control-Token": runtime.get_handshake()["token"]}
        assert client.post("/api/v1/workers/emergency-stop", json={"reason": "stop"}).status_code == 403
        assert (
            client.post("/api/v1/workers/emergency-stop", json={"reason": "  "}, headers=token).status_code
            == 422
        )
        response = client.post(
            "/api/v1/workers/emergency-stop", json={"reason": "host pressure"}, headers=token
        )
        assert response.status_code == 202
        assert response.json()["status"] == "cancel_requested"
        assert client.get("/api/v1/operations/processes").status_code == 200
        assert client.get("/api/v1/workers/status").json()["desiredState"] == "emergency_stopped"
    runtime.close()


def test_service_persists_fingerprint_without_command_or_secret(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    backend = FakeSupervisor()
    service = ProcessSupervisorService(
        db_path=db_path, backend=backend, resource_snapshot=ResourceSnapshot.test_snapshot()
    )

    managed = service.start(
        argv=[sys.executable, "-c", "print('secret-value')"],
        cwd=tmp_path,
        execution_id="execution-one",
        workload_class="agent_cli",
    )
    managed.process.returncode = 0
    service.complete(managed, exit_code=0)

    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        row = connection.execute(
            "SELECT * FROM managed_processes WHERE managed_process_id = ?",
            (managed.managed_process_id,),
        ).fetchone()
    assert row is not None
    assert row["execution_id"] == "execution-one"
    assert row["command_fingerprint"]
    assert "secret-value" not in " ".join(str(value) for value in row)
    assert row["finished_at"]
    assert row["exit_code"] == 0
    assert backend.released == [managed.managed_process_id]


def test_cancel_is_durable_terminates_tree_and_is_idempotent(tmp_path: Path) -> None:
    backend = FakeSupervisor()
    service = ProcessSupervisorService(
        db_path=tmp_path / "platform.sqlite",
        backend=backend,
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    managed = service.start(
        argv=[sys.executable, "--version"],
        cwd=tmp_path,
        execution_id="execution-cancel",
        workload_class="agent_cli",
    )

    first = service.cancel(managed.managed_process_id, reason="operator requested stop")
    second = service.cancel(managed.managed_process_id, reason="operator requested stop")

    assert first.cancelled is True
    assert second.cancelled is True
    assert backend.terminated == [managed.managed_process_id]
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        record = ManagedProcessRepository(connection).get(managed.managed_process_id)
    assert record is not None
    assert record.cancel_requested_at
    assert record.finished_at
    assert record.termination_reason == "operator requested stop"


def test_emergency_stop_only_targets_active_aido_managed_processes(tmp_path: Path) -> None:
    backend = FakeSupervisor()
    service = ProcessSupervisorService(
        db_path=tmp_path / "platform.sqlite",
        backend=backend,
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    first = service.start(
        argv=[sys.executable, "--version"],
        cwd=tmp_path,
        execution_id="execution-a",
        workload_class="qa_light",
    )
    finished = service.start(
        argv=[sys.executable, "--version"],
        cwd=tmp_path,
        execution_id="execution-finished",
        workload_class="qa_light",
    )
    finished.process.returncode = 0
    service.complete(finished, exit_code=0)

    stopped = service.emergency_stop(reason="host under pressure")

    assert stopped == [first.managed_process_id]
    assert backend.terminated == [first.managed_process_id]


def test_restricted_sandbox_exposes_managed_process_evidence(
    tmp_path: Path, monkeypatch, controlled_domain_host
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "-c", "print('managed')"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=5,
    )

    assert result["returnCode"] == 0
    assert result["managedProcessId"]
    assert result["workloadClass"] == "agent_cli"
    assert result["peakMemoryBytes"] >= 0
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        record = ManagedProcessRepository(connection).get(result["managedProcessId"])
    assert record is not None
    assert record.finished_at


def test_productive_process_creation_is_architecturally_centralized() -> None:
    root = Path(__file__).parents[1] / "local_control_center"
    allowed_files = {
        Path("nvidia_nim/system_probe.py"),
    }
    violations: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] == "process_supervision" or relative in allowed_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "subprocess"
        }
        functions = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
            for alias in node.names
            if alias.name in {"Popen", "run", "check_output", "check_call"}
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in functions:
                violations.append(f"{relative.as_posix()}:{node.lineno}:{node.func.id}")
            if not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                isinstance(owner, ast.Name)
                and owner.id in modules
                and node.func.attr in {"Popen", "run", "check_output", "check_call"}
            ):
                violations.append(f"{relative.as_posix()}:{node.lineno}:{node.func.attr}")

    assert violations == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_creates_separate_group_before_any_control_signal(
    tmp_path: Path, monkeypatch, controlled_domain_host
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    original = subprocess.Popen

    def checked_spawn(*args, **kwargs):
        if str(args[0][0]) == sys.executable:
            assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
            assert kwargs["creationflags"] & 0x4  # CREATE_SUSPENDED
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", checked_spawn)
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "-c", "print('contained')"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=5,
    )
    assert result["returnCode"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_timeout_kills_the_contained_descendant_tree(
    tmp_path: Path, monkeypatch, controlled_domain_host
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    result = RestrictedSubprocessSandbox().execute(
        argv=[
            sys.executable,
            "-c",
            (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                "print('tree-started', flush=True); time.sleep(30)"
            ),
        ],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=1,
        truncate_output=False,
    )

    assert result["timedOut"] is True
    assert result["terminationReason"] == "timeout"
    assert result["remainingDescendantCount"] == 0
