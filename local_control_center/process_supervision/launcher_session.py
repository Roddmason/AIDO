"""Opt-in native capture session owned by the existing launcher, not a daemon.

The launcher remains in the aggregate Job and creates sibling control-plane,
execution and collector Jobs. Only registered operations and owned target IDs
cross the authenticated local pipe; argv, paths and arbitrary PIDs do not.

@author Rodrigo Mason
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import uuid
from contextlib import closing, suppress
from dataclasses import replace
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

from local_control_center.executions.repository import ExecutionRepository
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.host_resources.profiles import CAPTURE_SESSION_PARTS, workload_profile
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.diagnostics import attempt_options, diagnostic_event, diagnostic_root
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.redaction import redact_secrets

from .context import ProcessExecutionContext, execution_scope
from .models import ProcessLaunchSpec
from .repository import ManagedProcessRepository
from .service import ProcessSupervisorService, ResourceWaitError, command_fingerprint, run_supervised_capture
from .session_client import SESSION_ENV

SESSION_BUDGET = {
    "memoryBytes": workload_profile("capture_session").memory_limit_bytes,
    "cpuPercent": workload_profile("capture_session").cpu_limit_percent,
    "parts": CAPTURE_SESSION_PARTS,
}


class SessionRequest(BaseModel):
    """Accept only fenced operation identities and owned managed-process identifiers."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["dispatch", "capture_preflight", "capture_prepare", "capture_finish"]
    execution_id: str = Field(alias="executionId", min_length=1, max_length=128)
    attempt_id: str = Field(alias="attemptId", min_length=1, max_length=128)
    owner_id: str = Field(alias="ownerId", min_length=1, max_length=128)
    fencing_token: int = Field(alias="fencingToken", ge=1)
    managed_process_id: str | None = Field(default=None, alias="managedProcessId", max_length=128)


class _Target:
    """Read-only native identity retained across target exit, including its exit code."""

    def __init__(self, pid, created):
        import win32api

        if abs(psutil.Process(pid).create_time() - created) >= 0.01:
            raise PermissionError("Capture target identity mismatch")
        self.pid = pid
        self.handle = win32api.OpenProcess(0x1000 | 0x100000, False, pid)

    def poll(self):
        import win32process

        code = win32process.GetExitCodeProcess(self.handle)
        return None if code == 259 else code

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


class LauncherCaptureSession:
    """Own the aggregate reservation, sibling Jobs and private capture coordination."""

    def __init__(self, db_path, root):
        self.db_path, self.root = Path(db_path).resolve(), Path(root).resolve()
        self.id = f"managed-process-{uuid.uuid4()}"
        self.children = {}
        self.captures = {}
        self.execution_lock = threading.Lock()
        self.stopping = threading.Event()
        self.handlers = []
        self.slots = threading.BoundedSemaphore(4)
        self.previous_environment = os.environ.get(SESSION_ENV)

    def __enter__(self):
        if os.name != "nt":
            raise RuntimeError("Native capture session is implemented only on Windows")
        import win32api
        import win32job

        from .windows_job import _configure_job, _readback, job_name

        snapshot = HostResourceProbe(relevant_paths=[self.db_path.parent]).sample(cpu_interval_seconds=0)
        with closing(open_sqlite_connection(self.db_path)) as connection:
            initialize_platform_schema(connection)
        from .recovery import recover_managed_processes

        recover_managed_processes(self.db_path)
        with closing(open_sqlite_connection(self.db_path)) as connection:
            decision = HostResourceGovernor(connection).admit(
                ResourceAdmissionRequest(
                    execution_id=self.id, owner_id=self.id, workload_class="capture_session"
                ),
                snapshot=snapshot,
            )
            if decision.lease is None:
                raise ResourceWaitError(f"resource_wait: {decision.reason_code}: {decision.reason}")
            self.lease = decision.lease
        spec = ProcessLaunchSpec(
            self.id,
            self.id,
            [sys.executable],
            str(self.root),
            "capture_session",
            command_fingerprint([sys.executable]),
            self.lease.memory_limit_bytes,
            self.lease.process_limit,
            self.lease.cpu_limit_percent,
            False,
        )
        self.job = win32job.CreateJobObject(None, job_name(self.id))
        try:
            if win32api.GetLastError() == 183:
                raise PermissionError("Launcher Job name collision")
            applied = _configure_job(self.job, spec)
            win32job.AssignProcessToJobObject(self.job, win32api.GetCurrentProcess())
            verified = _readback(self.job, win32api.GetCurrentProcess())
            with closing(open_sqlite_connection(self.db_path)) as connection:
                ManagedProcessRepository(connection).start(
                    spec, root_pid=os.getpid(), resource_lease_id=self.lease.id
                )
            self.context = ProcessExecutionContext(
                db_path=self.db_path,
                execution_id=self.id,
                resource_lease_id=self.lease.id,
                aggregate_managed_process_id=self.id,
            )
            address = rf"\\.\pipe\AIDO-capture-{uuid.uuid4().hex}"
            # Authenticate both peers using their Windows pipe PID + creation identity.
            # No blocking userland auth handshake, credential file or pickle protocol.
            self.listener = Listener(address, family="AF_PIPE", authkey=None)
            os.environ[SESSION_ENV] = json.dumps(
                {
                    "address": address,
                    "aggregateId": self.id,
                    "db": str(self.db_path),
                    "pid": os.getpid(),
                    "created": psutil.Process().create_time(),
                }
            )
            diagnostic_event(
                "launcher.session.ready",
                component="launcher",
                managedProcessId=self.id,
                resourceLeaseId=self.lease.id,
                pid=os.getpid(),
                processCreationTime=psutil.Process().create_time(),
                requested=SESSION_BUDGET,
                applied=applied,
                verified=verified,
                effectiveConfig={"externalHierarchyStatus": "UNKNOWN", "creatorBudget": "aggregate_headroom"},
            )
            self.server = threading.Thread(target=self._serve, daemon=True, name="launcher-capture")
            self.server.start()
            return self
        except BaseException:
            # If already assigned, closing kill-on-close would kill this owner before its
            # durable rollback. Keep that handle OS-owned until this failed launcher exits.
            self.job.Detach()
            with closing(open_sqlite_connection(self.db_path)) as connection:
                HostResourceGovernor(connection).release(self.lease.id, reason="session_prepare_failed")
            raise

    def start_control(self, mode, argv):
        """Start the canonical API or worker inside its explicit control-plane subbudget."""
        with execution_scope(replace(self.context, session_role=mode)):
            service = ProcessSupervisorService(db_path=self.db_path)
            managed = service.start(
                argv=argv,
                cwd=self.root,
                workload_class="control_plane",
                cpu_limit_percent=CAPTURE_SESSION_PARTS[mode]["cpuPercent"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.children[mode] = (service, managed)
        for stream in (managed.process.stdout, managed.process.stderr):
            thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
            thread.start()
            self.handlers.append(thread)
        return managed.process

    @staticmethod
    def _drain(stream):
        try:
            while stream.read1(65536):
                pass  # Existing bounded ArtifactCapture persists both streams.
        finally:
            stream.close()

    def tick(self):
        """Renew the joint lease without attributing it to each contained child again."""
        with closing(open_sqlite_connection(self.db_path)) as connection:
            if HostResourceGovernor(connection).heartbeat(self.lease.id, owner_id=self.id) is None:
                raise RuntimeError("Session reservation lost")

    def _serve(self):
        while not self.stopping.is_set():
            try:
                channel = self.listener.accept()
            except (OSError, EOFError):
                return
            if not self.slots.acquire(blocking=False):
                channel.close()
                continue
            self.handlers = [thread for thread in self.handlers if thread.is_alive()]
            thread = threading.Thread(target=self._handle, args=(channel,), daemon=True)
            self.handlers.append(thread)
            thread.start()

    def _handle(self, channel):
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            peer = ctypes.c_ulong()
            kernel.GetNamedPipeClientProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            if not kernel.GetNamedPipeClientProcessId(channel.fileno(), ctypes.byref(peer)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not channel.poll(5):
                raise TimeoutError("Launcher request deadline exceeded")
            request = SessionRequest.model_validate_json(channel.recv_bytes(8192))
            result = self._perform(request, peer.value)
            channel.send_bytes(json.dumps({"result": result}).encode("utf-8"))
        except Exception as error:
            diagnostic_event("launcher.coordination.error", component="launcher", error=error)
            with suppress(OSError):
                channel.send_bytes(
                    json.dumps({"error": str(redact_secrets(str(error)))[:500]}).encode("utf-8")
                )
        finally:
            channel.close()
            self.slots.release()

    @staticmethod
    def _member(pid, managed_id):
        import win32api
        import win32job

        from .windows_job import job_name

        job = win32job.OpenJobObject(win32job.JOB_OBJECT_QUERY, False, job_name(managed_id))
        process = win32api.OpenProcess(0x1000, False, pid)
        try:
            return win32job.IsProcessInJob(process, job)
        finally:
            process.Close()
            job.Close()

    def _perform(self, request, peer):
        if self.stopping.is_set():
            raise RuntimeError("Launcher is closing")
        with closing(open_sqlite_connection(self.db_path)) as connection:
            ExecutionRepository(connection).require_fence(
                request.execution_id, owner_id=request.owner_id, fencing_token=request.fencing_token
            )
            row = connection.execute(
                "SELECT * FROM operational_executions WHERE id=?", (request.execution_id,)
            ).fetchone()
            attempt = connection.execute(
                "SELECT id FROM job_runs WHERE job_id=? AND status='running' ORDER BY rowid DESC LIMIT 1",
                (request.execution_id,),
            ).fetchone()
            if attempt is None or attempt[0] != request.attempt_id:
                raise PermissionError("Execution attempt mismatch")
            if request.action != "capture_finish" and ManagedProcessRepository(
                connection
            ).cancellation_reason(request.execution_id):
                raise PermissionError("Execution was cancelled")
        from local_control_center.jobs_approvals.repository import JobsRepository

        with closing(open_sqlite_connection(self.db_path)) as connection:
            job = JobsRepository(connection).get_job(request.execution_id)
        context = replace(
            self.context,
            execution_id=request.execution_id,
            attempt_id=request.attempt_id,
            worker_id=request.owner_id,
            fencing_token=request.fencing_token,
            session_role="execution",
            request_id=job["payload"].get("diagnosticContext", {}).get("requestId"),
        )
        if request.action == "dispatch":
            if not self._member(peer, self.children["worker"][1].managed_process_id):
                raise PermissionError("Dispatch caller is not the owned worker")
            if not self.execution_lock.acquire(blocking=False):
                raise ResourceWaitError("resource_wait: capture_session_execution_slot")
            try:
                with execution_scope(context):
                    result = run_supervised_capture(
                        [
                            sys.executable,
                            "-m",
                            "local_control_center.executions.runner",
                            "--db",
                            str(self.db_path),
                            "--execution-id",
                            request.execution_id,
                            "--owner-id",
                            request.owner_id,
                            "--fencing-token",
                            str(request.fencing_token),
                        ],
                        cwd=self.root,
                        timeout_seconds=900,
                        workload_class=row["workload_class"],
                    )
                    # Output remains in existing bounded artifacts, not duplicated in IPC.
                    return {
                        key: result[key]
                        for key in (
                            "managedProcessId",
                            "returnCode",
                            "cancelled",
                            "timedOut",
                            "terminationReason",
                            "stdoutArtifactId",
                            "stderrArtifactId",
                        )
                    }
            finally:
                for managed_id, capture in list(self.captures.items()):
                    if capture.target.execution_id == request.execution_id:
                        with suppress(Exception):
                            self._finish_capture(managed_id)
                        self.captures.pop(managed_id, None)
                self.execution_lock.release()
        if request.action == "capture_preflight":
            from .native_diagnostics import PROCDUMP_SHA256, file_sha256

            with closing(open_sqlite_connection(self.db_path)) as connection:
                dispatchers = connection.execute(
                    "SELECT * FROM managed_processes WHERE execution_id=? AND owner_pid=? AND finished_at IS NULL",
                    (request.execution_id, os.getpid()),
                ).fetchall()
            if not any(self._member(peer, row["managed_process_id"]) for row in dispatchers):
                raise PermissionError("Capture preparation requires the owned dispatcher")
            options = attempt_options(diagnostic_root(), request.execution_id)
            if not options.get("enabled") or not options.get("nativeCollector"):
                raise PermissionError("Native capture authorization is absent or expired")
            if file_sha256(Path(options["nativeCollector"])) != PROCDUMP_SHA256:
                raise PermissionError("Prepared official collector is unavailable")
            if (diagnostic_root() / f"native-claimed-{request.execution_id}").exists():
                raise PermissionError("Native capture attempt already claimed; no replay")
            return {"resourceLeaseId": self.lease.id, "budget": SESSION_BUDGET, "outcome": "prepared"}
        with closing(open_sqlite_connection(self.db_path)) as connection:
            target = ManagedProcessRepository(connection).get(request.managed_process_id)
        if (
            target is None
            or target.execution_id != request.execution_id
            or target.resource_lease_id != self.lease.id
        ):
            raise PermissionError("Target is not owned by this execution and reservation")
        if (
            target.owner_pid != peer
            or abs(psutil.Process(peer).create_time() - target.owner_create_time) >= 0.01
        ):
            raise PermissionError("Capture caller does not own the target")
        if not self._member(peer, self.id):
            raise PermissionError("Capture caller is outside the session Job")
        if request.action == "capture_finish":
            return self._finish_capture(target.managed_process_id)
        from .models import SupervisedProcess
        from .native_diagnostics import NativeCapture, file_sha256

        options = attempt_options(diagnostic_root(), request.execution_id)
        if not options.get("nativeCollector") or not options.get("enabled"):
            raise PermissionError("No native capture authorization for this execution")
        if (
            target.root_pid <= 0
            or target.finished_at
            or not self._member(target.root_pid, target.managed_process_id)
        ):
            raise PermissionError("Target native identity is incomplete or unavailable")
        if psutil.Process(target.root_pid).ppid() != peer:
            raise PermissionError("Target was not created by the authenticated caller")
        executable = psutil.Process(target.root_pid).exe()
        if file_sha256(Path(executable)) != options.get("executableSha256"):
            raise PermissionError("Target executable fingerprint mismatch")
        if target.managed_process_id in self.captures:
            raise PermissionError("Capture already prepared; no replay")
        process = _Target(target.root_pid, target.root_create_time)
        try:
            capture = NativeCapture(
                managed=SupervisedProcess(target.managed_process_id, target.execution_id, process),
                options=options,
                db_path=self.db_path,
                context=replace(context, session_role="collector"),
                root=diagnostic_root(),
            )
            self.captures[target.managed_process_id] = capture
            capture.coordination_lock = threading.Lock()
            return capture.receipt
        except BaseException:
            process.close()
            raise

    def _finish_capture(self, managed_id):
        capture = self.captures[managed_id]
        with capture.coordination_lock:
            try:
                capture.finish()
            finally:
                capture.target.process.close()
        return capture.receipt

    def __exit__(self, *_):
        self.stopping.set()
        self.listener.close()
        for service, managed in reversed(list(self.children.values())):
            service.complete(managed, exit_code=managed.process.poll(), termination_reason="session_shutdown")
        for managed_id in list(self.captures):
            with suppress(Exception):
                self._finish_capture(managed_id)
        for thread in self.handlers:
            thread.join(timeout=2)
        import win32job

        info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        pids = win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicProcessIdList)
        if any(pid != os.getpid() for pid in pids):
            # The last OS-owned handle still kills all descendants if orderly cleanup failed.
            self.job.Detach()
            raise RuntimeError("Owned session descendants remain; recovery required")
        with closing(open_sqlite_connection(self.db_path)) as connection:
            connection.execute(
                "UPDATE managed_processes SET peak_memory_bytes=?, termination_reason='launcher_shutdown_pending_exit' WHERE managed_process_id=?",
                (int(info["PeakJobMemoryUsed"]), self.id),
            )
            # A process cannot attest its own death. The existing identity-based recovery
            # releases the lease after OS exit, never while this self-containing root lives.
        # Do not turn off kill-on-close. Keep this self-containing handle alive until OS exit.
        self.job.Detach()
        if self.previous_environment is None:
            os.environ.pop(SESSION_ENV, None)
        else:
            os.environ[SESSION_ENV] = self.previous_environment
