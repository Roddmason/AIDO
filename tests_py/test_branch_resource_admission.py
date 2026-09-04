from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

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
