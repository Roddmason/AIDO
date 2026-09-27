"""Prueba de ida y vuelta de un runtime a pedido del operador (operación ``models.validate_runtime``).

API, gateway y local reusan la completion fija de test-prompt; los CLI reusan el preflight del loop
con un ``AIResourceRequest`` del operador que autoriza el costo desconocido, porque el clic es la
aprobación: queda auditado como ``runtime.validation.operator_approved`` y consume cuota de
suscripción. Toda falla deja evidencia (salvo una denegación de política del proyecto), así una
prueba fallida invalida también la validación de 24 h; la causa sale del clasificador compartido.
Sin modelo pedido, una cuenta de API/gateway prueba candidatos acotados (último validado, modelos de
las políticas de rol, preferidos del operador, allowlist curada del gateway y una muestra repartida por
upstream) hasta que uno pase, dentro de un presupuesto total de tiempo: un gateway con 1000+ modelos
anuncia upstreams sin cuenta y probarlos en orden alfabético no representa lo que el operador usa.

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
from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.endpoint_locality import (
    catalog_entry_for_account,
    credential_transport_allowed,
)
from local_control_center.agents.gateway_model_allowlist import (
    GATEWAY_MODEL_ALLOWLIST_FILES,
    load_model_allowlist,
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
    provider_account_requires_credential,
)
from local_control_center.agents.providers.http_transport import http_error_excerpt
from local_control_center.agents.routing_profiles import RoutingProfileStore
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
_TIMEOUT_TEXT = re.compile(r"timed? ?out|timeout", re.IGNORECASE)
#: Campos de cada intento que viajan en ``attempts``.
_ATTEMPT_KEYS = ("model", "status", "reason", "evidence", "latencyMs", "httpStatus")
#: Ventana en la que una falla de un modelo lo manda al final de los candidatos automáticos.
RECENT_FAILURE_SECONDS = 30 * 60
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
#: Tope de modelos que prueba una validación sin modelo pedido en una cuenta de API/gateway.
MAX_AUTO_VALIDATION_ATTEMPTS = 5
#: Presupuesto total de una validación remota: la cola del operador nunca espera más que esto.
VALIDATION_TIME_BUDGET_SECONDS = 25.0
#: Tope de cada intento: un upstream colgado no se come el presupuesto de los demás candidatos.
VALIDATION_ATTEMPT_TIMEOUT_SECONDS = 12.0
#: Por debajo de este margen no se lanza otro intento (no alcanzaría a responder).
_MIN_ATTEMPT_SECONDS = 2.0
#: Causas a nivel de proveedor: probar otro modelo del mismo endpoint no cambiaría el resultado. Solo
#: cuentan cuando el endpoint no respondió HTTP: un gateway que contesta 504 "upstream timed out" sí
#: es alcanzable y otro upstream puede andar.
_PROVIDER_LEVEL_CAUSES = frozenset({"provider_unreachable"})


def _result(
    provider_id: str,
    kind: str,
    status: str,
    *,
    model: str | None = None,
    latency_ms: int | None = None,
    reason: str | None = None,
    evidence: str | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "kind": kind,
        "status": status,
        "model": model,
        "latencyMs": latency_ms,
        "reason": str(redact_secrets(reason)) if reason else None,
        "evidence": str(redact_secrets(evidence))[:EVIDENCE_LIMIT] if evidence else None,
        "httpStatus": http_status,
        "checkedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def credential_failure(account: dict[str, Any], detail: str) -> tuple[str, str]:
    """Causa estable y explicación legible de una credencial que no pasó el control previo a la red.

    Dice qué referencia se revisó y por qué no sirve (variable ausente en el entorno de AIDO, entrada
    del llavero vacía, almacén no soportado) sin resolver el secreto: una prueba que falla en 0 ms no
    llegó a hacer ninguna petición y el operador tiene que saber qué corregir en su máquina. ``detail``
    es el texto del control original y queda como respaldo.
    """
    provider_id = str(account.get("providerId") or "")
    ref = str(account.get("credentialRef") or "").strip()
    if not ref:
        if provider_account_requires_credential(account):
            return "credential_ref_required", (
                f"Provider {provider_id} has no credential ref configured; add one (for example "
                "env:NAME or keyring:service/account) in the provider settings."
            )
        return "credential_invalid", str(redact_secrets(detail))
    resolution = CredentialResolver().resolve(ref, fetch=False)
    public_ref = resolution.ref or ref
    note = f" ({resolution.message})" if resolution.message else ""
    if resolution.status == "missing" and resolution.source == "env":
        message = (
            f"Credential ref {public_ref} is not set in AIDO's environment{note}. Set the variable in "
            "the environment AIDO starts from (or use a keyring ref) and restart AIDO."
        )
        return "credential_missing", str(redact_secrets(message))
    if resolution.status == "missing":
        return "credential_missing", str(
            redact_secrets(f"Credential ref {public_ref} has no stored secret{note}.")
        )
    if resolution.status == "unsupported":
        message = f"Credential ref {public_ref} uses a store AIDO cannot read here{note}."
        return "credential_unsupported", str(redact_secrets(message))
    if resolution.status == "invalid":
        message = f"Credential ref {public_ref} is not a valid reference{note}."
        return "credential_invalid", str(redact_secrets(message))
    message = f"Credential ref {public_ref} is {resolution.status}{note}: {detail}"
    return "credential_invalid", str(redact_secrets(message))


def _stored_secret_missing(account: dict[str, Any]) -> tuple[str, str] | None:
    """Causa y explicación de una ref que el control sin lectura dio por buena pero sin secreto legible.

    Una ref de llavero o bóveda queda ``unverified`` sin leer el valor; si al leerlo no hay secreto el
    adapter fallaría con un error genérico de configuración. Devuelve ``None`` si hay valor.
    """
    ref = str(account.get("credentialRef") or "").strip()
    if not ref:
        return None
    resolver = CredentialResolver()
    if resolver.resolve(ref, fetch=False).status != "unverified":
        return None
    resolution = resolver.resolve(ref)
    if resolution.value:
        return None
    public_ref = resolution.ref or ref
    note = f" ({resolution.message})" if resolution.message else ""
    if resolution.status in {"unsupported", "unavailable"}:
        message = f"Credential ref {public_ref} uses a store AIDO cannot read here{note}."
        return "credential_unsupported", str(redact_secrets(message))
    return "credential_missing", str(
        redact_secrets(f"Credential ref {public_ref} has no stored secret{note}.")
    )


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


def _role_policy_models(connection: sqlite3.Connection, provider_id: str) -> list[str]:
    """Modelos concretos (no comodín) que las políticas de rol nombran para ``provider_id``, en orden.

    Recorre cada política en su orden de prioridad (preferred → fallback → escalation); un comodín
    ``*`` no nombra un modelo y se omite.
    """
    models: list[str] = []
    for policy in RoutingProfileStore(connection).list_role_policies():
        for ref in [*policy.get("preferred", []), *policy.get("fallback", []), *policy.get("escalation", [])]:
            if not isinstance(ref, dict) or str(ref.get("provider") or "") != provider_id:
                continue
            model = str(ref.get("model") or "").strip()
            if model and model != "*" and model not in models:
                models.append(model)
    return models


def _endpoint_unreachable(result: dict[str, Any]) -> bool:
    """Indica si la falla es del endpoint (no respondió) y no de un modelo: cortar la búsqueda.

    Una respuesta HTTP (aunque sea un 504 "upstream timed out") prueba que el gateway es alcanzable, y un
    timeout de lectura es de ese upstream: con 1000+ modelos, otro upstream puede responder.
    """
    if result.get("reason") not in _PROVIDER_LEVEL_CAUSES or result.get("httpStatus") is not None:
        return False
    return _TIMEOUT_TEXT.search(str(result.get("evidence") or "")) is None


def _curated_gateway_models(account: dict[str, Any]) -> list[str]:
    """Allowlist curada del gateway de la cuenta (OmniRoute), o vacía si no tiene o no se puede leer."""
    entry = catalog_entry_for_account(account)
    path = GATEWAY_MODEL_ALLOWLIST_FILES.get(entry.id) if entry is not None else None
    if path is None:
        return []
    try:
        return [str(item["model"]) for item in load_model_allowlist(path)]
    except (OSError, ValueError):
        return []


def _spread_by_upstream(models: list[str]) -> list[str]:
    """Reparte ``models`` por upstream (lo anterior a la primera ``/``): uno de cada uno antes de repetir.

    Conserva el orden de los upstreams y, dentro de cada uno, el del catálogo; es determinista.
    """
    groups: dict[str, list[str]] = {}
    for model in models:
        groups.setdefault(model.split("/", 1)[0] if "/" in model else "", []).append(model)
    spread: list[str] = []
    depth = 0
    while len(spread) < len(models):
        spread.extend(group[depth] for group in groups.values() if depth < len(group))
        depth += 1
    return spread


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

    def _models_to_validate(self, account: dict[str, Any], *, requested: str | None) -> list[str]:
        """Modelos a probar en orden: el pedido si está habilitado; en locales el resuelto; si no, candidatos.

        En una cuenta local sin modelo pedido se prueba lo que usaría un rol de chat (cargado → por defecto
        → orden del operador), no el primero alfabético. En API/gateway sin modelo pedido se devuelven hasta
        ``MAX_AUTO_VALIDATION_ATTEMPTS`` candidatos (ver :meth:`_auto_validation_candidates`): un gateway
        anuncia modelos upstream sin cuenta y el primero alfabético no representa lo que el operador usa.
        """
        provider_id = str(account["providerId"])
        enabled = [
            str(item["model"]) for item in self.accounts.list_models(provider_id) if item.get("enabled")
        ]
        if requested:
            return [requested] if requested in enabled else []
        if not enabled:
            return []
        if str(account.get("providerType") or "") != "local":
            return self._auto_validation_candidates(account, enabled)
        resolved = self._resolve_local_chat_model(account, provider_id, enabled)
        return [resolved] if resolved else []

    def _auto_validation_candidates(self, account: dict[str, Any], enabled: list[str]) -> list[str]:
        """Orden de prueba de una cuenta de API/gateway sin modelo pedido, acotado y sin repetidos.

        Primero los modelos habilitados con una validación exitosa (el más reciente antes), luego los
        modelos concretos que las políticas de rol nombran para este proveedor, los que el operador marcó
        (por defecto u ordenados), la allowlist curada del gateway, los de tier gratuito y, al final, una
        muestra que toma un modelo por upstream (prefijo ``upstream/``) antes de repetir: con 1000+
        modelos, los primeros alfabéticos suelen ser del mismo upstream sin cuenta. Un modelo cuya última
        evidencia con la configuración actual es una falla reciente pasa al final: repetirlo no ayuda.
        """
        provider_id = str(account["providerId"])
        enabled_set = set(enabled)
        validated = [
            str(row["model"])
            for row in self.connection.execute(
                """SELECT model, MAX(started_at) AS last_success FROM model_execution_health
                   WHERE provider_id = ? AND success = 1 GROUP BY model ORDER BY last_success DESC""",
                (provider_id,),
            ).fetchall()
            if str(row["model"]) in enabled_set
        ]
        free_tier = [
            str(item["model"])
            for item in self.accounts.list_models(provider_id)
            if item.get("enabled") and item.get("freeTier")
        ]
        preferred = [
            *validated,
            *_role_policy_models(self.connection, provider_id),
            *self._operator_preferred_models(provider_id),
            *_curated_gateway_models(account),
            *free_tier,
            *_spread_by_upstream(enabled),
        ]
        ordered = [model for model in dict.fromkeys(preferred) if model in enabled_set]
        failed = self._recently_failed_models(provider_id)
        ordered = [model for model in ordered if model not in failed] + [
            model for model in ordered if model in failed
        ]
        return ordered[:MAX_AUTO_VALIDATION_ATTEMPTS]

    def _operator_preferred_models(self, provider_id: str) -> list[str]:
        """Modelos que el operador marcó: el por defecto primero y luego los que ordenó a mano."""
        settings = LocalModelSettingsRepository(self.connection).list_for_account(provider_id)
        defaults = [item.model for item in settings if item.is_default]
        ordered = [item.model for item in settings if item.operator_order > 0 and not item.is_default]
        return [*defaults, *ordered]

    def _recently_failed_models(self, provider_id: str) -> set[str]:
        """Modelos cuya última evidencia con la configuración actual es una falla de la última media hora."""
        fingerprint = provider_configuration_fingerprint(self.connection, provider_id)
        cutoff = datetime.fromtimestamp(time.time() - RECENT_FAILURE_SECONDS, UTC).isoformat(
            timespec="microseconds"
        )
        rows = self.connection.execute(
            """SELECT model, success, configuration_fingerprint, started_at FROM model_execution_health
               WHERE provider_id = ? AND started_at >= ? ORDER BY started_at DESC, id DESC""",
            (provider_id, cutoff),
        ).fetchall()
        latest: dict[str, Any] = {}
        for row in rows:
            latest.setdefault(str(row["model"]), row)
        return {
            model
            for model, row in latest.items()
            if not row["success"] and row["configuration_fingerprint"] == fingerprint
        }

    def _resolve_local_chat_model(
        self, account: dict[str, Any], provider_id: str, enabled: list[str]
    ) -> str | None:
        """Modelo que usaría un rol de chat en una cuenta local."""
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
            cause, evidence = credential_failure(account, str(error.detail))
            return self._failed(provider_id, kind, reason=cause, evidence=evidence)
        if (unreadable := _stored_secret_missing(account)) is not None:
            return self._failed(provider_id, kind, reason=unreadable[0], evidence=unreadable[1])
        models = self._models_to_validate(account, requested=model)
        if not models:
            return self._failed(provider_id, kind, reason="model_required")
        try:
            provider = provider_instance(provider_id, connection=self.connection)
        except ProviderAdapterResolutionError as error:
            code = str(getattr(error, "public_code", error.code))
            return self._failed(provider_id, kind, model=models[0], reason=code, evidence=str(error))
        profile = _local_profile(account)
        if profile is not None:
            if not credential_transport_allowed(account):
                return self._failed(
                    provider_id, kind, model=models[0], reason="insecure_credential_transport"
                )
            return self._validate_local_model(account, provider, models[0], profile)
        if model:
            return self._probe_remote_model(
                provider_id, kind, models[0], provider, timeout_seconds=VALIDATION_TIME_BUDGET_SECONDS
            )
        return self._probe_remote_candidates(provider_id, kind, models, provider)

    def _probe_remote_candidates(
        self, provider_id: str, kind: str, models: list[str], provider: Any
    ) -> dict[str, Any]:
        """Prueba los candidatos en orden hasta que uno valide; cada intento deja su propia evidencia.

        Una falla de un modelo (p. ej. un upstream del gateway sin cuenta) no corta la búsqueda; una causa
        a nivel de proveedor (endpoint inalcanzable) sí, porque otro modelo fallaría igual. El resultado
        es el del modelo validado, o el del último intento, con ``attempts`` para que el operador vea qué
        modelo pasó o falló y por qué (estado HTTP y extracto del cuerpo). Todos los intentos comparten
        ``VALIDATION_TIME_BUDGET_SECONDS``: cada uno recibe lo que queda, con tope por intento.
        """
        attempts: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        deadline = time.monotonic() + VALIDATION_TIME_BUDGET_SECONDS
        for candidate in models:
            remaining = deadline - time.monotonic()
            if attempts and remaining < _MIN_ATTEMPT_SECONDS:
                break
            result = self._probe_remote_model(
                provider_id,
                kind,
                candidate,
                provider,
                timeout_seconds=max(min(VALIDATION_ATTEMPT_TIMEOUT_SECONDS, remaining), _MIN_ATTEMPT_SECONDS),
            )
            attempts.append({key: result.get(key) for key in _ATTEMPT_KEYS})
            if result["status"] == "validated" or _endpoint_unreachable(result):
                break
        return {**result, "attempts": attempts}

    def _probe_remote_model(
        self, provider_id: str, kind: str, model: str, provider: Any, *, timeout_seconds: float
    ) -> dict[str, Any]:
        """Completion fija de test-prompt contra un modelo de API/gateway, con su evidencia por modelo.

        La evidencia lleva el estado HTTP y el extracto del cuerpo que devolvió el proveedor (o qué traía un
        200 sin completion), así el operador distingue un upstream sin cuenta de un endpoint caído.
        """
        from local_control_center.agents.model_gateway_api import _run_provider_test_prompt

        started_at = datetime.now(UTC).isoformat(timespec="microseconds")
        fingerprint = provider_configuration_fingerprint(self.connection, provider_id)
        outcome = _run_provider_test_prompt(
            provider_id,
            model,
            connection=self.connection,
            provider=provider,
            timeout_seconds=timeout_seconds,
        )
        if outcome["ok"]:
            return _result(provider_id, kind, "validated", model=model, latency_ms=outcome["latencyMs"])
        error_text = str(outcome.get("error") or "")
        http_status = outcome.get("httpStatus")
        fallback = error_text if _STABLE_CODE.fullmatch(error_text) else "runtime_validation_failed"
        cause, evidence = _failure_cause(provider_id, error_text, fallback=fallback)
        detail = str(outcome.get("detail") or "")
        if detail:
            evidence = str(redact_secrets(detail))[:EVIDENCE_LIMIT]
        elif error_text and not _STABLE_CODE.fullmatch(error_text):
            # El clasificador recorta a la línea que matcheó; el cuerpo completo del proveedor explica más.
            evidence = str(redact_secrets(error_text))[:EVIDENCE_LIMIT]
        result = self._failed(
            provider_id,
            kind,
            model=model,
            latency_ms=outcome["latencyMs"],
            reason=cause,
            evidence=evidence,
            fingerprint=fingerprint,
            started_at=started_at,
        )
        return {**result, "httpStatus": http_status if isinstance(http_status, int) else None}

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
