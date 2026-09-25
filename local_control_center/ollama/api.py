"""Router de endpoints Ollama: alta, health y sincronización de catálogo por endpoint.

Cada endpoint Ollama se proyecta a las tablas existentes del Model Gateway:
``provider_accounts`` identifica el endpoint, ``runtime_installations`` y ``runtime_accounts`` lo
hacen visible como runtime, y ``model_catalog`` guarda sus modelos por ``providerId``.

@author Rodrigo Mason
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.endpoint_locality import is_local_model_runtime
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.ollama import OllamaProvider
from local_control_center.executions.router import ExecutionRouter, queued_operation
from local_control_center.local_runtimes.endpoints import (
    normalize_base_url as _normalize_base_url,
)
from local_control_center.local_runtimes.endpoints import (
    reconcile_absent_models,
    upsert_local_runtime_records,
)
from local_control_center.local_runtimes.endpoints import (
    validate_endpoint_id as _validate_endpoint_id,
)
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now


class _AliasedModel(BaseModel):
    """Base Pydantic con alias camelCase para contratos HTTP."""

    model_config = ConfigDict(populate_by_name=True)


class OllamaEndpointCreateRequest(_AliasedModel):
    """Payload para registrar o reemplazar un endpoint Ollama local/remoto."""

    id: str
    label: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    base_url: str = Field(alias="baseUrl")
    kind: Literal["local", "remote"] | None = None
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class OllamaEndpointRecord(_AliasedModel):
    """Endpoint Ollama expuesto al cliente sin secretos."""

    id: str
    label: str
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
    latency: int | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
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
    latency: int
    latency_ms: int = Field(alias="latencyMs")
    models: list[str] = Field(default_factory=list)


class OllamaEndpointHealthResponse(BaseModel):
    """Respuesta del health-check Ollama."""

    health: OllamaEndpointHealthRecord


class OllamaSyncModelsResponse(BaseModel):
    """Respuesta de sincronización de modelos Ollama."""

    models: list[dict[str, Any]]


def _infer_kind(base_url: str, requested: str | None) -> Literal["local", "remote"]:
    """Clasifica el endpoint con la fuente única de localidad: solo un host de loopback es local.

    `kind="remote"` del cliente se respeta porque solo restringe; `kind="local"` no promueve un host de red
    (declarar local exige el endpoint auditado de declaración). La duda se resuelve como remoto.
    """
    metadata = {"endpointKind": "remote"} if requested == "remote" else {}
    account = {"providerType": "local", "apiFormat": "ollama", "baseUrl": base_url, "metadata": metadata}
    return "local" if is_local_model_runtime(account) else "remote"


def _provider_type(kind: str) -> str:
    return "local" if kind == "local" else "gateway"


def _is_ollama_account(account: dict[str, Any]) -> bool:
    return str(account.get("apiFormat") or "") == "ollama"


def _is_configured_endpoint(account: dict[str, Any]) -> bool:
    """Un endpoint sin `baseUrl` es una cuenta sembrada, no un servidor al que se pueda hablar."""
    return _is_ollama_account(account) and bool(str(account.get("baseUrl") or "").strip())


def _endpoint_kind(account: dict[str, Any]) -> Literal["local", "remote"]:
    """Resuelve local/remoto desde la metadata del alta y, si falta, desde el host de la URL.

    `providerType` no sirve como fuente: este router lo escribe como local/gateway, pero el catálogo
    de providers siembra `ollama_remote` como `local`. La metadata registra la elección original y la
    URL es la verdad observable para cualquier cuenta creada fuera de este router.
    """
    stored = str((account.get("metadata") or {}).get("endpointKind") or "")
    if stored in {"local", "remote"}:
        return "local" if stored == "local" else "remote"
    return _infer_kind(str(account.get("baseUrl") or ""), None)


def _models_for_provider(store: ProviderAccountStore, provider_id: str) -> list[str]:
    return [item["model"] for item in store.list_models(provider_id) if item.get("enabled")]


def _latest_latency_for_provider(store: ProviderAccountStore, provider_id: str) -> int | None:
    row = store.connection.execute(
        """
        SELECT payload
        FROM provider_health_checks
        WHERE provider_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (provider_id,),
    ).fetchone()
    if not row:
        return None
    payload = json_loads(row["payload"], {})
    value = payload.get("latency")
    if value is None:
        value = payload.get("latencyMs")
    return int(value) if isinstance(value, int | float) else None


def _endpoint_record(store: ProviderAccountStore, account: dict[str, Any]) -> dict[str, Any]:
    kind = _endpoint_kind(account)
    credential_ref = str(account.get("credentialRef") or "").strip() or None
    provider_id = str(account["providerId"])
    latency = _latest_latency_for_provider(store, provider_id)
    return {
        "id": provider_id,
        "label": account["displayName"],
        "providerId": provider_id,
        "runtimeId": provider_id,
        "displayName": account["displayName"],
        "kind": kind,
        "baseUrl": account.get("baseUrl") or "",
        "credentialRef": credential_ref,
        "credentialStatus": account["credentialStatus"],
        "enabled": account["enabled"],
        "healthStatus": account["healthStatus"],
        "lastHealthCheckAt": account["lastHealthCheckAt"],
        "lastError": account["lastError"],
        "latency": latency,
        "latencyMs": latency,
        "models": _models_for_provider(store, provider_id),
        "createdAt": account["createdAt"],
        "updatedAt": account["updatedAt"],
    }


def _catalog_id(kind: str) -> str:
    """Entrada de catálogo del endpoint: el local es ``ollama``; el remoto, ``ollama_remote``."""
    return "ollama" if kind == "local" else "ollama_remote"


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
    upsert_local_runtime_records(
        runtime_repo,
        endpoint_id=endpoint_id,
        runtime_kind=_provider_type(kind),
        display_name=display_name,
        credential_ref=credential_ref,
        enabled=enabled,
        source="ollama_endpoint",
        endpoint_kind=kind,
        health_status=health_status,
        last_health_check_at=last_health_check_at,
        last_error=last_error,
    )


def _health_payload(endpoint_id: str, provider: OllamaProvider) -> dict[str, Any]:
    started = time.perf_counter()
    health = provider.health_check().model_dump(by_alias=True)
    latency = int((time.perf_counter() - started) * 1000)
    if health.get("status") == "not_available":
        health["status"] = "unavailable"
    models = [item.model for item in provider.list_models()] if health.get("status") == "available" else []
    return redact_secrets(
        {
            **health,
            "id": endpoint_id,
            "providerId": endpoint_id,
            "latency": latency,
            "latencyMs": latency,
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
    router = ExecutionRouter(
        platform=platform, require_write=require_write, prefix="/api/v1/ollama", tags=["ollama"]
    )

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
        display_name = body.display_name or body.label or endpoint_id
        providers().upsert_provider_account(
            {
                "id": endpoint_id,
                "providerId": endpoint_id,
                "displayName": display_name,
                "providerType": _provider_type(kind),
                "apiFormat": "ollama",
                "providerFamily": "ollama",
                "baseUrl": base_url,
                "credentialRef": credential_ref,
                "enabled": body.enabled,
                "quotaMode": "none",
                "metadata": {
                    **body.metadata,
                    "providerFamily": "ollama",
                    # Solo la restricción explícita del cliente persiste (P4/R13); un `kind="local"`
                    # demovido a remoto por `_infer_kind` no es una declaración real de remoto.
                    "endpointKind": body.kind,
                },
            }
        )
        provider = providers().set_provider_catalog_id(endpoint_id, _catalog_id(kind))
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
            if _is_configured_endpoint(account)
        ]
        return {"endpoints": endpoints}

    @router.post("/endpoints/{endpoint_id}/health", response_model=OllamaEndpointHealthResponse)
    @queued_operation("ollama.health_endpoint", workload_class="remote_llm_light")
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
        kind = _endpoint_kind(account)
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
    @queued_operation("ollama.sync_models", workload_class="remote_llm_light")
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
            providers().record_health_check(
                provider_id=endpoint_id, status=str(health["status"]), payload=health
            )
            raise HTTPException(
                status_code=503, detail=redact_secrets(health.get("message") or "unavailable")
            )
        present = [str(model) for model in health.get("models", [])]
        stored = [
            providers().upsert_model(_model_payload(endpoint_id, model), preserve_operator_enabled=True)
            for model in present
        ]
        absent = reconcile_absent_models(providers(), endpoint_id, present)
        providers().record_health_check(provider_id=endpoint_id, status="available", payload=health)
        audit(
            "ollama.endpoint.models_synced",
            endpoint_id,
            {"count": len(stored), "absentDisabled": len(absent), "source": "ollama_api_tags"},
        )
        return {"models": stored}

    return router
