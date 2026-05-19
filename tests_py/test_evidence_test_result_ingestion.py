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
