"""Claves de ``tool_call["input"]`` que dependen del modelo local elegido (contrato del adapter).

``coldStartExpected`` sale de ``LocalModelResolution.requires_switch`` del modelo ya elegido: solo es
verdadero si el servidor informa ese modelo como no cargado o cargándose (un estado desconocido nunca
anuncia un cambio). ``structuredOutput`` json_schema y su ``responseSchema`` se piden solo si el llamador
trae un schema y una validación real del modelo cumplió ``response_format`` json_schema; si no, el agente
conserva la instrucción de JSON en el prompt. Una cuenta que no es runtime local no recibe ninguna clave, y
nunca se agrega ``extraBody``: apagar el razonamiento es exclusivo de la sonda y de la validación.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from local_control_center.agents import local_model_state
from local_control_center.agents.endpoint_locality import is_local_model_runtime
from local_control_center.agents.local_model_selection import resolve_local_model
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore


def local_model_call_input(
    connection: sqlite3.Connection,
    *,
    provider_id: str,
    model: str | None,
    response_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Claves ``coldStartExpected``/``structuredOutput``/``responseSchema`` para la llamada del modelo.

    Devuelve ``{}`` si no hay modelo, la cuenta no existe o no es un runtime local; solo incluye las
    claves que cambian el comportamiento del adapter.
    """
    if not model:
        return {}
    try:
        account = ProviderAccountStore(connection).get_provider_account(provider_id)
    except KeyError:
        return {}
    if not is_local_model_runtime(account):
        return {}
    resolution = resolve_local_model(
        required_capabilities=frozenset(),
        enabled_models=[model],
        settings=(),
        validated_models=frozenset({model}),
        load_states=local_model_state.LOAD_STATE_CACHE.get(account),
    )
    call_input: dict[str, Any] = {}
    if resolution.requires_switch:
        call_input["coldStartExpected"] = True
    canonical_id = str(account["providerId"])
    if response_schema is not None:
        # Un modelo nunca sondeado se descubre aqui, en la primera llamada con contrato JSON: el
        # preflight solo sondea cuando valida de nuevo, y con evidencia vigente dejaba al modelo sin
        # gramatica (visto en vivo: el PO en gemma-4-26b-a4b omitio `productBriefPatch.title`). Una vez
        # por modelo: con procedencia registrada no vuelve a sondear; un fallo solo queda en el log.
        from local_control_center.agents.runtime_preflight import probe_json_schema_capability_if_unknown

        probe_json_schema_capability_if_unknown(
            connection, account=account, model_id=model, was_loaded=not resolution.requires_switch
        )
    if response_schema is not None and LocalModelSettingsRepository(connection).json_schema_enabled(
        canonical_id, model
    ):
        call_input["structuredOutput"] = "json_schema"
        call_input["responseSchema"] = {
            "name": str(response_schema["name"]),
            "schema": dict(response_schema["schema"]),
        }
    return call_input
