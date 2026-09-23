"""Bounded, executor-only model probes with durable validation and real admission.

Callers supply candidates already intersected with the role and executor contracts.
No endpoint, credential, permission or operator selection is repaired implicitly.

@author Rodrigo Mason
"""

from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_control_center.host_resources.branch_admission import BranchAdmission, BranchAdmissionDeferred
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.process_supervision.context import CURRENT_EXECUTION, execution_scope
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

from .model_execution_health import (
    model_validation_rejection,
    provider_authentication_failure,
    provider_configuration_fingerprint,
)
from .model_gateway import ModelGateway
from .pricing_catalog import PricingCatalog
from .provider_accounts import ProviderAccountStore
from .providers.factory import provider_account_policy_kind
from .quota_manager import QuotaAdmissionDenied, QuotaManager, QuotaRequest
from .runtime_readiness import healthy_evidence, provider_workload_class

MAX_ATTEMPTS = 4
TARGET_VALIDATED = 2
MAX_PREFLIGHT_COST_USD = 0.01
SCHEDULING_DEADLINE_SECONDS = 120
FAILURE_COOLDOWN_SECONDS = 300
INPUT_TOKEN_RESERVATION = 32
OUTPUT_TOKEN_LIMIT = 16


def _in_quality_environment() -> bool:
    """Explicit test seam: QA must replace this only together with a fake transport."""
    return any(
        os.environ.get(key)
        for key in (
            "AIDO_QUALITY_INVOCATION_ID",
            "AIDO_QUALITY_DB_PATH",
            "PYTEST_CURRENT_TEST",
        )
    )


def _round_robin(models: list[dict[str, Any]]):
    groups: dict[str, deque] = defaultdict(deque)
    seen = set()
    for model in models:
        identity = (str(model.get("providerId") or ""), str(model.get("model") or ""))
        if all(identity) and identity not in seen:
            groups[identity[0]].append(model)
            seen.add(identity)
    while groups:
        for provider in list(groups):
            yield groups[provider].popleft()
            if not groups[provider]:
                del groups[provider]


def _recent_failure(connection, provider: str, model: str, fingerprint: str):
    if provider in QuotaManager(connection).providers_in_cooldown():
        return "provider_quota_cooldown", None
    # Authentication failures exclude the account across roles, threads and models.
    status = provider_authentication_failure(connection, provider, fingerprint)
    if status:
        return "provider_authentication_cooldown", status
    rows = connection.execute(
        """SELECT model, success, http_status, started_at FROM model_execution_health
           WHERE provider_id=? AND configuration_fingerprint=?
           AND model=? ORDER BY started_at DESC,id DESC""",
        (provider, fingerprint, model),
    ).fetchall()
    now = datetime.now(UTC)
    for row in rows:
        try:
            age = (now - datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))).total_seconds()
        except (ValueError, TypeError):
            continue
        if not 0 <= age < FAILURE_COOLDOWN_SECONDS:
            continue
        if row["model"] == model:
            return (None, None) if row["success"] else ("model_validation_cooldown", row["http_status"])
    return None, None


@contextmanager
def _candidate_lock(connection, provider: str, fingerprint: str):
    """Native account lock also deduplicates model probes across worker processes."""
    database = str(connection.execute("PRAGMA database_list").fetchone()[2] or "")
    if not database:
        yield False
        return
    folder = Path(database).parent / ".runtime-preflight-locks"
    folder.mkdir(exist_ok=True)
    identity = hashlib.sha256(f"{Path(database).resolve()}:{provider}:{fingerprint}".encode()).hexdigest()
    with (folder / f"{identity}.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
        else:
            import fcntl
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _policy_reason(connection, account, model, request):
    provider = str(account["providerId"])
    if not account.get("enabled"):
        return "provider_disabled"
    if request.allowed_provider_ids is not None and provider not in request.allowed_provider_ids:
        return "provider_not_allowed_for_agent"
    is_cli = str(account.get("providerType")) == "cli"
    if is_cli and not request.allow_cli:
        return "role_blocks_cli"
    local = str(account.get("providerType")) == "local" or model.get("locality") == "local"
    if not is_cli and local and not request.allow_local:
        return "role_blocks_local"
    if (is_cli or not local) and (
        not request.allow_remote or request.privacy_level in {"local_only", "local_private"}
    ):
        return "privacy_blocks_remote"
    if not is_cli and not request.allow_api:
        return "role_blocks_api"
    policy = RuntimeConfigRepository(connection).runtime_policy_decision(
        provider_id=provider,
        kind=provider_account_policy_kind(account),
        project_id=request.project_id,
        provider_family=account.get("providerFamily"),
        account=account,
    )
    return None if policy["allowed"] else "runtime_policy_denied"


def _unknown_api_cost_reason(account, request):
    from .ai_resource_manager import _catalog_provider_locality, _normalized_policy

    # A loopback gateway may bill a remote model. Only the configured local
    # Ollama executor is exempt from remote-price authorization, never from quotas.
    local_runtime = (
        account.get("providerType") == "local"
        and account.get("apiFormat") == "ollama"
        and _catalog_provider_locality(account) == "local"
    )
    if local_runtime:
        return None
    if _normalized_policy(request.routing_policy) == "economy":
        return "unknown_remote_cost_rejected_by_policy"
    if not request.allow_unknown_cost or request.require_approval_for_unknown_cost:
        return "preflight_unknown_cost_requires_approval"
    return None


def _execute_probe(connection, *, account, model, request, estimate, budget):
    """Reserve before invocation and settle/release both authorities on every exit."""
    provider = str(account["providerId"])
    model_id = str(model["model"])
    gateway = ModelGateway(connection)
    configuration = gateway._provider_configuration(
        provider, runtime_type=None, project_id=request.project_id
    )
    if configuration["status"] != "configured":
        return {"status": "deferred", "reason": "provider_configuration_required", "attempted": False}
    database = Path(connection.execute("PRAGMA database_list").fetchone()[2])
    branch = f"runtime-preflight-{uuid.uuid4()}"
    admission = BranchAdmission(
        database,
        snapshot_source=lambda: HostResourceProbe(relevant_paths=[database]).sample(cpu_interval_seconds=0),
    )
    quota = QuotaManager(connection)
    try:
        with admission.reserve(branch, provider_workload_class(account), timeout_seconds=0) as resource_lease:
            lease = None
            settled = False
            try:
                lease = quota.acquire(
                    QuotaRequest(
                        provider_id=provider,
                        model=model_id,
                        reserved_tokens=INPUT_TOKEN_RESERVATION + OUTPUT_TOKEN_LIMIT,
                        estimated_cost_usd=estimate,
                        execution_id=request.workflow_run_id,
                        branch_id=branch,
                    )
                )
                plan = gateway.plan_model_call(
                    project_id=request.project_id,
                    provider=provider,
                    model=model_id,
                    runtime_type=configuration.get("runtimeType"),
                    messages=[{"role": "user", "content": "Reply OK."}],
                    max_tokens=OUTPUT_TOKEN_LIMIT,
                    temperature=None,
                    estimated_cost_usd=estimate,
                    budget_remaining_usd=budget,
                    metadata={"purpose": "runtime_preflight"},
                )
                plan.update(
                    {
                        "agentId": request.agent_id,
                        "workflowRunId": request.workflow_run_id,
                        "taskId": request.task_id,
                        "workflowStepId": request.workflow_step_id,
                    }
                )
                quota.mark_dispatched(lease.id)
                with execution_scope(
                    replace(CURRENT_EXECUTION.get(), connection=connection, project_id=request.project_id)
                ):
                    result = gateway.execute_model_call(plan)
                if result["status"] == "completed":
                    usage = result.get("usage") or {}
                    quota.commit(
                        lease.id,
                        actual_tokens=usage.get("totalTokens"),
                        actual_cost_usd=usage.get("actualCostUsd"),
                    )
                    settled = True
                return {
                    **result,
                    "attempted": True,
                    "resourceLeaseId": resource_lease.id,
                    "quotaLeaseId": lease.id,
                }
            finally:
                if lease is not None and not settled:
                    quota.release(lease.id, reason="runtime_preflight_not_completed")
    except QuotaAdmissionDenied as error:
        return {"status": "deferred", "reason": error.reason, "attempted": False}
    except BranchAdmissionDeferred as error:
        return {"status": "deferred", "reason": str(error), "attempted": False}


def prevalidate_candidates(
    connection: sqlite3.Connection, *, models, runtime_statuses, request
) -> dict[str, Any]:
    """Return bounded evidence; never turn one rejected candidate into a global failure."""
    evidence: dict[str, Any] = {
        "validated": [],
        "rejected": [],
        "deferred": [],
        "excludedProviderIds": [],
        "attempts": 0,
        "cliAttempts": 0,
        "budgetSpentUsd": 0.0,
        "knownBudgetSpentUsd": 0.0,
        "budgetCostUnknown": False,
        "deferredCount": 0,
        "deferredReasonCounts": {},
    }
    budgets = [
        MAX_PREFLIGHT_COST_USD,
        *(
            max(float(value), 0.0)
            for value in (request.budget_remaining_usd, request.require_approval_over_usd)
            if value is not None
        ),
    ]
    budget = min(budgets)
    deadline = time.monotonic() + SCHEDULING_DEADLINE_SECONDS
    accounts = ProviderAccountStore(connection)
    attempted_cli_accounts: set[str] = set()

    def defer(item, reason):
        evidence["deferredCount"] += 1
        counts = evidence["deferredReasonCounts"]
        counts[reason] = counts.get(reason, 0) + 1
        if len(evidence["deferred"]) < 32:
            evidence["deferred"].append({**item, "reason": reason})

    for model in _round_robin(models):
        provider, model_id = str(model["providerId"]), str(model["model"])
        item = {"providerId": provider, "model": model_id}
        executing = False
        estimate = 0.0
        is_cli = False
        try:
            account = accounts.get_provider_account(provider)
            is_cli = (runtime_statuses.get(provider) or {}).get("kind") == "cli" or account.get(
                "providerType"
            ) == "cli"
            reason = _policy_reason(connection, account, model, request)
            validation = model_validation_rejection(connection, provider, model_id)
            if reason or validation == "model_disabled":
                evidence["rejected"].append({**item, "reason": reason or validation})
                continue
            fingerprint = provider_configuration_fingerprint(connection, provider)
            item["configurationFingerprint"] = fingerprint
            reason, status = _recent_failure(connection, provider, model_id, fingerprint)
            if reason:
                evidence["rejected"].append({**item, "reason": reason, "httpStatus": status})
                if status in {401, 403} and provider not in evidence["excludedProviderIds"]:
                    evidence["excludedProviderIds"].append(provider)
                continue
            if validation is None and healthy_evidence(runtime_statuses.get(provider) or {}):
                evidence["validated"].append({**item, "cached": True})
            elif _in_quality_environment():
                defer(item, "quality_environment_no_inference")
            elif connection.in_transaction:
                defer(item, "preflight_requires_transaction_boundary")
            elif account.get("providerType") == "manual":
                defer(item, "manual_runtime_requires_operator")
            elif evidence["attempts"] >= MAX_ATTEMPTS or time.monotonic() >= deadline:
                defer(item, "preflight_attempt_or_time_budget")
            else:
                remaining_budget = budget - evidence["knownBudgetSpentUsd"]
                if remaining_budget <= 0:
                    defer(item, "preflight_cost_budget")
                    continue
                if is_cli:
                    if provider in attempted_cli_accounts or len(attempted_cli_accounts) >= 2:
                        defer(item, "preflight_cli_attempt_budget")
                        continue
                    if not request.allow_unknown_cost or request.require_approval_for_unknown_cost:
                        defer(item, "preflight_unknown_cost_requires_approval")
                        continue
                    estimate = None
                else:
                    pricing = PricingCatalog(connection).estimate(
                        provider_id=provider,
                        model=model_id,
                        input_tokens=INPUT_TOKEN_RESERVATION,
                        output_tokens=OUTPUT_TOKEN_LIMIT,
                    )
                    estimate = pricing["estimatedCostUsd"]
                    # Partial prices must not turn an unknown output rate into zero.
                    prices = connection.execute(
                        "SELECT input_price_per_mtok,output_price_per_mtok FROM model_catalog WHERE provider_id=? AND model=?",
                        (provider, model_id),
                    ).fetchone()
                    if estimate is None or (
                        not pricing["freeTier"] and (prices is None or any(value is None for value in prices))
                    ):
                        estimate = None
                        reason = _unknown_api_cost_reason(account, request)
                        if reason:
                            defer(item, reason)
                            continue
                    elif float(estimate) > remaining_budget:
                        defer(item, "preflight_cost_budget")
                        continue
                with _candidate_lock(connection, provider, fingerprint) as acquired:
                    if not acquired:
                        defer(item, "preflight_in_progress")
                        continue
                    account = accounts.get_provider_account(provider)
                    reason = _policy_reason(connection, account, model, request)
                    validation = model_validation_rejection(connection, provider, model_id)
                    if reason or validation == "model_disabled":
                        evidence["rejected"].append({**item, "reason": reason or validation})
                        continue
                    reason, status = _recent_failure(connection, provider, model_id, fingerprint)
                    if reason:
                        evidence["rejected"].append({**item, "reason": reason, "httpStatus": status})
                        continue
                    if validation is None and healthy_evidence(runtime_statuses.get(provider) or {}):
                        evidence["validated"].append({**item, "cached": True})
                        if len(evidence["validated"]) >= TARGET_VALIDATED:
                            break
                        continue
                    if fingerprint != provider_configuration_fingerprint(connection, provider):
                        defer(item, "model_validation_configuration_changed")
                        continue
                    executing = True
                    if is_cli:
                        from .runtime_preflight_cli import validate_cli_candidate

                        result = validate_cli_candidate(
                            connection,
                            model=model,
                            runtime_status=runtime_statuses.get(provider) or {},
                            # Only known spending is deducted; unknown authorization
                            # still does not establish an actual monetary ceiling.
                            request=replace(request, budget_remaining_usd=remaining_budget),
                        )
                    else:
                        result = _execute_probe(
                            connection,
                            account=account,
                            model=model,
                            request=request,
                            estimate=estimate,
                            budget=remaining_budget,
                        )
                if not result.get("attempted"):
                    executing = False
                    defer(item, result.get("reason") or "preflight_not_executed")
                    continue
                evidence["attempts"] += 1
                executing = False
                actual_cost = (result.get("usage") or {}).get("actualCostUsd")
                if is_cli:
                    evidence["cliAttempts"] += 1
                    attempted_cli_accounts.add(provider)
                    evidence["budgetCostUnknown"] |= actual_cost is None
                elif estimate is None:
                    # Missing or partial catalog prices cannot establish final cost,
                    # including when a gateway reports a partial computed amount.
                    evidence["budgetCostUnknown"] = True
                evidence["knownBudgetSpentUsd"] += max(float(estimate or 0), float(actual_cost or 0))
                rejection = model_validation_rejection(connection, provider, model_id)
                if result["status"] == "completed" and rejection is None:
                    accounts.record_health_check(
                        provider_id=provider,
                        status="healthy",
                        payload={"healthStatus": "healthy", "source": "model_execution", "model": model_id},
                    )
                    evidence["validated"].append(
                        {**item, "cached": False, "modelCallId": (result.get("modelCall") or {}).get("id")}
                    )
                else:
                    status = result.get("httpStatus")
                    evidence["rejected"].append(
                        {
                            **item,
                            "reason": rejection or "model_validation_failed",
                            "httpStatus": status,
                            "failureCause": result.get("failureCause"),
                            "failureEvidence": result.get("failureEvidence"),
                        }
                    )
                    if result.get("failureCause") in {"quota_exhausted", "rate_limited"}:
                        if provider not in evidence["excludedProviderIds"]:
                            evidence["excludedProviderIds"].append(provider)
                        evidence["validated"] = [
                            value for value in evidence["validated"] if value["providerId"] != provider
                        ]
                    if status in {401, 403} and provider not in evidence["excludedProviderIds"]:
                        evidence["excludedProviderIds"].append(provider)
                        accounts.record_health_check(
                            provider_id=provider,
                            status="authentication_required",
                            payload={
                                "healthStatus": "authentication_required",
                                "source": "model_execution",
                                "httpStatus": status,
                                "message": "provider_authentication_failed",
                            },
                        )
            if len(evidence["validated"]) >= TARGET_VALIDATED:
                break
        except (KeyError, ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
            if executing:
                evidence["attempts"] += 1
                if is_cli:
                    evidence["cliAttempts"] += 1
                    attempted_cli_accounts.add(provider)
                    evidence["budgetCostUnknown"] = True
                elif estimate is None:
                    evidence["budgetCostUnknown"] = True
                evidence["knownBudgetSpentUsd"] += float(estimate or 0)
            defer(item, f"preflight_unavailable:{type(error).__name__}")
    evidence["budgetSpentUsd"] = None if evidence["budgetCostUnknown"] else evidence["knownBudgetSpentUsd"]
    return evidence
