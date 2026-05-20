from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool


class HandshakeResponse(BaseModel):
    token: str
    loopback_only: bool = Field(alias="loopbackOnly")


class RetrievalStatusResponse(BaseModel):
    backend: str
    degraded: bool
    faiss_available: bool = Field(alias="faissAvailable")
    index_dir: str = Field(alias="indexDir")
    indexed: int
    dimensions: int
