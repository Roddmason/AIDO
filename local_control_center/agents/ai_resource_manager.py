"""Explainable AI resource selection with honest usage and cost accounting.

AIResourceManager keeps task routing separate from provider execution. It stores
model/runtime performance observations, scores candidates with explicit factors,
records immutable routing decisions, and preserves unknown usage as unknown rather
than inventing token or cost values.

Two independent policy gates can force human approval before execution, and both publish
their evidence under ``policyResult``: the unknown-cost policy (a remote model with no known
price) and the premium gate (a KNOWN estimated cost above the role's approval threshold).
Because an unknown cost is never coerced to ``0``, it can never look "cheap enough" to skip
the premium gate — it is escalated by the unknown-cost policy instead.

@author Rodrigo Mason
"""

from __future__ import annotations

import ipaddress
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from local_control_center.agents.provider_accounts import (
    ProviderAccountStore,
    provider_account_is_declared_free,
)
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

LOW_RISK_LEVELS = {"low", "routine"}
HIGH_RISK_LEVELS = {"high", "critical", "security", "release"}
CRITICAL_RISK_LEVELS = {"critical", "security", "release"}
REMOTE_LOCALITIES = {"remote"}
ROUTING_POLICY_MODES = {"economy", "balanced", "critical", "maximum"}
ROUTING_POLICY_ALIASES = {
    "balanced_best_value": "balanced",
    "best_value": "balanced",
    "free_first": "economy",
    "low_cost": "economy",
    "local_private": "economy",
    "max_performance": "maximum",
    "maximum_performance": "maximum",
}
TOKEN_KEYS = {
    "input": ("input_tokens", "prompt_tokens"),
    "cached_input": ("cached_input_tokens", "cached_prompt_tokens"),
    "output": ("output_tokens", "completion_tokens"),
    "reasoning": ("reasoning_tokens",),
    "tool": ("tool_tokens",),
    "total": ("total_tokens",),
}
MODEL_CATALOG_BASELINE_EVIDENCE_KIND = "model_catalog_baseline"
MODEL_CATALOG_CANDIDATE_INVENTORY = "model_catalog_with_performance_overlay"
PERFORMANCE_PROFILE_CANDIDATE_INVENTORY = "ai_model_performance"
UNOBSERVED_PERFORMANCE_PRIOR = 0.5


@dataclass(frozen=True)
class AIResourceRequest:
    """Inputs required to choose one model/runtime for a task."""

    task_type: str
    risk_level: str = "medium"
    context_tokens_estimate: int = 0
    required_capabilities: list[str] = field(default_factory=list)
    allowed_provider_ids: list[str] | None = None
    preferred_provider_ids: list[str] = field(default_factory=list)
    blocked_resources: list[dict[str, Any]] = field(default_factory=list)
    #: Candidatos descartados por haber fallado en este mismo turno. Va aparte de
    #: ``blocked_resources`` a propósito: ese contador se audita como política de rol y mezclar
    #: exclusiones de transporte lo falsearía.
    excluded_resources: list[dict[str, Any]] = field(default_factory=list)
    context_token_limit: int | None = None
    role_policy_id: str | None = None
    allow_remote: bool = True
    allow_local: bool = True
    allow_cli: bool = True
    allow_api: bool = True
    free_tier_only: bool = False
    privacy_level: str = "remote_allowed"
    budget_remaining_usd: float | None = None
    max_tokens: int | None = None
    routing_policy: str = "balanced"
    allow_unknown_cost: bool = False
    require_approval_for_unknown_cost: bool = True
    require_approval_over_usd: float | None = None
    require_multi_model_quorum: bool = False
    project_id: str | None = None
    workflow_run_id: str | None = None
    workflow_step_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None


def _bool(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, int) else bool(value)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _float_or_default(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _camel_payload_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def _performance_evidence_summary(evidence: list[Any]) -> tuple[str, int]:
    outcome_count = sum(
        1 for item in evidence if isinstance(item, dict) and str(item.get("kind") or "") == "outcome"
    )
    if outcome_count:
        return "observed", outcome_count
    if any(
        isinstance(item, dict) and str(item.get("kind") or "") == MODEL_CATALOG_BASELINE_EVIDENCE_KIND
        for item in evidence
    ):
        return "unobserved_prior", 0
    return "configured_prior", 0


def _catalog_provider_locality(provider: dict[str, Any] | None) -> str:
    if not provider:
        return "remote"
    metadata = provider.get("metadata") if isinstance(provider.get("metadata"), dict) else {}
    if str(metadata.get("endpointKind") or "").strip().lower() == "remote":
        return "remote"
    host = (urlparse(str(provider.get("baseUrl") or "")).hostname or "").lower()
    if host == "localhost":
        return "local"
    try:
        return "local" if ipaddress.ip_address(host).is_loopback else "remote"
    except ValueError:
        return "remote"


def _row_to_model(row: sqlite3.Row) -> dict[str, Any]:
    evidence = json_loads(row["evidence_json"], [])
    performance_status, performance_sample_count = _performance_evidence_summary(evidence)
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "runtime": row["runtime"],
        "capabilities": json_loads(row["capabilities_json"], []),
        "contextWindow": row["context_window"],
        "maxOutputTokens": row["max_output_tokens"],
        "inputPricePerMtok": row["input_price_per_mtok"],
        "cachedInputPricePerMtok": row["cached_input_price_per_mtok"],
        "outputPricePerMtok": row["output_price_per_mtok"],
        "reasoningPricePerMtok": row["reasoning_price_per_mtok"],
        "freeTier": str(row["locality"] or "") == "local"
        or (
            row["input_price_per_mtok"] is not None
            and row["output_price_per_mtok"] is not None
            and float(row["input_price_per_mtok"]) == 0.0
            and float(row["output_price_per_mtok"]) == 0.0
        ),
        "observedLatencyMs": row["observed_latency_ms"],
        "observedSuccessRate": row["observed_success_rate"],
        "reworkRate": row["rework_rate"],
        "totalObservedTokens": row["total_observed_tokens"],
        "qualityScore": row["quality_score"],
        "locality": row["locality"],
        "privacyLevel": row["privacy_level"],
        "profileSource": row["profile_source"],
        "evidence": evidence,
        "performanceStatus": performance_status,
        "performanceSampleCount": performance_sample_count,
        "enabled": _bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _ai_candidate_to_normalized(candidate: dict[str, Any]) -> dict[str, Any]:
    """Rename an ``ai_routing_decisions`` candidate's ``providerId`` to the shared ``provider`` key."""
    return {
        "provider": candidate.get("providerId") or candidate.get("provider"),
        "model": candidate.get("model"),
        "runtime": candidate.get("runtime"),
        "estimatedCostUsd": candidate.get("estimatedCostUsd"),
        "priceKnown": bool(candidate.get("priceKnown")),
    }


def _row_to_ai_routing_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Map an `ai_routing_decisions` row to the shared routing-decision dict shape."""
    policy_result = json_loads(row["policy_result_json"], {})
    return {
        "id": row["id"],
        "taskType": row["task_type"],
        "mode": policy_result.get("mode"),
        "selectedProvider": row["selected_provider"],
        "selectedModel": row["selected_model"],
        "selectedRuntime": row["selected_runtime"],
        # The AI resource manager selects a model, not a reasoning effort: absent, not zero.
        "selectedEffort": None,
        "taskId": row["task_id"],
        "estimatedCostUsd": row["estimated_cost_usd"],
        "costTier": row["cost_tier"],
        "approvalRequired": _bool(row["approval_required"]),
        "candidates": [_ai_candidate_to_normalized(item) for item in json_loads(row["candidates_json"], [])],
        "rejected": json_loads(row["rejected_json"], []),
        "decisionReason": row["decision_reason"],
        "scoreBreakdown": json_loads(row["score_breakdown_json"], {}),
        "policyResult": policy_result,
        "createdAt": row["created_at"],
    }


def _row_to_cost_observation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "runtime": row["runtime"],
        "inputTokens": row["input_tokens"],
        "cachedInputTokens": row["cached_input_tokens"],
        "outputTokens": row["output_tokens"],
        "reasoningTokens": row["reasoning_tokens"],
        "toolTokens": row["tool_tokens"],
        "totalTokens": row["total_tokens"],
        "estimatedCostUsd": row["estimated_cost_usd"],
        "actualCostUsd": row["actual_cost_usd"],
        "currency": row["currency"],
        "latencyMs": row["latency_ms"],
        "usageSource": row["usage_source"],
        "tokenStatus": row["token_status"],
        "costStatus": row["cost_status"],
        "rawUsage": json_loads(row["raw_usage_json"], {}),
        "evidenceRef": row["evidence_ref"],
        "createdAt": row["created_at"],
    }


def _normalized_risk(risk_level: str) -> str:
    value = risk_level.strip().lower()
    if value in LOW_RISK_LEVELS:
        return "low"
    if value in CRITICAL_RISK_LEVELS:
        return "critical"
    if value in HIGH_RISK_LEVELS:
        return "high"
    return "medium"


def _normalized_policy(routing_policy: str) -> str:
    value = routing_policy.strip().lower().replace("-", "_")
    mode = ROUTING_POLICY_ALIASES.get(value, value)
    if mode not in ROUTING_POLICY_MODES:
        raise ValueError(f"Unsupported AI routing policy: {routing_policy}")
    return mode


def _required_capabilities_missing(required: list[str], capabilities: list[str]) -> list[str]:
    available = {item.strip().lower() for item in capabilities}
    return [item for item in required if item.strip().lower() not in available]


class AIResourceManager:
    """Selects AI resources using deterministic policy gates and explainable scoring."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self._runtime_status_cache: dict[
            str | None,
            dict[str, dict[str, Any]],
        ] = {}

    def upsert_model_performance(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Insert or update one provider/model/runtime performance profile."""
        provider_id = str(_camel_payload_value(payload, "providerId", "provider_id"))
        model = str(payload["model"])
        runtime = str(payload["runtime"])
        now = utc_now()
        capabilities = list(_camel_payload_value(payload, "capabilities", default=[]) or [])
        evidence = redact_secrets(_camel_payload_value(payload, "evidence", default=[]) or [])
        self.connection.execute(
            """
            INSERT INTO ai_model_performance
                (id, provider_id, model, runtime, capabilities_json, context_window, max_output_tokens,
                 input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
                 reasoning_price_per_mtok, observed_latency_ms, observed_success_rate, rework_rate,
                 total_observed_tokens, quality_score, locality, privacy_level, profile_source,
                 evidence_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, model, runtime) DO UPDATE SET
                capabilities_json = excluded.capabilities_json,
                context_window = excluded.context_window,
                max_output_tokens = excluded.max_output_tokens,
                input_price_per_mtok = excluded.input_price_per_mtok,
                cached_input_price_per_mtok = excluded.cached_input_price_per_mtok,
                output_price_per_mtok = excluded.output_price_per_mtok,
                reasoning_price_per_mtok = excluded.reasoning_price_per_mtok,
                observed_latency_ms = excluded.observed_latency_ms,
                observed_success_rate = excluded.observed_success_rate,
                rework_rate = excluded.rework_rate,
                total_observed_tokens = excluded.total_observed_tokens,
                quality_score = excluded.quality_score,
                locality = excluded.locality,
                privacy_level = excluded.privacy_level,
                profile_source = excluded.profile_source,
                evidence_json = excluded.evidence_json,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                str(payload.get("id") or f"ai-model-{provider_id}:{model}:{runtime}"),
                provider_id,
                model,
                runtime,
                json_dumps(capabilities),
                int(_camel_payload_value(payload, "contextWindow", "context_window", default=0) or 0),
                int(_camel_payload_value(payload, "maxOutputTokens", "max_output_tokens", default=0) or 0),
                _float_or_none(_camel_payload_value(payload, "inputPricePerMtok", "input_price_per_mtok")),
                _float_or_none(
                    _camel_payload_value(
                        payload,
                        "cachedInputPricePerMtok",
                        "cached_input_price_per_mtok",
                    )
                ),
                _float_or_none(_camel_payload_value(payload, "outputPricePerMtok", "output_price_per_mtok")),
                _float_or_none(
                    _camel_payload_value(payload, "reasoningPricePerMtok", "reasoning_price_per_mtok")
                ),
                _int_or_none(_camel_payload_value(payload, "observedLatencyMs", "observed_latency_ms")),
                _float_or_default(
                    _camel_payload_value(
                        payload,
                        "observedSuccessRate",
                        "observed_success_rate",
                        default=0.5,
                    ),
                    0.5,
                ),
                _float_or_default(
                    _camel_payload_value(
                        payload,
                        "reworkRate",
                        "rework_rate",
                        default=0.0,
                    ),
                    0.0,
                ),
                _int_or_none(_camel_payload_value(payload, "totalObservedTokens", "total_observed_tokens")),
                _float_or_default(
                    _camel_payload_value(
                        payload,
                        "qualityScore",
                        "quality_score",
                        default=0.5,
                    ),
                    0.5,
                ),
                str(_camel_payload_value(payload, "locality", default="remote")),
                str(_camel_payload_value(payload, "privacyLevel", "privacy_level", default="remote_allowed")),
                str(_camel_payload_value(payload, "profileSource", "profile_source", default="explicit")),
                json_dumps(evidence),
                1 if _camel_payload_value(payload, "enabled", default=True) else 0,
                now,
                now,
            ),
        )
        return self._get_model(provider_id=provider_id, model=model, runtime=runtime)

    def select_resource(self, request: AIResourceRequest, *, record: bool = True) -> dict[str, Any]:
        """Choose the best model/runtime for the request and persist the decision."""
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        risk = _normalized_risk(request.risk_level)
        policy_mode = _normalized_policy(request.routing_policy)
        effective_risk = "critical" if policy_mode == "critical" else risk
        budget_stop = False
        models, runtime_statuses, candidate_inventory = self._selection_models(project_id=request.project_id)
        unknown_cost_policy: dict[str, Any] = {
            "action": "not_applicable",
            "reason": "cost_known_or_local",
            "mode": policy_mode,
        }
        for model in models:
            rejection = self._hard_reject_reason(model, request)
            if rejection:
                rejected.append(self._rejected(model, rejection))
                continue
            if runtime_statuses is None:
                runtime_statuses = self._runtime_statuses_by_provider(project_id=request.project_id)
            runtime_status = runtime_statuses.get(str(model.get("providerId") or ""))
            policy_rejection = self._role_execution_policy_reject_reason(
                model=model,
                request=request,
                runtime_status=runtime_status,
            )
            if policy_rejection:
                rejected.append(self._rejected(model, policy_rejection))
                continue
            runtime_rejection = self._runtime_executable_reject_reason(
                model=model,
                runtime_statuses=runtime_statuses,
                request=request,
            )
            if runtime_rejection:
                rejected.append(self._rejected(model, runtime_rejection))
                continue
            free_tier_rejection = self._free_tier_reject_reason(model, request)
            if free_tier_rejection:
                rejected.append(self._rejected(model, free_tier_rejection))
                continue
            estimate = self._estimate_cost(model, request)
            policy = self._unknown_cost_policy(model, request, estimate, policy_mode)
            if policy["action"] == "reject":
                rejected.append(self._rejected(model, policy["reason"]))
                unknown_cost_policy = policy
                continue
            if (
                estimate["estimatedCostUsd"] is not None
                and request.budget_remaining_usd is not None
                and float(estimate["estimatedCostUsd"]) > float(request.budget_remaining_usd)
            ):
                budget_stop = True
                rejected.append(self._rejected(model, "budget_stop"))
                continue
            candidate = self._candidate(model, request, effective_risk, policy_mode, estimate, policy)
            candidates.append(candidate)

        provider_preference = self._provider_preference(request.preferred_provider_ids)
        selected = min(
            candidates,
            key=lambda item: self._selection_sort_key(item, provider_preference),
            default=None,
        )
        if selected and selected.get("unknownCostPolicy", {}).get("action") != "not_applicable":
            unknown_cost_policy = selected["unknownCostPolicy"]
        reviewer = self._select_reviewer(candidates, selected, effective_risk)
        premium_approval = self._premium_approval(
            selected=selected,
            require_approval_over_usd=request.require_approval_over_usd,
        )
        approval_required = self._requires_approval(
            selected=selected,
            unknown_cost_policy=unknown_cost_policy,
            premium_approval=premium_approval,
        )
        max_tokens = self._max_tokens(selected, request)
        context_window = int(selected["contextWindow"] or 0) if selected else 0
        context_compression = bool(
            selected and context_window and request.context_tokens_estimate > int(context_window * 0.8)
        )
        multi_model_quorum = bool(
            request.require_multi_model_quorum
            or effective_risk == "critical"
            or (effective_risk == "high" and selected is not None and selected["score"] < 0.72)
        )
        decision = {
            "routingDecisionId": f"ai-routing-{uuid.uuid4()}" if record else None,
            "selected": self._public_selection(selected),
            "reviewerSelection": self._public_selection(reviewer),
            "localVsRemote": selected["locality"] if selected else None,
            "costTier": self._cost_tier(selected),
            "cheapVsPremium": self._cost_tier(selected),
            "multiModelQuorum": multi_model_quorum,
            "contextCompression": context_compression,
            "maxTokens": max_tokens,
            "budgetStop": budget_stop and selected is None,
            "approvalRequired": approval_required,
            "estimatedCostUsd": selected.get("estimatedCostUsd") if selected else None,
            "actualCostUsd": None,
            "usageStatus": "not_executed",
            "decisionReason": self._decision_reason(request, selected, approval_required, budget_stop),
            "candidates": [self._public_candidate(candidate) for candidate in candidates],
            "rejected": rejected,
            "scoreBreakdown": selected.get("scoreBreakdown", {}) if selected else {},
            "policyResult": {
                "mode": policy_mode,
                "riskLevel": risk,
                "effectiveRiskLevel": effective_risk,
                "unknownCostPolicy": unknown_cost_policy,
                "premiumApproval": premium_approval,
                "scoring": "deterministic_explainable",
                "opaqueMlUsed": False,
                "candidateInventory": candidate_inventory,
                "freeTierOnly": request.free_tier_only,
                "roleExecutionPolicy": {
                    "rolePolicyId": request.role_policy_id,
                    "allowRemote": request.allow_remote,
                    "allowLocal": request.allow_local,
                    "allowCli": request.allow_cli,
                    "allowApi": request.allow_api,
                    "blockedResourceCount": len(request.blocked_resources),
                    "failoverExcludedCount": len(request.excluded_resources),
                    "contextTokenLimit": request.context_token_limit,
                },
                "providerPreferenceOrder": provider_preference,
                "selectionOrder": "score_desc_provider_preference_asc_identity_asc",
            },
        }
        if record:
            self._record_routing_decision(request=request, decision=decision)
        return decision

    def list_routing_decisions_for_task_prefixes(self, task_prefixes: list[str]) -> list[dict[str, Any]]:
        """List this manager's routing decisions whose ``task_id`` starts with any prefix, newest first.

        Rows are normalised to the same camelCase shape ``RoutingProfileStore`` returns, so a reader
        (the thread cost/performance snapshot) can consume either audit trail without branching on
        which router produced the decision. Prefixes are internally derived loop ids, never operator
        input, so they carry no LIKE wildcards. Returns an empty list when no prefixes are given.
        """
        prefixes = [prefix for prefix in task_prefixes if prefix]
        if not prefixes:
            return []
        clause = " OR ".join("task_id LIKE ?" for _ in prefixes)
        rows = self.connection.execute(
            f"SELECT * FROM ai_routing_decisions WHERE {clause} ORDER BY created_at DESC, rowid DESC",
            [f"{prefix}%" for prefix in prefixes],
        ).fetchall()
        return [_row_to_ai_routing_decision(row) for row in rows]

    def record_outcome(
        self,
        *,
        provider_id: str,
        model: str,
        success: bool,
        rework: bool,
        quality_score: float,
        latency_ms: int | None,
        evidence_ref: str,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        """Learn from an outcome only when an evidence reference is provided."""
        if not evidence_ref:
            raise ValueError("AI outcome learning requires evidence_ref.")
        existing = self._find_model(provider_id=provider_id, model=model, runtime=runtime)
        materialized_from_catalog = False
        if existing is None:
            existing = self._materialize_catalog_model_performance(
                provider_id=provider_id,
                model=model,
                runtime=runtime,
            )
            materialized_from_catalog = existing is not None
        if existing is None:
            raise KeyError(f"AI model performance row not found: {provider_id}/{model}")
        evidence = list(existing.get("evidence") or [])
        evidence.append(
            {
                "id": evidence_ref,
                "kind": "outcome",
                "success": bool(success),
                "rework": bool(rework),
                "qualityScore": float(quality_score),
            }
        )
        if materialized_from_catalog:
            updated_success = 1.0 if success else 0.0
            updated_rework = 1.0 if rework else 0.0
            updated_quality = self._bounded(float(quality_score))
        else:
            updated_success = self._weighted_update(existing["observedSuccessRate"], 1.0 if success else 0.0)
            updated_rework = self._weighted_update(existing["reworkRate"], 1.0 if rework else 0.0)
            updated_quality = self._weighted_update(
                existing["qualityScore"], float(quality_score), weight=0.60
            )
        return self.upsert_model_performance(
            {
                **existing,
                "observedSuccessRate": updated_success,
                "reworkRate": updated_rework,
                "qualityScore": updated_quality,
                "observedLatencyMs": (
                    latency_ms if latency_ms is not None else existing.get("observedLatencyMs")
                ),
                "evidence": evidence,
            }
        )

    def record_cost_observation(
        self,
        *,
        provider_id: str,
        model: str,
        runtime: str,
        estimated_cost_usd: float | None,
        actual_cost_usd: float | None,
        provider_usage: dict[str, Any] | None,
        evidence_ref: str,
        latency_ms: int | None = None,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Record one cost/usage observation without fabricating missing token values."""
        if not evidence_ref:
            raise ValueError("Cost observations require evidence_ref.")
        token_values = self._token_values(provider_usage)
        usage_reported = provider_usage is not None and any(
            value is not None for value in token_values.values()
        )
        token_status = "actual" if usage_reported else "unknown"
        usage_source = "actual" if usage_reported else "unknown"
        cost_status = (
            "actual"
            if actual_cost_usd is not None
            else "estimated"
            if estimated_cost_usd is not None
            else "unknown"
        )
        observation_id = f"ai-cost-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO ai_cost_observations
                (id, provider_id, model, runtime, input_tokens, cached_input_tokens, output_tokens,
                 reasoning_tokens, tool_tokens, total_tokens, estimated_cost_usd, actual_cost_usd,
                 currency, latency_ms, usage_source, token_status, cost_status, raw_usage_json,
                 evidence_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                provider_id,
                model,
                runtime,
                token_values["input"],
                token_values["cached_input"],
                token_values["output"],
                token_values["reasoning"],
                token_values["tool"],
                token_values["total"],
                estimated_cost_usd,
                actual_cost_usd,
                currency,
                latency_ms,
                usage_source,
                token_status,
                cost_status,
                json_dumps(redact_secrets(provider_usage or {})),
                evidence_ref,
                utc_now(),
            ),
        )
        if token_values["total"] is not None:
            self._record_observed_tokens(
                provider_id=provider_id,
                model=model,
                runtime=runtime,
                total_tokens=token_values["total"],
            )
        row = self.connection.execute(
            "SELECT * FROM ai_cost_observations WHERE id = ?", (observation_id,)
        ).fetchone()
        return _row_to_cost_observation(row)

    def upsert_prompt_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store prompt profile defaults used by routing requests."""
        profile_id = str(payload.get("id") or f"ai-prompt-profile-{payload['taskType']}")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO ai_prompt_profiles
                (id, task_type, risk_level, required_capabilities_json, privacy_level,
                 max_context_tokens, max_output_tokens, budget_usd, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_type = excluded.task_type,
                risk_level = excluded.risk_level,
                required_capabilities_json = excluded.required_capabilities_json,
                privacy_level = excluded.privacy_level,
                max_context_tokens = excluded.max_context_tokens,
                max_output_tokens = excluded.max_output_tokens,
                budget_usd = excluded.budget_usd,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                payload["taskType"],
                payload.get("riskLevel", "medium"),
                json_dumps(payload.get("requiredCapabilities") or []),
                payload.get("privacyLevel", "remote_allowed"),
                payload.get("maxContextTokens"),
                payload.get("maxOutputTokens"),
                payload.get("budgetUsd"),
                json_dumps(redact_secrets(payload.get("metadata") or {})),
                now,
                now,
            ),
        )
        return {"id": profile_id, "taskType": payload["taskType"]}

    def record_context_summary(
        self,
        *,
        project_id: str | None,
        source_context_hash: str,
        summary: str,
        token_status: str,
        evidence_ref: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        compression_reason: str = "context_window",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a context summary, preserving unknown token counts as NULL."""
        summary_id = f"ai-context-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO ai_context_summaries
                (id, project_id, source_context_hash, summary, input_tokens, output_tokens,
                 token_status, compression_reason, evidence_ref, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                project_id,
                source_context_hash,
                summary,
                input_tokens,
                output_tokens,
                token_status,
                compression_reason,
                evidence_ref,
                json_dumps(redact_secrets(metadata or {})),
                utc_now(),
            ),
        )
        return {"id": summary_id, "tokenStatus": token_status}

    def _list_enabled_models(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM ai_model_performance WHERE enabled = 1 ORDER BY provider_id ASC, model ASC"
        ).fetchall()
        return [_row_to_model(row) for row in rows]

    def _selection_models(
        self,
        *,
        project_id: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]] | None, str]:
        performance_models = self._list_enabled_models()
        runtime_statuses = self._runtime_statuses_by_provider(project_id=project_id)
        catalog_models = self._catalog_models_with_performance_overlay(
            performance_models=performance_models,
            runtime_statuses=runtime_statuses,
        )
        catalog_keys = {self._model_profile_key(model) for model in catalog_models}
        explicit_models = [
            model for model in performance_models if self._model_profile_key(model) not in catalog_keys
        ]
        if not catalog_models:
            return explicit_models, runtime_statuses, PERFORMANCE_PROFILE_CANDIDATE_INVENTORY
        return (
            [*explicit_models, *catalog_models],
            runtime_statuses,
            MODEL_CATALOG_CANDIDATE_INVENTORY,
        )

    @staticmethod
    def _model_profile_key(model: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(model.get("providerId") or ""),
            str(model.get("model") or ""),
            str(model.get("runtime") or ""),
        )

    def _catalog_models_with_performance_overlay(
        self,
        *,
        performance_models: list[dict[str, Any]],
        runtime_statuses: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        store = ProviderAccountStore(self.connection)
        providers = {
            str(provider.get("providerId") or ""): provider for provider in store.list_provider_accounts()
        }
        performance_by_model = {self._model_profile_key(item): item for item in performance_models}
        profiles: list[dict[str, Any]] = []
        for catalog_model in store.list_models():
            if not bool(catalog_model.get("enabled")):
                continue
            provider_id = str(catalog_model.get("providerId") or "")
            profile = self._catalog_model_profile(
                catalog_model=catalog_model,
                provider=providers.get(provider_id),
                runtime_status=runtime_statuses.get(provider_id),
            )
            performance = performance_by_model.get(self._model_profile_key(profile))
            if performance is not None:
                profile = self._overlay_catalog_performance(profile, performance)
            profiles.append(profile)
        return profiles

    def _catalog_model_profile(
        self,
        *,
        catalog_model: dict[str, Any],
        provider: dict[str, Any] | None,
        runtime_status: dict[str, Any] | None,
    ) -> dict[str, Any]:
        provider_id = str(catalog_model.get("providerId") or "")
        provider_type = (
            str((provider or {}).get("providerType") or (runtime_status or {}).get("kind") or "api")
            .strip()
            .lower()
        )
        runtime = (
            provider_type if provider_type in {"api", "cli", "gateway", "local", "manual"} else provider_id
        )
        locality = _catalog_provider_locality(provider)
        catalog_id = str(catalog_model.get("id") or f"{provider_id}:{catalog_model.get('model')}")
        return {
            "id": f"catalog-profile:{catalog_id}",
            "providerId": provider_id,
            "model": str(catalog_model.get("model") or ""),
            "runtime": runtime,
            "capabilities": self._catalog_capabilities(catalog_model, runtime_status),
            "contextWindow": int(catalog_model.get("contextWindow") or 0),
            "maxOutputTokens": int(catalog_model.get("maxOutputTokens") or 0),
            "inputPricePerMtok": catalog_model.get("inputPricePerMtok"),
            "cachedInputPricePerMtok": catalog_model.get("cachedInputPricePerMtok"),
            "outputPricePerMtok": catalog_model.get("outputPricePerMtok"),
            "reasoningPricePerMtok": catalog_model.get("reasoningPricePerMtok"),
            "freeTier": bool(catalog_model.get("freeTier")),
            "observedLatencyMs": None,
            "observedSuccessRate": None,
            "reworkRate": None,
            "totalObservedTokens": None,
            "qualityScore": None,
            "locality": locality,
            "privacyLevel": "local_private" if locality == "local" else "remote_allowed",
            "evidence": [
                {
                    "id": catalog_id,
                    "kind": MODEL_CATALOG_BASELINE_EVIDENCE_KIND,
                    "source": str(catalog_model.get("source") or "model_catalog"),
                }
            ],
            "enabled": True,
            "createdAt": catalog_model.get("createdAt"),
            "updatedAt": catalog_model.get("updatedAt"),
            "profileSource": "model_catalog",
            "performanceStatus": "unobserved_prior",
            "performanceSampleCount": 0,
        }

    @staticmethod
    def _catalog_capabilities(
        catalog_model: dict[str, Any],
        runtime_status: dict[str, Any] | None,
    ) -> list[str]:
        runtime_capabilities = {
            str(item).strip().lower()
            for item in (runtime_status or {}).get("capabilities") or []
            if str(item).strip()
        }
        capabilities = runtime_capabilities & {"chat", "search"}
        if runtime_capabilities & {"code_edit", "issue_to_patch", "code"}:
            capabilities.add("code")
        if runtime_capabilities & {"code_review", "review"}:
            capabilities.add("review")
        end_to_end_capabilities = (
            ("supportsTools", "tools", {"tools", "issue_to_patch"}),
            ("supportsJson", "json", {"json"}),
            ("supportsVision", "vision", {"vision"}),
            ("supportsEmbeddings", "embeddings", {"embeddings"}),
            ("supportsRerank", "rerank", {"rerank"}),
            ("supportsReasoning", "reasoning", {"reasoning"}),
            ("supportsThinking", "reasoning", {"reasoning"}),
        )
        capabilities.update(
            capability
            for flag, capability, runtime_signals in end_to_end_capabilities
            if bool(catalog_model.get(flag)) and runtime_capabilities & runtime_signals
        )
        return sorted(capabilities)

    @staticmethod
    def _overlay_catalog_performance(
        catalog_profile: dict[str, Any],
        performance: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = list(catalog_profile["evidence"])
        evidence_ids = {str(item.get("id") or "") for item in evidence if isinstance(item, dict)}
        evidence.extend(
            item
            for item in performance.get("evidence") or []
            if not isinstance(item, dict)
            or not str(item.get("id") or "")
            or str(item.get("id") or "") not in evidence_ids
        )
        performance_status, performance_sample_count = _performance_evidence_summary(
            list(performance.get("evidence") or [])
        )
        catalog_backed = performance.get("profileSource") == "model_catalog"
        legacy_overrides = (
            {}
            if catalog_backed
            else {
                key: performance.get(key)
                for key in (
                    "capabilities",
                    "contextWindow",
                    "maxOutputTokens",
                    "inputPricePerMtok",
                    "cachedInputPricePerMtok",
                    "outputPricePerMtok",
                    "reasoningPricePerMtok",
                )
            }
        )
        return {
            **catalog_profile,
            **legacy_overrides,
            "id": performance["id"],
            "observedLatencyMs": performance.get("observedLatencyMs"),
            "observedSuccessRate": performance.get("observedSuccessRate"),
            "reworkRate": performance.get("reworkRate"),
            "totalObservedTokens": performance.get("totalObservedTokens"),
            "qualityScore": performance.get("qualityScore"),
            "evidence": evidence,
            "createdAt": performance.get("createdAt"),
            "performanceStatus": performance_status,
            "performanceSampleCount": performance_sample_count,
        }

    def _materialize_catalog_model_performance(
        self,
        *,
        provider_id: str,
        model: str,
        runtime: str | None,
    ) -> dict[str, Any] | None:
        profile = next(
            (
                item
                for item in self._catalog_models_with_performance_overlay(
                    performance_models=[],
                    runtime_statuses={},
                )
                if item["providerId"] == provider_id and item["model"] == model
            ),
            None,
        )
        if profile is None:
            return None
        return self.upsert_model_performance(
            {
                **profile,
                "runtime": runtime or profile["runtime"],
            }
        )

    def _get_model(self, *, provider_id: str, model: str, runtime: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT * FROM ai_model_performance
            WHERE provider_id = ? AND model = ? AND runtime = ?
            """,
            (provider_id, model, runtime),
        ).fetchone()
        if not row:
            raise KeyError(f"AI model performance row not found: {provider_id}/{model}/{runtime}")
        return _row_to_model(row)

    def _find_model(
        self, *, provider_id: str, model: str, runtime: str | None = None
    ) -> dict[str, Any] | None:
        if runtime:
            row = self.connection.execute(
                """
                SELECT * FROM ai_model_performance
                WHERE provider_id = ? AND model = ? AND runtime = ?
                """,
                (provider_id, model, runtime),
            ).fetchone()
            return _row_to_model(row) if row else None
        row = self.connection.execute(
            """
            SELECT * FROM ai_model_performance
            WHERE provider_id = ? AND model = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (provider_id, model),
        ).fetchone()
        return _row_to_model(row) if row else None

    def _record_observed_tokens(
        self,
        *,
        provider_id: str,
        model: str,
        runtime: str,
        total_tokens: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE ai_model_performance
            SET total_observed_tokens = COALESCE(total_observed_tokens, 0) + ?,
                updated_at = ?
            WHERE provider_id = ? AND model = ? AND runtime = ?
            """,
            (int(total_tokens), utc_now(), provider_id, model, runtime),
        )

    def _runtime_statuses_by_provider(self, *, project_id: str | None) -> dict[str, dict[str, Any]]:
        if project_id not in self._runtime_status_cache:
            self._runtime_status_cache[project_id] = {
                str(status.get("id") or ""): status
                for status in RuntimeStatusService(self.connection).list_provider_statuses(
                    project_id=project_id
                )
                if str(status.get("id") or "").strip()
            }
        return self._runtime_status_cache[project_id]

    @staticmethod
    def _runtime_executable_reject_reason(
        *,
        model: dict[str, Any],
        runtime_statuses: dict[str, dict[str, Any]],
        request: AIResourceRequest,
    ) -> str | None:
        provider_id = str(model.get("providerId") or "").strip()
        status = runtime_statuses.get(provider_id)
        if status is None:
            return f"runtime_not_executable: Provider {provider_id} is not configured in runtime status."
        if request.agent_id == "product_owner_agent" and status.get("productOwnerExecutable") is False:
            return "runtime_not_executable: ProductOwnerAgent-specific CLI safety verification failed."
        if status.get("executable") is not True:
            reason = str(status.get("reason") or "").strip()
            detail = reason or f"Provider {provider_id} is not executable."
            return f"runtime_not_executable: {detail}"
        advertised_models = status.get("models")
        if isinstance(advertised_models, list) and advertised_models:
            model_id = str(model.get("model") or "").strip()
            available_models = {str(item).strip() for item in advertised_models if str(item).strip()}
            if model_id and model_id not in available_models:
                return (
                    "runtime_model_not_available: "
                    f"Provider {provider_id} does not currently report model {model_id}."
                )
        return None

    @staticmethod
    def _role_execution_policy_reject_reason(
        *,
        model: dict[str, Any],
        request: AIResourceRequest,
        runtime_status: dict[str, Any] | None,
    ) -> str | None:
        runtime_kind = str((runtime_status or {}).get("kind") or model.get("runtime") or "").lower()
        if runtime_kind == "cli":
            return None if request.allow_cli else "role_blocks_cli"
        if runtime_kind in {"api", "gateway"} and not request.allow_api:
            return "role_blocks_api"
        locality = str(model.get("locality") or "remote").lower()
        if locality == "local":
            return None if request.allow_local else "role_blocks_local"
        return None if request.allow_remote else "role_blocks_remote"

    @staticmethod
    def _provider_preference(provider_ids: list[str]) -> list[str]:
        ordered: list[str] = []
        for provider_id in provider_ids:
            normalized = str(provider_id).strip()
            if normalized and normalized not in ordered:
                ordered.append(normalized)
        return ordered

    @staticmethod
    def _selection_sort_key(
        candidate: dict[str, Any],
        provider_preference: list[str],
    ) -> tuple[float, int, str, str, str]:
        provider_id = str(candidate.get("providerId") or "")
        try:
            preference_rank = provider_preference.index(provider_id)
        except ValueError:
            preference_rank = len(provider_preference)
        return (
            -float(candidate["score"]),
            preference_rank,
            provider_id,
            str(candidate.get("model") or ""),
            str(candidate.get("runtime") or ""),
        )

    def _hard_reject_reason(self, model: dict[str, Any], request: AIResourceRequest) -> str | None:
        if request.allowed_provider_ids is not None and model["providerId"] not in {
            str(provider_id).strip()
            for provider_id in request.allowed_provider_ids
            if str(provider_id).strip()
        }:
            return "provider_not_allowed_for_agent"
        if self._blocked_by_role_policy(model, request.blocked_resources):
            return "role_blocks_candidate"
        if self._blocked_by_role_policy(model, request.excluded_resources):
            return "runtime_failover_excluded"
        if (
            request.context_token_limit is not None
            and request.context_token_limit > 0
            and request.context_tokens_estimate > request.context_token_limit
        ):
            return "role_token_limit_exceeded"
        missing = _required_capabilities_missing(request.required_capabilities, model["capabilities"])
        if missing:
            return f"missing_capabilities:{','.join(missing)}"
        if request.privacy_level == "local_private" and model["locality"] != "local":
            return "privacy_blocks_remote"
        context_window = int(model["contextWindow"] or 0)
        if context_window and request.context_tokens_estimate > context_window * 2:
            return "context_window_too_small"
        return None

    def _free_tier_reject_reason(
        self,
        model: dict[str, Any],
        request: AIResourceRequest,
    ) -> str | None:
        if not request.free_tier_only:
            return None
        if (
            request.privacy_level.strip().lower() not in {"remote_allowed", "public"}
            and model["locality"] in REMOTE_LOCALITIES
        ):
            return "free_tier_sensitive_data_blocked"
        if model["locality"] != "local" and not bool(model.get("freeTier")):
            return "free_tier_model_required"
        if model["locality"] in REMOTE_LOCALITIES and not self._free_tier_account_declared(
            str(model.get("providerId") or "")
        ):
            return "free_tier_account_not_confirmed"
        return None

    @staticmethod
    def _blocked_by_role_policy(
        model: dict[str, Any],
        blocked_resources: list[dict[str, Any]],
    ) -> bool:
        for item in blocked_resources:
            if not isinstance(item, dict):
                continue
            provider_id = item.get("provider")
            model_id = item.get("model")
            if provider_id not in {None, "*", model["providerId"]}:
                continue
            if model_id not in {None, "*", "auto", model["model"]}:
                continue
            return True
        return False

    def _estimate_cost(self, model: dict[str, Any], request: AIResourceRequest) -> dict[str, Any]:
        output_tokens = self._estimated_output_tokens(model, request)
        input_price = model.get("inputPricePerMtok")
        output_price = model.get("outputPricePerMtok")
        reasoning_price = model.get("reasoningPricePerMtok")
        reasoning_tokens = int(output_tokens * 0.5) if "reasoning" in request.required_capabilities else 0
        if request.free_tier_only:
            return {
                "estimatedCostUsd": 0.0,
                "priceKnown": True,
                "estimatedInputTokens": request.context_tokens_estimate,
                "estimatedOutputTokens": output_tokens,
                "estimatedReasoningTokens": reasoning_tokens,
                "costEstimateSource": "operator_declared_free_tier_account",
            }
        observed = self._observed_cost_estimate(
            model=model,
            estimated_total_tokens=request.context_tokens_estimate + output_tokens + reasoning_tokens,
        )
        if observed is not None:
            return {
                "estimatedCostUsd": observed["estimatedCostUsd"],
                "priceKnown": True,
                "estimatedInputTokens": request.context_tokens_estimate,
                "estimatedOutputTokens": output_tokens,
                "estimatedReasoningTokens": reasoning_tokens,
                "costEstimateSource": "observed_actual_usage",
                "observedCostPerToken": observed["costPerToken"],
                "observedCostSamples": observed["sampleCount"],
                "observedTotalTokens": observed["totalTokens"],
            }
        if input_price is None or output_price is None or (reasoning_tokens and reasoning_price is None):
            return {
                "estimatedCostUsd": None,
                "priceKnown": False,
                "estimatedInputTokens": request.context_tokens_estimate,
                "estimatedOutputTokens": output_tokens,
                "estimatedReasoningTokens": reasoning_tokens,
                "costEstimateSource": "unknown_price",
            }
        total = (float(input_price) * request.context_tokens_estimate) / 1_000_000
        total += (float(output_price) * output_tokens) / 1_000_000
        if reasoning_tokens:
            total += (float(reasoning_price) * reasoning_tokens) / 1_000_000
        return {
            "estimatedCostUsd": round(total, 6),
            "priceKnown": True,
            "estimatedInputTokens": request.context_tokens_estimate,
            "estimatedOutputTokens": output_tokens,
            "estimatedReasoningTokens": reasoning_tokens,
            "costEstimateSource": "static_price",
        }

    def _free_tier_account_declared(self, provider_id: str) -> bool:
        try:
            account = ProviderAccountStore(self.connection).get_provider_account(provider_id)
        except KeyError:
            return False
        return provider_account_is_declared_free(account) or str(account.get("providerType") or "") == "local"

    def _observed_cost_estimate(
        self,
        *,
        model: dict[str, Any],
        estimated_total_tokens: int,
    ) -> dict[str, Any] | None:
        if estimated_total_tokens <= 0:
            return None
        row = self.connection.execute(
            """
            SELECT
                COUNT(*) AS sample_count,
                SUM(total_tokens) AS total_tokens,
                SUM(actual_cost_usd) AS actual_cost_usd
            FROM ai_cost_observations
            WHERE provider_id = ?
              AND model = ?
              AND runtime = ?
              AND total_tokens IS NOT NULL
              AND total_tokens > 0
              AND actual_cost_usd IS NOT NULL
            """,
            (model["providerId"], model["model"], model["runtime"]),
        ).fetchone()
        if (
            row is None
            or int(row["sample_count"] or 0) == 0
            or int(row["total_tokens"] or 0) <= 0
            or row["actual_cost_usd"] is None
        ):
            return None
        cost_per_token = float(row["actual_cost_usd"]) / float(row["total_tokens"])
        return {
            "estimatedCostUsd": round(cost_per_token * estimated_total_tokens, 6),
            "costPerToken": cost_per_token,
            "sampleCount": int(row["sample_count"]),
            "totalTokens": int(row["total_tokens"]),
        }

    def _unknown_cost_policy(
        self,
        model: dict[str, Any],
        request: AIResourceRequest,
        estimate: dict[str, Any],
        policy_mode: str,
    ) -> dict[str, Any]:
        if estimate["estimatedCostUsd"] is not None or model["locality"] not in REMOTE_LOCALITIES:
            return {"action": "not_applicable", "reason": "cost_known_or_local", "mode": policy_mode}
        if policy_mode == "economy":
            return {
                "action": "reject",
                "reason": "unknown_remote_cost_rejected_by_policy",
                "providerId": model["providerId"],
                "model": model["model"],
                "mode": policy_mode,
            }
        if request.allow_unknown_cost and not request.require_approval_for_unknown_cost:
            return {
                "action": "allow",
                "reason": "unknown_remote_cost_allowed_by_policy",
                "providerId": model["providerId"],
                "model": model["model"],
                "mode": policy_mode,
            }
        if request.require_approval_for_unknown_cost:
            return {
                "action": "require_approval",
                "reason": "unknown_remote_cost_requires_approval",
                "providerId": model["providerId"],
                "model": model["model"],
                "mode": policy_mode,
            }
        return {
            "action": "reject",
            "reason": "unknown_remote_cost_rejected_by_policy",
            "providerId": model["providerId"],
            "model": model["model"],
            "mode": policy_mode,
        }

    def _candidate(
        self,
        model: dict[str, Any],
        request: AIResourceRequest,
        risk: str,
        policy_mode: str,
        estimate: dict[str, Any],
        unknown_cost_policy: dict[str, Any],
    ) -> dict[str, Any]:
        breakdown = self._score_breakdown(model, request, risk, policy_mode, estimate)
        return {
            **model,
            "estimatedCostUsd": estimate["estimatedCostUsd"],
            "priceKnown": estimate["priceKnown"],
            "costEstimateSource": estimate["costEstimateSource"],
            "observedCostSamples": estimate.get("observedCostSamples"),
            "estimatedTokens": {
                "input": estimate["estimatedInputTokens"],
                "output": estimate["estimatedOutputTokens"],
                "reasoning": estimate["estimatedReasoningTokens"],
            },
            "unknownCostPolicy": unknown_cost_policy,
            "score": breakdown["score"],
            "scoreBreakdown": breakdown,
        }

    def _score_breakdown(
        self,
        model: dict[str, Any],
        request: AIResourceRequest,
        risk: str,
        policy_mode: str,
        estimate: dict[str, Any],
    ) -> dict[str, float]:
        weights = self._weights_for_policy(policy_mode, risk)
        capability_match = self._capability_match(request.required_capabilities, model["capabilities"])
        quality = self._performance_value(model, "qualityScore", UNOBSERVED_PERFORMANCE_PRIOR)
        success = self._performance_value(
            model,
            "observedSuccessRate",
            UNOBSERVED_PERFORMANCE_PRIOR,
        )
        rework = self._performance_value(model, "reworkRate", UNOBSERVED_PERFORMANCE_PRIOR)
        rework_rate_score = 1.0 - rework
        cost_efficiency = self._cost_efficiency(estimate["estimatedCostUsd"], request.budget_remaining_usd)
        cost_known = self._cost_known_score(model, estimate)
        privacy_locality = self._privacy_locality_fit(model, request, risk, policy_mode)
        context_fit = self._context_fit(int(model["contextWindow"] or 0), request.context_tokens_estimate)
        latency_penalty = self._latency_penalty(model.get("observedLatencyMs"))
        latency_score = 1.0 - latency_penalty
        task_risk = self._task_risk_fit(model, risk)
        rework_penalty = rework * weights["rework"]
        score = (
            capability_match * weights["capability"]
            + quality * weights["quality"]
            + success * weights["success"]
            + cost_efficiency * weights["cost"]
            + cost_known * weights["cost_known"]
            + privacy_locality * weights["locality"]
            + context_fit * weights["context"]
            + rework_rate_score * weights["rework"]
            + latency_score * weights["latency"]
            + task_risk * weights["task_risk"]
        )
        return {
            "score": round(score, 6),
            "capabilityMatchScore": round(capability_match, 6),
            "capabilityMatchContribution": round(capability_match * weights["capability"], 6),
            "qualityScore": round(quality * weights["quality"], 6),
            "observedSuccessScore": round(success, 6),
            "historicalSuccessScore": round(success, 6),
            "observedSuccessContribution": round(success * weights["success"], 6),
            "costEfficiencyScore": round(cost_efficiency * weights["cost"], 6),
            "costKnownScore": round(cost_known, 6),
            "costKnownContribution": round(cost_known * weights["cost_known"], 6),
            "localityFitScore": round(privacy_locality * weights["locality"], 6),
            "privacyLocalityScore": round(privacy_locality, 6),
            "privacyLocalityContribution": round(privacy_locality * weights["locality"], 6),
            "contextFitScore": round(context_fit * weights["context"], 6),
            "contextSizeScore": round(context_fit, 6),
            "contextSizeContribution": round(context_fit * weights["context"], 6),
            "reworkRateScore": round(rework_rate_score, 6),
            "reworkRateContribution": round(rework_rate_score * weights["rework"], 6),
            "reworkPenalty": round(rework_penalty, 6),
            "latencyScore": round(latency_score, 6),
            "latencyContribution": round(latency_score * weights["latency"], 6),
            "latencyPenalty": round(latency_penalty * weights["latency"], 6),
            "taskRiskScore": round(task_risk, 6),
            "taskRiskContribution": round(task_risk * weights["task_risk"], 6),
            "priceKnown": 1.0 if estimate["priceKnown"] else 0.0,
        }

    def _weights_for_policy(self, policy_mode: str, risk: str) -> dict[str, float]:
        if policy_mode == "economy":
            return {
                "capability": 0.07,
                "quality": 0.10,
                "success": 0.12,
                "cost": 0.28,
                "cost_known": 0.10,
                "locality": 0.18,
                "context": 0.07,
                "rework": 0.04,
                "latency": 0.03,
                "task_risk": 0.01,
            }
        if policy_mode == "maximum":
            return {
                "capability": 0.07,
                "quality": 0.34,
                "success": 0.18,
                "cost": 0.03,
                "cost_known": 0.03,
                "locality": 0.04,
                "context": 0.16,
                "rework": 0.07,
                "latency": 0.04,
                "task_risk": 0.04,
            }
        if policy_mode == "critical" or risk in {"high", "critical"}:
            return {
                "capability": 0.08,
                "quality": 0.31,
                "success": 0.20,
                "cost": 0.04,
                "cost_known": 0.04,
                "locality": 0.04,
                "context": 0.12,
                "rework": 0.11,
                "latency": 0.02,
                "task_risk": 0.04,
            }
        if risk == "low":
            return {
                "capability": 0.08,
                "quality": 0.12,
                "success": 0.16,
                "cost": 0.26,
                "cost_known": 0.08,
                "locality": 0.16,
                "context": 0.08,
                "rework": 0.04,
                "latency": 0.03,
                "task_risk": 0.01,
            }
        return {
            "capability": 0.08,
            "quality": 0.22,
            "success": 0.18,
            "cost": 0.16,
            "cost_known": 0.06,
            "locality": 0.10,
            "context": 0.10,
            "rework": 0.06,
            "latency": 0.02,
            "task_risk": 0.02,
        }

    def _capability_match(self, required: list[str], capabilities: list[str]) -> float:
        required_values = {item.strip().lower() for item in required if item.strip()}
        if not required_values:
            return 1.0
        available = {item.strip().lower() for item in capabilities if item.strip()}
        return len(required_values & available) / len(required_values)

    def _cost_known_score(self, model: dict[str, Any], estimate: dict[str, Any]) -> float:
        if model["locality"] not in REMOTE_LOCALITIES:
            return 1.0
        return 1.0 if estimate["priceKnown"] else 0.0

    def _privacy_locality_fit(
        self,
        model: dict[str, Any],
        request: AIResourceRequest,
        risk: str,
        policy_mode: str,
    ) -> float:
        if model["locality"] == "local":
            return 1.0
        if request.privacy_level == "local_private":
            return 0.0
        if request.privacy_level in {"private", "sensitive", "confidential"}:
            return 0.35
        if policy_mode == "economy":
            return 0.45
        if risk == "low":
            return 0.55
        return 0.75

    def _task_risk_fit(self, model: dict[str, Any], risk: str) -> float:
        quality = self._performance_value(model, "qualityScore", UNOBSERVED_PERFORMANCE_PRIOR)
        success = self._performance_value(
            model,
            "observedSuccessRate",
            UNOBSERVED_PERFORMANCE_PRIOR,
        )
        rework_fit = 1.0 - self._performance_value(
            model,
            "reworkRate",
            UNOBSERVED_PERFORMANCE_PRIOR,
        )
        if risk in {"high", "critical"}:
            return self._bounded((quality * 0.45) + (success * 0.45) + (rework_fit * 0.10))
        if risk == "low":
            return 1.0 if success >= 0.70 else 0.70
        return self._bounded((quality * 0.35) + (success * 0.45) + (rework_fit * 0.20))

    def _cost_efficiency(self, estimated_cost: float | None, budget_remaining: float | None) -> float:
        if estimated_cost is None:
            return 0.15
        if estimated_cost <= 0:
            return 1.0
        budget = max(float(budget_remaining) if budget_remaining is not None else 0.25, 0.01)
        return self._bounded(1.0 - min(float(estimated_cost) / budget, 1.0))

    def _context_fit(self, context_window: int, requested_tokens: int) -> float:
        if not context_window or requested_tokens <= 0:
            return 1.0
        if requested_tokens <= context_window:
            return self._bounded(context_window / max(requested_tokens, 1))
        return 0.45

    def _latency_penalty(self, latency_ms: Any) -> float:
        if latency_ms is None:
            return 0.1
        return min(float(latency_ms) / 10_000, 1.0)

    def _select_reviewer(
        self,
        candidates: list[dict[str, Any]],
        selected: dict[str, Any] | None,
        risk: str,
    ) -> dict[str, Any] | None:
        if risk not in {"high", "critical"} or selected is None:
            return None
        reviewer_candidates = [
            item
            for item in candidates
            if item["id"] != selected["id"] and "review" in {cap.lower() for cap in item["capabilities"]}
        ]
        return max(reviewer_candidates, key=lambda item: item["score"], default=None)

    def _premium_approval(
        self,
        *,
        selected: dict[str, Any] | None,
        require_approval_over_usd: float | None,
    ) -> dict[str, Any]:
        """Evalúa el gate premium de la política de rol y devuelve su evidencia auditable.

        Premium es una decisión de política, no una etiqueta: la selección solo lo es cuando su costo
        estimado es CONOCIDO y supera estrictamente el umbral del rol. Un costo desconocido nunca se
        trata como 0 ni como premium: lo resuelve la política de costo desconocido, que es la única
        autorizada a exigir aprobación por falta de precio.
        """
        threshold = (
            float(require_approval_over_usd)
            if require_approval_over_usd is not None and float(require_approval_over_usd) > 0
            else None
        )
        estimated_cost = selected.get("estimatedCostUsd") if selected else None
        if selected is None:
            reason = "no_selection"
        elif threshold is None:
            reason = "no_premium_threshold_in_policy"
        elif estimated_cost is None:
            reason = "cost_unknown_deferred_to_unknown_cost_policy"
        elif float(estimated_cost) > threshold:
            reason = "premium_cost_over_policy_threshold"
        else:
            reason = "cost_within_policy_threshold"
        return {
            "required": reason == "premium_cost_over_policy_threshold",
            "reason": reason,
            "thresholdUsd": threshold,
            "estimatedCostUsd": estimated_cost,
            "costTier": self._cost_tier(selected),
        }

    def _requires_approval(
        self,
        *,
        selected: dict[str, Any] | None,
        unknown_cost_policy: dict[str, Any],
        premium_approval: dict[str, Any],
    ) -> bool:
        if selected is None:
            return False
        if unknown_cost_policy.get("action") == "require_approval":
            return True
        return bool(premium_approval.get("required"))

    def _max_tokens(self, selected: dict[str, Any] | None, request: AIResourceRequest) -> int | None:
        if selected is None:
            return None
        model_limit = int(selected["maxOutputTokens"] or 0) or None
        if request.max_tokens is None:
            return model_limit
        if model_limit is None:
            return request.max_tokens
        return min(request.max_tokens, model_limit)

    def _estimated_output_tokens(self, model: dict[str, Any], request: AIResourceRequest) -> int:
        model_limit = int(model.get("maxOutputTokens") or 4096)
        if request.max_tokens is not None:
            return min(request.max_tokens, model_limit)
        return min(max(int(request.context_tokens_estimate * 0.1), 512), model_limit)

    def _cost_tier(self, selected: dict[str, Any] | None) -> str | None:
        if selected is None:
            return None
        cost = selected.get("estimatedCostUsd")
        if cost is None:
            return "unknown"
        if selected["locality"] == "local" or float(cost) <= 0.01:
            return "cheap"
        return "premium"

    def _decision_reason(
        self,
        request: AIResourceRequest,
        selected: dict[str, Any] | None,
        approval_required: bool,
        budget_stop: bool,
    ) -> str:
        if selected is None and budget_stop:
            return "No candidate stayed inside the remaining budget."
        if selected is None:
            return "No AI resource satisfied policy and capability filters."
        approval = " Approval is required before execution." if approval_required else ""
        return (
            f"Selected {selected['providerId']}/{selected['model']} for {request.task_type} "
            f"using deterministic explainable scoring.{approval}"
        )

    def _record_routing_decision(self, *, request: AIResourceRequest, decision: dict[str, Any]) -> None:
        selected = decision.get("selected") or {}
        reviewer = decision.get("reviewerSelection") or {}
        self.connection.execute(
            """
            INSERT INTO ai_routing_decisions
                (id, project_id, task_type, risk_level, selected_provider, selected_model,
                 selected_runtime, reviewer_provider, reviewer_model, reviewer_runtime,
                 local_vs_remote, cost_tier, multi_model_quorum, context_compression, max_tokens,
                 budget_stop, approval_required, estimated_cost_usd, actual_cost_usd, usage_status,
                 candidates_json, rejected_json, decision_reason, score_breakdown_json,
                 policy_result_json, workflow_run_id, workflow_step_id, agent_id, task_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.get("routingDecisionId") or f"ai-routing-{uuid.uuid4()}",
                request.project_id,
                request.task_type,
                request.risk_level,
                selected.get("providerId"),
                selected.get("model"),
                selected.get("runtime"),
                reviewer.get("providerId"),
                reviewer.get("model"),
                reviewer.get("runtime"),
                decision.get("localVsRemote"),
                decision.get("costTier"),
                1 if decision.get("multiModelQuorum") else 0,
                1 if decision.get("contextCompression") else 0,
                decision.get("maxTokens"),
                1 if decision.get("budgetStop") else 0,
                1 if decision.get("approvalRequired") else 0,
                decision.get("estimatedCostUsd"),
                decision.get("actualCostUsd"),
                decision.get("usageStatus"),
                json_dumps(decision.get("candidates") or []),
                json_dumps(decision.get("rejected") or []),
                decision.get("decisionReason", ""),
                json_dumps(decision.get("scoreBreakdown") or {}),
                json_dumps(decision.get("policyResult") or {}),
                request.workflow_run_id,
                request.workflow_step_id,
                request.agent_id,
                request.task_id,
                utc_now(),
            ),
        )

    def _token_values(self, provider_usage: dict[str, Any] | None) -> dict[str, int | None]:
        if provider_usage is None:
            return {
                "input": None,
                "cached_input": None,
                "output": None,
                "reasoning": None,
                "tool": None,
                "total": None,
            }
        values = {name: self._first_token_value(provider_usage, keys) for name, keys in TOKEN_KEYS.items()}
        if values["total"] is None and any(values[key] is not None for key in values if key != "total"):
            values["total"] = sum(int(values[key] or 0) for key in values if key != "total")
        return values

    def _first_token_value(self, provider_usage: dict[str, Any], keys: tuple[str, ...]) -> int | None:
        for key in keys:
            if key in provider_usage and provider_usage[key] is not None:
                return int(provider_usage[key])
        return None

    def _public_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._public_selection(candidate),
            "estimatedCostUsd": candidate.get("estimatedCostUsd"),
            "priceKnown": candidate.get("priceKnown"),
            "costEstimateSource": candidate.get("costEstimateSource"),
            "observedCostSamples": candidate.get("observedCostSamples"),
            "score": candidate["score"],
            "scoreBreakdown": candidate["scoreBreakdown"],
            "unknownCostPolicy": candidate["unknownCostPolicy"],
        }

    def _public_selection(self, candidate: dict[str, Any] | None) -> dict[str, Any] | None:
        if not candidate:
            return None
        return {
            "providerId": candidate["providerId"],
            "model": candidate["model"],
            "runtime": candidate["runtime"],
            "locality": candidate["locality"],
            "privacyLevel": candidate["privacyLevel"],
            "contextWindow": candidate["contextWindow"],
            "maxOutputTokens": candidate["maxOutputTokens"],
            "freeTier": bool(candidate.get("freeTier")),
            "qualityScore": candidate["qualityScore"],
            "observedSuccessRate": candidate["observedSuccessRate"],
            "reworkRate": candidate["reworkRate"],
            "profileSource": candidate.get("profileSource", "explicit"),
            "performanceStatus": candidate.get("performanceStatus", "configured_prior"),
            "performanceSampleCount": int(candidate.get("performanceSampleCount") or 0),
        }

    def _rejected(self, model: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "providerId": model["providerId"],
            "model": model["model"],
            "runtime": model["runtime"],
            "profileSource": model.get("profileSource", "explicit"),
            "reason": reason,
        }

    def _weighted_update(self, current: float | None, observed: float, *, weight: float = 0.45) -> float:
        base = float(current if current is not None else 0.5)
        return round(self._bounded(base * (1.0 - weight) + observed * weight), 6)

    def _performance_value(
        self,
        model: dict[str, Any],
        key: str,
        default: float,
    ) -> float:
        value = model.get(key)
        return self._bounded(default if value is None else float(value))

    def _bounded(self, value: float) -> float:
        return max(0.0, min(value, 1.0))
