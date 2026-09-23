"""Endpoint del tablero por historia de un hilo: resolución hilo→loop, columnas y payload acotado.

@author Rodrigo Mason
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.threads.repository import ThreadsRepository
from tests_py.test_workspace_isolation_contract import make_app as make_app

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _durable(
    thread_id: str, tasks: list[dict[str, Any]], progress: list[dict[str, Any]], output_id: str | None = None
) -> dict[str, Any]:
    durable: dict[str, Any] = {
        "thread": {"projectThreadId": thread_id},
        "agentTasks": tasks,
        "storyProgress": progress,
    }
    if output_id:
        durable["productOwner"] = {"productOwnerOutputId": output_id}
    return {"durableRun": durable}


def _seed(store: Any, tmp_path: Path) -> dict[str, Any]:
    connection = store.connection
    project_path = tmp_path / "board-project"
    project_path.mkdir()
    project = store.create_project(name="board-project", path=project_path, template_id="other")
    threads = ThreadsRepository(connection)
    thread = threads.create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Board thread"
    )
    other = threads.create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Other thread"
    )
    backlog = BacklogRepository(connection)
    epic = backlog.create_epic({"projectId": project["id"], "title": "Onboarding"})

    def story(title: str, status: str, priority: str, output_id: str = "po-output-board") -> dict[str, Any]:
        return backlog.create_user_story(
            {
                "projectId": project["id"],
                "epicId": epic["id"],
                "title": title,
                "asA": "operator",
                "iWant": f"to {title.lower()}",
                "soThat": "setup ends",
                "status": status,
                "priority": priority,
                "acceptanceCriteria": [f"{title} works."],
                "metadata": {"productOwnerOutputId": output_id},
            }
        )

    def task(for_story: dict[str, Any], status: str) -> dict[str, Any]:
        return backlog.create_agent_task(
            {
                "projectId": project["id"],
                "storyId": for_story["id"],
                "title": f"Build {for_story['title']}",
                "role": "backend_engineer",
                "status": status,
            }
        )

    done = story("Readiness checklist", "done", "high")
    in_qa = story("Setup reminders", "qa", "medium")
    blocked = story("Invite teammates", "blocked", "low")
    todo = story("Export report", "draft", "medium")
    story("Audit trail", "draft", "low")
    tasks = [task(todo, "todo"), task(blocked, "blocked"), task(in_qa, "qa"), task(done, "done")]
    loops = ProductLoopRepository(connection)
    loop = loops.create_loop(
        {
            "projectId": project["id"],
            "title": "Board loop",
            "state": "qa_running",
            "status": "active",
            "context": _durable(
                thread["id"],
                tasks,
                [
                    {
                        "storyId": blocked["id"],
                        "status": "blocked",
                        "reason": "QA failed after 2 rework rounds.",
                        "runtime": "codex_cli",
                    }
                ],
                "po-output-board",
            ),
        }
    )
    foreign_story = story("Other thread story", "draft", "critical", "po-output-other")
    loops.create_loop(
        {
            "projectId": project["id"],
            "title": "Other loop",
            "state": "executing",
            "status": "active",
            "context": _durable(other["id"], [task(foreign_story, "todo")], [], "po-output-other"),
        }
    )
    connection.commit()
    return {"project": project, "thread": thread, "loop": loop, "backlog": backlog}


def test_thread_board_projects_story_statuses_into_columns(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = make_app(tmp_path, monkeypatch)
    seeded = _seed(store, tmp_path)

    response = client.get(f"/api/v1/threads/{seeded['thread']['id']}/board")

    assert response.status_code == 200
    body = response.json()
    assert body["loopId"] == seeded["loop"]["id"]
    assert body["loopState"] == "qa_running"
    assert body["stage"] == "executing"
    columns = {column["id"]: [card["title"] for card in column["cards"]] for column in body["columns"]}
    assert columns == {
        "todo": ["Export report", "Audit trail"],
        "in_progress": ["Invite teammates"],
        "qa": ["Setup reminders"],
        "done": ["Readiness checklist"],
    }
    audit = body["columns"][0]["cards"][1]
    assert audit["tasks"] == []
    assert audit["index"] == 5
    blocked = next(card for column in body["columns"] for card in column["cards"] if card["blocked"])
    assert blocked["blockedReason"] == "QA failed after 2 rework rounds."
    assert blocked["runtime"] == "codex_cli"
    assert blocked["index"] == 4
    assert body["progress"] == {"done": 1, "total": 5}
    assert "Other thread story" not in json.dumps(body)


def test_thread_board_reads_the_most_recent_planned_loop(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = make_app(tmp_path, monkeypatch)
    seeded = _seed(store, tmp_path)
    backlog: BacklogRepository = seeded["backlog"]
    epic = backlog.list_epics(seeded["project"]["id"])[0]
    newer_story = backlog.create_user_story(
        {
            "projectId": seeded["project"]["id"],
            "epicId": epic["id"],
            "title": "Newer story",
            "asA": "operator",
            "iWant": "a newer loop",
            "soThat": "the board follows it",
            "acceptanceCriteria": ["Newer works."],
        }
    )
    newer_task = backlog.create_agent_task(
        {
            "projectId": seeded["project"]["id"],
            "storyId": newer_story["id"],
            "title": "Newer",
            "role": "developer",
        }
    )
    newer_loop = ProductLoopRepository(store.connection).create_loop(
        {
            "projectId": seeded["project"]["id"],
            "title": "Newer loop",
            "state": "executing",
            "status": "active",
            "context": _durable(seeded["thread"]["id"], [newer_task], []),
        }
    )
    store.connection.commit()

    body = client.get(f"/api/v1/threads/{seeded['thread']['id']}/board").json()

    assert body["loopId"] == newer_loop["id"]
    assert [card["title"] for column in body["columns"] for card in column["cards"]] == ["Newer story"]


def test_thread_board_without_a_planned_loop_is_an_empty_planning_board(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = make_app(tmp_path, monkeypatch)
    seeded = _seed(store, tmp_path)
    fresh = ThreadsRepository(store.connection).create_thread(
        project_id=seeded["project"]["id"],
        owner_type="workspace",
        owner_id=seeded["project"]["id"],
        title="Fresh thread",
    )
    store.connection.commit()

    body = client.get(f"/api/v1/threads/{fresh['id']}/board").json()

    assert body["loopId"] is None
    assert body["stage"] == "planning"
    assert body["progress"] == {"done": 0, "total": 0}
    assert [column["id"] for column in body["columns"]] == ["todo", "in_progress", "qa", "done"]


def test_thread_board_unknown_thread_is_404(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, client, _headers = make_app(tmp_path, monkeypatch)

    assert client.get("/api/v1/threads/thread-missing/board").status_code == 404


def test_thread_board_endpoint_runs_in_the_threadpool(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, client, _headers = make_app(tmp_path, monkeypatch)

    route = next(
        route
        for route in client.app.routes
        if getattr(route, "path", "") == "/api/v1/threads/{thread_id}/board"
    )

    assert not inspect.iscoroutinefunction(route.endpoint)
