"""Local fixture overrides do not change product defaults or escape their disposable scope.

@author Rodrigo Mason
"""

from contextlib import closing

import pytest

from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.profiles import resolve_resource_policy
from local_control_center.settings.repository import UNSET, SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py import operational_acceptance_support as support


@pytest.mark.parametrize("existing", [None, 14])
def test_fixture_margin_is_explicit_scoped_and_restored_on_failure(tmp_path, monkeypatch, existing):
    db = tmp_path / "fixture" / "native.sqlite"
    decoy = tmp_path / "decoy.sqlite"
    for path in (db, decoy):
        with closing(open_sqlite_connection(path)) as connection:
            initialize_platform_schema(connection)
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(decoy))
    with closing(open_sqlite_connection(db)) as connection:
        if existing:
            SettingsRepository(connection).set_value("resources.minFreeMemoryGiB", "general", None, existing)
    with (
        pytest.raises(RuntimeError, match="synthetic interruption"),
        support.low_impact_fixture_policy(db, fixture_root=db.parent) as provenance,
    ):
        assert provenance["database"] == str(db.resolve())
        assert provenance["origin"] == "explicit_test_fixture"
        with closing(open_sqlite_connection(db)) as connection:
            policy = resolve_resource_policy(connection, snapshot=ResourceSnapshot.test_snapshot())
            assert policy.min_free_memory_bytes == 12 * 1024**3
        with closing(open_sqlite_connection(decoy)) as connection:
            assert (
                resolve_resource_policy(
                    connection, snapshot=ResourceSnapshot.test_snapshot()
                ).min_free_memory_bytes
                == 16 * 1024**3
            )
            assert (
                SettingsRepository(connection).get_value("resources.minFreeMemoryGiB", "general", None)
                is UNSET
            )
        raise RuntimeError("synthetic interruption")
    with closing(open_sqlite_connection(db)) as connection:
        restored = SettingsRepository(connection).get_value("resources.minFreeMemoryGiB", "general", None)
        assert restored is UNSET if existing is None else restored == existing


def test_fixture_override_refuses_an_outside_database_before_open(tmp_path):
    outside = tmp_path / "outside.sqlite"
    with (
        pytest.raises(ValueError, match="fixture"),
        support.low_impact_fixture_policy(outside, fixture_root=tmp_path / "fixture"),
    ):
        pytest.fail("Outside fixture admitted")
    assert not outside.exists()
