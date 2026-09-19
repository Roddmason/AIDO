"""Perfiles de workload y resolución segura de la política de capacidad.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.settings.repository import UNSET, SettingsRepository

from .models import ResourceSnapshot, WorkloadClass, WorkloadProfile

GIB = 1024**3

# One definition of the admitted session: all parts are fractions of the same
# aggregate scope, not independent leases. The creator uses aggregate headroom.
CAPTURE_SESSION_PARTS = {
    "creator": {"memoryBytes": 2 * GIB, "cpuPercent": 13},
    "api": {"memoryBytes": 2 * GIB, "cpuPercent": 6.5},
    "worker": {"memoryBytes": 2 * GIB, "cpuPercent": 6.5},
    "execution": {"memoryBytes": 8 * GIB, "cpuPercent": 26},
    "collector": {"memoryBytes": 4 * GIB, "cpuPercent": 13},
}

WORKLOAD_PROFILES: dict[WorkloadClass, WorkloadProfile] = {
    "capture_session": WorkloadProfile(
        workload_class="capture_session",
        heavy=True,
        light=False,
        essential=False,
        cpu_limit_percent=sum(part["cpuPercent"] for part in CAPTURE_SESSION_PARTS.values()),
        memory_limit_bytes=sum(part["memoryBytes"] for part in CAPTURE_SESSION_PARTS.values()),
        process_limit=64,
        gpu_required=False,
    ),
    "control_plane": WorkloadProfile(
        workload_class="control_plane",
        heavy=False,
        light=False,
        essential=True,
        cpu_limit_percent=20,
        memory_limit_bytes=2 * GIB,
        process_limit=8,
        gpu_required=False,
    ),
    "remote_llm_light": WorkloadProfile(
        workload_class="remote_llm_light",
        heavy=False,
        light=True,
        essential=False,
        cpu_limit_percent=15,
        memory_limit_bytes=2 * GIB,
        process_limit=4,
        gpu_required=False,
    ),
    "qa_light": WorkloadProfile(
        workload_class="qa_light",
        heavy=False,
        light=True,
        essential=False,
        cpu_limit_percent=25,
        memory_limit_bytes=4 * GIB,
        process_limit=8,
        gpu_required=False,
    ),
    "agent_cli": WorkloadProfile(
        workload_class="agent_cli",
        heavy=True,
        light=False,
        essential=False,
        cpu_limit_percent=40,
        memory_limit_bytes=8 * GIB,
        process_limit=16,
        gpu_required=False,
    ),
    "browser_test": WorkloadProfile(
        workload_class="browser_test",
        heavy=True,
        light=False,
        essential=False,
        cpu_limit_percent=40,
        memory_limit_bytes=8 * GIB,
        process_limit=24,
        gpu_required=False,
    ),
    "build_heavy": WorkloadProfile(
        workload_class="build_heavy",
        heavy=True,
        light=False,
        essential=False,
        cpu_limit_percent=65,
        memory_limit_bytes=16 * GIB,
        process_limit=32,
        gpu_required=False,
    ),
    "unreal_editor": WorkloadProfile(
        workload_class="unreal_editor",
        heavy=True,
        light=False,
        essential=False,
        cpu_limit_percent=70,
        memory_limit_bytes=24 * GIB,
        process_limit=64,
        gpu_required=True,
    ),
    "unreal_cook": WorkloadProfile(
        workload_class="unreal_cook",
        heavy=True,
        light=False,
        essential=False,
        exclusive=True,
        cpu_limit_percent=80,
        memory_limit_bytes=32 * GIB,
        process_limit=64,
        gpu_required=False,
    ),
    "local_gpu_model": WorkloadProfile(
        workload_class="local_gpu_model",
        heavy=True,
        light=False,
        essential=False,
        cpu_limit_percent=50,
        memory_limit_bytes=16 * GIB,
        process_limit=16,
        gpu_required=True,
    ),
}


@dataclass(frozen=True)
class ResourcePolicy:
    """Configuración efectiva del gobernador después de validación defensiva."""

    profile: str
    max_heavy_workloads: int
    max_light_workloads: int
    min_free_memory_bytes: int
    hard_free_memory_bytes: int
    min_free_disk_bytes: int
    max_cpu_percent: float
    unreal_reserve_memory_bytes: int
    block_local_gpu_when_unreal: bool
    sample_interval_seconds: float


def workload_profile(workload_class: WorkloadClass) -> WorkloadProfile:
    """Devuelve el perfil canónico e inmutable de una clase de workload."""
    return WORKLOAD_PROFILES[workload_class]


def _setting(repository: SettingsRepository, key: str) -> Any:
    descriptor = descriptor_for(key)
    if descriptor is None:
        raise KeyError(f"Resource setting is not registered: {key}")
    value = repository.get_value(key, "general", None)
    candidate = descriptor.default if value is UNSET else value
    try:
        return validate_value(descriptor, candidate)
    except ValueError:
        return descriptor.default


def effective_min_free_disk_bytes(connection: sqlite3.Connection, *, snapshot: ResourceSnapshot) -> int:
    """Piso de disco efectivo: el MENOR entre el absoluto configurado y su porcentaje.

    Un piso absoluto que el equipo no puede alcanzar no protege nada, solo apaga el producto:
    medido con el default de 50 GiB contra 42-48 GiB libres, el gobernador rechazaba **todo**
    spawn. Es el mismo contrato inalcanzable que ya se corrigio en el TTL de salud.

    Kubernetes expresa sus señales de eviction en porcentaje **o** valor absoluto justamente
    porque 50 GiB son el 20% de un disco de 256 GB y el 2,5% de uno de 2 TB. Tomar el menor deja
    que el absoluto siga protegiendo en discos grandes y deje de ser inalcanzable en los chicos.

    Sin informacion de capacidad (un snapshot viejo o parcial) se cae al absoluto: la ausencia de
    un dato nunca puede relajar un piso.
    """
    repository = SettingsRepository(connection)
    absolute = int(float(_setting(repository, "resources.minFreeDiskGiB")) * GIB)
    percent = float(_setting(repository, "resources.minFreeDiskPercent"))
    capacities = [value for value in snapshot.disk_total_bytes.values() if value > 0]
    if not capacities or percent <= 0:
        return absolute
    # El gobernador juzga por el volumen mas apretado, asi que el piso se calcula sobre el mismo.
    relative = int(min(capacities) * percent / 100)
    return min(absolute, relative)


def resolve_resource_policy(
    connection: sqlite3.Connection,
    *,
    snapshot: ResourceSnapshot,
) -> ResourcePolicy:
    """Resuelve settings generales y aplica la reserva interactiva cuando Unreal está activo."""
    repository = SettingsRepository(connection)
    configured_profile = str(_setting(repository, "resources.profile"))
    effective_profile = (
        "interactive_unreal"
        if configured_profile == "auto" and snapshot.unreal_editor_running
        else "development"
        if configured_profile == "auto"
        else configured_profile
    )
    min_memory_gib = float(_setting(repository, "resources.minFreeMemoryGiB"))
    unreal_reserve_gib = float(_setting(repository, "resources.unrealReserveMemoryGiB"))
    if effective_profile == "interactive_unreal":
        min_memory_gib = max(min_memory_gib, unreal_reserve_gib)
    return ResourcePolicy(
        profile=effective_profile,
        max_heavy_workloads=int(_setting(repository, "resources.maxHeavyWorkloads")),
        max_light_workloads=int(_setting(repository, "resources.maxLightWorkloads")),
        min_free_memory_bytes=int(min_memory_gib * GIB),
        hard_free_memory_bytes=int(float(_setting(repository, "resources.hardFreeMemoryGiB")) * GIB),
        min_free_disk_bytes=effective_min_free_disk_bytes(connection, snapshot=snapshot),
        max_cpu_percent=float(_setting(repository, "resources.maxCpuPercent")),
        unreal_reserve_memory_bytes=int(unreal_reserve_gib * GIB),
        block_local_gpu_when_unreal=bool(_setting(repository, "resources.blockLocalGpuWhenUnreal")),
        sample_interval_seconds=float(_setting(repository, "resources.sampleIntervalSeconds")),
    )
