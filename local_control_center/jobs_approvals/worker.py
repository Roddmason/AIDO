from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Callable

from local_control_center.store import PlatformStore


class ConcurrentWorker:
    def __init__(
        self,
        *,
        store_factory: Callable[[], PlatformStore],
        lease_ms: int = 300000,
    ):
        self.store_factory = store_factory
        self.lease_ms = lease_ms

    def recover(self) -> list[dict]:
        store = self.store_factory()
        try:
            store.init()
            return store.requeue_expired_jobs()
        finally:
            store.close()

    def run_once(self, *, worker_id: str) -> dict | None:
        store = self.store_factory()
        try:
            store.init()
            claimed = store.claim_next_job(worker_id=worker_id, lease_ms=self.lease_ms)
            if not claimed:
                return None
            try:
                execution = execute_job(claimed["job"])
                return store.complete_job_run(
                    job_id=claimed["job"]["id"],
                    run_id=claimed["run"]["id"],
                    status="completed",
                    summary=execution["summary"],
                    metadata=execution["metadata"],
                )
            except Exception as error:
                return store.complete_job_run(
                    job_id=claimed["job"]["id"],
                    run_id=claimed["run"]["id"],
                    status="failed",
                    summary=str(error),
                    metadata={"error": str(error)},
                )
        finally:
            store.close()

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
            pool.submit(_process_worker_once, str(db_path), str(cwd), f"process-worker-{index}", lease_ms)
            for index in range(worker_count)
        ]
        return [result for result in (future.result() for future in futures) if result]


def _process_worker_once(db_path: str, cwd: str, worker_id: str, lease_ms: int) -> dict | None:
    worker = ConcurrentWorker(
        store_factory=lambda: PlatformStore(cwd=Path(cwd), db_path=Path(db_path)),
        lease_ms=lease_ms,
    )
    return worker.run_once(worker_id=worker_id)


def execute_job(job: dict) -> dict:
    kind = job["kind"]
    payload = job.get("payload") or {}
    if kind == "prompt.optimize":
        return {
            "summary": "Prompt optimization recorded for manual review",
            "metadata": {"prompt": payload.get("prompt", "")},
        }
    if kind == "chat.route":
        return {
            "summary": "Chat routed through Python backend",
            "metadata": {"prompt": payload.get("prompt", ""), "mode": payload.get("mode", "auto")},
        }
    if kind == "pipeline.intake":
        pipeline_id = payload.get("pipelineId") or f"pipeline-{job['id']}"
        return {
            "summary": f"Pipeline intake completed: {pipeline_id}",
            "metadata": {"pipelineId": pipeline_id},
        }
    if kind in {"pipeline.start", "pipeline.retry", "pipeline.stage.retry"}:
        return {
            "summary": f"Pipeline command accepted: {kind}",
            "metadata": {"pipelineId": payload.get("pipelineId", "")},
        }
    return {"summary": f"Job completed: {kind}", "metadata": {}}
