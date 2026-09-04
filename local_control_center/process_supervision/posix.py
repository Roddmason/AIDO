"""Supervisor POSIX basado en process groups y límites del proceso hijo.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from .models import ProcessLaunchSpec, ProcessStats, SupervisedProcess


class PosixProcessGroupSupervisor:
    """Contiene descendientes en una sesión POSIX y termina el grupo completo."""

    def start(self, spec: ProcessLaunchSpec, **popen_kwargs: Any) -> SupervisedProcess:
        """Crea una nueva sesión antes de ejecutar el programa solicitado."""
        popen_factory = popen_kwargs.pop("popen_factory", subprocess.Popen)
        command = [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("posix_child.py")),
            str(spec.memory_limit_bytes),
            str(int(spec.below_normal_priority)),
            *spec.argv,
        ]
        process = popen_factory(
            command,
            cwd=spec.cwd,
            shell=False,
            start_new_session=True,
            **popen_kwargs,
        )
        return SupervisedProcess(spec.managed_process_id, spec.execution_id, process, process.pid)

    def terminate_tree(
        self, process: SupervisedProcess, *, grace_seconds: float, reason: str
    ) -> ProcessStats:
        """Envía SIGTERM al grupo y escala a SIGKILL tras la gracia acotada."""
        root = process.process
        with suppress(ProcessLookupError, OSError):
            os.killpg(root.pid, signal.SIGTERM)
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while _group_members(root.pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        with suppress(ProcessLookupError):
            os.killpg(root.pid, signal.SIGKILL)
        root.wait(timeout=5)
        deadline = time.monotonic() + 5
        while _group_members(root.pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _group_members(root.pid):
            raise RuntimeError("El grupo POSIX conserva procesos activos.")
        stats = self.stats(process)
        stats.cancelled = reason != "timeout"
        stats.timed_out = reason == "timeout"
        stats.termination_reason = reason
        return stats

    def stats(self, process: SupervisedProcess) -> ProcessStats:
        """Suma CPU y memoria del árbol aún observable."""
        peak_memory = 0
        cpu_seconds = 0.0
        remaining = 0
        with suppress(psutil.Error):
            members = _group_members(process.process.pid)
            remaining = sum(member.is_running() for member in members)
            for member in members:
                with suppress(psutil.Error):
                    peak_memory += member.memory_info().rss
                    times = member.cpu_times()
                    cpu_seconds += times.user + times.system
        return ProcessStats(
            exit_code=process.process.poll(),
            peak_memory_bytes=peak_memory,
            cpu_time_seconds=cpu_seconds,
            remaining_descendant_count=max(0, remaining - int(process.process.poll() is None)),
        )

    def release(self, process: SupervisedProcess) -> None:
        """Marca liberado el grupo, que no mantiene un handle adicional."""
        if not process.released and _group_members(process.process.pid):
            self.terminate_tree(process, grace_seconds=0, reason="container_released")
        process.released = True


def _group_members(group_id: int) -> list[psutil.Process]:
    members = []
    for candidate in psutil.process_iter(["pid", "status"]):
        with suppress(ProcessLookupError, PermissionError, psutil.Error):
            if os.getpgid(candidate.pid) == group_id and candidate.status() != psutil.STATUS_ZOMBIE:
                members.append(candidate)
    return members
