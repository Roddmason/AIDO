"""Contratos tipados del gobernador global de recursos del host.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.shared.time import utc_now

WorkloadClass = Literal[
    "control_plane",
    "remote_llm_light",
    "agent_cli",
    "qa_light",
    "browser_test",
    "build_heavy",
    "capture_session",
    "unreal_editor",
    "unreal_cook",
    "local_gpu_model",
    "local_model_call",
]


class _AliasedModel(BaseModel):
    """Permite construir contratos con nombres Python o aliases camelCase."""

    model_config = ConfigDict(populate_by_name=True)


class ResourceSnapshot(_AliasedModel):
    """Fotografía acotada de capacidad y workloads visibles en el host."""

    sampled_at: str = Field(alias="sampledAt")
    cpu_percent_1s: float = Field(alias="cpuPercent1s", ge=0, le=100)
    cpu_percent_30s: float = Field(alias="cpuPercent30s", ge=0, le=100)
    logical_processors: int = Field(alias="logicalProcessors", ge=1)
    total_memory_bytes: int = Field(alias="totalMemoryBytes", ge=0)
    available_memory_bytes: int = Field(alias="availableMemoryBytes", ge=0)
    committed_memory_bytes: int | None = Field(default=None, alias="committedMemoryBytes", ge=0)
    commit_limit_bytes: int | None = Field(default=None, alias="commitLimitBytes", ge=0)
    swap_or_pagefile_used_bytes: int = Field(alias="swapOrPagefileUsedBytes", ge=0)
    disk_free_bytes: dict[str, int] = Field(alias="diskFreeBytes")
    #: Capacidad total por volumen. Sin esto un piso solo puede expresarse en valor absoluto,
    #: y un absoluto no significa lo mismo en un disco de 256 GB que en uno de 2 TB.
    disk_total_bytes: dict[str, int] = Field(default_factory=dict, alias="diskTotalBytes")
    disk_read_bytes_per_second: float = Field(alias="diskReadBytesPerSecond", ge=0)
    disk_write_bytes_per_second: float = Field(alias="diskWriteBytesPerSecond", ge=0)
    gpu_utilization_percent: float | None = Field(default=None, alias="gpuUtilizationPercent", ge=0, le=100)
    gpu_memory_used_bytes: int | None = Field(default=None, alias="gpuMemoryUsedBytes", ge=0)
    gpu_memory_free_bytes: int | None = Field(default=None, alias="gpuMemoryFreeBytes", ge=0)
    active_aido_process_count: int = Field(alias="activeAidoProcessCount", ge=0)
    active_workloads: list[str] = Field(default_factory=list, alias="activeWorkloads")
    unreal_editor_running: bool = Field(alias="unrealEditorRunning")
    docker_running: bool = Field(alias="dockerRunning")
    wsl_running: bool = Field(alias="wslRunning")
    ollama_running: bool = Field(alias="ollamaRunning")

    @classmethod
    def test_snapshot(cls, **overrides: Any) -> ResourceSnapshot:
        """Construye una fotografía determinista y válida para pruebas de política."""
        disk_free = overrides.pop("disk_free_bytes", {"test": 200 * 1024**3})
        disk_total = overrides.pop("disk_total_bytes", None)
        if isinstance(disk_free, int):
            disk_free = {"test": disk_free}
        values: dict[str, Any] = {
            "sampled_at": utc_now(),
            "cpu_percent_1s": 10.0,
            "cpu_percent_30s": 10.0,
            "logical_processors": 8,
            "total_memory_bytes": 64 * 1024**3,
            "available_memory_bytes": 32 * 1024**3,
            "committed_memory_bytes": None,
            "commit_limit_bytes": None,
            "swap_or_pagefile_used_bytes": 0,
            "disk_free_bytes": disk_free,
            # Sin capacidad declarada el piso cae al absoluto, que es como se comportaba antes
            # de que existiera el componente porcentual. Derivarla de lo libre haria que el
            # porcentaje quedara siempre satisfecho y silenciaria los tests que fuerzan rechazo.
            "disk_total_bytes": disk_total if disk_total is not None else {},
            "disk_read_bytes_per_second": 0.0,
            "disk_write_bytes_per_second": 0.0,
            "gpu_utilization_percent": None,
            "gpu_memory_used_bytes": None,
            "gpu_memory_free_bytes": None,
            "active_aido_process_count": 0,
            "active_workloads": [],
            "unreal_editor_running": False,
            "docker_running": False,
            "wsl_running": False,
            "ollama_running": False,
        }
        values.update(overrides)
        return cls.model_validate(values)


class WorkloadProfile(_AliasedModel):
    """Presupuesto y semántica de convivencia de una clase de workload."""

    workload_class: WorkloadClass = Field(alias="workloadClass")
    heavy: bool
    light: bool
    essential: bool
    exclusive: bool = False
    cpu_limit_percent: float = Field(alias="cpuLimitPercent", ge=1, le=100)
    memory_limit_bytes: int = Field(alias="memoryLimitBytes", ge=0)
    process_limit: int = Field(alias="processLimit", ge=1)
    gpu_required: bool = Field(alias="gpuRequired")


class ResourceAdmissionRequest(_AliasedModel):
    """Solicitud durable de capacidad antes de iniciar una ejecución."""

    execution_id: str = Field(alias="executionId", min_length=1)
    workload_class: WorkloadClass = Field(alias="workloadClass")
    owner_id: str = Field(alias="ownerId", min_length=1)
    job_id: str | None = Field(default=None, alias="jobId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    lease_seconds: int = Field(default=300, alias="leaseSeconds", ge=1, le=86_400)


class ResourceLease(_AliasedModel):
    """Reserva durable renovable para una ejecución admitida."""

    id: str
    execution_id: str = Field(alias="executionId")
    workload_class: WorkloadClass = Field(alias="workloadClass")
    owner_id: str = Field(alias="ownerId")
    cpu_limit_percent: float = Field(alias="cpuLimitPercent")
    memory_limit_bytes: int = Field(alias="memoryLimitBytes")
    process_limit: int = Field(alias="processLimit")
    gpu_required: bool = Field(alias="gpuRequired")
    acquired_at: str = Field(alias="acquiredAt")
    heartbeat_at: str = Field(alias="heartbeatAt")
    expires_at: str = Field(alias="expiresAt")
    released_at: str | None = Field(default=None, alias="releasedAt")
    release_reason: str = Field(default="", alias="releaseReason")


class ResourceAdmissionDecision(_AliasedModel):
    """Resultado explícito de admisión, incluido el motivo técnico de espera."""

    status: Literal["admitted", "resource_wait"]
    reason_code: str = Field(alias="reasonCode")
    reason: str
    lease: ResourceLease | None = None
    snapshot: ResourceSnapshot


class ResourceUsageSample(_AliasedModel):
    """Muestra durable y retenida de capacidad del host."""

    id: str
    sampled_at: str = Field(alias="sampledAt")
    snapshot: ResourceSnapshot


class ResourceViolation(_AliasedModel):
    """Violación operacional que solicita mitigación sobre una ejecución administrada."""

    id: str
    execution_id: str = Field(alias="executionId")
    lease_id: str | None = Field(default=None, alias="leaseId")
    violation_type: str = Field(alias="violationType")
    action: str
    reason: str
    created_at: str = Field(alias="createdAt")
    resolved_at: str | None = Field(default=None, alias="resolvedAt")


class ResourceStatusResponse(_AliasedModel):
    """Resumen operacional consumido por el dashboard sin ejecutar una sonda nueva."""

    latest_sample: ResourceUsageSample | None = Field(default=None, alias="latestSample")
    active_leases: list[ResourceLease] = Field(default_factory=list, alias="activeLeases")
    resource_wait_count: int = Field(alias="resourceWaitCount", ge=0)


class ResourceSampleResponse(_AliasedModel):
    """Respuesta tipada de una sonda explícita del host."""

    snapshot: ResourceSnapshot


class ResourceLeaseResponse(_AliasedModel):
    """Respuesta tipada de una mutación sobre una lease."""

    lease: ResourceLease
