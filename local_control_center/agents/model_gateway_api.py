from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.shared.event_bus import EventBus

from .model_router import ModelRouter, RoutingRequest
from .provider_accounts import ProviderAccountStore
from .providers.anthropic_api import AnthropicAPIProvider
from .providers.nvidia_nim import NvidiaNimProvider
from .providers.ollama import OllamaProvider
from .providers.openai_api import OpenAIAPIProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .providers.openrouter import OpenRouterProvider
from .providers.litellm_adapter import LiteLLMAdapter
from .routing_profiles import RoutingProfileStore
from .runtime_registry import RuntimeRegistry
from .usage_ledger import UsageLedger


def _provider_instance(provider_id: str, *, connection: Any, mock: bool = True):
    if provider_id == "nvidia_nim":
        return NvidiaNimProvider(connection=connection, mock=mock)
    if provider_id == "ollama":
        return OllamaProvider(mock=mock)
    if provider_id == "openai_api":
        return OpenAIAPIProvider(mock=mock)
    if provider_id == "anthropic_api":
        return AnthropicAPIProvider(mock=mock)
    if provider_id == "openrouter":
        return OpenRouterProvider(mock=mock)
    if provider_id == "litellm":
        return LiteLLMAdapter(mock=mock)
    return OpenAICompatibleProvider(provider_id=provider_id, mock=mock)


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1/model-gateway", tags=["model-gateway"])

    def providers() -> ProviderAccountStore:
        return ProviderAccountStore(platform.connection)

    def routing() -> RoutingProfileStore:
        return RoutingProfileStore(platform.connection)

    def usage() -> UsageLedger:
        return UsageLedger(platform.connection)

    def audit(action: str, target: str, payload: dict[str, Any] | None = None) -> None:
        EventBus(platform.connection).record_audit(action=action, target=target, payload=payload or {})

    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        provider_rows = providers().list_provider_accounts()
        usage_summary = usage().summary()
        limit_rows = routing().list_provider_limits()
        cli_sessions = routing().list_cli_sessions()
        return {
            "overview": {
                "providersEnabled": sum(1 for item in provider_rows if item["enabled"]),
                "apiProviders": sum(1 for item in provider_rows if item["providerType"] in {"api", "gateway"}),
                "cliRuntimes": sum(1 for item in provider_rows if item["providerType"] == "cli"),
                "localProviders": sum(1 for item in provider_rows if item["providerType"] == "local"),
                "healthy": sum(1 for item in provider_rows if item["healthStatus"] == "healthy"),
                "degraded": sum(1 for item in provider_rows if item["healthStatus"] == "degraded"),
                "offline": sum(1 for item in provider_rows if item["healthStatus"] in {"offline", "misconfigured"}),
                "totalTokensToday": usage_summary["totalTokens"],
                "estimatedCostToday": usage_summary["estimatedCostUsd"],
                "actualCostToday": usage_summary["actualCostUsd"],
                "pendingModelApprovals": 0,
                "providersInCooldown": sum(1 for item in limit_rows if item.get("cooldownUntil")),
                "activeCliSessions": sum(1 for item in cli_sessions if item["status"] == "running"),
            }
        }

    @router.get("/providers")
    async def list_providers() -> dict[str, Any]:
        return {"providers": providers().list_provider_accounts()}

    @router.post("/providers", status_code=201)
    async def create_provider(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        provider = providers().upsert_provider_account(body)
        audit("model_gateway.provider.upserted", provider["providerId"], {"providerId": provider["providerId"]})
        return {"provider": provider}

    @router.get("/providers/{provider_id}")
    async def get_provider(provider_id: str) -> dict[str, Any]:
        try:
            return {"provider": providers().get_provider_account(provider_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.patch("/providers/{provider_id}")
    async def patch_provider(provider_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            provider = providers().patch_provider_account(provider_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        audit("model_gateway.provider.updated", provider_id, {"providerId": provider_id})
        return {"provider": provider}

    @router.post("/providers/{provider_id}/health-check")
    async def provider_health_check(provider_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            providers().get_provider_account(provider_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        health = _provider_instance(provider_id, connection=platform.connection, mock=True).health_check().model_dump(by_alias=True)
        providers().record_health_check(provider_id=provider_id, status=health["healthStatus"], payload=health)
        audit("model_gateway.provider.health_checked", provider_id, health)
        return {"health": health}

    @router.post("/providers/{provider_id}/discover-models")
    async def discover_models(provider_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            providers().get_provider_account(provider_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        discovered = [
            item.model_dump(by_alias=True)
            for item in _provider_instance(provider_id, connection=platform.connection, mock=True).list_models()
        ]
        stored = [providers().upsert_model({**item, "providerId": provider_id, "enabled": True}) for item in discovered]
        audit("model_gateway.provider.models_discovered", provider_id, {"count": len(stored)})
        return {"models": stored}

    @router.get("/models")
    async def list_models() -> dict[str, Any]:
        return {"models": providers().list_models()}

    @router.post("/models", status_code=201)
    async def create_model(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        model = providers().upsert_model(body)
        audit("model_gateway.model.upserted", model["id"], {"providerId": model["providerId"], "model": model["model"]})
        return {"model": model}

    @router.patch("/models/{model_id:path}")
    async def patch_model(model_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            model = providers().patch_model(model_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        audit("model_gateway.model.updated", model_id, {"modelId": model_id})
        return {"model": model}

    @router.get("/routing-profiles")
    async def list_routing_profiles() -> dict[str, Any]:
        return {"routingProfiles": routing().list_routing_profiles()}

    @router.post("/routing-profiles", status_code=201)
    async def create_routing_profile(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        profile = routing().upsert_routing_profile(body)
        return {"routingProfile": profile}

    @router.patch("/routing-profiles/{profile_id}")
    async def patch_routing_profile(profile_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            profile = routing().patch_routing_profile(profile_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"routingProfile": profile}

    @router.get("/role-policies")
    async def list_role_policies() -> dict[str, Any]:
        return {"rolePolicies": routing().list_role_policies()}

    @router.post("/role-policies", status_code=201)
    async def create_role_policy(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        policy = routing().upsert_role_policy(body)
        return {"rolePolicy": policy}

    @router.patch("/role-policies/{policy_id}")
    async def patch_role_policy(policy_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            policy = routing().patch_role_policy(policy_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"rolePolicy": policy}

    @router.post("/route/preview")
    async def route_preview(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        result = ModelRouter(platform.connection).preview(RoutingRequest(**body), record=True)
        audit("model_gateway.route.previewed", body.get("role", "unknown"), {"selected": result.get("selected")})
        return result

    @router.post("/route/execute-mock")
    async def route_execute_mock(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        result = ModelRouter(platform.connection).preview(RoutingRequest(**body), record=True)
        selected = result.get("selected") or {"provider": "unresolved", "model": "unresolved", "runtime": "manual"}
        usage_record = usage().record_usage(
            provider_id=selected["provider"],
            model=selected["model"],
            runtime_type=selected["runtime"],
            role=body.get("role"),
            task_id=body.get("taskId"),
            input_tokens=int(body.get("contextTokensEstimate") or 0),
            output_tokens=0,
            estimated_cost_usd=result.get("estimatedCostUsd"),
            raw_usage={"usage_source": "estimated", "mock": True},
        )
        audit("model_gateway.route.execute_mock", selected["provider"], {"usageLedgerId": usage_record["id"]})
        return {"routing": result, "usage": usage_record}

    @router.get("/usage-ledger")
    async def list_usage_ledger() -> dict[str, Any]:
        return {"usageLedger": usage().list_usage()}

    @router.get("/usage-ledger/summary")
    async def usage_summary() -> dict[str, Any]:
        return {"summary": usage().summary()}

    @router.get("/routing-decisions")
    async def list_routing_decisions() -> dict[str, Any]:
        return {"routingDecisions": routing().list_routing_decisions()}

    @router.get("/provider-limits")
    async def list_provider_limits() -> dict[str, Any]:
        return {"providerLimits": routing().list_provider_limits()}

    @router.patch("/provider-limits/{limit_id}")
    async def patch_provider_limit(limit_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return {"providerLimit": routing().patch_provider_limit(limit_id, body)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/budget-rules")
    async def list_budget_rules() -> dict[str, Any]:
        return {"budgetRules": routing().list_budget_rules()}

    @router.post("/budget-rules", status_code=201)
    async def create_budget_rule(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        return {"budgetRule": routing().upsert_budget_rule(body)}

    @router.patch("/budget-rules/{rule_id}")
    async def patch_budget_rule(rule_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return {"budgetRule": routing().patch_budget_rule(rule_id, body)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/cli-runtimes")
    async def list_cli_runtimes() -> dict[str, Any]:
        return {"cliRuntimes": RuntimeRegistry().list_runtimes()}

    @router.post("/cli-runtimes/{runtime_id}/detect")
    async def detect_cli_runtime(runtime_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return {"detection": RuntimeRegistry().detect(runtime_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/cli-runtimes/{runtime_id}/health-check")
    async def health_cli_runtime(runtime_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        try:
            return {"health": RuntimeRegistry().health_check(runtime_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/cli-sessions")
    async def list_cli_sessions() -> dict[str, Any]:
        return {"cliSessions": routing().list_cli_sessions()}

    @router.get("/cli-sessions/{session_id}")
    async def get_cli_session(session_id: str) -> dict[str, Any]:
        try:
            return {"cliSession": routing().get_cli_session(session_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
