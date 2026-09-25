"""Remediaciones de runtimes locales derivadas del perfil de catálogo y de la causa clasificada.

Arma el ``providerSetup`` del asistente desde la cuenta real (URL guardada; el token solo aparece como
campo requerido si la cuenta ya lo usa o la causa lo exige) y las tres acciones del operador: probar
de nuevo, abrir el asistente y validar modelo, ordenadas por causa. Un bloqueo del gestor de recursos cuyo
colapso dejó una cuenta local sin modelos validados (``local_model_not_validated``, spec §4.3 paso 4) también
se remedia aquí, con "Validar modelo" primero. Funciones puras: no leen la base ni la red.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from local_control_center.agents.endpoint_locality import catalog_entry_for_account, endpoint_locality
from local_control_center.agents.local_runtime_causes import LOCAL_RUNTIME_CAUSES

LOCAL_RUNTIME_BLOCKER_TYPES = frozenset(
    {
        "runtime_not_executable",
        "runtime_execution_failed",
        "runtime_auth_missing",
        "provider_missing_credentials",
        "provider_health_failed",
    }
)
LOCAL_CAUSE_MESSAGES: dict[str, str] = {
    "local_server_unreachable": (
        "The local server is not answering. Start it (for example llama-server, LM Studio or vLLM) and try again."
    ),
    "model_loading": "The server is loading the model. Wait a moment and try again.",
    "local_model_load_failed": (
        "The server could not load the model. Check its log and free memory, then try again."
    ),
    "local_auth_required": (
        "The server requires a token. Save it as a credential reference in the setup wizard."
    ),
    "context_length_exceeded": (
        "The request exceeds the model context. Choose a model with a larger context or shorten the task."
    ),
    "insecure_credential_transport": (
        "A token cannot travel over plain http to a remote host. Use https or declare the endpoint as "
        "running on this computer."
    ),
    "local_endpoint_busy": "The local endpoint is busy with another call. Try again when it finishes.",
    "insufficient_time_for_model_load": (
        "Not enough time remains to load the model. Try again with a loaded model or more time."
    ),
    "local_model_not_validated": (
        "No validated model can serve this role. Validate a model for this runtime."
    ),
}
WSL_HINT_MESSAGE = (
    "If the server runs in WSL, check localhostForwarding in .wslconfig and that the Windows firewall "
    "allows the port."
)
WSL_HINT_MESSAGE_KEY = "app.remediation.local.hint.wsl"
RESOURCE_MANAGER_LOCAL_CAUSES = frozenset({"local_model_not_validated"})
"""Causas locales que convierten un bloqueo ``resource_manager_unconfigured`` en remediación local."""
_WSL_CATALOG_IDS = frozenset({"vllm"})
_AUTH_CAUSES = frozenset({"local_auth_required", "insecure_credential_transport"})
_VALIDATE_FIRST_CAUSES = frozenset({"local_model_not_validated"})
_CAUSE_DETAIL_KEYS = ("localRuntimeCause", "cause", "reasonCode")
_NOT_VALIDATED = "local_model_not_validated"


def cause_message_key(cause: str) -> str:
    """Clave i18n del texto de una causa local."""
    return f"app.remediation.local.cause.{cause}"


def unvalidated_local_provider_id(details: Mapping[str, Any]) -> str | None:
    """Cuenta local que el gestor de recursos rechazó por ``local_model_not_validated``, o ``None``.

    El colapso (Task 21) usa esa causa solo cuando el conjunto ``E`` de la cuenta quedó vacío, así que
    un rechazo con ella identifica la cuenta a validar aunque sus otros modelos tengan otra causa
    (p. ej. capacidades faltantes, fuera de ``E``).
    """
    blockers = details.get("resourceBlockers")
    if not isinstance(blockers, list):
        return None
    for blocker in blockers:
        decision = blocker.get("decision") if isinstance(blocker, Mapping) else None
        rejected = decision.get("rejected") if isinstance(decision, Mapping) else None
        for item in rejected if isinstance(rejected, list) else []:
            provider_id = str(item.get("providerId") or "").strip() if isinstance(item, Mapping) else ""
            if provider_id and item.get("reason") == _NOT_VALIDATED:
                return provider_id
    return None


def local_runtime_cause(reason: str, details: Mapping[str, Any]) -> str | None:
    """Causa local del bloqueo: campo estructurado, código en el texto o ``E`` vacío en el gestor."""
    for key in _CAUSE_DETAIL_KEYS:
        value = str(details.get(key) or "").strip()
        if value in LOCAL_RUNTIME_CAUSES:
            return value
    text = f"{reason or ''} {details.get('reason') or ''}"
    cause = next((cause for cause in sorted(LOCAL_RUNTIME_CAUSES) if cause in text), None)
    if cause is not None:
        return cause
    return _NOT_VALIDATED if unvalidated_local_provider_id(details) else None


def uses_local_runtime_specs(blocker_type: str, setup: Mapping[str, Any]) -> bool:
    """Indica si el bloqueo se remedia con las acciones locales (probar, asistente, validar modelo).

    Los tipos de runtime lo hacen siempre que haya ``providerSetup`` local; un
    ``resource_manager_unconfigured`` solo con una causa de ``RESOURCE_MANAGER_LOCAL_CAUSES``: el resto
    conserva la remediación del gestor de recursos.
    """
    if not setup:
        return False
    if blocker_type in LOCAL_RUNTIME_BLOCKER_TYPES:
        return True
    return (
        blocker_type == "resource_manager_unconfigured"
        and setup.get("localCause") in RESOURCE_MANAGER_LOCAL_CAUSES
    )


def local_runtime_setup_payload(account: Mapping[str, Any], *, cause: str | None) -> dict[str, Any]:
    """``providerSetup`` del asistente para una cuenta local con perfil, sin inventar campos de auth.

    Raises:
        ValueError: si la cuenta no resuelve a una entrada del catálogo.
    """
    entry = catalog_entry_for_account(account)
    if entry is None:
        raise ValueError(f"Local runtime account has no catalog entry: {account.get('providerId')}")
    has_credential = bool(str(account.get("credentialRef") or "").strip())
    auth_needed = has_credential or cause in _AUTH_CAUSES
    optional_auth = entry.credential_kind == "optional_bearer_token" and not auth_needed
    payload: dict[str, Any] = {
        "providerId": str(account["providerId"]),
        "catalogId": entry.id,
        "displayName": str(account.get("displayName") or account["providerId"]),
        "kind": "local",
        "knownProvider": True,
        "requiresManualBaseUrl": entry.default_base_url is None,
        "authFields": ["credentialRef"] if auth_needed else [],
        "optionalAuthFields": ["credentialRef"] if optional_auth else [],
        "requiredFields": ["baseUrl"],
        "baseUrl": str(account.get("baseUrl") or ""),
        "baseUrlSource": "provider_account",
        "wizard": "local_runtime",
    }
    if cause in LOCAL_CAUSE_MESSAGES:
        payload["localCause"] = cause
        payload["causeMessageKey"] = cause_message_key(cause)
        payload["causeMessage"] = LOCAL_CAUSE_MESSAGES[cause]
    declared = endpoint_locality(account) == "declared_local"
    if cause == "local_server_unreachable" and (declared or entry.id in _WSL_CATALOG_IDS):
        payload["hintMessageKey"] = WSL_HINT_MESSAGE_KEY
        payload["hintMessage"] = WSL_HINT_MESSAGE
    return payload


def local_runtime_specs(
    *,
    setup: Mapping[str, Any],
    runtime_settings_payload: Mapping[str, Any],
    runtime_recovery_payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Acciones "Probar de nuevo", "Abrir asistente" y "Validar modelo", ordenadas por causa."""
    cause = setup.get("localCause")
    provider_id = str(setup["providerId"])
    retry = {
        "actionType": "retry_loop",
        "title": "Try again",
        "description": str(setup.get("causeMessage") or "Review the local runtime and try again."),
        "payload": {**runtime_recovery_payload, "retryTarget": "runtime", "localCause": cause},
    }
    wizard = {
        "actionType": "open_settings_section",
        "title": "Open setup wizard",
        "description": "Review the local endpoint URL, token and models in the setup wizard.",
        "payload": {
            **runtime_settings_payload,
            "section": "providers-cli",
            "providerSetup": dict(setup),
            "openWizard": True,
        },
    }
    validate = {
        "actionType": "revalidate_runtime",
        "title": "Validate model",
        "description": "Run a real validation of the endpoint's default model.",
        "payload": {**runtime_recovery_payload, "runtimeIds": [provider_id], "retryTarget": "runtime"},
    }
    if cause in _AUTH_CAUSES:
        return [wizard, validate, retry]
    if cause in _VALIDATE_FIRST_CAUSES:
        return [validate, retry, wizard]
    return [retry, wizard, validate]
