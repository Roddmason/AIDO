from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.host_resources.branch_admission import BranchAdmission
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope


def test_three_branches_share_parent_slot_and_never_exceed_global_two(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    snapshot = ResourceSnapshot.test_snapshot()
    lease = (
        HostResourceGovernor(runtime.connection)
        .admit(
            ResourceAdmissionRequest(
                execution_id="parent", owner_id="worker", workload_class="remote_llm_light"
            ),
            snapshot=snapshot,
        )
        .lease
    )
    with execution_scope(
        ProcessExecutionContext(
            db_path=runtime.db_path, execution_id="parent", resource_lease_id=lease.id, worker_id="worker"
        )
    ):
        admission = BranchAdmission(runtime.db_path, snapshot_source=ResourceSnapshot.test_snapshot)
    active, peaks, lock = [], [], threading.Lock()

    def branch(index):
        with admission.reserve(f"branch-{index}", "remote_llm_light"):
            with lock:
                active.append(index)
                peaks.append(len(active))
            time.sleep(0.2)
            with lock:
                active.remove(index)

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(branch, range(3)))
        assert max(peaks) == 2
        assert [item.id for item in ResourceRepository(runtime.connection).active_leases()] == [lease.id]
    finally:
        HostResourceGovernor(runtime.connection).release(lease.id, reason="test_complete")
        runtime.close()


def test_expired_branch_lease_cannot_be_recovered_while_parent_tree_lives(tmp_path):
    from local_control_center.process_supervision.models import ProcessLaunchSpec
    from local_control_center.process_supervision.repository import ManagedProcessRepository
    from local_control_center.shared.time import utc_now

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        spec = ProcessLaunchSpec(
            managed_process_id="parent-process",
            execution_id="parent",
            argv=["test"],
            cwd=tmp_path,
            workload_class="remote_llm_light",
            command_fingerprint="test",
            memory_limit_bytes=1,
            process_limit=1,
            cpu_limit_percent=10,
            below_normal_priority=True,
        )
        ManagedProcessRepository(runtime.connection).start(spec, root_pid=999999)
        lease = (
            HostResourceGovernor(runtime.connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="branch",
                    parent_execution_id="parent",
                    owner_id="worker",
                    workload_class="remote_llm_light",
                ),
                snapshot=ResourceSnapshot.test_snapshot(),
            )
            .lease
        )
        runtime.connection.execute("UPDATE resource_leases SET expires_at='2000-01-01T00:00:00Z'")
        resources = ResourceRepository(runtime.connection)
        assert resources.recover_expired() == []
        assert resources.active_leases()[0].id == lease.id
        runtime.connection.execute("UPDATE managed_processes SET finished_at=?", (utc_now(),))
        assert resources.recover_expired()[0].id == lease.id
    finally:
        runtime.close()


@pytest.fixture
def admitted_parent(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    lease = (
        HostResourceGovernor(runtime.connection)
        .admit(
            ResourceAdmissionRequest(execution_id="parent", owner_id="worker", workload_class="agent_cli"),
            snapshot=ResourceSnapshot.test_snapshot(),
        )
        .lease
    )
    context = ProcessExecutionContext(
        db_path=runtime.db_path,
        execution_id="parent",
        resource_lease_id=lease.id,
        worker_id="worker",
    )
    try:
        yield SimpleNamespace(runtime=runtime, lease=lease, context=context)
    finally:
        runtime.close()


def test_lighter_branch_borrows_verified_parent_and_never_releases_it(admitted_parent):
    parent = admitted_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(available_memory_bytes=25 * 1024**3),
        )
        with (
            pytest.raises(ValueError, match="branch failure"),
            admission.reserve("probe-one", "remote_llm_light", timeout_seconds=0) as lease,
        ):
            assert lease.id == parent.lease.id
            assert len(ResourceRepository(parent.runtime.connection).active_leases()) == 1
            raise ValueError("branch failure")
        with admission.reserve("probe-two", "remote_llm_light", timeout_seconds=0) as lease:
            assert lease.id == parent.lease.id
    persisted = ResourceRepository(parent.runtime.connection).get_lease(parent.lease.id)
    assert persisted.released_at is None
    assert persisted.heartbeat_at == parent.lease.heartbeat_at


@pytest.mark.parametrize("field", ["db_path", "execution_id", "resource_lease_id", "worker_id"])
def test_parent_identity_mismatch_cannot_borrow(admitted_parent, field):
    parent = admitted_parent
    value = parent.runtime.db_path.parent / "different.sqlite" if field == "db_path" else "different"
    with execution_scope(replace(parent.context, **{field: value})):
        admission = BranchAdmission(parent.runtime.db_path, snapshot_source=ResourceSnapshot.test_snapshot)
        with (
            pytest.raises(RuntimeError, match="resource_parent_"),
            admission.reserve("probe", "remote_llm_light", timeout_seconds=0),
        ):
            pytest.fail("Unverified parent identity reached inference")
    assert ResourceRepository(parent.runtime.connection).get_lease(parent.lease.id).released_at is None


@pytest.mark.parametrize("state", ["expired", "released", "cancelled", "stale_fence"])
def test_inactive_cancelled_or_fenced_parent_cannot_borrow(admitted_parent, state):
    from local_control_center.jobs_approvals.repository import StaleWorkerFenceError
    from local_control_center.process_supervision.repository import ManagedProcessRepository

    parent = admitted_parent
    context = parent.context
    if state == "expired":
        parent.runtime.connection.execute("UPDATE resource_leases SET expires_at='2000-01-01T00:00:00Z'")
    elif state == "released":
        HostResourceGovernor(parent.runtime.connection).release(parent.lease.id, reason="test")
    elif state == "cancelled":
        ManagedProcessRepository(parent.runtime.connection).request_execution_cancel("parent", reason="test")
    else:
        context = replace(context, fencing_token=987)
    with execution_scope(context):
        admission = BranchAdmission(parent.runtime.db_path, snapshot_source=ResourceSnapshot.test_snapshot)
        with (
            pytest.raises((RuntimeError, StaleWorkerFenceError)),
            admission.reserve("probe", "remote_llm_light", timeout_seconds=0),
        ):
            pytest.fail("Invalid parent reached inference")


@pytest.mark.parametrize("insufficient", ["cpu_limit_percent", "memory_limit_bytes", "process_limit", "gpu"])
def test_parent_limits_must_cover_branch_without_gpu_expansion(admitted_parent, insufficient):
    parent = admitted_parent
    workload = "remote_llm_light"
    if insufficient == "gpu":
        parent.runtime.connection.execute(
            "UPDATE resource_leases SET cpu_limit_percent=100,memory_limit_bytes=?,process_limit=64",
            (32 * 1024**3,),
        )
        workload = "local_gpu_model"
    else:
        parent.runtime.connection.execute(f"UPDATE resource_leases SET {insufficient}=1")
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(available_memory_bytes=0),
        )
        with (
            pytest.raises(RuntimeError, match="hard_memory_floor"),
            admission.reserve("probe", workload, timeout_seconds=0),
        ):
            pytest.fail("Insufficient parent limits were borrowed")


def test_busy_parent_defers_immediately_with_real_capacity_reason(admitted_parent, monkeypatch):
    """El padre (request 4 GiB) cabe en 4,25 de margen; una segunda rama (0,5) ya no."""
    from local_control_center.host_resources import branch_admission

    def no_wait(_seconds):
        pytest.fail("Nonblocking preflight must not sleep")

    monkeypatch.setattr(branch_admission.time, "sleep", no_wait)
    parent = admitted_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(
                available_memory_bytes=int(20.25 * 1024**3)
            ),
        )
        with (
            admission.reserve("probe-one", "remote_llm_light", timeout_seconds=0),
            pytest.raises(RuntimeError, match="aggregate_memory_budget"),
            admission.reserve("probe-two", "remote_llm_light", timeout_seconds=0),
        ):
            pytest.fail("Concurrent branches reused the same reservation")
    assert ResourceRepository(parent.runtime.connection).get_lease(parent.lease.id).released_at is None


@pytest.mark.parametrize(
    "overrides,reason",
    [
        # El padre completo reserva 4 GiB (su request): con 19 GiB el margen es 3.
        ({"available_memory_bytes": 19 * 1024**3}, "aggregate_memory_budget"),
        ({"cpu_percent_1s": 99}, "host_cpu_saturated"),
        (
            {"sampled_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            "resource_snapshot_stale",
        ),
    ],
)
def test_borrow_checks_fresh_host_capacity_for_whole_parent(admitted_parent, overrides, reason):
    parent = admitted_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path, snapshot_source=lambda: ResourceSnapshot.test_snapshot(**overrides)
        )
        with (
            pytest.raises(RuntimeError, match=reason),
            admission.reserve("probe", "remote_llm_light", timeout_seconds=0),
        ):
            pytest.fail("Borrowing bypassed current host limits")
    assert ResourceRepository(parent.runtime.connection).get_lease(parent.lease.id).released_at is None


@pytest.mark.parametrize(
    "condition",
    ["verified", "unverified", "lease_mismatch", "aggregate_mismatch", "role", "cpu", "memory", "processes"],
)
def test_verified_capture_session_borrows_only_execution_subbudget(tmp_path, monkeypatch, condition):
    from local_control_center.host_resources import profiles
    from local_control_center.process_supervision import session_client

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    snapshot = ResourceSnapshot.test_snapshot(available_memory_bytes=38 * 1024**3)
    lease = (
        HostResourceGovernor(runtime.connection)
        .admit(
            ResourceAdmissionRequest(
                execution_id="launcher", owner_id="launcher-owner", workload_class="capture_session"
            ),
            snapshot=snapshot,
        )
        .lease
    )
    assert lease is not None
    if condition == "memory":
        monkeypatch.setitem(profiles.CAPTURE_SESSION_PARTS, "execution", {"memoryBytes": 1, "cpuPercent": 26})
    if condition == "processes":
        runtime.connection.execute("UPDATE resource_leases SET process_limit=1 WHERE id=?", (lease.id,))
        lease = ResourceRepository(runtime.connection).get_lease(lease.id)

    def verified_identity(database):
        assert database.resolve() == runtime.db_path.resolve()
        if condition == "unverified":
            return None
        return {"aggregateId": "launcher-process"}, lease

    monkeypatch.setattr(session_client, "session_identity", verified_identity)
    context = ProcessExecutionContext(
        db_path=runtime.db_path,
        execution_id="worker-job",
        worker_id="worker-owner",
        resource_lease_id="different" if condition == "lease_mismatch" else lease.id,
        aggregate_managed_process_id="different" if condition == "aggregate_mismatch" else "launcher-process",
        session_role="worker" if condition == "role" else "execution",
        in_job_runner=True,
    )
    try:
        with execution_scope(context):
            admission = BranchAdmission(runtime.db_path, snapshot_source=lambda: snapshot)
            if condition == "verified":
                with admission.reserve("probe", "remote_llm_light", timeout_seconds=0) as borrowed:
                    assert borrowed.id == lease.id
            else:
                workload = "agent_cli" if condition == "cpu" else "remote_llm_light"
                with (
                    pytest.raises((RuntimeError, PermissionError)),
                    admission.reserve("probe", workload, timeout_seconds=0),
                ):
                    pytest.fail("Unverified or excessive session budget was borrowed")
        assert ResourceRepository(runtime.connection).get_lease(lease.id).released_at is None
        assert runtime.connection.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 1
    finally:
        runtime.close()


@pytest.fixture
def admitted_gpu_parent(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "gpu.sqlite")
    runtime.init()
    lease = (
        HostResourceGovernor(runtime.connection)
        .admit(
            ResourceAdmissionRequest(
                execution_id="gpu-parent", owner_id="worker", workload_class="local_gpu_model"
            ),
            snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=48 * 1024**3),
        )
        .lease
    )
    assert lease is not None
    context = ProcessExecutionContext(
        db_path=runtime.db_path, execution_id="gpu-parent", resource_lease_id=lease.id, worker_id="worker"
    )
    try:
        yield SimpleNamespace(runtime=runtime, lease=lease, context=context)
    finally:
        runtime.close()


def test_cli_parent_never_borrows_gpu_even_with_sufficient_numeric_limits(admitted_parent):
    parent = admitted_parent
    parent.runtime.connection.execute(
        "UPDATE resource_leases SET cpu_limit_percent=60,memory_limit_bytes=?,process_limit=32 WHERE id=?",
        (32 * 1024**3, parent.lease.id),
    )
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(available_memory_bytes=64 * 1024**3),
        )
        with (
            pytest.raises(RuntimeError, match="heavy_workload_capacity"),
            admission.reserve("gpu-probe", "local_gpu_model", timeout_seconds=0),
        ):
            pytest.fail("A CLI parent claimed GPU authority")


def test_gpu_branch_borrows_only_one_verified_gpu_parent_slot(admitted_gpu_parent):
    parent = admitted_gpu_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(available_memory_bytes=48 * 1024**3),
        )
        with admission.reserve("gpu-one", "local_gpu_model", timeout_seconds=0) as lease:
            assert lease.id == parent.lease.id
            with (
                pytest.raises(RuntimeError, match="heavy_workload_capacity"),
                admission.reserve("gpu-two", "local_gpu_model", timeout_seconds=0),
            ):
                pytest.fail("Parallel GPU branches borrowed the same parent")
        with admission.reserve("gpu-next", "local_gpu_model", timeout_seconds=0) as lease:
            assert lease.id == parent.lease.id
    assert [lease.id for lease in ResourceRepository(parent.runtime.connection).active_leases()] == [
        parent.lease.id
    ]
    assert (
        ResourceRepository(parent.runtime.connection).get_lease(parent.lease.id).heartbeat_at
        == parent.lease.heartbeat_at
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "cpu_limit_percent",
        "memory_limit_bytes",
        "process_limit",
        "gpu_required",
        "cancelled",
        "stale_fence",
        "host_memory",
    ],
)
def test_gpu_borrow_preserves_limits_fence_cancellation_and_current_capacity(admitted_gpu_parent, invalid):
    from local_control_center.process_supervision.repository import ManagedProcessRepository

    parent = admitted_gpu_parent
    context = parent.context
    if invalid in {"cpu_limit_percent", "memory_limit_bytes", "process_limit", "gpu_required"}:
        parent.runtime.connection.execute(
            f"UPDATE resource_leases SET {invalid}=0 WHERE id=?", (parent.lease.id,)
        )
    elif invalid == "cancelled":
        ManagedProcessRepository(parent.runtime.connection).request_execution_cancel(
            context.execution_id, reason="test"
        )
    elif invalid == "stale_fence":
        context = replace(context, fencing_token=987)
    available = 25 if invalid == "host_memory" else 48
    with execution_scope(context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(
                available_memory_bytes=available * 1024**3
            ),
        )
        with (
            pytest.raises(RuntimeError),
            admission.reserve("gpu-probe", "local_gpu_model", timeout_seconds=0),
        ):
            pytest.fail("GPU branch exceeded its verified authority")
    assert ResourceRepository(parent.runtime.connection).get_lease(parent.lease.id).released_at is None


def test_local_model_call_borrows_the_cli_job_with_twenty_gib_free_and_the_default_floor(admitted_parent):
    parent = admitted_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(available_memory_bytes=20 * 1024**3),
        )
        with admission.reserve("local-call", "local_model_call", timeout_seconds=0) as lease:
            assert lease.id == parent.lease.id
    assert [item.id for item in ResourceRepository(parent.runtime.connection).active_leases()] == [
        parent.lease.id
    ]


def test_remote_child_still_rechecks_the_full_parent_budget(admitted_parent):
    """A diferencia de ``local_model_call``, el hijo remoto revisa el presupuesto del padre: 4 > 3,5."""
    parent = admitted_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(
                available_memory_bytes=int(19.5 * 1024**3)
            ),
        )
        with (
            pytest.raises(RuntimeError, match="aggregate_memory_budget"),
            admission.reserve("remote-call", "remote_llm_light", timeout_seconds=0),
        ):
            pytest.fail("A remote child hid the parent budget")


def test_unreal_blocks_a_local_model_call_even_when_the_parent_covers_it(admitted_parent):
    parent = admitted_parent
    with execution_scope(parent.context):
        admission = BranchAdmission(
            parent.runtime.db_path,
            snapshot_source=lambda: ResourceSnapshot.test_snapshot(
                available_memory_bytes=48 * 1024**3, unreal_editor_running=True
            ),
        )
        with (
            pytest.raises(RuntimeError, match="unreal_local_gpu_conflict"),
            admission.reserve("local-call", "local_model_call", timeout_seconds=0),
        ):
            pytest.fail("Local inference borrowed the parent while UnrealEditor was active")
