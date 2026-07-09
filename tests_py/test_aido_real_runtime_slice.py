from __future__ import annotations

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.providers.anthropic_api import AnthropicAPIProvider
from local_control_center.agents.providers.base import ProviderHealth
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.app import create_app
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.workflows.issue_to_patch_runner import _status_from_developer_result
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
    monkeypatch.delenv("AIDO_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("AIDO_GITHUB_REMOTE", raising=False)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    return store, client, auth_headers(client)


def create_project(store: ControlPlaneFixture, tmp_path: Path, name: str = "Patch Project") -> dict[str, Any]:
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    return store.create_project(name=name, path=project_path, template_id="other")


def clear_runtime_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AIDO_OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_API_KEY",
        "AIDO_OPENAI_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_MODEL",
        "OPENROUTER_API_KEY",
        "AIDO_OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "AIDO_OPENROUTER_BASE_URL",
        "AIDO_OPENROUTER_MODEL",
        "AIDO_NVIDIA_API_KEY",
        "AIDO_NVIDIA_BASE_URL",
        "AIDO_NVIDIA_MODEL",
        "NVIDIA_NIM_API_KEY",
        "AIDO_NVIDIA_NIM_API_KEY",
        "ANTHROPIC_API_KEY",
        "AIDO_ANTHROPIC_API_KEY",
        "AIDO_ANTHROPIC_MODEL",
        "AIDO_ANTHROPIC_BASE_URL",
        "LITELLM_API_KEY",
        "AIDO_LITELLM_API_KEY",
        "AIDO_OLLAMA_BASE_URL",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "AIDO_CODEX_COMMAND",
        "AIDO_CLAUDE_COMMAND",
        "AIDO_OPENHANDS_COMMAND",
        "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON",
        "AIDO_SWE_AGENT_COMMAND",
        "AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON",
        "CODEX_CLI_PATH",
        "CLAUDE_CODE_CLI_PATH",
        "OPENHANDS_CLI_PATH",
        "SWE_AGENT_CLI_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


REMOTE_PROVIDER_IDS = ("openai_compatible", "openrouter", "nvidia_nim", "anthropic_api")


def configure_remote_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "AIDO_OPENAI_COMPATIBLE_BASE_URL": "https://compatible.example.test/v1",
        "AIDO_OPENAI_COMPATIBLE_API_KEY": "unit-test-compatible-key",
        "AIDO_OPENAI_COMPATIBLE_MODEL": "configured_model",
        "AIDO_OPENROUTER_API_KEY": "unit-test-openrouter-key",
        "AIDO_OPENROUTER_MODEL": "openrouter/model",
        "AIDO_NVIDIA_API_KEY": "unit-test-nvidia-key",
        "AIDO_NVIDIA_BASE_URL": "https://nvidia.example.test/v1",
        "AIDO_NVIDIA_MODEL": "auto_best_available",
        "AIDO_ANTHROPIC_API_KEY": "unit-test-anthropic-key",
        "AIDO_ANTHROPIC_MODEL": "claude-test-model",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def enable_remote_provider_accounts(store: ControlPlaneFixture) -> None:
    runtime_settings = RuntimeConfigRepository(store.connection)
    runtime_settings.set_runtime_setting("runtime.remote.enabled", True)
    runtime_settings.set_runtime_setting("runtime.nvidia.enabled", True)
    store.connection.execute(
        """
        UPDATE runtime_installations
        SET enabled = 1
        WHERE runtime_id IN ('openai_compatible', 'openrouter', 'nvidia_nim', 'anthropic_api')
        """
    )
    store.connection.execute(
        """
        UPDATE provider_accounts
        SET enabled = 1,
            health_status = 'unknown',
            last_health_check_at = NULL,
            last_error = ''
        WHERE provider_id IN ('openai_compatible', 'openrouter', 'nvidia_nim', 'anthropic_api')
        """
    )


def create_git_project(
    store: ControlPlaneFixture, tmp_path: Path, name: str = "Patch Git Project"
) -> dict[str, Any]:
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# Patch project\n", encoding="utf-8")
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


def executable_runtime_status() -> list[dict[str, Any]]:
    return [
        {
            "id": "codex_cli",
            "kind": "cli",
            "displayName": "Codex CLI",
            "configured": True,
            "available": True,
            "executable": True,
            "requiresApproval": True,
            "reason": "Test runtime is executable.",
            "version": "test",
            "detectedCommand": sys.executable,
            "healthCheckedAt": None,
            "capabilities": ["code_edit"],
            "safety": {
                "workspaceBound": True,
                "shell": False,
                "structuredArgv": True,
                "network": "runtime_policy_gated",
            },
        }
    ]


def controlled_issue_to_patch_runtime_status(*, argv: list[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": "codex_cli",
            "kind": "cli",
            "displayName": "Controlled real runtime",
            "configured": True,
            "available": True,
            "executable": True,
            "requiresApproval": False,
            "reason": "Controlled runtime command is available for this test.",
            "version": "test",
            "detectedCommand": sys.executable,
            "developerAgentArgv": argv
            or [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('patched.txt').write_text('real runtime patch\\n', encoding='utf-8')",
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


def approve_issue_to_patch_for_integration(
    client: TestClient,
    headers: dict[str, str],
    created: dict[str, Any],
    *,
    reason: str = "Patch reviewed; QA evidence and diff artifact accepted.",
) -> dict[str, Any]:
    action = next(
        item
        for item in client.get("/api/v1/approvals").json()["actionRequests"]
        if item["jobId"] == created["job"]["id"]
        and item["actionType"] == "workflow.issue_to_patch.approve_patch"
    )
    approved_action = client.post(
        f"/api/v1/jobs/{created['job']['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": reason},
    )
    assert approved_action.status_code == 202

    approved = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/approve",
        headers=headers,
        json={"reason": "Human reviewer approves the patch for branch integration."},
    )
    assert approved.status_code == 202
    return approved.json()


def start_github_pr_http_mock(
    *,
    response_status: int = 201,
    response_payload: dict[str, Any] | None = None,
) -> tuple[ThreadingHTTPServer, str, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []
    payload = response_payload or {
        "id": 9001,
        "number": 42,
        "url": "https://api.github.test/repos/aido/patches/pulls/42",
        "html_url": "https://github.test/aido/patches/pull/42",
    }

    class GitHubPRHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(length)
            try:
                body = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                body = {"raw": raw_body.decode("utf-8", errors="replace")}
            requests.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                }
            )
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), GitHubPRHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return server, origin, requests


def create_promoted_issue_to_patch(
    store: ControlPlaneFixture,
    client: TestClient,
    headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch_name: str,
    project_name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = create_git_project(store, tmp_path, name=project_name)
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )
    created_response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch for pull request",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    )
    assert created_response.status_code == 202
    created = created_response.json()
    approved = approve_issue_to_patch_for_integration(client, headers, created)
    promoted_response = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/promote",
        headers=headers,
        json={"reason": "Promote approved patch before PR creation.", "branchName": branch_name},
    )
    assert promoted_response.status_code == 202
    promoted = promoted_response.json()
    assert promoted["status"] == "promoted_to_branch"
    return project, created, approved, promoted


def test_github_pull_request_config_is_not_required_for_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIDO_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("AIDO_GITHUB_REMOTE", raising=False)
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/workflows")

    assert response.status_code == 200


def test_workflow_detail_contract_exposes_full_audit_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path, name="Workflow Detail")
    workflow = store.workflows.create_workflow(
        project_id=project["id"],
        kind="issue_to_patch",
        title="Auditable workflow detail",
        metadata={"steps": ["implementation", "qa_validation", "technical_review"]},
    )
    started = store.workflows.start_workflow(workflow["id"], reason="audit detail")
    workflow_run = started["workflowRun"]
    implementation_step = next(step for step in started["workflowSteps"] if step["name"] == "implementation")

    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="workflow-detail",
        agent_id="developer_agent",
        workflow_run_id=workflow_run["id"],
        workflow_step_id=implementation_step["id"],
    )
    job_result = store.jobs.create_job(
        project_id=project["id"],
        kind="workflow.issue_to_patch",
        payload={
            "workflowRunId": workflow_run["id"],
            "workflowStepId": implementation_step["id"],
            "command": "issue_to_patch",
        },
        workflow_run_id=workflow_run["id"],
        workflow_step_id=implementation_step["id"],
    )
    claimed = store.jobs.claim_next_job(worker_id="workflow-detail-worker")
    assert claimed is not None
    job = claimed["job"]
    job_run = claimed["run"]
    action_request = store.jobs.create_action_request(
        job_id=job_result["job"]["id"],
        project_id=project["id"],
        action_type="workflow.issue_to_patch.approve_patch",
        risk_level="medium",
        command="workflow issue-to-patch approve",
        payload={
            "workflowRunId": workflow_run["id"],
            "workspaceId": workspace["id"],
            "runtimeId": "codex_cli",
        },
        reason="Human review is required before integration.",
    )
    profile = store.agents.upsert_agent_profile(
        {
            "id": "workflow_detail_agent",
            "name": "Workflow Detail Agent",
            "role": "developer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    agent_run = store.agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id="workflow-detail",
        input_payload={"workflowRunId": workflow_run["id"], "workspaceId": workspace["id"]},
        output_payload={"reason": "Runtime captured diff evidence."},
        job_id=job["id"],
        workflow_run_id=workflow_run["id"],
        workflow_step_id=implementation_step["id"],
        status="evidence_ready",
    )
    decision = store.security.record_decision(
        project_id=project["id"],
        workspace_id=workspace["id"],
        agent_id=profile["id"],
        role=profile["role"],
        tool="shell",
        command="git diff -- src/detail.py",
        path=workspace["path"],
        decision="allow",
        risk_level="medium",
        reason="Read-only diff capture is allowed.",
        payload={"workflowRunId": workflow_run["id"], "operation": "developer_agent_runtime"},
    )
    tool_call = store.agents.record_agent_tool_call(
        agent_run_id=agent_run["id"],
        tool_name="shell",
        status="completed",
        payload={"command": "git diff -- src/detail.py", "permissionDecisionId": decision["id"]},
    )
    model_call = store.agents.record_model_call(
        project_id=project["id"],
        agent_run_id=agent_run["id"],
        model_policy_id="implementation_default",
        provider="openai_compatible",
        model="configured_model",
        status="completed",
        prompt_tokens=17,
        completion_tokens=5,
        cost_usd=0.0,
        metadata={"workflowRunId": workflow_run["id"]},
    )
    evidence = store.evidence.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=workflow_run["id"],
        workflow_step_id=implementation_step["id"],
        agent_id=profile["id"],
        agent_run_id=agent_run["id"],
        job_id=job["id"],
        workspace_id=workspace["id"],
        runtime_id="codex_cli",
        task_id="workflow-detail",
        test_plan="Run real local QA.",
        test_results=[{"command": "uv run pytest tests_py -q", "status": "passed"}],
        diff_refs=[{"kind": "git_patch", "artifactId": "artifact-workflow-detail", "name": "diff.patch"}],
        model_calls=[{"id": model_call["id"], "status": model_call["status"]}],
        tool_calls=[{"id": tool_call["id"], "status": tool_call["status"]}],
        policy_decisions=[{"id": decision["id"], "decision": decision["decision"]}],
        approvals=[{"id": action_request["id"], "status": action_request["status"]}],
        evidence_source="qa_passed_by_command",
        qa_verdict="needs_human_review",
    )
    artifact = store.evidence.create_artifact(
        project_id=project["id"],
        evidence_package_id=evidence["id"],
        kind="git_patch",
        path=str(tmp_path / "diff.patch"),
        content_hash="sha256:workflow-detail",
        metadata={"name": "diff.patch", "workflowRunId": workflow_run["id"]},
        artifact_id="artifact-workflow-detail",
    )
    event = store.workflows.record_workflow_event(
        workflow_id=workflow["id"],
        workflow_run_id=workflow_run["id"],
        project_id=project["id"],
        step_id=implementation_step["id"],
        event_type="workflow.run.blocked",
        payload={"reason": "Human review is required before completion."},
        severity="warning",
    )

    response = client.get(f"/api/v1/workflows/{workflow['id']}")

    assert response.status_code == 200
    body = response.json()
    assert any(item["id"] == event["id"] for item in body["workflowEvents"])
    assert any(item["id"] == job_run["id"] for item in body["jobRuns"])
    assert any(item["id"] == action_request["id"] for item in body["actionRequests"])
    assert any(item["id"] == tool_call["id"] for item in body["agentToolCalls"])
    assert any(item["id"] == model_call["id"] for item in body["modelCalls"])
    assert any(item["id"] == decision["id"] for item in body["permissionDecisions"])
    assert any(item["id"] == artifact["id"] for item in body["artifacts"])
    assert any(item["evidencePackageId"] == evidence["id"] for item in body["testResultRecords"])
    run_detail = body["workflowRunDetails"][0]
    assert any(item["id"] == event["id"] for item in run_detail["workflowEvents"])
    assert any(item["id"] == job_run["id"] for item in run_detail["jobRuns"])
    assert any(item["id"] == action_request["id"] for item in run_detail["actionRequests"])
    assert any(item["id"] == tool_call["id"] for item in run_detail["agentToolCalls"])
    assert any(item["id"] == model_call["id"] for item in run_detail["modelCalls"])
    assert any(item["id"] == decision["id"] for item in run_detail["permissionDecisions"])
    assert any(item["id"] == artifact["id"] for item in run_detail["artifacts"])
    assert any(item["evidencePackageId"] == evidence["id"] for item in run_detail["testResultRecords"])


def test_runtime_provider_configuration_endpoint_detects_aido_env_without_exposing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    openai_compatible_api_key_env = "AIDO_OPENAI_COMPATIBLE_" + "API_KEY"
    openrouter_api_key_env = "AIDO_OPENROUTER_" + "API_KEY"
    nvidia_api_key_env = "AIDO_NVIDIA_" + "API_KEY"
    anthropic_api_key_env = "AIDO_ANTHROPIC_" + "API_KEY"
    secrets = {
        openai_compatible_api_key_env: "unit-test-compatible-key",
        openrouter_api_key_env: "unit-test-openrouter-key",
        nvidia_api_key_env: "unit-test-nvidia-key",
        anthropic_api_key_env: "unit-test-anthropic-key",
    }
    values = {
        **secrets,
        "AIDO_OPENAI_COMPATIBLE_BASE_URL": "https://compatible.example.test/v1",
        "AIDO_OPENAI_COMPATIBLE_MODEL": "vendor/model-compatible",
        "AIDO_OPENROUTER_MODEL": "openrouter/model",
        "AIDO_NVIDIA_BASE_URL": "https://nvidia.example.test/v1",
        "AIDO_NVIDIA_MODEL": "nvidia/model",
        "AIDO_ANTHROPIC_MODEL": "claude-test-model",
        "AIDO_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "AIDO_CODEX_COMMAND": "aido-cdx-runtime.exe",
        "AIDO_CLAUDE_COMMAND": "aido-cld-runtime.exe",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/provider-configuration")

    assert response.status_code == 200
    body = response.json()
    serialized = str(body)
    for value in values.values():
        assert value not in serialized
    assert "sk-" not in serialized
    providers = {provider["id"]: provider for provider in body["providers"]}
    assert providers["openai_compatible"]["configured"] is True
    assert providers["openai_compatible"]["missing"] == []
    api_key = next(
        variable
        for variable in providers["openai_compatible"]["variables"]
        if variable["name"] == "AIDO_OPENAI_COMPATIBLE_API_KEY"
    )
    assert api_key["configured"] is True
    assert api_key["secret"] is True
    assert api_key["fingerprint"].startswith("sha256:")
    assert len(api_key["fingerprint"]) < 80
    for provider_id in (
        "openrouter",
        "nvidia_nim",
        "anthropic_api",
        "ollama",
        "codex_cli",
        "claude_code_cli",
    ):
        assert providers[provider_id]["configured"] is True
        assert providers[provider_id]["missing"] == []

    stored_provider_text = str(store.connection.execute("SELECT * FROM provider_accounts").fetchall())
    for secret in secrets.values():
        assert secret not in stored_provider_text


def test_runtime_provider_configuration_endpoint_reports_missing_config_with_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/provider-configuration")

    assert response.status_code == 200
    providers = {provider["id"]: provider for provider in response.json()["providers"]}
    openai_compatible = providers["openai_compatible"]
    assert openai_compatible["configured"] is False
    assert openai_compatible["status"] == "configuration_required"
    assert openai_compatible["missing"] == [
        "AIDO_OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_API_KEY",
        "AIDO_OPENAI_COMPATIBLE_MODEL",
    ]
    assert "AIDO_OPENAI_COMPATIBLE_API_KEY" in openai_compatible["reason"]
    assert "configuration" in openai_compatible["reason"].lower()
    anthropic = providers["anthropic_api"]
    assert anthropic["configured"] is False
    assert anthropic["status"] == "configuration_required"
    assert anthropic["missing"] == [
        "AIDO_ANTHROPIC_API_KEY",
        "AIDO_ANTHROPIC_MODEL",
    ]
    assert "AIDO_ANTHROPIC_MODEL" in anthropic["reason"]


def test_runtime_provider_status_uses_aido_env_config_without_revealing_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", "https://compatible.example.test/v1")
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "unit-test-compatible-status-key")
    monkeypatch.setenv("AIDO_OPENAI_COMPATIBLE_MODEL", "vendor/model-compatible")
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    serialized = str(response.json())
    assert "unit-test-compatible-status-key" not in serialized
    assert "https://compatible.example.test" not in serialized
    provider = {item["id"]: item for item in response.json()["providers"]}["openai_compatible"]
    assert provider["configured"] is True
    assert provider["available"] is False
    assert provider["executable"] is False
    assert "health check" in provider["reason"].lower()


def test_anthropic_status_requires_aido_model_and_real_health_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("AIDO_ANTHROPIC_API_KEY", "unit-test-anthropic-status-key")
    monkeypatch.setenv("AIDO_ANTHROPIC_MODEL", "claude-test-model")
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    serialized = str(response.json())
    assert "unit-test-anthropic-status-key" not in serialized
    assert "claude-test-model" not in serialized
    provider = {item["id"]: item for item in response.json()["providers"]}["anthropic_api"]
    assert provider["configured"] is True
    assert provider["available"] is False
    assert provider["executable"] is False
    assert provider["requiredConfiguration"] == ["apiKey", "model"]
    assert "health check" in provider["reason"].lower()
    assert "not_implemented" not in provider["reason"]


def test_runtime_provider_status_does_not_report_api_available_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    body = response.json()
    providers = {provider["id"]: provider for provider in body["providers"]}
    assert body["api"]["available"] is False
    assert "internal_mock" not in body["runtimeModes"]
    assert "internal_mock" not in providers
    assert providers["openai_compatible"]["available"] is False
    assert providers["openai_compatible"]["configured"] is False
    assert "credential" in providers["openai_compatible"]["reason"].lower()
    assert all(provider["available"] is False for provider in body["providers"])
    assert all(provider["reason"] for provider in body["providers"])
    assert providers["manual"]["configured"] is True
    assert providers["manual"]["available"] is False
    assert providers["manual"]["executable"] is False
    assert providers["manual"]["requiredConfiguration"] == ["operator"]


def test_remote_provider_status_fails_closed_without_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    providers = {provider["id"]: provider for provider in response.json()["providers"]}
    for provider_id in REMOTE_PROVIDER_IDS:
        provider = providers[provider_id]
        assert provider["configured"] is False
        assert provider["available"] is False
        assert provider["executable"] is False
        assert provider["healthStatus"] == "unknown"
        assert provider["healthCheckedAt"] is None
        assert provider["lastError"] == ""
        assert provider["reason"]


def test_remote_provider_status_does_not_call_remote_health_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_health_checked(self: object) -> ProviderHealth:
        raise AssertionError("runtime status must not call provider health adapters")

    clear_runtime_provider_env(monkeypatch)
    configure_remote_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "health_check", fail_if_health_checked)
    monkeypatch.setattr(AnthropicAPIProvider, "health_check", fail_if_health_checked)
    store, client, _headers = create_client(tmp_path, monkeypatch)
    enable_remote_provider_accounts(store)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    providers = {provider["id"]: provider for provider in response.json()["providers"]}
    for provider_id in REMOTE_PROVIDER_IDS:
        provider = providers[provider_id]
        assert provider["configured"] is True
        assert provider["available"] is False
        assert provider["executable"] is False
        assert provider["healthStatus"] == "unknown"
        assert provider["healthCheckedAt"] is None
        assert "health check" in provider["reason"].lower()


def test_remote_provider_failed_healthcheck_persists_sanitized_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_provider(self: object) -> ProviderHealth:
        provider_id = str(getattr(self, "provider_id", "remote_provider"))
        return ProviderHealth(
            providerId=provider_id,
            status="not_available",
            healthStatus="offline",
            message="Provider failed with Bearer sk-healthfailed123456",
        )

    clear_runtime_provider_env(monkeypatch)
    configure_remote_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "health_check", failed_provider)
    monkeypatch.setattr(AnthropicAPIProvider, "health_check", failed_provider)
    store, client, headers = create_client(tmp_path, monkeypatch)
    enable_remote_provider_accounts(store)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")

    for provider_id in REMOTE_PROVIDER_IDS:
        health_response = client.post(
            f"/api/v1/model-gateway/providers/{provider_id}/health-check", headers=headers
        )
        assert health_response.status_code == 200
        serialized_health = str(health_response.json())
        assert "sk-healthfailed" not in serialized_health
        assert "Bearer" not in serialized_health

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    providers = {provider["id"]: provider for provider in response.json()["providers"]}
    for provider_id in REMOTE_PROVIDER_IDS:
        provider = providers[provider_id]
        assert provider["configured"] is True
        assert provider["available"] is False
        assert provider["executable"] is False
        assert provider["healthStatus"] == "offline"
        assert provider["healthCheckedAt"]
        assert "Provider failed" in provider["lastError"]
        assert "Provider failed" in provider["reason"]
        assert "sk-healthfailed" not in str(provider)
        assert "Bearer" not in str(provider)


def test_remote_provider_healthy_requires_enabled_account_and_sqlite_policy_for_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def healthy_provider(self: object) -> ProviderHealth:
        provider_id = str(getattr(self, "provider_id", "remote_provider"))
        return ProviderHealth(
            providerId=provider_id,
            status="available",
            healthStatus="healthy",
            message="Provider healthcheck reached configured provider.",
        )

    clear_runtime_provider_env(monkeypatch)
    configure_remote_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "health_check", healthy_provider)
    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", lambda _self: [])
    monkeypatch.setattr(AnthropicAPIProvider, "health_check", healthy_provider)
    monkeypatch.setattr(AnthropicAPIProvider, "list_models", lambda _self: [])
    store, client, headers = create_client(tmp_path, monkeypatch)
    enable_remote_provider_accounts(store)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")

    for provider_id in REMOTE_PROVIDER_IDS:
        health_response = client.post(
            f"/api/v1/model-gateway/providers/{provider_id}/health-check", headers=headers
        )
        assert health_response.status_code == 200

    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")
    disabled_flag = client.get("/api/v1/runtime/providers").json()
    disabled_providers = {provider["id"]: provider for provider in disabled_flag["providers"]}
    for provider_id in REMOTE_PROVIDER_IDS:
        provider = disabled_providers[provider_id]
        assert provider["configured"] is True
        assert provider["available"] is True
        assert provider["executable"] is True
        assert provider["healthStatus"] == "healthy"
        assert any(
            "AIDO_ENABLE_REAL_PROVIDER_CALLS" in warning
            for warning in provider["configurationWarnings"]
        )

    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    enabled_flag = client.get("/api/v1/runtime/providers").json()
    enabled_providers = {provider["id"]: provider for provider in enabled_flag["providers"]}
    for provider_id in REMOTE_PROVIDER_IDS:
        provider = enabled_providers[provider_id]
        assert provider["configured"] is True
        assert provider["available"] is True
        assert provider["executable"] is True
        assert provider["healthStatus"] == "healthy"
        assert provider["lastError"] == ""

    store.connection.execute(
        "UPDATE provider_accounts SET enabled = 0 WHERE provider_id = ?", ("openrouter",)
    )
    disabled_account = client.get("/api/v1/runtime/providers").json()
    openrouter = {provider["id"]: provider for provider in disabled_account["providers"]}["openrouter"]
    assert openrouter["configured"] is True
    assert openrouter["available"] is True
    assert openrouter["executable"] is False
    assert "disabled" in openrouter["reason"].lower()


def test_runtime_provider_status_reports_missing_cli_as_not_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    codex = {provider["id"]: provider for provider in response.json()["providers"]}["codex_cli"]
    assert codex["detected"] is False
    assert codex["configured"] is False
    assert codex["available"] is False
    assert codex["executable"] is False
    assert codex["detectedCommand"] is None
    assert codex["requiredConfiguration"] == ["command", "authentication"]
    assert "not detected" in codex["reason"].lower()


def test_runtime_provider_status_reports_ollama_down_with_real_health_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Connection refused",
        },
    )
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    ollama = {provider["id"]: provider for provider in response.json()["providers"]}["ollama"]
    assert ollama["detected"] is False
    assert ollama["configured"] is True
    assert ollama["available"] is False
    assert ollama["executable"] is False
    assert ollama["requiredConfiguration"] == ["baseUrl"]
    assert ollama["healthCheckedAt"]
    assert "connection refused" in ollama["reason"].lower()


def test_runtime_provider_status_treats_configured_remote_ollama_as_ollama_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)

    def fake_ollama_status(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("base_url") == "http://remote.ollama.test":
            return {
                "provider": "ollama_remote",
                "available": True,
                "models": ["llama-remote:latest"],
                "reason": "Remote Ollama responded.",
            }
        return {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Local Ollama is down.",
        }

    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        fake_ollama_status,
    )
    store, client, _headers = create_client(tmp_path, monkeypatch)
    store.connection.execute("UPDATE runtime_installations SET enabled = 1 WHERE runtime_id = 'ollama'")
    store.connection.execute("UPDATE provider_accounts SET enabled = 0 WHERE provider_id = 'ollama'")
    store.connection.execute(
        """
        UPDATE provider_accounts
        SET enabled = 1,
            base_url = ?,
            credential_ref = '',
            api_format = 'ollama',
            last_error = ''
        WHERE provider_id = 'ollama_remote'
        """,
        ("http://remote.ollama.test",),
    )

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    body = response.json()
    remote = {provider["id"]: provider for provider in body["providers"]}["ollama_remote"]
    assert remote["available"] is True
    assert remote["executable"] is True
    assert body["ollama"]["provider"] == "ollama_remote"
    assert body["ollama"]["available"] is True
    assert body["ollama"]["models"] == ["llama-remote:latest"]


def test_openai_compatible_status_requires_config_model_and_explicit_healthcheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def healthy_provider(self: OpenAICompatibleProvider) -> ProviderHealth:
        return ProviderHealth(
            providerId=self.provider_id,
            status="available",
            healthStatus="healthy",
            message="Test healthcheck reached configured provider.",
        )

    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-runtime-status123456")
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda **_kwargs: {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama test daemon is down.",
        },
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "health_check", healthy_provider)
    store, client, headers = create_client(tmp_path, monkeypatch)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    RuntimeConfigRepository(store.connection).set_runtime_setting("runtime.remote.enabled", True)
    store.connection.execute(
        "UPDATE runtime_installations SET enabled = 1 WHERE runtime_id = 'openai_compatible'"
    )
    store.connection.execute(
        """
        UPDATE provider_accounts
        SET enabled = 1, base_url = ?
        WHERE provider_id = 'openai_compatible'
        """,
        ("https://example.invalid/v1",),
    )
    store.connection.execute(
        """
        UPDATE model_catalog
        SET enabled = 1
        WHERE provider_id = 'openai_compatible' AND model = 'configured_model'
        """
    )

    before_health = client.get("/api/v1/runtime/providers").json()
    before_provider = {provider["id"]: provider for provider in before_health["providers"]}[
        "openai_compatible"
    ]
    assert before_provider["configured"] is True
    assert before_provider["available"] is False
    assert before_provider["executable"] is False
    assert before_provider["requiredConfiguration"] == ["baseUrl", "apiKey", "model"]
    assert "health check" in before_provider["reason"].lower()

    health_response = client.post(
        "/api/v1/model-gateway/providers/openai_compatible/health-check", headers=headers
    )

    assert health_response.status_code == 200
    after_health = client.get("/api/v1/runtime/providers").json()
    provider = {item["id"]: item for item in after_health["providers"]}["openai_compatible"]
    assert provider["configured"] is True
    assert provider["available"] is True
    assert provider["executable"] is True
    assert provider["healthCheckedAt"]
    assert provider["version"] is None


def test_issue_to_patch_requires_structured_qa_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Reject string command",
            "issueText": "Fix a bug",
            "qaCommands": ["python --version"],
        },
    )

    assert response.status_code == 422
    assert "qaCommands" in str(response.json()["detail"])


def test_issue_to_patch_without_executable_runtime_finishes_unavailable_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)
    Path(project["path"], "existing.txt").write_text("source\n", encoding="utf-8")

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Implement real patch",
            "issueText": "Change the application code.",
            "qaCommands": [[sys.executable, "--version"]],
            "preferredRuntime": "codex_cli",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "runtime_unavailable"
    assert body["workflowRun"]["status"] == "runtime_unavailable"
    assert body["agentRun"]["status"] == "failed"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["evidencePackage"]["diffRefs"][0]["state"] in {
        "captured",
        "degraded_git_unavailable",
        "degraded_not_git_repo",
    }
    assert body["evidencePackage"]["diffRefs"][0]["blockerState"] == "runtime_unavailable"
    diff_refs = body["evidencePackage"]["diffRefs"]
    assert {"git_diff", "workspace_manifest", "workspace_snapshot"} <= {ref["kind"] for ref in diff_refs}
    manifest = next(ref for ref in diff_refs if ref["kind"] == "workspace_manifest")
    assert manifest["ownerAgentId"] == "aido_issue_to_patch_runner"
    assert manifest["workspaceId"] == body["workspace"]["id"]
    snapshot = next(ref for ref in diff_refs if ref["kind"] == "workspace_snapshot")
    assert any(item["path"] == "existing.txt" and item["sha256"] for item in snapshot["files"])
    assert body["diffSummary"]["blockerState"] == "runtime_unavailable"
    assert body["diffSummary"]["manifestArtifactId"].startswith("artifact-")
    overview = client.get("/api/v1/overview").json()
    artifacts = [
        artifact
        for artifact in overview["artifacts"]
        if artifact["evidencePackageId"] == body["evidencePackage"]["id"]
    ]
    assert any(artifact["kind"] == "evidence_manifest" and artifact["hash"] for artifact in artifacts)
    assert "success" not in str(body).lower()


def test_issue_to_patch_removed_simulation_runtime_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Mock must not complete",
            "issueText": "Pretend to edit a file",
            "preferredRuntime": "internal_mock",
        },
    )

    assert response.status_code == 422
    assert "internal_mock" in response.json()["detail"]


def test_issue_to_patch_completed_requires_diff_evidence_and_passed_qa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "No empty patch success",
            "issueText": "Run a command that changes no files.",
            "preferredRuntime": "manual",
            "requireApproval": False,
            "qaCommands": [[sys.executable, "--version"]],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] != "completed"
    assert body["workflowRun"]["status"] in {"runtime_unavailable", "qa_failed", "evidence_ready"}
    assert body["evidencePackage"]["qaVerdict"] != "passed"
    assert body["diffSummary"]["changedFiles"] == []


def test_issue_to_patch_cannot_complete_without_qa_results() -> None:
    final_status, qa_verdict, reason = _status_from_developer_result(
        {
            "status": "evidence_ready",
            "reason": "QA results are required before DeveloperAgent can complete.",
            "evidencePackage": {"qaVerdict": "blocked"},
        },
        require_approval=False,
    )

    assert final_status != "completed"
    assert qa_verdict == "blocked"
    assert "QA" in reason


def test_restricted_subprocess_blocks_dangerous_flags(tmp_path: Path) -> None:
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "--no-sandbox", "--version"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
    )

    assert result["executed"] is False
    assert result["blocked"] is True
    assert "dangerous" in result["reason"].lower()


def test_restricted_subprocess_blocks_network_host_two_token_variant(tmp_path: Path) -> None:
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "--network", "host", "--version"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
    )

    assert result["executed"] is False
    assert result["blocked"] is True
    assert "--network host" in result["reason"]


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_runtime_unavailable_when_executable_runtime_has_no_real_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: executable_runtime_status(),
    )

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Patch with approval",
            "issueText": "Create an auditable file change.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "runtime_unavailable"
    assert body["agentRun"]["status"] == "failed"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["diffSummary"]["state"] == "captured"
    assert body["diffSummary"]["blockerState"] == "runtime_unavailable"
    assert "command" in body["runtimeResult"]["reason"].lower()
    approvals = client.get("/api/v1/approvals").json()["actionRequests"]
    review_actions = [item for item in approvals if item["jobId"] == body["job"]["id"]]
    assert review_actions == []
    assert body["job"]["status"] == "failed"
    assert not Path(body["workspace"]["path"], "patched.txt").exists()


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_real_runtime_creates_diff_patch_artifact_and_requires_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Real Runtime Review")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create auditable patch",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "evidence_ready"
    assert body["agentRun"]["status"] == "awaiting_permission"
    assert body["runtimeResult"]["status"] == "completed"
    assert body["qaResults"][0]["status"] == "passed"
    assert "patched.txt" in body["diffSummary"]["changedFiles"]
    assert body["diffSummary"]["patchArtifactId"].startswith("artifact-")
    assert body["evidencePackage"]["qaVerdict"] == "needs_human_review"
    assert "success" not in str(body).lower()
    overview = client.get("/api/v1/overview").json()
    patch_artifact = next(
        artifact
        for artifact in overview["artifacts"]
        if artifact["id"] == body["diffSummary"]["patchArtifactId"]
    )
    assert patch_artifact["kind"] == "git_patch"
    assert patch_artifact["hash"]
    assert any(
        action["jobId"] == body["job"]["id"]
        and action["payload"]["evidencePackageId"] == body["evidencePackage"]["id"]
        for action in client.get("/api/v1/approvals").json()["actionRequests"]
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_approval_transitions_workflow_job_and_agent_run_after_human_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Real Runtime Approve")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )

    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch and approve",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    action = next(
        item
        for item in client.get("/api/v1/approvals").json()["actionRequests"]
        if item["jobId"] == created["job"]["id"]
        and item["actionType"] == "workflow.issue_to_patch.approve_patch"
    )

    approved_action = client.post(
        f"/api/v1/jobs/{created['job']['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": "Patch reviewed; QA evidence and diff artifact accepted."},
    )
    assert approved_action.status_code == 202

    approved = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/approve",
        headers=headers,
        json={"reason": "Human reviewer approves the patch for branch integration."},
    )

    assert approved.status_code == 202
    body = approved.json()
    assert body["status"] == "approved_for_integration"
    assert body["workflowRun"]["status"] == "approved_for_integration"
    assert body["workflowRun"]["completedAt"] is None
    assert body["job"]["status"] == "approved"
    assert body["agentRun"]["status"] == "approved"
    assert body["evidencePackage"]["qaVerdict"] == "needs_human_review"
    assert body["diffSummary"]["patchArtifactId"] == created["diffSummary"]["patchArtifactId"]
    assert body["reason"] == "Human reviewer approves the patch for branch integration."

    overview = client.get("/api/v1/overview").json()
    run = next(item for item in overview["workflowRuns"] if item["id"] == created["workflowRun"]["id"])
    job = next(item for item in overview["jobs"] if item["id"] == created["job"]["id"])
    agent_run = next(item for item in overview["agentRuns"] if item["id"] == created["agentRun"]["id"])
    assert run["status"] == "approved_for_integration"
    assert job["status"] == "approved"
    assert agent_run["status"] == "approved"
    assert any(
        event["action"] == "workflow.issue_to_patch.approved_for_integration"
        and event["target"] == created["workflowRun"]["id"]
        for event in overview["auditEvents"]
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_approval_rejects_unapproved_action_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Real Runtime Approval Block")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )

    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch but do not approve action",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()

    blocked = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/approve",
        headers=headers,
        json={"reason": "Trying to bypass the action request approval."},
    )

    assert blocked.status_code == 409
    assert "approved action request" in blocked.json()["detail"]
    overview = client.get("/api/v1/overview").json()
    run = next(item for item in overview["workflowRuns"] if item["id"] == created["workflowRun"]["id"])
    assert run["status"] == "evidence_ready"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_approval_rejects_blocking_security_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Real Runtime Security Block")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )

    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch with blocking security finding",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    security_artifact = next(
        artifact
        for artifact in client.get("/api/v1/overview").json()["artifacts"]
        if artifact["evidencePackageId"] == created["evidencePackage"]["id"]
        and artifact["kind"] == "security_findings"
    )
    blocked_payload = {
        "status": "blocked",
        "source": "policy_decisions",
        "findings": [{"decision": "deny", "reason": "blocking policy decision"}],
    }
    artifact_path = Path(security_artifact["path"])
    content = json.dumps(blocked_payload, sort_keys=True)
    artifact_path.write_text(content, encoding="utf-8")
    store.connection.execute(
        "UPDATE artifacts SET hash = ? WHERE id = ?",
        (hashlib.sha256(content.encode("utf-8")).hexdigest(), security_artifact["id"]),
    )
    action = next(
        item
        for item in client.get("/api/v1/approvals").json()["actionRequests"]
        if item["jobId"] == created["job"]["id"]
        and item["actionType"] == "workflow.issue_to_patch.approve_patch"
    )
    approved_action = client.post(
        f"/api/v1/jobs/{created['job']['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": "Reviewer tried to approve despite blocked security finding."},
    )
    assert approved_action.status_code == 202

    blocked = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/approve",
        headers=headers,
        json={"reason": "Security block should prevent integration approval."},
    )

    assert blocked.status_code == 409
    assert "Security findings" in blocked.json()["detail"]
    overview = client.get("/api/v1/overview").json()
    run = next(item for item in overview["workflowRuns"] if item["id"] == created["workflowRun"]["id"])
    assert run["status"] == "evidence_ready"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_promote_patch_to_branch_requires_approved_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Promotion Approval Required")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )
    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch but do not approve",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()

    promoted = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/promote",
        headers=headers,
        json={"reason": "Trying to promote before approval.", "branchName": "aido/promote/not-approved"},
    )

    assert promoted.status_code == 409
    assert "approved_for_integration" in promoted.json()["detail"]
    assert (
        run_git(
            ["-C", project["path"], "rev-parse", "--verify", "refs/heads/aido/promote/not-approved"]
        ).returncode
        != 0
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_promote_patch_to_branch_rejects_tampered_patch_artifact_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Promotion Tamper Block")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )
    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch and tamper artifact",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    approved = approve_issue_to_patch_for_integration(client, headers, created)
    patch_artifact = next(
        artifact
        for artifact in client.get("/api/v1/overview").json()["artifacts"]
        if artifact["id"] == approved["diffSummary"]["patchArtifactId"]
    )
    Path(patch_artifact["path"]).write_text("tampered patch content\n", encoding="utf-8")

    promoted = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/promote",
        headers=headers,
        json={"reason": "Promote tampered patch.", "branchName": "aido/promote/tampered"},
    )

    assert promoted.status_code == 409
    assert "hash mismatch" in promoted.json()["detail"]
    assert (
        run_git(
            ["-C", project["path"], "rev-parse", "--verify", "refs/heads/aido/promote/tampered"]
        ).returncode
        != 0
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_promote_patch_to_branch_creates_safe_branch_applies_verified_patch_and_reruns_qa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Promotion Success")
    base_commit = run_git(["-C", project["path"], "rev-parse", "HEAD"]).stdout.strip()
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )
    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch and promote",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    approved = approve_issue_to_patch_for_integration(client, headers, created)

    branch_name = "aido/promote/unit-success"
    promoted = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/promote",
        headers=headers,
        json={"reason": "Promote approved patch to a local branch.", "branchName": branch_name},
    )

    assert promoted.status_code == 202
    body = promoted.json()
    assert body["status"] == "promoted_to_branch"
    assert body["workflowRun"]["status"] == "promoted_to_branch"
    assert body["workspace"]["isolationType"] == "git_worktree"
    assert body["diffSummary"]["branch"] == branch_name
    assert body["diffSummary"]["baseCommit"] == base_commit
    assert body["diffSummary"]["patchArtifactId"] == approved["diffSummary"]["patchArtifactId"]
    assert body["evidencePackage"]["id"] != approved["evidencePackage"]["id"]
    assert body["evidencePackage"]["taskId"] == "promote_patch_to_branch"
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert body["qaResults"] and all(result["status"] == "passed" for result in body["qaResults"])
    apply_check = body["runtimeResult"]["gitApplyCheck"]
    apply_result = body["runtimeResult"]["gitApply"]
    assert apply_check["toolCallId"]
    assert apply_check["permissionDecisionId"]
    assert apply_check["execution"] == "restricted_subprocess"
    assert apply_result["toolCallId"]
    assert apply_result["permissionDecisionId"]
    assert apply_result["execution"] == "restricted_subprocess"
    evidence_tool_call_ids = {tool_call["id"] for tool_call in body["evidencePackage"]["toolCalls"]}
    assert apply_check["toolCallId"] in evidence_tool_call_ids
    assert apply_result["toolCallId"] in evidence_tool_call_ids
    promotion_workspace = Path(body["workspace"]["path"])
    assert (promotion_workspace / "patched.txt").read_text(encoding="utf-8") == "real runtime patch\n"
    assert run_git(["-C", str(promotion_workspace), "branch", "--show-current"]).stdout.strip() == branch_name
    assert run_git(["-C", str(promotion_workspace), "rev-parse", "HEAD"]).stdout.strip() == base_commit
    assert (
        "patched.txt" in run_git(["-C", str(promotion_workspace), "diff", "--name-only"]).stdout.splitlines()
    )
    artifact_names = {artifact.get("name") for artifact in body["evidencePackage"]["artifacts"]}
    assert {"promotion-git-commands.json", "promotion-evidence.json", "git-status.json"} <= artifact_names
    overview = client.get("/api/v1/overview").json()
    run = next(item for item in overview["workflowRuns"] if item["id"] == created["workflowRun"]["id"])
    assert run["status"] == "promoted_to_branch"
    assert any(
        event["action"] == "workflow.issue_to_patch.promoted_to_branch"
        and event["target"] == created["workflowRun"]["id"]
        for event in overview["auditEvents"]
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_promote_patch_to_branch_does_not_mark_promoted_when_post_apply_qa_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Promotion QA Fails")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )
    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch and fail promotion QA",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    approve_issue_to_patch_for_integration(client, headers, created)

    promoted = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/promote",
        headers=headers,
        json={
            "reason": "Promotion QA must fail honestly.",
            "branchName": "aido/promote/qa-fails",
            "qaCommands": [[sys.executable, "-m", "pytest", "missing_qa_file.py"]],
        },
    )

    assert promoted.status_code == 202
    body = promoted.json()
    assert body["status"] == "promotion_failed"
    assert body["workflowRun"]["status"] == "promotion_failed"
    assert body["status"] != "promoted_to_branch"
    assert body["evidencePackage"]["qaVerdict"] == "failed"
    assert body["qaResults"][0]["status"] == "failed"
    assert Path(body["workspace"]["path"], "patched.txt").exists()
    overview = client.get("/api/v1/overview").json()
    run = next(item for item in overview["workflowRuns"] if item["id"] == created["workflowRun"]["id"])
    assert run["status"] == "promotion_failed"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_create_pull_request_rejects_runs_without_promoted_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="PR Requires Promotion")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )
    created = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch without promoting",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    approve_issue_to_patch_for_integration(client, headers, created)
    server, origin, requests = start_github_pr_http_mock()
    try:
        monkeypatch.setenv("AIDO_GITHUB_TOKEN", "unit-test-github-token")
        monkeypatch.setenv("AIDO_GITHUB_REMOTE", f"{origin}/aido/patches.git")

        response = client.post(
            f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/pull-request",
            headers=headers,
            json={"reason": "Create PR before branch promotion should be blocked."},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 409
    assert "promoted_to_branch" in response.json()["detail"]
    assert requests == []


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_create_pull_request_reports_pr_unavailable_when_github_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    _project, created, _approved, _promoted = create_promoted_issue_to_patch(
        store,
        client,
        headers,
        tmp_path,
        monkeypatch,
        branch_name="aido/promote/pr-config-missing",
        project_name="PR Missing Config",
    )

    response = client.post(
        f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/pull-request",
        headers=headers,
        json={"reason": "Create PR when GitHub is configured."},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pr_unavailable"
    assert "AIDO_GITHUB_TOKEN" in body["reason"]
    assert "AIDO_GITHUB_REMOTE" in body["reason"]
    assert body["workflowRun"]["status"] == "promoted_to_branch"
    assert body["evidencePackage"]["taskId"] == "create_pull_request"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["pullRequest"] is None
    overview = client.get("/api/v1/overview").json()
    assert any(
        event["action"] == "workflow.issue_to_patch.pr_unavailable"
        and event["target"] == created["workflowRun"]["id"]
        for event in overview["auditEvents"]
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_create_pull_request_posts_audited_pr_body_from_promoted_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    branch_name = "aido/promote/pr-success"
    _project, created, approved, promoted = create_promoted_issue_to_patch(
        store,
        client,
        headers,
        tmp_path,
        monkeypatch,
        branch_name=branch_name,
        project_name="PR Success",
    )
    server, origin, requests = start_github_pr_http_mock()
    try:
        monkeypatch.setenv("AIDO_GITHUB_TOKEN", "unit-test-github-token")
        monkeypatch.setenv("AIDO_GITHUB_REMOTE", f"{origin}/aido/patches.git")

        response = client.post(
            f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/pull-request",
            headers=headers,
            json={
                "reason": "Open a PR after promoted evidence is reviewed.",
                "title": "Patch: create audited file",
                "baseBranch": "main",
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pr_created"
    assert body["workflowRun"]["status"] == "pr_created"
    assert body["pullRequest"]["status"] == "created"
    assert body["pullRequest"]["number"] == 42
    assert body["pullRequest"]["htmlUrl"] == "https://github.test/aido/patches/pull/42"
    assert len(requests) == 1
    captured = requests[0]
    captured_headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert captured["path"] == "/repos/aido/patches/pulls"
    assert captured_headers["authorization"] == "Bearer unit-test-github-token"
    assert "application/vnd.github+json" in captured_headers["accept"]
    assert captured_headers["x-github-api-version"]
    assert captured["body"]["head"] == branch_name
    assert captured["body"]["base"] == "main"
    assert captured["body"]["title"] == "Patch: create audited file"
    pr_body = captured["body"]["body"]
    assert promoted["evidencePackage"]["id"] in pr_body
    assert approved["evidencePackage"]["id"] in pr_body
    assert "QA Summary" in pr_body
    assert "Security Findings" in pr_body
    assert "Artifact Hashes" in pr_body
    assert "Approval Reason" in pr_body
    assert "Human reviewer approves the patch for branch integration." in pr_body
    assert approved["diffSummary"]["patchArtifactId"] in pr_body
    assert approved["diffSummary"]["securityFindingsArtifactId"] in pr_body
    assert "unit-test-github-token" not in json.dumps(body)
    artifact_names = {artifact.get("name") for artifact in body["evidencePackage"]["artifacts"]}
    assert {
        "pull-request-request.json",
        "pull-request-response.json",
        "pull-request-evidence.json",
    } <= artifact_names


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_create_pull_request_does_not_fake_success_when_github_api_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    branch_name = "aido/promote/pr-api-fails"
    _project, created, _approved, _promoted = create_promoted_issue_to_patch(
        store,
        client,
        headers,
        tmp_path,
        monkeypatch,
        branch_name=branch_name,
        project_name="PR API Fails",
    )
    server, origin, requests = start_github_pr_http_mock(
        response_status=422,
        response_payload={"message": "Validation Failed", "errors": [{"field": "head", "code": "invalid"}]},
    )
    try:
        monkeypatch.setenv("AIDO_GITHUB_TOKEN", "unit-test-github-token")
        monkeypatch.setenv("AIDO_GITHUB_REMOTE", f"{origin}/aido/patches.git")

        response = client.post(
            f"/api/v1/workflows/issue-to-patch/{created['workflowRun']['id']}/pull-request",
            headers=headers,
            json={"reason": "GitHub API failure must remain auditable.", "baseBranch": "main"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pr_failed"
    assert body["workflowRun"]["status"] == "promoted_to_branch"
    assert body["pullRequest"]["status"] == "failed"
    assert body["pullRequest"]["httpStatus"] == 422
    assert "htmlUrl" not in body["pullRequest"]
    assert len(requests) == 1
    assert requests[0]["body"]["head"] == branch_name


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_real_runtime_completes_only_with_qa_evidence_and_no_review_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Real Runtime Complete")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch and complete",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["workflowRun"]["status"] == "completed"
    assert body["agentRun"]["status"] == "completed"
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert body["runtimeResult"]["status"] == "completed"
    assert body["qaResults"] and all(result["status"] == "passed" for result in body["qaResults"])
    assert body["evidencePackage"]["id"] in body["agentRun"]["output"]["evidence_refs"]
    assert body["diffSummary"]["patchArtifactId"].startswith("artifact-")
    package = body["evidencePackage"]
    assert package["workflowRunId"] == body["workflowRun"]["id"]
    assert package["jobId"] == body["job"]["id"]
    assert package["agentRunId"] == body["agentRun"]["id"]
    assert package["workspaceId"] == body["workspace"]["id"]
    assert package["runtimeId"] == body["runtime"]["id"]
    assert package["runtimeHealth"]["id"] == body["runtime"]["id"]
    assert package["toolCalls"]
    assert package["policyDecisions"]
    assert package["artifacts"]
    assert package["hashes"]
    artifact_names = {artifact.get("name") for artifact in package["artifacts"]}
    assert {
        "developer-agent.diff",
        "developer-agent-evidence.json",
        "security-findings.json",
    } <= artifact_names
    assert "diff.patch" not in artifact_names
    assert "issue-to-patch-evidence.json" not in artifact_names
    assert "qa-results.json" not in artifact_names
    assert all(artifact.get("hash") for artifact in package["artifacts"])
    overview = client.get("/api/v1/overview").json()
    qa_decisions = [
        decision for decision in overview["permissionDecisions"] if decision["agentId"] == "qa_agent"
    ]
    assert any(
        (decision["payload"] or {}).get("operation") == "qa_agent_command" for decision in qa_decisions
    )


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_issue_to_patch_real_runtime_with_failing_qa_finishes_qa_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Real Runtime QA Fail")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_issue_to_patch_runtime_status(),
    )

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Create patch but fail QA",
            "issueText": "Create patched.txt.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "-m", "pytest", "missing_qa_file.py"]],
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "qa_failed"
    assert body["workflowRun"]["status"] == "qa_failed"
    assert body["agentRun"]["status"] == "failed"
    assert body["qaResults"][0]["status"] == "failed"
    assert body["qaResults"][0]["returnCode"] not in {0, None}
    assert body["evidencePackage"]["qaVerdict"] == "failed"
    assert "patched.txt" in body["diffSummary"]["changedFiles"]


def test_issue_to_patch_completed_is_forbidden_without_evidence() -> None:
    final_status, qa_verdict, reason = _status_from_developer_result(
        {
            "status": "completed",
            "reason": "DeveloperAgent runtime, diff, QA, and evidence passed.",
            "evidencePackage": {},
        },
        require_approval=False,
    )

    assert final_status != "completed"
    assert qa_verdict == "blocked"
    assert "Evidence package" in reason


def test_issue_to_patch_completed_requires_valid_evidence_package_contract() -> None:
    final_status, qa_verdict, reason = _status_from_developer_result(
        {
            "status": "completed",
            "reason": "DeveloperAgent runtime, diff, QA, and evidence passed.",
            "evidencePackage": {"id": "evidence-1", "qaVerdict": "passed"},
        },
        require_approval=False,
        evidence_package_valid=False,
    )

    assert final_status != "completed"
    assert qa_verdict == "blocked"
    assert "Evidence package" in reason
    assert "contract" in reason


def test_issue_to_patch_does_not_execute_runtime_without_git_worktree_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path, name="Non Git Patch Project")
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: executable_runtime_status(),
    )

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "No git worktree",
            "issueText": "This must not execute without auditable git diff evidence.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "runtime_unavailable"
    assert body["workspace"]["isolationType"] == "directory"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["diffSummary"]["state"] in {"degraded_git_unavailable", "degraded_not_git_repo"}
    assert body["diffSummary"]["blockerState"] == "workspace_not_auditable"
    assert "worktree" in body["reason"].lower()
    assert not Path(body["workspace"]["path"], "patched.txt").exists()
