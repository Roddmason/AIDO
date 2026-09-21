"""Outer deadlines close only the exact stopped loop, without replaying partial work."""

from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from local_control_center.executions.repository import ExecutionRepository
from local_control_center.jobs_approvals.repository import JobsRepository, StaleWorkerFenceError
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.models import ProcessLaunchSpec, ProcessStats
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workers.leadership import WorkerLeadershipRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository


@pytest.fixture
def timed_out_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "local_control_center.process_supervision.repository.process_create_time", lambda _pid: 10.0
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        path = tmp_path / "project"
        path.mkdir()
        project = ProjectsRepository(connection).create_project(
            name="Partial work", path=path, template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Migration"
        )
        threads.set_status(thread["id"], "running")
        workspace = WorkspacesRepository(connection, root=tmp_path).allocate_workspace(
            project_id=project["id"],
            task_id="partial",
            agent_id="developer_agent",
            isolation_type="directory",
        )
        jobs = JobsRepository(connection)
        job = jobs.create_job(
            project_id=project["id"],
            kind="thread.product_loop.run",
            payload={"threadId": thread["id"], "root": str(tmp_path)},
        )["job"]
        leader = WorkerLeadershipRepository(connection).acquire(owner_id="timeout-worker", lease_seconds=120)
        claimed = jobs.claim_next_job(
            worker_id=leader.owner_id,
            lease_ms=120000,
            leader_fencing_token=leader.fencing_token,
            job_id=job["id"],
        )
        executions = ExecutionRepository(connection)
        executions.attach_claimed_job(
            job,
            workload_class="agent_cli",
            cwd=str(tmp_path),
            owner_id=leader.owner_id,
            fencing_token=leader.fencing_token,
        )
        executions.start(job["id"], owner_id=leader.owner_id, fencing_token=leader.fencing_token)
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Migration",
                "state": "executing",
                "status": "running",
                "context": {
                    "durableRun": {
                        "status": "executing",
                        "runActive": True,
                        "thread": {"projectThreadId": thread["id"]},
                        "effectiveRequestMeta": {"jobId": job["id"]},
                        "workspaceId": workspace["id"],
                        "workspacePath": workspace["path"],
                        "runtimeRiskReview": {
                            "id": "already-approved",
                            "status": "consumed",
                            "consumedAt": "unchanged",
                        },
                    }
                },
            }
        )
        processes = ManagedProcessRepository(connection)
        processes.start(
            ProcessLaunchSpec(
                "timeout-process",
                job["id"],
                ["python"],
                str(tmp_path),
                "agent_cli",
                "safe-fingerprint",
                100,
                2,
                25,
                False,
            ),
            root_pid=98765,
        )
        processes.finish(
            "timeout-process",
            stats=ProcessStats(timed_out=True, termination_reason="timeout", remaining_descendant_count=0),
        )
        outcome = {
            "managedProcessId": "timeout-process",
            "executionId": job["id"],
            "timedOut": True,
            "cancelled": False,
            "terminationReason": "timeout",
            "remainingDescendantCount": 0,
        }
        yield SimpleNamespace(
            connection=connection,
            project=project,
            thread=thread,
            workspace=workspace,
            jobs=jobs,
            job=job,
            run=claimed["run"],
            leader=leader,
            loop=loop,
            root=tmp_path,
            outcome=outcome,
        )


def _reconcile(case, *, historical=False, **overrides):
    from local_control_center.executions.timeout_reconciliation import reconcile_product_loop_timeout

    return reconcile_product_loop_timeout(
        case.connection,
        execution_id=case.job["id"],
        expected_loop_id=case.loop["id"],
        expected_loop_version=case.loop["version"],
        expected_attempt_id=case.run["id"],
        supervision_result=case.outcome,
        owner_id=case.leader.owner_id,
        fencing_token=case.leader.fencing_token,
        historical=historical,
        **overrides,
    )


def test_timeout_reconciles_atomically_preserving_workspace_and_consumed_review(timed_out_loop):
    case = timed_out_loop
    assert _reconcile(case)["status"] == "reconciled"
    loop = ProductLoopRepository(case.connection).get_loop(case.loop["id"])
    durable = loop["context"]["durableRun"]
    assert loop["state"] == "blocked"
    assert durable["runActive"] is False
    assert durable["workspaceId"] == case.workspace["id"]
    assert durable["runtimeRiskReview"] == case.loop["context"]["durableRun"]["runtimeRiskReview"]
    assert ThreadsRepository(case.connection).get_thread(case.thread["id"])["status"] == "blocked"
    events = ThreadsRepository(case.connection).list_events(case.thread["id"])
    assert [item["type"] for item in events] == ["worker_failed"]
    assert events[0]["payload"]["workspaceId"] == case.workspace["id"]
    assert (
        case.connection.execute(
            "SELECT action_type FROM remediation_actions WHERE status='pending'"
        ).fetchall()[0][0]
        == "view_diff"
    )
    assert _reconcile(case)["status"] == "already_reconciled"
    assert len(ThreadsRepository(case.connection).list_events(case.thread["id"])) == 1


@pytest.mark.parametrize(
    "change",
    [
        "cancelled",
        "replaced",
        "replacement_queued",
        "terminal",
        "scope",
        "revision",
        "descendants",
        "missing_descendants",
        "active_child",
        "stale_fence",
    ],
)
def test_timeout_reconciliation_cannot_touch_cancelled_replaced_or_unproven_loop(timed_out_loop, change):
    case = timed_out_loop
    if change == "cancelled":
        case.jobs.cancel_job(case.job["id"], reason="Operator stopped it")
    elif change == "replaced":
        ProductLoopRepository(case.connection).create_loop(
            {
                "projectId": case.project["id"],
                "title": "Replacement",
                "state": "blocked",
                "status": "blocked",
                "context": {"durableRun": {"thread": {"projectThreadId": case.thread["id"]}}},
            }
        )
    elif change == "replacement_queued":
        case.jobs.create_job(
            project_id=case.project["id"],
            kind="thread.product_loop.run",
            payload={"threadId": case.thread["id"]},
        )
    elif change == "terminal":
        case.connection.execute(
            "UPDATE product_loops SET state='delivered',status='completed' WHERE id=?", (case.loop["id"],)
        )
    elif change == "scope":
        case.connection.execute(
            "UPDATE product_loops SET context=json_set(context,'$.durableRun.effectiveRequestMeta.jobId','different-job') WHERE id=?",
            (case.loop["id"],),
        )
    elif change == "revision":
        case.connection.execute("UPDATE product_loops SET version=version+1 WHERE id=?", (case.loop["id"],))
    elif change == "descendants":
        case.outcome["remainingDescendantCount"] = 1
    elif change == "missing_descendants":
        case.outcome.pop("remainingDescendantCount")
    elif change == "active_child":
        ManagedProcessRepository(case.connection).start(
            ProcessLaunchSpec(
                "active-child",
                case.job["id"],
                ["codex"],
                str(case.root),
                "agent_cli",
                "child",
                100,
                2,
                25,
                False,
            ),
            root_pid=98766,
        )
    else:
        case.connection.execute("UPDATE worker_leader_leases SET fencing_token=fencing_token+1")
    before = ProductLoopRepository(case.connection).get_loop(case.loop["id"])
    with pytest.raises((ValueError, StaleWorkerFenceError)):
        _reconcile(case)
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"]) == before
    assert not ThreadsRepository(case.connection).list_events(case.thread["id"])


def test_timeout_reconciliation_rolls_back_loop_and_thread_if_event_write_fails(timed_out_loop, monkeypatch):
    case = timed_out_loop

    def fail(*_args, **_kwargs):
        raise OSError("Synthetic event persistence failure")

    monkeypatch.setattr(ThreadsRepository, "record_event", fail)
    with pytest.raises(OSError):
        _reconcile(case)
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"]) == case.loop
    assert ThreadsRepository(case.connection).get_thread(case.thread["id"])["status"] == "running"


@pytest.mark.parametrize("new_attempt", [False, True])
def test_historical_timeout_reconciliation_requires_exact_failed_attempt(timed_out_loop, new_attempt):
    case = timed_out_loop
    ExecutionRepository(case.connection).finish(
        case.job["id"],
        owner_id=case.leader.owner_id,
        fencing_token=case.leader.fencing_token,
        status="failed",
        reason="timeout",
    )
    case.jobs.complete_job_run(
        job_id=case.job["id"], run_id=case.run["id"], status="failed", summary="timeout", metadata={}
    )
    if new_attempt:
        case.jobs.retry_job(case.job["id"])
        case.jobs.claim_next_job(
            worker_id=case.leader.owner_id,
            lease_ms=120000,
            leader_fencing_token=case.leader.fencing_token,
            job_id=case.job["id"],
        )
        with pytest.raises(ValueError):
            _reconcile(case, historical=True)
    else:
        assert _reconcile(case, historical=True)["status"] == "reconciled"
        assert case.jobs.get_job(case.job["id"])["status"] == "failed"


def test_deadline_remaining_decreases_across_developer_calls_and_never_launches_exhausted(
    monkeypatch, tmp_path
):
    from local_control_center.agents.developer_agent import DeveloperAgentRunner
    from local_control_center.process_supervision import context as context_module

    now = [100.0]
    monkeypatch.setattr(context_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        "local_control_center.agents.developer_agent.build_developer_agent_argv", lambda **_: ["claude"]
    )
    calls = []

    class Captured(Exception):
        pass

    class Broker:
        def evaluate_tool_call(self, **kwargs):
            calls.append(kwargs["tool_call"]["timeoutSeconds"])
            raise Captured

    context = replace(
        ProcessExecutionContext(db_path=tmp_path / "unused.sqlite"), execution_deadline_monotonic=200.0
    )
    kwargs = {
        "payload": {"projectId": "p", "instruction": "Inspect"},
        "runtime": {"id": "claude_code_cli"},
        "workspace": {"id": "w", "path": str(tmp_path)},
        "agent_run": {"id": "a"},
        "job": {"id": "j"},
        "profile": {},
        "broker": Broker(),
    }
    with execution_scope(context):
        with pytest.raises(Captured):
            DeveloperAgentRunner._execute_cli_runtime(SimpleNamespace(connection=None), **kwargs)
        now[0] = 150.0
        with pytest.raises(Captured):
            DeveloperAgentRunner._execute_cli_runtime(SimpleNamespace(connection=None), **kwargs)
        now[0] = 199.0
        with pytest.raises(TimeoutError):
            DeveloperAgentRunner._execute_cli_runtime(SimpleNamespace(connection=None), **kwargs)
    assert calls == [85, 35]


def test_runner_deadline_uses_native_start_not_later_handler_start(timed_out_loop, monkeypatch):
    from local_control_center.process_supervision import context as module

    case = timed_out_loop
    wall = datetime(2026, 9, 21, 12, tzinfo=UTC)
    monkeypatch.setattr(module.time, "time", lambda: wall.timestamp())
    monkeypatch.setattr(module.time, "monotonic", lambda: 500.0)
    monkeypatch.setattr(
        "psutil.Process", lambda pid: SimpleNamespace(pid=pid, create_time=lambda: 10.0, parents=lambda: [])
    )
    case.connection.execute(
        "UPDATE managed_processes SET started_at=?,finished_at=NULL WHERE managed_process_id='timeout-process'",
        ((wall - timedelta(seconds=100)).isoformat(),),
    )
    case.connection.execute(
        "UPDATE operational_executions SET started_at=? WHERE id=?",
        ((wall - timedelta(seconds=20)).isoformat(), case.job["id"]),
    )
    case.connection.execute(
        "UPDATE job_runs SET started_at=? WHERE id=?",
        ((wall - timedelta(seconds=120)).isoformat(), case.run["id"]),
    )
    deadline = module.runner_execution_deadline(
        case.connection, execution_id=case.job["id"], runner_pid=98765
    )
    assert deadline == 1300.0


@pytest.mark.parametrize(
    "identity", ["windows_wrapper", "wrong_creation", "child_not_ancestor", "other_attempt"]
)
def test_runner_deadline_resolves_exact_registered_wrapper_ancestor(timed_out_loop, monkeypatch, identity):
    from local_control_center.process_supervision import context as module

    case = timed_out_loop
    wall = datetime(2026, 9, 21, 12, tzinfo=UTC)
    monkeypatch.setattr(module.time, "time", lambda: wall.timestamp())
    monkeypatch.setattr(module.time, "monotonic", lambda: 500.0)

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return 20.0 if identity == "wrong_creation" else 10.0

        def parents(self):
            return [] if identity == "child_not_ancestor" else [Process(98765)]

    monkeypatch.setattr("psutil.Process", Process)
    case.connection.execute(
        "UPDATE managed_processes SET started_at=?,finished_at=NULL WHERE managed_process_id='timeout-process'",
        ((wall - timedelta(seconds=100)).isoformat(),),
    )
    case.connection.execute(
        "UPDATE operational_executions SET started_at=? WHERE id=?",
        ((wall - timedelta(seconds=20)).isoformat(), case.job["id"]),
    )
    case.connection.execute(
        "UPDATE job_runs SET started_at=? WHERE id=?",
        ((wall - timedelta(seconds=50 if identity == "other_attempt" else 120)).isoformat(), case.run["id"]),
    )
    if identity == "windows_wrapper":
        assert (
            module.runner_execution_deadline(case.connection, execution_id=case.job["id"], runner_pid=98764)
            == 1300.0
        )
    else:
        with pytest.raises(ValueError, match=r"registered.*runner|runner.*identity"):
            module.runner_execution_deadline(case.connection, execution_id=case.job["id"], runner_pid=98764)


@pytest.mark.parametrize("launcher", [False, True])
def test_dispatch_timeout_closes_exact_loop_for_direct_and_launcher_paths(
    timed_out_loop, monkeypatch, launcher
):
    from local_control_center.executions import dispatcher

    case = timed_out_loop
    case.outcome.update(returnCode=None, stdoutArtifactId=None, stderrArtifactId=None)
    monkeypatch.setattr(dispatcher, "run_supervised_capture", lambda *_args, **_kwargs: case.outcome)
    monkeypatch.setattr(
        "local_control_center.process_supervision.session_client.request_session",
        lambda *_args, **_kwargs: case.outcome,
    )
    with execution_scope(
        ProcessExecutionContext(
            db_path=case.root / "platform.sqlite",
            connection=case.connection,
            execution_id=case.job["id"],
            project_id=case.project["id"],
            worker_id=case.leader.owner_id,
            fencing_token=case.leader.fencing_token,
            attempt_id=case.run["id"],
            aggregate_managed_process_id="launcher" if launcher else None,
        )
    ):
        result = dispatcher.dispatch_execution(
            case.job, connection=case.connection, db_path=case.root / "platform.sqlite"
        )
    assert result["metadata"]["status"] == "failed"
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"])["state"] == "blocked"


def test_launcher_dispatch_preserves_native_descendant_evidence(timed_out_loop, monkeypatch):
    from local_control_center.process_supervision import launcher_session as module

    case = timed_out_loop
    session = module.LauncherCaptureSession(case.root / "platform.sqlite", case.root)
    session.context = ProcessExecutionContext(db_path=session.db_path, execution_id="launcher")
    session.children["worker"] = (None, SimpleNamespace(managed_process_id="worker"))
    monkeypatch.setattr(session, "_member", lambda *_args: True)
    outcome = {**case.outcome, "returnCode": None, "stdoutArtifactId": None, "stderrArtifactId": None}
    monkeypatch.setattr(module, "run_supervised_capture", lambda *_args, **_kwargs: outcome)
    result = session._perform(
        module.SessionRequest(
            action="dispatch",
            executionId=case.job["id"],
            attemptId=case.run["id"],
            ownerId=case.leader.owner_id,
            fencingToken=case.leader.fencing_token,
        ),
        peer=1234,
    )
    assert result["remainingDescendantCount"] == 0


def test_timeout_poll_reconciles_only_after_native_proof_and_last_child_drains(timed_out_loop):
    from local_control_center.executions.timeout_reconciliation import (
        reconcile_thread_timeout,
        record_timeout_observation,
    )

    case = timed_out_loop
    kwargs = {"thread_id": case.thread["id"], "project_id": case.project["id"]}
    assert reconcile_thread_timeout(case.connection, **kwargs)["status"] == "pending_evidence"
    ExecutionRepository(case.connection).finish(
        case.job["id"],
        owner_id=case.leader.owner_id,
        fencing_token=case.leader.fencing_token,
        status="failed",
        reason="timeout",
    )
    case.jobs.complete_job_run(
        job_id=case.job["id"], run_id=case.run["id"], status="failed", summary="timeout", metadata={}
    )
    processes = ManagedProcessRepository(case.connection)
    processes.start(
        ProcessLaunchSpec(
            "draining-child",
            case.job["id"],
            ["codex"],
            str(case.root),
            "agent_cli",
            "child",
            100,
            2,
            25,
            False,
        ),
        root_pid=98766,
    )
    record_timeout_observation(
        case.connection,
        execution_id=case.job["id"],
        attempt_id=case.run["id"],
        supervision_result=case.outcome,
    )
    assert reconcile_thread_timeout(case.connection, **kwargs)["status"] == "not_applicable"
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"])["state"] == "executing"
    processes.finish("draining-child", stats=ProcessStats(cancelled=True, termination_reason="owner_crashed"))
    assert reconcile_thread_timeout(case.connection, **kwargs)["status"] == "reconciled"
    assert reconcile_thread_timeout(case.connection, **kwargs)["status"] == "not_applicable"


def test_execution_deadline_never_requests_provider_failover():
    from local_control_center.process_supervision.context import ExecutionDeadlineExceeded
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

    def deadline(_payload):
        raise ExecutionDeadlineExceeded("execution_deadline_exhausted")

    def forbidden(**_kwargs):
        pytest.fail("A deadline must not invoke Jev or select another provider")

    with pytest.raises(ExecutionDeadlineExceeded):
        ProductLoopCoordinator._run_with_failover(
            SimpleNamespace(_failover_replacement=forbidden),
            runtime=SimpleNamespace(run=deadline),
            payload={},
            run=SimpleNamespace(),
            attempts=[],
            thread_id="thread",
            role="developer",
        )


@pytest.mark.parametrize("reported", [False, True])
def test_developer_deadline_blocks_worker_with_trusted_identity_and_partial_evidence(
    timed_out_loop, reported
):
    from local_control_center.process_supervision.context import ExecutionDeadlineExceeded
    from local_control_center.product_loop.coordinator import _UserMessageRun
    from local_control_center.product_loop.phases.execution import execute_developer_phase

    case = timed_out_loop
    result = {
        "status": "failed",
        "runtimeResult": {"timedOut": True, "outputArtifactId": "preserved-artifact"},
    }

    def execute(**_kwargs):
        if reported:
            return result
        raise ExecutionDeadlineExceeded("execution_deadline_exhausted")

    blocked = []

    def block(loop, **kwargs):
        blocked.append(kwargs)
        return {"status": "blocked", "loop": loop}

    coordinator = SimpleNamespace(
        connection=case.connection,
        _run_with_failover=execute,
        _block_run=block,
        _story_specs_for_tasks=lambda _tasks: [],
    )
    run = _UserMessageRun(
        project_id=case.project["id"],
        message="Preserve partial work",
        actor="worker",
        thread_id=case.thread["id"],
    )
    run.loop, run.workspace = case.loop, case.workspace
    run.thread = case.thread
    run.product_owner_output_record = {"id": "po"}
    run.backlog_artifact = {"id": "backlog"}
    run.request_meta = {"jobId": "untrusted-request-job"}
    with execution_scope(
        ProcessExecutionContext(
            db_path=case.root / "platform.sqlite",
            connection=case.connection,
            execution_id=case.job["id"],
            project_id=case.project["id"],
            in_job_runner=True,
        )
    ):
        assert execute_developer_phase(coordinator, run)["status"] == "blocked"
    assert blocked[0]["stage"] == "worker"
    assert blocked[0]["details"]["interruptedExecutionId"] == case.job["id"]
    assert blocked[0]["details"]["workspaceId"] == case.workspace["id"]
    if reported:
        assert blocked[0]["details"]["runtimeResult"] == result


def test_persisted_cancel_precedes_deadline_block_and_creates_no_repair(timed_out_loop, monkeypatch):
    from local_control_center.process_supervision.context import ExecutionDeadlineExceeded
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun
    from local_control_center.product_loop.phases.execution import execute_developer_phase

    case = timed_out_loop
    coordinator = ProductLoopCoordinator(case.connection, root=case.root)
    run = _UserMessageRun(
        project_id=case.project["id"], message="Preserve work", actor="worker", thread_id=case.thread["id"]
    )
    run.loop, run.workspace, run.thread = case.loop, case.workspace, case.thread
    run.product_owner_output_record, run.backlog_artifact = {"id": "po"}, {"id": "backlog"}

    def deadline(**_kwargs):
        raise ExecutionDeadlineExceeded("execution_deadline_exhausted")

    monkeypatch.setattr(coordinator, "_run_with_failover", deadline)
    monkeypatch.setattr(
        coordinator, "_run_user_message", lambda **_kwargs: execute_developer_phase(coordinator, run)
    )
    case.jobs.cancel_job(case.job["id"], reason="Operator cancelled before deadline handling")
    with execution_scope(
        ProcessExecutionContext(
            db_path=case.root / "platform.sqlite",
            connection=case.connection,
            execution_id=case.job["id"],
            project_id=case.project["id"],
            in_job_runner=True,
        )
    ):
        result = coordinator.run_user_message(
            project_id=case.project["id"],
            message="Preserve work",
            thread_id=case.thread["id"],
            should_abort=lambda: False,
        )
    assert result["status"] == "cancelled"
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"])["state"] == "cancelled"
    assert case.jobs.get_job(case.job["id"])["status"] == "cancelled"
    assert not case.connection.execute("SELECT 1 FROM remediation_actions WHERE status='pending'").fetchone()


@pytest.mark.parametrize(
    "context_change",
    [None, "no_runner", "connection", "project", "attempt", "missing_attempt", "stale_fence"],
)
def test_nested_developer_links_only_its_trusted_current_parent_attempt(
    timed_out_loop, monkeypatch, context_change
):
    from local_control_center.agents.developer_agent import DeveloperAgentRunner

    case = timed_out_loop
    runner = DeveloperAgentRunner(case.connection, root=case.root)
    monkeypatch.setattr(
        runner, "status", lambda **_kwargs: {"selectedRuntimeId": "codex_cli", "reason": "synthetic"}
    )
    monkeypatch.setattr(runner, "_runtime_by_id", lambda _id: {"id": "codex_cli", "kind": "cli"})

    def stop_after_child_creation(**_kwargs):
        raise RuntimeError("synthetic stop before agent execution")

    monkeypatch.setattr(runner.agents, "create_agent_run", stop_after_child_creation)
    context = ProcessExecutionContext(
        db_path=case.root / "platform.sqlite",
        connection=case.connection,
        execution_id=case.job["id"],
        project_id=case.project["id"],
        attempt_id=case.run["id"],
        worker_id=case.leader.owner_id,
        fencing_token=case.leader.fencing_token,
        in_job_runner=True,
    )
    changes = {
        "no_runner": {"in_job_runner": False},
        "connection": {"connection": None},
        "project": {"project_id": "different-project"},
        "attempt": {"attempt_id": "different-attempt"},
        "missing_attempt": {"attempt_id": None},
        "stale_fence": {"fencing_token": case.leader.fencing_token + 1},
    }
    context = replace(context, **changes.get(context_change, {}))
    with execution_scope(context), pytest.raises(RuntimeError, match="synthetic stop"):
        runner.run(
            {
                "projectId": case.project["id"],
                "workspaceId": case.workspace["id"],
                "taskId": "partial",
                "metadata": {"loopId": case.loop["id"]},
                "parentExecutionId": "untrusted-parent",
                "parentAttemptId": "untrusted-attempt",
            }
        )
    row = case.connection.execute("SELECT id FROM jobs WHERE kind='agent.developer'").fetchone()
    payload = case.jobs.get_job(row["id"])["payload"]
    if context_change is None:
        assert payload["parentExecutionId"] == case.job["id"]
        assert payload["parentAttemptId"] == case.run["id"]
        assert payload["loopId"] == case.loop["id"]
    else:
        assert "parentExecutionId" not in payload
        assert "parentAttemptId" not in payload


def _linked_developer_child(case):
    from local_control_center.agents.developer_agent import DeveloperAgentRunner

    runner = DeveloperAgentRunner(case.connection, root=case.root)
    profile = runner._create_profile({"id": "codex_cli", "kind": "cli"})
    payload = {
        "parentExecutionId": case.job["id"],
        "parentAttemptId": case.run["id"],
        "loopId": case.loop["id"],
        "workspaceId": case.workspace["id"],
        "taskId": "partial",
    }
    child = case.jobs.create_job(
        project_id=case.project["id"], kind="agent.developer", status="running", payload=payload
    )["job"]
    agent_run = runner.agents.create_agent_run(
        project_id=case.project["id"],
        agent_profile_id=profile["id"],
        task_id="partial",
        job_id=child["id"],
        status="running",
        input_payload={
            "workspaceId": case.workspace["id"],
            "metadata": {"loopId": case.loop["id"]},
        },
        output_payload={
            "evidence_refs": ["partial-evidence"],
            "runtimeResult": {"stderrArtifactId": "partial-stderr"},
        },
    )
    return child, agent_run


def test_timeout_closes_exact_linked_developer_and_preserves_partial_agent_evidence(timed_out_loop):
    from local_control_center.agents.repository import AgentsRepository

    case = timed_out_loop
    child, agent_run = _linked_developer_child(case)
    assert _reconcile(case)["status"] == "reconciled"
    updated = case.jobs.get_job(child["id"])
    updated_run = AgentsRepository(case.connection).get_agent_run(agent_run["id"])
    assert updated["status"] == "failed"
    assert updated["payload"]["result"]["parentExecutionId"] == case.job["id"]
    assert updated["payload"]["result"]["parentAttemptId"] == case.run["id"]
    assert updated_run["status"] == "failed"
    for key, value in agent_run["output"].items():
        assert updated_run["output"][key] == value
    assert "qaVerdict" not in updated_run["output"]
    assert _reconcile(case)["status"] == "already_reconciled"
    assert case.jobs.get_job(child["id"]) == updated
    assert AgentsRepository(case.connection).get_agent_run(agent_run["id"]) == updated_run
    assert (
        case.connection.execute(
            "SELECT COUNT(*) FROM events WHERE job_id=? AND type='job.failed'", (child["id"],)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        "legacy",
        "parent",
        "attempt",
        "loop",
        "workspace",
        "project",
        "completed",
        "cancelled",
        "lease",
        "own_execution",
        "agent_scope",
    ],
)
def test_timeout_leaves_unlinked_or_independent_developer_children_untouched(timed_out_loop, change):
    from local_control_center.agents.repository import AgentsRepository
    from local_control_center.shared.serialization import json_dumps

    case = timed_out_loop
    child, agent_run = _linked_developer_child(case)
    fields = {
        "parent": "parentExecutionId",
        "attempt": "parentAttemptId",
        "loop": "loopId",
        "workspace": "workspaceId",
    }
    if change == "legacy":
        payload = {key: value for key, value in child["payload"].items() if not key.startswith("parent")}
        case.connection.execute("UPDATE jobs SET payload=? WHERE id=?", (json_dumps(payload), child["id"]))
    elif change in fields:
        payload = {**child["payload"], fields[change]: "different"}
        case.connection.execute("UPDATE jobs SET payload=? WHERE id=?", (json_dumps(payload), child["id"]))
    elif change == "project":
        other = ProjectsRepository(case.connection).create_project(
            name="Other", path=case.root / "other", template_id="other"
        )
        case.connection.execute("UPDATE jobs SET project_id=? WHERE id=?", (other["id"], child["id"]))
    elif change in {"completed", "cancelled"}:
        case.jobs.update_job_status(child["id"], status=change)
    elif change == "lease":
        case.connection.execute("UPDATE jobs SET lease_owner='independent-worker' WHERE id=?", (child["id"],))
    elif change == "own_execution":
        case.connection.execute(
            "INSERT INTO operational_executions (id,job_id,project_id,operation,workload_class,arguments_json,cwd,status,created_at,result_status_code) VALUES (?,?,?,'legacy_job:agent.developer','agent_cli','{}',?,'running',?,200)",
            (child["id"], child["id"], case.project["id"], str(case.root), child["createdAt"]),
        )
    else:
        case.connection.execute(
            "UPDATE agent_runs SET input=json_set(input,'$.metadata.loopId','different') WHERE id=?",
            (agent_run["id"],),
        )
    before_child = case.jobs.get_job(child["id"])
    before_run = AgentsRepository(case.connection).get_agent_run(agent_run["id"])
    assert _reconcile(case)["status"] == "reconciled"
    assert case.jobs.get_job(child["id"]) == before_child
    assert AgentsRepository(case.connection).get_agent_run(agent_run["id"]) == before_run


def test_timeout_preserves_terminal_agent_run_when_linked_child_fails(timed_out_loop):
    from local_control_center.agents.repository import AgentsRepository

    case = timed_out_loop
    child, agent_run = _linked_developer_child(case)
    repository = AgentsRepository(case.connection)
    terminal = repository.update_agent_run_status(
        agent_run["id"], status="completed", output_payload=agent_run["output"]
    )
    assert _reconcile(case)["status"] == "reconciled"
    assert case.jobs.get_job(child["id"])["status"] == "failed"
    assert repository.get_agent_run(agent_run["id"]) == terminal


def test_timeout_linked_child_closure_rolls_back_with_parent(timed_out_loop, monkeypatch):
    from local_control_center.agents.repository import AgentsRepository

    case = timed_out_loop
    child, agent_run = _linked_developer_child(case)

    def fail(*_args, **_kwargs):
        raise OSError("Synthetic nested agent persistence failure")

    monkeypatch.setattr(AgentsRepository, "update_agent_run_status", fail)
    with pytest.raises(OSError, match="Synthetic nested"):
        _reconcile(case)
    assert case.jobs.get_job(child["id"]) == child
    assert AgentsRepository(case.connection).get_agent_run(agent_run["id"]) == agent_run
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"]) == case.loop


@pytest.mark.parametrize(
    "qa_change",
    [None, "orphan", "unrelated", "workspace", "project", "completed", "cancelled", "own_execution"],
)
def test_timeout_during_qa_closes_only_the_developers_owned_qa_run(timed_out_loop, monkeypatch, qa_change):
    from local_control_center.agents.qa_agent import QAAgentRunner
    from local_control_center.agents.repository import AgentsRepository
    from local_control_center.agents.tool_broker import ToolBroker
    from local_control_center.shared.serialization import json_dumps

    case = timed_out_loop
    child, developer_run = _linked_developer_child(case)
    repository = AgentsRepository(case.connection)

    def interrupt_qa(*_args, **_kwargs):
        raise RuntimeError("synthetic interruption during QA")

    monkeypatch.setattr(ToolBroker, "evaluate_tool_call", interrupt_qa)
    with pytest.raises(RuntimeError, match="synthetic interruption during QA"):
        QAAgentRunner(case.connection, root=case.root).run_for_context(
            project_id=case.project["id"],
            workspace_id=case.workspace["id"],
            task_id="partial",
            commands=[["python", "-c", "pass"]],
            job_id=child["id"],
            parent_agent_run_id=developer_run["id"],
            metadata={"source": "developer_agent"},
        )
    row = case.connection.execute(
        "SELECT id FROM agent_runs WHERE job_id=? AND json_extract(metadata,'$.agentProfileId')='qa_agent'",
        (child["id"],),
    ).fetchone()
    qa_run = repository.get_agent_run(row["id"])
    assert "loopId" not in qa_run["input"]["metadata"]
    repository.update_agent_run_status(
        qa_run["id"], status="running", output_payload={"artifactIds": ["existing-partial-qa-log"]}
    )
    if qa_change in {"orphan", "unrelated", "workspace"}:
        changes = {"parentAgentRunId": None}
        if qa_change == "unrelated":
            _other_child, other_run = _linked_developer_child(case)
            changes = {"parentAgentRunId": other_run["id"]}
        elif qa_change == "workspace":
            changes = {"workspaceId": "another-workspace"}
        case.connection.execute(
            "UPDATE agent_runs SET input=? WHERE id=?",
            (json_dumps({**qa_run["input"], **changes}), qa_run["id"]),
        )
    elif qa_change == "project":
        other = ProjectsRepository(case.connection).create_project(
            name="Other QA", path=case.root / "other-qa", template_id="other"
        )
        case.connection.execute("UPDATE agent_runs SET project_id=? WHERE id=?", (other["id"], qa_run["id"]))
    elif qa_change in {"completed", "cancelled"}:
        repository.update_agent_run_status(
            qa_run["id"], status=qa_change, output_payload={"artifactIds": ["existing-partial-qa-log"]}
        )
    elif qa_change == "own_execution":
        independent = case.jobs.create_job(project_id=case.project["id"], kind="agent.qa")["job"]
        case.connection.execute(
            "INSERT INTO operational_executions (id,job_id,project_id,operation,workload_class,arguments_json,cwd,status,created_at,result_status_code) VALUES (?,?,?,'legacy_job:agent.qa','qa_light','{}',?,'running',?,200)",
            (qa_run["id"], independent["id"], case.project["id"], str(case.root), independent["createdAt"]),
        )
    before_qa = repository.get_agent_run(qa_run["id"])
    assert _reconcile(case)["status"] == "reconciled"
    assert case.jobs.get_job(child["id"])["status"] == "failed"
    assert repository.get_agent_run(developer_run["id"])["status"] == "failed"
    after_qa = repository.get_agent_run(qa_run["id"])
    if qa_change is None:
        assert after_qa["status"] == "failed"
        assert after_qa["output"]["artifactIds"] == before_qa["output"]["artifactIds"]
        assert "qaVerdict" not in after_qa["output"]
    else:
        assert after_qa == before_qa
    assert _reconcile(case)["status"] == "already_reconciled"
    assert repository.get_agent_run(qa_run["id"]) == after_qa
