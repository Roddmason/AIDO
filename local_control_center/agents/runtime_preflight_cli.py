"""Fixed prompt-only CLI validation through the existing managed subprocess boundary.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import nullcontext
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING, Any

from local_control_center.process_supervision.context import CURRENT_EXECUTION
from local_control_center.process_supervision.service import command_fingerprint
from local_control_center.runtime_integrations.config import resolve_executable
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from . import codex_compatibility
from .cli_runtimes.base import RuntimeRequest, RuntimeResult
from .cli_sessions import CliSessionStore
from .codex_compatibility import binary_fingerprint
from .codex_smoke import SMOKE_MARKER, SMOKE_PROMPT, claim_smoke_approval
from .model_execution_health import provider_configuration_fingerprint, record_model_execution
from .provider_accounts import ProviderAccountStore
from .quota_manager import QuotaAdmissionDenied, QuotaManager, QuotaRequest
from .runtime_failure_classifier import classify_runtime_failure
from .runtime_registry import (
    PRODUCT_OWNER_CODEX_EXTRA_ARGS,
    RuntimeCommandUnavailableError,
    build_product_owner_agent_argv,
    build_runtime_command,
    isolated_product_owner_codex_environment,
    parse_runtime_usage,
    validate_product_owner_codex_environment,
    validate_product_owner_runtime_argv,
)
from .usage_ledger import row_to_usage

if TYPE_CHECKING:
    from .ai_resource_manager import AIResourceRequest

PREFLIGHT_PROMPT = "Reply with the single word: ok."
PREFLIGHT_TIMEOUT_SECONDS = 30


def _output_is_ok(runtime_id: str, stdout: str, *, expected_answer: str = "ok") -> bool:
    """Do not accept tool events, errors, extra answers or an unfinished JSON turn."""
    if runtime_id == "claude_code_cli":
        return stdout.strip() == "ok"
    if runtime_id != "codex_cli":
        return False
    answers = []
    completed = False
    try:
        for line in stdout.splitlines():
            event = json.loads(line)
            if completed:
                return False
            kind = event.get("type")
            if kind in {"thread.started", "turn.started"}:
                continue
            if kind == "turn.completed":
                completed = True
                continue
            if kind not in {"item.started", "item.updated", "item.completed"}:
                return False
            item = event.get("item") or {}
            if item.get("type") not in {"reasoning", "agent_message"}:
                return False
            if item.get("type") == "agent_message" and kind == "item.completed":
                answers.append(str(item.get("text") or "").strip())
    except (ValueError, TypeError, AttributeError):
        return False
    return completed and answers == [expected_answer]


def _codex_preflight_smoke_request(
    connection: sqlite3.Connection, *, executable: str, workspace: dict, model_id: str
) -> tuple[RuntimeRequest, list[str], dict]:
    """Prepare only the fixed smoke without relaxing the normal ProductOwner builder."""
    compatibility = codex_compatibility.CodexCompatibilityService(connection).status(
        executable, verify_hash=True
    )
    if compatibility["status"] != "validation_required" or compatibility.get("blockingReasons") != [
        "validated_smoke_missing"
    ]:
        raise RuntimeCommandUnavailableError("Only a missing validated smoke can be repaired here.")
    smoke_request = RuntimeRequest(
        runtime="codex_cli",
        workspaceId=workspace["id"],
        workspacePath=workspace["path"],
        prompt=SMOKE_PROMPT,
        model=model_id,
        role="product_owner",
        agentId="product_owner_agent",
        envPolicy={"permissionProfile": "plan", "network": False, "secrets": False},
        extraArgs=[*PRODUCT_OWNER_CODEX_EXTRA_ARGS, "--json"],
    )
    argv = build_runtime_command("codex_cli", smoke_request, connection=connection, executable=executable)
    return smoke_request, argv, compatibility


def _deferred(reason: str) -> dict[str, Any]:
    return {
        "status": "deferred",
        "success": False,
        "reason": reason,
        "httpStatus": None,
        "attempted": False,
        "providerAttempted": False,
        "estimatedCostUsd": None,
        "costStatus": "unknown",
    }


def validate_cli_candidate(
    connection: sqlite3.Connection, *, model: dict, runtime_status: dict, request: AIResourceRequest
) -> dict[str, Any]:
    """Validate one explicit candidate, never a project task or an arbitrary command.

    Caller bounds batch attempts. Unknown CLI cost requires the role's explicit allowance;
    the canonical CLI contract has no token/cost cap, so no USD ceiling is fabricated.
    """
    from .runtime_preflight import _in_quality_environment

    if _in_quality_environment():
        return _deferred("quality_environment_no_inference")
    if connection.in_transaction:
        return _deferred("preflight_requires_transaction_boundary")
    context = CURRENT_EXECUTION.get()
    database = connection.execute("PRAGMA database_list").fetchone()[2]
    if (
        not context
        or not context.execution_id
        or not context.in_job_runner
        or not request.project_id
        or context.project_id != request.project_id
        or not database
        or Path(database).resolve() != Path(context.db_path).resolve()
        or context.connection is not connection
    ):
        return _deferred("preflight_requires_execution_context")
    provider, model_id = str(model.get("providerId") or ""), str(model.get("model") or "")
    if provider not in {"codex_cli", "claude_code_cli"} or runtime_status.get("id") != provider:
        return _deferred("preflight_cli_contract_unavailable")
    if runtime_status.get("productOwnerAgentArgv") is not None:
        return _deferred("preflight_cli_contract_invalid")
    if (
        not request.allow_cli
        or not request.allow_remote
        or request.privacy_level in {"local_only", "local_private"}
    ):
        return _deferred("role_blocks_cli_preflight")
    if request.allowed_provider_ids is not None and provider not in request.allowed_provider_ids:
        return _deferred("provider_not_allowed_for_agent")
    if not request.allow_unknown_cost or request.require_approval_for_unknown_cost:
        return _deferred("preflight_unknown_cost_requires_approval")
    if request.free_tier_only or any(
        value is not None and value <= 0
        for value in (request.budget_remaining_usd, request.require_approval_over_usd)
    ):
        return _deferred("preflight_cli_cost_budget")
    catalog = connection.execute(
        "SELECT enabled FROM model_catalog WHERE provider_id=? AND model=?", (provider, model_id)
    ).fetchone()
    if catalog is None or not catalog["enabled"]:
        return _deferred("model_disabled_or_missing")
    repo = RuntimeConfigRepository(connection)
    account = ProviderAccountStore(connection).get_provider_account(provider)
    installation = repo.get_installation(provider)
    if (
        not account["enabled"]
        or not installation["enabled"]
        or not any(item["enabled"] for item in repo.list_runtime_accounts(provider))
    ):
        return _deferred("runtime_disabled")
    if not connection.execute(
        "SELECT 1 FROM runtime_capabilities WHERE runtime=? AND capability='chat' AND enabled=1", (provider,)
    ).fetchone():
        return _deferred("runtime_chat_capability_required")
    if not repo.runtime_policy_decision(provider_id=provider, kind="cli", project_id=request.project_id)[
        "allowed"
    ]:
        return _deferred("runtime_policy_denied")
    executable = str(runtime_status.get("detectedCommand") or "")
    configured = str(resolve_executable(installation)["path"] or "")
    if not executable or Path(executable).resolve() != Path(which(configured) or configured).resolve():
        return _deferred("runtime_executable_configuration_changed")
    fingerprint = provider_configuration_fingerprint(connection, provider)
    binary_hash = binary_fingerprint(Path(executable))
    attempt_id = f"runtime-preflight-{uuid.uuid4()}"
    workspaces = WorkspacesRepository(connection, root=Path(database).parent)
    workspace = workspaces.allocate_prompt_workspace(
        project_id=request.project_id,
        task_id=attempt_id,
        agent_id="product_owner_agent",
        source_workspace_id=None,
        purpose="runtime_preflight",
        reason="Fixed CLI readiness prompt.",
        workflow_run_id=request.workflow_run_id,
        workflow_step_id=request.workflow_step_id,
    )
    quota = QuotaManager(connection)
    lease = None
    settled = False
    try:
        # Capability refresh cannot run before the same provider quota authorization.
        lease = quota.acquire(
            QuotaRequest(
                provider_id=provider,
                model=model_id,
                reserved_tokens=48,
                estimated_cost_usd=None,
                execution_id=context.execution_id,
                branch_id=attempt_id,
            )
        )
        smoke_request = None
        smoke_compatibility = None
        smoke_approval = None
        if provider == "codex_cli":
            compatibility = codex_compatibility.CodexCompatibilityService(connection)
            status = compatibility.status(executable, verify_hash=True)
            if status.get("blockingReasons") in [["capability_probe_required"], ["executable_changed"]]:
                status = compatibility.probe(executable)
            if status.get("blockingReasons") == ["validated_smoke_missing"]:
                smoke_request, argv, smoke_compatibility = _codex_preflight_smoke_request(
                    connection, executable=executable, workspace=workspace, model_id=model_id
                )
        if smoke_request is None:
            argv = build_product_owner_agent_argv(
                runtime=runtime_status,
                workspace_id=workspace["id"],
                workspace_path=workspace["path"],
                prompt=PREFLIGHT_PROMPT,
                model=model_id,
                agent_id="product_owner_agent",
                connection=connection,
            )
            if provider == "codex_cli":
                argv[-2:-2] = ["--json"]
        error = validate_product_owner_runtime_argv(
            runtime_id=provider, argv=argv, workspace_path=workspace["path"]
        )
        if error:
            return _deferred("preflight_cli_contract_invalid")
        policy = evaluate_action(
            {
                "operation": "product_owner_runtime",
                "agentId": "product_owner_agent",
                "agentRunId": context.execution_id,
                "tool": "shell",
                "runtimeId": provider,
                "permissionProfile": "plan",
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "command": " ".join(argv),
                "commandArgv": argv,
                "execute": True,
                "providerTransportRequired": True,
                "networkRequired": False,
                "secretsRequired": False,
            }
        )
        if policy["decision"] != "allow":
            return _deferred("preflight_security_policy_denied")
        environment_scope = (
            isolated_product_owner_codex_environment() if provider == "codex_cli" else nullcontext(None)
        )
        with environment_scope as environment:
            if provider == "codex_cli" and validate_product_owner_codex_environment(
                environment, workspace_path=workspace["path"]
            ):
                return _deferred("preflight_cli_environment_invalid")
            if smoke_request is not None:
                smoke_approval = EventBus(connection).record_audit(
                    action="runtime.codex.smoke_approved",
                    actor="runtime_preflight",
                    target=workspace["id"],
                    payload={
                        "reason": "Policy-authorized fixed CLI readiness smoke.",
                        "source": "policy_authorized_runtime_preflight",
                        "executionId": context.execution_id,
                        "model": model_id,
                        "commandFingerprint": command_fingerprint(argv),
                        "binaryFingerprint": binary_hash,
                        "contractFingerprint": smoke_compatibility["contractFingerprint"],
                        "configurationFingerprint": fingerprint,
                    },
                )
                claimed = claim_smoke_approval(connection, smoke_approval["id"], smoke_request, argv)
                smoke_policy = evaluate_action(
                    {
                        "operation": "codex_compatibility_smoke",
                        "tool": "shell",
                        "runtimeId": provider,
                        "role": "product_owner",
                        "permissionProfile": "plan",
                        "workspaceId": workspace["id"],
                        "workspacePath": workspace["path"],
                        "path": workspace["path"],
                        "command": " ".join(argv),
                        "commandArgv": argv,
                        "smokeApproved": claimed,
                        "networkRequired": False,
                        "secretsRequired": False,
                    },
                    trusted_smoke_approval=claimed,
                )
                if smoke_policy["decision"] != "allow":
                    return _deferred("preflight_smoke_approval_denied")
            if (
                binary_fingerprint(Path(executable)) != binary_hash
                or provider_configuration_fingerprint(connection, provider) != fingerprint
                or (
                    smoke_compatibility is not None
                    and smoke_compatibility["contractFingerprint"]
                    != codex_compatibility.contract_fingerprint()
                )
            ):
                return _deferred("preflight_cli_configuration_changed")
            started_at = utc_now()
            audit = EventBus(connection).record_audit(
                action="runtime.preflight.started",
                actor="runtime_preflight",
                target=attempt_id,
                payload={
                    "executionId": context.execution_id,
                    "projectId": request.project_id,
                    "requestingAgentId": request.agent_id,
                    "providerId": provider,
                    "model": model_id,
                    "workspaceId": workspace["id"],
                    "commandFingerprint": command_fingerprint(argv),
                    "configurationFingerprint": fingerprint,
                    "timeoutSeconds": PREFLIGHT_TIMEOUT_SECONDS,
                    "costStatus": "unknown",
                    "unknownCostAllowed": True,
                },
            )
            quota.mark_dispatched(lease.id)
            result = RestrictedSubprocessSandbox().execute(
                argv=argv,
                cwd=workspace["path"],
                workspace_path=workspace["path"],
                timeout_seconds=PREFLIGHT_TIMEOUT_SECONDS,
                truncate_output=False,
                environment=environment,
            )
        process = connection.execute(
            "SELECT * FROM managed_processes WHERE managed_process_id=?", (result.get("managedProcessId"),)
        ).fetchone()
        attempted = bool(process and process["root_pid"] and process["started_at"] >= started_at)
        failure = (
            classify_runtime_failure(
                runtime_id=provider,
                return_code=result.get("returnCode"),
                stdout=str(result.get("stdout") or ""),
                stderr=str(result.get("stderr") or ""),
            )
            if attempted
            else None
        )
        success = bool(
            attempted
            and failure is None
            and result.get("returnCode") == 0
            and not result.get("blocked")
            and process["command_fingerprint"] == command_fingerprint(argv)
            and process["execution_id"] == context.execution_id
            and process["resource_lease_id"]
            and process["finished_at"]
            and process["released_at"]
            and process["exit_code"] == 0
            and not process["cancelled"]
            and not process["timed_out"]
            and process["termination_reason"] not in {"native_exception_captured", "native_capture_failed"}
            and result.get("remainingDescendantCount") == 0
            and not result.get("stdoutCaptureTruncated")
            and not result.get("timedOut")
            and not result.get("cancelled")
            and _output_is_ok(
                provider,
                str(result.get("stdout") or ""),
                expected_answer=SMOKE_MARKER if smoke_request is not None else "ok",
            )
            and binary_fingerprint(Path(executable)) == binary_hash
            and provider_configuration_fingerprint(connection, provider) == fingerprint
            and (
                smoke_compatibility is None
                or (
                    smoke_compatibility["binaryFingerprint"] == binary_hash
                    and smoke_compatibility["contractFingerprint"]
                    == codex_compatibility.contract_fingerprint()
                    and not result.get("stderrCaptureTruncated")
                )
            )
        )
        if smoke_compatibility is not None and process is not None:
            receipt = {
                "status": "validated" if success else "failed",
                "version": smoke_compatibility["version"],
                "binaryFingerprint": binary_hash,
                "contractFingerprint": smoke_compatibility["contractFingerprint"],
                "managedProcessId": result.get("managedProcessId"),
                "approvalAuditId": smoke_approval["id"],
                "configurationFingerprint": fingerprint,
                "model": model_id,
                "reason": None if success else "CLI preflight smoke evidence was not valid.",
                "lastCheckedAt": utc_now(),
            }
            connection.execute(
                """INSERT INTO codex_smoke_receipts
                (managed_process_id, binary_fingerprint, contract_fingerprint, receipt_json, checked_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    receipt["managedProcessId"],
                    binary_hash,
                    receipt["contractFingerprint"],
                    json_dumps(receipt),
                    receipt["lastCheckedAt"],
                ),
            )
            EventBus(connection).record_audit(
                action="runtime.codex.smoke", actor="runtime_preflight", target=provider, payload=receipt
            )
        usage = None
        session = None
        if attempted:
            if not success and failure and failure.cause in {"quota_exhausted", "rate_limited"}:
                quota.record_rate_limit(
                    provider_id=provider,
                    model="*",
                    error_class="runtime_usage_limit",
                    status_code=None,
                )
            runtime_result = RuntimeResult(
                runtime=provider,
                status="completed" if success else "failed",
                stdout=str(result.get("stdout") or ""),
                stderr=str(result.get("stderr") or ""),
            )
            tokens = parse_runtime_usage(
                provider, runtime_result, connection=connection, executable=executable
            )
            session = CliSessionStore(connection).record_result(
                runtime=provider,
                executable=executable,
                workspace_id=workspace["id"],
                command=argv,
                workflow_run_id=request.workflow_run_id,
                workflow_step_id=request.workflow_step_id,
                agent_id=request.agent_id,
                model=model_id,
                role="runtime_preflight",
                env_policy={
                    "permissionProfile": "plan",
                    "purpose": "runtime_preflight",
                    "policyResult": policy,
                },
                status=runtime_result.status,
                stdout=runtime_result.stdout,
                stderr=runtime_result.stderr,
                error=None
                if success
                else (
                    f"{failure.cause}: {failure.evidence}"
                    if failure
                    else "CLI preflight did not produce verified ok."
                ),
                usage=tokens,
                process_evidence={
                    key: value for key, value in result.items() if key not in {"stdout", "stderr", "command"}
                },
            )
            usage = row_to_usage(
                connection.execute(
                    "SELECT * FROM usage_ledger WHERE id=?", (session["usage_ledger_id"],)
                ).fetchone()
            )
            record_model_execution(
                connection,
                provider,
                model_id,
                success,
                "test_prompt",
                configuration_fingerprint=fingerprint,
                started_at=started_at,
            )
            if success:
                quota.commit(
                    lease.id, actual_tokens=usage["totalTokens"], actual_cost_usd=usage["actualCostUsd"]
                )
                settled = True
        outcome = {
            "status": "completed" if success else "unavailable" if attempted else "deferred",
            "success": success,
            "reason": None if success else "cli_preflight_unverified",
            "httpStatus": None,
            "failureCause": failure.cause if failure else None,
            "failureEvidence": failure.evidence if failure else None,
            "attempted": attempted,
            "providerAttempted": attempted,
            "estimatedCostUsd": None,
            "costStatus": "unknown",
            "usage": usage,
            "modelCall": {"id": session["id"]} if session else None,
            "managedProcessId": result.get("managedProcessId"),
            "auditId": audit["id"],
        }
        EventBus(connection).record_audit(
            action="runtime.preflight.completed",
            actor="runtime_preflight",
            target=attempt_id,
            payload={key: value for key, value in outcome.items() if key != "usage"},
        )
        return outcome
    except (KeyError, ValueError, OSError, QuotaAdmissionDenied, RuntimeCommandUnavailableError) as error:
        return _deferred(f"cli_preflight_unavailable:{type(error).__name__}")
    finally:
        try:
            if lease is not None and not settled:
                quota.release(lease.id, reason="runtime_preflight_not_completed")
        finally:
            workspaces.archive_workspace(workspace["id"], reason="CLI preflight finished.")
