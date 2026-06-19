"""Sandboxes de ejecucion: aisla comandos en Docker o, como fallback, en subproceso restringido.

Provee dos backends de ejecucion controlada y comparte sus invariantes de aislamiento. Bloquea:
ejecutables fuera del allowlist, flags peligrosos (``--privileged``, ``--network host``,
``--mount``/``--volume``, ``--no-sandbox``) y cualquier cwd fuera del workspace asignado. El
sandbox Docker corre con ``--rm --read-only --network none``, tmpfs noexec/nosuid y el workspace
montado de solo lectura. ``open_restricted_text_process`` lanza ``PermissionError`` si la
invocacion viola la politica; los metodos ``execute*`` no lanzan: devuelven
``{"blocked": True, "reason": ...}`` cuando rechazan. Toda ejecucion usa ``shell=False`` con argv
estructurado (sin inyeccion) y la salida capturada se trunca a un maximo de caracteres.
"""

from __future__ import annotations

import shutil
import subprocess
import time
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
    "gitleaks",
    "gitleaks.cmd",
    "gitleaks.exe",
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
    "semgrep",
    "semgrep.cmd",
    "semgrep.exe",
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


def _validate_restricted_process(argv: Any, cwd: str | None, workspace_path: str | None) -> str | None:
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        return "Restricted subprocess requires a structured argv list."

    executable = Path(argv[0]).name.lower()
    if executable not in ALLOWED_EXECUTABLES:
        return f"Executable is not allowlisted for restricted subprocess: {executable}"
    blocked_arg = _dangerous_arg(argv)
    if blocked_arg:
        return f"Dangerous subprocess flag is blocked by runtime policy: {blocked_arg}"

    if cwd or workspace_path:
        workspace = Path(workspace_path or cwd or ".").resolve(strict=False)
        run_cwd = Path(cwd or workspace).resolve(strict=False)
        if not _inside(run_cwd, workspace):
            return "Working directory is outside the allocated workspace."
        if not run_cwd.exists() or not run_cwd.is_dir():
            return "Working directory does not exist."
    return None


def validate_restricted_process(argv: Any, cwd: str | None, workspace_path: str | None) -> str | None:
    """Devuelve el motivo de bloqueo si la invocacion no es admisible, o ``None`` si es segura.

    Comprueba que argv sea estructurado y no vacio, que el ejecutable este en el allowlist, que no
    use flags peligrosos y que el cwd resuelva dentro del workspace y exista.
    """
    return _validate_restricted_process(argv, cwd, workspace_path)


def open_restricted_text_process(
    *,
    argv: Any,
    cwd: str | None,
    workspace_path: str | None,
) -> subprocess.Popen[str]:
    """Abre un proceso de texto restringido con pipes stdin/stdout/stderr y ``shell=False``.

    Valida la invocacion antes de lanzarla y, ante violacion de politica, lanza
    ``PermissionError`` en vez de iniciar el proceso. El caller es responsable de drenar y cerrar
    los pipes.
    """
    error = _validate_restricted_process(argv, cwd, workspace_path)
    if error:
        raise PermissionError(error)
    run_cwd = str(Path(cwd).resolve(strict=False)) if cwd else None
    return subprocess.Popen(
        argv,
        cwd=run_cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        shell=False,
    )


def run_version_check(
    *,
    argv: Any,
    cwd: str | None = None,
    timeout_seconds: int = 5,
) -> dict[str, Any]:
    """Ejecuta un chequeo de version acotado: solo ``[ejecutable, flag-de-version]``.

    Invariante: rechaza (``blocked=True``) cualquier argv que no tenga exactamente dos elementos
    o cuyo segundo no sea un flag de version permitido, evitando colar otros subcomandos. El
    timeout se acota a [1, 30] s y la salida se trunca.
    """
    if (
        not isinstance(argv, list)
        or len(argv) != 2
        or not all(isinstance(item, str) and item for item in argv)
    ):
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

    def status(self) -> dict[str, Any]:
        """Reporta las garantias de este fallback: sin shell, argv requerido, atado al workspace."""
        return {
            "available": bool(ALLOWED_EXECUTABLES),
            "shell": False,
            "requiresArgv": True,
            "workspaceBound": True,
            "fallbackOnlyForLowRisk": True,
        }

    def execute(
        self,
        *,
        argv: Any,
        cwd: str | None,
        workspace_path: str | None,
        timeout_seconds: int = 30,
        truncate_output: bool = True,
    ) -> dict[str, Any]:
        """Corre un comando allowlisted dentro del workspace y devuelve su resultado capturado.

        Rechaza con ``{"blocked": True, ...}`` (sin lanzar) si argv es invalido, el ejecutable no
        esta allowlisted, hay un flag peligroso o el cwd cae fuera del workspace. El timeout se
        acota a [1, 120] s; la salida se trunca salvo que ``truncate_output`` sea ``False``.
        """
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


class DockerSandbox:
    """Ejecuta comandos en un contenedor efimero, aislado de red y con el workspace de solo lectura.

    Es el backend de aislamiento preferido. Invariantes de la ejecucion: ``--rm`` (efimero),
    ``--read-only`` con tmpfs ``noexec,nosuid`` para ``/tmp``, red por defecto ``none`` y el
    workspace montado de solo lectura en ``/workspace``. ``build_run_args`` lanza ``ValueError``
    ante una imagen con espacios o un argv mal formado; ``execute`` no lanza: si Docker no esta
    disponible o el workspace no existe devuelve ``{"blocked": True, ...}``.
    """

    def __init__(self, docker_executable: str | None = None):
        self.docker_executable = docker_executable

    def _docker(self) -> str | None:
        return self.docker_executable or shutil.which("docker")

    def status(self) -> dict[str, Any]:
        """Reporta disponibilidad de Docker y la postura de aislamiento (red none, mount RO)."""
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
        """Arma el argv de ``docker run`` con todos los flags de aislamiento aplicados.

        Fija ``--rm``, red, limites de memoria/cpu, ``--read-only`` con tmpfs noexec/nosuid y el
        workspace montado de solo lectura. Lanza ``ValueError`` si la imagen no es un unico valor
        de catalogo (sin espacios) o si el argv no es una lista de strings no vacios.
        """
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
        """Corre el contenedor aislado sobre el workspace y devuelve su resultado capturado.

        Devuelve ``{"blocked": True, ...}`` (sin lanzar) si Docker no esta disponible o el
        workspace no existe. El timeout se acota a [1, 900] s y la salida se trunca; el resultado
        incluye el argv efectivo en ``command``.
        """
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
