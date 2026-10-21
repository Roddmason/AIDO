"""Contrato del registro ordenado de fases de ``shared/migrations.py``.

Verifica que las versiones registradas sean contiguas desde 1 hasta ``CURRENT_SCHEMA_VERSION``, que cada
una resuelva a un ``init_phaseN_schema`` público y que el guard delegado al decorador sea idempotente.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from local_control_center.shared import migrations


def test_registered_versions_are_contiguous_and_end_at_current_schema_version() -> None:
    versions = migrations.registered_schema_versions()

    assert versions == tuple(range(1, migrations.CURRENT_SCHEMA_VERSION + 1))
    assert versions[-1] == migrations.CURRENT_SCHEMA_VERSION


def test_every_registered_version_resolves_to_its_public_initializer() -> None:
    for version in migrations.registered_schema_versions():
        expected = "init_base_schema" if version == 1 else f"init_phase{version}_schema"

        assert migrations._PHASE_REGISTRY[version] == expected
        assert callable(getattr(migrations, expected))


def test_skip_if_applied_returns_without_running_phase_body() -> None:
    calls: list[int] = []

    @migrations.migration(10_000, skip_if_applied=True)
    def init_phase_probe_schema(connection: sqlite3.Connection) -> None:
        calls.append(1)

    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
            )

            init_phase_probe_schema(connection)
            connection.execute("INSERT INTO schema_migrations VALUES (10000, 'now')")
            init_phase_probe_schema(connection)

        assert calls == [1]
    finally:
        migrations._PHASE_REGISTRY.pop(10_000, None)
