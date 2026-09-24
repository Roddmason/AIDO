"""Clasifica la salida real de un runtime fallido en una causa accionable para el cliente.

Sin esto, un fallo de ejecución llega al operador como "runtime process exited with return
code 1": no dice qué pasó ni cómo arreglarlo. El stderr del CLI sí lo dice ("OAuth access token
has expired", "You've hit your usage limit... try again at Aug 14th"), pero se descartaba.

Dos invariantes que vienen de comportamiento observado, no de teoría:

- **Nunca clasificar por ``return_code``**: ``codex exec`` imprime el error de cuota y termina
  con código 0. El returncode es una señal de apoyo, jamás la condición de detección.
- **La evidencia se muestra y se persiste**: se redacta con la política de secretos del
  repositorio y se acota, porque termina en la UI y en el paquete de evidencia.

El módulo no traduce: devuelve una causa estable y la evidencia. La copia para el cliente vive
en el catálogo i18n indexada por esa causa, para que el gate bilingüe la gobierne.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.error import URLError

from local_control_center.agents.local_runtime_causes import LocalRuntimeCause
from local_control_center.agents.providers.http_transport import ResponseTooLargeError
from local_control_center.shared.redaction import redact_secrets

#: Causas estables de fallo de runtime. La UI mapea cada una a copia traducida y a su remediación.
RuntimeFailureCause = Literal[
    "auth_expired",
    "auth_missing",
    "quota_exhausted",
    "rate_limited",
    "provider_unreachable",
    "model_not_found",
    "unknown",
]

#: Tope de la evidencia que se persiste y se muestra; el stderr de un CLI puede ser enorme.
EVIDENCE_LIMIT = 1000

# Orden = prioridad: la primera causa que matchea gana. `auth_expired` va antes que `auth_missing`
# porque un token vencido también menciona "authenticate", y el remedio difiere (re-login vs login).
_CAUSE_PATTERNS: tuple[tuple[RuntimeFailureCause, re.Pattern[str]], ...] = (
    (
        "auth_expired",
        re.compile(
            r"access token has expired|re-?authenticate|invalid authentication credentials"
            r"|401 unauthorized|failed to authenticate",
            re.IGNORECASE,
        ),
    ),
    (
        "auth_missing",
        re.compile(r"not logged in|no credentials|unauthenticated|please (run )?login", re.IGNORECASE),
    ),
    (
        "quota_exhausted",
        re.compile(
            r"usage limit|hit your weekly limit|quota (exceeded|exhausted)|out of credits|insufficient_quota",
            re.IGNORECASE,
        ),
    ),
    ("rate_limited", re.compile(r"rate limit|too many requests|429", re.IGNORECASE)),
    (
        "provider_unreachable",
        re.compile(
            r"connection refused|connection reset|could not connect|failed to connect"
            r"|network is unreachable|name or service not known|timed? out",
            re.IGNORECASE,
        ),
    ),
    ("model_not_found", re.compile(r"model .{0,80}not found|unknown model|no such model", re.IGNORECASE)),
)

# "try again at Aug 14th, 2026 8:52 AM." -> el cliente necesita saber cuándo vuelve el servicio.
_RETRY_AFTER_PATTERN = re.compile(r"try again at\s+(?P<when>[^.\n]+)", re.IGNORECASE)

# Señales de que hubo un fallo aunque el proceso haya terminado con código 0.
_FAILURE_MARKERS = re.compile(r"^\s*(error|fatal)\b|\berror:", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class RuntimeFailure:
    """Fallo de runtime ya clasificado, listo para blocker, remediación y UI."""

    runtime_id: str
    cause: RuntimeFailureCause
    evidence: str
    return_code: int | None = None
    retry_after: str | None = None


def _clean_evidence(text: str) -> str:
    """Redacta secretos y acota la evidencia que se persiste y se muestra al cliente."""
    redacted = str(redact_secrets(text))
    collapsed = "\n".join(line.rstrip() for line in redacted.splitlines() if line.strip())
    return collapsed[:EVIDENCE_LIMIT]


def _select_evidence(combined: str, match: re.Match[str] | None) -> str:
    """Prioriza la línea que explica el fallo; si no hay match, conserva el final de la salida."""
    if match is not None:
        for line in combined.splitlines():
            if match.re.search(line):
                return line.strip()
    lines = [line for line in combined.splitlines() if line.strip()]
    return "\n".join(lines[-5:])


def classify_runtime_failure(
    *,
    runtime_id: str,
    return_code: int | None,
    stdout: str,
    stderr: str,
) -> RuntimeFailure | None:
    """Clasifica la salida de un runtime; devuelve ``None`` cuando no hubo fallo.

    Args:
        runtime_id: Runtime que produjo la salida (``claude_code_cli``, ``codex_cli``, ``ollama``…).
        return_code: Código de salida del proceso. Solo apoya la detección: hay CLIs que fallan con 0.
        stdout: Salida estándar capturada.
        stderr: Salida de error capturada; es donde los CLIs explican la causa.

    Returns:
        El fallo clasificado con su evidencia redactada, o ``None`` si la ejecución fue sana.
    """
    combined = f"{stderr}\n{stdout}".strip()
    if not combined:
        # Sin salida: solo es fallo si el proceso lo declaró con un código distinto de cero.
        if return_code not in (None, 0):
            return RuntimeFailure(
                runtime_id=runtime_id, cause="unknown", evidence="", return_code=return_code
            )
        return None

    matched_cause: RuntimeFailureCause | None = None
    matched: re.Match[str] | None = None
    for cause, pattern in _CAUSE_PATTERNS:
        found = pattern.search(combined)
        if found is not None:
            matched_cause, matched = cause, found
            break

    if matched_cause is None:
        # Sin causa conocida, solo es fallo si el proceso lo declaró (rc != 0) o gritó ERROR/FATAL.
        declared = return_code not in (None, 0) or _FAILURE_MARKERS.search(combined) is not None
        if not declared:
            return None
        matched_cause = "unknown"

    retry_after = None
    if (retry_match := _RETRY_AFTER_PATTERN.search(combined)) is not None:
        retry_after = retry_match.group("when").strip()

    return RuntimeFailure(
        runtime_id=runtime_id,
        cause=matched_cause,
        evidence=_clean_evidence(_select_evidence(combined, matched)),
        return_code=return_code,
        retry_after=retry_after,
    )


#: Tope de la evidencia de un fallo local que viaja en la razón del resultado.
LOCAL_EVIDENCE_LIMIT = 300

_LOCAL_LOAD_FAILED_PATTERN = re.compile(
    r"failed to load|error loading model|could not load model|unable to load model"
    r"|model load(?:ing)? failed",
    re.IGNORECASE,
)
_LOCAL_LOADING_PATTERN = re.compile(r"loading model|model is loading|model is being loaded", re.IGNORECASE)
_LOCAL_CONTEXT_PATTERN = re.compile(
    r"context (?:size|length|window)|maximum context length|exceed_context_size|context_length_exceeded",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocalModelFailure:
    """Fallo de un servidor de modelo local reducido a una causa estable y evidencia redactada."""

    cause: LocalRuntimeCause
    evidence: str


def _is_connection_failure(error: BaseException) -> bool:
    reason = error.reason if isinstance(error, URLError) else error
    return isinstance(reason, OSError) and not isinstance(reason, TimeoutError | ResponseTooLargeError)


def classify_local_model_error(
    *, error: BaseException, http_status: int | None, body: str = ""
) -> LocalModelFailure | None:
    """Clasifica el fallo de una llamada a un servidor de modelo local; ``None`` si no hay causa conocida.

    Orden: 401/403 -> ``local_auth_required``; texto de carga fallida -> ``local_model_load_failed``;
    503 o texto de carga en curso -> ``model_loading``; texto de contexto excedido ->
    ``context_length_exceeded``; sin respuesta HTTP y conexión rechazada o sin resolución ->
    ``local_server_unreachable``. Un timeout no se clasifica: no distingue un servidor caído de un
    modelo lento. La evidencia sale redactada y acotada a ``LOCAL_EVIDENCE_LIMIT``.
    """
    cause: LocalRuntimeCause | None = None
    if http_status in {401, 403}:
        cause = "local_auth_required"
    elif _LOCAL_LOAD_FAILED_PATTERN.search(body):
        cause = "local_model_load_failed"
    elif http_status == 503 or _LOCAL_LOADING_PATTERN.search(body):
        cause = "model_loading"
    elif _LOCAL_CONTEXT_PATTERN.search(body):
        cause = "context_length_exceeded"
    elif http_status is None and _is_connection_failure(error):
        cause = "local_server_unreachable"
    if cause is None:
        return None
    return LocalModelFailure(cause=cause, evidence=_clean_evidence(body)[:LOCAL_EVIDENCE_LIMIT])
