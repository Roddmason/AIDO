"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "test-results",
}
MAX_FILES = 200


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_workspace_snapshot(workspace_path: str | Path) -> dict[str, Any]:
    root = Path(workspace_path)
    if not root.exists():
        return {
            "kind": "workspace_snapshot",
            "status": "missing",
            "path": str(root),
            "files": [],
            "fileCount": 0,
        }

    files: list[dict[str, Any]] = []
    total = 0
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root)
        if _is_ignored(relative) or not candidate.is_file():
            continue
        total += 1
        if len(files) >= MAX_FILES:
            continue
        files.append(
            {
                "path": relative.as_posix(),
                "sizeBytes": candidate.stat().st_size,
                "sha256": _sha256(candidate),
            }
        )

    return {
        "kind": "workspace_snapshot",
        "status": "captured",
        "path": str(root),
        "fileCount": total,
        "truncated": total > MAX_FILES,
        "files": files,
    }
