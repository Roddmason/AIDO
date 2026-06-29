"""Valida los runtimes no-CLI (Ollama local/remoto y la familia OpenAI-compatible).

Cubre las reglas del slice de runtimes: Ollama envia auth Bearer solo cuando hay
``credentialRef`` (remoto) y nunca por defecto (local); el sondeo de estado autentica
igual que el proveedor; y la familia OpenAI-compatible marca el uso como ``unknown``
cuando el proveedor no reporta tokens, en vez de inventar ceros. Todo contra un servidor
HTTP de prueba que graba la cabecera ``Authorization`` recibida.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from local_control_center.agents.model_gateway import ollama_status
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.ollama import OllamaProvider
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.agents.providers.openrouter import OpenRouterProvider

OLLAMA_TAGS_PAYLOAD = {"models": [{"name": "llama3:latest"}]}
OLLAMA_CHAT_PAYLOAD = {
    "message": {"role": "assistant", "content": "local model reply"},
    "prompt_eval_count": 4,
    "eval_count": 6,
}


class RecordingHandler(BaseHTTPRequestHandler):
    """Servidor HTTP de prueba que graba metodo/ruta/Authorization de cada peticion."""

    seen: list[dict[str, object]] = []

    def log_message(self, *_args: object) -> None:
        return

    def _record(self, method: str) -> None:
        type(self).seen.append(
            {"method": method, "path": self.path, "authorization": self.headers.get("Authorization")}
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
        if self.path == "/api/tags":
            self._send_json(OLLAMA_TAGS_PAYLOAD)
            return
        if self.path.endswith("/models"):
            self._send_json({"data": [{"id": "remote-model"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        self.rfile.read(length)
        self._record("POST")
        if self.path == "/api/chat":
            self._send_json(OLLAMA_CHAT_PAYLOAD)
            return
        self._send_json({"choices": [{"message": {"content": "remote reply"}}]})


def run_recording_server() -> tuple[str, type[RecordingHandler], HTTPServer]:
    handler = type("RecordingTestHandler", (RecordingHandler,), {"seen": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", handler, server


def test_ollama_provider_sends_bearer_auth_when_credential_ref_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_OLLAMA_REMOTE_KEY", "sk-ollamaremote123456")
    base_url, handler, server = run_recording_server()
    try:
        provider = OllamaProvider(base_url=base_url, credential_ref="env:AIDO_OLLAMA_REMOTE_KEY")
        health = provider.health_check()
        models = provider.list_models()
        response = provider.chat_completion(
            ModelRequest(model="llama3", messages=[{"role": "user", "content": "hi"}])
        )
    finally:
        server.shutdown()

    assert health.status == "available"
    assert [model.model for model in models] == ["llama3:latest"]
    assert response.content == "local model reply"
    assert handler.seen
    assert all(item["authorization"] == "Bearer sk-ollamaremote123456" for item in handler.seen)
    assert "sk-ollamaremote" not in health.message


def test_ollama_provider_omits_auth_header_for_local_default() -> None:
    base_url, handler, server = run_recording_server()
    try:
        provider = OllamaProvider(base_url=base_url)
        provider.health_check()
        provider.list_models()
    finally:
        server.shutdown()

    assert handler.seen
    assert all(item["authorization"] is None for item in handler.seen)


def test_ollama_status_probe_authenticates_remote_with_credential_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_OLLAMA_REMOTE_KEY", "sk-ollamastatus123456")
    base_url, handler, server = run_recording_server()
    try:
        status = ollama_status(base_url=base_url, credential_ref="env:AIDO_OLLAMA_REMOTE_KEY")
    finally:
        server.shutdown()

    assert status["available"] is True
    assert status["models"] == ["llama3:latest"]
    assert any(item["authorization"] == "Bearer sk-ollamastatus123456" for item in handler.seen)


def test_ollama_status_probe_omits_auth_header_when_no_credential() -> None:
    base_url, handler, server = run_recording_server()
    try:
        status = ollama_status(base_url=base_url)
    finally:
        server.shutdown()

    assert status["available"] is True
    assert all(item["authorization"] is None for item in handler.seen)


def test_openai_compatible_marks_usage_unknown_when_provider_omits_usage() -> None:
    provider = OpenAICompatibleProvider(
        provider_id="openai_compatible", base_url="https://example.invalid/v1"
    )

    missing = provider.parse_usage({"choices": [{"message": {"content": "no usage block"}}]})
    present = provider.parse_usage({"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})

    assert missing.input_tokens == 0
    assert missing.output_tokens == 0
    assert missing.total_tokens == 0
    assert missing.raw_usage == {
        "usage_source": "unknown",
        "reason": "provider_response_missing_usage",
    }
    assert present.input_tokens == 3
    assert present.output_tokens == 4
    assert present.total_tokens == 7
    assert present.raw_usage["usage_source"] == "provider"


def test_openrouter_marks_usage_unknown_when_provider_omits_usage() -> None:
    provider = OpenRouterProvider(credential_ref="env:OPENROUTER_TEST_KEY")

    usage = provider.parse_usage({"choices": [{"message": {"content": "text without usage"}}]})

    assert usage.input_tokens == 0
    assert usage.total_tokens == 0
    assert usage.raw_usage == {
        "usage_source": "unknown",
        "reason": "provider_response_missing_usage",
    }


def test_ollama_provider_fails_closed_when_credential_ref_is_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIDO_OLLAMA_MISSING_KEY", raising=False)
    base_url, handler, server = run_recording_server()
    try:
        provider = OllamaProvider(base_url=base_url, credential_ref="env:AIDO_OLLAMA_MISSING_KEY")
        health = provider.health_check()
        models = provider.list_models()
        with pytest.raises(RuntimeError):
            provider.chat_completion(
                ModelRequest(model="llama3", messages=[{"role": "user", "content": "hi"}])
            )
    finally:
        server.shutdown()

    assert health.status != "available"
    assert health.health_status == "misconfigured"
    assert "credentialref" in health.message.lower()
    assert models == []
    # Fail closed: a configured-but-unresolvable credential must NOT downgrade to an anonymous probe.
    assert handler.seen == []


def test_ollama_status_probe_fails_closed_when_credential_ref_is_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIDO_OLLAMA_MISSING_KEY", raising=False)
    base_url, handler, server = run_recording_server()
    try:
        status = ollama_status(base_url=base_url, credential_ref="env:AIDO_OLLAMA_MISSING_KEY")
    finally:
        server.shutdown()

    assert status["available"] is False
    assert status["models"] == []
    assert "credentialref" in status["reason"].lower()
    assert handler.seen == []


def test_ollama_status_probe_defers_remote_secret_store_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_SECRET_VAULT_ADDR", "https://127.0.0.1:1")
    monkeypatch.setenv("AIDO_SECRET_VAULT_TOKEN", "test-vault-token")
    base_url, handler, server = run_recording_server()
    try:
        status = ollama_status(base_url=base_url, credential_ref="openbao:secret/ollama#token")
    finally:
        server.shutdown()

    assert status["available"] is False
    assert "health-check" in status["reason"].lower()
    # No per-poll Ollama probe AND no per-poll remote secret fetch (Vault not contacted on status).
    assert handler.seen == []


def test_openai_compatible_treats_explicit_zero_usage_as_provider_reported() -> None:
    provider = OpenAICompatibleProvider(
        provider_id="openai_compatible", base_url="https://example.invalid/v1"
    )

    zeros = provider.parse_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
    empty = provider.parse_usage({"usage": {}})

    assert zeros.input_tokens == 0
    assert zeros.output_tokens == 0
    assert zeros.total_tokens == 0
    assert zeros.raw_usage["usage_source"] == "provider"
    assert empty.raw_usage == {
        "usage_source": "unknown",
        "reason": "provider_response_missing_usage",
    }
