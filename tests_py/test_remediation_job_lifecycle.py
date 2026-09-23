"""Job-scoped preflight repairs expire without hiding current loop or intake blockers."""

import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


@pytest.fixture
def lane(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Job repair lifecycle", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Repair lifecycle"
        )
        yield SimpleNamespace(
            connection=connection,
            project=project,
            thread=thread,
            threads=threads,
            jobs=JobsRepository(connection),
            service=BlockerRemediationService(connection, root=tmp_path),
        )


def _job(lane, status):
    return lane.jobs.create_job(
        project_id=lane.project["id"],
        kind="thread.product_loop.run",
        payload={"threadId": lane.thread["id"]},
        status=status,
    )["job"]


def _preflight_actions(lane, job):
    return lane.service.create_for_blocked_run(
        project_id=lane.project["id"],
        thread_id=lane.thread["id"],
        loop_id=None,
        stage="runtime",
        reason="No executable runtime is available for local worker execution.",
        details={"jobId": job["id"], "executable": False, "status": "configuration_required"},
    )


@pytest.mark.parametrize("old_status", ["cancelled", "completed"])
def test_terminal_job_preflight_repairs_resolve_while_current_blockers_stay_pending(lane, old_status):
    old_job = _job(lane, old_status)
    old_actions = _preflight_actions(lane, old_job)
    _job(lane, "running")
    lane.threads.set_status(lane.thread["id"], "running")
    current = lane.service.repository.create_action(
        project_id=lane.project["id"],
        thread_id=lane.thread["id"],
        loop_id="new-active-loop",
        stage="resource_manager",
        blocker_type="resource_manager_unconfigured",
        title="Current blocker",
        description="Current loop still needs repair.",
        action_type="retry_loop",
    )
    intake = lane.service.repository.create_action(
        project_id=lane.project["id"],
        thread_id=lane.thread["id"],
        loop_id=None,
        stage="thread_intake",
        blocker_type="thread_intake_decision_required",
        title="Choose intent",
        description="An unrelated decision.",
        action_type="answer_question",
        payload={"decisionId": "pending-intake"},
    )

    listed = lane.service.list_for_thread(thread_id=lane.thread["id"], worker_status={"running": True})
    by_id = {action["id"]: action for action in listed}
    assert len(listed) == len(old_actions) + 2
    for old in old_actions:
        assert by_id[old["id"]]["status"] == "resolved"
        assert by_id[old["id"]]["resolvedAt"] is not None
        assert by_id[old["id"]]["payload"]["details"]["jobId"] == old_job["id"]
    assert by_id[current["id"]]["status"] == "pending"
    assert by_id[intake["id"]]["status"] == "pending"
    assert lane.service.list_for_thread(thread_id=lane.thread["id"]) == listed


def test_new_job_preflight_does_not_overwrite_cancelled_job_repair_history(lane):
    old_job = _job(lane, "cancelled")
    old_actions = _preflight_actions(lane, old_job)
    new_job = _job(lane, "queued")
    new_actions = _preflight_actions(lane, new_job)
    assert {item["id"] for item in old_actions}.isdisjoint(item["id"] for item in new_actions)
    assert {item["id"] for item in _preflight_actions(lane, new_job)} == {item["id"] for item in new_actions}

    listed = lane.service.list_for_thread(thread_id=lane.thread["id"])
    assert len(listed) == len(old_actions) + len(new_actions)
    for item in listed:
        expected = "resolved" if item["payload"]["details"]["jobId"] == old_job["id"] else "pending"
        assert item["status"] == expected


@pytest.mark.parametrize("status", ["queued", "resource_wait", "running", "failed"])
def test_unfinished_or_retryable_job_repairs_remain_pending(lane, status):
    actions = _preflight_actions(lane, _job(lane, status))
    listed = lane.service.list_for_thread(thread_id=lane.thread["id"])
    assert {item["id"] for item in listed} == {item["id"] for item in actions}
    assert all(item["status"] == "pending" for item in listed)


def test_cancelled_job_repair_cannot_request_worker_batch_from_stale_ui(lane):
    actions = _preflight_actions(lane, _job(lane, "cancelled"))
    action = next(item for item in actions if item["actionType"] == "run_worker_once")
    result = lane.service.execute(action["id"], platform=object())
    assert result["remediation"]["status"] == "resolved"
    assert result["execution"]["status"] == "blocked"


@pytest.mark.parametrize("mismatch", ["project", "thread"])
def test_terminal_job_from_another_scope_does_not_resolve_thread_repairs(lane, tmp_path, mismatch):
    job = _job(lane, "cancelled")
    actions = _preflight_actions(lane, job)
    if mismatch == "project":
        other = ProjectsRepository(lane.connection).create_project(
            name="Other scope", path=tmp_path / "other", template_id="other"
        )
        lane.connection.execute("UPDATE jobs SET project_id=? WHERE id=?", (other["id"], job["id"]))
    else:
        other = lane.threads.create_thread(
            project_id=lane.project["id"],
            owner_type="workspace",
            owner_id=lane.project["id"],
            title="Other thread",
        )
        lane.connection.execute(
            "UPDATE jobs SET payload=? WHERE id=?", (json.dumps({"threadId": other["id"]}), job["id"])
        )

    listed = lane.service.list_for_thread(thread_id=lane.thread["id"])
    assert {item["id"] for item in listed} == {item["id"] for item in actions}
    assert all(item["status"] == "pending" and item["resolvedAt"] is None for item in listed)


def _loop(lane, *, state="blocked", project_id=None, thread_id=None):
    return ProductLoopRepository(lane.connection).create_loop(
        {
            "projectId": project_id or lane.project["id"],
            "title": "Loop repair lifecycle",
            "state": state,
            "status": "blocked" if state == "blocked" else "active",
            "context": {
                "durableRun": {
                    "thread": {"projectThreadId": thread_id or lane.thread["id"]},
                    "blockedStage": "runtime" if state == "blocked" else None,
                    "blockedReason": "No executable runtime is configured." if state == "blocked" else None,
                }
            },
        }
    )


def _loop_action(lane, loop, *, action_type="run_worker_once"):
    return lane.service.repository.create_action(
        project_id=lane.project["id"],
        thread_id=lane.thread["id"],
        loop_id=loop["id"],
        stage="runtime",
        blocker_type="runtime_not_executable",
        title="Repair this loop",
        description="Persisted repair with its original loop identity.",
        action_type=action_type,
        payload={"loopId": loop["id"], "decisionId": "manual-decision"}
        if action_type == "answer_question"
        else {},
    )


def test_new_loop_dismisses_old_operational_repairs_preserves_manual_and_backfills_current(lane):
    old = _loop(lane)
    old_action = _loop_action(lane, old)
    manual = [
        _loop_action(lane, old, action_type=action_type)
        for action_type in ("answer_question", "approve_resource_decision", "continue_plan_only")
    ]
    current = _loop(lane, state="runtime_check")
    # Creation ordering must be stable even within one timestamp and after an old loop update.
    lane.connection.execute(
        "UPDATE product_loops SET created_at='2026-01-01T00:00:00Z' WHERE id IN (?,?)",
        (old["id"], current["id"]),
    )
    lane.connection.execute("UPDATE product_loops SET updated_at='2099-01-01' WHERE id=?", (old["id"],))
    lane.threads.set_status(lane.thread["id"], "running")
    listed = lane.service.list_for_thread(thread_id=lane.thread["id"])
    by_id = {item["id"]: item for item in listed}
    assert by_id[old_action["id"]]["status"] == "dismissed"
    assert by_id[old_action["id"]]["resolvedAt"] is not None
    assert by_id[old_action["id"]]["payload"] == old_action["payload"]
    assert all(by_id[item["id"]]["status"] == "pending" for item in manual)
    assert len(listed) == 4
    assert ProductLoopRepository(lane.connection).get_loop(old["id"])["state"] == "blocked"

    context = current["context"]
    context["durableRun"].update(blockedStage="runtime", blockedReason="Current runtime failed.")
    ProductLoopRepository(lane.connection).update_loop_state(
        current["id"],
        state="blocked",
        previous_state="runtime_check",
        status="blocked",
        context=context,
        version=2,
    )
    lane.threads.set_status(lane.thread["id"], "blocked")
    listed = lane.service.list_for_thread(thread_id=lane.thread["id"])
    assert any(item["loopId"] == current["id"] and item["status"] == "pending" for item in listed)
    assert all(item["id"] != old_action["id"] or item["status"] == "dismissed" for item in listed)
    assert all(lane.service.repository.get(item["id"])["status"] == "pending" for item in manual)
    assert lane.service.list_for_thread(thread_id=lane.thread["id"]) == listed


def test_stale_loop_repair_cannot_run_from_old_ui_without_listing_first(lane, monkeypatch):
    old_action = _loop_action(lane, _loop(lane))
    _loop(lane, state="runtime_check")
    attempted = []

    def request_batch(payload):
        attempted.append(payload)
        return {"status": "queued"}

    monkeypatch.setattr(lane.service, "_request_worker_batch", request_batch)
    result = lane.service.execute(old_action["id"], platform=object())
    assert result["remediation"]["status"] == "dismissed"
    assert result["execution"]["status"] == "blocked"
    assert attempted == []


@pytest.mark.parametrize("mismatch", ["project", "thread"])
def test_new_loop_in_another_scope_does_not_dismiss_repairs(lane, tmp_path, mismatch):
    old_action = _loop_action(lane, _loop(lane))
    if mismatch == "project":
        other = ProjectsRepository(lane.connection).create_project(
            name="Other loop project", path=tmp_path / "other-loop", template_id="other"
        )
        _loop(lane, state="runtime_check", project_id=other["id"])
    else:
        other = lane.threads.create_thread(
            project_id=lane.project["id"],
            owner_type="workspace",
            owner_id=lane.project["id"],
            title="Other loop thread",
        )
        _loop(lane, state="runtime_check", thread_id=other["id"])
    listed = lane.service.list_for_thread(thread_id=lane.thread["id"])
    assert next(item for item in listed if item["id"] == old_action["id"])["status"] == "pending"


def test_job_failure_enriches_the_pending_generic_worker_action(lane):
    base = {
        "project_id": lane.project["id"],
        "thread_id": lane.thread["id"],
        "loop_id": None,
        "stage": "worker",
        "blocker_type": "worker_not_running",
        "title": "Run worker once",
        "description": "This thread is queued, but the local worker is not running.",
        "action_type": "run_worker_once",
    }
    generic = lane.service.repository.create_action(
        **base, payload={"projectId": lane.project["id"], "threadId": lane.thread["id"]}
    )
    failed_job = _job(lane, "failed")
    enriched = lane.service.repository.create_action(
        **base,
        payload={
            "projectId": lane.project["id"],
            "threadId": lane.thread["id"],
            "details": {"jobId": failed_job["id"]},
        },
    )
    other_job = _job(lane, "queued")
    separate = lane.service.repository.create_action(
        **base,
        payload={
            "projectId": lane.project["id"],
            "threadId": lane.thread["id"],
            "details": {"jobId": other_job["id"]},
        },
    )

    assert enriched["id"] == generic["id"]
    assert enriched["payload"]["details"]["jobId"] == failed_job["id"]
    assert separate["id"] != generic["id"]
