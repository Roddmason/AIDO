"""Durable, project-scoped artifact boundary for generated NVIDIA images.

@author Rodrigo Mason
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import uuid
from pathlib import Path

from local_control_center.evidence.artifacts import resolved_artifact_root, write_binary_artifact
from local_control_center.evidence.image_validation import (
    ImageValidationError,
    read_validated_image,
)
from local_control_center.evidence.image_validation import (
    inspect_image as validate_image,
)
from local_control_center.evidence.repository import EvidenceRepository

from .capabilities import ImageArtifactReference

ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg"}
ALLOWED_INPUT_ARTIFACT_KINDS = {"generated_image", "screenshot", "generic_artifact"}


class ImageArtifactError(RuntimeError):
    """Stable artifact conflict raised before provider transport or public file access."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def inspect_image(content: bytes) -> tuple[str, str, int, int]:
    """Translate dependency-neutral validation failures to the provider artifact contract."""
    try:
        return validate_image(content)
    except ImageValidationError as error:
        raise ImageArtifactError(error.code) from error


class DurableImageArtifactStore:
    """Validate, persist and reload images without exposing filesystem records."""

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.repository = EvidenceRepository(connection)

    def _require_project(self, project_id: str) -> None:
        row = self.connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise ImageArtifactError("project_not_found")

    def require_project(self, *, project_id: str) -> None:
        """Fail before provider transport when the output project does not exist."""
        self._require_project(project_id)

    def _confined_path(self, raw_path: str) -> Path:
        artifact_root = resolved_artifact_root(self.root)
        path = Path(raw_path).resolve(strict=False)
        try:
            path.relative_to(artifact_root)
        except ValueError as error:
            raise ImageArtifactError("image_artifact_path_forbidden") from error
        return path

    def read_image_data_url(self, *, project_id: str, artifact_id: str) -> str:
        """Load a validated image owned by the exact requested project."""
        try:
            artifact = self.repository.get_project_artifact(
                project_id=project_id,
                artifact_id=artifact_id,
            )
        except KeyError as error:
            raise ImageArtifactError("image_artifact_not_found") from error
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        mime_type = str(metadata.get("mimeType") or "")
        if (
            artifact.get("kind") not in ALLOWED_INPUT_ARTIFACT_KINDS
            or mime_type not in ALLOWED_IMAGE_MIME_TYPES
        ):
            raise ImageArtifactError("image_artifact_media_forbidden")
        path = self._confined_path(str(artifact.get("path") or ""))
        if not path.exists() or not path.is_file():
            raise ImageArtifactError("image_artifact_missing")
        try:
            content, actual_mime_type, _suffix, _width, _height = read_validated_image(path)
        except ImageValidationError as error:
            raise ImageArtifactError(error.code) from error
        expected_hash = str(artifact.get("hash") or "")
        if not expected_hash or hashlib.sha256(content).hexdigest() != expected_hash:
            raise ImageArtifactError("image_artifact_hash_mismatch")
        if actual_mime_type != mime_type:
            raise ImageArtifactError("image_artifact_media_mismatch")
        return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"

    def persist_image(
        self,
        *,
        project_id: str,
        provider_id: str,
        model: str,
        content: bytes,
    ) -> ImageArtifactReference:
        """Persist one provider image after all validation succeeds."""
        self._require_project(project_id)
        mime_type, suffix, width, height = inspect_image(content)
        artifact_id = f"artifact-{uuid.uuid4()}"
        artifact_file = write_binary_artifact(
            root=self.root,
            artifact_id=artifact_id,
            suffix=suffix,
            content=content,
        )
        path = self._confined_path(str(artifact_file["path"]))
        try:
            self.repository.create_artifact(
                project_id=project_id,
                evidence_package_id=None,
                kind="generated_image",
                path=str(path),
                content_hash=str(artifact_file["hash"]),
                metadata={
                    "name": f"{artifact_id}{suffix}",
                    "source": "nvidia_nim",
                    "mimeType": mime_type,
                    "sizeBytes": artifact_file["sizeBytes"],
                    "providerId": provider_id,
                    "model": model,
                    "width": width,
                    "height": height,
                },
                artifact_id=artifact_id,
            )
        except Exception:
            if path.exists() and path.is_file():
                path.unlink()
            raise
        return ImageArtifactReference(
            artifactId=artifact_id,
            mimeType=mime_type,
            sizeBytes=int(artifact_file["sizeBytes"]),
            sha256=str(artifact_file["hash"]),
            downloadPath=f"/api/v1/projects/{project_id}/artifacts/{artifact_id}",
        )
