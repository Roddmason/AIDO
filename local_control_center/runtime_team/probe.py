"""Prueba de ida y vuelta de un runtime a pedido del operador (operación ``models.validate_runtime``).

API, gateway y local reusan la completion fija de test-prompt; los CLI reusan el preflight del loop
con un ``AIResourceRequest`` del operador que autoriza el costo desconocido, porque el clic es la
aprobación: queda auditado como ``runtime.validation.operator_approved`` y consume cuota de
suscripción. Toda falla deja evidencia (salvo una denegación de política del proyecto), así una
prueba fallida invalida también la validación de 24 h; la causa sale del clasificador compartido.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from local_control_center.agents.ai_resource_manager import AIResourceRequest
from local_control_center.agents.model_execution_health import (
    provider_configuration_fingerprint,
    record_model_execution,
)
from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import (
    ProviderAdapterResolutionError,
    provider_account_policy_kind,
)
from local_control_center.agents.runtime_failure_classifier import EVIDENCE_LIMIT, classify_runtime_failure
from local_control_center.agents.runtime_preflight_cli import validate_cli_candidate
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets

OPERATOR_APPROVAL_AUDIT_ACTION = "runtime.validation.operator_approved"
_STABLE_CODE = re.compile(r"[a-z0-9_.:-]+")


def _result(
    provider_id: str,
    kind: str,
    status: str,
    *,
    model: str | None = None,
    latency_ms: int | None = None,
    reason: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "kind": kind,
        "status": status,
        "model": model,
        "latencyMs": latency_ms,
        "reason": str(redact_secrets(reason)) if reason else None,
        "evidence": str(redact_secrets(evidence))[:EVIDENCE_LIMIT] if evidence else None,
        "checkedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _failure_cause(provider_id: str, detail: str, *, fallback: str) -> tuple[str, str]:
    """Causa estable del clasificador compartido (o ``fallback``) y evidencia redactada de la falla."""
    failure = classify_runtime_failure(runtime_id=provider_id, return_code=None, stdout="", stderr=detail)
    if failure is not None and failure.cause != "unknown":
        return failure.cause, failure.evidence
    return fallback, str(redact_secrets(detail))[:EVIDENCE_LIMIT]


class RuntimeValidationService:
    """Ejecuta la prueba real de un runtime y deja la evidencia en ``model_execution_health``."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)

    def validate(
        self, provider_id: str, *, project_id: str | None, actor: str = "operator"
    ) -> dict[str, Any]:
        """Prueba un runtime y devuelve un resultado redactado (validated, failed o deferred).

        Raises:
            KeyError: el runtime no existe.
            ValueError: el runtime es manual y no admite una prueba automática.
        """
        account = self.accounts.get_provider_account(provider_id)
        kind = str(account.get("providerType") or "")
        if kind == "manual":
            raise ValueError(f"Runtime {provider_id} is manual and cannot be probed.")
        if not account.get("enabled"):
            return self._failed(provider_id, kind, reason="provider_disabled")
        if kind == "cli":
            return self._validate_cli(account, project_id=project_id, actor=actor)
        return self._validate_model_provider(account, project_id=project_id)

    def _last_recorded_model(self, provider_id: str) -> str | None:
        row = self.connection.execute(
            """SELECT model FROM model_execution_health WHERE provider_id = ?
               ORDER BY started_at DESC, id DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()
        return str(row["model"]) if row is not None else None

    def _record_failure(
        self,
        provider_id: str,
        model: str | None,
        *,
        fingerprint: str | None = None,
        started_at: str | None = None,
    ) -> None:
        """Deja evidencia fallida salvo que la prueba ya la haya dejado desde ``started_at``.

        La deduplicación se acota al modelo probado: un éxito concurrente de otro modelo no puede
        ocultar la falla. Sin modelo probado usa el último modelo registrado del proveedor; sin
        registro previo no hay validación que invalidar y no se escribe nada.
        """
        if started_at is not None and self._recorded_since(provider_id, model, started_at):
            return
        evidence_model = model or self._last_recorded_model(provider_id)
        if evidence_model is None:
            return
        record_model_execution(
            self.connection,
            provider_id,
            evidence_model,
            False,
            "test_prompt",
            configuration_fingerprint=fingerprint,
            started_at=started_at,
        )

    def _recorded_since(self, provider_id: str, model: str | None, started_at: str) -> bool:
        """Indica si ya hay evidencia del proveedor (y del modelo, si se probó uno) desde ``started_at``."""
        row = self.connection.execute(
            """SELECT 1 FROM model_execution_health
               WHERE provider_id = ? AND started_at >= ? AND (? IS NULL OR model = ?) LIMIT 1""",
            (provider_id, started_at, model, model),
        ).fetchone()
        return row is not None

    def _failed(
        self,
        provider_id: str,
        kind: str,
        *,
        reason: str,
        model: str | None = None,
        latency_ms: int | None = None,
        evidence: str | None = None,
        fingerprint: str | None = None,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        """Registra la falla (invalida la validación vigente) y devuelve el resultado ``failed``."""
        self._record_failure(provider_id, model, fingerprint=fingerprint, started_at=started_at)
        return _result(
            provider_id, kind, "failed", model=model, latency_ms=latency_ms, reason=reason, evidence=evidence
        )

    def _validate_model_provider(self, account: dict[str, Any], *, project_id: str | None) -> dict[str, Any]:
        """Prueba API/gateway/local con el prompt fijo de test-prompt.

        El import es diferido porque ``model_gateway_api`` registra la ruta que usa este servicio.
        """
        from local_control_center.agents.model_gateway_api import (
            _requires_remote_provider_call,
            _run_provider_test_prompt,
            _validate_real_discovery_credentials,
        )

        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        if _requires_remote_provider_call(account):
            decision = RuntimeConfigRepository(self.connection).runtime_policy_decision(
                provider_id=provider_id,
                provider_family=str(account.get("providerFamily") or ""),
                kind=provider_account_policy_kind(account),
                project_id=project_id,
            )
            if not decision.get("allowed"):
                return _result(
                    provider_id,
                    kind,
                    "failed",
                    reason="policy_denied",
                    evidence=str(decision.get("reason") or "Runtime policy denies this provider."),
                )
        try:
            _validate_real_discovery_credentials(account)
        except HTTPException as error:
            cause, evidence = _failure_cause(provider_id, str(error.detail), fallback="credential_invalid")
            return self._failed(provider_id, kind, reason=cause, evidence=evidence)
        model = self.accounts.first_enabled_model(provider_id)
        if model is None:
            return self._failed(provider_id, kind, reason="model_required")
        try:
            provider = provider_instance(provider_id, connection=self.connection)
        except ProviderAdapterResolutionError as error:
            code = str(getattr(error, "public_code", error.code))
            return self._failed(provider_id, kind, model=model, reason=code, evidence=str(error))
        started_at = datetime.now(UTC).isoformat(timespec="microseconds")
        fingerprint = provider_configuration_fingerprint(self.connection, provider_id)
        outcome = _run_provider_test_prompt(provider_id, model, connection=self.connection, provider=provider)
        if outcome["ok"]:
            return _result(provider_id, kind, "validated", model=model, latency_ms=outcome["latencyMs"])
        error_text = str(outcome.get("error") or "")
        fallback = error_text if _STABLE_CODE.fullmatch(error_text) else "runtime_validation_failed"
        cause, evidence = _failure_cause(provider_id, error_text, fallback=fallback)
        return self._failed(
            provider_id,
            kind,
            model=model,
            latency_ms=outcome["latencyMs"],
            reason=cause,
            evidence=evidence,
            fingerprint=fingerprint,
            started_at=started_at,
        )

    def _validate_cli(self, account: dict[str, Any], *, project_id: str | None, actor: str) -> dict[str, Any]:
        """Prueba un CLI con el preflight del loop; el clic del operador es la aprobación del costo."""
        provider_id = str(account["providerId"])
        if not project_id:
            return _result(provider_id, "cli", "deferred", reason="project_required_for_cli_validation")
        model = self.accounts.first_enabled_model(provider_id)
        if model is None:
            return self._failed(provider_id, "cli", reason="model_required")
        statuses = RuntimeStatusService(
            self.connection, probe_runtime_ids={provider_id}
        ).list_provider_statuses(project_id=project_id)
        runtime_status = next((item for item in statuses if item.get("id") == provider_id), None)
        if runtime_status is None:
            return self._failed(provider_id, "cli", model=model, reason="runtime_status_unavailable")
        EventBus(self.connection).record_audit(
            action=OPERATOR_APPROVAL_AUDIT_ACTION,
            target=provider_id,
            project_id=project_id,
            actor=actor,
            payload={"providerId": provider_id, "projectId": project_id, "model": model},
        )
        request = AIResourceRequest(
            task_type="runtime_team.validate",
            project_id=project_id,
            agent_id="runtime_team_validation",
            task_id=f"runtime-validation-{provider_id}",
            required_capabilities=["chat"],
            allowed_provider_ids=[provider_id],
            allow_unknown_cost=True,
            require_approval_for_unknown_cost=False,
        )
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat(timespec="microseconds")
        outcome = validate_cli_candidate(
            self.connection,
            model={"providerId": provider_id, "model": model},
            runtime_status=runtime_status,
            request=request,
        )
        latency = int((time.monotonic() - started) * 1000)
        if outcome.get("success"):
            return _result(provider_id, "cli", "validated", model=model, latency_ms=latency)
        if not outcome.get("attempted"):
            reason = str(outcome.get("reason") or "preflight_not_executed")
            return _result(provider_id, "cli", "deferred", model=model, reason=reason)
        reason = str(outcome.get("failureCause") or outcome.get("reason") or "cli_preflight_unverified")
        return self._failed(
            provider_id,
            "cli",
            model=model,
            latency_ms=latency,
            reason=reason,
            evidence=outcome.get("failureEvidence"),
            started_at=started_at,
        )
