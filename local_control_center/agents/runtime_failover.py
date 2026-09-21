"""Clasificación de fallas de runtime y política de reintento en otro proveedor.

Un fallo de transporte (conexión caída, 5xx) o de cuota (429, crédito agotado) dice algo sobre el
proveedor, no sobre el trabajo: reintentarlo en otro runtime es correcto. Un fallo semántico (el
modelo violó el contrato, el JSON no valida) dice algo sobre la respuesta, y reintentarlo en otro
proveedor solo gasta dinero repitiendo el mismo error. Este módulo separa ambos casos y decide si
un candidato de reemplazo es aceptable bajo el tope de costo del rol.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

MAX_FAILOVER_ATTEMPTS = 2

_QUOTA_MARKERS = re.compile(
    r"429|rate[ _-]?limit|usage[ _-]?limit|too many requests|quota|insufficient_quota|"
    r"resource_exhausted|billing|credit|out of tokens|hit your weekly limit",
    re.IGNORECASE,
)
_TRANSPORT_MARKERS = re.compile(
    r"\b5\d\d\b|connection (refused|reset|aborted)|timed out|timeout|unreachable|"
    r"temporarily unavailable|bad gateway|service unavailable|broken pipe",
    re.IGNORECASE,
)
_SEMANTIC_MARKERS = re.compile(
    r"contract|schema|validation|must be a json|is not valid json|must be one of|"
    r"required field|unexpected token",
    re.IGNORECASE,
)
_TRANSPORT_TYPES = (ConnectionError, TimeoutError, OSError)
_SEMANTIC_TYPES = (ValueError, TypeError, KeyError)


class FailureClass(StrEnum):
    """Naturaleza de una falla de ejecución, que decide si el failover es legítimo."""

    QUOTA = "quota"
    TRANSPORT = "transport"
    AUTHENTICATION = "authentication"
    PROVIDER_REQUEST = "provider_request"
    SEMANTIC = "semantic"
    UNKNOWN = "unknown"


#: Solo estas clases justifican reintentar en otro proveedor.
FAILOVER_ELIGIBLE = frozenset(
    {FailureClass.QUOTA, FailureClass.TRANSPORT, FailureClass.AUTHENTICATION, FailureClass.PROVIDER_REQUEST}
)


def looks_like_quota_exhaustion(output: str) -> bool:
    """Indica si la salida de un runtime declara que su cuota está agotada.

    Un CLI no responde 429: agota la cuota recién al ejecutar, escribe el aviso en texto plano
    ("hit your usage limit") y termina con rc=1. Por eso el veredicto se lee del texto emitido y
    no del código de salida, que es el mismo de cualquier otro fallo.
    """
    return bool(_QUOTA_MARKERS.search(output or ""))


def classify_runtime_failure(error: BaseException) -> FailureClass:
    """Clasifica una excepción de ejecución para decidir si corresponde failover.

    La cuota se evalúa antes que el transporte porque un 429 suele viajar dentro de un
    ``HTTPError``, que también es ``OSError``. Lo semántico se evalúa antes que el fallback para
    que un ``ValueError`` de contrato nunca termine clasificado como desconocido y reintentado.

    Returns:
        La clase de falla; cuota, transporte y rechazos HTTP permiten otro candidato.
    """
    message = str(error)
    status = next(
        (
            value
            for name in ("http_status", "status_code", "status", "code")
            if isinstance(value := getattr(error, name, None), int)
        ),
        None,
    )
    if status is None:
        match = re.search(r"\bhttp(?:_status\s*=\s*|\s+)([45]\d\d)\b", message, re.IGNORECASE)
        status = int(match.group(1)) if match else None
    if status in {401, 403}:
        return FailureClass.AUTHENTICATION
    if status in {400, 404, 410, 422}:
        return FailureClass.PROVIDER_REQUEST
    if status == 429 or looks_like_quota_exhaustion(message):
        return FailureClass.QUOTA
    if _SEMANTIC_MARKERS.search(message):
        return FailureClass.SEMANTIC
    if isinstance(error, _TRANSPORT_TYPES) or _TRANSPORT_MARKERS.search(message):
        return FailureClass.TRANSPORT
    if isinstance(error, _SEMANTIC_TYPES):
        return FailureClass.SEMANTIC
    return FailureClass.UNKNOWN


def should_failover(failure: FailureClass) -> bool:
    """Indica si una falla justifica reintentar en otro proveedor."""
    return failure in FAILOVER_ELIGIBLE


def exclusion_for(failure: FailureClass, *, provider_id: str, model: str) -> dict[str, Any]:
    """Construye la exclusión a aplicar tras una falla.

    La cuota se agota por cuenta, no por modelo, así que excluye el proveedor entero; una falla de
    transporte puede ser de un modelo puntual y solo excluye ese par.
    """
    if failure in {FailureClass.QUOTA, FailureClass.AUTHENTICATION}:
        return {"provider": provider_id, "model": "*"}
    return {"provider": provider_id, "model": model or "*"}


def is_affordable_candidate(
    decision: dict[str, Any], *, requires_approval_over_usd: float | None, allow_unknown_cost: bool = False
) -> tuple[bool, str]:
    """Decide si un candidato de reemplazo puede ejecutarse sin pedir aprobación humana.

    Sin umbral declarado sólo se acepta costo conocido gratuito. El costo desconocido
    requiere autorización explícita de la política y un umbral positivo; conserva su
    condición desconocida y no se presenta como gratuito ni como acotado por ese umbral.

    Returns:
        ``(aceptable, motivo)``; el motivo describe el rechazo cuando no lo es.
    """
    if (
        decision.get("estimatedCostUsd") is None
        or str(decision.get("costEstimateSource") or "") == "unknown_price"
    ):
        if allow_unknown_cost and requires_approval_over_usd is not None and requires_approval_over_usd > 0:
            return True, "unknown_cost_explicitly_allowed"
        return False, "unknown_price"
    estimated = float(decision["estimatedCostUsd"])
    cost_tier = str(decision.get("costTier") or "")
    is_free = estimated <= 0.0 or cost_tier in {"free", "local"}
    if requires_approval_over_usd is None:
        return (True, "free") if is_free else (False, "no_cost_cap_in_role_policy")
    if estimated <= float(requires_approval_over_usd):
        return True, "within_role_cost_cap"
    return False, "over_role_cost_cap"
