from __future__ import annotations

import urllib.request
from pathlib import Path

from local_control_center.agents.model_gateway import ModelGateway
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import (
    ProviderAdapterFactory,
    provider_account_requires_credential,
)
from local_control_center.agents.runtime_provider_config import (
    list_runtime_provider_configurations,
    runtime_provider_configuration,
)
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

CONFIG_TABLES = {
    "runtime_installations",
    "runtime_accounts",
    "runtime_health_checks",
    "runtime_capabilities",
    "runtime_preferences",
}
SECRET_TOKENS = ("token", "secret", "password", "api_key", "apikey")
REQUESTED_RUNTIME_IDS = {
    "codex_cli",
    "claude_code_cli",
    "openhands",
    "swe_agent",
    "ollama",
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
}


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
        health_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runtime_health_checks)").fetchall()
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
    assert {
        "runtime_id",
        "check_type",
        "status",
        "payload",
        "created_at",
    } <= health_columns
    # No tokens: runtime_accounts stores auth metadata and non-secret pointers, never secret values.
    assert not any(token in column.lower() for column in account_columns for token in SECRET_TOKENS)


def test_requested_runtimes_are_seeded_with_capabilities(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        installations = {item["runtimeId"]: item for item in repo.list_installations()}
        capabilities = {
            (row["runtime"], row["capability"])
            for row in connection.execute(
                "SELECT runtime, capability FROM runtime_capabilities WHERE enabled = 1"
            ).fetchall()
        }

    assert set(installations) >= REQUESTED_RUNTIME_IDS
    assert installations["openhands"]["kind"] == "cli"
    assert installations["swe_agent"]["kind"] == "cli"
    assert installations["ollama"]["kind"] == "local"
    assert installations["nvidia_nim"]["kind"] == "api"
    assert "product_owner" in installations["nvidia_nim"]["preferredRoles"]
    # openhands/swe_agent are autonomous code-editing CLIs: installed/discoverable (asserted above via
    # runtime_installations) but GATED — their code_edit/issue_to_patch capabilities are NOT enabled by
    # default and require an explicit developer_agent grant (least-privilege; see the release safety
    # contracts in test_openhands_swe_agent_release_contracts).
    assert ("openhands", "issue_to_patch") not in capabilities
    assert ("swe_agent", "issue_to_patch") not in capabilities
    assert ("openhands", "code_edit") not in capabilities
    assert ("swe_agent", "code_edit") not in capabilities
    assert ("ollama", "chat") in capabilities
    assert ("openai_compatible", "chat") in capabilities
    assert ("openrouter", "chat") in capabilities
    assert ("nvidia_nim", "chat") in capabilities
    assert ("anthropic_api", "chat") in capabilities


def test_ollama_configuration_represents_local_or_remote_base_url() -> None:
    providers = {
        provider["id"]: provider
        for provider in list_runtime_provider_configurations(
            environ={"AIDO_OLLAMA_BASE_URL": "http://192.0.2.10:11434"}
        )
    }

    assert providers["ollama"]["displayName"] == "Ollama Local/Remote"
    assert providers["ollama"]["configured"] is True
    assert providers["ollama"]["variables"][0]["name"] == "AIDO_OLLAMA_BASE_URL"


def test_known_nvidia_provider_config_does_not_require_manual_base_url() -> None:
    providers = {
        provider["id"]: provider
        for provider in list_runtime_provider_configurations(
            environ={
                "AIDO_NVIDIA_API_KEY": "unit-test-nvidia-key",
                "AIDO_NVIDIA_MODEL": "nvidia/model",
            }
        )
    }

    nvidia = providers["nvidia_nim"]
    base_url = next(variable for variable in nvidia["variables"] if variable["key"] == "baseUrl")
    assert nvidia["configured"] is True
    assert nvidia["missing"] == []
    assert base_url["required"] is False


def test_litellm_proxy_supports_optional_auth_in_config_adapter_and_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AIDO_LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.setenv("AIDO_LITELLM_BASE_URL", "https://litellm.example.test/v1")
    observed: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"id":"proxy-model"}]}'

    def fake_urlopen(request: urllib.request.Request, *, timeout: float):
        observed["request"] = request
        observed["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(
        "local_control_center.agents.providers.openai_compatible.urlopen_fail_closed",
        fake_urlopen,
    )
    configuration = runtime_provider_configuration("litellm")
    assert configuration is not None
    assert configuration.configured is True
    assert configuration.value("apiKey") is None

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.remote.enabled", True)
        repo.upsert_installation(
            {
                "runtimeId": "litellm",
                "kind": "gateway",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1,
                base_url = '',
                health_status = 'healthy',
                last_health_check_at = '2026-07-14T12:00:00Z'
            WHERE provider_id = 'litellm'
            """
        )
        connection.execute("UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'litellm'")
        account = next(
            item
            for item in ProviderAccountStore(connection).list_provider_accounts()
            if item["providerId"] == "litellm"
        )
        provider = ProviderAdapterFactory(connection).resolve("litellm")
        models = provider.list_models()
        status = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "litellm"
        )

    request = observed["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.get_header("Authorization") is None
    assert observed["timeout"] == 10
    assert [model.model for model in models] == ["proxy-model"]
    assert account["credentialRef"] == ""
    assert provider_account_requires_credential(account) is False
    assert provider.credential_required is False
    assert status["requiredConfiguration"] == ["baseUrl", "model"]
    assert status["configured"] is True
    assert status["authenticated"] is True
    assert status["executable"] is True


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


def test_runtime_settings_are_registered_for_global_and_project_policy(tmp_path: Path) -> None:
    """Todo transporte llega habilitado; la barrera real es la credencial, no un flag apagado.

    La politica es una conjuncion global AND proyecto, asi que apagar el flag de proyecto sigue
    bastando para vetar un transporte en un repositorio concreto.
    """
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)

        policy = repo.runtime_execution_policy(project_id="project-runtime")

    assert policy["global"]["cliEnabled"] is True
    assert policy["global"]["remoteEnabled"] is True
    assert policy["global"]["ollamaEnabled"] is True
    assert policy["global"]["nvidiaEnabled"] is True
    assert policy["project"]["cliEnabled"] is True
    assert policy["project"]["remoteEnabled"] is True
    assert policy["project"]["allowedProviders"] == []
    assert policy["project"]["defaultMode"] == "hybrid"


def test_sqlite_enabled_healthy_cli_is_executable_even_when_env_false(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_CODEX_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "false")

    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "installed" if runtime_id == "codex_cli" else "not_installed",
            "executable": executable if runtime_id == "codex_cli" else None,
            "version": "codex-cli 1.2.3" if runtime_id == "codex_cli" else None,
            "message": "" if runtime_id == "codex_cli" else "not installed in test",
        }

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.cli.enabled", True)
        repo.set_runtime_setting("project.runtime.cli.enabled", True, scope="project", scope_id="project-a")
        repo.upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "C:/tools/codex.exe",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        account = next(item for item in repo.list_runtime_accounts("codex_cli") if item["isDefault"])
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": "2026-06-27T12:00:00Z"},
        )

        status = RuntimeStatusService(connection).runtime_provider_status(project_id="project-a")
        codex = next(item for item in status["providers"] if item["id"] == "codex_cli")

    assert codex["available"] is True
    assert codex["authenticated"] is True
    assert codex["canRunPrompt"] is True
    assert codex["canEditWorkspace"] is True
    assert codex["executable"] is True
    assert "AIDO_ENABLE_CLI_RUNTIMES" not in codex["reason"]
    assert any("AIDO_ENABLE_CLI_RUNTIMES" in warning for warning in status["configurationWarnings"])
    assert any("environment_override" in warning for warning in codex["configurationWarnings"])


def test_env_override_warning_is_reported_without_becoming_execution_authority(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-openai-key")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.remote.enabled", False)
        repo.upsert_installation(
            {
                "runtimeId": "openai_compatible",
                "kind": "api",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1,
                base_url = ?,
                credential_ref = ?,
                health_status = 'healthy',
                last_health_check_at = ?
            WHERE provider_id = 'openai_compatible'
            """,
            ("https://example.invalid/v1", "env:OPENAI_API_KEY", "2026-06-27T12:00:00Z"),
        )
        connection.execute(
            """
            UPDATE model_catalog
            SET enabled = 1
            WHERE provider_id = 'openai_compatible' AND model = 'configured_model'
            """
        )

        status = RuntimeStatusService(connection).runtime_provider_status(project_id="project-a")
        provider = next(item for item in status["providers"] if item["id"] == "openai_compatible")

    assert provider["executable"] is False
    assert "runtime.remote.enabled" in provider["reason"]
    assert any("AIDO_ENABLE_REAL_PROVIDER_CALLS" in warning for warning in status["configurationWarnings"])


def test_project_remote_disabled_blocks_api_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-openai-key")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.remote.enabled", True)
        repo.set_runtime_setting(
            "project.runtime.remote.enabled", False, scope="project", scope_id="project-a"
        )
        repo.upsert_installation(
            {
                "runtimeId": "openai_compatible",
                "kind": "api",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1,
                base_url = ?,
                credential_ref = ?,
                health_status = 'healthy',
                last_health_check_at = ?
            WHERE provider_id = 'openai_compatible'
            """,
            ("https://example.invalid/v1", "env:OPENAI_API_KEY", "2026-06-27T12:00:00Z"),
        )
        connection.execute(
            """
            UPDATE model_catalog
            SET enabled = 1
            WHERE provider_id = 'openai_compatible' AND model = 'configured_model'
            """
        )

        status = RuntimeStatusService(connection).runtime_provider_status(project_id="project-a")
        provider = next(item for item in status["providers"] if item["id"] == "openai_compatible")
        planned = ModelGateway(connection).plan_model_call(
            project_id="project-a",
            provider="openai_compatible",
            model="configured_model",
            runtime_type="api",
            messages=[{"role": "user", "content": "smoke"}],
        )
        execution = ModelGateway(connection).execute_model_call(planned)

    assert provider["available"] is True
    assert provider["executable"] is False
    assert "project.runtime.remote.enabled" in provider["reason"]
    assert execution["status"] == "blocked"
    assert "project.runtime.remote.enabled" in execution["reason"]


def test_nvidia_runtime_status_uses_known_default_base_url_without_user_input(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIDO_NVIDIA_API_KEY", "unit-test-nvidia-key")
    monkeypatch.setenv("AIDO_NVIDIA_MODEL", "nvidia/model")
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.remote.enabled", True)
        repo.set_runtime_setting("runtime.nvidia.enabled", True)
        repo.upsert_installation(
            {
                "runtimeId": "nvidia_nim",
                "kind": "api",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1
            WHERE provider_id = 'nvidia_nim'
            """
        )
        connection.execute(
            """
            UPDATE model_catalog
            SET enabled = 1
            WHERE provider_id = 'nvidia_nim' AND model = 'auto_best_available'
            """
        )

        provider = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses(project_id="project-a")
            if item["id"] == "nvidia_nim"
        )

    assert provider["configured"] is True
    assert provider["requiredConfiguration"] == ["apiKey", "model"]
    assert "base URL is not configured" not in provider["reason"]


def test_ollama_alias_respects_global_ollama_setting(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.ollama.enabled", False)

        decision = repo.runtime_policy_decision(
            provider_id="local_ollama", kind="local", project_id="project-a"
        )

    assert decision["allowed"] is False
    assert decision["reason"] == "runtime.ollama.enabled is false."


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


def test_cli_installed_version_ok_is_not_executable_until_native_auth_is_validated(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AIDO_CODEX_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")

    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "installed" if runtime_id == "codex_cli" else "not_installed",
            "executable": executable if runtime_id == "codex_cli" else None,
            "version": "codex-cli 1.2.3" if runtime_id == "codex_cli" else None,
            "message": "" if runtime_id == "codex_cli" else "not installed in test",
        }

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        RuntimeConfigRepository(connection).upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "C:/tools/codex.exe",
                "enabled": True,
                "configurationSource": "manual",
            }
        )

        codex = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "codex_cli"
        )

    assert codex["detected"] is True
    assert codex["version"] == "codex-cli 1.2.3"
    assert codex["authenticated"] is False
    assert codex["canRunVersionCheck"] is True
    assert codex["canRunPrompt"] is False
    assert codex["canEditWorkspace"] is False
    assert codex["executable"] is False
    assert "authentication" in codex["reason"].lower()


def test_cli_version_ok_with_validated_native_account_can_run_prompt_and_edit_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AIDO_CODEX_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")

    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "installed" if runtime_id == "codex_cli" else "not_installed",
            "executable": executable if runtime_id == "codex_cli" else None,
            "version": "codex-cli 1.2.3" if runtime_id == "codex_cli" else None,
            "message": "" if runtime_id == "codex_cli" else "not installed in test",
        }

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "C:/tools/codex.exe",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        repo.set_runtime_setting("runtime.cli.enabled", True)
        account = next(item for item in repo.list_runtime_accounts("codex_cli") if item["isDefault"])
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": "2026-06-27T12:00:00Z"},
        )

        codex = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "codex_cli"
        )

    assert codex["authenticated"] is True
    assert codex["canRunVersionCheck"] is True
    assert codex["canRunPrompt"] is True
    assert codex["canEditWorkspace"] is True
    assert codex["executable"] is True


def test_chat_only_cli_with_mismatched_executable_fails_prompt_execution_closed(
    tmp_path: Path, monkeypatch
) -> None:
    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "installed" if runtime_id == "codex_cli" else "not_installed",
            "executable": "C:/tools/python.exe" if runtime_id == "codex_cli" else None,
            "version": "codex-cli 0.142.2" if runtime_id == "codex_cli" else None,
            "message": "" if runtime_id == "codex_cli" else "not installed in test",
        }

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.cli.enabled", True)
        repo.upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "C:/tools/python.exe",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        account = next(item for item in repo.list_runtime_accounts("codex_cli") if item["isDefault"])
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": "2026-07-13T12:00:00Z"},
        )
        connection.execute(
            "UPDATE runtime_capabilities SET enabled = 0 WHERE runtime = 'codex_cli' AND capability = 'code_edit'"
        )

        codex = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "codex_cli"
        )

    assert "chat" in codex["capabilities"]
    assert "code_edit" not in codex["capabilities"]
    assert codex["canRunPrompt"] is False
    assert codex["executable"] is False
    assert codex["productOwnerExecutable"] is False
    assert "does not match" in codex["reason"]


def test_stale_persisted_codex_version_cannot_authorize_product_owner(tmp_path: Path, monkeypatch) -> None:
    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "installed" if runtime_id == "codex_cli" else "not_installed",
            "executable": executable if runtime_id == "codex_cli" else None,
            "version": None,
            "message": "version probe timed out" if runtime_id == "codex_cli" else "not installed in test",
        }

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.cli.enabled", True)
        repo.upsert_installation(
            {
                "runtimeId": "codex_cli",
                "kind": "cli",
                "executablePath": "C:/tools/codex.exe",
                "detectedVersion": "codex-cli 0.142.2",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        account = next(item for item in repo.list_runtime_accounts("codex_cli") if item["isDefault"])
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": "2026-07-13T12:00:00Z"},
        )

        codex = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "codex_cli"
        )

    assert codex["version"] == "codex-cli 0.142.2"
    assert codex["canRunPrompt"] is True
    assert codex["canRunVersionCheck"] is False
    assert codex["versionVerified"] is False
    assert codex["productOwnerExecutable"] is False
    assert any("fresh" in warning for warning in codex["configurationWarnings"])


def test_ollama_ok_with_mocked_server_reports_prompt_capability(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": True,
            "models": ["llama3.1:8b"],
            "reason": "Ollama test daemon is reachable.",
        },
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE provider_accounts SET enabled = 1 WHERE provider_id = 'ollama'")
        ollama = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "ollama"
        )

    assert ollama["detected"] is True
    assert ollama["available"] is True
    assert ollama["executable"] is True
    assert ollama["canRunPrompt"] is True
    assert ollama["canEditWorkspace"] is False
    assert ollama["productOwnerExecutable"] is True
    assert ollama["models"] == ["llama3.1:8b"]


def test_ollama_legacy_blank_base_url_uses_local_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    observed: dict[str, str | None] = {}

    def fake_ollama_status(**kwargs):
        observed["base_url"] = kwargs.get("base_url")
        return {
            "provider": "ollama",
            "available": True,
            "models": ["llama3.1:8b"],
            "reason": "Local Ollama test daemon is reachable.",
        }

    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        fake_ollama_status,
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            "UPDATE provider_accounts SET enabled = 1, base_url = '' WHERE provider_id = 'ollama'"
        )
        ollama = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "ollama"
        )

    assert observed["base_url"] == "http://localhost:11434"
    assert ollama["configured"] is True
    assert ollama["available"] is True
    assert ollama["executable"] is True
    assert "base URL is not configured" not in ollama["reason"]


def test_invalid_api_credential_ref_blocks_runtime_with_configuration_reason(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1,
                base_url = ?,
                credential_ref = ?,
                health_status = 'healthy',
                last_health_check_at = ?
            WHERE provider_id = 'openai_compatible'
            """,
            ("https://example.invalid/v1", "env:missing-lowercase", "2026-06-27T12:00:00Z"),
        )
        provider = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "openai_compatible"
        )

    assert provider["configured"] is False
    assert provider["authenticated"] is False
    assert provider["available"] is False
    assert provider["executable"] is False
    assert "credential" in provider["reason"].lower()
    assert "invalid" in provider["reason"].lower()


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


def _claude_only_detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
    return {
        "runtime": runtime_id,
        "status": "installed" if runtime_id == "claude_code_cli" else "not_installed",
        "executable": executable if runtime_id == "claude_code_cli" else None,
        "version": "2.1.207 (Claude Code)" if runtime_id == "claude_code_cli" else None,
        "message": "" if runtime_id == "claude_code_cli" else "not installed in test",
    }


def _install_claude_cli(repo: RuntimeConfigRepository) -> None:
    repo.set_runtime_setting("runtime.cli.enabled", True)
    repo.upsert_installation(
        {
            "runtimeId": "claude_code_cli",
            "kind": "cli",
            "executablePath": "C:/tools/claude.exe",
            "enabled": True,
            "configurationSource": "manual",
        }
    )


def test_cli_becomes_executable_when_native_auth_probe_confirms_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_CLAUDE_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    monkeypatch.setattr(RuntimeRegistry, "detect", _claude_only_detect)

    def validate_native_auth(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "authenticated",
            "message": "Claude Code CLI session is logged in (claude.ai).",
        }

    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", validate_native_auth)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        _install_claude_cli(repo)

        claude = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "claude_code_cli"
        )
        account = next(item for item in repo.list_runtime_accounts("claude_code_cli") if item["isDefault"])

    assert claude["authenticated"] is True
    assert claude["canRunPrompt"] is True
    assert claude["executable"] is True
    assert account["healthStatus"] == "healthy"
    assert account["lastValidationAt"]
    assert account["configurationSource"] == "native_cli_status"


def test_cli_logged_out_probe_reports_login_command_and_blocks_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_CLAUDE_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    monkeypatch.setattr(RuntimeRegistry, "detect", _claude_only_detect)

    def validate_native_auth(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        return {
            "runtime": runtime_id,
            "status": "unauthenticated",
            "message": "Claude Code CLI is not logged in; run `claude auth login`.",
        }

    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", validate_native_auth)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        _install_claude_cli(repo)

        claude = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "claude_code_cli"
        )
        account = next(item for item in repo.list_runtime_accounts("claude_code_cli") if item["isDefault"])

    assert claude["authenticated"] is False
    assert claude["executable"] is False
    assert "claude auth login" in claude["reason"]
    assert account["healthStatus"] == "unauthenticated"
    assert not account["lastValidationAt"]


def test_validated_cli_account_is_not_reprobed_on_status_rollup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_CLAUDE_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    monkeypatch.setattr(RuntimeRegistry, "detect", _claude_only_detect)

    def validate_native_auth(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        raise AssertionError("a validated account must not trigger a native auth probe")

    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", validate_native_auth)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        _install_claude_cli(repo)
        account = next(item for item in repo.list_runtime_accounts("claude_code_cli") if item["isDefault"])
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": "2026-06-27T12:00:00Z"},
        )

        claude = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "claude_code_cli"
        )

    assert claude["authenticated"] is True
    assert claude["executable"] is True


def test_cli_version_probe_hiccup_falls_back_to_persisted_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_CLAUDE_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")

    def detect(self: RuntimeRegistry, runtime_id: str, *, executable: str | None = None):
        payload = _claude_only_detect(self, runtime_id, executable=executable)
        payload["version"] = None
        return payload

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        _install_claude_cli(repo)
        repo.upsert_installation(
            {
                "runtimeId": "claude_code_cli",
                "kind": "cli",
                "executablePath": "C:/tools/claude.exe",
                "detectedVersion": "2.1.207 (Claude Code)",
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        account = next(item for item in repo.list_runtime_accounts("claude_code_cli") if item["isDefault"])
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": "2026-06-27T12:00:00Z"},
        )

        claude = next(
            item
            for item in RuntimeStatusService(connection).list_provider_statuses()
            if item["id"] == "claude_code_cli"
        )

    assert claude["detected"] is True
    assert claude["version"] == "2.1.207 (Claude Code)"
    assert claude["available"] is True
    assert claude["executable"] is True


def test_rollup_persists_fresh_cli_version_for_future_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AIDO_CLAUDE_COMMAND", raising=False)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    monkeypatch.setattr(RuntimeRegistry, "detect", _claude_only_detect)
    monkeypatch.setattr(
        RuntimeRegistry,
        "validate_native_auth",
        lambda self, runtime_id, *, executable=None: {
            "runtime": runtime_id,
            "status": "unknown",
            "message": "probe unavailable in test",
        },
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repo = RuntimeConfigRepository(connection)
        _install_claude_cli(repo)

        RuntimeStatusService(connection).list_provider_statuses()
        installation = repo.get_installation("claude_code_cli")

    assert installation["detectedVersion"] == "2.1.207 (Claude Code)"
