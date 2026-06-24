"""Comandos de aplicación del slice de proyectos (orquestan repositorio, descubrimiento y auditoría).

Cada función envuelve su resultado en el shape de respuesta que espera la API y delega la
persistencia al repositorio. La creación resuelve la ruta destino, redacta la telemetría
sensible del metadata y emite un evento de auditoría tras escribir el proyecto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.telemetry import redact_telemetry

from .discovery import discover_project_path
from .repository import ProjectsRepository


def list_project_templates(projects: ProjectsRepository) -> dict[str, Any]:
    """Devuelve las plantillas de proyecto disponibles para el flujo de creación."""
    return {"projectTemplates": projects.list_project_templates()}


def list_projects(projects: ProjectsRepository) -> dict[str, Any]:
    """Devuelve los proyectos registrados, ordenados como los entrega el repositorio."""
    return {"projects": projects.list_projects()}


def discover_project(*, path: str | Path) -> dict[str, Any]:
    """Inspecciona una ruta y reporta runtimes, manifiestos y nombre sugerido (solo lectura)."""
    return {"discovery": discover_project_path(path)}


def _safe_directory_name(value: str) -> str:
    candidate = value.strip().strip("/\\")
    if not candidate or Path(candidate).name != candidate or candidate in {".", ".."}:
        raise ValueError("projectDirectoryName must be a single directory name.")
    return candidate


def _resolve_project_path(body: dict[str, Any], cwd: Path) -> tuple[Path, dict[str, Any]]:
    metadata: dict[str, Any] = dict(redact_telemetry(body.get("metadata") or {}))
    workspace_base_path = body.get("workspaceBasePath")
    directory_name = body.get("projectDirectoryName")
    if workspace_base_path and directory_name:
        safe_directory_name = _safe_directory_name(str(directory_name))
        workspace_root = Path(str(workspace_base_path)).expanduser()
        metadata.update(
            {
                "workspaceBasePath": str(workspace_root),
                "projectDirectoryName": safe_directory_name,
                "creationMode": "new_under_workspace",
            }
        )
        return workspace_root / safe_directory_name, metadata

    project_path = Path(str(body.get("path") or cwd)).expanduser()
    metadata.setdefault(
        "creationMode", "attach_existing" if not body.get("createDirectory", True) else "explicit_path"
    )
    if workspace_base_path:
        metadata["workspaceBasePath"] = str(Path(str(workspace_base_path)).expanduser())
    if directory_name:
        metadata["projectDirectoryName"] = str(directory_name)
    return project_path, metadata


def create_project(
    projects: ProjectsRepository,
    events: EventBus,
    *,
    cwd: Path,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Crea (o adjunta) un proyecto y registra el evento de auditoría con telemetría redactada.

    Returns:
        El proyecto persistido y el ``auditEvent`` emitido; ``created`` indica si la fila
        es nueva o ya existía para esa ruta.
    """
    project_path, metadata = _resolve_project_path(body, cwd)
    project = projects.create_project(
        name=body.get("name") or Path(project_path).name or "Project",
        path=project_path,
        template_id=body.get("templateId") or "other",
        create_directory=body.get("createDirectory", True),
        source="api",
        metadata=metadata,
    )
    created = bool(project.pop("_created", False))
    audit = events.record_audit(
        project_id=project["id"],
        action="project.create",
        target=project["id"],
        payload={**redact_telemetry(body), "path": str(project_path), "created": created},
    )
    return {"project": project, "auditEvent": audit}


def list_providers(projects: ProjectsRepository) -> dict[str, Any]:
    """Devuelve los proveedores de modelos/agentes registrados."""
    return {"providers": projects.list_providers()}


def list_teams(projects: ProjectsRepository, *, project_id: str | None = None) -> dict[str, Any]:
    """Devuelve los equipos (filtrables por proyecto) junto con el catálogo completo de agentes."""
    return {"teams": projects.list_teams(project_id=project_id), "agents": projects.list_agents()}


def list_agents(projects: ProjectsRepository, *, team_id: str | None = None) -> dict[str, Any]:
    """Devuelve los agentes del catálogo, opcionalmente acotados a un equipo."""
    return {"agents": projects.list_agents(team_id=team_id)}


def list_project_assessments(projects: ProjectsRepository, *, project_id: str) -> dict[str, Any]:
    """Devuelve los assessments estáticos persistidos del proyecto, el más reciente primero."""
    return {"assessments": projects.list_project_assessments(project_id)}


def list_project_findings(
    projects: ProjectsRepository, *, project_id: str, category: str | None = None
) -> dict[str, Any]:
    """Devuelve los hallazgos de assessment del proyecto, opcionalmente filtrados por categoría."""
    return {"findings": projects.list_project_findings(project_id=project_id, category=category)}
