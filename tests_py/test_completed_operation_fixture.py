"""Domain fixtures must preserve the real queued boundary and unrelated jobs."""

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
