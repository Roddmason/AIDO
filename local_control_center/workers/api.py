"""HTTP API for LocalWorkerRuntime status and operator controls.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anyio
from fastapi import APIRouter, Request

from .models import WorkerRunOnceResponse, WorkerStatusResponse
from .runtime import LocalWorkerRuntime


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build worker control routes bound to the process-local runtime."""
    router = APIRouter()

    def worker() -> LocalWorkerRuntime:
        runtime = getattr(platform, "local_worker_runtime", None)
        if runtime is None:
            runtime = LocalWorkerRuntime.from_settings(
                connection=platform.connection,
                db_path=platform.db_path,
                cwd=platform.cwd,
            )
            platform.local_worker_runtime = runtime
        runtime.refresh_settings(platform.connection)
        return runtime

    @router.get("/api/v1/workers/status", response_model=WorkerStatusResponse)
    async def worker_status() -> dict[str, Any]:
        """Return the current local worker status."""
        return await anyio.to_thread.run_sync(worker().status)

    @router.post("/api/v1/workers/run-once", response_model=WorkerRunOnceResponse)
    async def worker_run_once(request: Request) -> dict[str, Any]:
        """Run one bounded batch of queued jobs now."""
        require_write(request)
        return await anyio.to_thread.run_sync(worker().run_once)

    @router.post("/api/v1/workers/pause", response_model=WorkerStatusResponse)
    async def worker_pause(request: Request) -> dict[str, Any]:
        """Pause the local worker loop and manual run-once execution."""
        require_write(request)
        return await anyio.to_thread.run_sync(worker().pause)

    @router.post("/api/v1/workers/resume", response_model=WorkerStatusResponse)
    async def worker_resume(request: Request) -> dict[str, Any]:
        """Resume or start the local worker loop."""
        require_write(request)
        return await anyio.to_thread.run_sync(worker().resume)

    return router
