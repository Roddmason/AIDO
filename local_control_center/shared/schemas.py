"""Modelos Pydantic de respuesta compartidos entre endpoints transversales.

Define el contrato de salida (con alias camelCase para el frontend) de health,
handshake, estado de retrieval/telemetría y los registros de evento/auditoría.
Son DTO de borde: validan y serializan; no contienen lógica de dominio.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Respuesta del health-check: indica si el backend está operativo."""

    ok: bool


class HandshakeResponse(BaseModel):
    """Respuesta del handshake: token de sesión y si el acceso queda restringido a loopback."""

    token: str
    loopback_only: bool = Field(alias="loopbackOnly")


class RetrievalStatusResponse(BaseModel):
    """Estado del subsistema de retrieval: disponibilidad, backend, degradación e índice."""

    status: str
    available: bool
    reason: str
    backend: str
    degraded: bool
    faiss_available: bool = Field(alias="faissAvailable")
    index_dir: str = Field(alias="indexDir")
    indexed: int
    dimensions: int


class ExternalTelemetryStatus(BaseModel):
    """Estado del exportador OpenTelemetry externo: modo, disponibilidad y endpoints."""

    enabled: bool
    mode: str
    available: bool
    traces_enabled: bool = Field(alias="tracesEnabled")
    metrics_enabled: bool = Field(alias="metricsEnabled")
    reason: str
    service_name: str | None = Field(default=None, alias="serviceName")
    traces_endpoint: str | None = Field(default=None, alias="tracesEndpoint")
    metrics_endpoint: str | None = Field(default=None, alias="metricsEndpoint")


class TelemetryStatusResponse(BaseModel):
    """Respuesta de estado de telemetría: envuelve el estado del exportador externo."""

    external_exporter: ExternalTelemetryStatus = Field(alias="externalExporter")


class EventRecord(BaseModel):
    """Evento operativo expuesto por la API: tipo, severidad, payload y vínculos a job/proyecto."""

    id: str
    job_id: str | None = Field(default=None, alias="jobId")
    project_id: str | None = Field(default=None, alias="projectId")
    type: str
    severity: str = "info"
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class AuditEventRecord(BaseModel):
    """Evento de auditoría expuesto por la API: acción de un actor sobre un objetivo."""

    id: str
    project_id: str | None = Field(default=None, alias="projectId")
    action: str
    actor: str
    target: str
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")
