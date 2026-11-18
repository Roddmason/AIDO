"""Regresiones de la auditoría de dureza del product loop (política de gobierno y lecturas obsoletas)."""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.product_loop.models import ProductLoopStartRequest
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.execution_client import CompletedExecutionClient as TestClient


def test_start_request_normalizes_deadline_to_canonical_utc() -> None:
    explicit_offset = ProductLoopStartRequest.model_validate(
        {"title": "X", "deadline": "2026-01-01T03:00:00+03:00"}
    )
    naive = ProductLoopStartRequest.model_validate({"title": "X", "deadline": "2026-01-01T00:00:00"})
    canonical = ProductLoopStartRequest.model_validate({"title": "X", "deadline": "2026-01-01T00:00:00.000Z"})

    assert explicit_offset.deadline == "2026-01-01T00:00:00.000Z"
    assert naive.deadline == "2026-01-01T00:00:00.000Z"
    assert canonical.deadline == "2026-01-01T00:00:00.000Z"
    assert ProductLoopStartRequest.model_validate({"title": "X", "deadline": "  "}).deadline is None
    assert ProductLoopStartRequest.model_validate({"title": "X"}).deadline is None


@pytest.mark.parametrize("deadline", ["tomorrow", "2026-13-45", "0000-00-00", "1e9"])
def test_start_request_rejects_unparseable_deadline(deadline: str) -> None:
    with pytest.raises(ValidationError):
        ProductLoopStartRequest.model_validate({"title": "X", "deadline": deadline})


@pytest.mark.parametrize(
    "timeouts",
    [
        {"executing": "soon"},
        {"executing": 0},
        {"executing": -5},
        {"executing": float("inf")},
        {"executing": float("nan")},
        {"executing": True},
        {"executing": 10**30},
        {"not_a_state": 30},
    ],
)
def test_start_request_rejects_invalid_timeouts(timeouts: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProductLoopStartRequest.model_validate({"title": "X", "timeouts": timeouts})


def test_start_request_accepts_positive_timeouts_per_known_state() -> None:
    request = ProductLoopStartRequest.model_validate(
        {"title": "X", "timeouts": {"goal_received": 60, "executing": 90.5}}
    )

    assert request.timeouts == {"goal_received": 60, "executing": 90.5}


def test_stale_loop_write_does_not_clobber_concurrent_fsm_usage(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="stale", path=tmp_path / "stale", template_id="other"
        )
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")
        stale = coordinator.repository.get_loop(loop["id"])
        coordinator.record_usage(loop["id"], {"tokens": 5})

        coordinator._attach_resource_learning_to_result({"loop": stale}, {"outcome": "ok"})

        persisted = coordinator.repository.get_loop(loop["id"])
        assert persisted["context"]["fsm"]["usage"]["consumed"] == {"tokens": 5}
        assert persisted["context"]["durableRun"]["resourceLearning"] == {"outcome": "ok"}


def test_backlog_approval_rereads_loop_context_inside_the_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="Approve", path=tmp_path / "approve", template_id="other"
        )
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(runtime.connection)
        initiative = ProductDiscoveryRepository(runtime.connection).create_initiative(
            {"projectId": project_id, "title": "Initiative", "summary": "Summary."}
        )
        loop = coordinator.start(project_id=project_id, title="X", initiative_id=initiative["id"])
        backlog = BacklogRepository(runtime.connection)
        epic = backlog.create_epic({"projectId": project_id, "title": "Epic"})
        backlog.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Story",
                "acceptanceCriteria": ["Works."],
            }
        )
        original = BacklogRepository.list_user_stories
        injected: list[bool] = []

        def list_after_concurrent_usage(self, *args, **kwargs):
            stories = original(self, *args, **kwargs)
            if not injected:
                injected.append(True)
                with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as other:
                    ProductLoopCoordinator(other).record_usage(loop["id"], {"tokens": 7})
            return stories

        monkeypatch.setattr(BacklogRepository, "list_user_stories", list_after_concurrent_usage)
        token = client.get("/api/v1/security/handshake").json()["token"]

        response = client.post(
            f"/api/v1/projects/{project_id}/product-loop/{loop['id']}/backlog/approve",
            json={},
            headers={"X-Local-Control-Token": token},
        )

        assert response.status_code == 200, response.text
        context = coordinator.repository.get_loop(loop["id"])["context"]
        assert context["backlogApproval"]["approved"] is True
        assert context["fsm"]["usage"]["consumed"] == {"tokens": 7}
    finally:
        runtime.close()
