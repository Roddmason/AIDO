"""Temporary native-session lifecycle using synthetic auth only and existing recovery."""

import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents import runtime_registry
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.recovery import recover_managed_processes
from local_control_center.process_supervision.service import ExecutionCancelled, ProcessSupervisorService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


@pytest.fixture
def synthetic_auth(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    original = '{"synthetic":true}'
    (source / "auth.json").write_text(original)
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private"))
    db = tmp_path / "runtime.sqlite"
    with closing(open_sqlite_connection(db)) as connection:
        initialize_platform_schema(connection)
    yield db, source
    assert (source / "auth.json").read_text() == original


@pytest.mark.parametrize("cancel", [False, True])
def test_temporary_auth_removed_on_normal_exit_and_native_cancellation(synthetic_auth, tmp_path, cancel):
    db, _ = synthetic_auth
    with execution_scope(ProcessExecutionContext(db_path=db, execution_id="synthetic")):
        try:
            with runtime_registry.isolated_product_owner_codex_environment() as env:
                home = Path(env["CODEX_HOME"])
                assert json.loads((home / "auth.json").read_text()) == {"synthetic": True}
                if cancel:
                    service = ProcessSupervisorService(
                        db_path=db, resource_snapshot=ResourceSnapshot.test_snapshot()
                    )
                    child = service.start(
                        argv=[sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path
                    )
                    service.cancel(child.managed_process_id, reason="synthetic cancellation")
                    raise ExecutionCancelled("synthetic cancellation")
        except ExecutionCancelled:
            assert cancel
    assert not home.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows owner-crash cleanup")
def test_temporary_auth_recovered_after_native_owner_crash(synthetic_auth, tmp_path):
    db, _ = synthetic_auth
    program = """
import os, sys
from local_control_center.agents.runtime_registry import isolated_product_owner_codex_environment
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.service import ProcessSupervisorService
from local_control_center.host_resources.models import ResourceSnapshot
from pathlib import Path
with execution_scope(ProcessExecutionContext(db_path=Path(sys.argv[1]), execution_id='synthetic')):
    with isolated_product_owner_codex_environment() as env:
        s = ProcessSupervisorService(db_path=sys.argv[1], resource_snapshot=ResourceSnapshot.test_snapshot())
        child = s.start(argv=[sys.executable, '-c', 'import time; time.sleep(30)'], cwd=sys.argv[2])
        print(env['CODEX_HOME'], flush=True)
        os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(db), str(tmp_path)], capture_output=True, timeout=20
    )
    assert result.returncode == 17, result.stderr
    home = Path(result.stdout.decode().strip())
    assert home.exists()
    assert recover_managed_processes(db)
    assert not home.exists()
    assert recover_managed_processes(db) == []


def test_rejected_cleanup_is_durable_and_recovery_retries_only_owned_home(synthetic_auth, monkeypatch):
    db, _ = synthetic_auth
    original = runtime_registry.shutil.rmtree

    def rejected(path):
        raise PermissionError("synthetic cleanup denied")

    with (
        execution_scope(ProcessExecutionContext(db_path=db, execution_id="synthetic")),
        pytest.raises(PermissionError, match="synthetic cleanup denied"),
        runtime_registry.isolated_product_owner_codex_environment() as env,
    ):
        home = Path(env["CODEX_HOME"])
        monkeypatch.setattr(runtime_registry.shutil, "rmtree", rejected)
    assert home.exists()
    with closing(open_sqlite_connection(db)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='runtime.codex.home_cleanup_failed'"
            ).fetchone()[0]
            == 1
        )
    # A live owner is not permission for crash recovery to remove its home.
    monkeypatch.setattr(runtime_registry.shutil, "rmtree", original)
    recover_managed_processes(db)
    assert home.exists()
