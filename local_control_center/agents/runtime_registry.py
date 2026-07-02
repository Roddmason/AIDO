"""Builds workspace-bound CLI commands and detects the available CLI runtimes.

Composes the safety-prefixed prompts and structured argv for issue-to-patch and
DeveloperAgent runs (explicit configured argv first, otherwise the runtime's own
builder), and exposes detection/health checks per CLI runtime. Every generated command
stays inside the allocated workspace and never commits, pushes, or touches secrets.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import threading
import time
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
    """Raised when no safe workspace-bound command can be built for the requested runtime."""


def issue_to_patch_prompt(*, title: str, issue_text: str) -> str:
    """Build the workspace-scoped prompt that constrains an issue-to-patch run."""
    return (
        "Execute the issue_to_patch workflow in the current workspace only. "
        "Do not commit, push, install dependencies, or modify files outside the workspace.\n\n"
        f"Title: {title}\n\nIssue:\n{issue_text}\n"
    )


def developer_agent_prompt(*, instruction: str, qa_commands: list[list[str]]) -> str:
    """Build the DeveloperAgent prompt with workspace, secret, and QA-preservation rules."""
    qa_text = (
        "\n".join(" ".join(command) for command in qa_commands)
        if qa_commands
        else "No QA commands were provided."
    )
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


def _expand_issue_to_patch_argv(
    argv: list[str],
    *,
    workspace_path: str,
    title: str,
    issue_text: str,
) -> list[str]:
    prompt = issue_to_patch_prompt(title=title, issue_text=issue_text)
    replacements = {
        "{workspace}": workspace_path,
        "{workspace_path}": workspace_path,
        "{title}": title,
        "{issue_text}": issue_text,
        "{prompt}": prompt,
    }
    expanded: list[str] = []
    for item in argv:
        value = item
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        expanded.append(value)
    return expanded


def _explicit_issue_to_patch_argv(
    runtime: dict[str, Any],
    *,
    runtime_id: str,
    workspace_path: str,
    title: str,
    issue_text: str,
) -> list[str] | None:
    argv = runtime.get("issueToPatchArgv")
    if argv is None:
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise RuntimeCommandUnavailableError(
            "Runtime issueToPatchArgv must be a non-empty structured argv list."
        )
    if runtime_id in {"openhands", "swe_agent"}:
        executable_name = Path(argv[0]).name.lower()
        required_tokens = CLI_EXECUTABLE_TOKENS.get(runtime_id, ())
        if required_tokens and not all(token in executable_name for token in required_tokens):
            display_name = "OpenHands" if runtime_id == "openhands" else "SWE-agent"
            raise RuntimeCommandUnavailableError(
                f"{display_name} issueToPatchArgv must start with its own CLI executable."
            )
    return _expand_issue_to_patch_argv(
        list(argv),
        workspace_path=workspace_path,
        title=title,
        issue_text=issue_text,
    )


def _explicit_developer_agent_argv(runtime: dict[str, Any]) -> list[str] | None:
    argv = runtime.get("developerAgentArgv")
    if argv is None:
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise RuntimeCommandUnavailableError(
            "Runtime developerAgentArgv must be a non-empty structured argv list."
        )
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
    """Build the structured argv for an issue-to-patch run on the given runtime.

    Prefers the runtime's explicit `issueToPatchArgv`; otherwise requires a detected
    executable that matches the runtime's tokens and delegates to its command builder.

    Raises:
        RuntimeCommandUnavailableError: if the runtime cannot produce a safe command.
    """
    explicit = _explicit_issue_to_patch_argv(
        runtime,
        runtime_id=str(runtime.get("id") or ""),
        workspace_path=workspace_path,
        title=title,
        issue_text=issue_text,
    )
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
        raise RuntimeCommandUnavailableError(
            "Runtime detected executable does not match the declared runtime command."
        )
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
    """Build the structured argv for a DeveloperAgent run (Codex or Claude Code CLI only).

    Prefers the runtime's explicit `developerAgentArgv`; otherwise validates the detected
    executable and delegates to the runtime's command builder.

    Raises:
        RuntimeCommandUnavailableError: if the runtime cannot produce a safe command.
    """
    explicit = _explicit_developer_agent_argv(runtime)
    if explicit is not None:
        return explicit
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in {"codex_cli", "claude_code_cli"}:
        raise RuntimeCommandUnavailableError(
            "Runtime provider does not expose a DeveloperAgent CLI executor."
        )
    executable = str(runtime.get("detectedCommand") or "").strip()
    if not executable:
        raise RuntimeCommandUnavailableError("Runtime status did not provide a detected executable command.")
    executable_name = Path(executable).name.lower()
    required_tokens = CLI_EXECUTABLE_TOKENS.get(runtime_id, ())
    if required_tokens and not all(token in executable_name for token in required_tokens):
        raise RuntimeCommandUnavailableError(
            "Runtime detected executable does not match the declared runtime command."
        )
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


def _explicit_product_owner_agent_argv(runtime: dict[str, Any]) -> list[str] | None:
    argv = runtime.get("productOwnerAgentArgv")
    if argv is None:
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise RuntimeCommandUnavailableError(
            "Runtime productOwnerAgentArgv must be a non-empty structured argv list."
        )
    return list(argv)


def build_product_owner_agent_argv(
    *,
    runtime: dict[str, Any],
    workspace_id: str,
    workspace_path: str,
    prompt: str,
    agent_id: str,
    connection: sqlite3.Connection,
) -> list[str]:
    """Build the structured argv for a ProductOwnerAgent run (Codex or Claude Code CLI only).

    Prefers the runtime's explicit `productOwnerAgentArgv`; otherwise validates the detected executable
    and delegates to the runtime's command builder with the supplied analysis prompt.

    Raises:
        RuntimeCommandUnavailableError: if the runtime cannot produce a safe command.
    """
    explicit = _explicit_product_owner_agent_argv(runtime)
    if explicit is not None:
        return explicit
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in {"codex_cli", "claude_code_cli"}:
        raise RuntimeCommandUnavailableError(
            "Runtime provider does not expose a ProductOwnerAgent CLI executor."
        )
    executable = str(runtime.get("detectedCommand") or "").strip()
    if not executable:
        raise RuntimeCommandUnavailableError("Runtime status did not provide a detected executable command.")
    executable_name = Path(executable).name.lower()
    required_tokens = CLI_EXECUTABLE_TOKENS.get(runtime_id, ())
    if required_tokens and not all(token in executable_name for token in required_tokens):
        raise RuntimeCommandUnavailableError(
            "Runtime detected executable does not match the declared runtime command."
        )
    cli_runtime = runtime_for(runtime_id, connection=connection, executable=executable)
    request = RuntimeRequest.model_validate(
        {
            "runtime": runtime_id,
            "workspaceId": workspace_id,
            "workspacePath": workspace_path,
            "prompt": prompt,
            "envPolicy": {"permissionProfile": "plan", "network": False, "secrets": False},
            "extraArgs": [],
            "role": "product_owner",
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
    """Instantiate the CLI runtime implementation for a runtime id.

    Raises:
        KeyError: if `runtime_id` is not a known runtime.
    """
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


# La detección de un runtime CLI ejecuta `<binario> --version` por subprocess (~4 s en frío para
# el set completo en Windows) y su resultado —presencia y versión de un binario— es estable en
# ventanas cortas. Se cachea por `(runtime_id, executable)` con TTL para que el polling del shell
# (~cada 5 s) no repita el probe en cada request ni lo retenga bajo el lock global que serializa el
# acceso al runtime del control-plane. El costo real vive aquí; el rollup de estado se deriva de la BD.
_DETECTION_CACHE_TTL_SECONDS = 30.0
_detection_cache: dict[tuple[str, str | None], tuple[float, dict[str, Any]]] = {}
_detection_cache_lock = threading.Lock()


def reset_detection_cache() -> None:
    """Vacía el caché de detección de runtimes; usar entre tests para evitar fugas de estado global."""
    with _detection_cache_lock:
        _detection_cache.clear()


class RuntimeRegistry:
    """Detection facade over the known CLI runtimes."""

    def list_runtimes(self) -> list[dict[str, Any]]:
        """Detect every known runtime and return its detection payload."""
        result = []
        for runtime_id in ["codex_cli", "claude_code_cli", "openhands", "swe_agent", "manual"]:
            runtime = runtime_for(runtime_id)
            detection = runtime.detect().model_dump(by_alias=True)
            result.append({"id": runtime_id, "runtime": runtime_id, **detection})
        return result

    def detect(self, runtime_id: str, *, executable: str | None = None) -> dict[str, Any]:
        """Detect a single runtime, optionally probing a specific executable path.

        Sirve la detección cacheada dentro del TTL; ante fallo de caché ejecuta el probe FUERA del
        candado del caché (no serializa los probes entre sí) y guarda el resultado.
        """
        key = (runtime_id, executable)
        now = time.monotonic()
        with _detection_cache_lock:
            cached = _detection_cache.get(key)
            if cached is not None and now - cached[0] < _DETECTION_CACHE_TTL_SECONDS:
                return dict(cached[1])
        detection = runtime_for(runtime_id, executable=executable).detect().model_dump(by_alias=True)
        with _detection_cache_lock:
            _detection_cache[key] = (time.monotonic(), detection)
        return dict(detection)

    def health_check(self, runtime_id: str, *, executable: str | None = None) -> dict[str, Any]:
        """Run a runtime's safe version/health check and return its result."""
        return runtime_for(runtime_id, executable=executable).health_check().model_dump(by_alias=True)
