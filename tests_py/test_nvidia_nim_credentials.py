"""NVIDIA NIM con una API key válida: la ref del account manda, el valor se limpia y reingresarla rota.

Reproduce el reporte "NIM da error de api-key siendo válida": un ``AIDO_NVIDIA_API_KEY`` viejo pisaba la ref
de keyring del account, un salto de línea pegado viajaba en ``Authorization: Bearer`` y el placeholder
sembrado ``NVIDIA_NIM_API_KEY`` se ofrecía como credencial guardada.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.credentials import (
    CredentialResolver,
    clean_secret_value,
    preferred_credential_ref,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.app import create_app
from local_control_center.credentials.backends import KeyringBackend
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.execution_client import CompletedExecutionClient as TestClient

KEYRING_REF = "keyring:aido/providers/nvidia_nim"


class _FakeKeyring:
    def __init__(self, values: dict[tuple[str, str], str] | None = None) -> None:
        self.values = dict(values or {})

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as conn:
        initialize_platform_schema(conn)
        yield conn


def _clear_nvidia_env(monkeypatch) -> None:
    for name in ("AIDO_NVIDIA_API_KEY", "NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "AIDO_NVIDIA_NIM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_secret_values_lose_surrounding_whitespace_and_quotes() -> None:
    assert clean_secret_value("nvapi-GOOD\r\n") == "nvapi-GOOD"
    assert clean_secret_value('  "nvapi-q"  ') == "nvapi-q"
    assert clean_secret_value("'nvapi-q'") == "nvapi-q"
    assert clean_secret_value("   ") is None
    assert clean_secret_value(None) is None


def test_a_keyring_value_pasted_with_a_newline_is_sent_clean(monkeypatch) -> None:
    fake = _FakeKeyring({("aido", "providers/nvidia_nim"): "nvapi-GOOD\r\n"})
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=fake.get_password))
    resolution = CredentialResolver().resolve(KEYRING_REF, fetch=True)
    assert resolution.status == "configured"
    assert resolution.value == "nvapi-GOOD"


def test_the_account_ref_wins_over_a_stale_environment_variable(monkeypatch) -> None:
    _clear_nvidia_env(monkeypatch)
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=lambda *_: "x"))
    monkeypatch.setenv("AIDO_NVIDIA_API_KEY", "nvapi-STALE")
    assert preferred_credential_ref(KEYRING_REF, "env:AIDO_NVIDIA_API_KEY") == KEYRING_REF
    # Un placeholder heredado que no resuelve cede al entorno; sin ref en el account también.
    assert (
        preferred_credential_ref("NVIDIA_NIM_API_KEY", "env:AIDO_NVIDIA_API_KEY") == "env:AIDO_NVIDIA_API_KEY"
    )
    assert preferred_credential_ref("", "env:AIDO_NVIDIA_API_KEY") == "env:AIDO_NVIDIA_API_KEY"
    assert preferred_credential_ref("", None) == ""


def test_the_nim_adapter_uses_the_keyring_ref_even_with_the_env_variable_set(connection, monkeypatch) -> None:
    _clear_nvidia_env(monkeypatch)
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=lambda *_: "nvapi-GOOD"))
    monkeypatch.setenv("AIDO_NVIDIA_API_KEY", "nvapi-STALE")
    monkeypatch.setenv("AIDO_NVIDIA_MODEL", "meta/llama")
    ProviderAccountStore(connection).patch_provider_account(
        "nvidia_nim", {"credentialRef": KEYRING_REF, "enabled": True}
    )
    provider = ProviderAdapterFactory(connection).resolve("nvidia_nim")
    assert provider.credential_ref == KEYRING_REF


def test_nvidia_documentation_names_are_accepted_for_the_runtime_key(connection, monkeypatch) -> None:
    """Sin ref en el account, ``NVIDIA_API_KEY`` (el nombre de la documentación de NVIDIA) también sirve."""
    _clear_nvidia_env(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-DOC")
    monkeypatch.setenv("AIDO_NVIDIA_MODEL", "meta/llama")
    ProviderAccountStore(connection).patch_provider_account(
        "nvidia_nim", {"credentialRef": "", "enabled": True}
    )
    provider = ProviderAdapterFactory(connection).resolve("nvidia_nim")
    assert provider.credential_ref == "env:NVIDIA_API_KEY"


def test_status_reports_the_credential_the_request_will_use(connection, monkeypatch) -> None:
    """El placeholder sembrado no resuelve, pero el entorno sí: el estado no puede decir que falta."""
    _clear_nvidia_env(monkeypatch)
    monkeypatch.setenv("AIDO_NVIDIA_API_KEY", "nvapi-ENV")
    monkeypatch.setenv("AIDO_NVIDIA_MODEL", "meta/llama")
    ProviderAccountStore(connection).patch_provider_account(
        "nvidia_nim", {"credentialRef": "NVIDIA_NIM_API_KEY", "enabled": True}
    )
    status = next(
        item
        for item in RuntimeStatusService(connection).list_provider_statuses()
        if item["id"] == "nvidia_nim"
    )
    assert "NVIDIA_NIM_API_KEY is not set" not in str(status.get("reason"))


def test_a_dpapi_ref_is_a_supported_scheme_not_an_invalid_one(monkeypatch) -> None:
    monkeypatch.delenv("AIDO_DPAPI_SQLITE_PATH", raising=False)
    resolution = CredentialResolver().resolve("dpapi_sqlite:aido/providers/nvidia_nim", fetch=False)
    assert resolution.status == "unsupported"
    assert "AIDO_DPAPI_SQLITE_PATH" in (resolution.message or "")
    monkeypatch.setenv("AIDO_DPAPI_SQLITE_PATH", "/tmp/aido-dpapi.sqlite")
    assert CredentialResolver().resolve("dpapi_sqlite:aido/x", fetch=False).status == "unverified"


def test_reentering_the_same_provider_key_rotates_it_instead_of_failing(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(KeyringBackend, "_keyring", staticmethod(lambda: fake))
    with ExitStack() as stack:
        store = stack.enter_context(
            closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
        )
        store.init()
        client = stack.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}
        body = {
            "label": "NVIDIA NIM / Build API key",
            "source": "keyring",
            "credentialRef": "aido/providers/nvidia_nim",
            "authMode": "token",
            "value": "nvapi-FIRST-0123456789",
        }
        first = client.post("/api/v1/credentials", json=body, headers=headers)
        assert first.status_code == 201, first.text
        second = client.post(
            "/api/v1/credentials", json={**body, "value": "nvapi-SECOND-0123456789"}, headers=headers
        )
        assert second.status_code == 201, second.text
        assert second.json()["credential"]["credentialRef"] == first.json()["credential"]["credentialRef"]
        assert "nvapi-SECOND" not in second.text
        assert fake.get_password("aido", "providers/nvidia_nim") == "nvapi-SECOND-0123456789"
