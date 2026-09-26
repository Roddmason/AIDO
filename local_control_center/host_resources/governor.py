"""Admisión global, backpressure y recuperación de leases de recursos.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.diagnostics import diagnostic_event
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now

from .models import (
    ResourceAdmissionDecision,
    ResourceAdmissionRequest,
    ResourceLease,
    ResourceSnapshot,
    ResourceViolation,
)
from .profiles import (
    GIB,
    LOCAL_INFERENCE_CLASSES,
    admission_memory_bytes,
    resolve_resource_policy,
    workload_profile,
)
from .repository import ResourceRepository
from .retention import RESOURCE_SAMPLE_RETENTION_SECONDS

UNREAL_LOCAL_INFERENCE_REASON = "Local GPU inference is blocked while UnrealEditor is active."

EVICTION_GRACE_SECONDS = 6
"""3x el intervalo de muestreo por defecto (2 s): tiempo para que el SO libere la memoria de la
lease recién desalojada antes de evaluar una siguiente víctima con la misma fotografía de presión."""


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
        reserved_memory = sum(
            lease.memory_limit_bytes if lease.memory_request_bytes is None else lease.memory_request_bytes
            for lease in non_control_active
        )
        requested_memory = admission_memory_bytes(profile)
        headroom = snapshot.available_memory_bytes - policy.min_free_memory_bytes
        if reserved_memory + requested_memory > headroom:
            return wait(
                "aggregate_memory_budget",
                "Combined workload memory requests would consume the reserved free-memory headroom: "
                f"{requested_memory / GIB:.1f} GiB requested, {reserved_memory / GIB:.1f} GiB already "
                f"reserved, {max(headroom, 0) / GIB:.1f} GiB of headroom.",
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
        *,
        usage_source: Callable[[], Mapping[str, int]] | None = None,
        now_iso: str | None = None,
    ) -> list[ResourceViolation]:
        """Desaloja lease por lease bajo el piso duro, como el node-pressure eviction de Kubernetes.

        Kubernetes desaloja un pod a la vez, no todo el nodo
        (https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/). Ver ADR-005.
        Prioriza la lease cuyo uso real (RSS, ``usage_source``) más excede su reserva; sin esa
        fuente (compatibilidad hacia atrás) todas las leases no esenciales son candidatas con uso
        0 y se desempata por la más reciente. Una gracia de ``EVICTION_GRACE_SECONDS`` tras la
        última violación evita apilar desalojos antes de que el sistema operativo libere la
        memoria de la lease recién terminada.
        """
        policy = resolve_resource_policy(self.connection, snapshot=snapshot)
        if snapshot.available_memory_bytes >= policy.hard_free_memory_bytes:
            return []
        timestamp = now_iso or utc_now()
        usage: Mapping[str, int] = {}
        usage_known = usage_source is not None
        if usage_source is not None:
            try:
                usage = usage_source()
            except Exception as error:
                # Sin medición no se puede priorizar por exceso: se vuelve al criterio sin uso (todas
                # las no esenciales candidatas, la más reciente primero), nunca a dejar de desalojar.
                diagnostic_event(
                    "resources.eviction_usage_unavailable", component="governor", level="WARNING", error=error
                )
                usage, usage_known = {}, False
        with immediate_transaction(self.connection):
            latest_at = self.repository.latest_violation_created_at(violation_type="hard_memory_floor")
            if latest_at is not None and _seconds_between(latest_at, timestamp) < EVICTION_GRACE_SECONDS:
                return []
            excluded = self.repository.leases_with_unresolved_violation(
                action="cancel_non_essential_workload"
            )
            candidates = [
                lease
                for lease in self.repository.active_leases(now_iso=timestamp)
                if not workload_profile(lease.workload_class).essential
                and lease.id not in excluded
                and (not usage_known or lease.id in usage)
            ]
            if not candidates:
                return []
            victim = _rank_eviction_candidates(candidates, usage)[0]
            used = usage.get(victim.id, 0)
            reserved = _reserved_bytes(victim)
            reason = (
                "Available memory fell below the control-plane hard floor: "
                f"{used / GIB:.1f} GiB used, {reserved / GIB:.1f} GiB reserved, "
                f"{snapshot.available_memory_bytes / GIB:.1f} GiB available, "
                f"{policy.hard_free_memory_bytes / GIB:.1f} GiB floor."
            )
            return [
                self.repository.record_violation(
                    execution_id=victim.execution_id,
                    lease_id=victim.id,
                    violation_type="hard_memory_floor",
                    action="cancel_non_essential_workload",
                    reason=reason,
                    now_iso=timestamp,
                )
            ]


def _last_reason_code(payload: dict[str, Any]) -> str | None:
    """Motivo de admisión que el governor ya dejó escrito en ``payload.result`` del job."""
    result = payload.get("result") if isinstance(payload, dict) else None
    return str(result.get("reasonCode")) if isinstance(result, dict) and result.get("reasonCode") else None


def _seconds_between(earlier_iso: str, later_iso: str) -> float:
    """Diferencia en segundos entre dos timestamps ISO-8601 UTC (acepta sufijo ``Z``)."""
    earlier = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
    later = datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
    return (later - earlier).total_seconds()


def _reserved_bytes(lease: ResourceLease) -> int:
    """Reserva registrada en la lease, como la cuenta la admisión; una lease previa a la fase 81 usa su tope."""
    return lease.memory_limit_bytes if lease.memory_request_bytes is None else lease.memory_request_bytes


def _rank_eviction_candidates(
    candidates: list[ResourceLease], usage: Mapping[str, int]
) -> list[ResourceLease]:
    """Ordena por: excede su reserva primero, luego mayor exceso, luego más reciente, luego id.

    Python ordena en forma estable: aplicar los criterios de menos a más significativo produce el
    orden multicriterio final sin necesitar una única clave compuesta con signos mixtos.
    """
    ranked = sorted(candidates, key=lambda lease: lease.id)
    ranked.sort(key=lambda lease: lease.acquired_at, reverse=True)
    ranked.sort(key=lambda lease: usage.get(lease.id, 0) - _reserved_bytes(lease), reverse=True)
    ranked.sort(key=lambda lease: usage.get(lease.id, 0) > _reserved_bytes(lease), reverse=True)
    return ranked
