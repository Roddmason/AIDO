"""Readiness efectivo: causas independientes de configuración, policy, salud y host.

Una consulta no admite trabajos ni sondea procesos. Consume muestras durables con frescura
acotada; la admisión atómica del scheduler sigue siendo la autoridad al ejecutar.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import psutil

from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, WorkloadClass
from local_control_center.host_resources.profiles import CAPTURE_SESSION_PARTS, workload_profile
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.process_supervision.context import CURRENT_EXECUTION
from local_control_center.shared.time import utc_now

HEALTH_EVIDENCE_TTL_SECONDS = 300

CREDENTIAL_PROVIDER_KINDS = frozenset({"cli", "api", "gateway"})
"""Tipos de proveedor cuyo bloqueo se corrige con credenciales y no con otra cosa."""

HOST_CAPACITY_BLOCKERS = frozenset(
    {
        # Motivos del gobernador: el host esta ocupado, el runtime no tiene nada malo.
        "aggregate_cpu_budget",
        "aggregate_memory_budget",
        "browser_build_conflict",
        "hard_memory_floor",
        "heavy_workload_capacity",
        "host_cpu_saturated",
        "light_workload_capacity",
        "minimum_free_disk",
        "minimum_free_memory",
        "unreal_cook_active",
        "unreal_cook_exclusive",
        "unreal_local_gpu_conflict",
        # Motivos de este modulo cuando la muestra de recursos no sirve para decidir.
        "resource_session_unverified",
        "resource_snapshot_required",
        "resource_snapshot_stale",
    }
)
"""Bloqueos transitorios de capacidad del host, no fallas del runtime.

Se separan porque son la unica causa que el operador NO puede corregir desde el panel del
proveedor: reingresar una API key no baja la CPU. Presentarlos como "esta IA necesita atencion"
mandaba a reconfigurar algo que ya estaba bien. Siguen publicados en `reason` y
`blockingReasons`; lo que no hacen es levantar una alerta accionable falsa.
"""


def _readiness_resource_request(connection, account) -> ResourceAdmissionRequest:
    """Preview the entire verified session, not a second reservation for its own child."""
    context = CURRENT_EXECUTION.get()
    workload = provider_workload_class(account)
    execution_id = "readiness-preview"
    if context:
        from local_control_center.process_supervision.session_client import session_identity

        database = connection.execute("PRAGMA database_list").fetchone()[2]
        if database and Path(database).resolve() == Path(context.db_path).resolve():
            identity = session_identity(context.db_path)
            if identity is not None:
                value, lease = identity
                if context.resource_lease_id not in {
                    None,
                    lease.id,
                } or context.aggregate_managed_process_id not in {None, value["aggregateId"]}:
                    raise PermissionError("Readiness context differs from the verified launcher session")
                profile = workload_profile(workload)
                if (
                    not profile.gpu_required
                    and profile.memory_limit_bytes <= CAPTURE_SESSION_PARTS["execution"]["memoryBytes"]
                    and profile.process_limit <= lease.process_limit
                ):
                    # Retain live host CPU, disk and the full 18 GiB + host-reserve check.
                    # Session subbudgets and fencing remain enforced by the supervisor at spawn.
                    workload, execution_id = "capture_session", lease.execution_id
            elif (
                context.in_job_runner
                and context.connection is connection
                and context.execution_id
                and context.worker_id
                and context.resource_lease_id
            ):
                from local_control_center.host_resources.branch_admission import BranchAdmission

                lease = ResourceRepository(connection).active_lease_for_execution(context.execution_id)
                if (
                    lease is not None
                    and lease.id == context.resource_lease_id
                    and lease.owner_id == context.worker_id
                    and BranchAdmission._parent_covers(lease, workload)
                ):
                    # Preview the full reservation already held by this verified job.
                    # Transport admission still serializes any child using that budget.
                    workload, execution_id = lease.workload_class, lease.execution_id
    return ResourceAdmissionRequest(
        execution_id=execution_id, owner_id="readiness-preview", workload_class=workload
    )


def healthy_evidence(status: dict) -> bool:
    """Una marca healthy vencida o sin fecha no demuestra salud actual del proveedor."""
    if status.get("healthStatus") != "healthy" or not status.get("healthCheckedAt"):
        return False
    try:
        checked = datetime.fromisoformat(str(status["healthCheckedAt"]).replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        return 0 <= (datetime.now(UTC) - checked).total_seconds() < HEALTH_EVIDENCE_TTL_SECONDS
    except ValueError:
        return False


def provider_workload_class(account: dict) -> WorkloadClass:
    """Clasifica desde el contrato persistido; loopback por sí solo no demuestra un proxy."""
    if account.get("providerType") == "cli":
        return "agent_cli"
    from .provider_catalog import provider_catalog_entry

    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    entry = provider_catalog_entry(str(metadata.get("providerCatalogId") or ""))
    if (
        entry is not None
        and entry.id == "omniroute"
        and account.get("providerType") == entry.provider_type == "gateway"
        and account.get("apiFormat") == entry.api_format
        and account.get("providerFamily") == entry.provider_family
        and account.get("deploymentMode") in {"self_hosted_development", "self_hosted_enterprise"}
        and metadata.get("endpointKind") == "remote"
    ):
        # The configured gateway forwards inference off-host; its local HTTP
        # process still consumes the existing 2 GiB API admission budget.
        return "remote_llm_light"
    host = urlparse(str(account.get("baseUrl") or "")).hostname
    if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"} or str(
        account.get("deploymentMode") or ""
    ) in {"local", "self_hosted_local"}:
        return "local_gpu_model"
    return "remote_llm_light"


def apply_effective_readiness(
    connection: sqlite3.Connection, status: dict, account: dict, policy: dict
) -> dict:
    """Anexa causas verificables sin confundir enabled con executable ni sobreescribir razones previas."""
    kind = status["kind"]
    scope = policy.get("policy", {})
    flags = scope.get("global", {})
    project = scope.get("project", {})
    flag = "cliEnabled" if kind == "cli" else "remoteEnabled"
    global_enabled = bool(flags.get(flag))
    if status["id"] == "ollama" or account.get("apiFormat") == "ollama":
        global_enabled = bool(flags.get("ollamaEnabled"))
    if account.get("providerFamily") == "nvidia_nim":
        global_enabled &= bool(flags.get("nvidiaEnabled"))
    project_enabled = bool(project.get(flag))
    if kind == "manual":
        global_enabled = bool(account.get("enabled"))
        project_enabled = True
    reasons = []
    healthy = healthy_evidence(status)
    for condition, reason in (
        (not status["configured"], "configuration_required"),
        (kind == "cli" and not status["installed"], "not_installed"),
        (kind != "manual" and not status["authenticated"], "authentication_required"),
        (not global_enabled, "globally_disabled"),
        (not project_enabled, "project_disabled"),
        (not policy.get("allowed"), "policy_denied"),
        (kind != "manual" and not healthy, "health_check_required"),
        (not status["executable"], str(status.get("reason") or "execution_unavailable")),
    ):
        if condition:
            reasons.append(reason)
    sample = ResourceRepository(connection).latest_sample()
    admissible = kind == "manual"
    resource_reason = "resource_snapshot_required"
    if sample:
        age = (
            datetime.now(UTC) - datetime.fromisoformat(sample.sampled_at.replace("Z", "+00:00"))
        ).total_seconds()
        if 0 <= age <= 30:
            try:
                decision = HostResourceGovernor(connection).preview(
                    _readiness_resource_request(connection, account), snapshot=sample.snapshot
                )
                admissible = decision.status == "admitted"
                resource_reason = decision.reason_code
            except (OSError, ValueError, RuntimeError, psutil.Error):
                resource_reason = "resource_session_unverified"
        else:
            resource_reason = "resource_snapshot_stale"
    if not admissible:
        reasons.append(resource_reason)
    executable = bool(
        status["executable"]
        and status["configured"]
        and status["authenticated"]
        and healthy
        and global_enabled
        and project_enabled
        and policy.get("allowed")
        and admissible
    )
    if not executable:
        if status["executable"]:
            # A later resource/health/policy projection must not retain a successful
            # explanation. Earlier specific blockers (for example auth) keep precedence.
            ordered = list(dict.fromkeys(reasons))
            actionable = [reason for reason in ordered if reason not in HOST_CAPACITY_BLOCKERS]
            if not actionable:
                # Solo falta capacidad del host: es transitorio y se resuelve solo. Reportarlo
                # como runtime roto llenaba la cola de "necesitan atencion" con IAs sanas.
                blocker = None
            elif "authentication_required" in actionable and kind in CREDENTIAL_PROVIDER_KINDS:
                blocker = "runtime_auth_missing"
            else:
                blocker = "runtime_not_executable"
            status.update(reason=", ".join(ordered), blockerType=blocker)
        status.update(canRunPrompt=False, canEditWorkspace=False, productOwnerExecutable=False)
    status.update(
        globallyEnabled=global_enabled,
        projectEnabled=project_enabled,
        policyAllowed=bool(policy.get("allowed")),
        resourceAdmissible=admissible,
        healthy=healthy,
        executable=executable,
        effectiveStatus="executable"
        if executable
        else ("configuration_required" if not status["configured"] else "blocked"),
        blockingReasons=list(dict.fromkeys(reasons)),
        lastCheckedAt=utc_now(),
    )
    return status
