"""Worker concurrente que reclama jobs de la cola, los ejecuta y cierra su run.

`ConcurrentWorker` abre su propia conexión SQLite por operación, recupera leases vencidos y
drena la cola con un pool de hilos. Los jobs de Threads ejecutan el Product Loop real; otros kinds
siguen fallando cerrados si todavía no tienen executor configurado.

@author Rodrigo Mason
"""

from __future__ import annotations

import concurrent.futures
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from local_control_center.agents.research_agent import ResearchAgentRunner
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import (
    ResourceAdmissionDecision,
    ResourceAdmissionRequest,
    ResourceSnapshot,
    WorkloadClass,
)
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import JobsRepository, StaleWorkerFenceError
from local_control_center.memory_retrieval.models import MEMORY_FORGET_JOB_KIND
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.redaction import redact_secrets
from local_control_center.threads.coordinator import THREAD_RESEARCH_JOB_KIND
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
        worker_id: str | None = None,
        fencing_token: int | None = None,
        resource_snapshot: ResourceSnapshot | None = None,
    ):
        self.db_path = Path(db_path)
        self.lease_ms = lease_ms
        self.worker_id = worker_id
        self.fencing_token = fencing_token
        self.resource_snapshot = resource_snapshot

    def recover(self) -> list[dict]:
        """Reencola los jobs cuyo lease venció antes de empezar a procesar la cola."""
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            from local_control_center.process_supervision.recovery import recover_managed_processes

            recover_managed_processes(self.db_path)
            return JobsRepository(connection).requeue_expired_jobs()
        finally:
            connection.close()

    def run_once(self, *, worker_id: str, fencing_token: int | None = None) -> dict | None:
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
            governor = HostResourceGovernor(connection)
            snapshot = self._resource_snapshot(connection)
            from local_control_center.process_supervision.session_client import session_identity

            session = session_identity(self.db_path)
            if session is None:
                governor.reevaluate_waiting(snapshot=snapshot)
            next_job = jobs.peek_next_job()
            if next_job is None:
                return None
            admission = (
                ResourceAdmissionDecision(
                    status="admitted",
                    reason_code="verified_session_budget",
                    reason="Work is contained in the launcher's admitted aggregate Job.",
                    lease=session[1],
                    snapshot=snapshot,
                )
                if session
                else governor.admit(
                    ResourceAdmissionRequest(
                        execution_id=next_job["id"],
                        workload_class=_workload_class_for_job(next_job, connection=connection),
                        owner_id=worker_id,
                        job_id=next_job["id"],
                        lease_seconds=max(1, self.lease_ms // 1000),
                    ),
                    snapshot=snapshot,
                )
            )
            if admission.status == "resource_wait" or admission.lease is None:
                return None
            resource_lease = admission.lease
            try:
                claimed = jobs.claim_next_job(
                    worker_id=worker_id,
                    lease_ms=self.lease_ms,
                    leader_fencing_token=fencing_token,
                    job_id=next_job["id"],
                )
            except StaleWorkerFenceError:
                if session is None:
                    governor.release(resource_lease.id, reason="leadership_fence_lost")
                return None
            if not claimed:
                if session is None:
                    governor.release(resource_lease.id, reason="job_claim_lost")
                return None
            try:
                with (
                    self._job_lease_heartbeat(
                        job_id=claimed["job"]["id"],
                        worker_id=worker_id,
                        fencing_token=fencing_token,
                        resource_lease_id=None if session else resource_lease.id,
                    ),
                    execution_scope(
                        ProcessExecutionContext(
                            db_path=self.db_path,
                            execution_id=claimed["job"]["id"],
                            project_id=claimed["job"]["projectId"],
                            resource_lease_id=resource_lease.id,
                            connection=connection,
                            worker_id=worker_id,
                            fencing_token=fencing_token,
                            request_id=claimed["job"]["payload"]
                            .get("diagnosticContext", {})
                            .get("requestId"),
                            attempt_id=claimed["run"]["id"],
                            aggregate_managed_process_id=session[0]["aggregateId"] if session else None,
                            session_role="execution" if session else None,
                        )
                    ),
                ):
                    try:
                        from local_control_center.shared.diagnostics import diagnostic_event

                        diagnostic_event("worker.claimed", component="worker")
                        execution = execute_job(
                            claimed["job"],
                            connection=connection,
                            db_path=self.db_path,
                            worker_id=worker_id,
                        )
                        cancellation = ManagedProcessRepository(connection).cancellation_reason(
                            claimed["job"]["id"]
                        )
                        return jobs.complete_job_run(
                            job_id=claimed["job"]["id"],
                            run_id=claimed["run"]["id"],
                            status="cancelled" if cancellation else "completed",
                            summary=cancellation or execution["summary"],
                            metadata=execution["metadata"],
                            worker_id=worker_id if fencing_token is not None else None,
                            leader_fencing_token=fencing_token,
                        )
                    except JobExecutionUnavailable as error:
                        cancellation = ManagedProcessRepository(connection).cancellation_reason(
                            claimed["job"]["id"]
                        )
                        return jobs.complete_job_run(
                            job_id=claimed["job"]["id"],
                            run_id=claimed["run"]["id"],
                            status="cancelled" if cancellation else "failed",
                            summary=cancellation or error.summary,
                            metadata={"status": error.status, **error.metadata},
                            worker_id=worker_id if fencing_token is not None else None,
                            leader_fencing_token=fencing_token,
                        )
                    except StaleWorkerFenceError:
                        return None
                    except Exception as error:
                        cancellation = ManagedProcessRepository(connection).cancellation_reason(
                            claimed["job"]["id"]
                        )
                        return jobs.complete_job_run(
                            job_id=claimed["job"]["id"],
                            run_id=claimed["run"]["id"],
                            status="cancelled" if cancellation else "failed",
                            summary=cancellation or str(error),
                            metadata={"error": str(error)},
                            worker_id=worker_id if fencing_token is not None else None,
                            leader_fencing_token=fencing_token,
                        )
            finally:
                if session is None:
                    governor.release(resource_lease.id, reason="execution_finished")
        finally:
            connection.close()

    def _resource_snapshot(self, connection: Any) -> ResourceSnapshot:
        if self.resource_snapshot is not None:
            return self.resource_snapshot
        repository = ResourceRepository(connection)
        probe = HostResourceProbe(
            relevant_paths=[self.db_path.parent, self.db_path],
            active_workload_source=lambda: [lease.workload_class for lease in repository.active_leases()],
        )
        snapshot = probe.sample(cpu_interval_seconds=0)
        HostResourceGovernor(connection).record_sample(snapshot)
        return snapshot

    @contextmanager
    def _job_lease_heartbeat(
        self,
        *,
        job_id: str,
        worker_id: str,
        fencing_token: int | None,
        resource_lease_id: str | None = None,
    ):
        """Renueva la lease del job mientras su ejecución síncrona todavía está en curso."""
        if fencing_token is None and resource_lease_id is None:
            yield
            return
        stop = threading.Event()

        def renew() -> None:
            interval = max(0.1, self.lease_ms / 3000)
            while not stop.wait(interval):
                with closing(open_sqlite_connection(self.db_path)) as heartbeat_connection:
                    initialize_platform_schema(heartbeat_connection)
                    if fencing_token is not None and not JobsRepository(
                        heartbeat_connection
                    ).heartbeat_job_lease(
                        job_id=job_id,
                        worker_id=worker_id,
                        leader_fencing_token=fencing_token,
                        lease_ms=self.lease_ms,
                    ):
                        return
                    if resource_lease_id is not None and not HostResourceGovernor(
                        heartbeat_connection
                    ).heartbeat(
                        resource_lease_id,
                        owner_id=worker_id,
                        lease_seconds=max(1, self.lease_ms // 1000),
                    ):
                        return

        thread = threading.Thread(target=renew, name=f"aido-job-heartbeat-{job_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)

    def run_batch(
        self,
        *,
        worker_count: int = 1,
        max_jobs: int | None = None,
        worker_id: str | None = None,
        fencing_token: int | None = None,
    ) -> list[dict]:
        """Recupera leases y drena la cola con un pool de hilos, devolviendo los runs ejecutados.

        Lanza `max_jobs` (o `worker_count`) intentos `run_once` en paralelo; cada intento sin job
        disponible se descarta del resultado.
        """
        self.recover()
        total = max_jobs or worker_count
        results: list[dict] = []
        resolved_worker_id = worker_id or self.worker_id
        resolved_fence = fencing_token if fencing_token is not None else self.fencing_token
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(
                    self.run_once,
                    worker_id=resolved_worker_id or f"worker-{index}",
                    fencing_token=resolved_fence,
                )
                for index in range(total)
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
        return results


def _workload_class_for_job(job: dict[str, Any], *, connection: Any | None = None) -> WorkloadClass:
    """Clasifica conservadoramente jobs productivos antes de reservar capacidad."""
    kind = str(job.get("kind") or "")
    if kind == "operation.execute":
        return job["payload"]["workloadClass"]
    if connection is not None and kind in {
        THREAD_PRODUCT_LOOP_JOB_KIND,
        THREAD_RESEARCH_JOB_KIND,
        "prompt.optimize",
        "chat.route",
    }:
        from local_control_center.agents.provider_accounts import ProviderAccountStore
        from local_control_center.agents.providers.factory import provider_account_policy_kind
        from local_control_center.agents.runtime_readiness import provider_workload_class
        from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

        policy = RuntimeConfigRepository(connection)
        permitted_workloads = {
            provider_workload_class(account)
            for account in ProviderAccountStore(connection).list_provider_accounts()
            if account["enabled"]
            and account["providerType"] in {"local", "api", "gateway", "cli"}
            and policy.runtime_policy_decision(
                provider_id=account["providerId"],
                kind=provider_account_policy_kind(account),
                project_id=job.get("projectId"),
                provider_family=account.get("providerFamily"),
                account=account,
            )["allowed"]
        }
        if permitted_workloads == {"local_gpu_model"}:
            return "local_gpu_model"
    if kind == MEMORY_FORGET_JOB_KIND:
        # Borrado lógico en SQLite más un rebuild de índice: no merece el perfil pesado por defecto.
        return "control_plane"
    if kind == THREAD_RESEARCH_JOB_KIND or kind in {"prompt.optimize", "chat.route"}:
        return "remote_llm_light"
    if kind.startswith("pipeline."):
        return "build_heavy"
    return "agent_cli"


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
    from local_control_center.process_supervision.context import CURRENT_EXECUTION

    context = CURRENT_EXECUTION.get()
    contained_legacy = (
        kind in {THREAD_RESEARCH_JOB_KIND, THREAD_PRODUCT_LOOP_JOB_KIND}
        and context is not None
        and context.fencing_token is not None
        and not context.in_job_runner
    )
    if contained_legacy:
        from local_control_center.executions.repository import ExecutionRepository

        workload = _workload_class_for_job(job)
        if not context.aggregate_managed_process_id:
            lease = ResourceRepository(connection).active_lease_for_execution(job["id"])
            if lease is None or lease.id != context.resource_lease_id or lease.owner_id != context.worker_id:
                raise JobExecutionUnavailable(
                    status="resource_wait",
                    summary="The runner requires its own active resource reservation.",
                    metadata={"reason": "resource_parent_identity_mismatch"},
                )
            # The admitted envelope survives policy edits between claim and dispatch.
            workload = lease.workload_class
        ExecutionRepository(connection).attach_claimed_job(
            job,
            workload_class=workload,
            cwd=str(Path(db_path).parent),
            owner_id=context.worker_id,
            fencing_token=context.fencing_token,
        )
    if kind == "operation.execute" or contained_legacy:
        from local_control_center.executions.dispatcher import dispatch_execution

        result = dispatch_execution(job, connection=connection, db_path=db_path)
        if result["metadata"]["status"] != "completed":
            raise JobExecutionUnavailable(
                status=result["metadata"]["status"], summary=result["summary"], metadata=result["metadata"]
            )
        return result
    if kind == THREAD_RESEARCH_JOB_KIND:
        if connection is not None:
            return _execute_thread_research_job(job, connection=connection, worker_id=worker_id)
        if db_path is None:
            raise JobExecutionUnavailable(
                status="configuration_required",
                summary="Thread research jobs require a SQLite connection or db_path.",
                metadata={"kind": kind},
            )
        local_connection = open_sqlite_connection(db_path)
        try:
            initialize_platform_schema(local_connection)
            return _execute_thread_research_job(job, connection=local_connection, worker_id=worker_id)
        finally:
            local_connection.close()
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
            return _execute_thread_product_loop_job(job, connection=local_connection, worker_id=worker_id)
        finally:
            local_connection.close()
    if kind == MEMORY_FORGET_JOB_KIND:
        if connection is not None:
            return _execute_memory_forget_job(job, connection=connection, db_path=db_path)
        if db_path is None:
            raise JobExecutionUnavailable(
                status="configuration_required",
                summary="Memory forget jobs require a SQLite connection or db_path.",
                metadata={"kind": kind},
            )
        local_connection = open_sqlite_connection(db_path)
        try:
            initialize_platform_schema(local_connection)
            return _execute_memory_forget_job(job, connection=local_connection, db_path=db_path)
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


def _execute_memory_forget_job(job: dict, *, connection: Any, db_path: str | Path | None) -> dict:
    """Aplica la política de olvido: borra lógicamente los items vencidos y los saca del índice.

    A diferencia de los otros executors, recibe ``db_path`` en vez de ``worker_id``: el índice
    vectorial vive en ``db_path.parent / "faiss-index"`` (la misma ruta que arma el router en
    ``memory_retrieval/api.py``) y sin ella el retrieval seguiría devolviendo lo ya olvidado.

    La idempotencia sale del predicado, no de ``idempotency_key`` (que hoy no deduplica nada): la
    selección excluye lo ya borrado, así que una segunda corrida no encuentra filas ni emite
    eventos. El índice sólo se reconstruye si efectivamente se olvidó algo.
    """
    from local_control_center.memory_retrieval.index import RetrievalIndex
    from local_control_center.memory_retrieval.repository import MemoryRepository
    from local_control_center.shared.event_bus import EventBus

    project_id = str(job["payload"].get("projectId") or job["projectId"] or "")
    if not project_id:
        raise JobExecutionUnavailable(
            status="configuration_required",
            summary="Memory forget jobs require a projectId.",
            metadata={"kind": job["kind"]},
        )
    memory = MemoryRepository(connection)
    events = EventBus(connection)
    expired = memory.list_expired_memory_items(project_id)
    skipped = memory.count_non_canonical_expiry(project_id)
    for item in expired:
        memory.delete_memory_item(item["id"], reason="expired_by_retention_policy")
        events.record_event(
            project_id=project_id,
            event_type="memory.expired",
            payload={"memoryItemId": item["id"], "expiresAt": item["expiresAt"]},
        )
    reindexed = False
    if expired and db_path is not None:
        RetrievalIndex(memory=memory, index_dir=Path(db_path).parent / "faiss-index").rebuild(
            project_id=project_id
        )
        reindexed = True
    return {
        "summary": f"Forgot {len(expired)} expired memory items for project {project_id}.",
        "metadata": {
            "kind": job["kind"],
            "projectId": project_id,
            "forgotten": len(expired),
            "skippedNonCanonicalExpiry": skipped,
            "reindexed": reindexed,
        },
    }


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
    # Closes the claim→cancel window: the operator may have stopped this job between the claim and here,
    # and starting the loop now would run it beside the replacement run the operator queued instead.
    if _thread_job_was_cancelled(threads, job["id"]):
        threads.record_event(
            thread_id=thread_id,
            type="worker_aborted",
            agent_role="aido_lead",
            payload={"jobId": job["id"], "reason": "Job was cancelled before the Product Loop started."},
        )
        return {
            "summary": "Product Loop was cancelled before it started.",
            "metadata": {"kind": job["kind"], "threadId": thread_id, "productLoopStatus": "cancelled"},
        }
    threads.set_status(thread_id, "running")
    threads.record_event(
        thread_id=thread_id,
        type="worker_started",
        agent_role="aido_lead",
        payload={"jobId": job["id"], "workerId": worker_id or "worker"},
    )
    threads.record_event(
        thread_id=thread_id,
        type="worker_claimed",
        agent_role="aido_lead",
        payload={"jobId": job["id"], "workerId": worker_id or "worker"},
    )
    try:
        root = payload.get("root")
        run_metadata = (
            strip_untrusted_resource_cost_policy_metadata(payload.get("runMetadata"))
            if isinstance(payload.get("runMetadata"), dict)
            else {}
        )
        approved_resource_selections = (
            payload.get("approvedResourceSelections")
            if isinstance(payload.get("approvedResourceSelections"), list)
            else run_metadata.get("approvedResourceSelections")
        )
        if not isinstance(approved_resource_selections, list):
            approved_resource_selections = []
        run_metadata.update(
            {
                "jobId": job["id"],
                "threadId": thread_id,
                "messageId": payload.get("messageId"),
                "decision": payload.get("decision"),
                "teamPlan": payload.get("teamPlan"),
                "planOnly": bool(payload.get("planOnly") or run_metadata.get("planOnly")),
                "planOnlyOfLoopId": payload.get("planOnlyOfLoopId") or run_metadata.get("planOnlyOfLoopId"),
                "planOnlyStage": payload.get("planOnlyStage") or run_metadata.get("planOnlyStage"),
                "planOnlyReason": payload.get("planOnlyReason") or run_metadata.get("planOnlyReason"),
                "planOnlyQueuedAt": payload.get("planOnlyQueuedAt") or run_metadata.get("planOnlyQueuedAt"),
                "remediationActionId": payload.get("remediationActionId")
                or run_metadata.get("remediationActionId"),
                "approvedResourceSelections": approved_resource_selections,
                "retryOfLoopId": payload.get("retryOfLoopId") or run_metadata.get("retryOfLoopId"),
                "retryStage": payload.get("retryStage") or run_metadata.get("retryStage"),
                "retryReason": payload.get("retryReason") or run_metadata.get("retryReason"),
                "retryQueuedAt": payload.get("retryQueuedAt") or run_metadata.get("retryQueuedAt"),
                "continueOfLoopId": payload.get("continueOfLoopId") or run_metadata.get("continueOfLoopId"),
                "feedbackId": payload.get("feedbackId") or run_metadata.get("feedbackId"),
                "continueReason": payload.get("continueReason") or run_metadata.get("continueReason"),
                "continueQueuedAt": payload.get("continueQueuedAt") or run_metadata.get("continueQueuedAt"),
            }
        )
        result = ProductLoopCoordinator(connection, root=root).run_user_message(
            project_id=project_id,
            message=message,
            root=root,
            title=payload.get("title"),
            preferred_runtime=payload.get("preferredRuntime"),
            qa_commands=payload.get("qaCommands") if isinstance(payload.get("qaCommands"), list) else None,
            run_metadata=run_metadata,
            actor=worker_id or "thread_worker",
            thread_id=thread_id,
            # Live signal, re-read per stage: a cancel committed on another connection stops this loop.
            should_abort=lambda: _thread_job_was_cancelled(threads, job["id"]),
        )
    except Exception as error:
        reason = str(redact_secrets(str(error)))
        threads.record_event(
            thread_id=thread_id,
            type="worker_failed",
            agent_role="aido_lead",
            payload={"jobId": job["id"], "workerId": worker_id or "worker", "reason": reason},
        )
        _finish_thread_after_product_loop(
            threads=threads,
            thread_id=thread_id,
            job_id=job["id"],
            status="blocked",
            reason=reason,
            loop_id=None,
            evidence_id=None,
        )
        _materialize_product_loop_worker_remediations(
            threads=threads,
            project_id=project_id,
            thread_id=thread_id,
            job_id=job["id"],
            reason=reason,
            root=Path(str(payload.get("root") or ".")).resolve(strict=False),
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


def _execute_thread_research_job(
    job: dict,
    *,
    connection: Any,
    worker_id: str | None,
) -> dict:
    payload = dict(job.get("payload") or {})
    thread_id = str(payload.get("threadId") or "").strip()
    project_id = str(payload.get("projectId") or job.get("projectId") or "").strip()
    query = str(payload.get("query") or "").strip()
    workspace_id = str(payload.get("workspaceId") or "").strip()
    if not thread_id or not project_id or not query or not workspace_id:
        raise JobExecutionUnavailable(
            status="configuration_required",
            summary="Thread research job is missing threadId, projectId, query, or workspaceId.",
            metadata={
                "kind": job["kind"],
                "threadId": thread_id,
                "projectId": project_id,
                "workspaceId": workspace_id,
            },
        )

    threads = ThreadsRepository(connection)
    threads.set_status(thread_id, "running")
    threads.record_event(
        thread_id=thread_id,
        type="worker_claimed",
        agent_role="researcher",
        payload={"jobId": job["id"], "workerId": worker_id or "worker"},
    )
    root = Path(str(payload.get("root") or ".")).resolve(strict=False)
    research_payload = {
        "projectId": project_id,
        "workspaceId": workspace_id,
        "taskId": payload.get("taskId") or f"thread-research-{thread_id}",
        "query": query,
        "maxSources": payload.get("maxSources"),
        "sources": payload.get("sources") if isinstance(payload.get("sources"), list) else [],
        "conclusions": payload.get("conclusions") if isinstance(payload.get("conclusions"), list) else [],
        "claims": payload.get("claims") if isinstance(payload.get("claims"), list) else [],
        "technicalDecisions": payload.get("technicalDecisions")
        if isinstance(payload.get("technicalDecisions"), list)
        else [],
        "jobId": job["id"],
        "metadata": {
            **(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
            "threadId": thread_id,
            "messageId": payload.get("messageId"),
            "jobId": job["id"],
            "query": query,
        },
    }
    try:
        result = ResearchAgentRunner(connection, root=root).run(research_payload)
    except Exception as error:
        reason = str(redact_secrets(str(error)))
        _finish_thread_after_research(
            threads=threads,
            thread_id=thread_id,
            job_id=job["id"],
            status="blocked",
            reason=reason,
            report_artifact_id=None,
            event_type="blocked",
        )
        _materialize_research_remediations(
            threads=threads,
            project_id=project_id,
            thread_id=thread_id,
            job_id=job["id"],
            reason=reason,
            root=root,
            research_status="research_blocked",
            report_artifact_id=None,
            result={},
        )
        raise

    status = str(result.get("status") or "research_blocked")
    reason = str(result.get("reason") or status)
    report = result.get("reportArtifact") if isinstance(result.get("reportArtifact"), dict) else {}
    thread_status = "resolved" if status == "research_ready" else "blocked"
    event_type = "completed" if status == "research_ready" else "blocked"
    _finish_thread_after_research(
        threads=threads,
        thread_id=thread_id,
        job_id=job["id"],
        status=thread_status,
        reason=reason,
        report_artifact_id=report.get("id"),
        event_type=event_type,
    )
    if thread_status == "blocked":
        _materialize_research_remediations(
            threads=threads,
            project_id=project_id,
            thread_id=thread_id,
            job_id=job["id"],
            reason=reason,
            root=root,
            research_status=status,
            report_artifact_id=report.get("id"),
            result=result,
        )
    return {
        "summary": reason,
        "metadata": {
            "kind": job["kind"],
            "threadId": thread_id,
            "researchStatus": status,
            "threadStatus": thread_status,
            "reportArtifactId": report.get("id"),
        },
    }


# Ruteo explícito status del run -> (status del hilo, tipo de evento terminal). Cada status que el
# coordinator retorna (RUN_RESULT_STATUSES) debe tener su fila: el gate
# test_product_loop_result_taxonomy.py falla ante un status nuevo sin decisión, porque el fallback
# fail-closed de abajo pinta el pipeline del hilo como bloqueado y eso le mintió al operador cuando
# brief_ready caía en él.
PRODUCT_LOOP_RESULT_ROUTING: dict[str, tuple[str, str]] = {
    "awaiting_approval": ("awaiting_approval", "approval_required"),
    "awaiting_user": ("waiting_decision", "decision_required"),
    "blocked": ("blocked", "blocked"),
    "brief_ready": ("open", "brief_ready"),
    "cancelled": ("open", "cancelled"),
    "completed": ("resolved", "completed"),
    "delivered": ("resolved", "completed"),
    "failed": ("blocked", "blocked"),
    "plan_ready": ("open", "plan_ready"),
    "reworking": ("blocked", "blocked"),
}
# Fallback fail-closed para statuses fuera del contrato: mejor un falso bloqueo visible (que el gate
# de taxonomía convierte en rojo de test) que un hilo resuelto sin evidencia.
_UNMAPPED_RESULT_ROUTE = ("blocked", "blocked")


def _thread_status_for_product_loop_result(status: str) -> str:
    return PRODUCT_LOOP_RESULT_ROUTING.get(status, _UNMAPPED_RESULT_ROUTE)[0]


def _thread_terminal_event_for_product_loop_result(status: str) -> str:
    return PRODUCT_LOOP_RESULT_ROUTING.get(status, _UNMAPPED_RESULT_ROUTE)[1]


def _thread_job_was_cancelled(threads: ThreadsRepository, job_id: str) -> bool:
    """Indica si el job del hilo fue cancelado mientras el worker lo ejecutaba.

    Lee fresco (autocommit + WAL) para ver un cancel commiteado en otra conexión: le indica al worker
    que aborte su finalización en vez de resucitar un hilo que el operador ya detuvo.
    """
    row = threads.connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(row) and str(row["status"]) == "cancelled"


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
    # If the operator cancelled this job mid-run, do not overwrite the thread status: leave whatever
    # the cancel (or a replacement run) set and only leave a benign trace in the execution console.
    if _thread_job_was_cancelled(threads, job_id):
        threads.record_event(
            thread_id=thread_id,
            type="worker_aborted",
            agent_role="aido_lead",
            payload={"jobId": job_id, "reason": "Execution was cancelled; thread status preserved."},
        )
        return
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


def _finish_thread_after_research(
    *,
    threads: ThreadsRepository,
    thread_id: str,
    job_id: str,
    status: str,
    reason: str,
    report_artifact_id: str | None,
    event_type: str,
) -> None:
    # Same cancellation guard as the product-loop finalizer: a stopped research run must not resurrect
    # the thread the operator already reopened.
    if _thread_job_was_cancelled(threads, job_id):
        threads.record_event(
            thread_id=thread_id,
            type="worker_aborted",
            agent_role="researcher",
            payload={"jobId": job_id, "reason": "Research was cancelled; thread status preserved."},
        )
        return
    threads.set_status(thread_id, status)
    payload = {
        "jobId": job_id,
        "status": status,
        "reason": reason,
        "reportArtifactId": report_artifact_id,
    }
    threads.record_event(
        thread_id=thread_id,
        type=event_type,
        agent_role="researcher",
        payload=payload,
    )
    threads.append_message(
        thread_id=thread_id,
        kind="error" if status == "blocked" else "aido_lead",
        author="research_agent",
        content=reason,
        metadata=payload,
    )


def _materialize_research_remediations(
    *,
    threads: ThreadsRepository,
    project_id: str,
    thread_id: str,
    job_id: str,
    reason: str,
    root: Path,
    research_status: str,
    report_artifact_id: str | None,
    result: dict[str, Any],
) -> None:
    if _thread_job_was_cancelled(threads, job_id):
        return
    remediation = result.get("remediation") if isinstance(result.get("remediation"), dict) else {}
    research_run = result.get("researchRun") if isinstance(result.get("researchRun"), dict) else {}
    BlockerRemediationService(threads.connection, root=root).create_for_blocked_run(
        project_id=project_id,
        thread_id=thread_id,
        loop_id=None,
        stage="research",
        reason=reason,
        details={
            "status": research_status,
            "jobId": job_id,
            "researchRunId": research_run.get("id"),
            "reportArtifactId": report_artifact_id,
            "remediation": remediation,
        },
    )


def _materialize_product_loop_worker_remediations(
    *,
    threads: ThreadsRepository,
    project_id: str,
    thread_id: str,
    job_id: str,
    reason: str,
    root: Path,
) -> None:
    if _thread_job_was_cancelled(threads, job_id):
        return
    BlockerRemediationService(threads.connection, root=root).create_for_blocked_run(
        project_id=project_id,
        thread_id=thread_id,
        loop_id=None,
        stage="worker",
        reason=reason,
        details={
            "status": "blocked",
            "jobId": job_id,
            "kind": THREAD_PRODUCT_LOOP_JOB_KIND,
        },
    )
