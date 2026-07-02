"""FastAPI router for installing, listing, enabling, disabling, and validating plugins.

Every mutation revalidates the manifest fail-closed and appends install/audit events, so
blocked third-party plugins always leave an inspectable reason trail.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.plugins.manifest import (
    MANIFEST_FILE,
    PluginValidationError,
    load_and_validate_manifest,
    scan_local_candidates,
    sha256_file,
)
from local_control_center.shared.event_bus import EventBus

from .models import (
    PluginInstallEventsResponse,
    PluginInstallLocalRequest,
    PluginResponse,
    PluginScanLocalRequest,
    PluginScanLocalResponse,
    PluginsListResponse,
    PluginValidationResponse,
)
from .repository import PluginsRepository


def _blocked_manifest_identity(plugin_path: str) -> tuple[str | None, str | None]:
    manifest_path = Path(plugin_path).expanduser().resolve(strict=False) / MANIFEST_FILE
    if not manifest_path.exists() or not manifest_path.is_file():
        return None, None
    manifest_hash = sha256_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, manifest_hash
    plugin_id = manifest.get("id") if isinstance(manifest, dict) else None
    return str(plugin_id) if plugin_id else None, manifest_hash


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build the plugins router bound to the active platform connection."""
    router = APIRouter()

    def repository() -> PluginsRepository:
        return PluginsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/plugins", response_model=PluginsListResponse)
    async def list_plugins() -> dict[str, Any]:
        """Return installed plugins with their active version contracts."""
        return {"plugins": repository().list_plugins()}

    @router.get("/api/v1/plugins/install-events", response_model=PluginInstallEventsResponse)
    async def list_plugin_install_events(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List plugin install lifecycle events, optionally filtered by status (e.g. ``blocked``)."""
        bounded_limit = min(max(limit, 1), 500)
        return {"events": repository().list_install_events(status=status, limit=bounded_limit)}

    @router.post("/api/v1/plugins/scan-local", response_model=PluginScanLocalResponse)
    async def scan_local_plugins(body: PluginScanLocalRequest, request: Request) -> dict[str, Any]:
        """Scan a local folder for plugin candidates without installing or executing anything."""
        require_write(request)
        try:
            candidates = scan_local_candidates(body.path)
        except PluginValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        installed = repository().installed_version_statuses()
        for candidate in candidates:
            key = (candidate.get("id"), candidate.get("version"))
            status = installed.get(key) if key[0] and key[1] else None
            candidate["installed"] = status is not None
            candidate["installedStatus"] = status
        return {
            "root": str(Path(body.path).expanduser().resolve(strict=False)),
            "candidates": candidates,
        }

    @router.post("/api/v1/plugins/install-local", status_code=201, response_model=PluginResponse)
    async def install_local_plugin(body: PluginInstallLocalRequest, request: Request) -> dict[str, Any]:
        """Install a local plugin directory after strict manifest validation."""
        require_write(request)
        repo = repository()
        try:
            validated = load_and_validate_manifest(body.path)
        except PluginValidationError as exc:
            plugin_id, manifest_hash = _blocked_manifest_identity(body.path)
            repo.record_install_event(
                plugin_id=plugin_id,
                plugin_version_id=None,
                action="install_local",
                status="blocked",
                reason=str(exc),
                manifest_hash=manifest_hash,
                payload={"path": body.path},
            )
            event_bus().record_audit(
                action="plugin.install_local.blocked",
                target=plugin_id or body.path,
                payload={"reason": str(exc), "manifestHash": manifest_hash},
                actor="operator",
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        plugin = repo.install_validated_manifest(validated)
        event_bus().record_event(
            event_type="plugin.installed",
            payload={
                "pluginId": plugin["id"],
                "status": plugin["status"],
                "manifestHash": validated.manifest_hash,
            },
        )
        event_bus().record_audit(
            action="plugin.install_local",
            target=plugin["id"],
            payload={
                "status": plugin["status"],
                "manifestHash": validated.manifest_hash,
                "packageHash": validated.package_hash,
            },
            actor="operator",
        )
        return {"plugin": plugin}

    @router.post("/api/v1/plugins/{plugin_id}/enable", status_code=202, response_model=PluginResponse)
    async def enable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
        """Enable an installed plugin only after revalidating its active manifest."""
        require_write(request)
        repo = repository()
        try:
            active_version = repo.active_version(plugin_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            validated = load_and_validate_manifest(Path(active_version["manifestPath"]).parent)
        except PluginValidationError as exc:
            repo.mark_version_validated(active_version["id"], "invalid")
            repo.record_install_event(
                plugin_id=plugin_id,
                plugin_version_id=active_version["id"],
                action="enable",
                status="blocked",
                reason=str(exc),
                manifest_hash=active_version["manifestHash"],
                payload={"manifestPath": active_version["manifestPath"]},
            )
            event_bus().record_audit(
                action="plugin.enable.blocked",
                target=plugin_id,
                payload={"reason": str(exc), "manifestHash": active_version["manifestHash"]},
                actor="operator",
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        repo.mark_version_validated(active_version["id"], "valid")
        plugin = repo.set_plugin_status(plugin_id, "enabled")
        event_bus().record_event(
            event_type="plugin.enabled",
            payload={"pluginId": plugin_id, "manifestHash": validated.manifest_hash},
        )
        event_bus().record_audit(
            action="plugin.enable",
            target=plugin_id,
            payload={"manifestHash": validated.manifest_hash, "packageHash": validated.package_hash},
            actor="operator",
        )
        return {"plugin": plugin}

    @router.post("/api/v1/plugins/{plugin_id}/disable", status_code=202, response_model=PluginResponse)
    async def disable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
        """Disable an installed plugin and append audit/install lifecycle records."""
        require_write(request)
        repo = repository()
        try:
            plugin = repo.set_plugin_status(plugin_id, "disabled")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        event_bus().record_event(event_type="plugin.disabled", payload={"pluginId": plugin_id})
        event_bus().record_audit(
            action="plugin.disable",
            target=plugin_id,
            payload={"activeVersionId": plugin["activeVersionId"]},
            actor="operator",
        )
        return {"plugin": plugin}

    @router.post("/api/v1/plugins/{plugin_id}/validate", response_model=PluginValidationResponse)
    async def validate_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
        """Revalidate the active plugin manifest and record the result for auditability."""
        require_write(request)
        repo = repository()
        try:
            active_version = repo.active_version(plugin_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            validated = load_and_validate_manifest(Path(active_version["manifestPath"]).parent)
        except PluginValidationError as exc:
            repo.mark_version_validated(active_version["id"], "invalid")
            repo.record_install_event(
                plugin_id=plugin_id,
                plugin_version_id=active_version["id"],
                action="validate",
                status="invalid",
                reason=str(exc),
                manifest_hash=active_version["manifestHash"],
                payload={"manifestPath": active_version["manifestPath"]},
            )
            event_bus().record_audit(
                action="plugin.validate",
                target=plugin_id,
                payload={"valid": False, "reason": str(exc)},
                actor="operator",
            )
            return {"valid": False, "issues": [str(exc)], "plugin": repo.get_plugin(plugin_id)}
        repo.mark_version_validated(active_version["id"], "valid")
        repo.record_install_event(
            plugin_id=plugin_id,
            plugin_version_id=active_version["id"],
            action="validate",
            status="valid",
            reason="Active plugin manifest validated.",
            manifest_hash=validated.manifest_hash,
            payload={"manifestPath": active_version["manifestPath"]},
        )
        event_bus().record_audit(
            action="plugin.validate",
            target=plugin_id,
            payload={"valid": True, "manifestHash": validated.manifest_hash},
            actor="operator",
        )
        return {"valid": True, "issues": [], "plugin": repo.get_plugin(plugin_id)}

    return router
