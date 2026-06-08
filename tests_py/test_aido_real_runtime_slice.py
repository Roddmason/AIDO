from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
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
        "LITELLM_API_KEY",
        "AIDO_LITELLM_API_KEY",
        "AIDO_OLLAMA_BASE_URL",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "AIDO_CODEX_COMMAND",
        "AIDO_CLAUDE_COMMAND",
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
            "issueToPatchArgv": argv
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


def test_runtime_provider_configuration_endpoint_detects_aido_env_without_exposing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runtime_provider_env(monkeypatch)
    openai_compatible_api_key_env = "AIDO_OPENAI_COMPATIBLE_" + "API_KEY"
    openrouter_api_key_env = "AIDO_OPENROUTER_" + "API_KEY"
    nvidia_api_key_env = "AIDO_NVIDIA_" + "API_KEY"
    secrets = {
        openai_compatible_api_key_env: "unit-test-compatible-key",
        openrouter_api_key_env: "unit-test-openrouter-key",
        nvidia_api_key_env: "unit-test-nvidia-key",
    }
    values = {
        **secrets,
        "AIDO_OPENAI_COMPATIBLE_BASE_URL": "https://compatible.example.test/v1",
        "AIDO_OPENAI_COMPATIBLE_MODEL": "vendor/model-compatible",
        "AIDO_OPENROUTER_MODEL": "openrouter/model",
        "AIDO_NVIDIA_BASE_URL": "https://nvidia.example.test/v1",
        "AIDO_NVIDIA_MODEL": "nvidia/model",
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
    for provider_id in ("openrouter", "nvidia_nim", "ollama", "codex_cli", "claude_code_cli"):
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
        "local_control_center.agents.runtime_status.ollama_status",
        lambda **_kwargs: {"provider": "ollama", "available": False, "models": [], "reason": "Ollama test daemon is down."},
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
    final_status, qa_verdict, reason = _complete_run_status(
        runtime_status="completed",
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
        artifact for artifact in overview["artifacts"] if artifact["id"] == body["diffSummary"]["patchArtifactId"]
    )
    assert patch_artifact["kind"] == "git_patch"
    assert patch_artifact["hash"]
    assert any(
        action["jobId"] == body["job"]["id"] and action["payload"]["evidencePackageId"] == body["evidencePackage"]["id"]
        for action in client.get("/api/v1/approvals").json()["actionRequests"]
    )


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
        "diff.patch",
        "git-status.txt",
        "git-status.json",
        "qa-results.json",
        "security-findings.json",
        "issue-to-patch-evidence.json",
    } <= artifact_names
    assert all(artifact.get("hash") for artifact in package["artifacts"])
    overview = client.get("/api/v1/overview").json()
    qa_decisions = [decision for decision in overview["permissionDecisions"] if decision["agentId"] == "qa_agent"]
    assert any((decision["payload"] or {}).get("operation") == "qa_agent_command" for decision in qa_decisions)


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
    final_status, qa_verdict, reason = _complete_run_status(
        runtime_status="completed",
        require_approval=False,
        qa_results=[
            {
                "status": "passed",
                "exitCode": 0,
                "execution": "restricted_subprocess",
                "toolCallId": "agent-tool-call-1",
                "artifactHashes": {
                    "stdoutHash": "stdout-hash",
                    "stderrHash": "stderr-hash",
                    "outputArtifactHash": "output-hash",
                },
            }
        ],
        diff={"nameOnly": ["patched.txt"]},
        evidence_created=False,
    )

    assert final_status != "completed"
    assert qa_verdict == "blocked"
    assert "Evidence package" in reason


def test_issue_to_patch_completed_requires_valid_evidence_package_contract() -> None:
    final_status, qa_verdict, reason = _complete_run_status(
        runtime_status="completed",
        require_approval=False,
        qa_results=[
            {
                "status": "passed",
                "exitCode": 0,
                "execution": "restricted_subprocess",
                "toolCallId": "agent-tool-call-1",
                "artifactHashes": {
                    "stdoutHash": "stdout-hash",
                    "stderrHash": "stderr-hash",
                    "outputArtifactHash": "output-hash",
                },
            }
        ],
        diff={"nameOnly": ["patched.txt"], "patchFull": "diff --git a/patched.txt b/patched.txt\n"},
        evidence_created=True,
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
