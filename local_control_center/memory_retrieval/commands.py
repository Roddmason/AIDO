"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any

from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.time import add_millis

from .index import RetrievalIndex
from .repository import MemoryRepository


def list_memory(memory: MemoryRepository, project_id: str | None = None) -> dict[str, Any]:
    return {"memoryItems": memory.list_memory_items(project_id=project_id)}


def create_memory(memory: MemoryRepository, events: EventBus, body: dict[str, Any]) -> dict[str, Any]:
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


def delete_memory(memory: MemoryRepository, events: EventBus, memory_id: str, body: dict[str, Any]) -> dict[str, Any]:
    memory_item = memory.delete_memory_item(memory_id, reason=str(body.get("reason") or ""))
    events.record_event(
        project_id=memory_item["projectId"],
        event_type="memory.deleted",
        payload={"memoryItemId": memory_item["id"], "reason": body.get("reason") or ""},
    )
    return {"memoryItem": memory_item}


def retrieval_status(index: RetrievalIndex, project_id: str | None = None) -> dict[str, Any]:
    return index.status(project_id=project_id)


def retrieval_reindex(index: RetrievalIndex, body: dict[str, Any]) -> dict[str, Any]:
    return {"index": index.rebuild(project_id=body["projectId"])}


def retrieval_search(index: RetrievalIndex, body: dict[str, Any]) -> dict[str, Any]:
    return index.search(
        project_id=body["projectId"],
        query=body.get("query", ""),
        limit=int(body.get("limit", 5)),
    )
