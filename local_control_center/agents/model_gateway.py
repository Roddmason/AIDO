from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.telemetry import record_model_call

from .repository import AgentsRepository


LOCAL_MODEL_PROVIDERS = {"ollama", "local_ollama"}
REMOTE_MODEL_PROVIDERS = {"openai", "openai_compatible", "openai_agents", "openrouter"}


def _is_allowed_provider(candidate: dict[str, Any], policy: dict[str, Any]) -> bool:
    provider = str(candidate.get("provider") or "")
    if provider in LOCAL_MODEL_PROVIDERS:
        return bool(policy.get("allowLocal"))
    if provider in REMOTE_MODEL_PROVIDERS:
        return bool(policy.get("allowRemote"))
    return False


def _select_candidate(policy: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in [*policy.get("preferred", []), *policy.get("fallback", [])]:
        if candidate.get("provider") and candidate.get("model") and _is_allowed_provider(candidate, policy):
            return candidate
    return None


class ModelGateway:
    def __init__(self, connection: sqlite3.Connection):
        self.repository = AgentsRepository(connection)

    def redact_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        return redact_secrets(metadata or {})

    def plan_model_call(self, *, model_policy_id: str) -> dict[str, Any]:
        policy = self.repository.get_model_policy(model_policy_id)
        candidate = _select_candidate(policy)
        return {"policy": policy, "candidate": candidate}

    def record_model_usage(
        self,
        *,
        project_id: str,
        provider: str,
        model: str,
        status: str,
        model_policy_id: str | None = None,
        agent_run_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_call = self.repository.record_model_call(
            project_id=project_id,
            agent_run_id=agent_run_id,
            model_policy_id=model_policy_id,
            provider=provider,
            model=model,
            status=status,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            metadata=self.redact_metadata(metadata),
        )
        record_model_call(self.repository.connection, model_call)
        return model_call

    def block_if_budget_exceeded(
        self,
        *,
        project_id: str,
        policy: dict[str, Any],
        candidate: dict[str, Any],
        estimated_cost_usd: float,
    ) -> dict[str, Any] | None:
        current_spend = self.repository.total_cost_usage(project_id=project_id, scope="model_call")
        max_cost = float(policy.get("maxCostUsd") or 0)
        estimated_cost = float(estimated_cost_usd or 0)
        if max_cost > 0 and current_spend + estimated_cost > max_cost:
            return {
                "status": "blocked_budget",
                "provider": candidate["provider"],
                "model": candidate["model"],
                "metadata": {
                    "reason": "Estimated model call cost exceeds model policy budget.",
                    "currentSpendUsd": current_spend,
                    "estimatedCostUsd": estimated_cost,
                    "maxCostUsd": max_cost,
                },
            }
        return None

    def execute_model_call(self, planned_call: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "not_executed",
            "reason": "ModelGateway execution is gated by provider-specific APIs.",
            "plannedCall": redact_secrets(planned_call),
        }

    def provider_health(self, provider_id: str) -> dict[str, Any]:
        row = self.repository.connection.execute(
            """
            SELECT provider_id, health_status, last_health_check_at, last_error
            FROM provider_accounts
            WHERE provider_id = ? OR id = ?
            """,
            (provider_id, provider_id),
        ).fetchone()
        if not row:
            return {"providerId": provider_id, "healthStatus": "unknown", "reason": "Provider account not found."}
        return {
            "providerId": row["provider_id"],
            "healthStatus": row["health_status"],
            "healthCheckedAt": row["last_health_check_at"],
            "reason": redact_secrets(row["last_error"] or ""),
        }

    def prepare_model_call(
        self,
        *,
        project_id: str,
        model_policy_id: str,
        estimated_cost_usd: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        agent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sanitized_metadata = self.redact_metadata(metadata)
        plan = self.plan_model_call(model_policy_id=model_policy_id)
        policy = plan["policy"]
        candidate = plan["candidate"]
        if not candidate:
            model_call = self.record_model_usage(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider="unresolved",
                model="unresolved",
                status="blocked_policy",
                metadata={"reason": "No provider candidate is allowed by this model policy.", **sanitized_metadata},
            )
            return {"status": "blocked_policy", "modelCall": model_call}

        budget_block = self.block_if_budget_exceeded(
            project_id=project_id,
            policy=policy,
            candidate=candidate,
            estimated_cost_usd=estimated_cost_usd,
        )
        if budget_block:
            model_call = self.record_model_usage(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider=str(candidate["provider"]),
                model=str(candidate["model"]),
                status="blocked_budget",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                metadata={**budget_block["metadata"], **sanitized_metadata},
            )
            return {**budget_block, "modelCall": model_call}

        estimated_cost = float(estimated_cost_usd or 0)
        model_call = self.record_model_usage(
            project_id=project_id,
            agent_run_id=agent_run_id,
            model_policy_id=model_policy_id,
            provider=str(candidate["provider"]),
            model=str(candidate["model"]),
            status="prepared",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimated_cost,
            metadata={"estimated": True, **sanitized_metadata},
        )
        return {
            "status": "prepared",
            "provider": candidate["provider"],
            "model": candidate["model"],
            "modelCall": model_call,
        }


def ollama_status(*, base_url: str | None = None) -> dict[str, Any]:
    resolved_base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
    try:
        request = Request(f"{resolved_base_url}/api/tags", method="GET")
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as error:
        return {"provider": "ollama", "available": False, "models": [], "reason": str(error)}

    models = [str(item.get("name")) for item in payload.get("models", []) if item.get("name")]
    return {
        "provider": "ollama",
        "available": True,
        "models": models,
        "reason": "",
    }
