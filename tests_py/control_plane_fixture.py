from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.agents.repository import AgentsRepository
from local_control_center.control_plane.runtime import ControlCenterRuntime
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
from local_control_center.workflows.repository import WorkflowsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository


class ControlPlaneFixture:
    """Test-only composition root.

    Tests should call slice repositories explicitly through this object. It is
    intentionally not a product store facade and must not grow domain methods.
    """

    def __init__(self, cwd: str | Path | None = None, db_path: str | Path | None = None):
        self.runtime = ControlCenterRuntime(cwd=cwd, db_path=db_path)

    @property
    def cwd(self) -> Path:
        return self.runtime.cwd

    @property
    def db_path(self) -> Path:
        return self.runtime.db_path

    @property
    def connection(self) -> sqlite3.Connection:
        return self.runtime.connection

    def init(self) -> None:
        self.runtime.init()

    def close(self) -> None:
        self.runtime.close()

    def get_handshake(self) -> dict[str, Any]:
        return self.runtime.get_handshake()

    def ensure_runtime_project(self) -> dict[str, Any]:
        return self.runtime.ensure_runtime_project()

    def create_project(self, **kwargs: Any) -> dict[str, Any]:
        project = self.projects.create_project(**kwargs)
        created = bool(project.pop("_created", False))
        if created:
            self.events.record_audit(
                project_id=project["id"],
                action="project.create",
                target=project["id"],
                payload={"path": project["path"], "source": project["source"]},
                actor="operator",
            )
        return project

    @property
    def projects(self) -> ProjectsRepository:
        return ProjectsRepository(self.connection)

    @property
    def jobs(self) -> JobsRepository:
        return JobsRepository(self.connection)

    @property
    def memory(self) -> MemoryRepository:
        return MemoryRepository(self.connection)

    @property
    def events(self) -> EventBus:
        return EventBus(self.connection)

    @property
    def evidence(self) -> EvidenceRepository:
        return EvidenceRepository(self.connection)

    @property
    def agents(self) -> AgentsRepository:
        return AgentsRepository(self.connection)

    @property
    def workflows(self) -> WorkflowsRepository:
        return WorkflowsRepository(self.connection)

    @property
    def security(self) -> SecurityPolicyRepository:
        return SecurityPolicyRepository(self.connection)

    @property
    def workspaces(self) -> WorkspacesRepository:
        return WorkspacesRepository(self.connection, root=self.cwd)

    @property
    def sessions_chats(self) -> SessionsChatsRepository:
        return SessionsChatsRepository(self.connection)

    @property
    def pipelines(self) -> PipelinesRepository:
        return PipelinesRepository(self.connection)

    @property
    def integrations(self) -> IntegrationsRepository:
        return IntegrationsRepository(self.connection)

    @property
    def prompts(self) -> PromptsRepository:
        return PromptsRepository(self.connection)

    @property
    def governance(self) -> GovernanceRepository:
        return GovernanceRepository(self.connection)
