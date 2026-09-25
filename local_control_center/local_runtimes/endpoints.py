"""Helpers compartidos de endpoints locales: validación de entrada y registros de runtime por instancia.

Los usan el router genérico ``/api/v1/local-endpoints``, el envoltorio ``/api/v1/ollama/endpoints`` y el
alta desde el catálogo: ids compactos, URLs http(s) absolutas sin userinfo y la proyección de cada
instancia a ``runtime_installations``, ``runtime_accounts`` y ``runtime_capabilities`` con una fila propia
(no la fila compartida de la familia), para que la readiness y el borrado de un endpoint no afecten a
otro.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Collection, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from local_control_center.agents import local_model_state
from local_control_center.agents.endpoint_locality import endpoint_locality, endpoint_network_scope
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.local_model_state import LoadState
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_catalog import PROVIDER_CATALOG_VERSION, ProviderCatalogEntry
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team.validation import (
    RUNTIME_TEAM_FRESHNESS_SECONDS,
    runtime_validation_state,
)
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

from .catalog import local_endpoint_entry, local_profile_entry

ENDPOINT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,95}$")
LOCAL_RUNTIME_PREFERRED_ROLES = ("analyst", "product_owner", "developer", "technical_lead")
LOCAL_RUNTIME_CATALOG_SOURCE = "local_runtime_catalog"
ABSENT_MODEL_SOURCE = "endpoint_absent"
VIEW_LOAD_STATE_WAIT_S = 0.5
MAX_PARALLEL_STATE_READS = 8
LOCAL_ENDPOINT_SOURCE = "local_endpoint"
OLLAMA_ENDPOINT_SOURCE = "ollama_endpoint"


def validate_endpoint_id(endpoint_id: str) -> str:
    """Devuelve el id compacto sin espacios o lanza 422 si no cumple ``ENDPOINT_ID_RE``."""
    value = str(endpoint_id or "").strip()
    if not ENDPOINT_ID_RE.match(value):
        raise HTTPException(status_code=422, detail="Endpoint id must be a compact catalog id.")
    return value


def normalize_base_url(base_url: str) -> str:
    """Valida una URL http(s) absoluta sin userinfo, params, query ni fragmento y quita la barra final."""
    value = str(base_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="baseUrl must be an absolute http(s) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=422, detail="baseUrl must not contain URL userinfo.")
    if parsed.params or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="baseUrl must not include params, query or fragment.")
    return value.rstrip("/")


def upsert_local_runtime_records(
    runtime_repo: RuntimeConfigRepository,
    *,
    endpoint_id: str,
    runtime_kind: str,
    display_name: str,
    credential_ref: str | None,
    enabled: bool,
    source: str,
    endpoint_kind: str = "local",
    health_status: str = "unknown",
    last_health_check_at: str | None = None,
    last_error: str | None = None,
) -> None:
    """Proyecta un endpoint a su instalación, su cuenta de runtime y su capacidad ``chat``.

    Son varias sentencias: el llamador las agrupa en ``immediate_transaction`` si necesita
    atomicidad.
    """
    capabilities = ["chat"]
    preferred_roles = list(LOCAL_RUNTIME_PREFERRED_ROLES)
    runtime_repo.upsert_installation(
        {
            "runtimeId": endpoint_id,
            "kind": runtime_kind,
            "enabled": enabled,
            "capabilities": capabilities,
            "preferredRoles": preferred_roles,
            "healthStatus": health_status,
            "lastHealthCheckAt": last_health_check_at,
            "lastError": last_error or "",
            "configurationSource": source,
            "metadata": {"providerId": endpoint_id, "displayName": display_name, "kind": endpoint_kind},
        }
    )
    runtime_repo.upsert_runtime_account(
        {
            "runtimeId": endpoint_id,
            "accountLabel": endpoint_id,
            "authMode": "bearer" if credential_ref else "none",
            "credentialStoreKind": "credential_ref" if credential_ref else "none",
            "credentialRef": credential_ref,
            "enabled": enabled,
            "isDefault": True,
            "capabilities": capabilities,
            "preferredRoles": preferred_roles,
            "healthStatus": health_status,
            "lastValidationAt": last_health_check_at,
            "configurationSource": source,
            "metadata": {"providerId": endpoint_id, "kind": endpoint_kind},
        }
    )
    timestamp = utc_now()
    runtime_repo.connection.execute(
        """
        INSERT INTO runtime_capabilities
            (id, runtime, capability, enabled, metadata, created_at, updated_at)
        VALUES (?, ?, 'chat', 1, ?, ?, ?)
        ON CONFLICT(runtime, capability) DO UPDATE SET
            enabled = 1,
            metadata = excluded.metadata,
            updated_at = excluded.updated_at
        """,
        (f"{endpoint_id}:chat", endpoint_id, json_dumps({"source": source}), timestamp, timestamp),
    )


def reconcile_absent_models(
    store: ProviderAccountStore, provider_id: str, present: Collection[str]
) -> list[str]:
    """Deshabilita los modelos habilitados que el servidor ya no anuncia y devuelve sus nombres.

    Un listado vacío no reconcilia nada: con los adaptadores actuales es indistinguible de una
    lectura fallida. Solo toca filas habilitadas: una fila que el operador apagó conserva su
    procedencia, y un modelo ausente que reaparece vuelve a habilitarse en el siguiente sync porque
    su fuente deja de ser ``operator_override``.
    """
    announced = {str(model) for model in present if str(model).strip()}
    if not announced:
        return []
    absent: list[str] = []
    for row in store.list_models(provider_id):
        if row.get("enabled") and str(row["model"]) not in announced:
            store.upsert_model({**row, "enabled": False, "source": ABSENT_MODEL_SOURCE})
            absent.append(str(row["model"]))
    return absent


def local_account_payload(
    entry: ProviderCatalogEntry,
    *,
    endpoint_id: str,
    display_name: str,
    base_url: str,
    credential_ref: str,
) -> dict[str, Any]:
    """Cuerpo de ``upsert_provider_account`` para un endpoint local nuevo, derivado solo del catálogo."""
    return {
        "id": endpoint_id,
        "providerId": endpoint_id,
        "displayName": display_name,
        "providerType": entry.provider_type,
        "apiFormat": entry.api_format,
        "providerFamily": entry.provider_family,
        "deploymentMode": entry.deployment_mode,
        "apiFamily": entry.api_family,
        "adapterProfile": entry.adapter_profile,
        "termsMode": entry.terms_mode,
        "pricingMode": entry.pricing_mode,
        "baseUrl": base_url,
        "credentialRef": credential_ref,
        "enabled": True,
        "quotaMode": "none",
        "metadata": {
            "providerCatalogVersion": PROVIDER_CATALOG_VERSION,
            "credentialKind": entry.credential_kind,
            "modelSync": entry.model_sync,
            "docsUrl": entry.docs_url,
            "pricingSource": entry.pricing_source,
        },
    }


def cached_load_states(account: Mapping[str, Any]) -> dict[str, LoadState]:
    """Estado de carga por id canónico desde la caché compartida de 10 s, con espera acotada.

    Devuelve vacío (todo desconocido) si la cuenta no tiene perfil local (Ollama), está deshabilitada
    o es remota: una vista nunca consulta un servidor que el operador no dejó activo y local. Los alias
    que expone el router (p. ej. ``local`` de ``gemma-4-26b-a4b``) comparten el estado de su modelo y no
    se muestran como modelos propios.
    """
    if local_profile_entry(account) is None or not account.get("enabled"):
        return {}
    if endpoint_locality(account) == "remote":
        return {}
    states = dict(local_model_state.LOAD_STATE_CACHE.get(account, max_wait_s=VIEW_LOAD_STATE_WAIT_S))
    aliases = local_model_state.model_aliases_for(account)
    return {model: state for model, state in states.items() if model not in aliases}


def load_states_by_provider(accounts: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, LoadState]]:
    """Lee en paralelo el estado de carga de varias cuentas; la espera total queda acotada por cuenta."""
    items = list(accounts)
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(items), MAX_PARALLEL_STATE_READS)) as pool:
        states = list(pool.map(cached_load_states, items))
    return {str(account["providerId"]): state for account, state in zip(items, states, strict=True)}


def loaded_models(states: Mapping[str, LoadState]) -> list[str]:
    """Modelos con estado ``loaded``, en orden estable."""
    return sorted(model for model, state in states.items() if state == "loaded")


def local_model_views(
    connection: sqlite3.Connection, account: Mapping[str, Any], load_states: Mapping[str, LoadState]
) -> list[dict[str, Any]]:
    """Modelos del catálogo de la cuenta con configuración, estado de carga y validación por modelo."""
    provider_id = str(account["providerId"])
    settings = {
        item.model: item for item in LocalModelSettingsRepository(connection).list_for_account(provider_id)
    }
    views: list[dict[str, Any]] = []
    for row in ProviderAccountStore(connection).list_models(provider_id):
        model = str(row["model"])
        setting = settings.get(model)
        validation = runtime_validation_state(
            connection, provider_id, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS, model=model
        )
        validated = validation.status == "validated"
        views.append(
            {
                "model": model,
                "enabled": bool(row["enabled"]),
                "isDefault": bool(setting and setting.is_default),
                "codeEdit": bool(setting and setting.code_edit),
                "codeReview": bool(setting and setting.code_review),
                "operatorOrder": setting.operator_order if setting else 0,
                "loadState": load_states.get(model, "unknown"),
                "validated": validated,
                "validatedAt": validation.checked_at if validated else None,
            }
        )
    return views


def local_endpoint_view(
    connection: sqlite3.Connection,
    account: Mapping[str, Any],
    *,
    load_states: Mapping[str, LoadState] | None = None,
) -> dict[str, Any]:
    """Proyección ``LocalEndpointView`` de una cuenta local (contrato de ``/api/v1/local-endpoints``).

    Raises:
        ValueError: si la cuenta no corresponde a una entrada local del catálogo.
    """
    entry = local_endpoint_entry(account)
    if entry is None:
        raise ValueError(f"Not a local endpoint account: {account.get('providerId')}")
    states = dict(cached_load_states(account) if load_states is None else load_states)
    last_error = str(account.get("lastError") or "").strip()
    return {
        "id": str(account["providerId"]),
        "catalogId": entry.id,
        "displayName": str(account["displayName"]),
        "baseUrl": str(account.get("baseUrl") or ""),
        "enabled": bool(account.get("enabled")),
        "locality": endpoint_locality(account),
        "networkScope": endpoint_network_scope(account),
        "declaredLocal": account.get("localDeclaration") is not None,
        "healthStatus": account.get("healthStatus"),
        "healthCheckedAt": account.get("lastHealthCheckAt"),
        "healthReason": last_error or None,
        "loadedModels": loaded_models(states),
        "models": local_model_views(connection, account, states),
        "concurrencyLimit": int(account.get("localConcurrencyLimit") or 1),
        "hasCredential": bool(str(account.get("credentialRef") or "").strip()),
    }


def local_endpoint_views(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Todas las cuentas locales configuradas como ``LocalEndpointView``, ordenadas por id."""
    accounts = [
        account
        for account in ProviderAccountStore(connection).list_provider_accounts()
        if local_endpoint_entry(account) is not None
    ]
    states = load_states_by_provider(accounts)
    return [
        local_endpoint_view(connection, account, load_states=states.get(str(account["providerId"]), {}))
        for account in accounts
    ]


def runtime_records_source(account: Mapping[str, Any]) -> str:
    """Origen de los registros de runtime: Ollama conserva el suyo; el resto usa ``local_endpoint``."""
    return (
        OLLAMA_ENDPOINT_SOURCE if str(account.get("apiFormat") or "") == "ollama" else LOCAL_ENDPOINT_SOURCE
    )
