"""Detección best-effort de servidores de inferencia locales en puertos conocidos de 127.0.0.1.

Solo sondea loopback fijo con ``GET``, sin credenciales, sin host del cliente y sin redirecciones, con
timeout corto y lectura y listas acotadas. La firma identifica llama.cpp (``owned_by=llamacpp`` o
``/props``), vLLM (``owned_by=vllm``) y LM Studio (``/api/v1/models`` o ``/api/v0/models``); un
``/v1/models`` sin firma conocida se sugiere como servidor genérico solo con confirmación del operador.
Nunca crea cuentas: devuelve sugerencias para el asistente.

@author Rodrigo Mason
"""

from __future__ import annotations

import http.client
import json
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal
from urllib.parse import urlparse

from local_control_center.agents.endpoint_locality import is_loopback_endpoint
from local_control_center.agents.providers.http_transport import urlopen_fail_closed

DISCOVERY_HOST = "127.0.0.1"
DISCOVERY_PORTS: tuple[int, ...] = (8080, 8082, 1234, 8000, 1337, 5001)
DISCOVERY_TIMEOUT_S = 2.0
DISCOVERY_MAX_BODY_BYTES = 256 * 1024
DISCOVERY_MAX_MODELS = 64
DISCOVERY_MAX_MODEL_ID_CHARS = 256
DiscoveredServer = Literal["llama_cpp", "lm_studio", "vllm", "unknown_openai_compatible"]
JsonGetter = Callable[[str], Any]
_CATALOG_BY_SERVER: dict[str, str] = {
    "llama_cpp": "llama_cpp",
    "lm_studio": "lm_studio",
    "vllm": "vllm",
    "unknown_openai_compatible": "local_openai_compatible",
}
_LM_STUDIO_SIGNATURES = (("/api/v1/models", "models"), ("/api/v0/models", "data"))


def bounded_get_json(url: str) -> Any:
    """``GET`` sin credenciales ni redirecciones; ``None`` ante error, estado no 200, cuerpo excesivo o JSON inválido."""
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen_fail_closed(request, timeout=DISCOVERY_TIMEOUT_S) as response:
            if response.status != 200:
                return None
            raw = response.read(DISCOVERY_MAX_BODY_BYTES + 1)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if len(raw) > DISCOVERY_MAX_BODY_BYTES:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _model_items(payload: Any) -> list[dict[str, Any]] | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    return [item for item in data[:DISCOVERY_MAX_MODELS] if isinstance(item, dict)]


def identify_server(root_url: str, get_json: JsonGetter) -> tuple[DiscoveredServer, list[str]] | None:
    """Identifica el servidor de ``root_url`` por firma; ``None`` si no expone un ``/v1/models`` legible."""
    items = _model_items(get_json(f"{root_url}/v1/models"))
    if items is None:
        return None
    models = [
        str(item.get("id")).strip()[:DISCOVERY_MAX_MODEL_ID_CHARS]
        for item in items
        if str(item.get("id") or "").strip()
    ]
    owners = {str(item.get("owned_by") or "").strip().lower() for item in items}
    if "llamacpp" in owners:
        return "llama_cpp", models
    if "vllm" in owners:
        return "vllm", models
    props = get_json(f"{root_url}/props")
    if isinstance(props, dict) and ("role" in props or "default_generation_settings" in props):
        return "llama_cpp", models
    for path, key in _LM_STUDIO_SIGNATURES:
        payload = get_json(f"{root_url}{path}")
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return "lm_studio", models
    return "unknown_openai_compatible", models


def _configured_loopback_ports(base_urls: Iterable[str]) -> set[int]:
    """Puertos de las cuentas en loopback según ``is_loopback_endpoint`` (fuente única de Task 1)."""
    ports: set[int] = set()
    for base_url in base_urls:
        parsed = urlparse(str(base_url or "").strip())
        if not is_loopback_endpoint({"baseUrl": str(base_url or "")}):
            continue
        try:
            port = parsed.port
        except ValueError:
            continue
        ports.add(port or (443 if parsed.scheme == "https" else 80))
    return ports


def discover_local_runtimes(
    *,
    configured_base_urls: Iterable[str],
    ports: Sequence[int] | None = None,
    get_json: JsonGetter | None = None,
) -> list[dict[str, Any]]:
    """Sondea en paralelo los puertos de ``DISCOVERY_PORTS`` en 127.0.0.1 y devuelve sugerencias.

    ``alreadyConfigured`` marca un puerto que ya usa una cuenta en loopback (``localhost`` incluido);
    ``requiresConfirmation`` marca un servidor sin firma conocida.
    """
    scanned = list(DISCOVERY_PORTS if ports is None else ports)
    if not scanned:
        return []
    getter = bounded_get_json if get_json is None else get_json
    configured = _configured_loopback_ports(configured_base_urls)
    roots = [f"http://{DISCOVERY_HOST}:{port}" for port in scanned]
    with ThreadPoolExecutor(max_workers=len(roots)) as pool:
        results = list(pool.map(lambda root: identify_server(root, getter), roots))
    suggestions: list[dict[str, Any]] = []
    for port, root, result in zip(scanned, roots, results, strict=True):
        if result is None:
            continue
        server, models = result
        suggestions.append(
            {
                "catalogId": _CATALOG_BY_SERVER[server],
                "baseUrl": f"{root}/v1",
                "server": server,
                "models": models,
                "alreadyConfigured": port in configured,
                "requiresConfirmation": server == "unknown_openai_compatible",
            }
        )
    return suggestions
