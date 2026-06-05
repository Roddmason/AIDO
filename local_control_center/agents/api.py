from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.event_bus import EventBus

from .contracts import (
    AgentProfilesListResponse,
    AgentProfileResponse,
    AgentProfileUpsertRequest,
    AgentRunCreateRequest,
    AgentRunsListResponse,
    AgentRunResponse,
    DeveloperAgentRunRequest,
    DeveloperAgentRunResponse,
    DeveloperAgentStatusResponse,
    ModelPoliciesListResponse,
    ModelPolicyResponse,
    ModelPolicyUpsertRequest,
    ModelProvidersListResponse,
    RuntimeProviderConfigurationResponse,
    RuntimeProvidersResponse,
    SkillsListResponse,
    SkillsSyncRequest,
    SkillsSyncResponse,
)
from .developer_agent import DeveloperAgentRunner
from .model_gateway import LOCAL_MODEL_PROVIDERS, REMOTE_MODEL_PROVIDERS
from .repository import AgentsRepository
from .runtime_provider_config import list_runtime_provider_configurations
from .runtime_status import RUNTIME_MODES, RuntimeStatusService
from .skills import SkillRegistry
from .tool_broker import ToolBroker


EXECUTION_MODES_WITH_EVIDENCE = {"restricted_subprocess", "docker"}
ID_RE = re.compile(r"^[a-z0-9_-]{3,64}$")
TOOL_ID_RE = re.compile(r"^[a-z0-9_.:-]{2,80}$")
CATALOG_ID_RE = re.compile(r"^[a-z0-9_.:-]{2,96}$")
VALID_AGENT_ROLES = {
    "analyst",
    "product_owner",
    "technical_lead",
    "technical_lead_shadow",
    "developer",
    "backend_engineer",
    "frontend_engineer",
    "implementer",
    "qa",
    "qa_reviewer",
    "security_reviewer",
    "release_manager",
}
VALID_PERMISSION_PROFILES = {"plan", "dev_safe", "qa", "release"}
VALID_POLICY_STATUS = {"active", "disabled"}
DEVELOPER_AGENT_RUNTIMES = {"codex_cli", "claude_code_cli", "openai_compatible", "ollama"}


def _require_id(value: Any, *, label: str) -> str:
    candidate = str(value or "")
    if not ID_RE.match(candidate):
        raise HTTPException(
            status_code=422,
            detail=f"{label} must use lowercase letters, numbers, dashes or underscores.",
        )
    return candidate


def validate_agent_profile_body(body: dict[str, Any]) -> dict[str, Any]:
    _require_id(body.get("id"), label="Agent profile id")
    role = str(body.get("role") or "implementer")
    runtime_mode = str(body.get("runtimeMode") or body.get("runtimeType") or "hybrid")
    permission_profile = str(body.get("permissionProfile") or "plan")
    allowed_tools = body.get("allowedTools") or []
    allowed_providers = body.get("allowedProviders") or []
    allowed_runtimes = body.get("allowedRuntimes") or []
    if role not in VALID_AGENT_ROLES:
        raise HTTPException(status_code=422, detail="Agent role is not in the allowed catalog.")
    if runtime_mode not in RUNTIME_MODES:
        raise HTTPException(status_code=422, detail="Runtime mode is not in the allowed catalog.")
    if permission_profile not in VALID_PERMISSION_PROFILES:
        raise HTTPException(status_code=422, detail="Permission profile is not in the allowed catalog.")
    if not isinstance(allowed_tools, list) or not all(isinstance(item, str) and TOOL_ID_RE.match(item) for item in allowed_tools):
        raise HTTPException(status_code=422, detail="Allowed tools must be catalog ids, not free-form JSON.")
    if not isinstance(allowed_providers, list) or not all(isinstance(item, str) and CATALOG_ID_RE.match(item) for item in allowed_providers):
        raise HTTPException(status_code=422, detail="Allowed providers must be compact catalog ids.")
    if not isinstance(allowed_runtimes, list) or not all(isinstance(item, str) and CATALOG_ID_RE.match(item) for item in allowed_runtimes):
        raise HTTPException(status_code=422, detail="Allowed runtimes must be compact catalog ids.")
    for field in ("routingProfileId", "roleModelPolicyId"):
        if body.get(field) and not CATALOG_ID_RE.match(str(body[field])):
            raise HTTPException(status_code=422, detail=f"{field} must be a compact catalog id.")
    try:
        max_tokens = int(body.get("maxTokensPerRun") or 0)
        approval_threshold = body.get("requiresApprovalOverUsd")
        approval_value = None if approval_threshold in {None, ""} else float(approval_threshold)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Agent routing numeric fields are invalid.") from error
    if max_tokens < 0 or max_tokens > 200000:
        raise HTTPException(status_code=422, detail="maxTokensPerRun must be between 0 and 200000.")
    if approval_value is not None and approval_value < 0:
        raise HTTPException(status_code=422, detail="requiresApprovalOverUsd must be zero or positive.")
    for field in ("allowRemote", "allowCli", "allowApi"):
        if field in body and not isinstance(body[field], bool):
            raise HTTPException(status_code=422, detail=f"{field} must be a boolean.")
    return body


def validate_model_policy_body(body: dict[str, Any]) -> dict[str, Any]:
    _require_id(body.get("id"), label="Model policy id")
    provider_catalog = LOCAL_MODEL_PROVIDERS | REMOTE_MODEL_PROVIDERS
    for field in ("preferred", "fallback"):
        candidates = body.get(field) or []
        if not isinstance(candidates, list):
            raise HTTPException(status_code=422, detail=f"{field} must be a provider catalog list.")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise HTTPException(status_code=422, detail=f"{field} entries must be objects.")
            provider = str(candidate.get("provider") or "")
            model = str(candidate.get("model") or "")
            if provider not in provider_catalog:
                raise HTTPException(status_code=422, detail="Model provider is not in the allowed catalog.")
            if not model or len(model) > 160 or any(char.isspace() for char in model):
                raise HTTPException(status_code=422, detail="Model id must be a compact catalog value.")
    try:
        max_cost = float(body.get("maxCostUsd", 0))
        max_tokens = int(body.get("maxTokens", 0))
        temperature = float(body.get("temperature", 0.2))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Model policy numeric fields are invalid.") from error
    if max_cost < 0:
        raise HTTPException(status_code=422, detail="maxCostUsd must be zero or positive.")
    if max_tokens < 0 or max_tokens > 200000:
        raise HTTPException(status_code=422, detail="maxTokens must be between 0 and 200000.")
    if temperature < 0 or temperature > 2:
        raise HTTPException(status_code=422, detail="temperature must be between 0 and 2.")
    if body.get("status", "active") not in VALID_POLICY_STATUS:
        raise HTTPException(status_code=422, detail="Model policy status is invalid.")
    return body


def validate_developer_agent_run_body(body: DeveloperAgentRunRequest) -> dict[str, Any]:
    payload = body.model_dump(by_alias=True)
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="DeveloperAgent instruction is required.")
    if len(instruction) > 20000:
        raise HTTPException(status_code=422, detail="DeveloperAgent instruction must be 20000 characters or fewer.")
    preferred_runtime = payload.get("preferredRuntime")
    if preferred_runtime and preferred_runtime not in DEVELOPER_AGENT_RUNTIMES:
        raise HTTPException(status_code=422, detail=f"DeveloperAgent runtime is not allowed: {preferred_runtime}")
    qa_commands = payload.get("qaCommands") or []
    for index, argv in enumerate(qa_commands):
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(status_code=422, detail=f"qaCommands[{index}] must be a non-empty structured argv list.")
    max_cost = payload.get("maxCostUsd")
    if max_cost is not None and float(max_cost) < 0:
        raise HTTPException(status_code=422, detail="maxCostUsd must be zero or positive.")
    return payload


def _execution_test_result(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    output_refs = [
        artifact_id
        for artifact_id in (execution_result.get("stdoutArtifactId"), execution_result.get("stderrArtifactId"))
        if artifact_id
    ]
    return {
        "command": payload.get("command") or tool_call.get("toolName"),
        "status": tool_call.get("status"),
        "execution": payload.get("execution"),
        "returnCode": execution_result.get("returnCode"),
        "timedOut": bool(execution_result.get("timedOut", False)),
        "durationMs": execution_result.get("durationMs"),
        "blocked": bool(execution_result.get("blocked", False)),
        "reason": execution_result.get("reason"),
        "toolCallId": tool_call.get("id"),
        "outputRef": output_refs[0] if output_refs else None,
        "outputRefs": output_refs,
    }


def _execution_artifact_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    artifact_ids: list[str] = []
    for tool_call in tool_calls:
        payload = tool_call.get("payload") or {}
        execution_result = payload.get("executionResult") or {}
        for key in ("stdoutArtifactId", "stderrArtifactId"):
            artifact_id = execution_result.get(key)
            if isinstance(artifact_id, str) and artifact_id:
                artifact_ids.append(artifact_id)
    return artifact_ids


def _input_evidence_refs(input_payload: dict[str, Any]) -> list[str]:
    for key in ("evidenceRefs", "evidence_refs", "evidencePackageIds"):
        refs = input_payload.get(key)
        if isinstance(refs, list):
            return [str(ref) for ref in refs if isinstance(ref, str) and ref]
    return []


def _is_technical_review_run(*, profile: dict[str, Any], task_id: str, input_payload: dict[str, Any]) -> bool:
    markers = {
        str(task_id),
        str(input_payload.get("stage") or ""),
        str(input_payload.get("workflowStepName") or ""),
        str(input_payload.get("workflowStep") or ""),
    }
    return profile.get("role") == "technical_lead" and any("technical_review" in marker for marker in markers)


def _blocked_technical_review_output(*, profile: dict[str, Any], task_id: str) -> dict[str, Any]:
    return {
        "agent_id": profile["id"],
        "task_id": task_id,
        "verdict": "blocked",
        "summary": "Technical review requires at least one evidence package reference.",
        "evidence_refs": [],
        "risks": [
            {
                "severity": "high",
                "description": "Technical review attempted without evidence package references.",
                "mitigation": "Attach QA evidence package IDs before issuing a technical review verdict.",
            }
        ],
        "next_actions": ["Attach evidenceRefs to the agent run input."],
    }


def _create_execution_evidence(
    *,
    platform: Any,
    project_id: str,
    workflow_run_id: str | None,
    agent_id: str,
    task_id: str,
    tool_calls: list[dict[str, Any]],
) -> list[str]:
    executed_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if (tool_call.get("payload") or {}).get("execution") in EXECUTION_MODES_WITH_EVIDENCE
    ]
    if not executed_tool_calls:
        return []

    test_results = [_execution_test_result(tool_call) for tool_call in executed_tool_calls]
    failed = any(result["status"] in {"denied", "failed"} or result["returnCode"] not in {0, None} for result in test_results)
    evidence_repo = EvidenceRepository(platform.connection)
    evidence = evidence_repo.create_evidence_package(
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        agent_id=agent_id,
        task_id=task_id,
        test_plan="Capture policy-gated agent tool-call execution.",
        test_results=test_results,
        risk_notes=[
            {
                "severity": "low",
                "description": "Tool execution was mediated by policy and sandbox adapters.",
                "mitigation": "Keep command stdout/stderr redacted and attach larger logs as artifacts in a later hardening pass.",
            }
        ],
        qa_verdict="failed" if failed else "evidence_collected",
    )
    for artifact_id in _execution_artifact_ids(executed_tool_calls):
        evidence_repo.attach_artifact_to_evidence(
            artifact_id=artifact_id,
            evidence_package_id=evidence["id"],
        )
    EventBus(platform.connection).record_event(
        project_id=project_id,
        event_type="qa.evidence.created",
        payload={
            "evidencePackageId": evidence["id"],
            "source": "agent_tool_execution",
            "workflowRunId": workflow_run_id,
        },
    )
    return [evidence["id"]]


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> AgentsRepository:
        return AgentsRepository(platform.connection)

    def skill_registry() -> SkillRegistry:
        return SkillRegistry(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/agents/developer/status", response_model=DeveloperAgentStatusResponse)
    async def developer_agent_status() -> dict[str, Any]:
        return {"developerAgent": DeveloperAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post("/api/v1/agents/developer/runs", status_code=202, response_model=DeveloperAgentRunResponse)
    async def run_developer_agent(body: DeveloperAgentRunRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        payload = validate_developer_agent_run_body(body)
        try:
            result = DeveloperAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["agentRun"]["projectId"],
            event_type=f"agent.developer.{result['status']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "runtimeId": result["runtime"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
            },
        )
        return result

    @router.get("/api/v1/agent-profiles", response_model=AgentProfilesListResponse)
    async def list_agent_profiles() -> dict[str, Any]:
        return {"agentProfiles": repository().list_agent_profiles()}

    @router.post("/api/v1/agent-profiles", status_code=201, response_model=AgentProfileResponse)
    async def upsert_agent_profile(body: AgentProfileUpsertRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        payload = validate_agent_profile_body(body.model_dump(by_alias=True))
        profile = repository().upsert_agent_profile(payload)
        event_bus().record_event(event_type="agent.profile.upserted", payload={"agentProfileId": profile["id"]})
        return {"agentProfile": profile}

    @router.get("/api/v1/agent-runs", response_model=AgentRunsListResponse)
    async def list_agent_runs() -> dict[str, Any]:
        return {"agentRuns": repository().list_agent_runs()}

    @router.post("/api/v1/agent-runs", status_code=202, response_model=AgentRunResponse)
    async def create_agent_run(body: AgentRunCreateRequest, request: Request) -> AgentRunResponse:
        require_write(request)
        payload = body.model_dump(by_alias=True)
        repo = repository()
        profile = repo.get_agent_profile(payload["agentProfileId"])
        task_id = payload.get("taskId", "task")
        input_payload = payload.get("input") or {}
        tool_calls = input_payload.get("toolCalls") or []
        if profile["runtimeMode"] not in RUNTIME_MODES:
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "blocked",
                "summary": f"Runtime {profile['runtimeMode']} is not a supported product runtime.",
                "evidence_refs": [],
                "risks": [
                    {
                        "severity": "high",
                        "description": "Agent profile references a runtime outside the product catalog.",
                        "mitigation": "Update the profile to a configured product runtime before running it.",
                    }
                ],
                "next_actions": [],
            }
            status = "failed"
        elif tool_calls:
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "evaluating_tools",
                "summary": f"Runtime {profile['runtimeMode']} requested policy-gated tool calls.",
                "evidence_refs": [],
                "risks": [],
                "next_actions": [],
            }
            status = "running"
        elif _is_technical_review_run(profile=profile, task_id=task_id, input_payload=input_payload) and not _input_evidence_refs(
            input_payload
        ):
            output = _blocked_technical_review_output(profile=profile, task_id=task_id)
            status = "failed"
        else:
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "blocked",
                "summary": f"Runtime {profile['runtimeMode']} has no executable adapter configured for this run.",
                "evidence_refs": [],
                "risks": [
                    {
                        "severity": "medium",
                        "description": "Runtime adapter unavailable.",
                        "mitigation": "Route tool calls through the broker or install the adapter explicitly.",
                    }
                ],
                "next_actions": [],
            }
            status = "failed"
        run = repo.create_agent_run(
            project_id=payload["projectId"],
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=input_payload,
            output_payload=output,
            job_id=payload.get("jobId"),
            workflow_run_id=payload.get("workflowRunId"),
            workflow_step_id=payload.get("workflowStepId"),
            status=status,
        )
        if tool_calls:
            broker_results = ToolBroker(platform.connection, artifact_root=platform.cwd).evaluate_tool_calls(
                project_id=payload["projectId"],
                agent_run_id=run["id"],
                agent_profile=profile,
                tool_calls=tool_calls,
                job_id=payload.get("jobId"),
            )
            decisions = [result["decision"]["decision"] for result in broker_results]
            tool_statuses = [result["toolCall"]["status"] for result in broker_results]
            tool_call_records = [result["toolCall"] for result in broker_results]
            executed = any(
                result["toolCall"]["payload"].get("execution") in EXECUTION_MODES_WITH_EVIDENCE
                for result in broker_results
            )
            evidence_refs = _create_execution_evidence(
                platform=platform,
                project_id=payload["projectId"],
                workflow_run_id=payload.get("workflowRunId"),
                agent_id=profile["id"],
                task_id=task_id,
                tool_calls=tool_call_records,
            )
            if any(decision == "deny" for decision in decisions) or any(
                tool_status in {"denied", "failed"} for tool_status in tool_statuses
            ):
                status = "failed"
                verdict = "blocked"
                summary = "At least one tool call was denied by policy or failed sandbox execution."
            elif any(decision in {"requires_approval", "requires_human"} for decision in decisions):
                status = "awaiting_permission"
                verdict = "awaiting_permission"
                summary = "Tool calls are waiting for granular approval."
            else:
                status = "completed"
                verdict = "approved_with_risks"
                summary = (
                    "Tool calls were executed through sandboxed adapters."
                    if executed
                    else "Tool calls were policy-allowed but not executed by the control plane."
                )
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": verdict,
                "summary": summary,
                "evidence_refs": evidence_refs,
                "risks": [
                    {
                        "severity": "low",
                        "description": "Broker records decisions before execution.",
                        "mitigation": "Execute only through sandboxed adapters after evidence capture.",
                    }
                ],
                "next_actions": [],
                "tool_calls": tool_call_records,
            }
            run = repo.update_agent_run_status(run["id"], status=status, output_payload=output)
            for result in broker_results:
                decision = result["decision"]
                event_type = {
                    "allow": "tool.call.allowed",
                    "deny": "tool.call.denied",
                    "requires_approval": "approval.created",
                    "requires_human": "approval.created",
                }.get(decision["decision"], "tool.call.requested")
                event_bus().record_event(
                    project_id=payload["projectId"],
                    event_type=event_type,
                    payload={
                        "agentRunId": run["id"],
                        "toolCallId": result["toolCall"]["id"],
                        "permissionDecisionId": decision["id"],
                        "actionRequestId": result["actionRequest"]["id"] if result["actionRequest"] else None,
                    },
                )
        event_bus().record_event(
            project_id=payload["projectId"],
            event_type=f"agent.run.{status}",
            payload={"agentRunId": run["id"], "agentProfileId": profile["id"]},
        )
        return AgentRunResponse(agentRun=run)

    @router.get("/api/v1/model-providers", response_model=ModelProvidersListResponse)
    async def list_model_providers() -> dict[str, Any]:
        return {"modelProviders": repository().list_model_providers()}

    @router.get("/api/v1/runtime/providers", response_model=RuntimeProvidersResponse)
    async def list_runtime_providers() -> dict[str, Any]:
        return RuntimeStatusService(platform.connection).runtime_provider_status()

    @router.get("/api/v1/runtime/provider-configuration", response_model=RuntimeProviderConfigurationResponse)
    async def list_runtime_provider_configuration() -> dict[str, Any]:
        return {"providers": list_runtime_provider_configurations()}

    @router.get("/api/v1/skills", response_model=SkillsListResponse)
    async def list_skills() -> dict[str, Any]:
        return {"skills": skill_registry().list_skills()}

    @router.post("/api/v1/skills/sync", status_code=202, response_model=SkillsSyncResponse)
    async def sync_skills(body: SkillsSyncRequest, request: Request) -> SkillsSyncResponse:
        require_write(request)
        count = skill_registry().sync(body.skills_path)
        event_bus().record_event(event_type="skills.synced", payload={"synced": count})
        return SkillsSyncResponse(synced=count, skills=skill_registry().list_skills())

    @router.get("/api/v1/model-policies", response_model=ModelPoliciesListResponse)
    async def list_model_policies() -> dict[str, Any]:
        return {"modelPolicies": repository().list_model_policies()}

    @router.post("/api/v1/model-policies", status_code=201, response_model=ModelPolicyResponse)
    async def upsert_model_policy(body: ModelPolicyUpsertRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        payload = validate_model_policy_body(body.model_dump(by_alias=True))
        policy = repository().upsert_model_policy(payload)
        event_bus().record_event(event_type="model.policy.upserted", payload={"modelPolicyId": policy["id"]})
        return {"modelPolicy": policy}

    return router
