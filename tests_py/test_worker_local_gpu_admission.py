"""Local-only policy must reserve the GPU envelope before the worker invokes a runner."""

from contextlib import closing

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.jobs_approvals.worker import ConcurrentWorker
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.coordinator import THREAD_RESEARCH_JOB_KIND

GIB = 1024**3


@pytest.fixture
def lane(tmp_path, monkeypatch):
    from local_control_center.process_supervision import session_client

    monkeypatch.setattr(session_client, "session_identity", lambda _path: None)
    with closing(open_sqlite_connection(tmp_path / "worker.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="GPU admission", path=tmp_path, template_id="other"
        )
        connection.execute("UPDATE provider_accounts SET enabled=0")
        store = ProviderAccountStore(connection)
        for provider, kind, address in [
            ("ollama", "local", "http://127.0.0.1:11434"),
            ("fixture-api", "api", "https://fixture.invalid/v1"),
        ]:
            store.upsert_provider_account(
                {
                    "providerId": provider,
                    "providerType": kind,
                    "name": provider,
                    "providerFamily": provider,
                    "apiFormat": "ollama" if kind == "local" else "openai_compatible",
                    "baseUrl": address,
                    "enabled": True,
                }
            )
        yield connection, tmp_path / "worker.sqlite", project["id"]


@pytest.mark.parametrize(
    "kind", ["thread.product_loop.run", THREAD_RESEARCH_JOB_KIND, "prompt.optimize", "chat.route"]
)
@pytest.mark.parametrize("policy", ["ollama", "allowed_local", "remote_disabled", "hybrid"])
def test_worker_reserves_effective_local_policy_before_executor(lane, monkeypatch, kind, policy):
    connection, database, project_id = lane
    repository = RuntimeConfigRepository(connection)
    if policy == "ollama":
        repository.set_runtime_setting(
            "project.runtime.defaultMode", "ollama", scope="project", scope_id=project_id
        )
    elif policy == "allowed_local":
        repository.set_runtime_setting(
            "project.runtime.allowedProviders", ["ollama"], scope="project", scope_id=project_id
        )
    elif policy == "remote_disabled":
        repository.set_runtime_setting(
            "project.runtime.remote.enabled", False, scope="project", scope_id=project_id
        )
        repository.set_runtime_setting(
            "project.runtime.cli.enabled", False, scope="project", scope_id=project_id
        )
    job = JobsRepository(connection).create_job(
        project_id=project_id, kind=kind, payload={"workloadClass": "local_gpu_model"}
    )["job"]
    seen = []

    def capture(job, *, connection, **_kwargs):
        lease = ResourceRepository(connection).active_lease_for_execution(job["id"])
        seen.append((lease.workload_class, lease.gpu_required, lease.memory_limit_bytes))
        return {"summary": "fixture completed", "metadata": {}}

    monkeypatch.setattr("local_control_center.jobs_approvals.worker.execute_job", capture)
    ConcurrentWorker(
        db_path=database, resource_snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=48 * GIB)
    ).run_once(worker_id="worker")
    expected = (
        "agent_cli"
        if kind == "thread.product_loop.run"
        else "local_model_call"
        if policy != "hybrid"
        else "remote_llm_light"
    )
    assert seen == [(expected, False, (8 if expected == "agent_cli" else 2) * GIB)]
    assert JobsRepository(connection).get_job(job["id"])["status"] == "completed"
    assert ResourceRepository(connection).active_leases() == []


def test_local_only_policy_claims_the_cli_envelope_without_a_gpu_reservation(lane, monkeypatch):
    connection, database, project_id = lane
    RuntimeConfigRepository(connection).set_runtime_setting(
        "project.runtime.defaultMode", "ollama", scope="project", scope_id=project_id
    )
    job = JobsRepository(connection).create_job(project_id=project_id, kind="thread.product_loop.run")["job"]
    seen = []

    def capture(job, *, connection, **_kwargs):
        lease = ResourceRepository(connection).active_lease_for_execution(job["id"])
        seen.append((lease.workload_class, lease.gpu_required))
        return {"summary": "fixture completed", "metadata": {}}

    monkeypatch.setattr("local_control_center.jobs_approvals.worker.execute_job", capture)
    ConcurrentWorker(
        db_path=database, resource_snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=25 * GIB)
    ).run_once(worker_id="worker")
    assert seen == [("agent_cli", False)]
    assert JobsRepository(connection).get_job(job["id"])["status"] == "completed"
    assert ResourceRepository(connection).active_leases() == []


@pytest.mark.parametrize(
    "before,after,expected", [("ollama", "hybrid", "agent_cli"), ("hybrid", "ollama", "agent_cli")]
)
def test_runner_keeps_admitted_profile_when_policy_changes_after_claim(
    lane, monkeypatch, before, after, expected
):
    from local_control_center.executions.repository import ExecutionRepository
    from local_control_center.jobs_approvals import worker
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    connection, database, project_id = lane
    RuntimeConfigRepository(connection).set_runtime_setting(
        "project.runtime.defaultMode", before, scope="project", scope_id=project_id
    )
    job = JobsRepository(connection).create_job(project_id=project_id, kind="thread.product_loop.run")["job"]
    leader = WorkerLeadershipRepository(connection).acquire(owner_id="worker", lease_seconds=60)
    original = worker.execute_job
    seen = []

    def change_policy(job, *, connection, **kwargs):
        RuntimeConfigRepository(connection).set_runtime_setting(
            "project.runtime.defaultMode", after, scope="project", scope_id=project_id
        )
        return original(job, connection=connection, **kwargs)

    def capture(job, *, connection, **_kwargs):
        row = ExecutionRepository(connection).get(job["id"])
        lease = ResourceRepository(connection).active_lease_for_execution(job["id"])
        seen.append((row["workloadClass"], lease.workload_class))
        return {"summary": "fixture completed", "metadata": {"status": "completed"}}

    monkeypatch.setattr(worker, "execute_job", change_policy)
    monkeypatch.setattr("local_control_center.executions.dispatcher.dispatch_execution", capture)
    ConcurrentWorker(
        db_path=database, resource_snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=48 * GIB)
    ).run_once(worker_id="worker", fencing_token=leader.fencing_token)
    assert seen == [(expected, expected)]
    assert JobsRepository(connection).get_job(job["id"])["status"] == "completed"


@pytest.mark.parametrize(
    "before_mode,after_mode,before_workload,after_workload",
    [
        ("ollama", "hybrid", "local_model_call", "remote_llm_light"),
        ("hybrid", "ollama", "remote_llm_light", "local_model_call"),
    ],
)
def test_same_research_job_retry_updates_profile_before_dispatch(
    lane, monkeypatch, before_mode, after_mode, before_workload, after_workload
):
    from local_control_center.executions.repository import ExecutionRepository
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    connection, database, project_id = lane
    policy = RuntimeConfigRepository(connection)
    policy.set_runtime_setting(
        "project.runtime.defaultMode", before_mode, scope="project", scope_id=project_id
    )
    jobs = JobsRepository(connection)
    job = jobs.create_job(project_id=project_id, kind=THREAD_RESEARCH_JOB_KIND)["job"]
    leader = WorkerLeadershipRepository(connection).acquire(owner_id="worker", lease_seconds=60)
    dispatched = []

    def capture(job, *, connection, **_kwargs):
        executions = ExecutionRepository(connection)
        row = executions.get(job["id"])
        lease = ResourceRepository(connection).active_lease_for_execution(job["id"])
        dispatched.append((row["workloadClass"], lease.workload_class))
        executions.finish(
            job["id"], owner_id="worker", fencing_token=leader.fencing_token, status="completed"
        )
        return {"summary": "fixture completed", "metadata": {"status": "completed"}}

    monkeypatch.setattr("local_control_center.executions.dispatcher.dispatch_execution", capture)
    worker = ConcurrentWorker(
        db_path=database, resource_snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=48 * GIB)
    )
    worker.run_once(worker_id="worker", fencing_token=leader.fencing_token)
    previous = dict(
        connection.execute("SELECT * FROM operational_executions WHERE id=?", (job["id"],)).fetchone()
    )
    policy.set_runtime_setting(
        "project.runtime.defaultMode", after_mode, scope="project", scope_id=project_id
    )
    jobs.retry_job(job["id"], reason="Explicit retry under changed project policy")
    worker.run_once(worker_id="worker", fencing_token=leader.fencing_token)
    current = dict(
        connection.execute("SELECT * FROM operational_executions WHERE id=?", (job["id"],)).fetchone()
    )
    assert dispatched == [(before_workload, before_workload), (after_workload, after_workload)]
    for key in ("id", "job_id", "project_id", "operation", "arguments_json", "cwd", "created_at"):
        assert current[key] == previous[key]


@pytest.mark.parametrize(
    "state",
    [
        "queued",
        "resource_wait",
        "running",
        "completed",
        "failed",
        "cancelled",
        "cancel_requested",
        "cancel_control",
        "started",
        "operation_execute",
        "wrong_identity",
        "stale_fence",
    ],
)
def test_reattach_updates_only_unstarted_uncancelled_legacy_job(lane, state):
    from local_control_center.executions.repository import ExecutionRepository
    from local_control_center.jobs_approvals.repository import StaleWorkerFenceError
    from local_control_center.process_supervision.repository import ManagedProcessRepository
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    connection, database, project_id = lane
    jobs = JobsRepository(connection)
    job = jobs.create_job(project_id=project_id, kind=THREAD_RESEARCH_JOB_KIND)["job"]
    leader = WorkerLeadershipRepository(connection).acquire(owner_id="worker", lease_seconds=60)
    jobs.claim_next_job(worker_id="worker", leader_fencing_token=leader.fencing_token, job_id=job["id"])
    executions = ExecutionRepository(connection)
    kwargs = {"cwd": str(database.parent), "owner_id": "worker", "fencing_token": leader.fencing_token}
    executions.attach_claimed_job(job, workload_class="local_gpu_model", **kwargs)
    if state in {
        "queued",
        "resource_wait",
        "running",
        "completed",
        "failed",
        "cancelled",
        "cancel_requested",
    }:
        connection.execute("UPDATE operational_executions SET status=? WHERE id=?", (state, job["id"]))
    elif state == "cancel_control":
        ManagedProcessRepository(connection).request_execution_cancel(job["id"], reason="Operator cancelled")
    elif state == "started":
        connection.execute(
            "UPDATE operational_executions SET started_at='2026-01-01T00:00:00Z' WHERE id=?", (job["id"],)
        )
    elif state == "operation_execute":
        connection.execute(
            "UPDATE operational_executions SET operation='models.route_execute' WHERE id=?", (job["id"],)
        )
        connection.execute("UPDATE jobs SET kind='operation.execute' WHERE id=?", (job["id"],))
        job = jobs.get_job(job["id"])
    elif state == "wrong_identity":
        job = {**job, "projectId": "different-project"}
    elif state == "stale_fence":
        kwargs["fencing_token"] += 1
    before = dict(
        connection.execute("SELECT * FROM operational_executions WHERE id=?", (job["id"],)).fetchone()
    )
    if state == "stale_fence":
        with pytest.raises(StaleWorkerFenceError):
            executions.attach_claimed_job(job, workload_class="remote_llm_light", **kwargs)
    else:
        executions.attach_claimed_job(job, workload_class="remote_llm_light", **kwargs)
    after = dict(
        connection.execute("SELECT * FROM operational_executions WHERE id=?", (job["id"],)).fetchone()
    )
    expected = (
        {**before, "workload_class": "remote_llm_light"} if state in {"queued", "resource_wait"} else before
    )
    assert after == expected


@pytest.mark.parametrize(
    "preferred_runtime,expected",
    [
        ("ollama", "agent_cli"),
        ("edge-ollama", "agent_cli"),
        ("codex_cli", "agent_cli"),
        ("openai_compatible", "agent_cli"),
        ("unregistered-ollama", "agent_cli"),
        (None, "agent_cli"),
    ],
)
def test_developer_operation_classifies_persisted_preference_not_client_resource_hints(
    lane, preferred_runtime, expected
):
    from local_control_center.executions.router import OperationSpec
    from local_control_center.executions.workloads import operation_workload

    connection, _database, _project_id = lane
    ProviderAccountStore(connection).upsert_provider_account(
        {
            "providerId": "edge-ollama",
            "providerType": "local",
            "name": "Named local fixture",
            "providerFamily": "ollama",
            "apiFormat": "ollama",
            "baseUrl": "http://127.0.0.1:11435",
            "enabled": True,
        }
    )
    payload = {
        "workloadClass": "control_plane",
        "body": {
            "preferredRuntime": preferred_runtime,
            "workloadClass": "control_plane",
            "metadata": {"providerId": "ollama", "preferredRuntime": "ollama"},
        },
    }

    assert (
        operation_workload(connection, OperationSpec("agents.run_developer_agent", "agent_cli"), payload)
        == expected
    )


@pytest.mark.parametrize("available_gib,expected_status", [(20, "resource_wait"), (48, "completed")])
def test_developer_ollama_operation_reserves_only_the_cli_envelope(
    lane, monkeypatch, available_gib, expected_status
):
    from types import SimpleNamespace

    from local_control_center.executions.router import OperationSpec, enqueue_registered_operation

    connection, database, project_id = lane
    spec = OperationSpec("agents.run_developer_agent", "agent_cli")
    platform = SimpleNamespace(
        connection=connection,
        db_path=database,
        cwd=database.parent,
        execution_handlers={spec.name: (spec, None)},
    )
    queued = enqueue_registered_operation(
        platform,
        spec,
        {
            "body": {
                "projectId": project_id,
                "preferredRuntime": "ollama",
                "workloadClass": "control_plane",
            }
        },
    )
    assert queued["workloadClass"] == "agent_cli"
    invoked = []

    def capture(job, *, connection, **_kwargs):
        lease = ResourceRepository(connection).active_lease_for_execution(job["id"])
        invoked.append((lease.workload_class, lease.gpu_required, lease.memory_limit_bytes))
        return {"summary": "fixture completed", "metadata": {}}

    monkeypatch.setattr("local_control_center.jobs_approvals.worker.execute_job", capture)
    ConcurrentWorker(
        db_path=database,
        resource_snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=available_gib * GIB),
    ).run_once(worker_id="worker")

    assert JobsRepository(connection).get_job(queued["jobId"])["status"] == expected_status
    assert invoked == ([] if expected_status == "resource_wait" else [("agent_cli", False, 8 * GIB)])
    assert ResourceRepository(connection).active_leases() == []


def test_developer_local_account_borrows_the_cli_lease_for_a_local_model_call(lane):
    from local_control_center.agents.runtime_readiness import _readiness_resource_request
    from local_control_center.executions.router import OperationSpec
    from local_control_center.executions.workloads import operation_workload
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    connection, database, project_id = lane
    account_store = ProviderAccountStore(connection)
    workload = operation_workload(
        connection,
        OperationSpec("agents.run_developer_agent", "agent_cli"),
        {"body": {"preferredRuntime": "fixture-api"}},
    )
    assert workload == "agent_cli"
    sample = ResourceSnapshot.test_snapshot(available_memory_bytes=48 * GIB)
    governor = HostResourceGovernor(connection)
    lease = governor.admit(
        ResourceAdmissionRequest(execution_id="developer-job", owner_id="worker", workload_class=workload),
        snapshot=sample,
    ).lease
    assert lease is not None
    try:
        account = account_store.upsert_provider_account(
            {
                "providerId": "fixture-api",
                "providerType": "local",
                "providerFamily": "ollama",
                "apiFormat": "ollama",
                "baseUrl": "http://127.0.0.1:11435",
                "enabled": True,
            }
        )
        with execution_scope(
            ProcessExecutionContext(
                db_path=database,
                project_id=project_id,
                execution_id="developer-job",
                worker_id="worker",
                resource_lease_id=lease.id,
                connection=connection,
                in_job_runner=True,
            )
        ):
            request = _readiness_resource_request(connection, account)
            decision = governor.preview(request, snapshot=sample)
        assert request.workload_class == "local_model_call"
        assert request.execution_id == lease.execution_id
        assert decision.status == "admitted"
    finally:
        governor.release(lease.id, reason="test cleanup")
