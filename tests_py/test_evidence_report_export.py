from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_evidence_report_export_requires_token_and_returns_markdown_without_local_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="QA Export", path=tmp_path / "qa-export", template_id="other")
    repo = EvidenceRepository(store.connection)
    evidence = repo.create_evidence_package(
        project_id=project["id"],
        workflow_run_id="workflow-run-1",
        agent_id="qa_reviewer",
        task_id="story-report",
        test_plan="Run focused backend and frontend checks.",
        acceptance_checklist=["backend tests pass", "frontend smoke pass"],
        test_results=[
            {
                "command": "uv run pytest tests_py -q",
                "status": "passed",
                "durationMs": 1200,
                "metadata": {"format": "pytest", "counts": {"passed": 107}},
            }
        ],
        diff_refs=[{"kind": "git_diff", "files": ["local_control_center/evidence/api.py"]}],
        risk_notes=["No production deployment performed."],
        evidence_source="evidence_collected",
        qa_verdict="evidence_collected",
    )
    artifact_file = write_text_artifact(
        root=tmp_path,
        artifact_id="artifact-report-log",
        suffix=".log",
        content="pytest output",
    )
    repo.create_artifact(
        artifact_id="artifact-report-log",
        project_id=project["id"],
        evidence_package_id=evidence["id"],
        kind="execution_log",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"],
        metadata={"name": "pytest.log", "sizeBytes": artifact_file["sizeBytes"]},
    )
    client = TestClient(create_app(runtime=store, static_dir=None))

    unauthenticated = client.get(f"/api/v1/evidence/{evidence['id']}/report")
    assert unauthenticated.status_code == 403

    response = client.get(f"/api/v1/evidence/{evidence['id']}/report", headers=auth_headers(client))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-aido-evidence-id"] == evidence["id"]
    report = response.text
    assert "# QA Evidence Package: story-report" in report
    assert "Verdict: evidence_collected" in report
    assert "uv run pytest tests_py -q" in report
    assert "107 passed" in report
    assert "artifact-report-log" in report
    assert str(tmp_path) not in report
    assert any(
        event["action"] == "evidence.report.export"
        for event in store.events.list_audit_events(project_id=project["id"])
    )


def test_evidence_report_export_returns_404_for_missing_package(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))

    response = client.get("/api/v1/evidence/evidence-missing/report", headers=auth_headers(client))

    assert response.status_code == 404
