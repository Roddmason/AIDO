from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.agents.cli_runtimes.base import RuntimeResult
from local_control_center.agents.providers.base import ProviderHealth
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.workflows.issue_to_patch_runner import _complete_run_status
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
    monkeypatch.delenv("AIDO_ENABLE_CLI_RUNTIMES", raising=False)
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
        "OPENAI_API_KEY",
        "AIDO_OPENAI_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_BASE_URL",
        "OPENROUTER_API_KEY",
        "AIDO_OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "AIDO_OPENROUTER_BASE_URL",
        "NVIDIA_NIM_API_KEY",
        "AIDO_NVIDIA_NIM_API_KEY",
        "ANTHROPIC_API_KEY",
        "AIDO_ANTHROPIC_API_KEY",
        "LITELLM_API_KEY",
        "AIDO_LITELLM_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "CODEX_CLI_PATH",
        "CLAUDE_CODE_CLI_PATH",
        "OPENHANDS_CLI_PATH",
        "SWE_AGENT_CLI_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def create_git_project(store: ControlPlaneFixture, tmp_path: Path, name: str = "Patch Git Project") -> dict[str, Any]:
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


class FakePatchRuntime:
    def run(self, request: Any) -> RuntimeResult:
        Path(request.workspace_path, "patched.txt").write_text("patched\n", encoding="utf-8")
        return RuntimeResult(runtime=request.runtime, status="completed", command=["fake-runtime"], stdout="", returnCode=0)


def test_runtime_provider_status_does_not_report_api_available_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.ollama_status",
        lambda **_kwargs: {"provider": "ollama", "available": False, "models": [], "reason": "Ollama test daemon is down."},
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


def test_runtime_provider_status_reports_missing_cli_as_not_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.ollama_status",
        lambda **_kwargs: {"provider": "ollama", "available": False, "models": [], "reason": "Ollama test daemon is down."},
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
    assert codex["requiredConfiguration"] == ["command"]
    assert "not detected" in codex["reason"].lower()


def test_runtime_provider_status_reports_ollama_down_with_real_health_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.ollama_status",
        lambda **_kwargs: {"provider": "ollama", "available": False, "models": [], "reason": "Connection refused"},
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
        "local_control_center.agents.runtime_status.ollama_status",
        lambda **_kwargs: {"provider": "ollama", "available": False, "models": [], "reason": "Ollama test daemon is down."},
    )
    monkeypatch.setattr(OpenAICompatibleProvider, "health_check", healthy_provider)
    store, client, headers = create_client(tmp_path, monkeypatch)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
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
    before_provider = {provider["id"]: provider for provider in before_health["providers"]}["openai_compatible"]
    assert before_provider["configured"] is True
    assert before_provider["available"] is False
    assert before_provider["executable"] is False
    assert before_provider["requiredConfiguration"] == ["baseUrl", "apiKey", "model"]
    assert "health check" in before_provider["reason"].lower()

    health_response = client.post("/api/v1/model-gateway/providers/openai_compatible/health-check", headers=headers)

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
    assert body["status"] == "unavailable"
    assert body["workflowRun"]["status"] == "unavailable"
    assert body["agentRun"]["status"] == "failed"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["evidencePackage"]["diffRefs"][0]["state"] in {"blocked_no_executable_runtime", "captured"}
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
    assert body["workflowRun"]["status"] in {"unavailable", "qa_failed", "evidence_ready"}
    assert body["evidencePackage"]["qaVerdict"] != "passed"
    assert body["diffSummary"]["changedFiles"] == []


def test_issue_to_patch_cannot_complete_without_qa_results() -> None:
    final_status, qa_verdict, reason = _complete_run_status(
        runtime_status="running",
        require_approval=False,
        qa_results=[],
        diff={"nameOnly": ["local_control_center/example.py"]},
        evidence_created=True,
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
def test_issue_to_patch_require_approval_creates_action_request_for_reviewable_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: executable_runtime_status(),
    )
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.runtime_for",
        lambda _runtime_id, *, connection=None: FakePatchRuntime(),
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
    assert body["status"] == "evidence_ready"
    assert body["agentRun"]["status"] == "awaiting_permission"
    assert body["evidencePackage"]["qaVerdict"] == "needs_human_review"
    approvals = client.get("/api/v1/approvals").json()["actionRequests"]
    review_actions = [item for item in approvals if item["jobId"] == body["job"]["id"]]
    assert len(review_actions) == 1
    assert review_actions[0]["status"] == "pending"
    assert review_actions[0]["actionType"] == "workflow.issue_to_patch.approve_patch"
    assert review_actions[0]["payload"]["evidencePackageId"] == body["evidencePackage"]["id"]
    assert body["job"]["status"] == "approval_required"


def test_issue_to_patch_does_not_execute_runtime_without_git_worktree_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path, name="Non Git Patch Project")
    runtime = FakePatchRuntime()
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: executable_runtime_status(),
    )
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.runtime_for",
        lambda _runtime_id, *, connection=None: runtime,
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
    assert body["status"] == "unavailable"
    assert body["workspace"]["isolationType"] == "directory"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["diffSummary"]["state"] == "workspace_not_auditable"
    assert "worktree" in body["reason"].lower()
    assert not Path(body["workspace"]["path"], "patched.txt").exists()
