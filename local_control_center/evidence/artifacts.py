from __future__ import annotations

import hashlib
import base64
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.redaction import redact_secrets


INLINE_PATCH_LIMIT_BYTES = 12_000
INLINE_LOG_LIMIT_BYTES = 12_000


def evidence_artifact_root(root: Path) -> Path:
    return root / ".tmp" / "evidence-artifacts"


def resolved_artifact_root(root: Path) -> Path:
    return evidence_artifact_root(root).resolve(strict=False)


def _artifact_file_entry(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=False)
    size = path.stat().st_size if path.exists() and path.is_file() else 0
    return {"path": str(resolved), "sizeBytes": size}


def write_text_artifact(*, root: Path, artifact_id: str, suffix: str, content: str) -> dict[str, Any]:
    artifact_dir = evidence_artifact_root(root)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{artifact_id}{suffix}"
    content_bytes = content.encode("utf-8")
    path.write_bytes(content_bytes)
    return {
        "artifactId": artifact_id,
        "path": str(path),
        "hash": hashlib.sha256(content_bytes).hexdigest(),
        "sizeBytes": len(content_bytes),
    }


def write_binary_artifact(*, root: Path, artifact_id: str, suffix: str, content: bytes) -> dict[str, Any]:
    artifact_dir = evidence_artifact_root(root)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{artifact_id}{suffix}"
    path.write_bytes(content)
    return {
        "artifactId": artifact_id,
        "path": str(path),
        "hash": hashlib.sha256(content).hexdigest(),
        "sizeBytes": len(content),
    }


def artifact_ref(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    ref = {
        "id": artifact.get("id"),
        "kind": artifact.get("kind"),
        "hash": artifact.get("hash"),
        "name": metadata.get("name") or artifact.get("id"),
    }
    if metadata.get("sizeBytes") is not None:
        ref["sizeBytes"] = metadata.get("sizeBytes")
    return {key: value for key, value in ref.items() if value is not None}


def artifact_hashes(artifacts: list[dict[str, Any]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for artifact in artifacts:
        content_hash = artifact.get("hash")
        if not isinstance(content_hash, str) or not content_hash:
            continue
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        name = metadata.get("name")
        if isinstance(name, str) and name:
            hashes[name] = content_hash
        artifact_id = artifact.get("id")
        if isinstance(artifact_id, str) and artifact_id:
            hashes[artifact_id] = content_hash
    return hashes


def artifact_records_from_ids(repo: Any, artifact_ids: list[str]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for artifact_id in sorted(set(str(item) for item in artifact_ids if item)):
        try:
            artifacts.append(repo.get_artifact_by_id(artifact_id))
        except KeyError:
            continue
    return artifacts


def promote_large_git_patches(
    *,
    root: Path,
    diff_refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    promoted_refs: list[dict[str, Any]] = []
    artifact_specs: list[dict[str, Any]] = []
    for diff_ref in diff_refs:
        if diff_ref.get("kind") != "git_diff":
            promoted_refs.append(diff_ref)
            continue
        patch_full = str(diff_ref.pop("patchFull", ""))
        patch_size = len(patch_full.encode("utf-8"))
        if patch_full and patch_size > INLINE_PATCH_LIMIT_BYTES:
            artifact_id = f"artifact-{uuid.uuid4()}"
            artifact = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".patch", content=patch_full)
            sanitized = {key: value for key, value in diff_ref.items() if key != "patch"}
            sanitized.update(
                {
                    "patchArtifactId": artifact_id,
                    "patchSizeBytes": artifact["sizeBytes"],
                    "patchHash": artifact["hash"],
                    "truncated": True,
                }
            )
            promoted_refs.append(sanitized)
            artifact_specs.append(
                {
                    "id": artifact_id,
                    "kind": "git_patch",
                    "path": artifact["path"],
                    "hash": artifact["hash"],
                    "metadata": {
                        "sizeBytes": artifact["sizeBytes"],
                        "source": "workspace_archive",
                        "diffKind": "git_diff",
                    },
                }
            )
        else:
            if patch_full:
                diff_ref["patch"] = patch_full
                diff_ref["patchSizeBytes"] = patch_size
            promoted_refs.append(diff_ref)
    return promoted_refs, artifact_specs


def cleanup_unreferenced_artifacts(
    *,
    root: Path,
    referenced_paths: set[str],
    dry_run: bool = True,
) -> dict[str, Any]:
    artifact_root = resolved_artifact_root(root)
    if not artifact_root.exists():
        return {
            "dryRun": dry_run,
            "artifactRoot": str(artifact_root),
            "orphanFiles": [],
            "deletedFiles": [],
            "keptReferencedFiles": 0,
        }

    referenced = {str(Path(path).resolve(strict=False)) for path in referenced_paths if path}
    orphan_files: list[dict[str, Any]] = []
    deleted_files: list[dict[str, Any]] = []
    kept_referenced_files = 0
    for path in sorted(artifact_root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(artifact_root)
        except ValueError:
            continue
        entry = _artifact_file_entry(path)
        if str(resolved) in referenced:
            kept_referenced_files += 1
            continue
        orphan_files.append(entry)
        if not dry_run:
            path.unlink()
            deleted_files.append(entry)

    return {
        "dryRun": dry_run,
        "artifactRoot": str(artifact_root),
        "orphanFiles": orphan_files,
        "deletedFiles": deleted_files,
        "keptReferencedFiles": kept_referenced_files,
    }


def promote_large_logs(
    *,
    root: Path,
    logs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    promoted_logs: list[dict[str, Any]] = []
    artifact_specs: list[dict[str, Any]] = []
    for log in logs:
        content = log.get("content")
        if not isinstance(content, str):
            promoted_logs.append(redact_secrets(log))
            continue
        content = str(redact_secrets(content))
        size = len(content.encode("utf-8"))
        if size <= INLINE_LOG_LIMIT_BYTES:
            promoted_logs.append(redact_secrets({**log, "content": content}))
            continue
        artifact_id = f"artifact-{uuid.uuid4()}"
        artifact = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".log", content=content)
        sanitized = redact_secrets({key: value for key, value in log.items() if key != "content"})
        sanitized.update(
            {
                "logArtifactId": artifact_id,
                "logSizeBytes": artifact["sizeBytes"],
                "logHash": artifact["hash"],
                "truncated": True,
            }
        )
        promoted_logs.append(sanitized)
        artifact_specs.append(
            {
                "id": artifact_id,
                "kind": "execution_log",
                "path": artifact["path"],
                "hash": artifact["hash"],
                "metadata": {
                    "sizeBytes": artifact["sizeBytes"],
                    "source": "evidence_api",
                    "name": log.get("name", ""),
                },
            }
        )
    return promoted_logs, artifact_specs


def promote_execution_result_outputs(
    *,
    root: Path,
    execution_result: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    promoted_result = dict(execution_result)
    artifact_specs: list[dict[str, Any]] = []
    for stream in ("stdout", "stderr"):
        content = promoted_result.get(stream)
        if not isinstance(content, str):
            continue
        content = str(redact_secrets(content))
        promoted_result[stream] = content
        size = len(content.encode("utf-8"))
        if size <= INLINE_LOG_LIMIT_BYTES:
            continue
        artifact_id = f"artifact-{uuid.uuid4()}"
        artifact = write_text_artifact(root=root, artifact_id=artifact_id, suffix=f".{stream}.log", content=content)
        promoted_result.pop(stream, None)
        promoted_result.update(
            {
                f"{stream}ArtifactId": artifact_id,
                f"{stream}SizeBytes": artifact["sizeBytes"],
                f"{stream}Hash": artifact["hash"],
                f"{stream}Truncated": True,
            }
        )
        artifact_specs.append(
            {
                "id": artifact_id,
                "kind": "execution_log",
                "path": artifact["path"],
                "hash": artifact["hash"],
                "metadata": {
                    "sizeBytes": artifact["sizeBytes"],
                    "source": "agent_tool_execution",
                    "stream": stream,
                },
            }
        )
    return promoted_result, artifact_specs


def promote_screenshots(
    *,
    root: Path,
    screenshot_refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    promoted_refs: list[dict[str, Any]] = []
    artifact_specs: list[dict[str, Any]] = []
    for screenshot in screenshot_refs:
        encoded = screenshot.get("contentBase64")
        if not isinstance(encoded, str):
            promoted_refs.append(screenshot)
            continue
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError:
            promoted_refs.append({key: value for key, value in screenshot.items() if key != "contentBase64"})
            continue
        artifact_id = f"artifact-{uuid.uuid4()}"
        suffix = ".png" if screenshot.get("mimeType") == "image/png" else ".bin"
        artifact = write_binary_artifact(root=root, artifact_id=artifact_id, suffix=suffix, content=content)
        sanitized = {key: value for key, value in screenshot.items() if key != "contentBase64"}
        sanitized.update(
            {
                "screenshotArtifactId": artifact_id,
                "screenshotSizeBytes": artifact["sizeBytes"],
                "screenshotHash": artifact["hash"],
            }
        )
        promoted_refs.append(sanitized)
        artifact_specs.append(
            {
                "id": artifact_id,
                "kind": "screenshot",
                "path": artifact["path"],
                "hash": artifact["hash"],
                "metadata": {
                    "sizeBytes": artifact["sizeBytes"],
                    "source": "evidence_api",
                    "name": screenshot.get("name", ""),
                    "mimeType": screenshot.get("mimeType", "application/octet-stream"),
                },
            }
        )
    return promoted_refs, artifact_specs
