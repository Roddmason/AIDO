from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from local_control_center.store import PlatformStore

try:  # pragma: no cover - exercised only when faiss-cpu is installed.
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def text_embedding(text: str, dimensions: int = 128) -> np.ndarray:
    vector = np.zeros(dimensions, dtype="float32")
    for token in TOKEN_RE.findall(text.lower()):
        index = hash(token) % dimensions
        vector[index] += 1.0
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


class RetrievalIndex:
    def __init__(self, *, store: "PlatformStore", index_dir: str | Path | None = None, dimensions: int = 128):
        self.store = store
        self.index_dir = Path(index_dir or (store.db_path.parent / "faiss-index"))
        self.dimensions = dimensions

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    def status(self) -> dict[str, Any]:
        manifest = {}
        if self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        backend = "faiss" if faiss is not None else "numpy"
        return {
            "backend": backend,
            "degraded": backend != "faiss",
            "faissAvailable": faiss is not None,
            "indexDir": str(self.index_dir),
            "indexed": manifest.get("indexed", 0),
            "dimensions": manifest.get("dimensions", self.dimensions),
        }

    def rebuild(self) -> dict[str, Any]:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        memory_items = self.store.list_memory_items()
        ids: list[str] = []
        vectors: list[np.ndarray] = []
        for item in memory_items:
            vector = text_embedding(item["content"], self.dimensions)
            ids.append(item["id"])
            vectors.append(vector)
            self.store.upsert_memory_embedding(
                memory_item_id=item["id"],
                provider="local",
                model="hashing-bow",
                embedding=vector.tolist(),
            )
        matrix = np.vstack(vectors).astype("float32") if vectors else np.zeros((0, self.dimensions), dtype="float32")
        backend = "numpy"
        if faiss is not None and len(matrix):
            backend = "faiss"
            index = faiss.IndexFlatIP(self.dimensions)
            index.add(matrix)
            faiss.write_index(index, str(self.index_dir / "memory.faiss"))
        np.save(self.index_dir / "memory_vectors.npy", matrix)
        manifest = {
            "backend": backend,
            "dimensions": self.dimensions,
            "ids": ids,
            "indexed": len(ids),
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def _load(self) -> tuple[dict[str, Any], np.ndarray]:
        if not self.manifest_path.exists():
            self.rebuild()
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        vectors_path = self.index_dir / "memory_vectors.npy"
        vectors = np.load(vectors_path) if vectors_path.exists() else np.zeros((0, self.dimensions), dtype="float32")
        return manifest, vectors

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        manifest, vectors = self._load()
        if len(vectors) == 0:
            return []
        query_vector = text_embedding(query, int(manifest["dimensions"]))
        scores = vectors @ query_vector
        ordered = np.argsort(scores)[::-1][: max(1, limit)]
        results: list[dict[str, Any]] = []
        ids = manifest["ids"]
        for index in ordered:
            score = float(scores[index])
            if math.isclose(score, 0.0):
                continue
            memory_item = self.store.get_memory_item(ids[int(index)])
            results.append({"score": score, "memoryItem": memory_item})
        return results
