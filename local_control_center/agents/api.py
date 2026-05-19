from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from .repository import AgentsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> AgentsRepository:
        return AgentsRepository(platform.connection)

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
        rows = platform.connection.execute("SELECT * FROM agent_runs ORDER BY created_at DESC").fetchall()
        return {
            "agentRuns": [
                {
                    "id": row["id"],
                    "projectId": row["project_id"],
                    "jobId": row["job_id"],
                    "status": row["status"],
                    "input": row["input"],
                    "output": row["output"],
                    "metadata": row["metadata"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in rows
            ]
        }

    @router.get("/api/v1/model-providers")
    async def list_model_providers() -> dict[str, Any]:
        return {"modelProviders": platform.list_providers()}

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
