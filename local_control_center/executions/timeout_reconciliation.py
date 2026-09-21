"""Close a proven stopped Product Loop without replaying its interrupted workspace."""

from __future__ import annotations

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

from .repository import ExecutionRepository


def _close_linked_developer_jobs(connection, *, execution_id, attempt_id, project_id, loop_id, workspace_id):
    """Close explicit nested ownership inside the already-verified timeout transaction.

    Legacy correlation, shared workspaces and creation times never establish ownership.
    Independent leases/executions and terminal or cancelled records remain untouched.
    """
    from local_control_center.agents.repository import AgentsRepository

    if not connection.in_transaction:
        raise ValueError("Nested job closure requires the verified parent timeout transaction.")
    rows = connection.execute(
        "SELECT j.id FROM jobs j WHERE j.project_id=? AND j.kind='agent.developer' "
        "AND j.status='running' AND j.lease_owner IS NULL AND j.lease_expires_at IS NULL "
        "AND json_extract(j.payload,'$.parentExecutionId')=? "
        "AND json_extract(j.payload,'$.parentAttemptId')=? "
        "AND json_extract(j.payload,'$.loopId')=? AND json_extract(j.payload,'$.workspaceId')=? "
        "AND NOT EXISTS (SELECT 1 FROM operational_executions e WHERE e.job_id=j.id OR e.id=j.id) "
        "AND NOT EXISTS (SELECT 1 FROM job_runs r WHERE r.job_id=j.id) "
        "AND NOT EXISTS (SELECT 1 FROM resource_leases l WHERE l.execution_id=j.id AND l.released_at IS NULL) "
        "AND NOT EXISTS (SELECT 1 FROM managed_processes p WHERE p.execution_id=j.id)",
        (project_id, execution_id, attempt_id, loop_id, workspace_id),
    ).fetchall()
    jobs, agents = JobsRepository(connection), AgentsRepository(connection)
    closed = []
    for row in rows:
        if ManagedProcessRepository(connection).cancellation_reason(row["id"]):
            continue
        runs = [
            agents.get_agent_run(item["id"])
            for item in connection.execute(
                "SELECT id FROM agent_runs WHERE job_id=?", (row["id"],)
            ).fetchall()
        ]
        developer_runs = [
            run for run in runs if (run["metadata"] or {}).get("agentProfileId") == "developer_agent"
        ]
        if any(
            run["projectId"] != project_id
            or run["input"].get("workspaceId") != workspace_id
            or (run["input"].get("metadata") or {}).get("loopId") != loop_id
            for run in developer_runs
        ):
            continue
        developer_ids = {run["id"] for run in developer_runs}
        owned_runs = developer_runs + [
            run
            for run in runs
            if (
                (run["metadata"] or {}).get("agentProfileId") == "qa_agent"
                and run["projectId"] == project_id
                and run["input"].get("workspaceId") == workspace_id
                and run["input"].get("parentAgentRunId") in developer_ids
                and not ManagedProcessRepository(connection).cancellation_reason(run["id"])
                and not connection.execute(
                    "SELECT 1 WHERE EXISTS (SELECT 1 FROM operational_executions WHERE id=? OR job_id=?) "
                    "OR EXISTS (SELECT 1 FROM managed_processes WHERE execution_id=?) "
                    "OR EXISTS (SELECT 1 FROM resource_leases WHERE execution_id=? AND released_at IS NULL)",
                    (run["id"], run["id"], run["id"], run["id"]),
                ).fetchone()
            )
        ]
        interruption = {
            "reason": "execution_deadline_exhausted",
            "parentExecutionId": execution_id,
            "parentAttemptId": attempt_id,
            "loopId": loop_id,
            "workspaceId": workspace_id,
            "partialEvidence": True,
        }
        job = jobs.get_job(row["id"])
        jobs.update_job_status(
            job["id"], status="failed", metadata={**(job["payload"].get("result") or {}), **interruption}
        )
        for run in owned_runs:
            if run["status"] == "running":
                agents.update_agent_run_status(
                    run["id"],
                    status="failed",
                    output_payload={**run["output"], "interruptedExecution": interruption},
                )
        closed.append(job["id"])
    return closed


def timeout_loop_for_job(connection, job):
    """Resolve exact durable execution ownership; recency alone is never authority."""
    rows = connection.execute(
        "SELECT id FROM product_loops WHERE project_id=? "
        "AND json_extract(context,'$.durableRun.thread.projectThreadId')=? "
        "AND COALESCE(json_extract(context,'$.durableRun.effectiveRequestMeta.jobId'),"
        "json_extract(context,'$.durableRun.requestMeta.jobId'))=?",
        (job["projectId"], job["payload"].get("threadId"), job["id"]),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("The interrupted job does not own exactly one durable loop.")
    return ProductLoopRepository(connection).get_loop(rows[0]["id"])


def reconcile_product_loop_timeout(
    connection,
    *,
    execution_id,
    expected_loop_id,
    expected_loop_version,
    expected_attempt_id,
    supervision_result,
    owner_id,
    fencing_token,
    historical=False,
):
    """Consume a trusted internal outcome, never an HTTP/client assertion.

    Online callers retain the worker fence. Historical repair requires the exact latest failed
    attempt and immutable terminal execution, without restoring its expired lease or running it.
    """
    with immediate_transaction(connection):
        jobs = JobsRepository(connection)
        job = jobs.get_job(execution_id)
        loop = ProductLoopRepository(connection).get_loop(expected_loop_id)
        expected_context = json_dumps(loop["context"])
        durable = loop["context"].get("durableRun") or {}
        prior = durable.get("interruptedExecution") or {}
        if prior.get("jobId") == execution_id and prior.get("attemptId") == expected_attempt_id:
            return {"status": "already_reconciled", "loopId": loop["id"]}
        execution = ExecutionRepository(connection).get(execution_id)
        attempt = connection.execute(
            "SELECT * FROM job_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (execution_id,)
        ).fetchone()
        if (
            job["kind"] != "thread.product_loop.run"
            or execution["operation"] != "legacy_job:thread.product_loop.run"
            or job["projectId"] != loop["projectId"]
            or execution["projectId"] != loop["projectId"]
            or job["payload"].get("threadId") != (durable.get("thread") or {}).get("projectThreadId")
            or timeout_loop_for_job(connection, job)["id"] != loop["id"]
            or attempt is None
            or attempt["id"] != expected_attempt_id
            or ManagedProcessRepository(connection).cancellation_reason(execution_id)
            or job["status"] == "cancelled"
        ):
            raise ValueError("The interrupted job, attempt, loop or cancellation scope changed.")
        if historical:
            if (
                job["status"] != "failed"
                or attempt["status"] != "failed"
                or execution["status"] != "failed"
                or execution["reason"] != "timeout"
            ):
                raise ValueError("Historical repair requires the exact failed timeout attempt.")
        else:
            ExecutionRepository(connection).require_fence(
                execution_id, owner_id=owner_id, fencing_token=fencing_token
            )
            if (
                attempt["status"] != "running"
                or attempt["worker_owner_id"] != owner_id
                or attempt["leader_fencing_token"] != fencing_token
            ):
                raise ValueError("The interrupted attempt is no longer owned by this worker.")
        if (
            loop["state"] != "executing"
            or loop["version"] != expected_loop_version
            or connection.execute(
                "SELECT 1 FROM product_loops WHERE project_id=? AND rowid>(SELECT rowid FROM product_loops WHERE id=?) "
                "AND json_extract(context,'$.durableRun.thread.projectThreadId')=? LIMIT 1",
                (loop["projectId"], loop["id"], job["payload"]["threadId"]),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND kind='thread.product_loop.run' "
                "AND rowid>(SELECT rowid FROM jobs WHERE id=?) AND json_extract(payload,'$.threadId')=? "
                "AND status IN ('queued','running','resource_wait','approval_required') LIMIT 1",
                (loop["projectId"], execution_id, job["payload"]["threadId"]),
            ).fetchone()
        ):
            raise ValueError("The interrupted loop was replaced, completed or changed.")
        process = ManagedProcessRepository(connection).get(
            str(supervision_result.get("managedProcessId") or "")
        )
        if (
            process is None
            or process.execution_id != execution_id
            or supervision_result.get("executionId") != execution_id
            or supervision_result.get("timedOut") is not True
            or supervision_result.get("cancelled") is not False
            or supervision_result.get("terminationReason") != "timeout"
            or type(supervision_result.get("remainingDescendantCount")) is not int
            or supervision_result["remainingDescendantCount"] != 0
            or not process.timed_out
            or process.cancelled
            or process.termination_reason != "timeout"
            or not process.finished_at
            or not process.released_at
            or process.started_at < attempt["started_at"]
            or connection.execute(
                "SELECT 1 FROM managed_processes WHERE execution_id=? AND (finished_at IS NULL OR released_at IS NULL) LIMIT 1",
                (execution_id,),
            ).fetchone()
        ):
            raise ValueError("The timeout has no matching terminal evidence of a stopped process tree.")
        workspace = connection.execute(
            "SELECT * FROM workspaces WHERE id=?", (durable.get("workspaceId"),)
        ).fetchone()
        thread = ThreadsRepository(connection).get_thread(job["payload"]["threadId"])
        if (
            workspace is None
            or workspace["project_id"] != loop["projectId"]
            or thread["projectId"] != loop["projectId"]
            or thread["status"] not in {"running", "queued"}
        ):
            raise ValueError("The interrupted workspace or thread no longer matches the execution.")
        reason = "The execution reached its 900-second deadline. Partial workspace changes are preserved; inspect them before deciding how to continue."
        details = {
            "interruptedExecutionId": execution_id,
            "jobId": execution_id,
            "loopId": loop["id"],
            "threadId": thread["id"],
            "projectId": loop["projectId"],
            "workspaceId": workspace["id"],
            "managedProcessId": process.managed_process_id,
            "attemptId": attempt["id"],
            "timedOut": True,
            "remainingDescendantCount": 0,
            "reason": "execution_deadline_exhausted",
            "partialEvidence": True,
            "proofSource": supervision_result.get("proofSource", "native_supervisor"),
            "observedAt": supervision_result.get("observedAt"),
            "evidenceRef": supervision_result.get("evidenceRef"),
        }
        durable.update(
            status="blocked",
            blockedStage="worker",
            blockedReason=reason,
            runActive=False,
            worker=details,
            interruptedExecution={**details, "reconciledAt": utc_now()},
        )
        # The immediate transaction serializes scope checks with the FSM transition and UI evidence.
        if (
            connection.execute(
                "UPDATE product_loops SET context=context WHERE id=? AND version=? AND context=?",
                (loop["id"], expected_loop_version, expected_context),
            ).rowcount
            != 1
        ):
            raise ValueError("The interrupted loop changed concurrently.")
        _close_linked_developer_jobs(
            connection,
            execution_id=execution_id,
            attempt_id=attempt["id"],
            project_id=loop["projectId"],
            loop_id=loop["id"],
            workspace_id=workspace["id"],
        )
        coordinator = ProductLoopCoordinator(connection, root=job["payload"].get("root"))
        updated = coordinator.transition_in_transaction(
            loop["id"],
            to_state="blocked",
            reason=reason,
            actor="timeout_reconciliation",
            trigger="execution_timeout",
            expected_version=expected_loop_version,
            context_patch={"durableRun": durable},
            metadata=details,
        )
        threads = ThreadsRepository(connection)
        threads.set_status(thread["id"], "blocked")
        threads.record_event(
            thread_id=thread["id"],
            type="worker_failed",
            agent_role="aido_lead",
            payload={**details, "reason": reason},
        )
        cards = RemediationActionsRepository(connection)
        connection.execute(
            "UPDATE remediation_actions SET status='dismissed',resolved_at=? WHERE loop_id=? AND status='pending' AND action_type IN ('retry_loop','run_worker_once','approve_runtime_risk')",
            (utc_now(), loop["id"]),
        )
        action = cards.create_action(
            project_id=loop["projectId"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="worker",
            blocker_type="runtime_execution_failed",
            action_type="view_diff",
            title="Inspect partial workspace changes",
            description=reason,
            payload=details,
            technical_reason="execution_deadline_exhausted",
            primary=True,
        )
        return {"status": "reconciled", "loopId": updated["id"], "actionId": action["id"]}


def record_timeout_observation(connection, *, execution_id, attempt_id, supervision_result):
    """Persist only the supervisor's bounded native proof for later per-thread reconciliation."""
    from local_control_center.shared.event_bus import EventBus

    proof = {
        key: supervision_result.get(key)
        for key in (
            "managedProcessId",
            "executionId",
            "timedOut",
            "cancelled",
            "terminationReason",
            "remainingDescendantCount",
        )
    }
    EventBus(connection).record_event(
        job_id=execution_id,
        event_type="execution.timeout_observed",
        payload={"attemptId": attempt_id, "supervisionResult": proof},
    )


def reconcile_thread_timeout(connection, *, thread_id, project_id):
    """Best-effort repair of this thread only, after a failed attempt and proven native drainage."""
    row = connection.execute(
        "SELECT id FROM product_loops WHERE project_id=? AND json_extract(context,'$.durableRun.thread.projectThreadId')=? ORDER BY rowid DESC LIMIT 1",
        (project_id, thread_id),
    ).fetchone()
    if row is None:
        return {"status": "not_applicable"}
    loop = ProductLoopRepository(connection).get_loop(row["id"])
    if loop["state"] != "executing":
        return {"status": "not_applicable"}
    durable = loop["context"].get("durableRun") or {}
    job_id = (durable.get("effectiveRequestMeta") or {}).get("jobId") or (
        durable.get("requestMeta") or {}
    ).get("jobId")
    observation = connection.execute(
        "SELECT payload FROM events WHERE job_id=? AND type='execution.timeout_observed' ORDER BY rowid DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if observation is None:
        return {"status": "pending_evidence"}
    payload = json_loads(observation["payload"], {})
    if not isinstance(payload, dict):
        return {"status": "pending_evidence"}
    try:
        return reconcile_product_loop_timeout(
            connection,
            execution_id=job_id,
            expected_loop_id=loop["id"],
            expected_loop_version=loop["version"],
            expected_attempt_id=payload.get("attemptId"),
            supervision_result=payload.get("supervisionResult") or {},
            owner_id=None,
            fencing_token=None,
            historical=True,
        )
    except (ValueError, KeyError):
        return {"status": "not_applicable"}
