"""Hechos por runtime configurado para el equipo del hilo: tipo, etiqueta y roles elegibles.

Las capacidades salen del mismo estado de runtime que usa la selección de recursos, sin sondas:
el panel y la validación del PATCH ven exactamente lo que verá el loop. La política de runtimes del
proyecto (``runtime_policy_decision``) se evalúa aquí para que un runtime denegado nunca sea
seleccionable en ese proyecto.

@author Rodrigo Mason
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import provider_account_policy_kind
from local_control_center.agents.runtime_readiness import healthy_evidence
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

from .roles import RuntimeFacts, eligible_team_roles, role_capability_gaps

TEAM_RUNTIME_KINDS = frozenset({"cli", "api", "gateway", "local"})
logger = logging.getLogger(__name__)

CAPABILITY_LABELS = {"code_edit": "Can edit code", "code_review": "Can review code"}
"""Etiquetas de la UI del operador (``app.localRuntime.wizard.codeEdit``/``codeReview``)."""


def load_runtime_facts(
    connection: sqlite3.Connection, *, project_id: str | None, offline: bool = False
) -> dict[str, RuntimeFacts]:
    """Devuelve los runtimes habilitados y no manuales con roles elegibles y veto de política, por id.

    Un runtime local suma a sus capacidades las opt-in de sus modelos habilitados (``chat`` mas modelos).
    ``offline`` lee solo el estado persistido/cacheado, sin sondas de red: obligatorio dentro de una
    transacción SQLite.
    """
    statuses = {
        str(status.get("id") or ""): status
        for status in RuntimeStatusService(connection, offline=offline).list_provider_statuses(
            project_id=project_id
        )
    }
    policy = RuntimeConfigRepository(connection)
    local_settings = LocalModelSettingsRepository(connection)
    facts: dict[str, RuntimeFacts] = {}
    for account in ProviderAccountStore(connection).list_provider_accounts():
        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        if not account.get("enabled") or kind not in TEAM_RUNTIME_KINDS:
            continue
        status = statuses.get(provider_id) or {}
        capabilities = set(status.get("capabilities") or [])
        if kind == "local":
            capabilities |= local_settings.enabled_capabilities(provider_id)
        runtime = {
            **status,
            "id": provider_id,
            "providerFamily": status.get("providerFamily") or account.get("providerFamily"),
            "capabilities": sorted(capabilities),
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
            healthy=healthy_evidence(status),
            capability_gaps=role_capability_gaps(runtime),
        )
    return facts


def role_capability_hint(
    connection: sqlite3.Connection,
    *,
    team_role: str | None,
    rejected: Sequence[Mapping[str, Any]],
    project_id: str | None,
) -> tuple[str, list[dict[str, str]]]:
    """Explica los descartes ``provider_not_allowed_for_agent`` de runtimes sanos que solo carecen de capacidad.

    Best-effort: corre en el camino de un bloqueo, así que si los hechos no se pueden leer devuelve
    ``("", [])`` en vez de impedir que el bloqueo se reporte.

    Devuelve el texto accionable para el motivo de bloqueo y el detalle estructurado
    (``providerId``/``teamRole``/``capability``); ``("", [])`` si ningún descarte encaja. Solo lee el
    estado persistido (``offline``) y no altera qué candidatos acepta la selección.
    """
    if not team_role:
        return "", []
    provider_ids = list(
        dict.fromkeys(
            str(item.get("providerId") or "")
            for item in rejected
            if str(item.get("reason") or "").split(":", 1)[0].strip() == "provider_not_allowed_for_agent"
        )
    )
    if not provider_ids:
        return "", []
    try:
        facts = load_runtime_facts(connection, project_id=project_id, offline=True)
    except Exception:
        logger.warning("Capability hint unavailable for role %s.", team_role, exc_info=True)
        return "", []
    gaps = [
        {"providerId": provider_id, "teamRole": team_role, "capability": fact.capability_gaps[team_role]}
        for provider_id in provider_ids
        if (fact := facts.get(provider_id)) is not None
        and fact.healthy
        and fact.policy_denied_reason is None
        and team_role in fact.capability_gaps
    ]
    sentences = [
        f"{gap['providerId']} is healthy but not eligible for {team_role}: "
        f"declare '{CAPABILITY_LABELS[gap['capability']]}' for its model."
        for gap in gaps
    ]
    return " ".join(sentences), gaps
