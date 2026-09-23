"""Módulo puro del tablero por historia: orden, "done", huella de spec, columnas y etapas.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.backlog.board import (
    BOARD_COLUMNS,
    GENERAL_BATCH_ID,
    build_board,
    column_for_status,
    empty_board,
    order_story_batches,
    stage_for_loop_state,
    story_batch_is_done,
    story_fingerprint,
    story_progress_entry,
)
from local_control_center.backlog.repository import BacklogRepository
from tests_py.test_workspace_isolation_contract import make_app as make_app


def _story(
    story_id: str, *, priority: str = "medium", status: str = "draft", title: str | None = None
) -> dict:
    return {
        "id": story_id,
        "title": title or f"Story {story_id}",
        "asA": "operator",
        "iWant": "x",
        "soThat": "y",
        "priority": priority,
        "status": status,
        "metadata": {},
    }


def _task(task_id: str, story_id: str, *, status: str = "todo", role: str = "backend_engineer") -> dict:
    return {"id": task_id, "storyId": story_id, "title": f"Task {task_id}", "role": role, "status": status}


def test_batches_follow_priority_then_emission_order() -> None:
    stories = {
        "s1": _story("s1", priority="low"),
        "s2": _story("s2"),
        "s3": _story("s3", priority="critical"),
        "s4": _story("s4", priority="unknown"),
    }
    tasks = [_task("t1", "s1"), _task("t2", "s2"), _task("t3", "s3"), _task("t4", "s4"), _task("t5", "s2")]

    batches = order_story_batches(tasks, stories)

    assert [batch["storyId"] for batch in batches] == ["s3", "s2", "s4", "s1"]
    assert [task["id"] for task in batches[1]["tasks"]] == ["t2", "t5"]
    assert batches[0]["story"] is stories["s3"]


def test_tasks_without_a_resolvable_story_form_a_trailing_general_batch() -> None:
    batches = order_story_batches([_task("t1", "ghost"), _task("t2", "s1")], {"s1": _story("s1")})

    assert [batch["storyId"] for batch in batches] == ["s1", GENERAL_BATCH_ID]
    assert batches[-1]["story"] is None
    assert [task["id"] for task in batches[-1]["tasks"]] == ["t1"]


def test_emission_order_comes_from_the_backlog_not_from_the_task_order() -> None:
    stories = {"s1": _story("s1"), "s2": _story("s2"), "s3": _story("s3")}
    reversed_tasks = [_task("t3", "s3"), _task("t2", "s2"), _task("t1", "s1")]

    batches = order_story_batches(reversed_tasks, stories, story_order=["s1", "s2", "s3"])

    assert [batch["storyId"] for batch in batches] == ["s1", "s2", "s3"]


def test_a_backlog_story_without_tasks_still_gets_an_empty_batch() -> None:
    stories = {"s1": _story("s1"), "s2": _story("s2")}

    batches = order_story_batches([_task("t1", "s1")], stories, story_order=["s1", "s2"])

    assert [(batch["storyId"], len(batch["tasks"])) for batch in batches] == [("s1", 1), ("s2", 0)]
    assert not story_batch_is_done({"storyId": "s2", "story": _story("s2", status="done"), "tasks": []})


@pytest.mark.usefixtures("controlled_domain_host")
def test_output_stories_are_listed_in_emission_order(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project_path = tmp_path / "emission-project"
    project_path.mkdir()
    project = store.create_project(name="emission-project", path=project_path, template_id="other")
    backlog = BacklogRepository(store.connection)
    epic = backlog.create_epic({"projectId": project["id"], "title": "Onboarding"})

    def story(title: str, output_id: str) -> dict:
        return backlog.create_user_story(
            {
                "projectId": project["id"],
                "epicId": epic["id"],
                "title": title,
                "acceptanceCriteria": ["Given X, when Y, then Z."],
                "metadata": {"productOwnerOutputId": output_id},
            }
        )

    first = story("First", "po-output-1")
    story("Other output", "po-output-2")
    second = story("Second", "po-output-1")
    backlog.update_user_story(first["id"], {"status": "done"})

    listed = backlog.list_user_stories_for_output(project["id"], "po-output-1")

    assert [item["id"] for item in listed] == [first["id"], second["id"]]


def test_a_story_is_done_only_when_it_and_all_its_tasks_are_done() -> None:
    done_story = _story("s1", status="done")
    assert story_batch_is_done(
        {"storyId": "s1", "story": done_story, "tasks": [_task("t1", "s1", status="done")]}
    )
    assert not story_batch_is_done(
        {
            "storyId": "s1",
            "story": done_story,
            "tasks": [_task("t1", "s1", status="done"), _task("t2", "s1", status="todo")],
        }
    )
    assert not story_batch_is_done(
        {"storyId": "s1", "story": _story("s1", status="qa"), "tasks": [_task("t1", "s1", status="done")]}
    )
    assert story_batch_is_done(
        {"storyId": GENERAL_BATCH_ID, "story": None, "tasks": [_task("t1", "ghost", status="done")]}
    )


def test_story_fingerprint_ignores_whitespace_and_case_but_not_content() -> None:
    base = story_fingerprint(_story("s1", title="Readiness checklist"), ["Given X, then Y."])
    same = story_fingerprint(_story("s9", title="  readiness   CHECKLIST "), ["given x,  then y."])
    other = story_fingerprint(_story("s1", title="Readiness checklist"), ["Given X, then Z."])

    assert base == same
    assert base != other
    assert len(base) == 64


@pytest.mark.parametrize(
    ("status", "column"),
    [
        ("draft", "todo"),
        ("todo", "todo"),
        ("reopened", "todo"),
        ("in_progress", "in_progress"),
        ("qa", "qa"),
        ("done", "done"),
        ("blocked", "in_progress"),
        ("", "todo"),
    ],
)
def test_column_for_status(status: str, column: str) -> None:
    assert column_for_status(status) == column


@pytest.mark.parametrize(
    ("state", "stage"),
    [
        ("backlog_ready", "planning"),
        ("branch_ready", "planning"),
        ("executing", "executing"),
        ("qa_running", "executing"),
        ("reworking", "executing"),
        ("security_running", "security"),
        ("quality_review", "security"),
        ("review_ready", "approval"),
        ("awaiting_approval", "approval"),
        ("awaiting_feedback", "approval"),
        ("delivered", "delivered"),
        ("blocked", "blocked"),
        ("cancelled", "blocked"),
    ],
)
def test_stage_for_loop_state(state: str, stage: str) -> None:
    assert stage_for_loop_state(state) == stage


def test_build_board_places_cards_and_counts_progress() -> None:
    stories = {
        "s1": _story("s1", status="done", priority="high"),
        "s2": _story("s2", status="qa"),
        "s3": _story("s3", status="blocked", priority="low"),
    }
    tasks = [
        _task("t1", "s1", status="done"),
        _task("t2", "s2", status="qa"),
        _task("t3", "s3", status="blocked"),
    ]

    board = build_board(
        loop_id="loop-1",
        loop_state="qa_running",
        agent_tasks=tasks,
        stories_by_id=stories,
        criteria_by_story={"s1": ["c1"]},
        progress_by_story={"s3": {"reason": "QA failed twice", "runtime": "codex_cli"}},
    )

    assert board["loopId"] == "loop-1"
    assert board["stage"] == "executing"
    assert [column["id"] for column in board["columns"]] == list(BOARD_COLUMNS)
    by_column = {column["id"]: [card["storyId"] for card in column["cards"]] for column in board["columns"]}
    assert by_column == {"todo": [], "in_progress": ["s3"], "qa": ["s2"], "done": ["s1"]}
    blocked = board["columns"][1]["cards"][0]
    assert blocked["blocked"] is True
    assert blocked["blockedReason"] == "QA failed twice"
    assert blocked["runtime"] == "codex_cli"
    assert blocked["index"] == 3
    done_card = board["columns"][3]["cards"][0]
    assert done_card["index"] == 1
    assert done_card["acceptanceCriteria"] == ["c1"]
    assert done_card["tasks"] == [
        {"id": "t1", "title": "Task t1", "role": "backend_engineer", "status": "done"}
    ]
    assert board["progress"] == {"done": 1, "total": 3}


def test_build_board_does_not_place_a_done_story_with_a_pending_task_in_done() -> None:
    stories = {"s1": _story("s1", status="done")}
    tasks = [_task("t1", "s1", status="todo")]

    board = build_board(
        loop_id="loop-1",
        loop_state="executing",
        agent_tasks=tasks,
        stories_by_id=stories,
        criteria_by_story={},
        progress_by_story={},
    )

    by_column = {column["id"]: [card["storyId"] for card in column["cards"]] for column in board["columns"]}
    assert by_column == {"todo": ["s1"], "in_progress": [], "qa": [], "done": []}
    assert board["progress"] == {"done": 0, "total": 1}


def test_empty_board_has_four_empty_columns() -> None:
    board = empty_board()

    assert board["loopId"] is None
    assert board["loopState"] is None
    assert board["stage"] == "planning"
    assert board["progress"] == {"done": 0, "total": 0}
    assert [column["id"] for column in board["columns"]] == list(BOARD_COLUMNS)
    assert all(not column["cards"] for column in board["columns"])


def test_story_progress_entry_has_a_stable_shape() -> None:
    entry = story_progress_entry(
        story_id="s1",
        index=2,
        title="T",
        status="done",
        fingerprint="f",
        commit="abc",
        qa_verdict="passed",
        runs=2,
        runtime="codex_cli",
    )

    assert entry == {
        "storyId": "s1",
        "index": 2,
        "title": "T",
        "status": "done",
        "fingerprint": "f",
        "commit": "abc",
        "qaVerdict": "passed",
        "runs": 2,
        "runtime": "codex_cli",
        "reason": None,
        "outcome": None,
    }
