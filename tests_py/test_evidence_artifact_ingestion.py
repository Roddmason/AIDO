from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.evidence.repository import EvidenceRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_evidence(tmp_path: Path, store: ControlPlaneFixture) -> dict:
    project = store.create_project(name="Artifact Ingest", path=tmp_path / "artifact-ingest", template_id="other")
    return EvidenceRepository(store.connection).create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        agent_id="qa_reviewer",
        task_id="artifact-ingest",
        test_plan="Collect delayed runner artifacts",
        test_results=[{"command": "manual", "status": "passed"}],
        qa_verdict="passed",
    )


def test_ingest_text_artifact_after_evidence_creation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    response = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={"kind": "execution_log", "name": "late-pytest.log", "content": "late pytest output"},
        headers=headers,
    )

    assert response.status_code == 201
    artifact = response.json()["artifact"]
    assert artifact["kind"] == "execution_log"
    assert artifact["metadata"]["name"] == "late-pytest.log"
    assert artifact["metadata"]["source"] == "artifact_ingestion"
    assert Path(artifact["path"]).exists()
    assert Path(artifact["path"]).read_text(encoding="utf-8") == "late pytest output"
    assert any(event["type"] == "evidence.artifact.ingested" for event in store.events.list_events())
    assert any(event["action"] == "evidence.artifact.ingest" for event in store.events.list_audit_events())


def test_ingest_binary_artifact_after_evidence_creation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    payload = base64.b64encode(b"\x89PNG\r\n").decode("ascii")

    response = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={"kind": "screenshot", "name": "smoke.png", "mimeType": "image/png", "contentBase64": payload},
        headers=headers,
    )

    assert response.status_code == 201
    artifact = response.json()["artifact"]
    assert artifact["kind"] == "screenshot"
    assert artifact["metadata"]["mimeType"] == "image/png"
    assert Path(artifact["path"]).read_bytes() == b"\x89PNG\r\n"


def test_ingest_artifact_rejects_unauthenticated_unsupported_or_oversized_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    unauthenticated = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={"kind": "execution_log", "name": "late.log", "content": "late output"},
    )
    assert unauthenticated.status_code == 403

    unsupported = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={"kind": "secret_dump", "name": "bad.txt", "content": "no"},
        headers=headers,
    )
    assert unsupported.status_code == 422

    oversized = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={"kind": "execution_log", "name": "huge.log", "content": "x" * 2_100_000},
        headers=headers,
    )
    assert oversized.status_code == 422
