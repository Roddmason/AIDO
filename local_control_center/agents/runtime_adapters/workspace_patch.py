"""Guarded workspace file-patch adapter for DeveloperAgent writes inside the workspace.

Validates every patched path against traversal, symlink, secret/credential, file-count, and
total-byte guards before writing, and records a redacted patch manifest artifact for the
applied files.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.redaction import redact_secrets

from .common import _inside

PATCH_FILE_LIMIT = 20
PATCH_BYTES_LIMIT = 1024 * 1024
SENSITIVE_PATH_PARTS = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_ed25519",
    "id_rsa",
}


def _workspace_patch_path_error(workspace: Path, relative_path: str) -> str | None:
    if not relative_path.strip():
        return "Patch file path is required."
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return "Patch file paths must be relative to the workspace."
    parts = [part.lower() for part in candidate.parts]
    if any(part in {"", ".", ".."} for part in parts):
        return "Patch file paths cannot contain traversal segments."
    if any(part in SENSITIVE_PATH_PARTS or "secret" in part or "credential" in part for part in parts):
        return "DeveloperAgent cannot modify secret or credential paths."
    target = (workspace / candidate).resolve(strict=False)
    if not _inside(target, workspace):
        return "Patch file path is outside the allocated workspace."
    if target.exists() and target.is_symlink():
        return "DeveloperAgent cannot write through symlink targets."
    return None


class WorkspacePatchBrokerAdapter:
    """Applies DeveloperAgent file writes inside the workspace under strict path/size guards."""

    def __init__(self, *, connection: sqlite3.Connection, artifact_root: str | Path | None = None):
        self.connection = connection
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        """Validate and write `input.files` into the workspace, recording a redacted patch manifest.

        Blocks absolute/traversal/symlink/secret paths and enforces the file-count and
        total-byte limits; only relative, in-workspace targets are written.
        """
        workspace_value = str(policy_input.get("workspacePath") or "").strip()
        if not workspace_value:
            return {"executed": False, "blocked": True, "reason": "Workspace patch requires workspacePath."}
        workspace = Path(workspace_value).resolve(strict=False)
        patch_input = tool_call.get("input") or {}
        files = patch_input.get("files")
        if not isinstance(files, list) or not files:
            return {
                "executed": False,
                "blocked": True,
                "reason": "Workspace patch requires a non-empty files list.",
            }
        if len(files) > PATCH_FILE_LIMIT:
            return {
                "executed": False,
                "blocked": True,
                "reason": f"Workspace patch exceeds file limit: {PATCH_FILE_LIMIT}.",
            }

        normalized: list[dict[str, str]] = []
        total_bytes = 0
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                return {"executed": False, "blocked": True, "reason": f"files[{index}] must be an object."}
            path = str(item.get("path") or "")
            content = item.get("content")
            if not isinstance(content, str):
                return {
                    "executed": False,
                    "blocked": True,
                    "reason": f"files[{index}].content must be a string.",
                }
            path_error = _workspace_patch_path_error(workspace, path)
            if path_error:
                return {"executed": False, "blocked": True, "reason": path_error}
            total_bytes += len(content.encode("utf-8"))
            if total_bytes > PATCH_BYTES_LIMIT:
                return {
                    "executed": False,
                    "blocked": True,
                    "reason": f"Workspace patch exceeds byte limit: {PATCH_BYTES_LIMIT}.",
                }
            normalized.append({"path": path, "content": content})

        written: list[dict[str, Any]] = []
        for item in normalized:
            target = workspace / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8", newline="\n")
            written.append({"path": item["path"], "bytes": len(item["content"].encode("utf-8"))})

        output_artifact_id = None
        if self.artifact_root is not None:
            artifact_id = f"artifact-{uuid.uuid4()}"
            content = json.dumps(
                redact_secrets(
                    {
                        "summary": patch_input.get("summary"),
                        "writtenFiles": written,
                        "source": "developer_agent_patch_apply",
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
            artifact_file = write_text_artifact(
                root=self.artifact_root,
                artifact_id=artifact_id,
                suffix=".json",
                content=content,
            )
            EvidenceRepository(self.connection).create_artifact(
                artifact_id=artifact_id,
                project_id=str(policy_input["projectId"]),
                evidence_package_id=None,
                kind="workspace_patch_manifest",
                path=artifact_file["path"],
                content_hash=artifact_file["hash"],
                metadata={
                    "name": "developer-agent-patch-apply.json",
                    "source": "developer_agent",
                    "mimeType": "application/json",
                    "sizeBytes": artifact_file["sizeBytes"],
                },
            )
            output_artifact_id = artifact_id

        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "stdout": json.dumps({"writtenFiles": written}, ensure_ascii=False),
            "stderr": "",
            "outputArtifactId": output_artifact_id,
            "writtenFiles": written,
        }
