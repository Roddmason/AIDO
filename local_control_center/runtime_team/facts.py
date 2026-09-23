"""Hechos por runtime configurado para el equipo del hilo: tipo, etiqueta y roles elegibles.

Las capacidades salen del mismo estado de runtime que usa la selección de recursos, sin sondas:
el panel y la validación del PATCH ven exactamente lo que verá el loop. La política de runtimes del
proyecto (``runtime_policy_decision``) se evalúa aquí para que un runtime denegado nunca sea
seleccionable en ese proyecto.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import provider_account_policy_kind
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

from .roles import RuntimeFacts, eligible_team_roles

TEAM_RUNTIME_KINDS = frozenset({"cli", "api", "gateway", "local"})


def load_runtime_facts(connection: sqlite3.Connection, *, project_id: str | None) -> dict[str, RuntimeFacts]:
    """Devuelve los runtimes habilitados y no manuales con roles elegibles y veto de política, por id."""
    statuses = {
        str(status.get("id") or ""): status
        for status in RuntimeStatusService(connection).list_provider_statuses(project_id=project_id)
    }
    policy = RuntimeConfigRepository(connection)
    facts: dict[str, RuntimeFacts] = {}
    for account in ProviderAccountStore(connection).list_provider_accounts():
        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        if not account.get("enabled") or kind not in TEAM_RUNTIME_KINDS:
            continue
        status = statuses.get(provider_id) or {}
        runtime = {
            **status,
            "id": provider_id,
            "providerFamily": status.get("providerFamily") or account.get("providerFamily"),
            "capabilities": status.get("capabilities") or [],
        }
        decision = policy.runtime_policy_decision(
            provider_id=provider_id,
            kind=provider_account_policy_kind(account),
            project_id=project_id,
            account=account,
        )
        facts[provider_id] = RuntimeFacts(
            provider_id=provider_id,
            label=str(status.get("displayName") or account.get("displayName") or provider_id),
            kind=kind,
            eligible_roles=eligible_team_roles(runtime),
            policy_denied_reason=None
            if decision.get("allowed")
            else str(decision.get("reason") or "Runtime policy denies this provider."),
        )
    return facts
