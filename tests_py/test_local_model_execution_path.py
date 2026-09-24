"""Ruta de ejecución local: timeout acotado por deadline, formato validado y causas estables."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents import local_endpoint_lease
from local_control_center.agents.local_endpoint_lease import local_endpoint_slot
from local_control_center.agents.local_runtime_causes import LocalRuntimeError
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers import http_transport
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.providers.http_transport import ResponseTooLargeError
from local_control_center.agents.runtime_adapters import RuntimeAdapterRegistry, RuntimeExecutionRequest
from local_control_center.agents.runtime_failure_classifier import classify_local_model_error
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import ScriptedChatReply, reasoning_llm_server

PROVIDER = "llama-local"
MODEL = "qwen3-reasoner"
MESSAGES = [{"role": "user", "content": "Return JSON."}]


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "lane.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def register_local_account(connection, base_url: str, *, catalog_id: str = "llama_cpp") -> None:
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": PROVIDER,
            "displayName": "llama.cpp local",
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "baseUrl": base_url,
            "enabled": True,
        }
    )
    store.set_provider_catalog_id(PROVIDER, catalog_id)
    store.upsert_model({"providerId": PROVIDER, "model": MODEL, "enabled": True})


def execute_local(connection, tmp_path: Path, *, timeout_seconds: int = 120, **call_input):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    request = RuntimeExecutionRequest.model_validate(
        {
            "projectId": "project-local",
            "jobId": "job-local",
            "agentRunId": "agent-run-local",
            "workspaceId": "workspace-local",
            "workspacePath": str(workspace),
            "capability": "chat",
            "timeoutSeconds": timeout_seconds,
            "input": {"providerId": PROVIDER, "model": MODEL, "messages": MESSAGES, **call_input},
        }
    )
    return RuntimeAdapterRegistry(connection=connection, artifact_root=tmp_path).execute(
        "openai_compatible", request
    )


def spy_timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    seen: list[float] = []
    real = http_transport.urlopen_fail_closed

    def spy(request, *, timeout):
        seen.append(timeout)
        return real(request, timeout=timeout)

    monkeypatch.setattr("local_control_center.agents.providers.openai_compatible.urlopen_fail_closed", spy)
    return seen


def nearly_exhausted_deadline(tmp_path: Path) -> ProcessExecutionContext:
    return ProcessExecutionContext(
        db_path=tmp_path / "lane.sqlite", execution_deadline_monotonic=time.monotonic() + 60
    )


def closed_port_base_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


def test_local_call_uses_the_requested_budget_instead_of_sixty_seconds(lane, tmp_path, monkeypatch):
    seen = spy_timeouts(monkeypatch)
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        result = execute_local(lane, tmp_path, timeout_seconds=120)

    assert result.status == "completed"
    assert seen == [pytest.approx(120, abs=1)]


def test_expected_model_switch_adds_cold_start_capped_by_the_local_ceiling(lane, tmp_path, monkeypatch):
    RuntimeConfigRepository(lane).set_runtime_setting("runtime.local.maxCallSeconds", 200)
    seen = spy_timeouts(monkeypatch)
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        result = execute_local(lane, tmp_path, timeout_seconds=120, coldStartExpected=True)

    assert result.status == "completed"
    assert seen == [pytest.approx(200, abs=1)]


def test_a_local_ceiling_below_the_cold_start_runs_with_the_ceiling(lane, tmp_path, monkeypatch):
    RuntimeConfigRepository(lane).set_runtime_setting("runtime.local.maxCallSeconds", 120)
    seen = spy_timeouts(monkeypatch)
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        result = execute_local(lane, tmp_path, timeout_seconds=120, coldStartExpected=True)

    assert result.status == "completed"
    assert result.failure_cause is None
    assert seen == [pytest.approx(120, abs=1)]


def test_nearly_exhausted_deadline_bounds_the_call(lane, tmp_path, monkeypatch):
    seen = spy_timeouts(monkeypatch)
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        with execution_scope(nearly_exhausted_deadline(tmp_path)):
            result = execute_local(lane, tmp_path, timeout_seconds=120)

    assert result.status == "completed"
    assert len(seen) == 1
    assert 40 <= seen[0] <= 45


def test_deadline_that_cannot_cover_a_cold_start_fails_before_invoking(lane, tmp_path):
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        with execution_scope(nearly_exhausted_deadline(tmp_path)):
            result = execute_local(lane, tmp_path, timeout_seconds=120, coldStartExpected=True)

    assert result.status == "unavailable"
    assert result.failure_cause == "insufficient_time_for_model_load"
    assert result.provider_attempted is False
    assert server.requests == []


@pytest.mark.parametrize(
    ("reply", "cause"),
    [
        (
            ScriptedChatReply(
                status=401,
                error_body={
                    "error": {
                        "code": 401,
                        "message": "Invalid API Key sk-leakedkey0123456789",
                        "type": "authentication_error",
                    }
                },
            ),
            "local_auth_required",
        ),
        (
            ScriptedChatReply(
                status=503,
                error_body={"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
            ),
            "model_loading",
        ),
        (
            ScriptedChatReply(
                status=400,
                error_body={
                    "error": {
                        "code": 400,
                        "message": "the request exceeds the available context size, try increasing it",
                        "type": "exceed_context_size_error",
                    }
                },
            ),
            "context_length_exceeded",
        ),
        (
            ScriptedChatReply(
                status=500,
                error_body={"error": {"code": 500, "message": "failed to load model 'qwen3-reasoner'"}},
            ),
            "local_model_load_failed",
        ),
    ],
    ids=["auth", "loading", "context", "load-failed"],
)
def test_local_server_errors_map_to_stable_causes_with_redacted_reasons(lane, tmp_path, reply, cause):
    with reasoning_llm_server([reply]) as server:
        register_local_account(lane, server.base_url)
        result = execute_local(lane, tmp_path)

    assert result.status == "unavailable"
    assert result.failure_cause == cause
    assert cause in (result.reason or "")
    assert "sk-leakedkey" not in (result.reason or "")
    assert result.provider_attempted is True


def test_refused_connection_is_local_server_unreachable(lane, tmp_path):
    register_local_account(lane, closed_port_base_url())

    result = execute_local(lane, tmp_path, timeout_seconds=10)

    assert result.status == "unavailable"
    assert result.failure_cause == "local_server_unreachable"


def test_timeouts_and_oversized_bodies_are_not_misread_as_unreachable():
    assert classify_local_model_error(error=TimeoutError("timed out"), http_status=None) is None
    assert classify_local_model_error(error=ResponseTooLargeError("too big"), http_status=None) is None
    refused = classify_local_model_error(error=ConnectionRefusedError(), http_status=None)
    assert refused is not None
    assert refused.cause == "local_server_unreachable"


def test_max_tokens_and_validated_json_schema_reach_the_local_server(lane, tmp_path):
    schema = {
        "name": "patch",
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    }
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        execute_local(lane, tmp_path, maxTokens=256, structuredOutput="json_schema", responseSchema=schema)
        execute_local(lane, tmp_path, maxTokens=256)

    with_schema, without_schema = (item["body"] for item in server.requests)
    assert with_schema["max_tokens"] == 256
    assert with_schema["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "patch", "schema": schema["schema"], "strict": True},
    }
    assert "maxTokens" not in with_schema
    assert "metadata" not in with_schema
    assert "chat_template_kwargs" not in with_schema
    assert "response_format" not in without_schema


def _held_slot(database: Path):
    return local_endpoint_slot(
        lambda: open_sqlite_connection(database), PROVIDER, limit=1, ttl_seconds=600, wait_seconds=0
    )


def test_a_held_endpoint_slot_fails_the_call_within_its_deadline(lane, tmp_path, monkeypatch):
    monkeypatch.setattr(local_endpoint_lease, "LOCAL_ENDPOINT_WAIT_SECONDS", 20.0)
    database = tmp_path / "lane.sqlite"
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        deadline = ProcessExecutionContext(
            db_path=database, execution_deadline_monotonic=time.monotonic() + 18
        )
        with _held_slot(database), execution_scope(deadline):
            started = time.monotonic()
            result = execute_local(lane, tmp_path, timeout_seconds=120)
            elapsed = time.monotonic() - started

    assert result.status == "unavailable"
    assert result.failure_cause == "local_endpoint_busy"
    assert server.requests == []
    assert elapsed < 10


def test_a_held_endpoint_slot_never_outwaits_the_call_timeout(lane, tmp_path):
    database = tmp_path / "lane.sqlite"
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        with _held_slot(database):
            started = time.monotonic()
            result = execute_local(lane, tmp_path, timeout_seconds=2)
            elapsed = time.monotonic() - started

    assert result.status == "unavailable"
    assert result.failure_cause == "local_endpoint_busy"
    assert server.requests == []
    assert elapsed < 10


def test_the_http_timeout_is_what_remains_after_waiting_for_the_slot(lane, tmp_path, monkeypatch):
    seen = spy_timeouts(monkeypatch)
    database = tmp_path / "lane.sqlite"
    held = _held_slot(database)
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        register_local_account(lane, server.base_url)
        held.__enter__()
        releaser = threading.Timer(2.0, held.__exit__, args=(None, None, None))
        releaser.start()
        try:
            deadline = ProcessExecutionContext(
                db_path=database, execution_deadline_monotonic=time.monotonic() + 21
            )
            with execution_scope(deadline):
                result = execute_local(lane, tmp_path, timeout_seconds=120)
        finally:
            releaser.join()

    assert result.status == "completed"
    assert len(seen) == 1
    assert seen[0] <= 4.5


def test_busy_local_endpoint_keeps_its_cause_and_is_not_attempted(lane, tmp_path, monkeypatch):
    def busy(_request):
        raise LocalRuntimeError("local_endpoint_busy", "slot 0 is held")

    register_local_account(lane, "http://127.0.0.1:1/v1")
    monkeypatch.setattr(
        ProviderAdapterFactory,
        "resolve_for_execution",
        lambda *_args: SimpleNamespace(
            base_url="http://127.0.0.1:1/v1", credential_ref="", chat_completion=busy
        ),
    )

    result = execute_local(lane, tmp_path)

    assert result.status == "unavailable"
    assert result.failure_cause == "local_endpoint_busy"
    assert (result.reason or "").endswith("execution failed: local_endpoint_busy")
    assert result.provider_attempted is False
