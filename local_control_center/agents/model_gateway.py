"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.telemetry import record_model_call

from .repository import AgentsRepository

LOCAL_MODEL_PROVIDERS = {"ollama", "local_ollama"}
REMOTE_MODEL_PROVIDERS = {"openai", "openai_compatible", "openai_agents", "openrouter"}
REMOTE_PROVIDER_TYPES = {"api", "gateway"}
LOCAL_PROVIDER_TYPES = {"local"}


def real_provider_calls_enabled() -> bool:
    return os.environ.get("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false").lower() == "true"


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


def _runtime_type(account: dict[str, Any] | None, fallback: str | None = None) -> str:
    provider_type = str((account or {}).get("providerType") or "")
    if provider_type == "local":
        return "local"
    if provider_type == "gateway":
        return "gateway"
    if provider_type == "cli":
        return "cli"
    if provider_type == "manual":
        return "manual"
    return fallback or "api"


def _public_error(error: BaseException) -> str:
    return str(redact_secrets(f"{error.__class__.__name__}: {error}"))


def provider_instance(provider_id: str, *, connection: sqlite3.Connection):
    from .provider_accounts import ProviderAccountStore
    from .providers.anthropic_api import AnthropicAPIProvider
    from .providers.litellm_adapter import LiteLLMAdapter
    from .providers.nvidia_nim import NvidiaNimProvider
    from .providers.ollama import OllamaProvider
    from .providers.openai_api import OpenAIAPIProvider
    from .providers.openai_compatible import OpenAICompatibleProvider
    from .providers.openrouter import OpenRouterProvider
    from .runtime_provider_config import runtime_provider_configuration

    try:
        account = ProviderAccountStore(connection).get_provider_account(provider_id)
    except KeyError:
        account = {}
    runtime_configuration = runtime_provider_configuration(provider_id)
    base_url = (
        (runtime_configuration.value("baseUrl") if runtime_configuration else None)
        or account.get("baseUrl")
        or None
    )
    credential_ref = (
        (runtime_configuration.configured_env_ref("apiKey") if runtime_configuration else None)
        or account.get("credentialRef")
        or None
    )
    if provider_id == "nvidia_nim":
        return NvidiaNimProvider(
            connection=connection, base_url=base_url or "", credential_ref=credential_ref or ""
        )
    if provider_id in {"ollama", "local_ollama"}:
        return OllamaProvider(base_url=base_url)
    if provider_id in {"openai", "openai_api"}:
        return OpenAIAPIProvider(base_url=base_url, credential_ref=credential_ref or "")
    if provider_id == "anthropic_api":
        return AnthropicAPIProvider(base_url=base_url, credential_ref=credential_ref or None)
    if provider_id == "openrouter":
        return OpenRouterProvider(base_url=base_url, credential_ref=credential_ref or "")
    if provider_id == "litellm":
        return LiteLLMAdapter(base_url=base_url, credential_ref=credential_ref or "")
    return OpenAICompatibleProvider(provider_id=provider_id, base_url=base_url, credential_ref=credential_ref)


def _provider_usage_reported(raw_usage: dict[str, Any]) -> bool:
    reported_keys = {
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_eval_count",
        "eval_count",
    }
    return any(raw_usage.get(key) is not None for key in reported_keys)


class ModelGateway:
    def __init__(self, connection: sqlite3.Connection):
        self.repository = AgentsRepository(connection)

    def redact_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        return redact_secrets(metadata or {})

    def plan_model_call(
        self,
        *,
        model_policy_id: str | None = None,
        project_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        runtime_type: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        agent_run_id: str | None = None,
        estimated_cost_usd: float | None = None,
        budget_remaining_usd: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy: dict[str, Any] | None = None
        candidate: dict[str, Any] | None = None
        if model_policy_id:
            policy = self.repository.get_model_policy(model_policy_id)
            candidate = _select_candidate(policy)
            if candidate:
                provider = str(candidate["provider"])
                model = str(candidate["model"])
        if provider and model and candidate is None:
            candidate = {"provider": provider, "model": model}
        resolved_messages = messages or ([{"role": "user", "content": prompt}] if prompt else [])
        plan_status = "planned" if candidate else "blocked_policy"
        return {
            "status": plan_status,
            "projectId": project_id,
            "agentRunId": agent_run_id,
            "modelPolicyId": model_policy_id,
            "policy": policy,
            "candidate": candidate,
            "provider": provider,
            "model": model,
            "runtimeType": runtime_type,
            "messages": redact_secrets(resolved_messages),
            "request": {
                "model": model,
                "messages": resolved_messages,
                "temperature": temperature,
                "maxTokens": max_tokens,
            },
            "estimatedCostUsd": estimated_cost_usd,
            "budgetRemainingUsd": budget_remaining_usd,
            "metadata": self.redact_metadata(metadata),
        }

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
        policy: dict[str, Any] | None,
        candidate: dict[str, Any],
        estimated_cost_usd: float | None,
        budget_remaining_usd: float | None = None,
    ) -> dict[str, Any] | None:
        estimated_cost = float(estimated_cost_usd or 0)
        if (
            budget_remaining_usd is not None
            and estimated_cost_usd is not None
            and estimated_cost > float(budget_remaining_usd)
        ):
            return {
                "status": "blocked_budget",
                "provider": candidate["provider"],
                "model": candidate["model"],
                "reason": "budget_remaining_exceeded",
                "metadata": {
                    "reason": "budget_remaining_exceeded",
                    "budgetRemainingUsd": float(budget_remaining_usd),
                    "estimatedCostUsd": estimated_cost,
                },
            }
        if not policy:
            return None
        current_spend = self.repository.total_cost_usage(project_id=project_id, scope="model_call")
        max_cost = float(policy.get("maxCostUsd") or 0)
        if max_cost > 0 and current_spend + estimated_cost > max_cost:
            return {
                "status": "blocked_budget",
                "provider": candidate["provider"],
                "model": candidate["model"],
                "reason": "model_policy_budget_exceeded",
                "metadata": {
                    "reason": "model_policy_budget_exceeded",
                    "currentSpendUsd": current_spend,
                    "estimatedCostUsd": estimated_cost,
                    "maxCostUsd": max_cost,
                },
            }
        return None

    def execute_model_call(self, planned_call: dict[str, Any]) -> dict[str, Any]:
        from .providers.base import ModelRequest

        plan = redact_secrets(planned_call)
        candidate = planned_call.get("candidate")
        project_id = str(planned_call.get("projectId") or "model-gateway")
        model_policy_id = planned_call.get("modelPolicyId")
        agent_run_id = planned_call.get("agentRunId")
        if not candidate:
            model_call = self.record_model_usage(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider="unresolved",
                model="unresolved",
                status="blocked_policy",
                metadata={
                    "reason": "No provider candidate is allowed by this model policy.",
                    "plannedCall": plan,
                },
            )
            return {
                "status": "blocked_policy",
                "reason": "no_allowed_provider_candidate",
                "modelCall": model_call,
            }

        budget_block = self.block_if_budget_exceeded(
            project_id=project_id,
            policy=planned_call.get("policy"),
            candidate=candidate,
            estimated_cost_usd=planned_call.get("estimatedCostUsd"),
            budget_remaining_usd=planned_call.get("budgetRemainingUsd"),
        )
        if budget_block:
            model_call = self.record_model_usage(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider=str(candidate["provider"]),
                model=str(candidate["model"]),
                status="blocked_budget",
                metadata={**budget_block["metadata"], "plannedCall": plan},
            )
            return {**budget_block, "modelCall": model_call}

        provider_id = str(candidate["provider"])
        model = str(candidate["model"])
        runtime_type = str(planned_call.get("runtimeType") or "")
        configuration = self._provider_configuration(provider_id, runtime_type=runtime_type)
        if configuration["status"] != "configured":
            status = configuration["status"]
            model_call = self.record_model_usage(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider=provider_id,
                model=model,
                status=status,
                metadata={"reason": configuration["reason"], "plannedCall": plan},
            )
            return {
                "status": status,
                "provider": provider_id,
                "model": model,
                "reason": configuration["reason"],
                "modelCall": model_call,
            }

        request_payload = planned_call.get("request") or {}
        messages = request_payload.get("messages") or []
        start = time.monotonic()
        try:
            response = provider_instance(provider_id, connection=self.repository.connection).chat_completion(
                ModelRequest(
                    model=model,
                    messages=messages,
                    temperature=request_payload.get("temperature"),
                    maxTokens=request_payload.get("maxTokens"),
                )
            )
        except Exception as error:  # provider boundary: convert external failures into fail-closed state
            reason = f"provider_request_failed:{_public_error(error)}"
            model_call = self.record_model_usage(
                project_id=project_id,
                agent_run_id=agent_run_id,
                model_policy_id=model_policy_id,
                provider=provider_id,
                model=model,
                status="unavailable",
                metadata={"reason": reason, "plannedCall": plan},
            )
            return {
                "status": "unavailable",
                "provider": provider_id,
                "model": model,
                "reason": redact_secrets(reason),
                "modelCall": model_call,
            }

        latency_ms = int((time.monotonic() - start) * 1000)
        usage_result = self._record_successful_provider_usage(
            provider_id=provider_id,
            model=model,
            runtime_type=configuration["runtimeType"],
            response=response,
            planned_call=planned_call,
            latency_ms=latency_ms,
        )
        model_call = self.record_model_usage(
            project_id=project_id,
            agent_run_id=agent_run_id,
            model_policy_id=model_policy_id,
            provider=provider_id,
            model=model,
            status="completed",
            prompt_tokens=usage_result["promptTokens"],
            completion_tokens=usage_result["completionTokens"],
            cost_usd=float(usage_result["actualCostUsd"] or 0.0),
            metadata={
                "usageLedgerId": usage_result["usage"]["id"],
                "tokenStatus": usage_result["tokenStatus"],
                "costStatus": usage_result["costStatus"],
            },
        )
        return {
            "status": "completed",
            "provider": provider_id,
            "model": model,
            "content": response.content,
            "usage": usage_result["usage"],
            "modelCall": model_call,
            "tokenStatus": usage_result["tokenStatus"],
            "costStatus": usage_result["costStatus"],
        }

    def provider_health(self, provider_id: str) -> dict[str, Any]:
        configuration = self._provider_configuration(provider_id, runtime_type=None, for_health=True)
        if configuration["status"] != "configured":
            return {
                "providerId": provider_id,
                "status": configuration["status"],
                "healthStatus": configuration["status"],
                "message": configuration["reason"],
                "lastError": None,
                "models": [],
            }
        try:
            provider = provider_instance(provider_id, connection=self.repository.connection)
            health = provider.health_check().model_dump(by_alias=True)
            if health.get("status") == "not_available":
                health["status"] = "unavailable"
            health = redact_secrets(health)
            models = (
                [item.model for item in provider.list_models()] if health.get("status") == "available" else []
            )
            return {**health, "models": models}
        except Exception as error:
            return {
                "providerId": provider_id,
                "status": "unavailable",
                "healthStatus": "offline",
                "message": f"Provider health check failed: {_public_error(error)}",
                "lastError": _public_error(error),
                "models": [],
            }

    def _provider_configuration(
        self,
        provider_id: str,
        *,
        runtime_type: str | None,
        for_health: bool = False,
    ) -> dict[str, Any]:
        from .credentials import CredentialResolver
        from .provider_accounts import ProviderAccountStore
        from .runtime_provider_config import runtime_provider_configuration

        try:
            account = ProviderAccountStore(self.repository.connection).get_provider_account(provider_id)
        except KeyError:
            return {
                "status": "configuration_required",
                "reason": f"Provider account not found: {provider_id}",
            }
        if not account.get("enabled"):
            return {"status": "configuration_required", "reason": "Provider account is disabled."}
        resolved_runtime = runtime_type or _runtime_type(account)
        provider_type = str(account.get("providerType") or "")
        if resolved_runtime in {"cli", "manual"} or provider_type in {"cli", "manual"}:
            return {
                "status": "blocked",
                "reason": "CLI/manual model execution must be launched through policy-approved runtime sessions.",
                "runtimeType": resolved_runtime,
            }
        if provider_type in REMOTE_PROVIDER_TYPES:
            runtime_configuration = runtime_provider_configuration(provider_id)
            credential_ref = str(
                (
                    runtime_configuration.configured_env_ref("apiKey")
                    if runtime_configuration and runtime_configuration.configured
                    else None
                )
                or account.get("credentialRef")
                or ""
            )
            if not credential_ref:
                return {
                    "status": "configuration_required",
                    "reason": "Credential ref is required for remote provider execution.",
                }
            credential = CredentialResolver().resolve(credential_ref, fetch=not for_health)
            if (for_health and credential.status == "configured") or (
                for_health and credential.status == "unverified"
            ):
                pass
            elif not credential.configured:
                return {
                    "status": "configuration_required",
                    "reason": redact_secrets(
                        f"Credential ref {credential_ref} is {credential.status}. {credential.message}".strip()
                    ),
                }
            provider = provider_instance(provider_id, connection=self.repository.connection)
            if not getattr(provider, "base_url", ""):
                return {"status": "configuration_required", "reason": "Provider base URL is not configured."}
            if not real_provider_calls_enabled():
                return {
                    "status": "blocked",
                    "reason": "Real provider calls are disabled by AIDO_ENABLE_REAL_PROVIDER_CALLS=false.",
                }
        if provider_type in LOCAL_PROVIDER_TYPES and provider_id not in {"ollama", "local_ollama"}:
            return {
                "status": "configuration_required",
                "reason": f"Unsupported local model provider: {provider_id}",
            }
        return {"status": "configured", "reason": "", "runtimeType": resolved_runtime}

    def _record_successful_provider_usage(
        self,
        *,
        provider_id: str,
        model: str,
        runtime_type: str,
        response: Any,
        planned_call: dict[str, Any],
        latency_ms: int,
    ) -> dict[str, Any]:
        from .pricing_catalog import PricingCatalog
        from .usage_ledger import UsageLedger

        raw_usage = redact_secrets(response.usage.raw_usage or {})
        token_status = "actual" if _provider_usage_reported(raw_usage) else "unknown"
        pricing = PricingCatalog(self.repository.connection).estimate(
            provider_id=provider_id,
            model=model,
            input_tokens=response.usage.input_tokens,
            cached_input_tokens=response.usage.cached_input_tokens,
            output_tokens=response.usage.output_tokens,
            reasoning_tokens=response.usage.reasoning_tokens,
        )
        actual_cost_usd: float | None = None
        cost_status = "unknown"
        if token_status == "actual" and pricing["priceKnown"]:
            actual_cost_usd = pricing["estimatedCostUsd"]
            cost_status = "free" if pricing["freeTier"] else "actual"
        enriched_usage = redact_secrets(
            {
                **raw_usage,
                "usage_source": "provider" if token_status == "actual" else "unknown",
                "token_status": token_status,
                "cost_status": cost_status,
                "pricing_source": pricing["source"],
                "pricing_staleness": pricing["staleness"],
            }
        )
        usage = UsageLedger(self.repository.connection).record_usage(
            provider_id=provider_id,
            model=model,
            runtime_type=runtime_type,
            agent_id=planned_call.get("agentId"),
            role=planned_call.get("role"),
            workflow_run_id=planned_call.get("workflowRunId"),
            workflow_step_id=planned_call.get("workflowStepId"),
            job_id=planned_call.get("jobId"),
            task_id=planned_call.get("taskId"),
            input_tokens=response.usage.input_tokens if token_status == "actual" else 0,
            cached_input_tokens=response.usage.cached_input_tokens if token_status == "actual" else 0,
            output_tokens=response.usage.output_tokens if token_status == "actual" else 0,
            reasoning_tokens=response.usage.reasoning_tokens if token_status == "actual" else 0,
            tool_tokens=response.usage.tool_tokens if token_status == "actual" else 0,
            estimated_cost_usd=None,
            actual_cost_usd=actual_cost_usd,
            latency_ms=latency_ms,
            raw_usage=enriched_usage,
            usage_source="actual" if token_status == "actual" else "unknown",
        )
        return {
            "usage": usage,
            "tokenStatus": token_status,
            "costStatus": cost_status,
            "actualCostUsd": actual_cost_usd,
            "promptTokens": response.usage.input_tokens if token_status == "actual" else 0,
            "completionTokens": response.usage.output_tokens if token_status == "actual" else 0,
        }

    def prepare_model_call(
        self,
        *,
        project_id: str,
        model_policy_id: str,
        estimated_cost_usd: float | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        agent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sanitized_metadata = self.redact_metadata(metadata)
        plan = self.plan_model_call(
            project_id=project_id,
            model_policy_id=model_policy_id,
            agent_run_id=agent_run_id,
            estimated_cost_usd=estimated_cost_usd,
            metadata=metadata,
        )
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
                metadata={
                    "reason": "No provider candidate is allowed by this model policy.",
                    **sanitized_metadata,
                },
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
                metadata={**budget_block["metadata"], **sanitized_metadata},
            )
            return {**budget_block, "modelCall": model_call}

        model_call = self.record_model_usage(
            project_id=project_id,
            agent_run_id=agent_run_id,
            model_policy_id=model_policy_id,
            provider=str(candidate["provider"]),
            model=str(candidate["model"]),
            status="planned",
            metadata={
                "execution": "not_started",
                "tokenStatus": "unknown",
                "costStatus": "unknown",
                "estimatedCostUsd": float(estimated_cost_usd) if estimated_cost_usd is not None else None,
                **sanitized_metadata,
            },
        )
        return {
            "status": "planned",
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
        return {"provider": "ollama", "available": False, "models": [], "reason": _public_error(error)}

    models = [str(item.get("name")) for item in payload.get("models", []) if item.get("name")]
    status = "available"
    return {
        "provider": "ollama",
        "available": status == "available",
        "models": models,
        "reason": "",
    }
