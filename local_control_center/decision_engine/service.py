"""Gate shadow: valida recomendaciones y conserva siempre la decisión efectiva de AIDO.

No retorna comandos, reservas ni autoridad. Las cancelaciones se propagan y las
respuestas externas se revalidan incluso cuando vienen de un adaptador inyectado.
@author Rodrigo Mason
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
import uuid
from collections.abc import Callable
from itertools import pairwise

from local_control_center.shared.diagnostics import diagnostic_event
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.time import utc_now

from .config import DecisionConfig, real_jev_calls_enabled
from .models import RISK_ORDER, DecisionEngine, DecisionRequest, DecisionResult, effective_risk
from .providers import DeterministicDecisionProvider, JevDecisionProvider, ProviderError
from .repository import DecisionRepository


class ShadowDecisionEngine:
    """Evalúa sólo en shadow y registra intento antes de inferencia sin mantener transacciones."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        config: DecisionConfig,
        provider: DecisionEngine | None = None,
    ):
        self.connection = connection
        self.config = config
        self.injected_provider = provider is not None
        self.provider = provider or (
            JevDecisionProvider(config) if config.provider == "jev" else DeterministicDecisionProvider()
        )
        self.repository = DecisionRepository(connection)

    async def observe(self, request: DecisionRequest) -> dict | None:
        """Record a recommendation without changing the caller's effective decision."""
        if not self.config.active or self.config.mode != "shadow":
            return None
        return await self._evaluate(request)

    async def select_runtime(
        self, request: DecisionRequest, *, revalidate: Callable[[str], str | None]
    ) -> dict | None:
        """Rank only AIDO's allowlist; persist a choice only after fresh AIDO validation."""
        if not self.config.selects_runtime:
            return None
        request = request.model_copy(update={"effective_decision": None})
        return await self._evaluate(request, revalidate=revalidate)

    async def _evaluate(
        self, request: DecisionRequest, *, revalidate: Callable[[str], str | None] | None = None
    ) -> dict:
        selecting = revalidate is not None
        request = DecisionRequest.model_validate(request.model_dump())
        receipt = self._receipt(request)
        receipt["mode"] = "runtime_selection" if selecting else "shadow"
        self.repository.start(receipt)
        started = time.perf_counter()
        provider_failed = False
        attempted = False
        try:
            self._preflight(request)
            attempted = True
            async with asyncio.timeout(self.config.timeout_seconds):
                result = await self.provider.decide(request.model_copy(deep=True))
            result = DecisionResult.model_validate(result.model_dump())
            reason = self._validate_and_gate(request, result)
            risk = effective_risk(request.constraints.deterministic_risk, result.recommended_risk)
            receipt.update(
                engine=result.engine,
                provider=result.provider,
                model=result.model,
                version=result.version,
                ranking=list(result.ranking),
                probabilities=result.probabilities,
                confidence=result.confidence,
                margin=result.margin,
                jevRecommendation=result.selected if result.provider == "jev" else None,
                recommendation=result.selected,
                effectiveRisk=risk,
                reasonCode=reason,
                fallbackUsed=reason != "recommendation_usable",
                escalationRequired=(
                    risk in {"high", "critical"} or result.selected in {"human_review", "specialist_review"}
                ),
                decisionInference={
                    "inputTokens": result.input_tokens,
                    "outputTokens": result.output_tokens,
                    "cost": None,
                },
            )
            if selecting and reason == "recommendation_usable":
                rejection = revalidate(result.selected)
                if rejection:
                    receipt["reasonCode"] = rejection
                else:
                    receipt["effectiveDecision"] = result.selected
        except asyncio.CancelledError:
            receipt.update(status="cancelled", reasonCode="cancelled", fallbackUsed=True)
            raise
        except (TimeoutError, ProviderError) as error:
            code = "timeout" if isinstance(error, TimeoutError) else error.code
            provider_failed = attempted
            receipt.update(
                reasonCode=code, fallbackUsed=True, fallbackReason=code, providerFailed=provider_failed
            )
        except Exception:
            provider_failed = attempted
            receipt.update(
                reasonCode="invalid_provider_result",
                fallbackUsed=True,
                fallbackReason="invalid_provider_result",
                providerFailed=provider_failed,
            )
        finally:
            if selecting:
                # Holding a run is not a deterministic/provider fallback.
                receipt["fallbackUsed"] = False
                receipt["fallbackReason"] = None
            receipt["latencyMs"] = (time.perf_counter() - started) * 1000
            receipt["totalRoutingLatencyMs"] = (
                request.routing_latency_ms + receipt["latencyMs"]
                if request.routing_latency_ms is not None
                else None
            )
            if receipt["status"] != "cancelled":
                receipt["status"] = "completed"
            if receipt["fallbackUsed"]:
                receipt["fallbackReason"] = receipt["reasonCode"]
            if attempted and receipt["status"] != "cancelled" and self.config.provider == "jev":
                self.repository.provider_result(
                    self.config.configuration_fingerprint,
                    failed=provider_failed,
                    now=time.time(),
                    threshold=self.config.circuit_failure_threshold,
                    cooldown=self.config.circuit_cooldown_seconds,
                )
            receipt = self.repository.finish(receipt)
            event_type = (
                "decision_provider_failed"
                if provider_failed
                else "decision_runtime_selected"
                if selecting and receipt["effectiveDecision"] is not None
                else "decision_selection_blocked"
                if selecting
                else "decision_shadow_completed"
            )
            EventBus(self.connection).record_event(
                event_type=event_type,
                payload={
                    key: receipt[key]
                    for key in (
                        "decisionId",
                        "decisionType",
                        "provider",
                        "model",
                        "version",
                        "mode",
                        "confidence",
                        "margin",
                        "latencyMs",
                        "fallbackUsed",
                        "reasonCode",
                        "executionId",
                        "agentRunId",
                    )
                },
                project_id=request.project_id,
                job_id=request.job_id,
            )
            diagnostic_event(
                event_type,
                component="decision_engine",
                decisionId=receipt["decisionId"],
                decisionType=receipt["decisionType"],
                provider=receipt["provider"],
                latencyMs=receipt["latencyMs"],
                reasonCode=receipt["reasonCode"],
                fallback=receipt["fallbackUsed"],
            )
        return receipt

    def _preflight(self, request: DecisionRequest) -> None:
        if self.connection.in_transaction:
            raise ProviderError("transaction_active")
        if not request.candidates:
            raise ProviderError("no_eligible_candidates")
        if self.config.selects_runtime:
            if request.decision_type != "runtime_model_ranking":
                raise ProviderError("unsupported_active_decision")
            if self.config.provider != "jev":
                raise ProviderError("jev_required_for_selection")
        elif request.effective_decision is None:
            raise ProviderError("deterministic_blocked")
        if self.config.provider == "jev":
            if not self.injected_provider and not real_jev_calls_enabled():
                raise ProviderError("real_provider_calls_disabled")
            if not self.config.jev_enabled:
                raise ProviderError("provider_disabled")
            if request.context.privacy_mode == "local_only":
                raise ProviderError("privacy_blocked")
            state = self.repository.health(self.config.configuration_fingerprint)
            if state["open_until"] > time.time():
                raise ProviderError("circuit_open")

    def _validate_and_gate(self, request: DecisionRequest, result: DecisionResult) -> str:
        allowed = request.constraints.allowed_candidates
        if result.decision_type != request.decision_type or result.selected not in allowed:
            raise ProviderError("invalid_choice")
        if result.provider == "deterministic" and self.config.provider == "deterministic":
            if result.selected != request.effective_decision:
                raise ProviderError("invalid_choice")
            return "deterministic_provider"
        if (
            result.provider != "jev"
            or result.engine not in {"jev", "typesafe"}
            or result.model != self.config.model
            or result.version != self.config.version
        ):
            raise ProviderError("model_mismatch")
        probabilities = result.probabilities
        if set(probabilities) != allowed or not math.isclose(
            sum(probabilities.values()), 1, abs_tol=self.config.probability_tolerance, rel_tol=0
        ):
            raise ProviderError("invalid_distribution")
        expected = result.ranking
        if (
            len(expected) != len(allowed)
            or set(expected) != allowed
            or result.selected != expected[0]
            or any(probabilities[left] < probabilities[right] for left, right in pairwise(expected))
        ):
            raise ProviderError("invalid_choice")
        margin = probabilities[expected[0]] - (probabilities[expected[1]] if len(expected) > 1 else 0)
        if result.margin is None or not math.isclose(result.margin, margin, rel_tol=0, abs_tol=1e-9):
            raise ProviderError("invalid_distribution")
        risk = effective_risk(request.constraints.deterministic_risk, result.recommended_risk)
        if RISK_ORDER.index(risk) > RISK_ORDER.index(self.config.max_risk) and (
            request.decision_type != "escalation_decision"
            or result.selected not in {"human_review", "specialist_review"}
        ):
            return "risk_requires_review"
        if result.confidence is None or result.confidence < self.config.confidence_threshold:
            return "confidence_below_threshold"
        if margin < self.config.margin_threshold:
            return "margin_below_threshold"
        return "recommendation_usable"

    def _receipt(self, request: DecisionRequest) -> dict:
        return {
            "decisionId": f"decision-{uuid.uuid4()}",
            "timestamp": utc_now(),
            "status": "pending",
            "decisionType": request.decision_type,
            "taskFingerprint": request.context.task_fingerprint,
            "engineVersion": "1",
            "engine": "typesafe" if self.config.provider == "jev" else "deterministic",
            "provider": self.config.provider,
            "model": self.config.model if self.config.provider == "jev" else None,
            "version": self.config.version if self.config.provider == "jev" else None,
            "configurationFingerprint": self.config.configuration_fingerprint,
            "mode": "shadow",
            "candidates": [item.id for item in request.candidates],
            "ranking": [],
            "probabilities": {},
            "confidence": None,
            "margin": None,
            "effectiveDecision": request.effective_decision,
            "jevRecommendation": None,
            "recommendation": None,
            "deterministicRisk": request.constraints.deterministic_risk,
            "effectiveRisk": request.constraints.deterministic_risk,
            "privacyMode": request.context.privacy_mode,
            "maxCost": request.constraints.max_cost,
            "fallbackUsed": False,
            "fallbackReason": None,
            "escalationRequired": False,
            "providerFailed": False,
            "reasonCode": "pending",
            "latencyMs": None,
            "routingLatencyMs": request.routing_latency_ms,
            "totalRoutingLatencyMs": None,
            "policy": {
                "confidenceThreshold": self.config.confidence_threshold,
                "marginThreshold": self.config.margin_threshold,
                "maxRisk": self.config.max_risk,
                "probabilityTolerance": self.config.probability_tolerance,
                "timeoutSeconds": self.config.timeout_seconds,
            },
            "decisionInference": {"inputTokens": None, "outputTokens": None, "cost": None},
            "projectId": request.project_id,
            "jobId": request.job_id,
            "agentRunId": request.agent_run_id,
            "executionId": request.execution_id,
            "sourceDecisionId": request.source_decision_id,
        }
