"""HTTP API for LocalWorkerRuntime status and operator controls.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.settings.repository import UNSET, SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

from .leadership import (
    DEFAULT_HEARTBEAT_STALE_SECONDS,
    WorkerControlRepository,
    WorkerLeadershipRepository,
)
from .models import WorkerRunOnceResponse, WorkerStatusResponse
from .runtime import DEFAULT_MAX_CONCURRENT_JOBS, DEFAULT_POLL_INTERVAL_SECONDS


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build worker routes over SQLite state, without owning a worker object in FastAPI."""
    router = APIRouter()

    def status_snapshot() -> dict[str, Any]:
        with closing(open_sqlite_connection(platform.db_path)) as connection:
            leadership = WorkerLeadershipRepository(connection)
            control = WorkerControlRepository(connection).get()
            lease = leadership.current()
            heartbeats = leadership.list_heartbeats()
            runs = connection.execute(
                """SELECT COUNT(*) AS claimed, COALESCE(SUM(status='running'), 0) AS active,
                COALESCE(SUM(status='completed'), 0) AS completed,
                COALESCE(SUM(status='failed'), 0) AS failed, MAX(started_at) AS last_run
                FROM job_runs"""
            ).fetchone()
            last_error = connection.execute(
                "SELECT summary FROM job_runs WHERE status='failed' ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()
            settings = SettingsRepository(connection)
            interval = settings.get_value("worker.pollIntervalSeconds", "general", None)
            max_jobs = settings.get_value("worker.maxConcurrentJobs", "general", None)
            autostart = settings.get_value("worker.autostart", "general", None)
        now = utc_now()
        fresh_after = (
            (datetime.now(UTC) - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_SECONDS))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        heartbeats = [
            row
            for row in heartbeats
            if row["heartbeatAt"] > fresh_after and row["status"] not in {"stopped", "fenced"}
        ]
        active_lease = lease if lease and str(lease["expires_at"]) > now else None
        connected = active_lease is not None
        leader_owner = str(active_lease["owner_id"]) if active_lease else None
        latest = heartbeats[0] if heartbeats else None
        if connected:
            role = "leader"
        elif latest and latest["status"] not in {"stopped", "fenced"}:
            role = "standby"
            connected = True
        else:
            role = "offline"
        desired = str(control["desiredState"])
        status = desired if connected else "stopped"
        return {
            "status": status,
            "running": connected and role == "leader" and desired == "running",
            "paused": desired == "paused",
            "autostart": False if autostart is UNSET else bool(autostart),
            "reason": str(control["reason"]),
            "maxConcurrentJobs": (
                DEFAULT_MAX_CONCURRENT_JOBS if max_jobs is UNSET else max(1, min(int(float(max_jobs)), 1))
            ),
            "pollIntervalSeconds": (DEFAULT_POLL_INTERVAL_SECONDS if interval is UNSET else float(interval)),
            "inFlightJobs": runs["active"],
            "claimedJobs": runs["claimed"],
            "completedRuns": runs["completed"],
            "failedRuns": runs["failed"],
            "lastRunAt": runs["last_run"],
            "lastIdleAt": None,
            "lastError": last_error["summary"] if last_error else None,
            "connected": connected,
            "role": role,
            "desiredState": desired,
            "ownerId": leader_owner or (str(latest["ownerId"]) if latest else None),
            "fencingToken": int(active_lease["fencing_token"]) if active_lease else None,
            "heartbeatAt": (
                str(active_lease["heartbeat_at"])
                if active_lease
                else str(latest["heartbeatAt"])
                if latest
                else None
            ),
            "leaseExpiresAt": str(active_lease["expires_at"]) if active_lease else None,
            "standbyCount": sum(heartbeat["role"] == "standby" for heartbeat in heartbeats),
        }

    def request_state(state: str, *, reason: str) -> dict[str, Any]:
        with closing(open_sqlite_connection(platform.db_path)) as connection:
            WorkerControlRepository(connection).request_state(state, reason=reason)
            EventBus(connection).record_event(
                event_type=f"worker.{state}",
                payload={"desiredState": state, "reason": reason},
            )
            EventBus(connection).record_audit(
                action=f"worker.{state}",
                target="local-worker",
                payload={"reason": reason},
                actor="operator",
            )
            if state == "paused":
                threads = ThreadsRepository(connection)
                for job in JobsRepository(connection).list_jobs():
                    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
                    thread_id = str(payload.get("threadId") or "").strip()
                    if job["status"] == "queued" and thread_id:
                        threads.record_event(
                            thread_id=thread_id,
                            type="worker_paused",
                            agent_role="worker",
                            payload={"jobId": job["id"], "reason": reason},
                        )
        return status_snapshot()

    @router.get("/api/v1/workers/status", response_model=WorkerStatusResponse)
    async def worker_status() -> dict[str, Any]:
        """Return the current local worker status."""
        return status_snapshot()

    @router.post("/api/v1/workers/run-once", status_code=202, response_model=WorkerRunOnceResponse)
    async def worker_run_once(request: Request) -> dict[str, Any]:
        """Persist one bounded-batch request for the separate worker process."""
        require_write(request)
        with closing(open_sqlite_connection(platform.db_path)) as connection:
            WorkerControlRepository(connection).request_run_once(reason="Run once requested by operator.")
            EventBus(connection).record_event(
                event_type="worker.run_once_requested",
                payload={"requestedBy": "operator"},
            )
        return {**status_snapshot(), "runs": []}

    @router.post("/api/v1/workers/pause", response_model=WorkerStatusResponse)
    async def worker_pause(request: Request) -> dict[str, Any]:
        """Pause the local worker loop and manual run-once execution."""
        require_write(request)
        return request_state("paused", reason="Worker paused by operator.")

    @router.post("/api/v1/workers/resume", response_model=WorkerStatusResponse)
    async def worker_resume(request: Request) -> dict[str, Any]:
        """Allow the separate worker leader to resume claiming jobs."""
        require_write(request)
        return request_state("running", reason="Worker resumed by operator.")

    @router.post("/api/v1/workers/drain", response_model=WorkerStatusResponse)
    async def worker_drain(request: Request) -> dict[str, Any]:
        """Stop claiming new jobs and ask the active worker to exit after current work."""
        require_write(request)
        return request_state("draining", reason="Worker drain requested by operator.")

    return router
