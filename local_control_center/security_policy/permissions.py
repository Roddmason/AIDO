"""Parsea comandos y los mapea a categorias de allowlist (test, build, lint, instalacion...).

Convierte un comando crudo en ``ParsedCommand`` (ejecutable + args, sin shell) y lo clasifica
contra allowlists explicitas de scripts pnpm/uv, gestores de paquetes y binarios de solo
lectura. Invariante: solo los scripts/ejecutables enumerados aqui obtienen una categoria de
bajo riesgo; cualquier cosa fuera de la lista queda sin categoria y el motor la denegara o
elevara. No lanza: ante un comando mal formado devuelve ``None`` o categoria vacia.

@author Rodrigo Mason
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

NODE_RUN_EXECUTABLES = frozenset({"pnpm", "npm", "yarn"})
"""Gestores de Node que ejecutan scripts de `package.json`; se eligen por el lockfile del repo."""

MAVEN_EXECUTABLES = frozenset({"mvn", "mvnw", "./mvnw"})
GRADLE_EXECUTABLES = frozenset({"gradle", "gradlew", "./gradlew"})
ALLOWED_MAVEN_GOALS: dict[str, str] = {
    "clean": "build",
    "compile": "build",
    "test": "test",
    "verify": "build",
    "package": "build",
}
ALLOWED_GRADLE_TASKS: dict[str, str] = {
    "clean": "build",
    "classes": "build",
    "test": "test",
    "check": "test",
    "build": "build",
    "assemble": "build",
}
ALLOWED_GO_VERBS: dict[str, str] = {"test": "test", "build": "build", "vet": "lint"}
ALLOWED_CARGO_VERBS: dict[str, str] = {
    "test": "test",
    "build": "build",
    "check": "build",
    "clippy": "lint",
}
"""Verbos de validacion por toolchain: leen, compilan y prueban el proyecto.

Fuera quedan a proposito `deploy`, `release:*`, `publish`, `uploadArchives`, `install`, `get` y
`fmt` (reformatea en el lugar): publican fuera de la maquina, traen codigo de terceros o
escriben. Un comando con cualquier verbo no enumerado queda sin categoria y el motor lo eleva a
aprobacion, que es el default seguro.
"""

ALLOWED_MAVEN_FLAGS = frozenset({"-B", "--batch-mode", "-DskipTests", "-q", "--quiet", "-o", "--offline"})
ALLOWED_GRADLE_FLAGS = frozenset({"--console=plain", "-q", "--quiet", "--offline", "--no-daemon"})
ALLOWED_GO_FLAGS = frozenset({"-v", "-race", "-count=1", "-json"})
ALLOWED_CARGO_FLAGS = frozenset({"-q", "--quiet", "--locked", "--offline", "--all-targets", "--all-features"})
ALLOWED_GO_OPERANDS = frozenset({"./...", ".", "all"})
"""Banderas admitidas por toolchain. Es una allowlist a proposito, no una blocklist.

Estas herramientas exponen banderas que ejecutan un programa arbitrario como parte de su
operacion normal: `go test -exec/-toolexec` envuelve la ejecucion del binario de test o de todo
el toolchain, `gradle --init-script` corre Groovy antes del build, `mvn -Dmaven.ext.class.path`
inyecta una extension y `cargo --config target.*.runner` reemplaza el lanzador de binarios. Una
lista de banderas prohibidas siempre va a estar incompleta frente a eso; enumerar lo permitido
no. Validar solo los objetivos e ignorar las banderas dejaba pasar todo lo anterior como `test`.
"""

TOOLCHAIN_INSTALL_EXECUTABLES = frozenset({"cargo", "go"})
TOOLCHAIN_VERSION_EXECUTABLES = frozenset(
    MAVEN_EXECUTABLES | GRADLE_EXECUTABLES | {"go", "cargo", "rustc", "java", "javac"}
)

_LOWERED_PACKAGE_SCRIPT_HOOKS = frozenset(hook.lower() for hook in PACKAGE_SCRIPT_HOOKS)

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


def _base_executable(executable: str) -> str:
    """Normaliza la extension de Windows (`gradlew.bat` -> `gradlew`) y nada mas.

    **No** pela el directorio a proposito. Los llamadores legitimos ya entregan el nombre pelado
    (el runner reduce el argv a su basename antes de clasificar), asi que aceptar ademas una ruta
    arbitraria convertiria el nombre del archivo en la unica credencial: bastaria traer un
    `gradlew.bat` propio en cualquier carpeta para heredar la categoria de bajo riesgo.
    """
    if "/" in executable or "\\" in executable:
        return executable
    for suffix in (".cmd", ".bat", ".exe", ".ps1"):
        if executable.endswith(suffix):
            return executable[: -len(suffix)]
    return executable


def _toolchain_invocation_category(
    *,
    allowed_goals: dict[str, str],
    allowed_flags: frozenset[str],
    args: tuple[str, ...],
    allowed_operands: frozenset[str] = frozenset(),
) -> str | None:
    """Clasifica una invocacion solo si TODOS sus tokens estan allowlisted.

    Invariante de seguridad: un solo token desconocido —objetivo *o bandera*— invalida el
    comando completo. Sin la parte de banderas, `go test -exec ./evil.sh ./...` y
    `gradle --init-script=evil.gradle test` se clasificaban `test` y corrian sin aprobacion.
    Sin la parte de objetivos, `mvn -B test deploy` se colaba por su primer objetivo.
    """
    if not args:
        return None
    categories: set[str] = set()
    for token in args:
        if token.startswith("-"):
            if token not in allowed_flags:
                return None
            continue
        goal = token.lower()
        if goal in allowed_goals:
            categories.add(allowed_goals[goal])
            continue
        if goal in allowed_operands:
            continue
        return None
    if not categories:
        return None
    return "test" if "test" in categories else sorted(categories)[0]


def _is_corepack_pnpm(parsed: ParsedCommand) -> tuple[bool, tuple[str, ...]]:
    # Se compara el nombre base: el runner puede entregar el binario ya resuelto
    # (`corepack.cmd`, una ruta absoluta), y una igualdad estricta lo dejaria sin categoria.
    if _base_executable(parsed.executable) != "corepack":
        return False, ()
    if not parsed.args:
        return False, ()
    pnpm_token = parsed.args[0].lower()
    if not pnpm_token.startswith("pnpm"):
        return False, ()
    return True, parsed.args[1:]


_FROZEN_PNPM_INSTALL_ARGS: tuple[str, ...] = (
    "install",
    "--frozen-lockfile",
    "--prefer-offline",
    "--ignore-scripts",
)
_FROZEN_NPM_INSTALL_ARGS: tuple[str, ...] = (
    "ci",
    "--prefer-offline",
    "--no-audit",
    "--no-fund",
    "--ignore-scripts",
)
_FROZEN_YARN_INSTALL_ARGS: tuple[str, ...] = ("install", "--frozen-lockfile", "--ignore-scripts")
_IMMUTABLE_YARN_INSTALL_ARGS: tuple[str, ...] = ("install", "--immutable", "--mode=skip-build")


def is_frozen_node_dependency_install(parsed: ParsedCommand) -> bool:
    """Reconoce, por argv exacto, la instalacion determinista y preferentemente offline de Node.

    Invariante de seguridad: solo estos cuatro argv exactos califican. Agregar un paquete,
    instalar sin `--frozen-lockfile`/`ci`/`--immutable`, o cualquier otra variante sigue sin
    categoria de bajo riesgo (`package_manager_category` la sigue clasificando riesgo medio).

    Nunca corre scripts de ciclo de vida de las dependencias (`preinstall`/`postinstall`/`prepare`):
    el lockfile del worktree puede traer un paquete que agrego el developer (un LLM puede alucinar un
    nombre que un atacante registro), y esta instalacion se aprueba sin humano. Por eso todos llevan
    `--ignore-scripts` (npm https://docs.npmjs.com/cli/commands/npm-ci, pnpm
    https://pnpm.io/cli/install, yarn classic https://classic.yarnpkg.com/en/docs/cli/install) o, en
    Yarn Berry, `--mode=skip-build` (https://yarnpkg.com/cli/install). pnpm solo con la version fijada
    por la toolchain.
    """
    from local_control_center.projects.toolchain import PNPM_VERSION

    is_pnpm, pnpm_args = _is_corepack_pnpm(parsed)
    if (
        is_pnpm
        and parsed.args[0].lower() == f"pnpm@{PNPM_VERSION}"
        and pnpm_args == _FROZEN_PNPM_INSTALL_ARGS
    ):
        return True
    base = _base_executable(parsed.executable)
    if base == "npm" and parsed.args == _FROZEN_NPM_INSTALL_ARGS:
        return True
    return bool(base == "yarn" and parsed.args in (_FROZEN_YARN_INSTALL_ARGS, _IMMUTABLE_YARN_INSTALL_ARGS))


def _is_node_runner(parsed: ParsedCommand) -> tuple[bool, tuple[str, ...]]:
    """Reconoce pnpm, npm, yarn y `corepack pnpm`, que corren scripts de `package.json`.

    Los tres comparten la misma superficie de riesgo, asi que comparten allowlist y guarda de
    hooks de ciclo de vida: tener una rama aparte por gestor haria que la guarda se olvidara en
    una de ellas.
    """
    if _base_executable(parsed.executable) in NODE_RUN_EXECUTABLES:
        return True, parsed.args
    return _is_corepack_pnpm(parsed)


def node_script_category(parsed: ParsedCommand) -> str | None:
    """Clasifica un ``<pnpm|npm|yarn> run <script>`` (o ``corepack pnpm run ...``) por su script.

    Mapea scripts allowlisted a su categoria (test/build/lint/typecheck/quality/security_scan).
    Invariante de seguridad: cualquier script con nombre de hook de ciclo de vida (install,
    prepare, prefijos ``pre``/``post``, etc.) se marca ``package_script_hook`` porque puede
    ejecutar codigo arbitrario; los scripts no reconocidos caen a ``package_script``.
    """
    is_node, args = _is_node_runner(parsed)
    if not is_node or len(args) < 2 or args[0] != "run":
        return None
    script = args[1]
    # Sin normalizar, `npm run Preinstall` evadia la categoria dedicada que este invariante
    # promete: el hook quedaba como script generico en vez de marcarse como hook.
    lowered = script.lower()
    if lowered in _LOWERED_PACKAGE_SCRIPT_HOOKS or lowered.startswith(("pre", "post")):
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
    if _base_executable(executable) == "corepack":
        is_pnpm, pnpm_args = _is_corepack_pnpm(parsed)
        if is_pnpm and pnpm_args == _FROZEN_PNPM_INSTALL_ARGS:
            # Instalacion determinista y offline: la clasifica low_risk_shell_category, no esto.
            return None
        if is_pnpm and pnpm_args and pnpm_args[0] in PACKAGE_MANAGER_INSTALL_VERBS:
            return "install"
    if (
        executable in {"uv", "uv.exe", "pip", "pip.exe", "winget", "choco"}
        and args
        and args[0] in PACKAGE_MANAGER_INSTALL_VERBS
    ):
        return "install"
    if (
        _base_executable(executable) in TOOLCHAIN_INSTALL_EXECUTABLES
        and args
        and args[0]
        in {
            *PACKAGE_MANAGER_INSTALL_VERBS,
            "get",
        }
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
    if is_frozen_node_dependency_install(parsed):
        return "dependency_install_frozen"
    node_category = node_script_category(parsed)
    if node_category in {"test", "build", "lint", "typecheck", "quality", "security_scan"}:
        return node_category
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
        and parsed.args[:2] in {("-m", "pytest"), ("-m", "unittest")}
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
    base = _base_executable(parsed.executable)
    toolchain = {
        **dict.fromkeys(MAVEN_EXECUTABLES, (ALLOWED_MAVEN_GOALS, ALLOWED_MAVEN_FLAGS, frozenset())),
        **dict.fromkeys(GRADLE_EXECUTABLES, (ALLOWED_GRADLE_TASKS, ALLOWED_GRADLE_FLAGS, frozenset())),
        "go": (ALLOWED_GO_VERBS, ALLOWED_GO_FLAGS, ALLOWED_GO_OPERANDS),
        "cargo": (ALLOWED_CARGO_VERBS, ALLOWED_CARGO_FLAGS, frozenset()),
    }.get(base)
    if toolchain is not None:
        goals, flags, operands = toolchain
        category = _toolchain_invocation_category(
            allowed_goals=goals,
            allowed_flags=flags,
            args=parsed.args,
            allowed_operands=operands,
        )
        if category:
            return category
    if base in TOOLCHAIN_VERSION_EXECUTABLES and parsed.args in {("--version",), ("-version",), ("version",)}:
        return "interpreter_version"
    if parsed.executable in READ_ONLY_EXECUTABLES:
        return "read_only"
    if parsed.executable == "git" and parsed.args[:1] in {("status",), ("diff",)}:
        return "read_only"
    return None
