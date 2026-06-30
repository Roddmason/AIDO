"""Ensambla el snapshot global read-only del estado consultando cada repositorio de slice.

Punto unico que el endpoint de overview usa para devolver, en una sola respuesta, las
colecciones de todos los slices (proyectos, sesiones, pipelines, jobs, gobernanza, agentes,
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
from local_control_center.pipelines.repository import PipelinesRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.prompts.repository import PromptsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.sessions_chats.repository import SessionsChatsRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workflows.repository import WorkflowsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository


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
    sessions_chats = SessionsChatsRepository(connection)
    pipelines = PipelinesRepository(connection)
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
        "sessions": sessions_chats.list_sessions(),
        "chats": sessions_chats.list_chats(),
        "threads": threads.list_threads(),
        "pipelines": pipelines.list_pipelines(),
        "jobs": jobs.list_jobs(),
        "jobRuns": jobs.list_job_runs(),
        "events": events.list_events(),
        "auditEvents": events.list_audit_events(),
        "memoryItems": memory.list_memory_items(),
        "promptTemplates": prompts.list_prompt_templates(),
        "actionRequests": jobs.list_action_requests(),
        "ideConnections": integrations.list_ide_connections(),
        "mcpServers": integrations.list_mcp_servers(),
        "workflows": workflows.list_workflows(),
        "workflowRuns": workflows.list_workflow_runs(),
        "workflowSteps": workflows.list_workflow_steps(),
        "workflowEvents": workflows.list_workflow_events(),
        "permissionDecisions": security_policy.list_decisions(),
        "policyRevisions": security_policy.list_policy_revisions(),
        "permissionGrants": security_policy.list_grants(),
        "sandboxProfiles": security_policy.list_sandbox_profiles(),
        "evidencePackages": evidence.list_evidence_packages(),
        "artifacts": evidence.list_all_artifacts(),
        "testResultRecords": evidence.list_all_test_results(),
        "agentProfiles": agents.list_agent_profiles(),
        "agentRuns": agents.list_agent_runs(),
        "modelPolicies": agents.list_model_policies(),
        "modelProviders": agents.list_model_providers(),
        "agentToolCalls": agents.list_agent_tool_calls(),
        "modelCalls": agents.list_model_calls(),
        "costUsage": agents.list_cost_usage(),
        "runtimeWorkspaces": workspaces.list_workspaces(),
        "skills": skills.list_skills(),
        "architectureDecisions": governance.list_architecture_decisions(),
        "riskRegister": governance.list_risks(),
        "nextSteps": governance.list_next_steps(),
        "openDesign": {"status": "python-backend"},
        "security": {"loopbackOnly": True, "writeTokenRequired": True},
    }
