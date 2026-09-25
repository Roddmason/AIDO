"""Router genérico de endpoints locales bajo ``/api/v1/local-endpoints``.

Alta desde las entradas locales del catálogo (``providerCatalogId`` escrito por el servidor y registros
de runtime por instancia), edición de URL/nombre/token/concurrencia, configuración por modelo y
validación real de un modelo. Sondear y sincronizar reutilizan las rutas del Model Gateway y del
catálogo de providers. Toda escritura exige el token de loopback (``require_write``).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_catalog import provider_catalog_entry
from local_control_center.executions.router import ExecutionRouter, queued_operation
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team.contracts import RuntimeValidationResponse
from local_control_center.runtime_team.probe import RuntimeValidationService
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus

from .catalog import local_endpoint_entry
from .contracts import (
    LocalEndpointCreateRequest,
    LocalEndpointPatchRequest,
    LocalEndpointsListResponse,
    LocalEndpointView,
    LocalModelPatchRequest,
    LocalModelValidateRequest,
    LocalModelView,
)
from .endpoints import (
    LOCAL_ENDPOINT_SOURCE,
    cached_load_states,
    local_account_payload,
    local_endpoint_view,
    local_endpoint_views,
    local_model_views,
    normalize_base_url,
    runtime_records_source,
    upsert_local_runtime_records,
    validate_endpoint_id,
)


def _normalized_credential_ref(raw: str | None) -> str:
    resolver = CredentialResolver()
    credential_ref = str(resolver.normalize_ref_for_storage(raw) or "")
    try:
        resolver.validate_ref(credential_ref)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"Invalid credentialRef: {error}") from error
    return credential_ref


def _record_runtime(runtime_repo: RuntimeConfigRepository, account: dict[str, Any]) -> None:
    upsert_local_runtime_records(
        runtime_repo,
        endpoint_id=str(account["providerId"]),
        runtime_kind=str(account["providerType"]),
        display_name=str(account["displayName"]),
        credential_ref=str(account.get("credentialRef") or "") or None,
        enabled=bool(account["enabled"]),
        source=runtime_records_source(account),
        endpoint_kind="remote" if account["providerType"] == "gateway" else "local",
        health_status=str(account.get("healthStatus") or "unknown"),
        last_health_check_at=account.get("lastHealthCheckAt"),
        last_error=str(account.get("lastError") or ""),
    )


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Construye el router de endpoints locales montado bajo ``/api/v1``."""
    router = ExecutionRouter(
        platform=platform, require_write=require_write, prefix="/api/v1", tags=["local-runtimes"]
    )

    def providers() -> ProviderAccountStore:
        return ProviderAccountStore(platform.connection)

    def runtimes() -> RuntimeConfigRepository:
        return RuntimeConfigRepository(platform.connection)

    def audit(action: str, target: str, payload: dict[str, Any] | None = None) -> None:
        EventBus(platform.connection).record_audit(
            action=action, target=target, payload=payload or {}, actor="operator"
        )

    def local_account_or_404(provider_id: str) -> dict[str, Any]:
        try:
            account = providers().get_provider_account(provider_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if local_endpoint_entry(account) is None:
            raise HTTPException(status_code=404, detail=f"Local endpoint not found: {provider_id}")
        return account

    @router.get("/local-endpoints", response_model=LocalEndpointsListResponse)
    def list_local_endpoints() -> dict[str, Any]:
        """Lista los endpoints locales (Ollama incluido) con modelos y estado de carga acotado."""
        return {"endpoints": local_endpoint_views(platform.connection)}

    @router.post("/local-endpoints", status_code=201, response_model=LocalEndpointView)
    def create_local_endpoint(body: LocalEndpointCreateRequest, request: Request) -> dict[str, Any]:
        """Crea un endpoint desde una entrada local del catálogo; nunca reemplaza uno existente."""
        require_write(request)
        entry = provider_catalog_entry(body.catalog_id)
        if entry is None or entry.local_profile is None:
            raise HTTPException(
                status_code=422, detail=f"catalogId is not a local runtime preset: {body.catalog_id}"
            )
        endpoint_id = validate_endpoint_id(body.instance_id or entry.id)
        raw_base_url = str(body.base_url or entry.default_base_url or "").strip()
        if not raw_base_url:
            raise HTTPException(status_code=422, detail=f"baseUrl is required for {entry.id}.")
        base_url = normalize_base_url(raw_base_url)
        credential_ref = _normalized_credential_ref(body.credential_ref)
        store = providers()
        try:
            with immediate_transaction(platform.connection):
                try:
                    store.get_provider_account(endpoint_id)
                except KeyError:
                    pass
                else:
                    raise HTTPException(
                        status_code=409, detail=f"Local endpoint already exists: {endpoint_id}."
                    )
                store.upsert_provider_account(
                    local_account_payload(
                        entry,
                        endpoint_id=endpoint_id,
                        display_name=body.display_name or entry.display_name,
                        base_url=base_url,
                        credential_ref=credential_ref,
                    )
                )
                account = store.set_provider_catalog_id(endpoint_id, entry.id)
                upsert_local_runtime_records(
                    runtimes(),
                    endpoint_id=endpoint_id,
                    runtime_kind=entry.provider_type,
                    display_name=str(account["displayName"]),
                    credential_ref=credential_ref or None,
                    enabled=True,
                    source=LOCAL_ENDPOINT_SOURCE,
                )
                audit("local_endpoint.created", endpoint_id, {"catalogId": entry.id, "baseUrl": base_url})
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid local endpoint: {error}") from error
        return local_endpoint_view(platform.connection, store.get_provider_account(endpoint_id))

    @router.patch("/local-endpoints/{provider_id}", response_model=LocalEndpointView)
    def patch_local_endpoint(
        provider_id: str, body: LocalEndpointPatchRequest, request: Request
    ) -> dict[str, Any]:
        """Edita URL, nombre, habilitación, token o concurrencia; cambiar de host retira la declaración local."""
        require_write(request)
        account = local_account_or_404(provider_id)
        endpoint_id = str(account["providerId"])
        changes = body.model_dump(by_alias=True, exclude_unset=True)
        patch: dict[str, Any] = {}
        if changes.get("baseUrl") is not None:
            patch["baseUrl"] = normalize_base_url(changes["baseUrl"])
        if changes.get("displayName"):
            patch["displayName"] = changes["displayName"]
        if changes.get("enabled") is not None:
            patch["enabled"] = changes["enabled"]
        if "credentialRef" in changes:
            patch["credentialRef"] = _normalized_credential_ref(changes["credentialRef"])
        store = providers()
        try:
            with immediate_transaction(platform.connection):
                updated = store.patch_provider_account(endpoint_id, patch) if patch else account
                old_host = urlparse(str(account.get("baseUrl") or "")).hostname
                new_host = urlparse(str(updated.get("baseUrl") or "")).hostname
                if old_host != new_host and account.get("localDeclaration") is not None:
                    updated = store.set_local_declaration(endpoint_id, None)
                if changes.get("concurrencyLimit") is not None:
                    updated = store.set_local_concurrency_limit(endpoint_id, changes["concurrencyLimit"])
                _record_runtime(runtimes(), updated)
                audit("local_endpoint.updated", endpoint_id, {"fields": sorted(changes)})
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid local endpoint: {error}") from error
        return local_endpoint_view(platform.connection, store.get_provider_account(endpoint_id))

    @router.patch("/local-endpoints/{provider_id}/models", response_model=LocalModelView)
    def patch_local_model(provider_id: str, body: LocalModelPatchRequest, request: Request) -> dict[str, Any]:
        """Habilita un modelo, lo marca por defecto, fija su orden o sus capacidades de código opt-in."""
        require_write(request)
        account = local_account_or_404(provider_id)
        endpoint_id = str(account["providerId"])
        store = providers()
        row = next((item for item in store.list_models(endpoint_id) if item["model"] == body.model), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Model not found for {endpoint_id}: {body.model}")
        if body.enabled is not None and body.enabled != row["enabled"]:
            store.patch_model(str(row["id"]), {"enabled": body.enabled})
        if any(
            value is not None
            for value in (body.is_default, body.code_edit, body.code_review, body.operator_order)
        ):
            LocalModelSettingsRepository(platform.connection).upsert(
                endpoint_id,
                body.model,
                actor="operator",
                is_default=body.is_default,
                code_edit=body.code_edit,
                code_review=body.code_review,
                operator_order=body.operator_order,
            )
        audit(
            "local_endpoint.model_updated",
            endpoint_id,
            {"model": body.model, "fields": sorted(body.model_dump(by_alias=True, exclude_unset=True))},
        )
        refreshed = store.get_provider_account(endpoint_id)
        views = local_model_views(platform.connection, refreshed, cached_load_states(refreshed))
        return next(item for item in views if item["model"] == body.model)

    @router.post("/local-endpoints/{provider_id}/validate-model", response_model=RuntimeValidationResponse)
    @queued_operation("local_endpoints.validate_model", workload_class="local_model_call")
    async def validate_local_model(
        provider_id: str, body: LocalModelValidateRequest, request: Request
    ) -> dict[str, Any]:
        """Valida de verdad un modelo concreto del endpoint y deja la evidencia por (provider, modelo)."""
        require_write(request)
        account = local_account_or_404(provider_id)
        endpoint_id = str(account["providerId"])
        if body.model not in {str(row["model"]) for row in providers().list_models(endpoint_id)}:
            raise HTTPException(status_code=404, detail=f"Model not found for {endpoint_id}: {body.model}")
        try:
            result = RuntimeValidationService(platform.connection).validate(
                endpoint_id, project_id=None, model=body.model
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        audit(
            "local_endpoint.model_validated",
            endpoint_id,
            {"model": body.model, "status": result.get("status")},
        )
        return {"validation": result}

    return router
