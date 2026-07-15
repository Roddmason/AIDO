"""Fail-closed execution of explicit one-or-many chat branches.

The service persists only execution identity, endpoint/model identity, quota settlement and
content fingerprints. Request messages, response bodies, raw provider payloads and raw errors
never cross the SQLite boundary.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import urllib.error
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .ai_execution_models import (
    AIExecutionBranchPlan,
    AIExecutionBranchResult,
    AIExecutionPlan,
    AIExecutionResult,
)
from .credentials import CredentialResolver
from .pricing_catalog import PricingCatalog
from .provider_accounts import ProviderAccountStore, provider_account_is_declared_free
from .providers.base import ModelRequest, ModelResponse
from .providers.factory import (
    ProviderAdapterFactory,
    provider_account_policy_kind,
    provider_account_requires_credential,
)
from .quota_manager import QuotaAdmissionDenied, QuotaLease, QuotaManager, QuotaRequest
from .usage_ledger import UsageLedger

ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
MAX_PERSISTED_CONTENT_FINGERPRINT_BYTES = 1_048_576


class AIExecutionProjectNotFoundError(KeyError):
    """Raised before persistence when an execution references an unknown project."""


class _BranchBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _BranchSettlementFailed(RuntimeError):
    """Stable error used after provider success when local atomic settlement rolls back."""

    code = "execution_settlement_failed"


@dataclass
class _PreparedBranch:
    id: str
    ordinal: int
    plan: AIExecutionBranchPlan
    adapter: Any
    reserved_tokens: int
    estimated_cost_usd: float | None
    pricing_source: str
    lease: QuotaLease | None = None


def _provider_usage_reported(response: ModelResponse) -> bool:
    raw_usage = response.usage.raw_usage or {}
    return any(
        raw_usage.get(key) is not None
        for key in (
            "prompt_tokens",
            "input_tokens",
            "completion_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_eval_count",
            "eval_count",
        )
    )


def _public_error_code(error: BaseException) -> str:
    for attribute in ("public_code", "code"):
        candidate = str(getattr(error, attribute, "") or "").strip().lower()
        if ERROR_CODE_RE.fullmatch(candidate):
            return candidate
    return "provider_request_failed"


def _input_reservation(plan: AIExecutionPlan) -> int:
    """Return a tokenizer-independent upper bound based on UTF-8 bytes plus role framing."""
    return sum(
        len(message.role.encode("utf-8")) + len(message.content.encode("utf-8")) + 8
        for message in plan.messages
    )


class AIExecutionService:
    """Validate, admit, dispatch and reconcile explicit synchronous chat branches."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        provider_resolver: Callable[[str], Any] | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)
        self.runtime_policy = RuntimeConfigRepository(connection)
        self.pricing = PricingCatalog(connection)
        self.quota = QuotaManager(connection)
        self.usage = UsageLedger(connection)
        self.provider_resolver = provider_resolver or (
            lambda provider_id: ProviderAdapterFactory(connection).resolve_for_execution(provider_id)
        )
        self.credential_resolver = credential_resolver or CredentialResolver()

    def execute(self, plan: AIExecutionPlan) -> AIExecutionResult:
        """Execute a typed plan without persisting its messages or raw provider material."""
        self._require_project(plan.project_id)
        execution_id = f"ai-execution-{uuid.uuid4()}"
        created_at = utc_now()
        branch_ids = [f"ai-execution-branch-{uuid.uuid4()}" for _ in plan.branches]
        self._persist_identity(
            execution_id=execution_id,
            plan=plan,
            branch_ids=branch_ids,
            created_at=created_at,
        )

        input_reservation = _input_reservation(plan)
        prepared: list[_PreparedBranch] = []
        results: dict[str, AIExecutionBranchResult] = {}
        for ordinal, (branch_id, branch_plan) in enumerate(zip(branch_ids, plan.branches, strict=True)):
            try:
                branch = self._prepare_branch(
                    branch_id=branch_id,
                    ordinal=ordinal,
                    plan=plan,
                    branch_plan=branch_plan,
                    input_reservation=input_reservation,
                )
                with immediate_transaction(self.connection):
                    branch.lease = self.quota.acquire(
                        QuotaRequest(
                            provider_id=branch_plan.provider_id,
                            model=branch_plan.model,
                            reserved_tokens=branch.reserved_tokens,
                            estimated_cost_usd=branch.estimated_cost_usd,
                            execution_id=execution_id,
                            branch_id=branch_id,
                        )
                    )
                    self._update_branch(
                        branch_id,
                        status="admitted",
                        lease_id=branch.lease.id,
                        reserved_tokens=branch.reserved_tokens,
                        estimated_cost_usd=branch.estimated_cost_usd,
                        pricing_source=branch.pricing_source,
                    )
                prepared.append(branch)
            except QuotaAdmissionDenied as error:
                results[branch_id] = self._blocked_result(
                    branch_id,
                    branch_plan,
                    code=error.reason,
                    reserved_tokens=input_reservation + branch_plan.max_tokens,
                )
                self._update_branch(branch_id, status="blocked", error_code=error.reason)
            except _BranchBlocked as error:
                results[branch_id] = self._blocked_result(
                    branch_id,
                    branch_plan,
                    code=error.code,
                    reserved_tokens=input_reservation + branch_plan.max_tokens,
                )
                self._update_branch(branch_id, status="blocked", error_code=error.code)

        required = plan.min_successful
        if len(prepared) < required:
            for branch in prepared:
                code = "execution_minimum_not_admitted"
                with immediate_transaction(self.connection):
                    if branch.lease is not None:
                        self.quota.release(branch.lease.id, reason=code)
                    self._update_branch(branch.id, status="blocked", error_code=code)
                results[branch.id] = self._blocked_result(
                    branch.id,
                    branch.plan,
                    code=code,
                    reserved_tokens=branch.reserved_tokens,
                    estimated_cost_usd=branch.estimated_cost_usd,
                    pricing_source=branch.pricing_source,
                    lease_id=branch.lease.id if branch.lease else None,
                )
            return self._finish(
                execution_id=execution_id,
                plan=plan,
                created_at=created_at,
                results=results,
                status="blocked",
            )

        self.connection.execute(
            "UPDATE ai_executions SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
            (utc_now(), utc_now(), execution_id),
        )
        future_branches: dict[Future[tuple[ModelResponse, int]], _PreparedBranch] = {}
        with ThreadPoolExecutor(
            max_workers=min(plan.max_parallelism, len(prepared)),
            thread_name_prefix="aido-ai-branch",
        ) as executor:
            for branch in prepared:
                if branch.lease is None:  # pragma: no cover - protected by admission construction
                    continue
                with immediate_transaction(self.connection):
                    self.quota.mark_dispatched(branch.lease.id)
                    self._update_branch(branch.id, status="running")
                future = executor.submit(self._invoke, branch, plan)
                future_branches[future] = branch
            for future in as_completed(future_branches):
                branch = future_branches[future]
                try:
                    response, latency_ms = future.result()
                    results[branch.id] = self._complete_branch(
                        branch,
                        response=response,
                        latency_ms=latency_ms,
                        execution_id=execution_id,
                    )
                except Exception as error:
                    rate_limited = isinstance(error, urllib.error.HTTPError) and error.code == 429
                    if rate_limited:
                        headers = (
                            {str(key): str(value) for key, value in error.headers.items()}
                            if error.headers is not None
                            else {}
                        )
                        self.quota.record_rate_limit(
                            provider_id=branch.plan.provider_id,
                            model=branch.plan.model,
                            headers=headers,
                            error_class=error.__class__.__name__,
                        )
                    code = "provider_rate_limited" if rate_limited else _public_error_code(error)
                    with immediate_transaction(self.connection):
                        if branch.lease is not None:
                            self.quota.release(branch.lease.id, reason=code)
                        self._update_branch(branch.id, status="failed", error_code=code)
                    results[branch.id] = AIExecutionBranchResult(
                        branch_id=branch.id,
                        provider_id=branch.plan.provider_id,
                        model=branch.plan.model,
                        status="failed",
                        lease_id=branch.lease.id if branch.lease else None,
                        error_code=code,
                        reserved_tokens=branch.reserved_tokens,
                        estimated_cost_usd=branch.estimated_cost_usd,
                        pricing_source=branch.pricing_source,
                        usage_status="unknown",
                        cost_status="unknown",
                    )

        successful = sum(result.status == "completed" for result in results.values())
        succeeded = successful >= required
        if succeeded and successful == len(plan.branches):
            status = "completed"
        elif succeeded:
            status = "partial"
        else:
            status = "failed"
        return self._finish(
            execution_id=execution_id,
            plan=plan,
            created_at=created_at,
            results=results,
            status=status,
        )

    def _require_project(self, project_id: str) -> None:
        if not self.connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise AIExecutionProjectNotFoundError(f"Project not found: {project_id}")

    def _persist_identity(
        self,
        *,
        execution_id: str,
        plan: AIExecutionPlan,
        branch_ids: list[str],
        created_at: str,
    ) -> None:
        with immediate_transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO ai_executions
                    (id, project_id, capability, strategy, status, min_successful,
                     max_parallelism, branch_count, successful_branches, failed_branches,
                     created_at, started_at, completed_at, updated_at)
                VALUES (?, ?, 'chat_completions', ?, 'planned', ?, ?, ?, 0, 0, ?, NULL, NULL, ?)
                """,
                (
                    execution_id,
                    plan.project_id,
                    plan.strategy,
                    plan.min_successful,
                    plan.max_parallelism,
                    len(plan.branches),
                    created_at,
                    created_at,
                ),
            )
            for ordinal, (branch_id, branch) in enumerate(zip(branch_ids, plan.branches, strict=True)):
                self.connection.execute(
                    """
                    INSERT INTO ai_execution_branches
                        (id, execution_id, ordinal, provider_id, model, status, lease_id,
                         reserved_tokens, estimated_cost_usd, pricing_source, input_tokens,
                         output_tokens, total_tokens, actual_cost_usd, usage_status,
                         cost_status, latency_ms, error_code, content_sha256, content_bytes,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'planned', NULL, 0, NULL, 'unknown', NULL,
                            NULL, NULL, NULL, 'unknown', 'unknown', NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        branch_id,
                        execution_id,
                        ordinal,
                        branch.provider_id,
                        branch.model,
                        created_at,
                        created_at,
                    ),
                )

    def _prepare_branch(
        self,
        *,
        branch_id: str,
        ordinal: int,
        plan: AIExecutionPlan,
        branch_plan: AIExecutionBranchPlan,
        input_reservation: int,
    ) -> _PreparedBranch:
        try:
            account = self.accounts.get_provider_account(branch_plan.provider_id)
        except KeyError as error:
            raise _BranchBlocked("provider_account_not_found") from error
        if not account.get("enabled"):
            raise _BranchBlocked("provider_account_disabled")
        if str(account.get("apiFamily") or "") != "chat_completions":
            raise _BranchBlocked("unsupported_api_family")
        if str(account.get("providerType") or "") in {"cli", "manual"}:
            raise _BranchBlocked("unsupported_runtime_type")
        policy = self.runtime_policy.runtime_policy_decision(
            provider_id=branch_plan.provider_id,
            provider_family=str(account.get("providerFamily") or ""),
            kind=provider_account_policy_kind(account),
            project_id=plan.project_id,
            account=account,
        )
        if not policy.get("allowed"):
            raise _BranchBlocked("runtime_policy_denied")
        if provider_account_requires_credential(account):
            credential_ref = str(account.get("credentialRef") or "")
            if not credential_ref:
                raise _BranchBlocked("credential_missing")
            credential = self.credential_resolver.resolve(credential_ref, fetch=True)
            if not credential.configured:
                raise _BranchBlocked("credential_missing")

        manifest = next(
            (
                model
                for model in self.accounts.list_models(branch_plan.provider_id)
                if str(model.get("model") or "") == branch_plan.model
            ),
            None,
        )
        if manifest is None:
            raise _BranchBlocked("model_manifest_required")
        if not manifest.get("enabled"):
            raise _BranchBlocked("model_disabled")
        if str(manifest.get("apiFamily") or "") != "chat_completions":
            raise _BranchBlocked("model_api_family_mismatch")
        max_output_tokens = int(manifest.get("maxOutputTokens") or 0)
        if max_output_tokens and branch_plan.max_tokens > max_output_tokens:
            raise _BranchBlocked("model_max_output_tokens_exceeded")
        reserved_tokens = input_reservation + branch_plan.max_tokens
        context_window = int(manifest.get("contextWindow") or 0)
        if context_window and reserved_tokens > context_window:
            raise _BranchBlocked("model_context_window_exceeded")

        pricing = self.pricing.estimate(
            provider_id=branch_plan.provider_id,
            model=branch_plan.model,
            input_tokens=input_reservation,
            output_tokens=branch_plan.max_tokens,
        )
        try:
            adapter = self.provider_resolver(branch_plan.provider_id)
        except Exception as error:
            raise _BranchBlocked(_public_error_code(error)) from error
        if not callable(getattr(adapter, "chat_completion", None)):
            raise _BranchBlocked("unsupported_api_family")
        return _PreparedBranch(
            id=branch_id,
            ordinal=ordinal,
            plan=branch_plan,
            adapter=adapter,
            reserved_tokens=reserved_tokens,
            estimated_cost_usd=pricing["estimatedCostUsd"],
            pricing_source=str(pricing["source"]),
        )

    @staticmethod
    def _invoke(branch: _PreparedBranch, plan: AIExecutionPlan) -> tuple[ModelResponse, int]:
        started = time.monotonic()
        response = branch.adapter.chat_completion(
            ModelRequest(
                model=branch.plan.model,
                messages=[message.model_dump() for message in plan.messages],
                temperature=plan.temperature,
                maxTokens=branch.plan.max_tokens,
            )
        )
        if not isinstance(response, ModelResponse):
            raise RuntimeError("provider_response_invalid")
        return response, int((time.monotonic() - started) * 1000)

    def _complete_branch(
        self,
        branch: _PreparedBranch,
        *,
        response: ModelResponse,
        latency_ms: int,
        execution_id: str,
    ) -> AIExecutionBranchResult:
        if branch.lease is None:  # pragma: no cover - protected by admission construction
            raise RuntimeError("quota_lease_missing")
        content = str(redact_secrets(response.content or ""))
        content_bytes = content.encode("utf-8")
        if not content or len(content_bytes) > MAX_PERSISTED_CONTENT_FINGERPRINT_BYTES:
            raise RuntimeError("provider_response_invalid")
        usage_known = _provider_usage_reported(response)
        input_tokens = response.usage.input_tokens if usage_known else None
        output_tokens = response.usage.output_tokens if usage_known else None
        total_tokens = response.usage.total_tokens if usage_known else None
        manifest = next(
            model
            for model in self.accounts.list_models(branch.plan.provider_id)
            if str(model.get("model") or "") == branch.plan.model
        )
        if usage_known:
            actual_pricing = self.pricing.estimate(
                provider_id=branch.plan.provider_id,
                model=branch.plan.model,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                reasoning_tokens=int(response.usage.reasoning_tokens or 0),
            )
            actual_cost_usd = actual_pricing["estimatedCostUsd"]
            cost_status = (
                "free"
                if actual_pricing["priceKnown"] and actual_pricing["freeTier"]
                else "actual"
                if actual_pricing["priceKnown"]
                else "unknown"
            )
        else:
            account = self.accounts.get_provider_account(branch.plan.provider_id)
        if (
            not usage_known
            and manifest.get("freeTier")
            and (
                provider_account_is_declared_free(account)
                or str(account.get("providerType") or "") in {"local", "manual"}
            )
        ):
            actual_cost_usd = 0.0
            cost_status = "free"
        elif not usage_known:
            actual_cost_usd = None
            cost_status = "unknown"
        try:
            with immediate_transaction(self.connection):
                self.quota.commit(
                    branch.lease.id,
                    actual_tokens=int(total_tokens) if total_tokens is not None else None,
                    actual_cost_usd=float(actual_cost_usd) if actual_cost_usd is not None else None,
                )
                self.usage.record_usage(
                    provider_id=branch.plan.provider_id,
                    model=branch.plan.model,
                    runtime_type="api",
                    request_id=execution_id,
                    session_id=branch.id,
                    input_tokens=int(input_tokens) if input_tokens is not None else None,
                    output_tokens=int(output_tokens) if output_tokens is not None else None,
                    total_tokens=int(total_tokens) if total_tokens is not None else None,
                    estimated_cost_usd=branch.estimated_cost_usd,
                    actual_cost_usd=float(actual_cost_usd) if actual_cost_usd is not None else None,
                    latency_ms=latency_ms,
                    raw_usage={
                        "usage_source": "actual" if usage_known else "unknown",
                        "execution_id": execution_id,
                        "branch_id": branch.id,
                        "pricing_source": branch.pricing_source,
                    },
                    usage_source="actual" if usage_known else "unknown",
                )
                self._update_branch(
                    branch.id,
                    status="completed",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    actual_cost_usd=actual_cost_usd,
                    usage_status="actual" if usage_known else "unknown",
                    cost_status=cost_status,
                    latency_ms=latency_ms,
                    content_sha256=hashlib.sha256(content_bytes).hexdigest(),
                    content_bytes=len(content_bytes),
                )
        except Exception as error:
            raise _BranchSettlementFailed from error
        return AIExecutionBranchResult(
            branch_id=branch.id,
            provider_id=branch.plan.provider_id,
            model=branch.plan.model,
            status="completed",
            lease_id=branch.lease.id,
            content=content,
            reserved_tokens=branch.reserved_tokens,
            estimated_cost_usd=branch.estimated_cost_usd,
            pricing_source=branch.pricing_source,
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            usage_status="actual" if usage_known else "unknown",
            actual_cost_usd=float(actual_cost_usd) if actual_cost_usd is not None else None,
            cost_status=cost_status,
            latency_ms=latency_ms,
        )

    def _update_branch(self, branch_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "lease_id",
            "reserved_tokens",
            "estimated_cost_usd",
            "pricing_source",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "actual_cost_usd",
            "usage_status",
            "cost_status",
            "latency_ms",
            "error_code",
            "content_sha256",
            "content_bytes",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported branch fields: {sorted(invalid)}")
        assignments = [f"{field} = ?" for field in fields]
        assignments.append("updated_at = ?")
        self.connection.execute(
            f"UPDATE ai_execution_branches SET {', '.join(assignments)} WHERE id = ?",
            (*fields.values(), utc_now(), branch_id),
        )

    @staticmethod
    def _blocked_result(
        branch_id: str,
        plan: AIExecutionBranchPlan,
        *,
        code: str,
        reserved_tokens: int,
        estimated_cost_usd: float | None = None,
        pricing_source: str = "unknown",
        lease_id: str | None = None,
    ) -> AIExecutionBranchResult:
        return AIExecutionBranchResult(
            branch_id=branch_id,
            provider_id=plan.provider_id,
            model=plan.model,
            status="blocked",
            lease_id=lease_id,
            error_code=code,
            reserved_tokens=reserved_tokens,
            estimated_cost_usd=estimated_cost_usd,
            pricing_source=pricing_source,
            usage_status="unknown",
            cost_status="unknown",
        )

    def _finish(
        self,
        *,
        execution_id: str,
        plan: AIExecutionPlan,
        created_at: str,
        results: dict[str, AIExecutionBranchResult],
        status: str,
    ) -> AIExecutionResult:
        completed_at = utc_now()
        ordered_results = sorted(
            results.values(),
            key=lambda result: next(
                index
                for index, branch in enumerate(plan.branches)
                if branch.provider_id == result.provider_id and branch.model == result.model
            ),
        )
        successful = sum(result.status == "completed" for result in ordered_results)
        failed = len(ordered_results) - successful
        self.connection.execute(
            """
            UPDATE ai_executions
            SET status = ?, successful_branches = ?, failed_branches = ?,
                completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, successful, failed, completed_at, completed_at, execution_id),
        )
        return AIExecutionResult(
            execution_id=execution_id,
            project_id=plan.project_id,
            strategy=plan.strategy,
            status=status,
            succeeded=successful >= plan.min_successful,
            min_successful=plan.min_successful,
            max_parallelism=plan.max_parallelism,
            successful_branches=successful,
            failed_branches=failed,
            branches=ordered_results,
            created_at=created_at,
            completed_at=completed_at,
        )
