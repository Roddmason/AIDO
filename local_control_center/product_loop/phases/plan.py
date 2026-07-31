"""Fase Plan: materializa el plan técnico del TechnicalLeadPlanner como estado y artefacto reales.

Antes de este módulo, ``TechnicalLeadPlanner.plan()`` calculaba quality gates agregados, handoffs y
estrategia branch/worktree que el coordinator descartaba en el mismo stack frame, y el estado
``architecture_review`` no tenía ningún productor. Ahora la fase decide con el intent/risk ya
clasificado: los mensajes de bajo riesgo (docs/tests/cleanup/bugfix) saltan el plan con motivo
durable visible (``specDriven: skipped_low_risk``), y el resto emite ``architecture_review`` con el
plan persistido como artefacto ``technical_plan`` y sellado en el contexto durable. Best-effort: un
fallo de la fase deja evento y el run continúa al backlog (el plan es gobierno, no gate duro).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

# Intents cuyo riesgo bajo no amerita plan técnico formal: el carril rápido del diseño §8.1.
FAST_LANE_INTENTS = {"docs", "tests", "cleanup", "bugfix"}
SKIPPED_LOW_RISK = "skipped_low_risk"
PLANNED = "planned"


def plan_phase_decision(intent: dict[str, Any]) -> str:
    """Decide si el run amerita la fase Plan o toma el carril rápido, desde el intent clasificado."""
    risk = str(intent.get("risk") or "").strip().lower()
    intents = {str(item).strip().lower() for item in intent.get("intents") or [] if str(item).strip()}
    if risk == "low" and intents and intents <= FAST_LANE_INTENTS:
        return SKIPPED_LOW_RISK
    return PLANNED


def technical_plan_view(planned: Any, *, agent_tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Vista durable del plan completo del TechnicalLeadPlanner (lo que antes se botaba).

    Conserva los agregados no persistidos en ``agent_tasks``: quality gates del plan, handoffs y la
    estrategia branch/worktree. Devuelve ``None`` cuando el planner no entregó forma de plan (modo
    runner-inyectado legacy que retorna solo la lista de tareas).
    """
    if not isinstance(planned, dict):
        return None
    return {
        "qualityGates": planned.get("quality_gates") or planned.get("qualityGates") or [],
        "assignmentHandoffs": planned.get("assignment_handoffs") or planned.get("assignmentHandoffs") or [],
        "branchWorktreePlan": planned.get("branch_worktree_plan") or planned.get("branchWorktreePlan") or {},
        "taskCount": len(agent_tasks),
        "roles": sorted({str(task.get("role") or "") for task in agent_tasks if task.get("role")}),
    }


def render_plan_md(technical_plan: dict[str, Any], *, generated_header: str) -> str:
    """Render Markdown del plan técnico para el worktree (.aido/specs/<dir>/plan.md)."""
    lines = [generated_header, "", "# Technical plan", ""]
    roles = technical_plan.get("roles") or []
    if roles:
        lines.extend(["## Team", ""])
        lines.extend(f"- {role}" for role in roles)
        lines.append("")
    gates = technical_plan.get("qualityGates") or []
    if gates:
        lines.extend(["## Quality gates", ""])
        for gate in gates:
            label = gate.get("id") or gate.get("name") if isinstance(gate, dict) else gate
            lines.append(f"- {label}")
        lines.append("")
    strategy = technical_plan.get("branchWorktreePlan") or {}
    if strategy:
        lines.extend(["## Branch / worktree strategy", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(strategy.items()))
        lines.append("")
    return "\n".join(lines) + "\n"
