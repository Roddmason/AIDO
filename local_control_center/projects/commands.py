from __future__ import annotations

from pathlib import Path
from typing import Any

from local_control_center.shared.event_bus import EventBus

from .repository import ProjectsRepository


def list_project_templates(projects: ProjectsRepository) -> dict[str, Any]:
    return {"projectTemplates": projects.list_project_templates()}


def list_projects(projects: ProjectsRepository) -> dict[str, Any]:
    return {"projects": projects.list_projects()}


def create_project(
    projects: ProjectsRepository,
    events: EventBus,
    *,
    cwd: Path,
    body: dict[str, Any],
) -> dict[str, Any]:
    project_path = body.get("path") or cwd
    project = projects.create_project(
        name=body.get("name") or Path(project_path).name or "Project",
        path=project_path,
        template_id=body.get("templateId") or "other",
        create_directory=body.get("createDirectory", True),
        source="api",
        metadata=body.get("metadata") or {},
    )
    created = bool(project.pop("_created", False))
    audit = events.record_audit(
        project_id=project["id"],
        action="project.create",
        target=project["id"],
        payload={**body, "created": created},
    )
    return {"project": project, "auditEvent": audit}


def list_providers(projects: ProjectsRepository) -> dict[str, Any]:
    return {"providers": projects.list_providers()}


def list_teams(projects: ProjectsRepository, *, project_id: str | None = None) -> dict[str, Any]:
    return {"teams": projects.list_teams(project_id=project_id), "agents": projects.list_agents()}


def list_agents(projects: ProjectsRepository, *, team_id: str | None = None) -> dict[str, Any]:
    return {"agents": projects.list_agents(team_id=team_id)}
