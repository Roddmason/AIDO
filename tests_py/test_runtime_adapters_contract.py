from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from local_control_center.agents.model_gateway import ollama_status
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.anthropic_api import AnthropicAPIProvider
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.ollama import OllamaProvider
from local_control_center.agents.runtime_adapters import (
    AnthropicAdapter,
    CliVersionAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    ProviderFactoryAdapter,
    RestrictedSubprocessAdapter,
    RuntimeAdapterRegistry,
    RuntimeExecutionRequest,
)
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ADAPTER_FILES = [
    ROOT / "local_control_center" / "agents" / "runtime_adapters.py",
    ROOT / "local_control_center" / "agents" / "runtime_registry.py",
    *(ROOT / "local_control_center" / "agents" / "cli_runtimes").glob("*.py"),
    *(ROOT / "local_control_center" / "agents" / "providers").glob("*.py"),
]


@contextmanager
def redirect_transport_servers(
    *,
    redirect_paths: set[str],
    success_payloads: dict[str, dict[str, object]],
    redirect_codes: dict[str, int] | None = None,
) -> Iterator[tuple[str, dict[str, list[dict[str, object]]]]]:
    """Run an origin and redirect target while recording every received header/body."""
    state: dict[str, list[dict[str, object]]] = {
        "originRequests": [],
        "targetRequests": [],
    }

    class JsonHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _request_record(self, body: bytes) -> dict[str, object]:
            return {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "xApiKey": self.headers.get("x-api-key"),
                "body": body.decode("utf-8"),
            }

        def _send_json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class TargetHandler(JsonHandler):
        def _capture(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            state["targetRequests"].append(self._request_record(body))
            self._send_json(
                {
                    "data": [{"id": "redirected-model"}],
                    "models": [{"name": "redirected-model"}],
                    "choices": [{"message": {"content": "redirected response"}}],
                    "message": {"content": "redirected response"},
                    "content": [{"type": "text", "text": "redirected response"}],
                }
            )

        do_GET = _capture
        do_POST = _capture

    target_server = HTTPServer(("127.0.0.1", 0), TargetHandler)
    target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
    target_thread.start()
    target_url = f"http://127.0.0.1:{target_server.server_port}/redirect-target"

    class OriginHandler(JsonHandler):
        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            state["originRequests"].append(self._request_record(body))
            if self.path in redirect_paths:
                self.send_response((redirect_codes or {}).get(self.path, 302))
                self.send_header("Location", target_url)
                self.end_headers()
                return
            self._send_json(success_payloads.get(self.path, {}))

        do_GET = _respond
        do_POST = _respond

    origin_server = HTTPServer(("127.0.0.1", 0), OriginHandler)
    origin_thread = threading.Thread(target=origin_server.serve_forever, daemon=True)
    origin_thread.start()
    try:
        yield f"http://127.0.0.1:{origin_server.server_port}", state
    finally:
        origin_server.shutdown()
        origin_server.server_close()
        origin_thread.join(timeout=5)
        target_server.shutdown()
        target_server.server_close()
        target_thread.join(timeout=5)


def runtime_request(tmp_path: Path, **overrides: object) -> RuntimeExecutionRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    payload = {
        "projectId": "project-runtime",
        "workflowRunId": "workflow-run-runtime",
        "workflowStepId": "workflow-step-runtime",
        "jobId": "job-runtime",
        "agentRunId": "agent-run-runtime",
        "workspaceId": "workspace-runtime",
        "workspacePath": str(workspace),
        "capability": "version_check",
        "argv": [sys.executable, "--version"],
        "input": {},
        "timeoutSeconds": 5,
        "approvalGrantId": "grant-runtime",
        "metadata": {},
    }
    payload.update(overrides)
    return RuntimeExecutionRequest.model_validate(payload)


def register_runtime_workspace(connection, request: RuntimeExecutionRequest) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (
            id, project_id, task_id, owner_agent_id, path, status,
            isolation_type, metadata, created_at, updated_at, archived_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.workspace_id,
            request.project_id,
            request.capability,
            "implementer",
            request.workspace_path,
            "ready",
            "directory",
            "{}",
            now,
            now,
            None,
        ),
    )
    connection.commit()


def test_restricted_subprocess_rejects_command_string(tmp_path: Path) -> None:
    request = runtime_request(tmp_path, argv="python --version")

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "blocked"
    assert result.exit_code is None
    assert "structured argv" in (result.reason or "").lower()


def test_restricted_subprocess_requires_argv(tmp_path: Path) -> None:
    request = runtime_request(tmp_path, argv=[])

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "blocked"
    assert "argv" in (result.reason or "").lower()


def test_restricted_subprocess_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    request = runtime_request(tmp_path, metadata={"cwd": str(outside)})

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "blocked"
    assert "outside the allocated workspace" in (result.reason or "").lower()


def test_restricted_subprocess_records_real_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "sleep.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    request = runtime_request(
        tmp_path,
        workspacePath=str(workspace),
        argv=[sys.executable, str(script)],
        timeoutSeconds=1,
    )

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert result.started_at
    assert result.completed_at
    assert "timed out" in (result.reason or "").lower()


def test_restricted_subprocess_promotes_large_stdout_and_stderr_to_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "large_output.py"
    script.write_text(
        "import sys\nsys.stdout.write('stdout-line-' * 1400)\nsys.stderr.write('stderr-line-' * 1400)\n",
        encoding="utf-8",
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        adapter = RestrictedSubprocessAdapter(connection=connection, artifact_root=tmp_path)
        request = runtime_request(
            tmp_path,
            workspacePath=str(workspace),
            argv=[sys.executable, str(script)],
        )
        register_runtime_workspace(connection, request)

        result = adapter.execute(request)

        assert result.status == "completed"
        assert result.stdout_artifact_id and result.stdout_artifact_id.startswith("artifact-")
        assert result.stderr_artifact_id and result.stderr_artifact_id.startswith("artifact-")
        assert result.evidence_package_id and result.evidence_package_id.startswith("evidence-")
        artifacts = EvidenceRepository(connection).list_artifacts(result.evidence_package_id)
        artifact_ids = {artifact["id"] for artifact in artifacts}
        assert {result.stdout_artifact_id, result.stderr_artifact_id} <= artifact_ids
        for artifact in artifacts:
            assert Path(artifact["path"]).exists()
            assert artifact["kind"] == "execution_log"


def test_restricted_subprocess_requires_registered_workspace_when_connected(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        adapter = RestrictedSubprocessAdapter(connection=connection, artifact_root=tmp_path)
        request = runtime_request(tmp_path)

        result = adapter.execute(request)

        assert result.status == "blocked"
        assert "registered workspace" in (result.reason or "").lower()


def test_restricted_subprocess_never_uses_shell_true(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    real_popen = subprocess.Popen

    def capture_popen(*args: object, **kwargs: object):
        calls.append(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr("local_control_center.security_policy.sandbox.subprocess.Popen", capture_popen)
    request = runtime_request(tmp_path)

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "completed"
    assert calls
    assert calls[0]["shell"] is False


def test_cli_version_adapter_rejects_non_version_execution(tmp_path: Path) -> None:
    request = runtime_request(tmp_path, capability="code_edit", argv=["codex", "exec", "change files"])

    result = CliVersionAdapter(adapter_id="codex").execute(request)

    assert result.status == "blocked"
    assert "version_check" in (result.reason or "")


def test_cli_version_adapter_reports_unavailable_when_executable_is_missing(tmp_path: Path) -> None:
    missing_executable = tmp_path / "missing-codex.exe"
    request = runtime_request(tmp_path, argv=[str(missing_executable), "--version"])

    result = CliVersionAdapter(adapter_id="codex").execute(request)

    assert result.status == "unavailable"
    assert result.exit_code is None
    assert result.reason


def test_provider_adapters_require_real_configuration(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "AIDO_OLLAMA_BASE_URL",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "AIDO_OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_API_KEY",
        "AIDO_OPENAI_COMPATIBLE_MODEL",
        "OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_ENABLE_REAL_PROVIDER_CALLS",
    ):
        monkeypatch.delenv(name, raising=False)

    ollama = OllamaAdapter().execute(runtime_request(tmp_path, capability="chat", argv=[]))
    openai_compatible = OpenAICompatibleAdapter().execute(
        runtime_request(tmp_path, capability="chat", argv=[])
    )

    assert ollama.status == "configuration_required"
    assert openai_compatible.status == "configuration_required"
    assert ollama.reason
    assert openai_compatible.reason


def test_openai_compatible_adapter_fails_closed_on_health_redirect(tmp_path: Path) -> None:
    with (
        redirect_transport_servers(
            redirect_paths={"/v1/models"},
            success_payloads={},
        ) as (origin_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        health = OpenAICompatibleAdapter(
            base_url=f"{origin_url}/v1",
            api_key="origin-only-openai-token",
            model="origin-model",
            connection=connection,
        ).health_check()

    assert health["status"] == "unavailable"
    assert state["originRequests"][0]["authorization"] == "Bearer origin-only-openai-token"
    assert state["targetRequests"] == []


def test_openai_compatible_adapter_fails_closed_on_chat_redirect(tmp_path: Path) -> None:
    with (
        redirect_transport_servers(
            redirect_paths={"/v1/chat/completions"},
            success_payloads={},
            redirect_codes={"/v1/chat/completions": 307},
        ) as (origin_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "model": "origin-model",
                "messages": [{"role": "user", "content": "Do not follow redirects."}],
            },
        )
        register_runtime_workspace(connection, request)
        result = OpenAICompatibleAdapter(
            base_url=f"{origin_url}/v1",
            api_key="origin-only-openai-token",
            model="origin-model",
            connection=connection,
            artifact_root=tmp_path,
        ).execute(request)

    assert result.status == "unavailable"
    assert state["originRequests"][0]["method"] == "POST"
    assert state["originRequests"][0]["authorization"] == "Bearer origin-only-openai-token"
    assert state["targetRequests"] == []


def test_credentialed_ollama_adapter_fails_closed_on_health_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_REDIRECT_OLLAMA_TOKEN", "origin-only-ollama-token")
    with (
        redirect_transport_servers(
            redirect_paths={"/api/tags"},
            success_payloads={},
        ) as (origin_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "ollama",
            {
                "enabled": True,
                "baseUrl": origin_url,
                "credentialRef": "env:AIDO_REDIRECT_OLLAMA_TOKEN",
            },
        )
        health = OllamaAdapter(connection=connection, environ={}).health_check()

    assert health["status"] == "unavailable"
    assert state["originRequests"][0]["authorization"] == "Bearer origin-only-ollama-token"
    assert state["targetRequests"] == []


def test_credentialed_ollama_adapter_fails_closed_on_chat_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_REDIRECT_OLLAMA_TOKEN", "origin-only-ollama-token")
    with (
        redirect_transport_servers(
            redirect_paths={"/api/chat"},
            success_payloads={"/api/tags": {"models": [{"name": "origin-model"}]}},
        ) as (origin_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "ollama",
            {
                "enabled": True,
                "baseUrl": origin_url,
                "credentialRef": "env:AIDO_REDIRECT_OLLAMA_TOKEN",
            },
        )
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "model": "origin-model",
                "messages": [{"role": "user", "content": "Do not follow redirects."}],
            },
        )
        register_runtime_workspace(connection, request)
        result = OllamaAdapter(
            connection=connection,
            artifact_root=tmp_path,
            environ={},
        ).execute(request)

    assert result.status == "unavailable"
    assert [item["method"] for item in state["originRequests"]] == ["GET", "POST"]
    assert all(
        item["authorization"] == "Bearer origin-only-ollama-token"
        for item in state["originRequests"]
    )
    assert state["targetRequests"] == []


def test_ollama_provider_fails_closed_on_health_list_and_chat_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_REDIRECT_OLLAMA_TOKEN", "origin-only-ollama-token")
    with redirect_transport_servers(
        redirect_paths={"/api/tags", "/api/chat"},
        success_payloads={},
    ) as (origin_url, state):
        provider = OllamaProvider(
            base_url=origin_url,
            credential_ref="env:AIDO_REDIRECT_OLLAMA_TOKEN",
        )

        health = provider.health_check()
        models = provider.list_models()
        with pytest.raises(urllib.error.HTTPError):
            provider.chat_completion(
                ModelRequest(
                    model="origin-model",
                    messages=[{"role": "user", "content": "Do not follow redirects."}],
                )
            )

    assert health.status == "not_available"
    assert models == []
    assert state["targetRequests"] == []


def test_ollama_runtime_status_fails_closed_on_health_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_REDIRECT_OLLAMA_TOKEN", "origin-only-ollama-token")
    with redirect_transport_servers(
        redirect_paths={"/api/tags"},
        success_payloads={},
    ) as (origin_url, state):
        status = ollama_status(
            base_url=origin_url,
            credential_ref="env:AIDO_REDIRECT_OLLAMA_TOKEN",
        )

    assert status["available"] is False
    assert state["originRequests"][0]["authorization"] == "Bearer origin-only-ollama-token"
    assert state["targetRequests"] == []


def test_anthropic_provider_fails_closed_on_health_list_and_chat_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_REDIRECT_ANTHROPIC_TOKEN", "origin-only-anthropic-token")
    with redirect_transport_servers(
        redirect_paths={"/v1/models", "/v1/messages"},
        success_payloads={},
    ) as (origin_url, state):
        provider = AnthropicAPIProvider(
            base_url=f"{origin_url}/v1",
            credential_ref="env:AIDO_REDIRECT_ANTHROPIC_TOKEN",
        )

        health = provider.health_check()
        models = provider.list_models()
        with pytest.raises(urllib.error.HTTPError):
            provider.chat_completion(
                ModelRequest(
                    model="claude-origin",
                    messages=[{"role": "user", "content": "Do not follow redirects."}],
                )
            )

    assert health.status == "not_available"
    assert models == []
    assert state["targetRequests"] == []


def test_anthropic_adapter_fails_closed_on_health_and_chat_redirects(tmp_path: Path) -> None:
    with (
        redirect_transport_servers(
            redirect_paths={"/v1/models", "/v1/messages"},
            success_payloads={},
        ) as (origin_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
        adapter = AnthropicAdapter(
            base_url=f"{origin_url}/v1",
            api_key="origin-only-anthropic-token",
            model="claude-origin",
            connection=connection,
            artifact_root=tmp_path,
        )
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "model": "claude-origin",
                "messages": [{"role": "user", "content": "Do not follow redirects."}],
            },
        )
        register_runtime_workspace(connection, request)

        health = adapter.health_check()
        result = adapter.execute(request)

    assert health["status"] == "unavailable"
    assert result.status == "unavailable"
    assert all(
        item["xApiKey"] == "origin-only-anthropic-token"
        for item in state["originRequests"]
    )
    assert state["targetRequests"] == []


def test_ollama_adapter_executes_against_persisted_remote_endpoint(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request, timeout: int):
        calls.append((request.full_url, request.get_header("Authorization")))
        if request.full_url.endswith("/api/tags"):
            return Response(b'{"models":[{"name":"controlled-model"}]}')
        return Response(b'{"message":{"content":"persisted endpoint reply"}}')

    monkeypatch.setattr("local_control_center.agents.runtime_adapters.urlopen", fake_urlopen)
    monkeypatch.setenv("AIDO_TEST_OLLAMA_TOKEN", "controlled-bearer-token")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1, base_url = ?, credential_ref = ?
            WHERE provider_id = 'ollama'
            """,
            ("http://remote-ollama.invalid:11434", "env:AIDO_TEST_OLLAMA_TOKEN"),
        )
        connection.commit()
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "model": "controlled-model",
                "messages": [{"role": "user", "content": "Use the configured endpoint."}],
            },
        )
        register_runtime_workspace(connection, request)

        result = OllamaAdapter(connection=connection, artifact_root=tmp_path, environ={}).execute(request)

    assert result.status == "completed"
    assert calls == [
        ("http://remote-ollama.invalid:11434/api/tags", "Bearer controlled-bearer-token"),
        ("http://remote-ollama.invalid:11434/api/chat", "Bearer controlled-bearer-token"),
    ]


def test_ollama_adapter_never_forwards_persisted_bearer_to_an_override_host(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request, timeout: int):
        calls.append((request.full_url, request.get_header("Authorization")))
        payload = (
            b'{"models":[{"name":"controlled-model"}]}'
            if request.full_url.endswith("/api/tags")
            else b'{"message":{"content":"override reply"}}'
        )
        return Response(payload)

    monkeypatch.setattr("local_control_center.agents.runtime_adapters.urlopen", fake_urlopen)
    monkeypatch.setenv("AIDO_TEST_OLLAMA_TOKEN", "must-not-leave-the-persisted-host")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1, base_url = ?, credential_ref = ?
            WHERE provider_id = 'ollama'
            """,
            ("http://persisted-ollama.invalid:11434", "env:AIDO_TEST_OLLAMA_TOKEN"),
        )
        connection.commit()
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "model": "controlled-model",
                "messages": [{"role": "user", "content": "Use the override endpoint."}],
            },
        )
        register_runtime_workspace(connection, request)

        result = OllamaAdapter(
            connection=connection,
            artifact_root=tmp_path,
            environ={"AIDO_OLLAMA_BASE_URL": "http://override-ollama.invalid:11434"},
        ).execute(request)

    assert result.status == "completed"
    assert calls == [
        ("http://override-ollama.invalid:11434/api/tags", None),
        ("http://override-ollama.invalid:11434/api/chat", None),
    ]


def test_ollama_adapter_executes_the_endpoint_selected_in_the_request(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request, timeout: int):
        calls.append(request.full_url)
        payload = (
            b'{"models":[{"name":"edge-model"}]}'
            if request.full_url.endswith("/api/tags")
            else b'{"message":{"content":"edge reply"}}'
        )
        return Response(payload)

    monkeypatch.setattr("local_control_center.agents.runtime_adapters.urlopen", fake_urlopen)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1, api_format = 'ollama', base_url = ?
            WHERE provider_id = 'ollama_remote'
            """,
            ("http://edge-ollama.invalid:11434",),
        )
        connection.commit()
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "providerId": "ollama_remote",
                "model": "edge-model",
                "messages": [{"role": "user", "content": "Use the selected endpoint."}],
            },
        )
        register_runtime_workspace(connection, request)

        result = OllamaAdapter(connection=connection, artifact_root=tmp_path, environ={}).execute(request)

    assert result.status == "completed"
    assert calls == [
        "http://edge-ollama.invalid:11434/api/tags",
        "http://edge-ollama.invalid:11434/api/chat",
    ]


def test_runtime_adapter_registry_reports_unavailable_for_unregistered_adapter(tmp_path: Path) -> None:
    result = RuntimeAdapterRegistry().execute("missing_adapter", runtime_request(tmp_path))

    assert result.status == "unavailable"
    assert "not registered" in (result.reason or "").lower()


def test_runtime_registry_routes_model_provider_families_through_factory() -> None:
    registry = RuntimeAdapterRegistry()

    for provider_family in (
        "ollama",
        "openai_compatible",
        "openrouter",
        "nvidia_nim",
        "anthropic_api",
    ):
        assert isinstance(registry.adapters[provider_family], ProviderFactoryAdapter)


def test_runtime_registry_keeps_credentialless_ollama_execution(
    tmp_path: Path,
) -> None:
    with (
        redirect_transport_servers(
            redirect_paths=set(),
            success_payloads={
                "/api/tags": {"models": [{"name": "local-model"}]},
                "/api/chat": {"message": {"content": "local response"}},
            },
        ) as (origin_url, state),
        open_sqlite_connection(tmp_path / "platform.sqlite") as connection,
    ):
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "ollama",
            {
                "enabled": True,
                "baseUrl": origin_url,
                "credentialRef": "",
            },
        )
        request = runtime_request(
            tmp_path,
            capability="chat",
            argv=[],
            input={
                "providerId": "ollama",
                "model": "local-model",
                "messages": [{"role": "user", "content": "Use local Ollama."}],
            },
        )
        register_runtime_workspace(connection, request)
        result = RuntimeAdapterRegistry(
            connection=connection,
            artifact_root=tmp_path,
        ).execute("ollama", request)

    assert result.status == "completed"
    assert state["originRequests"]
    assert all(item["authorization"] is None for item in state["originRequests"])


def test_product_runtime_adapter_files_do_not_define_mock_or_fake_adapters() -> None:
    prohibited = ("mock", "fake", "dummy", "placeholder", "demo")
    violations: list[str] = []
    for path in PRODUCT_ADAPTER_FILES:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip().lower()
            if not stripped.startswith("class ") or "adapter" not in stripped:
                continue
            if any(term in stripped for term in prohibited):
                violations.append(f"{path.relative_to(ROOT).as_posix()}:{line_number}: {line.strip()}")

    assert not violations
