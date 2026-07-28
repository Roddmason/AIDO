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
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

from .credentials import CredentialResolver
from .model_gateway_models import (
    COMPACT_ENDPOINT_ID_PATTERN,
    ApiFamily,
    DeploymentMode,
    DiscoverModelsResponse,
    PricingMode,
    ProviderAccountResponse,
    TermsMode,
)
from .provider_accounts import ProviderAccountStore
from .provider_catalog import (
    PROVIDER_CATALOG_VERSION,
    ProviderCatalogEntry,
    enrich_catalog_model,
    list_provider_catalog,
    provider_catalog_entry,
)
from .providers.factory import (
    ProviderAdapterFactory,
    ProviderAdapterResolutionError,
    provider_account_policy_kind,
    provider_account_requires_credential,
    provider_account_requires_explicit_model_manifest,
)
from .providers.nvidia_nim import NvidiaNimCapabilityError

DEPLOYMENT_MODES_REQUIRING_BASE_URL = frozenset(
    {"self_hosted_development", "self_hosted_enterprise", "partner_paid"}
)

# Mismas capabilities que scripts/setup_omniroute.py: `chat` es obligatorio para ejecutar y las
# filas de runtime_capabilities por provider_id ocultan por completo las de la familia, así que sin
# esta siembra una cuenta OmniRoute creada desde el wizard queda solo con el `chat` de la familia
# openai_compatible y los roles de build (code) y review la descartan con missing_capabilities.
OMNIROUTE_RUNTIME_CAPABILITIES = ("chat", "code_edit", "code_review")


def _seed_omniroute_runtime_capabilities(connection: Any, *, runtime_id: str) -> None:
    """Siembra las capabilities de runtime que los roles de build y review exigen al gateway."""
    timestamp = utc_now()
    metadata = json_dumps({"source": "provider_catalog_from_catalog"})
    for capability in OMNIROUTE_RUNTIME_CAPABILITIES:
        connection.execute(
            """
            INSERT INTO runtime_capabilities
                (id, runtime, capability, enabled, metadata, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(runtime, capability) DO UPDATE SET
                enabled = 1,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (f"{runtime_id}:{capability}", runtime_id, capability, metadata, timestamp, timestamp),
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
    provider_family: str = Field(alias="providerFamily")
    deployment_mode: DeploymentMode = Field(alias="deploymentMode")
    api_family: ApiFamily = Field(alias="apiFamily")
    adapter_profile: str = Field(alias="adapterProfile")
    terms_mode: TermsMode = Field(alias="termsMode")
    pricing_mode: PricingMode = Field(alias="pricingMode")
    aliases: list[str] = Field(default_factory=list)


class ProviderCatalogListResponse(CatalogApiModel):
    """Response wrapper for the canonical setup catalog."""

    providers: list[ProviderCatalogEntryRecord]
    version: str


class ProviderAccountFromCatalogRequest(CatalogApiModel):
    """Request to create/update a provider account from a catalog preset."""

    provider_id: str = Field(alias="providerId")
    instance_id: str | None = Field(
        default=None,
        alias="instanceId",
        min_length=2,
        max_length=96,
        pattern=COMPACT_ENDPOINT_ID_PATTERN,
    )
    display_name: str | None = Field(default=None, alias="displayName")
    base_url: str | None = Field(default=None, alias="baseUrl")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    deployment_mode: DeploymentMode | None = Field(default=None, alias="deploymentMode")
    api_family: ApiFamily | None = Field(default=None, alias="apiFamily")
    adapter_profile: str | None = Field(
        default=None,
        alias="adapterProfile",
        min_length=2,
        max_length=96,
        pattern=COMPACT_ENDPOINT_ID_PATTERN,
    )
    terms_mode: TermsMode | None = Field(default=None, alias="termsMode")
    pricing_mode: PricingMode | None = Field(default=None, alias="pricingMode")
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


def provider_instance(provider_id: str, *, connection: Any) -> Any:
    """Resolve a capability-neutral endpoint adapter for catalog synchronization."""
    return ProviderAdapterFactory(connection).resolve(provider_id)


def _catalog_or_404(provider_id: str) -> ProviderCatalogEntry:
    entry = provider_catalog_entry(provider_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider is not in the catalog: {provider_id}")
    return entry


def _catalog_for_account(account: dict[str, Any]) -> ProviderCatalogEntry:
    """Resolve an account's preset without deriving family from its endpoint id."""
    metadata = account.get("metadata")
    if isinstance(metadata, dict):
        stored_catalog_id = str(metadata.get("providerCatalogId") or "").strip()
        if stored_catalog_id:
            return _catalog_or_404(stored_catalog_id)

    for legacy_catalog_id in (account.get("providerFamily"), account.get("providerId")):
        entry = provider_catalog_entry(str(legacy_catalog_id or "").strip())
        if entry is not None:
            return entry
    raise HTTPException(
        status_code=404,
        detail=f"Provider account has no catalog preset: {account.get('providerId')}",
    )


def _normalize_base_url(value: str) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="baseUrl must be an absolute http(s) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(status_code=422, detail="baseUrl must not contain URL userinfo.")
    if parsed.params or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="baseUrl must not include params, query or fragment.")
    return candidate.rstrip("/")


def _base_url_for_request(entry: ProviderCatalogEntry, body: ProviderAccountFromCatalogRequest) -> str:
    provided = str(body.base_url or "").strip()
    if provided:
        return _normalize_base_url(provided)
    deployment_mode = body.deployment_mode or entry.deployment_mode
    api_family = body.api_family or entry.api_family
    if entry.provider_family == "nvidia_nim" and api_family in {
        "rerank",
        "image_generation",
        "image_editing",
    }:
        raise HTTPException(
            status_code=422,
            detail=f"baseUrl is required for NVIDIA NIM apiFamily {api_family}.",
        )
    if entry.provider_family == "nvidia_nim" and deployment_mode == "custom":
        raise HTTPException(
            status_code=422,
            detail="baseUrl is required for NVIDIA NIM custom deployments.",
        )
    if deployment_mode in DEPLOYMENT_MODES_REQUIRING_BASE_URL:
        raise HTTPException(
            status_code=422,
            detail=f"baseUrl is required for deploymentMode {deployment_mode}.",
        )
    if "baseUrl" in entry.required_fields:
        raise HTTPException(status_code=422, detail=f"baseUrl is required for provider {entry.id}.")
    return entry.default_base_url or ""


def _credential_ref_for_request(entry: ProviderCatalogEntry, body: ProviderAccountFromCatalogRequest) -> str:
    credential_ref = str(body.credential_ref or "").strip()
    deployment_mode = body.deployment_mode or entry.deployment_mode
    credential_required = "credentialRef" in entry.required_fields and not str(deployment_mode).startswith(
        "self_hosted"
    )
    if credential_required and not credential_ref:
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


def _validate_catalog_semantics(
    entry: ProviderCatalogEntry,
    body: ProviderAccountFromCatalogRequest,
) -> None:
    deployment_mode = body.deployment_mode or entry.deployment_mode
    pricing_mode = body.pricing_mode or entry.pricing_mode
    if entry.provider_family == "gemini":
        if pricing_mode == "free" and body.metadata.get("freeTierDeclaredByOperator") is not True:
            raise HTTPException(
                status_code=422,
                detail="Gemini Free tier requires explicit operator attestation.",
            )
        return
    if entry.provider_family != "nvidia_nim" or deployment_mode != "hosted_trial":
        return
    terms_mode = body.terms_mode or entry.terms_mode
    if terms_mode != "evaluation" or pricing_mode != "unknown":
        raise HTTPException(
            status_code=422,
            detail=("NVIDIA NIM hosted_trial requires termsMode evaluation and pricingMode unknown."),
        )


def _requires_remote_policy(account: dict[str, Any]) -> bool:
    return provider_account_policy_kind(account) in {"api", "gateway"}


def _requires_credential(account: dict[str, Any]) -> bool:
    return provider_account_requires_credential(account)


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
            provider_id=provider_id,
            provider_family=str(account.get("providerFamily") or ""),
            kind=provider_account_policy_kind(account),
        )
        if not decision.get("allowed"):
            raise HTTPException(
                status_code=403,
                detail=str(decision.get("reason") or "Remote provider sync is disabled."),
            )


def _validate_sync_credentials(account: dict[str, Any]) -> None:
    provider_id = str(account["providerId"])
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
        _validate_catalog_semantics(entry, body)
        instance_id = body.instance_id or entry.id
        account_payload = {
            "providerId": instance_id,
            "displayName": body.display_name or entry.display_name,
            "providerType": entry.provider_type,
            "apiFormat": entry.api_format,
            "providerFamily": entry.provider_family,
            "deploymentMode": body.deployment_mode or entry.deployment_mode,
            "apiFamily": body.api_family or entry.api_family,
            "adapterProfile": body.adapter_profile or entry.adapter_profile,
            "termsMode": body.terms_mode or entry.terms_mode,
            "pricingMode": body.pricing_mode or entry.pricing_mode,
            "baseUrl": _base_url_for_request(entry, body),
            "credentialRef": _credential_ref_for_request(entry, body),
            "enabled": body.enabled,
            "quotaMode": "provider_reported" if entry.provider_family == "gemini" else "none",
            "metadata": _catalog_metadata(entry, body.metadata),
        }
        try:
            provider_store = providers()
            if body.instance_id is None:
                provider = provider_store.upsert_provider_account(account_payload)
            else:
                with immediate_transaction(platform.connection):
                    try:
                        provider_store.get_provider_account(instance_id)
                    except KeyError:
                        pass
                    else:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Provider instance already exists: {instance_id}. "
                                "Update it through PATCH "
                                f"/api/v1/model-gateway/providers/{instance_id}."
                            ),
                        )
                    provider = provider_store.upsert_provider_account(account_payload)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid provider account: {error}") from error
        if entry.id == "omniroute":
            _seed_omniroute_runtime_capabilities(platform.connection, runtime_id=instance_id)
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
        catalog_entry = _catalog_for_account(account)
        _validate_sync_preconditions(account, runtime_repo=runtimes())
        provider_id = str(account["providerId"])
        if provider_account_requires_explicit_model_manifest(account):
            raise HTTPException(status_code=409, detail="explicit_model_manifest_required")
        try:
            provider = provider_instance(provider_id, connection=platform.connection)
        except ProviderAdapterResolutionError as error:
            detail = getattr(error, "public_code", error.code)
            raise HTTPException(status_code=409, detail=detail) from error
        _validate_sync_credentials(account)
        try:
            discovered = [item.model_dump(by_alias=True) for item in provider.list_models()]
        except NvidiaNimCapabilityError as error:
            status_code = 502 if error.code.startswith("provider_") else 409
            raise HTTPException(status_code=status_code, detail=error.code) from error
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=redact_secrets(f"Model sync failed for {provider_id}: {error}"),
            ) from error
        api_family = str(account.get("apiFamily") or "")
        excluded_prefixes = catalog_entry.excluded_model_prefixes

        def excluded_by_catalog_rule(model_name: str) -> bool:
            return any(model_name.startswith(prefix) for prefix in excluded_prefixes)

        excluded_count = 0
        stored: list[dict[str, Any]] = []
        for item in discovered:
            if excluded_by_catalog_rule(str(item.get("model") or "")):
                excluded_count += 1
                continue
            enriched = enrich_catalog_model(catalog_entry, item)
            stored.append(
                providers().upsert_model(
                    {
                        **enriched,
                        "providerId": provider_id,
                        "apiFamily": api_family,
                        "supportsEmbeddings": api_family == "embeddings",
                        "supportsRerank": api_family == "rerank",
                        "enabled": True,
                        "source": enriched.get("source", f"provider_account_sync:{provider_id}"),
                    }
                )
            )
        # La exclusión es regla de proyecto, no preferencia: el sync también apaga las filas que
        # un sync anterior (sin la regla) dejó habilitadas, para que resincronizar sea curativo.
        if excluded_prefixes:
            for row in providers().list_models(provider_id):
                if row.get("enabled") and excluded_by_catalog_rule(str(row.get("model") or "")):
                    providers().upsert_model({**row, "enabled": False})
        audit(
            "provider_catalog.account.models_synced",
            provider_id,
            {
                "providerId": provider_id,
                "catalogId": catalog_entry.id,
                "count": len(stored),
                "excludedByCatalogRule": excluded_count,
            },
        )
        return {"models": stored}

    return router
