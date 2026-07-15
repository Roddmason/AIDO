"""Expone los endpoints HTTP del product loop sobre FastAPI: una lectura agregada y dos mutaciones.

La lectura agrega, por proyecto y en una sola llamada para el Workbench, el estado durable del loop y su
bitácora (slice product_loop), las preguntas de clarificación, el brief vivo, los supuestos y las
decisiones (slice product_discovery) y el backlog —épicas, historias, tareas e iteraciones— (slice
backlog). Las mutaciones arrancan un loop y lo avanzan por su FSM durable vía el ``ProductLoopCoordinator``;
exigen el token de escritura y validan la transición contra el mapa permitido. No contiene lógica de
negocio: delega en los repositorios y el coordinador.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.agents.product_owner_agent import persist_product_owner_backlog
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.backlog.story_spec import build_story_spec, render_story_spec_prompt
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.time import utc_now

from .coordinator import (
    ProductLoopCoordinator,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
    allowed_next_states,
)
from .models import (
    ProductLoopAidoDecideRequest,
    ProductLoopApprovalRequest,
    ProductLoopFeedbackApplyResponse,
    ProductLoopFeedbackRequest,
    ProductLoopResumeResponse,
    ProductLoopStartRequest,
    ProductLoopStateResponse,
    ProductLoopTransitionRequest,
    StorySpecResponse,
)
from .repository import ProductLoopRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router del product loop: una lectura agregada y dos mutaciones protegidas por token."""
    router = APIRouter()

    def coordinator() -> ProductLoopCoordinator:
        return ProductLoopCoordinator(platform.connection)

    def discovery_repository() -> ProductDiscoveryRepository:
        return ProductDiscoveryRepository(platform.connection)

    def backlog_repository() -> BacklogRepository:
        return BacklogRepository(platform.connection)

    def product_loop_state(project_id: str) -> dict[str, Any]:
        loops_repo = ProductLoopRepository(platform.connection)
        discovery = discovery_repository()
        backlog = backlog_repository()
        loops = loops_repo.list_loops(project_id)
        briefs = discovery.list_product_briefs(project_id=project_id)
        tasks = sorted(
            backlog.list_agent_tasks(project_id=project_id),
            key=lambda item: str(item.get("createdAt") or ""),
        )
        stories = sorted(
            backlog.list_user_stories(project_id=project_id),
            key=lambda item: str(item.get("createdAt") or ""),
        )
        acceptance_criteria = backlog.list_acceptance_criteria(project_id=project_id)
        return {
            "loops": loops,
            "transitions": loops_repo.list_transitions(loops[0]["id"]) if loops else [],
            "feedback": loops_repo.list_feedback(project_id=project_id),
            "questions": discovery.list_clarification_questions(project_id=project_id),
            "brief": briefs[0] if briefs else None,
            "assumptions": discovery.list_assumptions(project_id=project_id),
            "decisions": discovery.list_product_decisions(project_id=project_id),
            "epics": backlog.list_epics(project_id),
            "stories": stories,
            "acceptanceCriteria": acceptance_criteria,
            "storyDependencies": backlog.list_story_dependencies(project_id=project_id),
            "tasks": tasks,
            "taskDependencies": backlog.list_task_dependencies(project_id=project_id),
            "assignments": backlog.list_agent_assignments(project_id=project_id),
            "assignmentHandoffs": backlog.list_assignment_handoffs(project_id=project_id),
            "assignmentReviews": backlog.list_assignment_reviews(project_id=project_id),
            "assignmentConflicts": backlog.list_assignment_conflicts(project_id=project_id),
            "iterations": backlog.list_iterations(project_id),
        }

    def require_project_loop(project_id: str, loop_id: str) -> dict[str, Any]:
        try:
            loop = coordinator().get(loop_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if loop["projectId"] != project_id:
            raise HTTPException(status_code=404, detail=f"Product loop not found in project: {loop_id}")
        return loop

    def selected_question_answer(question: dict[str, Any]) -> str:
        metadata = question.get("metadata") or {}
        options = [str(option).strip() for option in metadata.get("options", []) if str(option).strip()]
        for key in ("defaultDecision", "recommendation"):
            value = str(metadata.get(key) or "").strip()
            if value and (not options or value in options):
                return value
        return options[0] if options else ""

    @router.get(
        "/api/v1/projects/{project_id}/product-loop",
        response_model=ProductLoopStateResponse,
    )
    async def get_product_loop_state(project_id: str) -> dict[str, Any]:
        return product_loop_state(project_id)

    @router.get(
        "/api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec",
        response_model=StorySpecResponse,
    )
    async def get_story_spec(project_id: str, story_id: str) -> dict[str, Any]:
        """Devuelve el spec ejecutable de una historia: épica, HU, criterios, roles y texto de prompt."""
        backlog = backlog_repository()
        try:
            story = backlog.get_user_story(story_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if story["projectId"] != project_id:
            raise HTTPException(status_code=404, detail=f"User story not found in project: {story_id}")
        spec = build_story_spec(backlog, story_id)
        return {**spec, "promptText": render_story_spec_prompt([spec])}

    @router.post(
        "/api/v1/projects/{project_id}/product-loop",
        status_code=201,
        response_model=ProductLoopResumeResponse,
    )
    async def start_product_loop(
        project_id: str, body: ProductLoopStartRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        engine = coordinator()
        loop = engine.start(
            project_id=project_id,
            title=body.title,
            initiative_id=body.initiative_id,
            context=body.context,
            correlation_id=body.correlation_id,
            budget=body.budget,
            timeouts=body.timeouts,
            max_rework_rounds=body.max_rework_rounds,
            deadline=body.deadline,
        )
        return engine.resume(loop["id"])

    @router.post(
        "/api/v1/projects/{project_id}/product-loop/{loop_id}/transition",
        response_model=ProductLoopResumeResponse,
    )
    async def transition_product_loop(
        project_id: str, loop_id: str, body: ProductLoopTransitionRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        engine = coordinator()
        require_project_loop(project_id, loop_id)
        try:
            engine.transition(
                loop_id,
                to_state=body.to_state,
                reason=body.reason or "",
                trigger=body.trigger or "",
                correlation_id=body.correlation_id,
                expected_version=body.expected_version,
            )
        except (ProductLoopTransitionError, ProductLoopStopConditionError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return engine.resume(loop_id)

    @router.post(
        "/api/v1/projects/{project_id}/product-loop/{loop_id}/aido-decide",
        response_model=ProductLoopStateResponse,
    )
    async def aido_decide_product_loop(
        project_id: str, loop_id: str, body: ProductLoopAidoDecideRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        loop = require_project_loop(project_id, loop_id)
        discovery = discovery_repository()
        briefs = discovery.list_product_briefs(project_id=project_id, initiative_id=loop.get("initiativeId"))
        questions = [
            question
            for question in discovery.list_clarification_questions(
                project_id=project_id,
                initiative_id=loop.get("initiativeId"),
            )
            if question["status"] == "open"
        ]
        actor = body.actor or "aido_decide"
        with immediate_transaction(platform.connection):
            for question in questions:
                answer = selected_question_answer(question)
                if not answer:
                    continue
                metadata = question.get("metadata") or {}
                discovery.create_clarification_answer(
                    {
                        "projectId": project_id,
                        "questionId": question["id"],
                        "initiativeId": question.get("initiativeId") or loop.get("initiativeId"),
                        "answer": answer,
                        "status": "accepted",
                        "answeredBy": actor,
                        "metadata": {
                            "source": "aido_decide",
                            "reason": body.reason or "",
                            "options": metadata.get("options", []),
                        },
                    }
                )
                discovery.update_clarification_question(
                    question["id"],
                    {
                        "status": "answered",
                        "metadata": {
                            **metadata,
                            "aidoDecision": answer,
                            "aidoDecidedAt": utc_now(),
                            "aidoDecidedBy": actor,
                            "aidoReason": body.reason or "",
                        },
                    },
                )
                discovery.create_product_decision(
                    {
                        "projectId": project_id,
                        "initiativeId": question.get("initiativeId") or loop.get("initiativeId"),
                        "briefId": briefs[0]["id"] if briefs else None,
                        "title": f"AIDO decide: {question['question'][:120]}",
                        "status": "accepted",
                        "context": question["question"],
                        "decision": answer,
                        "rationale": metadata.get("whyItMatters", ""),
                        "consequences": [],
                        "linkedQuestionIds": [question["id"]],
                        "decidedBy": actor,
                        "decidedAt": utc_now(),
                        "metadata": {
                            "source": "aido_decide",
                            "sourceQuestionId": question["id"],
                            "automatic": True,
                            "options": metadata.get("options", []),
                            "confidence": metadata.get("confidence"),
                            "reason": body.reason or "",
                        },
                    }
                )
        return product_loop_state(project_id)

    @router.post(
        "/api/v1/projects/{project_id}/product-loop/brief/{brief_id}/approve",
        response_model=ProductLoopStateResponse,
    )
    async def approve_product_brief(
        project_id: str, brief_id: str, body: ProductLoopApprovalRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        discovery = discovery_repository()
        backlog = backlog_repository()
        try:
            brief = discovery.get_product_brief(brief_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if brief["projectId"] != project_id:
            raise HTTPException(status_code=404, detail=f"Product brief not found in project: {brief_id}")
        outputs = discovery.list_product_owner_outputs(brief_id=brief_id)
        if not outputs:
            raise HTTPException(
                status_code=422,
                detail="Product brief has no validated ProductOwnerAgent output to approve.",
            )
        output = outputs[0]
        if not output["epics"] or not output["userStories"]:
            raise HTTPException(
                status_code=422,
                detail="ProductOwnerAgent output has no backlog payload to materialize.",
            )
        actor = body.actor or "operator"
        with immediate_transaction(platform.connection):
            approved_brief = discovery.upsert_product_brief(
                {
                    "briefId": brief_id,
                    "projectId": project_id,
                    "initiativeId": brief["initiativeId"],
                    "title": brief["title"],
                    "status": "approved",
                    "summary": brief["summary"],
                    "problemStatement": brief["problemStatement"],
                    "goals": brief["goals"],
                    "targetUsers": brief["targetUsers"],
                    "successMetrics": brief["successMetrics"],
                    "scope": brief["scope"],
                    "outOfScope": brief["outOfScope"],
                    "changeSummary": body.reason or "Product brief approved.",
                    "authoredBy": actor,
                }
            )
            persist_product_owner_backlog(
                backlog,
                project_id=project_id,
                output=output,
                product_owner_output_id=output["id"],
            )
            discovery.create_product_decision(
                {
                    "projectId": project_id,
                    "initiativeId": brief["initiativeId"],
                    "briefId": approved_brief["id"],
                    "title": "Approve product brief",
                    "status": "accepted",
                    "context": "Operator approved the ProductOwnerAgent product brief.",
                    "decision": "Approved",
                    "rationale": body.reason or "Brief accepted for backlog generation.",
                    "consequences": ["Backlog materialized from the approved ProductOwnerAgent output."],
                    "decidedBy": actor,
                    "decidedAt": utc_now(),
                    "metadata": {"source": "brief_approval", "productOwnerOutputId": output["id"]},
                }
            )
        engine = coordinator()
        for loop in ProductLoopRepository(platform.connection).list_loops(project_id):
            if "backlog_ready" not in allowed_next_states(loop["state"]):
                continue
            try:
                engine.transition(
                    loop["id"],
                    to_state="backlog_ready",
                    reason=body.reason or "Product brief approved and backlog generated.",
                    trigger="brief_approved",
                    expected_version=loop["version"],
                )
            except (ProductLoopTransitionError, ProductLoopStopConditionError):
                continue
            break
        return product_loop_state(project_id)

    @router.post(
        "/api/v1/projects/{project_id}/product-loop/{loop_id}/backlog/approve",
        response_model=ProductLoopStateResponse,
    )
    async def approve_product_loop_backlog(
        project_id: str, loop_id: str, body: ProductLoopApprovalRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        loop = require_project_loop(project_id, loop_id)
        if not backlog_repository().list_user_stories(project_id=project_id):
            raise HTTPException(status_code=422, detail="Cannot approve an empty product backlog.")
        actor = body.actor or "operator"
        with immediate_transaction(platform.connection):
            ProductLoopRepository(platform.connection).update_loop_context(
                loop_id,
                context={
                    **(loop.get("context") or {}),
                    "backlogApproval": {
                        "approved": True,
                        "approvedBy": actor,
                        "approvedAt": utc_now(),
                        "reason": body.reason or "",
                    },
                },
            )
            briefs = discovery_repository().list_product_briefs(project_id=project_id)
            discovery_repository().create_product_decision(
                {
                    "projectId": project_id,
                    "initiativeId": loop.get("initiativeId") or (briefs[0]["initiativeId"] if briefs else None),
                    "briefId": briefs[0]["id"] if briefs else None,
                    "title": "Approve product backlog",
                    "status": "accepted",
                    "context": "Operator approved the generated product backlog.",
                    "decision": "Approved",
                    "rationale": body.reason or "Backlog accepted for iteration planning.",
                    "consequences": ["Product loop can continue to iteration planning."],
                    "decidedBy": actor,
                    "decidedAt": utc_now(),
                    "metadata": {"source": "backlog_approval"},
                }
            )
        if "iteration_planning" in allowed_next_states(loop["state"]):
            with suppress(ProductLoopTransitionError, ProductLoopStopConditionError):
                coordinator().transition(
                    loop_id,
                    to_state="iteration_planning",
                    reason=body.reason or "Product backlog approved.",
                    trigger="backlog_approved",
                    expected_version=loop["version"],
                )
        return product_loop_state(project_id)

    @router.post(
        "/api/v1/projects/{project_id}/product-loop/{loop_id}/feedback",
        response_model=ProductLoopFeedbackApplyResponse,
    )
    async def apply_product_loop_feedback(
        project_id: str, loop_id: str, body: ProductLoopFeedbackRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        engine = coordinator()
        try:
            loop = engine.get(loop_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if loop["projectId"] != project_id:
            raise HTTPException(status_code=404, detail=f"Product loop not found in project: {loop_id}")
        try:
            return engine.apply_feedback(
                loop_id,
                action=body.action,
                feedback=body.feedback,
                actor=body.actor or "operator",
                target_type=body.target_type,
                target_id=body.target_id,
                payload=body.payload,
                correlation_id=body.correlation_id,
                expected_version=body.expected_version,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ProductLoopTransitionError, ProductLoopStopConditionError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
