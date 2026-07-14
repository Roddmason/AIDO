"""Pure NVIDIA NIM compatibility evaluation over sanitized host observations."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .contracts import (
    NvidiaNimPreflightBlocker,
    NvidiaNimProfilePreflightResult,
    NvidiaNimRemediationAction,
    NvidiaNimRequirement,
    NvidiaNimSystemFacts,
)

CONTAINER_TOOLKIT_URL = (
    "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
)
CDI_CONFIGURATION_URL = (
    "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html"
)

HARDWARE_CATEGORIES = {"hardware", "platform", "observation"}


def _documentation_action(
    action_id: str,
    *,
    label: str,
    description: str,
    href: str,
) -> NvidiaNimRemediationAction:
    return NvidiaNimRemediationAction(
        id=action_id,
        kind="open_documentation",
        label=label,
        description=description,
        href=href,
    )


def _supported_target_action(requirement: NvidiaNimRequirement) -> NvidiaNimRemediationAction:
    return NvidiaNimRemediationAction(
        id="select_supported_target",
        kind="select_supported_target",
        label="Select an officially supported NVIDIA NIM target",
        description=(
            "Use a separate target that satisfies the current NVIDIA support matrix; "
            "this preflight does not modify the current host."
        ),
        href=requirement.source_url,
    )


def _blocker(
    *,
    code: str,
    category: str,
    message: str,
    observed: str | float | int | None,
    required: str | float | int | None,
    source_url: str | None,
    actions: Iterable[NvidiaNimRemediationAction],
) -> NvidiaNimPreflightBlocker:
    return NvidiaNimPreflightBlocker(
        code=code,
        category=category,
        message=message,
        observed=observed,
        required=required,
        sourceUrl=source_url,
        actions=list(actions),
    )


def _major_version(value: str | None) -> int | None:
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _compute_capability(value: str | None) -> float | None:
    try:
        return float(str(value or "").strip())
    except ValueError:
        return None


def _wsl_geforce_series(gpu_name: str) -> int | None:
    if "geforce" not in gpu_name.lower():
        return None
    match = re.search(r"\bRTX\s+([1-9]\d)\d{2}\b", gpu_name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _hardware_blockers(
    facts: NvidiaNimSystemFacts,
    requirement: NvidiaNimRequirement,
) -> list[NvidiaNimPreflightBlocker]:
    blockers: list[NvidiaNimPreflightBlocker] = []
    support_action = _documentation_action(
        "review_model_support_matrix",
        label="Review the NVIDIA model support matrix",
        description="Confirm the model's current GPU, memory, driver, and platform requirements.",
        href=requirement.source_url,
    )
    target_action = _supported_target_action(requirement)
    target = facts.platform.target
    if target not in requirement.supported_targets:
        blockers.append(
            _blocker(
                code="platform_unsupported",
                category="platform",
                message=f"NVIDIA documents this profile for {', '.join(requirement.supported_targets)}.",
                observed=target,
                required=", ".join(requirement.supported_targets),
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
    if target == "wsl2" and (not facts.platform.wsl_available or facts.platform.wsl_version != 2):
        blockers.append(
            _blocker(
                code="wsl2_not_verified",
                category="platform",
                message="A working WSL2 target was not verified from the AIDO host process.",
                observed=facts.platform.wsl_version,
                required=2,
                source_url=requirement.platform_source_url,
                actions=[
                    _documentation_action(
                        "review_wsl2_requirements",
                        label="Review NVIDIA NIM on WSL2 requirements",
                        description="Verify WSL2 on a separately selected deployment target.",
                        href=requirement.platform_source_url,
                    )
                ],
            )
        )
    normalized_architecture = facts.platform.architecture.lower().replace("-", "_")
    if normalized_architecture not in {"amd64", "x86_64"}:
        blockers.append(
            _blocker(
                code="cpu_architecture_unsupported",
                category="hardware",
                message="This NVIDIA NIM profile requires an x86_64 host architecture.",
                observed=facts.platform.architecture,
                required="x86_64",
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
    if not facts.gpus:
        blockers.append(
            _blocker(
                code="nvidia_gpu_not_detected",
                category="observation",
                message="No NVIDIA GPU was detected by the read-only nvidia-smi probe.",
                observed=None,
                required=requirement.minimum_gpu_architecture,
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
        return blockers

    platform_gpu_candidates = list(facts.gpus)
    if target == "wsl2":
        platform_gpu_candidates = [
            gpu
            for gpu in facts.gpus
            if _wsl_geforce_series(gpu.name) in requirement.wsl_supported_geforce_series
        ]
        if not platform_gpu_candidates:
            blockers.append(
                _blocker(
                    code="wsl_gpu_generation_unsupported",
                    category="platform",
                    message=(
                        "NVIDIA's current NIM-on-WSL2 path supports GeForce RTX 40- and "
                        "50-series GPUs; no matching GPU was detected."
                    ),
                    observed=", ".join(gpu.name for gpu in facts.gpus),
                    required="GeForce RTX 40-series or 50-series",
                    source_url=requirement.platform_source_url,
                    actions=[
                        _documentation_action(
                            "review_wsl2_requirements",
                            label="Review NVIDIA NIM on WSL2 requirements",
                            description="Verify the current NVIDIA WSL2 support matrix.",
                            href=requirement.platform_source_url,
                        ),
                        target_action,
                    ],
                )
            )
    diagnostic_gpu_candidates = platform_gpu_candidates or list(facts.gpus)
    memory_capable_gpus = [
        gpu for gpu in diagnostic_gpu_candidates if gpu.memory_total_gib >= requirement.minimum_gpu_memory_gib
    ]
    observed_memory = max(gpu.memory_total_gib for gpu in diagnostic_gpu_candidates)
    if not memory_capable_gpus:
        blockers.append(
            _blocker(
                code="insufficient_gpu_memory",
                category="hardware",
                message="Detected GPU memory is below this model profile's minimum.",
                observed=observed_memory,
                required=requirement.minimum_gpu_memory_gib,
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
    if target == "wsl2" and facts.resource_scope != "runtime_target":
        blockers.append(
            _blocker(
                code="runtime_target_memory_not_verified",
                category="observation",
                message=(
                    "Host memory was detected, but the selected WSL2 target memory limit was not verified."
                ),
                observed=facts.system_memory_gib,
                required=requirement.minimum_system_memory_gib,
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
    elif facts.system_memory_gib is None:
        blockers.append(
            _blocker(
                code="system_memory_not_detected",
                category="observation",
                message="System memory could not be verified from the AIDO host process.",
                observed=None,
                required=requirement.minimum_system_memory_gib,
                source_url=requirement.source_url,
                actions=[support_action],
            )
        )
    elif facts.system_memory_gib < requirement.minimum_system_memory_gib:
        blockers.append(
            _blocker(
                code="insufficient_system_memory",
                category="hardware",
                message="Detected system memory is below this model profile's minimum.",
                observed=facts.system_memory_gib,
                required=requirement.minimum_system_memory_gib,
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )

    compute_capable_gpus = [
        gpu
        for gpu in diagnostic_gpu_candidates
        if (_compute_capability(gpu.compute_capability) or -1) >= requirement.minimum_compute_capability
    ]
    if not compute_capable_gpus:
        observed_capabilities = [gpu.compute_capability for gpu in diagnostic_gpu_candidates]
        blockers.append(
            _blocker(
                code="gpu_architecture_not_supported",
                category="hardware",
                message=("No detected GPU proves the minimum architecture required by this model profile."),
                observed=", ".join(filter(None, observed_capabilities)) or None,
                required=requirement.minimum_compute_capability,
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
    elif memory_capable_gpus and not any(gpu in compute_capable_gpus for gpu in memory_capable_gpus):
        blockers.append(
            _blocker(
                code="no_single_gpu_meets_profile",
                category="hardware",
                message=(
                    "Memory and architecture requirements were detected only across different GPUs; "
                    "one GPU must satisfy the complete profile."
                ),
                observed=", ".join(gpu.name for gpu in diagnostic_gpu_candidates),
                required=(
                    f"one {requirement.minimum_gpu_architecture} or newer GPU with "
                    f"{requirement.minimum_gpu_memory_gib:g} GiB"
                ),
                source_url=requirement.source_url,
                actions=[support_action, target_action],
            )
        )
    driver_majors = [_major_version(gpu.driver_version) for gpu in facts.gpus]
    detected_driver_majors = [major for major in driver_majors if major is not None]
    if not detected_driver_majors:
        blockers.append(
            _blocker(
                code="driver_version_not_detected",
                category="observation",
                message="The NVIDIA driver version could not be verified.",
                observed=None,
                required=requirement.minimum_driver_major,
                source_url=requirement.source_url,
                actions=[support_action],
            )
        )
    elif max(detected_driver_majors) < requirement.minimum_driver_major:
        blockers.append(
            _blocker(
                code="driver_version_unsupported",
                category="hardware",
                message="The detected NVIDIA driver is older than the profile minimum.",
                observed=max(detected_driver_majors),
                required=requirement.minimum_driver_major,
                source_url=requirement.source_url,
                actions=[support_action],
            )
        )
    return blockers


def _runtime_blockers(facts: NvidiaNimSystemFacts) -> list[NvidiaNimPreflightBlocker]:
    blockers: list[NvidiaNimPreflightBlocker] = []
    if not any(runtime.server_reachable for runtime in facts.container_runtimes):
        blockers.append(
            _blocker(
                code="container_runtime_unavailable",
                category="runtime",
                message="No detected Docker or Podman server is reachable from AIDO.",
                observed=", ".join(
                    f"{runtime.id}:{'installed' if runtime.installed else 'missing'}"
                    for runtime in facts.container_runtimes
                ),
                required="one reachable Docker or Podman server",
                source_url=None,
                actions=[
                    NvidiaNimRemediationAction(
                        id="configure_container_runtime",
                        kind="open_configuration",
                        label="Configure an isolated container runtime",
                        description=(
                            "Select and configure the intended runtime target; AIDO will not start or "
                            "modify one during preflight."
                        ),
                    )
                ],
            )
        )
    if not facts.container_toolkit.installed:
        blockers.append(
            _blocker(
                code="container_toolkit_not_detected",
                category="runtime",
                message="NVIDIA Container Toolkit was not detected from the AIDO host process.",
                observed="not detected",
                required="NVIDIA Container Toolkit on the selected runtime target",
                source_url=CONTAINER_TOOLKIT_URL,
                actions=[
                    _documentation_action(
                        "review_container_toolkit_setup",
                        label="Review NVIDIA Container Toolkit setup",
                        description=(
                            "Configure the toolkit on a separately selected target; this action does not install it."
                        ),
                        href=CONTAINER_TOOLKIT_URL,
                    )
                ],
            )
        )
    if not facts.container_toolkit.cdi_available:
        blockers.append(
            _blocker(
                code="cdi_not_detected",
                category="runtime",
                message="The NVIDIA CDI device declaration was not detected.",
                observed="not detected",
                required="nvidia.com/gpu CDI device available to the selected runtime",
                source_url=CDI_CONFIGURATION_URL,
                actions=[
                    _documentation_action(
                        "review_cdi_setup",
                        label="Review NVIDIA CDI support",
                        description="Configure CDI explicitly on the selected runtime target.",
                        href=CDI_CONFIGURATION_URL,
                    )
                ],
            )
        )
    return blockers


def evaluate_profile(
    *,
    facts: NvidiaNimSystemFacts,
    requirement: NvidiaNimRequirement,
) -> NvidiaNimProfilePreflightResult:
    """Evaluate one sourced profile against sanitized facts without side effects."""
    hardware_blockers = _hardware_blockers(facts, requirement)
    runtime_blockers = _runtime_blockers(facts)
    blockers = [*hardware_blockers, *runtime_blockers]
    hardware_compatible = not any(blocker.category in HARDWARE_CATEGORIES for blocker in hardware_blockers)
    runtime_ready = not runtime_blockers
    if not hardware_compatible:
        status = "blocked_incompatible_hardware"
    elif not runtime_ready:
        status = "blocked_runtime_not_ready"
    else:
        status = "ready"
    observed_memory = max((gpu.memory_total_gib for gpu in facts.gpus), default=None)
    return NvidiaNimProfilePreflightResult(
        profileId=requirement.profile_id,
        modelId=requirement.model_id,
        displayName=requirement.display_name,
        status=status,
        hardwareCompatible=hardware_compatible,
        runtimeReady=runtime_ready,
        observedGpuMemoryGiB=observed_memory,
        requiredGpuMemoryGiB=requirement.minimum_gpu_memory_gib,
        mutationAttempted=False,
        requirement=requirement,
        blockers=blockers,
    )
