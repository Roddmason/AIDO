from __future__ import annotations

from pathlib import Path

from local_control_center.runtime_integrations.config import (
    is_cli_runtime,
    resolve_default_runtime,
    resolve_executable,
)
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

CONFIG_TABLES = {"runtime_installations", "cli_accounts", "runtime_preferences"}
SECRET_TOKENS = ("token", "secret", "password", "api_key", "apikey")


def test_runtime_config_schema_adds_tables_without_secret_columns_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase21_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 21"
        ).fetchone()["total"]
        account_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(cli_accounts)").fetchall()
        }

    assert tables >= CONFIG_TABLES
    assert phase21_rows == 1
    # No tokens: the cli_accounts table has no column that could hold a secret.
    assert not any(token in column.lower() for column in account_columns for token in SECRET_TOKENS)


def test_cli_is_the_seeded_default_runtime(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)

        preferences = repo.get_preferences()  # global
        assert preferences["defaultRuntime"] == "codex_cli"
        assert preferences["runtimeOrder"][0] == "codex_cli"
        assert is_cli_runtime(resolve_default_runtime(preferences))

        # The CLI installations are seeded, disabled until detected/configured.
        codex = repo.get_installation("codex_cli")
        assert codex["kind"] == "cli"
        assert codex["enabled"] is False
        assert codex["executablePath"] is None
        assert {i["runtimeId"] for i in repo.list_installations()} >= {"codex_cli", "claude_code_cli"}


def test_env_vars_are_override_only_not_normal_config(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)

        # Normal config lives in the database, not in env vars.
        installation = repo.upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "/usr/bin/codex",
                "detectedVersion": "1.2.3",
                "enabled": True,
                "healthStatus": "healthy",
            }
        )
        assert installation["executablePath"] == "/usr/bin/codex"
        assert installation["enabled"] is True
        assert installation["detectedVersion"] == "1.2.3"

        # Without an env var, the persisted value is used.
        assert resolve_executable(installation, env={}) == {"path": "/usr/bin/codex", "source": "persisted"}
        # The env var ONLY overrides the persisted config.
        assert resolve_executable(installation, env={"AIDO_CODEX_COMMAND": "/opt/codex"}) == {
            "path": "/opt/codex",
            "source": "environment_override",
        }

        preferences = repo.get_preferences()
        assert resolve_default_runtime(preferences, env={}) == "codex_cli"
        assert (
            resolve_default_runtime(preferences, env={"AIDO_DEFAULT_RUNTIME": "claude_code_cli"})
            == "claude_code_cli"
        )


def test_cli_account_persists_auth_without_storing_secrets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)

        account = repo.create_cli_account(
            {
                "runtimeId": "codex_cli",
                "accountLabel": "personal",
                "authMode": "subscription",
                "credentialStoreKind": "os_keychain",
                "credentialRef": "keychain:codex-personal",  # a non-secret pointer, not the token
                "isDefault": True,
            }
        )
        assert account["authMode"] == "subscription"
        assert account["credentialStoreKind"] == "os_keychain"
        assert account["credentialRef"] == "keychain:codex-personal"
        assert account["isDefault"] is True
        # The persisted/exposed account carries no secret value.
        assert not any(token in key.lower() for key in account for token in SECRET_TOKENS)
        assert repo.list_cli_accounts("codex_cli")[0]["id"] == account["id"]

        updated = repo.update_cli_account(
            account["id"], {"authMode": "api_key", "credentialStoreKind": "cli_managed"}
        )
        assert updated["authMode"] == "api_key"
        assert updated["credentialStoreKind"] == "cli_managed"


def test_runtime_preferences_round_trip_with_default_profiles(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)

        assert repo.get_preferences()["defaultProfiles"]["permissionProfile"] == "dev_safe"

        role_pref = repo.upsert_preferences(
            {
                "scope": "role",
                "scopeId": "developer",
                "defaultRuntime": "claude_code_cli",
                "runtimeOrder": ["claude_code_cli", "codex_cli"],
                "defaultProfiles": {"permissionProfile": "dev_safe"},
            }
        )
        assert role_pref["scope"] == "role"
        assert role_pref["defaultRuntime"] == "claude_code_cli"

        # Upsert updates in place (does not duplicate the scoped row).
        updated = repo.upsert_preferences(
            {"scope": "role", "scopeId": "developer", "defaultRuntime": "codex_cli"}
        )
        assert updated["defaultRuntime"] == "codex_cli"
        assert len([p for p in repo.list_preferences() if p["scope"] == "role"]) == 1
