"""Resuelve con qué comandos se valida un proyecto, según la toolchain que ese proyecto usa.

Antes de este módulo todo el pipeline asumía `corepack pnpm run <script>`. Un proyecto Maven,
Gradle, Go o Rust no tiene `package.json`, así que la validación no fallaba: simplemente **no
corría nada** y el resultado se reportaba como saltado. Acá se decide, leyendo los manifiestos del
propio proyecto, qué comandos lo validan de verdad.

Dos reglas de "runtime del proyecto, no del sistema":

- **El wrapper del repo gana.** Si existe `mvnw`/`gradlew` se usa ese, no el binario global: es la
  versión que el proyecto fijó y la que usa su CI.
- **El gestor de paquetes sale del lockfile.** `pnpm-lock.yaml`, `package-lock.json` y `yarn.lock`
  eligen pnpm, npm o yarn respectivamente; asumir pnpm en un repo con `package-lock.json` instala
  un árbol de dependencias distinto al que el proyecto fijó.

Este módulo es de sólo lectura: arma el plan y reporta si el ejecutable está presente en el host.
No ejecuta nada ni decide política — la allowlist de `security_policy.permissions` sigue siendo la
autoridad sobre qué se permite correr, y cada comando de acá debe pasar por ella.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discovery import discover_project_path

NODE_SCRIPT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "test": ("test:web", "test"),
    "build": ("build:control-center", "build:web", "build"),
    "typecheck": ("typecheck:web", "typecheck"),
    "lint": ("lint:web", "lint:py", "lint"),
}
"""Scripts de `package.json` por propósito, en orden de preferencia.

Deliberadamente genéricos y acotados: un script de `package.json` ejecuta código arbitrario, así
que ampliarlos a cualquier nombre sería un agujero. Los scripts propios de un proyecto se habilitan
por el ajuste `project.quality.gateCommands`, bajo control explícito del operador.
"""

PNPM_VERSION = "10.24.0"
"""Versión de pnpm que corepack materializa, alineada con la allowlist de comandos."""

_NODE_LOCKFILES: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)

_MAX_MANIFEST_BYTES = 512 * 1024


@dataclass(frozen=True)
class ToolchainCommand:
    """Un comando de validación con todo lo que el ejecutor y la política necesitan saber."""

    purpose: str
    """`test`, `build`, `typecheck` o `lint`."""

    label: str
    argv: list[str]
    policy_command: str
    """El comando como texto, que es lo que `security_policy.permissions` clasifica."""

    toolchain: str
    """Id de runtime detectado (`node`, `java-maven`, `java-gradle`, `python`, `go`, `rust`)."""

    executable: str
    """Binario que debe existir en el host para que este comando pueda correr."""

    critical: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_command_spec(self) -> dict[str, Any]:
        """Proyecta el comando al dict que consume el runner de QA."""
        return {
            "label": self.label,
            "argv": list(self.argv),
            "critical": self.critical,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_bytes()[:_MAX_MANIFEST_BYTES].decode("utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_executable(name: str) -> str:
    """Resuelve un binario en el host probando las extensiones de Windows antes del nombre pelado."""
    candidates = (f"{name}.cmd", f"{name}.bat", f"{name}.exe", name) if "." not in name else (name,)
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return name


def executable_is_available(name: str) -> bool:
    """Indica si el binario existe en el host, que es lo que decide si hace falta dockerizar."""
    if "." not in name:
        return any(
            shutil.which(candidate) for candidate in (f"{name}.cmd", f"{name}.bat", f"{name}.exe", name)
        )
    return shutil.which(name) is not None


def node_package_manager(workspace: Path) -> str:
    """Elige el gestor de paquetes por el lockfile presente; pnpm es el default del repo."""
    for filename, manager in _NODE_LOCKFILES:
        if (workspace / filename).is_file():
            return manager
    return "pnpm"


def _wrapper_path(workspace: Path, *names: str) -> Path | None:
    """Devuelve el wrapper versionado del repo, que manda sobre cualquier binario global."""
    for name in names:
        candidate = workspace / name
        if candidate.is_file():
            return candidate
    return None


def _node_commands(workspace: Path) -> list[ToolchainCommand]:
    scripts = {str(name) for name in (_read_json(workspace / "package.json").get("scripts") or {})}
    if not scripts:
        return []
    manager = node_package_manager(workspace)
    commands: list[ToolchainCommand] = []
    for purpose, candidates in NODE_SCRIPT_CANDIDATES.items():
        script = next((candidate for candidate in candidates if candidate in scripts), None)
        if script is None:
            continue
        if manager == "pnpm":
            argv = [resolve_executable("corepack"), f"pnpm@{PNPM_VERSION}", "run", script]
            policy = f"corepack pnpm@{PNPM_VERSION} run {script}"
            executable = "corepack"
        else:
            argv = [resolve_executable(manager), "run", script]
            policy = f"{manager} run {script}"
            executable = manager
        commands.append(
            ToolchainCommand(
                purpose=purpose,
                label=f"{purpose.capitalize()} ({manager} run {script})",
                argv=argv,
                policy_command=policy,
                toolchain="node",
                executable=executable,
                metadata={"script": script, "packageManager": manager},
            )
        )
    return commands


def _maven_commands(workspace: Path) -> list[ToolchainCommand]:
    wrapper = _wrapper_path(workspace, "mvnw.cmd", "mvnw.bat", "mvnw")
    executable = wrapper.name if wrapper else "mvn"
    argv_head = str(wrapper) if wrapper else resolve_executable("mvn")
    # `-B` (batch) evita que Maven pida entrada interactiva, que colgaría el subproceso.
    # `verify` corre tests y empaqueta sin publicar nada fuera de la máquina.
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (maven)",
            argv=[argv_head, "-B", "test"],
            policy_command=f"{executable} -B test",
            toolchain="java-maven",
            executable=executable,
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (maven)",
            argv=[argv_head, "-B", "verify", "-DskipTests"],
            policy_command=f"{executable} -B verify -DskipTests",
            toolchain="java-maven",
            executable=executable,
        ),
    ]


def _gradle_commands(workspace: Path) -> list[ToolchainCommand]:
    wrapper = _wrapper_path(workspace, "gradlew.bat", "gradlew")
    executable = wrapper.name if wrapper else "gradle"
    argv_head = str(wrapper) if wrapper else resolve_executable("gradle")
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (gradle)",
            argv=[argv_head, "--console=plain", "test"],
            policy_command=f"{executable} --console=plain test",
            toolchain="java-gradle",
            executable=executable,
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (gradle)",
            argv=[argv_head, "--console=plain", "assemble"],
            policy_command=f"{executable} --console=plain assemble",
            toolchain="java-gradle",
            executable=executable,
        ),
    ]


def _python_commands(workspace: Path) -> list[ToolchainCommand]:
    directory = next(
        (name for name in ("tests_py", "tests") if (workspace / name).is_dir()),
        None,
    )
    if directory is None:
        return []
    return [
        ToolchainCommand(
            purpose="test",
            label=f"Test (pytest {directory})",
            argv=[resolve_executable("uv"), "run", "pytest", directory, "-q"],
            policy_command=f"uv run pytest {directory} -q",
            toolchain="python",
            executable="uv",
            metadata={"directory": directory},
        )
    ]


def _go_commands(workspace: Path) -> list[ToolchainCommand]:  # noqa: ARG001
    """Su plan no depende de archivos del repo; la firma se mantiene para el dispatch."""
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (go)",
            argv=[resolve_executable("go"), "test", "./..."],
            policy_command="go test ./...",
            toolchain="go",
            executable="go",
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (go)",
            argv=[resolve_executable("go"), "build", "./..."],
            policy_command="go build ./...",
            toolchain="go",
            executable="go",
        ),
    ]


def _rust_commands(workspace: Path) -> list[ToolchainCommand]:  # noqa: ARG001
    """Su plan no depende de archivos del repo; la firma se mantiene para el dispatch."""
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (cargo)",
            argv=[resolve_executable("cargo"), "test"],
            policy_command="cargo test",
            toolchain="rust",
            executable="cargo",
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (cargo)",
            argv=[resolve_executable("cargo"), "build"],
            policy_command="cargo build",
            toolchain="rust",
            executable="cargo",
        ),
    ]


_BUILDERS = {
    "node": _node_commands,
    "java-maven": _maven_commands,
    "java-gradle": _gradle_commands,
    "python": _python_commands,
    "go": _go_commands,
    "rust": _rust_commands,
}


def detected_toolchains(workspace: str | Path) -> list[str]:
    """Ids de runtime que el proyecto declara en sus manifiestos, en orden de detección."""
    discovery = discover_project_path(workspace)
    return [
        str(runtime["id"])
        for runtime in discovery.get("detectedRuntimes", [])
        if str(runtime.get("id", "")) in _BUILDERS
    ]


def plan_workspace_commands(workspace: str | Path) -> list[ToolchainCommand]:
    """Arma el plan de validación del proyecto a partir de sus propios manifiestos.

    Un proyecto políglota (por ejemplo backend Python + frontend Node) devuelve los comandos de
    cada toolchain: se valida lo que el proyecto realmente tiene, no lo que el sistema supone.
    """
    root = Path(workspace)
    commands: list[ToolchainCommand] = []
    for toolchain in detected_toolchains(root):
        commands.extend(_BUILDERS[toolchain](root))
    return commands


def missing_toolchains(commands: list[ToolchainCommand]) -> list[str]:
    """Toolchains cuyo ejecutable no está en el host: exactamente el caso que se dockeriza.

    Se devuelve el id de toolchain y no el binario porque es lo que el operador reconoce y lo que
    identifica la imagen a construir.
    """
    missing: list[str] = []
    for command in commands:
        if command.toolchain in missing:
            continue
        if not executable_is_available(command.executable):
            missing.append(command.toolchain)
    return missing
