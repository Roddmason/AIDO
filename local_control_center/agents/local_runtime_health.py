"""Sonda de salud de servidores de inferencia locales según el perfil de su entrada de catálogo.

Distingue "cargando modelo" de "caído": un `503` del liveness (llama-server mientras carga) o un modelo
habilitado en `loading` es `model_loading`, no una falla, y no abre cooldowns. Un servidor que no responde
es `local_server_unreachable`, un `401/403` es `local_auth_required` y un servidor de un solo modelo que ya
no sirve el modelo configurado es `local_model_load_failed`. Lectura acotada: nunca más de
`MAX_PROBE_BYTES` por respuesta de un servidor local no confiable. Un bearer nunca sale por `http://` hacia
un host que no es loopback ni está declarado local (`insecure_credential_transport`, sin ningún pedido): el
sync llega aquí sin pasar por el guard de `ModelGateway._provider_configuration`.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .credentials import CredentialResolver
from .endpoint_locality import catalog_entry_for_account, credential_transport_allowed, server_root_url
from .local_runtime_causes import LocalRuntimeCause
from .provider_catalog import LocalRuntimeProfile
from .providers.http_transport import urlopen_fail_closed
from .providers.openai_compatible import PROVIDER_USER_AGENT

LIVENESS_TIMEOUT_SECONDS = 5.0
MAX_PROBE_BYTES = 1024 * 1024
LocalHealthStatus = Literal["healthy", "model_loading", "offline", "misconfigured"]
_PROVIDER_STATUS = {
    "healthy": "available",
    "model_loading": "model_loading",
    "offline": "unavailable",
    "misconfigured": "misconfigured",
}


@dataclass(frozen=True)
class LocalHealthResult:
    """Resultado de la sonda: estado de salud, causa clasificada, mensaje sin secretos y modelos listados."""

    health_status: LocalHealthStatus
    cause: LocalRuntimeCause | None
    message: str
    models: tuple[str, ...] = ()

    def as_provider_health(self, provider_id: str) -> dict[str, Any]:
        """Proyecta el resultado al contrato `ProviderHealth` del gateway (más `cause` y `models`)."""
        return {
            "providerId": provider_id,
            "status": _PROVIDER_STATUS[self.health_status],
            "healthStatus": self.health_status,
            "message": self.message,
            "lastError": None if self.health_status == "healthy" else self.message,
            "cause": self.cause,
            "models": list(self.models),
        }


def local_profile_for_account(account: Mapping[str, Any]) -> LocalRuntimeProfile | None:
    """Perfil de sondeo de una cuenta `local` según su identidad de catálogo; None si no tiene."""
    if str(account.get("providerType") or "") != "local":
        return None
    entry = catalog_entry_for_account(account)
    return entry.local_profile if entry is not None else None


def _get_json(url: str, headers: Mapping[str, str], timeout_s: float) -> tuple[int, Any]:
    """GET acotado; un código HTTP de error se devuelve como estado, no como excepción.

    Raises:
        OSError: si el servidor no responde (incluye `URLError` y timeouts).
        ValueError: si la respuesta supera `MAX_PROBE_BYTES`.
    """
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen_fail_closed(request, timeout=timeout_s) as response:
            status = int(response.status)
            raw = response.read(MAX_PROBE_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        return int(error.code), None
    if len(raw) > MAX_PROBE_BYTES:
        raise ValueError("Local runtime response exceeds the probe size limit.")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None


def _model_ids(payload: Any) -> tuple[str, ...]:
    items = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        return ()
    return tuple(str(item["id"]) for item in items if isinstance(item, Mapping) and item.get("id"))


def _model_status_values(payload: Any) -> dict[str, str]:
    """`status.value` por id de `/v1/models` (router llama.cpp); un id sin estado no aparece."""
    items = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        return {}
    values: dict[str, str] = {}
    for item in items:
        status = item.get("status") if isinstance(item, Mapping) else None
        if isinstance(status, Mapping) and item.get("id"):
            values[str(item["id"])] = str(status.get("value") or "").strip().lower()
    return values


def _expected_models_result(
    profile: LocalRuntimeProfile, payload: Any, models: tuple[str, ...], expected_models: Collection[str]
) -> LocalHealthResult | None:
    """Estado de los modelos esperados: uno en `loading` o un servidor de un solo modelo que no los sirve."""
    expected = [str(model) for model in expected_models if str(model).strip()]
    if not expected:
        return None
    states = _model_status_values(payload)
    loading = next((model for model in expected if states.get(model) == "loading"), None)
    if loading is not None:
        return LocalHealthResult(
            "model_loading", "model_loading", f"model_loading: {loading} is loading.", models
        )
    if profile.model_state_source == "single_model" and not set(expected) & set(models):
        return LocalHealthResult(
            "offline",
            "local_model_load_failed",
            "local_model_load_failed: /v1/models does not list the configured model.",
            models,
        )
    return None


def _status_result(status: int, path: str) -> LocalHealthResult | None:
    if status == 503:
        return LocalHealthResult(
            "model_loading", "model_loading", "model_loading: the server is loading a model."
        )
    if status in {401, 403}:
        return LocalHealthResult(
            "misconfigured",
            "local_auth_required",
            f"local_auth_required: {path} answered HTTP {status}; configure the server token.",
        )
    if status >= 400:
        return LocalHealthResult(
            "offline", "local_server_unreachable", f"local_server_unreachable: {path} answered HTTP {status}."
        )
    return None


def probe_local_runtime(
    account: Mapping[str, Any],
    profile: LocalRuntimeProfile,
    *,
    timeout_s: float = LIVENESS_TIMEOUT_SECONDS,
    expected_models: Collection[str] = (),
) -> LocalHealthResult:
    """Sondea liveness y `/v1/models` del servidor local; nunca lanza por fallas de red.

    `expected_models` son los modelos habilitados de la cuenta (vacío en el sync, que los descubre).
    """
    root = server_root_url(str(account.get("baseUrl") or ""))
    if not root:
        return LocalHealthResult("misconfigured", None, "Local runtime base URL is not configured.")
    headers = {"Accept": "application/json", "User-Agent": PROVIDER_USER_AGENT}
    credential_ref = str(account.get("credentialRef") or "").strip()
    if credential_ref and not credential_transport_allowed(account):
        return LocalHealthResult(
            "misconfigured",
            "insecure_credential_transport",
            "insecure_credential_transport: a bearer credential cannot travel over http:// "
            "to a host that is neither loopback nor declared local.",
        )
    if credential_ref:
        resolution = CredentialResolver().resolve(credential_ref)
        if not resolution.configured:
            return LocalHealthResult(
                "misconfigured",
                "local_auth_required",
                f"local_auth_required: credential ref is {resolution.status}.",
            )
        headers["Authorization"] = f"Bearer {resolution.value}"
    try:
        liveness_status, _payload = _get_json(root + profile.liveness_path, headers, timeout_s)
        failure = _status_result(liveness_status, profile.liveness_path)
        if failure is not None:
            return failure
        models_status, models_payload = _get_json(f"{root}/v1/models", headers, timeout_s)
    except (OSError, ValueError) as error:
        return LocalHealthResult(
            "offline", "local_server_unreachable", f"local_server_unreachable: {error.__class__.__name__}."
        )
    failure = _status_result(models_status, "/v1/models")
    if failure is not None:
        return failure
    models = _model_ids(models_payload)
    if profile.health_requires_models and not models:
        return LocalHealthResult(
            "offline", "local_server_unreachable", "local_server_unreachable: /v1/models listed no models."
        )
    expected_failure = _expected_models_result(profile, models_payload, models, expected_models)
    if expected_failure is not None:
        return expected_failure
    return LocalHealthResult("healthy", None, "Local runtime answered its liveness probe.", models)
