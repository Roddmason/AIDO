"""Planificador de iteraciones: del brief aprobado y las historias ready a un plan ejecutable.

A partir del brief aprobado, las historias ready, la arquitectura, los agentes disponibles, los
runtimes ejecutables y los presupuestos, produce una iteración: un DAG de tareas (una por rol y por
historia, con dependencias por capas), asignaciones por rol, una estrategia de workspace, quality
gates, security gates basados en riesgo y un costo estimado SIEMPRE marcado como estimado. Materializa
el DAG en las tablas del slice backlog (agent_tasks/task_dependencies/agent_assignments) y persiste la
iteración de forma atómica; no ejecuta nada: solo planifica y persiste.
"""

from __future__ import annotations

from typing import Any

from local_control_center.shared.db import immediate_transaction

from .repository import BacklogRepository

ITERATION_PLANNER_ID = "iteration_planner"
APPROVED_BRIEF_STATUSES = {"approved", "published"}
READY_STORY_STATUSES = {"ready"}
DEFAULT_TASK_ROLES = ["backend_engineer", "frontend_engineer", "qa"]
# Capa de cada rol en el DAG intra-historia: una tarea depende de todas las de capa estrictamente menor.
ROLE_TIER = {
    "backend_engineer": 0,
    "developer": 0,
    "implementer": 0,
    "frontend_engineer": 1,
    "qa": 2,
    "qa_reviewer": 2,
    "security_reviewer": 2,
    "devops": 2,
}
DEFAULT_ROLE_TIER = 1
ROLE_FALLBACKS = {
    "backend_engineer": ["developer", "implementer"],
    "frontend_engineer": ["developer", "implementer"],
    "qa": ["qa_reviewer"],
    "qa_reviewer": ["qa"],
    "devops": ["developer"],
    "security_reviewer": ["technical_lead"],
}
TIER_BASE_HOURS = {0: 6.0, 1: 5.0, 2: 3.0}
BASE_QUALITY_GATES = ["lint", "typecheck", "unit_tests", "build", "coverage"]
SECURITY_SENSITIVE_KEYWORDS = (
    "auth",
    "login",
    "password",
    "credential",
    "token",
    "secret",
    "payment",
    "billing",
    "invoice",
    "pii",
    "personal data",
    "gdpr",
    "compliance",
    "encryption",
    "permission",
)
HIGH_RISK_SEVERITIES = {"high", "critical"}
# Runtimes que pueden operar en un workspace aislado (git worktree). Sin uno ejecutable, los agentes
# comparten un único workspace en vez de aislar por tarea/historia.
WORKTREE_CAPABLE_RUNTIMES = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
DEFAULT_TOKENS_PER_TASK = 80_000
DEFAULT_PRICE_PER_MTOK_USD = 3.0


def _positive_number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return default
    return float(value)


class IterationPlannerError(ValueError):
    """Se lanza cuando faltan o son inválidos los insumos del IterationPlanner."""


def _role_label(role: str) -> str:
    return role.replace("_", " ").capitalize()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _is_sensitive(story: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(story.get(field) or "") for field in ("title", "asA", "iWant", "soThat", "description")
    ).lower()
    return any(keyword in haystack for keyword in SECURITY_SENSITIVE_KEYWORDS)


def _estimate_hours(story: dict[str, Any], tier: int) -> float:
    points = story.get("storyPoints")
    factor = _positive_number(points, 3.0) / 3
    return round(TIER_BASE_HOURS.get(tier, 4.0) * factor, 1)


def _decompose_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for story in stories:
        roles = _dedupe([str(role) for role in (story.get("roles") or DEFAULT_TASK_ROLES)])
        for role in roles:
            tier = ROLE_TIER.get(role, DEFAULT_ROLE_TIER)
            nodes.append(
                {
                    "key": f"{story['id']}:{role}",
                    "storyId": story["id"],
                    "role": role,
                    "tier": tier,
                    "title": f"{_role_label(role)}: {story['title']}",
                    "estimateHours": _estimate_hours(story, tier),
                }
            )
    return nodes


def _build_dag_edges(
    nodes: list[dict[str, Any]], story_dependencies: list[dict[str, Any]]
) -> list[dict[str, str]]:
    by_story: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        by_story.setdefault(node["storyId"], []).append(node)
    edges: list[dict[str, str]] = []
    # Intra-historia: cada tarea depende de todas las de capa estrictamente menor de su misma historia.
    for story_nodes in by_story.values():
        for node in story_nodes:
            edges.extend(
                {"taskKey": node["key"], "dependsOnKey": other["key"]}
                for other in story_nodes
                if other["tier"] < node["tier"]
            )
    # Inter-historia: una arista por dependencia declarada (la tarea de menor capa de la historia
    # dependiente se enlaza con la de mayor capa de la prerequisito).
    for dependency in story_dependencies:
        story_id = str(dependency.get("storyId"))
        depends_on_id = str(dependency.get("dependsOnStoryId"))
        if story_id == depends_on_id:
            continue  # una historia no depende de sí misma (evita ciclos en el DAG)
        dependent = by_story.get(story_id)
        prerequisite = by_story.get(depends_on_id)
        if not dependent or not prerequisite:
            continue
        first = min(dependent, key=lambda node: node["tier"])
        last = max(prerequisite, key=lambda node: node["tier"])
        if first["key"] != last["key"]:
            edges.append({"taskKey": first["key"], "dependsOnKey": last["key"]})
    # Deduplica aristas (las inter-historia pueden repetirse o coincidir con una intra-historia).
    seen: set[tuple[str, str]] = set()
    unique_edges: list[dict[str, str]] = []
    for edge in edges:
        identity = (edge["taskKey"], edge["dependsOnKey"])
        if identity not in seen:
            seen.add(identity)
            unique_edges.append(edge)
    return unique_edges


def _assign_by_role(
    nodes: list[dict[str, Any]], available_agents: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], dict[str, list[str]], list[dict[str, str]]]:
    by_role: dict[str, list[str]] = {}
    for agent in available_agents:
        by_role.setdefault(str(agent["role"]), []).append(str(agent["agentId"]))
    cursor: dict[str, int] = {}
    assignments: list[dict[str, str]] = []
    assignments_by_role: dict[str, list[str]] = {}
    unassigned: list[dict[str, str]] = []
    for node in nodes:
        role = node["role"]
        pool_key = role
        candidates = by_role.get(role)
        if not candidates:
            for fallback in ROLE_FALLBACKS.get(role, []):
                if by_role.get(fallback):
                    candidates = by_role[fallback]
                    pool_key = fallback
                    break
        if not candidates:
            unassigned.append({"taskKey": node["key"], "storyId": node["storyId"], "role": role})
            continue
        # El cursor se indexa por el pool resuelto (rol o fallback) para repartir la carga aun cuando
        # dos roles distintos caen en el mismo pool de fallback.
        index = cursor.get(pool_key, 0)
        agent_id = candidates[index % len(candidates)]
        cursor[pool_key] = index + 1
        assignments.append({"taskKey": node["key"], "agentId": agent_id, "role": role})
        assignments_by_role.setdefault(role, []).append(agent_id)
    return assignments, assignments_by_role, unassigned


def _workspace_strategy(
    stories: list[dict[str, Any]], nodes: list[dict[str, Any]], executable_runtimes: list[str]
) -> dict[str, Any]:
    worktree_capable = any(str(runtime) in WORKTREE_CAPABLE_RUNTIMES for runtime in executable_runtimes)
    if len(nodes) <= 1:
        strategy, rationale = "single_shared_workspace", "A single task does not need isolation."
    elif not worktree_capable:
        strategy, rationale = (
            "single_shared_workspace",
            "No worktree-capable runtime is executable; agents share one workspace and serialize.",
        )
    elif len(stories) == 1:
        strategy, rationale = (
            "worktree_per_story",
            "One story: a dedicated worktree keeps its parallel role tasks together while isolated.",
        )
    else:
        strategy, rationale = (
            "worktree_per_task",
            "Multiple stories run in parallel: an isolated git worktree per task avoids agent conflicts.",
        )
    return {"strategy": strategy, "rationale": rationale, "isolation": strategy != "single_shared_workspace"}


def _quality_gates(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gates = [{"id": gate, "blocking": True} for gate in BASE_QUALITY_GATES]
    roles = {node["role"] for node in nodes}
    if {"frontend_engineer"} & roles and {"backend_engineer", "developer", "implementer"} & roles:
        gates.append({"id": "integration_tests", "blocking": True})
    return gates


def _security_gates(
    stories: list[dict[str, Any]], architecture: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    sensitive = [story for story in stories if _is_sensitive(story)]
    high_risk = [
        finding
        for finding in architecture
        if isinstance(finding, dict) and str(finding.get("severity") or "").lower() in HIGH_RISK_SEVERITIES
    ]
    gates: list[dict[str, Any]] = [
        {"id": "secret_scan", "riskLevel": "baseline", "blocking": True, "trigger": "always"}
    ]
    if sensitive or high_risk:
        triggers = [story["title"] for story in sensitive] + [
            str(finding.get("title") or "architecture risk") for finding in high_risk
        ]
        risk_level = "high" if high_risk else "elevated"
        gates.extend(
            {"id": gate, "riskLevel": risk_level, "blocking": True, "trigger": triggers}
            for gate in ("dependency_audit", "sast", "threat_review")
        )
    return gates


def _estimated_cost(nodes: list[dict[str, Any]], budgets: dict[str, Any]) -> dict[str, Any]:
    tokens_per_task = int(_positive_number(budgets.get("estimatedTokensPerTask"), DEFAULT_TOKENS_PER_TASK))
    price_per_mtok = _positive_number(budgets.get("pricePerMtokUsd"), DEFAULT_PRICE_PER_MTOK_USD)
    per_task = round(tokens_per_task / 1_000_000 * price_per_mtok, 4)
    amount = round(per_task * len(nodes), 4)
    max_cost = float(budgets["maxCostUsd"])
    return {
        "amountUsd": amount,
        "currency": str(budgets.get("currency") or "USD"),
        "estimated": True,
        "basis": (
            f"{len(nodes)} task(s) x {tokens_per_task} tokens x ${price_per_mtok}/Mtok; "
            "estimate only, not metered usage."
        ),
        "perTaskUsd": per_task,
        "tokensPerTask": tokens_per_task,
        "pricePerMtokUsd": price_per_mtok,
        "maxCostUsd": max_cost,
        "overBudget": amount > max_cost,
    }


class IterationPlanner:
    """Planifica una iteración y la materializa en el backlog a partir de los insumos del loop."""

    def __init__(self, repository: BacklogRepository):
        self.repository = repository

    def _validate(self, payload: dict[str, Any]) -> None:
        brief = payload.get("brief")
        if (
            not isinstance(brief, dict)
            or str(brief.get("status") or "").lower() not in APPROVED_BRIEF_STATUSES
        ):
            raise IterationPlannerError("IterationPlanner requires an approved product brief.")
        if not str(payload.get("briefId") or "").strip():
            raise IterationPlannerError("IterationPlanner requires a briefId.")
        stories = payload.get("stories")
        if not isinstance(stories, list) or not stories:
            raise IterationPlannerError("IterationPlanner requires at least one ready story.")
        for index, story in enumerate(stories):
            if not isinstance(story, dict) or not story.get("id") or not story.get("title"):
                raise IterationPlannerError(f"stories[{index}] requires id and title.")
            if str(story.get("status") or "").lower() not in READY_STORY_STATUSES:
                raise IterationPlannerError(f"stories[{index}] '{story.get('id')}' is not ready.")
        agents = payload.get("availableAgents")
        if not isinstance(agents, list) or not agents:
            raise IterationPlannerError("IterationPlanner requires at least one available agent.")
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict) or not agent.get("agentId") or not agent.get("role"):
                raise IterationPlannerError(f"availableAgents[{index}] requires agentId and role.")
        runtimes = payload.get("executableRuntimes")
        if not isinstance(runtimes, list) or not runtimes:
            raise IterationPlannerError("IterationPlanner requires at least one executable runtime.")
        budgets = payload.get("budgets")
        max_cost = budgets.get("maxCostUsd") if isinstance(budgets, dict) else None
        if not isinstance(max_cost, (int, float)) or max_cost < 0:
            raise IterationPlannerError("IterationPlanner requires budgets.maxCostUsd >= 0.")

    def plan(self, *, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Valida los insumos, construye el plan y lo persiste atómicamente; devuelve la iteración.

        Produce iteración, DAG de tareas, asignaciones por rol, estrategia de workspace, quality gates,
        security gates basados en riesgo y costo estimado (marcado como estimado). Materializa tareas,
        dependencias y asignaciones en el backlog dentro de una única ``immediate_transaction``.

        Raises:
            IterationPlannerError: si falta un insumo (brief no aprobado, sin historias ready, sin
                agentes, sin runtime ejecutable o presupuesto inválido).
        """
        self._validate(payload)
        stories = payload["stories"]
        architecture = payload.get("architecture") or []
        runtimes = [str(runtime) for runtime in payload["executableRuntimes"]]
        nodes = _decompose_stories(stories)
        edges = _build_dag_edges(nodes, payload.get("storyDependencies") or [])
        assignments, assignments_by_role, unassigned = _assign_by_role(nodes, payload["availableAgents"])
        workspace = _workspace_strategy(stories, nodes, runtimes)
        quality_gates = _quality_gates(nodes)
        security_gates = _security_gates(stories, architecture)
        estimated_cost = _estimated_cost(nodes, payload["budgets"])
        brief_title = str((payload["brief"].get("title")) or "product")
        title = str(payload.get("title") or f"Iteration for {brief_title}")
        goal = str(payload.get("goal") or f"Deliver {len(stories)} ready story(ies) with quality gates.")

        with immediate_transaction(self.repository.connection):
            iteration = self.repository.create_iteration(
                {
                    "projectId": project_id,
                    "briefId": payload["briefId"],
                    "title": title,
                    "goal": goal,
                    "status": "planned",
                    "storyIds": [story["id"] for story in stories],
                    "workspaceStrategy": workspace["strategy"],
                    "qualityGates": quality_gates,
                    "securityGates": security_gates,
                    "estimatedCost": estimated_cost,
                    "runtimes": runtimes,
                    "taskCount": len(nodes),
                    "assignmentCount": len(assignments),
                }
            )
            iteration_id = iteration["id"]
            key_to_task_id: dict[str, str] = {}
            dag_tasks: list[dict[str, Any]] = []
            for node in nodes:
                task = self.repository.create_agent_task(
                    {
                        "projectId": project_id,
                        "storyId": node["storyId"],
                        "title": node["title"],
                        "role": node["role"],
                        "category": "implementation",
                        "estimateHours": node["estimateHours"],
                        "metadata": {
                            "iterationId": iteration_id,
                            "source": ITERATION_PLANNER_ID,
                            "tier": node["tier"],
                        },
                    }
                )
                key_to_task_id[node["key"]] = task["id"]
                dag_tasks.append(
                    {
                        "taskId": task["id"],
                        "storyId": node["storyId"],
                        "role": node["role"],
                        "tier": node["tier"],
                        "title": node["title"],
                        "dependsOn": [],
                    }
                )
            dag_edges: list[dict[str, str]] = []
            for edge in edges:
                task_id = key_to_task_id[edge["taskKey"]]
                depends_on_id = key_to_task_id[edge["dependsOnKey"]]
                self.repository.create_task_dependency(
                    {
                        "projectId": project_id,
                        "taskId": task_id,
                        "dependsOnTaskId": depends_on_id,
                        "type": "blocks",
                        "metadata": {"iterationId": iteration_id},
                    }
                )
                dag_edges.append({"taskId": task_id, "dependsOnTaskId": depends_on_id})
            created_assignments = [
                self.repository.create_agent_assignment(
                    {
                        "projectId": project_id,
                        "taskId": key_to_task_id[assignment["taskKey"]],
                        "agentId": assignment["agentId"],
                        "role": assignment["role"],
                        "assignedBy": ITERATION_PLANNER_ID,
                        "status": "proposed",
                        "metadata": {"iterationId": iteration_id},
                    }
                )
                for assignment in assignments
            ]

        depends_by_task: dict[str, list[str]] = {}
        for edge in dag_edges:
            depends_by_task.setdefault(edge["taskId"], []).append(edge["dependsOnTaskId"])
        for task in dag_tasks:
            task["dependsOn"] = depends_by_task.get(task["taskId"], [])

        return {
            "iteration": iteration,
            "taskDag": {"tasks": dag_tasks, "edges": dag_edges},
            "assignmentsByRole": assignments_by_role,
            "assignments": created_assignments,
            "workspaceStrategy": workspace,
            "qualityGates": quality_gates,
            "securityGates": security_gates,
            "estimatedCost": estimated_cost,
            "unassignedRoles": unassigned,
        }
