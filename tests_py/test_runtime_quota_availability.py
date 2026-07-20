from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_adapters import ProviderFactoryAdapter
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

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


def test_a_provider_without_quota_is_not_offered_as_executable(tmp_path: Path) -> None:
    """Sin esto un proveedor sin tokens sigue configurado y autenticado, y los agentes lo eligen."""
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
