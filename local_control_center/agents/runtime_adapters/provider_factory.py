"""Adapter that executes a selected endpoint through the authoritative provider factory.

Resolves the provider account, runtime policy, and credential fail-closed before delegating
chat transport to the provider resolved by `ProviderAdapterFactory`, then records the
redacted reply as evidence.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.error import HTTPError

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.time import utc_now

from ..credentials import CredentialResolver
from ..provider_accounts import ProviderAccountStore
from ..providers.base import ModelRequest
from ..providers.factory import (
    ProviderAccountDisabledError,
    ProviderAdapterFactory,
    UnsupportedProviderCapabilityError,
    provider_account_policy_kind,
    provider_account_requires_credential,
)
from ..quota_manager import QuotaManager
from ..runtime_provider_config import runtime_provider_configuration_for_account
from .common import _ArtifactRecorder, _redact_text, _result
from .models import RuntimeExecutionRequest, RuntimeExecutionResult

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
        try:
            response = provider.chat_completion(
                ModelRequest(
                    model=model,
                    messages=messages,
                    temperature=request.input.get("temperature"),
                    maxTokens=request.input.get("maxTokens"),
                )
            )
        except HTTPError as error:
            # Un 429 sin registrar deja al provider luciendo sano y los agentes lo reeligen en cada
            # intento; el cooldown persistido es lo que lo saca de la seleccion hasta que se repone.
            if error.code == _TOO_MANY_REQUESTS:
                self._record_provider_rate_limit(
                    connection, provider_id=provider_id, model=model, error=error
                )
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"{self.display_name} execution failed: provider_request_failed",
                redacted=True,
            )
        except Exception:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"{self.display_name} execution failed: provider_request_failed",
                redacted=True,
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
        return _result(
            status="completed",
            started_at=started_at,
            exit_code=0,
            output_artifact_id=output_id,
            evidence_package_id=evidence_id,
            redacted=redacted,
        )
