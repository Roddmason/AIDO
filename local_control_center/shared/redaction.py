"""Saneamiento de secretos antes de persistir o exportar payloads (control de seguridad).

Invariante: ningún valor que pase por aquí debe contener credenciales en claro al
salir. Redacta tanto por nombre de clave sospechosa (api_key, token, secret...) como
por patrón de valor (claves OpenAI, Bearer, tokens de GitHub/GitLab/Slack, AWS...),
preservando contadores de tokens y campos de estado que no son secretos. No lanza:
ante valores no reconocidos los devuelve sin tocar, por lo que la cobertura del patrón
es la última línea de defensa. Todo evento/auditoría debe redactarse antes de escribirse.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

SECRET_KEY_PATTERN = re.compile(r"(api[_-]?key|authorization|credential|secret|token)", re.I)
SECRET_VALUE_PATTERN = re.compile(
    r"("
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"Bearer\s+[A-Za-z0-9._-]+|"
    r"ghp_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"password\s*=\s*[^&\s]+|"
    r"[a-z][a-z0-9+.\-]{0,30}://[^\s:@/]{1,256}:[^\s@/]{1,256}@[^\s]{1,256}|"
    r"-----BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY-----[\s\S]{1,10000}?-----END[A-Z0-9 ]{0,40}PRIVATE KEY-----"
    r")",
    re.I,
)
GIT_REMOTE_LINE_PATTERN = re.compile(r"(?m)^(?P<prefix>\S+\s+)(?P<url>\S+)(?P<suffix>\s+\((?:fetch|push)\))$")


def _strip_url_userinfo(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or "@" not in parsed.netloc:
        return value
    host = parsed.hostname or ""
    if not host:
        return "[redacted]"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _sanitize_git_remote_lines(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{_strip_url_userinfo(match.group('url'))}{match.group('suffix')}"

    return GIT_REMOTE_LINE_PATTERN.sub(replace, value)


def redact_secrets(value: Any, *, key: str = "") -> Any:
    """Reemplaza secretos por ``[redacted]`` recursivamente, conservando contadores y estados no sensibles.

    Args:
        value: Dato a sanear; recorre dicts y listas y aplica el patrón de valor a strings.
        key: Nombre de la clave contenedora; activa la redacción por clave sospechosa y las
            excepciones de contadores de tokens y campos ``*_status``.
    """
    if key.lower() in {"token_status", "tokenstatus", "cost_status", "coststatus"}:
        return value
    if key.lower().endswith(("tokens", "_tokens", "token_count")) and isinstance(value, int | float):
        return value
    if SECRET_KEY_PATTERN.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            item_key: redact_secrets(item_value, key=str(item_key)) for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_PATTERN.sub("[redacted]", _sanitize_git_remote_lines(value))
    return value
