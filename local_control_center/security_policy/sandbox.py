"""Sandboxes de ejecucion: aisla comandos en Docker o, como fallback, en subproceso restringido.

Provee dos backends de ejecucion controlada y comparte sus invariantes de aislamiento. Bloquea:
ejecutables fuera del allowlist, flags peligrosos (``--privileged``, ``--network host``,
``--mount``/``--volume``, ``--no-sandbox``) y cualquier cwd fuera del workspace asignado. El
sandbox Docker corre con ``--rm --read-only --network none``, tmpfs noexec/nosuid y el workspace
montado de solo lectura. ``open_restricted_text_process`` lanza ``PermissionError`` si la
invocacion viola la politica; los metodos ``execute*`` no lanzan: devuelven
``{"blocked": True, "reason": ...}`` cuando rechazan. Toda ejecucion usa ``shell=False`` con argv
estructurado (sin inyeccion) y la salida capturada se trunca a un maximo de caracteres.

@author Rodrigo Mason
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from local_control_center.process_supervision.docker import run_docker_capture
from local_control_center.process_supervision.service import (
    ProcessSupervisorService,
    classify_workload,
    run_probe_command,
    run_supervised_capture,
)

ALLOWED_EXECUTABLES = {
    # Toolchains de proyecto: las emite `projects/toolchain.py` para validar el repo del usuario
    # con SUS herramientas. Estar en la allowlist de `permissions` no alcanza — son dos capas
    # distintas: aquella clasifica el riesgo, esta autoriza el spawn. Los wrappers versionados
    # (`mvnw`, `gradlew`) van incluidos porque son la version que el proyecto fijo.
    "cargo",
    "cargo.exe",
    "go",
    "go.exe",
    "gradle",
    "gradle.bat",
    "gradle.exe",
    "gradlew",
    "gradlew.bat",
    "mvn",
    "mvn.cmd",
    "mvn.exe",
    "mvnw",
    "mvnw.bat",
    "mvnw.cmd",
    "npm",
    "npm.cmd",
    "npm.exe",
    "yarn",
    "yarn.cmd",
    "yarn.exe",
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
    "git",
    "git.cmd",
    "git.exe",
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
AUTH_STATUS_ARGS: set[tuple[str, ...]] = {("auth", "status", "--json"), ("login", "status")}

MAX_CAPTURE_CHARS = 4000
MAX_COMPLETE_CAPTURE_BYTES = 1_048_576
MAX_RESTRICTED_SUBPROCESS_TIMEOUT_SECONDS = 900


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


def _resolved_subprocess_argv(argv: list[str], *, search_path: str | None = None) -> list[str]:
    """Resolve PATH shims to a CreateProcess-compatible executable without invoking a shell.

    ``search_path`` resolves against the PATH the child will actually get (a project command's),
    instead of the control plane's own PATH.
    """
    resolved = list(argv)
    executable = resolved[0]
    if os.name == "nt" and not Path(executable).suffix:
        for extension in (".exe", ".cmd", ".com"):
            candidate = shutil.which(f"{executable}{extension}", path=search_path)
            if candidate and Path(candidate).name.lower() in ALLOWED_EXECUTABLES:
                resolved[0] = candidate
                return resolved
    candidate = shutil.which(executable, path=search_path)
    if candidate and (os.name != "nt" or Path(candidate).suffix):
        resolved[0] = candidate
    return resolved


def project_command_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Entorno de un comando del proyecto sin el virtualenv con el que corre el propio AIDO.

    AIDO corre desde su ``.venv`` (``uv run``) con ``Scripts`` primero en el ``PATH``: heredarlo tal
    cual hacía que ``python``/``pytest`` de un QA resolvieran al intérprete de AIDO y no al del
    proyecto, y ``uv run`` avisaba que ``VIRTUAL_ENV`` no coincide (visto en vivo). Se quitan ese
    virtualenv del ``PATH`` y de ``VIRTUAL_ENV`` y la profundidad de ``uv run`` de la cadena de AIDO;
    un virtualenv ajeno y el resto del entorno no se tocan.
    """
    environment = dict(base)
    own_prefix = Path(sys.prefix).resolve(strict=False)
    if own_prefix == Path(sys.base_prefix).resolve(strict=False):
        return environment
    virtual_env = environment.get("VIRTUAL_ENV")
    if virtual_env and _inside(Path(virtual_env), own_prefix):
        environment.pop("VIRTUAL_ENV")
        environment.pop("UV_RUN_RECURSION_DEPTH", None)
    path_key = next((key for key in environment if key.upper() == "PATH"), None)
    if path_key:
        environment[path_key] = os.pathsep.join(
            entry
            for entry in environment[path_key].split(os.pathsep)
            if entry and not _inside(Path(entry), own_prefix)
        )
    return environment


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
    execution_id: str | None = None,
    db_path: str | Path | None = None,
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
    command = _resolved_subprocess_argv([str(item) for item in argv])
    service = ProcessSupervisorService(db_path=db_path, popen_factory=subprocess.Popen)
    managed = service.start(
        argv=command,
        cwd=run_cwd or Path.cwd(),
        execution_id=execution_id,
        workload_class=classify_workload(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    return managed.process


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
        completed = run_probe_command(
            _resolved_subprocess_argv([str(item) for item in argv]),
            run_factory=subprocess.run,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(1, min(timeout_seconds, 30)),
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


def run_auth_status_check(
    *,
    argv: Any,
    cwd: str | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Ejecuta un sondeo de estado de autenticación nativa de un CLI, de solo lectura.

    Invariante: rechaza (``blocked=True``) cualquier argv cuyo sufijo tras el ejecutable no sea
    exactamente una de las tuplas allowlisted en ``AUTH_STATUS_ARGS`` — nunca ejecuta login,
    logout ni otros subcomandos. El timeout se acota a [1, 30] s y la salida se trunca.
    """
    if (
        not isinstance(argv, list)
        or len(argv) < 2
        or not all(isinstance(item, str) and item for item in argv)
    ):
        return {
            "executed": False,
            "blocked": True,
            "reason": "Auth status check requires argv shaped as [executable, *status subcommand].",
        }
    if tuple(argv[1:]) not in AUTH_STATUS_ARGS:
        return {
            "executed": False,
            "blocked": True,
            "reason": "Auth status check requires an allowlisted read-only status subcommand.",
        }
    started = time.perf_counter()
    try:
        completed = run_probe_command(
            _resolved_subprocess_argv([str(item) for item in argv]),
            run_factory=subprocess.run,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(1, min(timeout_seconds, 30)),
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
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Corre un comando allowlisted dentro del workspace y devuelve su resultado capturado.

        Rechaza con ``{"blocked": True, ...}`` (sin lanzar) si argv es invalido, el ejecutable no
        esta allowlisted, hay un flag peligroso o el cwd cae fuera del workspace. El timeout se
        valida en [1, 900] s sin recortarlo silenciosamente. Ambos streams se drenan
        concurrentemente y su captura queda acotada; ``truncate_output=False`` eleva el límite a
        1 MiB por stream, pero nunca lo deshabilita. ``environment`` reemplaza el entorno heredado
        cuando el broker entrega una allowlist construida por una operación interna confiable.
        """
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= MAX_RESTRICTED_SUBPROCESS_TIMEOUT_SECONDS
        ):
            return {
                "executed": False,
                "blocked": True,
                "reason": (
                    "Restricted subprocess timeout_seconds must be an integer between 1 and "
                    f"{MAX_RESTRICTED_SUBPROCESS_TIMEOUT_SECONDS}."
                ),
            }
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

        if environment is None:
            environment = project_command_environment(os.environ)
            path_key = next((key for key in environment if key.upper() == "PATH"), "PATH")
            command = _resolved_subprocess_argv(
                [str(item) for item in argv], search_path=environment.get(path_key, "")
            )
        else:
            command = _resolved_subprocess_argv([str(item) for item in argv])
        workload_class = classify_workload(command)
        try:
            result = run_supervised_capture(
                command,
                cwd=str(run_cwd),
                timeout_seconds=timeout_seconds,
                workload_class=workload_class,
                environment=environment,
                popen_factory=subprocess.Popen,
                capture_limit=MAX_CAPTURE_CHARS if truncate_output else MAX_COMPLETE_CAPTURE_BYTES,
            )
        except OSError as exc:
            return {
                "executed": False,
                "blocked": True,
                "reason": str(exc),
            }
        return {"executed": True, "blocked": False, **result}


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
        if not image or image.startswith("-") or any(char.isspace() for char in image):
            raise ValueError("Docker image must be a single catalog value.")
        if network not in {"none", "bridge"}:
            raise ValueError("Docker host/custom networks are not allowed by this sandbox.")
        memory_match = re.fullmatch(r"([1-9][0-9]*)([kmg]?)", str(memory).lower())
        memory_bytes = (
            int(memory_match[1]) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[memory_match[2]]
            if memory_match
            else 0
        )
        if not 0 < memory_bytes <= 2 * 1024**3:
            raise ValueError("Docker memory must be positive and no more than 2 GiB.")
        if not math.isfinite(float(cpus)) or not 0 < float(cpus) <= 2:
            raise ValueError("Docker CPU budget must be positive and no more than 2 CPUs.")
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
            "--pids-limit",
            "16",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
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
            completed = run_docker_capture(
                args,
                cwd=workspace,
                timeout_seconds=max(1, min(timeout_seconds, 900)),
            )
        except OSError as exc:
            return {
                "executed": False,
                "blocked": True,
                "reason": str(exc),
            }
        return {
            "executed": True,
            "blocked": False,
            **completed,
            "timedOut": completed["timedOut"],
            "returnCode": completed["returnCode"],
            "durationMs": int((time.perf_counter() - started) * 1000),
            "stdout": _truncate(completed["stdout"]),
            "stderr": _truncate(completed["stderr"]),
            "command": args,
            "managedProcessId": completed["managedProcessId"],
            "peakMemoryBytes": completed["peakMemoryBytes"],
            "cpuTimeSeconds": completed["cpuTimeSeconds"],
            "terminationReason": completed["terminationReason"],
        }


class ProjectBuildContainer:
    """Corre la validacion de un proyecto dentro de un contenedor con su propia toolchain.

    Existe aparte de ``DockerSandbox`` a proposito, y ese no se toca. ``DockerSandbox`` monta el
    workspace **readonly**, corre ``--read-only``, ``--network none`` y ``--pids-limit 16``: es un
    sandbox de *analisis*. Un build real necesita justo lo contrario — escribir ``target/``,
    ``build/`` o ``node_modules/``, resolver dependencias por red y levantar un daemon de Gradle con
    decenas de procesos. Meter ambas necesidades en una sola clase habria significado relajar el
    sandbox de analisis para todos sus usos.

    **Lo que se relaja, y por que:**

    - Workspace montado **lectura/escritura**: un build escribe sus artefactos por definicion.
    - Red ``bridge``: sin red, la primera resolucion de dependencias falla y el runtime no sirve.
    - ``--pids-limit`` amplio: el daemon de Gradle y los forks de la JVM superan 16 procesos.
    - Memoria y CPU con topes mayores: un build de JVM no cabe en 2 GiB.

    **Lo que NO se relaja, pase lo que pase:** nada de ``--privileged``, red del host, namespaces
    del host, ni montaje del socket de Docker — eso convertiria el contenedor en acceso root al
    equipo. Se conservan ``--rm``, ``--cap-drop ALL`` y ``--security-opt no-new-privileges``.

    Es **opt-in por proyecto** (`project.runtime.containerized`, default apagado): contenerizar
    cambia donde corre el codigo del usuario, y esa decision es del operador.
    """

    ALLOWED_NETWORKS = frozenset({"bridge", "none"})
    MAX_MEMORY_BYTES = 8 * 1024**3
    MAX_CPUS = 4.0
    PIDS_LIMIT = 256
    """Tope de procesos DENTRO del contenedor: un daemon de Gradle con sus forks supera 16.

    Es una proteccion contra fork bombs, no una declaracion de capacidad. Para el supervisor del
    host, `docker run` es **un** proceso cliente; los procesos del contenedor los administra el
    daemon de Docker. Por eso el `process_limit` del lease y este tope miden cosas distintas.
    """

    @staticmethod
    def workload_class_for(memory: str) -> str:
        """Perfil de recursos que corresponde al cap real del contenedor.

        `--memory` es un limite duro de cgroup, asi que reservar `build_heavy` (16 GiB) para un
        contenedor capado en 4 hace que el gobernador rechace por `aggregate_memory_budget` un
        trabajo que si cabia. Se declara el perfil mas chico que cubra el cap.
        """
        match = re.fullmatch(r"([1-9][0-9]*)([kmg]?)", str(memory).lower())
        requested = int(match[1]) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match[2]] if match else 0
        gib = 1024**3
        if requested <= 4 * gib:
            return "qa_light"
        if requested <= 8 * gib:
            return "browser_test"
        return "build_heavy"

    def __init__(self, docker_executable: str | None = None):
        self.docker_executable = docker_executable

    def _docker(self) -> str | None:
        return self.docker_executable or shutil.which("docker")

    def status(self) -> dict[str, Any]:
        """Reporta disponibilidad de Docker y la postura real de este runtime."""
        docker = self._docker()
        return {
            "mode": "project_build_container",
            "available": bool(docker),
            "required": False,
            "fallback": "host_toolchain",
            "executable": docker,
            "defaultNetwork": "bridge",
            "hostMount": "read_write",
            "writes": "project_workspace",
        }

    def build_run_args(
        self,
        *,
        image: str,
        argv: list[str],
        workspace_path: str | Path,
        network: str = "bridge",
        memory: str = "4g",
        cpus: str = "2",
    ) -> list[str]:
        """Arma el argv de ``docker run`` para un build, validando cada valor que entra.

        Lanza ``ValueError`` si la imagen no esta en el catalogo de toolchains, si la red no es
        ``bridge``/``none``, si memoria o CPU exceden el tope, o si el argv no es estructurado.
        """
        from local_control_center.projects.toolchain import TOOLCHAIN_IMAGES

        if not image or image.startswith("-") or any(char.isspace() for char in image):
            raise ValueError("Container image must be a single catalog value.")
        if image not in set(TOOLCHAIN_IMAGES.values()):
            # La imagen entra directo al argv de `docker run`: un valor libre es ejecucion
            # arbitraria con la red y el workspace ya montados.
            raise ValueError(f"Container image is not in the toolchain catalog: {image!r}")
        if network not in self.ALLOWED_NETWORKS:
            raise ValueError("Container network must be 'bridge' or 'none'; host networking is denied.")
        memory_match = re.fullmatch(r"([1-9][0-9]*)([kmg]?)", str(memory).lower())
        memory_bytes = (
            int(memory_match[1]) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[memory_match[2]]
            if memory_match
            else 0
        )
        if not 0 < memory_bytes <= self.MAX_MEMORY_BYTES:
            raise ValueError("Container memory must be positive and no more than 8 GiB.")
        if not math.isfinite(float(cpus)) or not 0 < float(cpus) <= self.MAX_CPUS:
            raise ValueError("Container CPU budget must be positive and no more than 4 CPUs.")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("Container runtime requires structured argv.")
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
            "--pids-limit",
            str(self.PIDS_LIMIT),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,source={workspace},target=/workspace",
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
        network: str = "bridge",
        memory: str = "4g",
        cpus: str = "2",
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Corre el build en el contenedor y devuelve su resultado capturado.

        Devuelve ``{"blocked": True, ...}`` sin lanzar si Docker no esta disponible o el workspace
        no existe: el agente que lo invoca no puede caerse porque el operador activo el modo
        contenerizado sin tener Docker.
        """
        docker = self._docker()
        if not docker:
            return {
                "executed": False,
                "blocked": True,
                "reason": "Docker executable is not available for the containerized project runtime.",
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
            completed = run_docker_capture(
                args,
                cwd=workspace,
                timeout_seconds=max(1, min(timeout_seconds, 1800)),
                workload_class=self.workload_class_for(memory),
            )
        except OSError as exc:
            return {"executed": False, "blocked": True, "reason": str(exc)}
        return {
            "executed": True,
            "blocked": False,
            **completed,
            "timedOut": completed["timedOut"],
            "returnCode": completed["returnCode"],
            "durationMs": int((time.perf_counter() - started) * 1000),
            "stdout": _truncate(completed["stdout"]),
            "stderr": _truncate(completed["stderr"]),
            "command": args,
            "managedProcessId": completed["managedProcessId"],
            "peakMemoryBytes": completed["peakMemoryBytes"],
            "cpuTimeSeconds": completed["cpuTimeSeconds"],
            "terminationReason": completed["terminationReason"],
        }
