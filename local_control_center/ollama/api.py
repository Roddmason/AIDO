"""Router de endpoints Ollama: alta, health y sincronización de catálogo por endpoint.

Cada endpoint Ollama se proyecta a las tablas existentes del Model Gateway:
``provider_accounts`` identifica el endpoint, ``runtime_installations`` y ``runtime_accounts`` lo
hacen visible como runtime, y ``model_catalog`` guarda sus modelos por ``providerId``.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.ollama import OllamaProvider
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

ENDPOINT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,95}$")
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class _AliasedModel(BaseModel):
    """Base Pydantic con alias camelCase para contratos HTTP."""

    model_config = ConfigDict(populate_by_name=True)


class OllamaEndpointCreateRequest(_AliasedModel):
    """Payload para registrar o reemplazar un endpoint Ollama local/remoto."""

    id: str
    display_name: str | None = Field(default=None, alias="displayName")
    base_url: str = Field(alias="baseUrl")
    kind: Literal["local", "remote"] | None = None
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class OllamaEndpointRecord(_AliasedModel):
    """Endpoint Ollama expuesto al cliente sin secretos."""

    id: str
    provider_id: str = Field(alias="providerId")
    runtime_id: str = Field(alias="runtimeId")
    display_name: str = Field(alias="displayName")
    kind: Literal["local", "remote"]
    base_url: str = Field(alias="baseUrl")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    credential_status: str = Field(alias="credentialStatus")
    enabled: bool
    health_status: str = Field(alias="healthStatus")
    last_health_check_at: str | None = Field(default=None, alias="lastHealthCheckAt")
    last_error: str = Field(alias="lastError")
    models: list[str] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class OllamaEndpointResponse(BaseModel):
    """Respuesta con un único endpoint Ollama."""

    endpoint: OllamaEndpointRecord


class OllamaEndpointsListResponse(BaseModel):
    """Respuesta con todos los endpoints Ollama configurados."""

    endpoints: list[OllamaEndpointRecord]


class OllamaEndpointHealthRecord(_AliasedModel):
    """Resultado de health-check de un endpoint Ollama."""

    id: str
    provider_id: str = Field(alias="providerId")
    status: str
    health_status: str = Field(alias="healthStatus")
    message: str = ""
    last_error: str | None = Field(default=None, alias="lastError")
    models: list[str] = Field(default_factory=list)


class OllamaEndpointHealthResponse(BaseModel):
    """Respuesta del health-check Ollama."""

    health: OllamaEndpointHealthRecord


class OllamaSyncModelsResponse(BaseModel):
    """Respuesta de sincronización de modelos Ollama."""

    models: list[dict[str, Any]]


def _validate_endpoint_id(endpoint_id: str) -> str:
    value = str(endpoint_id or "").strip()
    if not ENDPOINT_ID_RE.match(value):
        raise HTTPException(status_code=422, detail="Endpoint id must be a compact catalog id.")
    return value


def _normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="baseUrl must be an absolute http(s) URL.")
    if parsed.params or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="baseUrl must not include params, query or fragment.")
    return value


def _infer_kind(base_url: str, requested: str | None) -> Literal["local", "remote"]:
    if requested in {"local", "remote"}:
        return requested
    host = (urlparse(base_url).hostname or "").lower()
    return "local" if host in LOOPBACK_HOSTS else "remote"


def _provider_type(kind: str) -> str:
    return "local" if kind == "local" else "gateway"


def _is_ollama_account(account: dict[str, Any]) -> bool:
    return str(account.get("apiFormat") or "") == "ollama"


def _models_for_provider(store: ProviderAccountStore, provider_id: str) -> list[str]:
    return [item["model"] for item in store.list_models(provider_id) if item.get("enabled")]


def _endpoint_record(store: ProviderAccountStore, account: dict[str, Any]) -> dict[str, Any]:
    kind = "local" if account["providerType"] == "local" else "remote"
    credential_ref = str(account.get("credentialRef") or "").strip() or None
    return {
        "id": account["providerId"],
        "providerId": account["providerId"],
        "runtimeId": account["providerId"],
        "displayName": account["displayName"],
        "kind": kind,
        "baseUrl": account.get("baseUrl") or "",
        "credentialRef": credential_ref,
        "credentialStatus": account["credentialStatus"],
        "enabled": account["enabled"],
        "healthStatus": account["healthStatus"],
        "lastHealthCheckAt": account["lastHealthCheckAt"],
        "lastError": account["lastError"],
        "models": _models_for_provider(store, str(account["providerId"])),
        "createdAt": account["createdAt"],
        "updatedAt": account["updatedAt"],
    }


def _upsert_runtime_capability(connection: Any, *, runtime_id: str) -> None:
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO runtime_capabilities
            (id, runtime, capability, enabled, metadata, created_at, updated_at)
        VALUES (?, ?, 'chat', 1, ?, ?, ?)
        ON CONFLICT(runtime, capability) DO UPDATE SET
            enabled = 1,
            metadata = excluded.metadata,
            updated_at = excluded.updated_at
        """,
        (
            f"{runtime_id}:chat",
            runtime_id,
            json_dumps({"source": "ollama_endpoints"}),
            timestamp,
            timestamp,
        ),
    )


def _upsert_runtime_records(
    runtime_repo: RuntimeConfigRepository,
    *,
    endpoint_id: str,
    kind: str,
    display_name: str,
    credential_ref: str | None,
    enabled: bool,
    health_status: str = "unknown",
    last_health_check_at: str | None = None,
    last_error: str | None = None,
) -> None:
    capabilities = ["chat"]
    preferred_roles = ["analyst", "product_owner", "developer", "technical_lead"]
    runtime_repo.upsert_installation(
        {
            "runtimeId": endpoint_id,
            "kind": _provider_type(kind),
            "enabled": enabled,
            "capabilities": capabilities,
            "preferredRoles": preferred_roles,
            "healthStatus": health_status,
            "lastHealthCheckAt": last_health_check_at,
            "lastError": last_error or "",
            "configurationSource": "ollama_endpoint",
            "metadata": {
                "providerId": endpoint_id,
                "displayName": display_name,
                "kind": kind,
            },
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
            "configurationSource": "ollama_endpoint",
            "metadata": {"providerId": endpoint_id, "kind": kind},
        }
    )
    _upsert_runtime_capability(runtime_repo.connection, runtime_id=endpoint_id)


def _health_payload(endpoint_id: str, provider: OllamaProvider) -> dict[str, Any]:
    health = provider.health_check().model_dump(by_alias=True)
    if health.get("status") == "not_available":
        health["status"] = "unavailable"
    models = [item.model for item in provider.list_models()] if health.get("status") == "available" else []
    return redact_secrets(
        {
            **health,
            "id": endpoint_id,
            "providerId": endpoint_id,
            "models": models,
        }
    )


def _model_payload(provider_id: str, model: str) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "model": model,
        "displayName": model,
        "modelFamily": "ollama",
        "supportsStreaming": True,
        "freeTier": True,
        "enabled": True,
        "source": "ollama_endpoint_sync",
    }


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Construye el router `/api/v1/ollama/endpoints`."""
    router = APIRouter(prefix="/api/v1/ollama", tags=["ollama"])

    def providers() -> ProviderAccountStore:
        return ProviderAccountStore(platform.connection)

    def runtimes() -> RuntimeConfigRepository:
        return RuntimeConfigRepository(platform.connection)

    def audit(action: str, target: str, payload: dict[str, Any] | None = None) -> None:
        EventBus(platform.connection).record_audit(action=action, target=target, payload=payload or {})

    def get_endpoint_account(endpoint_id: str) -> dict[str, Any]:
        try:
            account = providers().get_provider_account(endpoint_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if not _is_ollama_account(account):
            raise HTTPException(status_code=404, detail=f"Ollama endpoint not found: {endpoint_id}")
        return account

    @router.post("/endpoints", status_code=201, response_model=OllamaEndpointResponse)
    async def create_endpoint(body: OllamaEndpointCreateRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza un endpoint Ollama y sus registros de runtime asociados."""
        require_write(request)
        endpoint_id = _validate_endpoint_id(body.id)
        base_url = _normalize_base_url(body.base_url)
        kind = _infer_kind(base_url, body.kind)
        resolver = CredentialResolver()
        credential_ref = resolver.normalize_ref_for_storage(body.credential_ref)
        try:
            resolver.validate_ref(credential_ref)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid credential_ref: {error}") from error
        display_name = body.display_name or endpoint_id
        provider = providers().upsert_provider_account(
            {
                "id": endpoint_id,
                "providerId": endpoint_id,
                "displayName": display_name,
                "providerType": _provider_type(kind),
                "apiFormat": "ollama",
                "baseUrl": base_url,
                "credentialRef": credential_ref,
                "enabled": body.enabled,
                "quotaMode": "none",
                "metadata": {
                    **body.metadata,
                    "providerFamily": "ollama",
                    "endpointKind": kind,
                },
            }
        )
        _upsert_runtime_records(
            runtimes(),
            endpoint_id=endpoint_id,
            kind=kind,
            display_name=display_name,
            credential_ref=provider.get("credentialRef") or None,
            enabled=body.enabled,
        )
        audit("ollama.endpoint.upserted", endpoint_id, {"kind": kind, "baseUrl": base_url})
        return {"endpoint": _endpoint_record(providers(), provider)}

    @router.get("/endpoints", response_model=OllamaEndpointsListResponse)
    async def list_endpoints() -> dict[str, Any]:
        """Lista los endpoints Ollama configurados."""
        store = providers()
        endpoints = [
            _endpoint_record(store, account)
            for account in store.list_provider_accounts()
            if _is_ollama_account(account)
        ]
        return {"endpoints": endpoints}

    @router.post("/endpoints/{endpoint_id}/health", response_model=OllamaEndpointHealthResponse)
    async def health_endpoint(endpoint_id: str, request: Request) -> dict[str, Any]:
        """Ejecuta `/api/tags` contra un endpoint Ollama, con Bearer opcional."""
        require_write(request)
        endpoint_id = _validate_endpoint_id(endpoint_id)
        account = get_endpoint_account(endpoint_id)
        provider = OllamaProvider(
            base_url=str(account.get("baseUrl") or ""),
            credential_ref=str(account.get("credentialRef") or "") or None,
        )
        health = _health_payload(endpoint_id, provider)
        now = utc_now()
        providers().record_health_check(
            provider_id=endpoint_id,
            status=str(health["status"]),
            payload={**health, "lastHealthCheckAt": now},
        )
        kind = "local" if account["providerType"] == "local" else "remote"
        _upsert_runtime_records(
            runtimes(),
            endpoint_id=endpoint_id,
            kind=kind,
            display_name=str(account["displayName"]),
            credential_ref=str(account.get("credentialRef") or "") or None,
            enabled=bool(account["enabled"]),
            health_status=str(health["healthStatus"]),
            last_health_check_at=now,
            last_error=str(health.get("lastError") or health.get("message") or ""),
        )
        runtimes().record_health_check(
            runtime_id=endpoint_id,
            check_type="ollama_tags",
            status=str(health["healthStatus"]),
            payload=health,
        )
        audit("ollama.endpoint.health_checked", endpoint_id, health)
        return {"health": health}

    @router.post("/endpoints/{endpoint_id}/sync-models", response_model=OllamaSyncModelsResponse)
    async def sync_models(endpoint_id: str, request: Request) -> dict[str, Any]:
        """Sincroniza `model_catalog` desde `/api/tags` para un endpoint Ollama."""
        require_write(request)
        endpoint_id = _validate_endpoint_id(endpoint_id)
        account = get_endpoint_account(endpoint_id)
        provider = OllamaProvider(
            base_url=str(account.get("baseUrl") or ""),
            credential_ref=str(account.get("credentialRef") or "") or None,
        )
        health = _health_payload(endpoint_id, provider)
        if health["status"] != "available":
            providers().record_health_check(provider_id=endpoint_id, status=str(health["status"]), payload=health)
            raise HTTPException(status_code=503, detail=redact_secrets(health.get("message") or "unavailable"))
        stored = [
            providers().upsert_model(_model_payload(endpoint_id, model))
            for model in health.get("models", [])
        ]
        providers().record_health_check(provider_id=endpoint_id, status="available", payload=health)
        audit(
            "ollama.endpoint.models_synced",
            endpoint_id,
            {"count": len(stored), "source": "ollama_api_tags"},
        )
        return {"models": stored}

    return router
