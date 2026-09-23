"""Proveedores de búsqueda web del ResearchAgent: SearXNG propio y diagnóstico de bloqueo.

El ResearchAgent descubre fuentes con el proveedor configurado en ``research.webSearch.provider``.
``searxng`` consulta una instancia propia por su API JSON (``GET {baseUrl}/search?q=...&format=json``,
que exige ``search.formats: [html, json]`` en la configuración del contenedor); ``duckduckgo``
conserva el scraping HTML previo. Una respuesta distinta de 200, un cuerpo que no es la API JSON o
una página de desafío se reportan como ``research_provider_blocked`` (el proveedor bloqueó la
consulta), nunca como "sin fuentes"; una caída de red o timeout es ``ResearchWebSearchError``, tipada
para que el runner responda ``research_blocked`` en vez de reventar el job. Nunca se evade la detección
de bots de un tercero cambiando el User-Agent. La URL base sólo puede apuntar a loopback; las fuentes
que el agente descarga, en cambio, sólo pueden apuntar a hosts públicos (``assert_public_source_url``),
revalidando cada redirect y conectando a la misma IP que se validó en cada salto. Este módulo es la
única fuente de las constantes y del fetch acotado que comparten ambos proveedores.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Callable
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
    urlopen,
)

from local_control_center.process_supervision.context import assert_external_boundary
from local_control_center.shared.redaction import redact_secrets

WebSearchProvider = Callable[[str, int], list[dict[str, Any]]]
SEARXNG_PROVIDER = "searxng"
DUCKDUCKGO_PROVIDER = "duckduckgo"
DEFAULT_SEARXNG_BASE_URL = "http://127.0.0.1:8888"
DUCKDUCKGO_HTML_ENDPOINT = "https://duckduckgo.com/html/"
PROVIDER_BLOCKED_CODE = "research_provider_blocked"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SEARCH_USER_AGENT = "AIDO-ResearchAgent/1.0"
SEARCH_TIMEOUT_SECONDS = 10
MAX_SEARCH_RESPONSE_BYTES = 512_000
SEARCH_CANDIDATE_MULTIPLIER = 4
MIN_SEARCH_CANDIDATES = 10
MAX_SEARCH_CANDIDATES = 50


class ResearchWebSearchError(ValueError):
    """La búsqueda web no pudo completarse: red, timeout, límite de bytes o consulta vacía."""


class ResearchProviderBlockedError(ResearchWebSearchError):
    """El proveedor de búsqueda rechazó la consulta: HTTP distinto de 200 o página de desafío."""

    code = PROVIDER_BLOCKED_CODE

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"The web search provider blocked the query ({provider}: {detail}).")
        self.provider = provider
        self.detail = detail


class ResearchSourceUrlError(ValueError):
    """La URL de una fuente, o el destino de un redirect, no es un host público y no se descarga."""


def _unmapped(address: IPv4Address | IPv6Address) -> IPv4Address | IPv6Address:
    """IPv4 real de una IPv6 mapeada (``::ffff:a.b.c.d``); la misma dirección en otro caso."""
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _literal_address(host: str) -> IPv4Address | IPv6Address | None:
    """Dirección del host cuando es una IP literal; ``None`` si es un nombre."""
    try:
        return _unmapped(ip_address(host))
    except ValueError:
        return None


def _host_addresses(host: str, port: int) -> list[IPv4Address | IPv6Address]:
    """Direcciones a las que conectaría la descarga, en el orden del resolver; vacío si no resuelve."""
    literal = _literal_address(host)
    if literal is not None:
        return [literal]
    if host == "localhost" or host.endswith(".localhost"):
        return [ip_address("127.0.0.1")]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    resolved = (_unmapped(ip_address(str(info[4][0]).split("%", 1)[0])) for info in infos)
    return list(dict.fromkeys(resolved))


def _public_addresses(host: str, port: int) -> list[IPv4Address | IPv6Address]:
    """Direcciones del host si todas son globales; falla cerrado si no resuelve o alguna no lo es."""
    addresses = _host_addresses(host.lower(), port)
    if not addresses:
        raise ResearchSourceUrlError(f"ResearchAgent source host does not resolve: {host}.")
    if any(not address.is_global for address in addresses):
        raise ResearchSourceUrlError(f"ResearchAgent source URL must target a public host: {host}.")
    return addresses


def _connect_to_public_address(
    address: tuple[str, int], timeout: Any, source_address: Any = None
) -> socket.socket:
    """Conecta a una IP recién validada: la resolución que se valida es la misma a la que se conecta."""
    host, port = address
    errors: list[OSError] = []
    for public in _public_addresses(host, port):
        try:
            return socket.create_connection((str(public), port), timeout, source_address)
        except OSError as error:
            errors.append(error)
    raise errors[-1]


class _PinnedHTTPConnection(HTTPConnection):
    """HTTP que conecta a la IP pública validada en el mismo paso, sin una segunda resolución."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_to_public_address


class _PinnedHTTPSConnection(HTTPSConnection):
    """HTTPS fijado a la IP validada; SNI, certificado y ``Host`` siguen usando el nombre original."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_to_public_address


class _PinnedHTTPHandler(HTTPHandler):
    """Abre cada salto HTTP (incluidos los redirects) con ``_PinnedHTTPConnection``."""

    def http_open(self, req: Request) -> Any:
        """Descarga HTTP conectando sólo a la IP pública validada."""
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(HTTPSHandler):
    """Abre cada salto HTTPS (incluidos los redirects) con ``_PinnedHTTPSConnection``."""

    def https_open(self, req: Request) -> Any:
        """Descarga HTTPS conectando sólo a la IP pública validada."""
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def assert_public_source_url(url: str) -> None:
    """Rechaza fuentes que no sean http(s) hacia hosts públicos.

    Bloquea credenciales embebidas, nombres que no resuelven y todo destino cuya dirección literal o
    resuelta (A/AAAA) no sea global: loopback, RFC 1918, link-local (``169.254.169.254``, metadata de
    nube), CGNAT, ULA y reservadas, incluida la IPv4 mapeada en IPv6. Es la validación previa; la
    conexión real vuelve a resolver, valida y conecta a esa misma IP (``_PinnedHTTPConnection``), así
    que un DNS rebinding entre ambas resoluciones no alcanza un host interno.
    """
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ResearchSourceUrlError("ResearchAgent source URL must be absolute HTTP(S).")
    if parsed.username or parsed.password:
        raise ResearchSourceUrlError("ResearchAgent source URL must not contain credentials.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ResearchSourceUrlError("ResearchAgent source URL has an invalid port.") from error
    _public_addresses(parsed.hostname, port)


class PublicRedirectHandler(HTTPRedirectHandler):
    """Revalida cada redirect con ``assert_public_source_url`` antes de seguirlo."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        """Sigue el redirect sólo si el destino es público; si no, aborta la descarga."""
        assert_public_source_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_SOURCE_OPENER = build_opener(
    ProxyHandler({}), PublicRedirectHandler(), _PinnedHTTPHandler(), _PinnedHTTPSHandler()
)
"""Opener sin proxies del entorno: con proxy la IP conectada sería la del proxy y el fijado no protegería nada."""
_STANDARD_URLOPEN = urlopen


def open_public_source(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    urlopen_override: Callable[..., Any] | None = None,
) -> Any:
    """Abre una fuente pública: valida el destino inicial y cada redirect antes de conectar.

    ``urlopen_override`` conserva la costura de los tests que parchean ``urlopen`` del módulo que
    llama (mismo patrón que ``agents/providers/http_transport.py``); la validación corre igual.
    """
    assert_public_source_url(url)
    request = Request(url, headers=headers)
    if urlopen_override is not None and urlopen_override is not _STANDARD_URLOPEN:
        return urlopen_override(request, timeout=timeout)
    return _PUBLIC_SOURCE_OPENER.open(request, timeout=timeout)


def validate_search_base_url(value: str) -> str:
    """Valida la URL base del proveedor: http(s), puerto válido, sin credenciales ni query, y sólo loopback.

    ``parsed.port`` lanza ``ValueError`` si el puerto no es numérico o está fuera de 0-65535.
    """
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("research.webSearch.baseUrl must be an absolute http(s) URL.")
    try:
        port = parsed.port
    except ValueError:
        port = 0
    if port == 0:
        raise ValueError("research.webSearch.baseUrl has an invalid port.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("research.webSearch.baseUrl must not carry credentials, query or fragment.")
    if parsed.hostname.lower() not in LOOPBACK_HOSTS:
        raise ValueError("research.webSearch.baseUrl must point to a loopback host.")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def publisher_from_url(url: str) -> str:
    """Host del resultado sin ``www.``, usado como publisher de la fuente."""
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def search_candidate_limit(max_sources: int) -> int:
    """Candidatos a pedir al proveedor: margen sobre ``max_sources`` para filtrar por confianza."""
    return min(MAX_SEARCH_CANDIDATES, max(max_sources * SEARCH_CANDIDATE_MULTIPLIER, MIN_SEARCH_CANDIDATES))


def redacted_search_query(query: str) -> str:
    """Consulta sin secretos; falla si no queda nada que buscar tras la redacción."""
    safe_query = str(redact_secrets(query)).strip()
    if not safe_query or safe_query == "[redacted]":
        raise ResearchWebSearchError("ResearchAgent web search query is empty after secret redaction.")
    return safe_query


def fetch_search_response(
    request: Request,
    *,
    provider: str,
    opener: Callable[..., Any] = urlopen,
) -> tuple[bytes, str]:
    """Ejecuta la consulta acotada y devuelve ``(cuerpo, charset)``.

    HTTP distinto de 200 ⇒ ``ResearchProviderBlockedError``; red, timeout o cuerpo sobre el límite ⇒
    ``ResearchWebSearchError``. La red incluye todo ``OSError`` (``urllib`` no envuelve el que ocurre
    al leer la respuesta, p. ej. ``ConnectionResetError``) y ``HTTPException`` (``RemoteDisconnected``,
    ``IncompleteRead``). ``opener`` conserva la costura de los tests que parchean ``urlopen`` del
    módulo que llama.
    """
    try:
        assert_external_boundary()
        with opener(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            content = response.read(MAX_SEARCH_RESPONSE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        error.close()
        raise ResearchProviderBlockedError(provider, f"HTTP {error.code}") from error
    except (OSError, HTTPException) as error:
        raise ResearchWebSearchError(f"ResearchAgent web search failed: {error}") from error
    if status != 200:
        raise ResearchProviderBlockedError(provider, f"HTTP {status}")
    if len(content) > MAX_SEARCH_RESPONSE_BYTES:
        raise ResearchWebSearchError("ResearchAgent web search response exceeds the fetch limit.")
    return content, charset


def _searxng_sources(results: list[Any], *, limit: int) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "url": url,
                "title": str(result.get("title") or "").strip() or publisher_from_url(url),
                "publisher": publisher_from_url(url),
                "sourceType": "web_search",
            }
        )
        if len(sources) >= limit:
            break
    return sources


def _searxng_search_url(root: str, query: str) -> str:
    return f"{root}/search?{urlencode({'q': query, 'format': 'json'})}"


def searxng_web_search_provider(base_url: str) -> WebSearchProvider:
    """Construye el proveedor SearXNG para una instancia propia en loopback."""
    root = validate_search_base_url(base_url)

    def search(query: str, max_sources: int) -> list[dict[str, Any]]:
        request = Request(
            _searxng_search_url(root, redacted_search_query(query)),
            headers={"User-Agent": SEARCH_USER_AGENT, "Accept": "application/json"},
        )
        content, charset = fetch_search_response(request, provider=SEARXNG_PROVIDER)
        try:
            document = json.loads(content.decode(charset, errors="replace"))
        except json.JSONDecodeError as error:
            raise ResearchProviderBlockedError(
                SEARXNG_PROVIDER, "response is not the JSON API; enable search.formats json"
            ) from error
        results = document.get("results") if isinstance(document, dict) else None
        if not isinstance(results, list):
            raise ResearchProviderBlockedError(SEARXNG_PROVIDER, "response without results")
        return _searxng_sources(results, limit=search_candidate_limit(max_sources))

    return search


def _configured_provider(connection: sqlite3.Connection, *, project_id: str | None) -> tuple[str, str]:
    """Proveedor y URL base efectivos con precedencia proyecto > general > default."""
    from local_control_center.settings.resolver import resolve_setting_value

    provider = resolve_setting_value(
        connection=connection, key="research.webSearch.provider", project_id=project_id
    )
    base_url = resolve_setting_value(
        connection=connection, key="research.webSearch.baseUrl", project_id=project_id
    )
    return str(provider or SEARXNG_PROVIDER), str(base_url or DEFAULT_SEARXNG_BASE_URL)


def configured_web_search_provider(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    duckduckgo: WebSearchProvider,
) -> WebSearchProvider:
    """Resuelve el proveedor configurado; una URL base inválida guardada bloquea, no revienta el job."""
    provider, base_url = _configured_provider(connection, project_id=project_id)
    if provider == DUCKDUCKGO_PROVIDER:
        return duckduckgo
    try:
        return searxng_web_search_provider(base_url)
    except ValueError as error:
        raise ResearchWebSearchError(f"ResearchAgent web search is misconfigured: {error}") from error


def configured_search_probe_url(connection: sqlite3.Connection, *, project_id: str | None) -> str:
    """Endpoint que sondea ``check_network_access``: el mismo proveedor que usará el ResearchAgent."""
    provider, base_url = _configured_provider(connection, project_id=project_id)
    if provider == DUCKDUCKGO_PROVIDER:
        return DUCKDUCKGO_HTML_ENDPOINT
    return _searxng_search_url(validate_search_base_url(base_url), "ping")
