"""Cliente de contratos de dominio que consume explícitamente la nueva aceptación 202.

El handler real corre fuera del request, con job, liderazgo, reserva y conexión propios.
Usa un snapshot de test y ejecución in-process para conservar los fakes inyectados del slice.
No prueba aislamiento OS: test_durable_executions y test_process_supervision lo hacen con
TestClient normal y procesos reales. No habilita providers ni altera políticas o resultados.

@author Rodrigo Mason
"""

from __future__ import annotations

import uuid

import httpx
from fastapi.testclient import TestClient

from local_control_center.executions.repository import ExecutionRepository
from local_control_center.executions.runner import run_registered_operation
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.workers.leadership import WorkerLeadershipRepository


class CompletedExecutionClient(TestClient):
    """Encola por HTTP, ejecuta el job real y retorna el resultado terminal al test de dominio."""

    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        if response.status_code != 202 or "executionId" not in response.json():
            return response
        execution_id = response.json()["executionId"]
        platform = self.app.state.runtime
        complete_operation(platform, execution_id)
        terminal = super().get(f"/api/v1/executions/{execution_id}").json()
        assert terminal["status"] in {"completed", "blocked", "failed", "cancelled"}
        return httpx.Response(
            terminal["resultStatusCode"] or 500,
            json=terminal["result"] or {"detail": terminal["reason"]},
            request=response.request,
            headers={"X-Test-Execution-Id": execution_id},
        )


def complete_operation(platform, execution_id):
    """Completa sólo la operación seleccionada en una base de pruebas aislada."""
    with platform.operation_connection() as connection:
        job = JobsRepository(connection).get_job(execution_id)
        assert job["kind"] == "operation.execute", "Domain fixture cannot consume thread jobs"
    _complete_claimed_operation(platform, execution_id)


def _complete_claimed_operation(platform, execution_id):
    with platform.operation_connection() as connection:
        owner = f"test-domain-{uuid.uuid4().hex}"
        leaders = WorkerLeadershipRepository(connection)
        leadership = leaders.acquire(owner_id=owner, lease_seconds=300)
        assert leadership.acquired, "Another worker owns the isolated test database"
        jobs = JobsRepository(connection)
        governor = HostResourceGovernor(connection)
        snapshot = ResourceSnapshot.test_snapshot()
        governor.record_sample(snapshot)
        execution = ExecutionRepository(connection).get(execution_id)
        admission = governor.admit(
            ResourceAdmissionRequest(
                execution_id=execution_id,
                owner_id=owner,
                job_id=execution_id,
                workload_class=execution["workloadClass"],
                lease_seconds=300,
            ),
            snapshot=snapshot,
        )
        assert admission.lease is not None, admission.reason_code
        try:
            claimed = jobs.claim_next_job(
                worker_id=owner,
                lease_ms=300000,
                leader_fencing_token=leadership.fencing_token,
                job_id=execution_id,
            )
            assert claimed is not None
            result = run_registered_operation(
                platform, execution_id, owner_id=owner, fencing_token=leadership.fencing_token
            )
            jobs.complete_job_run(
                job_id=execution_id,
                run_id=claimed["run"]["id"],
                status="completed" if result["status"] == "completed" else "failed",
                summary=result["reason"],
                metadata={"executionId": execution_id},
                worker_id=owner,
                leader_fencing_token=leadership.fencing_token,
            )
        finally:
            governor.release(admission.lease.id, reason="test_domain_finished")
            leaders.release(
                owner_id=owner, fencing_token=leadership.fencing_token, reason="test_domain_finished"
            )
