"""Presupuesto de tiempo y formato de salida de una llamada de modelo desde el broker.

El timeout efectivo nunca es un valor fijo: es el menor entre lo pedido por el agente (más el
arranque en frío del servidor local cuando el llamador anuncia un cambio de modelo), el techo
``runtime.local.maxCallSeconds`` de los runtimes locales (que el adapter lee con
``local_endpoint_lease.max_local_call_seconds``, la misma fuente del TTL de la lease) y lo que
queda del deadline de la ejecución. Si ese resto no cubre el arranque en frío, la llamada falla
antes de invocar: lanza ``LocalRuntimeError("insufficient_time_for_model_load")``.
``response_format`` solo se pide como ``json_schema`` cuando el llamador declara esa capacidad
validada para el modelo.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from local_control_center.process_supervision.context import (
    ExecutionDeadlineExceeded,
    remaining_execution_timeout,
)

from ..endpoint_locality import catalog_entry_for_account
from ..local_runtime_causes import LocalRuntimeError


def effective_model_call_timeout(
    *,
    requested_seconds: int,
    cold_start_seconds: int = 0,
    max_call_seconds: int | None = None,
) -> int:
    """Calcula el timeout efectivo en segundos de una llamada de modelo.

    Raises:
        LocalRuntimeError: ``insufficient_time_for_model_load`` si el deadline restante no cubre
            el arranque en frío esperado.
        ExecutionDeadlineExceeded: si no queda presupuesto y no se esperaba arranque en frío.
    """
    cold_start = max(0, int(cold_start_seconds))
    desired = max(1, int(requested_seconds)) + cold_start
    if max_call_seconds is not None:
        desired = min(desired, max(1, int(max_call_seconds)))
    try:
        budget = remaining_execution_timeout(desired)
    except ExecutionDeadlineExceeded as error:
        if cold_start:
            raise LocalRuntimeError(
                "insufficient_time_for_model_load",
                "The execution deadline leaves no time to load the model.",
            ) from error
        raise
    if cold_start and budget < cold_start:
        raise LocalRuntimeError(
            "insufficient_time_for_model_load",
            f"Only {budget}s remain for a model switch that may need {cold_start}s to load.",
        )
    return budget


def local_cold_start_seconds(account: Mapping[str, Any], call_input: Mapping[str, Any]) -> int:
    """Devuelve el arranque en frío del perfil del servidor solo si el llamador anuncia cambio de modelo."""
    if call_input.get("coldStartExpected") is not True:
        return 0
    entry = catalog_entry_for_account(account)
    profile = getattr(entry, "local_profile", None) if entry is not None else None
    return int(profile.cold_start_timeout_s) if profile is not None else 0


def response_format_for(call_input: Mapping[str, Any]) -> dict[str, Any] | None:
    """Traduce ``structuredOutput``/``responseSchema`` del input a ``response_format`` OpenAI."""
    mode = call_input.get("structuredOutput")
    if mode == "json_object":
        return {"type": "json_object"}
    schema = call_input.get("responseSchema")
    if mode == "json_schema" and isinstance(schema, Mapping) and isinstance(schema.get("schema"), Mapping):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": str(schema.get("name") or "aido_output"),
                "schema": dict(schema["schema"]),
                "strict": True,
            },
        }
    return None
