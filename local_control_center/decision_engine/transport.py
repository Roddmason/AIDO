"""Transporte HTTPS Jev con DNS asyncio cancelable y destino público validado.

Resolver con dnspython evita el executor getaddrinfo que asyncio.run espera al cerrar.
La extensión pública sni_hostname conserva verificación TLS del dominio al conectar su IP:
https://www.python-httpx.org/advanced/extensions/#sni_hostname

@author Rodrigo Mason
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable

import httpx

JEV_HOST = "api.typesafe.ai"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
AddressResolver = Callable[[str, float], Awaitable[str]]


def _public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise httpx.ConnectError("dns_invalid_address") from None
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise httpx.ConnectError("dns_nonpublic_address")
    return address.compressed


async def resolve_jev_address(host: str, timeout: float) -> str:
    """Resuelve mediante sockets asyncio propios; cancelar el await cierra la consulta DNS."""
    try:
        import dns.asyncresolver
        import dns.exception
        import dns.nameserver
        import dns.resolver
    except ImportError:
        raise httpx.ConnectError("dns_dependency_unavailable") from None
    try:
        resolver = dns.asyncresolver.Resolver()
        # System resolver addresses may be private; only literal Do53 destinations are used.
        servers = []
        for server in resolver.nameservers:
            if isinstance(server, dns.nameserver.Do53Nameserver):
                server = server.address
            servers.append(str(ipaddress.ip_address(server)))
        resolver.nameservers = servers
        for record_type in ("A", "AAAA"):
            try:
                answer = await resolver.resolve(host, record_type, lifetime=timeout, search=False)
            except dns.resolver.NoAnswer:
                continue
            addresses = [_public_address(record.address) for record in answer]
            if addresses:
                return addresses[0]
    except dns.exception.Timeout:
        raise httpx.ConnectTimeout("dns_timeout") from None
    except (dns.exception.DNSException, ValueError, OSError):
        raise httpx.ConnectError("dns_resolution_failed") from None
    raise httpx.ConnectError("dns_no_address")


class JevAsyncTransport(httpx.AsyncBaseTransport):
    """Conecta una IP DNS validada conservando Host/SNI y sin proxies, redirecciones ni retries."""

    def __init__(
        self,
        *,
        timeout: float = 1.0,
        resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.timeout = timeout
        self.resolver = resolver or resolve_jev_address
        self.transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Resuelve sólo el dominio permitido y preserva TLS contra su nombre original."""
        if str(request.url) != JEV_URL:
            raise httpx.ConnectError("invalid_endpoint")
        address = _public_address(await self.resolver(JEV_HOST, self.timeout))
        headers = request.headers.copy()
        headers["Host"] = JEV_HOST
        resolved_request = httpx.Request(
            request.method,
            request.url.copy_with(host=address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": JEV_HOST},
        )
        if self.transport is None:
            self.transport = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
        return await self.transport.handle_async_request(resolved_request)

    async def aclose(self) -> None:
        """Cierra conexiones del transporte; el resolver no conserva tareas ni canales globales."""
        if self.transport is not None:
            await self.transport.aclose()
