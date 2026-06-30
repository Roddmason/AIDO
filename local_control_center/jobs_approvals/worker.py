"""Worker concurrente que reclama jobs de la cola, los ejecuta y cierra su run.

`ConcurrentWorker` abre su propia conexión SQLite por operación, recupera leases vencidos y
drena la cola con un pool de hilos. Los jobs de Threads ejecutan el Product Loop real; otros kinds
siguen fallando cerrados si todavía no tienen executor configurado.

@author Rodrigo Mason
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.redaction import redact_secrets
from local_control_center.threads.repository import ThreadsRepository

THREAD_PRODUCT_LOOP_JOB_KIND = "thread.product_loop.run"


class JobExecutionUnavailable(RuntimeError):
    """Señala que un job no se pudo ejecutar, portando el status y metadata para marcar el run fallido."""

    def __init__(self, *, status: str, summary: str, metadata: dict):
        super().__init__(summary)
        self.status = status
        self.summary = summary
        self.metadata = metadata


class ConcurrentWorker:
    """Ejecuta jobs de la cola SQLite, gestionando recuperación de leases y drenado concurrente."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        lease_ms: int = 300000,
    ):
        self.db_path = Path(db_path)
        self.lease_ms = lease_ms

    def recover(self) -> list[dict]:
        """Reencola los jobs cuyo lease venció antes de empezar a procesar la cola."""
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            return JobsRepository(connection).requeue_expired_jobs()
        finally:
            connection.close()

    def run_once(self, *, worker_id: str) -> dict | None:
        """Reclama y ejecuta un único job, cerrando su run como completado o fallido.

        Todo fallo de ejecución (incluido `JobExecutionUnavailable`) se captura y se materializa
        como run `failed` con su motivo; no propaga la excepción.

        Returns:
            El resultado del run cerrado, o `None` si no había job para reclamar.
        """
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            jobs = JobsRepository(connection)
            claimed = jobs.claim_next_job(worker_id=worker_id, lease_ms=self.lease_ms)
            if not claimed:
                return None
            try:
                execution = execute_job(
                    claimed["job"],
                    connection=connection,
                    db_path=self.db_path,
                    worker_id=worker_id,
                )
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
        """Recupera leases y drena la cola con un pool de hilos, devolviendo los runs ejecutados.

        Lanza `max_jobs` (o `worker_count`) intentos `run_once` en paralelo; cada intento sin job
        disponible se descarta del resultado.
        """
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


def execute_job(
    job: dict,
    *,
    connection: Any | None = None,
    db_path: str | Path | None = None,
    worker_id: str | None = None,
) -> dict:
    """Ejecuta un job según su kind.

    Placeholder actual: ningún kind tiene executor real conectado.

    Raises:
        JobExecutionUnavailable: siempre, con `configuration_required` para kinds conocidos y
            `unsupported_job_kind` para el resto.
    """
    kind = job["kind"]
    if kind == THREAD_PRODUCT_LOOP_JOB_KIND:
        if connection is not None:
            return _execute_thread_product_loop_job(job, connection=connection, worker_id=worker_id)
        if db_path is None:
            raise JobExecutionUnavailable(
                status="configuration_required",
                summary="Thread Product Loop jobs require a SQLite connection or db_path.",
                metadata={"kind": kind},
            )
        local_connection = open_sqlite_connection(db_path)
        try:
            initialize_platform_schema(local_connection)
            return _execute_thread_product_loop_job(
                job, connection=local_connection, worker_id=worker_id
            )
        finally:
            local_connection.close()
    if kind in {
        "prompt.optimize",
        "chat.route",
        "pipeline.intake",
        "pipeline.start",
        "pipeline.retry",
        "pipeline.stage.retry",
    }:
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


def _execute_thread_product_loop_job(
    job: dict,
    *,
    connection: Any,
    worker_id: str | None,
) -> dict:
    payload = dict(job.get("payload") or {})
    thread_id = str(payload.get("threadId") or "").strip()
    project_id = str(payload.get("projectId") or job.get("projectId") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not thread_id or not project_id or not message:
        raise JobExecutionUnavailable(
            status="configuration_required",
            summary="Thread Product Loop job is missing threadId, projectId, or message.",
            metadata={"kind": job["kind"], "threadId": thread_id, "projectId": project_id},
        )

    threads = ThreadsRepository(connection)
    threads.set_status(thread_id, "running")
    threads.record_event(
        thread_id=thread_id,
        type="worker_claimed",
        agent_role="aido_lead",
        payload={"jobId": job["id"], "workerId": worker_id or "worker"},
    )
    try:
        root = payload.get("root")
        result = ProductLoopCoordinator(connection, root=root).run_user_message(
            project_id=project_id,
            message=message,
            root=root,
            title=payload.get("title"),
            preferred_runtime=payload.get("preferredRuntime"),
            qa_commands=payload.get("qaCommands") if isinstance(payload.get("qaCommands"), list) else None,
            run_metadata={
                "jobId": job["id"],
                "threadId": thread_id,
                "messageId": payload.get("messageId"),
                "decision": payload.get("decision"),
                "teamPlan": payload.get("teamPlan"),
            },
            actor=worker_id or "thread_worker",
            thread_id=thread_id,
        )
    except Exception as error:
        reason = str(redact_secrets(str(error)))
        _finish_thread_after_product_loop(
            threads=threads,
            thread_id=thread_id,
            job_id=job["id"],
            status="blocked",
            reason=reason,
            loop_id=None,
            evidence_id=None,
        )
        raise

    loop = result.get("loop") if isinstance(result.get("loop"), dict) else {}
    evidence = result.get("evidencePackage") if isinstance(result.get("evidencePackage"), dict) else {}
    status = str(result.get("status") or "failed")
    reason = str(result.get("reason") or status)
    thread_status = _thread_status_for_product_loop_result(status)
    event_type = _thread_terminal_event_for_product_loop_result(status)
    _finish_thread_after_product_loop(
        threads=threads,
        thread_id=thread_id,
        job_id=job["id"],
        status=thread_status,
        reason=reason,
        loop_id=loop.get("id"),
        evidence_id=evidence.get("id"),
        event_type=event_type,
    )
    return {
        "summary": reason,
        "metadata": {
            "kind": job["kind"],
            "threadId": thread_id,
            "loopId": loop.get("id"),
            "productLoopStatus": status,
            "threadStatus": thread_status,
            "evidencePackageId": evidence.get("id"),
        },
    }


def _thread_status_for_product_loop_result(status: str) -> str:
    if status == "awaiting_approval":
        return "awaiting_approval"
    if status in {"completed", "delivered"}:
        return "resolved"
    if status in {"blocked", "failed", "reworking"}:
        return "blocked"
    return "open"


def _thread_terminal_event_for_product_loop_result(status: str) -> str:
    if status == "awaiting_approval":
        return "approval_required"
    if status in {"completed", "delivered"}:
        return "completed"
    return "blocked"


def _finish_thread_after_product_loop(
    *,
    threads: ThreadsRepository,
    thread_id: str,
    job_id: str,
    status: str,
    reason: str,
    loop_id: str | None,
    evidence_id: str | None,
    event_type: str = "blocked",
) -> None:
    threads.set_status(thread_id, status)
    payload = {
        "jobId": job_id,
        "loopId": loop_id,
        "status": status,
        "reason": reason,
        "evidencePackageId": evidence_id,
    }
    threads.record_event(
        thread_id=thread_id,
        type=event_type,
        agent_role="aido_lead",
        payload=payload,
    )
    message_kind = "error" if status == "blocked" else "aido_lead"
    threads.append_message(
        thread_id=thread_id,
        kind=message_kind,
        author="product_loop" if status == "blocked" else "aido_lead",
        content=reason,
        metadata=payload,
    )
