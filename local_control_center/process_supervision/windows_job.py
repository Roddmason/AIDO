"""Supervisor Windows aislado sobre Job Objects mediante la dependencia condicional pywin32.

@author Rodrigo Mason
"""

from __future__ import annotations

import ctypes
import signal
import subprocess
import time
from contextlib import suppress
from typing import Any

import psutil

from local_control_center.shared.diagnostics import diagnostic_event

from .models import ProcessLaunchSpec, ProcessStats, SupervisedProcess

_CREATE_SUSPENDED = 0x00000004
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 15
_JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
_JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4


class _CpuRateControlInformation(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


class WindowsJobObjectProcessSupervisor:
    """Asigna el proceso suspendido a un Job Object antes de reanudar su primer hilo."""

    def start(self, spec: ProcessLaunchSpec, **popen_kwargs: Any) -> SupervisedProcess:
        """Aplica memoria, procesos, kill-on-close, CPU y prioridad al árbol."""
        import win32api
        import win32con
        import win32job

        popen_factory = popen_kwargs.pop("popen_factory", subprocess.Popen)
        before_resume = popen_kwargs.pop("before_resume", None)
        job = win32job.CreateJobObject(None, "")
        try:
            applied = _configure_job(job, spec)
        except Exception as error:
            diagnostic_event(
                "process.limits.error",
                component="windows_job",
                error=error,
                executionId=spec.execution_id,
                managedProcessId=spec.managed_process_id,
                phase="configure_job",
                requested={
                    "memoryBytes": spec.memory_limit_bytes,
                    "cpuPercent": spec.cpu_limit_percent,
                    "processes": spec.process_limit,
                },
            )
            job.Close()
            raise

        creationflags = (
            int(popen_kwargs.pop("creationflags", 0))
            | _CREATE_SUSPENDED
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        if spec.below_normal_priority:
            creationflags |= _BELOW_NORMAL_PRIORITY_CLASS
        process = managed = None
        try:
            process = popen_factory(
                spec.argv,
                cwd=spec.cwd,
                shell=False,
                creationflags=creationflags,
                **popen_kwargs,
            )
            diagnostic_event(
                "process.created.suspended",
                component="windows_job",
                executionId=spec.execution_id,
                managedProcessId=spec.managed_process_id,
                pid=process.pid,
                processCreationTime=psutil.Process(process.pid).create_time(),
                outcome="suspended",
            )
            process_handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_INFORMATION,
                False,
                process.pid,
            )
            try:
                win32job.AssignProcessToJobObject(job, process_handle)
                verified = _readback(job, process_handle)
            finally:
                process_handle.Close()
            managed = SupervisedProcess(spec.managed_process_id, spec.execution_id, process, job)
            managed.containment_evidence = {
                "requested": {
                    "memoryBytes": spec.memory_limit_bytes,
                    "processes": spec.process_limit,
                    "cpuPercent": spec.cpu_limit_percent,
                    "killOnClose": True,
                },
                "applied": applied,
                "verified": verified,
            }
            diagnostic_event(
                "process.contained",
                component="windows_job",
                executionId=spec.execution_id,
                managedProcessId=spec.managed_process_id,
                pid=process.pid,
                processCreationTime=psutil.Process(process.pid).create_time(),
                **managed.containment_evidence,
            )
            if before_resume is not None:
                before_resume(managed)
            _resume_root_thread(process.pid)
            diagnostic_event(
                "process.resumed",
                component="windows_job",
                executionId=spec.execution_id,
                managedProcessId=spec.managed_process_id,
                pid=process.pid,
            )
            return managed
        except Exception as error:
            diagnostic_event(
                "process.native.error",
                component="windows_job",
                error=error,
                executionId=spec.execution_id,
                managedProcessId=spec.managed_process_id,
                pid=process.pid if process is not None else None,
                phase="containment_or_resume",
            )
            if process is not None:
                with suppress(Exception):
                    win32job.TerminateJobObject(job, 1)
                # La asignación puede fallar antes de que el Job Object posea la raíz suspendida.
                with suppress(OSError):
                    process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
            if managed is not None and managed.native_capture is not None:
                try:
                    managed.native_capture.finish()
                except Exception as cleanup_error:
                    diagnostic_event(
                        "native.cleanup.error",
                        component="windows_job",
                        error=cleanup_error,
                        executionId=spec.execution_id,
                        managedProcessId=spec.managed_process_id,
                    )
            job.Close()
            raise

    def terminate_tree(
        self, process: SupervisedProcess, *, grace_seconds: float, reason: str
    ) -> ProcessStats:
        """Solicita CTRL_BREAK al grupo y termina el Job Object si no sale en gracia."""
        import win32job

        root = process.process
        if root.poll() is None:
            with suppress(OSError):
                root.send_signal(signal.CTRL_BREAK_EVENT)
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while _active_pids(process.native_handle) and time.monotonic() < deadline:
            time.sleep(0.02)
        # La raíz puede haber salido dejando descendientes dentro del Job Object.
        if _active_pids(process.native_handle):
            win32job.TerminateJobObject(process.native_handle, 1)
        root.wait(timeout=5)
        deadline = time.monotonic() + 5
        while _active_pids(process.native_handle) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _active_pids(process.native_handle):
            raise RuntimeError("El Job Object conserva procesos activos tras terminarlo.")
        stats = self.stats(process)
        stats.cancelled = reason != "timeout"
        stats.timed_out = reason == "timeout"
        stats.termination_reason = reason
        return stats

    def stats(self, process: SupervisedProcess) -> ProcessStats:
        """Consulta contadores nativos; un error no demuestra ausencia de descendientes."""
        import win32job

        extended = win32job.QueryInformationJobObject(
            process.native_handle, win32job.JobObjectExtendedLimitInformation
        )
        accounting = win32job.QueryInformationJobObject(
            process.native_handle, win32job.JobObjectBasicAndIoAccountingInformation
        )["BasicInfo"]
        pids = _active_pids(process.native_handle)
        cpu_seconds = (float(accounting["TotalUserTime"]) + float(accounting["TotalKernelTime"])) / 10_000_000
        return ProcessStats(
            exit_code=process.process.poll(),
            peak_memory_bytes=int(extended["PeakJobMemoryUsed"]),
            cpu_time_seconds=cpu_seconds,
            remaining_descendant_count=max(0, len(pids) - int(process.process.poll() is None)),
        )

    def release(self, process: SupervisedProcess) -> None:
        """Cierra el Job Object una vez; kill-on-close contiene también rutas de error."""
        if process.released:
            return
        process.native_handle.Close()
        process.released = True


def _configure_job(job: Any, spec: ProcessLaunchSpec) -> dict:
    import win32job

    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    basic = info["BasicLimitInformation"]
    basic["LimitFlags"] |= (
        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
        | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    )
    basic["ActiveProcessLimit"] = spec.process_limit
    info["JobMemoryLimit"] = spec.memory_limit_bytes
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    cpu_applied = _apply_cpu_rate(job, spec.cpu_limit_percent)
    return {
        "memoryProcessKillOnClose": True,
        "cpu": cpu_applied,
        "cpuWin32Error": 0 if cpu_applied else ctypes.get_last_error(),
    }


def _readback(job: Any, process_handle: Any) -> dict:
    import win32job

    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    basic = info["BasicLimitInformation"]
    cpu = _CpuRateControlInformation()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    ok = kernel.QueryInformationJobObject(int(job), 15, ctypes.byref(cpu), ctypes.sizeof(cpu), None)
    return {
        "member": bool(win32job.IsProcessInJob(process_handle, job)),
        "memoryBytes": int(info["JobMemoryLimit"]),
        "processes": int(basic["ActiveProcessLimit"]),
        "killOnClose": bool(basic["LimitFlags"] & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
        "cpuPercent": cpu.CpuRate / 100 if ok else None,
        "cpuControlFlags": cpu.ControlFlags if ok else None,
        "cpuWin32Error": 0 if ok else ctypes.get_last_error(),
    }


def _resume_root_thread(pid: int) -> None:
    import win32api
    import win32con
    import win32process

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with suppress(psutil.Error):
            threads = psutil.Process(pid).threads()
            if threads:
                thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, threads[0].id)
                try:
                    win32process.ResumeThread(thread)
                    return
                finally:
                    thread.Close()
        time.sleep(0.01)
    raise RuntimeError("Could not resume the contained Windows process thread.")


def _apply_cpu_rate(job: Any, cpu_limit_percent: float) -> bool:
    """Aplica hard cap si el Windows host soporta CPU rate control."""
    rate = max(1, min(100, int(cpu_limit_percent))) * 100
    information = _CpuRateControlInformation(
        _JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | _JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
        rate,
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    applied = kernel32.SetInformationJobObject(
        int(job),
        _JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    if not applied and ctypes.get_last_error() not in {1, 50, 120}:
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(applied)


def _active_pids(job: Any) -> tuple[int, ...]:
    import win32job

    return tuple(win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList))
