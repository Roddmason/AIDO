"""Proveedores de decisión con contrato estricto y envío remoto de metadatos acotados.

Jev recibe opciones opacas y categorías permitidas: nunca identificadores, texto libre,
huellas ni correlaciones. Los errores públicos son códigos constantes sin cuerpos HTTP.
El proveedor determinista conserva la selección existente sin inventar probabilidades.

@author Rodrigo Mason
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Annotated, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.process_supervision.context import assert_external_boundary

from .config import DecisionConfig
from .models import DecisionRequest, DecisionResult
from .transport import JevAsyncTransport

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_PAYLOAD_BYTES = 65_536
MAX_RESPONSE_BYTES = 65_536
MAX_CANDIDATES = 255
_TASK_TYPES = frozenset(
    {"bug", "feature", "research", "architecture", "review", "security", "unreal_asset", "build", "unknown"}
)
_RISKS = frozenset({"low", "medium", "high", "critical"})
_METADATA_ENUMS = {
    "provider": frozenset({"api", "gateway", "local", "cli"}),
    "family": frozenset(
        {"openai", "anthropic", "google", "ollama", "nvidia_nim", "typesafe", "codex", "claude", "gemini"}
    ),
    "capability": frozenset({"code", "reasoning", "tools", "search", "vision", "json", "general"}),
    "locality": frozenset({"local", "remote"}),
    # Explicit egress allowlist from team_scheduler.scheduler.ALL_ROLES; new roles require review.
    "role": frozenset(
        {
            "aido_lead",
            "product_owner",
            "project_manager",
            "scrum_master",
            "architect",
            "technical_lead",
            "backend_engineer",
            "frontend_engineer",
            "mobile_engineer",
            "database_engineer",
            "data_engineer",
            "qa_engineer",
            "security_engineer",
            "pentester",
            "devops_engineer",
            "researcher",
            "release_manager",
        }
    ),
}
_INSTRUCTIONS = {
    "runtime_model_ranking": "Select the candidate whose permitted capability and quality metadata best fits the task.",
    "workflow_classification": "Select the candidate workflow that best fits the task type and risk metadata.",
    "escalation_decision": "Select the level of review warranted by the task type and risk metadata.",
    "agent_role_selection": "Select the candidate whose permitted capability metadata best fits the task.",
}
_ENUM_DESCRIPTIONS = {
    "workflow_classification": {
        "bug": "Correct defective behavior",
        "feature": "Implement requested functionality",
        "research": "Investigate evidence",
        "architecture": "Design software architecture",
        "review": "Review an existing change",
        "security": "Assess security",
        "unreal_asset": "Work on an Unreal Engine asset",
        "build": "Build or package software",
    },
    "escalation_decision": {
        "continue": "Continue under existing deterministic permissions",
        "deterministic_fallback": "Use the existing deterministic selection",
        "specialist_review": "Seek specialist review",
        "human_review": "Seek human review",
    },
}

Probability = Annotated[StrictFloat, Field(ge=0, le=1, allow_inf_nan=False)]
TokenCount = Annotated[StrictInt, Field(ge=0)]


class _ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability


class _Answers(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: _ChoiceAnswer


class _Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_tokens: TokenCount
    output_tokens: TokenCount


class _JevResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: str
    answers: _Answers
    usage: _Usage


class ProviderError(RuntimeError):
    """Fallo tipado cuyo código público nunca incluye datos del proveedor o credenciales."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DecisionProvider(Protocol):
    """Contrato asíncrono para proponer una decisión sin ejecutar la acción seleccionada."""

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        """Devuelve la propuesta validada o lanza un error tipado."""
        ...


class DeterministicDecisionProvider:
    """Conserva la decisión efectiva previa sin presentar puntajes como confianza estadística."""

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        """Proyecta la selección efectiva existente sin inferencia ni efectos externos."""
        selected = request.effective_decision
        return DecisionResult(
            selected=selected,
            ranking=(selected,) if selected is not None else (),
            probabilities={},
            confidence=None,
            margin=None,
            engine="deterministic",
            provider="deterministic",
            model=None,
            version=None,
            decision_type=request.decision_type,
            reason_code="deterministic_selected" if selected is not None else "deterministic_abstained",
        )


def _safe_metadata(metadata: dict) -> dict[str, str | float | int | bool]:
    safe: dict[str, str | float | int | bool] = {}
    for key, allowed in _METADATA_ENUMS.items():
        value = metadata.get(key)
        if isinstance(value, str) and value in allowed:
            safe[key] = value
    quality = metadata.get("quality_score")
    if type(quality) in {float, int} and 0 <= quality <= 1 and math.isfinite(quality):
        safe["quality_score"] = quality
    window = metadata.get("context_window")
    if type(window) is int and 0 <= window <= 16_000_000:
        safe["context_window"] = window
    if type(metadata.get("free_tier")) is bool:
        safe["free_tier"] = metadata["free_tier"]
    return safe


def _request_payload(request: DecisionRequest, model: str) -> tuple[bytes, dict[str, str]]:
    candidates = request.candidates
    candidate_ids = [candidate.id for candidate in candidates]
    if (
        not 1 <= len(candidates) <= MAX_CANDIDATES
        or len(set(candidate_ids)) != len(candidate_ids)
        or set(candidate_ids) != request.constraints.allowed_candidates
        or request.decision_type not in _INSTRUCTIONS
    ):
        raise ProviderError("invalid_request")
    mapping = {f"c{index}": candidate.id for index, candidate in enumerate(candidates)}
    criteria = {f"c{index}": _safe_metadata(candidate.metadata) for index, candidate in enumerate(candidates)}
    enum_descriptions = _ENUM_DESCRIPTIONS.get(request.decision_type)
    if enum_descriptions is not None:
        for index, candidate in enumerate(candidates):
            if candidate.id not in enum_descriptions:
                raise ProviderError("invalid_request")
            criteria[f"c{index}"]["description"] = enum_descriptions[candidate.id]
    task_type = request.context.task_type
    risk = request.context.risk
    payload = {
        "state": {
            "task_type": task_type if task_type in _TASK_TYPES else "unknown",
            "risk": risk if risk in _RISKS else "unknown",
        },
        "model": model,
        "questions": {
            "decision": {
                "type": "choice",
                "instructions": _INSTRUCTIONS[request.decision_type],
                "criteria": criteria,
            }
        },
    }
    body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_PAYLOAD_BYTES:
        raise ProviderError("invalid_request")
    return body, mapping


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _parse_response(raw: bytes) -> _JevResponse:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_keys)
        return _JevResponse.model_validate(value)
    except (ValueError, RecursionError):
        raise ProviderError("invalid_response") from None


class JevDecisionProvider:
    """Consulta el endpoint oficial con opciones opacas, sin reintentos ni redirecciones."""

    def __init__(
        self,
        config: DecisionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        credential_resolver: CredentialResolver | None = None,
    ):
        self.config = config
        self.transport = transport
        self.credential_resolver = credential_resolver or CredentialResolver()

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        """Valida privacidad y contrato, limita la espera total y entrega códigos de error redactados."""
        if request.context.privacy_mode != "metadata_only":
            raise ProviderError("privacy_blocked")
        if self.config.endpoint != JEV_ENDPOINT:
            raise ProviderError("invalid_endpoint")
        if (
            self.config.model != "jev-1.13.0"
            or self.config.version != "1.13.0"
            or not math.isfinite(self.config.timeout_seconds)
            or self.config.timeout_seconds <= 0
            or not math.isfinite(self.config.probability_tolerance)
            or not 0 <= self.config.probability_tolerance <= 0.001
        ):
            raise ProviderError("invalid_config")
        body, mapping = _request_payload(request, self.config.model)
        # Only environment references resolve synchronously without blocking OS/network IO.
        if not re.fullmatch(r"env:[A-Z_][A-Z0-9_]*", self.config.api_key_reference):
            raise ProviderError("credential_unavailable")
        try:
            assert_external_boundary()
        except RuntimeError:
            raise ProviderError("external_boundary") from None
        try:
            credential = self.credential_resolver.resolve(self.config.api_key_reference)
        except Exception:
            raise ProviderError("credential_unavailable") from None
        if (
            not credential.configured
            or not isinstance(credential.value, str)
            or not 1 <= len(credential.value) <= 4096
            or any(not 33 <= ord(character) <= 126 for character in credential.value)
        ):
            raise ProviderError("credential_unavailable")
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                async with (
                    httpx.AsyncClient(
                        transport=self.transport or JevAsyncTransport(timeout=self.config.timeout_seconds),
                        timeout=self.config.timeout_seconds,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client,
                    client.stream(
                        "POST",
                        self.config.endpoint,
                        content=body,
                        headers={
                            "Authorization": f"Bearer {credential.value}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ) as response,
                ):
                    if response.status_code != 200:
                        raise ProviderError("http_error")
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise ProviderError("invalid_response")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ProviderError("response_too_large")
                        raw.extend(chunk)
                parsed = _parse_response(bytes(raw))
                return self._result(parsed, mapping, request.decision_type)
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError("timeout") from None
        except httpx.HTTPError:
            raise ProviderError("transport_error") from None

    def _result(self, response: _JevResponse, mapping: dict[str, str], decision_type: str) -> DecisionResult:
        if response.model != self.config.model:
            raise ProviderError("model_mismatch")
        answer = response.answers.decision
        probabilities = answer.probabilities
        if (
            set(probabilities) != set(mapping)
            or abs(math.fsum(probabilities.values()) - 1) > self.config.probability_tolerance
        ):
            raise ProviderError("invalid_distribution")
        if answer.choice not in mapping or probabilities[answer.choice] != max(probabilities.values()):
            raise ProviderError("invalid_choice")
        ranking = sorted(mapping, key=lambda key: (-probabilities[key], key != answer.choice))
        first = probabilities[ranking[0]]
        second = probabilities[ranking[1]] if len(ranking) > 1 else 0.0
        return DecisionResult(
            selected=mapping[answer.choice],
            ranking=tuple(mapping[key] for key in ranking),
            probabilities={mapping[key]: value for key, value in probabilities.items()},
            confidence=answer.confidence,
            margin=first - second,
            engine="typesafe",
            provider="jev",
            model=response.model,
            version=self.config.version,
            decision_type=decision_type,
            reason_code="provider_selected",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
