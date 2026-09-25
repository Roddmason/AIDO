"""Contratos HTTP de endpoints locales y detección de runtimes, con Literal acotados para el cliente generado.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.agents.provider_accounts import MAX_LOCAL_CONCURRENCY_LIMIT

LoadStateValue = Literal["loaded", "loading", "unloaded", "unknown"]
EndpointLocalityValue = Literal["loopback", "declared_local", "remote"]
NetworkScopeValue = Literal["loopback", "private_network", "declared_local", "public"]
DiscoveredServerValue = Literal["llama_cpp", "lm_studio", "vllm", "unknown_openai_compatible"]


class _LocalRuntimesModel(BaseModel):
    """Base con alias camelCase para los contratos de endpoints locales."""

    model_config = ConfigDict(populate_by_name=True)


class _StrictRequest(_LocalRuntimesModel):
    """Base de los cuerpos de escritura: rechaza campos desconocidos, p. ej. ``providerCatalogId``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LocalModelView(_LocalRuntimesModel):
    """Modelo de un endpoint local con su configuración, estado de carga y validación reciente."""

    model: str
    enabled: bool
    is_default: bool = Field(alias="isDefault")
    code_edit: bool = Field(alias="codeEdit")
    code_review: bool = Field(alias="codeReview")
    operator_order: int = Field(alias="operatorOrder")
    load_state: LoadStateValue = Field(alias="loadState")
    validated: bool
    validated_at: str | None = Field(default=None, alias="validatedAt")


class LocalEndpointView(_LocalRuntimesModel):
    """Endpoint local configurado, sin secretos, tal como lo muestran el panel y el rollup de runtimes."""

    id: str
    catalog_id: str = Field(alias="catalogId")
    display_name: str = Field(alias="displayName")
    base_url: str = Field(alias="baseUrl")
    enabled: bool
    locality: EndpointLocalityValue
    network_scope: NetworkScopeValue = Field(alias="networkScope")
    declared_local: bool = Field(alias="declaredLocal")
    health_status: str | None = Field(default=None, alias="healthStatus")
    health_checked_at: str | None = Field(default=None, alias="healthCheckedAt")
    health_reason: str | None = Field(default=None, alias="healthReason")
    loaded_models: list[str] = Field(default_factory=list, alias="loadedModels")
    models: list[LocalModelView] = Field(default_factory=list)
    concurrency_limit: int = Field(alias="concurrencyLimit")
    has_credential: bool = Field(alias="hasCredential")


class LocalEndpointsListResponse(_LocalRuntimesModel):
    """Respuesta con todos los endpoints locales configurados."""

    endpoints: list[LocalEndpointView]


class LocalEndpointCreateRequest(_StrictRequest):
    """Alta de un endpoint desde una entrada local del catálogo; la identidad la escribe el servidor."""

    catalog_id: str = Field(alias="catalogId", min_length=2, max_length=96)
    base_url: str | None = Field(default=None, alias="baseUrl", max_length=2048)
    instance_id: str | None = Field(default=None, alias="instanceId", max_length=96)
    display_name: str | None = Field(default=None, alias="displayName", max_length=120)
    credential_ref: str | None = Field(default=None, alias="credentialRef", max_length=512)


class LocalEndpointPatchRequest(_StrictRequest):
    """Edición parcial de un endpoint local; un campo omitido conserva su valor."""

    base_url: str | None = Field(default=None, alias="baseUrl", max_length=2048)
    display_name: str | None = Field(default=None, alias="displayName", max_length=120)
    enabled: bool | None = None
    credential_ref: str | None = Field(default=None, alias="credentialRef", max_length=512)
    concurrency_limit: int | None = Field(
        default=None, alias="concurrencyLimit", ge=1, le=MAX_LOCAL_CONCURRENCY_LIMIT
    )


class LocalEndpointDeclareRequest(_StrictRequest):
    """Declaración (o retiro) de que el endpoint corre en este equipo vía WSL o Docker."""

    declared: bool


class LocalModelPatchRequest(_StrictRequest):
    """Configuración por modelo de un endpoint local; un campo omitido conserva su valor."""

    model: str = Field(min_length=1, max_length=256)
    enabled: bool | None = None
    is_default: bool | None = Field(default=None, alias="isDefault")
    code_edit: bool | None = Field(default=None, alias="codeEdit")
    code_review: bool | None = Field(default=None, alias="codeReview")
    operator_order: int | None = Field(default=None, alias="operatorOrder", ge=0, le=10_000)


class LocalModelValidateRequest(_StrictRequest):
    """Modelo concreto del endpoint que se valida de verdad (chat + JSON)."""

    model: str = Field(min_length=1, max_length=256)


class LocalRuntimeSuggestion(_LocalRuntimesModel):
    """Servidor local detectado en loopback; nunca crea cuentas por sí mismo."""

    catalog_id: str = Field(alias="catalogId")
    base_url: str = Field(alias="baseUrl")
    server: DiscoveredServerValue
    models: list[str] = Field(default_factory=list)
    already_configured: bool = Field(alias="alreadyConfigured")
    requires_confirmation: bool = Field(alias="requiresConfirmation")


class LocalRuntimeDiscoveryResponse(_LocalRuntimesModel):
    """Resultado de la detección de runtimes locales."""

    suggestions: list[LocalRuntimeSuggestion]
