"""Worker de fault injection, usa cola/fencing/supervisor reales y un cuerpo de job de prueba.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from tests_py.operational_acceptance_support import native_readback, save, wait_until

MODULE = "tests_py.operational_acceptance_worker"


def writer(directory: Path, depth: int, duration: float) -> None:
    if depth:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                MODULE,
                "writer",
                str(directory),
                "--depth",
                str(depth - 1),
                "--duration",
                str(duration),
            ]
        )
    path = directory / f"writer-{os.getpid()}.log"
    deadline = time.monotonic() + duration
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        while time.monotonic() < deadline:
            start = time.monotonic()
            while time.monotonic() - start < 0.002:
                _ = sum(range(100))
            stream.write(f"{time.time_ns()}\n")
            time.sleep(0.048)


def worker(directory: Path, mode: str, db: Path) -> int:
    from local_control_center.host_resources.models import ResourceSnapshot
    from local_control_center.jobs_approvals import worker as module
    from local_control_center.process_supervision.service import ProcessSupervisorService
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    owner = f"acceptance-{os.getpid()}"
    with closing(open_sqlite_connection(db)) as connection:
        leader = WorkerLeadershipRepository(connection).acquire(owner_id=owner, lease_seconds=60)
    if not leader.acquired:
        return 75

    def execute(_job, **_kwargs):
        service = ProcessSupervisorService(db_path=db)
        child = service.start(
            argv=[
                sys.executable,
                "-m",
                MODULE,
                "writer",
                str(directory),
                "--depth",
                "1",
                "--duration",
                "1" if mode == "normal" else "30",
            ],
            cwd=Path.cwd(),
            workload_class="qa_light",
            cpu_limit_percent=5,
            memory_limit_bytes=256 * 1024**2,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_until(lambda: len(list(directory.glob("writer-*.log"))) >= 2)
            save(
                directory / "ready.json",
                {
                    "owner": owner,
                    "ownerPid": os.getpid(),
                    "fence": leader.fencing_token,
                    "managedId": child.managed_process_id,
                    "native": native_readback(child),
                },
            )
            while child.process.poll() is None:
                if mode == "crash" and (directory / "crash.flag").exists():
                    os._exit(17)
                time.sleep(0.025)
            child.process.communicate(timeout=5)
            result = service.complete(child, exit_code=child.process.returncode)
            return {"summary": result.termination_reason or "test writer finished", "metadata": {}}
        finally:
            if not child.released:
                service.cancel(child.managed_process_id, reason="acceptance_cleanup")

    # La única sustitución es el cuerpo no facturable del job. Claim, heartbeat, fencing,
    # recuperación, procesos y SQLite usan sus implementaciones productivas sin mocks.
    module.execute_job = execute
    runner = module.ConcurrentWorker(db_path=db, resource_snapshot=ResourceSnapshot.test_snapshot())
    runner.recover()
    result = runner.run_once(worker_id=owner, fencing_token=leader.fencing_token)
    save(directory / "worker-result.json", {"result": result})
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["writer", "normal", "crash", "loss", "cancel"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.action == "writer":
        writer(args.directory, args.depth, args.duration)
    else:
        raise SystemExit(worker(args.directory, args.action, args.db))
