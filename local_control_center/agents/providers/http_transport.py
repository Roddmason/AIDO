"""Credential-safe urllib transport primitives shared by model providers.

@author Rodrigo Mason
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from local_control_center.process_supervision.context import assert_external_boundary

UrlopenCallable = Callable[..., Any]
_STANDARD_URLOPEN = urllib.request.urlopen


class FailClosedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject every HTTP redirect before urllib can replay headers or rewrite a POST."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Return no follow-up request for every redirect status and destination."""


_FAIL_CLOSED_OPENER = urllib.request.build_opener(FailClosedRedirectHandler())


def urlopen_fail_closed(
    request: urllib.request.Request,
    *,
    timeout: float,
    urlopen_override: UrlopenCallable | None = None,
):
    """Open one request without redirects, retaining explicit test injection seams."""
    assert_external_boundary()
    if urlopen_override is not None and urlopen_override is not _STANDARD_URLOPEN:
        return urlopen_override(request, timeout=timeout)
    return _FAIL_CLOSED_OPENER.open(request, timeout=timeout)


DEFAULT_CHAT_TIMEOUT_SECONDS = 60
"""Timeout de chat solo para llamadores que no declaran presupuesto (gateway, sondas)."""

MAX_PROVIDER_RESPONSE_BYTES = 16 * 1024 * 1024
"""Tope de cuerpo aceptado de cualquier proveedor: un servidor local no es de confianza."""

ERROR_BODY_EXCERPT_BYTES = 4096


class ResponseTooLargeError(OSError):
    """El proveedor anunció o envió un cuerpo mayor que el tope; se trata como fallo de transporte."""


def read_bounded(response: Any, *, limit: int = MAX_PROVIDER_RESPONSE_BYTES) -> bytes:
    """Lee el cuerpo sin exceder ``limit`` bytes.

    Rechaza antes de leer cuando ``Content-Length`` declara más que el tope y, si el servidor
    no lo declara o miente, lee como máximo ``limit + 1`` bytes para detectar el exceso.

    Raises:
        ResponseTooLargeError: si el cuerpo supera ``limit``.
    """
    headers = getattr(response, "headers", None)
    declared = str(headers.get("Content-Length") or "").strip() if headers is not None else ""
    if declared.isdigit() and int(declared) > limit:
        raise ResponseTooLargeError(f"provider_response_too_large: declared {declared} bytes over {limit}")
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ResponseTooLargeError(f"provider_response_too_large: more than {limit} bytes")
    return body


def http_error_excerpt(error: urllib.error.HTTPError, *, limit: int = ERROR_BODY_EXCERPT_BYTES) -> str:
    """Lee un extracto acotado del cuerpo de un ``HTTPError`` y cierra la respuesta.

    El texto no está redactado: el llamador debe pasarlo por la política de secretos antes de
    persistirlo o mostrarlo.
    """
    try:
        raw = error.read(limit)
    except (AttributeError, OSError, ValueError):
        raw = b""
    finally:
        error.close()
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else ""
