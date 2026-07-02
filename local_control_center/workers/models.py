"""Pydantic contracts for the LocalWorkerRuntime control API.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Aliased(BaseModel):
    """Base that accepts Python field names and emits camelCase aliases on the wire."""

    model_config = ConfigDict(populate_by_name=True)


WorkerRuntimeState = Literal["running", "stopped", "paused", "idle", "blocked", "failed", "completed"]


class WorkerStatusResponse(_Aliased):
    """Status snapshot of the local worker runtime."""

    status: WorkerRuntimeState
    running: bool
    paused: bool
    autostart: bool
    reason: str
    max_concurrent_jobs: int = Field(alias="maxConcurrentJobs")
    poll_interval_seconds: float = Field(alias="pollIntervalSeconds")
    in_flight_jobs: int = Field(alias="inFlightJobs")
    claimed_jobs: int = Field(alias="claimedJobs")
    completed_runs: int = Field(alias="completedRuns")
    failed_runs: int = Field(alias="failedRuns")
    last_run_at: str | None = Field(default=None, alias="lastRunAt")
    last_idle_at: str | None = Field(default=None, alias="lastIdleAt")
    last_error: str | None = Field(default=None, alias="lastError")


class WorkerRunOnceResponse(WorkerStatusResponse):
    """Manual worker execution result: status plus the runs closed by this batch."""

    runs: list[dict[str, Any]]
