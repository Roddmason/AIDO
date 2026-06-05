from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .cli_runtimes.base import RuntimeRequest
from .cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from .cli_runtimes.codex_cli import CodexCliRuntime
from .cli_runtimes.manual import ManualRuntime
from .cli_runtimes.openhands import OpenHandsRuntime
from .cli_runtimes.swe_agent import SweAgentRuntime


CLI_EXECUTABLE_TOKENS = {
    "codex_cli": ("codex",),
    "claude_code_cli": ("claude",),
    "openhands": ("openhands",),
    "swe_agent": ("swe", "agent"),
}


class RuntimeCommandUnavailableError(RuntimeError):
    pass


def issue_to_patch_prompt(*, title: str, issue_text: str) -> str:
    return (
        "Execute the issue_to_patch workflow in the current workspace only. "
        "Do not commit, push, install dependencies, or modify files outside the workspace.\n\n"
        f"Title: {title}\n\nIssue:\n{issue_text}\n"
    )


def developer_agent_prompt(*, instruction: str, qa_commands: list[list[str]]) -> str:
    qa_text = "\n".join(" ".join(command) for command in qa_commands) if qa_commands else "No QA commands were provided."
    return (
        "You are DeveloperAgent executing real implementation work in the current workspace only.\n"
        "Rules:\n"
        "- Modify only files inside the allocated workspace.\n"
        "- Do not read, write, print, commit, push, or exfiltrate secrets or credentials.\n"
        "- Do not skip applicable tests; if QA commands are provided, preserve them as required verification.\n"
        "- Do not modify the source repository root outside this workspace.\n"
        "- Produce a concise structured summary with changed files, tests run, blockers, and residual risks.\n\n"
        f"Instruction:\n{instruction}\n\nRequired QA commands:\n{qa_text}\n"
    )


def _explicit_issue_to_patch_argv(runtime: dict[str, Any]) -> list[str] | None:
    argv = runtime.get("issueToPatchArgv")
    if argv is None:
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise RuntimeCommandUnavailableError("Runtime issueToPatchArgv must be a non-empty structured argv list.")
    return list(argv)


def _explicit_developer_agent_argv(runtime: dict[str, Any]) -> list[str] | None:
    argv = runtime.get("developerAgentArgv")
    if argv is None:
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise RuntimeCommandUnavailableError("Runtime developerAgentArgv must be a non-empty structured argv list.")
    return list(argv)


def build_issue_to_patch_argv(
    *,
    runtime: dict[str, Any],
    workspace_id: str,
    workspace_path: str,
    title: str,
    issue_text: str,
    workflow_run_id: str,
    workflow_step_id: str | None,
    agent_id: str,
    connection: sqlite3.Connection,
) -> list[str]:
    explicit = _explicit_issue_to_patch_argv(runtime)
    if explicit is not None:
        return explicit
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in CLI_EXECUTABLE_TOKENS:
        raise RuntimeCommandUnavailableError(
            "Runtime provider does not expose a workspace-bound issue_to_patch CLI executor."
        )
    executable = str(runtime.get("detectedCommand") or "").strip()
    if not executable:
        raise RuntimeCommandUnavailableError("Runtime status did not provide a detected executable command.")
    executable_name = Path(executable).name.lower()
    required_tokens = CLI_EXECUTABLE_TOKENS.get(runtime_id, ())
    if required_tokens and not all(token in executable_name for token in required_tokens):
        raise RuntimeCommandUnavailableError("Runtime detected executable does not match the declared runtime command.")
    cli_runtime = runtime_for(runtime_id, connection=connection, executable=executable)
    request = RuntimeRequest.model_validate(
        {
            "runtime": runtime_id,
            "workspaceId": workspace_id,
            "workspacePath": workspace_path,
            "prompt": issue_to_patch_prompt(title=title, issue_text=issue_text),
            "envPolicy": {
                "permissionProfile": "dev_safe",
                "network": False,
                "secrets": False,
            },
            "extraArgs": [],
            "role": "implementer",
            "agentId": agent_id,
            "workflowRunId": workflow_run_id,
            "workflowStepId": workflow_step_id,
        }
    )
    try:
        return cli_runtime.build_command(request)
    except ValueError as error:
        raise RuntimeCommandUnavailableError(str(error)) from error


def build_developer_agent_argv(
    *,
    runtime: dict[str, Any],
    workspace_id: str,
    workspace_path: str,
    instruction: str,
    qa_commands: list[list[str]],
    agent_id: str,
    connection: sqlite3.Connection,
) -> list[str]:
    explicit = _explicit_developer_agent_argv(runtime)
    if explicit is not None:
        return explicit
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in {"codex_cli", "claude_code_cli"}:
        raise RuntimeCommandUnavailableError("Runtime provider does not expose a DeveloperAgent CLI executor.")
    executable = str(runtime.get("detectedCommand") or "").strip()
    if not executable:
        raise RuntimeCommandUnavailableError("Runtime status did not provide a detected executable command.")
    executable_name = Path(executable).name.lower()
    required_tokens = CLI_EXECUTABLE_TOKENS.get(runtime_id, ())
    if required_tokens and not all(token in executable_name for token in required_tokens):
        raise RuntimeCommandUnavailableError("Runtime detected executable does not match the declared runtime command.")
    cli_runtime = runtime_for(runtime_id, connection=connection, executable=executable)
    request = RuntimeRequest.model_validate(
        {
            "runtime": runtime_id,
            "workspaceId": workspace_id,
            "workspacePath": workspace_path,
            "prompt": developer_agent_prompt(instruction=instruction, qa_commands=qa_commands),
            "envPolicy": {
                "permissionProfile": "dev_safe",
                "network": False,
                "secrets": False,
            },
            "extraArgs": [],
            "role": "developer",
            "agentId": agent_id,
        }
    )
    try:
        return cli_runtime.build_command(request)
    except ValueError as error:
        raise RuntimeCommandUnavailableError(str(error)) from error


def runtime_for(
    runtime_id: str,
    *,
    connection: sqlite3.Connection | None = None,
    executable: str | None = None,
):
    runtimes = {
        "codex_cli": CodexCliRuntime(executable=executable, connection=connection),
        "claude_code_cli": ClaudeCodeCliRuntime(executable=executable, connection=connection),
        "openhands": OpenHandsRuntime(executable=executable, connection=connection),
        "swe_agent": SweAgentRuntime(executable=executable, connection=connection),
        "manual": ManualRuntime(connection=connection),
    }
    if runtime_id not in runtimes:
        raise KeyError(f"Runtime not found: {runtime_id}")
    return runtimes[runtime_id]


class RuntimeRegistry:
    def list_runtimes(self) -> list[dict[str, Any]]:
        result = []
        for runtime_id in ["codex_cli", "claude_code_cli", "openhands", "swe_agent", "manual"]:
            runtime = runtime_for(runtime_id)
            detection = runtime.detect().model_dump(by_alias=True)
            result.append({"id": runtime_id, "runtime": runtime_id, **detection})
        return result

    def detect(self, runtime_id: str, *, executable: str | None = None) -> dict[str, Any]:
        return runtime_for(runtime_id, executable=executable).detect().model_dump(by_alias=True)

    def health_check(self, runtime_id: str, *, executable: str | None = None) -> dict[str, Any]:
        return runtime_for(runtime_id, executable=executable).health_check().model_dump(by_alias=True)
