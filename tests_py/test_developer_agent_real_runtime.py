from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.developer_agent_contract import developer_agent_readiness
from local_control_center.app import create_app
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy.git_command_runner import git_available, run_git
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
    monkeypatch.delenv("AIDO_ENABLE_CLI_RUNTIMES", raising=False)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    return store, client, auth_headers(client)


def create_git_project(
    store: ControlPlaneFixture, tmp_path: Path, name: str = "Developer Agent Project"
) -> dict[str, Any]:
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# Developer agent project\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=project_path).returncode == 0
    commit = run_git(
        [
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            "init",
        ],
        cwd=project_path,
    )
    assert commit.returncode == 0
    return store.create_project(name=name, path=project_path, template_id="other")


def controlled_developer_runtime_status(
    *, argv: list[str] | None = None, executable: bool = True
) -> list[dict[str, Any]]:
    return [
        {
            "id": "codex_cli",
            "kind": "cli",
            "displayName": "Controlled developer runtime",
            "configured": executable,
            "available": executable,
            "executable": executable,
            "requiresApproval": False,
            "reason": "Controlled runtime command is available."
            if executable
            else "CLI runtime is not configured.",
            "version": "test",
            "detectedCommand": sys.executable if executable else None,
            "developerAgentArgv": argv
            or [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('developer-agent-output.txt').write_text('real developer agent change\\n', encoding='utf-8'); "
                    'print(\'{"summary":"changed by controlled runtime"}\')'
                ),
            ],
            "healthCheckedAt": None,
            "capabilities": ["code_edit"],
            "safety": {
                "workspaceBound": True,
                "shell": False,
                "structuredArgv": True,
                "network": "none",
            },
        }
    ]


class ControlledOllamaHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        payload = {"models": [{"name": "controlled-model"}]}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        agent_payload = {
            "summary": "changed by controlled ollama runtime",
            "files": [{"path": "ollama-agent-output.txt", "content": "real ollama developer agent change\n"}],
            "tests": [],
            "risks": [],
        }
        payload = {
            "message": {"content": json.dumps(agent_payload)},
            "prompt_eval_count": 1,
            "eval_count": 1,
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class ControlledOpenAICompatibleHandler(BaseHTTPRequestHandler):
    output_path = "openai-agent-output.txt"
    output_content = "real openai-compatible developer agent change\n"
    summary = "changed by controlled openai-compatible runtime"

    def do_GET(self) -> None:
        if self.path != "/models":
            self.send_response(404)
            self.end_headers()
            return
        payload = {"data": [{"id": "controlled-model"}]}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        agent_payload = {
            "summary": self.summary,
            "files": [
                {
                    "path": self.output_path,
                    "content": self.output_content,
                }
            ],
            "tests": [],
            "risks": [],
        }
        payload = {
            "choices": [{"message": {"content": json.dumps(agent_payload)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class ControlledNvidiaNimHandler(ControlledOpenAICompatibleHandler):
    output_path = "nvidia-agent-output.txt"
    output_content = "real nvidia nim developer agent change\n"
    summary = "changed by controlled nvidia nim runtime"


def start_controlled_ollama_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def start_controlled_openai_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledOpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def start_controlled_nvidia_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledNvidiaNimHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def test_developer_agent_status_reports_contract_and_no_executable_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_developer_runtime_status(executable=False),
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/agents/developer/status")

    assert response.status_code == 200
    body = response.json()["developerAgent"]
    assert body["contract"]["id"] == "developer_agent"
    assert body["contract"]["requiredWorkspace"] is True
    assert body["contract"]["requiredEvidence"] is True
    assert "shell" in body["contract"]["allowedTools"]
    assert "workspace_patch" in body["contract"]["allowedTools"]
    assert "code_edit" in body["contract"]["requiredRuntimeCapabilities"]
    assert body["executable"] is False
    assert "runtime" in body["reason"].lower()
    assert "internal_mock" not in str(body)


def test_developer_agent_readiness_accepts_configured_remote_model_runtimes() -> None:
    statuses = [
        {"id": "openrouter", "executable": True, "configured": True, "capabilities": ["chat"]},
        {"id": "nvidia_nim", "executable": True, "configured": True, "capabilities": ["chat"]},
        {"id": "anthropic_api", "executable": True, "configured": True, "capabilities": ["chat"]},
    ]

    readiness = developer_agent_readiness(statuses, preferred_runtime="nvidia_nim")

    assert readiness["executable"] is True
    assert readiness["selectedRuntimeId"] == "nvidia_nim"
    assert readiness["candidateRuntimeIds"] == ["openrouter", "nvidia_nim", "anthropic_api"]


def test_developer_agent_readiness_accepts_a_named_ollama_endpoint() -> None:
    statuses = [
        {
            "id": "edge-ollama",
            "providerFamily": "ollama",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
            "models": ["edge-model"],
        }
    ]

    readiness = developer_agent_readiness(statuses, preferred_runtime="edge-ollama")

    assert readiness["executable"] is True
    assert readiness["selectedRuntimeId"] == "edge-ollama"


def test_developer_agent_requires_workspace_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = store.create_project(name="No Workspace", path=tmp_path / "no-workspace", template_id="other")

    response = client.post(
        "/api/v1/agents/developer/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "taskId": "developer-agent-no-workspace",
            "instruction": "Create a file.",
            "preferredRuntime": "codex_cli",
        },
    )

    assert response.status_code == 422
    assert "workspace" in str(response.json()["detail"]).lower()


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_developer_agent_real_cli_runtime_changes_only_workspace_and_creates_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Developer Runtime Complete")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="developer-agent-complete",
        agent_id="developer_agent",
        reason="developer agent test workspace",
        isolation_type="git_worktree",
    )
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_developer_runtime_status(),
    )

    response = client.post(
        "/api/v1/agents/developer/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "developer-agent-complete",
            "instruction": "Create developer-agent-output.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["runtimeResult"]["status"] == "completed"
    assert "changed by controlled runtime" in body["runtimeResult"]["stdout"]
    assert body["qaResults"][0]["status"] == "passed"
    assert "developer-agent-output.txt" in body["diffSummary"]["changedFiles"]
    assert body["diffSummary"]["patchArtifactId"].startswith("artifact-")
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert body["evidencePackage"]["id"] in body["agentRun"]["output"]["evidence_refs"]
    assert Path(workspace["path"], "developer-agent-output.txt").exists()
    assert not Path(project["path"], "developer-agent-output.txt").exists()
    overview = client.get("/api/v1/overview").json()
    decisions = [
        decision for decision in overview["permissionDecisions"] if decision["agentId"] == "developer_agent"
    ]
    assert any(
        (decision["payload"] or {}).get("operation") == "developer_agent_runtime" for decision in decisions
    )
    qa_decisions = [
        decision for decision in overview["permissionDecisions"] if decision["agentId"] == "qa_agent"
    ]
    assert any(
        (decision["payload"] or {}).get("operation") == "qa_agent_command" for decision in qa_decisions
    )
    assert "internal_mock" not in str(body)


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_developer_agent_real_cli_runtime_with_failing_qa_finishes_qa_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Developer Runtime QA Fail")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="developer-agent-qa-fail",
        agent_id="developer_agent",
        reason="developer agent qa test workspace",
        isolation_type="git_worktree",
    )
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_developer_runtime_status(),
    )

    response = client.post(
        "/api/v1/agents/developer/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "developer-agent-qa-fail",
            "instruction": "Create developer-agent-output.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "-m", "pytest", "missing_developer_agent_qa.py"]],
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "qa_failed"
    assert body["agentRun"]["status"] == "failed"
    assert body["qaResults"][0]["status"] == "failed"
    assert body["evidencePackage"]["qaVerdict"] == "failed"
    assert "developer-agent-output.txt" in body["diffSummary"]["changedFiles"]


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_developer_agent_ollama_runtime_applies_structured_patch_in_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, base_url = start_controlled_ollama_server()
    try:
        monkeypatch.setenv("AIDO_OLLAMA_BASE_URL", base_url)
        store, client, headers = create_client(tmp_path, monkeypatch)
        store.connection.execute("UPDATE provider_accounts SET enabled = 1 WHERE provider_id = 'ollama'")
        project = create_git_project(store, tmp_path, name="Developer Ollama Runtime")
        workspace = store.workspaces.allocate_workspace(
            project_id=project["id"],
            task_id="developer-agent-ollama",
            agent_id="developer_agent",
            reason="developer agent ollama test workspace",
            isolation_type="git_worktree",
        )

        response = client.post(
            "/api/v1/agents/developer/runs",
            headers=headers,
            json={
                "projectId": project["id"],
                "workspaceId": workspace["id"],
                "taskId": "developer-agent-ollama",
                "instruction": "Create ollama-agent-output.txt.",
                "preferredRuntime": "ollama",
                "model": "controlled-model",
                "qaCommands": [[sys.executable, "--version"]],
                "requireApproval": False,
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "completed"
        assert body["runtimeResult"]["modelCall"]["status"] == "completed"
        assert body["runtimeResult"]["patchApply"]["status"] == "completed"
        assert body["runtimeResult"]["outputArtifactId"].startswith("artifact-")
        assert "ollama-agent-output.txt" in body["diffSummary"]["changedFiles"]
        assert body["evidencePackage"]["qaVerdict"] == "passed"
        assert Path(workspace["path"], "ollama-agent-output.txt").exists()
        assert not Path(project["path"], "ollama-agent-output.txt").exists()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_developer_agent_openai_compatible_runtime_applies_structured_patch_in_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, base_url = start_controlled_openai_server()
    try:
        store, client, headers = create_client(tmp_path, monkeypatch)
        monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", base_url)
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "unit-test-openai-compatible-key")
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_MODEL", "controlled-model")
        RuntimeConfigRepository(store.connection).set_runtime_setting("runtime.remote.enabled", True)
        store.connection.execute(
            "UPDATE runtime_installations SET enabled = 1 WHERE runtime_id = 'openai_compatible'"
        )
        store.connection.execute(
            "UPDATE provider_accounts SET enabled = 1 WHERE provider_id = 'openai_compatible'"
        )
        health = client.post(
            "/api/v1/model-gateway/providers/openai_compatible/health-check", headers=headers
        )
        assert health.status_code == 200
        assert health.json()["health"]["healthStatus"] == "healthy"
        project = create_git_project(store, tmp_path, name="Developer OpenAI Compatible Runtime")
        workspace = store.workspaces.allocate_workspace(
            project_id=project["id"],
            task_id="developer-agent-openai-compatible",
            agent_id="developer_agent",
            reason="developer agent openai-compatible test workspace",
            isolation_type="git_worktree",
        )

        response = client.post(
            "/api/v1/agents/developer/runs",
            headers=headers,
            json={
                "projectId": project["id"],
                "workspaceId": workspace["id"],
                "taskId": "developer-agent-openai-compatible",
                "instruction": "Create openai-agent-output.txt.",
                "preferredRuntime": "openai_compatible",
                "model": "controlled-model",
                "qaCommands": [[sys.executable, "--version"]],
                "requireApproval": False,
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "completed"
        assert body["runtimeResult"]["modelCall"]["status"] == "completed"
        assert body["runtimeResult"]["patchApply"]["status"] == "completed"
        assert body["runtimeResult"]["outputArtifactId"].startswith("artifact-")
        assert "openai-agent-output.txt" in body["diffSummary"]["changedFiles"]
        assert body["evidencePackage"]["qaVerdict"] == "passed"
        assert Path(workspace["path"], "openai-agent-output.txt").exists()
        assert not Path(project["path"], "openai-agent-output.txt").exists()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_developer_agent_nvidia_nim_runtime_applies_structured_patch_in_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, base_url = start_controlled_nvidia_server()
    try:
        store, client, headers = create_client(tmp_path, monkeypatch)
        monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
        monkeypatch.setenv("AIDO_NVIDIA_BASE_URL", base_url)
        monkeypatch.setenv("AIDO_NVIDIA_API_KEY", "unit-test-nvidia-key")
        monkeypatch.setenv("AIDO_NVIDIA_MODEL", "controlled-model")
        runtime_settings = RuntimeConfigRepository(store.connection)
        runtime_settings.set_runtime_setting("runtime.remote.enabled", True)
        runtime_settings.set_runtime_setting("runtime.nvidia.enabled", True)
        store.connection.execute(
            "UPDATE runtime_installations SET enabled = 1 WHERE runtime_id = 'nvidia_nim'"
        )
        store.connection.execute("UPDATE provider_accounts SET enabled = 1 WHERE provider_id = 'nvidia_nim'")
        health = client.post("/api/v1/model-gateway/providers/nvidia_nim/health-check", headers=headers)
        assert health.status_code == 200
        assert health.json()["health"]["healthStatus"] == "healthy"
        project = create_git_project(store, tmp_path, name="Developer NVIDIA Runtime")
        workspace = store.workspaces.allocate_workspace(
            project_id=project["id"],
            task_id="developer-agent-nvidia-nim",
            agent_id="developer_agent",
            reason="developer agent nvidia-nim test workspace",
            isolation_type="git_worktree",
        )

        response = client.post(
            "/api/v1/agents/developer/runs",
            headers=headers,
            json={
                "projectId": project["id"],
                "workspaceId": workspace["id"],
                "taskId": "developer-agent-nvidia-nim",
                "instruction": "Create nvidia-agent-output.txt.",
                "preferredRuntime": "nvidia_nim",
                "model": "controlled-model",
                "qaCommands": [[sys.executable, "--version"]],
                "requireApproval": False,
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "completed"
        assert body["runtimeResult"]["modelCall"]["status"] == "completed"
        assert body["runtimeResult"]["patchApply"]["status"] == "completed"
        assert body["runtimeResult"]["outputArtifactId"].startswith("artifact-")
        assert "nvidia-agent-output.txt" in body["diffSummary"]["changedFiles"]
        assert body["evidencePackage"]["qaVerdict"] == "passed"
        assert Path(workspace["path"], "nvidia-agent-output.txt").exists()
        assert not Path(project["path"], "nvidia-agent-output.txt").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_developer_agent_ui_and_openapi_expose_executable_status() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (
        root / "local-control-center" / "web" / "src" / "features" / "agents" / "AgentsPage.tsx"
    ).read_text(encoding="utf-8")
    client = (root / "local-control-center" / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    openapi = (root / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts").read_text(
        encoding="utf-8"
    )

    assert "DeveloperAgent" in page
    assert "getDeveloperAgentStatus" in client
    assert "/api/v1/agents/developer/status" in openapi
    assert "DeveloperAgentStatusResponse" in openapi
