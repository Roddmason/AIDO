"""Worker concurrente que reclama jobs de la cola, los ejecuta y cierra su run.

`ConcurrentWorker` abre su propia conexión SQLite por operación, recupera leases vencidos y
drena la cola con un pool de hilos. `execute_job` es hoy un placeholder: ningún kind tiene
executor real, así que cada job termina en `failed` con el motivo en su metadata.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


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


def execute_job(job: dict) -> dict:
    """Ejecuta un job según su kind.

    Placeholder actual: ningún kind tiene executor real conectado.

    Raises:
        JobExecutionUnavailable: siempre, con `configuration_required` para kinds conocidos y
            `unsupported_job_kind` para el resto.
    """
    kind = job["kind"]
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
