from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .index import RetrievalIndex

if TYPE_CHECKING:
    from local_control_center.store import PlatformStore


def list_memory(store: "PlatformStore") -> dict[str, Any]:
    return {"memoryItems": store.list_memory_items()}


def create_memory(store: "PlatformStore", body: dict[str, Any]) -> dict[str, Any]:
    return {
        "memoryItem": store.create_memory_item(
            project_id=body["projectId"],
            scope=body.get("scope", "project"),
            scope_id=body.get("scopeId", body["projectId"]),
            kind=body.get("kind", "note"),
            content=body["content"],
            source_ref=body.get("sourceRef", "api"),
            metadata=body.get("metadata") or {},
        )
    }


def retrieval_status(store: "PlatformStore") -> dict[str, Any]:
    return RetrievalIndex(store=store).status()


def retrieval_reindex(store: "PlatformStore") -> dict[str, Any]:
    return {"index": RetrievalIndex(store=store).rebuild()}


def retrieval_search(store: "PlatformStore", body: dict[str, Any]) -> dict[str, Any]:
    return {"results": RetrievalIndex(store=store).search(body.get("query", ""), int(body.get("limit", 5)))}
