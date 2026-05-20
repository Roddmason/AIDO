from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.agents.providers.base import UsageRecord
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox


DANGEROUS_CLI_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--danger-full-access",
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
    mock: bool = False


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

    def __init__(self, *, executable: str, mock: bool = False):
        self.executable = executable
        self.mock = mock

    def _which(self) -> str | None:
        from shutil import which

        return which(self.executable)

    def _version(self, executable: str) -> str | None:
        return None

    def _validate_workspace(self, request: RuntimeRequest) -> Path:
        if not request.workspace_id:
            raise ValueError("workspace_id is required for CLI runtimes")
        workspace = Path(request.workspace_path).resolve(strict=False)
        if not workspace.exists() and not self.mock:
            raise ValueError("workspace_path does not exist")
        return workspace

    def _validate_safe_args(self, request: RuntimeRequest) -> None:
        lowered = {str(arg).lower() for arg in request.extra_args}
        if lowered & DANGEROUS_CLI_FLAGS:
            raise ValueError("dangerous CLI flags are blocked by runtime policy")

    def detect(self) -> RuntimeDetection:
        if self.mock:
            return RuntimeDetection(runtime=self.runtime_id, status="installed", executable=self.executable, version="mock", message=f"{self.display_name} mock runtime")
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
        command = self.build_command(request)
        if self.mock or request.mock:
            result = RuntimeResult(runtime=self.runtime_id, status="completed", command=command, stdout="mock runtime completed", returnCode=0)
            return result.model_copy(update={"usage": self.parse_usage(result)})
        if os.environ.get("AIDO_ENABLE_CLI_RUNTIMES", "false").lower() != "true":
            return RuntimeResult(runtime=self.runtime_id, status="blocked", command=command, error="CLI runtimes are disabled")
        workspace = self._validate_workspace(request)
        result = RestrictedSubprocessSandbox().execute(
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
        return runtime_result.model_copy(update={"usage": self.parse_usage(runtime_result)})

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
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
    if not isinstance(usage, dict):
        return None
    token_keys = {
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "tool_tokens",
        "total_tokens",
    }
    if not (set(usage) & token_keys):
        nested_usage = payload.get("token_usage")
        if isinstance(nested_usage, dict):
            usage = nested_usage
        else:
            return None
    input_tokens = _int_token(usage.get("prompt_tokens") or usage.get("input_tokens"))
    output_tokens = _int_token(usage.get("completion_tokens") or usage.get("output_tokens"))
    cached_input_tokens = _int_token(usage.get("cached_input_tokens") or (usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
    reasoning_tokens = _int_token(usage.get("reasoning_tokens") or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    tool_tokens = _int_token(usage.get("tool_tokens"))
    total_tokens = _int_token(usage.get("total_tokens")) or (
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
