"""Fuente única de identidad de catálogo, localidad de red y costo self-hosted de una cuenta de proveedor.

Privacidad (`local_private`), habilitación (`runtime.local.enabled` frente a `runtime.remote.enabled`), costo
cero, clase de recursos y transporte de credenciales leen estas funciones puras en vez de reinterpretar la URL
cada uno. Invariante: ante la duda la cuenta es remota (R13); la metadata del cliente solo puede restringir
(`endpointKind="remote"`), nunca declarar una cuenta local. Una declaración local (WSL/Docker) solo cuenta si
la escribió el servidor y su host sigue resolviendo a una IP aceptada en cada evaluación.

@author Rodrigo Mason
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlparse

from .provider_catalog import ProviderCatalogEntry, provider_catalog_entry
from .runtime_provider_config import (
    DEFAULT_OLLAMA_BASE_URL,
    known_provider_default_base_url,
    runtime_provider_configuration,
    runtime_provider_configuration_for_account,
)

EndpointLocality = Literal["loopback", "declared_local", "remote"]
NetworkScope = Literal["loopback", "private_network", "declared_local", "public"]
HostResolver = Callable[[str], Sequence[str]]
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

SELF_HOSTED_PRICING_SOURCE = "local_runtime_cost_only"
DOCKER_HOST_ALIAS = "host.docker.internal"
LEGACY_OLLAMA_IDS = frozenset({"ollama", "local_ollama"})
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _metadata(account: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = account.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _legacy_ollama_default_base_url(account: Mapping[str, Any]) -> str:
    """URL que `OllamaProvider` resuelve para una cuenta Ollama integrada sin URL."""
    is_ollama_adapter = (
        str(account.get("providerFamily") or "") in LEGACY_OLLAMA_IDS
        or str(account.get("apiFormat") or "") == "ollama"
    )
    if str(account.get("providerId") or "") not in LEGACY_OLLAMA_IDS or not is_ollama_adapter:
        return ""
    configuration = runtime_provider_configuration("ollama")
    return (
        (configuration.value("baseUrl") if configuration else None)
        or os.environ.get("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or DEFAULT_OLLAMA_BASE_URL
    )


def effective_connection(account: Mapping[str, Any]) -> tuple[str, str]:
    """URL base y referencia de credencial que usará el adapter de la cuenta (vacías si no hay).

    Mismo orden que `ProviderAdapterFactory._connection_configuration`: la configuración por entorno de la
    cuenta canónica (`AIDO_*_BASE_URL`/`AIDO_*_API_KEY`) gana sobre lo persistido; luego la URL de la cuenta,
    el endpoint oficial de la familia y, para Ollama integrado, el default de `OllamaProvider`. Una cuenta con
    alcance de endpoint no hereda la configuración global (`runtime_provider_configuration_for_account`).
    """
    configuration = runtime_provider_configuration_for_account(account)
    provider_id = str(account.get("providerId") or "")
    provider_family = str(account.get("providerFamily") or "")
    family_default_base_url = (
        known_provider_default_base_url(provider_family)
        if str(account.get("apiFamily") or "") in {"chat_completions", "embeddings"}
        and (provider_id == provider_family or str(account.get("deploymentMode") or "") == "hosted_trial")
        else None
    )
    base_url = (
        (configuration.value("baseUrl") if configuration else None)
        or str(account.get("baseUrl") or "").strip()
        or family_default_base_url
        or _legacy_ollama_default_base_url(account)
    )
    credential_ref = (configuration.configured_env_ref("apiKey") if configuration else None) or str(
        account.get("credentialRef") or ""
    ).strip()
    return base_url, credential_ref


def _effective_base_url(account: Mapping[str, Any]) -> str:
    return effective_connection(account)[0]


def _host(account: Mapping[str, Any]) -> str:
    return (urlparse(_effective_base_url(account)).hostname or "").lower()


def _ip_address(host: str) -> IpAddress | None:
    """IP literal normalizada; una IPv6 que mapea una IPv4 se evalúa como la IPv4 que representa."""
    try:
        address = ipaddress.ip_address(str(host or "").strip().strip("[]"))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    address = _ip_address(host)
    return address is not None and address.is_loopback


def _is_private_literal(address: IpAddress) -> bool:
    return any(address in network for network in _PRIVATE_NETWORKS)


def default_host_resolver(host: str) -> list[str]:
    """Resuelve un nombre a sus IPs en texto; un fallo de resolución devuelve lista vacía (no acepta)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return sorted({str(info[4][0]) for info in infos})


def is_acceptable_declaration_host(host: str, *, resolver: HostResolver | None = None) -> bool:
    """Indica si un host puede declararse local: IP literal privada/link-local o `host.docker.internal`.

    El alias de Docker se resuelve en cada llamada (anti DNS rebinding) y solo se acepta si todas sus
    direcciones son privadas, link-local o loopback. Un nombre cualquiera nunca es declarable.
    """
    normalized = str(host or "").strip().lower().strip("[]")
    literal = _ip_address(normalized)
    if literal is not None:
        return _is_private_literal(literal)
    if normalized != DOCKER_HOST_ALIAS:
        return False
    resolved = [_ip_address(value) for value in (resolver or default_host_resolver)(normalized)]
    return bool(resolved) and all(
        address is not None and (address.is_loopback or _is_private_literal(address)) for address in resolved
    )


def _declaration_is_current(account: Mapping[str, Any], host: str, resolver: HostResolver | None) -> bool:
    declaration = account.get("localDeclaration")
    if not isinstance(declaration, Mapping):
        return False
    declared_host = str(declaration.get("host") or "").strip().lower().strip("[]")
    return (
        bool(declared_host)
        and declared_host == host
        and is_acceptable_declaration_host(host, resolver=resolver)
    )


def _forced_remote(account: Mapping[str, Any]) -> bool:
    return (
        str(account.get("providerType") or "") == "gateway"
        or str(_metadata(account).get("endpointKind") or "").strip().lower() == "remote"
    )


def endpoint_locality(
    account: Mapping[str, Any], *, resolver: HostResolver | None = None
) -> EndpointLocality:
    """Localidad para privacidad y habilitación.

    Orden: gateway o `endpointKind="remote"` ⇒ remoto siempre; host loopback ⇒ `loopback`; declaración del
    servidor vigente para ese host ⇒ `declared_local`; cualquier otro caso ⇒ remoto.
    """
    if _forced_remote(account):
        return "remote"
    host = _host(account)
    if _is_loopback_host(host):
        return "loopback"
    if host and _declaration_is_current(account, host, resolver):
        return "declared_local"
    return "remote"


def endpoint_network_scope(
    account: Mapping[str, Any], *, resolver: HostResolver | None = None
) -> NetworkScope:
    """Alcance de red para costo: como la localidad, pero separa una IP literal privada no declarada."""
    locality = endpoint_locality(account, resolver=resolver)
    if locality != "remote":
        return locality
    address = _ip_address(_host(account))
    return "private_network" if address is not None and _is_private_literal(address) else "public"


def is_loopback_endpoint(account: Mapping[str, Any]) -> bool:
    """Solo mira el host: la URL apunta a este equipo, sea cual sea el tipo o la marca de la cuenta."""
    return _is_loopback_host(_host(account))


def is_local_model_runtime(account: Mapping[str, Any], *, resolver: HostResolver | None = None) -> bool:
    """Cuenta `providerType=local` cuyo endpoint es loopback o está declarado local por el servidor."""
    return str(account.get("providerType") or "") == "local" and (
        endpoint_locality(account, resolver=resolver) != "remote"
    )


def catalog_entry_for_account(account: Mapping[str, Any]) -> ProviderCatalogEntry | None:
    """Entrada de catálogo de la cuenta: el campo del servidor y, si falta, los fallbacks legados.

    Fallbacks en orden: id o alias canónico del `providerId` (una cuenta `llama_cpp` legada tiene familia
    `openai_compatible` y no debe caer en la entrada genérica); `apiFormat=ollama` ⇒ entrada `ollama` (cuentas
    creadas por el router Ollama, que no escriben el campo); y por último `providerFamily`. Un id guardado
    desconocido no cae a fallback.
    """
    stored = str(account.get("providerCatalogId") or "").strip()
    if stored:
        return provider_catalog_entry(stored)
    entry = provider_catalog_entry(str(account.get("providerId") or "").strip())
    if entry is not None:
        return entry
    if str(account.get("apiFormat") or "") == "ollama":
        return provider_catalog_entry("ollama")
    return provider_catalog_entry(str(account.get("providerFamily") or "").strip())


def is_self_hosted_inference(account: Mapping[str, Any], *, resolver: HostResolver | None = None) -> bool:
    """Inferencia propia de costo marginal cero: catálogo local, `providerType=local` y red no pública.

    Nunca para `gateway`/`api` ni para `endpointKind="remote"`; una IP literal privada de la LAN sí cuenta,
    un nombre DNS o una IP pública no (evita que una URL mal tipeada hacia un servicio pagado quede gratis).
    """
    if str(account.get("providerType") or "") != "local":
        return False
    if str(_metadata(account).get("endpointKind") or "").strip().lower() == "remote":
        return False
    entry = catalog_entry_for_account(account)
    if entry is None or entry.pricing_source != SELF_HOSTED_PRICING_SOURCE:
        return False
    return endpoint_network_scope(account, resolver=resolver) != "public"


def credential_transport_allowed(account: Mapping[str, Any], *, resolver: HostResolver | None = None) -> bool:
    """Un bearer solo viaja por `https`, a un host loopback o a un host declarado local por el servidor.

    El bearer y la URL son los que resuelve el adapter: de la cuenta o de la configuración por entorno.
    """
    base_url, credential_ref = effective_connection(account)
    if not credential_ref:
        return True
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return True
    host = (parsed.hostname or "").lower()
    return _is_loopback_host(host) or (bool(host) and _declaration_is_current(account, host, resolver))


def server_root_url(base_url: str) -> str:
    """Raíz del servidor: quita la barra final y el sufijo `/v1` del `baseUrl` OpenAI-compatible."""
    candidate = str(base_url or "").strip().rstrip("/")
    return candidate[: -len("/v1")] if candidate.endswith("/v1") else candidate
