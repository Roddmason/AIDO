"""Admisión global, backpressure y recuperación de leases de recursos.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now

from .models import (
    ResourceAdmissionDecision,
    ResourceAdmissionRequest,
    ResourceLease,
    ResourceSnapshot,
    ResourceViolation,
)
from .profiles import LOCAL_INFERENCE_CLASSES, resolve_resource_policy, workload_profile
from .repository import ResourceRepository
from .retention import RESOURCE_SAMPLE_RETENTION_SECONDS

UNREAL_LOCAL_INFERENCE_REASON = "Local GPU inference is blocked while UnrealEditor is active."


def _blocks_local_inference(workload_class: str, snapshot: ResourceSnapshot, policy: Any) -> bool:
    """La inferencia local (conjunto, no un literal) cede la GPU a UnrealEditor si la política lo pide."""
    return (
        workload_class in LOCAL_INFERENCE_CLASSES
        and snapshot.unreal_editor_running
        and policy.block_local_gpu_when_unreal
    )


class HostResourceGovernor:
    """Serializa admisiones durables y convierte falta de capacidad en espera recuperable."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = ResourceRepository(connection)

    def admit(
        self,
        request: ResourceAdmissionRequest,
        *,
        snapshot: ResourceSnapshot,
        now_iso: str | None = None,
    ) -> ResourceAdmissionDecision:
        """Admite una ejecución o devuelve ``resource_wait`` con un motivo técnico estable."""
        timestamp = now_iso or utc_now()
        profile = workload_profile(request.workload_class)
        policy = resolve_resource_policy(self.connection, snapshot=snapshot)
        with immediate_transaction(self.connection):
            self.repository.recover_expired(now_iso=timestamp)
            existing = self.repository.active_lease_for_execution(request.execution_id, now_iso=timestamp)
            if existing is not None:
                return ResourceAdmissionDecision(
                    status="admitted",
                    reason_code="existing_lease",
                    reason="Execution already owns an active resource lease.",
                    lease=existing,
                    snapshot=snapshot,
                )
            active = self.repository.active_leases(now_iso=timestamp)
            decision = self._decide(
                request=request,
                snapshot=snapshot,
                active=active,
                policy=policy,
            )
            if decision is None:
                lease = self.repository.create_lease(request, profile, now_iso=timestamp)
                decision = ResourceAdmissionDecision(
                    status="admitted",
                    reason_code="capacity_available",
                    reason="Host capacity is available for this workload profile.",
                    lease=lease,
                    snapshot=snapshot,
                )
                self._set_job_status(request.job_id, "queued", decision)
            else:
                self._set_job_status(request.job_id, "resource_wait", decision)
            self.repository.record_admission(request, decision)
            return decision

    def preview(
        self, request: ResourceAdmissionRequest, *, snapshot: ResourceSnapshot
    ) -> ResourceAdmissionDecision:
        """Evalúa capacidad sin crear leases ni escribir admisiones desde una lectura HTTP."""
        active = [
            lease for lease in self.repository.active_leases() if lease.execution_id != request.execution_id
        ]
        decision = self._decide(
            request=request,
            snapshot=snapshot,
            active=active,
            policy=resolve_resource_policy(self.connection, snapshot=snapshot),
        )
        return decision or ResourceAdmissionDecision(
            status="admitted",
            reason_code="capacity_available",
            reason="Host capacity is available; execution still requires atomic admission.",
            snapshot=snapshot,
        )

    def local_inference_conflict(
        self, workload_class: str, *, snapshot: ResourceSnapshot
    ) -> ResourceAdmissionDecision | None:
        """Evalúa el conflicto con Unreal usando la clase que pide el hijo, antes de cualquier préstamo.

        `admit` y `preview` lo aplican a la clase solicitada; quien reescribe esa clase a la del job padre (el
        préstamo de `BranchAdmission`, el preview de readiness) debe llamarlo antes con la clase original.
        """
        policy = resolve_resource_policy(self.connection, snapshot=snapshot)
        if not _blocks_local_inference(workload_class, snapshot, policy):
            return None
        return ResourceAdmissionDecision(
            status="resource_wait",
            reason_code="unreal_local_gpu_conflict",
            reason=UNREAL_LOCAL_INFERENCE_REASON,
            lease=None,
            snapshot=snapshot,
        )

    def _decide(
        self,
        *,
        request: ResourceAdmissionRequest,
        snapshot: ResourceSnapshot,
        active: list[ResourceLease],
        policy: Any,
    ) -> ResourceAdmissionDecision | None:
        profile = workload_profile(request.workload_class)

        def wait(reason_code: str, reason: str) -> ResourceAdmissionDecision:
            return ResourceAdmissionDecision(
                status="resource_wait",
                reason_code=reason_code,
                reason=reason,
                lease=None,
                snapshot=snapshot,
            )

        # Each lease is one independent CPU budget, including essential control-plane roots.
        # Their memory remains funded by the existing host headroom; never double-count it.
        reserved_cpu = sum(lease.cpu_limit_percent for lease in active)
        cpu_exceeded = reserved_cpu + profile.cpu_limit_percent > policy.max_cpu_percent
        if profile.essential and cpu_exceeded:
            return wait(
                "aggregate_cpu_budget",
                "Combined workload CPU limits exceed the configured aggregate CPU budget.",
            )
        if profile.essential:
            return None

        if snapshot.available_memory_bytes < policy.hard_free_memory_bytes:
            return wait(
                "hard_memory_floor",
                "Available memory is below the hard floor reserved for the control plane.",
            )
        if snapshot.available_memory_bytes < policy.min_free_memory_bytes:
            return wait(
                "minimum_free_memory",
                "Available memory is below the configured workload admission threshold.",
            )
        if (
            not snapshot.disk_free_bytes
            or min(snapshot.disk_free_bytes.values()) < policy.min_free_disk_bytes
        ):
            return wait(
                "minimum_free_disk",
                "A relevant volume is below the configured free-disk threshold.",
            )
        if max(snapshot.cpu_percent_1s, snapshot.cpu_percent_30s) > policy.max_cpu_percent:
            return wait("host_cpu_saturated", "Host CPU is above the configured admission threshold.")
        if _blocks_local_inference(request.workload_class, snapshot, policy):
            return wait("unreal_local_gpu_conflict", UNREAL_LOCAL_INFERENCE_REASON)

        active_classes = [lease.workload_class for lease in active]
        non_control_active = [lease for lease in active if lease.workload_class != "control_plane"]
        if request.workload_class == "unreal_cook" and non_control_active:
            return wait("unreal_cook_exclusive", "Unreal cook requires exclusive host capacity.")
        if "unreal_cook" in active_classes:
            return wait("unreal_cook_active", "An exclusive Unreal cook lease is active.")
        if (request.workload_class == "browser_test" and "build_heavy" in active_classes) or (
            request.workload_class == "build_heavy" and "browser_test" in active_classes
        ):
            return wait(
                "browser_build_conflict",
                "Browser tests and heavy builds cannot execute simultaneously.",
            )
        heavy_count = sum(workload_profile(lease.workload_class).heavy for lease in active)
        if profile.heavy and heavy_count >= policy.max_heavy_workloads:
            return wait(
                "heavy_workload_capacity",
                "The global heavy-workload slot is already reserved.",
            )
        light_count = sum(workload_profile(lease.workload_class).light for lease in active)
        if profile.light and light_count >= policy.max_light_workloads:
            return wait(
                "light_workload_capacity",
                "The configured light-workload concurrency limit is already reserved.",
            )
        if cpu_exceeded:
            return wait(
                "aggregate_cpu_budget",
                "Combined workload CPU limits exceed the configured aggregate CPU budget.",
            )
        # Conservador: la muestra no atribuye consumo actual a cada reserva viva.
        reserved_memory = sum(lease.memory_limit_bytes for lease in non_control_active)
        if (
            reserved_memory + profile.memory_limit_bytes
            > snapshot.available_memory_bytes - policy.min_free_memory_bytes
        ):
            return wait(
                "aggregate_memory_budget",
                "Combined workload memory limits would consume the reserved free-memory headroom.",
            )
        return None

    def _set_job_status(
        self,
        job_id: str | None,
        status: str,
        decision: ResourceAdmissionDecision,
    ) -> None:
        if not job_id:
            return
        row = self.connection.execute("SELECT status, payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        current = str(row["status"])
        if status == "resource_wait" and current not in {"queued", "resource_wait"}:
            return
        if status == "queued" and current != "resource_wait":
            return
        payload = json_loads(row["payload"], {})
        if (
            status == "resource_wait"
            and current == "resource_wait"
            and _last_reason_code(payload) == decision.reason_code
        ):
            return
        JobsRepository(self.connection).update_job_status(
            job_id,
            status=status,
            metadata={
                "resourceAdmission": decision.status,
                "reasonCode": decision.reason_code,
                "reason": decision.reason,
                "leaseId": decision.lease.id if decision.lease else None,
            },
        )
        if status == "resource_wait":
            self._record_thread_resource_wait(job_id, payload, decision)

    def _record_thread_resource_wait(
        self, job_id: str, payload: dict[str, Any], decision: ResourceAdmissionDecision
    ) -> None:
        """Avisa al hilo dueño del job por qué espera capacidad; un hilo ya borrado se omite."""
        thread_id = str(payload.get("threadId") or "").strip()
        if not thread_id:
            return
        from local_control_center.threads.repository import ThreadsRepository

        try:
            ThreadsRepository(self.connection).record_event(
                thread_id=thread_id,
                type="resource_wait",
                agent_role="worker",
                payload={
                    "jobId": job_id,
                    "reasonCode": decision.reason_code,
                    "reason": decision.reason,
                },
            )
        except KeyError:
            return

    def recover_expired(self, *, now_iso: str | None = None) -> list[ResourceLease]:
        """Recupera leases vencidas dentro de una transacción breve."""
        with immediate_transaction(self.connection):
            return self.repository.recover_expired(now_iso=now_iso)

    def heartbeat(
        self,
        lease_id: str,
        *,
        owner_id: str,
        lease_seconds: int = 300,
    ) -> ResourceLease | None:
        """Renueva una lease activa para una ejecución todavía supervisada."""
        with immediate_transaction(self.connection):
            return self.repository.heartbeat(
                lease_id,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )

    def release(self, lease_id: str, *, reason: str) -> ResourceLease:
        """Libera una lease explícitamente de forma idempotente."""
        with immediate_transaction(self.connection):
            return self.repository.release(lease_id, reason=reason)

    def reevaluate_waiting(
        self,
        *,
        snapshot: ResourceSnapshot,
        limit: int = 20,
    ) -> list[ResourceAdmissionDecision]:
        """Reevalúa solicitudes durables; las admitidas vuelven a ``queued`` con una lease."""
        requests = self.repository.waiting_requests(limit=limit)
        return [self.admit(request, snapshot=snapshot) for request in requests]

    def record_sample(self, snapshot: ResourceSnapshot) -> None:
        """Record a sample and apply retention without touching domain events."""
        with immediate_transaction(self.connection):
            self.repository.record_sample(snapshot)
            self.repository.prune_samples(retention_seconds=RESOURCE_SAMPLE_RETENTION_SECONDS)

    def violations_for_snapshot(
        self,
        snapshot: ResourceSnapshot,
    ) -> list[ResourceViolation]:
        """Registra solicitudes de cancelación para cargas no esenciales bajo el hard floor."""
        policy = resolve_resource_policy(self.connection, snapshot=snapshot)
        if snapshot.available_memory_bytes >= policy.hard_free_memory_bytes:
            return []
        violations: list[ResourceViolation] = []
        with immediate_transaction(self.connection):
            for lease in self.repository.active_leases():
                if workload_profile(lease.workload_class).essential:
                    continue
                violations.append(
                    self.repository.record_violation(
                        execution_id=lease.execution_id,
                        lease_id=lease.id,
                        violation_type="hard_memory_floor",
                        action="cancel_non_essential_workload",
                        reason="Available memory fell below the control-plane hard floor.",
                    )
                )
        return violations


def _last_reason_code(payload: dict[str, Any]) -> str | None:
    """Motivo de admisión que el governor ya dejó escrito en ``payload.result`` del job."""
    result = payload.get("result") if isinstance(payload, dict) else None
    return str(result.get("reasonCode")) if isinstance(result, dict) and result.get("reasonCode") else None
