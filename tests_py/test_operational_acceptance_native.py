"""Aceptación Windows acotada con procesos de prueba y evidencia opcional persistente.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import psutil
import pytest

from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.recovery import recover_managed_processes
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.quality.owned_tree import stop_owned_tree
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.operational_acceptance_support import evidence, identities_gone, process_sample, wait_until

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Native Windows acceptance only")
MODULE = "tests_py.operational_acceptance_worker"


def _launch(directory, db, mode):
    directory.mkdir(parents=True)
    child = subprocess.Popen(
        [sys.executable, "-m", MODULE, mode, str(directory), "--db", str(db)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return child, psutil.Process(child.pid).create_time()


def _expire(connection):
    connection.execute("UPDATE worker_leader_leases SET expires_at='2000-01-01T00:00:00.000Z'")
    connection.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00.000Z' WHERE status='running'")


def _cycle(root: Path, db: Path, index: int, mode: str) -> dict:
    with closing(open_sqlite_connection(db)) as connection:
        _expire(connection)
        job = JobsRepository(connection).create_job(project_id="local-operations", kind="acceptance.writer")[
            "job"
        ]
    directory = root / f"cycle-{index}"
    child, created = _launch(directory, db, mode)
    receipt = {"cycle": index, "mode": mode, "jobId": job["id"], "startedAt": time.time()}
    try:

        def ready_or_error():
            if child.poll() is not None:
                out, err = child.communicate()
                raise AssertionError(f"Worker exited before ready: {child.returncode}; {out!r}; {err!r}")
            return (directory / "ready.json").exists()

        wait_until(ready_or_error, 20)
        ready = json.loads((directory / "ready.json").read_text())
        receipt.update(ready)
        native = ready["native"]
        assert len(native["members"]) >= 2
        assert all(m["inJob"] for m in native["members"])
        assert native["cpuFlags"] == 5 and native["cpuRate"] == 500
        assert native["memoryBytes"] == 256 * 1024**2
        assert native["limitFlags"] & 0x2000  # KILL_ON_JOB_CLOSE, independently queried.
        started = time.monotonic()
        if mode == "crash":
            (directory / "crash.flag").touch()
        elif mode == "cancel":
            with closing(open_sqlite_connection(db)) as connection:
                JobsRepository(connection).cancel_job(job["id"], reason="acceptance cancellation")
        elif mode == "loss":
            with closing(open_sqlite_connection(db)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _expire(connection)
                    # A standby cannot recover this same work while its old CLI is still registered.
                    assert JobsRepository(connection).requeue_expired_jobs() == []
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        out, err = child.communicate(timeout=20)
        receipt.update(
            workerExit=child.returncode,
            stderr=err.decode(errors="replace"),
            stdout=out.decode(errors="replace"),
            stopLatencyMs=(time.monotonic() - started) * 1000,
        )
        assert child.returncode == (17 if mode == "crash" else 0), receipt
        wait_until(lambda: identities_gone(native["members"]), 5)
        old_sizes = {str(p): p.stat().st_size for p in directory.glob("writer-*.log")}
        recovered = recover_managed_processes(db)
        receipt["recoveredProcessIds"] = recovered
        with closing(open_sqlite_connection(db)) as connection:
            _expire(connection)
            JobsRepository(connection).requeue_expired_jobs()
            record = ManagedProcessRepository(connection).get(ready["managedId"])
            receipt["processOutcome"] = asdict(record)
            assert record.finished_at and record.released_at
            assert ResourceRepository(connection).active_leases() == []
            if mode != "normal":
                # Explicit review/retry: never claim automatic replay of uncertain external effects.
                JobsRepository(connection).retry_job(
                    job["id"], reason="disposable test reviewed; writers stopped"
                )
        if mode != "normal":
            replacement, replacement_created = _launch(directory / "replacement", db, "normal")
            try:
                out, err = replacement.communicate(timeout=20)
                assert replacement.returncode == 0, err.decode(errors="replace")
                after = json.loads((directory / "replacement/ready.json").read_text())
                assert after["fence"] > ready["fence"]
                assert identities_gone(after["native"]["members"])
                receipt["replacement"] = after
            finally:
                if replacement.poll() is None:
                    stop_owned_tree(replacement.pid, os.getpid(), replacement_created)
                replacement.communicate(timeout=5)
            with closing(open_sqlite_connection(db)) as connection:
                assert JobsRepository(connection).get_job(job["id"])["status"] == "completed"
            assert all(Path(p).stat().st_size == size for p, size in old_sizes.items())
            receipt["sameJobRecoveredWithoutOldWrites"] = True
        receipt.update(status="PASS", remainingIdentities=0, finishedAt=time.time())
        return receipt
    finally:
        if child.poll() is None:
            stop_owned_tree(child.pid, os.getpid(), created)
        child.communicate(timeout=5)


@pytest.mark.parametrize("mode", ["normal", "cancel", "crash", "loss"])
def test_native_job_membership_and_same_job_recovery(tmp_path, mode):
    db = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db)) as connection:
        initialize_platform_schema(connection)
    result = _cycle(tmp_path, db, 0, mode)
    evidence(f"native-{mode}", result)


def test_thirty_sequential_cycles_with_cancellation_and_recovery(tmp_path):
    if os.environ.get("AIDO_ACCEPTANCE_SOAK") != "1":
        pytest.skip("Explicit 30-cycle acceptance opt-in; no inference is performed")
    db = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db)) as connection:
        initialize_platform_schema(connection)
    samples, cycles = [process_sample(db)], []
    report = {
        "status": "NOT_RUN",
        "samples": samples,
        "cycles": cycles,
        "scope": "30 serialized test jobs; controlled admission snapshot; real Windows processes/SQLite",
        "faultExpiry": "isolated DB timestamps advanced; no operational database mutation",
    }
    try:
        for index in range(30):
            mode = ["normal", "normal", "cancel", "crash", "loss"][index % 5]
            cycles.append(_cycle(tmp_path, db, index, mode))
            samples.append(process_sample(db))
            evidence("soak-30", report)
        with closing(open_sqlite_connection(db)) as connection:
            assert ManagedProcessRepository(connection).active() == []
            assert ResourceRepository(connection).active_leases() == []
            report["checkpoint"] = tuple(connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
        report["status"] = "PASS"
    except Exception as error:
        report.update(status="FAIL", error=str(error))
        raise
    finally:
        evidence("soak-30", report)
