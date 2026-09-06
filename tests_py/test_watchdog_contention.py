"""Native watchdog regressions; synchronization observes real SQLite operations, never replaces them."""

import sqlite3
import sys
import threading
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from local_control_center.api import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.service import ProcessSupervisorService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.workers.leadership import WorkerLeadershipRepository
from tests_py.operational_acceptance_support import wait_until


@pytest.mark.parametrize("release_lock", [True, False])
def test_native_watchdog_write_contention_has_a_bounded_safe_window(
    tmp_path, monkeypatch, caplog, release_lock
):
    db = tmp_path / "runtime.sqlite"
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=db)
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    busy = threading.Event()
    original = HostResourceGovernor.heartbeat

    def observed_heartbeat(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
            assert not self.connection.in_transaction
            busy.set()
            raise

    monkeypatch.setattr(HostResourceGovernor, "heartbeat", observed_heartbeat)
    service = ProcessSupervisorService(db_path=db, resource_snapshot=ResourceSnapshot.test_snapshot())
    child = service.start(
        argv=[sys.executable, "-c", "import time; time.sleep(40)"],
        cwd=tmp_path,
        workload_class="control_plane",
    )
    try:
        with closing(open_sqlite_connection(db)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            try:
                assert busy.wait(9), "Real heartbeat must encounter the independent writer"
                started = time.monotonic()
                response = client.get("/api/v1/executions")
                assert response.status_code == 200
                assert time.monotonic() - started < 1
                if release_lock:
                    blocker.rollback()
                    with closing(open_sqlite_connection(db)) as reader:
                        before = reader.execute(
                            "SELECT heartbeat_at FROM resource_leases WHERE id=?", (child.resource_lease_id,)
                        ).fetchone()[0]
                        wait_until(
                            lambda: (
                                reader.execute(
                                    "SELECT heartbeat_at FROM resource_leases WHERE id=?",
                                    (child.resource_lease_id,),
                                ).fetchone()[0]
                                != before
                            ),
                            4,
                        )
                    assert child.process.poll() is None
                else:
                    wait_until(lambda: child.process.poll() is not None, 5)
                    assert child.terminal_stats.termination_reason == "control_watch_deadline_exceeded"
                    assert "SQLITE_BUSY" in caplog.text
                    assert "phase=lease_heartbeat" in caplog.text
            finally:
                blocker.rollback()
    finally:
        service.complete(child, exit_code=child.process.poll())
        runtime.close()


def test_native_watchdog_stops_lost_fence_without_waiting_for_cancel_write(tmp_path):
    db = tmp_path / "runtime.sqlite"
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=db)
    runtime.init()
    leader = WorkerLeadershipRepository(runtime.connection).acquire(owner_id="test-owner", lease_seconds=60)
    with execution_scope(
        ProcessExecutionContext(db_path=db, worker_id="test-owner", fencing_token=leader.fencing_token)
    ):
        service = ProcessSupervisorService(db_path=db, resource_snapshot=ResourceSnapshot.test_snapshot())
        child = service.start(argv=[sys.executable, "-c", "import time; time.sleep(40)"], cwd=tmp_path)
    try:
        with closing(open_sqlite_connection(db)) as blocker:
            blocker.execute("UPDATE worker_leader_leases SET expires_at='2000-01-01T00:00:00Z'")
            blocker.execute("BEGIN IMMEDIATE")
            try:
                wait_until(lambda: child.process.poll() is not None, 3)
                assert child.terminal_stats.termination_reason == "leadership_fence_lost"
            finally:
                blocker.rollback()
    finally:
        service.complete(child, exit_code=child.process.poll())
        runtime.close()
