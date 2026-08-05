"""Almacén en disco de artefactos de evidencia y promoción de payloads grandes.

Escribe artefactos bajo `<root>/.tmp/evidence-artifacts/` con su hash SHA-256, y promueve a
ese almacén los logs, diffs y screenshots que exceden los límites inline para no inflar la fila
del paquete. Todo contenido textual pasa por `redact_secrets`. También limpia artefactos
huérfanos comparándolos contra el conjunto de rutas aún referenciadas.

@author Rodrigo Mason
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.redaction import redact_secrets

INLINE_PATCH_LIMIT_BYTES = 12_000
INLINE_LOG_LIMIT_BYTES = 12_000


def evidence_artifact_root(root: Path) -> Path:
    """Devuelve el directorio donde viven los artefactos para un root de trabajo dado."""
    return root / ".tmp" / "evidence-artifacts"


def resolved_artifact_root(root: Path) -> Path:
    """Versión canónica (resuelta) del root de artefactos, base para confinar rutas de descarga."""
    return evidence_artifact_root(root).resolve(strict=False)


def _artifact_file_entry(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=False)
    size = path.stat().st_size if path.exists() and path.is_file() else 0
    return {"path": str(resolved), "sizeBytes": size}


def write_text_artifact(*, root: Path, artifact_id: str, suffix: str, content: str) -> dict[str, Any]:
    """Escribe contenido textual (UTF-8) como artefacto y devuelve su ruta, hash y tamaño."""
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
    """Escribe bytes crudos como artefacto y devuelve su ruta, hash y tamaño."""
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
    """Resume un artefacto en la referencia compacta (id/kind/hash/name/sizeBytes) que guarda el paquete."""
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
    """Indexa los hashes de los artefactos tanto por nombre como por id, para verificación cruzada."""
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
    """Resuelve ids a registros de artefacto vía el repo, deduplicando y omitiendo los inexistentes.

    El repo llega duck-typed desde varios agentes: si expone ``get_artifacts_by_ids`` se
    resuelve el lote en un solo SELECT; si no (dobles de test), cae al loop id a id.
    """
    if hasattr(repo, "get_artifacts_by_ids"):
        return repo.get_artifacts_by_ids(artifact_ids)
    artifacts: list[dict[str, Any]] = []
    for artifact_id in sorted({str(item) for item in artifact_ids if item}):
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
    """Mueve los patches git que superan el límite inline a artefactos `.patch` en disco.

    Para cada diff_ref `git_diff` con `patchFull` grande, escribe el patch como artefacto y deja
    en su lugar una referencia truncada (id/tamaño/hash). Los patches pequeños quedan inline.

    Returns:
        Las refs de diff promovidas y las specs de artefacto creadas, en ese orden.
    """
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
            artifact = write_text_artifact(
                root=root, artifact_id=artifact_id, suffix=".patch", content=patch_full
            )
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
    """Detecta (y en `dry_run=False` borra) los ficheros del root no presentes en `referenced_paths`.

    Compara las rutas reales bajo el root de artefactos contra las aún referenciadas por algún
    artefacto registrado. En modo dry-run solo informa; nunca toca ficheros fuera del root.

    Returns:
        Resumen con root, huérfanos detectados, borrados efectivos y cuántos referenciados se conservaron.
    """
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
    """Redacta cada log y promueve a artefacto `.log` los que exceden el límite inline.

    Los logs cortos quedan inline (ya redactados); los grandes se escriben en disco y se
    reemplazan por una referencia truncada (id/tamaño/hash).

    Returns:
        Los logs resultantes y las specs de artefacto generadas, en ese orden.
    """
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
    force_streams: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Redacta stdout/stderr de una ejecución y promueve a artefacto los streams demasiado grandes.

    Mantiene los streams cortos inline; los largos se sacan del dict y se sustituyen por
    referencias (`<stream>ArtifactId/SizeBytes/Hash/Truncated`). `Truncated` refleja pérdida real
    durante la captura, no el hecho de haber movido contenido completo a un artefacto.

    Returns:
        El resultado de ejecución ajustado y las specs de artefacto generadas, en ese orden.
    """
    promoted_result = dict(execution_result)
    artifact_specs: list[dict[str, Any]] = []
    for stream in ("stdout", "stderr"):
        content = promoted_result.get(stream)
        if not isinstance(content, str):
            continue
        content = str(redact_secrets(content))
        promoted_result[stream] = content
        size = len(content.encode("utf-8"))
        capture_truncated = bool(
            promoted_result.get(f"{stream}CaptureTruncated", False)
            or promoted_result.get(f"{stream}Truncated", False)
        )
        if size <= INLINE_LOG_LIMIT_BYTES and stream not in (force_streams or set()):
            continue
        artifact_id = f"artifact-{uuid.uuid4()}"
        artifact = write_text_artifact(
            root=root, artifact_id=artifact_id, suffix=f".{stream}.log", content=content
        )
        promoted_result.pop(stream, None)
        promoted_result.update(
            {
                f"{stream}ArtifactId": artifact_id,
                f"{stream}SizeBytes": artifact["sizeBytes"],
                f"{stream}Hash": artifact["hash"],
                f"{stream}Truncated": capture_truncated,
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
                    "truncated": capture_truncated,
                },
            }
        )
    return promoted_result, artifact_specs


def promote_screenshots(
    *,
    root: Path,
    screenshot_refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Decodifica los screenshots base64 y los escribe como artefactos binarios.

    Cada screenshot con `contentBase64` válido se persiste (png/bin según mimeType) y su ref se
    sanea reemplazando el base64 por id/tamaño/hash; los base64 inválidos se descartan del ref.

    Returns:
        Las refs de screenshot saneadas y las specs de artefacto generadas, en ese orden.
    """
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
