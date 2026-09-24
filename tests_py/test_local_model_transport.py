"""Transporte de proveedores: body explícito, timeout del llamador, lectura acotada y razonamiento."""

from __future__ import annotations

import io
import json
import time
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError

import pytest

from local_control_center.agents.local_runtime_causes import LocalRuntimeError
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers import http_transport
from local_control_center.agents.providers.azure_openai import AzureOpenAIProvider
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.providers.http_transport import ResponseTooLargeError
from local_control_center.agents.providers.ollama import OllamaProvider
from local_control_center.agents.providers.openai_api import OpenAIAPIProvider
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.agents.providers.openrouter import OpenRouterProvider
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import ScriptedChatReply, reasoning_llm_server

MESSAGES = [{"role": "user", "content": "Return JSON."}]
REMOTE_KEY_ENV = "AIDO_TRANSPORT_TEST_KEY"


def local_provider(base_url: str) -> OpenAICompatibleProvider:
    """Provider como lo deja la factory para una cuenta local (send_output_limit=True)."""
    provider = OpenAICompatibleProvider(
        provider_id="llama-local",
        base_url=base_url,
        credential_ref="",
        credential_required=False,
        use_legacy_fallbacks=False,
    )
    provider.send_output_limit = True
    return provider


def spy_timeouts(monkeypatch: pytest.MonkeyPatch, target: str) -> list[float]:
    seen: list[float] = []
    real = http_transport.urlopen_fail_closed

    def spy(request, *, timeout):
        seen.append(timeout)
        return real(request, timeout=timeout)

    monkeypatch.setattr(target, spy)
    return seen


def test_openai_compatible_body_contains_only_protocol_fields() -> None:
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        local_provider(server.base_url).chat_completion(
            ModelRequest(
                model="qwen3-reasoner",
                messages=MESSAGES,
                temperature=0.2,
                maxTokens=321,
                metadata={"trace": "internal-only"},
                responseFormat={"type": "json_object"},
                extraBody={"chat_template_kwargs": {"enable_thinking": False}, "model": "must-not-override"},
            )
        )

    assert server.requests[-1]["body"] == {
        "model": "qwen3-reasoner",
        "messages": MESSAGES,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 321,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_openai_compatible_drops_reasoning_from_content_and_raw_response() -> None:
    reply = ScriptedChatReply(
        content='<think>plan reasoning-marker-7f3a9c</think>\n{"ok": true}',
        reasoning_content="more reasoning-marker-7f3a9c notes",
    )
    with reasoning_llm_server([reply]) as server:
        response = local_provider(server.base_url).chat_completion(
            ModelRequest(model="qwen3-reasoner", messages=MESSAGES)
        )

    assert response.content == '{"ok": true}'
    assert response.reasoning_present is True
    assert response.finish_reason == "stop"
    assert "reasoning-marker-7f3a9c" not in json.dumps(response.raw_response)
    assert response.usage.raw_usage["usage_source"] == "provider"


def test_length_cut_with_only_reasoning_reports_flags_and_empty_content() -> None:
    reply = ScriptedChatReply(content="", reasoning_content="still thinking", finish_reason="length")
    with reasoning_llm_server([reply]) as server:
        response = local_provider(server.base_url).chat_completion(
            ModelRequest(model="qwen3-reasoner", messages=MESSAGES, maxTokens=64)
        )

    assert response.content == ""
    assert response.finish_reason == "length"
    assert response.reasoning_present is True


def test_openai_compatible_uses_the_request_timeout_and_defaults_to_sixty(monkeypatch) -> None:
    seen = spy_timeouts(
        monkeypatch, "local_control_center.agents.providers.openai_compatible.urlopen_fail_closed"
    )
    with reasoning_llm_server([ScriptedChatReply(content="ok")]) as server:
        provider = local_provider(server.base_url)
        provider.chat_completion(ModelRequest(model="qwen3-reasoner", messages=MESSAGES, timeoutSeconds=7.5))
        provider.chat_completion(ModelRequest(model="qwen3-reasoner", messages=MESSAGES))

    assert seen == [7.5, 60]


def test_the_http_timeout_is_cut_to_the_call_deadline() -> None:
    now = time.monotonic()
    assert ModelRequest(model="m", messages=MESSAGES, timeoutSeconds=30).http_timeout(60) == 30
    assert ModelRequest(model="m", messages=MESSAGES).http_timeout(60) == 60
    cut = ModelRequest(model="m", messages=MESSAGES, timeoutSeconds=30, deadlineMonotonic=now + 5)
    assert 0 < cut.http_timeout(60) <= 5
    assert "deadlineMonotonic" not in cut.model_dump(by_alias=True)
    exhausted = {"model": "m", "messages": MESSAGES, "deadlineMonotonic": now - 1}
    with pytest.raises(LocalRuntimeError, match="local_endpoint_busy"):
        ModelRequest.model_validate(exhausted).http_timeout(60)
    with pytest.raises(LocalRuntimeError, match="insufficient_time_for_model_load"):
        ModelRequest.model_validate({**exhausted, "coldStartExpected": True}).http_timeout(60)


def test_oversized_chat_response_is_rejected() -> None:
    oversized = b'{"choices": [' + b" " * 4096 + b"]}"
    with reasoning_llm_server([ScriptedChatReply(raw_body=oversized)]) as server:
        provider = local_provider(server.base_url)
        provider.max_response_bytes = 1024
        with pytest.raises(ResponseTooLargeError):
            provider.chat_completion(ModelRequest(model="qwen3-reasoner", messages=MESSAGES))


def test_declared_content_length_over_the_limit_is_rejected_before_reading() -> None:
    class DeclaredResponse:
        headers = {"Content-Length": "4096"}

        def read(self, _size: int = -1) -> bytes:
            raise AssertionError("the body must not be read")

    with pytest.raises(ResponseTooLargeError):
        http_transport.read_bounded(DeclaredResponse(), limit=1024)


def test_http_error_excerpt_is_bounded_and_closes_the_body() -> None:
    body = io.BytesIO(b"x" * 10_000)
    error = HTTPError("http://127.0.0.1:1/v1/chat/completions", 500, "boom", {}, body)

    assert http_transport.http_error_excerpt(error, limit=100) == "x" * 100
    assert body.closed


def test_ollama_provider_sends_num_predict_format_timeout_and_real_usage_source(monkeypatch) -> None:
    seen = spy_timeouts(monkeypatch, "local_control_center.agents.providers.ollama.urlopen_fail_closed")
    reply = ScriptedChatReply(content="<think>x</think>done", usage=None, finish_reason="length")
    with reasoning_llm_server([reply]) as server:
        response = OllamaProvider(provider_id="ollama-lan", base_url=server.root_url).chat_completion(
            ModelRequest(
                model="qwen3",
                messages=MESSAGES,
                maxTokens=64,
                timeoutSeconds=12,
                responseFormat={"type": "json_object"},
            )
        )

    body = server.requests[-1]["body"]
    assert body["options"] == {"num_predict": 64}
    assert body["format"] == "json"
    assert seen == [12]
    assert response.content == "done"
    assert response.finish_reason == "length"
    assert response.reasoning_present is True
    assert response.usage.raw_usage["usage_source"] == "unknown"


@pytest.mark.parametrize(
    "build",
    [
        lambda base_url: OpenAIAPIProvider(base_url=base_url, credential_ref=f"env:{REMOTE_KEY_ENV}"),
        lambda base_url: AzureOpenAIProvider(base_url=base_url, credential_ref=f"env:{REMOTE_KEY_ENV}"),
        lambda base_url: OpenRouterProvider(
            provider_id="openrouter-fixture", base_url=base_url, credential_ref=f"env:{REMOTE_KEY_ENV}"
        ),
    ],
    ids=["openai", "azure", "openrouter"],
)
def test_remote_openai_dialect_bodies_never_carry_output_limit_or_metadata(build, monkeypatch) -> None:
    monkeypatch.setenv(REMOTE_KEY_ENV, "unit-test-remote-key")
    with reasoning_llm_server([ScriptedChatReply(content="ok")]) as server:
        provider = build(server.base_url)
        provider.chat_completion(
            ModelRequest(
                model="remote-model",
                messages=MESSAGES,
                maxTokens=16,
                metadata={"trace": "internal-only"},
            )
        )

    body = server.requests[-1]["body"]
    assert provider.send_output_limit is False
    assert {"max_tokens", "maxTokens", "metadata"}.isdisjoint(body)
    assert body["model"] == "remote-model"


def test_factory_enables_the_output_limit_only_for_local_model_runtimes(tmp_path: Path) -> None:
    with (
        reasoning_llm_server([ScriptedChatReply(content="ok")]) as server,
        closing(open_sqlite_connection(tmp_path / "factory.sqlite")) as connection,
    ):
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        for provider_id, provider_type, base_url in (
            ("llama-local", "local", server.base_url),
            ("fixture-api", "api", "https://fixture.example.invalid/v1"),
        ):
            store.upsert_provider_account(
                {
                    "providerId": provider_id,
                    "providerType": provider_type,
                    "providerFamily": "openai_compatible",
                    "apiFormat": "openai_compatible",
                    "baseUrl": base_url,
                    "enabled": True,
                }
            )
        store.set_provider_catalog_id("llama-local", "llama_cpp")
        factory = ProviderAdapterFactory(connection)
        local = factory.resolve("llama-local")
        remote = factory.resolve("fixture-api")
        local.chat_completion(ModelRequest(model="qwen3-reasoner", messages=MESSAGES, maxTokens=321))

    assert local.send_output_limit is True
    assert remote.send_output_limit is False
    assert server.requests[-1]["body"]["max_tokens"] == 321
    assert "maxTokens" not in server.requests[-1]["body"]
