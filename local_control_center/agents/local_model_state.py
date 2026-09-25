"""Estado de carga de los modelos de un servidor local, leído en vivo y cacheado 10 s por proceso.

``read_load_states`` despacha por ``LOAD_STATE_READERS`` según la fuente que declara el perfil del
catálogo, con parseo defensivo (campo ausente o desconocido ⇒ ``unknown``): ``openai_models_status`` lee
``data[].status.value`` de ``/v1/models`` (router de llama.cpp; cada alias de ``data[].aliases`` comparte el
estado de su id canónico y la tabla alias → id queda en ``model_aliases_for``), ``single_model`` marca cargado
el único id que expone el servidor y ``none`` no consulta nada. El estado nunca se persiste. ``LoadStateCache``
comparte la lectura: un solo refresco en vuelo por cuenta, en segundo plano, y como máximo ``max_wait_s`` de
espera; un servidor lento o caído deja el estado desconocido en vez de bloquear a quien lee.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Literal

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.endpoint_locality import (
    catalog_entry_for_account,
    credential_transport_allowed,
    server_root_url,
)
from local_control_center.agents.provider_catalog import LocalRuntimeProfile
from local_control_center.agents.providers.http_transport import urlopen_fail_closed

logger = logging.getLogger(__name__)

LoadState = Literal["loaded", "loading", "unloaded", "unknown"]
HttpGetJson = Callable[[str, Mapping[str, str], float], Any]
LoadStateSourceReader = Callable[[str, Mapping[str, str], HttpGetJson], dict[str, LoadState]]
LoadStateReader = Callable[[Mapping[str, Any]], dict[str, LoadState]]

LOAD_STATE_TTL_SECONDS = 10.0
LOAD_STATE_HTTP_TIMEOUT_SECONDS = 2.0
LOAD_STATE_MAX_BYTES = 1024 * 1024
_KNOWN_STATES = frozenset({"loaded", "loading", "unloaded"})
_aliases_lock = threading.Lock()
_LISTED_ALIASES: dict[str, dict[str, str]] = {}


def default_http_get_json(url: str, headers: Mapping[str, str], timeout_s: float) -> Any:
    """GET sin redirecciones que lee como máximo ``LOAD_STATE_MAX_BYTES`` y decodifica JSON.

    Raises:
        OSError: transporte fallido o estado HTTP de error (``HTTPError`` hereda de ``OSError``).
        ValueError: cuerpo mayor al tope o JSON inválido.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json", **headers}, method="GET")
    with urlopen_fail_closed(request, timeout=timeout_s) as response:
        body = response.read(LOAD_STATE_MAX_BYTES + 1)
    if len(body) > LOAD_STATE_MAX_BYTES:
        raise ValueError("Local model listing exceeds the response size limit.")
    return json.loads(body.decode("utf-8"))


def _auth_headers(account: Mapping[str, Any]) -> dict[str, str]:
    """Bearer solo si la ref resuelve y el transporte lo permite (nunca por http a un host remoto)."""
    credential_ref = str(account.get("credentialRef") or "").strip()
    if not credential_ref or not credential_transport_allowed(account):
        return {}
    credential = CredentialResolver().resolve(credential_ref)
    return {"Authorization": f"Bearer {credential.value}"} if credential.value else {}


def _listed_models(payload: Any) -> list[Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    return [item for item in data if isinstance(item, Mapping)] if isinstance(data, list) else []


def _status_value(item: Mapping[str, Any]) -> LoadState:
    status = item.get("status")
    value = status.get("value") if isinstance(status, Mapping) else None
    return value if value in _KNOWN_STATES else "unknown"


def _identified_models(
    root_url: str, headers: Mapping[str, str], http_get_json: HttpGetJson
) -> list[tuple[str, Mapping[str, Any]]]:
    """``(id, item)`` de cada modelo que lista ``/v1/models`` del servidor (ids vacíos se descartan)."""
    items = _listed_models(http_get_json(f"{root_url}/v1/models", headers, LOAD_STATE_HTTP_TIMEOUT_SECONDS))
    identified = [(str(item.get("id") or "").strip(), item) for item in items]
    return [(model_id, item) for model_id, item in identified if model_id]


def _alias_names(item: Mapping[str, Any]) -> list[str]:
    """Alias no vacíos de un modelo del listado (``data[].aliases``); otro tipo de valor se ignora."""
    aliases = item.get("aliases")
    if not isinstance(aliases, list):
        return []
    return [alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()]


def read_openai_models_status_states(
    root_url: str, headers: Mapping[str, str], http_get_json: HttpGetJson
) -> dict[str, LoadState]:
    """Router de llama.cpp: ``data[].status.value`` de ``/v1/models``; valor ausente o raro ⇒ ``unknown``.

    Cada alias de ``data[].aliases`` recibe el estado de su id canónico, así un modelo guardado o elegido
    por su alias resuelve su carga; un alias que coincide con otro id canónico no lo tapa. La tabla alias → id
    observada reemplaza la anterior de ese servidor en ``model_aliases_for``.
    """
    identified = _identified_models(root_url, headers, http_get_json)
    states: dict[str, LoadState] = {model_id: _status_value(item) for model_id, item in identified}
    aliases: dict[str, str] = {}
    for model_id, item in identified:
        for alias in _alias_names(item):
            if alias not in states:
                aliases.setdefault(alias, model_id)
    with _aliases_lock:
        _LISTED_ALIASES[root_url] = aliases
    return {**states, **{alias: states[model_id] for alias, model_id in aliases.items()}}


def read_single_model_states(
    root_url: str, headers: Mapping[str, str], http_get_json: HttpGetJson
) -> dict[str, LoadState]:
    """Servidor de un solo modelo (vLLM): su único id está cargado; más de uno ⇒ desconocido."""
    identified = _identified_models(root_url, headers, http_get_json)
    return {identified[0][0]: "loaded"} if len(identified) == 1 else {}


def read_no_load_states(
    _root_url: str, _headers: Mapping[str, str], _http_get_json: HttpGetJson
) -> dict[str, LoadState]:
    """Servidor genérico sin fuente de estado: no consulta nada y todo queda desconocido."""
    return {}


LM_STUDIO_STATE_TIMEOUT_S = 2.0
LM_STUDIO_MAX_MODELS = 256
_LM_STUDIO_NON_CHAT_TYPES = frozenset({"embedding", "embeddings"})
_LM_STUDIO_V0_STATES: dict[str, LoadState] = {"loaded": "loaded", "not-loaded": "unloaded"}


def _lm_studio_get(url: str, headers: Mapping[str, str], http_get_json: HttpGetJson) -> Any:
    try:
        return http_get_json(url, headers, LM_STUDIO_STATE_TIMEOUT_S)
    except (OSError, ValueError, RuntimeError):
        return None


def _lm_studio_chat_items(payload: Any, key: str) -> list[dict[str, Any]] | None:
    items = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None
    return [
        item
        for item in items[:LM_STUDIO_MAX_MODELS]
        if isinstance(item, dict)
        and str(item.get("type") or "llm").strip().lower() not in _LM_STUDIO_NON_CHAT_TYPES
    ]


def read_lm_studio_rest_states(
    root_url: str, headers: Mapping[str, str], http_get_json: HttpGetJson
) -> dict[str, LoadState]:
    """Lee el estado de carga de LM Studio: ``loaded_instances`` de la API REST v1, con fallback a v0.

    ``/api/v1/models`` (LM Studio 0.4.0+) marca cargado un modelo con ``loaded_instances`` no vacío;
    si esa ruta no existe o no trae ``models``, ``/api/v0/models`` (0.3.6+) informa ``state``
    (``loaded``/``not-loaded``). Los modelos de embeddings se omiten y las listas se acotan. Un fallo de
    lectura devuelve un dict vacío y un campo ausente ``unknown``: nunca lanza.
    """
    current = _lm_studio_chat_items(
        _lm_studio_get(f"{root_url}/api/v1/models", headers, http_get_json), "models"
    )
    if current is not None:
        states: dict[str, LoadState] = {}
        for item in current:
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            instances = item.get("loaded_instances")
            states[key] = (
                ("loaded" if instances else "unloaded") if isinstance(instances, list) else "unknown"
            )
        return states
    legacy = _lm_studio_chat_items(
        _lm_studio_get(f"{root_url}/api/v0/models", headers, http_get_json), "data"
    )
    if legacy is None:
        return {}
    return {
        str(item.get("id")).strip(): _LM_STUDIO_V0_STATES.get(str(item.get("state") or ""), "unknown")
        for item in legacy
        if str(item.get("id") or "").strip()
    }


LOAD_STATE_READERS: dict[str, LoadStateSourceReader] = {
    "openai_models_status": read_openai_models_status_states,
    "single_model": read_single_model_states,
    "none": read_no_load_states,
    "lm_studio_rest": read_lm_studio_rest_states,
}
"""Lector por ``model_state_source`` del perfil; otras rebanadas registran fuentes nuevas aquí."""


def read_load_states(
    account: Mapping[str, Any], profile: LocalRuntimeProfile, *, http_get_json: HttpGetJson
) -> dict[str, LoadState]:
    """Estado de carga por id de modelo según ``profile.model_state_source``; vacío si no se sabe.

    Despacha por ``LOAD_STATE_READERS`` con la URL raíz del servidor (sin ``/v1``), las cabeceras de
    autenticación permitidas y el getter; una fuente sin lector registrado queda desconocida.

    Raises:
        OSError, ValueError: propagados desde ``http_get_json``; ``LoadStateCache`` los vuelve desconocido.
    """
    reader = LOAD_STATE_READERS.get(profile.model_state_source)
    if reader is None:
        return {}
    root_url = server_root_url(str(account.get("baseUrl") or ""))
    return reader(root_url, _auth_headers(account), http_get_json)


def model_aliases_for(account: Mapping[str, Any]) -> dict[str, str]:
    """Última tabla alias → id canónico que ``openai_models_status`` observó en el servidor de la cuenta.

    Caché por proceso que cada lectura reemplaza (la refresca ``LOAD_STATE_CACHE``); vacía si el servidor no
    expone alias o todavía no se leyó. Nunca hace pedidos HTTP.
    """
    root_url = server_root_url(str(account.get("baseUrl") or ""))
    with _aliases_lock:
        return dict(_LISTED_ALIASES.get(root_url, {}))


def _read_account_load_states(account: Mapping[str, Any]) -> dict[str, LoadState]:
    """Lector por defecto: perfil de la entrada de catálogo de la cuenta y GET real acotado."""
    entry = catalog_entry_for_account(account)
    profile = entry.local_profile if entry is not None else None
    if profile is None:
        return {}
    return read_load_states(account, profile, http_get_json=default_http_get_json)


class LoadStateCache:
    """Caché por proceso del estado de carga: TTL corto, un refresco en vuelo por cuenta y espera acotada."""

    def __init__(
        self,
        *,
        ttl_s: float = LOAD_STATE_TTL_SECONDS,
        reader: LoadStateReader | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._reader = reader or _read_account_load_states
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, LoadState]]] = {}
        self._inflight: dict[str, threading.Event] = {}

    def get(
        self, account: Mapping[str, Any], *, max_wait_s: float = 1.0, allow_stale: bool = False
    ) -> dict[str, LoadState]:
        """Estado vigente de la cuenta; si venció, refresca en segundo plano y espera ``max_wait_s``.

        Sin una lectura vigente a tiempo devuelve ``{}`` (todo desconocido), nunca un estado vencido, salvo
        con ``allow_stale``: las vistas consultadas por polling reciben al instante la última lectura de la
        misma cuenta y URL mientras se refresca, sin esperar bajo el lock global del control-plane.
        """
        key = f"{account.get('providerId')}|{str(account.get('baseUrl') or '').rstrip('/')}"
        with self._lock:
            fresh = self._fresh(key)
            if fresh is not None:
                return fresh
            stale = self._entries.get(key) if allow_stale else None
            done = self._inflight.get(key)
            if done is None:
                done = threading.Event()
                self._inflight[key] = done
                threading.Thread(
                    target=self._refresh,
                    args=(key, dict(account), done),
                    daemon=True,
                    name=f"aido-load-state-{key}",
                ).start()
            if stale is not None:
                return dict(stale[1])
        done.wait(max(0.0, max_wait_s))
        with self._lock:
            return self._fresh(key) or {}

    def clear(self) -> None:
        """Olvida todas las lecturas (tests y cambios de configuración de la cuenta)."""
        with self._lock:
            self._entries.clear()

    def _fresh(self, key: str) -> dict[str, LoadState] | None:
        entry = self._entries.get(key)
        if entry is None or self._clock() - entry[0] >= self._ttl_s:
            return None
        return dict(entry[1])

    def _refresh(self, key: str, account: dict[str, Any], done: threading.Event) -> None:
        """Hilo de refresco: una falla del servidor o del parseo queda como estado desconocido y se registra."""
        try:
            states = self._reader(account)
        except Exception as error:
            logger.warning(
                "local_model_state.read_failed",
                extra={"providerId": account.get("providerId"), "errorType": error.__class__.__name__},
            )
            states = {}
        with self._lock:
            self._entries[key] = (self._clock(), states)
            self._inflight.pop(key, None)
        done.set()


LOAD_STATE_CACHE = LoadStateCache()
