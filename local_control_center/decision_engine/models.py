"""Contratos inmutables de recomendaciones, restricciones y resultados observados.

No contienen grants, herramientas ni operaciones ejecutables.
@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

DecisionType = Literal[
    "agent_role_selection", "runtime_model_ranking", "workflow_classification", "escalation_decision"
]
Risk = Literal["low", "medium", "high", "critical"]
WORKFLOWS = ("bug", "feature", "research", "architecture", "review", "security", "unreal_asset", "build")
ESCALATIONS = ("continue", "deterministic_fallback", "specialist_review", "human_review")
RISK_ORDER = ("low", "medium", "high", "critical")
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


def normalized_risk(value: str) -> Risk:
    """Mapea R0/R1 a low y R2/R3/R4 al dominio ordinal real; rechaza desconocidos."""
    result = {"R0": "low", "R1": "low", "R2": "medium", "R3": "high", "R4": "critical"}.get(value, value)
    if result not in RISK_ORDER:
        raise ValueError("unknown_risk")
    return result


def effective_risk(deterministic: str, recommended: str | None) -> Risk:
    """Conserva como piso el riesgo determinista aunque el proveedor recomiende bajarlo."""
    baseline = normalized_risk(deterministic)
    proposal = normalized_risk(recommended) if recommended else baseline
    return max((baseline, proposal), key=RISK_ORDER.index)


def fingerprint(value: Any) -> str:
    """Identifica contenido/configuración sin persistir el material original."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DecisionCandidate(_Contract):
    """Identidad elegible suministrada por AIDO; metadata interna nunca es contexto libre."""

    id: str = Field(min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionContext(_Contract):
    """Contexto mínimo sin prompts, archivos, logs, credenciales ni datos personales."""

    task_type: Literal[
        "bug", "feature", "research", "architecture", "review", "security", "unreal_asset", "build", "unknown"
    ]
    risk: Risk
    task_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    privacy_mode: Literal["metadata_only", "local_only"] = "metadata_only"


class DecisionConstraints(_Contract):
    """Allowlist y restricciones fijadas antes de cualquier evaluación probabilística."""

    allowed_candidates: frozenset[str]
    deterministic_risk: Risk
    max_cost: Nonnegative | None = None


class DecisionRequest(_Contract):
    """Snapshot de una decisión ya tomada; su resultado efectivo nunca procede de Jev."""

    decision_type: DecisionType
    context: DecisionContext
    candidates: tuple[DecisionCandidate, ...] = Field(max_length=255)
    constraints: DecisionConstraints
    effective_decision: str | None
    project_id: str | None = None
    job_id: str | None = None
    agent_run_id: str | None = None
    execution_id: str | None = None
    source_decision_id: str | None = None
    routing_latency_ms: Nonnegative | None = None

    @model_validator(mode="after")
    def validate_candidates(self) -> DecisionRequest:
        """Rechaza duplicados, ampliación de allowlist y enums ajenos al dominio."""
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)) or set(ids) != self.constraints.allowed_candidates:
            raise ValueError("invalid_candidate_set")
        if self.effective_decision is not None and self.effective_decision not in ids:
            raise ValueError("ineligible_effective_decision")
        domain = {"workflow_classification": WORKFLOWS, "escalation_decision": ESCALATIONS}.get(
            self.decision_type
        )
        if domain and not set(ids).issubset(domain):
            raise ValueError("unknown_candidate_enum")
        if self.context.risk != self.constraints.deterministic_risk:
            raise ValueError("risk_mismatch")
        return self


class DecisionResult(_Contract):
    """Recomendación sin capacidad de ejecutar ni autorizar efectos externos."""

    selected: str | None
    ranking: tuple[str, ...]
    probabilities: dict[str, Probability]
    confidence: Probability | None
    margin: Probability | None
    engine: str
    provider: str
    model: str | None
    version: str | None
    decision_type: DecisionType
    reason_code: str
    fallback_used: bool = False
    escalation_required: bool = False
    recommended_risk: Risk | None = None
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None


class DecisionEngine(Protocol):
    """Puerto pluggable: devuelve recomendaciones tipadas sin conocer el control plane."""

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        """Evalúa exclusivamente candidatos permitidos por el llamador determinista."""
        ...


class DecisionOutcome(_Contract):
    """Mediciones posteriores del resultado efectivo, nunca del contrafactual Jev."""

    evidence_ref: str = Field(min_length=1, max_length=256)
    execution_succeeded: bool | None = None
    tests_passed: bool | None = None
    review_passed: bool | None = None
    retries: Annotated[int, Field(strict=True, ge=0)] | None = None
    duration_ms: Nonnegative | None = None
    tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    cost: Nonnegative | None = None
    human_override: bool | None = None
