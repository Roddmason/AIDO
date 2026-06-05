from __future__ import annotations

import subprocess
import time
import shutil
from pathlib import Path
from typing import Any


ALLOWED_EXECUTABLES = {
    "claude",
    "claude.cmd",
    "claude.exe",
    "codex",
    "codex.cmd",
    "codex.exe",
    "corepack",
    "corepack.cmd",
    "corepack.exe",
    "pnpm",
    "pnpm.cmd",
    "pnpm.exe",
    "py",
    "py.exe",
    "pytest",
    "pytest.exe",
    "node",
    "node.exe",
    "openhands",
    "openhands.exe",
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "ruff",
    "ruff.exe",
    "swe-agent",
    "swe-agent.exe",
    "sweagent",
    "sweagent.exe",
    "uv",
    "uv.exe",
}
DANGEROUS_ARG_PREFIXES = (
    "--mount",
    "--network=host",
    "--no-sandbox",
    "--privileged",
    "--volume",
)
VERSION_ARGS = {"--version", "-V", "version"}

MAX_CAPTURE_CHARS = 4000


def _truncate(value: str) -> str:
    if len(value) <= MAX_CAPTURE_CHARS:
        return value
    return value[:MAX_CAPTURE_CHARS] + "\n[truncated]"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _dangerous_arg(argv: list[str]) -> str | None:
    args = argv[1:]
    for index, arg in enumerate(args):
        lowered = arg.strip().lower()
        if lowered == "--network" and index + 1 < len(args) and args[index + 1].strip().lower() == "host":
            return "--network host"
        if any(lowered == prefix or lowered.startswith(f"{prefix}=") for prefix in DANGEROUS_ARG_PREFIXES):
            return arg
    return None


def run_version_check(
    *,
    argv: Any,
    cwd: str | None = None,
    timeout_seconds: int = 5,
) -> dict[str, Any]:
    if not isinstance(argv, list) or len(argv) != 2 or not all(isinstance(item, str) and item for item in argv):
        return {
            "executed": False,
            "blocked": True,
            "reason": "Version check requires argv shaped as [executable, version flag].",
        }
    if argv[1] not in VERSION_ARGS:
        return {
            "executed": False,
            "blocked": True,
            "reason": "Version check requires an allowed version flag.",
        }
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(1, min(timeout_seconds, 30)),
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "executed": True,
            "blocked": False,
            "timedOut": True,
            "returnCode": None,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "stdout": _truncate(exc.stdout or ""),
            "stderr": _truncate(exc.stderr or ""),
        }
    except OSError as exc:
        return {
            "executed": False,
            "blocked": True,
            "reason": str(exc),
        }
    return {
        "executed": True,
        "blocked": False,
        "timedOut": False,
        "returnCode": completed.returncode,
        "durationMs": int((time.perf_counter() - started) * 1000),
        "stdout": _truncate(completed.stdout or ""),
        "stderr": _truncate(completed.stderr or ""),
    }


class RestrictedSubprocessSandbox:
    """Runs low-risk commands without invoking a shell.

    This is the degraded local sandbox for Windows-first MVP use. It is not a
    replacement for Docker isolation; it only executes already policy-allowed,
    structured argv calls in the allocated workspace.
    """

    def execute(
        self,
        *,
        argv: Any,
        cwd: str | None,
        workspace_path: str | None,
        timeout_seconds: int = 30,
        truncate_output: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            return {
                "executed": False,
                "blocked": True,
                "reason": "Restricted subprocess requires a structured argv list.",
            }

        executable = Path(argv[0]).name.lower()
        if executable not in ALLOWED_EXECUTABLES:
            return {
                "executed": False,
                "blocked": True,
                "reason": f"Executable is not allowlisted for restricted subprocess: {executable}",
            }
        blocked_arg = _dangerous_arg(argv)
        if blocked_arg:
            return {
                "executed": False,
                "blocked": True,
                "reason": f"Dangerous subprocess flag is blocked by runtime policy: {blocked_arg}",
            }

        workspace = Path(workspace_path or cwd or ".").resolve(strict=False)
        run_cwd = Path(cwd or workspace).resolve(strict=False)
        if not _inside(run_cwd, workspace):
            return {
                "executed": False,
                "blocked": True,
                "reason": "Working directory is outside the allocated workspace.",
            }
        if not run_cwd.exists() or not run_cwd.is_dir():
            return {
                "executed": False,
                "blocked": True,
                "reason": "Working directory does not exist.",
            }

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=str(run_cwd),
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout_seconds, 120)),
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "executed": True,
                "blocked": False,
                "timedOut": True,
                "returnCode": None,
                "durationMs": int((time.perf_counter() - started) * 1000),
                "stdout": _truncate(exc.stdout or "") if truncate_output else (exc.stdout or ""),
                "stderr": _truncate(exc.stderr or "") if truncate_output else (exc.stderr or ""),
            }
        except OSError as exc:
            return {
                "executed": False,
                "blocked": True,
                "reason": str(exc),
            }

        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": completed.returncode,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "stdout": _truncate(completed.stdout or "") if truncate_output else (completed.stdout or ""),
            "stderr": _truncate(completed.stderr or "") if truncate_output else (completed.stderr or ""),
        }

    def execute_with_input(
        self,
        *,
        argv: Any,
        stdin_text: str,
        cwd: str | None,
        workspace_path: str | None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            return {
                "executed": False,
                "blocked": True,
                "reason": "Restricted subprocess requires a structured argv list.",
            }

        executable = Path(argv[0]).name.lower()
        if executable not in ALLOWED_EXECUTABLES:
            return {
                "executed": False,
                "blocked": True,
                "reason": f"Executable is not allowlisted for restricted subprocess: {executable}",
            }
        blocked_arg = _dangerous_arg(argv)
        if blocked_arg:
            return {
                "executed": False,
                "blocked": True,
                "reason": f"Dangerous subprocess flag is blocked by runtime policy: {blocked_arg}",
            }

        workspace = Path(workspace_path or cwd or ".").resolve(strict=False)
        run_cwd = Path(cwd or workspace).resolve(strict=False)
        if not _inside(run_cwd, workspace):
            return {
                "executed": False,
                "blocked": True,
                "reason": "Working directory is outside the allocated workspace.",
            }
        if not run_cwd.exists() or not run_cwd.is_dir():
            return {
                "executed": False,
                "blocked": True,
                "reason": "Working directory does not exist.",
            }

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                input=stdin_text,
                cwd=str(run_cwd),
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout_seconds, 120)),
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "executed": True,
                "blocked": False,
                "timedOut": True,
                "returnCode": None,
                "durationMs": int((time.perf_counter() - started) * 1000),
                "stdout": _truncate(exc.stdout or ""),
                "stderr": _truncate(exc.stderr or ""),
            }
        except OSError as exc:
            return {
                "executed": False,
                "blocked": True,
                "reason": str(exc),
            }

        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": completed.returncode,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "stdout": _truncate(completed.stdout or ""),
            "stderr": _truncate(completed.stderr or ""),
        }


class DockerSandbox:
    def __init__(self, docker_executable: str | None = None):
        self.docker_executable = docker_executable

    def _docker(self) -> str | None:
        return self.docker_executable or shutil.which("docker")

    def status(self) -> dict[str, Any]:
        docker = self._docker()
        return {
            "mode": "docker",
            "available": bool(docker),
            "required": False,
            "fallback": "restricted_subprocess",
            "executable": docker,
            "defaultNetwork": "none",
            "hostMount": "read_only",
            "writes": "container_tmpfs_or_staging_only",
        }

    def build_run_args(
        self,
        *,
        image: str,
        argv: list[str],
        workspace_path: str | Path,
        network: str = "none",
        memory: str = "2g",
        cpus: str = "2",
    ) -> list[str]:
        if not image or any(char.isspace() for char in image):
            raise ValueError("Docker image must be a single catalog value.")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("Docker sandbox requires structured argv.")
        workspace = Path(workspace_path).resolve(strict=False)
        docker = self._docker() or "docker"
        return [
            docker,
            "run",
            "--rm",
            "--network",
            network,
            "--memory",
            memory,
            "--cpus",
            cpus,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly",
            "--workdir",
            "/workspace",
            image,
            *argv,
        ]

    def execute(
        self,
        *,
        image: str,
        argv: list[str],
        workspace_path: str | Path,
        network: str = "none",
        memory: str = "2g",
        cpus: str = "2",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        docker = self._docker()
        if not docker:
            return {
                "executed": False,
                "blocked": True,
                "reason": "Docker executable is not available.",
            }
        workspace = Path(workspace_path).resolve(strict=False)
        if not workspace.exists() or not workspace.is_dir():
            return {
                "executed": False,
                "blocked": True,
                "reason": "Workspace path does not exist.",
            }

        args = self.build_run_args(
            image=image,
            argv=argv,
            workspace_path=workspace,
            network=network,
            memory=memory,
            cpus=cpus,
        )
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                args,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout_seconds, 900)),
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "executed": True,
                "blocked": False,
                "timedOut": True,
                "returnCode": None,
                "durationMs": int((time.perf_counter() - started) * 1000),
                "stdout": _truncate(exc.stdout or ""),
                "stderr": _truncate(exc.stderr or ""),
            }
        except OSError as exc:
            return {
                "executed": False,
                "blocked": True,
                "reason": str(exc),
            }
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": completed.returncode,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "stdout": _truncate(completed.stdout or ""),
            "stderr": _truncate(completed.stderr or ""),
            "command": args,
        }
