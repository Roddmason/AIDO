"""Read-only NVIDIA NIM local-runtime API.

@author Rodrigo Mason
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from local_control_center.shared.time import utc_now

from .contracts import NvidiaNimPreflightResponse, NvidiaNimSystemFacts
from .preflight import evaluate_profile
from .requirements import get_requirement, list_requirements
from .system_probe import collect_system_facts


def create_router() -> APIRouter:
    """Build the read-only NVIDIA NIM local preflight router."""
    router = APIRouter(prefix="/api/v1/nvidia-nim", tags=["nvidia-nim"])

    @router.get("/preflight", response_model=NvidiaNimPreflightResponse)
    def local_preflight(profileId: str | None = None) -> NvidiaNimPreflightResponse:
        """Collect sanitized host facts and evaluate catalogued profiles without mutation."""
        if profileId is None:
            requirements = list_requirements()
        else:
            requirement = get_requirement(profileId)
            if requirement is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"NVIDIA NIM profile not found: {profileId}",
                )
            requirements = [requirement]
        raw_facts = collect_system_facts()
        facts = (
            raw_facts
            if isinstance(raw_facts, NvidiaNimSystemFacts)
            else NvidiaNimSystemFacts.model_validate(raw_facts)
        )
        profiles = [evaluate_profile(facts=facts, requirement=item) for item in requirements]
        return NvidiaNimPreflightResponse(
            status="ready" if profiles and all(item.status == "ready" for item in profiles) else "blocked",
            observedAt=utc_now(),
            probeScope="aido_host_process",
            mutationAttempted=False,
            facts=facts,
            profiles=profiles,
        )

    return router
