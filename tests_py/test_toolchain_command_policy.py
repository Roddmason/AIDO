"""Tests del borde de seguridad para las toolchains que no son pnpm.

La allowlist sólo conocía pnpm y uv porque espejaba el `package.json` de AIDO. Planear comandos de
Maven, Gradle, Go o Rust sin extenderla los dejaría sin categoría, y el motor de política los
elevaría a aprobación manual: la validación por proyecto no correría nunca sola.

El test que importa no es la lista de verbos, es el acoplamiento: **todo comando que el planificador
pueda emitir tiene que ser clasificable**, o el plan promete algo que la política no deja ejecutar.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_control_center.projects.toolchain import plan_workspace_commands
from local_control_center.security_policy.permissions import low_risk_shell_category, parse_command

POM = '<?xml version="1.0"?><project><artifactId>demo</artifactId></project>'

LOW_RISK_CATEGORIES = {"test", "build", "lint", "typecheck", "quality", "security_scan"}


def _category(command: str) -> str | None:
    parsed = parse_command(command)
    assert parsed is not None, command
    return low_risk_shell_category(parsed)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("mvn -B test", "test"),
        ("mvnw.cmd -B test", "test"),
        ("mvn -B verify -DskipTests", "build"),
        ("gradlew.bat --console=plain test", "test"),
        ("gradle --console=plain assemble", "build"),
        ("go test ./...", "test"),
        ("go build ./...", "build"),
        ("go vet ./...", "lint"),
        ("cargo test", "test"),
        ("cargo build", "build"),
        ("cargo clippy", "lint"),
        ("npm run test", "test"),
        ("yarn run build", "build"),
    ],
)
def test_project_toolchain_verbs_are_low_risk(command: str, expected: str) -> None:
    """Verbos estándar de validación: leen y compilan el proyecto, no publican nada."""
    assert _category(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "mvn -B deploy",
        "mvn -B release:perform",
        "gradlew publish",
        "gradle uploadArchives",
        "cargo publish",
        "cargo install ripgrep",
        "go install example.com/tool@latest",
        "npm run postinstall",
        "yarn run prepare",
        "npm publish",
        "mvn",
        "cargo",
    ],
)
def test_publishing_and_install_verbs_are_never_low_risk(command: str) -> None:
    """Publicar o instalar sale de la máquina o trae código de terceros: nunca es automático."""
    assert _category(command) not in LOW_RISK_CATEGORIES, command


@pytest.mark.parametrize("command", ["mvn --version", "gradle --version", "go version", "cargo --version"])
def test_version_probes_stay_classified_as_version_checks(command: str) -> None:
    """El sondeo de versión es cómo se comprueba que la toolchain está instalada en el host."""
    assert _category(command) == "interpreter_version"


def _workspaces(tmp_path: Path) -> list[Path]:
    maven = tmp_path / "maven"
    maven.mkdir()
    (maven / "pom.xml").write_text(POM, encoding="utf-8")

    maven_wrapped = tmp_path / "maven-wrapped"
    maven_wrapped.mkdir()
    (maven_wrapped / "pom.xml").write_text(POM, encoding="utf-8")
    (maven_wrapped / "mvnw.cmd").write_text("@echo off\n", encoding="utf-8")

    gradle = tmp_path / "gradle"
    gradle.mkdir()
    (gradle / "build.gradle").write_text('rootProject.name = "demo"\n', encoding="utf-8")
    (gradle / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")

    gradle_global = tmp_path / "gradle-global"
    gradle_global.mkdir()
    (gradle_global / "build.gradle.kts").write_text('rootProject.name = "demo"\n', encoding="utf-8")

    go_project = tmp_path / "go"
    go_project.mkdir()
    (go_project / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")

    rust = tmp_path / "rust"
    rust.mkdir()
    (rust / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")

    python_project = tmp_path / "python"
    python_project.mkdir()
    (python_project / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (python_project / "tests").mkdir()

    scripts = {"test": "x", "test:web": "x", "build": "x", "lint": "x", "typecheck": "x"}
    for name, lockfile in (("node-pnpm", "pnpm-lock.yaml"), ("node-npm", "package-lock.json")):
        node = tmp_path / name
        node.mkdir()
        (node / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
        (node / lockfile).write_text("{}", encoding="utf-8")

    return sorted(tmp_path.iterdir())


def test_every_planned_command_is_executable_under_policy(tmp_path: Path) -> None:
    """Invariante estructural: el planificador no puede emitir algo que la política no clasifique.

    Sin esto, agregar una toolchain nueva al planificador la deja muerta en aprobación manual y el
    síntoma aparece recién en producción, como un comando que nunca corre.
    """
    unclassified: list[str] = []
    planned = 0
    for workspace in _workspaces(tmp_path):
        for command in plan_workspace_commands(workspace):
            planned += 1
            if low_risk_shell_category(parse_command(command.policy_command)) not in LOW_RISK_CATEGORIES:
                unclassified.append(f"{workspace.name}: {command.policy_command}")

    assert planned >= 14, planned
    assert unclassified == []


@pytest.mark.parametrize(
    "command",
    [
        "corepack pnpm@10.24.0 run test:web",
        "corepack.cmd pnpm@10.24.0 run test:web",
        "corepack.exe pnpm@10.24.0 run test:web",
    ],
)
def test_a_resolved_corepack_binary_is_still_recognized(command: str) -> None:
    """El runner de QA muestra el argv resuelto (`corepack.cmd`), no el nombre pelado.

    Con la comparación estricta anterior, resolver el binario dejaba el comando sin categoría y la
    suite de QA quedaba esperando aprobación manual para siempre.
    """
    assert _category(command) == "test"


@pytest.mark.parametrize(
    "command",
    [
        # Banderas documentadas de estas herramientas que ejecutan un programa arbitrario.
        "go test -exec ./evil.sh ./...",
        "go test -toolexec ./evil.sh ./...",
        "go build -o C:/Users/Public/evil.exe .",
        "cargo test --config target.'cfg(all())'.runner='sh -c id'",
        "gradle --init-script=evil.gradle test",
        "gradle -I evil.gradle test",
        "gradlew.bat --init-script evil.gradle test",
        "mvn -Dmaven.ext.class.path=C:/evil/ext.jar test",
        "mvn -Dmaven.repo.local=C:/evil/repo test",
        "mvn -Pevilprofile test",
        # El valor de una bandera de dos tokens no puede colarse como si fuera un objetivo.
        "mvn -f test test",
        "mvn -s test verify",
        "gradle -I test test",
        # Reformatea archivos en el lugar: no es una lectura.
        "cargo fmt",
        "cargo clippy --fix --allow-dirty",
    ],
)
def test_flags_that_inject_execution_are_never_low_risk(command: str) -> None:
    """Validar sólo los objetivos e ignorar las banderas deja pasar ejecución arbitraria.

    `-exec`/`-toolexec` de Go envuelven la ejecución del binario de test; `--init-script` de Gradle
    corre Groovy antes del build; `-Dmaven.ext.class.path` inyecta una extensión de Maven. Todas son
    capacidades documentadas de la herramienta, así que la defensa tiene que ser allowlist de
    banderas, no blocklist: un token no reconocido invalida el comando completo.
    """
    assert _category(command) not in LOW_RISK_CATEGORIES, command


@pytest.mark.parametrize(
    "command",
    [
        "C:/Temp/attacker/gradlew.bat test",
        "/tmp/attacker/gradlew test",
        r"..\..\evil\gradlew.bat test",
        "C:/Temp/attacker/mvnw.cmd -B test",
        "C:/Temp/attacker/corepack.cmd pnpm@10.24.0 run test",
    ],
)
def test_an_executable_from_another_directory_is_not_recognized_by_basename(command: str) -> None:
    """Un ejecutable con ruta no puede pasar sólo porque su nombre de archivo coincida.

    Los llamadores legítimos entregan el nombre pelado (`corepack.cmd`) porque el runner ya reduce
    el argv a su basename antes de clasificar. Aceptar además una ruta arbitraria convertía el
    nombre del binario en la única credencial.
    """
    assert _category(command) not in LOW_RISK_CATEGORIES, command


@pytest.mark.parametrize("script", ["Preinstall", "PostInstall", "PREPARE"])
def test_the_lifecycle_hook_guard_ignores_capitalisation(script: str) -> None:
    """La guarda de hooks documenta que un hook se marca como tal; mayúsculas no pueden evadirla."""
    from local_control_center.security_policy.permissions import node_script_category

    assert node_script_category(parse_command(f"npm run {script}")) == "package_script_hook"


def test_every_planned_executable_passes_the_sandbox_boundary(tmp_path: Path) -> None:
    """Decidir `allow` en la política y morir en el sandbox es peor que negar de entrada.

    Son DOS allowlists distintas: `security_policy.permissions` clasifica el riesgo y
    `sandbox.ALLOWED_EXECUTABLES` autoriza el spawn. Agregar toolchains sólo a la primera dejaba la
    validación por proyecto decidiendo que sí y despuès fallando con "Executable is not allowlisted
    for restricted subprocess" — el mismo "saltado parece verificado" que este trabajo vino a
    eliminar. Este test ata las dos capas.
    """
    from local_control_center.security_policy.sandbox import validate_restricted_process

    rejected: list[str] = []
    checked = 0
    for workspace in _workspaces(tmp_path):
        for command in plan_workspace_commands(workspace):
            checked += 1
            argv = [command.executable, *command.policy_command.split()[1:]]
            error = validate_restricted_process(argv, None, None)
            if error:
                rejected.append(f"{command.policy_command}: {error}")

    assert checked >= 14, checked
    assert rejected == []
