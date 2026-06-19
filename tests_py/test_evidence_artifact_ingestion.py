from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_evidence(tmp_path: Path, store: ControlPlaneFixture) -> dict:
    project = store.create_project(
        name="Artifact Ingest", path=tmp_path / "artifact-ingest", template_id="other"
    )
    return EvidenceRepository(store.connection).create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        agent_id="qa_reviewer",
        task_id="artifact-ingest",
        test_plan="Collect delayed runner artifacts",
        test_results=[{"command": "manual", "status": "passed"}],
        evidence_source="evidence_collected",
        qa_verdict="evidence_collected",
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


def test_download_artifact_rejects_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    evidence = make_evidence(tmp_path, store)
    artifact_file = write_text_artifact(
        root=tmp_path,
        artifact_id="artifact-hash-mismatch",
        suffix=".txt",
        content="original evidence",
    )
    artifact = EvidenceRepository(store.connection).create_artifact(
        artifact_id="artifact-hash-mismatch",
        project_id=evidence["projectId"],
        evidence_package_id=evidence["id"],
        kind="execution_log",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"],
        metadata={"name": "hash-mismatch.txt", "mimeType": "text/plain"},
    )
    Path(artifact["path"]).write_text("tampered evidence", encoding="utf-8")
    client = TestClient(create_app(runtime=store, static_dir=None))

    response = client.get(
        f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact['id']}", headers=auth_headers(client)
    )

    assert response.status_code == 409
    assert "hash" in response.text.lower()


def test_download_artifact_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    evidence = make_evidence(tmp_path, store)
    outside_path = tmp_path / "outside-artifact.txt"
    outside_path.write_text("outside evidence root", encoding="utf-8")
    artifact = EvidenceRepository(store.connection).create_artifact(
        artifact_id="artifact-outside-root",
        project_id=evidence["projectId"],
        evidence_package_id=evidence["id"],
        kind="execution_log",
        path=str(outside_path),
        content_hash=hashlib.sha256(outside_path.read_bytes()).hexdigest(),
        metadata={"name": "outside-artifact.txt", "mimeType": "text/plain"},
    )
    client = TestClient(create_app(runtime=store, static_dir=None))

    response = client.get(
        f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact['id']}", headers=auth_headers(client)
    )

    assert response.status_code == 403
    assert "outside" in response.text.lower()
