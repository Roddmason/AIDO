"""Casos de uso de memoria y retrieval que orquestan repositorio, índice y eventos.

Capa fina entre los handlers HTTP y la persistencia: arma los argumentos
desde el body del contrato, registra los eventos de auditoría correspondientes
y delega el ranking en el índice. No contiene SQL ni lógica de transporte.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.time import add_millis

from .index import RetrievalIndex
from .repository import MemoryRepository


def list_memory(memory: MemoryRepository, project_id: str | None = None) -> dict[str, Any]:
    """Devuelve los memory items activos del proyecto bajo la clave de contrato."""
    return {"memoryItems": memory.list_memory_items(project_id=project_id)}


def create_memory(memory: MemoryRepository, events: EventBus, body: dict[str, Any]) -> dict[str, Any]:
    """Crea un memory item y emite el evento memory.created.

    Si no hay expiresAt pero sí ttlSeconds, deriva la expiración absoluta a
    partir del ahora más el TTL.
    """
    expires_at = body.get("expiresAt")
    if not expires_at and body.get("ttlSeconds"):
        expires_at = add_millis(int(body["ttlSeconds"]) * 1000)
    memory_item = memory.create_memory_item(
        project_id=body["projectId"],
        scope=body.get("scope", "project"),
        scope_id=body.get("scopeId", body["projectId"]),
        kind=body.get("kind", "note"),
        content=body["content"],
        source_ref=body.get("sourceRef", "api"),
        expires_at=expires_at,
        metadata=body.get("metadata") or {},
    )
    events.record_event(
        project_id=body["projectId"],
        event_type="memory.created",
        payload={"memoryItemId": memory_item["id"], "expiresAt": memory_item.get("expiresAt")},
    )
    return {"memoryItem": memory_item}


def delete_memory(
    memory: MemoryRepository, events: EventBus, memory_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Borra lógicamente un memory item con su motivo y emite el evento memory.deleted."""
    memory_item = memory.delete_memory_item(memory_id, reason=str(body.get("reason") or ""))
    events.record_event(
        project_id=memory_item["projectId"],
        event_type="memory.deleted",
        payload={"memoryItemId": memory_item["id"], "reason": body.get("reason") or ""},
    )
    return {"memoryItem": memory_item}


def retrieval_status(index: RetrievalIndex, project_id: str | None = None) -> dict[str, Any]:
    """Reporta el estado del índice de retrieval para el proyecto indicado."""
    return index.status(project_id=project_id)


def retrieval_reindex(index: RetrievalIndex, body: dict[str, Any]) -> dict[str, Any]:
    """Reconstruye el índice del proyecto y devuelve su resumen bajo la clave index."""
    return {"index": index.rebuild(project_id=body["projectId"])}


def retrieval_search(index: RetrievalIndex, body: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta la búsqueda semántica con el query y límite recibidos del contrato."""
    return index.search(
        project_id=body["projectId"],
        query=body.get("query", ""),
        limit=int(body.get("limit", 5)),
    )
