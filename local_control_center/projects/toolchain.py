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

Este módulo arma el plan y reporta si el ejecutable está presente en el host; no ejecuta ningún
comando del proyecto ni decide política — la allowlist de `security_policy.permissions` sigue
siendo la autoridad sobre qué se permite correr, y cada comando de acá debe pasar por ella. Su
única escritura es un marcador propio de AIDO (`node_modules/.aido-deps.json`), no un artefacto
del proyecto ni el resultado de ejecutar nada.

Los workspaces de las historias son git worktrees: `node_modules/` está en `.gitignore` y ningún
worktree nuevo lo trae, así que todo comando Node del QA moría con `Cannot find module`. Antes de
esos comandos, este módulo decide si hace falta instalar dependencias (con el gestor del
lockfile, de forma congelada y preferentemente offline) o si instalar sería no determinista
(falta el lockfile), caso en el que bloquea en vez de instalar a ciegas.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
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

TOOLCHAIN_IMAGES: dict[str, str] = {
    "java-maven": "maven:3.9-eclipse-temurin-21",
    "java-gradle": "gradle:8.10-jdk21",
    "node": "node:22-bookworm",
    "python": "ghcr.io/astral-sh/uv:python3.13-bookworm-slim",
    "go": "golang:1.23-bookworm",
    "rust": "rust:1.83-bookworm",
}
"""Imagen oficial por toolchain, para correr el proyecto cuando el host no tiene sus herramientas.

Cada una trae el ejecutable que el planificador emite: la de Node incluye npm y corepack (de ahi
salen pnpm y yarn), y la de Python es la de Astral porque el plan usa `uv run pytest`, que no
existe en `python:slim`. Se fijan a major.minor y no a `latest`: reproducible sin congelar
parches de seguridad. Verificadas contra su registry el 2026-09-18.

Es tambien la allowlist: `ProjectBuildContainer` rechaza cualquier imagen fuera de este catalogo,
porque la imagen entra al argv de `docker run`.
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

    container_argv: list[str] = field(default_factory=list)
    """El mismo comando, pero con la herramienta del contenedor en vez de la ruta del host.

    `argv` trae la ruta resuelta en este equipo (`C:/.../mvn.cmd`, o el wrapper del repo), que
    dentro del contenedor no existe. Y en contenedor no se usa el wrapper del repo aunque exista:
    la imagen **es** la toolchain fijada, y `mvnw.cmd` es un batch de Windows que ahi no corre.
    """

    critical: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    """Timeout explicito para comandos que lo necesitan mas amplio que el default del QA (p. ej.
    instalar dependencias). ``None`` deja que el runner de QA use su propio default."""

    unresolved_reason: str | None = None
    """Si no es ``None``, este comando no tiene un argv ejecutable real: no correspondia
    ejecutarlo de forma determinista (p. ej. instalar dependencias sin lockfile). El runner de QA
    lo registra como ``skipped_with_reason`` con este motivo, sin pasarlo por el broker."""

    node_install_marker: dict[str, str] | None = None
    """Si esta presente (``{"manager", "lockfileSha256"}``), y el comando pasa, el runner de QA
    escribe el marcador de dependencias Node para no reinstalar en la proxima corrida."""

    def is_available_on_host(self) -> bool:
        """Indica si este comando puede correr en el host tal como esta planificado.

        Se mira ``argv[0]``, no el nombre del ejecutable: el wrapper del repo (`mvnw.cmd`,
        `gradlew.bat`) vive dentro del proyecto y **nunca** esta en el PATH, asi que buscarlo con
        `which` lo declaraba ausente y le pedia al operador instalar algo que ya tenia. Un
        comando ``unresolved_reason`` nunca esta "disponible": no hay nada que buscar.
        """
        if self.unresolved_reason is not None:
            return False
        head = self.argv[0] if self.argv else self.executable
        if "/" in head or "\\" in head:
            return Path(head).is_file()
        return executable_is_available(head)

    def as_command_spec(self) -> dict[str, Any]:
        """Proyecta el comando al dict que consume el runner de QA."""
        spec: dict[str, Any] = {
            "label": self.label,
            "argv": list(self.argv),
            "critical": self.critical,
        }
        if self.timeout_seconds is not None:
            spec["timeoutSeconds"] = self.timeout_seconds
        if self.unresolved_reason is not None:
            spec["unresolvedReason"] = self.unresolved_reason
        if self.node_install_marker is not None:
            spec["nodeInstallMarker"] = dict(self.node_install_marker)
        return spec


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_bytes()[:_MAX_MANIFEST_BYTES].decode("utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


# El argv se emite con el nombre pelado del binario, sin resolverlo a una ruta absoluta.
# `sandbox._resolved_subprocess_argv` ya lo resuelve al ejecutar, contra su propia allowlist.
# Resolverlo aca duplicaba esa logica, producia `corepack.cmd` donde el resto del sistema espera
# `corepack`, y metia una ruta absoluta justo donde la politica solo ve el basename.
# El wrapper del repo es la excepcion: ahi la ruta ES el dato, porque no esta en el PATH.


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


NODE_DEPENDENCY_MARKER_NAME = ".aido-deps.json"
"""Marcador propio de AIDO dentro de `node_modules/` que registra con que gestor y lockfile se
instalo, para no reinstalar en cada corrida de QA."""

NODE_NO_LOCKFILE_REASON = "sin lockfile no hay instalación reproducible"
"""Motivo de bloqueo cuando faltan `node_modules` y no hay lockfile del gestor detectado.

Instalar sin lockfile resuelve versiones nuevas en cada corrida (no es determinista) y puede
introducir codigo que nadie reviso; se bloquea, igual que un comando faltante, en vez de
instalar a ciegas."""

_FROZEN_NODE_INSTALL_TIMEOUT_SECONDS = 300
"""Una instalacion en frio (store sin calentar) tarda mas que el default de QA; el maximo que el
runner de QA acepta ya es este valor, asi que no hace falta pedir mas."""

_LOCKFILE_FILENAME_BY_MANAGER: dict[str, str] = {manager: filename for filename, manager in _NODE_LOCKFILES}


def _has_node_dependencies(workspace: Path) -> bool:
    manifest = _read_json(workspace / "package.json")
    return bool(manifest.get("dependencies") or manifest.get("devDependencies"))


def _node_lockfile_path(workspace: Path, manager: str) -> Path | None:
    filename = _LOCKFILE_FILENAME_BY_MANAGER.get(manager)
    if filename is None:
        return None
    candidate = workspace / filename
    return candidate if candidate.is_file() else None


def node_lockfile_sha256(path: Path) -> str:
    """Hash del lockfile: cambia si, y solo si, las dependencias resueltas cambiaron."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _node_dependency_marker_path(workspace: Path) -> Path:
    return workspace / "node_modules" / NODE_DEPENDENCY_MARKER_NAME


def node_dependency_marker_matches(workspace: Path, *, manager: str, lockfile_sha256: str) -> bool:
    """Indica si el `node_modules` presente ya corresponde a este gestor y a este lockfile."""
    marker = _node_dependency_marker_path(workspace)
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("manager") == manager and data.get("lockfileSha256") == lockfile_sha256


def write_node_dependency_marker(workspace: Path, *, manager: str, lockfile_sha256: str) -> None:
    """Registra que `node_modules` ya quedo instalado para este gestor y este lockfile.

    Unica escritura de este modulo: no es un artefacto del proyecto ni un comando ejecutado, es
    metadata propia de AIDO para no reinstalar en cada corrida de QA.
    """
    marker = _node_dependency_marker_path(workspace)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"manager": manager, "lockfileSha256": lockfile_sha256}), encoding="utf-8")


@dataclass(frozen=True)
class NodeInstallPlan:
    """Decision sobre si (y como) instalar dependencias Node en un workspace antes del QA."""

    needs_install: bool
    command: ToolchainCommand | None = None
    blocking_reason: str | None = None
    """Motivo por el que no se puede instalar de forma determinista (sin lockfile). Si esta
    presente, ``command`` es ``None``: no hay nada ejecutable que ofrecer."""


def _node_install_command(manager: str, workspace: Path, *, lockfile_sha256: str) -> ToolchainCommand:
    marker = {"manager": manager, "lockfileSha256": lockfile_sha256}
    if manager == "pnpm":
        argv = [
            "corepack",
            f"pnpm@{PNPM_VERSION}",
            "install",
            "--frozen-lockfile",
            "--prefer-offline",
            "--ignore-scripts",
        ]
        label = "Install dependencies (pnpm install --frozen-lockfile)"
        executable = "corepack"
    elif manager == "npm":
        argv = ["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund", "--ignore-scripts"]
        label = "Install dependencies (npm ci)"
        executable = "npm"
    else:
        # Yarn Berry no tiene --ignore-scripts; --mode=skip-build omite los scripts de build.
        flags = (
            ["--immutable", "--mode=skip-build"]
            if (workspace / ".yarnrc.yml").is_file()
            else ["--frozen-lockfile", "--ignore-scripts"]
        )
        argv = ["yarn", "install", *flags]
        label = f"Install dependencies (yarn install {flags[0]})"
        executable = "yarn"
    return ToolchainCommand(
        purpose="install",
        label=label,
        argv=argv,
        policy_command=" ".join(argv),
        toolchain="node",
        executable=executable,
        container_argv=list(argv),
        critical=True,
        timeout_seconds=_FROZEN_NODE_INSTALL_TIMEOUT_SECONDS,
        node_install_marker=marker,
    )


def resolve_node_install_plan(workspace: Path, *, manager: str) -> NodeInstallPlan:
    """Decide si hace falta instalar dependencias Node, y si es posible hacerlo de forma reproducible.

    Sin `dependencies`/`devDependencies` declaradas no hay nada que instalar. Con `node_modules`
    ya presente y un marcador vigente (mismo gestor, mismo hash de lockfile), tampoco: no se
    reinstala en cada corrida. Sin lockfile del gestor detectado, instalar seria no determinista;
    se bloquea salvo que `node_modules` ya exista (no se toca lo que ya esta ahi).
    """
    if not _has_node_dependencies(workspace):
        return NodeInstallPlan(needs_install=False)
    node_modules_present = (workspace / "node_modules").is_dir()
    lockfile = _node_lockfile_path(workspace, manager)
    if lockfile is None:
        if node_modules_present:
            return NodeInstallPlan(needs_install=False)
        return NodeInstallPlan(needs_install=True, blocking_reason=NODE_NO_LOCKFILE_REASON)
    lockfile_sha256 = node_lockfile_sha256(lockfile)
    if node_modules_present and node_dependency_marker_matches(
        workspace, manager=manager, lockfile_sha256=lockfile_sha256
    ):
        return NodeInstallPlan(needs_install=False)
    return NodeInstallPlan(
        needs_install=True, command=_node_install_command(manager, workspace, lockfile_sha256=lockfile_sha256)
    )


def _wrapper_path(workspace: Path, *names: str) -> Path | None:
    """Devuelve el wrapper versionado del repo, que manda sobre cualquier binario global."""
    for name in names:
        candidate = workspace / name
        if candidate.is_file():
            return candidate
    return None


def _node_unresolved_install_command(reason: str) -> ToolchainCommand:
    """Comando sin argv ejecutable real: representa el bloqueo por falta de lockfile.

    El runner de QA lo reconoce por ``unresolved_reason`` y lo registra directo como
    ``skipped_with_reason``, sin intentar ejecutar el argv (que es solo un marcador legible).
    """
    return ToolchainCommand(
        purpose="install",
        label="Install dependencies (no lockfile)",
        argv=["node-dependency-install-unresolved"],
        policy_command="",
        toolchain="node",
        executable="",
        critical=True,
        unresolved_reason=reason,
    )


def _node_commands(workspace: Path) -> list[ToolchainCommand]:
    scripts = {str(name) for name in (_read_json(workspace / "package.json").get("scripts") or {})}
    if not scripts:
        return []
    manager = node_package_manager(workspace)
    install_plan = resolve_node_install_plan(workspace, manager=manager)
    if install_plan.blocking_reason:
        # Sin lockfile no hay como instalar de forma determinista: ningun comando Node (test,
        # build...) puede correr con confianza, asi que el plan se reduce a este unico bloqueo.
        return [_node_unresolved_install_command(install_plan.blocking_reason)]
    commands: list[ToolchainCommand] = []
    if install_plan.command is not None:
        commands.append(install_plan.command)
    for purpose, candidates in NODE_SCRIPT_CANDIDATES.items():
        script = next((candidate for candidate in candidates if candidate in scripts), None)
        if script is None:
            continue
        if manager == "pnpm":
            argv = ["corepack", f"pnpm@{PNPM_VERSION}", "run", script]
            policy = f"corepack pnpm@{PNPM_VERSION} run {script}"
            executable = "corepack"
            container = ["corepack", f"pnpm@{PNPM_VERSION}", "run", script]
        else:
            argv = [manager, "run", script]
            policy = f"{manager} run {script}"
            executable = manager
            container = [manager, "run", script]
        commands.append(
            ToolchainCommand(
                purpose=purpose,
                label=f"{purpose.capitalize()} ({manager} run {script})",
                argv=argv,
                policy_command=policy,
                toolchain="node",
                executable=executable,
                container_argv=container,
                metadata={"script": script, "packageManager": manager},
            )
        )
    return commands


def _maven_commands(workspace: Path) -> list[ToolchainCommand]:
    wrapper = _wrapper_path(workspace, "mvnw.cmd", "mvnw.bat", "mvnw")
    executable = wrapper.name if wrapper else "mvn"
    argv_head = str(wrapper) if wrapper else "mvn"
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
            container_argv=["mvn", "-B", "test"],
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (maven)",
            argv=[argv_head, "-B", "verify", "-DskipTests"],
            policy_command=f"{executable} -B verify -DskipTests",
            toolchain="java-maven",
            executable=executable,
            container_argv=["mvn", "-B", "verify", "-DskipTests"],
        ),
    ]


def _gradle_commands(workspace: Path) -> list[ToolchainCommand]:
    wrapper = _wrapper_path(workspace, "gradlew.bat", "gradlew")
    executable = wrapper.name if wrapper else "gradle"
    argv_head = str(wrapper) if wrapper else "gradle"
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (gradle)",
            argv=[argv_head, "--console=plain", "test"],
            policy_command=f"{executable} --console=plain test",
            toolchain="java-gradle",
            executable=executable,
            container_argv=["gradle", "--console=plain", "test"],
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (gradle)",
            argv=[argv_head, "--console=plain", "assemble"],
            policy_command=f"{executable} --console=plain assemble",
            toolchain="java-gradle",
            executable=executable,
            container_argv=["gradle", "--console=plain", "assemble"],
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
            argv=["uv", "run", "pytest", directory, "-q"],
            policy_command=f"uv run pytest {directory} -q",
            toolchain="python",
            executable="uv",
            container_argv=["uv", "run", "pytest", directory, "-q"],
            metadata={"directory": directory},
        )
    ]


def _go_commands(workspace: Path) -> list[ToolchainCommand]:  # noqa: ARG001
    """Su plan no depende de archivos del repo; la firma se mantiene para el dispatch."""
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (go)",
            argv=["go", "test", "./..."],
            policy_command="go test ./...",
            toolchain="go",
            executable="go",
            container_argv=["go", "test", "./..."],
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (go)",
            argv=["go", "build", "./..."],
            policy_command="go build ./...",
            toolchain="go",
            executable="go",
            container_argv=["go", "build", "./..."],
        ),
    ]


def _rust_commands(workspace: Path) -> list[ToolchainCommand]:  # noqa: ARG001
    """Su plan no depende de archivos del repo; la firma se mantiene para el dispatch."""
    return [
        ToolchainCommand(
            purpose="test",
            label="Test (cargo)",
            argv=["cargo", "test"],
            policy_command="cargo test",
            toolchain="rust",
            executable="cargo",
            container_argv=["cargo", "test"],
        ),
        ToolchainCommand(
            purpose="build",
            label="Build (cargo)",
            argv=["cargo", "build"],
            policy_command="cargo build",
            toolchain="rust",
            executable="cargo",
            container_argv=["cargo", "build"],
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
        if not command.is_available_on_host():
            missing.append(command.toolchain)
    return missing


def container_image_for(toolchain: str) -> str | None:
    """Imagen del catalogo para esa toolchain, o ``None`` si no hay ninguna declarada."""
    return TOOLCHAIN_IMAGES.get(toolchain)


def _command_is_available(command: ToolchainCommand) -> bool:
    """Indireccion por defecto, para que los tests puedan simular un host sin la toolchain."""
    return command.is_available_on_host()


CONTAINERIZED_SETTING_KEY = "project.runtime.containerized"
"""Preferencia por proyecto que habilita el runtime contenerizado. Apagada por defecto."""


def container_image_for_command(
    connection: Any,
    *,
    project_id: str,
    command: ToolchainCommand,
    available: Callable[[ToolchainCommand], bool] = _command_is_available,
) -> str | None:
    """Imagen en la que correr ese comando, o ``None`` si corre en el host.

    La toolchain local **siempre gana**: es la que el proyecto usa de verdad y no paga el costo
    de un contenedor. Solo cuando falta, y solo si el operador opto explicitamente por
    contenerizar ese proyecto, se resuelve la imagen del catalogo.
    """
    from local_control_center.settings.resolver import resolve_setting_value

    if available(command):
        return None
    enabled = resolve_setting_value(
        connection=connection, key=CONTAINERIZED_SETTING_KEY, project_id=project_id
    )
    if not enabled:
        return None
    return container_image_for(command.toolchain)
