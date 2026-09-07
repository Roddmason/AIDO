"""Private launcher capability, checked against its actual OS identity and Job.

Only Python control-plane children receive this ephemeral capability. It is not a
provider credential and is never forwarded through the runtime environment isolator.
"""

from __future__ import annotations

import ctypes
import json
import os
from contextlib import closing
from multiprocessing.connection import Client
from pathlib import Path

import psutil

from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.time import utc_now

SESSION_ENV = "AIDO_PRIVATE_LAUNCHER_SESSION"


def session_identity(db_path):
    """Verify the native ancestor and reservation before granting shared-scope authority."""
    raw = os.environ.get(SESSION_ENV)
    if not raw:
        return None
    if os.name != "nt":
        raise RuntimeError("Native capture session is unavailable on this platform")
    import win32api
    import win32job

    from .windows_job import job_name

    value = json.loads(raw)
    if Path(value["db"]).resolve() != Path(db_path).resolve():
        raise PermissionError("Launcher session database mismatch")
    with closing(open_sqlite_connection(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM managed_processes WHERE managed_process_id=? AND finished_at IS NULL",
            (value["aggregateId"],),
        ).fetchone()
        if row is None:
            raise PermissionError("Launcher aggregate is unavailable")
        lease = ResourceRepository(connection).get_lease(row["resource_lease_id"])
        if lease.released_at or lease.expires_at <= utc_now() or lease.workload_class != "capture_session":
            raise PermissionError("Launcher reservation is unavailable")
    owner = psutil.Process(row["root_pid"])
    if abs(owner.create_time() - row["root_create_time"]) >= 0.01:
        raise PermissionError("Launcher identity mismatch")
    if value["pid"] != owner.pid or abs(value["created"] - owner.create_time()) >= 0.01:
        raise PermissionError("Launcher endpoint owner mismatch")
    ancestors = {p.pid for p in [psutil.Process(), *psutil.Process().parents()]}
    if owner.pid not in ancestors:
        raise PermissionError("Caller is not a launcher descendant")
    job = win32job.OpenJobObject(win32job.JOB_OBJECT_QUERY, False, job_name(row["managed_process_id"]))
    try:
        if not win32job.IsProcessInJob(win32api.GetCurrentProcess(), job):
            raise PermissionError("Caller is outside the launcher aggregate Job")
    finally:
        job.Close()
    return value, lease


def request_session(context, action, *, managed_process_id=None):
    """Send a typed operation identity through the authenticated private launcher pipe."""
    identity = session_identity(context.db_path)
    if identity is None:
        raise PermissionError("Compatible launcher creator is unavailable")
    value, lease = identity
    if context.resource_lease_id != lease.id:
        raise PermissionError("Execution does not own the session reservation")
    payload = {
        "action": action,
        "executionId": context.execution_id,
        "attemptId": context.attempt_id,
        "ownerId": context.worker_id,
        "fencingToken": context.fencing_token,
        "managedProcessId": managed_process_id,
    }
    # A local authenticated pipe, not an HTTP endpoint or a pickle command protocol.
    with Client(value["address"], family="AF_PIPE", authkey=None) as channel:
        server = ctypes.c_ulong()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetNamedPipeServerProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        if not kernel.GetNamedPipeServerProcessId(channel.fileno(), ctypes.byref(server)):
            raise ctypes.WinError(ctypes.get_last_error())
        if (
            server.value != value["pid"]
            or abs(psutil.Process(server.value).create_time() - value["created"]) >= 0.01
        ):
            raise PermissionError("Named pipe is not owned by the verified launcher")
        channel.send_bytes(json.dumps(payload).encode("utf-8"))
        if not channel.poll(905 if action == "dispatch" else 30):
            raise TimeoutError("Launcher response deadline exceeded; attempt must not be replayed")
        result = json.loads(channel.recv_bytes(65536))
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["result"]


class RemoteNativeCapture:
    """Target-side receipt proxy; collector lifetime and handles belong to the launcher."""

    def __init__(self, managed, context):
        self.context, self.managed_process_id = context, managed.managed_process_id
        self.closed = False
        self.receipt = request_session(context, "capture_prepare", managed_process_id=self.managed_process_id)

    def finish(self):
        """Obtain the collector's actual terminal receipt without replaying preparation."""
        if not self.closed:
            self.receipt = request_session(
                self.context, "capture_finish", managed_process_id=self.managed_process_id
            )
            self.closed = True
        if self.receipt.get("outcome") == "FAIL":
            raise RuntimeError("Native capture failed; see the private capture receipt")
