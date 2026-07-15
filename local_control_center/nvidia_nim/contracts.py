"""Typed contracts for read-only NVIDIA NIM local preflight.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _AliasedModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class NvidiaNimPlatformFacts(_AliasedModel):
    """Observed operating-system and WSL target facts."""

    os: str
    target: Literal["linux", "wsl2", "windows", "unknown"]
    architecture: str
    wsl_available: bool = Field(alias="wslAvailable")
    wsl_version: int | None = Field(default=None, alias="wslVersion")
    distributions: list[str] = Field(default_factory=list)


class NvidiaNimGpuFacts(_AliasedModel):
    """One sanitized NVIDIA GPU observation from nvidia-smi."""

    index: int
    name: str
    memory_total_gib: float = Field(alias="memoryTotalGiB")
    driver_version: str | None = Field(default=None, alias="driverVersion")
    compute_capability: str | None = Field(default=None, alias="computeCapability")


class NvidiaNimContainerRuntimeFacts(_AliasedModel):
    """Read-only Docker or Podman client/server observation."""

    id: Literal["docker", "podman"]
    installed: bool
    server_reachable: bool = Field(alias="serverReachable")
    version: str | None = None


class NvidiaNimContainerToolkitFacts(_AliasedModel):
    """NVIDIA Container Toolkit and CDI detection state."""

    installed: bool
    version: str | None = None
    cdi_available: bool = Field(alias="cdiAvailable")
    detection_scope: Literal["aido_host_process"] = Field(alias="detectionScope")


class NvidiaNimSystemFacts(_AliasedModel):
    """Sanitized facts collected by the bounded host probe."""

    platform: NvidiaNimPlatformFacts
    gpus: list[NvidiaNimGpuFacts]
    system_memory_gib: float | None = Field(default=None, alias="systemMemoryGiB")
    swap_gib: float | None = Field(default=None, alias="swapGiB")
    disk_free_gib: float | None = Field(default=None, alias="diskFreeGiB")
    resource_scope: Literal["host", "runtime_target"] = Field(alias="resourceScope")
    container_runtimes: list[NvidiaNimContainerRuntimeFacts] = Field(alias="containerRuntimes")
    container_toolkit: NvidiaNimContainerToolkitFacts = Field(alias="containerToolkit")


class NvidiaNimRequirement(_AliasedModel):
    """Versioned model-profile requirements with official provenance."""

    profile_id: str = Field(alias="profileId")
    model_id: str = Field(alias="modelId")
    display_name: str = Field(alias="displayName")
    minimum_gpu_architecture: str = Field(alias="minimumGpuArchitecture")
    minimum_compute_capability: float = Field(alias="minimumComputeCapability")
    minimum_gpu_memory_gib: float = Field(alias="minimumGpuMemoryGiB")
    minimum_system_memory_gib: float = Field(alias="minimumSystemMemoryGiB")
    minimum_driver_major: int = Field(alias="minimumDriverMajor")
    supported_targets: list[Literal["linux", "wsl2"]] = Field(alias="supportedTargets")
    wsl_supported_geforce_series: list[int] = Field(alias="wslSupportedGeForceSeries")
    source_url: str = Field(alias="sourceUrl")
    platform_source_url: str = Field(alias="platformSourceUrl")
    verified_at: str = Field(alias="verifiedAt")


class NvidiaNimRemediationAction(_AliasedModel):
    """Non-mutating recovery direction associated with one blocker."""

    id: str
    kind: Literal["open_documentation", "open_configuration", "select_supported_target"]
    label: str
    description: str
    href: str | None = None


class NvidiaNimPreflightBlocker(_AliasedModel):
    """Stable, actionable incompatibility or missing-runtime reason."""

    code: str
    category: Literal["hardware", "platform", "runtime", "observation"]
    message: str
    observed: str | float | int | None = None
    required: str | float | int | None = None
    source_url: str | None = Field(default=None, alias="sourceUrl")
    actions: list[NvidiaNimRemediationAction] = Field(default_factory=list)


class NvidiaNimProfilePreflightResult(_AliasedModel):
    """Compatibility and runtime readiness for one local NIM profile."""

    profile_id: str = Field(alias="profileId")
    model_id: str = Field(alias="modelId")
    display_name: str = Field(alias="displayName")
    status: Literal["ready", "blocked_incompatible_hardware", "blocked_runtime_not_ready"]
    hardware_compatible: bool = Field(alias="hardwareCompatible")
    runtime_ready: bool = Field(alias="runtimeReady")
    observed_gpu_memory_gib: float | None = Field(default=None, alias="observedGpuMemoryGiB")
    required_gpu_memory_gib: float = Field(alias="requiredGpuMemoryGiB")
    mutation_attempted: Literal[False] = Field(alias="mutationAttempted")
    requirement: NvidiaNimRequirement
    blockers: list[NvidiaNimPreflightBlocker]


class NvidiaNimPreflightResponse(_AliasedModel):
    """Read-only preflight response for one or more local NIM profiles."""

    status: Literal["ready", "blocked"]
    observed_at: str = Field(alias="observedAt")
    probe_scope: Literal["aido_host_process"] = Field(alias="probeScope")
    mutation_attempted: Literal[False] = Field(alias="mutationAttempted")
    facts: NvidiaNimSystemFacts
    profiles: list[NvidiaNimProfilePreflightResult]
