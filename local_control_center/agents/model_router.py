"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .budget_rules import BudgetRuleEvaluator
from .model_benchmarks import ModelBenchmarkStore
from .pricing_catalog import PricingCatalog
from .provider_accounts import ProviderAccountStore
from .quota_manager import QuotaManager
from .routing_profiles import RoutingProfileStore

REMOTE_PROVIDER_TYPES = {"api", "gateway"}
LOCAL_PROVIDER_TYPES = {"local"}
CLI_PROVIDER_TYPES = {"cli"}
MIN_BENCHMARK_SAMPLES = 5
UNKNOWN_REMOTE_COST_NOT_ALLOWED = "unknown_remote_cost_not_allowed"
UNKNOWN_REMOTE_COST_REQUIRES_APPROVAL = "unknown_remote_cost_requires_approval"


class RoutingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = "developer"
    task_type: str = Field(default="task", alias="taskType")
    mode: str = "balanced_best_value"
    risk_level: str = Field(default="medium", alias="riskLevel")
    context_tokens_estimate: int = Field(default=0, alias="contextTokensEstimate")
    requires_tools: bool = Field(default=False, alias="requiresTools")
    requires_code_edit: bool = Field(default=False, alias="requiresCodeEdit")
    requires_search: bool = Field(default=False, alias="requiresSearch")
    requires_reasoning: bool = Field(default=False, alias="requiresReasoning")
    requires_vision: bool = Field(default=False, alias="requiresVision")
    requires_json: bool = Field(default=False, alias="requiresJson")
    privacy_level: str = Field(default="remote_allowed", alias="privacyLevel")
    budget_remaining_usd: float | None = Field(default=None, alias="budgetRemainingUsd")
    manual_provider: str | None = Field(default=None, alias="manualProvider")
    manual_model: str | None = Field(default=None, alias="manualModel")
    manual_runtime: str | None = Field(default=None, alias="manualRuntime")
    allow_fallback: bool = Field(default=True, alias="allowFallback")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    job_id: str | None = Field(default=None, alias="jobId")
    task_id: str | None = Field(default=None, alias="taskId")


class ModelRouter:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.providers = ProviderAccountStore(connection)
        self.routes = RoutingProfileStore(connection)
        self.pricing = PricingCatalog(connection)
        self.quota = QuotaManager(connection)
        self.budgets = BudgetRuleEvaluator(connection)
        self.benchmarks = ModelBenchmarkStore(connection)

    def preview(self, request: RoutingRequest, *, record: bool = True) -> dict[str, Any]:
        providers = {item["providerId"]: item for item in self.providers.list_provider_accounts()}
        models = self.providers.list_models()
        try:
            role_policy = self.routes.get_role_policy(request.role)
        except KeyError:
            role_policy = self.routes.get_role_policy("developer")

        preferred = role_policy.get("preferred", [])
        fallback = role_policy.get("fallback", [])
        escalation = role_policy.get("escalation", [])
        if request.mode in {"max_performance", "manual_by_profile"}:
            ordered_preferences = [*escalation, *preferred, *fallback]
        else:
            ordered_preferences = [*preferred, *fallback, *escalation]
        benchmark_index = self._benchmark_index(request.role)

        preferred_rank = {
            (item.get("provider"), item.get("model", "auto")): index
            for index, item in enumerate(ordered_preferences)
            if isinstance(item, dict)
        }
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        selected_budget_result: dict[str, Any] | None = None
        selected_quota_result: dict[str, Any] | None = None
        last_budget_result: dict[str, Any] = {
            "allowed": True,
            "action": "allow",
            "reason": "within_budget",
            "requiresApproval": False,
        }
        last_quota_result: dict[str, Any] = {
            "allowed": True,
            "reason": "no_limit",
            "quotaPressure": 0.0,
        }

        for model in models:
            provider = providers.get(model["providerId"])
            if not provider:
                rejected.append(
                    {"provider": model["providerId"], "model": model["model"], "reason": "missing_provider"}
                )
                continue
            reason = self._hard_reject_reason(request, role_policy, provider, model)
            if reason:
                rejected.append(
                    {
                        "provider": provider["providerId"],
                        "model": model["model"],
                        "runtime": self._runtime_type(provider),
                        "reason": reason,
                    }
                )
                continue
            estimate = self._estimate(request, provider, model)
            unknown_cost_policy = self._unknown_cost_policy(role_policy, provider, estimate)
            if unknown_cost_policy and unknown_cost_policy["action"] == "reject":
                rejected.append(
                    {
                        "provider": provider["providerId"],
                        "model": model["model"],
                        "runtime": self._runtime_type(provider),
                        "reason": unknown_cost_policy["reason"],
                    }
                )
                continue
            budget = self.budgets.evaluate(
                role=request.role,
                provider_id=provider["providerId"],
                agent_id=request.agent_id,
                workflow_run_id=request.workflow_run_id,
                estimated_cost_usd=estimate["cost"],
                estimated_tokens=request.context_tokens_estimate,
                budget_remaining_usd=request.budget_remaining_usd,
            )
            last_budget_result = budget.as_dict()
            if not budget.allowed:
                rejected.append(
                    {
                        "provider": provider["providerId"],
                        "model": model["model"],
                        "runtime": self._runtime_type(provider),
                        "reason": budget.reason,
                    }
                )
                continue
            quota = self.quota.check(
                provider_id=provider["providerId"],
                model=model["model"],
                request_tokens=request.context_tokens_estimate,
            )
            last_quota_result = quota.as_dict()
            if not quota.allowed:
                rejected.append(
                    {
                        "provider": provider["providerId"],
                        "model": model["model"],
                        "runtime": self._runtime_type(provider),
                        "reason": quota.reason,
                    }
                )
                continue
            score_breakdown = self._score(
                request,
                role_policy,
                provider,
                model,
                preferred_rank,
                estimate["cost"],
                estimate,
                self._benchmark_for(benchmark_index, provider["providerId"], model["model"]),
                quota.quota_pressure,
            )
            candidate = {
                "provider": provider["providerId"],
                "model": model["model"],
                "runtime": self._runtime_type(provider),
                "effort": self._effort_for(request, provider, model, ordered_preferences),
                "estimatedCostUsd": estimate["cost"],
                "pricingSource": estimate["source"],
                "pricingStaleness": estimate["staleness"],
                "priceKnown": estimate["priceKnown"],
                "freeTier": estimate["freeTier"],
                "score": score_breakdown["score"],
                "scoreBreakdown": score_breakdown,
                "_unknownCostPolicy": unknown_cost_policy,
                "_budgetResult": budget.as_dict(),
                "_quotaResult": quota.as_dict(),
            }
            candidates.append(candidate)

        if request.mode == "manual_by_profile" and request.manual_provider:
            selected = next(
                (
                    item
                    for item in candidates
                    if item["provider"] == request.manual_provider
                    and (not request.manual_model or item["model"] == request.manual_model)
                    and (not request.manual_runtime or item["runtime"] == request.manual_runtime)
                ),
                None,
            )
        if selected is None and candidates:
            selected = sorted(candidates, key=lambda item: item["score"], reverse=True)[0]
        if selected:
            selected_budget_result = selected.get("_budgetResult") or last_budget_result
            selected_quota_result = selected.get("_quotaResult") or last_quota_result

        estimated_cost = selected.get("estimatedCostUsd") if selected else None
        selected_unknown_cost_policy = selected.get("_unknownCostPolicy") if selected else None
        approval_threshold = role_policy.get("requiresApprovalOverUsd")
        if approval_threshold is None:
            approval_threshold = role_policy.get("maxCostPerTaskUsd")
        requires_approval = bool(
            estimated_cost is not None
            and approval_threshold is not None
            and float(approval_threshold) > 0
            and float(estimated_cost) > float(approval_threshold)
        )
        if (
            selected
            and selected.get("effort") in {"xhigh", "max"}
            and role_policy.get("requiresApprovalForReasoningMax")
        ):
            requires_approval = True
        if selected_budget_result and selected_budget_result.get("requiresApproval"):
            requires_approval = True
        if selected_unknown_cost_policy and selected_unknown_cost_policy.get("action") == "require_approval":
            requires_approval = True

        result = {
            "selected": {
                "provider": selected["provider"],
                "model": selected["model"],
                "runtime": selected["runtime"],
                "effort": selected.get("effort"),
            }
            if selected
            else None,
            "estimatedCostUsd": estimated_cost,
            "estimatedTokens": request.context_tokens_estimate,
            "decisionReason": self._decision_reason(request, selected),
            "candidates": candidates,
            "rejected": rejected,
            "scoreBreakdown": selected.get("scoreBreakdown", {}) if selected else {},
            "policyResult": {
                "requiresApproval": requires_approval,
                "rolePolicyId": role_policy["id"],
                "maxCostPerTaskUsd": role_policy.get("maxCostPerTaskUsd"),
                "allowUnknownCost": bool(role_policy.get("allowUnknownCost", True)),
                "requireApprovalForUnknownCost": bool(role_policy.get("requireApprovalForUnknownCost", True)),
                "unknownCostPolicy": selected_unknown_cost_policy
                or {"action": "not_applicable", "reason": "cost_known_or_not_remote"},
            },
            "budgetResult": selected_budget_result or last_budget_result,
            "quotaResult": selected_quota_result or last_quota_result,
        }
        result["policyResult"]["budgetResult"] = result["budgetResult"]
        result["policyResult"]["quotaResult"] = result["quotaResult"]
        if record:
            self.routes.record_routing_decision(
                {
                    "role": request.role,
                    "taskType": request.task_type,
                    "mode": request.mode,
                    "selectedProvider": selected["provider"] if selected else None,
                    "selectedModel": selected["model"] if selected else None,
                    "selectedRuntime": selected["runtime"] if selected else None,
                    "selectedEffort": selected.get("effort") if selected else None,
                    "workflowRunId": request.workflow_run_id,
                    "workflowStepId": request.workflow_step_id,
                    "agentId": request.agent_id,
                    "jobId": request.job_id,
                    "taskId": request.task_id,
                    "estimatedCostUsd": estimated_cost,
                    "estimatedTokens": request.context_tokens_estimate,
                    "candidates": candidates,
                    "rejected": rejected,
                    "decisionReason": result["decisionReason"],
                    "scoreBreakdown": result["scoreBreakdown"],
                    "policyResult": result["policyResult"],
                }
            )
        return result

    def _hard_reject_reason(
        self,
        request: RoutingRequest,
        role_policy: dict[str, Any],
        provider: dict[str, Any],
        model: dict[str, Any],
    ) -> str | None:
        provider_type = provider["providerType"]
        runtime_type = self._runtime_type(provider)
        if self._blocked_by_role_policy(role_policy.get("blocked") or [], provider, model):
            return "role_blocks_candidate"
        if provider_type == "manual" and request.mode != "manual_by_profile":
            return "manual_requires_manual_mode"
        if not provider["enabled"]:
            return "provider_disabled"
        if not model["enabled"]:
            return "model_disabled"
        if provider_type != "manual" and not provider.get("lastHealthCheckAt"):
            return "provider_healthcheck_required"
        if provider["healthStatus"] != "healthy":
            return "provider_unhealthy"
        if (
            request.context_tokens_estimate
            and model["contextWindow"]
            and request.context_tokens_estimate > model["contextWindow"]
        ):
            return "context_window_too_small"
        if role_policy.get("maxTokensPerRun") and request.context_tokens_estimate > int(
            role_policy["maxTokensPerRun"]
        ):
            return "role_token_limit_exceeded"
        if request.requires_vision and not model["supportsVision"]:
            return "vision_not_supported"
        if request.requires_json and not model["supportsJson"]:
            return "json_not_supported"
        if request.requires_reasoning and not model["supportsReasoning"] and runtime_type != "cli":
            return "reasoning_not_supported"
        if request.requires_tools and runtime_type == "api" and not model["supportsTools"]:
            return "tools_not_supported"
        if request.requires_code_edit and runtime_type not in {"cli", "manual"}:
            return "code_edit_requires_cli_runtime"
        if provider_type in REMOTE_PROVIDER_TYPES and not role_policy.get("allowRemote", True):
            return "role_blocks_remote"
        if provider_type in LOCAL_PROVIDER_TYPES and not role_policy.get("allowLocal", True):
            return "role_blocks_local"
        if provider_type in CLI_PROVIDER_TYPES and not role_policy.get("allowCli", True):
            return "role_blocks_cli"
        if runtime_type == "api" and not role_policy.get("allowApi", True):
            return "role_blocks_api"
        if (
            request.mode == "local_private" or request.privacy_level == "local_private"
        ) and provider_type in REMOTE_PROVIDER_TYPES:
            return "privacy_blocks_remote"
        if (
            request.mode == "manual_by_profile"
            and request.manual_provider
            and provider["providerId"] != request.manual_provider
        ):
            return "manual_provider_mismatch"
        return None

    def _blocked_by_role_policy(
        self,
        blocked: list[dict[str, Any]],
        provider: dict[str, Any],
        model: dict[str, Any],
    ) -> bool:
        for item in blocked:
            item_provider = item.get("provider")
            item_model = item.get("model")
            if item_provider not in {None, "*", provider["providerId"]}:
                continue
            if item_model not in {None, "*", "auto", model["model"]}:
                continue
            return True
        return False

    def _runtime_type(self, provider: dict[str, Any]) -> str:
        provider_type = provider["providerType"]
        if provider_type == "cli":
            return "cli"
        if provider_type == "local":
            return "local"
        if provider_type == "manual":
            return "manual"
        if provider_type == "gateway":
            return "gateway"
        return "api"

    def _estimate(
        self, request: RoutingRequest, provider: dict[str, Any], model: dict[str, Any]
    ) -> dict[str, Any]:
        input_tokens = request.context_tokens_estimate
        output_tokens = min(max(int(input_tokens * 0.1), 512), model["maxOutputTokens"] or 4096)
        reasoning_tokens = int(output_tokens * 0.5) if request.requires_reasoning else 0
        pricing = self.pricing.estimate(
            provider_id=provider["providerId"],
            model=model["model"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        return {
            "cost": pricing["estimatedCostUsd"],
            "source": pricing["source"],
            "staleness": pricing["staleness"],
            "priceKnown": pricing["priceKnown"],
            "freeTier": pricing["freeTier"],
        }

    def _unknown_cost_policy(
        self,
        role_policy: dict[str, Any],
        provider: dict[str, Any],
        estimate: dict[str, Any],
    ) -> dict[str, str] | None:
        if estimate.get("cost") is not None or provider["providerType"] not in REMOTE_PROVIDER_TYPES:
            return None
        policy = {
            "provider": provider["providerId"],
            "runtime": self._runtime_type(provider),
        }
        if not role_policy.get("allowUnknownCost", True):
            return {
                **policy,
                "action": "reject",
                "reason": UNKNOWN_REMOTE_COST_NOT_ALLOWED,
            }
        if role_policy.get("requireApprovalForUnknownCost", True):
            return {
                **policy,
                "action": "require_approval",
                "reason": UNKNOWN_REMOTE_COST_REQUIRES_APPROVAL,
            }
        return {
            **policy,
            "action": "allow",
            "reason": "unknown_remote_cost_allowed_by_policy",
        }

    def _score(
        self,
        request: RoutingRequest,
        role_policy: dict[str, Any],
        provider: dict[str, Any],
        model: dict[str, Any],
        preferred_rank: dict[tuple[Any, Any], int],
        estimated_cost: float | None,
        estimate: dict[str, Any],
        benchmark: dict[str, Any] | None,
        quota_pressure: float = 0.0,
    ) -> dict[str, float]:
        runtime = self._runtime_type(provider)
        capability_score = 1.0
        if request.requires_code_edit and runtime == "cli":
            capability_score += 0.35
        if request.requires_search and provider["providerId"] == "nvidia_nim":
            capability_score += 0.15
        rank = min(
            preferred_rank.get((provider["providerId"], model["model"]), 999),
            preferred_rank.get((provider["providerId"], "auto"), 999),
            preferred_rank.get((provider["providerId"], "auto_best_available"), 999),
            preferred_rank.get((provider["providerId"], "gpt-5.5"), 999),
        )
        role_fit_score = max(0.0, 1.0 - (rank * 0.12)) if rank != 999 else 0.45
        reliability_score = 1.0 if provider["healthStatus"] == "healthy" else 0.65
        context_fit_score = (
            1.0
            if not model["contextWindow"]
            else min(1.0, model["contextWindow"] / max(request.context_tokens_estimate, 1))
        )
        tool_fit_score = (
            1.0 if not request.requires_tools or runtime == "cli" or model["supportsTools"] else 0.2
        )
        if estimate.get("freeTier"):
            cost_penalty = 0.0
        elif estimated_cost is None:
            cost_penalty = 1.0
        else:
            cost_penalty = min(
                estimated_cost / max(float(role_policy.get("maxCostPerTaskUsd") or 1), 0.01), 3.0
            )
        latency_penalty = 0.1 if runtime == "cli" else 0.0
        privacy_penalty = 0.0
        if request.privacy_level == "sensitive" and provider["providerType"] in REMOTE_PROVIDER_TYPES:
            privacy_penalty = 0.5
        score = (
            capability_score * 0.35
            + role_fit_score * 0.25
            + reliability_score * 0.15
            + context_fit_score * 0.10
            + tool_fit_score * 0.10
            - cost_penalty * 0.25
            - quota_pressure * 0.15
            - latency_penalty * 0.05
            - privacy_penalty * 0.20
        )
        if request.mode == "free_first" and model["freeTier"]:
            score += 0.35
        if request.mode == "max_performance" and model["supportsReasoning"]:
            score += 0.2
        if runtime == "cli" and request.role in {"developer", "technical_lead"}:
            score += 0.2
        benchmark_breakdown = self._benchmark_score(benchmark)
        score += benchmark_breakdown["benchmarkContribution"]
        return {
            "score": round(score, 6),
            "capabilityScore": capability_score,
            "roleFitScore": role_fit_score,
            "reliabilityScore": reliability_score,
            "contextFitScore": context_fit_score,
            "toolFitScore": tool_fit_score,
            "costPenalty": cost_penalty,
            "quotaPressure": quota_pressure,
            "latencyPenalty": latency_penalty,
            "privacyPenalty": privacy_penalty,
            "priceKnown": 1.0 if estimate.get("priceKnown") else 0.0,
            "freeTier": 1.0 if estimate.get("freeTier") else 0.0,
            **benchmark_breakdown,
        }

    def _benchmark_index(self, role: str) -> dict[tuple[str, str, str], dict[str, Any]]:
        index: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in self.benchmarks.list_benchmarks():
            item_role = str(item.get("role") or "*")
            if item_role not in {role, "*"}:
                continue
            index[(str(item["providerId"]), str(item["model"]), item_role)] = item
        return index

    def _benchmark_for(
        self,
        index: dict[tuple[str, str, str], dict[str, Any]],
        provider_id: str,
        model: str,
    ) -> dict[str, Any] | None:
        return index.get((provider_id, model, "*")) or next(
            (item for key, item in index.items() if key[0] == provider_id and key[1] == model),
            None,
        )

    def _benchmark_score(self, benchmark: dict[str, Any] | None) -> dict[str, float]:
        total_sample_count = int((benchmark or {}).get("tasksAttempted") or 0)
        sample_count = int((benchmark or {}).get("objectiveTasksAttempted") or 0)
        operator_reported_count = int((benchmark or {}).get("operatorReportedTasks") or 0)
        automated_run_count = int((benchmark or {}).get("automatedRunTasks") or 0)
        release_validation_count = int((benchmark or {}).get("releaseValidationTasks") or 0)
        insufficient = 1.0 if sample_count < MIN_BENCHMARK_SAMPLES or not benchmark else 0.0
        if insufficient:
            return {
                "benchmarkSampleCount": float(total_sample_count),
                "benchmarkTotalSampleCount": float(total_sample_count),
                "benchmarkObjectiveSampleCount": float(sample_count),
                "benchmarkOperatorReportedSampleCount": float(operator_reported_count),
                "benchmarkAutomatedRunSampleCount": float(automated_run_count),
                "benchmarkReleaseValidationSampleCount": float(release_validation_count),
                "benchmarkInsufficientData": 1.0,
                "benchmarkScore": 0.0,
                "benchmarkContribution": 0.0,
            }
        success = float(benchmark.get("successRate") if benchmark.get("successRate") is not None else 0.5)
        qa_pass = float(benchmark.get("qaPassRate") if benchmark.get("qaPassRate") is not None else 0.5)
        rework = float(benchmark.get("reworkRate") if benchmark.get("reworkRate") is not None else 0.5)
        benchmark_score = max(0.0, min((success * 0.45) + (qa_pass * 0.35) + ((1.0 - rework) * 0.20), 1.0))
        return {
            "benchmarkSampleCount": float(total_sample_count),
            "benchmarkTotalSampleCount": float(total_sample_count),
            "benchmarkObjectiveSampleCount": float(sample_count),
            "benchmarkOperatorReportedSampleCount": float(operator_reported_count),
            "benchmarkAutomatedRunSampleCount": float(automated_run_count),
            "benchmarkReleaseValidationSampleCount": float(release_validation_count),
            "benchmarkInsufficientData": 0.0,
            "benchmarkScore": round(benchmark_score, 6),
            "benchmarkContribution": round(benchmark_score * 0.10, 6),
        }

    def _effort_for(
        self,
        request: RoutingRequest,
        provider: dict[str, Any],
        model: dict[str, Any],
        preferences: list[dict[str, Any]],
    ) -> str | None:
        for item in preferences:
            if item.get("provider") == provider["providerId"] and item.get("model") in {
                model["model"],
                "auto",
                "auto_best_available",
            }:
                return item.get("effort")
        efforts = model.get("effortLevels") or []
        if request.mode == "max_performance" and "xhigh" in efforts:
            return "xhigh"
        return efforts[0] if efforts else None

    def _decision_reason(self, request: RoutingRequest, selected: dict[str, Any] | None) -> str:
        if not selected:
            return "No provider/model/runtime satisfied hard filters."
        return (
            f"Selected {selected['provider']}/{selected['model']} for role {request.role} "
            f"in {request.mode} mode using {selected['runtime']} runtime."
        )
