"""Lectura de candidatos para el panel del equipo del hilo con el reparto calculado en backend.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

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
        """Lista runtimes habilitados; el reparto usa ``selected`` (o todos) filtrado a los validados."""
        facts = load_runtime_facts(self.connection, project_id=project_id)
        states = {
            provider_id: runtime_validation_state(
                self.connection, provider_id, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
            )
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
        return {
            "candidates": [
                {
                    "providerId": item.provider_id,
                    "label": item.label,
                    "kind": item.kind,
                    "validation": _validation_record(item, states[item.provider_id]),
                    "eligibleRoles": list(item.eligible_roles),
                }
                for item in ordered
            ],
            "freshnessSeconds": RUNTIME_TEAM_FRESHNESS_SECONDS,
            "suggestedRoleRuntimes": auto_assign_roles(pool, self._runtime_order()),
        }

    def _runtime_order(self) -> list[str]:
        try:
            preferences = RuntimeConfigRepository(self.connection).get_preferences()
        except KeyError:
            return []
        return [str(item) for item in preferences.get("runtimeOrder") or [] if str(item).strip()]
