"""Ensambla el snapshot global read-only del estado consultando cada repositorio de slice.

Punto unico que el endpoint de overview usa para devolver, en una sola respuesta, las
colecciones de todos los slices activos (proyectos, threads, jobs, gobernanza, agentes,
seguridad, etc.). Solo lee: instancia repositorios sobre la conexion recibida, garantiza el
proyecto runtime y arma el diccionario alineado con ``OverviewResponse``.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.skills import SkillRegistry
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.governance.repository import GovernanceRepository
from local_control_center.integrations.repository import IntegrationsRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.prompts.repository import PromptsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workflows.repository import WorkflowsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository

OVERVIEW_EVENT_LIMIT = 250
OVERVIEW_AUDIT_EVENT_LIMIT = 100
OVERVIEW_PERMISSION_DECISION_LIMIT = 300
OVERVIEW_AGENT_TOOL_CALL_LIMIT = 300
OVERVIEW_AGENT_RUN_LIMIT = 200
OVERVIEW_MODEL_CALL_LIMIT = 300
OVERVIEW_COST_USAGE_LIMIT = 200
OVERVIEW_EVIDENCE_LIMIT = 100
OVERVIEW_TEST_RESULT_LIMIT = 150
OVERVIEW_ARTIFACT_LIMIT = 300
OVERVIEW_EMBEDDED_VALUE_BYTE_LIMIT = 8_192
_COMPACT_DEPTH = 2
_COMPACT_LIST_HEAD = 20


def _exceeds_embedded_budget(value: Any, *, budget: int) -> bool:
    """Responde si el valor serializado superaría el presupuesto, cortando el conteo temprano.

    El poll solo necesita saber si un subárbol excede el límite, no su tamaño exacto:
    el recorrido iterativo se detiene apenas la suma supera ``budget``, así los blobs
    de MBs cuestan lo mismo que uno de 8KB.
    """
    total = 0
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            total += len(node) + 2
        elif isinstance(node, dict):
            total += 2
            for key, item in node.items():
                total += len(str(key)) + 4
                stack.append(item)
        elif isinstance(node, list):
            total += 2 + 2 * len(node)
            stack.extend(node)
        else:
            total += 8
        if total > budget:
            return True
    return False


def _compact_embedded_value(value: Any, *, depth: int) -> Any:
    """Acota un valor embebido al presupuesto por campo del snapshot.

    El overview viaja en cada poll: conserva escalares y contenedores chicos, recorta
    strings largos y sustituye los subárboles que exceden
    ``OVERVIEW_EMBEDDED_VALUE_BYTE_LIMIT`` por ``{"overviewTruncated": True}``.
    El registro íntegro sigue disponible en el endpoint de detalle de su slice.
    """
    limit = OVERVIEW_EMBEDDED_VALUE_BYTE_LIMIT
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}…[overviewTruncated]"
    if not isinstance(value, (dict, list)):
        return value
    if not _exceeds_embedded_budget(value, budget=limit):
        return value
    if depth <= 0:
        return {"overviewTruncated": True}
    if isinstance(value, dict):
        return {key: _compact_embedded_value(item, depth=depth - 1) for key, item in value.items()}
    head = [_compact_embedded_value(item, depth=depth - 1) for item in value[:_COMPACT_LIST_HEAD]]
    if len(value) > _COMPACT_LIST_HEAD:
        head.append({"overviewTruncated": True, "omittedItems": len(value) - _COMPACT_LIST_HEAD})
    return head


def _compact_overview_record(record: dict[str, Any]) -> dict[str, Any]:
    """Aplica el presupuesto de campo a cada valor de un registro del snapshot."""
    return {key: _compact_embedded_value(value, depth=_COMPACT_DEPTH) for key, value in record.items()}


def ensure_runtime_project(connection: sqlite3.Connection, cwd: str | Path) -> dict[str, Any]:
    """Devuelve el proyecto del cwd, creandolo si falta; no audita (variante para overview)."""
    projects = ProjectsRepository(connection)
    existing = projects.get_project_by_path(cwd)
    if existing:
        return existing
    runtime_path = Path(cwd)
    return projects.create_project(
        name=runtime_path.name or "Local Control Center",
        path=runtime_path,
        template_id="other",
        create_directory=True,
        source="runtime",
    )


def build_overview_from_connection(*, connection: sqlite3.Connection, cwd: str | Path) -> dict[str, Any]:
    """Agrega y devuelve las colecciones de todos los slices como un unico snapshot read-only.

    Garantiza primero el proyecto runtime y luego compone el diccionario cuyas claves camelCase
    mapean a los campos de ``OverviewResponse``.
    """
    ensure_runtime_project(connection, cwd)

    projects = ProjectsRepository(connection)
    jobs = JobsRepository(connection)
    events = EventBus(connection)
    memory = MemoryRepository(connection)
    prompts = PromptsRepository(connection)
    integrations = IntegrationsRepository(connection)
    workflows = WorkflowsRepository(connection)
    security_policy = SecurityPolicyRepository(connection)
    evidence = EvidenceRepository(connection)
    agents = AgentsRepository(connection)
    workspaces = WorkspacesRepository(connection, root=Path(cwd))
    skills = SkillRegistry(connection)
    governance = GovernanceRepository(connection)
    threads = ThreadsRepository(connection)

    return {
        "projectTemplates": projects.list_project_templates(),
        "projects": projects.list_projects(),
        "providers": projects.list_providers(),
        "teams": projects.list_teams(),
        "agents": projects.list_agents(),
        "threads": threads.list_threads(),
        "jobs": jobs.list_jobs(),
        "jobRuns": jobs.list_job_runs(),
        "events": events.list_events(limit=OVERVIEW_EVENT_LIMIT),
        "auditEvents": events.list_audit_events(limit=OVERVIEW_AUDIT_EVENT_LIMIT),
        "memoryItems": memory.list_memory_items(),
        "promptTemplates": prompts.list_prompt_templates(),
        "actionRequests": jobs.list_action_requests(),
        "ideConnections": integrations.list_ide_connections(),
        "mcpServers": integrations.list_mcp_servers(),
        "workflows": workflows.list_workflows(),
        "workflowRuns": workflows.list_workflow_runs(),
        "workflowSteps": workflows.list_workflow_steps(),
        "workflowEvents": workflows.list_workflow_events(),
        "permissionDecisions": security_policy.list_decisions(limit=OVERVIEW_PERMISSION_DECISION_LIMIT),
        "policyRevisions": security_policy.list_policy_revisions(),
        "permissionGrants": security_policy.list_grants(),
        "sandboxProfiles": security_policy.list_sandbox_profiles(),
        "evidencePackages": [
            _compact_overview_record(record)
            for record in evidence.list_evidence_packages(limit=OVERVIEW_EVIDENCE_LIMIT)
        ],
        "artifacts": evidence.list_all_artifacts(limit=OVERVIEW_ARTIFACT_LIMIT),
        "testResultRecords": [
            _compact_overview_record(record)
            for record in evidence.list_all_test_results(limit=OVERVIEW_TEST_RESULT_LIMIT)
        ],
        "agentProfiles": agents.list_agent_profiles(),
        "agentRuns": [
            _compact_overview_record(record)
            for record in agents.list_agent_runs(limit=OVERVIEW_AGENT_RUN_LIMIT)
        ],
        "modelPolicies": agents.list_model_policies(),
        "modelProviders": agents.list_model_providers(),
        "agentToolCalls": agents.list_agent_tool_calls(limit=OVERVIEW_AGENT_TOOL_CALL_LIMIT),
        "modelCalls": agents.list_model_calls(limit=OVERVIEW_MODEL_CALL_LIMIT),
        "costUsage": agents.list_cost_usage(limit=OVERVIEW_COST_USAGE_LIMIT),
        "runtimeWorkspaces": [_compact_overview_record(record) for record in workspaces.list_workspaces()],
        "skills": skills.list_skills(),
        "architectureDecisions": governance.list_architecture_decisions(),
        "riskRegister": governance.list_risks(),
        "nextSteps": governance.list_next_steps(),
        "openDesign": {"status": "python-backend"},
        "security": {"loopbackOnly": True, "writeTokenRequired": True},
    }
