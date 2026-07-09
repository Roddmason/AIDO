"""HTTP API for canonical provider catalog setup and account model sync.

This router exposes the backend provider catalog as the setup source of truth and
creates provider accounts from catalog presets without accepting raw secrets into
``provider_accounts``. Model synchronization reuses the existing provider adapters,
so each provider has one integration path.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets

from .credentials import CredentialResolver
from .model_gateway import provider_instance
from .model_gateway_models import DiscoverModelsResponse, ProviderAccountResponse
from .provider_accounts import ProviderAccountStore
from .provider_catalog import (
    PROVIDER_CATALOG_VERSION,
    ProviderCatalogEntry,
    canonical_provider_id,
    list_provider_catalog,
    provider_catalog_entry,
)


class CatalogApiModel(BaseModel):
    """Base model for catalog APIs, with camelCase aliases and strict inputs."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ProviderCatalogEntryRecord(CatalogApiModel):
    """Public catalog entry used by setup clients."""

    id: str
    display_name: str = Field(alias="displayName")
    provider_type: str = Field(alias="providerType")
    api_format: str = Field(alias="apiFormat")
    default_base_url: str | None = Field(default=None, alias="defaultBaseUrl")
    required_fields: list[str] = Field(alias="requiredFields")
    credential_kind: str = Field(alias="credentialKind")
    known_models: list[str] = Field(alias="knownModels")
    model_sync: dict[str, Any] = Field(alias="modelSync")
    capabilities: list[str]
    docs_url: str = Field(alias="docsUrl")
    pricing_source: str = Field(alias="pricingSource")
    aliases: list[str] = Field(default_factory=list)


class ProviderCatalogListResponse(CatalogApiModel):
    """Response wrapper for the canonical setup catalog."""

    providers: list[ProviderCatalogEntryRecord]
    version: str


class ProviderAccountFromCatalogRequest(CatalogApiModel):
    """Request to create/update a provider account from a catalog preset."""

    provider_id: str = Field(alias="providerId")
    display_name: str | None = Field(default=None, alias="displayName")
    base_url: str | None = Field(default=None, alias="baseUrl")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


def _catalog_or_404(provider_id: str) -> ProviderCatalogEntry:
    entry = provider_catalog_entry(provider_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider is not in the catalog: {provider_id}")
    return entry


def _normalize_base_url(value: str) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="baseUrl must be an absolute http(s) URL.")
    if parsed.params or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="baseUrl must not include params, query or fragment.")
    return candidate.rstrip("/")


def _base_url_for_request(entry: ProviderCatalogEntry, body: ProviderAccountFromCatalogRequest) -> str:
    provided = str(body.base_url or "").strip()
    if provided:
        return _normalize_base_url(provided)
    if "baseUrl" in entry.required_fields:
        raise HTTPException(status_code=422, detail=f"baseUrl is required for provider {entry.id}.")
    return entry.default_base_url or ""


def _credential_ref_for_request(
    entry: ProviderCatalogEntry, body: ProviderAccountFromCatalogRequest
) -> str:
    credential_ref = str(body.credential_ref or "").strip()
    if "credentialRef" in entry.required_fields and not credential_ref:
        raise HTTPException(status_code=422, detail=f"credentialRef is required for provider {entry.id}.")
    return credential_ref


def _catalog_metadata(entry: ProviderCatalogEntry, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        **metadata,
        "providerCatalogId": entry.id,
        "providerCatalogVersion": PROVIDER_CATALOG_VERSION,
        "credentialKind": entry.credential_kind,
        "capabilities": list(entry.capabilities),
        "modelSync": entry.model_sync,
        "docsUrl": entry.docs_url,
        "pricingSource": entry.pricing_source,
    }


def _requires_remote_policy(account: dict[str, Any]) -> bool:
    return str(account.get("providerType") or "") in {"api", "gateway"}


def _requires_credential(account: dict[str, Any]) -> bool:
    return _requires_remote_policy(account) and str(account.get("apiFormat") or "") != "ollama"


def _validate_sync_preconditions(
    account: dict[str, Any],
    *,
    runtime_repo: RuntimeConfigRepository,
) -> None:
    provider_id = str(account["providerId"])
    if not account.get("enabled"):
        raise HTTPException(
            status_code=403,
            detail=f"Provider {provider_id} is disabled; model sync requires explicit enablement.",
        )
    if _requires_remote_policy(account):
        decision = runtime_repo.runtime_policy_decision(
            provider_id=provider_id, kind=str(account.get("providerType") or "")
        )
        if not decision.get("allowed"):
            raise HTTPException(
                status_code=403,
                detail=str(decision.get("reason") or "Remote provider sync is disabled."),
            )
    credential_ref = str(account.get("credentialRef") or "")
    if _requires_credential(account) and not credential_ref:
        raise HTTPException(status_code=400, detail=f"Credential ref is required for provider {provider_id}.")
    if credential_ref:
        credential = CredentialResolver().resolve(credential_ref, fetch=False)
        if credential.status not in {"configured", "unverified"}:
            raise HTTPException(
                status_code=400,
                detail=redact_secrets(f"Credential ref {credential_ref} is {credential.status}."),
            )


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Build the provider catalog API router under `/api/v1`."""
    router = APIRouter(prefix="/api/v1", tags=["provider-catalog"])

    def providers() -> ProviderAccountStore:
        return ProviderAccountStore(platform.connection)

    def runtimes() -> RuntimeConfigRepository:
        return RuntimeConfigRepository(platform.connection)

    def audit(action: str, target: str, payload: dict[str, Any] | None = None) -> None:
        EventBus(platform.connection).record_audit(action=action, target=target, payload=payload or {})

    @router.get("/providers/catalog", response_model=ProviderCatalogListResponse)
    async def list_catalog() -> dict[str, Any]:
        """Return known provider presets from the backend canonical catalog."""
        return {"providers": list_provider_catalog(), "version": PROVIDER_CATALOG_VERSION}

    @router.post(
        "/provider-accounts/from-catalog",
        status_code=201,
        response_model=ProviderAccountResponse,
    )
    async def create_account_from_catalog(
        body: ProviderAccountFromCatalogRequest, request: Request
    ) -> dict[str, Any]:
        """Create or update a provider account using catalog defaults and required field rules."""
        require_write(request)
        entry = _catalog_or_404(body.provider_id)
        account_payload = {
            "providerId": canonical_provider_id(body.provider_id),
            "displayName": body.display_name or entry.display_name,
            "providerType": entry.provider_type,
            "apiFormat": entry.api_format,
            "baseUrl": _base_url_for_request(entry, body),
            "credentialRef": _credential_ref_for_request(entry, body),
            "enabled": body.enabled,
            "quotaMode": "none",
            "metadata": _catalog_metadata(entry, body.metadata),
        }
        try:
            provider = providers().upsert_provider_account(account_payload)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid credential_ref: {error}") from error
        audit(
            "provider_catalog.account.upserted",
            provider["providerId"],
            {"providerId": provider["providerId"], "catalogId": entry.id},
        )
        return {"provider": provider}

    @router.post(
        "/provider-accounts/{account_id}/sync-models",
        response_model=DiscoverModelsResponse,
    )
    async def sync_provider_account_models(account_id: str, request: Request) -> dict[str, Any]:
        """Synchronize model_catalog for one provider account through its configured adapter."""
        require_write(request)
        try:
            account = providers().get_provider_account(account_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        _catalog_or_404(str(account["providerId"]))
        _validate_sync_preconditions(account, runtime_repo=runtimes())
        provider_id = str(account["providerId"])
        try:
            discovered = [
                item.model_dump(by_alias=True)
                for item in provider_instance(provider_id, connection=platform.connection).list_models()
            ]
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=redact_secrets(f"Model sync failed for {provider_id}: {error}"),
            ) from error
        stored = [
            providers().upsert_model(
                {
                    **item,
                    "providerId": provider_id,
                    "enabled": True,
                    "source": f"provider_account_sync:{provider_id}",
                }
            )
            for item in discovered
        ]
        audit(
            "provider_catalog.account.models_synced",
            provider_id,
            {"providerId": provider_id, "count": len(stored)},
        )
        return {"models": stored}

    return router
