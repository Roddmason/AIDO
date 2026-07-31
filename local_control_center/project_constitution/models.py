"""Esquemas Pydantic del slice de constitución: documento vigente, historial y upsert del operador.

Solo modelan el contrato HTTP (camelCase vía alias); la validación de vocabularios y contenido vive
en el repositorio, que es la única puerta de escritura.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ConstitutionSource = Literal["operator", "bootstrapped"]
ConstitutionEnforcement = Literal["advisory", "enforced"]


class ProjectConstitutionRecord(BaseModel):
    """Constitución vigente del proyecto tal como se expone al cliente."""

    id: str
    project_id: str = Field(alias="projectId")
    title: str
    principles: list[str]
    non_negotiables: list[str] = Field(alias="nonNegotiables")
    quality_gates: list[str] = Field(alias="qualityGates")
    source: ConstitutionSource
    enforcement: ConstitutionEnforcement
    content_hash: str = Field(alias="contentHash")
    version: int
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProjectConstitutionVersionRecord(BaseModel):
    """Snapshot inmutable de una versión de la constitución."""

    id: str
    constitution_id: str = Field(alias="constitutionId")
    project_id: str = Field(alias="projectId")
    version: int
    title: str
    principles: list[str]
    non_negotiables: list[str] = Field(alias="nonNegotiables")
    quality_gates: list[str] = Field(alias="qualityGates")
    source: ConstitutionSource
    enforcement: ConstitutionEnforcement
    content_hash: str = Field(alias="contentHash")
    change_summary: str = Field(alias="changeSummary")
    authored_by: str = Field(alias="authoredBy")
    created_at: str = Field(alias="createdAt")


class ProjectConstitutionResponse(BaseModel):
    """Documento vigente (o ``null`` si el proyecto aún no tiene) más su historial."""

    constitution: ProjectConstitutionRecord | None = None
    versions: list[ProjectConstitutionVersionRecord]


class ProjectConstitutionUpsertRequest(BaseModel):
    """Cuerpo del upsert del operador; el repositorio valida vocabularios y contenido mínimo."""

    title: str | None = None
    principles: list[str]
    non_negotiables: list[str] | None = Field(default=None, alias="nonNegotiables")
    quality_gates: list[str] | None = Field(default=None, alias="qualityGates")
    enforcement: ConstitutionEnforcement | None = None
    change_summary: str | None = Field(default=None, alias="changeSummary")
    authored_by: str | None = Field(default=None, alias="authoredBy")
