"""Canonical opt-in launcher -> HTTP -> real worker/dispatcher -> offline native load."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import closing, suppress
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request, urlopen

import psutil
import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.process_supervision.native_diagnostics import private_directory
from local_control_center.quality.__main__ import _run as run_quality_step
from local_control_center.quality.plans import QualityStep
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.diagnostics import activate_attempt
from tests_py.operational_acceptance_support import (
    evidence,
    identities_gone,
    native_readback,
    quality_envelope,
    wait_until,
)
from tests_py.test_model_runtime_gateway import register_workspace

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native capture session")


def _verify_live_jobs(rows, *, diagnostic):
    """Concrete owned handles, not NULL or a process-tree inference about Job nesting."""
    import win32job

    from local_control_center.process_supervision.windows_job import job_name

    result = {}
    for row in rows:
        handle = win32job.OpenJobObject(win32job.JOB_OBJECT_QUERY, False, job_name(row["managed_process_id"]))
        try:
            result[row["managed_process_id"]] = native_readback(SimpleNamespace(native_handle=handle))
        finally:
            handle.Close()
    aggregate = next(row for row in rows if row["workload_class"] == "capture_session")
    controls = [row for row in rows if row["workload_class"] == "control_plane"]
    agents = [row for row in rows if row["workload_class"] == "agent_cli"]
    dispatcher, target = sorted(agents, key=lambda row: row["started_at"])

    def members(row):
        return {member["pid"] for member in result[row["managed_process_id"]]["members"]}

    for row, memory, cpu in [
        (aggregate, 18, 6500),
        (dispatcher, 8, 4000),
        (target, 8, 10000),
        *((row, 2, 1000) for row in controls),
    ]:
        readback = result[row["managed_process_id"]]
        assert readback["memoryBytes"] == memory * 1024**3
        assert readback["cpuFlags"] == 5 and readback["cpuRate"] == cpu
        assert readback["limitFlags"] & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert row["root_pid"] in members(row) and row["root_pid"] in members(aggregate)
    assert target["root_pid"] in members(dispatcher)
    assert all(target["root_pid"] not in members(row) for row in controls)
    if diagnostic:
        collector = next(row for row in rows if row["workload_class"] == "qa_light")
        readback = result[collector["managed_process_id"]]
        assert readback["memoryBytes"] == 4 * 1024**3 and readback["cpuRate"] == 2000
        assert collector["root_pid"] in members(aggregate)
        assert collector["root_pid"] not in members(dispatcher) | members(target)
        assert target["root_pid"] not in members(collector)
        assert collector["owner_pid"] == aggregate["root_pid"]
    return result


@pytest.fixture(scope="module")
def offline_native_cli(tmp_path_factory):
    vc = Path("C:/Program Files/Microsoft Visual Studio/18/Community/VC/Tools/MSVC/14.50.35717")
    sdk = Path("C:/Program Files (x86)/Windows Kits/10")
    folder = tmp_path_factory.mktemp("native-http")
    target = folder / "codex.exe"
    result = subprocess.run(
        [
            str(vc / "bin/Hostx64/x64/cl.exe"),
            "/nologo",
            "/c",
            "/Zi",
            "/Oi",
            *[
                f"/I{path}"
                for path in [
                    vc / "include",
                    sdk / "Include/10.0.26100.0/um",
                    sdk / "Include/10.0.26100.0/shared",
                    sdk / "Include/10.0.26100.0/ucrt",
                ]
            ],
            f"/Fd{folder / 'compile.pdb'}",
            f"/Fo{folder / 'codex.obj'}",
            str(Path("tests_py/fixtures/watchdog/native_http.cpp").resolve()),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(
        [
            str(vc / "bin/Hostx64/x64/link.exe"),
            "/NOLOGO",
            "/NODEFAULTLIB",
            "/ENTRY:mainCRTStartup",
            "/SUBSYSTEM:CONSOLE",
            "/DEBUG",
            f"/OUT:{target}",
            str(folder / "codex.obj"),
            str(sdk / "Lib/10.0.26100.0/um/x64/kernel32.lib"),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return target


@pytest.mark.parametrize(
    "condition",
    [
        "normal",
        "diagnostic-normal",
        "fastfail",
        "pressure",
        "cancel",
        "fence",
        "collector-unavailable",
        "owner-crash",
    ],
)
def test_canonical_launcher_positive_http_capture(tmp_path, monkeypatch, offline_native_cli, condition):
    collector = Path(os.environ.get("AIDO_TEST_PROCDUMP", ""))
    cdb = Path(os.environ.get("AIDO_TEST_CDB", ""))
    if not collector.is_file() or not cdb.is_file():
        pytest.skip("Prepared official collector and CDB must be selected explicitly")
    if condition == "collector-unavailable":
        owned_collector = tmp_path / "collector.exe"
        shutil.copy2(collector, owned_collector)
        collector = owned_collector
    db, workspace, diagnostics = (
        tmp_path / "isolated.sqlite",
        tmp_path / "workspace",
        tmp_path / "diagnostics",
    )
    workspace.mkdir()
    source = tmp_path / "synthetic-auth"
    source.mkdir()
    (source / "auth.json").write_text('{"offlineFixture":true}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private-local"))
    monkeypatch.setenv("AIDO_DIAGNOSTICS_DIR", str(diagnostics))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=db)
    runtime.init()
    register_workspace(runtime.connection, "offline-workspace", workspace)
    RuntimeConfigRepository(runtime.connection).upsert_installation(
        {"runtimeId": "codex_cli", "enabled": True, "executablePath": str(offline_native_cli)}
    )
    runtime.connection.execute("UPDATE provider_accounts SET enabled=1 WHERE provider_id='codex_cli'")
    runtime.connection.execute("UPDATE runtime_accounts SET enabled=1 WHERE runtime_id='codex_cli'")
    ProviderAccountStore(runtime.connection).upsert_model(
        {
            "providerId": "codex_cli",
            "model": "offline-fixture",
            "displayName": "offline-fixture",
            "source": "manual",
            "enabled": True,
        }
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def http(path, body=None, token=None):
        request = Request(
            f"http://127.0.0.1:{port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": f"capture-{condition}",
                **({"X-Local-Control-Token": token} if token else {}),
            },
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    # Same documented launcher and API/worker argv; no extra 2 GiB worker wrapper.
    launcher = subprocess.Popen(
        [
            sys.executable,
            "local-control-center/scripts/start_control_center.py",
            "--capture-session",
            "--no-build",
            "--dashboard-port",
            str(port),
            "--worker-interval-ms",
            "100",
            "--workspace",
            str(tmp_path),
            "--db-path",
            str(db),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    receipt = {
        "status": "FAIL",
        "condition": condition,
        "inference": False,
        "qualityEnvelope": quality_envelope(),
    }
    receipt_name = f"launcher-http-{condition}-{uuid.uuid4().hex}"
    analysis_request = None
    try:

        def ready():
            if launcher.poll() is not None:
                raise AssertionError(f"Launcher exited: {launcher.returncode}")
            try:
                return http("/api/v1/security/handshake")
            except URLError:
                return None

        token = wait_until(ready, 45)["token"]
        execution = http(
            "/api/v1/model-gateway/cli-runtimes/codex_cli/compatibility/smoke",
            {
                "workspaceId": "offline-workspace",
                "approved": True,
                "reason": "Synthetic native integration; no inference",
            },
            token,
        )["executionId"]
        receipt["executionId"] = execution
        if condition != "normal":
            activate_attempt(
                diagnostics,
                execution,
                ttl_seconds=120,
                native_collector=str(collector),
                executable_sha256=hashlib.sha256(offline_native_cli.read_bytes()).hexdigest(),
            )
        if condition == "collector-unavailable":
            collector.rename(collector.with_suffix(".unavailable"))  # Only our disposable copy.
        if condition in {"fastfail", "pressure"}:
            (workspace / f"{condition}.flag").touch()
        http("/api/v1/workers/run-once", {}, token)
        if condition == "collector-unavailable":
            wait_until(
                lambda: http(f"/api/v1/executions/{execution}")["status"] in {"completed", "failed"}, 45
            )
            receipt["operation"] = http(f"/api/v1/executions/{execution}")
            assert receipt["operation"].get("result", {}).get("status") != "validated"
            assert not (workspace / "ready.flag").exists()
            with closing(open_sqlite_connection(db)) as connection:
                rows = connection.execute(
                    "SELECT * FROM managed_processes WHERE execution_id=?", (execution,)
                ).fetchall()
            assert len(rows) == 1, "Only the dispatcher may start; target and collector must not spawn"
            assert http("/healthz")
            receipt["targetNotSpawned"] = True
            receipt["status"] = "PASS"
            return  # finally below still verifies and records all owner/lease cleanup.
        wait_until(lambda: (workspace / "ready.flag").exists(), 45)
        started = time.monotonic()
        assert http("/healthz")
        receipt["healthLatencyMs"] = (time.monotonic() - started) * 1000
        assert http(f"/api/v1/executions/{execution}")["status"] == "running"
        assert receipt["healthLatencyMs"] < 3000
        with closing(open_sqlite_connection(db)) as connection:
            receipt["liveProcesses"] = [
                dict(row)
                for row in connection.execute("SELECT * FROM managed_processes WHERE finished_at IS NULL")
            ]
            receipt["reservations"] = [
                dict(row)
                for row in connection.execute("SELECT * FROM resource_leases WHERE released_at IS NULL")
            ]
            receipt["attempts"] = [
                dict(row) for row in connection.execute("SELECT * FROM job_runs WHERE job_id=?", (execution,))
            ]
        assert len(receipt["reservations"]) == 1
        receipt["jobsReadback"] = _verify_live_jobs(
            receipt["liveProcesses"], diagnostic=condition != "normal"
        )
        receipt["processTree"] = [
            {"pid": p.pid, "created": p.create_time(), "parentPid": p.ppid()}
            for p in psutil.Process(launcher.pid).children(recursive=True)
        ]
        stop_started = time.monotonic()
        if condition == "owner-crash":
            # The venv Popen can be a redirector. Kill only the actual Job-handle owner,
            # identified in the durable aggregate and checked against this process tree.
            row = next(row for row in receipt["liveProcesses"] if row["workload_class"] == "capture_session")
            owner = psutil.Process(row["root_pid"])
            assert abs(owner.create_time() - row["root_create_time"]) < 0.01
            assert launcher.pid in {owner.pid, *(p.pid for p in owner.parents())}
            receipt["crashedOwner"] = {"pid": owner.pid, "created": owner.create_time()}
            owner.kill()
            launcher.wait(timeout=10)
            receipt["status"] = "PASS"
            return
        if condition == "cancel":
            http(f"/api/v1/executions/{execution}/cancel", {"reason": "Synthetic cancellation"}, token)
        elif condition == "fence":
            with closing(open_sqlite_connection(db)) as connection:
                connection.execute(
                    "UPDATE worker_leader_leases SET owner_id='synthetic-replacement', fencing_token=fencing_token+1"
                )
        else:
            (workspace / "finish.flag").touch()

        def finished():
            with closing(open_sqlite_connection(db)) as connection:
                return not connection.execute(
                    "SELECT 1 FROM managed_processes WHERE execution_id=? AND finished_at IS NULL",
                    (execution,),
                ).fetchone()

        wait_until(finished, 45)
        receipt["closeLatencyMs"] = (time.monotonic() - stop_started) * 1000
        receipt["operation"] = http(f"/api/v1/executions/{execution}")
        assert http("/healthz")
        capture_receipts = list(diagnostics.glob("native/*/capture.receipt.json"))
        receipt["captures"] = [json.loads(path.read_text()) for path in capture_receipts]
        if condition in {"normal", "diagnostic-normal"}:
            assert receipt["operation"]["status"] == "completed"
            assert receipt["operation"]["result"]["status"] == "validated"
        if condition in {"fastfail", "pressure"}:
            assert len(capture_receipts) == 1
            capture = receipt["captures"][0]
            assert capture["outcome"] == "captured" and capture["exceptionCode"] == 0xC0000409
            assert capture["collectorReadyBeforeResume"] and capture["collectorClosed"]
            assert receipt["operation"]["result"]["status"] != "validated"
            retained = (
                Path(os.environ.get("AIDO_ACCEPTANCE_EVIDENCE", str(tmp_path))).parent
                / "native-private"
                / capture_receipts[0].parent.name
            )
            private_directory(retained)
            for file in capture_receipts[0].parent.iterdir():
                if file.is_file():
                    shutil.copy2(file, retained / file.name)
            for file in (offline_native_cli, offline_native_cli.with_suffix(".pdb")):
                shutil.copy2(file, retained / file.name)
            analysis_request = (retained, str(cdb))
    finally:
        (workspace / "finish.flag").touch()
        owned = psutil.Process(launcher.pid) if launcher.poll() is None else None
        descendants = owned.children(recursive=True) if owned else []
        identities = [{"pid": p.pid, "created": p.create_time()} for p in descendants]
        with closing(open_sqlite_connection(db)) as connection:
            api = connection.execute(
                "SELECT root_pid, root_create_time FROM managed_processes WHERE workload_class='control_plane' ORDER BY started_at LIMIT 1"
            ).fetchone()
        if api:
            with suppress(psutil.NoSuchProcess):
                process = psutil.Process(api[0])
                if abs(process.create_time() - api[1]) < 0.01:
                    process.terminate()
        try:
            stdout, stderr = launcher.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            launcher.kill()
            stdout, stderr = launcher.communicate(timeout=10)
        receipt["launcher"] = {
            "exitCode": launcher.returncode,
            "stdout": stdout.decode("utf-8", "replace")[-16000:],
            "stderr": stderr.decode("utf-8", "replace")[-16000:],
        }
        from local_control_center.process_supervision.recovery import recover_managed_processes

        receipt["recoveredAfterOwnerExit"] = recover_managed_processes(db)
        receipt["ownProcessesGone"] = identities_gone(identities)
        if receipt.get("processTree"):
            receipt["ownProcessesGone"] &= identities_gone(receipt["processTree"])
        with closing(open_sqlite_connection(db)) as connection:
            receipt["remainingReservations"] = [
                dict(row)
                for row in connection.execute("SELECT * FROM resource_leases WHERE released_at IS NULL")
            ]
            logs = connection.execute("SELECT path FROM artifacts WHERE kind='execution_log'").fetchall()
            receipt["controlPlaneOutput"] = [
                Path(row[0]).read_text(encoding="utf-8", errors="replace")[-16000:]
                for row in logs
                if Path(row[0]).is_file()
            ]
            receipt["closedProcesses"] = [
                dict(row) for row in connection.execute("SELECT * FROM managed_processes")
            ]
        try:
            assert receipt["ownProcessesGone"] and not receipt["remainingReservations"]
            assert "Traceback" not in receipt["launcher"]["stderr"]
            if condition == "pressure":
                assert any(
                    "allocationRejectedUnder32MiBJob" in text for text in receipt["controlPlaneOutput"]
                )
        except BaseException:
            receipt["status"] = "FAIL"
            raise
        finally:
            evidence(receipt_name, receipt)
            runtime.close()
    try:
        assert receipt["ownProcessesGone"] and not receipt["remainingReservations"]
        assert "Traceback" not in receipt["launcher"]["stderr"]
        if analysis_request:
            retained, debugger = analysis_request
            analysis = run_quality_step(
                QualityStep(
                    "synthetic-cdb-analysis",
                    (
                        debugger,
                        "-sins",
                        "-y",
                        str(retained),
                        "-z",
                        # Retained invocation paths can exceed CDB's legacy path limit.
                        "\\\\?\\" + str(retained / "exception.dmp"),
                        "-c",
                        ".ecxr; k 12; lm; q",
                    ),
                    "qa_light",
                    30,
                ),
                root=retained,
                db_path=db,
                display_output=False,
            )
            assert analysis["returnCode"] == 0 and "c0000409" in analysis["stdout"].lower()
            assert "mainCRTStartup" in analysis["stdout"]
            receipt["analysis"] = analysis
        receipt["status"] = "PASS"
    finally:
        evidence(receipt_name, receipt)
