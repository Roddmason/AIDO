"""Startup credential loading is scoped to Jev and is disabled in QA."""

import os
from contextlib import closing
from unittest.mock import Mock

import pytest

from local_control_center.decision_engine.bootstrap import load_jev_startup_credential
from local_control_center.decision_engine.config import real_jev_calls_enabled

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows credential bootstrap")


@pytest.fixture
def vault(monkeypatch):
    # Explicit synthetic boundary: these tests never read the real Windows vault.
    from keyring.backends.Windows import WinVaultKeyring

    reader = Mock(return_value="synthetic-jev-bootstrap-key")
    monkeypatch.setattr(WinVaultKeyring, "get_password", reader)
    for key in (
        "PYTEST_CURRENT_TEST",
        "AIDO_QUALITY_INVOCATION_ID",
        "AIDO_QUALITY_DB_PATH",
        "TYPESAFE_API_KEY",
        "AIDO_ENABLE_JEV_CALLS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")
    return reader


def test_opt_in_loads_existing_vault_without_enabling_other_providers(tmp_path, monkeypatch, vault):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_JEV_CALLS", "true")
    assert load_jev_startup_credential(tmp_path / "unused.sqlite") is True
    import os

    assert os.environ["TYPESAFE_API_KEY"] == "synthetic-jev-bootstrap-key"
    assert os.environ["AIDO_ENABLE_REAL_PROVIDER_CALLS"] == "false"
    assert real_jev_calls_enabled() is True
    vault.assert_called_once_with("AIDO", "Jev")


def test_startup_without_active_setting_does_not_read_vault(tmp_path, vault):
    assert load_jev_startup_credential(tmp_path / "absent.sqlite") is False
    vault.assert_not_called()


@pytest.mark.parametrize(
    "guard", ["PYTEST_CURRENT_TEST", "AIDO_QUALITY_INVOCATION_ID", "AIDO_QUALITY_DB_PATH"]
)
def test_qa_never_loads_real_key(tmp_path, monkeypatch, vault, guard):
    monkeypatch.setenv("AIDO_ENABLE_JEV_CALLS", "true")
    monkeypatch.setenv(guard, "synthetic-test")
    assert load_jev_startup_credential(tmp_path / "unused.sqlite") is False
    vault.assert_not_called()


def test_explicit_disable_does_not_read_vault(tmp_path, monkeypatch, vault):
    monkeypatch.setenv("AIDO_ENABLE_JEV_CALLS", "false")
    assert load_jev_startup_credential(tmp_path / "unused.sqlite") is False
    vault.assert_not_called()


def test_missing_key_stays_unavailable_without_disclosing_error(tmp_path, monkeypatch, vault):
    monkeypatch.setenv("AIDO_ENABLE_JEV_CALLS", "true")
    vault.side_effect = RuntimeError("synthetic confidential error")
    assert load_jev_startup_credential(tmp_path / "unused.sqlite") is False


def test_persisted_active_mode_loads_key_on_future_launch(tmp_path, monkeypatch, vault):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from local_control_center.settings.repository import SettingsRepository
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    path = tmp_path / "configured.sqlite"
    with closing(open_sqlite_connection(path)) as connection:
        initialize_platform_schema(connection)
        SettingsRepository(connection).set_value("decision_engine.mode", "general", None, "runtime_selection")
    assert load_jev_startup_credential(path) is True
    vault.assert_called_once_with("AIDO", "Jev")


def test_qa_overrides_inherited_real_call_flags_and_key(monkeypatch):
    monkeypatch.setenv("AIDO_QUALITY_INVOCATION_ID", "synthetic-qa")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("AIDO_ENABLE_JEV_CALLS", "true")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-inherited-key")
    assert real_jev_calls_enabled() is False
