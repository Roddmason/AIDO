"""HTTP -> local worker -> dispatcher -> CLI sandbox -> native watchdog -> SQLite.

Only the configured AI executable is replaced with a compiled, credential-free fixture.
All receipts remain in the test DB and are NOT evidence of real Codex compatibility.
"""

import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.process_supervision.service import ProcessSupervisorService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.workers.runtime import LocalWorkerRuntime
from tests_py.operational_acceptance_support import evidence, identities_gone, native_readback, wait_until
from tests_py.test_model_runtime_gateway import register_workspace

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native operational pipeline")


@pytest.fixture(scope="module")
def offline_codex(tmp_path_factory):
    binary = tmp_path_factory.mktemp("offline-cli") / "codex.exe"
    compiler = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            f"/out:{binary}",
            str(Path("tests_py/fixtures/watchdog/codex.cs").resolve()),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


@pytest.mark.parametrize("action", ["transient", "loss", "cancel"])
def test_http_worker_dispatcher_native_cli_keeps_api_and_contains_writers(
    tmp_path, monkeypatch, offline_codex, action
):
    db = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source-home"
    source.mkdir()
    (source / "auth.json").write_text('{"offlineFixture":true}')
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private-local"))
    monkeypatch.setenv("AIDO_DIAGNOSTICS_DIR", str(tmp_path / "diagnostics"))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=db)
    runtime.init()
    register_workspace(runtime.connection, "offline-workspace", workspace)
    RuntimeConfigRepository(runtime.connection).upsert_installation(
        {"runtimeId": "codex_cli", "enabled": True, "executablePath": str(offline_codex)}
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
    origin = f"http://127.0.0.1:{port}"

    def http(path, body=None, token=None):
        request = Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": f"corr-offline-{action}",
                **({"X-Local-Control-Token": token} if token else {}),
            },
        )
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    service = ProcessSupervisorService(db_path=db)
    api = service.start(
        argv=[
            sys.executable,
            "-m",
            "local_control_center",
            "--dashboard-only",
            "--no-worker",
            "--dashboard-port",
            str(port),
            "--workspace",
            str(tmp_path),
            "--db-path",
            str(db),
        ],
        cwd=Path.cwd(),
        workload_class="control_plane",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    worker = LocalWorkerRuntime(db_path=db, cwd=tmp_path)
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    busy = threading.Event()
    heartbeat = HostResourceGovernor.heartbeat

    def observed(self, *args, **kwargs):
        try:
            return heartbeat(self, *args, **kwargs)
        except Exception:
            busy.set()
            raise

    monkeypatch.setattr(HostResourceGovernor, "heartbeat", observed)
    receipt = {
        "action": action,
        "inference": False,
        "binarySha256": hashlib.sha256(offline_codex.read_bytes()).hexdigest(),
    }
    try:

        def ready():
            try:
                return http("/api/v1/security/handshake")
            except URLError:
                assert api.process.poll() is None
                return None

        token = wait_until(ready, 20)["token"]
        job = http(
            "/api/v1/model-gateway/cli-runtimes/codex_cli/compatibility/smoke",
            {
                "workspaceId": "offline-workspace",
                "approved": True,
                "reason": "Offline watchdog integration test; no inference",
            },
            token,
        )["executionId"]
        receipt["executionId"] = job
        http("/api/v1/workers/run-once", {}, token)
        thread.start()
        wait_until(lambda: len(list(workspace.glob("writer-*.log"))) == 2, 30)
        with closing(open_sqlite_connection(db)) as reader:
            rows = reader.execute("SELECT * FROM managed_processes WHERE execution_id=?", (job,)).fetchall()
            assert len(rows) >= 2 and all(row["root_pid"] > 0 for row in rows)
            receipt["managedProcesses"] = [dict(row) for row in rows]
        # The API's OS Job Object is independently inspected, not a backend double.
        receipt["apiNativeJob"] = native_readback(api)
        assert all(member["inJob"] for member in receipt["apiNativeJob"]["members"])
        writers = [
            {
                "pid": int(p.stem.split("-")[1]),
                "created": __import__("psutil").Process(int(p.stem.split("-")[1])).create_time(),
            }
            for p in workspace.glob("writer-*.log")
        ]
        if action == "transient":
            with closing(open_sqlite_connection(db)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                try:
                    assert busy.wait(7)
                    start = time.monotonic()
                    assert http(f"/api/v1/executions/{job}")["status"] == "running"
                    receipt["statusLatencyMs"] = (time.monotonic() - start) * 1000
                    assert time.monotonic() - start < 1
                    assert api.process.poll() is None
                finally:
                    blocker.rollback()
            (workspace / "finish.flag").touch()
        elif action == "loss":
            with closing(open_sqlite_connection(db)) as blocker:
                blocker.execute(
                    "UPDATE worker_leader_leases SET owner_id='replacement-fixture', fencing_token=fencing_token+1"
                )
        else:
            http(f"/api/v1/executions/{job}/cancel", {"reason": "Offline cancellation"}, token)
        wait_until(lambda: identities_gone(writers), 10)
        sizes = {p: p.stat().st_size for p in workspace.glob("writer-*.log")}
        assert api.process.poll() is None
        assert http("/api/v1/workers/status")
        if action == "transient":
            assert (
                wait_until(
                    lambda: (
                        value
                        if (value := http(f"/api/v1/executions/{job}"))["status"] == "completed"
                        else None
                    ),
                    10,
                )["result"]["status"]
                == "validated"
            )
        assert all(p.stat().st_size == size for p, size in sizes.items())
        if action == "transient":
            from local_control_center.shared.diagnostics import incident_events

            events = wait_until(lambda: list(incident_events(tmp_path / "diagnostics", job)), 5)
            assert events, "Missing HTTP/worker/native diagnostic correlation"
            assert all(
                e.get("requestId") == f"corr-offline-{action}"
                for e in events
                if e["event"] in {"process.created", "dispatcher.started", "worker.claimed"}
            )
            assert {"process.created", "dispatcher.started", "worker.claimed"}.issubset(
                {e["event"] for e in events}
            )
        # Writer exit is earlier than the worker's durable completion. Do not revoke its
        # fence from fixture teardown while complete_job_run is still in flight.
        wait_until(lambda: worker.status()["inFlightJobs"] == 0, 15)
        receipt.update(status="PASS", oldWriterIdentities=writers, oldWritersGone=True)
        evidence(f"watchdog-http-{action}-{uuid.uuid4().hex}", receipt)
    finally:
        (workspace / "finish.flag").touch()
        worker.stop(reason="Offline fixture finished")
        if thread.is_alive():
            thread.join(timeout=15)
        service.complete(api, exit_code=api.process.poll(), termination_reason="offline_fixture_finished")
        runtime.close()
