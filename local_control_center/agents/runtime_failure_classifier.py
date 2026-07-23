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
            r"usage limit|quota (exceeded|exhausted)|out of credits|insufficient_quota", re.IGNORECASE
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
