"""Exercise the native launch/identity gap with disposable processes, not AI runtimes."""

import subprocess
import sys
from contextlib import closing

import pytest

from local_control_center.process_supervision.recovery import recover_managed_processes
from local_control_center.shared.db import open_sqlite_connection


@pytest.mark.skipif(sys.platform != "win32", reason="Windows kill-on-close identity gap")
def test_crash_after_native_launch_before_identity_stays_unknown(tmp_path):
    db = tmp_path / "runtime.sqlite"
    program = """
import os, sys
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.process_supervision.service import ProcessSupervisorService
s = ProcessSupervisorService(db_path=sys.argv[1], resource_snapshot=ResourceSnapshot.test_snapshot())
native_start = s.backend.start
def crash_after_start(*args, **kwargs):
    child = native_start(*args, **kwargs)
    print(child.process.pid, flush=True)
    os._exit(17)
s.backend.start = crash_after_start
s.start(argv=[sys.executable, '-c', 'import time; time.sleep(30)'], cwd=sys.argv[2])
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(db), str(tmp_path)], capture_output=True, timeout=20
    )
    assert result.returncode == 17, result.stderr
    assert int(result.stdout.strip()) > 0  # OS process existed despite durable root_pid=0.
    assert recover_managed_processes(db) == []
    with closing(open_sqlite_connection(db)) as connection:
        row = connection.execute("SELECT * FROM managed_processes").fetchone()
        assert row["root_pid"] == 0 and row["finished_at"] is None
        assert (
            connection.execute("SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL").fetchone()[0]
            == 1
        )


def test_identity_persistence_failure_contains_native_tree_and_logs_original(tmp_path, caplog):
    from local_control_center.host_resources.models import ResourceSnapshot
    from local_control_center.process_supervision.service import ProcessSupervisorService
    from local_control_center.shared.migrations import initialize_platform_schema

    db = tmp_path / "runtime.sqlite"
    with closing(open_sqlite_connection(db)) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            "CREATE TRIGGER fail_identity BEFORE UPDATE OF root_pid ON managed_processes BEGIN SELECT RAISE(ABORT, 'synthetic identity failure'); END"
        )
    service = ProcessSupervisorService(db_path=db, resource_snapshot=ResourceSnapshot.test_snapshot())
    with pytest.raises(Exception, match="synthetic identity failure"):
        service.start(argv=[sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    with closing(open_sqlite_connection(db)) as connection:
        row = connection.execute("SELECT * FROM managed_processes").fetchone()
        assert row["finished_at"] and row["termination_reason"] == "registration_failed"
        assert (
            connection.execute("SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL").fetchone()[0]
            == 0
        )
    assert "phase=identity_persist" in caplog.text
    assert "SQLITE_CONSTRAINT_TRIGGER" in caplog.text
