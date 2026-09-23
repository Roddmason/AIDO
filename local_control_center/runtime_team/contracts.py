"""Contratos HTTP del equipo de runtimes por hilo: prueba de runtime, candidatos y roles.

Literales acotados para que el cliente generado no degrade a ``string`` (drift de response_model).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RuntimeValidationOutcome = Literal["validated", "failed", "deferred"]


class RuntimeValidationRequest(BaseModel):
    """Cuerpo de la prueba: el proyecto es obligatorio para CLI (preflight con contexto de ejecución)."""

    project_id: str | None = Field(default=None, alias="projectId")


class RuntimeValidationResultRecord(BaseModel):
    """Resultado redactado de una prueba de ida y vuelta de un runtime."""

    provider_id: str = Field(alias="providerId")
    kind: str
    status: RuntimeValidationOutcome
    model: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    reason: str | None = None
    evidence: str | None = None
    checked_at: str = Field(alias="checkedAt")


class RuntimeValidationResponse(BaseModel):
    """Respuesta de ``models.validate_runtime``."""

    validation: RuntimeValidationResultRecord
