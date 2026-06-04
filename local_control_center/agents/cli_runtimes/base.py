from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.agents.providers.base import UsageRecord
from local_control_center.agents.cli_sessions import CliSessionStore
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.security_policy import sandbox as subprocess_sandbox


DANGEROUS_CLI_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--danger-full-access",
    "--no-sandbox",
    "--privileged",
    "--mount",
    "--volume",
    "--network=host",
    "danger-full-access",
}


class RuntimeDetection(BaseModel):
    runtime: str
    status: str
    executable: str | None = None
    version: str | None = None
    message: str = ""


class RuntimeHealth(BaseModel):
    runtime: str
    status: str
    message: str = ""


class RuntimeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    runtime: str
    workspace_id: str = Field(alias="workspaceId")
    workspace_path: str = Field(alias="workspacePath")
    prompt: str
    profile: str | None = None
    model: str | None = None
    effort: str | None = None
    env_policy: dict[str, Any] = Field(default_factory=dict, alias="envPolicy")
    extra_args: list[str] = Field(default_factory=list, alias="extraArgs")
    role: str | None = None
    agent_id: str | None = Field(default=None, alias="agentId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")


class RuntimeResult(BaseModel):
    runtime: str
    status: str
    command: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = Field(default=None, alias="returnCode")
    error: str | None = None
    usage: UsageRecord | None = None


class CliRuntime(ABC):
    runtime_id: str
    display_name: str

    def __init__(self, *, executable: str, connection: sqlite3.Connection | None = None):
        self.executable = executable
        self.connection = connection

    def _which(self) -> str | None:
        from shutil import which

        return which(self.executable)

    def _version(self, executable: str) -> str | None:
        try:
            result = subprocess.run(
                [executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        output = (result.stdout or result.stderr).strip()
        if not output:
            return None
        return output.splitlines()[0][:200]

    def _validate_workspace(self, request: RuntimeRequest) -> Path:
        if not request.workspace_id:
            raise ValueError("workspace_id is required for CLI runtimes")
        workspace = Path(request.workspace_path).resolve(strict=False)
        if not workspace.exists():
            raise ValueError("workspace_path does not exist")
        if self.connection is not None:
            row = self.connection.execute("SELECT path FROM workspaces WHERE id = ?", (request.workspace_id,)).fetchone()
            if not row:
                raise ValueError("workspace must be registered before real CLI execution")
            registered = Path(row["path"]).resolve(strict=False)
            if registered != workspace:
                raise ValueError("workspace_path does not match registered workspace")
        return workspace

    def _validate_safe_args(self, request: RuntimeRequest) -> None:
        lowered = {str(arg).lower() for arg in request.extra_args}
        if lowered & DANGEROUS_CLI_FLAGS:
            raise ValueError("dangerous CLI flags are blocked by runtime policy")

    def detect(self) -> RuntimeDetection:
        executable = self._which()
        if not executable:
            return RuntimeDetection(runtime=self.runtime_id, status="not_installed", message=f"{self.display_name} not detected")
        return RuntimeDetection(runtime=self.runtime_id, status="installed", executable=executable, version=self._version(executable), message=f"{self.display_name} detected")

    def health_check(self) -> RuntimeHealth:
        detection = self.detect()
        status = "healthy" if detection.status == "installed" else detection.status
        return RuntimeHealth(runtime=self.runtime_id, status=status, message=detection.message)

    @abstractmethod
    def build_command(self, request: RuntimeRequest) -> list[str]:
        raise NotImplementedError

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        try:
            command = self.build_command(request)
        except ValueError as error:
            blocked = RuntimeResult(
                runtime=self.runtime_id,
                status="blocked",
                command=self._blocked_command(request),
                error=str(error),
            )
            validation_request = request.model_copy(
                update={
                    "env_policy": {
                        **request.env_policy,
                        "runtimeValidation": {
                            "decision": "deny",
                            "reason": str(error),
                        },
                    }
                }
            )
            self._record_result(validation_request, blocked)
            return blocked
        workspace = Path(request.workspace_path).resolve(strict=False)
        if os.environ.get("AIDO_ENABLE_CLI_RUNTIMES", "false").lower() != "true":
            blocked = RuntimeResult(runtime=self.runtime_id, status="blocked", command=command, error="CLI runtimes are disabled")
            gated_request = request.model_copy(
                update={
                    "env_policy": {
                        **request.env_policy,
                        "runtimeGate": {
                            "decision": "deny",
                            "reason": "AIDO_ENABLE_CLI_RUNTIMES=false",
                        },
                    }
                }
            )
            self._record_result(gated_request, blocked)
            return blocked
        policy = self._evaluate_policy(request, command, workspace)
        policy_request = request.model_copy(update={"env_policy": {**request.env_policy, "policyResult": policy}})
        if policy.get("decision") != "allow":
            blocked = RuntimeResult(
                runtime=self.runtime_id,
                status="blocked",
                command=command,
                error=str(policy.get("reason") or "CLI runtime blocked by security policy"),
            )
            self._record_result(policy_request, blocked)
            return blocked
        result = subprocess_sandbox.RestrictedSubprocessSandbox().execute(
            argv=command,
            cwd=str(workspace),
            workspace_path=str(workspace),
            timeout_seconds=900,
        )
        status = "completed" if result.get("returnCode") == 0 else "failed"
        runtime_result = RuntimeResult(
            runtime=self.runtime_id,
            status=status,
            command=command,
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            returnCode=result.get("returnCode"),
            error=result.get("reason"),
        )
        completed = runtime_result.model_copy(update={"usage": self.parse_usage(runtime_result)})
        self._record_result(policy_request, completed)
        return completed

    def _blocked_command(self, request: RuntimeRequest) -> list[str]:
        command = [self.executable, "<blocked>"]
        command.extend(str(arg) for arg in request.extra_args)
        if request.prompt:
            command.append("<prompt>")
        return command

    def _evaluate_policy(
        self,
        request: RuntimeRequest,
        command: list[str],
        workspace: Path,
    ) -> dict[str, Any]:
        return evaluate_action(
            {
                "tool": "shell",
                "command": " ".join(command),
                "path": str(workspace),
                "workspacePath": str(workspace),
                "role": request.role,
                "permissionProfile": request.env_policy.get("permissionProfile", "dev_safe"),
                "networkRequired": bool(request.env_policy.get("network", False)),
                "secretsRequired": bool(request.env_policy.get("secrets") or request.env_policy.get("secretRefs")),
            }
        )

    def _record_result(self, request: RuntimeRequest, result: RuntimeResult) -> None:
        if self.connection is None:
            return
        CliSessionStore(self.connection).record_result(
            runtime=self.runtime_id,
            executable=self.executable,
            workspace_id=request.workspace_id,
            workflow_run_id=request.workflow_run_id,
            workflow_step_id=request.workflow_step_id,
            agent_id=request.agent_id,
            command=result.command,
            env_policy=request.env_policy,
            status=result.status,
            model=request.model,
            role=request.role,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.error,
            usage=result.usage,
        )

    def parse_usage(self, result: RuntimeResult) -> UsageRecord | None:
        if result.usage is not None:
            return result.usage
        for payload in _json_payloads_from_text(result.stdout):
            usage = _usage_from_payload(payload)
            if usage is not None:
                return usage
        for payload in _json_payloads_from_text(result.stderr):
            usage = _usage_from_payload(payload)
            if usage is not None:
                return usage
        return None


def _json_payloads_from_text(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    stripped = text.strip()
    if not stripped:
        return payloads
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            payloads.append(parsed)
            return payloads
        if isinstance(parsed, list):
            payloads.extend(item for item in parsed if isinstance(item, dict))
            return payloads
    except json.JSONDecodeError:
        pass
    for line in stripped.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _usage_from_payload(payload: dict[str, Any]) -> UsageRecord | None:
    for usage in _usage_candidates(payload):
        parsed = _usage_record_from_mapping(usage)
        if parsed is not None:
            return parsed
    return None


def _usage_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("usage", "token_usage", "tokens", "llm_metrics"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        candidates.append(message["usage"])
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        candidates.append(result["usage"])
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        for key in ("usage", "token_usage", "tokens", "llm_metrics"):
            value = metrics.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    candidates.append(payload)
    return candidates


def _usage_record_from_mapping(usage: dict[str, Any]) -> UsageRecord | None:
    token_keys = {
        "prompt_tokens",
        "input_tokens",
        "input",
        "completion_tokens",
        "output_tokens",
        "output",
        "cached_input_tokens",
        "cached_input",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "reasoning_tokens",
        "reasoning",
        "tool_tokens",
        "tool",
        "total_tokens",
        "total",
    }
    if not (set(usage) & token_keys):
        return None
    input_tokens = _int_token(usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("input"))
    output_tokens = _int_token(usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("output"))
    cached_input_tokens = _int_token(
        usage.get("cached_input_tokens")
        or usage.get("cached_input")
        or usage.get("cache_read_input_tokens")
        or usage.get("cache_creation_input_tokens")
        or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    )
    reasoning_tokens = _int_token(
        usage.get("reasoning_tokens")
        or usage.get("reasoning")
        or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    )
    tool_tokens = _int_token(usage.get("tool_tokens") or usage.get("tool"))
    total_tokens = _int_token(usage.get("total_tokens") or usage.get("total")) or (
        input_tokens + cached_input_tokens + output_tokens + reasoning_tokens + tool_tokens
    )
    if total_tokens == 0:
        return None
    return UsageRecord(
        inputTokens=input_tokens,
        cachedInputTokens=cached_input_tokens,
        outputTokens=output_tokens,
        reasoningTokens=reasoning_tokens,
        toolTokens=tool_tokens,
        totalTokens=total_tokens,
        rawUsage=redact_secrets({"usage_source": "cli_output", **usage}),
    )


def _int_token(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
