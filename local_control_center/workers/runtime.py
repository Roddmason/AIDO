"""LocalWorkerRuntime: non-blocking local loop that drains queued jobs under safety gates.

The runtime owns process-level scheduling only. Actual job claiming/execution stays in
``ConcurrentWorker`` so execution continues to use JobsRepository leases and the existing
Product Loop / ToolBroker / gitleaks corridor.

@author Rodrigo Mason
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.settings.repository import UNSET, SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.telemetry import prune_http_request_telemetry
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.worker import ConcurrentWorker

from .leadership import (
    DEFAULT_LEADER_LEASE_SECONDS,
    WorkerControlRepository,
    WorkerLeadershipRepository,
)

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_CONCURRENT_JOBS = 1
TELEMETRY_PRUNE_INTERVAL_SECONDS = 3600.0


@dataclass(frozen=True)
class WorkerSettings:
    """Resolved settings that control local worker scheduling."""

    autostart: bool = False
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS


@dataclass(frozen=True)
class WorkerPreflight:
    """Result of checking whether local execution is allowed before claiming jobs."""

    ok: bool
    reason: str
    runtime_ids: list[str]
    gitleaks_executable: str | None
    remediation_stage: str | None = None
    remediation_details: dict[str, Any] = field(default_factory=dict)


def resolve_worker_settings(connection: sqlite3.Connection) -> WorkerSettings:
    """Resolve general worker settings, falling back to registry defaults."""
    repo = SettingsRepository(connection)
    autostart = repo.get_value("worker.autostart", "general", None)
    interval = repo.get_value("worker.pollIntervalSeconds", "general", None)
    max_jobs = repo.get_value("worker.maxConcurrentJobs", "general", None)
    return WorkerSettings(
        autostart=False if autostart is UNSET else bool(autostart),
        poll_interval_seconds=_bounded_interval(interval if interval is not UNSET else None),
        max_concurrent_jobs=_bounded_max_jobs(max_jobs if max_jobs is not UNSET else None),
    )


def _bounded_interval(value: Any) -> float:
    try:
        parsed = float(value if value is not None else DEFAULT_POLL_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        parsed = DEFAULT_POLL_INTERVAL_SECONDS
    return max(0.25, min(parsed, 3600.0))


def _bounded_max_jobs(value: Any) -> int:
    try:
        parsed = int(float(value if value is not None else DEFAULT_MAX_CONCURRENT_JOBS))
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_CONCURRENT_JOBS
    return max(1, min(parsed, DEFAULT_MAX_CONCURRENT_JOBS))


class LocalWorkerRuntime:
    """API-controlled background runtime for local queued-job execution."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        cwd: str | Path,
        settings: WorkerSettings | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.cwd = Path(cwd)
        self.settings = settings or WorkerSettings()
        self.worker_id = f"local-worker-{uuid.uuid4()}"
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._batch_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._leadership_thread: threading.Thread | None = None
        self._status = "stopped"
        self._reason = "Worker has not started."
        self._last_run_at: str | None = None
        self._last_idle_at: str | None = None
        self._last_error: str | None = None
        self._claimed_jobs = 0
        self._completed_runs = 0
        self._failed_runs = 0
        self._in_flight_jobs = 0
        self._last_telemetry_prune_monotonic: float | None = None
        self._last_resource_sample_monotonic: float | None = None
        self._resource_sample_lock = threading.Lock()
        self._latest_resource_snapshot: ResourceSnapshot | None = None
        self._resource_probe = HostResourceProbe(
            relevant_paths=[self.cwd, self.db_path],
            active_workload_source=self._active_workload_classes,
        )
        self._fencing_token: int | None = None

    @classmethod
    def from_settings(
        cls,
        *,
        connection: sqlite3.Connection,
        db_path: str | Path,
        cwd: str | Path,
    ) -> LocalWorkerRuntime:
        """Create a runtime using the persisted general worker settings."""
        return cls(db_path=db_path, cwd=cwd, settings=resolve_worker_settings(connection))

    def refresh_settings(self, connection: sqlite3.Connection) -> None:
        """Refresh settings from SQLite so API status reflects current configuration."""
        self.settings = resolve_worker_settings(connection)

    @property
    def running(self) -> bool:
        """Whether the background thread is alive and actively allowed to poll."""
        return bool(self._thread and self._thread.is_alive() and not self._pause_event.is_set())

    @property
    def paused(self) -> bool:
        """Whether polling is paused by operator command."""
        return self._pause_event.is_set()

    def start(self) -> dict[str, Any]:
        """Start the non-blocking background loop if preflight passes."""
        if self.running:
            return self.status()
        preflight = self.preflight()
        if not preflight.ok:
            self._status = "blocked"
            self._reason = preflight.reason
            self._last_error = preflight.reason
            self._record_worker_event("worker_failed", self._preflight_failure_payload(preflight))
            self._record_queued_thread_event("worker_failed", self._preflight_failure_payload(preflight))
            return self.status()
        self._stop_event.clear()
        self._pause_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="aido-local-worker-runtime",
            daemon=True,
        )
        self._thread.start()
        self._status = "running"
        self._reason = "Worker loop is running."
        self._last_error = None
        self._record_worker_event(
            "worker_started",
            {
                "workerId": self.worker_id,
                "maxConcurrentJobs": self.settings.max_concurrent_jobs,
                "pollIntervalSeconds": self.settings.poll_interval_seconds,
                "runtimeIds": preflight.runtime_ids,
                "gitleaksExecutable": preflight.gitleaks_executable,
            },
        )
        return self.status()

    def stop(self, *, reason: str = "Worker stopped.") -> dict[str, Any]:
        """Stop the background loop and wait briefly for the daemon thread to exit."""
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        leadership_thread = self._leadership_thread
        if leadership_thread and leadership_thread.is_alive():
            leadership_thread.join(timeout=2.0)
        self._thread = None
        self._leadership_thread = None
        self._release_leadership(reason)
        self._status = "stopped"
        self._reason = reason
        return self.status()

    def run_forever(self) -> None:
        """Ejecuta el scheduler en primer plano como proceso worker independiente.

        El proceso siempre publica heartbeat, pero sólo el owner de la lease vigente puede reclamar
        jobs. El estado durable ``paused`` permite arrancarlo conectado sin ejecutar trabajo.
        """
        self._stop_event.clear()
        self._status = "standby"
        while not self._stop_event.is_set():
            connection = open_sqlite_connection(self.db_path)
            try:
                initialize_platform_schema(connection)
                self.settings = resolve_worker_settings(connection)
                leadership = WorkerLeadershipRepository(connection)
                decision = leadership.acquire(
                    owner_id=self.worker_id,
                    lease_seconds=DEFAULT_LEADER_LEASE_SECONDS,
                )
                self._fencing_token = decision.fencing_token if decision.acquired else None
                from local_control_center.shared.diagnostics import diagnostic_event

                diagnostic_event(
                    "leadership.acquire",
                    component="worker",
                    workerId=self.worker_id,
                    fencingToken=decision.fencing_token,
                    outcome=decision.role,
                )
                control = WorkerControlRepository(connection).get()
            finally:
                connection.close()
            if not decision.acquired:
                self._status = "standby"
                self._reason = decision.reason
                self._stop_event.wait(min(self.settings.poll_interval_seconds, 2.0))
                continue
            self._ensure_leadership_watcher()
            try:
                from local_control_center.process_supervision.recovery import recover_managed_processes

                recover_managed_processes(self.db_path)
            except (OSError, RuntimeError) as error:
                self._reason = f"Recuperación de procesos pendiente: {type(error).__name__}."
                self._renew_leadership(status="resource_wait")
                self._stop_event.wait(self.settings.poll_interval_seconds)
                continue
            if not self._govern_resources_if_due():
                self._renew_leadership(status="resource_wait")
                self._stop_event.wait(self.settings.poll_interval_seconds)
                continue
            desired_state = str(control["desiredState"])
            self._status = desired_state
            self._reason = str(control["reason"] or f"Worker is {desired_state}.")
            if desired_state in {"stopped", "emergency_stopped"}:
                self._release_leadership(self._reason)
                return
            if desired_state == "draining":
                self._release_leadership("Worker drained and stopped.")
                return
            run_once_at = control.get("runOnceRequestedAt")
            should_run = desired_state == "running" or bool(run_once_at)
            if should_run and self._fencing_token is not None:
                if run_once_at:
                    with closing(open_sqlite_connection(self.db_path)) as consume_connection:
                        initialize_platform_schema(consume_connection)
                        if not WorkerControlRepository(consume_connection).consume_run_once(str(run_once_at)):
                            self._stop_event.wait(0.1)
                            continue
                if self._queued_job_preflight():
                    self._run_batch_once(fencing_token=decision.fencing_token)
            else:
                self._renew_leadership(status=desired_state)
            self._stop_event.wait(self.settings.poll_interval_seconds)

    def _queued_job_preflight(self) -> bool:
        """Preserva el gate de conversación sin impedir operaciones para reparar el runtime."""
        from local_control_center.jobs_approvals.worker import (
            THREAD_PRODUCT_LOOP_JOB_KIND,
            THREAD_RESEARCH_JOB_KIND,
        )

        with closing(open_sqlite_connection(self.db_path)) as connection:
            job = JobsRepository(connection).peek_next_job()
        if not job or job["kind"] not in {THREAD_PRODUCT_LOOP_JOB_KIND, THREAD_RESEARCH_JOB_KIND}:
            return True
        preflight = self.preflight()
        if preflight.ok:
            return True
        self._status = "blocked"
        self._reason = self._last_error = preflight.reason
        payload = self._preflight_failure_payload(preflight)
        self._record_worker_event("worker_failed", payload)
        self._record_queued_thread_event("worker_failed", payload)
        return False

    def _renew_leadership(self, *, status: str) -> bool:
        if self._fencing_token is None:
            return False
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
            renewed = WorkerLeadershipRepository(connection).renew(
                owner_id=self.worker_id,
                fencing_token=self._fencing_token,
                lease_seconds=DEFAULT_LEADER_LEASE_SECONDS,
                status=status,
            )
        from local_control_center.shared.diagnostics import diagnostic_event

        diagnostic_event(
            "leadership.renew",
            component="worker",
            workerId=self.worker_id,
            fencingToken=self._fencing_token,
            outcome="renewed" if renewed else "lost",
        )
        if not renewed:
            from local_control_center.shared.diagnostics import diagnostic_event

            diagnostic_event(
                "leadership.lost",
                component="worker",
                workerId=self.worker_id,
                fencingToken=self._fencing_token,
                outcome="authority_lost",
            )
            self._fencing_token = None
            self._status = "standby"
            self._reason = "Worker leadership fence is no longer valid."
        return renewed

    def _ensure_leadership_watcher(self) -> None:
        """Mantiene heartbeat mientras el hilo principal está dentro de un job largo."""
        if self._leadership_thread and self._leadership_thread.is_alive():
            return
        self._leadership_thread = threading.Thread(
            target=self._leadership_watch_loop,
            name="aido-worker-leadership-heartbeat",
            daemon=True,
        )
        self._leadership_thread.start()

    def _leadership_watch_loop(self) -> None:
        interval = min(1.0, max(0.5, DEFAULT_LEADER_LEASE_SECONDS / 3))
        while not self._stop_event.wait(interval):
            if self._fencing_token is not None:
                self._renew_leadership(status=self._status)
                self._govern_resources_if_due()

    def _release_leadership(self, reason: str) -> None:
        if self._fencing_token is None:
            return
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
            WorkerLeadershipRepository(connection).release(
                owner_id=self.worker_id,
                fencing_token=self._fencing_token,
                reason=reason,
            )
        self._fencing_token = None

    def pause(self) -> dict[str, Any]:
        """Pause background and manual worker execution."""
        self._pause_event.set()
        self._status = "paused"
        self._reason = "Worker paused by operator."
        self._record_worker_event("worker_paused", {"workerId": self.worker_id})
        self._record_queued_thread_event("worker_paused", {"reason": self._reason})
        return self.status()

    def resume(self) -> dict[str, Any]:
        """Resume the worker loop, starting it first if needed."""
        self._pause_event.clear()
        if not self._thread or not self._thread.is_alive():
            return self.start()
        self._status = "running"
        self._reason = "Worker resumed."
        return self.status()

    def run_once(self) -> dict[str, Any]:
        """Run one bounded batch now, respecting pause, preflight and concurrency limits."""
        if self.paused:
            self._status = "paused"
            self._reason = "Worker is paused."
            self._record_worker_event("worker_paused", {"workerId": self.worker_id})
            self._record_queued_thread_event("worker_paused", {"reason": self._reason})
            return {**self._operation_status(), "runs": []}
        preflight = self.preflight()
        if not preflight.ok:
            self._status = "blocked"
            self._reason = preflight.reason
            self._last_error = preflight.reason
            self._record_worker_event("worker_failed", self._preflight_failure_payload(preflight))
            self._record_queued_thread_event("worker_failed", self._preflight_failure_payload(preflight))
            return {**self._operation_status(), "runs": []}
        return self._run_batch_once()

    def status(self) -> dict[str, Any]:
        """Return a serializable status snapshot."""
        running = self.running
        status = "running" if running else "paused" if self.paused else self._status
        return self._status_payload(status=status, running=running)

    def _operation_status(self) -> dict[str, Any]:
        """Return a status snapshot for the last explicit operation rather than loop liveness."""
        return self._status_payload(status=self._status, running=self.running)

    def _status_payload(self, *, status: str, running: bool) -> dict[str, Any]:
        """Build the common wire payload for status endpoints and run-once responses."""
        return {
            "status": status,
            "running": running,
            "paused": self.paused,
            "autostart": self.settings.autostart,
            "reason": self._reason,
            "maxConcurrentJobs": self.settings.max_concurrent_jobs,
            "pollIntervalSeconds": self.settings.poll_interval_seconds,
            "inFlightJobs": self._in_flight_jobs,
            "claimedJobs": self._claimed_jobs,
            "completedRuns": self._completed_runs,
            "failedRuns": self._failed_runs,
            "lastRunAt": self._last_run_at,
            "lastIdleAt": self._last_idle_at,
            "lastError": self._last_error,
        }

    def preflight(self) -> WorkerPreflight:
        """Check runtime and gitleaks availability before any job can be claimed."""
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            providers = RuntimeStatusService(connection).list_provider_statuses()
        finally:
            connection.close()
        executable_runtimes = [
            str(provider.get("id"))
            for provider in providers
            if provider.get("executable") is True and str(provider.get("id") or "").strip()
        ]
        if not executable_runtimes:
            return WorkerPreflight(
                ok=False,
                reason="No executable runtime is available for local worker execution.",
                runtime_ids=[],
                gitleaks_executable=None,
                remediation_stage="runtime",
                remediation_details={"executable": False, "status": "configuration_required"},
            )
        gitleaks_executable = _which_gitleaks()
        if not gitleaks_executable:
            return WorkerPreflight(
                ok=False,
                reason="Gitleaks executable was not found on PATH.",
                runtime_ids=executable_runtimes,
                gitleaks_executable=None,
                remediation_stage="gitleaks",
                remediation_details={"status": "configuration_required"},
            )
        return WorkerPreflight(
            ok=True,
            reason="Local worker preflight passed.",
            runtime_ids=executable_runtimes,
            gitleaks_executable=gitleaks_executable,
        )

    @staticmethod
    def _preflight_failure_payload(preflight: WorkerPreflight) -> dict[str, Any]:
        return {
            "reason": preflight.reason,
            "remediationStage": preflight.remediation_stage,
            "remediationDetails": preflight.remediation_details,
        }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.settings.poll_interval_seconds):
                break
            if self._pause_event.is_set():
                self._record_worker_event("worker_paused", {"workerId": self.worker_id})
                continue
            try:
                self._run_batch_once()
            except Exception as error:
                reason = str(redact_secrets(str(error)))
                self._status = "failed"
                self._reason = reason
                self._last_error = reason
                self._record_worker_event("worker_failed", {"reason": reason})
                self._record_queued_thread_event("worker_failed", {"reason": reason})

    def _run_batch_once(self, *, fencing_token: int | None = None) -> dict[str, Any]:
        # A heartbeat may clear the mutable field mid-preflight. The OS loop must retain its
        # acquired token so claim validation rejects a lost lease instead of accepting no fence.
        batch_fence = self._fencing_token if fencing_token is None else fencing_token
        if not self._batch_lock.acquire(blocking=False):
            self._status = "running"
            self._reason = "Worker is already processing a batch."
            return {**self._operation_status(), "runs": []}
        try:
            self._prune_telemetry_if_due()
            self._in_flight_jobs = self.settings.max_concurrent_jobs
            worker_options: dict[str, Any] = {"db_path": self.db_path}
            if self._latest_resource_snapshot is not None:
                worker_options["resource_snapshot"] = self._latest_resource_snapshot
            runs = ConcurrentWorker(**worker_options).run_batch(
                worker_count=self.settings.max_concurrent_jobs,
                max_jobs=self.settings.max_concurrent_jobs,
                worker_id=self.worker_id,
                fencing_token=batch_fence,
            )
            now = utc_now()
            self._last_run_at = now
            claimed = len(runs)
            self._claimed_jobs += claimed
            completed = sum(1 for run in runs if (run.get("run") or {}).get("status") == "completed")
            failed = sum(1 for run in runs if (run.get("run") or {}).get("status") != "completed")
            self._completed_runs += completed
            self._failed_runs += failed
            if claimed == 0:
                self._status = "idle"
                self._reason = "No queued jobs were available."
                self._last_idle_at = now
                self._record_worker_event("worker_idle", {"workerId": self.worker_id})
            elif failed:
                self._status = "failed"
                self._reason = f"Worker completed {completed} run(s) and failed {failed} run(s)."
                self._last_error = self._reason
                self._record_worker_event(
                    "worker_failed",
                    {"completedRuns": completed, "failedRuns": failed},
                )
            else:
                self._status = "completed"
                self._reason = f"Worker completed {completed} run(s)."
                self._last_error = None
            self._in_flight_jobs = 0
            return {**self._operation_status(), "runs": runs}
        finally:
            self._in_flight_jobs = 0
            self._batch_lock.release()

    def _govern_resources_if_due(self) -> bool:
        """Evita sondas simultáneas entre heartbeat y polling principal."""
        if not self._resource_sample_lock.acquire(blocking=False):
            return self._latest_resource_snapshot is not None
        try:
            return self._sample_resources_if_due()
        finally:
            self._resource_sample_lock.release()

    def _sample_resources_if_due(self) -> bool:
        """Muestrea, retiene y reevalúa esperas sin mantener una transacción durante la sonda."""
        now_monotonic = time.monotonic()
        with closing(open_sqlite_connection(self.db_path)) as settings_connection:
            initialize_platform_schema(settings_connection)
            configured_interval = SettingsRepository(settings_connection).get_value(
                "resources.sampleIntervalSeconds", "general", None
            )
        try:
            interval = float(2 if configured_interval is UNSET else configured_interval)
        except (TypeError, ValueError):
            interval = 2.0
        interval = max(0.25, min(interval, 60.0))
        if (
            self._latest_resource_snapshot is not None
            and self._last_resource_sample_monotonic is not None
            and now_monotonic - self._last_resource_sample_monotonic < interval
        ):
            return True
        try:
            snapshot = self._resource_probe.sample(cpu_interval_seconds=min(1.0, interval))
            with closing(open_sqlite_connection(self.db_path)) as connection:
                initialize_platform_schema(connection)
                governor = HostResourceGovernor(connection)
                governor.record_sample(snapshot)
                governor.recover_expired()
                governor.reevaluate_waiting(snapshot=snapshot)
                governor.violations_for_snapshot(snapshot)
            self._latest_resource_snapshot = snapshot
            self._last_resource_sample_monotonic = time.monotonic()
            return True
        except Exception as error:
            reason = f"Host resource probe failed closed: {type(error).__name__}."
            self._status = "resource_wait"
            self._reason = reason
            self._last_error = reason
            return False

    def _active_workload_classes(self) -> list[str]:
        """Lee las clases reservadas desde una conexión corta propia de la sonda."""
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
            return [lease.workload_class for lease in ResourceRepository(connection).active_leases()]

    def _prune_telemetry_if_due(self) -> None:
        """Prune expired ``telemetry.http.request`` events at most once per interval.

        Complementa la poda de arranque del API para procesos de larga duracion; usa una
        conexion propia (el hilo del worker no puede reusar la del platform) y jamas
        interrumpe el batch ante un fallo de poda.
        """
        now_monotonic = time.monotonic()
        last = self._last_telemetry_prune_monotonic
        if last is not None and (now_monotonic - last) < TELEMETRY_PRUNE_INTERVAL_SECONDS:
            return
        self._last_telemetry_prune_monotonic = now_monotonic
        try:
            connection = open_sqlite_connection(self.db_path)
            try:
                initialize_platform_schema(connection)
                prune_http_request_telemetry(connection)
            finally:
                connection.close()
        except Exception:  # pragma: no cover - la retencion nunca debe interrumpir el batch
            pass

    def _record_worker_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            EventBus(connection).record_event(
                event_type=event_type,
                payload=redact_secrets({"workerId": self.worker_id, **(payload or {})}),
            )
        finally:
            connection.close()

    def _record_queued_thread_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection = open_sqlite_connection(self.db_path)
        try:
            initialize_platform_schema(connection)
            jobs = JobsRepository(connection).list_jobs()
            threads = ThreadsRepository(connection)
            for job in jobs:
                if job["status"] != "queued":
                    continue
                job_payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
                thread_id = str(job_payload.get("threadId") or "").strip()
                if not thread_id:
                    continue
                threads.record_event(
                    thread_id=thread_id,
                    type=event_type,
                    agent_role="worker",
                    payload=redact_secrets(
                        {
                            "jobId": job["id"],
                            "workerId": self.worker_id,
                            **(payload or {}),
                        }
                    ),
                )
                self._create_preflight_remediations(
                    connection=connection,
                    job=job,
                    thread_id=thread_id,
                    event_type=event_type,
                    payload=payload or {},
                )
        finally:
            connection.close()

    def _create_preflight_remediations(
        self,
        *,
        connection: sqlite3.Connection,
        job: dict[str, Any],
        thread_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if event_type != "worker_failed":
            return
        reason = str(payload.get("reason") or "").strip()
        stage = str(payload.get("remediationStage") or "").strip()
        details = (
            payload.get("remediationDetails") if isinstance(payload.get("remediationDetails"), dict) else {}
        )
        if not stage:
            return
        BlockerRemediationService(connection, root=self.cwd).create_for_blocked_run(
            project_id=str(job["projectId"]),
            thread_id=thread_id,
            loop_id=None,
            stage=stage,
            reason=reason,
            details={"jobId": job["id"], **details},
        )


def _which_gitleaks() -> str | None:
    for candidate in ("gitleaks", "gitleaks.cmd", "gitleaks.exe"):
        executable = shutil.which(candidate)
        if executable:
            return executable
    return None
