"""Versioned, source-attributed NVIDIA NIM local hardware requirements.

@author Rodrigo Mason
"""

from __future__ import annotations

from .contracts import NvidiaNimRequirement

VISUAL_SUPPORT_MATRIX_URL = "https://docs.nvidia.com/nim/visual-genai/latest/support-matrix.html"
NIM_WSL2_REQUIREMENTS_URL = "https://docs.nvidia.com/nim/wsl2/latest/getting-started.html"
REQUIREMENTS_VERIFIED_AT = "2026-07-13"


def _qwen_requirement(*, profile_id: str, model_id: str, display_name: str) -> NvidiaNimRequirement:
    return NvidiaNimRequirement(
        profileId=profile_id,
        modelId=model_id,
        displayName=display_name,
        minimumGpuArchitecture="ampere",
        minimumComputeCapability=8.0,
        minimumGpuMemoryGiB=80.0,
        minimumSystemMemoryGiB=64.0,
        minimumDriverMajor=570,
        supportedTargets=["linux", "wsl2"],
        wslSupportedGeForceSeries=[40, 50],
        sourceUrl=VISUAL_SUPPORT_MATRIX_URL,
        platformSourceUrl=NIM_WSL2_REQUIREMENTS_URL,
        verifiedAt=REQUIREMENTS_VERIFIED_AT,
    )


NVIDIA_NIM_REQUIREMENTS: tuple[NvidiaNimRequirement, ...] = (
    _qwen_requirement(
        profile_id="qwen-image",
        model_id="qwen/qwen-image",
        display_name="Qwen-Image",
    ),
    _qwen_requirement(
        profile_id="qwen-image-edit",
        model_id="qwen/qwen-image-edit",
        display_name="Qwen-Image-Edit",
    ),
)


def list_requirements() -> list[NvidiaNimRequirement]:
    """Return detached Pydantic copies so callers cannot mutate the catalog."""
    return [item.model_copy(deep=True) for item in NVIDIA_NIM_REQUIREMENTS]


def get_requirement(profile_id: str) -> NvidiaNimRequirement | None:
    """Return a detached requirement by stable profile id, if catalogued."""
    for item in NVIDIA_NIM_REQUIREMENTS:
        if item.profile_id == profile_id:
            return item.model_copy(deep=True)
    return None
