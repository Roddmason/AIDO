"""Valida el catálogo de setup de proveedores: seeds nuevos, adaptador Azure y test-prompt.

Cubre el slice que convierte "Providers & CLI" en un setup moderno: (1) los proveedores nuevos
(DeepSeek, Groq, Mistral, Kimi, Gemini, Azure OpenAI, Ollama remoto) quedan sembrados con su base URL
verificada; (2) Azure autentica con la cabecera ``api-key`` (no ``Authorization: Bearer``); (3) el
factory despacha los ids nuevos al adaptador correcto; y (4) el endpoint ``test-prompt`` falla cerrado
(CLI, deshabilitado, desconocido) y nunca expone el secreto en su respuesta.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.providers.azure_openai import AzureOpenAIProvider
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.app import create_app
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture

CATALOG_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "kimi": "https://api.moonshot.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


class RecordingHandler(BaseHTTPRequestHandler):
    """Servidor HTTP de prueba que graba método, ruta y cabeceras de auth de cada petición."""

    seen: list[dict[str, object]] = []

    def log_message(self, *_args: object) -> None:
        return

    def _record(self, method: str) -> None:
        type(self).seen.append(
            {
                "method": method,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "apiKey": self.headers.get("api-key"),
            }
        )

    def _send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._record("GET")
        if self.path.endswith("/models"):
            self._send_json({"data": [{"id": "remote-model"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        self.rfile.read(length)
        self._record("POST")
        self._send_json(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
        )


def run_recording_server() -> tuple[str, type[RecordingHandler], HTTPServer]:
    """Levanta un servidor de prueba en un puerto libre y devuelve (base_url, handler, server)."""
    handler = type("RecordingCatalogHandler", (RecordingHandler,), {"seen": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", handler, server


def create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Construye un TestClient sobre un control plane recién inicializado en tmp_path."""
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    return TestClient(app)


def auth_headers(client: TestClient) -> dict[str, str]:
    """Obtiene el token de escritura local vía handshake y arma las cabeceras autorizadas."""
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def enable_remote_provider(
    client: TestClient, headers: dict[str, str], provider_id: str, *, base_url: str, credential_ref: str
) -> None:
    """Habilita un proveedor remoto apuntando su base URL al servidor de prueba y permite la política."""
    response = client.patch(
        f"/api/v1/model-gateway/providers/{provider_id}",
        json={"enabled": True, "baseUrl": base_url, "credentialRef": credential_ref},
        headers=headers,
    )
    assert response.status_code == 200
    with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)


def catalog_by_id(client: TestClient) -> dict[str, dict[str, object]]:
    response = client.get("/api/v1/providers/catalog")
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["providers"]}


def test_backend_provider_catalog_declares_required_fields_without_manual_base_url_for_known_presets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)

    catalog = catalog_by_id(client)

    assert set(catalog) >= {
        "openai_api",
        "anthropic_api",
        "openrouter",
        "nvidia_nim",
        "deepseek",
        "kimi",
        "mistral",
        "groq",
        "gemini",
        "azure_openai",
        "ollama",
        "ollama_remote",
        "openai_compatible",
    }
    assert "baseUrl" not in catalog["deepseek"]["requiredFields"]
    assert "baseUrl" not in catalog["kimi"]["requiredFields"]
    assert "baseUrl" in catalog["ollama_remote"]["requiredFields"]
    assert catalog["ollama_remote"]["providerFamily"] == "ollama"
    assert "baseUrl" in catalog["openai_compatible"]["requiredFields"]
    assert catalog["deepseek"]["defaultBaseUrl"] == "https://api.deepseek.com"
    assert catalog["kimi"]["defaultBaseUrl"] == "https://api.moonshot.ai/v1"
    assert "deepseek-v4-pro" in catalog["deepseek"]["knownModels"]
    assert "kimi-k2.7-code-highspeed" in catalog["kimi"]["knownModels"]
    assert "gemini-3.5-flash" in catalog["gemini"]["knownModels"]


def test_from_catalog_creates_deepseek_and_kimi_without_manual_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_CATALOG_DEEPSEEK_KEY", "test-catalog-deepseek-token-123456")
    monkeypatch.setenv("AIDO_CATALOG_KIMI_KEY", "test-catalog-kimi-token-123456")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    deepseek = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "deepseek",
            "credentialRef": "env:AIDO_CATALOG_DEEPSEEK_KEY",
            "enabled": True,
        },
    )
    kimi = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "kimi",
            "credentialRef": "env:AIDO_CATALOG_KIMI_KEY",
            "enabled": True,
        },
    )

    assert deepseek.status_code == 201
    assert kimi.status_code == 201
    assert deepseek.json()["provider"]["baseUrl"] == "https://api.deepseek.com"
    assert kimi.json()["provider"]["baseUrl"] == "https://api.moonshot.ai/v1"
    assert deepseek.json()["provider"]["credentialRef"] == "env:AIDO_CATALOG_DEEPSEEK_KEY"
    assert "test-catalog-deepseek-token" not in deepseek.text
    assert "test-catalog-kimi-token" not in kimi.text


def test_from_catalog_requires_base_url_for_remote_ollama_and_custom_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)

    remote_ollama = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={"providerId": "ollama_remote", "enabled": True},
    )
    remote_ollama_with_url = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "ollama_remote",
            "baseUrl": "http://127.0.0.1:11434",
            "enabled": True,
        },
    )
    custom = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "openai_compatible",
            "credentialRef": "env:AIDO_CUSTOM_PROVIDER_KEY",
            "enabled": True,
        },
    )

    assert remote_ollama.status_code == 422
    assert remote_ollama_with_url.status_code == 201
    assert custom.status_code == 422
    assert "baseUrl" in remote_ollama.json()["detail"]
    assert not remote_ollama_with_url.json()["provider"]["credentialRef"]
    assert "baseUrl" in custom.json()["detail"]


def test_provider_account_sync_models_uses_catalog_account_and_keeps_credential_secret_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_CUSTOM_SYNC_KEY", "test-catalog-sync-token-123456")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    base_url, _handler, server = run_recording_server()
    try:
        with open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])) as connection:
            RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        created = client.post(
            "/api/v1/provider-accounts/from-catalog",
            headers=headers,
            json={
                "providerId": "openai_compatible",
                "baseUrl": base_url,
                "credentialRef": "env:AIDO_CUSTOM_SYNC_KEY",
                "enabled": True,
            },
        )
        synced = client.post(
            "/api/v1/provider-accounts/openai_compatible/sync-models",
            headers=headers,
        )
    finally:
        server.shutdown()

    assert created.status_code == 201
    assert "test-catalog-sync-token" not in created.text
    assert synced.status_code == 200
    assert "test-catalog-sync-token" not in synced.text
    assert [item["model"] for item in synced.json()["models"]] == ["remote-model"]


def test_setup_catalog_seeds_new_providers_with_verified_base_urls(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        rows = {
            row["provider_id"]: row
            for row in connection.execute(
                "SELECT provider_id, base_url, api_format, provider_type FROM provider_accounts"
            ).fetchall()
        }
    for provider_id, base_url in CATALOG_BASE_URLS.items():
        assert rows[provider_id]["base_url"] == base_url
        assert rows[provider_id]["api_format"] == "openai_compatible"
        assert rows[provider_id]["provider_type"] == "api"
    assert rows["azure_openai"]["api_format"] == "azure_openai"
    assert (rows["azure_openai"]["base_url"] or "") == ""
    assert rows["ollama_remote"]["api_format"] == "ollama"
    assert rows["ollama_remote"]["provider_type"] == "local"
    assert (rows["ollama_remote"]["base_url"] or "") == ""


def test_azure_adapter_authenticates_with_api_key_header_not_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_AZURE_TEST_KEY", "unit-test-azure-key")
    base_url, handler, server = run_recording_server()
    try:
        provider = AzureOpenAIProvider(base_url=base_url, credential_ref="env:AIDO_AZURE_TEST_KEY")
        health = provider.health_check()
        response = provider.chat_completion(
            ModelRequest(model="my-deployment", messages=[{"role": "user", "content": "hi"}])
        )
    finally:
        server.shutdown()

    assert health.status == "available"
    assert response.content == "ok"
    assert handler.seen
    assert all(item["apiKey"] == "unit-test-azure-key" for item in handler.seen)
    assert all(item["authorization"] is None for item in handler.seen)
    assert "unit-test-azure-key" not in health.message


def test_provider_instance_dispatch_for_new_catalog_ids(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        azure = provider_instance("azure_openai", connection=connection)
        deepseek = provider_instance("deepseek", connection=connection)
    assert isinstance(azure, AzureOpenAIProvider)
    assert isinstance(deepseek, OpenAICompatibleProvider)
    assert deepseek.base_url == "https://api.deepseek.com"


def test_test_prompt_returns_ok_and_never_exposes_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_DEEPSEEK_TEST_KEY", "unit-test-deepseek-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    base_url, _handler, server = run_recording_server()
    try:
        enable_remote_provider(
            client, headers, "deepseek", base_url=base_url, credential_ref="env:AIDO_DEEPSEEK_TEST_KEY"
        )
        response = client.post(
            "/api/v1/model-gateway/providers/deepseek/test-prompt",
            json={"model": "deepseek-chat"},
            headers=headers,
        )
    finally:
        server.shutdown()

    assert response.status_code == 200
    result = response.json()["test"]
    assert result["ok"] is True
    assert result["model"] == "deepseek-chat"
    assert result["sample"] == "ok"
    assert "unit-test-deepseek-key" not in response.text


def test_test_prompt_fails_closed_for_cli_disabled_and_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    unknown = client.post(
        "/api/v1/model-gateway/providers/does_not_exist/test-prompt", json={}, headers=headers
    )
    cli = client.post("/api/v1/model-gateway/providers/codex_cli/test-prompt", json={}, headers=headers)
    disabled = client.post(
        "/api/v1/model-gateway/providers/deepseek/test-prompt", json={"model": "x"}, headers=headers
    )
    assert unknown.status_code == 404
    assert cli.status_code == 400
    assert disabled.status_code == 403
