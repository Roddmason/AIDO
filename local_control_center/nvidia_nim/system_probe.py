"""Bounded allowlisted system probes for NVIDIA NIM local compatibility facts.

@author Rodrigo Mason
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contracts import (
    NvidiaNimContainerRuntimeFacts,
    NvidiaNimContainerToolkitFacts,
    NvidiaNimGpuFacts,
    NvidiaNimPlatformFacts,
    NvidiaNimSystemFacts,
)

GIB = 1024**3
PROBE_TIMEOUT_SECONDS = 2.0
MAX_CAPTURE_BYTES = 64 * 1024
ALLOWED_COMMANDS = {"docker", "nvidia-ctk", "nvidia-smi", "podman", "wsl", "wsl.exe"}
ALLOWED_PROBE_ARGUMENTS = {
    "docker": {
        ("--version",),
        ("version", "--format", "{{.Server.Version}}"),
    },
    "nvidia-ctk": {
        ("--version",),
        ("cdi", "list"),
    },
    "nvidia-smi": {
        (
            "--query-gpu=index,name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ),
    },
    "podman": {
        ("--version",),
        ("info", "--format", "json"),
    },
    "wsl": {("--list", "--verbose")},
}


@dataclass(frozen=True)
class ProbeCommandResult:
    """Bounded command result that intentionally excludes raw stderr."""

    return_code: int
    stdout: str


class ProbeCommandRunner(Protocol):
    """Port for locating and running only allowlisted read-only probes."""

    def which(self, command: str) -> str | None:
        """Resolve an allowlisted probe executable without executing it."""
        ...

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProbeCommandResult:
        """Run one exact allowlisted probe argv under a bounded timeout."""
        ...


def _decode_output(payload: bytes) -> str:
    bounded = payload[:MAX_CAPTURE_BYTES]
    if bounded.startswith((b"\xff\xfe", b"\xfe\xff")) or bounded.count(b"\x00") > len(bounded) // 4:
        try:
            return bounded.decode("utf-16").replace("\x00", "")
        except UnicodeDecodeError:
            pass
    return bounded.decode("utf-8", errors="replace").replace("\x00", "")


class SubprocessProbeCommandRunner:
    """Runs only fixed read-only commands with no shell and bounded output/time."""

    def which(self, command: str) -> str | None:
        """Resolve only a known read-only probe executable."""
        if command not in ALLOWED_COMMANDS:
            return None
        return shutil.which(command)

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProbeCommandResult:
        """Execute one exact allowlisted read-only argv without a shell."""
        command_name = Path(argv[0]).name.lower() if argv else ""
        normalized_command = command_name.removesuffix(".exe")
        allowed_arguments = ALLOWED_PROBE_ARGUMENTS.get(normalized_command, set())
        if not argv or tuple(argv[1:]) not in allowed_arguments:
            raise ValueError("System probe command is not allowlisted.")
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ProbeCommandResult(return_code=-1, stdout="")
        return ProbeCommandResult(
            return_code=int(completed.returncode),
            stdout=_decode_output(completed.stdout),
        )


def _parse_version(output: str) -> str | None:
    match = re.search(r"(?i)\bversion\s+v?(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?)", output)
    if match:
        return match.group(1)
    stripped = output.strip()
    return stripped if re.fullmatch(r"v?\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?", stripped) else None


def _parse_gpus(output: str) -> list[NvidiaNimGpuFacts]:
    gpus: list[NvidiaNimGpuFacts] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            index = int(parts[0])
            memory_gib = round(float(parts[2]) / 1024, 2)
        except ValueError:
            continue
        gpus.append(
            NvidiaNimGpuFacts(
                index=index,
                name=parts[1],
                memoryTotalGiB=memory_gib,
                driverVersion=parts[3] or None,
                computeCapability=parts[4] or None,
            )
        )
    return gpus


def _parse_wsl_distributions(output: str) -> tuple[list[str], int | None]:
    distributions: list[str] = []
    versions: list[int] = []
    for raw_line in output.splitlines():
        line = raw_line.strip().lstrip("*").strip()
        if not line or line.lower().startswith("name"):
            continue
        columns = line.split()
        if len(columns) < 2 or columns[-1] not in {"1", "2"}:
            continue
        distributions.append(columns[0])
        versions.append(int(columns[-1]))
    return distributions, (2 if 2 in versions else max(versions, default=None))


def _windows_memory() -> tuple[float | None, float | None]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_phys", ctypes.c_ulonglong),
            ("avail_phys", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("avail_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("avail_virtual", ctypes.c_ulonglong),
            ("avail_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None, None
    except (AttributeError, OSError):
        return None, None
    swap_bytes = max(0, int(status.total_page_file) - int(status.total_phys))
    return round(status.total_phys / GIB, 2), round(swap_bytes / GIB, 2)


def _linux_memory() -> tuple[float | None, float | None]:
    try:
        values = {
            key: int(value.split()[0]) * 1024
            for key, value in (
                line.split(":", 1) for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
            )
        }
    except (OSError, ValueError):
        return None, None
    total = values.get("MemTotal")
    swap = values.get("SwapTotal")
    return (
        round(total / GIB, 2) if total is not None else None,
        round(swap / GIB, 2) if swap is not None else None,
    )


class ReadOnlySystemProbe:
    """Collect normalized host facts through an injectable command boundary."""

    def __init__(
        self,
        *,
        runner: ProbeCommandRunner | None = None,
        system_name: str | None = None,
        machine: str | None = None,
        release: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.runner = runner or SubprocessProbeCommandRunner()
        self.system_name = system_name or platform.system()
        self.machine = machine or platform.machine()
        self.release = release or platform.release()
        self.environment = dict(os.environ if environment is None else environment)

    def _platform_facts(self) -> NvidiaNimPlatformFacts:
        normalized_system = self.system_name.lower()
        is_linux_wsl = normalized_system == "linux" and (
            "microsoft" in self.release.lower() or bool(self.environment.get("WSL_DISTRO_NAME"))
        )
        wsl_command = self.runner.which("wsl.exe") or self.runner.which("wsl")
        distributions: list[str] = []
        wsl_version: int | None = 2 if is_linux_wsl else None
        if normalized_system == "windows" and wsl_command:
            result = self.runner.run(
                [wsl_command, "--list", "--verbose"],
                timeout_seconds=PROBE_TIMEOUT_SECONDS,
            )
            if result.return_code == 0:
                distributions, wsl_version = _parse_wsl_distributions(result.stdout)
        elif is_linux_wsl and self.environment.get("WSL_DISTRO_NAME"):
            distributions = [self.environment["WSL_DISTRO_NAME"]]
        if is_linux_wsl or wsl_version == 2:
            target = "wsl2"
        elif normalized_system == "linux":
            target = "linux"
        elif normalized_system == "windows":
            target = "windows"
        else:
            target = "unknown"
        return NvidiaNimPlatformFacts(
            os=normalized_system or "unknown",
            target=target,
            architecture=self.machine or "unknown",
            wslAvailable=bool(wsl_command or is_linux_wsl),
            wslVersion=wsl_version,
            distributions=distributions,
        )

    def _gpus(self) -> list[NvidiaNimGpuFacts]:
        executable = self.runner.which("nvidia-smi")
        if not executable:
            return []
        result = self.runner.run(
            [
                executable,
                "--query-gpu=index,name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
        )
        return _parse_gpus(result.stdout) if result.return_code == 0 else []

    def _container_runtime(self, runtime_id: str) -> NvidiaNimContainerRuntimeFacts:
        executable = self.runner.which(runtime_id)
        if not executable:
            return NvidiaNimContainerRuntimeFacts(
                id=runtime_id,
                installed=False,
                serverReachable=False,
                version=None,
            )
        version_result = self.runner.run(
            [executable, "--version"],
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
        )
        if runtime_id == "docker":
            server_argv = [executable, "version", "--format", "{{.Server.Version}}"]
        else:
            server_argv = [executable, "info", "--format", "json"]
        server_result = self.runner.run(server_argv, timeout_seconds=PROBE_TIMEOUT_SECONDS)
        return NvidiaNimContainerRuntimeFacts(
            id=runtime_id,
            installed=True,
            serverReachable=server_result.return_code == 0,
            version=_parse_version(version_result.stdout) if version_result.return_code == 0 else None,
        )

    def _container_toolkit(self) -> NvidiaNimContainerToolkitFacts:
        executable = self.runner.which("nvidia-ctk")
        if not executable:
            return NvidiaNimContainerToolkitFacts(
                installed=False,
                version=None,
                cdiAvailable=False,
                detectionScope="aido_host_process",
            )
        version_result = self.runner.run(
            [executable, "--version"],
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
        )
        cdi_result = self.runner.run(
            [executable, "cdi", "list"],
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
        )
        return NvidiaNimContainerToolkitFacts(
            installed=version_result.return_code == 0,
            version=_parse_version(version_result.stdout) if version_result.return_code == 0 else None,
            cdiAvailable=(cdi_result.return_code == 0 and "nvidia.com/gpu" in cdi_result.stdout.lower()),
            detectionScope="aido_host_process",
        )

    def collect(self) -> NvidiaNimSystemFacts:
        """Collect facts without pulling images, starting runtimes, or changing the host."""
        normalized_system = self.system_name.lower()
        if normalized_system == "windows":
            system_memory, swap = _windows_memory()
        elif normalized_system == "linux":
            system_memory, swap = _linux_memory()
        else:
            system_memory, swap = None, None
        try:
            disk_free = round(shutil.disk_usage(Path.home()).free / GIB, 2)
        except OSError:
            disk_free = None
        return NvidiaNimSystemFacts(
            platform=self._platform_facts(),
            gpus=self._gpus(),
            systemMemoryGiB=system_memory,
            swapGiB=swap,
            diskFreeGiB=disk_free,
            resourceScope="host" if normalized_system == "windows" else "runtime_target",
            containerRuntimes=[
                self._container_runtime("docker"),
                self._container_runtime("podman"),
            ],
            containerToolkit=self._container_toolkit(),
        )


def collect_system_facts() -> NvidiaNimSystemFacts:
    """Collect a fresh sanitized fact snapshot from the AIDO host process."""
    return ReadOnlySystemProbe().collect()
