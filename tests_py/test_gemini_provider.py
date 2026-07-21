from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.providers.gemini import (
    GEMINI_CONTEXT_WINDOW,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_OPENAI_BASE_URL,
    GeminiProvider,
)
from local_control_center.agents.runtime_provider_config import (
    AmbiguousRuntimeProviderCredentialError,
    list_runtime_provider_configurations,
    runtime_provider_configuration,
)
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _clear_gemini_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AIDO_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_gemini_chat_uses_official_openai_endpoint_and_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_gemini_credentials(monkeypatch)
    monkeypatch.setenv("AIDO_GEMINI_API_KEY", "unit-test-gemini-secret")
    monkeypatch.setenv("AIDO_GEMINI_MODEL", "gemini-3.5-flash")
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> _FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "choices": [{"message": {"content": "planned"}}],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 3,
                    "total_tokens": 11,
                },
            }
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.openai_compatible.urlopen_fail_closed",
        fake_urlopen,
    )
    provider = GeminiProvider()
    response = provider.chat_completion(
        ModelRequest(
            model="gemini-3.5-flash",
            messages=[{"role": "user", "content": "Plan the work"}],
        )
    )

    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == f"{GEMINI_OPENAI_BASE_URL}/chat/completions"
    assert request.get_header("Authorization") == "Bearer unit-test-gemini-secret"
    assert request.get_header("Content-type") == "application/json"
    assert captured["timeout"] == 60
    assert response.provider_id == "gemini"
    assert response.content == "planned"
    assert response.usage.input_tokens == 8
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 11


def test_gemini_list_models_enriches_only_documented_standard_free_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_TEST_API_KEY", "unit-test-gemini-list-secret")

    def fake_urlopen(_request: urllib.request.Request, *, timeout: float) -> _FakeResponse:
        assert timeout == 10
        return _FakeResponse(
            {
                "data": [
                    {"id": "gemini-3.5-flash"},
                    {"id": "models/gemini-3.1-flash-lite"},
                    {"id": "gemini-experimental-unknown"},
                ]
            }
        )

    monkeypatch.setattr(
        "local_control_center.agents.providers.openai_compatible.urlopen_fail_closed",
        fake_urlopen,
    )
    provider = GeminiProvider(credential_ref="env:GEMINI_TEST_API_KEY")
    models = {model.model: model for model in provider.list_models()}

    stable = models["gemini-3.5-flash"]
    assert stable.display_name == "Gemini 3.5 Flash"
    assert stable.context_window == GEMINI_CONTEXT_WINDOW
    assert stable.max_output_tokens == GEMINI_MAX_OUTPUT_TOKENS
    assert stable.supports_tools is True
    assert stable.supports_json is True
    assert stable.supports_streaming is True
    assert stable.supports_vision is True
    assert stable.supports_reasoning is True
    assert stable.free_tier is True
    assert models["gemini-3.1-flash-lite"].free_tier is True
    assert models["gemini-experimental-unknown"].free_tier is False


def test_gemini_parse_usage_preserves_standard_and_detailed_token_counters() -> None:
    provider = GeminiProvider(credential_ref="env:GEMINI_TEST_API_KEY")

    usage = provider.parse_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "prompt_tokens_details": {"cached_tokens": 6},
                "completion_tokens_details": {"reasoning_tokens": 2},
            }
        }
    )

    assert usage.input_tokens == 10
    assert usage.cached_input_tokens == 6
    assert usage.output_tokens == 4
    assert usage.reasoning_tokens == 2
    assert usage.total_tokens == 14
    assert usage.raw_usage["usage_source"] == "provider"


def test_gemini_runtime_config_prefers_aido_then_gemini_and_never_google() -> None:
    aido = runtime_provider_configuration(
        "gemini",
        environ={
            "AIDO_GEMINI_API_KEY": "same-secret",
            "GEMINI_API_KEY": "same-secret",
            "GOOGLE_API_KEY": "same-secret",
            "AIDO_GEMINI_MODEL": "gemini-3.5-flash",
        },
    )
    fallback = runtime_provider_configuration(
        "gemini",
        environ={
            "GEMINI_API_KEY": "fallback-secret",
            "AIDO_GEMINI_MODEL": "gemini-3.5-flash",
        },
    )
    google_only = runtime_provider_configuration(
        "gemini",
        environ={
            "GOOGLE_API_KEY": "google-only-secret",
            "AIDO_GEMINI_MODEL": "gemini-3.5-flash",
        },
    )

    assert aido is not None
    assert aido.configured is True
    assert aido.configured_env_ref("apiKey") == "env:AIDO_GEMINI_API_KEY"
    assert "same-secret" not in str(aido.public_dict())
    assert fallback is not None
    assert fallback.configured is True
    assert fallback.configured_env_ref("apiKey") == "env:GEMINI_API_KEY"
    assert "fallback-secret" not in str(fallback.public_dict())
    assert google_only is not None
    assert google_only.configured is False
    assert google_only.configured_env_ref("apiKey") is None
    assert "AIDO_GEMINI_API_KEY" in google_only.missing


@pytest.mark.parametrize(
    "environ",
    [
        {"AIDO_GEMINI_API_KEY": "secret-a", "GEMINI_API_KEY": "secret-b"},
    ],
)
def test_gemini_runtime_config_rejects_ambiguous_credentials_without_leaking_values(
    environ: dict[str, str],
) -> None:
    with pytest.raises(AmbiguousRuntimeProviderCredentialError) as caught:
        runtime_provider_configuration("gemini", environ=environ, raise_on_ambiguity=True)

    message = str(caught.value)
    assert "ambiguous_runtime_provider_credential:gemini" in message
    assert "secret-a" not in message
    assert "secret-b" not in message


def test_gemini_credential_conflict_isolated_in_aggregate_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_gemini_credentials(monkeypatch)
    monkeypatch.setenv("AIDO_GEMINI_API_KEY", "secret-a")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-b")
    monkeypatch.setenv("AIDO_GEMINI_MODEL", "gemini-3.5-flash")

    configurations = {item["id"]: item for item in list_runtime_provider_configurations()}
    assert configurations["gemini"]["configured"] is False
    assert "Conflicting credential environment variables" in configurations["gemini"]["reason"]
    assert "secret-a" not in str(configurations)
    assert "secret-b" not in str(configurations)
    assert "ollama" in configurations

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1,
                credential_ref = 'env:PERSISTED_GEMINI_KEY',
                health_status = 'healthy',
                last_health_check_at = '2026-07-14T12:00:00Z'
            WHERE provider_id = 'gemini'
            """
        )
        statuses = RuntimeStatusService(connection).list_provider_statuses()

    gemini = next(item for item in statuses if item["id"] == "gemini")
    assert gemini["configured"] is False
    assert gemini["authenticated"] is False
    assert gemini["executable"] is False
    assert "Conflicting credential environment variables" in gemini["reason"]
    assert any(item["id"] == "ollama" for item in statuses)


def test_gemini_runtime_config_ignores_unrelated_google_api_key() -> None:
    configuration = runtime_provider_configuration(
        "gemini",
        environ={
            "AIDO_GEMINI_API_KEY": "gemini-secret",
            "GOOGLE_API_KEY": "different-google-service-secret",
            "AIDO_GEMINI_MODEL": "gemini-3.5-flash",
        },
    )

    assert configuration is not None
    assert configuration.configured is True
    assert configuration.configured_env_ref("apiKey") == "env:AIDO_GEMINI_API_KEY"


def test_factory_resolves_named_gemini_family_to_dedicated_adapter(tmp_path: Path) -> None:
    database_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "gemini-team-a",
                "displayName": "Gemini Team A",
                "providerType": "api",
                "apiFormat": "openai_compatible",
                "providerFamily": "gemini",
                "baseUrl": GEMINI_OPENAI_BASE_URL,
                "credentialRef": "env:GEMINI_TEAM_A_API_KEY",
                "enabled": True,
                "quotaMode": "free_tier_dynamic",
            }
        )

        provider = ProviderAdapterFactory(connection).resolve("gemini-team-a")

    assert isinstance(provider, GeminiProvider)
    assert provider.provider_id == "gemini-team-a"
    assert provider.base_url == GEMINI_OPENAI_BASE_URL
    assert provider.credential_ref == "env:GEMINI_TEAM_A_API_KEY"
