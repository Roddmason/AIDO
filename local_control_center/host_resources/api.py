"""API operacional de recursos: estado, muestreo y recuperación de leases.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .governor import HostResourceGovernor
from .models import ResourceLeaseResponse, ResourceSampleResponse, ResourceStatusResponse
from .probes import HostResourceProbe
from .repository import ResourceRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Construye rutas de observabilidad y controles mutadores protegidos por token."""
    router = APIRouter()

    @router.get("/api/v1/operations/resources", response_model=ResourceStatusResponse)
    async def resource_status() -> dict[str, Any]:
        repository = ResourceRepository(platform.connection)
        latest = repository.latest_sample()
        waiting = platform.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'resource_wait'"
        ).fetchone()[0]
        return {
            "latestSample": latest.model_dump(by_alias=True) if latest else None,
            "activeLeases": [lease.model_dump(by_alias=True) for lease in repository.active_leases()],
            "resourceWaitCount": int(waiting),
        }

    @router.post("/api/v1/operations/resources/sample", response_model=ResourceSampleResponse)
    def sample_resources(request: Request) -> dict[str, Any]:
        require_write(request)
        repository = ResourceRepository(platform.connection)
        probe = HostResourceProbe(
            relevant_paths=[platform.cwd, platform.db_path],
            active_workload_source=lambda: [lease.workload_class for lease in repository.active_leases()],
        )
        snapshot = probe.sample()
        HostResourceGovernor(platform.connection).record_sample(snapshot)
        return {"snapshot": snapshot.model_dump(by_alias=True)}

    @router.post(
        "/api/v1/operations/resources/leases/{lease_id}/release",
        response_model=ResourceLeaseResponse,
    )
    async def release_lease(lease_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            lease = HostResourceGovernor(platform.connection).release(lease_id, reason="operator_released")
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"lease": lease.model_dump(by_alias=True)}

    return router
