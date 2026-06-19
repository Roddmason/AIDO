from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.evidence.artifacts import write_text_artifact
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    return store, client, auth_headers(client)


def create_project_and_workspace(
    store: ControlPlaneFixture,
    tmp_path: Path,
    *,
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project_path = tmp_path / task_id
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text("# Architect agent test project\n", encoding="utf-8")
    project = store.create_project(name=f"Architect {task_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="architect_agent",
        reason="architect agent test workspace",
        isolation_type="directory",
    )
    return project, workspace


def create_diff_artifact(store: ControlPlaneFixture, project_id: str) -> dict[str, Any]:
    artifact_id = f"artifact-{uuid.uuid4()}"
    patch = (
        "diff --git a/local_control_center/agents/example.py b/local_control_center/agents/example.py\n"
        "--- a/local_control_center/agents/example.py\n"
        "+++ b/local_control_center/agents/example.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new brokered architecture path\n"
    )
    artifact_file = write_text_artifact(
        root=store.cwd, artifact_id=artifact_id, suffix=".patch", content=patch
    )
    return store.evidence.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=None,
        kind="git_patch",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"],
        metadata={"name": "architect-agent-input.diff", "source": "test", "mimeType": "text/x-diff"},
    )


def executable_openai_runtime_status() -> list[dict[str, Any]]:
    return [
        {
            "id": "openai_compatible",
            "kind": "api",
            "displayName": "Controlled OpenAI-compatible runtime",
            "detected": True,
            "configured": True,
            "available": True,
            "executable": True,
            "requiresApproval": False,
            "reason": "Controlled provider is executable.",
            "version": None,
            "detectedCommand": None,
            "healthCheckedAt": "2026-06-05T00:00:00Z",
            "capabilities": ["chat"],
            "requiredConfiguration": ["baseUrl", "apiKey", "model"],
            "safety": {
                "workspaceBound": False,
                "shell": False,
                "structuredArgv": True,
                "network": "remote_calls_disabled_by_default",
            },
        }
    ]


class ControlledArchitectProviderHandler(BaseHTTPRequestHandler):
    response_content = "{}"

    def do_GET(self) -> None:
        if self.path != "/models":
            self.send_response(404)
            self.end_headers()
            return
        payload = {"data": [{"id": "controlled-architect-model"}]}
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
        payload = {
            "choices": [{"message": {"content": self.response_content}}],
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


def start_controlled_provider(content: str) -> tuple[ThreadingHTTPServer, str]:
    ControlledArchitectProviderHandler.response_content = content
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledArchitectProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def architect_request(
    project: dict[str, Any], workspace: dict[str, Any], diff_artifact: dict[str, Any]
) -> dict[str, Any]:
    return {
        "projectId": project["id"],
        "workspaceId": workspace["id"],
        "taskId": "architect-review",
        "diffArtifactId": diff_artifact["id"],
        "workflowContext": {
            "workflowRunId": "workflow-run-architect",
            "workflowStepId": "workflow-step-architecture-review",
            "title": "Review brokered architecture change",
        },
        "relevantDocs": [
            {
                "id": "docs/security-policy.md",
                "title": "Security policy",
                "content": "All execution must pass ToolBroker and produce evidence.",
            }
        ],
        "testResults": [
            {
                "id": "test-result-architect",
                "status": "passed",
                "command": "pytest tests_py/test_execution_boundary_architecture.py",
                "evidenceRefs": ["test-evidence-1"],
            }
        ],
        "riskRegister": [],
        "evidenceRefs": ["test-evidence-1"],
        "preferredRuntime": "openai_compatible",
    }


def test_architect_agent_without_real_runtime_returns_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: [],
    )
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="architect-unavailable")
    diff_artifact = create_diff_artifact(store, project["id"])

    response = client.post(
        "/api/v1/agents/architect/runs",
        headers=headers,
        json=architect_request(project, workspace, diff_artifact),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "runtime_unavailable"
    assert body["reason"]
    assert body["architectureDecision"] is None
    assert body["riskEntries"] == []
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert store.governance.list_architecture_decisions(project["id"]) == []
    assert store.governance.list_risks(project["id"]) == []
    assert "internal_mock" not in str(body)


def test_architect_agent_valid_provider_output_persists_decision_and_risks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="architect-valid")
    diff_artifact = create_diff_artifact(store, project["id"])
    output = {
        "verdict": "changes_required",
        "architectureFindings": [
            {
                "title": "Broker operation required",
                "severity": "medium",
                "description": "The diff adds an architecture review path that must stay behind ToolBroker.",
                "evidenceRefs": [diff_artifact["id"], "test-evidence-1"],
            }
        ],
        "risks": [
            {
                "title": "Architect model call could bypass policy",
                "severity": "high",
                "description": "The new agent path must not call the model provider directly.",
                "mitigation": "Use a scoped architect_agent_model_call policy operation through ToolBroker.",
                "evidenceRefs": [diff_artifact["id"]],
            }
        ],
        "requiredChanges": [
            {
                "title": "Add policy operation",
                "description": "Add a scoped policy branch and architecture tests.",
                "evidenceRefs": [diff_artifact["id"]],
            }
        ],
        "approvalRecommendation": {
            "decision": "hold",
            "reason": "Architecture approval should wait for policy tests.",
            "evidenceRefs": [diff_artifact["id"]],
        },
        "evidenceRefs": [diff_artifact["id"], "test-evidence-1"],
    }
    server, base_url = start_controlled_provider(json.dumps(output))
    try:
        monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", base_url)
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "unit-test-openai-compatible-key")
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_MODEL", "controlled-architect-model")
        monkeypatch.setattr(
            "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
            lambda _service: executable_openai_runtime_status(),
        )

        response = client.post(
            "/api/v1/agents/architect/runs",
            headers=headers,
            json=architect_request(project, workspace, diff_artifact),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["output"]["verdict"] == "changes_required"
    assert body["runtimeResult"]["status"] == "completed"
    assert body["architectureDecision"]["id"].startswith("adr-")
    assert body["riskEntries"][0]["title"] == "Architect model call could bypass policy"
    assert body["riskEntries"][0]["evidenceRefs"] == [diff_artifact["id"]]
    assert body["evidencePackage"]["qaVerdict"] == "architecture_reviewed"
    assert body["agentRun"]["status"] == "completed"
    assert store.governance.list_architecture_decisions(project["id"])
    assert store.governance.list_risks(project["id"])
    overview = client.get("/api/v1/overview").json()
    decisions = [
        decision for decision in overview["permissionDecisions"] if decision["agentId"] == "architect_agent"
    ]
    assert any(
        (decision["payload"] or {}).get("operation") == "architect_agent_model_call" for decision in decisions
    )


def test_architect_agent_invalid_provider_output_fails_validation_without_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="architect-invalid")
    diff_artifact = create_diff_artifact(store, project["id"])
    server, base_url = start_controlled_provider(json.dumps({"verdict": "approved"}))
    try:
        monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", base_url)
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "unit-test-openai-compatible-key")
        monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_MODEL", "controlled-architect-model")
        monkeypatch.setattr(
            "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
            lambda _service: executable_openai_runtime_status(),
        )

        response = client.post(
            "/api/v1/agents/architect/runs",
            headers=headers,
            json=architect_request(project, workspace, diff_artifact),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed_validation"
    assert "schema" in body["reason"].lower() or "required" in body["reason"].lower()
    assert body["architectureDecision"] is None
    assert body["riskEntries"] == []
    assert body["evidencePackage"]["qaVerdict"] == "failed"
    assert store.governance.list_architecture_decisions(project["id"]) == []
    assert store.governance.list_risks(project["id"]) == []
