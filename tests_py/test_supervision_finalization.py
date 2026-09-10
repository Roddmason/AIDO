"""Native exits survive a failing SQLite finalization; no provider or OS-cause claim.

@author Rodrigo Mason
"""

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

import pytest

from local_control_center.evidence.artifacts import evidence_artifact_root
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.process_supervision import service as module
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.shared.db import open_sqlite_connection


@pytest.mark.parametrize("exit_code", [0, 23])
def test_observed_native_exit_precedes_sqlite_finalization_and_handle_close(tmp_path, monkeypatch, exit_code):
    service = module.ProcessSupervisorService(db_path=tmp_path / "native.sqlite")
    managed = service.start(
        argv=[sys.executable, "-c", f"print('synthetic'); raise SystemExit({exit_code})"],
        cwd=tmp_path,
        workload_class="qa_light",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    managed.process.communicate(timeout=10)
    managed.stop_watcher.set()
    managed.watcher.join(4)
    original = sqlite3.OperationalError("disk I/O error; password=synthetic-sensitive-value")
    original.sqlite_errorcode = 1546
    original.sqlite_errorname = "SQLITE_IOERR_TRUNCATE"
    observed_before_sql = []

    def fail_connection(*args, **kwargs):
        observed_before_sql.extend(evidence_artifact_root(tmp_path).glob("*.process-outcome.json"))
        assert not managed.released
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(module, "open_sqlite_connection", fail_connection)
        with pytest.raises(sqlite3.OperationalError) as caught:
            service.complete(managed, exit_code=managed.process.returncode)
    assert caught.value is original
    assert managed.released and managed.process.poll() == exit_code
    assert len(observed_before_sql) == 1, "Known native result was not retained before failing SQL"
    observation = json.loads(observed_before_sql[0].read_text(encoding="utf8"))
    assert observation["returnCode"] == exit_code
    assert observation["processCreationTime"] > 0
    assert observation["pid"] == managed.process.pid
    assert observation["durableFinalization"] == "pending"
    failure = caught.value.supervision_outcome
    assert failure["returnCode"] == exit_code and failure["durableFinalization"] == "pending"
    assert failure["exceptionChain"][0]["sqlite_errorcode"] == 1546
    assert "synthetic-sensitive-value" not in json.dumps(failure)
    with closing(open_sqlite_connection(service.db_path)) as connection:
        assert ManagedProcessRepository(connection).get(managed.managed_process_id).finished_at is None
        assert len(ResourceRepository(connection).active_leases()) == 1
    # Recovery cannot claim a live owner. The outer test controller recovers after this owner exits.
    from local_control_center.process_supervision.recovery import recover_managed_processes

    assert recover_managed_processes(service.db_path) == []


def test_secondary_receipt_failure_does_not_replace_sqlite_error(tmp_path, monkeypatch):
    from local_control_center.host_resources.models import ResourceSnapshot
    from local_control_center.shared import serialization
    from tests_py.test_process_supervision import FakeSupervisor

    backend = FakeSupervisor()
    service = module.ProcessSupervisorService(
        db_path=tmp_path / "synthetic.sqlite",
        backend=backend,
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    managed = service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
    managed.process.returncode = 23
    managed.stop_watcher.set()
    managed.watcher.join(4)
    original = sqlite3.OperationalError("synthetic I/O error")

    def fail_sql(*args, **kwargs):
        raise original

    def fail_publish(*args, **kwargs):
        raise PermissionError("synthetic receipt denied")

    with monkeypatch.context() as patch:
        patch.setattr(module, "open_sqlite_connection", fail_sql)
        patch.setattr(serialization, "publish_json_exclusive", fail_publish)
        with pytest.raises(sqlite3.OperationalError) as caught:
            service.complete(managed, exit_code=23)
    assert caught.value is original
    assert managed.released and backend.released == [managed.managed_process_id]
    assert caught.value.supervision_outcome["returnCode"] == 23
    assert caught.value.supervision_outcome["receiptPublication"] == "failed"
    # Unit backend has no OS identity. Retire only its deliberately synthetic rows.
    from local_control_center.process_supervision.models import ProcessStats

    with closing(open_sqlite_connection(service.db_path)) as connection:
        ManagedProcessRepository(connection).finish(
            managed.managed_process_id, stats=ProcessStats(exit_code=23)
        )
        ResourceRepository(connection).release(managed.resource_lease_id, reason="unit fixture cleanup")


@pytest.mark.parametrize("ending", ["normal", "cancel", "child-death"])
def test_native_close_preserves_borrowed_connection_wal_and_foreign_keys(tmp_path, ending):
    service = module.ProcessSupervisorService(db_path=tmp_path / "lifecycle.sqlite")
    command = (
        "from pathlib import Path; import time; "
        "Path('ready').write_text('ready'); "
        "exec(\"while not Path('finish').exists():\\n time.sleep(.01)\"); print('finished')"
    )
    managed = service.start(
        argv=[sys.executable, "-c", command],
        cwd=tmp_path,
        workload_class="qa_light",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        with closing(open_sqlite_connection(service.db_path)) as borrowed:
            borrowed.execute("BEGIN")
            borrowed.execute("SELECT count(*) FROM managed_processes").fetchone()
            if ending == "cancel":
                record = service.cancel(managed.managed_process_id, reason="synthetic orderly cancellation")
                assert record.cancelled
            else:
                if ending == "normal":
                    (tmp_path / "finish").write_text("finish", encoding="utf8")
                else:
                    managed.process.kill()
                managed.process.communicate(timeout=10)
                record = service.complete(managed, exit_code=managed.process.returncode)
                assert record.exit_code == (0 if ending == "normal" else managed.process.returncode)
            assert borrowed.in_transaction
            assert borrowed.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert borrowed.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            borrowed.rollback()
        assert managed.released and record.released_at
        with closing(open_sqlite_connection(service.db_path)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            assert not ResourceRepository(connection).active_leases()
    finally:
        if not managed.released:
            service.cancel(managed.managed_process_id, reason="synthetic cleanup")
        for stream in (managed.process.stdout, managed.process.stderr):
            stream.close()


def test_capture_cleanup_still_runs_when_capture_sql_persistence_fails(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from local_control_center.host_resources.models import ResourceSnapshot
    from tests_py.test_process_supervision import FakeSupervisor

    service = module.ProcessSupervisorService(
        db_path=tmp_path / "unit.sqlite",
        backend=FakeSupervisor(),
        resource_snapshot=ResourceSnapshot.test_snapshot(),
    )
    managed = service.start(argv=[sys.executable, "--version"], cwd=tmp_path)
    managed.process.returncode = 0
    closed = []
    managed.native_capture = SimpleNamespace(closed=False, finish=lambda: closed.append(True))
    original = sqlite3.OperationalError("synthetic finalization failure")

    def fail(*args):
        raise original

    monkeypatch.setattr(service, "_finish_captures", fail)
    with pytest.raises(sqlite3.OperationalError) as caught:
        service.complete(managed, exit_code=0)
    assert caught.value is original
    assert closed == [True], "Independent collector was skipped after SQL failure"
    assert managed.released
    from local_control_center.process_supervision.models import ProcessStats

    with closing(open_sqlite_connection(service.db_path)) as connection:
        ManagedProcessRepository(connection).finish(
            managed.managed_process_id, stats=ProcessStats(exit_code=0)
        )
        ResourceRepository(connection).release(managed.resource_lease_id, reason="unit fixture cleanup")
