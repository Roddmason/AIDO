"""Contratos tipados de procesos administrados, límites y estadísticas de ejecución.

@author Rodrigo Mason
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from local_control_center.host_resources.models import WorkloadClass


@dataclass(frozen=True)
class ProcessLaunchSpec:
    """Describe una ejecución sin exponer su argv en el registro durable."""

    managed_process_id: str
    execution_id: str
    argv: list[str] = field(repr=False)
    cwd: str
    workload_class: WorkloadClass
    command_fingerprint: str
    memory_limit_bytes: int
    process_limit: int
    cpu_limit_percent: float
    below_normal_priority: bool


@dataclass
class ProcessStats:
    """Estadísticas acumuladas y resultado terminal de un árbol administrado."""

    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    peak_memory_bytes: int = 0
    cpu_time_seconds: float = 0.0
    termination_reason: str = ""
    remaining_descendant_count: int = 0


@dataclass
class SupervisedProcess:
    """Handle en memoria que une un proceso raíz con su contenedor nativo."""

    managed_process_id: str
    execution_id: str
    process: Any
    native_handle: Any = None
    released: bool = False
    lock: Any = field(default_factory=threading.RLock, repr=False)
    terminal_stats: ProcessStats | None = None
    stop_watcher: threading.Event = field(default_factory=threading.Event, repr=False)
    watcher: threading.Thread | None = field(default=None, repr=False)
    resource_lease_id: str | None = None
    owns_resource_lease: bool = False
    captures: dict[str, Any] = field(default_factory=dict, repr=False)
    capture_failure: threading.Event = field(default_factory=threading.Event, repr=False)
    containment_evidence: dict[str, Any] = field(default_factory=dict)
    native_capture: Any = field(default=None, repr=False)
    root_create_time: float = 0
    terminal_outcome: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManagedProcessRecord:
    """Representación durable y no secreta de una ejecución administrada."""

    managed_process_id: str
    execution_id: str
    root_pid: int
    workload_class: WorkloadClass
    command_fingerprint: str
    started_at: str
    finished_at: str | None
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    peak_memory_bytes: int
    cpu_time_seconds: float
    stdout_artifact_id: str | None
    stderr_artifact_id: str | None
    termination_reason: str
    cancel_requested_at: str | None
    released_at: str | None
    resource_lease_id: str | None = None
    owner_pid: int = 0
    owner_create_time: float = 0
    root_create_time: float = 0
