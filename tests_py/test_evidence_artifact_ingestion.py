from __future__ import annotations

import base64
import hashlib
import json
import secrets
from contextlib import ExitStack, closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


@pytest.fixture
def owned_runtime_resources():
    """Close this test's clients before its borrowed runtimes, including failure paths."""
    with ExitStack() as resources:
        yield resources


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


@pytest.mark.parametrize(
    "format_name", ["json", "duplicate_json", "header", "long_header", "signed_url", "large_signed_url"]
)
def test_text_artifact_redacts_before_persistence_and_hashing(tmp_path: Path, format_name: str) -> None:
    canary = secrets.token_urlsafe(32)
    if format_name == "json":
        content = json.dumps({"model": "fixture", "metadata": {"apiKey": canary}, "total_tokens": 42})
    elif format_name == "duplicate_json":
        content = '{"metadata": {"apiKey": ' + json.dumps(canary) + '}, "metadata": {"safe": true}}'
    elif format_name in {"header", "long_header"}:
        if format_name == "long_header":
            canary = secrets.token_urlsafe(15_000)
        content = f"X-Local-Control-Token: {canary}\npublic evidence\n"
    else:
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
        claims = {"sub": canary}
        if format_name == "large_signed_url":
            claims["padding"] = "A" * 25_000
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        canary = f"{header}.{payload}.{canary}"
        content = f"https://example.invalid/image?jwt={canary}&width=100"

    artifact = write_text_artifact(
        root=tmp_path, artifact_id="artifact-redaction-canary", suffix=".txt", content=content
    )
    stored_bytes = Path(artifact["path"]).read_bytes()
    stored = stored_bytes.decode("utf-8")
    contains_canary = canary in stored
    assert not contains_canary
    if format_name != "duplicate_json":
        assert "[redacted]" in stored
    assert artifact["hash"] == hashlib.sha256(stored_bytes).hexdigest()
    assert artifact["sizeBytes"] == len(stored_bytes)
    if format_name == "json":
        assert json.loads(stored) == {
            "model": "fixture",
            "metadata": {"apiKey": "[redacted]"},
            "total_tokens": 42,
        }
    elif format_name == "duplicate_json":
        assert json.loads(stored) == {"metadata": {"safe": True}}
    elif format_name in {"header", "long_header"}:
        assert "public evidence" in stored
    else:
        assert "&width=100" in stored


@pytest.mark.parametrize("encoding", ["content", "contentBase64", "binary_label", "binary_header"])
def test_ingest_json_artifact_preserves_format_and_redacts_secrets(
    owned_runtime_resources, tmp_path: Path, monkeypatch, encoding: str
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = owned_runtime_resources.enter_context(
        closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    )
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = owned_runtime_resources.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
    headers = auth_headers(client)
    canary = secrets.token_urlsafe(32)
    content = json.dumps({"metadata": {"apiKey": canary}, "model": "fixture"})
    body = {"kind": "execution_log", "name": "redaction.json", "mimeType": "application/json"}
    if encoding in {"binary_label", "binary_header"}:
        body.update(
            {"name": "response.bin", "mimeType": "application/octet-stream", "kind": "generic_artifact"}
        )
    if encoding == "binary_header":
        content = f"X-Local-Control-Token: {canary}\npublic evidence"
    body["contentBase64" if encoding != "content" else "content"] = (
        base64.b64encode(content.encode()).decode() if encoding != "content" else content
    )

    response = client.post(f"/api/v1/evidence/{evidence['id']}/artifacts", json=body, headers=headers)
    assert response.status_code == 201
    artifact = response.json()["artifact"]
    stored = Path(artifact["path"]).read_bytes()
    contains_canary = canary.encode() in stored
    assert not contains_canary
    if encoding == "binary_header":
        assert "[redacted]" in stored.decode() and "public evidence" in stored.decode()
    else:
        assert json.loads(stored) == {"metadata": {"apiKey": "[redacted]"}, "model": "fixture"}
    assert artifact["hash"] == hashlib.sha256(stored).hexdigest()
    download = client.get(f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact['id']}", headers=headers)
    assert download.status_code == 200
    assert download.content == stored


def test_research_source_hash_tracks_sanitized_artifact(owned_runtime_resources, tmp_path: Path) -> None:
    from local_control_center.projects.repository import ProjectsRepository
    from local_control_center.research.source_log import persist_source
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    connection = owned_runtime_resources.enter_context(
        closing(open_sqlite_connection(tmp_path / "platform.sqlite"))
    )
    initialize_platform_schema(connection)
    project = ProjectsRepository(connection).create_project(
        name="Artifact Source", path=tmp_path / "artifact-source", template_id="other"
    )
    canary = secrets.token_urlsafe(32)
    content = json.dumps({"apiKey": canary, "source": "fixture"})
    record = {
        "url": "https://example.invalid/source",
        "title": "Fixture source",
        "publisher": "Fixture publisher",
        "sourceType": "documentation",
        "fetchedAt": "2026-01-01T00:00:00Z",
        "trustLevel": "official",
        "hash": hashlib.sha256(content.encode()).hexdigest(),
    }
    result = persist_source(
        EvidenceRepository(connection),
        root=tmp_path,
        project_id=project["id"],
        record=record,
        content=content,
    )
    stored = Path(result["path"]).read_bytes()
    assert canary.encode() not in stored
    actual_hash = hashlib.sha256(stored).hexdigest()
    assert result["artifact"]["hash"] == result["hash"] == actual_hash
    assert (
        connection.execute(
            "SELECT content_hash FROM research_sources WHERE id = ?", (result["id"],)
        ).fetchone()[0]
        == actual_hash
    )


def test_patch_artifact_preserves_nonsecret_code(tmp_path: Path) -> None:
    patch = (
        "diff --git a/client.py b/client.py\n--- a/client.py\n+++ b/client.py\n@@ -0,0 +1,3 @@\n"
        '+api_key = os.environ["API_KEY"]\n'
        "+def respond(prompt: str):\n+    return client(api_key=api_key, prompt=prompt)\n"
    )
    artifact = write_text_artifact(root=tmp_path, artifact_id="benign-patch", suffix=".patch", content=patch)
    assert Path(artifact["path"]).read_text(encoding="utf-8") == patch


@pytest.mark.parametrize(
    ("target", "style"),
    [
        ("client.py", "plain"),
        ("client.py", "prefixed"),
        ("client.py", "typed"),
        ("client.py", "triple"),
        ("client.py", "multiline"),
        ("client.py", "leading_minus"),
        ("client.py", "leading_plus"),
        ("client.py", "leading_space"),
        ("client.py", "pwd"),
        (".env", "plain"),
        (".env.local", "plain"),
        (".env", "prefixed"),
        (".env", "pwd"),
    ],
)
def test_patch_artifact_redacts_secret_values_without_removing_assignments(
    tmp_path: Path, target: str, style: str
) -> None:
    canary = secrets.token_urlsafe(32)
    if style.startswith("leading_"):
        canary = {"leading_minus": "-", "leading_plus": "+", "leading_space": " "}[style] + canary
    key = "OPENAI_API_KEY" if style == "prefixed" else "api_key" if target.endswith(".py") else "API_KEY"
    if style == "pwd":
        key = "pwd"
    if target.endswith(".py"):
        annotation = ": str" if style == "typed" else ""
        value = (
            f'"""{canary}"""'
            if style == "triple"
            else f'"""\n{canary}\n"""'
            if style == "multiline"
            else f'"{canary}"'
        )
        assignment = f"{key}{annotation} = {value}"
    else:
        assignment = f"{key}={canary}"
    added = "\n".join("+" + line for line in assignment.splitlines())
    count = len(assignment.splitlines())
    patch = (
        f"diff --git a/{target} b/{target}\n--- a/{target}\n+++ b/{target}\n@@ -0,0 +1,{count} @@\n{added}\n"
    )
    artifact = write_text_artifact(root=tmp_path, artifact_id="secret-patch", suffix=".patch", content=patch)
    stored = Path(artifact["path"]).read_text(encoding="utf-8")
    contains_canary = canary in stored
    assert not contains_canary
    assert stored == patch.replace(canary, "[redacted]")


def test_patch_artifact_preserves_environment_references(tmp_path: Path) -> None:
    patch = 'diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -0,0 +1 @@\n+API_KEY="${OPENAI_API_KEY}"\n'
    artifact = write_text_artifact(root=tmp_path, artifact_id="env-reference", suffix=".patch", content=patch)
    assert Path(artifact["path"]).read_text(encoding="utf-8") == patch


def test_ingest_text_artifact_after_evidence_creation(
    owned_runtime_resources, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = owned_runtime_resources.enter_context(
        closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    )
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = owned_runtime_resources.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
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


def test_ingest_binary_artifact_after_evidence_creation(
    owned_runtime_resources, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = owned_runtime_resources.enter_context(
        closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    )
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = owned_runtime_resources.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
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
    owned_runtime_resources,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = owned_runtime_resources.enter_context(
        closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    )
    store.init()
    evidence = make_evidence(tmp_path, store)
    client = owned_runtime_resources.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
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

    invalid_utf8 = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={
            "kind": "execution_log",
            "name": "invalid.txt",
            "mimeType": "text/plain",
            "contentBase64": base64.b64encode(b"\xff\xfe").decode(),
        },
        headers=headers,
    )
    assert invalid_utf8.status_code == 422


def test_download_artifact_rejects_hash_mismatch(
    owned_runtime_resources, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = owned_runtime_resources.enter_context(
        closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    )
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
    client = owned_runtime_resources.enter_context(TestClient(create_app(runtime=store, static_dir=None)))

    response = client.get(
        f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact['id']}", headers=auth_headers(client)
    )

    assert response.status_code == 409
    assert "hash" in response.text.lower()


def test_download_artifact_blocks_path_traversal(
    owned_runtime_resources, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = owned_runtime_resources.enter_context(
        closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    )
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
    client = owned_runtime_resources.enter_context(TestClient(create_app(runtime=store, static_dir=None)))

    response = client.get(
        f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact['id']}", headers=auth_headers(client)
    )

    assert response.status_code == 403
    assert "outside" in response.text.lower()


def test_get_artifacts_by_ids_batches_dedupes_and_skips_missing(tmp_path: Path) -> None:
    """La resolución por lote conserva el contrato del loop id a id: orden sorted, dedupe y omisión."""
    from local_control_center.evidence.artifacts import artifact_records_from_ids
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    with closing(open_sqlite_connection(tmp_path / "batch.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        repo = EvidenceRepository(connection)
        for artifact_id in ("artifact-a", "artifact-b"):
            repo.create_artifact(
                artifact_id=artifact_id,
                project_id="project-batch",
                evidence_package_id=None,
                kind="execution_log",
                path=f"{artifact_id}.txt",
            )

        records = artifact_records_from_ids(
            repo, ["artifact-b", "artifact-a", "artifact-b", "artifact-missing", ""]
        )
        assert [item["id"] for item in records] == ["artifact-a", "artifact-b"]
