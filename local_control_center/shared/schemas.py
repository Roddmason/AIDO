"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool


class HandshakeResponse(BaseModel):
    token: str
    loopback_only: bool = Field(alias="loopbackOnly")


class RetrievalStatusResponse(BaseModel):
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
    external_exporter: ExternalTelemetryStatus = Field(alias="externalExporter")


class EventRecord(BaseModel):
    id: str
    job_id: str | None = Field(default=None, alias="jobId")
    project_id: str | None = Field(default=None, alias="projectId")
    type: str
    severity: str = "info"
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class AuditEventRecord(BaseModel):
    id: str
    project_id: str | None = Field(default=None, alias="projectId")
    action: str
    actor: str
    target: str
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")
