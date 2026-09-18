"""Tests del plan de validación por proyecto.

Antes, todo proyecto se validaba con `corepack pnpm run <script>`. Un Spring Boot con Maven no
fallaba: no corría nada y el resultado salía como saltado, que es peor que un error porque parece
que se verificó. Acá se fija que el plan sale de los manifiestos del propio proyecto y que el
wrapper versionado del repo le gana al binario global.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

from local_control_center.projects.toolchain import (
    ToolchainCommand,
    detected_toolchains,
    missing_toolchains,
    node_package_manager,
    plan_workspace_commands,
)

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <artifactId>demo-service</artifactId>
  <name>Demo Service</name>
</project>
"""


def _purposes(commands: list[ToolchainCommand]) -> dict[str, ToolchainCommand]:
    return {command.purpose: command for command in commands}


def test_a_maven_project_is_validated_with_maven_not_with_pnpm(tmp_path: Path) -> None:
    """El caso que motivó el módulo: sin package.json no se validaba nada."""
    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")

    commands = plan_workspace_commands(tmp_path)

    assert commands, "un proyecto Maven debe producir comandos de validación"
    assert {command.toolchain for command in commands} == {"java-maven"}
    by_purpose = _purposes(commands)
    assert by_purpose["test"].policy_command == "mvn -B test"
    assert "pnpm" not in " ".join(command.policy_command for command in commands)


def test_the_repo_wrapper_wins_over_the_global_binary(tmp_path: Path) -> None:
    """`mvnw`/`gradlew` fijan la versión que el proyecto usa; el binario global puede ser otra."""
    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    wrapper = tmp_path / "mvnw.cmd"
    wrapper.write_text("@echo off\n", encoding="utf-8")

    command = _purposes(plan_workspace_commands(tmp_path))["test"]

    assert command.argv[0] == str(wrapper)
    assert command.executable == "mvnw.cmd"


def test_gradle_project_uses_its_wrapper_and_plain_console(tmp_path: Path) -> None:
    """Sin `--console=plain` Gradle emite escapes ANSI que ensucian la evidencia capturada."""
    (tmp_path / "build.gradle.kts").write_text('rootProject.name = "demo"\n', encoding="utf-8")
    wrapper = tmp_path / "gradlew.bat"
    wrapper.write_text("@echo off\n", encoding="utf-8")

    commands = plan_workspace_commands(tmp_path)
    by_purpose = _purposes(commands)

    assert by_purpose["test"].argv[0] == str(wrapper)
    assert "--console=plain" in by_purpose["test"].argv
    assert by_purpose["build"].policy_command == "gradlew.bat --console=plain assemble"


def test_the_node_package_manager_comes_from_the_lockfile(tmp_path: Path) -> None:
    """Correr pnpm en un repo con package-lock.json instala un árbol distinto al que fijó el proyecto."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"test": "jest", "build": "webpack"}}), encoding="utf-8"
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    assert node_package_manager(tmp_path) == "npm"
    by_purpose = _purposes(plan_workspace_commands(tmp_path))
    assert by_purpose["test"].policy_command == "npm run test"
    assert by_purpose["build"].policy_command == "npm run build"


def test_pnpm_stays_the_default_when_its_lockfile_is_present(tmp_path: Path) -> None:
    """El repo propio usa pnpm por corepack: ese camino no puede cambiar."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"test:web": "playwright test"}}), encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    command = _purposes(plan_workspace_commands(tmp_path))["test"]

    assert command.policy_command == "corepack pnpm@10.24.0 run test:web"
    assert command.metadata["packageManager"] == "pnpm"


def test_a_script_that_the_project_does_not_define_is_never_planned(tmp_path: Path) -> None:
    """Planear un script inexistente reproduce el `skipped_with_reason` que había que eliminar."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"dev": "vite"}}), encoding="utf-8"
    )

    assert plan_workspace_commands(tmp_path) == []


def test_a_polyglot_project_plans_every_toolchain_it_actually_has(tmp_path: Path) -> None:
    """Backend Python + frontend Node se valida con los dos, no con el primero que aparezca."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "tests_py").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"build": "vite build"}}), encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    commands = plan_workspace_commands(tmp_path)

    assert {command.toolchain for command in commands} == {"python", "node"}
    assert any(command.policy_command.startswith("uv run pytest") for command in commands)


def test_python_without_a_test_directory_plans_nothing(tmp_path: Path) -> None:
    """Un pytest apuntando a un directorio inexistente falla por configuración, no por el código."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")

    assert plan_workspace_commands(tmp_path) == []


def test_go_and_rust_projects_are_planned_with_their_own_tools(tmp_path: Path) -> None:
    """Cobertura de las dos toolchains restantes que `discovery` ya detectaba sin que nadie las usara."""
    go_root = tmp_path / "go-project"
    go_root.mkdir()
    (go_root / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    rust_root = tmp_path / "rust-project"
    rust_root.mkdir()
    (rust_root / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")

    assert _purposes(plan_workspace_commands(go_root))["test"].policy_command == "go test ./..."
    assert _purposes(plan_workspace_commands(rust_root))["test"].policy_command == "cargo test"


def test_a_missing_toolchain_is_reported_so_the_operator_can_dockerize(tmp_path: Path) -> None:
    """Es la señal que habilita preguntar "¿lo dockerizamos?" en vez de fallar con un error opaco."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    commands = plan_workspace_commands(tmp_path)

    absent = ToolchainCommand(
        purpose="test",
        label="Test",
        argv=["binario-que-no-existe-en-ninguna-parte"],
        policy_command="binario-que-no-existe-en-ninguna-parte",
        toolchain="imaginaria",
        executable="binario-que-no-existe-en-ninguna-parte",
    )

    assert missing_toolchains([absent]) == ["imaginaria"]
    # El plan real sólo reporta faltante lo que de verdad no está instalado en este host.
    assert set(missing_toolchains(commands)) <= {"rust"}


def test_detection_only_reports_toolchains_that_can_actually_be_planned(tmp_path: Path) -> None:
    """`discovery` detecta Terraform, pero no hay plan de validación para él: no se promete de más."""
    (tmp_path / "main.tf").write_text('resource "null_resource" "demo" {}\n', encoding="utf-8")

    assert detected_toolchains(tmp_path) == []
    assert plan_workspace_commands(tmp_path) == []


def test_qa_discovery_uses_the_project_toolchain(tmp_path: Path) -> None:
    """QA descubría comandos sólo para pytest y pnpm: un Spring Boot no tenía nada que correr."""
    from local_control_center.agents.qa_agent import discover_qa_commands

    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    commands = discover_qa_commands(tmp_path)

    assert commands, "un proyecto Maven debe producir comandos de QA"
    joined = " ".join(" ".join(command["argv"]) for command in commands)
    assert "-B" in joined and "test" in joined
    assert all(command["critical"] is True for command in commands)


def test_qa_discovery_keeps_working_for_this_repo(tmp_path: Path) -> None:
    """El camino que ya funcionaba (pytest + pnpm por corepack) no puede cambiar de forma."""
    from local_control_center.agents.qa_agent import discover_qa_commands

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "tests_py").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"test:web": "x", "build": "x"}}), encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    commands = discover_qa_commands(tmp_path)
    flattened = [" ".join(command["argv"]) for command in commands]

    assert any("pytest" in item and "tests_py" in item for item in flattened), flattened
    assert any("pnpm@10.24.0 run test:web" in item for item in flattened), flattened


def test_devops_plans_the_project_toolchain_and_skips_node(tmp_path: Path) -> None:
    """DevOps ya corre los scripts de `package.json`; duplicarlos gastaría el doble por nada."""
    from local_control_center.agents.devops_agent import toolchain_validation_commands

    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"build": "vite build"}}), encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    commands = toolchain_validation_commands(tmp_path)

    assert {command.toolchain for command in commands} == {"java-maven"}


def test_devops_reports_a_missing_toolchain_as_an_actionable_finding(tmp_path: Path) -> None:
    """Sin la toolchain instalada el comando fallaría con un error opaco del sistema operativo.

    El operador necesita saber que la opción es instalarla o contenerizar el proyecto, no leer un
    'no se reconoce como comando interno o externo'.
    """
    from local_control_center.agents.devops_agent import missing_toolchain_findings

    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    findings = missing_toolchain_findings(tmp_path, available=lambda _executable: False)

    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["checkId"] == "toolchain_not_installed"
    assert "java-maven" in finding["message"]
    assert "container" in finding["message"].lower()
    assert finding["evidence"]["toolchain"] == "java-maven"


def test_an_installed_toolchain_produces_no_finding(tmp_path: Path) -> None:
    """No se molesta al operador con algo que ya está resuelto."""
    from local_control_center.agents.devops_agent import missing_toolchain_findings

    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")

    assert missing_toolchain_findings(tmp_path, available=lambda _executable: True) == []


def test_discovery_does_not_walk_into_dependency_directories(tmp_path: Path) -> None:
    """El planificador corre en cada ejecución de QA y DevOps: no puede recorrer node_modules.

    Medido antes del arreglo: 7,9 s sobre el repo de AIDO, porque la detección de Terraform hacía
    `rglob("*.tf")` sobre todo el árbol, incluidos `node_modules`, `.git` y `.venv`.
    """
    from local_control_center.projects.discovery import discover_project_path

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}), encoding="utf-8")
    buried = tmp_path / "node_modules" / "some-package" / "fixtures"
    buried.mkdir(parents=True)
    (buried / "main.tf").write_text("# no es del proyecto\n", encoding="utf-8")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.tf").write_text('resource "null_resource" "a" {}\n', encoding="utf-8")

    discovery = discover_project_path(tmp_path)
    manifests = {str(source["manifest"]) for source in discovery["manifestSources"]}

    assert "infra/main.tf" in manifests, manifests
    assert not any("node_modules" in manifest for manifest in manifests), manifests


def test_discovery_stays_fast_on_a_large_repository() -> None:
    """Ancla de rendimiento sobre el repo real: es el caso que motivó el arreglo."""
    import time

    from local_control_center.projects.discovery import discover_project_path

    started = time.perf_counter()
    discover_project_path(Path(__file__).resolve().parents[1])
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"la detección tardó {elapsed:.1f}s; antes del arreglo eran ~7,9s"
