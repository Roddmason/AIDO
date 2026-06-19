"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from local_control_center.shared.serialization import stable_hash

from .repository import MemoryRepository

try:  # pragma: no cover - exercised only when faiss-cpu is installed.
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


CONFIGURATION_REQUIRED_REASON = (
    "No persisted real memory embeddings are available for this project. Configure a real embedding "
    "provider and write embeddings before enabling retrieval."
)
QUERY_PROVIDER_REQUIRED_REASON = "No real query embedding provider is configured for memory retrieval search."


class EmbeddingProvider(Protocol):
    def status(self) -> dict[str, Any]: ...

    def embed_text(self, text: str) -> list[float]: ...


class UnavailableEmbeddingProvider:
    def status(self) -> dict[str, Any]:
        return {"status": "configuration_required", "reason": QUERY_PROVIDER_REQUIRED_REASON}

    def embed_text(self, text: str) -> list[float]:
        _ = text
        raise RuntimeError(QUERY_PROVIDER_REQUIRED_REASON)


class RetrievalIndex:
    def __init__(
        self,
        *,
        memory: MemoryRepository,
        index_dir: str | Path,
        dimensions: int = 128,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        self.memory = memory
        self.index_dir = Path(index_dir)
        self.dimensions = dimensions
        self.embedding_provider = embedding_provider or UnavailableEmbeddingProvider()

    def _project_dir(self, project_id: str) -> Path:
        return self.index_dir / f"project-{stable_hash(project_id)[:16]}"

    def manifest_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "manifest.json"

    def _summary(
        self,
        *,
        project_id: str,
        status: str,
        reason: str,
        backend: str = "unavailable",
        dimensions: int | None = None,
        ids: list[str] | None = None,
    ) -> dict[str, Any]:
        indexed_ids = ids or []
        return {
            "status": status,
            "reason": reason,
            "projectId": project_id,
            "backend": backend,
            "degraded": status == "available" and backend != "faiss",
            "dimensions": dimensions or self.dimensions,
            "ids": indexed_ids,
            "indexed": len(indexed_ids),
        }

    def status(self, project_id: str | None = None) -> dict[str, Any]:
        provider_status = self.embedding_provider.status()
        manifest: dict[str, Any] = {}
        if project_id and self.manifest_path(project_id).exists():
            manifest = json.loads(self.manifest_path(project_id).read_text(encoding="utf-8"))
        status = str(provider_status.get("status") or "configuration_required")
        reason = str(provider_status.get("reason") or "")
        return {
            "status": status,
            "available": status == "available",
            "reason": reason,
            "backend": manifest.get("backend", "unavailable" if status != "available" else "numpy"),
            "degraded": bool(manifest.get("degraded", False)),
            "faissAvailable": faiss is not None,
            "indexDir": str(self.index_dir if not project_id else self._project_dir(project_id)),
            "indexed": int(manifest.get("indexed", 0)),
            "dimensions": int(manifest.get("dimensions", self.dimensions)),
        }

    def rebuild(self, *, project_id: str) -> dict[str, Any]:
        project_dir = self._project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        rows = self.memory.list_indexable_embeddings(project_id)
        if not rows:
            for stale_name in ("memory.faiss", "memory_vectors.npy"):
                stale_path = project_dir / stale_name
                if stale_path.exists():
                    stale_path.unlink()
            manifest = self._summary(
                project_id=project_id,
                status="configuration_required",
                reason=CONFIGURATION_REQUIRED_REASON,
                ids=[],
            )
            self.manifest_path(project_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return manifest

        ids: list[str] = []
        vectors: list[np.ndarray] = []
        dimensions: int | None = None
        for row in rows:
            embedding = row["embedding"]
            if not isinstance(embedding, list) or not all(
                isinstance(value, int | float) for value in embedding
            ):
                return self._summary(
                    project_id=project_id,
                    status="blocked",
                    reason=f"Invalid embedding payload for memory item {row['memoryItemId']}.",
                    ids=[],
                )
            vector = np.asarray(embedding, dtype="float32")
            dimensions = dimensions or len(vector)
            if len(vector) != dimensions:
                return self._summary(
                    project_id=project_id,
                    status="blocked",
                    reason="Memory embeddings have inconsistent dimensions; rebuild requires one embedding model.",
                    ids=[],
                )
            ids.append(str(row["memoryItemId"]))
            vectors.append(vector)

        matrix = np.vstack(vectors).astype("float32")
        backend = "numpy"
        if faiss is not None and len(matrix):
            backend = "faiss"
            faiss_index = faiss.IndexFlatIP(int(dimensions or self.dimensions))
            faiss_index.add(matrix)
            faiss.write_index(faiss_index, str(project_dir / "memory.faiss"))
        np.save(project_dir / "memory_vectors.npy", matrix)
        manifest = self._summary(
            project_id=project_id,
            status="available",
            reason="",
            backend=backend,
            dimensions=int(dimensions or self.dimensions),
            ids=ids,
        )
        self.manifest_path(project_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def _load(self, *, project_id: str) -> tuple[dict[str, Any], np.ndarray | None]:
        if not self.manifest_path(project_id).exists():
            manifest = self.rebuild(project_id=project_id)
        else:
            manifest = json.loads(self.manifest_path(project_id).read_text(encoding="utf-8"))
        if manifest.get("status") != "available":
            return manifest, None
        vectors_path = self._project_dir(project_id) / "memory_vectors.npy"
        if not vectors_path.exists():
            return {
                **manifest,
                "status": "unavailable",
                "available": False,
                "reason": "Retrieval vectors are missing; run reindex for this project.",
            }, None
        vectors = np.load(vectors_path)
        return manifest, vectors

    def search_embedding(
        self, *, project_id: str, embedding: list[float], limit: int = 5
    ) -> list[dict[str, Any]]:
        manifest, vectors = self._load(project_id=project_id)
        if vectors is None or len(vectors) == 0:
            return []
        query_vector = np.asarray(embedding, dtype="float32")
        if len(query_vector) != int(manifest["dimensions"]):
            return []
        scores = vectors @ query_vector
        ordered = np.argsort(scores)[::-1][: max(1, limit)]
        results: list[dict[str, Any]] = []
        ids = manifest["ids"]
        for index in ordered:
            score = float(scores[index])
            if math.isclose(score, 0.0):
                continue
            memory_item = self.memory.get_memory_item(ids[int(index)])
            results.append({"score": score, "memoryItem": memory_item})
        return results

    def search(self, *, project_id: str, query: str, limit: int = 5) -> dict[str, Any]:
        provider_status = self.embedding_provider.status()
        if provider_status.get("status") != "available":
            return {
                "status": str(provider_status.get("status") or "configuration_required"),
                "reason": str(provider_status.get("reason") or QUERY_PROVIDER_REQUIRED_REASON),
                "results": [],
            }
        try:
            query_embedding = self.embedding_provider.embed_text(query)
        except Exception as error:
            return {"status": "unavailable", "reason": f"Query embedding failed: {error}", "results": []}
        manifest, vectors = self._load(project_id=project_id)
        if manifest.get("status") != "available" or vectors is None:
            return {
                "status": str(manifest.get("status") or "unavailable"),
                "reason": str(manifest.get("reason") or "Retrieval index is unavailable."),
                "results": [],
            }
        return {
            "status": "available",
            "reason": "",
            "results": self.search_embedding(project_id=project_id, embedding=query_embedding, limit=limit),
        }
