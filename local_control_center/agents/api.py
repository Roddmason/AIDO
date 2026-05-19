from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from .executor import run_internal_mock_agent
from .repository import AgentsRepository
from .skills import SkillRegistry


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> AgentsRepository:
        return AgentsRepository(platform.connection)

    def skill_registry() -> SkillRegistry:
        return SkillRegistry(platform.connection)

    @router.get("/api/v1/agent-profiles")
    async def list_agent_profiles() -> dict[str, Any]:
        return {"agentProfiles": repository().list_agent_profiles()}

    @router.post("/api/v1/agent-profiles", status_code=201)
    async def upsert_agent_profile(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        profile = repository().upsert_agent_profile(body)
        platform.record_event(event_type="agent.profile.upserted", payload={"agentProfileId": profile["id"]})
        return {"agentProfile": profile}

    @router.get("/api/v1/agent-runs")
    async def list_agent_runs() -> dict[str, Any]:
        return {"agentRuns": repository().list_agent_runs()}

    @router.post("/api/v1/agent-runs", status_code=202)
    async def create_agent_run(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        repo = repository()
        profile = repo.get_agent_profile(body["agentProfileId"])
        task_id = body.get("taskId", "task")
        if profile["runtimeType"] != "internal_mock":
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "blocked",
                "summary": f"Runtime {profile['runtimeType']} is optional and not installed.",
                "evidence_refs": [],
                "risks": [{"severity": "medium", "description": "Runtime unavailable.", "mitigation": "Install adapter."}],
                "next_actions": [],
            }
            status = "failed"
        else:
            output = run_internal_mock_agent(agent_profile=profile, task_id=task_id, input_payload=body.get("input") or {})
            status = "completed"
        run = repo.create_agent_run(
            project_id=body["projectId"],
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=body.get("input") or {},
            output_payload=output,
            status=status,
        )
        platform.record_event(
            project_id=body["projectId"],
            event_type=f"agent.run.{status}",
            payload={"agentRunId": run["id"], "agentProfileId": profile["id"]},
        )
        return {"agentRun": run}

    @router.get("/api/v1/model-providers")
    async def list_model_providers() -> dict[str, Any]:
        return {"modelProviders": platform.list_providers()}

    @router.get("/api/v1/skills")
    async def list_skills() -> dict[str, Any]:
        return {"skills": skill_registry().list_skills()}

    @router.post("/api/v1/skills/sync", status_code=202)
    async def sync_skills(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        count = skill_registry().sync(body.get("skillsPath", "skills"))
        platform.record_event(event_type="skills.synced", payload={"synced": count})
        return {"synced": count, "skills": skill_registry().list_skills()}

    @router.get("/api/v1/model-policies")
    async def list_model_policies() -> dict[str, Any]:
        return {"modelPolicies": repository().list_model_policies()}

    @router.post("/api/v1/model-policies", status_code=201)
    async def upsert_model_policy(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        policy = repository().upsert_model_policy(body)
        platform.record_event(event_type="model.policy.upserted", payload={"modelPolicyId": policy["id"]})
        return {"modelPolicy": policy}

    return router
