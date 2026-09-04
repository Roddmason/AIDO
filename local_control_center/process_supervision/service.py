"""Orquestación durable de supervisores nativos y registro de handles AIDO activos.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing, suppress
from pathlib import Path
from typing import Any

import psutil

from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import (
    ResourceAdmissionRequest,
    ResourceSnapshot,
    WorkloadClass,
)
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.host_resources.profiles import workload_profile
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.settings import default_db_path

from .base import ProcessSupervisor
from .capture import ArtifactCapture, CapturedReader
from .context import CURRENT_EXECUTION, assert_external_boundary
from .models import ManagedProcessRecord, ProcessLaunchSpec, ProcessStats, SupervisedProcess
from .posix import PosixProcessGroupSupervisor
from .repository import ManagedProcessRepository

_REGISTRY_LOCK = threading.RLock()
_ACTIVE: dict[str, tuple[ProcessSupervisorService, SupervisedProcess]] = {}
_PROCESS_IDS: dict[int, str] = {}


class ExecutionCancelled(RuntimeError):
    """Impide iniciar una etapa posterior a la solicitud durable de cancelación."""


class ResourceWaitError(OSError):
    """Expresa falta temporal de capacidad sin iniciar ningún proceso."""


def command_fingerprint(argv: list[str]) -> str:
    """Vincula evidencia al argv exacto mediante SHA-256, sin persistir su contenido."""
    payload = json.dumps(argv, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_command_summary(argv: list[str]) -> list[str]:
    """Devuelve un resumen apto para auditoría sin contenido libre del operador."""
    if not argv:
        return []
    # Un valor libre puede parecer un flag (incluso después de --prompt).
    # La autorización conserva un hash exacto separado; el display nunca necesita sus valores.
    return [Path(argv[0]).name, *(["[argument]"] * (len(argv) - 1))]


def classify_workload(argv: list[str]) -> WorkloadClass:
    """Clasifica conservadoramente los ejecutables productivos cubiertos por el sandbox."""
    tokens = [Path(value).stem.lower() for value in argv]
    while tokens and tokens[0] in {"uv", "corepack", "python", "python3", "py"}:
        wrapper = tokens.pop(0)
        if tokens and tokens[0] in {"run", "exec", "-m"}:
            tokens.pop(0)
        elif wrapper != "corepack":
            return "agent_cli"
    executable = tokens[0] if tokens else ""
    if executable in {"pytest", "ruff", "semgrep", "git", "gitleaks"}:
        return "qa_light"
    if executable in {"node", "pnpm", "npm", "npx", "yarn"}:
        if "playwright" in tokens:
            return "browser_test"
        if any(token in tokens for token in ("build", "compile", "bundle", "vite", "tsc")):
            return "build_heavy"
    if executable == "docker":
        return "build_heavy"
    return "agent_cli"


def _default_backend() -> ProcessSupervisor:
    if os.name == "nt":
        from .windows_job import WindowsJobObjectProcessSupervisor

        return WindowsJobObjectProcessSupervisor()
    return PosixProcessGroupSupervisor()


class ProcessSupervisorService:
    """Une persistencia, backend nativo, cancelación y liberación idempotente."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        backend: ProcessSupervisor | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        resource_snapshot: ResourceSnapshot | None = None,
        cleanup_only: bool = False,
    ) -> None:
        self.context = CURRENT_EXECUTION.get()
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else (self.context.db_path if self.context is not None else default_db_path())
        )
        self.backend = backend or _default_backend()
        self.popen_factory = popen_factory
        self.resource_snapshot = resource_snapshot
        self.cleanup_only = cleanup_only

    def start(
        self,
        *,
        argv: list[str],
        cwd: str | Path,
        execution_id: str | None = None,
        workload_class: WorkloadClass = "agent_cli",
        memory_limit_bytes: int | None = None,
        process_limit: int | None = None,
        cpu_limit_percent: float | None = None,
        **popen_kwargs: Any,
    ) -> SupervisedProcess:
        """Registra y lanza un árbol con límites derivados del perfil de carga."""
        assert_external_boundary()
        if self.cleanup_only:
            valid_cleanup = (
                len(argv) == 4
                and Path(argv[0]).stem.lower() == "docker"
                and argv[1:3] == ["rm", "--force"]
                and re.fullmatch(r"aido-p0-[0-9a-f]{32}", argv[3])
            )
            if not valid_cleanup:
                raise PermissionError(
                    "La excepción de cleanup sólo permite retirar un contenedor AIDO exacto."
                )
            workload_class = "control_plane"
        profile = workload_profile(workload_class)
        managed_process_id = f"managed-process-{uuid.uuid4()}"
        execution_id = (
            execution_id or (self.context.execution_id if self.context else None) or managed_process_id
        )
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
            if (
                self.cleanup_only
                and not connection.execute(
                    "SELECT 1 FROM managed_containers WHERE name = ? AND executable = ? AND released_at IS NULL",
                    (argv[3], argv[0]),
                ).fetchone()
            ):
                raise PermissionError("El contenedor de cleanup no pertenece al registro durable AIDO.")
            reason = ManagedProcessRepository(connection).cancellation_reason(execution_id)
            if self.context and self.context.execution_id:
                reason = reason or ManagedProcessRepository(connection).cancellation_reason(
                    self.context.execution_id
                )
            if reason and not self.cleanup_only:
                raise ExecutionCancelled(reason)
            if not self.cleanup_only:
                self._assert_fence(connection)
        inherited_id = self.context.resource_lease_id if self.context and not self.cleanup_only else None
        if inherited_id:
            with closing(open_sqlite_connection(self.db_path)) as connection:
                lease = ResourceRepository(connection).get_lease(inherited_id)
                from local_control_center.shared.time import utc_now

                if lease.released_at or lease.expires_at <= utc_now():
                    raise ExecutionCancelled("La reserva de la ejecución dejó de estar vigente.")
            if profile.heavy and not workload_profile(lease.workload_class).heavy:
                raise ResourceWaitError("resource_wait: una etapa pesada requiere una reserva pesada.")
        else:
            snapshot = self.resource_snapshot or HostResourceProbe(
                relevant_paths=[self.db_path.parent, Path(cwd)]
            ).sample(cpu_interval_seconds=0)
            with closing(open_sqlite_connection(self.db_path)) as connection:
                decision = HostResourceGovernor(connection).admit(
                    ResourceAdmissionRequest(
                        execution_id=managed_process_id,
                        workload_class=workload_class,
                        owner_id=managed_process_id,
                    ),
                    snapshot=snapshot,
                )
                if decision.lease is None:
                    raise ResourceWaitError(f"resource_wait: {decision.reason_code}: {decision.reason}")
                lease = decision.lease
        spec = ProcessLaunchSpec(
            managed_process_id=managed_process_id,
            execution_id=execution_id,
            argv=list(argv),
            cwd=str(Path(cwd).resolve(strict=False)),
            workload_class=workload_class,
            command_fingerprint=command_fingerprint(argv),
            memory_limit_bytes=min(
                memory_limit_bytes or profile.memory_limit_bytes, lease.memory_limit_bytes
            ),
            process_limit=min(process_limit or profile.process_limit, lease.process_limit),
            cpu_limit_percent=min(cpu_limit_percent or profile.cpu_limit_percent, lease.cpu_limit_percent),
            below_normal_priority=profile.heavy,
        )
        managed = None
        try:
            managed = self.backend.start(spec, popen_factory=self.popen_factory, **popen_kwargs)
            managed.resource_lease_id = lease.id
            managed.owns_resource_lease = inherited_id is None
            with closing(open_sqlite_connection(self.db_path)) as connection:
                initialize_platform_schema(connection)
                ManagedProcessRepository(connection).start(
                    spec, root_pid=int(managed.process.pid), resource_lease_id=lease.id
                )
            for stream_name in ("stdout", "stderr"):
                stream = getattr(managed.process, stream_name, None)
                if stream is not None:
                    capture = ArtifactCapture(self.db_path.parent, stream_name, managed.capture_failure)
                    managed.captures[stream_name] = capture
                    setattr(managed.process, stream_name, CapturedReader(stream, capture))
            self._register_captures(managed)
        except Exception:
            if managed is not None:
                try:
                    stats = self.backend.terminate_tree(
                        managed, grace_seconds=0, reason="registration_failed"
                    )
                    with closing(open_sqlite_connection(self.db_path)) as connection:
                        repository = ManagedProcessRepository(connection)
                        if repository.get(managed.managed_process_id):
                            repository.finish(managed.managed_process_id, stats=stats)
                finally:
                    self.backend.release(managed)
                    for capture in managed.captures.values():
                        with suppress(OSError):
                            capture.finish()
            if inherited_id is None:
                with closing(open_sqlite_connection(self.db_path)) as connection:
                    HostResourceGovernor(connection).release(lease.id, reason="launch_failed")
            raise
        with _REGISTRY_LOCK:
            _ACTIVE[managed_process_id] = (self, managed)
            _PROCESS_IDS[id(managed.process)] = managed_process_id
        managed.process._aido_managed_process_id = managed_process_id
        managed.watcher = threading.Thread(
            target=self._watch_controls,
            args=(managed,),
            name=f"process-control-{managed_process_id}",
            daemon=True,
        )
        managed.watcher.start()
        return managed

    def complete(
        self,
        managed: SupervisedProcess,
        *,
        exit_code: int | None,
        timed_out: bool = False,
        cancelled: bool = False,
        termination_reason: str = "",
        stdout_artifact_id: str | None = None,
        stderr_artifact_id: str | None = None,
    ) -> ManagedProcessRecord:
        """Registra estadísticas finales y libera el contenedor una sola vez."""
        managed.stop_watcher.set()
        if managed.watcher and managed.watcher is not threading.current_thread():
            managed.watcher.join(timeout=4)
        with managed.lock:
            try:
                stats = managed.terminal_stats or self.backend.stats(managed)
                if not managed.released and (
                    managed.process.poll() is None or stats.remaining_descendant_count
                ):
                    stats = self.backend.terminate_tree(
                        managed, grace_seconds=0, reason=termination_reason or "remaining_children"
                    )
                if not stats.cancelled and not stats.timed_out:
                    stats.exit_code = exit_code
                stats.timed_out |= timed_out
                stats.cancelled |= cancelled
                stats.termination_reason = stats.termination_reason or termination_reason
                managed.terminal_stats = stats
                artifact_ids = self._finish_captures(managed)
                with closing(open_sqlite_connection(self.db_path)) as connection:
                    record = ManagedProcessRepository(connection).finish(
                        managed.managed_process_id,
                        stats=stats,
                        stdout_artifact_id=stdout_artifact_id or artifact_ids.get("stdout"),
                        stderr_artifact_id=stderr_artifact_id or artifact_ids.get("stderr"),
                    )
                    if managed.owns_resource_lease and managed.resource_lease_id:
                        HostResourceGovernor(connection).release(
                            managed.resource_lease_id, reason=stats.termination_reason or "process_finished"
                        )
                return record
            finally:
                # Una falla de evidencia no puede mantener un árbol ejecutando sin control.
                # Su registro y lease permanecen recuperables, sin inventar un resultado terminal.
                if not managed.released:
                    try:
                        self.backend.release(managed)
                        managed.released = True
                    finally:
                        self._unregister(managed)
                for capture in managed.captures.values():
                    with suppress(OSError):
                        capture.finish()

    def _assert_fence(self, connection: Any) -> None:
        if self.context and self.context.fencing_token is not None:
            from local_control_center.shared.time import utc_now

            valid = connection.execute(
                "SELECT 1 FROM worker_leader_leases WHERE owner_id = ? AND fencing_token = ? AND expires_at > ?",
                (self.context.worker_id, self.context.fencing_token, utc_now()),
            ).fetchone()
            if not valid:
                raise ExecutionCancelled("leadership_fence_lost")

    def cancel(self, managed_process_id: str, *, reason: str) -> ManagedProcessRecord:
        """Registra cancel_requested y termina únicamente el árbol registrado por AIDO."""
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValueError("Cancellation reason is required.")
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
            repository = ManagedProcessRepository(connection)
            record = repository.request_cancel(managed_process_id, reason=clean_reason)
        if record is None:
            raise KeyError(f"Unknown managed process: {managed_process_id}")
        if record.finished_at:
            return record
        with _REGISTRY_LOCK:
            entry = _ACTIVE.get(managed_process_id)
        if entry is None:
            return record
        owner, managed = entry
        with managed.lock:
            if managed.terminal_stats is None and not managed.released:
                managed.terminal_stats = owner.backend.terminate_tree(
                    managed, grace_seconds=2, reason=clean_reason
                )
                managed.terminal_stats.cancelled = True
        return owner.complete(
            managed, exit_code=managed.process.poll(), cancelled=True, termination_reason=clean_reason
        )

    def emergency_stop(self, *, reason: str) -> list[str]:
        """Cancela de forma idempotente los handles activos registrados por este host AIDO."""
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValueError("Emergency stop reason is required.")
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
            active_ids = [
                record.managed_process_id for record in ManagedProcessRepository(connection).active()
            ]
        stopped: list[str] = []
        for managed_process_id in active_ids:
            self.cancel(managed_process_id, reason=clean_reason)
            stopped.append(managed_process_id)
        return stopped

    def _watch_controls(self, managed: SupervisedProcess) -> None:
        next_heartbeat = time.monotonic() + 5
        next_memory_check = 0.0
        while not managed.stop_watcher.wait(0.2):
            try:
                with closing(open_sqlite_connection(self.db_path)) as connection:
                    connection.execute("PRAGMA busy_timeout = 250")
                    repository = ManagedProcessRepository(connection)
                    reason = repository.cancellation_reason(
                        managed.execution_id
                    ) or repository.cancellation_reason(managed.managed_process_id)
                    if self.cleanup_only:
                        reason = None
                    elif self.context and self.context.execution_id:
                        reason = reason or repository.cancellation_reason(self.context.execution_id)
                    if managed.capture_failure.is_set():
                        reason = "output_capture_limit"
                    try:
                        if not self.cleanup_only:
                            self._assert_fence(connection)
                    except ExecutionCancelled as error:
                        reason = str(error)
                    if not self.cleanup_only and time.monotonic() >= next_memory_check:
                        from local_control_center.host_resources.profiles import GIB, _setting
                        from local_control_center.settings.repository import SettingsRepository

                        floor = (
                            float(_setting(SettingsRepository(connection), "resources.hardFreeMemoryGiB"))
                            * GIB
                        )
                        if psutil.virtual_memory().available < floor:
                            reason = "hard_memory_floor"
                            repository.request_execution_cancel(managed.execution_id, reason=reason)
                        next_memory_check = time.monotonic() + 1
                    if managed.owns_resource_lease and time.monotonic() >= next_heartbeat:
                        renewed = HostResourceGovernor(connection).heartbeat(
                            managed.resource_lease_id, owner_id=managed.managed_process_id
                        )
                        if renewed is None:
                            reason = "resource_lease_lost"
                        next_heartbeat = time.monotonic() + 5
                    if reason:
                        repository.request_execution_cancel(managed.execution_id, reason=reason)
                if reason:
                    with managed.lock:
                        if not managed.released and managed.terminal_stats is None:
                            managed.terminal_stats = self.backend.terminate_tree(
                                managed, grace_seconds=2, reason=reason
                            )
                            managed.terminal_stats.cancelled = True
                    return
            except Exception:
                with managed.lock:
                    if not managed.released and managed.terminal_stats is None:
                        managed.terminal_stats = self.backend.terminate_tree(
                            managed, grace_seconds=0, reason="control_watch_failed"
                        )
                        managed.terminal_stats.cancelled = True
                return

    def _register_captures(self, managed: SupervisedProcess) -> None:
        from local_control_center.evidence.repository import EvidenceRepository

        with closing(open_sqlite_connection(self.db_path)) as connection:
            for stream, capture in managed.captures.items():
                EvidenceRepository(connection).create_artifact(
                    project_id=(self.context.project_id if self.context else None) or "local-process",
                    evidence_package_id=None,
                    kind="execution_log",
                    path=str(capture.path),
                    artifact_id=capture.id,
                    metadata={
                        "managedProcessId": managed.managed_process_id,
                        "status": "capturing",
                        "stream": stream,
                    },
                )
                column = "stdout_artifact_id" if stream == "stdout" else "stderr_artifact_id"
                connection.execute(
                    f"UPDATE managed_processes SET {column} = ? WHERE managed_process_id = ?",
                    (capture.id, managed.managed_process_id),
                )

    def _finish_captures(self, managed: SupervisedProcess) -> dict[str, str]:
        from local_control_center.evidence.repository import EvidenceRepository

        ids: dict[str, str] = {}
        for stream, capture in managed.captures.items():
            artifact = capture.finish()
            with closing(open_sqlite_connection(self.db_path)) as connection:
                if not connection.execute("SELECT 1 FROM artifacts WHERE id = ?", (capture.id,)).fetchone():
                    EvidenceRepository(connection).create_artifact(
                        project_id=(self.context.project_id if self.context else None) or "local-process",
                        evidence_package_id=None,
                        kind="execution_log",
                        path=artifact["path"],
                        content_hash=artifact["hash"],
                        artifact_id=capture.id,
                        metadata={"managedProcessId": managed.managed_process_id, **artifact},
                    )
                else:
                    connection.execute(
                        "UPDATE artifacts SET hash = ?, metadata = ? WHERE id = ?",
                        (
                            artifact["hash"],
                            json_dumps(
                                {
                                    "managedProcessId": managed.managed_process_id,
                                    "status": "complete",
                                    **artifact,
                                }
                            ),
                            capture.id,
                        ),
                    )
            ids[stream] = capture.id
        return ids

    @staticmethod
    def _unregister(managed: SupervisedProcess) -> None:
        with _REGISTRY_LOCK:
            _ACTIVE.pop(managed.managed_process_id, None)
            _PROCESS_IDS.pop(id(managed.process), None)


def managed_process_for(process: Any) -> tuple[ProcessSupervisorService, SupervisedProcess] | None:
    """Resuelve el handle central asociado a un objeto Popen público existente."""
    managed_process_id = getattr(process, "_aido_managed_process_id", None)
    if not managed_process_id:
        with _REGISTRY_LOCK:
            managed_process_id = _PROCESS_IDS.get(id(process))
    with _REGISTRY_LOCK:
        return _ACTIVE.get(str(managed_process_id)) if managed_process_id else None


def complete_managed_process(
    process: Any,
    *,
    exit_code: int | None,
    timed_out: bool = False,
    cancelled: bool = False,
    termination_reason: str = "",
) -> ManagedProcessRecord | None:
    """Cierra el registro asociado a Popen; no altera procesos inyectados no administrados."""
    entry = managed_process_for(process)
    if entry is None:
        return None
    service, managed = entry
    return service.complete(
        managed,
        exit_code=exit_code,
        timed_out=timed_out,
        cancelled=cancelled,
        termination_reason=termination_reason,
    )


def terminate_managed_process(process: Any, *, reason: str, grace_seconds: float = 2) -> ProcessStats:
    """Termina el árbol administrado o degrada al proceso raíz para dobles de prueba."""
    entry = managed_process_for(process)
    if entry is None:
        if process.poll() is None:
            process.terminate()
        return ProcessStats(exit_code=process.poll(), cancelled=True, termination_reason=reason)
    service, managed = entry
    with managed.lock:
        if managed.terminal_stats is None:
            stats = service.backend.terminate_tree(managed, grace_seconds=grace_seconds, reason=reason)
            stats.cancelled = reason not in {"timeout", "descendants_cleanup"}
            stats.timed_out = reason == "timeout"
            stats.termination_reason = reason
            managed.terminal_stats = stats
        return managed.terminal_stats


def request_managed_cancellation(process: Any, *, reason: str) -> ManagedProcessRecord | None:
    """Registra la intención de cancelar sin forzar todavía el árbol nativo."""
    entry = managed_process_for(process)
    if entry is None:
        return None
    service, managed = entry
    with closing(open_sqlite_connection(service.db_path)) as connection:
        initialize_platform_schema(connection)
        return ManagedProcessRepository(connection).request_cancel(
            managed.managed_process_id, reason=reason.strip() or "cancel_requested"
        )


def run_probe_command(
    argv: list[str], *, run_factory: Callable[..., Any] = subprocess.run, **kwargs: Any
) -> Any:
    """Ejecuta un probe read-only explícito desde el único módulo autorizado a invocar runners."""
    assert_external_boundary()
    # Los callers de probes también validan; este límite rechaza comandos productivos directos.
    permitted = len(argv) == 2 and argv[1] in {"--version", "-V", "version", "--help"}
    permitted |= tuple(argv[1:]) in {("auth", "status", "--json"), ("login", "status"), ("exec", "--help")}
    if not permitted:
        raise PermissionError("Probe read-only fuera del allowlist.")
    return run_factory(argv, shell=False, **kwargs)


def run_supervised_capture(
    argv: list[str],
    *,
    cwd: str | Path,
    timeout_seconds: int,
    workload_class: WorkloadClass,
    environment: dict[str, str] | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    capture_limit: int = 1_048_576,
    cleanup_only: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Ejecuta y captura un comando productivo dentro del supervisor nativo."""
    started = time.perf_counter()
    service = ProcessSupervisorService(
        popen_factory=popen_factory, cleanup_only=cleanup_only, db_path=db_path
    )
    managed = service.start(
        argv=argv,
        cwd=cwd,
        workload_class=workload_class,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    process = managed.process
    states: dict[str, dict[str, Any]] = {"stdout": {}, "stderr": {}}
    readers = [
        threading.Thread(
            target=_drain_prefix, args=(getattr(process, stream), capture_limit, states[stream]), daemon=True
        )
        for stream in states
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    termination_reason = ""
    exit_code = None
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        termination_reason = "timeout"
        terminate_managed_process(process, grace_seconds=2, reason=termination_reason)
    except BaseException:
        termination_reason = "capture_interrupted"
        terminate_managed_process(process, grace_seconds=0, reason=termination_reason)
        raise
    finally:
        try:
            if service.backend.stats(managed).remaining_descendant_count:
                terminate_managed_process(process, reason="descendants_cleanup", grace_seconds=0)
            for reader in readers:
                reader.join(timeout=5)
            if any(reader.is_alive() for reader in readers):
                raise RuntimeError("Un lector conserva un pipe abierto tras terminar el árbol.")
            for stream in states:
                getattr(process, stream).close()
        finally:
            record = service.complete(
                managed,
                exit_code=exit_code,
                timed_out=timed_out,
                termination_reason=termination_reason,
            )
    return {
        "stdout": states["stdout"].get("text", ""),
        "stderr": states["stderr"].get("text", ""),
        "stdoutCaptureTruncated": states["stdout"].get("truncated", True),
        "stderrCaptureTruncated": states["stderr"].get("truncated", True),
        "stdoutTotalBytes": states["stdout"].get("totalBytes", 0),
        "stderrTotalBytes": states["stderr"].get("totalBytes", 0),
        "returnCode": exit_code,
        "timedOut": timed_out,
        "cancelled": record.cancelled,
        "durationMs": int((time.perf_counter() - started) * 1000),
        "workloadClass": workload_class,
        "resourceLeaseId": managed.resource_lease_id,
        "stdoutArtifactId": record.stdout_artifact_id,
        "stderrArtifactId": record.stderr_artifact_id,
        "managedProcessId": managed.managed_process_id,
        "peakMemoryBytes": record.peak_memory_bytes,
        "cpuTimeSeconds": record.cpu_time_seconds,
        "terminationReason": record.termination_reason,
        "remainingDescendantCount": managed.terminal_stats.remaining_descendant_count,
    }


def _drain_prefix(stream: Any, max_bytes: int, state: dict[str, Any]) -> None:
    prefix = bytearray()
    total = 0
    try:
        while chunk := stream.read(65536):
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            total += len(chunk)
            if len(prefix) < max_bytes:
                prefix.extend(chunk[: max_bytes - len(prefix)])
        truncated = total > max_bytes
        if truncated:
            marker = b"\n[truncated]"
            prefix = prefix[: max(0, max_bytes - len(marker))] + marker
        state.update(text=prefix.decode("utf-8", errors="replace"), totalBytes=total, truncated=truncated)
    except (OSError, ValueError):
        state.update(text=prefix.decode("utf-8", errors="replace"), totalBytes=total, truncated=True)
