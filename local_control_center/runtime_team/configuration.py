"""Equipo de runtimes del hilo: persistencia en ``runConfiguration``, sellado y readiness.

El operador guarda ``allowedRuntimes`` y ``roleRuntimes`` en el hilo (mismo patrón que ``teamMode``).
Al sellar un mensaje, la metadata del run recibe ``runtimeTeam`` estrechado por
``project.runtime.allowedProviders`` y por la frescura de 30 min: un runtime vencido sale del conjunto
y sus roles quedan sin asignar, y lo descartado queda en ``runtimeTeamDiscarded``. Para los roles en
runtimes locales el servidor resuelve y sella ``roleModels`` (modelo validado en la ventana y con las
capacidades del rol). El envío solo se rechaza si PO o Developer quedan sin runtime fresco. La política
del proyecto solo restringe, nunca amplía, y un valor entrante del cliente (incluido ``roleModels``) se
descarta siempre. Un equipo estrechado a vacío se conserva vacío para fallar cerrado en el loop en vez
de volver al ruteo automático.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Container, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from local_control_center.agents import local_model_state
from local_control_center.agents.local_model_selection import (
    normalize_model_capabilities,
    resolve_local_model,
)
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.local_model_state import LoadState
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

from .facts import load_runtime_facts
from .roles import TEAM_ROLES, RuntimeFacts, missing_required_roles
from .validation import RUNTIME_TEAM_FRESHNESS_SECONDS, runtime_validation_state

THREAD_RUN_CONFIGURATION_KEY = "runConfiguration"
"""Clave dentro de `project_threads.metadata` donde el hilo recuerda lo que el operador eligió."""
RUNTIME_TEAM_METADATA_KEY = "runtimeTeam"
"""Clave sellada en la metadata del run con el equipo efectivo; nunca se acepta del cliente."""
RUNTIME_TEAM_DISCARDED_METADATA_KEY = "runtimeTeamDiscarded"
"""Clave sellada con los runtimes elegidos que el sellado dejó fuera (política o vencidos) y su causa."""
PROJECT_ALLOWLIST_EXCLUDED_REASON = "project_runtime_allowlist_excluded"
ALLOWED_RUNTIMES_KEY = "allowedRuntimes"
ROLE_RUNTIMES_KEY = "roleRuntimes"
ROLE_MODELS_KEY = "roleModels"
"""Clave sellada rol→modelo para los runtimes locales; la escribe solo el servidor al sellar."""
ROLE_MODEL_CAPABILITIES: Mapping[str, frozenset[str]] = {
    role: normalize_model_capabilities(capabilities)
    for role, capabilities in {
        "product_owner": ("chat",),
        "developer": ("chat", "code"),
        "architect": ("chat",),
        "security": ("chat", "review"),
    }.items()
}
"""Capacidades que el modelo sellado de cada rol debe tener: las del scheduler llevadas al vocabulario del
modelo por ``normalize_model_capabilities`` (``code`` ⇒ ``code_edit``, ``review`` ⇒ ``code_review``, P22)."""


class RuntimeTeamNotReadyError(ValueError):
    """El equipo del hilo no puede ejecutar: rol obligatorio sin runtime o runtime sin validación fresca."""


@dataclass(frozen=True)
class RuntimeTeamReadiness:
    """Resultado de revisar un equipo contra la evidencia de validación vigente."""

    missing_roles: tuple[str, ...]
    stale_runtimes: tuple[dict[str, str], ...]

    @property
    def ready(self) -> bool:
        """Verdadero si no faltan roles obligatorios ni hay runtimes sin validación fresca."""
        return not self.missing_roles and not self.stale_runtimes

    def reason(self) -> str:
        """Causa legible para el 422 del envío o para el bloqueo del loop."""
        parts: list[str] = []
        if self.missing_roles:
            parts.append(f"Roles without an assigned runtime: {', '.join(self.missing_roles)}.")
        if self.stale_runtimes:
            listed = ", ".join(
                f"{item['providerId']}{'/' + item['model'] if item.get('model') else ''} ({item['reason']})"
                for item in self.stale_runtimes
            )
            parts.append(f"Runtimes without a fresh validation: {listed}.")
        return " ".join(parts)

    def details(self) -> dict[str, Any]:
        """Evidencia estructurada para la remediación (ids a re-probar y roles faltantes)."""
        return {
            "missingRoles": list(self.missing_roles),
            "staleRuntimes": [dict(item) for item in self.stale_runtimes],
            "runtimeIds": list(dict.fromkeys(item["providerId"] for item in self.stale_runtimes)),
        }


def _clean_ids(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip()))


def _team_from(raw: Any, *, sealed: bool = False) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or ALLOWED_RUNTIMES_KEY not in raw:
        return None
    allowed = _clean_ids(raw.get(ALLOWED_RUNTIMES_KEY) or [])
    roles_raw = raw.get(ROLE_RUNTIMES_KEY) if isinstance(raw.get(ROLE_RUNTIMES_KEY), dict) else {}
    roles = {
        role: str(roles_raw[role]).strip()
        for role in TEAM_ROLES
        if str(roles_raw.get(role) or "").strip() in allowed
    }
    team: dict[str, Any] = {ALLOWED_RUNTIMES_KEY: allowed, ROLE_RUNTIMES_KEY: roles}
    models_raw = raw.get(ROLE_MODELS_KEY) if isinstance(raw.get(ROLE_MODELS_KEY), dict) else {}
    role_models = {
        role: str(models_raw[role]).strip() for role in roles if str(models_raw.get(role) or "").strip()
    }
    if sealed and role_models:
        team[ROLE_MODELS_KEY] = role_models
    return team


def _thread_metadata(connection: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread_id,)).fetchone()
    return None if row is None else json_loads(row["metadata"], {})


def read_thread_runtime_team(connection: sqlite3.Connection, thread_id: str | None) -> dict[str, Any] | None:
    """Equipo guardado en el hilo, o ``None`` si el hilo usa el ruteo automático."""
    if not thread_id:
        return None
    configuration = (_thread_metadata(connection, thread_id) or {}).get(THREAD_RUN_CONFIGURATION_KEY) or {}
    team = _team_from(configuration)
    return team if team and team[ALLOWED_RUNTIMES_KEY] else None


def write_thread_runtime_team(
    connection: sqlite3.Connection,
    *,
    thread_id: str,
    project_id: str,
    allowed_runtimes: Iterable[str],
    role_runtimes: Mapping[str, str],
    facts: Mapping[str, RuntimeFacts] | None = None,
) -> dict[str, Any] | None:
    """Guarda (o limpia con una lista vacía) el equipo del hilo tras validarlo contra la configuración.

    ``facts`` evita recalcular el estado de runtimes dentro de la transacción del llamador.

    Raises:
        KeyError: el hilo no existe.
        ValueError: un runtime no está habilitado o la política del proyecto lo deniega, un rol
            apunta fuera del conjunto o a un runtime que el contrato de ese rol no acepta, o llegan
            roles asignados con un conjunto vacío (pedido contradictorio que abriría el ruteo).
    """
    metadata = _thread_metadata(connection, thread_id)
    if metadata is None:
        raise KeyError(f"Thread not found: {thread_id}")
    allowed = _clean_ids(allowed_runtimes)
    if not allowed and any(str(provider_id or "").strip() for provider_id in role_runtimes.values()):
        raise ValueError("Role runtimes require allowedRuntimes; send both empty to use automatic routing.")
    configuration = dict(metadata.get(THREAD_RUN_CONFIGURATION_KEY) or {})
    team: dict[str, Any] | None = None
    if allowed:
        facts = facts if facts is not None else load_runtime_facts(connection, project_id=project_id)
        unknown = [provider_id for provider_id in allowed if provider_id not in facts]
        if unknown:
            raise ValueError(f"Runtimes are not enabled for a thread team: {', '.join(unknown)}.")
        denied = [
            f"{provider_id} ({facts[provider_id].policy_denied_reason})"
            for provider_id in allowed
            if facts[provider_id].policy_denied_reason
        ]
        if denied:
            raise ValueError(f"Runtimes are denied by the project runtime policy: {', '.join(denied)}.")
        roles: dict[str, str] = {}
        for role, provider_id in role_runtimes.items():
            clean = str(provider_id or "").strip()
            if not clean:
                continue
            if role not in TEAM_ROLES:
                raise ValueError(f"Unknown team role: {role}.")
            if clean not in allowed:
                raise ValueError(f"Role {role} uses {clean}, which is not selected for this thread.")
            if role not in facts[clean].eligible_roles:
                raise ValueError(f"Runtime {clean} is not eligible for role {role}.")
            roles[role] = clean
        team = {ALLOWED_RUNTIMES_KEY: allowed, ROLE_RUNTIMES_KEY: roles}
        configuration.update(team)
    else:
        configuration.pop(ALLOWED_RUNTIMES_KEY, None)
        configuration.pop(ROLE_RUNTIMES_KEY, None)
    metadata[THREAD_RUN_CONFIGURATION_KEY] = configuration
    connection.execute(
        "UPDATE project_threads SET metadata = ?, updated_at = ? WHERE id = ?",
        (json_dumps(metadata), utc_now(), thread_id),
    )
    return team


def narrow_runtime_team(
    connection: sqlite3.Connection, *, project_id: str, team: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Intersecta con ``project.runtime.allowedProviders``; una lista vacía del proyecto no restringe."""
    if team is None:
        return None
    project_allowed = set(
        _clean_ids(
            resolve_setting_value(
                connection=connection, key="project.runtime.allowedProviders", project_id=project_id
            )
            or []
        )
    )
    return _team_restricted_to(team, project_allowed) if project_allowed else team


def _team_restricted_to(team: dict[str, Any], keep: Container[str]) -> dict[str, Any]:
    """Deja en el conjunto solo ``keep`` y desasigna los roles de los runtimes que salen."""
    allowed = [provider_id for provider_id in team[ALLOWED_RUNTIMES_KEY] if provider_id in keep]
    roles = {
        role: provider_id for role, provider_id in team[ROLE_RUNTIMES_KEY].items() if provider_id in allowed
    }
    return {ALLOWED_RUNTIMES_KEY: allowed, ROLE_RUNTIMES_KEY: roles}


def assess_runtime_team(
    connection: sqlite3.Connection,
    team: dict[str, Any],
    *,
    max_age_seconds: int,
    only_assigned: bool = False,
) -> RuntimeTeamReadiness:
    """Revisa roles obligatorios (PO y Developer) y la validación de los runtimes dentro de la ventana.

    Sin ``only_assigned`` revisa todo el conjunto seleccionado (base del sellado de envío, spec §3.4);
    con él, solo los runtimes que tienen un rol asignado (gate de ejecución, spec §1.4). Si el equipo
    sellado trae ``roleModels``, cada runtime se evalúa por los modelos sellados de sus roles.
    """
    roles = team.get(ROLE_RUNTIMES_KEY) or {}
    role_models = team.get(ROLE_MODELS_KEY) or {}
    missing = tuple(missing_required_roles(roles))
    runtime_ids = (
        list(dict.fromkeys(roles[role] for role in TEAM_ROLES if roles.get(role)))
        if only_assigned
        else list(team.get(ALLOWED_RUNTIMES_KEY) or [])
    )
    stale: list[dict[str, str]] = []
    for provider_id in runtime_ids:
        models = sorted(
            {
                role_models[role]
                for role in TEAM_ROLES
                if roles.get(role) == provider_id and role_models.get(role)
            }
        )
        for model in models or [None]:
            state = runtime_validation_state(
                connection, provider_id, max_age_seconds=max_age_seconds, model=model
            )
            if state.status != "validated":
                item = {
                    "providerId": provider_id,
                    "status": state.status,
                    "reason": state.reason or state.status,
                }
                if model:
                    item["model"] = model
                stale.append(item)
    return RuntimeTeamReadiness(missing_roles=missing, stale_runtimes=tuple(stale))


def _cached_load_states_without_wait(account: Mapping[str, Any]) -> Mapping[str, LoadState]:
    """Estado de carga de la caché compartida sin esperar un refresco (el sellado no bloquea el envío)."""
    return local_model_state.LOAD_STATE_CACHE.get(account, max_wait_s=0.0)


def resolve_team_role_models(
    connection: sqlite3.Connection,
    role_runtimes: Mapping[str, str | None],
    *,
    load_states_for: Callable[[Mapping[str, Any]], Mapping[str, LoadState]] | None = None,
) -> dict[str, str]:
    """Modelo por rol para los roles asignados a runtimes locales (spec §4.3, ``roleModels``).

    ``E`` son los modelos habilitados validados en la ventana de 30 min y con las capacidades del rol; el
    estado de carga sale de ``load_states_for`` (por defecto, la caché compartida sin esperar; desconocido
    ⇒ por defecto). Los roles se recorren en ``TEAM_ROLES`` con afinidad por runtime. Un rol sin modelo
    resoluble queda fuera y la ejecución usa la selección determinista. La usan el sellado y la vista
    previa ``suggestedRoleModels`` de los candidatos del equipo.
    """
    read_states = load_states_for or _cached_load_states_without_wait
    store = ProviderAccountStore(connection)
    settings = LocalModelSettingsRepository(connection)
    resolved: dict[str, str] = {}
    affinity: dict[str, str] = {}
    for role in TEAM_ROLES:
        provider_id = str(role_runtimes.get(role) or "").strip()
        if not provider_id:
            continue
        try:
            account = store.get_provider_account(provider_id)
        except KeyError:
            continue
        if str(account.get("providerType") or "") != "local":
            continue
        enabled = [str(item["model"]) for item in store.list_models(provider_id) if item.get("enabled")]
        validated = frozenset(
            model
            for model in enabled
            if runtime_validation_state(
                connection, provider_id, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS, model=model
            ).status
            == "validated"
        )
        resolution = resolve_local_model(
            required_capabilities=ROLE_MODEL_CAPABILITIES[role],
            enabled_models=enabled,
            settings=settings.list_for_account(provider_id),
            validated_models=validated,
            load_states=read_states(account),
            affinity_model=affinity.get(provider_id),
            model_aliases=local_model_state.model_aliases_for(account),
        )
        if resolution.model is not None:
            resolved[role] = resolution.model
            affinity.setdefault(provider_id, resolution.model)
    return resolved


@dataclass(frozen=True)
class _EffectiveRuntimeTeam:
    """Equipo del hilo tras la política del proyecto (``narrowed``) y tras la frescura (``fresh``)."""

    configured: dict[str, Any]
    narrowed: dict[str, Any]
    fresh: dict[str, Any]
    excluded: tuple[dict[str, str], ...]
    stale: tuple[dict[str, str], ...]

    @property
    def missing_roles(self) -> list[str]:
        """Roles obligatorios que quedan sin runtime fresco dentro de la política del proyecto."""
        return missing_required_roles(self.fresh[ROLE_RUNTIMES_KEY])


def _effective_thread_runtime_team(
    connection: sqlite3.Connection, *, project_id: str, thread_id: str | None
) -> _EffectiveRuntimeTeam | None:
    team = read_thread_runtime_team(connection, thread_id)
    narrowed = narrow_runtime_team(connection, project_id=project_id, team=team)
    if team is None or narrowed is None:
        return None
    excluded = tuple(
        {"providerId": provider_id, "status": "policy_denied", "reason": PROJECT_ALLOWLIST_EXCLUDED_REASON}
        for provider_id in team[ALLOWED_RUNTIMES_KEY]
        if provider_id not in narrowed[ALLOWED_RUNTIMES_KEY]
    )
    role_models = resolve_team_role_models(connection, narrowed[ROLE_RUNTIMES_KEY])
    assessed = {**narrowed, ROLE_MODELS_KEY: role_models} if role_models else narrowed
    stale = assess_runtime_team(
        connection, assessed, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
    ).stale_runtimes
    fresh = _team_restricted_to(
        narrowed, set(narrowed[ALLOWED_RUNTIMES_KEY]) - {item["providerId"] for item in stale}
    )
    return _EffectiveRuntimeTeam(
        configured=team, narrowed=narrowed, fresh=fresh, excluded=excluded, stale=stale
    )


def ensure_thread_runtime_team_ready(
    connection: sqlite3.Connection, *, project_id: str, thread_id: str
) -> None:
    """Gate de envío: PO y Developer deben quedar con un runtime validado en los últimos 30 min.

    Los runtimes vencidos o fuera de la política del proyecto no bloquean por sí solos: el sellado los
    saca del conjunto. Solo se rechaza cuando un rol obligatorio queda sin runtime fresco.

    Raises:
        RuntimeTeamNotReadyError: con la causa legible; no se escribe nada antes de este chequeo.
    """
    effective = _effective_thread_runtime_team(connection, project_id=project_id, thread_id=thread_id)
    if effective is None or not effective.missing_roles:
        return
    message = f"Roles without a freshly validated runtime: {', '.join(effective.missing_roles)}."
    discarded = effective.excluded + effective.stale
    if discarded:
        listed = ", ".join(f"{item['providerId']} ({item['reason']})" for item in discarded)
        message = f"{message} Discarded runtimes: {listed}."
    raise RuntimeTeamNotReadyError(message)


def seal_thread_runtime_team(
    connection: sqlite3.Connection, *, project_id: str, thread_id: str | None, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Sella en la metadata del run el equipo efectivo del hilo; descarta cualquier valor entrante.

    Saca los runtimes vencidos (30 min) y los que excluye el proyecto, y deja lo descartado en
    ``runtimeTeamDiscarded`` con los roles que el operador les había asignado. Si sacar los vencidos dejaría PO o Developer sin runtime (un re-sellado
    de retry, donde no corre el gate de envío), conserva los vencidos: el gate de ejecución (24 h)
    decide y bloquea con "Re-probar runtime" sobre los runtimes asignados en vez de perder sus ids.
    """
    stamped = dict(metadata)
    stamped.pop(RUNTIME_TEAM_METADATA_KEY, None)
    stamped.pop(RUNTIME_TEAM_DISCARDED_METADATA_KEY, None)
    effective = _effective_thread_runtime_team(connection, project_id=project_id, thread_id=thread_id)
    if effective is None:
        return stamped
    if effective.missing_roles:
        team, discarded = effective.narrowed, effective.excluded
    else:
        team, discarded = effective.fresh, effective.excluded + effective.stale
    role_models = resolve_team_role_models(connection, team[ROLE_RUNTIMES_KEY])
    stamped[RUNTIME_TEAM_METADATA_KEY] = {**team, ROLE_MODELS_KEY: role_models} if role_models else team
    if discarded:
        configured_roles = effective.configured[ROLE_RUNTIMES_KEY]
        stamped[RUNTIME_TEAM_DISCARDED_METADATA_KEY] = [
            {
                **item,
                "roles": [role for role in TEAM_ROLES if configured_roles.get(role) == item["providerId"]],
            }
            for item in discarded
        ]
    return stamped


def discarded_runtimes_of(request_meta: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Runtimes que el sellado sacó del equipo del run (vencidos o fuera de la política del proyecto)."""
    raw = (request_meta or {}).get(RUNTIME_TEAM_DISCARDED_METADATA_KEY)
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def discarded_role_runtime(request_meta: Mapping[str, Any] | None, team_role: str) -> dict[str, Any] | None:
    """Runtime descartado al sellar que el operador había asignado a ``team_role``, si lo hubo."""
    return next(
        (item for item in discarded_runtimes_of(request_meta) if team_role in (item.get("roles") or [])),
        None,
    )


def runtime_team_of(request_meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Equipo sellado en la metadata del run (con ``roleModels`` si hay), o ``None`` con ruteo automático."""
    return _team_from((request_meta or {}).get(RUNTIME_TEAM_METADATA_KEY), sealed=True)


def assigned_runtime(request_meta: Mapping[str, Any] | None, team_role: str) -> str | None:
    """Runtime asignado al rol del equipo en el run sellado, si lo hay."""
    team = runtime_team_of(request_meta)
    return team[ROLE_RUNTIMES_KEY].get(team_role) if team else None


def role_allowlist(request_meta: Mapping[str, Any] | None, team_role: str | None) -> list[str] | None:
    """Allowlist dura para ``AIResourceRequest.allowed_provider_ids``.

    ``None`` sin equipo (comportamiento previo); un proveedor único para un rol asignado. Un rol sin
    asignación propia (aido_lead, technical_lead, opcional vacío) hereda el runtime del PO: con dos o
    más candidatos Jev bloquea por ``confidence_below_threshold`` y el reparto del operador, no su
    umbral, debe decidir. Sin PO asignado se conserva el conjunto completo del hilo.
    """
    team = runtime_team_of(request_meta)
    if team is None:
        return None
    role_runtimes = team[ROLE_RUNTIMES_KEY]
    assigned = (role_runtimes.get(team_role) if team_role else None) or role_runtimes.get("product_owner")
    return [assigned] if assigned else list(team[ALLOWED_RUNTIMES_KEY])


def role_model_pins(request_meta: Mapping[str, Any] | None, team_role: str | None) -> dict[str, str]:
    """Modelo sellado del rol por runtime local (``{providerId: model}``) para ``AIResourceRequest``.

    Solo fija roles con asignación y modelo sellado propios. A diferencia de ``role_allowlist``, un rol sin
    asignación propia (qa_engineer, aido_lead, technical_lead u opcional vacío) no hereda el modelo del PO:
    ese modelo puede no tener las capacidades del rol (p. ej. ``review``), así que usa la selección
    determinista y la afinidad. Vacío sin equipo o sin modelo sellado (equipos sellados antes de
    ``roleModels``).
    """
    team = runtime_team_of(request_meta)
    if team is None or not team_role:
        return {}
    provider_id = team[ROLE_RUNTIMES_KEY].get(team_role)
    model = (team.get(ROLE_MODELS_KEY) or {}).get(team_role)
    return {provider_id: model} if provider_id and model else {}


def restrict_to_allowlist(provider_ids: Iterable[str], allowlist: list[str] | None) -> list[str]:
    """Filtra ``provider_ids`` por la allowlist del hilo, conservando el orden original."""
    ids = list(provider_ids)
    if allowlist is None:
        return ids
    allowed = set(allowlist)
    return [provider_id for provider_id in ids if provider_id in allowed]
