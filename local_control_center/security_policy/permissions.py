"""Parsea comandos y los mapea a categorias de allowlist (test, build, lint, instalacion...).

Convierte un comando crudo en ``ParsedCommand`` (ejecutable + args, sin shell) y lo clasifica
contra allowlists explicitas de scripts pnpm/uv, gestores de paquetes y binarios de solo
lectura. Invariante: solo los scripts/ejecutables enumerados aqui obtienen una categoria de
bajo riesgo; cualquier cosa fuera de la lista queda sin categoria y el motor la denegara o
elevara. No lanza: ante un comando mal formado devuelve ``None`` o categoria vacia.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

ALLOWED_PNPM_TEST_SCRIPTS = frozenset({"test", "test:py", "test:web", "test:e2e"})
ALLOWED_PNPM_BUILD_SCRIPTS = frozenset({"build", "build:web", "build:control-center"})
ALLOWED_PNPM_LINT_SCRIPTS = frozenset({"lint", "lint:py", "lint:web"})
ALLOWED_PNPM_TYPECHECK_SCRIPTS = frozenset({"typecheck", "typecheck:web"})
ALLOWED_PNPM_QUALITY_SCRIPTS = frozenset(
    {"quality", "quality:productive-truth", "quality:architecture", "test:all"}
)
ALLOWED_PNPM_SECURITY_SCRIPTS = frozenset({"security:secrets", "security:sast"})
PACKAGE_SCRIPT_HOOKS = frozenset(
    {
        "preinstall",
        "install",
        "postinstall",
        "prepare",
        "prepublish",
        "prepublishOnly",
        "publish",
        "deploy",
        "release",
    }
)

PACKAGE_MANAGER_INSTALL_VERBS = frozenset({"install", "add", "remove", "uninstall", "update", "upgrade"})
NETWORK_EXECUTABLES = frozenset({"curl", "curl.exe", "invoke-webrequest", "wget", "wget.exe", "ssh", "scp"})
READ_ONLY_EXECUTABLES = frozenset({"rg", "rg.exe", "get-content", "ls", "dir"})
RUNTIME_VERSION_EXECUTABLES = frozenset(
    {
        "corepack",
        "corepack.cmd",
        "corepack.exe",
        "node",
        "node.exe",
        "openhands",
        "openhands.exe",
        "pnpm",
        "pnpm.cmd",
        "pnpm.exe",
        "sweagent",
        "sweagent.exe",
        "swe-agent",
        "swe-agent.exe",
        "uv",
        "uv.exe",
    }
)


@dataclass(frozen=True)
class ParsedCommand:
    """Comando ya tokenizado: ejecutable normalizado en minuscula y sus argumentos."""

    executable: str
    args: tuple[str, ...]


def parse_command(command: str | None) -> ParsedCommand | None:
    """Tokeniza un comando con ``shlex`` (modo no-POSIX) sin invocar shell.

    Devuelve ``None`` si el comando esta vacio. Si el tokenizado falla por comillas mal
    balanceadas no lanza: retorna un ``ParsedCommand`` con ejecutable vacio para que el
    clasificador lo trate como desconocido.
    """
    text = (command or "").strip()
    if not text:
        return None
    try:
        words = shlex.split(text, posix=False)
    except ValueError:
        return ParsedCommand(executable="", args=())
    if not words:
        return None
    return ParsedCommand(
        executable=words[0].strip("\"'").lower(), args=tuple(word.strip("\"'") for word in words[1:])
    )


def _is_corepack_pnpm(parsed: ParsedCommand) -> tuple[bool, tuple[str, ...]]:
    if parsed.executable != "corepack":
        return False, ()
    if not parsed.args:
        return False, ()
    pnpm_token = parsed.args[0].lower()
    if not pnpm_token.startswith("pnpm"):
        return False, ()
    return True, parsed.args[1:]


def _is_pnpm(parsed: ParsedCommand) -> tuple[bool, tuple[str, ...]]:
    if parsed.executable in {"pnpm", "pnpm.cmd", "pnpm.exe"}:
        return True, parsed.args
    return _is_corepack_pnpm(parsed)


def pnpm_script_category(parsed: ParsedCommand) -> str | None:
    """Clasifica un ``pnpm run <script>`` (o ``corepack pnpm run ...``) por su script.

    Mapea scripts allowlisted a su categoria (test/build/lint/typecheck/quality/security_scan).
    Invariante de seguridad: cualquier script con nombre de hook de ciclo de vida (install,
    prepare, prefijos ``pre``/``post``, etc.) se marca ``package_script_hook`` porque puede
    ejecutar codigo arbitrario; los scripts no reconocidos caen a ``package_script``.
    """
    is_pnpm, args = _is_pnpm(parsed)
    if not is_pnpm or len(args) < 2 or args[0] != "run":
        return None
    script = args[1]
    if script in PACKAGE_SCRIPT_HOOKS or script.startswith(("pre", "post")):
        return "package_script_hook"
    if script in ALLOWED_PNPM_TEST_SCRIPTS:
        return "test"
    if script in ALLOWED_PNPM_BUILD_SCRIPTS:
        return "build"
    if script in ALLOWED_PNPM_LINT_SCRIPTS:
        return "lint"
    if script in ALLOWED_PNPM_TYPECHECK_SCRIPTS:
        return "typecheck"
    if script in ALLOWED_PNPM_QUALITY_SCRIPTS:
        return "quality"
    if script in ALLOWED_PNPM_SECURITY_SCRIPTS:
        return "security_scan"
    return "package_script"


def package_manager_category(parsed: ParsedCommand) -> str | None:
    """Detecta instalaciones/actualizaciones de dependencias (pnpm/npm/uv/pip/winget/choco).

    Devuelve ``"install"`` cuando el primer verbo es de mutacion de dependencias
    (install/add/remove/update/upgrade...), o ``None`` en otro caso. Estas acciones traen codigo
    de terceros, por eso el motor las trata como riesgo medio.
    """
    executable = parsed.executable
    args = parsed.args
    if (
        executable in {"pnpm", "pnpm.cmd", "pnpm.exe", "npm", "npm.cmd", "npm.exe"}
        and args
        and args[0] in PACKAGE_MANAGER_INSTALL_VERBS
    ):
        return "install"
    if executable == "corepack":
        is_pnpm, pnpm_args = _is_corepack_pnpm(parsed)
        if is_pnpm and pnpm_args and pnpm_args[0] in PACKAGE_MANAGER_INSTALL_VERBS:
            return "install"
    if (
        executable in {"uv", "uv.exe", "pip", "pip.exe", "winget", "choco"}
        and args
        and args[0] in PACKAGE_MANAGER_INSTALL_VERBS
    ):
        return "install"
    return None


def low_risk_shell_category(parsed: ParsedCommand) -> str | None:
    """Reconoce comandos de bajo riesgo: tests, lint, chequeos de version y solo-lectura.

    Solo concede categoria a invocaciones exactas y allowlisted (pnpm/uv scripts seguros,
    pytest/vitest/playwright, ``--version``, ``git status``/``diff``, binarios de lectura).
    Invariante: cualquier comando que no calce exactamente devuelve ``None`` y no se considera
    de bajo riesgo; el motor lo elevara a aprobacion.
    """
    pnpm_category = pnpm_script_category(parsed)
    if pnpm_category in {"test", "build", "lint", "typecheck", "quality", "security_scan"}:
        return pnpm_category
    if parsed.executable == "corepack" and len(parsed.args) == 2:
        pnpm_spec, version_arg = parsed.args
        if pnpm_spec.lower().startswith("pnpm") and version_arg in {"--version", "-V", "version"}:
            return "interpreter_version"
    if parsed.executable in {"uv", "uv.exe"} and len(parsed.args) >= 2 and parsed.args[0] == "run":
        if parsed.args[1] == "pytest":
            return "test"
        if parsed.args[1] == "ruff" and len(parsed.args) >= 3 and parsed.args[2] == "check":
            return "lint"
    if parsed.executable in {"pytest", "pytest.exe", "vitest", "vitest.cmd", "vitest.exe"}:
        return "test"
    if parsed.executable in {"playwright", "playwright.cmd", "playwright.exe"} and parsed.args[:1] == (
        "test",
    ):
        return "test"
    if (
        parsed.executable in {"python", "python.exe", "python3", "py", "py.exe"}
        and len(parsed.args) >= 2
        and parsed.args[:2] == ("-m", "pytest")
    ):
        return "test"
    if parsed.executable in {"python", "python.exe", "python3", "py", "py.exe"} and parsed.args == (
        "--version",
    ):
        return "interpreter_version"
    if parsed.executable in RUNTIME_VERSION_EXECUTABLES and parsed.args in {
        ("--version",),
        ("-V",),
        ("version",),
    }:
        return "interpreter_version"
    if parsed.executable in READ_ONLY_EXECUTABLES:
        return "read_only"
    if parsed.executable == "git" and parsed.args[:1] in {("status",), ("diff",)}:
        return "read_only"
    return None
