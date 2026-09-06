"""Captura Windows privada y opt-in: identidad exacta, ProcDump contenido, sin registro global.

Los imports Windows son locales. Un minidump se clasifica SENSITIVE_NATIVE y nunca se exporta.
@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import shutil
import struct
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import psutil

from local_control_center.shared.diagnostics import diagnostic_event

# Official Sysinternals 12.01 x64 download; Authenticode Microsoft signature checked at preparation.
PROCDUMP_SHA256 = "d1fc99ae304bd1d2bf28abeb62531da959e2431916194981b88c958fd713a8e6"


def private_directory(path: Path) -> None:
    """Restringe una carpeta nueva al usuario efectivo y SYSTEM antes de capturar memoria."""
    if os.name != "nt":
        raise RuntimeError("Windows native capture unavailable on this platform")
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    path.mkdir(parents=True, exist_ok=False)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    inherit = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
    for principal in (sid, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)):
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION, inherit, ntsecuritycon.FILE_ALL_ACCESS, principal
        )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


def debugger_attached(pid: int, created: float) -> bool:
    """Readback OS de asociación al PID/creation time retenido por el supervisor."""
    import ctypes

    import win32api
    import win32con

    if abs(psutil.Process(pid).create_time() - created) > 0.001:
        raise RuntimeError("Native diagnostic target identity changed")
    process = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
    try:
        result = ctypes.c_int()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CheckRemoteDebuggerPresent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not kernel.CheckRemoteDebuggerPresent(int(process), ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(result.value)
    finally:
        process.Close()


class NativeCapture:
    """Un colector con reserva propia, supervisado; no recibe credenciales del CLI."""

    def __init__(self, *, managed, options: dict, db_path: Path, context, root: Path):
        from .context import ProcessExecutionContext, execution_scope
        from .service import ProcessSupervisorService

        self.target = managed
        self.created = psutil.Process(managed.process.pid).create_time()
        self.directory = root / "native" / managed.managed_process_id
        self.receipt = {
            "classification": "SENSITIVE_NATIVE",
            "targetPid": managed.process.pid,
            "targetCreationTime": self.created,
            "collectorReadyBeforeResume": False,
            "collectorClosed": False,
            "debuggerMayPauseTarget": True,
        }
        if file_sha256(Path(options["nativeCollector"])) != PROCDUMP_SHA256:
            raise ValueError("Native collector differs from the prepared official fingerprint")
        self.receipt["collectorSha256"] = PROCDUMP_SHA256
        free_bytes = shutil.disk_usage(root).free
        self.receipt.update(
            freeSpaceBeforeBytes=free_bytes, freeSpaceFloorBytes=1024**3, exclusiveDiskReservation=False
        )
        if free_bytes < 1024**3:
            raise OSError("Native capture requires 1 GiB free-space floor")
        private_directory(self.directory)
        # Atomic one-shot marker: even a failed attach must not replay an uncertain attempt.
        marker = root / f"native-claimed-{managed.execution_id}"
        with marker.open("x", encoding="utf-8") as output:
            output.write(managed.managed_process_id)
        capture_context = (
            replace(context, resource_lease_id=None)
            if context
            else ProcessExecutionContext(db_path=db_path, execution_id=managed.execution_id)
        )
        self.readers = []
        self.closed = False
        with execution_scope(capture_context):
            self.service = ProcessSupervisorService(db_path=db_path)
            self.collector = self.service.start(
                argv=[
                    options["nativeCollector"],
                    "-accepteula",
                    "-mm",
                    "-e",
                    "-n",
                    "1",
                    "-at",
                    "10",
                    str(managed.process.pid),
                    str(self.directory / "exception.dmp"),
                ],
                cwd=self.directory,
                workload_class="qa_light",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                capture_encoding="utf-16-le",
                env={
                    k: v
                    for k, v in os.environ.items()
                    if k in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "USERPROFILE", "PATH"}
                },
            )
        for stream in (self.collector.process.stdout, self.collector.process.stderr):
            reader = threading.Thread(target=self._drain, args=(stream,), daemon=True)
            reader.start()
            self.readers.append(reader)
        try:
            deadline = min(time.time() + 10, options["expiresAt"])
            while time.time() < deadline:
                if self.collector.process.poll() is not None:
                    raise RuntimeError("Native collector exited before attaching")
                if debugger_attached(managed.process.pid, self.created):
                    self.receipt["collectorReadyBeforeResume"] = True
                    self.receipt["collectorPid"] = self.collector.process.pid
                    diagnostic_event(
                        "native.collector.ready",
                        component="native_capture",
                        executionId=managed.execution_id,
                        managedProcessId=managed.managed_process_id,
                        pid=managed.process.pid,
                        processCreationTime=self.created,
                        debuggerPaused=True,
                        evidenceRefs=[
                            {"classification": "SENSITIVE_NATIVE", "id": managed.managed_process_id}
                        ],
                    )
                    return
                time.sleep(0.02)
            raise TimeoutError("Native collector attach deadline exceeded")
        except BaseException:
            try:
                self.finish()
            except Exception as cleanup_error:
                diagnostic_event(
                    "native.cleanup.error",
                    component="native_capture",
                    error=cleanup_error,
                    executionId=managed.execution_id,
                    managedProcessId=managed.managed_process_id,
                )
            raise

    @staticmethod
    def _drain(stream):
        try:
            while stream.read1(65536):
                pass  # CapturedReader preserves the bounded, redacted artifact.
        finally:
            stream.close()

    def finish(self):
        """Plazo de captura acotado; cleanup sólo del colector/target propios."""
        if self.closed:
            return
        try:
            self.collector.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            # The supervisor still owns the target Job Object; never detach and leave it running.
            self.service.backend.terminate_tree(
                self.collector, grace_seconds=0, reason="native_capture_deadline"
            )
        finally:
            for reader in self.readers:
                reader.join(timeout=2)
            self.service.complete(
                self.collector,
                exit_code=self.collector.process.poll(),
                termination_reason="native_capture_finished",
            )
            self.closed = True
            self.receipt["collectorClosed"] = self.collector.released
        dumps = list(self.directory.glob("*.dmp"))
        if len(dumps) == 1:
            info = inspect_minidump(dumps[0])
            if info["pid"] != self.target.process.pid:
                raise RuntimeError("Captured dump belongs to a different PID")
            if info["processCreationTime"] != int(self.created):
                raise RuntimeError("Captured dump process creation time does not match")
            self.receipt.update(info)
            self.receipt["sha256"] = file_sha256(dumps[0])
        if len(dumps) > 1:
            raise RuntimeError("Native capture produced more than one artifact")
        self.receipt["outcome"] = "captured" if len(dumps) == 1 else "NOT_RUN"
        with (self.directory / "capture.receipt.json").open("x", encoding="utf-8") as output:
            json.dump(self.receipt, output, indent=2)
        diagnostic_event(
            "native.capture.closed",
            component="native_capture",
            executionId=self.target.execution_id,
            managedProcessId=self.target.managed_process_id,
            outcome="captured" if len(dumps) == 1 else "NOT_RUN",
            errorCode=self.receipt.get("exceptionCode"),
            evidenceRefs=[{"classification": "SENSITIVE_NATIVE", "id": self.target.managed_process_id}],
        )


def file_sha256(path: Path) -> str:
    """Fingerprint por streaming, sin cargar binarios completos en RAM."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inspect_minidump(path: Path) -> dict:
    """Valida rangos y streams de identidad/excepción; no pretende desenrollar una pila."""
    with path.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if len(data) < 32 or data[:4] != b"MDMP":
            raise ValueError("Invalid minidump header")
        count, directory = struct.unpack_from("<II", data, 8)
        if not 1 <= count <= 128 or directory + count * 12 > len(data):
            raise ValueError("Invalid minidump stream directory")
        streams = {}
        for index in range(count):
            kind, size, offset = struct.unpack_from("<III", data, directory + index * 12)
            if offset + size > len(data):
                raise ValueError("Truncated minidump stream")
            streams[kind] = (offset, size)
        if 6 not in streams or 15 not in streams or 3 not in streams:
            raise ValueError("Minidump lacks exception, process identity or threads")
        exception, exception_size = streams[6]
        misc, misc_size = streams[15]
        if exception_size < 168 or misc_size < 24:
            raise ValueError("Incomplete exception or process stream")
        flags = struct.unpack_from("<I", data, misc + 4)[0]
        if flags & 3 != 3:
            raise ValueError("Minidump lacks valid process identity flags")
        pid = struct.unpack_from("<I", data, misc + 8)[0]
        created = struct.unpack_from("<I", data, misc + 12)[0]
        thread_id, code = (
            struct.unpack_from("<I", data, exception)[0],
            struct.unpack_from("<I", data, exception + 8)[0],
        )
        address = struct.unpack_from("<Q", data, exception + 24)[0]
        return {
            "pid": pid,
            "processCreationTime": created,
            "threadId": thread_id,
            "exceptionCode": code,
            "exceptionAddress": hex(address),
            "sizeBytes": len(data),
            "streamCount": count,
            "symbolsAvailable": False,
            "stackAnalysis": "NOT_RUN",
            "classification": "SENSITIVE_NATIVE",
        }
