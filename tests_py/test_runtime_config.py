from __future__ import annotations

from pathlib import Path

from local_control_center.agents.runtime_provider_config import runtime_provider_configuration
from local_control_center.agents.runtime_registry import RuntimeRegistry
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.config import (
    is_cli_runtime,
    resolve_default_runtime,
    resolve_executable,
)
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

CONFIG_TABLES = {"runtime_installations", "runtime_accounts", "runtime_preferences"}
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
        installation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runtime_installations)").fetchall()
        }
        account_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runtime_accounts)").fetchall()
        }

    assert tables >= CONFIG_TABLES
    assert phase21_rows == 1
    assert {
        "capabilities",
        "preferred_roles",
        "last_validation_at",
        "configuration_source",
    } <= installation_columns
    assert {
        "auth_mode",
        "account_label",
        "enabled",
        "capabilities",
        "preferred_roles",
        "health_status",
        "last_validation_at",
        "configuration_source",
    } <= account_columns
    # No tokens: runtime_accounts stores auth metadata and non-secret pointers, never secret values.
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
        assert codex["capabilities"] == ["code_edit"]
        assert "developer" in codex["preferredRoles"]
        assert codex["lastValidationAt"] is None
        assert codex["configurationSource"] == "seed"
        assert {i["runtimeId"] for i in repo.list_installations()} >= {"codex_cli", "claude_code_cli"}

        account = next(item for item in repo.list_runtime_accounts("codex_cli") if item["isDefault"])
        assert account["accountLabel"] == "Local Codex CLI"
        assert account["authMode"] == "provider_native_cli"
        assert account["credentialStoreKind"] == "provider_native_cli"
        assert account["credentialRef"] is None
        assert account["enabled"] is True
        assert account["capabilities"] == ["code_edit"]
        assert "developer" in account["preferredRoles"]
        assert account["healthStatus"] == "unknown"
        assert account["lastValidationAt"] is None
        assert account["configurationSource"] == "seed"


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
                "lastValidationAt": "2026-06-24T12:00:00Z",
                "capabilities": ["code_edit", "chat"],
                "preferredRoles": ["developer", "qa"],
                "configurationSource": "manual",
            }
        )
        assert installation["executablePath"] == "/usr/bin/codex"
        assert installation["enabled"] is True
        assert installation["detectedVersion"] == "1.2.3"
        assert installation["lastValidationAt"] == "2026-06-24T12:00:00Z"
        assert installation["capabilities"] == ["code_edit", "chat"]
        assert installation["preferredRoles"] == ["developer", "qa"]
        assert installation["configurationSource"] == "manual"

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


def test_runtime_account_persists_auth_metadata_without_storing_secrets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)

        account = repo.create_runtime_account(
            {
                "runtimeId": "codex_cli",
                "accountLabel": "personal",
                "authMode": "subscription",
                "credentialStoreKind": "os_keychain",
                "credentialRef": "keychain:codex-personal",  # a non-secret pointer, not the token
                "isDefault": True,
                "capabilities": ["code_edit", "chat"],
                "preferredRoles": ["developer", "technical_lead"],
                "healthStatus": "healthy",
                "lastValidationAt": "2026-06-24T12:30:00Z",
                "configurationSource": "manual",
            }
        )
        assert account["authMode"] == "subscription"
        assert account["credentialStoreKind"] == "os_keychain"
        assert account["credentialRef"] == "keychain:codex-personal"
        assert account["isDefault"] is True
        assert account["capabilities"] == ["code_edit", "chat"]
        assert account["preferredRoles"] == ["developer", "technical_lead"]
        assert account["lastValidationAt"] == "2026-06-24T12:30:00Z"
        assert account["configurationSource"] == "manual"
        # The persisted/exposed account carries no secret value.
        assert not any(token in key.lower() for key in account for token in SECRET_TOKENS)
        assert any(item["id"] == account["id"] for item in repo.list_runtime_accounts("codex_cli"))

        updated = repo.update_runtime_account(
            account["id"],
            {
                "authMode": "provider_native_cli",
                "credentialStoreKind": "provider_native_cli",
                "capabilities": ["code_edit"],
                "preferredRoles": ["developer"],
                "configurationSource": "detected",
            },
        )
        assert updated["authMode"] == "provider_native_cli"
        assert updated["credentialStoreKind"] == "provider_native_cli"
        assert updated["capabilities"] == ["code_edit"]
        assert updated["preferredRoles"] == ["developer"]
        assert updated["configurationSource"] == "detected"


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


def test_runtime_status_uses_persisted_cli_installation_without_env_command(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AIDO_CODEX_COMMAND", raising=False)
    monkeypatch.delenv("AIDO_ENABLE_CLI_RUNTIMES", raising=False)

    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        if runtime_id == "codex_cli":
            return {
                "runtime": runtime_id,
                "status": "installed",
                "executable": executable,
                "version": "1.2.3",
                "message": "",
            }
        return {
            "runtime": runtime_id,
            "status": "not_installed",
            "executable": executable,
            "version": None,
            "message": "not installed in test",
        }

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        RuntimeConfigRepository(connection).upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "C:/tools/codex.exe",
                "detectedVersion": "1.2.3",
                "enabled": True,
                "healthStatus": "healthy",
                "lastValidationAt": "2026-06-24T13:00:00Z",
                "configurationSource": "manual",
            }
        )

        codex = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "codex_cli"
        )

    assert codex["configured"] is True
    assert codex["available"] is True
    assert codex["detectedCommand"] == "C:/tools/codex.exe"
    assert codex["version"] == "1.2.3"
    assert codex["executable"] is False
    assert "AIDO_CODEX_COMMAND" not in codex["reason"]


def test_cli_command_env_is_deprecated_optional_override() -> None:
    unset = runtime_provider_configuration("codex_cli", environ={})
    assert unset is not None
    assert unset.configured is False
    assert unset.status == "override_unset"
    assert unset.missing == []
    assert "runtime_installations" in unset.reason

    override = runtime_provider_configuration("codex_cli", environ={"AIDO_CODEX_COMMAND": "codex"})
    assert override is not None
    assert override.configured is True
    assert override.status == "configured"
    assert override.value("command") == "codex"
