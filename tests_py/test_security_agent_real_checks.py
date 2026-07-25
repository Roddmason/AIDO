from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.security_agent_contract import security_agent_status
from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
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
    (project_path / "README.md").write_text("# Security agent test project\n", encoding="utf-8")
    project = store.create_project(name=f"Security {task_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="security_agent",
        reason="security agent test workspace",
        isolation_type="directory",
    )
    return project, workspace


def security_request(project: dict[str, Any], workspace: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "projectId": project["id"],
        "workspaceId": workspace["id"],
        "taskId": "security-review",
        **extra,
    }


def write_scanner_cli(bin_dir: Path, name: str, script: str) -> None:
    impl = bin_dir / f"{name}_impl.py"
    impl.write_text(script, encoding="utf-8")
    command = bin_dir / f"{name}.cmd"
    command.write_text(f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n', encoding="utf-8")


def configure_external_scanners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    gitleaks_script: str,
    semgrep_script: str,
) -> Path:
    scanner_bin = tmp_path / "scanner-bin"
    scanner_bin.mkdir(parents=True, exist_ok=True)
    write_scanner_cli(scanner_bin, "gitleaks", gitleaks_script)
    write_scanner_cli(scanner_bin, "semgrep", semgrep_script)
    monkeypatch.setenv("PATH", f"{scanner_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    (tmp_path / ".gitleaks.toml").write_text('[allowlist]\ndescription = "test config"\n', encoding="utf-8")
    (tmp_path / ".semgrep.yml").write_text(
        "rules:\n"
        "  - id: test-noop\n"
        "    patterns:\n"
        "      - pattern: $X\n"
        "      - pattern-not: $X\n"
        "    message: noop\n"
        "    severity: INFO\n"
        "    languages: [generic]\n",
        encoding="utf-8",
    )
    return scanner_bin


def evidence_test_result(body: dict[str, Any], command: str) -> dict[str, Any]:
    return next(result for result in body["evidencePackage"]["testResults"] if result["command"] == command)


def test_security_agent_status_lists_configured_remote_model_runtimes() -> None:
    statuses = [
        {
            "id": "openrouter",
            "providerFamily": "openrouter",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
        {
            "id": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
        {
            "id": "anthropic_api",
            "providerFamily": "anthropic_api",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
    ]

    status = security_agent_status(statuses)

    assert status["executable"] is True
    assert status["selectedRuntimeId"] == "openrouter"
    assert status["candidateRuntimeIds"] == ["openrouter", "nvidia_nim", "anthropic_api"]


def test_model_analysis_prompt_carries_story_specs_alongside_findings() -> None:
    from local_control_center.agents.security_agent import SecurityAgentRunner

    prompt = SecurityAgentRunner._model_analysis_prompt(
        {"storySpecs": "HU-1: el login exige MFA"},
        {"findings": [{"id": "finding-1"}]},
    )

    assert "HU-1: el login exige MFA" in prompt
    assert "finding-1" in prompt


def test_model_analysis_prompt_without_story_specs_stays_findings_only() -> None:
    from local_control_center.agents.security_agent import SecurityAgentRunner

    prompt = SecurityAgentRunner._model_analysis_prompt({}, {"findings": [{"id": "finding-1"}]})

    assert json.loads(prompt) == {"findings": [{"id": "finding-1"}]}
    assert "Story specs" not in prompt


def test_security_agent_secret_like_key_in_workspace_blocks_with_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-secret")
    secret_file = Path(workspace["path"]) / "config.txt"
    secret = "sk-" + "test1234567890abcdef"
    secret_file.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    assert any(finding["checkId"] == "secret_scan" for finding in body["findings"])
    assert secret not in json.dumps(body)
    assert body["findingsArtifact"]["id"].startswith("artifact-")
    assert body["evidencePackage"]["artifactIds"]
    assert body["filesScanned"][0]["hash"]
    assert body["agentRun"]["status"] == "failed"


def test_security_agent_clean_workspace_passes_with_file_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-clean")
    Path(workspace["path"], "package.json").write_text(
        json.dumps({"scripts": {"test": "pytest"}, "dependencies": {"left-pad": "1.3.0"}}),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "passed"
    assert body["verdict"] == "passed"
    assert body["findings"] == []
    assert body["filesScanned"]
    assert any(item["path"] == "package.json" for item in body["dependencyFiles"])
    assert all(item["hash"] for item in body["filesScanned"])
    assert body["evidencePackage"]["qaVerdict"] == "security_passed"
    assert body["agentRun"]["status"] == "completed"


def test_security_agent_path_traversal_candidate_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-traversal")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace, pathsToCheck=["../outside.txt"]),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    finding = next(finding for finding in body["findings"] if finding["checkId"] == "path_traversal")
    assert finding["severity"] == "critical"
    assert "outside" in finding["message"].lower()


def test_security_agent_dangerous_docker_flags_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-docker")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(
            project,
            workspace,
            commandCandidates=[
                {
                    "label": "unsafe docker",
                    "argv": ["docker", "run", "--privileged", "--network", "host", "alpine"],
                }
            ],
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    findings = [finding for finding in body["findings"] if finding["checkId"] == "dangerous_command"]
    assert findings
    assert any(
        "--privileged" in finding["message"] or "--network host" in finding["message"] for finding in findings
    )
    assert body["evidencePackage"]["qaVerdict"] == "security_blocked"


def test_security_agent_missing_external_scanners_records_skipped_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner_bin = tmp_path / "empty-scanner-bin"
    scanner_bin.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PATH", str(scanner_bin))
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-scanners-missing")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "passed"
    scanners = {scanner["name"]: scanner for scanner in body["externalScanners"]}
    assert scanners["gitleaks"]["status"] == "skipped_with_reason"
    assert scanners["semgrep"]["status"] == "skipped_with_reason"
    assert all(scanner["status"] != "passed" for scanner in scanners.values())
    assert evidence_test_result(body, "security_agent.gitleaks")["status"] == "skipped_with_reason"
    assert evidence_test_result(body, "security_agent.semgrep")["status"] == "skipped_with_reason"


def test_security_agent_runs_external_scanners_and_attaches_report_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_external_scanners(
        tmp_path,
        monkeypatch,
        gitleaks_script=(
            "from pathlib import Path\n"
            "import sys\n"
            "report = Path(sys.argv[sys.argv.index('--report-path') + 1])\n"
            "report.write_text('[]', encoding='utf-8')\n"
            "raise SystemExit(0)\n"
        ),
        semgrep_script=(
            "from pathlib import Path\n"
            "import sys\n"
            "report = Path(sys.argv[sys.argv.index('--json-output') + 1])\n"
            "report.write_text('{\"results\": []}', encoding='utf-8')\n"
            "raise SystemExit(0)\n"
        ),
    )
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-scanners-clean")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "passed"
    scanners = {scanner["name"]: scanner for scanner in body["externalScanners"]}
    assert scanners["gitleaks"]["status"] == "passed"
    assert scanners["semgrep"]["status"] == "passed"
    assert scanners["gitleaks"]["reportArtifactId"].startswith("artifact-")
    assert scanners["semgrep"]["reportArtifactId"].startswith("artifact-")
    assert scanners["gitleaks"]["reportHash"]
    assert scanners["semgrep"]["reportHash"]
    artifact_names = {artifact["name"] for artifact in body["evidencePackage"]["artifacts"]}
    assert {"gitleaks-report.json", "semgrep-report.json"} <= artifact_names
    assert (
        body["evidencePackage"]["hashes"][scanners["gitleaks"]["reportArtifactId"]]
        == scanners["gitleaks"]["reportHash"]
    )
    assert (
        body["evidencePackage"]["hashes"][scanners["semgrep"]["reportArtifactId"]]
        == scanners["semgrep"]["reportHash"]
    )
    assert (
        evidence_test_result(body, "security_agent.gitleaks")["outputRef"]
        == scanners["gitleaks"]["reportArtifactId"]
    )
    assert (
        evidence_test_result(body, "security_agent.semgrep")["outputRef"]
        == scanners["semgrep"]["reportArtifactId"]
    )


def test_security_agent_gitleaks_secret_blocks_with_report_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaked_secret = "secret-value-from-report"
    configure_external_scanners(
        tmp_path,
        monkeypatch,
        gitleaks_script=(
            "from pathlib import Path\n"
            "import json\n"
            "import sys\n"
            "report = Path(sys.argv[sys.argv.index('--report-path') + 1])\n"
            f"report.write_text(json.dumps([{{'RuleID': 'generic-api-key', 'Description': 'Hardcoded API key', 'File': 'settings.ini', 'StartLine': 1, 'Secret': '{leaked_secret}'}}]), encoding='utf-8')\n"
            "raise SystemExit(1)\n"
        ),
        semgrep_script=(
            "from pathlib import Path\n"
            "import sys\n"
            "report = Path(sys.argv[sys.argv.index('--json-output') + 1])\n"
            "report.write_text('{\"results\": []}', encoding='utf-8')\n"
            "raise SystemExit(0)\n"
        ),
    )
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-gitleaks-block")
    Path(workspace["path"], "settings.ini").write_text("plain_value=not-a-regex-secret\n", encoding="utf-8")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    assert body["evidencePackage"]["qaVerdict"] == "security_blocked"
    assert any(
        finding["checkId"] == "gitleaks_secret" and finding["severity"] == "critical"
        for finding in body["findings"]
    )
    assert leaked_secret not in json.dumps(body)
    scanners = {scanner["name"]: scanner for scanner in body["externalScanners"]}
    assert scanners["gitleaks"]["status"] == "blocked"
    assert scanners["gitleaks"]["reportArtifactId"].startswith("artifact-")
    assert (
        body["evidencePackage"]["hashes"][scanners["gitleaks"]["reportArtifactId"]]
        == scanners["gitleaks"]["reportHash"]
    )
