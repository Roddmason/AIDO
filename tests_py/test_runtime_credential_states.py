"""Tests de los estados de credencial que el operador ve.

Pedirle configurar algo que ya configuró lo hace reingresar la misma credencial una y otra vez.
Una referencia de credencial presente y bien formada SÍ es configuración; que no hayamos leído su
valor es un estado de *verificación*, no de *configuración*. Y una cuenta deshabilitada está
configurada: le falta habilitarse, no configurarse.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.agents.runtime_status import _api_provider_status
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _account(**overrides) -> dict:
    base = {
        "providerId": "nvidia_nim",
        "displayName": "NVIDIA NIM",
        "providerType": "api",
        "apiFormat": "openai_compatible",
        "apiFamily": "chat_completions",
        "providerFamily": "",
        "baseUrl": "https://integrate.api.nvidia.com/v1",
        "credentialRef": "keyring:aido/providers/nvidia_nim",
        # Una credencial en keyring SIEMPRE resuelve 'unverified' con fetch=False: no leemos el
        # secreto a proposito. Eso no significa que falte.
        "credentialStatus": "unverified",
        "enabled": True,
        "healthStatus": "unknown",
        "lastHealthCheckAt": None,
        "lastError": "",
    }
    base.update(overrides)
    return base


def _status(connection, account: dict) -> dict:
    return _api_provider_status(
        connection,
        account,
        {"enabled": True},
        ["chat"],
        {"allowed": True},
    )


def test_unverified_credential_counts_as_configured(tmp_path: Path) -> None:
    """Una referencia presente en keyring no puede reportarse como falta de configuración.

    Es el caso que obligaba a reingresar la misma API key una y otra vez: sin health check previo,
    'unverified' se leía como 'no hay credencial'.
    """
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        status = _status(connection, _account(healthStatus="unknown", lastHealthCheckAt=None))

    assert status["configured"] is True, status["reason"]
    assert "configure the required API key" not in status["reason"], status["reason"]


def test_missing_credential_still_asks_for_configuration(tmp_path: Path) -> None:
    """Sin ninguna referencia, pedir configuración sigue siendo la respuesta correcta."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        status = _status(connection, _account(credentialRef="", credentialStatus="missing"))

    assert status["configured"] is False
    assert "configure the required API key" in status["reason"]


def test_unverified_credential_is_not_executable_without_health(tmp_path: Path) -> None:
    """Contar la credencial como presente no relaja la ejecución: sigue exigiendo salud real."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        status = _status(connection, _account(healthStatus="unknown", lastHealthCheckAt=None))

    assert status["executable"] is False
    assert status["available"] is False


def test_disabled_account_is_blocked_not_unconfigured(tmp_path: Path) -> None:
    """Una cuenta deshabilitada está configurada: la acción es habilitarla, no reconfigurarla."""
    from local_control_center.agents.model_gateway import ModelGateway
    from local_control_center.agents.provider_accounts import ProviderAccountStore

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        account = store.get_provider_account("openai_api")
        store.patch_provider_account(account["id"], {"enabled": False})
        readiness = ModelGateway(connection)._provider_configuration("openai_api", runtime_type="api")

    assert readiness["status"] == "blocked", readiness
    assert "disabled" in readiness["reason"].lower()


def test_a_cli_that_needs_a_browser_login_publishes_the_exact_command() -> None:
    """El login de Claude y ChatGPT sólo puede hacerlo el usuario en su navegador.

    Anthropic prohíbe que un tercero intermedie tokens de sesión de claude.ai, así que la vía
    permitida es que el operador inicie sesión en el binario oficial y AIDO lo consuma. Lo único
    que AIDO puede hacer por él es darle el comando exacto, como dato estructurado y no enterrado
    dentro de una frase, para que la UI lo ofrezca copiable en vez de pedirle leerlo.
    """
    from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
    from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime

    assert ClaudeCodeCliRuntime.login_command == "claude auth login"
    assert CodexCliRuntime.login_command == "codex login"


def test_a_runtime_without_an_interactive_login_publishes_nothing() -> None:
    """Prometer un comando que no existe manda al operador a un callejón sin salida."""
    from local_control_center.agents.cli_runtimes.base import CliRuntime

    assert CliRuntime.login_command == ""
