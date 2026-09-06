"""Smoke explícito del contrato ProductOwner Codex, contenido y gobernado por política.

No se ejecuta desde un GET ni al iniciar AIDO. Sólo se registra una aprobación a partir de
un proceso propio terminado, sin timeout/cancelación, con salida JSON reconocida y sin tools.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from local_control_center.process_supervision.service import command_fingerprint
from local_control_center.runtime_integrations.config import resolve_executable
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.diagnostics import diagnostic_event
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

from .cli_runtimes.base import RuntimeRequest
from .cli_runtimes.codex_cli import CodexCliRuntime
from .codex_compatibility import CodexCompatibilityService, binary_fingerprint, contract_fingerprint
from .model_aliases import resolve_model_alias
from .runtime_registry import (
    PRODUCT_OWNER_CODEX_EXTRA_ARGS,
    isolated_product_owner_codex_environment,
    validate_product_owner_runtime_argv,
)

SMOKE_MARKER = "AIDO_READ_ONLY_SMOKE_OK"
SMOKE_PROMPT = f"Respond with exactly {SMOKE_MARKER}. Do not call tools or modify files."


def claim_smoke_approval(connection, audit_id: str, request: RuntimeRequest, command: list[str]) -> bool:
    """Consume a command-bound approval once; never trust permission fields in a CLI request."""
    if (
        connection is None
        or request.runtime != "codex_cli"
        or request.role != "product_owner"
        or request.prompt != SMOKE_PROMPT
        or request.env_policy != {"permissionProfile": "plan", "network": False, "secrets": False}
    ):
        return False
    with immediate_transaction(connection):
        row = connection.execute(
            "SELECT target, payload FROM audit_events WHERE id=? AND action='runtime.codex.smoke_approved'",
            (audit_id,),
        ).fetchone()
        if not row or row["target"] != request.workspace_id:
            return False
        approval = json.loads(row["payload"])
        if (
            not approval.get("reason")
            or approval.get("commandFingerprint") != command_fingerprint(command)
            or approval.get("binaryFingerprint") != binary_fingerprint(Path(command[0]))
            or connection.execute(
                "SELECT 1 FROM audit_events WHERE action='runtime.codex.smoke_claimed' AND target=?",
                (audit_id,),
            ).fetchone()
        ):
            return False
        EventBus(connection).record_audit(
            action="runtime.codex.smoke_claimed",
            actor="codex_smoke_service",
            target=audit_id,
            payload={"workspaceId": request.workspace_id},
        )
    return True


class CodexSmokeRequest(BaseModel):
    """Exige workspace explícito, motivo y consentimiento para una llamada potencialmente facturable."""

    workspace_id: str = Field(alias="workspaceId")
    reason: str = Field(min_length=1, max_length=500)
    approved: bool = False


def smoke_output_is_safe(stdout: str) -> bool:
    """Rechaza eventos desconocidos/tools, errores y salidas sin cierre exitoso reconocido."""
    marker, completed = False, False
    try:
        for line in stdout.splitlines():
            event = json.loads(line)
            event_type = event.get("type")
            if event_type in {"thread.started", "turn.started"}:
                continue
            if event_type == "turn.completed":
                completed = True
                continue
            if event_type not in {"item.started", "item.updated", "item.completed"}:
                return False
            item = event.get("item") or {}
            if item.get("type") not in {"reasoning", "agent_message"}:
                return False
            if item.get("type") == "agent_message" and event_type == "item.completed":
                marker |= str(item.get("text") or "").strip() == SMOKE_MARKER
    except (ValueError, AttributeError, TypeError):
        return False
    return marker and completed


def run_codex_smoke(connection: sqlite3.Connection, body: CodexSmokeRequest) -> dict[str, Any]:
    """Ejecuta el contrato exacto con el modelo resuelto y conserva sólo evidencia operacional."""
    if not body.approved or not body.reason.strip():
        return {"status": "configuration_required", "reason": "Explicit smoke approval and reason required."}
    workspace = connection.execute("SELECT * FROM workspaces WHERE id=?", (body.workspace_id,)).fetchone()
    if not workspace:
        raise ValueError("Workspace not found.")
    repository = RuntimeConfigRepository(connection)
    installation = repository.get_installation("codex_cli")
    provider_enabled = connection.execute(
        "SELECT enabled FROM provider_accounts WHERE provider_id='codex_cli'"
    ).fetchone()
    accounts = repository.list_runtime_accounts()
    policy = repository.runtime_policy_decision(
        provider_id="codex_cli", kind="cli", project_id=workspace["project_id"]
    )
    if (
        not installation.get("enabled")
        or not provider_enabled
        or not provider_enabled[0]
        or not any(account.get("enabled") and account.get("runtimeId") == "codex_cli" for account in accounts)
        or not policy["allowed"]
    ):
        return {
            "status": "configuration_required",
            "reason": "Codex installation, account and project policy must be enabled explicitly.",
        }
    executable = resolve_executable(installation).get("path")
    if not executable:
        return {"status": "configuration_required", "reason": "Codex executable is not configured."}
    compatibility = CodexCompatibilityService(connection)
    probe = compatibility.probe(executable)
    diagnostic_event(
        "runtime.readiness",
        component="codex_smoke",
        runtimeId="codex_cli",
        runtimeVersion=probe.get("version"),
        executableResolved=executable,
        executableSha256=probe.get("binaryFingerprint"),
        outcome=probe["status"],
    )
    if probe["status"] == "incompatible":
        return probe
    cli = CodexCliRuntime(executable=executable, connection=connection)
    if cli.validate_native_auth().status != "authenticated":
        return {"status": "configuration_required", "reason": "Codex native authentication is unavailable."}
    try:
        model = resolve_model_alias(connection, provider_id="codex_cli", alias="fast_analysis")
    except ValueError as error:
        return {"status": "configuration_required", "reason": str(error)}
    request = RuntimeRequest(
        runtime="codex_cli",
        workspaceId=body.workspace_id,
        workspacePath=workspace["path"],
        prompt=SMOKE_PROMPT,
        model=model,
        role="product_owner",
        envPolicy={"permissionProfile": "plan", "network": False, "secrets": False},
        extraArgs=[*PRODUCT_OWNER_CODEX_EXTRA_ARGS, "--json"],
    )
    command = cli.build_command(request)
    validation = validate_product_owner_runtime_argv(
        runtime_id="codex_cli", argv=command, workspace_path=workspace["path"]
    )
    if validation:
        raise ValueError(validation)
    started_at = utc_now()
    approval = EventBus(connection).record_audit(
        action="runtime.codex.smoke_approved",
        actor="codex_smoke_service",
        target=body.workspace_id,
        payload={
            "reason": body.reason,
            "source": "explicit_authenticated_request",
            "commandFingerprint": command_fingerprint(command),
            "binaryFingerprint": probe["binaryFingerprint"],
        },
    )
    with isolated_product_owner_codex_environment() as environment:
        diagnostic_event(
            "runtime.auth.ready",
            component="codex_smoke",
            runtimeId="codex_cli",
            effectiveConfig={
                "model": model,
                "effortRequested": None,
                "sandbox": "read-only",
                "configIsolation": True,
                "rustLog": environment.get("RUST_LOG"),
                "authMethod": "native_session" if not environment.get("OPENAI_API_KEY") else "api_key",
            },
        )
        result = cli.run(
            request, trusted_subprocess_environment=environment, trusted_smoke_audit_id=approval["id"]
        )
    evidence = result.process_evidence
    managed_id = evidence.get("managedProcessId")
    process = connection.execute(
        "SELECT * FROM managed_processes WHERE managed_process_id=?", (managed_id,)
    ).fetchone()
    valid = bool(
        result.status == "completed"
        and process
        and process["command_fingerprint"] == command_fingerprint(command)
        and process["started_at"] >= started_at
        and process["finished_at"]
        and process["released_at"]
        and process["exit_code"] == 0
        and not process["cancelled"]
        and not process["timed_out"]
        and process["termination_reason"] not in {"native_exception_captured", "native_capture_failed"}
        and evidence.get("remainingDescendantCount") == 0
        and not evidence.get("stdoutCaptureTruncated")
        and smoke_output_is_safe(result.stdout)
        and binary_fingerprint(Path(cli._which())) == probe["binaryFingerprint"]
    )
    receipt = {
        "status": "validated" if valid else "failed",
        "version": probe["version"],
        "binaryFingerprint": probe["binaryFingerprint"],
        "contractFingerprint": contract_fingerprint(),
        "managedProcessId": managed_id,
        "approvalAuditId": approval["id"],
        "reason": result.error if not valid else None,
        "model": model,
        "lastCheckedAt": utc_now(),
    }
    diagnostic_event(
        "smoke.contract.result",
        component="codex_smoke",
        runtimeId="codex_cli",
        runtimeVersion=probe["version"],
        managedProcessId=managed_id,
        outcome=receipt["status"],
        errorCode=process["exit_code"] if process else None,
    )
    if process:
        connection.execute(
            """INSERT INTO codex_smoke_receipts (managed_process_id, binary_fingerprint, contract_fingerprint, receipt_json, checked_at)
            VALUES (?, ?, ?, ?, ?)""",
            (managed_id, probe["binaryFingerprint"], contract_fingerprint(), json_dumps(receipt), utc_now()),
        )
    EventBus(connection).record_audit(
        action="runtime.codex.smoke",
        actor="local_operator",
        target="codex_cli",
        payload={**receipt, "reason": body.reason},
    )
    return {**receipt, "processEvidence": evidence}
