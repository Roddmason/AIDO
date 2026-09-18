"""Tests de la configuración de ejecución que un hilo recuerda entre mensajes.

Hoy un hilo no tiene configuración propia: `seal_operator_cost_decision` resuelve `teamMode` como
`metadata del mensaje > setting del proyecto`. Si el operador elige "critical" para una ejecución,
el mensaje siguiente del **mismo hilo** vuelve al default del proyecto y tiene que volver a
elegirlo. Trabajar en un hilo es trabajar en un contexto; repetir su configuración en cada mensaje
es fricción pura.

Se agrega un nivel en el medio: `mensaje > hilo > proyecto > general > default`. La elección se
recuerda en el propio hilo, así que un hilo nuevo sigue naciendo con lo del proyecto.

`forceLocal` queda deliberadamente fuera: es un control de privacidad del proyecto y un nivel más
fino jamás puede relajarlo.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.product_loop.metadata import seal_operator_cost_decision
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

PROJECT = "project-hilo"


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
            handle.execute(
                "INSERT INTO projects (id, name, path, template_id, source, status, metadata, "
                "created_at, updated_at) VALUES (?, 'demo', '/tmp/demo', 'other', 'api', 'active', "
                "'{}', '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')",
                (PROJECT,),
            )
        yield handle


def _thread(connection) -> dict:
    with connection:
        return ThreadsRepository(connection).create_thread(
            project_id=PROJECT, owner_type="loop", owner_id="loop-a", title="Hilo"
        )


def test_the_thread_remembers_the_mode_the_operator_chose(connection) -> None:
    """Elegir el modo una vez en el hilo alcanza; repetirlo en cada mensaje es fricción."""
    thread = _thread(connection)

    with connection:
        first = seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={"teamMode": "critical"}, thread_id=thread["id"]
        )
        second = seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={}, thread_id=thread["id"]
        )

    assert first["teamMode"] == "critical"
    assert second["teamMode"] == "critical", "el hilo olvido lo que el operador acababa de elegir"


def test_a_new_message_can_still_change_its_mind(connection) -> None:
    """Recordar no puede volverse imponer: el mensaje sigue mandando sobre el hilo."""
    thread = _thread(connection)

    with connection:
        seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={"teamMode": "critical"}, thread_id=thread["id"]
        )
        changed = seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={"teamMode": "economy"}, thread_id=thread["id"]
        )
        later = seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={}, thread_id=thread["id"]
        )

    assert changed["teamMode"] == "economy"
    assert later["teamMode"] == "economy", "el hilo tiene que recordar el ULTIMO elegido"


def test_a_brand_new_thread_starts_from_the_project(connection) -> None:
    """Lo que se recuerda es del hilo, no global: un hilo nuevo nace con lo del proyecto."""
    with connection:
        SettingsRepository(connection).set_value("project.loop.teamMode", "project", PROJECT, "maximum")
    recordado = _thread(connection)
    nuevo = _thread(connection)

    with connection:
        seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={"teamMode": "economy"}, thread_id=recordado["id"]
        )
        limpio = seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={}, thread_id=nuevo["id"]
        )

    assert limpio["teamMode"] == "maximum"


def test_without_a_thread_the_behaviour_is_exactly_what_it_was(connection) -> None:
    """Los llamadores que no son de hilo no pueden cambiar de comportamiento."""
    with connection:
        SettingsRepository(connection).set_value("project.loop.teamMode", "project", PROJECT, "balanced")
        sealed = seal_operator_cost_decision(connection, project_id=PROJECT, metadata={})

    assert sealed["teamMode"] == "balanced"


def test_the_privacy_control_is_never_relaxed_by_the_thread(connection) -> None:
    """`forceLocal` es del proyecto: un nivel más fino no puede aflojar una restricción puesta."""
    thread = _thread(connection)
    with connection:
        SettingsRepository(connection).set_value("project.routing.forceLocal", "project", PROJECT, True)
        sealed = seal_operator_cost_decision(
            connection,
            project_id=PROJECT,
            metadata={"privacyLevel": "cloud_ok", "teamMode": "economy"},
            thread_id=thread["id"],
        )

    assert sealed["privacyLevel"] == "local_private"


def test_an_invalid_mode_is_not_remembered(connection) -> None:
    """Recordar un modo que no existe dejaria el hilo pegado a un valor que nadie puede usar."""
    thread = _thread(connection)
    with connection:
        SettingsRepository(connection).set_value("project.loop.teamMode", "project", PROJECT, "balanced")
        seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={"teamMode": "inventado"}, thread_id=thread["id"]
        )
        later = seal_operator_cost_decision(
            connection, project_id=PROJECT, metadata={}, thread_id=thread["id"]
        )

    assert later["teamMode"] == "balanced"
