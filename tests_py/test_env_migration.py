from __future__ import annotations

from pathlib import Path

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.credentials.repository import CredentialRepository
from local_control_center.runtime_integrations.env_migration import migrate_environment_config
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

# Clearly non-secret placeholder values (no real-key patterns) so the env values are easy to scan for.
GITHUB = "github-token-not-a-real-secret-001"
OPENAI = "openai-key-not-a-real-secret-001"
VAULT = "vault-token-not-a-real-secret-001"
SECRET_VALUES = (GITHUB, OPENAI, VAULT)
ENV = {
    "AIDO_CODEX_COMMAND": "C:/tools/codex.exe",
    "CODEX_CLI_PATH": "C:/legacy/codex.exe",
    "AIDO_CLAUDE_COMMAND": "C:/tools/claude.exe",
    "AIDO_GITHUB_TOKEN": GITHUB,
    "AIDO_OPENAI_COMPATIBLE_API_KEY": OPENAI,
    "AIDO_VAULT_TOKEN": VAULT,
}


def test_migration_populates_tables_warns_and_never_copies_the_secret(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        report = migrate_environment_config(connection, env=ENV)

        runtime_repo = RuntimeConfigRepository(connection)
        credential_repo = CredentialRepository(connection)
        provider_store = ProviderAccountStore(connection)

        # Runtime executables migrate to runtime_installations (AIDO_CODEX_COMMAND wins over CODEX_CLI_PATH).
        codex = runtime_repo.get_installation("codex_cli")
        assert codex["executablePath"] == "C:/tools/codex.exe"
        assert codex["metadata"]["source"] == "environment_override"
        assert codex["metadata"]["migratedFrom"] == "AIDO_CODEX_COMMAND"
        assert codex["enabled"] is False  # seed state preserved, not force-enabled
        assert runtime_repo.get_installation("claude_code_cli")["executablePath"] == "C:/tools/claude.exe"

        # Secrets migrate to credential_refs as references (backend=environment_override) with a
        # fingerprint, NOT the value.
        github = credential_repo.get_credential_by_name("github/token")
        assert github["backendKind"] == "environment_override"
        assert github["locator"] == "AIDO_GITHUB_TOKEN"
        assert github["fingerprint"] and len(github["fingerprint"]) == 64
        assert github["metadata"]["source"] == "environment_override"
        assert credential_repo.get_credential_by_name("provider/openai_compatible")["locator"] == (
            "AIDO_OPENAI_COMPATIBLE_API_KEY"
        )
        assert credential_repo.get_credential_by_name("vault/auth")["locator"] == "AIDO_VAULT_TOKEN"

        # The provider account is relinked to the env credential ref.
        assert provider_store.get_provider_account("openai_compatible")["credentialRef"] == (
            "env:AIDO_OPENAI_COMPATIBLE_API_KEY"
        )

        # A deprecation warning is shown per migrated variable, marking source=environment_override.
        assert report["warnings"]
        assert all("deprecated" in warning for warning in report["warnings"])
        assert any("environment_override" in warning for warning in report["warnings"])

        # The secret VALUE is never stored or surfaced anywhere.
        assert all(secret not in str(report) for secret in SECRET_VALUES)
        rows = connection.execute("SELECT * FROM credential_refs").fetchall()
        assert all(secret not in str(tuple(row)) for row in rows for secret in SECRET_VALUES)
        audit = connection.execute("SELECT * FROM credential_audit").fetchall()
        assert all(secret not in str(tuple(row)) for row in audit for secret in SECRET_VALUES)
        assert {entry["action"] for entry in credential_repo.list_audit()} == {"migrate"}
        assert {entry["backendKind"] for entry in credential_repo.list_audit()} == {"environment_override"}


def test_migration_falls_back_to_legacy_env_var_by_precedence(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        migrate_environment_config(connection, env={"CODEX_CLI_PATH": "C:/legacy/codex.exe"})

        codex = RuntimeConfigRepository(connection).get_installation("codex_cli")
        assert codex["executablePath"] == "C:/legacy/codex.exe"
        assert codex["metadata"]["migratedFrom"] == "CODEX_CLI_PATH"


def test_migration_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        migrate_environment_config(connection, env=ENV)
        migrate_environment_config(connection, env=ENV)

        credential_repo = CredentialRepository(connection)
        names = [credential["name"] for credential in credential_repo.list_credential_refs()]
        assert names.count("github/token") == 1  # upsert by name, not duplicated
        installations = RuntimeConfigRepository(connection).list_installations()
        assert len([i for i in installations if i["runtimeId"] == "codex_cli"]) == 1


def test_migration_skips_absent_env_vars(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        report = migrate_environment_config(connection, env={})

        assert report["runtimes"] == []
        assert report["credentials"] == []
        assert report["warnings"] == []
        assert report["skipped"]  # everything was skipped because nothing was set
