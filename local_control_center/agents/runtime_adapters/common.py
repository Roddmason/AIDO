"""Shared validation, redaction, and evidence-recording helpers for runtime adapters.

Hosts the runtime tool-name allowlist plus the argv/workspace/timeout guards every adapter
applies before executing, and the `_ArtifactRecorder` that persists redacted adapter output
as artifacts and evidence packages.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import INLINE_LOG_LIMIT_BYTES, write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.security_policy.sandbox import (
    ALLOWED_EXECUTABLES,
    DANGEROUS_ARG_PREFIXES,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from ..provider_catalog import MODEL_PROVIDER_FAMILIES
from .models import RuntimeExecutionRequest, RuntimeExecutionResult

RUNTIME_ADAPTER_TOOLS = {
    "mcp",
    "openhands",
    "swe_agent",
    "workspace_patch",
} | set(MODEL_PROVIDER_FAMILIES)


def _result(
    *,
    status: str,
    started_at: str,
    exit_code: int | None = None,
    reason: str | None = None,
    stdout_artifact_id: str | None = None,
    stderr_artifact_id: str | None = None,
    output_artifact_id: str | None = None,
    evidence_package_id: str | None = None,
    redacted: bool = False,
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        status=status,
        exitCode=exit_code,
        stdoutArtifactId=stdout_artifact_id,
        stderrArtifactId=stderr_artifact_id,
        outputArtifactId=output_artifact_id,
        evidencePackageId=evidence_package_id,
        startedAt=started_at,
        completedAt=utc_now(),
        reason=reason,
        redacted=redacted,
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _validate_structured_argv(argv: Any) -> list[str] | str:
    if isinstance(argv, str):
        return "Runtime execution requires structured argv; command strings are not accepted."
    if not isinstance(argv, list) or not argv:
        return "Runtime execution requires a non-empty argv list."
    if not all(isinstance(item, str) and item for item in argv):
        return "Runtime execution requires structured argv strings."
    return argv


def _dangerous_arg(argv: list[str]) -> str | None:
    args = argv[1:]
    for index, arg in enumerate(args):
        lowered = arg.strip().lower()
        if lowered == "--network" and index + 1 < len(args) and args[index + 1].strip().lower() == "host":
            return "--network host"
        if any(lowered == prefix or lowered.startswith(f"{prefix}=") for prefix in DANGEROUS_ARG_PREFIXES):
            return arg
    return None


def _validate_restricted_subprocess_argv(argv: list[str]) -> str | None:
    executable = Path(argv[0]).name.lower()
    if executable not in ALLOWED_EXECUTABLES:
        return f"Executable is not allowlisted for restricted subprocess: {executable}"
    blocked_arg = _dangerous_arg(argv)
    if blocked_arg:
        return f"Dangerous subprocess flag is blocked by runtime policy: {blocked_arg}"
    return None


def _execution_cwd(request: RuntimeExecutionRequest) -> Path | str:
    workspace = Path(request.workspace_path).resolve(strict=False)
    cwd_value = request.metadata.get("cwd") or request.metadata.get("workdir") or request.metadata.get("path")
    cwd = Path(str(cwd_value)).resolve(strict=False) if cwd_value else workspace
    if not _inside(cwd, workspace):
        return "Working directory is outside the allocated workspace."
    if not cwd.exists() or not cwd.is_dir():
        return "Working directory does not exist."
    return cwd


def _registered_workspace_error(
    *,
    request: RuntimeExecutionRequest,
    connection: sqlite3.Connection | None,
) -> str | None:
    if connection is None:
        return None
    row = connection.execute(
        "SELECT path, status FROM workspaces WHERE id = ?",
        (request.workspace_id,),
    ).fetchone()
    if row is None:
        return "Runtime execution requires a registered workspace."
    registered_path = Path(str(row["path"])).resolve(strict=False)
    requested_path = Path(request.workspace_path).resolve(strict=False)
    if row["status"] == "archived":
        return "Runtime execution workspace is archived."
    if registered_path != requested_path:
        return "Runtime execution workspacePath does not match the registered workspace."
    if not requested_path.exists() or not requested_path.is_dir():
        return "Runtime execution workspace path does not exist."
    return None


def _bounded_timeout(request: RuntimeExecutionRequest) -> int:
    return max(1, min(int(request.timeout_seconds or 30), 900))


def _redact_text(value: str) -> tuple[str, bool]:
    redacted = str(redact_secrets(value))
    return redacted, redacted != value


class _ArtifactRecorder:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection | None,
        artifact_root: str | Path | None,
        adapter_id: str,
        inline_limit_bytes: int = INLINE_LOG_LIMIT_BYTES,
    ):
        self.connection = connection
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None
        self.adapter_id = adapter_id
        self.inline_limit_bytes = inline_limit_bytes

    def record_large_stream(
        self,
        *,
        request: RuntimeExecutionRequest,
        stream: str,
        content: str,
    ) -> tuple[str | None, bool]:
        if not content:
            return None, False
        clean_content, redacted = _redact_text(content)
        if len(clean_content.encode("utf-8")) <= self.inline_limit_bytes:
            return None, redacted
        return self.record_output(
            request=request, name=f"{stream}.log", stream=stream, content=clean_content
        ), redacted

    def record_stream_artifact(
        self,
        *,
        request: RuntimeExecutionRequest,
        stream: str,
        content: str,
    ) -> tuple[str | None, bool]:
        clean_content, redacted = _redact_text(content)
        return self.record_output(
            request=request,
            name=f"{stream}.log",
            stream=stream,
            content=clean_content,
            allow_empty=True,
        ), redacted

    def record_output(
        self,
        *,
        request: RuntimeExecutionRequest,
        name: str,
        content: str,
        stream: str = "output",
        allow_empty: bool = False,
    ) -> str | None:
        if self.connection is None or self.artifact_root is None or (not content and not allow_empty):
            return None
        artifact_id = f"artifact-{uuid.uuid4()}"
        suffix = Path(name).suffix or ".log"
        artifact_file = write_text_artifact(
            root=self.artifact_root,
            artifact_id=artifact_id,
            suffix=suffix,
            content=content,
        )
        EvidenceRepository(self.connection).create_artifact(
            artifact_id=artifact_id,
            project_id=request.project_id,
            evidence_package_id=None,
            kind="execution_log",
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={
                "name": name,
                "source": f"runtime_adapter:{self.adapter_id}",
                "stream": stream,
                "sizeBytes": artifact_file["sizeBytes"],
                "mimeType": "text/plain",
            },
        )
        return artifact_id

    def create_evidence(
        self,
        *,
        request: RuntimeExecutionRequest,
        status: str,
        exit_code: int | None,
        reason: str | None,
        artifact_ids: list[str],
    ) -> str | None:
        if self.connection is None:
            return None
        command = " ".join(request.argv) if isinstance(request.argv, list) else "<invalid argv>"
        qa_verdict = (
            "evidence_collected" if status == "completed" else "failed" if status == "failed" else "blocked"
        )
        evidence_repo = EvidenceRepository(self.connection)
        evidence = evidence_repo.create_evidence_package(
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            workflow_step_id=request.workflow_step_id,
            agent_run_id=request.agent_run_id,
            job_id=request.job_id,
            workspace_id=request.workspace_id,
            runtime_id=self.adapter_id,
            task_id=request.capability,
            test_plan="Capture real runtime adapter execution.",
            test_results=[
                {
                    "command": str(redact_secrets(command)),
                    "status": status,
                    "returnCode": exit_code,
                    "reason": str(redact_secrets(reason or "")),
                    "adapterId": self.adapter_id,
                }
            ],
            artifact_ids=artifact_ids,
            evidence_source="evidence_collected",
            qa_verdict=qa_verdict,
        )
        for artifact_id in artifact_ids:
            evidence_repo.attach_artifact_to_evidence(
                artifact_id=artifact_id,
                evidence_package_id=evidence["id"],
            )
        return str(evidence["id"])
