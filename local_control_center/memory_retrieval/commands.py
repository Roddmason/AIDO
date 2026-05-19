from __future__ import annotations

from typing import Any

from local_control_center.shared.event_bus import EventBus

from .index import RetrievalIndex
from .repository import MemoryRepository


def list_memory(memory: MemoryRepository) -> dict[str, Any]:
    return {"memoryItems": memory.list_memory_items()}


def create_memory(memory: MemoryRepository, events: EventBus, body: dict[str, Any]) -> dict[str, Any]:
    memory_item = memory.create_memory_item(
        project_id=body["projectId"],
        scope=body.get("scope", "project"),
        scope_id=body.get("scopeId", body["projectId"]),
        kind=body.get("kind", "note"),
        content=body["content"],
        source_ref=body.get("sourceRef", "api"),
        metadata=body.get("metadata") or {},
    )
    events.record_event(
        project_id=body["projectId"],
        event_type="memory.created",
        payload={"memoryItemId": memory_item["id"]},
    )
    return {"memoryItem": memory_item}


def retrieval_status(index: RetrievalIndex) -> dict[str, Any]:
    return index.status()


def retrieval_reindex(index: RetrievalIndex) -> dict[str, Any]:
    return {"index": index.rebuild()}


def retrieval_search(index: RetrievalIndex, body: dict[str, Any]) -> dict[str, Any]:
    return {"results": index.search(body.get("query", ""), int(body.get("limit", 5)))}
