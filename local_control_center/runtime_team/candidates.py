"""Lectura de candidatos para el panel del equipo del hilo con el reparto calculado en backend.

Para runtimes locales agrega los modelos cargados (caché compartida, espera acotada) y la vista
previa del modelo por rol con el mismo resolvedor que sella ``roleModels`` en el hilo. Un runtime
local vale como validado si alguno de sus modelos habilitados lo está (validación por (provider,
modelo), igual que el gate de envío y el sellado).

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.local_runtimes.endpoints import load_states_by_provider, loaded_models
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

from .configuration import resolve_team_role_models
from .facts import load_runtime_facts
from .roles import RuntimeFacts, auto_assign_roles
from .validation import RUNTIME_TEAM_FRESHNESS_SECONDS, RuntimeValidationState, runtime_validation_state


def _validation_record(item: RuntimeFacts, state: RuntimeValidationState) -> dict[str, Any]:
    """La política del proyecto prevalece sobre la evidencia: un runtime vetado nunca se ofrece."""
    if item.policy_denied_reason:
        return {
            "status": "policy_denied",
            "checkedAt": None,
            "latencyMs": None,
            "model": None,
            "reason": item.policy_denied_reason,
        }
    return state.to_record()


class RuntimeTeamCandidatesService:
    """Arma la respuesta de candidatos: validación de 30 min, veto de política, roles y reparto."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_candidates(self, *, project_id: str | None, selected: list[str] | None) -> dict[str, Any]:
        """Lista runtimes habilitados; el reparto usa ``selected`` (o todos) filtrado a los validados.

        El estado de carga se lee una vez para todas las cuentas (en paralelo, espera acotada) y deja
        caliente la caché compartida que ``resolve_team_role_models`` consulta sin esperar, así la vista
        previa de ``suggestedRoleModels`` usa los mismos estados que ``loadedModels``.
        """
        facts = load_runtime_facts(self.connection, project_id=project_id)
        store = ProviderAccountStore(self.connection)
        accounts = {
            str(account["providerId"]): account
            for account in store.list_provider_accounts()
            if str(account["providerId"]) in facts
        }
        states = {
            provider_id: self._runtime_state(store, provider_id, accounts.get(provider_id))
            for provider_id in facts
        }
        ordered = sorted(
            facts.values(), key=lambda item: (item.kind != "cli", item.label.lower(), item.provider_id)
        )
        pool_ids = set(facts) if selected is None else set(selected)
        pool = [
            item
            for item in ordered
            if item.provider_id in pool_ids
            and not item.policy_denied_reason
            and states[item.provider_id].status == "validated"
        ]
        load_states = load_states_by_provider(list(accounts.values()))
        suggested = auto_assign_roles(pool, self._runtime_order())
        return {
            "candidates": [
                {
                    "providerId": item.provider_id,
                    "label": item.label,
                    "kind": item.kind,
                    "validation": _validation_record(item, states[item.provider_id]),
                    "eligibleRoles": list(item.eligible_roles),
                    "loadedModels": loaded_models(load_states.get(item.provider_id, {})),
                }
                for item in ordered
            ],
            "freshnessSeconds": RUNTIME_TEAM_FRESHNESS_SECONDS,
            "suggestedRoleRuntimes": suggested,
            "suggestedRoleModels": resolve_team_role_models(self.connection, suggested),
        }

    def _runtime_state(
        self, store: ProviderAccountStore, provider_id: str, account: Mapping[str, Any] | None
    ) -> RuntimeValidationState:
        """Validación de 30 min del runtime; una cuenta local vale por su primer modelo habilitado validado.

        La falla de un modelo del mismo servidor no saca del reparto a un runtime local que tiene otro
        modelo habilitado validado (misma regla por (provider, modelo) que el gate de envío y el sellado).
        Sin modelo validado, o fuera de las cuentas locales, queda el estado a nivel de runtime.
        """
        if account is not None and str(account.get("providerType") or "") == "local":
            for item in store.list_models(provider_id):
                if not item.get("enabled"):
                    continue
                state = runtime_validation_state(
                    self.connection,
                    provider_id,
                    max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
                    model=str(item["model"]),
                )
                if state.status == "validated":
                    return state
        return runtime_validation_state(
            self.connection, provider_id, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
        )

    def _runtime_order(self) -> list[str]:
        try:
            preferences = RuntimeConfigRepository(self.connection).get_preferences()
        except KeyError:
            return []
        return [str(item) for item in preferences.get("runtimeOrder") or [] if str(item).strip()]
