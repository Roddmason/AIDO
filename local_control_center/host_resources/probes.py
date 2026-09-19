"""Sondas acotadas de CPU, memoria, disco, procesos y GPU para el gobernador.

@author Rodrigo Mason
"""

from __future__ import annotations

import ctypes
import os
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import psutil

from local_control_center.nvidia_nim.system_probe import (
    PROBE_TIMEOUT_SECONDS,
    ProbeCommandRunner,
    SubprocessProbeCommandRunner,
)
from local_control_center.shared.time import utc_now

from .models import ResourceSnapshot

MIB = 1024**2


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_commit_memory() -> tuple[int | None, int | None]:
    if os.name != "nt":
        return None, None
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None, None
    commit_limit = int(status.ullTotalPageFile)
    committed = max(0, commit_limit - int(status.ullAvailPageFile))
    return committed, commit_limit


def _gpu_usage(runner: ProbeCommandRunner) -> tuple[float | None, int | None, int | None]:
    executable = runner.which("nvidia-smi")
    if not executable:
        return None, None, None
    result = runner.run(
        [
            executable,
            "--query-gpu=utilization.gpu,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ],
        timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    if result.return_code != 0:
        return None, None, None
    rows: list[tuple[float, int, int]] = []
    for line in result.stdout.splitlines():
        columns = [column.strip() for column in line.split(",")]
        if len(columns) != 3:
            continue
        try:
            rows.append((float(columns[0]), int(float(columns[1])) * MIB, int(float(columns[2])) * MIB))
        except ValueError:
            continue
    if not rows:
        return None, None, None
    return max(row[0] for row in rows), sum(row[1] for row in rows), sum(row[2] for row in rows)


def _process_state() -> tuple[int, bool, bool, bool, bool]:
    active_aido = 0
    unreal = docker = wsl = ollama = False
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            name = str(process.info.get("name") or "").lower()
            command = " ".join(str(part) for part in (process.info.get("cmdline") or [])).lower()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        material = f"{name} {command}"
        active_aido += int(
            "local_control_center" in material
            or "local-control-center" in material
            or "start_control_center" in material
        )
        unreal = unreal or "unrealeditor" in name
        docker = docker or name in {"docker.exe", "dockerd.exe", "docker desktop.exe"}
        wsl = wsl or name in {"wsl.exe", "wslhost.exe", "wslservice.exe", "vmmemwsl.exe"}
        ollama = ollama or name in {"ollama", "ollama.exe", "ollama app.exe"}
    return active_aido, unreal, docker, wsl, ollama


class HostResourceProbe:
    """Recolecta métricas con psutil y reutiliza el runner allowlisteado de NVIDIA NIM."""

    def __init__(
        self,
        *,
        relevant_paths: Sequence[str | Path],
        gpu_runner: ProbeCommandRunner | None = None,
        active_workload_source: Callable[[], list[str]] | None = None,
    ) -> None:
        self.relevant_paths = [Path(path).resolve() for path in relevant_paths]
        self.gpu_runner = gpu_runner or SubprocessProbeCommandRunner()
        self.active_workload_source = active_workload_source or (lambda: [])
        self._cpu_samples: deque[tuple[float, float]] = deque()
        self._previous_disk_io: tuple[float, int, int] | None = None

    def sample(self, *, cpu_interval_seconds: float = 1.0) -> ResourceSnapshot:
        """Toma una muestra; la ventana bloqueante de CPU queda limitada a un segundo."""
        # A fresh probe/thread has no psutil baseline: interval=0 can report meaningless 0%.
        # Admission always needs a measured window, even for callers requesting a quick sample.
        # https://psutil.io/api/#psutil.cpu_percent
        interval = max(0.1, min(float(cpu_interval_seconds), 1.0)) if cpu_interval_seconds > 0 else 1.0
        cpu_1s = float(psutil.cpu_percent(interval=interval))
        now_monotonic = time.monotonic()
        self._cpu_samples.append((now_monotonic, cpu_1s))
        while self._cpu_samples and now_monotonic - self._cpu_samples[0][0] > 30:
            self._cpu_samples.popleft()
        cpu_30s = sum(sample[1] for sample in self._cpu_samples) / len(self._cpu_samples)

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        committed_memory, commit_limit = _windows_commit_memory()
        disk_free, disk_total = self._disk_by_volume()
        disk_read_rate, disk_write_rate = self._disk_rates(now_monotonic)
        gpu_utilization, gpu_used, gpu_free = _gpu_usage(self.gpu_runner)
        active_aido, unreal, docker, wsl, ollama = _process_state()
        return ResourceSnapshot(
            sampled_at=utc_now(),
            cpu_percent_1s=cpu_1s,
            cpu_percent_30s=cpu_30s,
            logical_processors=max(1, int(psutil.cpu_count(logical=True) or 1)),
            total_memory_bytes=int(memory.total),
            available_memory_bytes=int(memory.available),
            committed_memory_bytes=committed_memory,
            commit_limit_bytes=commit_limit,
            swap_or_pagefile_used_bytes=max(0, int(swap.used)),
            disk_free_bytes=disk_free,
            disk_total_bytes=disk_total,
            disk_read_bytes_per_second=disk_read_rate,
            disk_write_bytes_per_second=disk_write_rate,
            gpu_utilization_percent=gpu_utilization,
            gpu_memory_used_bytes=gpu_used,
            gpu_memory_free_bytes=gpu_free,
            active_aido_process_count=active_aido,
            active_workloads=list(self.active_workload_source()),
            unreal_editor_running=unreal,
            docker_running=docker,
            wsl_running=wsl,
            ollama_running=ollama,
        )

    def _disk_by_volume(self) -> tuple[dict[str, int], dict[str, int]]:
        """Espacio libre y capacidad por volumen relevante, en una sola pasada.

        La capacidad hace falta para expresar el piso como porcentaje: un piso absoluto de 50
        GiB es el 20% de un disco de 256 GB y el 2,5% de uno de 2 TB.
        """
        free: dict[str, int] = {}
        total: dict[str, int] = {}
        for path in self.relevant_paths:
            probe_path = path if path.is_dir() else path.parent
            while not probe_path.is_dir() and probe_path != probe_path.parent:
                probe_path = probe_path.parent
            volume = path.anchor or str(probe_path)
            if volume in free:
                continue
            try:
                usage = psutil.disk_usage(str(probe_path))
            except (FileNotFoundError, OSError):
                continue
            free[volume] = int(usage.free)
            total[volume] = int(usage.total)
        return free, total

    def _disk_rates(self, now_monotonic: float) -> tuple[float, float]:
        counters: Any = psutil.disk_io_counters()
        if counters is None:
            return 0.0, 0.0
        current = (now_monotonic, int(counters.read_bytes), int(counters.write_bytes))
        previous = self._previous_disk_io
        self._previous_disk_io = current
        if previous is None:
            return 0.0, 0.0
        elapsed = max(0.001, current[0] - previous[0])
        return (
            max(0.0, (current[1] - previous[1]) / elapsed),
            max(0.0, (current[2] - previous[2]) / elapsed),
        )
