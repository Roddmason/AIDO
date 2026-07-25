from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from local_control_center.agents.product_owner_agent_contract import is_product_owner_runtime
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_adapters import ProviderFactoryAdapter
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture

OLLAMA_ACCOUNT = {
    "providerId": "ollama",
    "displayName": "Ollama Local/Remote",
    "providerType": "local",
    "apiFormat": "ollama",
    "providerFamily": "ollama",
    "baseUrl": "http://127.0.0.1:11434",
    "enabled": True,
}


def _ollama_status(connection) -> dict:
    statuses = {str(item["id"]): item for item in RuntimeStatusService(connection).list_provider_statuses()}
    return statuses["ollama"]


def test_a_provider_without_quota_is_not_offered_as_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin esto un proveedor sin tokens sigue configurado y autenticado, y los agentes lo eligen."""
    # El estado de Ollama sale de un probe de red real; sin daemon vivo el provider ya está caído por
    # 'provider request failed' y la demotion por cuota no reescribe su motivo. Se fija un daemon
    # disponible para ejercer la precondición real: un provider ejecutable que luego pierde la cuota.
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {"provider": "ollama", "available": True, "models": ["llama3"], "reason": ""},
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(dict(OLLAMA_ACCOUNT))

        before = _ollama_status(connection)

        QuotaManager(connection).record_rate_limit(
            provider_id="ollama",
            model="*",
            retry_after_seconds=900,
            error_class="insufficient_quota",
        )

        after = _ollama_status(connection)

    assert after["executable"] is False
    assert after["canRunPrompt"] is False
    assert "quota is exhausted" in after["reason"]
    # The demotion must be caused by the cooldown, not by the account being broken to begin with.
    assert before["reason"] != after["reason"]


def test_a_429_from_the_agent_path_records_the_cooldown_that_demotes_the_provider(
    tmp_path: Path,
) -> None:
    """El adapter de agentes tragaba el HTTPError, así que el 429 nunca llegaba a la cuota."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(dict(OLLAMA_ACCOUNT))

        ProviderFactoryAdapter._record_provider_rate_limit(
            connection,
            provider_id="ollama",
            model="llama3",
            error=HTTPError(
                url="http://127.0.0.1:11434/v1/chat/completions",
                code=429,
                msg="Too Many Requests",
                hdrs={"Retry-After": "600"},
                fp=None,
            ),
        )

        demoted = _ollama_status(connection)

    assert demoted["executable"] is False
    assert demoted["canRunPrompt"] is False


def test_recording_a_rate_limit_never_masks_the_transport_error(tmp_path: Path) -> None:
    """Es una señal auxiliar: si falla, el error real de transporte debe seguir reportándose."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute("DROP TABLE provider_limits")

        ProviderFactoryAdapter._record_provider_rate_limit(
            connection,
            provider_id="ollama",
            model="llama3",
            error=HTTPError(url="http://x", code=429, msg="Too Many Requests", hdrs=None, fp=None),
        )


def test_a_cli_without_quota_stops_being_a_product_owner_runtime(tmp_path: Path) -> None:
    """`is_product_owner_runtime` corta en productOwnerExecutable para CLIs e ignora `executable`."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        QuotaManager(connection).record_rate_limit(
            provider_id="codex_cli", model="*", retry_after_seconds=900
        )
        statuses = {
            str(item["id"]): item for item in RuntimeStatusService(connection).list_provider_statuses()
        }

    codex = statuses["codex_cli"]
    assert codex["executable"] is False
    assert codex["available"] is False
    assert codex["canRunPrompt"] is False
    assert codex["productOwnerExecutable"] is False
    assert is_product_owner_runtime(codex) is False


def test_an_exhausted_provider_keeps_its_more_actionable_reason(tmp_path: Path) -> None:
    """Una causa reparable (falta credencial) es mas util que 'espera el cooldown'; no debe pisarse."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "anthropic_api",
                "displayName": "Anthropic API",
                "providerType": "api",
                "apiFormat": "anthropic",
                "providerFamily": "anthropic_api",
                "enabled": True,
            }
        )

        before = {str(i["id"]): i for i in RuntimeStatusService(connection).list_provider_statuses()}[
            "anthropic_api"
        ]
        assert before["executable"] is False, "fixture must start non-executable"

        QuotaManager(connection).record_rate_limit(
            provider_id="anthropic_api", model="*", retry_after_seconds=900
        )
        after = {str(i["id"]): i for i in RuntimeStatusService(connection).list_provider_statuses()}[
            "anthropic_api"
        ]

    assert after["reason"] == before["reason"]


def _failing_shell_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stderr: str,
    tool_call: dict[str, Any],
    return_code: int = 1,
) -> tuple[ControlPlaneFixture, dict[str, Any]]:
    """Ejecuta una corrida real por el ToolBroker con el sandbox simulado y salida fallida.

    Solo se sustituye el sandbox (la frontera con el proceso externo): la policy, el broker, la
    cuota y el estado de runtimes son los reales, que es donde vive el comportamiento a probar.
    """
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    project_path = tmp_path / "quota-project"
    project_path.mkdir(parents=True, exist_ok=True)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Quota Project", path=project_path, template_id="other")
    workspace = WorkspacesRepository(store.connection, root=tmp_path).allocate_workspace(
        project_id=project["id"],
        task_id="task-quota",
        agent_id="developer_agent",
    )
    agents = AgentsRepository(store.connection)
    profile = agents.upsert_agent_profile(
        {
            "id": "developer_agent",
            "name": "Developer Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    agent_run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id="task-quota",
        input_payload={},
        output_payload={},
        status="running",
    )
    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        lambda *_args, **_kwargs: {
            "stdout": "",
            "stderr": stderr,
            "returnCode": return_code,
            "blocked": False,
        },
    )
    result = ToolBroker(store.connection).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=agent_run["id"],
        agent_profile=profile,
        tool_call={
            "workspaceId": workspace["id"],
            "workspacePath": workspace["path"],
            "path": workspace["path"],
            "execute": True,
            **tool_call,
        },
    )
    return store, result


def _run_developer_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stderr: str,
) -> tuple[ControlPlaneFixture, dict[str, Any]]:
    """Corre el runtime CLI del DeveloperAgent (la ruta real del loop) hasta fallar."""
    return _failing_shell_run(
        tmp_path,
        monkeypatch,
        stderr=stderr,
        tool_call={
            "tool": "shell",
            "command": "codex --ask-for-approval never exec",
            "argv": ["codex", "--ask-for-approval", "never", "exec"],
            "operation": "developer_agent_runtime",
            "runtimeId": "codex_cli",
            "capability": "code_edit",
        },
    )


def test_a_cli_that_exits_on_a_usage_limit_stops_being_offered_as_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La cuota de un CLI solo falla al EJECUTAR: el health-check la da por buena y el loop lo re-elige."""
    store, result = _run_developer_cli(
        tmp_path,
        monkeypatch,
        stderr="You've hit your usage limit. Try again later.",
    )

    assert result["toolCall"]["status"] == "failed", "la corrida debe registrarse como fallida"
    exhausted = QuotaManager(store.connection).providers_in_cooldown()
    statuses = {
        str(item["id"]): item for item in RuntimeStatusService(store.connection).list_provider_statuses()
    }

    assert "codex_cli" in exhausted
    assert statuses["codex_cli"]["executable"] is False
    assert statuses["codex_cli"]["productOwnerExecutable"] is False


def test_an_ordinary_cli_failure_keeps_the_provider_selectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fallo de codigo no dice nada del proveedor; degradarlo dejaria la instalacion sin runtimes."""
    store, result = _run_developer_cli(
        tmp_path,
        monkeypatch,
        stderr="SyntaxError: invalid syntax",
    )

    assert result["toolCall"]["status"] == "failed"
    assert QuotaManager(store.connection).providers_in_cooldown() == set()


def test_a_shell_command_without_a_runtime_never_invents_a_provider_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin runtime declarado el broker cae al nombre del sandbox; eso no es un proveedor."""
    store, result = _failing_shell_run(
        tmp_path,
        monkeypatch,
        stderr="FAILED tests/test_quota.py::test_rate_limit_is_enforced",
        tool_call={
            "tool": "shell",
            "command": "python --version",
            "argv": ["python", "--version"],
            "sandbox": "restricted_subprocess",
        },
    )

    assert result["toolCall"]["status"] == "failed"
    limits = store.connection.execute("SELECT provider_id FROM provider_limits").fetchall()
    recorded = {str(row["provider_id"]) for row in limits}

    assert QuotaManager(store.connection).providers_in_cooldown() == set()
    assert recorded.isdisjoint({"restricted_subprocess", "docker"})


def test_providers_without_a_cooldown_are_left_untouched(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(dict(OLLAMA_ACCOUNT))

        baseline = _ollama_status(connection)

        QuotaManager(connection).record_rate_limit(
            provider_id="some-other-provider",
            model="*",
            retry_after_seconds=900,
        )

        unaffected = _ollama_status(connection)

    assert unaffected["executable"] == baseline["executable"]
    assert unaffected["reason"] == baseline["reason"]
