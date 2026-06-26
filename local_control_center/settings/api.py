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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .models import SetSettingRequest, SettingsResponse
from .registry import descriptor_for, validate_value
from .repository import SettingsRepository
from .resolver import resolve_settings


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build the Settings router bound to ``platform``'s SQLite connection."""
    router = APIRouter()

    @router.get("/api/v1/settings", response_model=SettingsResponse)
    async def get_settings(projectId: str | None = None) -> dict[str, Any]:
        """Return all resolved settings for both general and project scopes."""
        return resolve_settings(connection=platform.connection, project_id=projectId)

    @router.put("/api/v1/settings/{key}", status_code=204)
    async def put_setting(key: str, body: SetSettingRequest, request: Request) -> Response:
        """Persist a value for a registered setting key; validates descriptor and value.

        Returns 404 if the key is not registered, 422 if the value fails validation,
        and 403 if the write token is absent or incorrect.
        """
        descriptor = descriptor_for(key)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")

        try:
            coerced = validate_value(descriptor, body.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        require_write(request)

        repo = SettingsRepository(platform.connection)
        repo.set_value(key, body.scope, body.scope_id, coerced)
        return Response(status_code=204)

    @router.delete("/api/v1/settings/{key}", status_code=204)
    async def delete_setting(
        key: str,
        request: Request,
        scope: str = "general",
        scopeId: str | None = None,
    ) -> Response:
        """Clear a setting override so resolution falls back to the inherited value."""
        require_write(request)
        repo = SettingsRepository(platform.connection)
        repo.clear_value(key, scope, scopeId)
        return Response(status_code=204)

    return router
