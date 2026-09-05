from __future__ import annotations

from contextlib import closing
from types import SimpleNamespace

import pytest

from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def test_upgrade_from_schema66_does_not_skip_compatibility_migration(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "upgrade.sqlite")) as connection:
        initialize_platform_schema(connection)
        connection.execute("DELETE FROM schema_migrations WHERE version=67")
        connection.execute(
            "UPDATE model_catalog SET enabled=1 WHERE source='manual_seed' AND provider_id='codex_cli'"
        )
        initialize_platform_schema(connection)
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=67").fetchone()
        assert not connection.execute(
            "SELECT 1 FROM model_catalog WHERE source='manual_seed' AND provider_id='codex_cli' AND enabled=1"
        ).fetchone()
        initialize_platform_schema(connection)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_process_evidence_preserves_subsecond_duration(tmp_path):
    from local_control_center.process_supervision.evidence import process_evidence

    record = SimpleNamespace(
        started_at="2026-09-04T00:00:00.000Z",
        finished_at="2026-09-04T00:00:00.125Z",
        managed_process_id="test",
        execution_id="test",
        resource_lease_id=None,
        workload_class="qa_light",
        command_fingerprint="test",
        exit_code=0,
        timed_out=False,
        cancelled=False,
        peak_memory_bytes=0,
        cpu_time_seconds=0,
        termination_reason="exited",
        stdout_artifact_id=None,
        stderr_artifact_id=None,
    )
    with closing(open_sqlite_connection(tmp_path / "evidence.sqlite")) as connection:
        initialize_platform_schema(connection)
        assert process_evidence(connection, record)["durationMs"] == 125


def test_codex_effort_cannot_inject_a_toml_configuration_value(tmp_path):
    from local_control_center.agents.cli_runtimes.base import RuntimeRequest
    from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime

    request = RuntimeRequest(
        runtime="codex_cli",
        workspace_id="test",
        prompt="test only",
        workspace_path=str(tmp_path),
        model="operator-model",
        effort='high"\nother_setting="value',
    )
    with pytest.raises(ValueError, match="effort"):
        CodexCliRuntime().build_command(request)


def test_sqlite_backup_restore_preserves_wal_and_never_overwrites(tmp_path):
    from local_control_center.quality.maintenance import backup_bundle, restore_bundle

    source = tmp_path / "live" / "platform.sqlite"
    with closing(open_sqlite_connection(source)) as connection:
        initialize_platform_schema(connection)
        connection.execute("CREATE TABLE operator_data(value TEXT)")
        connection.execute("INSERT INTO operator_data VALUES ('durable WAL value')")
        report = backup_bundle(source, tmp_path / "backup")
        assert report["status"] == "completed"
        restored = restore_bundle(tmp_path / "backup", tmp_path / "restored")
        with closing(open_sqlite_connection(restored)) as restored_connection:
            assert (
                restored_connection.execute("SELECT value FROM operator_data").fetchone()[0]
                == "durable WAL value"
            )
            assert restored_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pytest.raises(FileExistsError):
            restore_bundle(tmp_path / "backup", tmp_path / "restored")
        with pytest.raises(FileExistsError):
            backup_bundle(source, tmp_path / "backup")


def test_backup_fails_closed_while_a_worker_lease_is_live(tmp_path):
    from local_control_center.quality.maintenance import backup_bundle
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    source = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(source)) as connection:
        initialize_platform_schema(connection)
        WorkerLeadershipRepository(connection).acquire(owner_id="test-owner")
        with pytest.raises(RuntimeError, match="worker"):
            backup_bundle(source, tmp_path / "backup")


def test_restore_rejects_tampered_database(tmp_path):
    from local_control_center.quality.maintenance import backup_bundle, restore_bundle

    source = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(source)) as connection:
        initialize_platform_schema(connection)
    backup_bundle(source, tmp_path / "backup")
    with (tmp_path / "backup" / "platform.sqlite").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        restore_bundle(tmp_path / "backup", tmp_path / "restore")


def test_supervised_capture_persists_progress_before_pipe_eof(tmp_path):
    import os
    import threading
    import time

    from local_control_center.process_supervision.capture import ArtifactCapture, CapturedReader
    from local_control_center.process_supervision.service import _drain_prefix

    read_fd, write_fd = os.pipe()
    capture = ArtifactCapture(tmp_path, "stdout", threading.Event())
    source = os.fdopen(read_fd, "rb")
    writer = os.fdopen(write_fd, "wb", buffering=0)
    reader = threading.Thread(target=_drain_prefix, args=(CapturedReader(source, capture), 4000, {}))
    reader.start()
    try:
        writer.write(b"incremental progress\n")
        deadline = time.monotonic() + 1
        while capture.path.stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert capture.path.read_bytes() == b"incremental progress\n"
    finally:
        writer.close()
        reader.join(timeout=3)
        source.close()
        capture.finish()


@pytest.mark.parametrize("version", [(3, 50, 4), (3, 51, 2), (3, 52, 0)])
def test_sqlite_wal_unsafe_runtime_is_rejected_before_database_creation(tmp_path, monkeypatch, version):
    from local_control_center.shared import db

    monkeypatch.setattr(db.sqlite3, "sqlite_version_info", version)
    target = tmp_path / "must-not-be-created.sqlite"
    with pytest.raises(RuntimeError, match="SQLite"):
        db.open_sqlite_connection(target)
    assert not target.exists()


def test_first_cpu_sample_never_uses_psutil_unprimed_zero(tmp_path, monkeypatch):
    from local_control_center.host_resources import probes

    intervals = []

    def measured_cpu(*, interval):
        intervals.append(interval)
        return 90.0 if interval > 0 else 0.0

    monkeypatch.setattr(probes.psutil, "cpu_percent", measured_cpu)
    monkeypatch.setattr(probes, "_gpu_usage", lambda runner: (None, None, None))
    sample = probes.HostResourceProbe(relevant_paths=[tmp_path]).sample(cpu_interval_seconds=0)
    assert sample.cpu_percent_1s == 90.0
    assert intervals == [1.0]


@pytest.mark.parametrize("module", ["remediations.service", "product_loop.coordinator"])
def test_worker_public_export_has_no_import_order_cycle(tmp_path, controlled_domain_host, module):
    import sys
    from pathlib import Path

    from local_control_center.process_supervision.service import run_supervised_capture

    result = run_supervised_capture(
        [
            sys.executable,
            "-c",
            f"import local_control_center.{module}; from local_control_center.workers import LocalWorkerRuntime; "
            "assert LocalWorkerRuntime.__name__ == 'LocalWorkerRuntime'",
        ],
        cwd=Path(__file__).resolve().parents[1],
        db_path=tmp_path / "import-probe.sqlite",
        workload_class="qa_light",
        timeout_seconds=30,
    )
    assert result["returnCode"] == 0, result["stderr"]


def test_clean_install_contains_launchers_and_checks_native_api(tmp_path, monkeypatch):
    from pathlib import Path

    from local_control_center.quality import release

    steps = []

    def check_step(step, *, root, db_path):
        assert (root / "local-control-center/scripts/start_control_center.py").is_file()
        assert (root / "local-control-center/scripts/start-control-center.ps1").is_file()
        assert (root / "scripts/verify-operational-hardening.ps1").is_file()
        steps.append(step.name)
        return {"returnCode": 0, "timedOut": False, "cancelled": False}

    monkeypatch.setattr(release, "_run", check_step)
    report = {"steps": []}
    release.clean_install(
        Path.cwd(), tmp_path, tmp_path / "supervision.sqlite", report, tmp_path / "report.json"
    )
    assert steps[-1] == "clean-native-api"
    assert report["cleanInstall"] == "passed"
