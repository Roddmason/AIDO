"""Contrato HTTP del campo ``eta``: presente en el detalle y en el stream de eventos del hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads.repository import ThreadsRepository
from tests_py.execution_client import CompletedExecutionClient as TestClient

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Threads ETA", path=tmp_path / "threads-eta", template_id="other"
    )
    return project["id"]


def _token(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def _create_thread(client, headers, project_id: str) -> dict:
    response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project_id,
            "ownerType": "workspace",
            "ownerId": "workspace-1",
            "title": "ETA thread",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["thread"]


def test_thread_detail_and_events_expose_null_eta_without_a_loop(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, _token(runtime), project_id)

        detail = client.get(f"/api/v1/threads/{thread['id']}")
        assert detail.status_code == 200, detail.text
        assert "eta" in detail.json()
        assert detail.json()["eta"] is None

        events = client.get(f"/api/v1/threads/{thread['id']}/events", params={"afterSeq": 0})
        assert events.status_code == 200, events.text
        assert "eta" in events.json()
        assert events.json()["eta"] is None
    finally:
        runtime.close()


def test_thread_detail_and_events_expose_eta_shape_once_a_loop_exists(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _project(runtime, tmp_path)
        thread = _create_thread(client, _token(runtime), project_id)
        loop = ProductLoopRepository(runtime.connection).create_loop(
            {
                "projectId": project_id,
                "title": "ETA loop",
                "state": "workspace_check",
                "status": "running",
                "context": {"durableRun": {"thread": {"projectThreadId": thread["id"]}}},
            }
        )
        # El resolutor hilo->loop lee thread_agent_events (nunca escanea `context` de todo el
        # proyecto); un loop sembrado directo en el test necesita este evento para ser encontrado.
        ThreadsRepository(runtime.connection).record_event(
            thread_id=thread["id"], type="workspace_check", payload={"loopId": loop["id"]}
        )

        detail = client.get(f"/api/v1/threads/{thread['id']}")
        assert detail.status_code == 200, detail.text
        eta = detail.json()["eta"]
        assert eta is not None
        assert eta["status"] in {"estimating", "insufficient_history", "waiting_operator"}
        assert eta["currentState"] == "workspace_check"
        assert set(eta.keys()) == {
            "status",
            "remainingSeconds",
            "remainingP90Seconds",
            "currentState",
            "sampleCount",
            "includesOperatorApproval",
        }

        events = client.get(f"/api/v1/threads/{thread['id']}/events", params={"afterSeq": 0})
        assert events.status_code == 200, events.text
        assert events.json()["eta"] == eta
    finally:
        runtime.close()
