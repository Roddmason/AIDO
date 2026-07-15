"""Builds workspace-bound CLI commands and detects the available CLI runtimes.

Composes the safety-prefixed prompts and structured argv for issue-to-patch and
DeveloperAgent runs (explicit configured argv first, otherwise the runtime's own
builder), and exposes detection/health checks per CLI runtime. Every generated command
stays inside the allocated workspace and never commits, pushes, or touches secrets.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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

PRODUCT_OWNER_SUPPORTED_CODEX_VERSIONS = frozenset({"codex-cli 0.142.2"})
PRODUCT_OWNER_CODEX_EXTRA_ARGS = [
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--strict-config",
    "--config",
    "tools.web_search=false",
    "--config",
    "mcp_servers={}",
    "--config",
    "skills.config=[]",
    "--config",
    "skills.include_instructions=false",
    "--config",
    "project_doc_max_bytes=0",
    "--config",
    "project_root_markers=[]",
    "--config",
    'shell_environment_policy.inherit="none"',
    "--config",
    "shell_environment_policy.experimental_use_profile=false",
    "--config",
    "allow_login_shell=false",
    "--skip-git-repo-check",
    "--disable",
    "shell_tool",
    "--disable",
    "unified_exec",
    "--disable",
    "apps",
    "--disable",
    "browser_use",
    "--disable",
    "browser_use_external",
    "--disable",
    "browser_use_full_cdp_access",
    "--disable",
    "computer_use",
    "--disable",
    "image_generation",
    "--disable",
    "in_app_browser",
    "--disable",
    "goals",
    "--disable",
    "hooks",
    "--disable",
    "memories",
    "--disable",
    "multi_agent",
    "--disable",
    "plugin_sharing",
    "--disable",
    "plugins",
    "--disable",
    "shell_snapshot",
    "--disable",
    "skill_mcp_dependency_install",
    "--disable",
    "tool_call_mcp_elicitation",
    "--disable",
    "tool_suggest",
    "--disable",
    "workspace_dependencies",
]
PRODUCT_OWNER_CLAUDE_EXTRA_ARGS = [
    "--safe-mode",
    "--no-session-persistence",
    "--tools=",
]

PRODUCT_OWNER_CODEX_ENVIRONMENT_KEYS = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)
PRODUCT_OWNER_CODEX_ALLOWED_ENVIRONMENT_KEYS = PRODUCT_OWNER_CODEX_ENVIRONMENT_KEYS | {
    "CODEX_HOME",
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
}


class RuntimeCommandUnavailableError(RuntimeError):
    """Raised when no safe workspace-bound command can be built for the requested runtime."""


def _codex_source_home() -> Path:
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    return (
        Path(configured).expanduser().resolve(strict=False)
        if configured
        else (Path.home() / ".codex").resolve(strict=False)
    )


def _product_owner_codex_home_root() -> Path:
    base = str(os.environ.get("LOCALAPPDATA") or "").strip()
    root = Path(base) if base else Path(tempfile.gettempdir())
    return (root / "AIDO" / "product-owner-codex-homes").resolve(strict=False)


def _strict_descendant(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


def _product_owner_codex_environment(runtime_home: Path, *, native_auth_copied: bool) -> dict[str, str]:
    environment = {
        key: value
        for key in PRODUCT_OWNER_CODEX_ENVIRONMENT_KEYS
        if (value := os.environ.get(key)) is not None
    }
    environment["CODEX_HOME"] = str(runtime_home)
    if not native_auth_copied:
        for key in ("OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
    return environment


def validate_product_owner_codex_environment(
    environment: Any,
    *,
    workspace_path: str,
) -> str | None:
    """Validate the trusted, non-persisted environment for a ProductOwner Codex process."""
    error = "ProductOwnerAgent Codex runtime requires a controlled, minimal subprocess environment."
    if not isinstance(environment, dict) or not environment:
        return error
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        return error
    if not set(environment).issubset(PRODUCT_OWNER_CODEX_ALLOWED_ENVIRONMENT_KEYS):
        return error

    codex_home_value = environment.get("CODEX_HOME")
    if not codex_home_value:
        return error
    codex_home = Path(codex_home_value).resolve(strict=False)
    controlled_root = _product_owner_codex_home_root()
    if not _strict_descendant(codex_home, controlled_root):
        return error
    if not codex_home.exists() or not codex_home.is_dir():
        return error

    workspace = Path(workspace_path).resolve(strict=False)
    if codex_home == workspace or codex_home in workspace.parents or workspace in codex_home.parents:
        return error
    if any((codex_home / entry).exists() for entry in ("AGENTS.md", "config.toml", "skills", "plugins")):
        return error
    return None


@contextmanager
def isolated_product_owner_codex_environment() -> Iterator[dict[str, str]]:
    """Yield a minimal Codex environment without operator AGENTS, skills, plugins, or config.

    Native authentication is copied into the private per-run ``CODEX_HOME`` so Codex cannot mutate
    the operator's credential file through a shared inode. The copy is mode-restricted and removed
    with the runtime home after execution. Failure to create this boundary fails closed.
    """
    controlled_root = _product_owner_codex_home_root()
    runtime_home = (controlled_root / str(uuid.uuid4())).resolve(strict=False)
    if not _strict_descendant(runtime_home, controlled_root):
        raise RuntimeCommandUnavailableError(
            "ProductOwnerAgent Codex runtime home escaped its controlled root."
        )
    runtime_home.mkdir(parents=True, exist_ok=False)
    native_auth_copied = False
    try:
        source_auth = (_codex_source_home() / "auth.json").resolve(strict=False)
        if source_auth.exists() and source_auth.is_file():
            target_auth = runtime_home / "auth.json"
            try:
                shutil.copyfile(source_auth, target_auth)
                target_auth.chmod(0o600)
            except OSError as error:
                raise RuntimeCommandUnavailableError(
                    "ProductOwnerAgent could not isolate the Codex native authentication file."
                ) from error
            native_auth_copied = True
        yield _product_owner_codex_environment(
            runtime_home,
            native_auth_copied=native_auth_copied,
        )
    finally:
        resolved_home = runtime_home.resolve(strict=False)
        if _strict_descendant(resolved_home, controlled_root) and resolved_home.exists():
            shutil.rmtree(resolved_home)


def issue_to_patch_prompt(*, title: str, issue_text: str) -> str:
    """Build the workspace-scoped prompt that constrains an issue-to-patch run."""
    return (
        "Execute the issue_to_patch workflow in the current workspace only. "
        "Do not commit, push, install dependencies, or modify files outside the workspace.\n\n"
        f"Title: {title}\n\nIssue:\n{issue_text}\n"
    )


def developer_agent_prompt(
    *,
    instruction: str,
    qa_commands: list[list[str]],
    story_specs: str | None = None,
) -> str:
    """Build the DeveloperAgent prompt with workspace, secret, and QA-preservation rules.

    When ``story_specs`` is provided, the rendered user-story spec (epic, story,
    acceptance criteria, and role responsibilities) is included as the acceptance
    source of truth; without it the prompt is byte-identical to the legacy form.
    """
    qa_text = (
        "\n".join(" ".join(command) for command in qa_commands)
        if qa_commands
        else "No QA commands were provided."
    )
    spec_block = (
        "User story spec (source of truth for acceptance):\n" + story_specs + "\n\n" if story_specs else ""
    )
    return (
        "You are DeveloperAgent executing real implementation work in the current workspace only.\n"
        "Rules:\n"
        "- Modify only files inside the allocated workspace.\n"
        "- Do not read, write, print, commit, push, or exfiltrate secrets or credentials.\n"
        "- Do not skip applicable tests; if QA commands are provided, preserve them as required verification.\n"
        "- Do not modify the source repository root outside this workspace.\n"
        "- Produce a concise structured summary with changed files, tests run, blockers, and residual risks.\n\n"
        f"{spec_block}"
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
    story_specs: str | None = None,
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
            "prompt": developer_agent_prompt(
                instruction=instruction, qa_commands=qa_commands, story_specs=story_specs
            ),
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


def _matches_runtime_executable(runtime_id: str, executable: str) -> bool:
    executable_name = Path(executable).name.lower()
    required_tokens = CLI_EXECUTABLE_TOKENS.get(runtime_id, ())
    return bool(required_tokens) and all(token in executable_name for token in required_tokens)


def _canonical_product_owner_argv_error() -> str:
    return "ProductOwnerAgent runtime argv must match the canonical read-only command contract."


def validate_product_owner_runtime_argv(
    *,
    runtime_id: str,
    argv: Any,
    workspace_path: str,
) -> str | None:
    """Validate the exact tool-isolated, read-only CLI shape used by ProductOwnerAgent.

    This is intentionally stricter than the generic restricted-subprocess allowlist. It binds the
    executable to the declared runtime, the CLI workspace to the allocated workspace, and rejects
    duplicate/unknown flags instead of trying to infer whether they override an earlier safe flag.
    """
    error = _canonical_product_owner_argv_error()
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        return error
    if runtime_id not in {"codex_cli", "claude_code_cli"}:
        return error
    if not _matches_runtime_executable(runtime_id, argv[0]):
        return error

    workspace = str(Path(workspace_path).resolve(strict=False))
    if runtime_id == "codex_cli":
        required_prefix = [
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            "read-only",
            "--cd",
            workspace,
        ]
        required_extra_args = PRODUCT_OWNER_CODEX_EXTRA_ARGS
    else:
        required_prefix = [
            "--print",
            "--permission-mode",
            "plan",
            "--add-dir",
            workspace,
        ]
        required_extra_args = PRODUCT_OWNER_CLAUDE_EXTRA_ARGS

    remaining = argv[1:]
    if remaining[: len(required_prefix)] != required_prefix:
        return error
    remaining = remaining[len(required_prefix) :]
    if remaining[:1] == ["--model"]:
        if len(remaining) < 2 or not remaining[1] or remaining[1].startswith("-"):
            return error
        remaining = remaining[2:]
    if remaining[: len(required_extra_args)] != required_extra_args:
        return error
    remaining = remaining[len(required_extra_args) :]
    if len(remaining) != 2 or remaining[0] != "--" or not remaining[1]:
        return error
    return None


def build_product_owner_agent_argv(
    *,
    runtime: dict[str, Any],
    workspace_id: str,
    workspace_path: str,
    prompt: str,
    model: str | None = None,
    agent_id: str,
    connection: sqlite3.Connection,
) -> list[str]:
    """Build the structured argv for a ProductOwnerAgent run (Codex or Claude Code CLI only).

    Rejects runtime-specific argv overrides, validates the detected executable, and delegates to the
    runtime's command builder with the supplied analysis prompt. The resulting argv is validated again
    against the canonical read-only/tool-isolated contract before it can reach ToolBroker. Codex is
    pinned to versions whose feature surface has been reviewed because its CLI has no global tools
    allowlist; an unknown version fails closed instead of silently inheriting new built-in tools.

    Raises:
        RuntimeCommandUnavailableError: if the runtime cannot produce a safe command.
    """
    if runtime.get("productOwnerAgentArgv") is not None:
        raise RuntimeCommandUnavailableError(
            "Runtime productOwnerAgentArgv overrides are not supported for ProductOwnerAgent."
        )
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in {"codex_cli", "claude_code_cli"}:
        raise RuntimeCommandUnavailableError(
            "Runtime provider does not expose a ProductOwnerAgent CLI executor."
        )
    if runtime_id == "codex_cli":
        version = str(runtime.get("version") or "").strip()
        if runtime.get("versionVerified") is not True:
            raise RuntimeCommandUnavailableError(
                "Codex CLI version was not verified by the current runtime probe; "
                "ProductOwnerAgent execution fails closed."
            )
        if version not in PRODUCT_OWNER_SUPPORTED_CODEX_VERSIONS:
            supported = ", ".join(sorted(PRODUCT_OWNER_SUPPORTED_CODEX_VERSIONS))
            raise RuntimeCommandUnavailableError(
                "Codex CLI version is not approved for ProductOwnerAgent's tool-isolated contract "
                f"(detected: {version or 'unknown'}; supported: {supported})."
            )
    executable = str(runtime.get("detectedCommand") or "").strip()
    if not executable:
        raise RuntimeCommandUnavailableError("Runtime status did not provide a detected executable command.")
    if not _matches_runtime_executable(runtime_id, executable):
        raise RuntimeCommandUnavailableError(
            "Runtime detected executable does not match the declared runtime command."
        )
    cli_runtime = runtime_for(runtime_id, connection=connection, executable=executable)
    extra_args = (
        PRODUCT_OWNER_CODEX_EXTRA_ARGS
        if runtime_id == "codex_cli"
        else PRODUCT_OWNER_CLAUDE_EXTRA_ARGS
    )
    request = RuntimeRequest.model_validate(
        {
            "runtime": runtime_id,
            "workspaceId": workspace_id,
            "workspacePath": workspace_path,
            "prompt": prompt,
            "model": model,
            "envPolicy": {"permissionProfile": "plan", "network": False, "secrets": False},
            "extraArgs": extra_args,
            "role": "product_owner",
            "agentId": agent_id,
        }
    )
    try:
        command = cli_runtime.build_command(request)
    except ValueError as error:
        raise RuntimeCommandUnavailableError(str(error)) from error
    validation_error = validate_product_owner_runtime_argv(
        runtime_id=runtime_id,
        argv=command,
        workspace_path=workspace_path,
    )
    if validation_error:
        raise RuntimeCommandUnavailableError(validation_error)
    return command


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

# El probe de auth nativa también lanza un subprocess (`claude auth status` / `codex login status`)
# y su veredicto es estable en ventanas cortas; se cachea aparte de la detección porque solo se
# consulta mientras la cuenta no está validada y no debe repetirse en cada poll del shell.
_AUTH_CACHE_TTL_SECONDS = 60.0
_auth_cache: dict[tuple[str, str | None], tuple[float, dict[str, Any]]] = {}


def reset_detection_cache() -> None:
    """Vacía los cachés de detección y de auth nativa; usar entre tests para evitar fugas globales."""
    with _detection_cache_lock:
        _detection_cache.clear()
        _auth_cache.clear()


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

    def validate_native_auth(self, runtime_id: str, *, executable: str | None = None) -> dict[str, Any]:
        """Sondea el estado de login nativo de un runtime CLI, con caché TTL propio.

        Devuelve el payload de ``RuntimeAuthStatus`` (status authenticated/unauthenticated/unknown).
        Cachea por ``(runtime_id, executable)`` para que el polling del shell no repita el
        subprocess; el probe corre fuera del candado para no serializar probes entre sí.
        """
        key = (runtime_id, executable)
        now = time.monotonic()
        with _detection_cache_lock:
            cached = _auth_cache.get(key)
            if cached is not None and now - cached[0] < _AUTH_CACHE_TTL_SECONDS:
                return dict(cached[1])
        status = runtime_for(runtime_id, executable=executable).validate_native_auth().model_dump()
        with _detection_cache_lock:
            _auth_cache[key] = (time.monotonic(), status)
        return dict(status)
