"""Adapter that executes a selected endpoint through the authoritative provider factory.

Resolves the provider account, runtime policy, and credential fail-closed before delegating
chat transport to the provider resolved by `ProviderAdapterFactory`. Bounds the call by the
execution deadline (plus the server cold start when a local model switch is announced),
classifies local-server failures into stable causes with redacted reasons, records usage and
latency in the usage ledger, persists only the redacted reply as evidence and hands the
unredacted reply to the in-process transient channel keyed by the broker tool-call id.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from local_control_center.process_supervision.context import ExecutionDeadlineExceeded
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.time import utc_now

from ..credentials import CredentialResolver
from ..endpoint_locality import credential_transport_allowed, is_local_model_runtime, is_self_hosted_inference
from ..local_endpoint_lease import max_local_call_seconds
from ..local_runtime_causes import LocalRuntimeCause, LocalRuntimeError
from ..model_wildcards import is_nvidia_nim_auto_selection_sentinel
from ..provider_accounts import ProviderAccountStore
from ..providers.base import ModelRequest
from ..providers.factory import (
    ProviderAccountDisabledError,
    ProviderAdapterFactory,
    UnsupportedProviderCapabilityError,
    provider_account_policy_kind,
    provider_account_requires_credential,
)
from ..providers.http_transport import http_error_excerpt
from ..providers.nvidia_nim import NvidiaNimCapabilityError
from ..quota_manager import QuotaManager
from ..runtime_failure_classifier import classify_local_model_error
from ..runtime_provider_config import runtime_provider_configuration_for_account
from ..usage_ledger import UsageLedger
from .common import _ArtifactRecorder, _bounded_timeout, _redact_text, _result
from .model_call_budget import (
    effective_model_call_timeout,
    local_cold_start_seconds,
    response_format_for,
)
from .models import RuntimeExecutionRequest, RuntimeExecutionResult
from .transient_output import put_transient_output

_TOO_MANY_REQUESTS = 429


class ProviderFactoryAdapter:
    """Execute a selected endpoint through the authoritative model-provider factory."""

    def __init__(
        self,
        *,
        provider_family: str,
        display_name: str,
        connection: sqlite3.Connection | None,
        artifact_root: str | Path | None = None,
    ):
        self.adapter_id = provider_family
        self.provider_family = provider_family
        self.display_name = display_name
        self.recorder = _ArtifactRecorder(
            connection=connection,
            artifact_root=artifact_root,
            adapter_id=provider_family,
        )

    @staticmethod
    def _record_provider_rate_limit(
        connection: sqlite3.Connection, *, provider_id: str, model: str, error: HTTPError
    ) -> None:
        """Registra la evidencia de 429 para sacar al provider de la selección hasta que se reponga.

        Es una señal auxiliar: si el registro falla, el error de transporte original debe seguir
        reportándose igual, así que la excepción se traga a propósito.
        """
        try:
            QuotaManager(connection).record_rate_limit(
                provider_id=provider_id,
                model=model or "*",
                headers=dict(getattr(error, "headers", None) or {}),
                error_class="rate_limited",
            )
        except Exception:
            return

    def _local_failure(
        self,
        *,
        started_at: str,
        cause: LocalRuntimeCause,
        evidence: str,
        http_status: int | None,
        attempted: bool,
    ) -> RuntimeExecutionResult:
        """Build the unavailable result of a classified local-runtime failure with redacted evidence."""
        detail = f" ({evidence})" if evidence else ""
        return _result(
            status="unavailable",
            started_at=started_at,
            reason=f"{self.display_name} execution failed: {cause}{detail}",
            http_status=http_status,
            provider_attempted=attempted,
            failure_cause=cause,
            redacted=True,
        )

    @staticmethod
    def _record_usage(
        connection: sqlite3.Connection,
        *,
        account: dict[str, Any],
        model: str,
        request: RuntimeExecutionRequest,
        response: Any,
        latency_ms: int,
    ) -> str:
        """Record provider-reported tokens (or unknown usage with NULL tokens) and latency in the ledger."""
        usage = getattr(response, "usage", None)
        raw_usage = dict(getattr(usage, "raw_usage", None) or {})
        reported = raw_usage.get("usage_source") == "provider"

        def tokens(field: str) -> int | None:
            return int(getattr(usage, field, 0) or 0) if reported else None

        row = UsageLedger(connection).record_usage(
            provider_id=str(account["providerId"]),
            model=model,
            runtime_type=str(account.get("providerType") or "api"),
            workflow_run_id=request.workflow_run_id,
            workflow_step_id=request.workflow_step_id,
            job_id=request.job_id,
            request_id=request.transient_output_key,
            session_id=request.agent_run_id,
            input_tokens=tokens("input_tokens"),
            cached_input_tokens=tokens("cached_input_tokens"),
            output_tokens=tokens("output_tokens"),
            reasoning_tokens=tokens("reasoning_tokens"),
            tool_tokens=tokens("tool_tokens"),
            total_tokens=tokens("total_tokens"),
            actual_cost_usd=0.0 if is_self_hosted_inference(account) else None,
            latency_ms=latency_ms,
            raw_usage={
                **raw_usage,
                "usage_source": "provider" if reported else "unknown",
                "source": "tool_broker",
            },
            usage_source="actual" if reported else "unknown",
        )
        return str(row["id"])

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Resolve policy/account first, then delegate chat transport to the selected provider."""
        started_at = utc_now()
        connection = self.recorder.connection
        if connection is None:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason=f"SQLite provider configuration is required for {self.display_name} execution.",
            )
        provider_id = str(request.input.get("providerId") or "").strip()
        if not provider_id:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason=f"{self.display_name} execution requires input.providerId.",
            )
        try:
            account = ProviderAccountStore(connection).get_provider_account(provider_id)
        except KeyError:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason=f"Provider account not found: {provider_id}",
            )
        if not account.get("enabled"):
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason=f"Provider account is disabled: {provider_id}",
            )
        if str(account.get("providerFamily") or "") != self.provider_family:
            return _result(
                status="blocked",
                started_at=started_at,
                reason=(
                    f"Provider endpoint {provider_id} is not bound to adapter family {self.provider_family}."
                ),
            )
        policy_decision = RuntimeConfigRepository(connection).runtime_policy_decision(
            provider_id=provider_id,
            provider_family=self.provider_family,
            kind=provider_account_policy_kind(account),
            project_id=request.project_id,
        )
        if not policy_decision.get("allowed"):
            return _result(
                status="blocked",
                started_at=started_at,
                reason=str(
                    policy_decision.get("reason")
                    or f"{self.display_name} execution is disabled by runtime policy."
                ),
            )
        try:
            provider = ProviderAdapterFactory(connection).resolve_for_execution(provider_id)
        except UnsupportedProviderCapabilityError as error:
            return _result(status="blocked", started_at=started_at, reason=error.public_code)
        except ProviderAccountDisabledError as error:
            return _result(status="configuration_required", started_at=started_at, reason=str(error))

        base_url = str(getattr(provider, "base_url", "") or "").strip()
        credential_ref = str(getattr(provider, "credential_ref", "") or "").strip()
        if not base_url:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason=f"{self.display_name} endpoint is missing base URL.",
            )
        credential_required = provider_account_requires_credential(account)
        if credential_required and not credential_ref:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason=f"{self.display_name} endpoint is missing credential ref.",
            )
        if credential_ref:
            credential = CredentialResolver().resolve(credential_ref, fetch=False)
            if credential.status not in {"configured", "unverified"}:
                return _result(
                    status="configuration_required",
                    started_at=started_at,
                    reason=f"Credential ref for {provider_id} is {credential.status}.",
                )
            if str(account.get("providerType") or "") == "local" and not credential_transport_allowed(
                account
            ):
                return _result(
                    status="blocked", started_at=started_at, reason="insecure_credential_transport"
                )
        messages = request.input.get("messages")
        model = str(request.input.get("model") or "").strip()
        if not isinstance(messages, list) or not messages:
            return _result(
                status="blocked",
                started_at=started_at,
                reason=f"{self.display_name} execution requires input.messages.",
            )
        if not model:
            # Solo la cuenta canonica (providerId == providerFamily) hereda el modelo
            # configurado por entorno; los endpoints dedicados siguen fail-closed.
            configuration = runtime_provider_configuration_for_account(account)
            model = str(configuration.value("model") or "").strip() if configuration else ""
        if not model:
            return _result(
                status="blocked",
                started_at=started_at,
                reason=f"{self.display_name} execution requires input.model.",
            )
        if is_nvidia_nim_auto_selection_sentinel(self.provider_family, model):
            return _result(
                status="blocked",
                started_at=started_at,
                reason="nvidia_model_selection_required",
            )
        local_runtime = is_local_model_runtime(account)
        cold_start = local_cold_start_seconds(account, request.input) if local_runtime else 0
        max_call = max_local_call_seconds(connection) if local_runtime else None
        try:
            timeout_seconds = effective_model_call_timeout(
                requested_seconds=_bounded_timeout(request),
                cold_start_seconds=cold_start,
                max_call_seconds=max_call,
            )
        except LocalRuntimeError as error:
            return self._local_failure(
                started_at=started_at, cause=error.cause, evidence="", http_status=None, attempted=False
            )
        call_started = time.monotonic()
        try:
            response = provider.chat_completion(
                ModelRequest(
                    model=model,
                    messages=messages,
                    temperature=request.input.get("temperature"),
                    maxTokens=request.input.get("maxTokens"),
                    responseFormat=response_format_for(request.input),
                    timeoutSeconds=timeout_seconds,
                    deadlineMonotonic=time.monotonic() + timeout_seconds,
                    coldStartExpected=bool(cold_start),
                )
            )
        except NvidiaNimCapabilityError as error:
            status_suffix = f" (http_status={error.status_code})" if error.status_code is not None else ""
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"{self.display_name} execution failed: {error.code}{status_suffix}",
                http_status=error.status_code,
                provider_attempted=error.request_attempted or error.status_code is not None,
                redacted=True,
            )
        except HTTPError as error:
            # Un 429 sin registrar deja al provider luciendo sano y los agentes lo reeligen en cada
            # intento; el cooldown persistido es lo que lo saca de la seleccion hasta que se repone.
            if error.code == _TOO_MANY_REQUESTS:
                # El cooldown se indexa por el providerId canonico de la cuenta: get_provider_account
                # resuelve por `provider_id OR id`, asi que usar el token del caller podria escribir
                # una clave que la lectura de disponibilidad nunca vuelve a encontrar.
                self._record_provider_rate_limit(
                    connection,
                    provider_id=str(account["providerId"]),
                    model=model,
                    error=error,
                )
            if local_runtime:
                failure = classify_local_model_error(
                    error=error, http_status=error.code, body=http_error_excerpt(error)
                )
                if failure is not None:
                    return self._local_failure(
                        started_at=started_at,
                        cause=failure.cause,
                        evidence=failure.evidence,
                        http_status=error.code,
                        attempted=True,
                    )
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"{self.display_name} execution failed: provider_request_failed",
                http_status=error.code,
                provider_attempted=True,
                redacted=True,
            )
        except LocalRuntimeError as error:
            return self._local_failure(
                started_at=started_at, cause=error.cause, evidence="", http_status=None, attempted=False
            )
        except ExecutionDeadlineExceeded:
            # El deadline se agota al tomar el slot local, antes de cualquier request: no es un intento fallido
            # del modelo y ningún failover puede renovarlo, así que sube hasta quien cierra la ejecución.
            raise
        except Exception as error:
            attempted = isinstance(error, (URLError, TimeoutError, ConnectionError))
            if local_runtime:
                failure = classify_local_model_error(error=error, http_status=None)
                if failure is not None:
                    return self._local_failure(
                        started_at=started_at,
                        cause=failure.cause,
                        evidence=failure.evidence,
                        http_status=None,
                        attempted=attempted,
                    )
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"{self.display_name} execution failed: provider_request_failed",
                provider_attempted=attempted,
                redacted=True,
            )
        latency_ms = int((time.monotonic() - call_started) * 1000)
        usage_ledger_id = self._record_usage(
            connection,
            account=account,
            model=model,
            request=request,
            response=response,
            latency_ms=latency_ms,
        )
        if not str(getattr(response, "content", "") or "").strip() or (
            getattr(response, "provider_id", None) != str(account["providerId"])
            or getattr(response, "model", None) != model
        ):
            return _result(
                status="unavailable",
                started_at=started_at,
                reason="model_validation_invalid_response",
                provider_attempted=True,
                redacted=True,
                latency_ms=latency_ms,
                usage_ledger_id=usage_ledger_id,
            )
        clean_content, redacted = _redact_text(response.content)
        output_id = self.recorder.record_output(
            request=request,
            name=f"{provider_id.replace('_', '-')}-output.txt",
            content=clean_content,
        )
        evidence_id = self.recorder.create_evidence(
            request=request,
            status="completed",
            exit_code=0,
            reason=None,
            artifact_ids=[output_id] if output_id else [],
        )
        if request.transient_output_key:
            put_transient_output(request.transient_output_key, str(response.content))
        return _result(
            status="completed",
            started_at=started_at,
            exit_code=0,
            output_artifact_id=output_id,
            evidence_package_id=evidence_id,
            redacted=redacted,
            provider_attempted=True,
            latency_ms=latency_ms,
            usage_ledger_id=usage_ledger_id,
        )
