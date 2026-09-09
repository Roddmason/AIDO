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


def _readiness_resource_request(connection, account) -> ResourceAdmissionRequest:
    """Preview the entire verified session, not a second reservation for its own child."""
    context = CURRENT_EXECUTION.get()
    workload = provider_workload_class(account)
    execution_id = context.execution_id if context and context.execution_id else "readiness-preview"
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
    """Clasifica inferencia conservadoramente desde la configuración, no desde el nombre comercial."""
    if account.get("providerType") == "cli":
        return "agent_cli"
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
            status.update(reason=", ".join(dict.fromkeys(reasons)), blockerType="runtime_not_executable")
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
