"""Domain fixtures must preserve the real queued boundary and unrelated jobs."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from local_control_center.api import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.jobs_approvals.repository import JobsRepository
from tests_py.execution_client import complete_operation


def test_completion_fixture_preserves_202_and_does_not_drain_other_jobs(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
            headers = {"X-Local-Control-Token": client.get("/api/v1/security/handshake").json()["token"]}
            project_id = client.get("/api/v1/projects").json()["projects"][0]["id"]
            job = client.post(
                "/api/v1/jobs",
                headers=headers,
                json={
                    "projectId": project_id,
                    "kind": "chat.route",
                    "payload": {},
                    "idempotencyKey": "unrelated-domain-fixture-job",
                },
            ).json()["job"]
            target = tmp_path / "created-by-operation"
            response = client.post(
                "/api/v1/projects",
                headers=headers,
                json={
                    "name": "Fixture project",
                    "path": str(target),
                    "templateId": "other",
                    "createDirectory": True,
                },
            )
            assert response.status_code == 202
            assert not target.exists()
            execution_id = response.json()["executionId"]
            complete_operation(runtime, execution_id)
            terminal = client.get(f"/api/v1/executions/{execution_id}").json()
            assert terminal["status"] == "completed"
            assert terminal["resultStatusCode"] == 201
            assert target.is_dir()
            assert JobsRepository(runtime.connection).get_job(job["id"])["status"] == "queued"
            assert client.get("/api/v1/workers/status").json()["connected"] is False
    finally:
        runtime.close()


def test_web_completion_process_accepts_the_external_runner_database(tmp_path):
    scratch = tmp_path / "scratch"
    retained = tmp_path / "retained"
    scratch.mkdir()
    retained.mkdir()
    db = scratch / "playwright-domain.sqlite"
    runtime = ControlCenterRuntime(cwd=scratch, db_path=db)
    try:
        with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
            token = client.get("/api/v1/security/handshake").json()["token"]
            target = scratch / "project"
            response = client.post(
                "/api/v1/projects",
                headers={"X-Local-Control-Token": token},
                json={
                    "name": "External fixture",
                    "path": str(target),
                    "templateId": "other",
                    "createDirectory": True,
                },
            )
            assert response.status_code == 202
            assert not target.exists()
            execution_id = response.json()["executionId"]
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests_web.fixtures.complete_operation",
                    "--db",
                    str(db),
                    "--execution-id",
                    execution_id,
                ],
                env={
                    **os.environ,
                    "AIDO_QUALITY_SCRATCH": str(scratch),
                    "AIDO_QUALITY_RETAINED": str(retained),
                    "PLAYWRIGHT_DB_PATH": str(db),
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            terminal = client.get(f"/api/v1/executions/{execution_id}").json()
            assert terminal["status"] == "completed"
            assert terminal["resultStatusCode"] == 201
            assert target.is_dir()
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", ["outside", "retained", "other_chunk", "wrong_name", "missing_context"])
def test_web_fixture_rejects_unowned_databases_before_opening(tmp_path, monkeypatch, invalid):
    from tests_web.fixtures.complete_operation import fixture_database

    scratch = tmp_path / "scratch"
    retained = tmp_path / "retained"
    scratch.mkdir()
    retained.mkdir()
    expected = scratch / "playwright-current.sqlite"
    expected.write_bytes(b"fixture-sentinel")
    monkeypatch.setenv("AIDO_QUALITY_SCRATCH", str(scratch))
    monkeypatch.setenv("AIDO_QUALITY_RETAINED", str(retained))
    monkeypatch.setenv("PLAYWRIGHT_DB_PATH", str(expected))
    assert fixture_database(str(expected)) == expected
    target = {
        "outside": tmp_path / "playwright-outside.sqlite",
        "retained": retained / "playwright-evidence.sqlite",
        "other_chunk": scratch / "playwright-other.sqlite",
        "wrong_name": scratch / "operational.sqlite",
        "missing_context": expected,
    }[invalid]
    if invalid == "missing_context":
        monkeypatch.delenv("AIDO_QUALITY_SCRATCH")
    else:
        target.write_bytes(b"must-not-be-opened")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        fixture_database(str(target))
    assert target.read_bytes() == before
