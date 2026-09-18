"""Tests de la configuración que AIDO se asigna sola al crear un proyecto.

Hoy un proyecto nace sin puerta de calidad: `project.quality.gateCommands` es `[]` por defecto, así
que el operador tiene que escribir a mano los comandos que ya se pueden deducir de los manifiestos
del repo. La detección existía (`projects/discovery.py`) pero su resultado era efímero y, peor, lo
que llegaba al alta venía del **cliente** — dato no confiable — en vez de calcularlo el servidor.

El invariante que estos tests protegen no es "auto-asignar", es **no pisar nunca al operador**: un
valor que la persona puso gana siempre, y se puede distinguir de uno que puso la máquina.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.projects.auto_configuration import (
    apply_project_auto_configuration,
    derive_project_settings,
)
from local_control_center.settings.repository import SettingsRepository
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

POM = '<?xml version="1.0"?><project><artifactId>demo</artifactId></project>'
PROJECT = "project-demo"


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
        yield handle


def _maven(tmp_path: Path) -> Path:
    workspace = tmp_path / "maven-project"
    workspace.mkdir()
    (workspace / "pom.xml").write_text(POM, encoding="utf-8")
    return workspace


def test_a_maven_project_gets_its_own_gate_commands(tmp_path: Path) -> None:
    """Lo que el proyecto declara en su pom es lo que debe validarlo, sin que nadie lo escriba."""
    derived = derive_project_settings(_maven(tmp_path))

    commands = derived["project.quality.gateCommands"]
    assert commands, derived
    assert all(command.startswith("mvn") for command in commands), commands


def test_a_project_without_a_recognised_toolchain_derives_nothing(tmp_path: Path) -> None:
    """Inventar una puerta de calidad para algo que no se reconoce es peor que no poner ninguna."""
    empty = tmp_path / "vacio"
    empty.mkdir()
    (empty / "README.md").write_text("# nada", encoding="utf-8")

    assert derive_project_settings(empty) == {}


def test_a_polyglot_project_gets_every_toolchain_it_has(tmp_path: Path) -> None:
    """Un backend Python con frontend Node se valida con los dos, no con el primero detectado."""
    workspace = tmp_path / "poliglota"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (workspace / "tests").mkdir()
    (workspace / "package.json").write_text(
        json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8"
    )
    (workspace / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    commands = derive_project_settings(workspace)["project.quality.gateCommands"]

    assert any("pytest" in command for command in commands), commands
    assert any("pnpm" in command for command in commands), commands


def test_applying_writes_the_settings_with_automatic_provenance(tmp_path: Path, connection) -> None:
    """Se escribe marcado como automático: sin procedencia no se puede volver a detectar sin pisar."""
    result = apply_project_auto_configuration(connection, project_id=PROJECT, project_path=_maven(tmp_path))

    assert "project.quality.gateCommands" in result["assigned"], result
    stored = resolve_setting_value(
        connection=connection, key="project.quality.gateCommands", project_id=PROJECT
    )
    assert stored and all(command.startswith("mvn") for command in stored)
    repository = SettingsRepository(connection)
    assert repository.get_assigned_by("project.quality.gateCommands", "project", PROJECT) == "auto"


def test_a_value_the_operator_set_is_never_overwritten(tmp_path: Path, connection) -> None:
    """El invariante que importa: la persona gana siempre, incluso si su valor parece peor."""
    repository = SettingsRepository(connection)
    with connection:
        repository.set_value("project.quality.gateCommands", "project", PROJECT, ["mi comando propio"])

    result = apply_project_auto_configuration(connection, project_id=PROJECT, project_path=_maven(tmp_path))

    assert result["assigned"] == {}, result
    assert result["preserved"] == ["project.quality.gateCommands"], result
    assert resolve_setting_value(
        connection=connection, key="project.quality.gateCommands", project_id=PROJECT
    ) == ["mi comando propio"]


def test_re_detecting_may_replace_its_own_previous_guess(tmp_path: Path, connection) -> None:
    """Lo que puso la máquina sí se puede corregir: el proyecto cambia de toolchain con el tiempo."""
    workspace = _maven(tmp_path)
    apply_project_auto_configuration(connection, project_id=PROJECT, project_path=workspace)
    (workspace / "pom.xml").unlink()
    (workspace / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")

    result = apply_project_auto_configuration(connection, project_id=PROJECT, project_path=workspace)

    stored = resolve_setting_value(
        connection=connection, key="project.quality.gateCommands", project_id=PROJECT
    )
    assert all(command.startswith("cargo") for command in stored), stored
    assert result["preserved"] == []


def test_an_operator_write_always_claims_its_own_provenance(connection) -> None:
    """Cualquier escritura del endpoint es del operador, aunque antes hubiera un valor automático."""
    repository = SettingsRepository(connection)
    with connection:
        repository.set_value("project.goal.statement", "project", PROJECT, "auto", assigned_by="auto")
        assert repository.get_assigned_by("project.goal.statement", "project", PROJECT) == "auto"
        repository.set_value("project.goal.statement", "project", PROJECT, "mío")

    assert repository.get_assigned_by("project.goal.statement", "project", PROJECT) == "operator"


def test_settings_written_before_this_feature_count_as_the_operators(connection) -> None:
    """Una fila existente sin marca es del operador: asumir lo contrario la haría pisable."""
    with connection:
        connection.execute(
            "INSERT INTO settings_value (key, scope, scope_id, value_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("project.goal.statement", "project", PROJECT, '"heredado"', "2026-01-01T00:00:00.000Z"),
        )

    assert (
        SettingsRepository(connection).get_assigned_by("project.goal.statement", "project", PROJECT)
        == "operator"
    )


def test_creating_a_project_assigns_its_gate_commands(tmp_path: Path, connection) -> None:
    """El cableado: sin esto el modulo seria codigo muerto y el proyecto seguiria naciendo vacio."""
    from local_control_center.projects.commands import create_project
    from local_control_center.projects.repository import ProjectsRepository
    from local_control_center.shared.event_bus import EventBus

    workspace = _maven(tmp_path)
    with connection:
        result = create_project(
            ProjectsRepository(connection),
            EventBus(connection),
            cwd=tmp_path,
            body={"name": "demo", "path": str(workspace), "createDirectory": False},
        )

    project_id = result["project"]["id"]
    commands = resolve_setting_value(
        connection=connection, key="project.quality.gateCommands", project_id=project_id
    )
    assert commands and all(command.startswith("mvn") for command in commands), commands
    assert result["autoConfiguration"]["assigned"], result["autoConfiguration"]


def test_attaching_an_existing_project_does_not_reassign(tmp_path: Path, connection) -> None:
    """Adjuntar dos veces la misma ruta no puede volver a proponer configuración ya resuelta."""
    from local_control_center.projects.commands import create_project
    from local_control_center.projects.repository import ProjectsRepository
    from local_control_center.shared.event_bus import EventBus

    workspace = _maven(tmp_path)
    body = {"name": "demo", "path": str(workspace), "createDirectory": False}
    repository = ProjectsRepository(connection)
    with connection:
        create_project(repository, EventBus(connection), cwd=tmp_path, body=body)
        repeated = create_project(repository, EventBus(connection), cwd=tmp_path, body=body)

    assert repeated["project"]["id"]
    assert repeated["autoConfiguration"]["assigned"] == {}, repeated["autoConfiguration"]


def test_what_was_auto_assigned_is_auditable(tmp_path: Path, connection) -> None:
    """Configurar por su cuenta sin dejar traza le quita al operador la posibilidad de revisarlo."""
    from local_control_center.projects.commands import create_project
    from local_control_center.projects.repository import ProjectsRepository
    from local_control_center.shared.event_bus import EventBus

    with connection:
        create_project(
            ProjectsRepository(connection),
            EventBus(connection),
            cwd=tmp_path,
            body={"name": "demo", "path": str(_maven(tmp_path)), "createDirectory": False},
        )

    rows = connection.execute(
        "SELECT action, actor, payload FROM audit_events WHERE action = 'project.auto_configured'"
    ).fetchall()

    assert len(rows) == 1, rows
    assert rows[0]["actor"] == "system"
    assert "gateCommands" in str(rows[0]["payload"])


def test_the_resolved_setting_says_who_assigned_it(tmp_path: Path, connection) -> None:
    """Sin verlo, la auto-asignación es magia: el operador no puede revisar lo que no distingue.

    Es el patrón de VS Code, que marca visualmente lo que difiere del default. Acá el dato que hace
    falta es distinto — no "modificado" sino **quién** lo puso — y va aparte de `origin`, que ya
    significa de qué ámbito salió el valor.
    """
    from local_control_center.settings.resolver import resolve_settings

    apply_project_auto_configuration(connection, project_id=PROJECT, project_path=_maven(tmp_path))
    with connection:
        SettingsRepository(connection).set_value("project.goal.statement", "project", PROJECT, "objetivo mío")

    resolved = {
        setting["key"]: setting
        for setting in resolve_settings(connection=connection, project_id=PROJECT)["project"]
    }

    automatic = resolved["project.quality.gateCommands"]
    assert automatic["assignedBy"] == "auto", automatic
    assert automatic["origin"] == "project", "`origin` sigue siendo el ámbito, no quién lo puso"

    manual = resolved["project.goal.statement"]
    assert manual["assignedBy"] == "operator", manual


def test_an_inherited_value_is_never_credited_to_the_detection(connection) -> None:
    """Un valor heredado del ámbito general no lo asignó la detección de ESTE proyecto."""
    from local_control_center.settings.resolver import resolve_settings

    with connection:
        SettingsRepository(connection).set_value("project.goal.statement", "general", None, "objetivo global")

    resolved = {
        setting["key"]: setting
        for setting in resolve_settings(connection=connection, project_id=PROJECT)["project"]
    }
    heredado = resolved["project.goal.statement"]

    assert heredado["inherited"] is True
    assert heredado["assignedBy"] == "operator"
