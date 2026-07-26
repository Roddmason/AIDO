"""Router HTTP del slice Git Workspace.

Expone estado Git, ramas, checkout, diff y gitleaks por proyecto. Las mutaciones exigen el
token local y la ejecucion real se delega a ``GitWorkspaceService``. Los GET de snapshot
(status/branches) corren su fase subprocess fuera del lock global de /api/ sobre una conexion
sqlite dedicada, con single-flight por proyecto para no apilar snapshots concurrentes.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Request

from local_control_center.shared.db import open_sqlite_connection

from .models import (
    GitBranchCreateRequest,
    GitBranchesResponse,
    GitBranchMutationResponse,
    GitBranchPolicyApplyRequest,
    GitBranchPolicyApplyResponse,
    GitCheckoutRequest,
    GitCheckoutResponse,
    GitDiffResponse,
    GitGitleaksScanResponse,
    GitInitRequest,
    GitInitResponse,
    GitRemoteAddRequest,
    GitRemoteMutationResponse,
    GitRemoteTestRequest,
    GitRemoteTestResponse,
    GitStatusResponse,
)
from .service import GIT_TIMEOUT_SECONDS, GitWorkspaceService, branches_view_from_status

GIT_SNAPSHOT_PATH_PATTERN = re.compile(r"^/api/v1/projects/[^/]+/git/(?:status|branches)$")
# Espera máxima de un request que comparte un snapshot en vuelo: sobre el peor caso de los
# 7 comandos git (7 x GIT_TIMEOUT_SECONDS) para que el fallback a snapshot propio solo
# dispare si el dueño del vuelo murió sin publicar resultado.
GIT_SNAPSHOT_WAIT_SECONDS = GIT_TIMEOUT_SECONDS * 8


def is_git_snapshot_request(method: str, path: str) -> bool:
    """Indica si el request es un GET de snapshot git (status/branches) exento del lock global.

    Única fuente de verdad para el middleware de ``api.py``: estos dos GET corren su fase
    sqlite bajo el lock por su cuenta y ejecutan los subprocess git fuera de él. Solo GET:
    el POST de branches (crear rama) es mutación y sigue serializado como el resto de /api/.
    """
    return method.upper() == "GET" and GIT_SNAPSHOT_PATH_PATTERN.match(path) is not None


@dataclass
class _SnapshotFlight:
    """Snapshot en vuelo, compartido por los requests concurrentes del mismo proyecto."""

    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


def create_router(
    *, platform: Any, require_write: Callable[[Request], None], snapshot_lock: threading.Lock
) -> APIRouter:
    """Arma el router Git por proyecto.

    ``snapshot_lock`` es el mismo lock global que serializa /api/: los GET de snapshot lo toman
    solo durante su fase sqlite corta (``prepare_status``) y sueltan el resto de la request.
    """
    router = APIRouter()
    in_flight_snapshots: dict[str, _SnapshotFlight] = {}
    in_flight_guard = threading.Lock()

    def service() -> GitWorkspaceService:
        return GitWorkspaceService(platform.connection, root=Path(platform.cwd))

    def execute_status_snapshot(project_id: str) -> dict[str, Any]:
        """Corre un snapshot completo: fase sqlite bajo el lock global, subprocess sin él."""
        snapshot_lock.acquire()
        try:
            early, prepared = service().prepare_status(project_id)
        finally:
            snapshot_lock.release()
        if early is not None or prepared is None:
            return early or {}
        connection = open_sqlite_connection(platform.db_path)
        try:
            collector = GitWorkspaceService(connection, root=Path(platform.cwd))
            return collector.collect_status(prepared)
        finally:
            connection.close()

    def run_status_snapshot(project_id: str) -> dict[str, Any]:
        """Single-flight por proyecto: requests solapados comparten una sola ejecución brokered."""
        with in_flight_guard:
            flight = in_flight_snapshots.get(project_id)
            owns_flight = flight is None
            if flight is None:
                flight = _SnapshotFlight()
                in_flight_snapshots[project_id] = flight
        if not owns_flight:
            if flight.done.wait(timeout=GIT_SNAPSHOT_WAIT_SECONDS):
                if flight.error is not None:
                    raise flight.error
                if flight.result is not None:
                    return flight.result
            # El dueño del vuelo no publicó (proceso colgado): degradar a snapshot propio.
            return execute_status_snapshot(project_id)
        try:
            result = execute_status_snapshot(project_id)
            flight.result = result
            return result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            flight.done.set()
            with in_flight_guard:
                in_flight_snapshots.pop(project_id, None)

    @router.get("/api/v1/projects/{project_id}/git/status", response_model=GitStatusResponse)
    async def git_status(project_id: str) -> dict[str, Any]:
        try:
            return await anyio.to_thread.run_sync(run_status_snapshot, project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/v1/projects/{project_id}/git/branches", response_model=GitBranchesResponse)
    async def git_branches(project_id: str) -> dict[str, Any]:
        try:
            snapshot = await anyio.to_thread.run_sync(run_status_snapshot, project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return branches_view_from_status(snapshot, project_id=project_id)

    @router.post("/api/v1/projects/{project_id}/git/init", response_model=GitInitResponse)
    async def init_git_repository(project_id: str, body: GitInitRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().init_repository(project_id, default_branch=body.default_branch)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/projects/{project_id}/git/remotes", response_model=GitRemoteMutationResponse)
    async def add_git_remote(project_id: str, body: GitRemoteAddRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().add_remote(project_id, name=body.name, url=body.url)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/remotes/{name}/test",
        response_model=GitRemoteTestResponse,
    )
    async def test_git_remote(
        project_id: str, name: str, body: GitRemoteTestRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().test_remote(project_id, name=name, allow_network=body.allow_network)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branches",
        status_code=201,
        response_model=GitBranchMutationResponse,
    )
    async def create_git_branch(
        project_id: str, body: GitBranchCreateRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().create_branch(project_id, name=body.name, base=body.base)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/branch-policy/apply",
        response_model=GitBranchPolicyApplyResponse,
    )
    async def apply_git_branch_policy(
        project_id: str, body: GitBranchPolicyApplyRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().apply_branch_policy(
                project_id,
                intent=body.intent,
                selected_base=body.selected_base,
                branch_name=body.branch_name,
                create_branch=body.create_branch,
                allow_protected=body.allow_protected,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/projects/{project_id}/git/checkout", response_model=GitCheckoutResponse)
    async def checkout_git_branch(
        project_id: str, body: GitCheckoutRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return service().checkout(project_id, branch=body.branch, allow_dirty=body.allow_dirty)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/v1/projects/{project_id}/git/diff", response_model=GitDiffResponse)
    async def git_diff(project_id: str) -> dict[str, Any]:
        try:
            return service().diff(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/projects/{project_id}/git/gitleaks/scan",
        response_model=GitGitleaksScanResponse,
    )
    async def git_gitleaks_scan(project_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return service().gitleaks_scan(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
