"""Esquemas Pydantic de request/response y registros del slice de proyectos.

Definen el contrato HTTP (validación de entrada y forma de salida) y traducen entre el
``snake_case`` de Python y el ``camelCase`` del frontend vía alias de campo. Solo modelan
datos: no contienen lógica de negocio ni acceso a la base.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from local_control_center.shared.schemas import AuditEventRecord


class ProjectCreateRequest(BaseModel):
    """Cuerpo para crear un proyecto: ruta explícita o nombre+workspace base, más plantilla."""

    name: str | None = None
    path: str | None = None
    workspace_base_path: str | None = Field(default=None, alias="workspaceBasePath")
    project_directory_name: str | None = Field(default=None, alias="projectDirectoryName")
    template_id: str | None = Field(default=None, alias="templateId")
    create_directory: bool = Field(default=True, alias="createDirectory")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectDiscoveryRequest(BaseModel):
    """Cuerpo para descubrir el perfil de un proyecto a partir de una ruta del filesystem."""

    path: str


class ProjectDiscoveryResponse(BaseModel):
    """Respuesta del descubrimiento: payload libre con runtimes, manifiestos y sugerencias."""

    discovery: dict[str, Any]


class DirectoryPickerRequest(BaseModel):
    """Cuerpo para abrir el diálogo nativo de selección de carpeta del workspace."""

    title: str = "Select workspace folder"
    initial_path: str | None = Field(default=None, alias="initialPath")


class DirectoryPickerResponse(BaseModel):
    """Resultado del picker: estado (selected/cancelled/unavailable) y ruta elegida."""

    status: str
    selected_path: str | None = Field(default=None, alias="selectedPath")
    reason: str | None = None


class ProjectRecord(BaseModel):
    """Proyecto registrado tal como se expone al cliente (con timestamps y metadata)."""

    id: str
    name: str
    path: str
    template_id: str = Field(alias="templateId")
    source: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProjectTemplateRecord(BaseModel):
    """Plantilla de proyecto seleccionable (frontend/backend/tooling/genérica)."""

    id: str
    name: str
    kind: str


class ProviderRecord(BaseModel):
    """Proveedor de modelos/agentes con sus capacidades, modelos y estado."""

    id: str
    kind: str
    label: str
    capabilities: list[Any]
    models: list[Any]
    status: str
    metadata: dict[str, Any]
    updated_at: str = Field(alias="updatedAt")


class TeamRecord(BaseModel):
    """Equipo de agentes asociado a un proyecto, con versión y capacidades."""

    id: str
    project_id: str = Field(alias="projectId")
    name: str
    version: str
    capabilities: list[Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class CatalogAgentRecord(BaseModel):
    """Agente del catálogo: rol, proveedor/modelo, capacidades y permisos efectivos."""

    id: str
    team_id: str = Field(alias="teamId")
    name: str
    role: str
    kind: str
    provider_id: str = Field(alias="providerId")
    model: str
    capabilities: list[Any]
    permissions: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProjectResponse(BaseModel):
    """Respuesta de creación: el proyecto y, si se emitió, el evento de auditoría."""

    project: ProjectRecord
    audit_event: AuditEventRecord | None = Field(default=None, alias="auditEvent")


class ProjectTemplatesResponse(BaseModel):
    """Listado de plantillas de proyecto disponibles."""

    project_templates: list[ProjectTemplateRecord] = Field(alias="projectTemplates")


class ProjectsListResponse(BaseModel):
    """Listado de proyectos registrados."""

    projects: list[ProjectRecord]


class ProvidersListResponse(BaseModel):
    """Listado de proveedores registrados."""

    providers: list[ProviderRecord]


class TeamsListResponse(BaseModel):
    """Listado de equipos."""

    teams: list[TeamRecord]


class AgentsListResponse(BaseModel):
    """Listado de agentes del catálogo."""

    agents: list[CatalogAgentRecord]
