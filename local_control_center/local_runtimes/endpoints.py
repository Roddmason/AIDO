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
from urllib.parse import urlparse

from fastapi import HTTPException

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

ENDPOINT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,95}$")
LOCAL_RUNTIME_PREFERRED_ROLES = ("analyst", "product_owner", "developer", "technical_lead")
LOCAL_RUNTIME_CATALOG_SOURCE = "local_runtime_catalog"


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
