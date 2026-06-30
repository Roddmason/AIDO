"""Endpoints HTTP de pipelines: listar y crear, publicando el evento de creación.

Expone `GET/POST /api/v1/pipelines`. La creación exige permiso de escritura, delega la persistencia
en `PipelinesRepository` y emite `pipeline.created` en el `EventBus` para que el resto del sistema
reaccione. La dependencia `platform` aporta la conexión SQLite por petición.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.product_loop.coordinator import (
    ProductLoopCoordinator,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
)
from local_control_center.product_loop.intent_classifier import IntentClassificationInput, IntentClassifier
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.sessions_chats.repository import SessionsChatsRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.workspaces_projects.repository import (
    WorkspaceConflictError,
    WorkspaceIsolationError,
    WorkspacesRepository,
)

from .models import PipelineCreateRequest, PipelineResponse, PipelinesListResponse
from .repository import PipelinesRepository

PRODUCT_OWNER_INTAKE_STAGES = [
    {"id": "intake", "status": "created", "owner": "product_owner", "source": "workbench_chat"},
    {"id": "planning", "status": "pending", "owner": "technical_lead"},
    {"id": "architecture", "status": "pending", "owner": "architect_agent"},
    {"id": "implementation", "status": "pending", "owner": "developer"},
    {"id": "validation", "status": "pending", "owner": "qa_reviewer"},
    {"id": "delivery", "status": "pending", "owner": "release_manager"},
]


def _intake_stages(stages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in (stages or PRODUCT_OWNER_INTAKE_STAGES)]


def _product_owner_stage_status(status: str) -> str:
    if status == "completed":
        return "completed"
    if status == "blocked":
        return "blocked"
    if status == "runtime_unavailable":
        return "blocked"
    return "failed"


def _pipeline_status(status: str) -> str:
    if status == "completed":
        return "running"
    if status in {"blocked", "runtime_unavailable"}:
        return "blocked"
    return "failed"


def _stages_with_product_owner_result(
    stages: list[dict[str, Any]], result: dict[str, Any]
) -> list[dict[str, Any]]:
    next_stages = [dict(item) for item in stages]
    if not next_stages:
        next_stages = _intake_stages(None)
    stage = dict(next_stages[0])
    stage.setdefault("id", "intake")
    stage["status"] = _product_owner_stage_status(str(result.get("status") or "failed"))
    stage["reason"] = str(result.get("reason") or "")
    if result.get("agentRunId"):
        stage["agentRunId"] = result["agentRunId"]
    if result.get("evidencePackageId"):
        stage["evidencePackageId"] = result["evidencePackageId"]
    next_stages[0] = stage
    return next_stages


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Construye el `APIRouter` de pipelines enlazado a la plataforma y al guard de escritura."""
    router = APIRouter()

    def repository() -> PipelinesRepository:
        return PipelinesRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    def sessions_chats() -> SessionsChatsRepository:
        return SessionsChatsRepository(platform.connection)

    def projects_repository() -> ProjectsRepository:
        return ProjectsRepository(platform.connection)

    def workspaces() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    def loop_coordinator() -> ProductLoopCoordinator:
        return ProductLoopCoordinator(platform.connection)

    def product_owner_runner() -> ProductOwnerAgentRunner:
        return ProductOwnerAgentRunner(platform.connection, root=platform.cwd)

    def project_assessment(project_id: str) -> dict[str, Any]:
        assessments = projects_repository().list_project_assessments(project_id)
        latest = dict(assessments[0]) if assessments else {"summary": {}, "findingsCount": 0}
        latest["runtimeStatus"] = RuntimeStatusService(platform.connection).runtime_provider_status()
        return latest

    def git_state(project_id: str) -> dict[str, Any]:
        try:
            return GitWorkspaceService(platform.connection, root=platform.cwd).status(project_id)
        except Exception as error:
            return {
                "status": "failed",
                "reason": str(redact_secrets(str(error))),
                "changedFiles": [],
                "untrackedFiles": [],
                "stagedFiles": [],
                "dirty": False,
            }

    def classify_intent(*, body: PipelineCreateRequest, prompt: str) -> dict[str, Any]:
        state = git_state(body.project_id)
        changed_files = list(
            dict.fromkeys(
                [
                    *[str(item) for item in state.get("changedFiles", [])],
                    *[str(item) for item in state.get("stagedFiles", [])],
                    *[str(item) for item in state.get("untrackedFiles", [])],
                ]
            )
        )
        decision = IntentClassifier().classify(
            IntentClassificationInput(
                prompt=prompt,
                project_assessment=project_assessment(body.project_id),
                changed_files=changed_files,
                git_state=state,
                user_mode=body.user_mode,
            )
        )
        return redact_secrets(decision.to_dict())

    def block_pipeline(
        pipeline: dict[str, Any],
        *,
        stages: list[dict[str, Any]],
        loop_id: str | None,
        reason: str,
        status: str = "configuration_required",
    ) -> dict[str, Any]:
        result = {
            "status": status,
            "reason": reason,
            "loopId": loop_id,
            "execution": "not_executed",
        }
        return repository().update_pipeline(
            pipeline["id"],
            status="blocked",
            stages=_stages_with_product_owner_result(stages, result),
            metadata={**pipeline["metadata"], "productOwnerIntake": redact_secrets(result)},
        )

    def transition_loop_best_effort(
        *,
        loop_id: str,
        to_state: str,
        reason: str,
        context_patch: dict[str, Any],
    ) -> None:
        try:
            loop_coordinator().transition(
                loop_id,
                to_state=to_state,
                reason=reason,
                trigger="workbench_intake",
                context_patch=context_patch,
            )
        except (ProductLoopTransitionError, ProductLoopStopConditionError, KeyError):
            return

    def run_product_owner_intake(
        *,
        pipeline: dict[str, Any],
        body: PipelineCreateRequest,
        stages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not body.chat_id:
            return block_pipeline(
                pipeline,
                stages=stages,
                loop_id=None,
                reason="productOwnerIntake requires a linked chatId.",
            )
        try:
            chat = sessions_chats().get_chat(body.chat_id)
        except KeyError:
            return block_pipeline(
                pipeline,
                stages=stages,
                loop_id=None,
                reason=f"Chat not found for productOwnerIntake: {body.chat_id}",
            )
        if chat["projectId"] != body.project_id:
            return block_pipeline(
                pipeline,
                stages=stages,
                loop_id=None,
                reason="productOwnerIntake chat does not belong to the pipeline project.",
            )

        intent_decision = classify_intent(body=body, prompt=chat["prompt"])
        pipeline = repository().update_pipeline(
            pipeline["id"],
            status=pipeline["status"],
            stages=pipeline["stages"],
            metadata={**pipeline["metadata"], "intentDecision": intent_decision},
        )
        loop = loop_coordinator().start(
            project_id=body.project_id,
            title=body.title or chat["title"] or pipeline["title"],
            context={
                "intake": {
                    "source": "workbench_chat",
                    "pipelineId": pipeline["id"],
                    "sessionId": body.session_id,
                    "chatId": body.chat_id,
                    "intentDecision": intent_decision,
                }
            },
            correlation_id=pipeline["id"],
            actor="workbench",
            reason="Product loop started from Workbench chat intake.",
        )
        if intent_decision.get("planMode") == "ask":
            transition_loop_best_effort(
                loop_id=loop["id"],
                to_state="discovering",
                reason="Intent classification requires clarification.",
                context_patch={"intentDecision": intent_decision},
            )
            transition_loop_best_effort(
                loop_id=loop["id"],
                to_state="awaiting_user",
                reason="Intent classification confidence is below the execution threshold.",
                context_patch={
                    "productOwner": {
                        "status": "awaiting_user",
                        "reason": "Intent classification confidence is below the execution threshold.",
                        "questions": list(intent_decision.get("questions") or []),
                    }
                },
            )
            return block_pipeline(
                pipeline,
                stages=stages,
                loop_id=loop["id"],
                reason="Intent classification confidence is below the execution threshold.",
                status="blocked",
            )
        transition_loop_best_effort(
            loop_id=loop["id"],
            to_state="discovering",
            reason="ProductOwnerAgent intake started.",
            context_patch={"productOwner": {"status": "running", "pipelineId": pipeline["id"]}},
        )

        task_id = f"product-owner-{pipeline['id']}"
        try:
            workspace = workspaces().allocate_workspace(
                project_id=body.project_id,
                task_id=task_id,
                agent_id=PRODUCT_OWNER_AGENT_ID,
                reason="Workbench chat intake ProductOwnerAgent workspace",
            )
        except (WorkspaceConflictError, WorkspaceIsolationError, ValueError) as error:
            reason = str(error)
            transition_loop_best_effort(
                loop_id=loop["id"],
                to_state="blocked",
                reason=reason,
                context_patch={"productOwner": {"status": "configuration_required", "reason": reason}},
            )
            return block_pipeline(
                pipeline,
                stages=stages,
                loop_id=loop["id"],
                reason=reason,
            )

        result = product_owner_runner().run(
            {
                "projectId": body.project_id,
                "workspaceId": workspace["id"],
                "taskId": task_id,
                "idea": chat["prompt"],
                "metadata": {
                    "source": "workbench_chat",
                    "pipelineId": pipeline["id"],
                    "chatId": chat["id"],
                    "sessionId": body.session_id,
                    "loopId": loop["id"],
                },
            }
        )
        metadata = {
            "status": result["status"],
            "reason": result["reason"],
            "loopId": loop["id"],
            "workspaceId": workspace["id"],
            "jobId": result["job"]["id"],
            "agentRunId": result["agentRun"]["id"],
            "evidencePackageId": result["evidencePackage"]["id"],
            "runtimeId": result["runtime"].get("id"),
        }
        final_state = {
            "completed": "backlog_ready",
            "blocked": "awaiting_user",
            "runtime_unavailable": "blocked",
            "failed_validation": "blocked",
        }.get(str(result["status"]), "blocked")
        transition_loop_best_effort(
            loop_id=loop["id"],
            to_state=final_state,
            reason=result["reason"],
            context_patch={"productOwner": metadata},
        )
        return repository().update_pipeline(
            pipeline["id"],
            status=_pipeline_status(str(result["status"])),
            stages=_stages_with_product_owner_result(stages, metadata),
            metadata={
                **pipeline["metadata"],
                "intentDecision": intent_decision,
                "productOwnerIntake": redact_secrets(metadata),
            },
        )

    @router.get("/api/v1/pipelines", response_model=PipelinesListResponse)
    async def list_pipelines() -> dict[str, Any]:
        return {"pipelines": repository().list_pipelines()}

    @router.post("/api/v1/pipelines", status_code=201, response_model=PipelineResponse)
    async def create_pipeline(body: PipelineCreateRequest, request: Request) -> PipelineResponse:
        require_write(request)
        stages = _intake_stages(body.stages) if body.product_owner_intake else body.stages
        pipeline = repository().create_pipeline(
            project_id=body.project_id,
            title=body.title or "Pipeline",
            session_id=body.session_id,
            chat_id=body.chat_id,
            stages=stages,
        )
        if body.product_owner_intake:
            pipeline = run_product_owner_intake(pipeline=pipeline, body=body, stages=stages or [])
        event_bus().record_event(
            project_id=pipeline["projectId"],
            event_type="pipeline.created",
            payload={
                "pipelineId": pipeline["id"],
                "sessionId": pipeline["sessionId"],
                "chatId": pipeline["chatId"],
                "status": pipeline["status"],
            },
        )
        return PipelineResponse(pipeline=pipeline)

    return router
