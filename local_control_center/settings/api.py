"""HTTP routes for the Settings plane: resolve, set, and clear persistent preferences.

Exposes three routes:
- ``GET /api/v1/settings?projectId=<id>`` — resolves all descriptors at both general and project
  scope, returning inheritance metadata so the frontend can render source chips.
- ``PUT /api/v1/settings/{key}`` — persists a value for a descriptor at the requested scope;
  validates the key (404 if unknown) and value (422 if invalid); requires the loopback token.
- ``DELETE /api/v1/settings/{key}?scope=&scopeId=`` — clears an override so it reverts to the
  inherited value; requires the loopback token.

Write routes call ``require_write(request)`` first and return 204 No Content on success so
the client re-resolves rather than trying to interpret a partial response.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from local_control_center.shared.event_bus import EventBus

from .models import SetSettingRequest, SettingsResponse
from .registry import descriptor_for, validate_value
from .repository import UNSET, SettingsRepository
from .resolver import resolve_settings


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build the Settings router bound to ``platform``'s SQLite connection."""
    router = APIRouter()

    def _audit_change(
        action: str,
        *,
        key: str,
        scope: str,
        scope_id: str | None,
        value: Any = None,
    ) -> None:
        """Deja traza de quien cambio una preferencia y a que la dejo.

        Las preferencias gobiernan cosas sensibles — umbrales del gobernador, si los runtimes CLI
        estan habilitados, si un proyecto corre en un contenedor con el workspace escribible — y
        hasta ahora la unica pista de un cambio era la columna `updated_at`.

        Se registra el valor **nuevo** y no el anterior: una preferencia puede contener una ruta
        u otro dato del operador, y la auditoria no es lugar para duplicarlo.
        """
        payload: dict[str, Any] = {"key": key, "scope": scope, "scopeId": scope_id}
        if value is not None:
            payload["value"] = value
        EventBus(platform.connection).record_audit(
            action=action,
            target=key,
            payload=payload,
            project_id=scope_id if scope == "project" else None,
            actor="operator",
        )

    @router.get("/api/v1/settings", response_model=SettingsResponse)
    def get_settings(projectId: str | None = None) -> dict[str, Any]:
        """Return all resolved settings for both general and project scopes."""
        return resolve_settings(connection=platform.connection, project_id=projectId)

    @router.put("/api/v1/settings/{key}", status_code=204)
    async def put_setting(key: str, body: SetSettingRequest, request: Request) -> Response:
        """Persist a value for a registered setting key; validates descriptor and value.

        Returns 403 if the write token is absent or incorrect, 404 if the key is not
        registered, and 422 if the value or scope/scopeId combination is invalid.
        """
        require_write(request)

        descriptor = descriptor_for(key)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")

        if body.scope == "project":
            if descriptor.project_section is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Setting {key!r} is only editable at general scope.",
                )
            if not body.scope_id:
                raise HTTPException(
                    status_code=422,
                    detail="scopeId must be a non-empty string when scope is 'project'.",
                )
            effective_scope_id = body.scope_id
        else:
            if body.scope_id is not None:
                raise HTTPException(
                    status_code=422,
                    detail="scopeId must be null/absent when scope is 'general'.",
                )
            effective_scope_id = None

        try:
            coerced = validate_value(descriptor, body.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        repo = SettingsRepository(platform.connection)
        if key in {"resources.minFreeMemoryGiB", "resources.hardFreeMemoryGiB"}:
            min_descriptor = descriptor_for("resources.minFreeMemoryGiB")
            hard_descriptor = descriptor_for("resources.hardFreeMemoryGiB")
            if min_descriptor is None or hard_descriptor is None:  # pragma: no cover - registry invariant
                raise RuntimeError("Resource memory settings are not registered.")
            stored_minimum = repo.get_value("resources.minFreeMemoryGiB", "general", None)
            stored_hard = repo.get_value("resources.hardFreeMemoryGiB", "general", None)
            effective_minimum = min_descriptor.default if stored_minimum is UNSET else float(stored_minimum)
            effective_hard = hard_descriptor.default if stored_hard is UNSET else float(stored_hard)
            if key == "resources.minFreeMemoryGiB":
                effective_minimum = float(coerced)
            else:
                effective_hard = float(coerced)
            if effective_hard > effective_minimum:
                raise HTTPException(
                    status_code=422,
                    detail="resources.hardFreeMemoryGiB cannot exceed resources.minFreeMemoryGiB.",
                )
        repo.set_value(key, body.scope, effective_scope_id, coerced)
        _audit_change(
            "settings.value_set",
            key=key,
            scope=body.scope,
            scope_id=effective_scope_id,
            value=coerced,
        )
        return Response(status_code=204)

    @router.delete("/api/v1/settings/{key}", status_code=204)
    async def delete_setting(
        key: str,
        request: Request,
        scope: str = "general",
        scopeId: str | None = None,
    ) -> Response:
        """Clear a setting override so resolution falls back to the inherited value.

        Returns 403 if the write token is absent or incorrect, 404 if the key is not
        registered, and 422 if the scope/scopeId combination is invalid.
        """
        require_write(request)

        descriptor = descriptor_for(key)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")

        if scope == "project":
            if not scopeId:
                raise HTTPException(
                    status_code=422,
                    detail="scopeId must be a non-empty string when scope is 'project'.",
                )
            effective_scope_id = scopeId
        elif scope == "general":
            if scopeId is not None:
                raise HTTPException(
                    status_code=422,
                    detail="scopeId must be absent when scope is 'general'.",
                )
            effective_scope_id = None
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid scope {scope!r}; must be 'general' or 'project'.",
            )

        repo = SettingsRepository(platform.connection)
        repo.clear_value(key, scope, effective_scope_id)
        _audit_change("settings.value_cleared", key=key, scope=scope, scope_id=effective_scope_id)
        return Response(status_code=204)

    return router
