"""Observadores de decisiones reales, sin retornar preferencias ni modificar sus entradas.

La frontera síncrona espera una evaluación acotada y nunca crea tareas huérfanas.
Los fallos de evidencia se registran y no cambian autorizaciones operacionales.
@author Rodrigo Mason
"""

from __future__ import annotations

import asyncio
import sqlite3

from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.diagnostics import diagnostic_event
from local_control_center.shared.redaction import redact_secrets

from .config import resolve_config
from .models import (
    ESCALATIONS,
    WORKFLOWS,
    DecisionCandidate,
    DecisionConstraints,
    DecisionContext,
    DecisionOutcome,
    DecisionRequest,
    fingerprint,
    normalized_risk,
)
from .repository import DecisionRepository
from .service import ShadowDecisionEngine


def _enabled(connection: sqlite3.Connection, project_id: str | None) -> bool:
    return (
        resolve_setting_value(connection=connection, key="decision_engine.enabled", project_id=project_id)
        is True
    )


def _failure(operation: str) -> None:
    diagnostic_event(
        "decision_evidence_failed",
        component="decision_engine",
        operation=operation,
        outcome="evidence_unavailable",
        level="ERROR",
    )


def _observe(connection: sqlite3.Connection, requests: list[DecisionRequest]) -> None:
    try:
        config = resolve_config(connection, requests[0].project_id)
        if not config.active:
            return
        project_id = requests[0].project_id
        force_local = resolve_setting_value(
            connection=connection, key="project.routing.forceLocal", project_id=project_id
        )
        remote_enabled = all(
            resolve_setting_value(connection=connection, key=key, project_id=project_id) is True
            for key in ("runtime.remote.enabled", "project.runtime.remote.enabled")
        )
        if force_local or not remote_enabled:
            requests = [
                request.model_copy(
                    update={"context": request.context.model_copy(update={"privacy_mode": "local_only"})}
                )
                for request in requests
            ]
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            _failure("sync_observer_in_event_loop")
            return

        async def observe_all() -> None:
            engine = ShadowDecisionEngine(connection, config=config)
            for request in requests:
                await engine.observe(request)

        asyncio.run(observe_all())
    except Exception:
        _failure("observe")


def _identity(candidate: dict) -> str:
    value = ":".join(str(candidate.get(key) or "") for key in ("providerId", "runtime", "model"))
    return value if len(value) <= 256 and redact_secrets(value) == value else f"resource-{fingerprint(value)}"


def observe_resource_decision(
    connection: sqlite3.Connection,
    *,
    decision: dict,
    project_id: str | None,
    execution_id: str | None = None,
    task_id: str | None = None,
    risk: str = "medium",
    privacy_mode: str = "metadata_only",
    routing_latency_ms: float | None = None,
) -> None:
    """Observa sólo la allowlist ya filtrada por AIResourceManager; jamás toma rejected."""
    try:
        if not _enabled(connection, project_id):
            return
        risk = normalized_risk((decision.get("policyResult") or {}).get("effectiveRiskLevel") or risk)
        candidates = tuple(
            DecisionCandidate(
                id=_identity(item),
                metadata={
                    "family": item.get("runtime"),
                    "locality": item.get("locality"),
                    "quality_score": item.get("qualityScore"),
                    "context_window": item.get("contextWindow"),
                    "free_tier": item.get("freeTier"),
                },
            )
            for item in decision.get("candidates", [])
        )
        allowed = frozenset(item.id for item in candidates)
        selected = _identity(decision["selected"]) if decision.get("selected") else None
        request = DecisionRequest(
            decision_type="runtime_model_ranking",
            candidates=candidates,
            context=DecisionContext(
                task_type="unknown",
                risk=risk,
                privacy_mode=privacy_mode,
                task_fingerprint=fingerprint([task_id, execution_id]),
            ),
            constraints=DecisionConstraints(allowed_candidates=allowed, deterministic_risk=risk),
            effective_decision=selected,
            project_id=project_id,
            execution_id=execution_id,
            source_decision_id=decision.get("routingDecisionId"),
            routing_latency_ms=routing_latency_ms,
        )
        _observe(connection, [request])
    except Exception:
        _failure("resource_decision")


def observe_intake(
    connection: sqlite3.Connection,
    *,
    decision: dict,
    team_plan: dict,
    project_id: str,
    source_id: str,
    job_id: str | None = None,
    privacy_mode: str = "metadata_only",
) -> None:
    """Compara clasificaciones/roles/escalación tras commit; no sustituye plan ni roles."""
    try:
        if not _enabled(connection, project_id):
            return
        risk = normalized_risk(decision["risk"])
        mapping = {
            "bugfix": "bug",
            "tests": "review",
            "refactor": "architecture",
            "migration": "architecture",
        }
        mapped = [mapping.get(item, item) for item in decision.get("intents", [])]
        workflow = next((item for item in mapped if item in WORKFLOWS), None)
        context = DecisionContext(
            task_type=workflow or "unknown",
            risk=risk,
            privacy_mode=privacy_mode,
            task_fingerprint=fingerprint(source_id),
        )
        roles = tuple(dict.fromkeys(item["role"] for item in team_plan.get("roles", [])))
        role = next((item for item in decision.get("requiredRoles", []) if item in roles), None)
        escalation = "human_review" if decision.get("planMode") in {"blocked", "ask"} else "continue"
        decisions = [
            ("workflow_classification", WORKFLOWS, workflow),
            ("agent_role_selection", roles, role),
            ("escalation_decision", ESCALATIONS, escalation),
        ]
        requests = [
            DecisionRequest(
                decision_type=kind,
                context=context,
                candidates=tuple(
                    DecisionCandidate(
                        id=item, metadata={"role": item} if kind == "agent_role_selection" else {}
                    )
                    for item in candidates
                ),
                constraints=DecisionConstraints(
                    allowed_candidates=frozenset(candidates), deterministic_risk=risk
                ),
                effective_decision=selected,
                project_id=project_id,
                job_id=job_id,
                source_decision_id=source_id,
            )
            for kind, candidates, selected in decisions
        ]
        _observe(connection, requests)
    except Exception:
        _failure("intake")


def record_resource_outcome(
    connection: sqlite3.Connection, *, source_decision_id: str | None, outcome: DecisionOutcome | dict
) -> None:
    """Vincula evidencia real por routingDecisionId, incluso si shadow fue desactivado después."""
    if not source_decision_id:
        return
    try:
        outcome = DecisionOutcome.model_validate(outcome)
        rows = connection.execute(
            "SELECT id FROM decision_receipts WHERE source_decision_id=? AND status='completed'",
            (source_decision_id,),
        ).fetchall()
        for row in rows:
            DecisionRepository(connection).record_outcome(row["id"], outcome)
    except Exception:
        _failure("outcome")
