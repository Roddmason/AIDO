from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.evidence.artifacts import INLINE_LOG_LIMIT_BYTES, write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.security_policy.sandbox import (
    ALLOWED_EXECUTABLES,
    DANGEROUS_ARG_PREFIXES,
    RestrictedSubprocessSandbox,
    run_version_check,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .runtime_provider_config import runtime_provider_configuration


RUNTIME_ADAPTER_TOOLS = {"mcp", "openhands", "swe_agent", "ollama", "openai_compatible", "workspace_patch"}
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


class RuntimeExecutionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    job_id: str | None = Field(default=None, alias="jobId")
    agent_run_id: str | None = Field(default=None, alias="agentRunId")
    workspace_id: str = Field(alias="workspaceId")
    workspace_path: str = Field(alias="workspacePath")
    capability: str
    argv: Any = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeExecutionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    exit_code: int | None = Field(default=None, alias="exitCode")
    stdout_artifact_id: str | None = Field(default=None, alias="stdoutArtifactId")
    stderr_artifact_id: str | None = Field(default=None, alias="stderrArtifactId")
    output_artifact_id: str | None = Field(default=None, alias="outputArtifactId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    started_at: str = Field(default_factory=utc_now, alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    reason: str | None = None
    redacted: bool = False


class RuntimeAdapter(Protocol):
    adapter_id: str

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Execute a typed runtime request through a real adapter."""


class RuntimeExecutionAdapter(Protocol):
    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        """Execute a broker-approved runtime tool call."""


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
        return self.record_output(request=request, name=f"{stream}.log", stream=stream, content=clean_content), redacted

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
        qa_verdict = "evidence_collected" if status == "completed" else "failed" if status == "failed" else "blocked"
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
            qa_verdict=qa_verdict,
        )
        for artifact_id in artifact_ids:
            evidence_repo.attach_artifact_to_evidence(
                artifact_id=artifact_id,
                evidence_package_id=evidence["id"],
            )
        return str(evidence["id"])


class RestrictedSubprocessAdapter:
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
                reason=str(redact_secrets(completed.get("reason") or "Restricted subprocess execution was blocked.")),
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
    def __init__(self, *, adapter_id: str):
        self.adapter_id = adapter_id

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
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


class OllamaAdapter:
    adapter_id = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.environ = environ
        self.recorder = _ArtifactRecorder(connection=connection, artifact_root=artifact_root, adapter_id=self.adapter_id)

    def _configured_base_url(self) -> str | None:
        source = self.environ or os.environ
        configuration = runtime_provider_configuration("ollama", environ=source)
        base_url = (
            self.base_url
            or (configuration.value("baseUrl") if configuration else None)
            or source.get("OLLAMA_BASE_URL")
            or source.get("OLLAMA_HOST")
            or ""
        ).strip()
        return base_url.rstrip("/") or None

    def health_check(self) -> dict[str, Any]:
        base_url = self._configured_base_url()
        if not base_url:
            return {"status": "configuration_required", "available": False, "reason": "Ollama base URL is not configured."}
        try:
            request = Request(f"{base_url}/api/tags", method="GET")
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return {
                "status": "unavailable",
                "available": False,
                "reason": str(redact_secrets(f"Ollama health check failed: {error.__class__.__name__}")),
            }
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return {"status": "available", "available": True, "reason": "Ollama daemon responded.", "models": models}

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started_at = utc_now()
        base_url = self._configured_base_url()
        if not base_url:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason="Ollama base URL is not configured.",
            )
        health = self.health_check()
        if not health.get("available"):
            return _result(status=str(health["status"]), started_at=started_at, reason=str(health["reason"]))
        model = str(request.input.get("model") or "").strip()
        messages = request.input.get("messages")
        if not model:
            return _result(status="configuration_required", started_at=started_at, reason="Ollama model is required.")
        if not isinstance(messages, list) or not messages:
            return _result(status="blocked", started_at=started_at, reason="Ollama execution requires input.messages.")
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": request.input.get("options") or {},
            }
        ).encode("utf-8")
        try:
            http_request = Request(
                f"{base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(http_request, timeout=_bounded_timeout(request)) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=str(redact_secrets(f"Ollama execution failed: {error.__class__.__name__}")),
                redacted=True,
            )
        message = raw.get("message") if isinstance(raw, dict) else {}
        content = str((message or {}).get("content") or "")
        clean_content, redacted = _redact_text(content)
        output_id = self.recorder.record_output(request=request, name="ollama-output.txt", content=clean_content)
        evidence_id = self.recorder.create_evidence(
            request=request,
            status="completed",
            exit_code=0,
            reason=None,
            artifact_ids=[output_id] if output_id else [],
        )
        return _result(
            status="completed",
            started_at=started_at,
            exit_code=0,
            output_artifact_id=output_id,
            evidence_package_id=evidence_id,
            redacted=redacted,
        )


class OpenAICompatibleAdapter:
    adapter_id = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.environ = environ
        self.recorder = _ArtifactRecorder(connection=connection, artifact_root=artifact_root, adapter_id=self.adapter_id)

    def _configuration(self) -> dict[str, str | None]:
        source = self.environ or os.environ
        runtime_configuration = runtime_provider_configuration("openai_compatible", environ=source)
        return {
            "baseUrl": (
                self.base_url
                or (runtime_configuration.value("baseUrl") if runtime_configuration else None)
                or source.get("OPENAI_COMPATIBLE_BASE_URL")
                or ""
            ).rstrip("/")
            or None,
            "apiKey": self.api_key or (runtime_configuration.value("apiKey") if runtime_configuration else None),
            "model": self.model or (runtime_configuration.value("model") if runtime_configuration else None),
            "enabled": source.get("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false").strip().lower(),
        }

    def _configuration_gap(self, configuration: dict[str, str | None]) -> str | None:
        missing = [
            name
            for key, name in (
                ("baseUrl", "base URL"),
                ("apiKey", "API key"),
                ("model", "model"),
            )
            if not configuration.get(key)
        ]
        if missing:
            return "OpenAI-compatible provider is missing required configuration: " + ", ".join(missing) + "."
        if configuration["enabled"] != "true":
            return "OpenAI-compatible provider execution is disabled by AIDO_ENABLE_REAL_PROVIDER_CALLS=false."
        return None

    def health_check(self) -> dict[str, Any]:
        configuration = self._configuration()
        gap = self._configuration_gap(configuration)
        if gap:
            status = "configuration_required" if "missing required configuration" in gap else "unavailable"
            return {"status": status, "available": False, "reason": gap}
        request = Request(
            f"{configuration['baseUrl']}/models",
            headers={"Authorization": f"Bearer {configuration['apiKey']}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=10) as response:
                json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return {
                "status": "unavailable",
                "available": False,
                "reason": str(redact_secrets(f"OpenAI-compatible health check failed: {error.__class__.__name__}")),
            }
        return {"status": "available", "available": True, "reason": "OpenAI-compatible /models responded."}

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started_at = utc_now()
        configuration = self._configuration()
        gap = self._configuration_gap(configuration)
        if gap:
            status = "configuration_required" if "missing required configuration" in gap else "unavailable"
            return _result(status=status, started_at=started_at, reason=gap)
        messages = request.input.get("messages")
        if not isinstance(messages, list) or not messages:
            return _result(
                status="blocked",
                started_at=started_at,
                reason="OpenAI-compatible execution requires input.messages.",
            )
        model = str(request.input.get("model") or configuration["model"])
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": request.input.get("temperature", 0.2),
                "stream": False,
            }
        ).encode("utf-8")
        http_request = Request(
            f"{configuration['baseUrl']}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {configuration['apiKey']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=_bounded_timeout(request)) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=str(redact_secrets(f"OpenAI-compatible execution failed: {error.__class__.__name__}")),
                redacted=True,
            )
        choices = raw.get("choices") if isinstance(raw, dict) else []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
        content = str((message or {}).get("content") or "")
        clean_content, redacted = _redact_text(content)
        output_id = self.recorder.record_output(
            request=request,
            name="openai-compatible-output.txt",
            content=clean_content,
        )
        evidence_id = self.recorder.create_evidence(
            request=request,
            status="completed",
            exit_code=0,
            reason=None,
            artifact_ids=[output_id] if output_id else [],
        )
        return _result(
            status="completed",
            started_at=started_at,
            exit_code=0,
            output_artifact_id=output_id,
            evidence_package_id=evidence_id,
            redacted=redacted,
        )


class RuntimeAdapterRegistry:
    def __init__(
        self,
        adapters: dict[str, RuntimeAdapter] | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.adapters: dict[str, RuntimeAdapter] = adapters or {
            "restricted_subprocess": RestrictedSubprocessAdapter(connection=connection, artifact_root=artifact_root),
            "codex": CliVersionAdapter(adapter_id="codex"),
            "codex_cli": CliVersionAdapter(adapter_id="codex_cli"),
            "claude": CliVersionAdapter(adapter_id="claude"),
            "claude_code_cli": CliVersionAdapter(adapter_id="claude_code_cli"),
            "openhands": CliVersionAdapter(adapter_id="openhands"),
            "swe-agent": CliVersionAdapter(adapter_id="swe-agent"),
            "swe_agent": CliVersionAdapter(adapter_id="swe_agent"),
            "ollama": OllamaAdapter(connection=connection, artifact_root=artifact_root, environ=environ),
            "openai_compatible": OpenAICompatibleAdapter(connection=connection, artifact_root=artifact_root, environ=environ),
        }

    def register(self, adapter_id: str, adapter: RuntimeAdapter) -> None:
        self.adapters[adapter_id] = adapter

    def execute(self, adapter_id: str, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started_at = utc_now()
        adapter = self.adapters.get(adapter_id)
        if adapter is None:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"Runtime adapter is not registered: {adapter_id}",
            )
        return adapter.execute(request)


class RuntimeAdapterBrokerAdapter:
    def __init__(
        self,
        *,
        adapter_id: str,
        connection: sqlite3.Connection,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.adapter_id = adapter_id
        self.registry = RuntimeAdapterRegistry(connection=connection, artifact_root=artifact_root, environ=environ)

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        request = RuntimeExecutionRequest.model_validate(
            {
                "projectId": policy_input["projectId"],
                "workflowRunId": tool_call.get("workflowRunId"),
                "workflowStepId": tool_call.get("workflowStepId"),
                "jobId": tool_call.get("jobId"),
                "agentRunId": tool_call.get("agentRunId"),
                "workspaceId": policy_input.get("workspaceId"),
                "workspacePath": policy_input.get("workspacePath"),
                "capability": tool_call.get("capability") or tool_call.get("operation") or "runtime_execution",
                "argv": tool_call.get("argv") or [],
                "input": tool_call.get("input") or {},
                "timeoutSeconds": tool_call.get("timeoutSeconds") or 30,
                "approvalGrantId": tool_call.get("approvalGrantId"),
                "metadata": tool_call.get("metadata") or {},
            }
        )
        result = self.registry.execute(self.adapter_id, request)
        payload = result.model_dump(by_alias=True)
        return {
            "executed": result.status == "completed",
            "blocked": result.status in {"blocked", "configuration_required", "unavailable"},
            "returnCode": result.exit_code,
            "reason": result.reason,
            "timedOut": result.status == "timed_out",
            **payload,
        }


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
    def __init__(self, *, connection: sqlite3.Connection, artifact_root: str | Path | None = None):
        self.connection = connection
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        workspace_value = str(policy_input.get("workspacePath") or "").strip()
        if not workspace_value:
            return {"executed": False, "blocked": True, "reason": "Workspace patch requires workspacePath."}
        workspace = Path(workspace_value).resolve(strict=False)
        patch_input = tool_call.get("input") or {}
        files = patch_input.get("files")
        if not isinstance(files, list) or not files:
            return {"executed": False, "blocked": True, "reason": "Workspace patch requires a non-empty files list."}
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
                return {"executed": False, "blocked": True, "reason": f"files[{index}].content must be a string."}
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


class UnavailableRuntimeAdapter:
    def __init__(self, adapter_id: str, reason: str):
        self.adapter_id = adapter_id
        self.reason = reason

    def execute(
        self,
        request: RuntimeExecutionRequest | None = None,
        *,
        tool_call: dict[str, Any] | None = None,
        policy_input: dict[str, Any] | None = None,
    ) -> RuntimeExecutionResult | dict[str, Any]:
        if request is not None:
            return _result(status="unavailable", started_at=utc_now(), reason=self.reason)
        return {
            "executed": False,
            "blocked": True,
            "adapter": self.adapter_id,
            "reason": self.reason,
        }
