"""Router HTTP del CredentialManager: ciclo de vida de secretos sin exponer valores.

La API acepta valores de secreto solo en operaciones mutantes (crear/rotar) y responde siempre con
metadatos seguros. SQLite conserva referencias, fingerprints internos, metadata y auditoría; los
valores quedan exclusivamente en el backend configurado (keyring por defecto, OpenBao/Vault,
dpapi_sqlite o environment_override para bootstrap).

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from local_control_center.runtime_integrations.env_migration import migrate_environment_config

from .backends import CredentialBackend, KeyringBackend, default_backends
from .manager import DEFAULT_BACKEND, CredentialError, CredentialManager
from .repository import CredentialRepository

SUPPORTED_BACKENDS: tuple[str, ...] = (
    "keyring",
    "openbao",
    "vault",
    "dpapi_sqlite",
    "environment_override",
)
PUBLIC_REF_SOURCES = {*SUPPORTED_BACKENDS, "env"}
RAW_SECRET_PATTERN = re.compile(
    r"(?i)(^bearer\s+|^sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*=|secret\s*=|token\s*=)"
)


class CredentialApiModel(BaseModel):
    """Base de modelos Pydantic que acepta aliases camelCase en requests y responses."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CredentialProviderUsageRecord(CredentialApiModel):
    """Proveedor que referencia una credencial gestionada; no incluye secretos ni headers."""

    provider_id: str = Field(alias="providerId")
    display_name: str = Field(alias="displayName")
    credential_ref: str = Field(alias="credentialRef")
    enabled: bool


class CredentialRecord(CredentialApiModel):
    """Metadato público de una credencial; no contiene valor, fingerprint ni sal."""

    id: str
    name: str
    label: str
    backend_kind: str = Field(alias="backendKind")
    source: str
    locator: str
    credential_ref: str = Field(alias="credentialRef")
    auth_mode: str = Field(alias="authMode")
    status: str
    enabled: bool
    fingerprint_algo: str = Field(alias="fingerprintAlgo")
    has_fingerprint: bool = Field(alias="hasFingerprint")
    rotated_at: str | None = Field(alias="rotatedAt")
    last_rotated_at: str | None = Field(alias="lastRotatedAt")
    last_validated_at: str | None = Field(alias="lastValidatedAt")
    provider_usages: list[CredentialProviderUsageRecord] = Field(alias="providerUsages")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class CredentialBackendStatus(CredentialApiModel):
    """Estado público de un adapter de secretos soportado."""

    kind: str
    configured: bool
    default: bool
    read_only: bool = Field(alias="readOnly")
    detail: str


class CredentialAuditRecord(CredentialApiModel):
    """Entrada pública de auditoría del CredentialManager; tampoco contiene secretos."""

    id: str
    credential_id: str = Field(alias="credentialId")
    name: str
    action: str
    outcome: str
    actor: str
    backend_kind: str = Field(alias="backendKind")
    detail: str
    created_at: str = Field(alias="createdAt")


class CredentialsListResponse(CredentialApiModel):
    """Respuesta de listado: credenciales seguras y adapters soportados."""

    credentials: list[CredentialRecord]
    backends: list[CredentialBackendStatus]


class CredentialResponse(CredentialApiModel):
    """Respuesta con una credencial pública."""

    credential: CredentialRecord


class CredentialCreateRequest(CredentialApiModel):
    """Payload para crear una credencial; ``value`` entra solo hacia el backend.

    Acepta tanto el contrato original (``name/backendKind/locator``) como el contrato de
    producto (``label/source/credentialRef``). La respuesta siempre devuelve ambos nombres seguros.
    """

    name: str | None = None
    label: str | None = None
    value: str
    locator: str | None = None
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    backend_kind: str | None = Field(default=None, alias="backendKind")
    source: str | None = None
    auth_mode: str = Field(default="token", alias="authMode")
    actor: str = "operator"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CredentialRotateRequest(CredentialApiModel):
    """Payload para rotar una credencial; ``value`` nunca vuelve en la respuesta."""

    value: str
    actor: str = "operator"


class CredentialValidationRecord(CredentialApiModel):
    """Resultado público de validación por fingerprint."""

    name: str
    present: bool
    valid: bool
    fingerprint_matches: bool = Field(alias="fingerprintMatches")


class CredentialValidationResponse(CredentialApiModel):
    """Respuesta de validación de una credencial."""

    validation: CredentialValidationRecord


class CredentialDeletedRecord(CredentialApiModel):
    """Resultado público de borrado."""

    name: str
    deleted: bool


class CredentialDeleteResponse(CredentialApiModel):
    """Respuesta de borrado de una credencial."""

    deleted: CredentialDeletedRecord


class CredentialAuditResponse(CredentialApiModel):
    """Respuesta de auditoría del CredentialManager."""

    audit: list[CredentialAuditRecord]


class CredentialMigrateRequest(CredentialApiModel):
    """Payload opcional para migrar configuración heredada de entorno."""

    actor: str = "operator"


class CredentialMigrationResponse(CredentialApiModel):
    """Reporte de migración desde environment overrides, sin valores de secreto."""

    report: dict[str, Any]


def _registry(platform: Any) -> dict[str, CredentialBackend]:
    override = getattr(platform, "credential_backends", None)
    if override is not None:
        return dict(override)
    return default_backends()


def _default_backend(platform: Any) -> str:
    return str(getattr(platform, "credential_default_backend", DEFAULT_BACKEND) or DEFAULT_BACKEND)


def _repository(platform: Any) -> CredentialRepository:
    return CredentialRepository(platform.connection)


def _manager(platform: Any) -> CredentialManager:
    return CredentialManager(
        _repository(platform), backends=_registry(platform), default_backend=_default_backend(platform)
    )


def _normalize_source(source: str | None) -> str | None:
    if source is None:
        return None
    normalized = source.strip()
    if normalized == "env":
        return "environment_override"
    return normalized


def _is_raw_secret_reference(value: str) -> bool:
    return bool(RAW_SECRET_PATTERN.search(value.strip()))


def _resolve_create_fields(body: CredentialCreateRequest) -> tuple[str, str | None, str]:
    label = str(body.name or body.label or "").strip()
    source = _normalize_source(body.backend_kind or body.source)
    raw_ref = str(body.locator or body.credential_ref or "").strip()
    if _is_raw_secret_reference(raw_ref):
        raise CredentialError("credentialRef must point to a supported credential reference, never a raw secret.")
    parsed_source = source
    locator = raw_ref
    if body.credential_ref and ":" in raw_ref:
        prefix, rest = raw_ref.split(":", 1)
        if prefix in PUBLIC_REF_SOURCES:
            parsed_source = _normalize_source(prefix)
            locator = rest.strip()
            if source is not None and parsed_source != source:
                raise CredentialError("credentialRef source does not match source/backendKind.")
    if parsed_source and parsed_source not in SUPPORTED_BACKENDS:
        raise CredentialError(f"Unsupported credential source: {parsed_source}")
    return label, parsed_source, locator


def _keyring_detail(backends: dict[str, CredentialBackend]) -> tuple[bool, str]:
    backend = backends.get("keyring")
    if not isinstance(backend, KeyringBackend):
        return "keyring" in backends, "Windows Credential Manager through Python keyring."
    try:
        backend._keyring()
    except Exception as error:
        return False, f"Python keyring unavailable: {error.__class__.__name__}."
    return True, "Windows Credential Manager through Python keyring."


def _backend_statuses(backends: dict[str, CredentialBackend], default_backend: str) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    details = {
        "openbao": "OpenBao KV v2; configured by AIDO_OPENBAO_ADDR and AIDO_OPENBAO_TOKEN.",
        "vault": "Vault KV v2; configured by AIDO_VAULT_ADDR and AIDO_VAULT_TOKEN.",
        "dpapi_sqlite": "Windows DPAPI-encrypted SQLite backend; configured by AIDO_DPAPI_SQLITE_PATH.",
        "environment_override": "Read-only environment override backend for bootstrap and migration.",
    }
    for kind in SUPPORTED_BACKENDS:
        configured = kind in backends
        detail = details.get(kind, "")
        if kind == "keyring":
            configured, detail = _keyring_detail(backends)
        statuses.append(
            {
                "kind": kind,
                "configured": configured,
                "default": kind == default_backend,
                "readOnly": kind == "environment_override",
                "detail": detail,
            }
        )
    return statuses


def _credential_by_id(repository: CredentialRepository, credential_id: str) -> dict[str, Any]:
    try:
        return repository.get_credential(credential_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _credential_error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


def _provider_usages(platform: Any, credential_ref: str) -> list[dict[str, Any]]:
    rows = platform.connection.execute(
        """
        SELECT provider_id, display_name, credential_ref, enabled
        FROM provider_accounts
        WHERE credential_ref = ?
        ORDER BY provider_id ASC
        """,
        (credential_ref,),
    ).fetchall()
    return [
        {
            "providerId": row["provider_id"],
            "displayName": row["display_name"],
            "credentialRef": row["credential_ref"],
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]


def _with_provider_usages(platform: Any, credential: dict[str, Any]) -> dict[str, Any]:
    credential_ref = str(credential.get("credentialRef") or "")
    return {**credential, "providerUsages": _provider_usages(platform, credential_ref)}


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Construye el APIRouter de credenciales, cableado al runtime y al guard de escritura."""
    router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])

    @router.get("", response_model=CredentialsListResponse)
    async def list_credentials() -> dict[str, Any]:
        """Lista credenciales seguras y adapters soportados."""
        backends = _registry(platform)
        return {
            "credentials": [
                _with_provider_usages(platform, credential)
                for credential in _manager(platform).list_metadata()
            ],
            "backends": _backend_statuses(backends, _default_backend(platform)),
        }

    @router.post("", status_code=201, response_model=CredentialResponse)
    async def create_credential(body: CredentialCreateRequest, request: Request) -> dict[str, Any]:
        """Crea una credencial escribiendo el valor solo en el backend configurado."""
        require_write(request)
        try:
            label, source, locator = _resolve_create_fields(body)
            credential = _manager(platform).create_credential(
                name=label,
                value=body.value,
                locator=locator,
                backend=source,
                auth_mode=body.auth_mode,
                actor=body.actor,
                metadata=body.metadata,
            )
        except (CredentialError, KeyError) as error:
            raise _credential_error(error) from error
        return {"credential": credential}

    @router.get("/audit", response_model=CredentialAuditResponse)
    async def list_credential_audit(credential_id: str | None = None) -> dict[str, Any]:
        """Lista la auditoría de credenciales, opcionalmente filtrada por id."""
        return {"audit": _repository(platform).list_audit(credential_id=credential_id)}

    @router.post("/migrate", response_model=CredentialMigrationResponse)
    async def migrate_credentials(
        request: Request, body: CredentialMigrateRequest | None = None
    ) -> dict[str, Any]:
        """Migra configuración heredada de entorno hacia referencias y metadata seguras."""
        require_write(request)
        report = migrate_environment_config(
            platform.connection,
            env=dict(os.environ),
            actor=(body.actor if body is not None else "operator"),
        )
        return {"report": report}

    @router.post("/{credential_id}/validate", response_model=CredentialValidationResponse)
    async def validate_credential(credential_id: str, request: Request) -> dict[str, Any]:
        """Valida una credencial por fingerprint sin devolver el secreto."""
        require_write(request)
        record = _credential_by_id(_repository(platform), credential_id)
        try:
            validation = _manager(platform).validate(record["name"])
        except (CredentialError, KeyError) as error:
            raise _credential_error(error) from error
        return {"validation": validation}

    @router.post("/{credential_id}/rotate", response_model=CredentialResponse)
    async def rotate_credential(
        credential_id: str, body: CredentialRotateRequest, request: Request
    ) -> dict[str, Any]:
        """Rota una credencial escribiendo el nuevo valor solo en el backend."""
        require_write(request)
        record = _credential_by_id(_repository(platform), credential_id)
        try:
            credential = _manager(platform).rotate(record["name"], body.value, actor=body.actor)
        except (CredentialError, KeyError) as error:
            raise _credential_error(error) from error
        return {"credential": _with_provider_usages(platform, credential)}

    @router.delete("/{credential_id}", response_model=CredentialDeleteResponse)
    async def delete_credential(credential_id: str, request: Request) -> dict[str, Any]:
        """Borra una credencial del backend y elimina su referencia, conservando auditoría."""
        require_write(request)
        record = _credential_by_id(_repository(platform), credential_id)
        try:
            deleted = _manager(platform).delete(record["name"])
        except (CredentialError, KeyError) as error:
            raise _credential_error(error) from error
        return {"deleted": deleted}

    return router
