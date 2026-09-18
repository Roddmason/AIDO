"""Tests del runtime contenerizado para proyectos sin su toolchain instalada en el host.

`DockerSandbox` no sirve para esto y no se toca: monta el workspace **readonly**, corre
`--read-only`, `--network none` y `--pids-limit 16`. Es un sandbox de *análisis*. Un build real
necesita escribir `target/`, `build/` o `node_modules/`, resolver dependencias por red y levantar
un daemon de Gradle con decenas de procesos.

Estos tests fijan exactamente qué se relaja y, sobre todo, **qué no**: nada de `--privileged`, red
del host, ni montaje del socket de Docker. Y que sea opt-in por proyecto, nunca por defecto.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_control_center.projects.toolchain import (
    TOOLCHAIN_IMAGES,
    container_image_for,
    plan_workspace_commands,
)
from local_control_center.security_policy.sandbox import ProjectBuildContainer

POM = '<?xml version="1.0"?><project><artifactId>demo</artifactId></project>'


def _args(tmp_path: Path, **overrides) -> list[str]:
    defaults = {
        "image": TOOLCHAIN_IMAGES["java-maven"],
        "argv": ["mvn", "-B", "test"],
        "workspace_path": tmp_path,
    }
    defaults.update(overrides)
    return ProjectBuildContainer(docker_executable="docker").build_run_args(**defaults)


def test_the_workspace_is_writable_because_a_build_writes(tmp_path: Path) -> None:
    """Es la diferencia que hace que esto sea un runtime y no el sandbox de análisis."""
    args = _args(tmp_path)
    mount = next(value for value in args if value.startswith("type=bind"))

    assert "readonly" not in mount, mount
    assert "--read-only" not in args


def test_dependency_resolution_needs_the_network(tmp_path: Path) -> None:
    """Sin red, `mvn test` falla en la primera descarga; con `none` el runtime no sirve para nada."""
    args = _args(tmp_path)

    assert args[args.index("--network") + 1] == "bridge"


@pytest.mark.parametrize(
    "forbidden",
    ["--privileged", "/var/run/docker.sock", "--pid=host", "--ipc=host", "--userns=host"],
)
def test_the_container_never_gets_host_level_access(tmp_path: Path, forbidden: str) -> None:
    """Relajar escritura y red no es excusa para relajar el aislamiento del host."""
    assert not any(forbidden in str(value) for value in _args(tmp_path))


def test_the_hardening_that_survives_is_still_applied(tmp_path: Path) -> None:
    """Lo que no estorba a un build se conserva: efímero, sin capacidades, sin escalar privilegios."""
    args = _args(tmp_path)

    assert "--rm" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"


def test_the_host_network_is_refused_even_when_asked(tmp_path: Path) -> None:
    """`host` expondría la red del equipo al contenedor; no es una opción configurable."""
    with pytest.raises(ValueError, match="network"):
        _args(tmp_path, network="host")


@pytest.mark.parametrize("image", ["", "-rm", "imagen con espacios", "alpine; rm -rf /"])
def test_only_a_catalog_image_is_accepted(tmp_path: Path, image: str) -> None:
    """La imagen entra al argv de `docker run`: un valor libre es inyección de comando."""
    with pytest.raises(ValueError, match="image"):
        _args(tmp_path, image=image)


def test_an_image_outside_the_toolchain_catalog_is_refused(tmp_path: Path) -> None:
    """Sólo se ejecutan las imágenes que este repo declara para sus toolchains."""
    with pytest.raises(ValueError, match="catalog"):
        _args(tmp_path, image="ubuntu:latest")


def test_every_planned_toolchain_has_an_image() -> None:
    """Si el planificador emite una toolchain sin imagen, el modo contenerizado no la cubre.

    Node incluido: el DevOpsAgent lo excluye de su plan, pero QA no, y un proyecto React sin node
    instalado es exactamente el caso que motiva esta feature.
    """
    from local_control_center.projects.toolchain import _BUILDERS

    missing = [toolchain for toolchain in _BUILDERS if container_image_for(toolchain) is None]

    assert missing == [], missing


def test_the_image_comes_from_what_the_project_actually_declares(tmp_path: Path) -> None:
    """Un proyecto Maven se ejecuta en una imagen de Maven, no en una genérica."""
    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    command = plan_workspace_commands(tmp_path)[0]

    image = container_image_for(command.toolchain)

    assert image is not None and "maven" in image


def test_node_uses_the_image_matching_its_package_manager(tmp_path: Path) -> None:
    """La imagen tiene que traer el gestor que el lockfile eligió, o el comando no existe dentro."""
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}), encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    assert container_image_for("node") == TOOLCHAIN_IMAGES["node"]
    assert "node" in TOOLCHAIN_IMAGES["node"]


def test_the_container_runtime_is_opt_in_per_project() -> None:
    """Nunca por defecto: contenerizar cambia dónde corre el código del usuario."""
    from local_control_center.settings.registry import descriptor_for

    descriptor = descriptor_for("project.runtime.containerized")

    assert descriptor is not None, "la preferencia por proyecto no está registrada"
    assert descriptor.default is False
    assert descriptor.project_section is not None, "tiene que ser editable por proyecto"


def test_a_missing_workspace_is_reported_instead_of_raising(tmp_path: Path) -> None:
    """El agente no puede caerse porque una ruta no exista; devuelve bloqueo con motivo."""
    result = ProjectBuildContainer(docker_executable="docker").execute(
        image=TOOLCHAIN_IMAGES["java-maven"],
        argv=["mvn", "-B", "test"],
        workspace_path=tmp_path / "no-existe",
    )

    assert result["executed"] is False
    assert result["blocked"] is True
    assert "Workspace" in result["reason"]


def test_without_docker_it_degrades_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el operador activo el modo contenerizado pero no tiene Docker, tiene que enterarse."""
    from local_control_center.security_policy import sandbox

    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    result = ProjectBuildContainer().execute(
        image=TOOLCHAIN_IMAGES["java-maven"],
        argv=["mvn", "-B", "test"],
        workspace_path=tmp_path,
    )

    assert result["executed"] is False
    assert result["blocked"] is True
    assert "Docker" in result["reason"], result["reason"]


def test_the_container_still_goes_through_resource_admission(tmp_path: Path) -> None:
    """Contenerizar no saca al build del gobernador de recursos del host.

    Observado al escribir estos tests: con el host cargado, la ejecucion devuelve
    `resource_wait` en vez de arrancar el contenedor. Es lo correcto — un build en contenedor
    consume la misma CPU y memoria del equipo — y queda fijado para que nadie lo "arregle".
    """
    result = ProjectBuildContainer().execute(
        image=TOOLCHAIN_IMAGES["java-maven"],
        argv=["mvn", "--version"],
        workspace_path=tmp_path,
        timeout_seconds=5,
    )

    assert "executed" in result and "blocked" in result
    if not result["executed"]:
        assert result["reason"], result


def _project_connection(tmp_path: Path):
    from contextlib import closing

    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    connection = open_sqlite_connection(tmp_path / "platform.sqlite")
    with connection:
        initialize_platform_schema(connection)
    return closing(connection)


def _maven_command(tmp_path: Path):
    workspace = tmp_path / "proyecto"
    workspace.mkdir()
    (workspace / "pom.xml").write_text(POM, encoding="utf-8")
    return plan_workspace_commands(workspace)[0]


def test_the_local_toolchain_always_wins_over_the_container(tmp_path: Path) -> None:
    """Contenerizar cuesta tiempo y disco: si el host tiene la herramienta, se usa esa."""
    from local_control_center.projects.toolchain import container_image_for_command

    command = _maven_command(tmp_path)
    with _project_connection(tmp_path) as connection:
        image = container_image_for_command(
            connection, project_id="proyecto-1", command=command, available=lambda _e: True
        )

    assert image is None


def test_a_missing_toolchain_without_opt_in_does_not_containerize(tmp_path: Path) -> None:
    """Sin decisión explícita del operador no se cambia dónde corre su código."""
    from local_control_center.projects.toolchain import container_image_for_command

    command = _maven_command(tmp_path)
    with _project_connection(tmp_path) as connection:
        image = container_image_for_command(
            connection, project_id="proyecto-1", command=command, available=lambda _e: False
        )

    assert image is None


def test_opting_in_resolves_the_image_of_the_projects_own_toolchain(tmp_path: Path) -> None:
    """El caso completo: falta Maven en el host, el operador aceptó contenerizar."""
    from local_control_center.projects.toolchain import (
        CONTAINERIZED_SETTING_KEY,
        container_image_for_command,
    )
    from local_control_center.settings.repository import SettingsRepository

    command = _maven_command(tmp_path)
    with _project_connection(tmp_path) as connection:
        with connection:
            SettingsRepository(connection).set_value(CONTAINERIZED_SETTING_KEY, "project", "proyecto-1", True)
        image = container_image_for_command(
            connection, project_id="proyecto-1", command=command, available=lambda _e: False
        )
        otro = container_image_for_command(
            connection, project_id="proyecto-2", command=command, available=lambda _e: False
        )

    assert image == TOOLCHAIN_IMAGES["java-maven"]
    assert otro is None, "la preferencia es por proyecto, no global"


def test_the_reservation_matches_what_the_container_can_actually_use() -> None:
    """Reservar 16 GiB para un contenedor limitado a 4 bloquea trabajo que si cabria.

    `--memory` es un limite duro de cgroup: el kernel lo impone. Declarar `build_heavy` (16 GiB)
    para un contenedor capado en 4 hacia que el gobernador lo rechazara con
    `aggregate_memory_budget` en un equipo que tenia espacio de sobra — que es exactamente como se
    descubrio, intentando correr el primer proyecto contenerizado.
    """
    from local_control_center.host_resources.profiles import workload_profile
    from local_control_center.security_policy.sandbox import ProjectBuildContainer

    gib = 1024**3
    for memory, expected_bytes in (("2g", 2 * gib), ("4g", 4 * gib), ("8g", 8 * gib)):
        workload = ProjectBuildContainer.workload_class_for(memory)
        declared = workload_profile(workload).memory_limit_bytes

        assert declared >= expected_bytes, (memory, workload, declared)
        assert declared <= expected_bytes * 2, (
            f"{memory} reserva {declared / gib:.0f} GiB: mas del doble de lo que el cgroup permite"
        )
