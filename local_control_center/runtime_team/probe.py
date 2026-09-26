"""Prueba de ida y vuelta de un runtime a pedido del operador (operación ``models.validate_runtime``).

API, gateway y local reusan la completion fija de test-prompt; los CLI reusan el preflight del loop
con un ``AIResourceRequest`` del operador que autoriza el costo desconocido, porque el clic es la
aprobación: queda auditado como ``runtime.validation.operator_approved`` y consume cuota de
suscripción. Toda falla deja evidencia (salvo una denegación de política del proyecto), así una
prueba fallida invalida también la validación de 24 h; la causa sale del clasificador compartido.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError

from fastapi import HTTPException

from local_control_center.agents import local_model_state
from local_control_center.agents.ai_resource_manager import AIResourceRequest
from local_control_center.agents.endpoint_locality import (
    catalog_entry_for_account,
    credential_transport_allowed,
)
from local_control_center.agents.local_model_selection import resolve_local_model
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.local_runtime_causes import TRANSIENT_LOCAL_RUNTIME_CAUSES, LocalRuntimeError
from local_control_center.agents.model_execution_health import (
    provider_configuration_fingerprint,
    record_model_execution,
)
from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_catalog import LocalRuntimeProfile
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.factory import (
    ProviderAdapterResolutionError,
    provider_account_policy_kind,
)
from local_control_center.agents.providers.http_transport import http_error_excerpt
from local_control_center.agents.runtime_failure_classifier import (
    EVIDENCE_LIMIT,
    classify_local_model_error,
    classify_runtime_failure,
)
from local_control_center.agents.runtime_preflight_cli import validate_cli_candidate
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets

OPERATOR_APPROVAL_AUDIT_ACTION = "runtime.validation.operator_approved"
_STABLE_CODE = re.compile(r"[a-z0-9_.:-]+")
VALIDATION_MAX_TOKENS = 1024
VALIDATION_JSON_PROMPT = 'Reply with exactly this JSON object and nothing else: {"ok": true}'
VALIDATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "aido_runtime_validation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}
DEFERRED_VALIDATION_CAUSES = TRANSIENT_LOCAL_RUNTIME_CAUSES | frozenset({"insufficient_time_for_model_load"})
HTTP_BAD_REQUEST = 400


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


def _local_profile(account: dict[str, Any]) -> LocalRuntimeProfile | None:
    """Perfil de runtime local de la cuenta, o ``None`` si no es local o su entrada no declara uno."""
    if str(account.get("providerType") or "") != "local":
        return None
    entry = catalog_entry_for_account(account)
    return entry.local_profile if entry is not None else None


def _local_validation_exchange(
    provider: Any, model: str, profile: LocalRuntimeProfile, *, was_loaded: bool
) -> tuple[str, bool]:
    """Pide el JSON de validación; devuelve el texto y si el servidor aceptó ``response_format`` json_schema.

    Un modelo que no consta cargado recibe ``cold_start_timeout_s`` del perfil en vez del default de chat:
    con 60 s un modelo del router que tarda más en cargar quedaría diferido en cada intento.

    Raises:
        HTTPError: un estado distinto de 400 en el primer intento, o cualquier error del segundo.
        LocalRuntimeError, OSError: propagados desde el adapter.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": VALIDATION_JSON_PROMPT}],
        "temperature": 0,
        "maxTokens": VALIDATION_MAX_TOKENS,
        "extraBody": dict(profile.disable_reasoning_body or {}),
    }
    if not was_loaded:
        body["timeoutSeconds"] = profile.cold_start_timeout_s
    try:
        response = provider.chat_completion(
            ModelRequest.model_validate({**body, "responseFormat": VALIDATION_RESPONSE_FORMAT})
        )
    except HTTPError as error:
        if error.code != HTTP_BAD_REQUEST:
            raise
        error.close()  # libera el socket del 400 antes del reintento, sin esperar al GC
        fallback = provider.chat_completion(ModelRequest.model_validate(body))
        return str(fallback.content or ""), False
    return str(response.content or ""), True


def probe_local_json_schema_capability(
    provider: Any, model: str, profile: LocalRuntimeProfile, *, was_loaded: bool
) -> bool:
    """Ida y vuelta reutilizable para descubrir si un modelo local soporta ``response_format`` json_schema.

    Comparte el intercambio de :func:`_local_validation_exchange` (mismo prompt fijo y el mismo fallback
    sin formato ante un 400) para que la validación manual del operador y el descubrimiento automático del
    preflight prueben exactamente lo mismo. Solo el JSON ``{"ok": true}`` cuenta como sonda exitosa.

    Raises:
        ValueError: la respuesta no fue el JSON de validación esperado.
        HTTPError, LocalRuntimeError, OSError: propagadas del adapter del proveedor; el llamador decide
            si las trata como diferidas o como fallo.
    """
    content, json_schema = _local_validation_exchange(provider, model, profile, was_loaded=was_loaded)
    if not _validation_json_ok(content):
        raise ValueError('Local json_schema probe response was not the expected {"ok": true} JSON.')
    return json_schema


def _validation_json_ok(content: str) -> bool:
    """Indica si la respuesta es el objeto JSON ``{"ok": true}`` (tolera un fence Markdown)."""
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip().removeprefix("json").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("ok") is True


class RuntimeValidationService:
    """Ejecuta la prueba real de un runtime y deja la evidencia en ``model_execution_health``."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)

    def validate(
        self,
        provider_id: str,
        *,
        project_id: str | None = None,
        actor: str = "operator",
        model: str | None = None,
    ) -> dict[str, Any]:
        """Prueba un runtime (o uno de sus modelos) y devuelve un resultado redactado.

        El estado es validated, failed o deferred; ``model`` fija el modelo a probar en runtimes de modelo.

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
        return self._validate_model_provider(account, project_id=project_id, model=model)

    def _last_recorded_model(self, provider_id: str) -> str | None:
        row = self.connection.execute(
            """SELECT model FROM model_execution_health WHERE provider_id = ?
               ORDER BY started_at DESC, id DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()
        return str(row["model"]) if row is not None else None

    def _model_to_validate(self, account: dict[str, Any], *, requested: str | None) -> str | None:
        """Modelo a probar: el pedido si está habilitado; en cuentas locales el resuelto; si no, el primero.

        En una cuenta local sin modelo pedido se prueba lo que usaría un rol de chat (cargado → por defecto
        → orden del operador), no el primero alfabético.
        """
        provider_id = str(account["providerId"])
        enabled = [
            str(item["model"]) for item in self.accounts.list_models(provider_id) if item.get("enabled")
        ]
        if requested:
            return requested if requested in enabled else None
        if str(account.get("providerType") or "") != "local" or not enabled:
            return enabled[0] if enabled else None
        return resolve_local_model(
            required_capabilities=frozenset({"chat"}),
            enabled_models=enabled,
            settings=LocalModelSettingsRepository(self.connection).list_for_account(provider_id),
            validated_models=frozenset(enabled),
            load_states=local_model_state.LOAD_STATE_CACHE.get(account),
            model_aliases=local_model_state.model_aliases_for(account),
        ).model

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

    def _validate_model_provider(
        self, account: dict[str, Any], *, project_id: str | None, model: str | None = None
    ) -> dict[str, Any]:
        """Prueba API/gateway/local; un local con perfil de catálogo prueba chat + JSON del modelo resuelto.

        La prueba local llama al provider sin pasar por el adapter del broker, así que aplica aquí el mismo
        guard de transporte: un bearer nunca viaja por ``http://`` a un host no loopback ni declarado local.
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
        model = self._model_to_validate(account, requested=model)
        if model is None:
            return self._failed(provider_id, kind, reason="model_required")
        try:
            provider = provider_instance(provider_id, connection=self.connection)
        except ProviderAdapterResolutionError as error:
            code = str(getattr(error, "public_code", error.code))
            return self._failed(provider_id, kind, model=model, reason=code, evidence=str(error))
        profile = _local_profile(account)
        if profile is not None:
            if not credential_transport_allowed(account):
                return self._failed(provider_id, kind, model=model, reason="insecure_credential_transport")
            return self._validate_local_model(account, provider, model, profile)
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

    def _validate_local_model(
        self, account: dict[str, Any], provider: Any, model: str, profile: LocalRuntimeProfile
    ) -> dict[str, Any]:
        """Chat + JSON del modelo con ``max_tokens`` 1024 y el cuerpo del perfil que apaga el razonamiento.

        Solo un objeto JSON con ``ok=true`` valida; la capacidad json_schema del modelo queda registrada
        cuando el primer intento la cumplió. Un modelo cargándose (``model_loading`` del adapter o del
        clasificador local: 503 o texto de carga), la lease del endpoint ocupada (``local_endpoint_busy``, P23)
        o un cold start que no alcanzó a responder se difieren sin evidencia fallida: no invalidan la
        validación vigente. Una falla que el clasificador local reconoce (401/403, carga fallida, servidor
        inalcanzable) deja su ``LocalRuntimeCause`` como ``reason`` (P18), y cualquier otra falla del
        provider (p. ej. un 200 con HTML de un proxy) termina ``failed`` con evidencia, igual que
        ``_run_provider_test_prompt``.
        """
        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        was_loaded = local_model_state.LOAD_STATE_CACHE.get(account).get(model) == "loaded"
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat(timespec="microseconds")
        fingerprint = provider_configuration_fingerprint(self.connection, provider_id)
        failure = {"model": model, "fingerprint": fingerprint, "started_at": started_at}
        try:
            content, json_schema = _local_validation_exchange(provider, model, profile, was_loaded=was_loaded)
        except LocalRuntimeError as error:
            if error.cause in DEFERRED_VALIDATION_CAUSES:
                return _result(provider_id, kind, "deferred", model=model, reason=error.cause)
            return self._failed(provider_id, kind, reason=error.cause, evidence=str(error), **failure)
        except HTTPError as error:
            load_failure = classify_local_model_error(
                error=error, http_status=error.code, body=http_error_excerpt(error)
            )
            if load_failure is not None and load_failure.cause in DEFERRED_VALIDATION_CAUSES:
                return _result(provider_id, kind, "deferred", model=model, reason=load_failure.cause)
            record_model_execution(
                self.connection,
                provider_id,
                model,
                False,
                "test_prompt",
                http_status=error.code,
                configuration_fingerprint=fingerprint,
                started_at=started_at,
            )
            detail = f"HTTP {error.code}: {error.reason}"
            if load_failure is not None:
                evidence = load_failure.evidence or str(redact_secrets(detail))[:EVIDENCE_LIMIT]
                return self._failed(
                    provider_id, kind, reason=load_failure.cause, evidence=evidence, **failure
                )
            cause, evidence = _failure_cause(provider_id, detail, fallback="runtime_validation_failed")
            return self._failed(provider_id, kind, reason=cause, evidence=evidence, **failure)
        except TimeoutError as error:
            if not was_loaded:
                return _result(provider_id, kind, "deferred", model=model, reason="model_loading")
            cause, evidence = _failure_cause(
                provider_id, f"TimeoutError: {error}", fallback="runtime_validation_failed"
            )
            return self._failed(provider_id, kind, reason=cause, evidence=evidence, **failure)
        except (URLError, OSError) as error:
            detail = f"{error.__class__.__name__}: {error}"
            classified = classify_local_model_error(error=error, http_status=None)
            if classified is not None:
                evidence = classified.evidence or str(redact_secrets(detail))[:EVIDENCE_LIMIT]
                return self._failed(provider_id, kind, reason=classified.cause, evidence=evidence, **failure)
            cause, evidence = _failure_cause(provider_id, detail, fallback="runtime_validation_failed")
            return self._failed(provider_id, kind, reason=cause, evidence=evidence, **failure)
        except (ValueError, RuntimeError) as error:
            record_model_execution(
                self.connection,
                provider_id,
                model,
                False,
                "test_prompt",
                configuration_fingerprint=fingerprint,
                started_at=started_at,
            )
            return self._failed(
                provider_id,
                kind,
                reason="runtime_validation_failed",
                evidence=str(redact_secrets(f"{error.__class__.__name__}: {error}"))[:EVIDENCE_LIMIT],
                **failure,
            )
        latency = int((time.monotonic() - started) * 1000)
        valid = _validation_json_ok(content)
        record_model_execution(
            self.connection,
            provider_id,
            model,
            valid,
            "test_prompt",
            configuration_fingerprint=fingerprint,
            started_at=started_at,
        )
        if not valid:
            return self._failed(
                provider_id, kind, latency_ms=latency, reason="model_validation_invalid_json", **failure
            )
        LocalModelSettingsRepository(self.connection).upsert(
            provider_id, model, actor="runtime_validation", json_schema=json_schema
        )
        return _result(provider_id, kind, "validated", model=model, latency_ms=latency)

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
