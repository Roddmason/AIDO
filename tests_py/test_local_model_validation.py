"""Validación real por (provider, modelo) de un runtime local: JSON, 1024 tokens y razonamiento apagado.

@author Rodrigo Mason
"""

from __future__ import annotations

import io
import json
from contextlib import closing
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.local_runtime_causes import LocalRuntimeError
from local_control_center.agents.model_execution_health import (
    model_validation_rejection,
    record_model_execution,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_catalog import LLAMA_CPP_LOCAL_PROFILE
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.runtime_team import probe
from local_control_center.runtime_team.probe import RuntimeValidationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_provider_setup_catalog import auth_headers, create_client, enable_remote_provider


class _States:
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states

    def get(self, account, *, max_wait_s: float = 1.0) -> dict[str, str]:
        return dict(self.states)


class _LocalProvider:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.requests: list = []

    def chat_completion(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return ModelResponse(
            providerId="llama_cpp", model=request.model, content=outcome, usage=UsageRecord()
        )


def _http_error(code: int, body: bytes = b"") -> HTTPError:
    return HTTPError("http://127.0.0.1:1/v1/chat/completions", code, "error", Message(), io.BytesIO(body))


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(local_model_state, "LOAD_STATE_CACHE", _States({}))
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        store = ProviderAccountStore(handle)
        store.upsert_provider_account(
            {
                "providerId": "llama_cpp",
                "displayName": "llama.cpp",
                "providerType": "local",
                "apiFormat": "openai_compatible",
                "providerFamily": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        store.set_provider_catalog_id("llama_cpp", "llama_cpp")
        for model in ("gemma-a", "qwen-b"):
            store.upsert_model({"providerId": "llama_cpp", "model": model, "enabled": True, "source": "test"})
        yield handle


def _use(monkeypatch: pytest.MonkeyPatch, provider: _LocalProvider) -> None:
    monkeypatch.setattr(probe, "provider_instance", lambda provider_id, *, connection: provider)


def _evidence_rows(connection) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM model_execution_health WHERE provider_id = 'llama_cpp'"
    ).fetchone()[0]


def test_local_validation_probes_the_resolved_model_with_json_schema_and_reasoning_off(
    connection, monkeypatch
):
    LocalModelSettingsRepository(connection).upsert("llama_cpp", "qwen-b", actor="operator", is_default=True)
    provider = _LocalProvider(['{"ok": true}'])
    _use(monkeypatch, provider)
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None)
    assert (result["status"], result["model"]) == ("validated", "qwen-b")
    request = provider.requests[0]
    assert (request.model, request.max_tokens) == ("qwen-b", 1024)
    assert request.response_format["type"] == "json_schema"
    assert request.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert LocalModelSettingsRepository(connection).json_schema_enabled("llama_cpp", "qwen-b") is True
    assert model_validation_rejection(connection, "llama_cpp", "qwen-b") is None


def test_a_rejected_json_schema_falls_back_to_prompt_json(connection, monkeypatch):
    body = io.BytesIO(b"")
    rejected = HTTPError("http://127.0.0.1:1/v1/chat/completions", 400, "error", Message(), body)
    provider = _LocalProvider([rejected, '```json\n{"ok": true}\n```'])
    _use(monkeypatch, provider)
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (result["status"], result["model"]) == ("validated", "gemma-a")
    assert provider.requests[1].response_format is None
    assert LocalModelSettingsRepository(connection).json_schema_enabled("llama_cpp", "gemma-a") is False
    assert body.closed, "the rejected 400 must release its socket before the retry"


def test_a_reply_that_is_not_the_json_object_fails_validation(connection, monkeypatch):
    _use(monkeypatch, _LocalProvider(["ok"]))
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (result["status"], result["reason"]) == ("failed", "model_validation_invalid_json")
    assert model_validation_rejection(connection, "llama_cpp", "gemma-a") == "model_validation_failed"


@pytest.mark.parametrize(
    "outcome",
    [
        LocalRuntimeError("model_loading", "Loading model"),
        _http_error(503),
        _http_error(500, b'{"error": {"code": 500, "message": "Loading model"}}'),
    ],
    ids=["model_loading", "http_503", "loading_text"],
)
def test_a_loading_model_defers_without_failure_evidence(connection, monkeypatch, outcome):
    _use(monkeypatch, _LocalProvider([outcome]))
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (result["status"], result["reason"]) == ("deferred", "model_loading")
    assert _evidence_rows(connection) == 0


def test_a_busy_endpoint_lease_defers_without_evidence_or_invalidation(connection, monkeypatch):
    record_model_execution(connection, "llama_cpp", "gemma-a", True, "test_prompt")
    _use(monkeypatch, _LocalProvider([LocalRuntimeError("local_endpoint_busy", "slot 0 is held")]))
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (result["status"], result["reason"]) == ("deferred", "local_endpoint_busy")
    assert _evidence_rows(connection) == 1
    assert model_validation_rejection(connection, "llama_cpp", "gemma-a") is None


def test_a_cold_start_timeout_defers_but_a_loaded_model_timeout_fails(connection, monkeypatch):
    monkeypatch.setattr(
        local_model_state, "LOAD_STATE_CACHE", _States({"gemma-a": "unloaded", "qwen-b": "loaded"})
    )
    _use(monkeypatch, _LocalProvider([TimeoutError("timed out")]))
    cold = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (cold["status"], cold["reason"]) == ("deferred", "model_loading")
    _use(monkeypatch, _LocalProvider([TimeoutError("timed out")]))
    warm = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="qwen-b")
    assert warm["status"] == "failed"


def test_a_model_that_is_not_loaded_gets_the_profile_cold_start_budget(connection, monkeypatch):
    """Sin el presupuesto de cold start, el default de chat (60 s) difiere siempre al modelo que carga lento."""
    monkeypatch.setattr(
        local_model_state, "LOAD_STATE_CACHE", _States({"gemma-a": "unloaded", "qwen-b": "loaded"})
    )
    provider = _LocalProvider([_http_error(400), '{"ok": true}', '{"ok": true}'])
    _use(monkeypatch, provider)
    cold = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    warm = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="qwen-b")
    assert (cold["status"], warm["status"]) == ("validated", "validated")
    cold_start = LLAMA_CPP_LOCAL_PROFILE.cold_start_timeout_s
    assert [request.timeout_seconds for request in provider.requests] == [cold_start, cold_start, None]


def test_a_requested_model_must_be_enabled(connection, monkeypatch):
    _use(monkeypatch, _LocalProvider([]))
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="missing")
    assert (result["status"], result["reason"]) == ("failed", "model_required")


@pytest.mark.parametrize(
    ("outcome", "cause", "rejection"),
    [
        (_http_error(401), "local_auth_required", "provider_authentication_cooldown"),
        (
            _http_error(500, b'{"error": {"code": 500, "message": "failed to load model"}}'),
            "local_model_load_failed",
            "model_validation_failed",
        ),
        (
            URLError(ConnectionRefusedError(10061, "connection refused")),
            "local_server_unreachable",
            "model_validation_failed",
        ),
    ],
    ids=["http_401", "load_failed_text", "connection_refused"],
)
def test_a_classified_local_failure_keeps_its_cause_as_the_reason(
    connection, monkeypatch, outcome, cause, rejection
):
    """Un 401/403 queda registrado con su estado HTTP, así que rechaza toda la cuenta (cooldown de auth)."""
    _use(monkeypatch, _LocalProvider([outcome]))
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (result["status"], result["reason"]) == ("failed", cause)
    assert model_validation_rejection(connection, "llama_cpp", "gemma-a") == rejection


def test_an_unreadable_reply_fails_with_failure_evidence(connection, monkeypatch):
    _use(monkeypatch, _LocalProvider([json.JSONDecodeError("Expecting value", "<html>proxy</html>", 0)]))
    result = RuntimeValidationService(connection).validate("llama_cpp", project_id=None, model="gemma-a")
    assert (result["status"], result["reason"]) == ("failed", "runtime_validation_failed")
    assert _evidence_rows(connection) == 1
    assert model_validation_rejection(connection, "llama_cpp", "gemma-a") == "model_validation_failed"


def test_a_bearer_never_travels_over_http_to_an_undeclared_lan_host(connection, monkeypatch):
    monkeypatch.setenv("AIDO_LOCAL_VALIDATION_TEST_TOKEN", "synthetic-local-token")
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": "lan-llama",
            "displayName": "llama.cpp LAN",
            "providerType": "local",
            "apiFormat": "openai_compatible",
            "providerFamily": "openai_compatible",
            "baseUrl": "http://192.168.1.50:8082/v1",
            "credentialRef": "env:AIDO_LOCAL_VALIDATION_TEST_TOKEN",
            "enabled": True,
        }
    )
    store.set_provider_catalog_id("lan-llama", "llama_cpp")
    store.upsert_model({"providerId": "lan-llama", "model": "gemma-a", "enabled": True, "source": "test"})
    provider = _LocalProvider(['{"ok": true}'])
    _use(monkeypatch, provider)
    result = RuntimeValidationService(connection).validate("lan-llama", project_id=None, model="gemma-a")
    assert (result["status"], result["reason"]) == ("failed", "insecure_credential_transport")
    assert provider.requests == []


def test_the_validate_runtime_route_accepts_the_model_to_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AIDO_TEAM_TEST_KEY", "unit-test-team-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        "deepseek",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_TEAM_TEST_KEY",
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle, handle:
        for model in ("deepseek-chat", "deepseek-reasoner"):
            ProviderAccountStore(handle).upsert_model(
                {"providerId": "deepseek", "model": model, "enabled": True}
            )
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _LocalProvider(["ok"]),
    )
    response = client.post(
        "/api/v1/model-gateway/providers/deepseek/validate-runtime",
        json={"model": "deepseek-reasoner"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["model"] == "deepseek-reasoner"
