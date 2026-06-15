"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


class JobExecutionUnavailable(RuntimeError):
    def __init__(self, *, status: str, summary: str, metadata: dict):
        super().__init__(summary)
        self.status = status
        self.summary = summary
        self.metadata = metadata


class ConcurrentWorker:
    def __init__(
        self,
        *,
        db_path: str | Path,
        lease_ms: int = 300000,
    ):
        self.db_path = Path(db_path)
        self.lease_ms = lease_ms

    def recover(self) -> list[dict]:
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            return JobsRepository(connection).requeue_expired_jobs()
        finally:
            connection.close()

    def run_once(self, *, worker_id: str) -> dict | None:
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            jobs = JobsRepository(connection)
            claimed = jobs.claim_next_job(worker_id=worker_id, lease_ms=self.lease_ms)
            if not claimed:
                return None
            try:
                execution = execute_job(claimed["job"])
                return jobs.complete_job_run(
                    job_id=claimed["job"]["id"],
                    run_id=claimed["run"]["id"],
                    status="completed",
                    summary=execution["summary"],
                    metadata=execution["metadata"],
                )
            except JobExecutionUnavailable as error:
                return jobs.complete_job_run(
                    job_id=claimed["job"]["id"],
                    run_id=claimed["run"]["id"],
                    status="failed",
                    summary=error.summary,
                    metadata={"status": error.status, **error.metadata},
                )
            except Exception as error:
                return jobs.complete_job_run(
                    job_id=claimed["job"]["id"],
                    run_id=claimed["run"]["id"],
                    status="failed",
                    summary=str(error),
                    metadata={"error": str(error)},
                )
        finally:
            connection.close()

    def run_batch(self, *, worker_count: int = 2, max_jobs: int | None = None) -> list[dict]:
        self.recover()
        total = max_jobs or worker_count
        results: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(self.run_once, worker_id=f"worker-{index}") for index in range(total)]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
        return results


def run_process_pool(*, db_path: str | Path, cwd: str | Path, worker_count: int = 2, lease_ms: int = 300000) -> list[dict]:
    with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = [
            pool.submit(_process_worker_once, str(db_path), f"process-worker-{index}", lease_ms)
            for index in range(worker_count)
        ]
        return [result for result in (future.result() for future in futures) if result]


def _process_worker_once(db_path: str, worker_id: str, lease_ms: int) -> dict | None:
    worker = ConcurrentWorker(
        db_path=Path(db_path),
        lease_ms=lease_ms,
    )
    return worker.run_once(worker_id=worker_id)


def execute_job(job: dict) -> dict:
    kind = job["kind"]
    if kind in {"prompt.optimize", "chat.route", "pipeline.intake", "pipeline.start", "pipeline.retry", "pipeline.stage.retry"}:
        raise JobExecutionUnavailable(
            status="configuration_required",
            summary=f"No real job executor is configured for {kind}.",
            metadata={"kind": kind},
        )
    raise JobExecutionUnavailable(
        status="unsupported_job_kind",
        summary=f"Unsupported job kind: {kind}.",
        metadata={"kind": kind},
    )
