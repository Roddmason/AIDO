from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_evidence_ingests_junit_xml_into_normalized_test_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="JUnit Evidence", path=tmp_path / "junit", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    junit_xml = """
    <testsuite name="unit" tests="3" failures="1" errors="0" skipped="1" time="1.25">
      <testcase classname="tests.test_api" name="test_ok" time="0.10" />
      <testcase classname="tests.test_api" name="test_failure" time="0.20">
        <failure message="expected true">assert false</failure>
      </testcase>
      <testcase classname="tests.test_api" name="test_skip" time="0.00">
        <skipped message="not on windows" />
      </testcase>
    </testsuite>
    """

    response = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-junit-ingest",
            "testPlan": "Run unit tests",
            "acceptanceChecklist": ["junit parsed"],
            "qaVerdict": "needs_human_review",
            "testResultReports": [{"format": "junit", "command": "uv run pytest --junitxml=report.xml", "content": junit_xml}],
        },
        headers=headers,
    )

    assert response.status_code == 201
    evidence = response.json()["evidencePackage"]
    assert [item["status"] for item in evidence["testResults"]] == ["passed", "failed", "skipped"]
    assert evidence["testResults"][1]["metadata"]["failureMessage"] == "expected true"

    detail = client.get(f"/api/v1/evidence/{evidence['id']}")
    assert detail.status_code == 200
    records = detail.json()["testResultRecords"]
    assert len(records) == 3
    failed_record = next(item for item in records if item["metadata"]["caseName"] == "test_failure")
    assert failed_record["status"] == "failed"
    assert failed_record["metadata"]["format"] == "junit"


def test_evidence_ingests_pytest_summary_into_normalized_test_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Pytest Evidence", path=tmp_path / "pytest", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    response = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-pytest-ingest",
            "testPlan": "Run pytest smoke",
            "qaVerdict": "passed",
            "testResultReports": [
                {
                    "format": "pytest",
                    "command": "uv run pytest tests_py -q",
                    "content": "97 passed, 2 skipped in 11.42s",
                }
            ],
        },
        headers=headers,
    )

    assert response.status_code == 201
    evidence = response.json()["evidencePackage"]
    assert evidence["qaVerdict"] == "passed"
    assert evidence["testResults"][0]["status"] == "passed"
    assert evidence["testResults"][0]["durationMs"] == 11420
    assert evidence["testResults"][0]["metadata"]["counts"] == {"passed": 97, "skipped": 2}


def test_evidence_rejects_passed_verdict_with_failed_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Failed Evidence", path=tmp_path / "failed-evidence", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    response = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-failed-evidence",
            "testPlan": "Run tests with one failure",
            "qaVerdict": "passed",
            "testResults": [
                {"command": "uv run pytest", "status": "passed"},
                {"command": "corepack pnpm test", "status": "failed"},
            ],
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert "failed test result" in response.json()["detail"].lower()


def test_evidence_persists_runtime_links_and_redacts_logs_risks_and_test_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Evidence Redaction", path=tmp_path / "evidence-redaction", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    sample_key = "sk-" + "evidencesecret123456"
    bearer = "Bearer evidencebearer123456"
    patch_key = "sk-" + "patchcontent123456"
    large_log = (f"line with {sample_key} and {bearer}\n" * 900).strip()

    response = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "workflowRunId": "workflow-run-redaction",
            "workflowStepId": "workflow-step-redaction",
            "jobId": "job-redaction",
            "agentRunId": "agent-run-redaction",
            "workspaceId": "workspace-redaction",
            "runtimeId": "codex_cli",
            "taskId": "story-redaction",
            "testPlan": "Run redaction checks",
            "qaVerdict": "needs_human_review",
            "testResults": [
                {
                    "command": f"uv run pytest --token {sample_key}",
                    "status": "passed",
                    "metadata": {"api_key": sample_key, "authorization": bearer},
                }
            ],
            "logs": [{"name": "large.log", "content": large_log}],
            "riskNotes": [{"description": f"risk includes {sample_key}", "authorization": bearer}],
            "diffRefs": [
                {
                    "kind": "git_diff",
                    "changedFiles": ["app.py"],
                    "authorization": bearer,
                    "patch": f"+value = '{patch_key}'",
                }
            ],
            "artifactIds": ["artifact-existing"],
            "diffSummary": {"state": "changed", "authorization": bearer, "changedFiles": ["app.py"]},
        },
        headers=headers,
    )

    assert response.status_code == 201
    evidence = response.json()["evidencePackage"]
    assert evidence["workflowStepId"] == "workflow-step-redaction"
    assert evidence["jobId"] == "job-redaction"
    assert evidence["agentRunId"] == "agent-run-redaction"
    assert evidence["workspaceId"] == "workspace-redaction"
    assert evidence["runtimeId"] == "codex_cli"
    assert "artifact-existing" in evidence["artifactIds"]
    assert len(evidence["artifactIds"]) == 2
    assert evidence["diffRefs"][0]["authorization"] == "[redacted]"
    assert patch_key in evidence["diffRefs"][0]["patch"]
    assert evidence["diffSummary"]["authorization"] == "[redacted]"
    assert "evidencesecret" not in str(evidence)
    assert "evidencebearer" not in str(evidence)

    detail = client.get(f"/api/v1/evidence/{evidence['id']}")
    assert detail.status_code == 200
    serialized_detail = str(detail.json())
    assert "evidencesecret" not in serialized_detail
    assert "evidencebearer" not in serialized_detail

    artifacts = detail.json()["artifacts"]
    log_artifact = next(item for item in artifacts if item["kind"] == "execution_log")
    downloaded = client.get(f"/api/v1/evidence/{evidence['id']}/artifacts/{log_artifact['id']}", headers=headers)
    assert downloaded.status_code == 200
    assert "evidencesecret" not in downloaded.text
    assert "evidencebearer" not in downloaded.text
    assert "[redacted]" in downloaded.text


def test_evidence_package_contract_includes_required_operational_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Evidence Contract", path=tmp_path / "evidence-contract", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    large_log = "large log line\n" * 1200

    response = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "workflowRunId": "workflow-run-contract",
            "jobId": "job-contract",
            "agentRunId": "agent-run-contract",
            "workspaceId": "workspace-contract",
            "runtimeId": "codex_cli",
            "taskId": "story-evidence-contract",
            "testPlan": "Verify evidence package contract",
            "qaVerdict": "needs_human_review",
            "testResults": [{"command": "uv run pytest tests_py -q", "status": "passed"}],
            "logs": [{"name": "large-contract.log", "content": large_log}],
            "runtimeHealth": {"status": "available", "checkedAt": "2026-06-05T00:00:00Z"},
            "modelCalls": [],
            "toolCalls": [{"id": "tool-call-contract", "status": "completed"}],
            "policyDecisions": [{"id": "policy-contract", "decision": "allow"}],
            "approvals": [{"id": "approval-contract", "status": "approved"}],
            "artifacts": [{"id": "artifact-contract", "kind": "test_report", "hash": "sha256-placeholder"}],
            "hashes": {"diff.patch": "sha256-placeholder"},
        },
        headers=headers,
    )

    assert response.status_code == 201
    evidence = response.json()["evidencePackage"]
    for key in (
        "workflowRunId",
        "jobId",
        "agentRunId",
        "workspaceId",
        "runtimeId",
        "runtimeHealth",
        "modelCalls",
        "toolCalls",
        "policyDecisions",
        "approvals",
        "qaVerdict",
        "artifacts",
        "diffSummary",
        "hashes",
        "createdAt",
    ):
        assert key in evidence
    assert evidence["runtimeHealth"]["status"] == "available"
    assert evidence["toolCalls"][0]["id"] == "tool-call-contract"
    assert evidence["hashes"]["diff.patch"] == "sha256-placeholder"
    assert "content" not in evidence["logs"][0]
    assert evidence["logs"][0]["logArtifactId"].startswith("artifact-")

    detail = client.get(f"/api/v1/evidence/{evidence['id']}")
    assert detail.status_code == 200
    artifacts = detail.json()["artifacts"]
    log_artifact = next(item for item in artifacts if item["id"] == evidence["logs"][0]["logArtifactId"])
    assert log_artifact["hash"] == evidence["logs"][0]["logHash"]


def test_evidence_rejects_unsafe_junit_xml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Unsafe JUnit", path=tmp_path / "unsafe", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    response = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-unsafe-junit",
            "testPlan": "Reject unsafe XML",
            "qaVerdict": "needs_human_review",
            "testResultReports": [
                {
                    "format": "junit",
                    "command": "pytest --junitxml=report.xml",
                    "content": "<!DOCTYPE x [<!ENTITY xxe SYSTEM 'file:///c:/secret'>]><testsuite />",
                }
            ],
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert "Unsafe XML" in response.json()["detail"]
