"""FastAPI router for workflows: lifecycle, issue-to-patch/PR runs and governed gate advances.

Exposes the workflow endpoints (list/create/start, pause/resume/cancel, detail) plus the
issue-to-patch and issue-to-pr transitions (run, approve, promote, create PR) and the
governed gate-advance endpoint that enforces pr_review/release_gate/retro before a step may
proceed. Request bodies are validated here (raising HTTP 422) and persistence/orchestration is
delegated to the repositories and runner state machines; all write routes pass ``require_write``.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agents.model_router import ModelRouter, RoutingRequest
from ..agents.product_owner_agent import ProductOwnerAgentRunner
from ..agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
from ..agents.repository import AgentsRepository
from ..agents.routing_profiles import RoutingProfileStore
from ..evidence.quality import qa_passed_without_failed_results
from ..evidence.repository import EvidenceRepository
from ..governance.repository import GovernanceRepository
from ..governance.signals import record_governance_risk
from ..jobs_approvals.repository import JobsRepository
from ..product_loop.coordinator import (
    ProductLoopCoordinator,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
)
from ..security_policy.repository import SecurityPolicyRepository
from ..shared.event_bus import EventBus
from ..shared.redaction import redact_secrets
from ..shared.time import utc_now
from ..workspaces_projects.repository import (
    WorkspaceConflictError,
    WorkspaceIsolationError,
    WorkspacesRepository,
)
from .issue_to_patch_runner import IssueToPatchRunner
from .issue_to_pr_runner import IssueToPrRunner
from .models import (
    IssueToPatchRequest,
    IssueToPatchResponse,
    IssueToPrRequest,
    IssueToPrResponse,
    PromotePatchToBranchRequest,
    PullRequestCreateRequest,
    WorkflowCreateRequest,
    WorkflowDetailResponse,
    WorkflowGateAdvanceRequest,
    WorkflowGateAdvanceResponse,
    WorkflowResponse,
    WorkflowsListResponse,
    WorkflowStartResponse,
    WorkflowStatusChangeRequest,
)
from .repository import WorkflowsRepository, validate_workflow_metadata

ALLOWED_WORKFLOW_KINDS = {
    "idea_to_pr",
    "project_discovery",
    "issue_to_patch",
    "issue_to_pr",
    "qa_validation",
    "release_candidate",
    "pr_release_retro",
}


CODE_EDIT_STEPS = {"implementation", "pr_creation"}
TOOL_STEPS = {
    "workspace_create",
    "implementation",
    "local_tests",
    "qa_validation",
    "technical_review",
    "pr_creation",
    "pr_review",
    "release_gate",
}
SEARCH_STEPS = {"project_discovery", "backlog_generation"}
REASONING_STEPS = {
    "architecture_review",
    "technical_review",
    "release_candidate",
    "pr_review",
    "release_gate",
    "retro",
}


def _product_owner_intake_step_status(status: str) -> str:
    if status in {"completed", "blocked"}:
        return "completed"
    if status == "runtime_unavailable":
        return "blocked"
    return "failed"


def _product_owner_backlog_step_status(status: str) -> str:
    if status == "completed":
        return "completed"
    if status in {"blocked", "runtime_unavailable"}:
        return "blocked"
    return "failed"


def _workflow_status_from_product_owner(status: str) -> str:
    if status == "completed":
        return "running"
    if status == "runtime_unavailable":
        return "runtime_unavailable"
    if status == "blocked":
        return "blocked"
    return "failed"


def _manual_override(step: dict[str, Any]) -> dict[str, str]:
    override = step.get("manualModelOverride")
    if not isinstance(override, str) or not override.strip():
        return {}
    parts = [part.strip() for part in override.split("/") if part.strip()]
    if len(parts) == 1:
        return {"manualModel": parts[0]}
    if len(parts) == 2:
        return {"manualProvider": parts[0], "manualModel": parts[1]}
    return {"manualProvider": parts[0], "manualModel": parts[1], "manualRuntime": parts[2]}


def validate_workflow_create_body(body: WorkflowCreateRequest) -> dict[str, Any]:
    """Validate and normalize a create request into repository kwargs.

    Checks the kind against the allowed set, requires a title (falling back to ``idea``) of at
    most 180 chars, and runs ``validate_workflow_metadata`` on the metadata.

    Raises:
        HTTPException: 422 on any invalid field.
    """
    payload = body.model_dump(by_alias=True)
    kind = str(payload.get("kind") or "idea_to_pr").strip().lower()
    if kind not in ALLOWED_WORKFLOW_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of: {', '.join(sorted(ALLOWED_WORKFLOW_KINDS))}.",
        )
    title = str(payload.get("title") or payload.get("idea") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Workflow title is required.")
    if len(title) > 180:
        raise HTTPException(status_code=422, detail="Workflow title must be 180 characters or fewer.")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata must be an object.")
    try:
        validate_workflow_metadata(metadata)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"project_id": payload["projectId"], "kind": kind, "title": title, "metadata": metadata}


def validate_issue_to_patch_body(body: IssueToPatchRequest) -> dict[str, Any]:
    """Validate an issue-to-patch request and return its aliased payload.

    Requires a title (<=180 chars) and ``issueText``, and checks each ``qaCommands`` entry is a
    non-empty structured argv list (no shell strings).

    Raises:
        HTTPException: 422 on any invalid field.
    """
    payload = body.model_dump(by_alias=True)
    title = str(payload.get("title") or "").strip()
    issue_text = str(payload.get("issueText") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Issue title is required.")
    if len(title) > 180:
        raise HTTPException(status_code=422, detail="Issue title must be 180 characters or fewer.")
    if not issue_text:
        raise HTTPException(status_code=422, detail="issueText is required.")
    qa_commands = payload.get("qaCommands") or []
    for index, argv in enumerate(qa_commands):
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(
                status_code=422, detail=f"qaCommands[{index}] must be a non-empty structured argv list."
            )
    return payload


def validate_issue_to_pr_body(body: IssueToPrRequest) -> dict[str, Any]:
    """Validate an issue-to-pr request, reusing the issue-to-patch field checks."""
    return validate_issue_to_patch_body(body)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build the workflows ``APIRouter`` bound to a platform connection and write guard.

    ``platform`` supplies the shared SQLite connection and working dir for the repositories and
    runners; ``require_write`` is invoked on every mutating route to enforce write authorization.
    """
    router = APIRouter()

    def repository() -> WorkflowsRepository:
        return WorkflowsRepository(platform.connection)

    def workspaces() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    def evidence() -> EvidenceRepository:
        return EvidenceRepository(platform.connection)

    def jobs() -> JobsRepository:
        return JobsRepository(platform.connection)

    def governance() -> GovernanceRepository:
        return GovernanceRepository(platform.connection)

    def agents() -> AgentsRepository:
        return AgentsRepository(platform.connection)

    def security_policy() -> SecurityPolicyRepository:
        return SecurityPolicyRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    def product_loop() -> ProductLoopCoordinator:
        return ProductLoopCoordinator(platform.connection)

    def product_owner_runner() -> ProductOwnerAgentRunner:
        return ProductOwnerAgentRunner(platform.connection, root=platform.cwd)

    def transition_product_loop(
        *, loop_id: str, to_state: str, reason: str, context_patch: dict[str, Any]
    ) -> None:
        try:
            product_loop().transition(
                loop_id,
                to_state=to_state,
                reason=reason,
                trigger="workflow_product_owner_intake",
                context_patch=context_patch,
            )
        except (ProductLoopTransitionError, ProductLoopStopConditionError, KeyError):
            return

    def apply_product_owner_loop_result(
        *, loop_id: str, result_status: str, reason: str, metadata: dict[str, Any]
    ) -> None:
        if result_status == "completed":
            transition_product_loop(
                loop_id=loop_id,
                to_state="brief_ready",
                reason=reason,
                context_patch={"productOwner": metadata},
            )
            transition_product_loop(
                loop_id=loop_id,
                to_state="backlog_ready",
                reason=reason,
                context_patch={"productOwner": metadata},
            )
            return
        if result_status == "blocked":
            transition_product_loop(
                loop_id=loop_id,
                to_state="awaiting_user",
                reason=reason,
                context_patch={"productOwner": metadata},
            )
            return
        transition_product_loop(
            loop_id=loop_id,
            to_state="blocked",
            reason=reason,
            context_patch={"productOwner": metadata},
        )

    def workflow_product_owner_metadata(
        *, result: dict[str, Any], loop_id: str, workspace_id: str | None
    ) -> dict[str, Any]:
        return redact_secrets(
            {
                "status": result["status"],
                "reason": result["reason"],
                "loopId": loop_id,
                "workspaceId": workspace_id,
                "jobId": (result.get("job") or {}).get("id"),
                "agentRunId": (result.get("agentRun") or {}).get("id"),
                "evidencePackageId": (result.get("evidencePackage") or {}).get("id"),
                "runtimeId": (result.get("runtime") or {}).get("id"),
            }
        )

    def block_workflow_product_owner_intake(
        result: dict[str, Any],
        *,
        idea_step: dict[str, Any],
        loop_id: str | None,
        reason: str,
        status: str = "configuration_required",
    ) -> dict[str, Any]:
        repo = repository()
        metadata = redact_secrets(
            {
                "status": status,
                "reason": reason,
                "loopId": loop_id,
                "execution": "not_executed",
            }
        )
        if loop_id:
            transition_product_loop(
                loop_id=loop_id,
                to_state="blocked",
                reason=reason,
                context_patch={"productOwner": metadata},
            )
        repo.update_workflow_step(
            idea_step["id"],
            status="blocked",
            metadata={**idea_step["metadata"], "productOwnerIntake": metadata},
            output={**idea_step["output"], "productOwnerIntake": metadata},
        )
        run_metadata = {
            **result["workflowRun"]["metadata"],
            "productOwnerIntake": metadata,
        }
        workflow_status = "runtime_unavailable" if status == "runtime_unavailable" else "blocked"
        run = repo.update_workflow_run_status(
            result["workflowRun"]["id"],
            status=workflow_status,
            metadata=run_metadata,
            completed=True,
        )
        workflow = repo.update_workflow_status(
            result["workflow"]["id"], status=workflow_status, reason=reason
        )
        repo.record_workflow_event(
            workflow_id=result["workflow"]["id"],
            workflow_run_id=run["id"],
            step_id=idea_step["id"],
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.idea_intake.{workflow_status}",
            payload=metadata,
            severity="warning",
        )
        return {
            **result,
            "workflow": workflow,
            "workflowRun": run,
            "workflowSteps": repo.list_workflow_steps(workflow_run_id=run["id"]),
        }

    def execute_product_owner_intake_for_workflow(result: dict[str, Any]) -> dict[str, Any]:
        workflow = result["workflow"]
        if workflow["kind"] != "idea_to_pr":
            return result
        steps = result["workflowSteps"]
        idea_step = next((step for step in steps if step["name"] == "idea_intake"), None)
        if not idea_step:
            return result
        run = result["workflowRun"]
        loop = product_loop().start(
            project_id=workflow["projectId"],
            title=workflow["title"],
            context={
                "intake": {
                    "source": "workflow_start",
                    "workflowId": workflow["id"],
                    "workflowRunId": run["id"],
                    "workflowStepId": idea_step["id"],
                }
            },
            correlation_id=run["id"],
            actor="workflow",
            reason="Product loop started from idea_to_pr workflow intake.",
        )
        transition_product_loop(
            loop_id=loop["id"],
            to_state="discovering",
            reason="ProductOwnerAgent workflow intake started.",
            context_patch={"productOwner": {"status": "running", "workflowRunId": run["id"]}},
        )
        task_id = f"workflow-product-owner-{run['id']}"
        try:
            workspace = workspaces().allocate_workspace(
                project_id=workflow["projectId"],
                task_id=task_id,
                agent_id=PRODUCT_OWNER_AGENT_ID,
                reason="idea_to_pr workflow ProductOwnerAgent workspace",
                workflow_run_id=run["id"],
                workflow_step_id=idea_step["id"],
            )
        except (WorkspaceConflictError, WorkspaceIsolationError, ValueError) as error:
            return block_workflow_product_owner_intake(
                result,
                idea_step=idea_step,
                loop_id=loop["id"],
                reason=str(error),
            )

        owner_result = product_owner_runner().run(
            {
                "projectId": workflow["projectId"],
                "workspaceId": workspace["id"],
                "taskId": task_id,
                "idea": str((workflow.get("metadata") or {}).get("idea") or workflow["title"]),
                "workflowContext": {
                    "workflowRunId": run["id"],
                    "workflowStepId": idea_step["id"],
                    "title": workflow["title"],
                },
                "metadata": {
                    "source": "workflow_start",
                    "workflowId": workflow["id"],
                    "workflowRunId": run["id"],
                    "workflowStepId": idea_step["id"],
                    "loopId": loop["id"],
                },
            }
        )
        metadata = workflow_product_owner_metadata(
            result=owner_result, loop_id=loop["id"], workspace_id=workspace["id"]
        )
        owner_status = str(owner_result["status"])
        apply_product_owner_loop_result(
            loop_id=loop["id"],
            result_status=owner_status,
            reason=owner_result["reason"],
            metadata=metadata,
        )

        repo = repository()
        repo.update_workflow_step(
            idea_step["id"],
            status=_product_owner_intake_step_status(owner_status),
            metadata={**idea_step["metadata"], "productOwnerIntake": metadata},
            output={**idea_step["output"], "productOwnerIntake": metadata},
        )
        backlog_step = next((step for step in steps if step["name"] == "backlog_generation"), None)
        if backlog_step and owner_status in {"completed", "blocked"}:
            repo.update_workflow_step(
                backlog_step["id"],
                status=_product_owner_backlog_step_status(owner_status),
                metadata={**backlog_step["metadata"], "productOwnerIntake": metadata},
                output={
                    **backlog_step["output"],
                    "productOwnerIntake": metadata,
                    "epicIds": [
                        item["epic"]["id"]
                        for item in (owner_result.get("epics") or [])
                        if isinstance(item, dict) and isinstance(item.get("epic"), dict)
                    ],
                },
            )
        next_status = _workflow_status_from_product_owner(owner_status)
        run_metadata = {
            **run["metadata"],
            "productOwnerIntake": metadata,
        }
        updated_run = repo.update_workflow_run_status(
            run["id"],
            status=next_status,
            metadata=run_metadata,
            completed=next_status != "running",
            clear_completed=next_status == "running",
        )
        updated_workflow = repo.update_workflow_status(
            workflow["id"], status=next_status, reason=owner_result["reason"]
        )
        repo.record_workflow_event(
            workflow_id=workflow["id"],
            workflow_run_id=run["id"],
            step_id=idea_step["id"],
            project_id=workflow["projectId"],
            event_type=f"workflow.idea_intake.{next_status}",
            payload=metadata,
            severity="warning" if next_status != "running" else "info",
        )
        return {
            **result,
            "workflow": updated_workflow,
            "workflowRun": updated_run,
            "workflowSteps": repo.list_workflow_steps(workflow_run_id=run["id"]),
        }

    def permission_decision_ids_from_tool_calls(tool_calls: list[dict[str, Any]]) -> set[str]:
        decision_ids: set[str] = set()
        for tool_call in tool_calls:
            payload = tool_call.get("payload") if isinstance(tool_call.get("payload"), dict) else {}
            decision_id = str(payload.get("permissionDecisionId") or "").strip()
            if decision_id:
                decision_ids.add(decision_id)
        return decision_ids

    def route_started_workflow_steps(result: dict[str, Any]) -> None:
        router = ModelRouter(platform.connection)
        profiles = RoutingProfileStore(platform.connection)
        workflow_run_id = result["workflowRun"]["id"]
        for step in result["workflowSteps"]:
            role = step.get("role") or "developer"
            try:
                role_policy = profiles.get_role_policy(role)
                mode = step.get("modelMode") or role_policy["routingProfileId"]
            except KeyError:
                mode = step.get("modelMode") or "balanced_best_value"
            step_name = str(step.get("name") or step.get("taskType") or "workflow_step")
            router.preview(
                RoutingRequest(
                    role=role,
                    taskType=step.get("taskType") or step_name,
                    mode=mode,
                    riskLevel=step.get("riskLevel") or "medium",
                    contextTokensEstimate=12000 if step_name in REASONING_STEPS else 4000,
                    requiresCodeEdit=step_name in CODE_EDIT_STEPS,
                    requiresTools=step_name in TOOL_STEPS,
                    requiresSearch=step_name in SEARCH_STEPS,
                    requiresReasoning=step_name in REASONING_STEPS,
                    privacyLevel="remote_allowed",
                    workflowRunId=workflow_run_id,
                    workflowStepId=step["id"],
                    taskId=step_name,
                    **_manual_override(step),
                ),
                record=True,
            )

    def record_gate_result(
        step: dict[str, Any], *, suffix: str, payload: dict[str, Any], severity: str = "info"
    ) -> None:
        action = f"workflow.gate.{step['name']}.{suffix}"
        repository().record_workflow_event(
            workflow_id=step["workflowId"],
            workflow_run_id=step["workflowRunId"],
            step_id=step["id"],
            project_id=step["projectId"],
            event_type=action,
            payload=payload,
            severity=severity,
        )
        event_bus().record_audit(
            project_id=step["projectId"],
            action=action,
            actor="system",
            target=step["id"],
            payload=payload,
        )

    def block_gate(step: dict[str, Any], detail: str, *, payload: dict[str, Any]) -> None:
        record_gate_result(step, suffix="blocked", payload={"reason": detail, **payload}, severity="warning")
        raise HTTPException(status_code=409, detail=detail)

    def passed_qa_evidence_for_step(
        step: dict[str, Any], evidence_package_id: str | None
    ) -> dict[str, Any] | None:
        packages = evidence().list_evidence_for_workflow_runs([step["workflowRunId"]])
        for package in packages:
            if evidence_package_id and package["id"] != evidence_package_id:
                continue
            if qa_passed_without_failed_results(package):
                return package
        return None

    def pending_release_actions_for_step(step: dict[str, Any]) -> list[dict[str, Any]]:
        release_jobs = [
            job
            for job in jobs().list_jobs_for_workflow_runs([step["workflowRunId"]])
            if job["workflowStepId"] == step["id"] and job["kind"] == "release.production"
        ]
        pending: list[dict[str, Any]] = []
        for job in release_jobs:
            pending.extend(
                [action for action in jobs().list_action_requests(job["id"]) if action["status"] == "pending"]
            )
        return pending

    def has_retro_governance_records(step: dict[str, Any]) -> bool:
        records = [
            *governance().list_architecture_decisions(step["projectId"]),
            *governance().list_risks(step["projectId"]),
            *governance().list_next_steps(step["projectId"]),
        ]
        return any(
            record.get("metadata", {}).get("sourceType") == "workflow_retro"
            and record.get("metadata", {}).get("workflowStepId") == step["id"]
            for record in records
        )

    @router.get("/api/v1/workflows", response_model=WorkflowsListResponse)
    async def list_workflows() -> dict[str, Any]:
        repo = repository()
        return {
            "workflows": repo.list_workflows(),
            "workflowRuns": repo.list_workflow_runs(),
            "workflowSteps": repo.list_workflow_steps(),
        }

    @router.post("/api/v1/workflows", status_code=201, response_model=WorkflowResponse)
    async def create_workflow(body: WorkflowCreateRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        validated = validate_workflow_create_body(body)
        workflow = repository().create_workflow(
            project_id=validated["project_id"],
            kind=validated["kind"],
            title=validated["title"],
            metadata=validated["metadata"],
        )
        event_bus().record_event(
            project_id=workflow["projectId"],
            event_type="workflow.created",
            payload={"workflowId": workflow["id"]},
        )
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/issue-to-patch", status_code=202, response_model=IssueToPatchResponse)
    async def run_issue_to_patch(body: IssueToPatchRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        payload = validate_issue_to_patch_body(body)
        try:
            result = IssueToPatchRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_patch.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "runtimeId": result["runtime"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/issue-to-patch/{run_id}/approve",
        status_code=202,
        response_model=IssueToPatchResponse,
    )
    async def approve_issue_to_patch(
        run_id: str, body: WorkflowStatusChangeRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        reason = str(body.reason or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Approval reason is required.")
        try:
            result = IssueToPatchRunner(platform.connection, root=platform.cwd).approve_patch(
                run_id, reason=reason
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_patch.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/issue-to-patch/{run_id}/promote",
        status_code=202,
        response_model=IssueToPatchResponse,
    )
    async def promote_patch_to_branch(
        run_id: str, body: PromotePatchToBranchRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            result = IssueToPatchRunner(platform.connection, root=platform.cwd).promote_patch_to_branch(
                run_id,
                reason=body.reason,
                branch_name=body.branch_name,
                evidence_package_id=body.evidence_package_id,
                qa_commands=body.qa_commands,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_patch.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
                "branchName": result["diffSummary"].get("branch"),
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/issue-to-patch/{run_id}/pull-request",
        status_code=202,
        response_model=IssueToPatchResponse,
    )
    async def create_pull_request_from_promoted_branch(
        run_id: str,
        body: PullRequestCreateRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_write(request)
        try:
            result = IssueToPatchRunner(
                platform.connection, root=platform.cwd
            ).create_pull_request_from_promoted_branch(
                run_id,
                reason=body.reason,
                title=body.title,
                base_branch=body.base_branch,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_patch.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
                "pullRequest": result.get("pullRequest"),
            },
        )
        return result

    @router.post("/api/v1/workflows/issue-to-pr", status_code=202, response_model=IssueToPrResponse)
    async def run_issue_to_pr(body: IssueToPrRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        payload = validate_issue_to_pr_body(body)
        try:
            result = IssueToPrRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_pr.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/issue-to-pr/{run_id}/approve", status_code=202, response_model=IssueToPrResponse
    )
    async def approve_issue_to_pr(
        run_id: str, body: WorkflowStatusChangeRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        reason = str(body.reason or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Approval reason is required.")
        try:
            result = IssueToPrRunner(platform.connection, root=platform.cwd).approve_issue_to_pr(
                run_id, reason=reason
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_pr.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/issue-to-pr/{run_id}/promote", status_code=202, response_model=IssueToPrResponse
    )
    async def promote_issue_to_pr_branch(
        run_id: str, body: PromotePatchToBranchRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        try:
            result = IssueToPrRunner(platform.connection, root=platform.cwd).promote_branch(
                run_id,
                reason=body.reason,
                branch_name=body.branch_name,
                evidence_package_id=body.evidence_package_id,
                qa_commands=body.qa_commands,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_pr.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
                "branchName": result["diffSummary"].get("branch"),
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/issue-to-pr/{run_id}/pull-request",
        status_code=202,
        response_model=IssueToPrResponse,
    )
    async def create_pull_request_from_issue_to_pr(
        run_id: str,
        body: PullRequestCreateRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_write(request)
        try:
            result = IssueToPrRunner(platform.connection, root=platform.cwd).create_pull_request(
                run_id,
                reason=body.reason,
                title=body.title,
                base_branch=body.base_branch,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type=f"workflow.issue_to_pr.{result['status']}",
            payload={
                "workflowId": result["workflow"]["id"],
                "workflowRunId": result["workflowRun"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "jobId": result["job"]["id"],
                "agentRunId": result["agentRun"]["id"],
                "pullRequest": result.get("pullRequest"),
            },
        )
        return result

    @router.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowDetailResponse)
    async def get_workflow(workflow_id: str) -> dict[str, Any]:
        repo = repository()
        try:
            workflow = repo.get_workflow(workflow_id)
            workflow_runs = repo.list_workflow_runs(workflow_id=workflow_id)
            workflow_run_ids = [run["id"] for run in workflow_runs]
            workflow_run_id_set = set(workflow_run_ids)
            workflow_steps = [
                step
                for run_id in workflow_run_ids
                for step in repo.list_workflow_steps(workflow_run_id=run_id)
            ]
            workflow_events = [
                event
                for event in repo.list_workflow_events()
                if event["workflowId"] == workflow_id
                or (event.get("workflowRunId") and event["workflowRunId"] in workflow_run_id_set)
            ]
            runtime_workspaces = workspaces().list_workspaces_for_workflow(workflow_run_ids)
            evidence_repo = evidence()
            evidence_packages = evidence_repo.list_evidence_for_workflow_runs(workflow_run_ids)
            evidence_ids = {package["id"] for package in evidence_packages}
            artifacts = [
                artifact
                for artifact in evidence_repo.list_all_artifacts()
                if artifact.get("evidencePackageId") in evidence_ids
            ]
            test_result_records = [
                result
                for result in evidence_repo.list_all_test_results()
                if result.get("evidencePackageId") in evidence_ids
            ]
            jobs_repo = jobs()
            workflow_jobs = jobs_repo.list_jobs_for_workflow_runs(workflow_run_ids)
            job_ids = {job["id"] for job in workflow_jobs}
            job_runs = [run for job_id in job_ids for run in jobs_repo.list_job_runs(job_id)]
            action_requests = [
                request for job_id in job_ids for request in jobs_repo.list_action_requests(job_id)
            ]
            agents_repo = agents()
            agent_runs = agents_repo.list_agent_runs_for_workflow_runs(workflow_run_ids)
            agent_run_ids = {run["id"] for run in agent_runs}
            agent_tool_calls = [
                tool_call
                for tool_call in agents_repo.list_agent_tool_calls()
                if tool_call.get("agentRunId") in agent_run_ids
            ]
            permission_decision_ids = permission_decision_ids_from_tool_calls(agent_tool_calls)
            model_calls = [
                model_call
                for model_call in agents_repo.list_model_calls()
                if model_call.get("agentRunId") in agent_run_ids
                or (model_call.get("metadata") or {}).get("workflowRunId") in workflow_run_id_set
            ]
            permission_decisions = [
                decision
                for decision in security_policy().list_decisions(project_id=workflow["projectId"])
                if decision["id"] in permission_decision_ids
                or (decision.get("payload") or {}).get("workflowRunId") in workflow_run_id_set
            ]
            workflow_run_details = []
            for run in workflow_runs:
                run_id = run["id"]
                run_evidence = [
                    package for package in evidence_packages if package["workflowRunId"] == run_id
                ]
                run_evidence_ids = {package["id"] for package in run_evidence}
                run_jobs = [job for job in workflow_jobs if job["workflowRunId"] == run_id]
                run_job_ids = {job["id"] for job in run_jobs}
                run_agent_runs = [
                    agent_run for agent_run in agent_runs if agent_run["workflowRunId"] == run_id
                ]
                run_agent_run_ids = {agent_run["id"] for agent_run in run_agent_runs}
                run_tool_calls = [
                    tool_call
                    for tool_call in agent_tool_calls
                    if tool_call["agentRunId"] in run_agent_run_ids
                ]
                run_permission_decision_ids = permission_decision_ids_from_tool_calls(run_tool_calls)
                workflow_run_details.append(
                    {
                        "workflowRun": run,
                        "workflowSteps": [step for step in workflow_steps if step["workflowRunId"] == run_id],
                        "workflowEvents": [
                            event for event in workflow_events if event.get("workflowRunId") == run_id
                        ],
                        "workspaces": [
                            workspace
                            for workspace in runtime_workspaces
                            if workspace["workflowRunId"] == run_id
                        ],
                        "evidencePackages": run_evidence,
                        "artifacts": [
                            artifact
                            for artifact in artifacts
                            if artifact.get("evidencePackageId") in run_evidence_ids
                        ],
                        "testResultRecords": [
                            result
                            for result in test_result_records
                            if result.get("evidencePackageId") in run_evidence_ids
                        ],
                        "jobs": run_jobs,
                        "jobRuns": [job_run for job_run in job_runs if job_run["jobId"] in run_job_ids],
                        "actionRequests": [
                            action for action in action_requests if action["jobId"] in run_job_ids
                        ],
                        "agentRuns": run_agent_runs,
                        "agentToolCalls": run_tool_calls,
                        "modelCalls": [
                            model_call
                            for model_call in model_calls
                            if model_call.get("agentRunId") in run_agent_run_ids
                            or (model_call.get("metadata") or {}).get("workflowRunId") == run_id
                        ],
                        "permissionDecisions": [
                            decision
                            for decision in permission_decisions
                            if (decision.get("payload") or {}).get("workflowRunId") == run_id
                            or decision["id"] in run_permission_decision_ids
                        ],
                    }
                )
            return {
                "workflow": workflow,
                "workflowRuns": workflow_runs,
                "workflowSteps": workflow_steps,
                "workflowEvents": workflow_events,
                "workflowRunDetails": workflow_run_details,
                "workspaces": runtime_workspaces,
                "evidencePackages": evidence_packages,
                "artifacts": artifacts,
                "testResultRecords": test_result_records,
                "jobs": workflow_jobs,
                "jobRuns": job_runs,
                "actionRequests": action_requests,
                "agentRuns": agent_runs,
                "agentToolCalls": agent_tool_calls,
                "modelCalls": model_calls,
                "permissionDecisions": permission_decisions,
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/workflows/{workflow_id}/start", status_code=202, response_model=WorkflowStartResponse
    )
    async def start_workflow(
        workflow_id: str, body: WorkflowStatusChangeRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        result = repository().start_workflow(workflow_id, reason=body.reason)
        route_started_workflow_steps(result)
        result = execute_product_owner_intake_for_workflow(result)
        event_bus().record_event(
            project_id=result["workflow"]["projectId"],
            event_type="workflow.started",
            payload={
                "workflowId": workflow_id,
                "workflowRunId": result["workflowRun"]["id"],
                "routingDecisionCount": len(result["workflowSteps"]),
            },
        )
        return result

    @router.post(
        "/api/v1/workflows/{workflow_id}/steps/{step_id}/advance",
        status_code=202,
        response_model=WorkflowGateAdvanceResponse,
    )
    async def advance_workflow_gate(
        workflow_id: str,
        step_id: str,
        body: WorkflowGateAdvanceRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_write(request)
        repo = repository()
        try:
            step = repo.get_workflow_step(step_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if step["workflowId"] != workflow_id:
            raise HTTPException(status_code=404, detail=f"Workflow step not found for workflow: {step_id}")
        if step["name"] not in {"pr_review", "release_gate", "retro"}:
            raise HTTPException(
                status_code=422, detail=f"Workflow step {step['name']} is not a governed gate."
            )

        reason = body.reason or "Advance governed workflow gate."
        if step["name"] == "pr_review":
            package = passed_qa_evidence_for_step(step, body.evidence_package_id)
            if not package:
                block_gate(
                    step,
                    "pr_review requires passed QA evidence for this workflow run.",
                    payload={"evidencePackageId": body.evidence_package_id},
                )
            metadata = {
                **step["metadata"],
                "gateState": "evidence_satisfied",
                "evidencePackageId": package["id"],
                "advancedAt": utc_now(),
            }
            updated = repo.update_workflow_step(
                step_id,
                status="completed",
                metadata=metadata,
                output={"advanced": True, "evidencePackageId": package["id"], "reason": reason},
            )
            record_gate_result(
                updated,
                suffix="advanced",
                payload={"evidencePackageId": package["id"], "reason": reason},
            )
            return {
                "workflowStep": updated,
                "advanced": True,
                "gateState": metadata["gateState"],
                "reason": reason,
            }

        if step["name"] == "release_gate":
            pending_actions = pending_release_actions_for_step(step)
            if pending_actions:
                block_gate(
                    step,
                    "release_gate requires approved production release action requests.",
                    payload={"pendingActionRequestIds": [item["id"] for item in pending_actions]},
                )
            metadata = {
                **step["metadata"],
                "gateState": "approval_satisfied",
                "advancedAt": utc_now(),
            }
            updated = repo.update_workflow_step(
                step_id,
                status="completed",
                metadata=metadata,
                output={"advanced": True, "reason": reason},
            )
            record_gate_result(updated, suffix="advanced", payload={"reason": reason})
            return {
                "workflowStep": updated,
                "advanced": True,
                "gateState": metadata["gateState"],
                "reason": reason,
            }

        if not has_retro_governance_records(step):
            block_gate(
                step,
                "retro requires governance records seeded for this workflow step.",
                payload={},
            )
        metadata = {
            **step["metadata"],
            "gateState": "retro_recorded",
            "advancedAt": utc_now(),
        }
        updated = repo.update_workflow_step(
            step_id,
            status="completed",
            metadata=metadata,
            output={"advanced": True, "reason": reason},
        )
        record_gate_result(updated, suffix="advanced", payload={"reason": reason})
        return {
            "workflowStep": updated,
            "advanced": True,
            "gateState": metadata["gateState"],
            "reason": reason,
        }

    @router.post("/api/v1/workflows/{workflow_id}/pause", status_code=202, response_model=WorkflowResponse)
    async def pause_workflow(
        workflow_id: str, body: WorkflowStatusChangeRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        workflow = repository().update_workflow_status(workflow_id, status="paused", reason=body.reason)
        event_bus().record_event(
            project_id=workflow["projectId"],
            event_type="workflow.paused",
            payload={"workflowId": workflow_id},
        )
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/{workflow_id}/resume", status_code=202, response_model=WorkflowResponse)
    async def resume_workflow(
        workflow_id: str, body: WorkflowStatusChangeRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        workflow = repository().update_workflow_status(workflow_id, status="running", reason=body.reason)
        event_bus().record_event(
            project_id=workflow["projectId"],
            event_type="workflow.resumed",
            payload={"workflowId": workflow_id},
        )
        return {"workflow": workflow}

    @router.post("/api/v1/workflows/{workflow_id}/cancel", status_code=202, response_model=WorkflowResponse)
    async def cancel_workflow(
        workflow_id: str, body: WorkflowStatusChangeRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        workflow = repository().update_workflow_status(workflow_id, status="cancelled", reason=body.reason)
        event_bus().record_event(
            project_id=workflow["projectId"],
            event_type="workflow.cancelled",
            payload={"workflowId": workflow_id},
        )
        risk = record_governance_risk(
            platform.connection,
            project_id=workflow["projectId"],
            title=f"Workflow cancelled: {workflow['title']}",
            source_type="workflow_status",
            source_id=workflow["id"],
            severity="medium",
            description=body.reason or "Workflow was cancelled before completion.",
            mitigation="Review workflow events, open approvals, and evidence gaps before retrying.",
            owner="technical_lead",
            metadata={"status": workflow["status"]},
        )
        if risk:
            event_bus().record_event(
                project_id=risk["projectId"],
                event_type="risk.created",
                payload={"riskId": risk["id"], "sourceType": "workflow_status"},
            )
        return {"workflow": workflow}

    return router
