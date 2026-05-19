from __future__ import annotations

import json
import re
import shutil
import sqlite3
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from local_control_center.shared.telemetry import record_model_call

from .repository import AgentsRepository


RUNTIME_MODES = ["api", "cli", "ollama", "hybrid", "manual", "internal_mock"]
LOCAL_MODEL_PROVIDERS = {"internal_mock", "ollama", "local_ollama"}
REMOTE_MODEL_PROVIDERS = {"openai", "openai_compatible", "openai_agents", "openrouter"}
SECRET_VALUE_PATTERN = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]+)", re.I)
SECRET_KEY_PATTERN = re.compile(r"(api[_-]?key|authorization|credential|secret|token)", re.I)


def redact_secrets(value: Any, *, key: str = "") -> Any:
    if SECRET_KEY_PATTERN.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {item_key: redact_secrets(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_PATTERN.sub("[redacted]", value)
    return value


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
        policy = self.repository.get_model_policy(model_policy_id)
        sanitized_metadata = redact_secrets(metadata or {})
        candidate = _select_candidate(policy)
        if not candidate:
            model_call = self.repository.record_model_call(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider="unresolved",
                model="unresolved",
                status="blocked_policy",
                metadata={"reason": "No provider candidate is allowed by this model policy.", **sanitized_metadata},
            )
            record_model_call(self.repository.connection, model_call)
            return {"status": "blocked_policy", "modelCall": model_call}

        current_spend = self.repository.total_cost_usage(project_id=project_id, scope="model_call")
        max_cost = float(policy.get("maxCostUsd") or 0)
        estimated_cost = float(estimated_cost_usd or 0)
        if max_cost > 0 and current_spend + estimated_cost > max_cost:
            model_call = self.repository.record_model_call(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider=str(candidate["provider"]),
                model=str(candidate["model"]),
                status="blocked_budget",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                metadata={
                    "reason": "Estimated model call cost exceeds model policy budget.",
                    "currentSpendUsd": current_spend,
                    "estimatedCostUsd": estimated_cost,
                    "maxCostUsd": max_cost,
                    **sanitized_metadata,
                },
            )
            record_model_call(self.repository.connection, model_call)
            return {
                "status": "blocked_budget",
                "provider": candidate["provider"],
                "model": candidate["model"],
                "modelCall": model_call,
            }

        model_call = self.repository.record_model_call(
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
        record_model_call(self.repository.connection, model_call)
        return {
            "status": "prepared",
            "provider": candidate["provider"],
            "model": candidate["model"],
            "modelCall": model_call,
        }


def ollama_status() -> dict[str, Any]:
    try:
        request = Request("http://127.0.0.1:11434/api/tags", method="GET")
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


def runtime_provider_status() -> dict[str, Any]:
    return {
        "runtimeModes": RUNTIME_MODES,
        "ollama": ollama_status(),
        "cli": {
            "provider": "cli",
            "available": bool(shutil.which("codex") or shutil.which("claude")),
            "adapters": {
                "cli_codex": bool(shutil.which("codex")),
                "cli_claude": bool(shutil.which("claude")),
            },
        },
        "api": {"provider": "api", "available": True, "adapters": ["openai_compatible", "openrouter", "openai_agents"]},
    }
