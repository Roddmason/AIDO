"""Explainable AI resource selection with honest usage and cost accounting.

AIResourceManager keeps task routing separate from provider execution. It stores
model/runtime performance observations, scores candidates with explicit factors,
records immutable routing decisions, and preserves unknown usage as unknown rather
than inventing token or cost values.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

LOW_RISK_LEVELS = {"low", "routine"}
HIGH_RISK_LEVELS = {"high", "critical", "security", "release"}
CRITICAL_RISK_LEVELS = {"critical", "security", "release"}
REMOTE_LOCALITIES = {"remote"}
TOKEN_KEYS = {
    "input": ("input_tokens", "prompt_tokens"),
    "cached_input": ("cached_input_tokens", "cached_prompt_tokens"),
    "output": ("output_tokens", "completion_tokens"),
    "reasoning": ("reasoning_tokens",),
    "tool": ("tool_tokens",),
    "total": ("total_tokens",),
}


@dataclass(frozen=True)
class AIResourceRequest:
    """Inputs required to choose one model/runtime for a task."""

    task_type: str
    risk_level: str = "medium"
    context_tokens_estimate: int = 0
    required_capabilities: list[str] = field(default_factory=list)
    privacy_level: str = "remote_allowed"
    budget_remaining_usd: float | None = None
    max_tokens: int | None = None
    allow_unknown_cost: bool = False
    require_approval_for_unknown_cost: bool = True
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


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _camel_payload_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def _row_to_model(row: sqlite3.Row) -> dict[str, Any]:
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
        "observedLatencyMs": row["observed_latency_ms"],
        "observedSuccessRate": row["observed_success_rate"],
        "reworkRate": row["rework_rate"],
        "totalObservedTokens": row["total_observed_tokens"],
        "qualityScore": row["quality_score"],
        "locality": row["locality"],
        "privacyLevel": row["privacy_level"],
        "evidence": json_loads(row["evidence_json"], []),
        "enabled": _bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
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


def _required_capabilities_missing(required: list[str], capabilities: list[str]) -> list[str]:
    available = {item.strip().lower() for item in capabilities}
    return [item for item in required if item.strip().lower() not in available]


class AIResourceManager:
    """Selects AI resources using deterministic policy gates and explainable scoring."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

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
                 total_observed_tokens, quality_score, locality, privacy_level, evidence_json,
                 enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _float_or_none(
                    _camel_payload_value(payload, "inputPricePerMtok", "input_price_per_mtok")
                ),
                _float_or_none(
                    _camel_payload_value(
                        payload,
                        "cachedInputPricePerMtok",
                        "cached_input_price_per_mtok",
                    )
                ),
                _float_or_none(
                    _camel_payload_value(payload, "outputPricePerMtok", "output_price_per_mtok")
                ),
                _float_or_none(
                    _camel_payload_value(payload, "reasoningPricePerMtok", "reasoning_price_per_mtok")
                ),
                _int_or_none(_camel_payload_value(payload, "observedLatencyMs", "observed_latency_ms")),
                float(
                    _camel_payload_value(
                        payload,
                        "observedSuccessRate",
                        "observed_success_rate",
                        default=0.5,
                    )
                    or 0.5
                ),
                float(_camel_payload_value(payload, "reworkRate", "rework_rate", default=0.0) or 0.0),
                _int_or_none(_camel_payload_value(payload, "totalObservedTokens", "total_observed_tokens")),
                float(_camel_payload_value(payload, "qualityScore", "quality_score", default=0.5) or 0.5),
                str(_camel_payload_value(payload, "locality", default="remote")),
                str(_camel_payload_value(payload, "privacyLevel", "privacy_level", default="remote_allowed")),
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
        budget_stop = False
        runtime_statuses: dict[str, dict[str, Any]] | None = None
        unknown_cost_policy: dict[str, Any] = {
            "action": "not_applicable",
            "reason": "cost_known_or_local",
        }
        for model in self._list_enabled_models():
            rejection = self._hard_reject_reason(model, request)
            if rejection:
                rejected.append(self._rejected(model, rejection))
                continue
            if self._requires_runtime_executable_gate(model):
                if runtime_statuses is None:
                    runtime_statuses = self._runtime_statuses_by_provider(project_id=request.project_id)
                runtime_rejection = self._runtime_executable_reject_reason(
                    model=model,
                    runtime_statuses=runtime_statuses,
                )
                if runtime_rejection:
                    rejected.append(self._rejected(model, runtime_rejection))
                    continue
            estimate = self._estimate_cost(model, request)
            policy = self._unknown_cost_policy(model, request, estimate)
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
            candidate = self._candidate(model, request, risk, estimate, policy)
            candidates.append(candidate)

        selected = max(candidates, key=lambda item: item["score"], default=None)
        if selected and selected.get("unknownCostPolicy", {}).get("action") != "not_applicable":
            unknown_cost_policy = selected["unknownCostPolicy"]
        reviewer = self._select_reviewer(candidates, selected, risk)
        approval_required = self._requires_approval(
            selected=selected,
            unknown_cost_policy=unknown_cost_policy,
            risk=risk,
        )
        max_tokens = self._max_tokens(selected, request)
        context_window = int(selected["contextWindow"] or 0) if selected else 0
        context_compression = bool(
            selected and context_window and request.context_tokens_estimate > int(context_window * 0.8)
        )
        multi_model_quorum = bool(
            request.require_multi_model_quorum
            or risk == "critical"
            or (risk == "high" and selected is not None and selected["score"] < 0.72)
        )
        decision = {
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
                "unknownCostPolicy": unknown_cost_policy,
                "scoring": "deterministic_explainable",
                "opaqueMlUsed": False,
            },
        }
        if record:
            self._record_routing_decision(request=request, decision=decision)
        return decision

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
        updated_success = self._weighted_update(existing["observedSuccessRate"], 1.0 if success else 0.0)
        updated_rework = self._weighted_update(existing["reworkRate"], 1.0 if rework else 0.0)
        updated_quality = self._weighted_update(existing["qualityScore"], float(quality_score), weight=0.60)
        return self.upsert_model_performance(
            {
                **existing,
                "observedSuccessRate": updated_success,
                "reworkRate": updated_rework,
                "qualityScore": updated_quality,
                "observedLatencyMs": latency_ms or existing.get("observedLatencyMs"),
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
        usage_reported = provider_usage is not None and any(value is not None for value in token_values.values())
        token_status = "actual" if usage_reported else "unknown"
        usage_source = "actual" if usage_reported else "unknown"
        cost_status = "actual" if actual_cost_usd is not None else "estimated" if estimated_cost_usd is not None else "unknown"
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
            return self._get_model(provider_id=provider_id, model=model, runtime=runtime)
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

    @staticmethod
    def _requires_runtime_executable_gate(model: dict[str, Any]) -> bool:
        return (
            model["locality"] in REMOTE_LOCALITIES
            and str(model.get("runtime") or "").strip().lower() in {"api", "gateway"}
        )

    def _runtime_statuses_by_provider(self, *, project_id: str | None) -> dict[str, dict[str, Any]]:
        return {
            str(status.get("id") or ""): status
            for status in RuntimeStatusService(self.connection).list_provider_statuses(
                project_id=project_id
            )
            if str(status.get("id") or "").strip()
        }

    @staticmethod
    def _runtime_executable_reject_reason(
        *,
        model: dict[str, Any],
        runtime_statuses: dict[str, dict[str, Any]],
    ) -> str | None:
        provider_id = str(model.get("providerId") or "").strip()
        status = runtime_statuses.get(provider_id)
        if status is None:
            return f"Provider {provider_id} is not configured in runtime status."
        if status.get("executable") is True:
            return None
        reason = str(status.get("reason") or "").strip()
        return reason or f"Provider {provider_id} is not executable."

    def _hard_reject_reason(self, model: dict[str, Any], request: AIResourceRequest) -> str | None:
        missing = _required_capabilities_missing(request.required_capabilities, model["capabilities"])
        if missing:
            return f"missing_capabilities:{','.join(missing)}"
        if request.privacy_level == "local_private" and model["locality"] != "local":
            return "privacy_blocks_remote"
        context_window = int(model["contextWindow"] or 0)
        if context_window and request.context_tokens_estimate > context_window * 2:
            return "context_window_too_small"
        return None

    def _estimate_cost(self, model: dict[str, Any], request: AIResourceRequest) -> dict[str, Any]:
        output_tokens = self._estimated_output_tokens(model, request)
        input_price = model.get("inputPricePerMtok")
        output_price = model.get("outputPricePerMtok")
        reasoning_price = model.get("reasoningPricePerMtok")
        reasoning_tokens = int(output_tokens * 0.5) if "reasoning" in request.required_capabilities else 0
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
    ) -> dict[str, Any]:
        if estimate["estimatedCostUsd"] is not None or model["locality"] not in REMOTE_LOCALITIES:
            return {"action": "not_applicable", "reason": "cost_known_or_local"}
        if request.allow_unknown_cost and not request.require_approval_for_unknown_cost:
            return {
                "action": "allow",
                "reason": "unknown_remote_cost_allowed_by_policy",
                "providerId": model["providerId"],
                "model": model["model"],
            }
        if request.require_approval_for_unknown_cost:
            return {
                "action": "require_approval",
                "reason": "unknown_remote_cost_requires_approval",
                "providerId": model["providerId"],
                "model": model["model"],
            }
        return {
            "action": "reject",
            "reason": "unknown_remote_cost_rejected_by_policy",
            "providerId": model["providerId"],
            "model": model["model"],
        }

    def _candidate(
        self,
        model: dict[str, Any],
        request: AIResourceRequest,
        risk: str,
        estimate: dict[str, Any],
        unknown_cost_policy: dict[str, Any],
    ) -> dict[str, Any]:
        breakdown = self._score_breakdown(model, request, risk, estimate)
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
        estimate: dict[str, Any],
    ) -> dict[str, float]:
        weights = self._weights_for_risk(risk)
        quality = self._bounded(float(model["qualityScore"] or 0.5))
        success = self._bounded(float(model["observedSuccessRate"] or 0.5))
        rework = self._bounded(float(model["reworkRate"] or 0.0))
        cost_efficiency = self._cost_efficiency(estimate["estimatedCostUsd"], request.budget_remaining_usd)
        locality_fit = 1.0 if model["locality"] == "local" else 0.35 if risk == "low" else 0.65
        context_fit = self._context_fit(int(model["contextWindow"] or 0), request.context_tokens_estimate)
        latency_penalty = self._latency_penalty(model.get("observedLatencyMs"))
        rework_penalty = rework * weights["rework"]
        score = (
            quality * weights["quality"]
            + success * weights["success"]
            + cost_efficiency * weights["cost"]
            + locality_fit * weights["locality"]
            + context_fit * weights["context"]
            - rework_penalty
            - latency_penalty * weights["latency"]
        )
        return {
            "score": round(score, 6),
            "qualityScore": round(quality * weights["quality"], 6),
            "observedSuccessScore": round(success, 6),
            "observedSuccessContribution": round(success * weights["success"], 6),
            "costEfficiencyScore": round(cost_efficiency * weights["cost"], 6),
            "localityFitScore": round(locality_fit * weights["locality"], 6),
            "contextFitScore": round(context_fit * weights["context"], 6),
            "reworkPenalty": round(rework_penalty, 6),
            "latencyPenalty": round(latency_penalty * weights["latency"], 6),
            "priceKnown": 1.0 if estimate["priceKnown"] else 0.0,
        }

    def _weights_for_risk(self, risk: str) -> dict[str, float]:
        if risk == "low":
            return {
                "quality": 0.14,
                "success": 0.20,
                "cost": 0.34,
                "locality": 0.24,
                "context": 0.08,
                "rework": 0.20,
                "latency": 0.04,
            }
        if risk in {"high", "critical"}:
            return {
                "quality": 0.40,
                "success": 0.25,
                "cost": 0.05,
                "locality": 0.02,
                "context": 0.14,
                "rework": 0.30,
                "latency": 0.03,
            }
        return {
            "quality": 0.26,
            "success": 0.24,
            "cost": 0.20,
            "locality": 0.10,
            "context": 0.12,
            "rework": 0.24,
            "latency": 0.04,
        }

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

    def _requires_approval(
        self,
        *,
        selected: dict[str, Any] | None,
        unknown_cost_policy: dict[str, Any],
        risk: str,
    ) -> bool:
        if selected is None:
            return False
        if unknown_cost_policy.get("action") == "require_approval":
            return True
        return risk == "critical"

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
                f"ai-routing-{uuid.uuid4()}",
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
            "qualityScore": candidate["qualityScore"],
            "observedSuccessRate": candidate["observedSuccessRate"],
            "reworkRate": candidate["reworkRate"],
        }

    def _rejected(self, model: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "providerId": model["providerId"],
            "model": model["model"],
            "runtime": model["runtime"],
            "reason": reason,
        }

    def _weighted_update(self, current: float | None, observed: float, *, weight: float = 0.45) -> float:
        base = float(current if current is not None else 0.5)
        return round(self._bounded(base * (1.0 - weight) + observed * weight), 6)

    def _bounded(self, value: float) -> float:
        return max(0.0, min(value, 1.0))
