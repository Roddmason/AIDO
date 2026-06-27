"""FastAPI router for AIDO self-improvement proposals, lessons and performance records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .coordinator import SelfImprovementCoordinator
from .models import (
    SelfImprovementLessonRequest,
    SelfImprovementLessonResponse,
    SelfImprovementPerformanceRequest,
    SelfImprovementPerformanceResponse,
    SelfImprovementPromoteLessonRequest,
    SelfImprovementProposalRequest,
    SelfImprovementProposalResponse,
    SelfImprovementStateResponse,
)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build the self-improvement router bound to the platform connection and root."""
    router = APIRouter()

    def coordinator() -> SelfImprovementCoordinator:
        return SelfImprovementCoordinator(platform.connection, root=platform.cwd)

    @router.get("/api/v1/self-improvement", response_model=SelfImprovementStateResponse)
    async def get_self_improvement_state(source_project_id: str | None = None) -> dict[str, Any]:
        return coordinator().state(source_project_id=source_project_id)

    @router.post(
        "/api/v1/self-improvement/proposals",
        status_code=201,
        response_model=SelfImprovementProposalResponse,
    )
    async def create_self_improvement_proposal(
        body: SelfImprovementProposalRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return coordinator().propose_change(
                source_project_id=body.source_project_id,
                title=body.title,
                summary=body.summary,
                proposed_by=body.proposed_by,
                qa_commands=body.qa_commands,
                target_paths=body.target_paths,
                metadata=body.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/self-improvement/lessons",
        status_code=201,
        response_model=SelfImprovementLessonResponse,
    )
    async def record_self_improvement_lesson(
        body: SelfImprovementLessonRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return coordinator().record_lesson(
                source_project_id=body.source_project_id,
                scope=body.scope,
                title=body.title,
                lesson=body.lesson,
                evidence_package_ids=body.evidence_package_ids,
                proposal_id=body.proposal_id,
                proposed_by=body.proposed_by,
                metadata=body.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/self-improvement/lessons/{lesson_id}/promote",
        status_code=202,
        response_model=SelfImprovementLessonResponse,
    )
    async def promote_self_improvement_lesson(
        lesson_id: str, body: SelfImprovementPromoteLessonRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return coordinator().promote_global_lesson(
                lesson_id,
                actor=body.actor,
                reason=body.reason,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/api/v1/self-improvement/performance-records",
        status_code=201,
        response_model=SelfImprovementPerformanceResponse,
    )
    async def record_self_improvement_performance(
        body: SelfImprovementPerformanceRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            return coordinator().record_performance(
                source_project_id=body.source_project_id,
                metric_name=body.metric_name,
                value=body.value,
                unit=body.unit,
                evidence_package_id=body.evidence_package_id,
                proposal_id=body.proposal_id,
                baseline_value=body.baseline_value,
                target_value=body.target_value,
                recorded_by=body.recorded_by,
                metadata=body.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
