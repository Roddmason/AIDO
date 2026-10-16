"""Regresiones de la auditoría de dureza del núcleo (jobs, worker, hilos, redacción, git, admisión)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import HTTPException

from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.jobs_approvals import commands
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.jobs_approvals.worker import ConcurrentWorker
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.redaction import redact_secrets
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workers.leadership import WorkerLeadershipRepository
from local_control_center.workers.runtime import LocalWorkerRuntime


@pytest.fixture
def connection(tmp_path: Path):
    db_path = tmp_path / "audit.sqlite"
    with closing(open_sqlite_connection(db_path)) as conn:
        initialize_platform_schema(conn)
        yield conn


def _run_worker(tmp_path: Path, *, worker_id: str = "audit-worker") -> dict | None:
    with closing(open_sqlite_connection(tmp_path / "audit.sqlite")) as conn:
        leader = WorkerLeadershipRepository(conn).acquire(owner_id=worker_id, lease_seconds=60)
    return ConcurrentWorker(
        db_path=tmp_path / "audit.sqlite", resource_snapshot=ResourceSnapshot.test_snapshot()
    ).run_once(worker_id=worker_id, fencing_token=leader.fencing_token)


def test_operation_job_without_workload_class_fails_instead_of_wedging_queue(tmp_path, connection):
    jobs = JobsRepository(connection)
    poison = jobs.create_job(project_id="test", kind="operation.execute", payload={})["job"]

    result = _run_worker(tmp_path)

    assert result["run"]["status"] == "failed"
    assert jobs.get_job(poison["id"])["status"] == "failed"
    assert jobs.peek_next_job() is None


@pytest.mark.parametrize("kind", ["operation.execute", "thread.product_loop.run", "thread.research.run"])
def test_api_rejects_internal_job_kinds(connection, kind):
    with pytest.raises(HTTPException) as caught:
        commands.create_job(JobsRepository(connection), {"projectId": "test", "kind": kind, "payload": {}})
    assert caught.value.status_code == 422


def test_cancel_job_releases_idle_lease_keeps_running_lease_and_refuses_terminal_jobs(connection):
    jobs = JobsRepository(connection)
    waiting = jobs.create_job(project_id="test", kind="workflow.qa", payload={})["job"]
    connection.execute(
        "UPDATE jobs SET status = 'approval_required', lease_owner = 'w0', lease_expires_at = '2999-01-01' WHERE id = ?",
        (waiting["id"],),
    )
    assert jobs.cancel_job(waiting["id"], reason="stop")["job"]["status"] == "cancelled"
    row = connection.execute(
        "SELECT lease_owner, lease_expires_at FROM jobs WHERE id = ?", (waiting["id"],)
    ).fetchone()
    assert (row["lease_owner"], row["lease_expires_at"]) == (None, None)

    running = jobs.create_job(project_id="test", kind="workflow.qa", payload={})["job"]
    jobs.claim_next_job(worker_id="w1", lease_ms=60_000, job_id=running["id"])
    jobs.cancel_job(running["id"], reason="stop")
    assert jobs.get_job(running["id"])["leaseOwner"] == "w1"

    with pytest.raises(ValueError, match="cannot be cancelled"):
        jobs.cancel_job(waiting["id"], reason="again")

    done = jobs.create_job(project_id="test", kind="workflow.qa", payload={})["job"]
    connection.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (done["id"],))
    with pytest.raises(ValueError, match="cannot be cancelled"):
        jobs.cancel_job(done["id"])
    assert jobs.get_job(done["id"])["status"] == "completed"


def test_claim_next_job_refuses_open_transaction_and_keeps_callers_work(connection):
    jobs = JobsRepository(connection)
    jobs.create_job(project_id="test", kind="workflow.qa", payload={})
    connection.execute("BEGIN")
    connection.execute("CREATE TABLE caller_work (id INTEGER)")
    with pytest.raises(RuntimeError, match="open transaction"):
        jobs.claim_next_job(worker_id="w1", lease_ms=60_000)
    assert connection.in_transaction
    connection.execute("ROLLBACK")


def test_claim_next_job_lock_error_does_not_issue_rollback(tmp_path, connection):
    jobs = JobsRepository(connection)
    jobs.create_job(project_id="test", kind="workflow.qa", payload={})
    blocker = sqlite3.connect(tmp_path / "audit.sqlite", isolation_level=None, timeout=0)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA busy_timeout = 0")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            jobs.claim_next_job(worker_id="w1", lease_ms=60_000)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_unarchive_only_applies_to_archived_threads(connection):
    threads = ThreadsRepository(connection)
    thread = threads.create_thread(project_id="test", owner_type="workspace", owner_id="ws-1", title="t")
    threads.set_status(thread["id"], "running")

    with pytest.raises(ValueError, match="archived"):
        threads.unarchive_thread(thread["id"], reason="oops", actor="operator")
    assert threads.get_thread(thread["id"])["status"] == "running"

    threads.set_status(thread["id"], "open")
    threads.archive_thread(thread["id"], reason="done", actor="operator")
    assert threads.unarchive_thread(thread["id"], reason="back", actor="operator")["status"] == "open"


def test_list_thread_jobs_filters_in_sql(connection):
    jobs = JobsRepository(connection)
    mine = jobs.create_job(project_id="test", kind="workflow.qa", payload={"threadId": "t1"})["job"]
    done = jobs.create_job(project_id="test", kind="workflow.qa", payload={"threadId": "t1"})["job"]
    jobs.create_job(project_id="test", kind="workflow.qa", payload={})
    connection.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (done["id"],))

    queued = jobs.list_thread_jobs(project_id="test", statuses=("queued",))
    assert [job["id"] for job in queued] == [mine["id"]]
    assert {job["id"] for job in jobs.list_thread_jobs()} == {mine["id"], done["id"]}


def test_research_job_without_workspace_blocks_thread(tmp_path, connection):
    threads = ThreadsRepository(connection)
    thread = threads.create_thread(project_id="test", owner_type="workspace", owner_id="ws-1", title="t")
    threads.set_status(thread["id"], "queued")
    JobsRepository(connection).create_job(
        project_id="test",
        kind="thread.research.run",
        payload={"threadId": thread["id"], "projectId": "test", "query": "q", "workspaceId": None},
    )

    result = _run_worker(tmp_path)

    assert result["run"]["status"] == "failed"
    assert threads.get_thread(thread["id"])["status"] == "blocked"


def test_run_forever_survives_cycle_failure_and_releases_leadership(tmp_path, monkeypatch):
    db_path = tmp_path / "loop.sqlite"
    with closing(open_sqlite_connection(db_path)) as conn:
        initialize_platform_schema(conn)
    runtime = LocalWorkerRuntime(db_path=db_path, cwd=tmp_path)
    calls = {"count": 0}
    released: list[str] = []

    def flaky_cycle() -> bool:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        runtime._stop_event.set()
        return True

    monkeypatch.setattr(runtime, "_run_forever_cycle", flaky_cycle)
    monkeypatch.setattr(runtime, "_release_leadership", released.append)
    monkeypatch.setattr("local_control_center.workers.runtime.WORKER_CYCLE_FAILURE_BACKOFF_SECONDS", 0.01)

    runtime.run_forever()

    assert calls["count"] == 2
    assert released == ["Worker loop exited."]
    assert "RuntimeError" in str(runtime.status()["lastError"])


def test_redact_secrets_handles_tuple_set_and_frozenset():
    secret = "sk-" + "a" * 30
    assert redact_secrets((secret,)) == ["[redacted]"]
    assert redact_secrets({secret}) == ["[redacted]"]
    assert redact_secrets(frozenset({secret})) == ["[redacted]"]
    assert redact_secrets({"items": (secret, "plain")}) == {"items": ["[redacted]", "plain"]}


def test_porcelain_z_output_keeps_unicode_and_renames():
    from local_control_center.git_workspace.service import _parse_porcelain, _porcelain_entries

    nul = chr(0)
    stdout = nul.join([" M src/ñandú.py", "?? docs/a -> b.md", "R  new.py", "old.py", ""])
    parsed = _parse_porcelain(stdout)
    assert parsed["changedFiles"] == ["src/ñandú.py"]
    assert parsed["untrackedFiles"] == ["docs/a -> b.md"]
    assert parsed["stagedFiles"] == ["new.py"]
    assert _porcelain_entries(stdout)[-1][2] == "R  old.py -> new.py"


def test_porcelain_line_format_still_unquotes_names():
    from local_control_center.git_workspace.service import _parse_porcelain

    parsed = _parse_porcelain('?? "with space.txt"\n M plain.py\n')
    assert parsed["untrackedFiles"] == ["with space.txt"]
    assert parsed["changedFiles"] == ["plain.py"]


def test_branch_heartbeat_opens_connection_inside_guard_and_polls_slowly():
    from local_control_center.host_resources import branch_admission

    source = Path(branch_admission.__file__).read_text(encoding="utf-8")
    renew_body = source.split("def renew():", 1)[1].split("thread = (", 1)[0]
    assert renew_body.index("try:") < renew_body.index("open_sqlite_connection")
    assert branch_admission.ADMISSION_POLL_SECONDS >= 0.5
