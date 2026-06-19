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


def make_expired_artifact(tmp_path: Path, store: ControlPlaneFixture) -> tuple[dict, dict]:
    project = store.create_project(
        name="Retention Action", path=tmp_path / "retention-action", template_id="other"
    )
    repo = EvidenceRepository(store.connection)
    evidence = repo.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        agent_id="qa_reviewer",
        task_id="expired-artifact",
        test_plan="Retain evidence",
        test_results=[{"command": "manual", "status": "passed"}],
        evidence_source="evidence_collected",
        qa_verdict="evidence_collected",
    )
    artifact_file = write_text_artifact(
        root=tmp_path,
        artifact_id="artifact-expired-log",
        suffix=".log",
        content="expired but referenced",
    )
    artifact = repo.create_artifact(
        artifact_id="artifact-expired-log",
        project_id=project["id"],
        evidence_package_id=evidence["id"],
        kind="execution_log",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"],
        metadata={"expiresAt": "2026-01-01T00:00:00Z", "sizeBytes": artifact_file["sizeBytes"]},
    )
    return evidence, artifact


def test_retention_delete_removes_expired_physical_file_but_keeps_audit_record(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    _evidence, artifact = make_expired_artifact(tmp_path, store)
    assert Path(artifact["path"]).exists()

    response = client.post(
        "/api/v1/evidence/artifacts/retention/actions",
        json={
            "action": "delete",
            "artifactIds": [artifact["id"]],
            "reason": "Expired and exported externally.",
            "now": "2026-05-19T00:00:00Z",
        },
        headers=headers,
    )

    assert response.status_code == 202
    result = response.json()["artifacts"][0]
    assert result["retentionAction"]["action"] == "delete"
    assert result["retentionAction"]["reason"] == "Expired and exported externally."
    assert not Path(artifact["path"]).exists()

    refreshed = EvidenceRepository(store.connection).get_artifact_by_id(artifact["id"])
    assert refreshed["evidencePackageId"]
    assert refreshed["metadata"]["retentionAction"]["action"] == "delete"
    assert any(event["type"] == "evidence.artifact.retention_delete" for event in store.events.list_events())
    assert any(
        event["action"] == "evidence.artifact.retention.delete" for event in store.events.list_audit_events()
    )


def test_retention_export_records_manifest_without_deleting_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    _evidence, artifact = make_expired_artifact(tmp_path, store)

    response = client.post(
        "/api/v1/evidence/artifacts/retention/actions",
        json={
            "action": "export",
            "artifactIds": [artifact["id"]],
            "reason": "Keep in external QA archive.",
            "now": "2026-05-19T00:00:00Z",
        },
        headers=headers,
    )

    assert response.status_code == 202
    result = response.json()["artifacts"][0]
    assert result["retentionAction"]["action"] == "export"
    assert result["path"] == artifact["path"]
    assert result["hash"] == artifact["hash"]
    assert Path(artifact["path"]).exists()


def test_retention_action_rejects_unexpired_or_unauthenticated_requests(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    _evidence, artifact = make_expired_artifact(tmp_path, store)

    unauthenticated = client.post(
        "/api/v1/evidence/artifacts/retention/actions",
        json={"action": "delete", "artifactIds": [artifact["id"]], "reason": "try delete"},
    )
    assert unauthenticated.status_code == 403

    unexpired = client.post(
        "/api/v1/evidence/artifacts/retention/actions",
        json={
            "action": "delete",
            "artifactIds": [artifact["id"]],
            "reason": "too early",
            "now": "2025-01-01T00:00:00Z",
        },
        headers=headers,
    )
    assert unexpired.status_code == 422
    assert "not expired" in unexpired.json()["detail"]
