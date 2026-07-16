"""Workspace-bound subprocess adapters: restricted sandbox commands and CLI version probes.

`RestrictedSubprocessAdapter` runs allowlisted argv inside the workspace via the restricted
sandbox and captures stdout/stderr as evidence; `CliVersionAdapter` only allows a strict
`[executable, version-flag]` liveness check for each CLI runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from local_control_center.evidence.artifacts import INLINE_LOG_LIMIT_BYTES
from local_control_center.security_policy.sandbox import (
    RestrictedSubprocessSandbox,
    run_version_check,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .common import (
    _ArtifactRecorder,
    _bounded_timeout,
    _execution_cwd,
    _registered_workspace_error,
    _result,
    _validate_restricted_subprocess_argv,
    _validate_structured_argv,
)
from .models import RuntimeExecutionRequest, RuntimeExecutionResult

CLI_VERSION_ADAPTERS = {
    "codex": ("codex",),
    "codex_cli": ("codex",),
    "claude": ("claude",),
    "claude_code_cli": ("claude",),
    "openhands": ("openhands",),
    "swe-agent": ("swe", "agent"),
    "swe_agent": ("swe", "agent"),
}
VERSION_ARGS = {"--version", "-V", "version"}


class RestrictedSubprocessAdapter:
    """Runs allowlisted commands inside the workspace via the restricted sandbox, with evidence."""

    adapter_id = "restricted_subprocess"

    def __init__(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        inline_limit_bytes: int = INLINE_LOG_LIMIT_BYTES,
    ):
        self.recorder = _ArtifactRecorder(
            connection=connection,
            artifact_root=artifact_root,
            adapter_id=self.adapter_id,
            inline_limit_bytes=inline_limit_bytes,
        )

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Validate argv/workspace/cwd, run the command, and capture stdout/stderr as evidence.

        Blocks on unsafe argv, dangerous flags, unregistered/archived workspace, or a cwd
        outside the workspace; otherwise returns completed/failed/timed_out with artifacts.
        """
        started_at = utc_now()
        argv = _validate_structured_argv(request.argv)
        if isinstance(argv, str):
            return _result(status="blocked", started_at=started_at, reason=argv)
        blocked_argv = _validate_restricted_subprocess_argv(argv)
        if blocked_argv:
            return _result(status="blocked", started_at=started_at, reason=blocked_argv)

        workspace_error = _registered_workspace_error(request=request, connection=self.recorder.connection)
        if workspace_error:
            return _result(status="blocked", started_at=started_at, reason=workspace_error)

        cwd = _execution_cwd(request)
        if isinstance(cwd, str):
            return _result(status="blocked", started_at=started_at, reason=cwd)

        completed = RestrictedSubprocessSandbox().execute(
            argv=argv,
            cwd=str(cwd),
            workspace_path=request.workspace_path,
            timeout_seconds=_bounded_timeout(request),
            truncate_output=False,
        )
        if completed.get("timedOut"):
            stdout = str(completed.get("stdout") or "")
            stderr = str(completed.get("stderr") or "")
            stdout_id, stdout_redacted = self.recorder.record_large_stream(
                request=request,
                stream="stdout",
                content=stdout,
            )
            stderr_id, stderr_redacted = self.recorder.record_large_stream(
                request=request,
                stream="stderr",
                content=stderr,
            )
            reason = f"Runtime execution timed out after {_bounded_timeout(request)} seconds."
            artifact_ids = [artifact_id for artifact_id in (stdout_id, stderr_id) if artifact_id]
            evidence_id = self.recorder.create_evidence(
                request=request,
                status="timed_out",
                exit_code=None,
                reason=reason,
                artifact_ids=artifact_ids,
            )
            return _result(
                status="timed_out",
                started_at=started_at,
                reason=reason,
                stdout_artifact_id=stdout_id,
                stderr_artifact_id=stderr_id,
                evidence_package_id=evidence_id,
                redacted=stdout_redacted or stderr_redacted,
            )
        if completed.get("blocked"):
            return _result(
                status="blocked",
                started_at=started_at,
                reason=str(
                    redact_secrets(completed.get("reason") or "Restricted subprocess execution was blocked.")
                ),
                redacted=True,
            )

        stdout = str(completed.get("stdout") or "")
        stderr = str(completed.get("stderr") or "")
        if request.capability == "issue_to_patch_runtime":
            stdout_id, stdout_redacted = self.recorder.record_stream_artifact(
                request=request,
                stream="stdout",
                content=stdout,
            )
            stderr_id, stderr_redacted = self.recorder.record_stream_artifact(
                request=request,
                stream="stderr",
                content=stderr,
            )
        else:
            stdout_id, stdout_redacted = self.recorder.record_large_stream(
                request=request,
                stream="stdout",
                content=stdout,
            )
            stderr_id, stderr_redacted = self.recorder.record_large_stream(
                request=request,
                stream="stderr",
                content=stderr,
            )
        return_code = completed.get("returnCode")
        status = "completed" if return_code == 0 else "failed"
        reason = None if return_code == 0 else f"Process exited with code {return_code}."
        artifact_ids = [artifact_id for artifact_id in (stdout_id, stderr_id) if artifact_id]
        evidence_id = self.recorder.create_evidence(
            request=request,
            status=status,
            exit_code=return_code,
            reason=reason,
            artifact_ids=artifact_ids,
        )
        return _result(
            status=status,
            started_at=started_at,
            exit_code=return_code,
            reason=reason,
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
            evidence_package_id=evidence_id,
            redacted=stdout_redacted or stderr_redacted,
        )


class CliVersionAdapter:
    """Runs only a CLI runtime's `--version` check as a safe liveness probe."""

    def __init__(self, *, adapter_id: str):
        self.adapter_id = adapter_id

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Run a strict `[executable, version-flag]` check for this adapter's own CLI.

        Blocks any capability other than `version_check`, malformed argv, or a foreign
        executable; returns completed when the version probe exits 0.
        """
        started_at = utc_now()
        if self.adapter_id not in CLI_VERSION_ADAPTERS:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"CLI version adapter is not supported: {self.adapter_id}",
            )
        if request.capability != "version_check":
            return _result(
                status="blocked",
                started_at=started_at,
                reason="CliVersionAdapter only allows version_check execution.",
            )
        argv = _validate_structured_argv(request.argv)
        if isinstance(argv, str):
            return _result(status="blocked", started_at=started_at, reason=argv)
        if len(argv) != 2 or argv[1] not in VERSION_ARGS:
            return _result(
                status="blocked",
                started_at=started_at,
                reason="CLI version checks require argv shaped as [executable, version flag].",
            )
        executable_name = Path(argv[0]).name.lower()
        if not all(token in executable_name for token in CLI_VERSION_ADAPTERS[self.adapter_id]):
            return _result(
                status="blocked",
                started_at=started_at,
                reason=f"{self.adapter_id} version check requires its own executable.",
            )
        cwd = _execution_cwd(request)
        if isinstance(cwd, str):
            return _result(status="blocked", started_at=started_at, reason=cwd)
        completed = run_version_check(
            argv=argv,
            cwd=str(cwd),
            timeout_seconds=min(_bounded_timeout(request), 30),
        )
        if completed.get("timedOut"):
            return _result(
                status="timed_out",
                started_at=started_at,
                reason="CLI version check timed out.",
            )
        if completed.get("blocked"):
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=str(redact_secrets(completed.get("reason") or "CLI version check failed.")),
                redacted=True,
            )
        return_code = completed.get("returnCode")
        status = "completed" if return_code == 0 else "unavailable"
        reason = "CLI version check completed." if return_code == 0 else "CLI version check failed."
        return _result(status=status, started_at=started_at, exit_code=return_code, reason=reason)
