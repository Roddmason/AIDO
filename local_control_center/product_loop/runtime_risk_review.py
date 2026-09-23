"""Scoped risk consent over immutable Jev receipts and an existing planning checkpoint.

No inference, credential changes, cost approvals or OS permission grants occur here.

@author Rodrigo Mason
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from local_control_center.agents.agent_resource_policy import (
    apply_profile_limits,
    effective_resource_profile,
    profile_fingerprint,
)
from local_control_center.agents.model_execution_health import (
    model_validation_rejection,
    provider_configuration_fingerprint,
)
from local_control_center.decision_engine.config import resolve_config
from local_control_center.decision_engine.models import fingerprint
from local_control_center.decision_engine.observers import _identity
from local_control_center.decision_engine.repository import DecisionRepository
from local_control_center.decision_engine.runtime_selection import risk_review_evidence
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.context import CURRENT_EXECUTION, assert_external_boundary
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.product_loop.research_resolution import (
    _artifact_bytes,
    hydrate_research_run,
    resolution_snapshot,
    validate_research,
)
from local_control_center.project_constitution.repository import ProjectConstitutionRepository
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

RISK_REVIEW_TTL = timedelta(hours=24)
ACTION_TYPE = "product_loop.approve_runtime_risk"


def _planning_dependencies(coordinator, loop, tasks):
    ids = {item["id"] for item in tasks}
    return sorted(
        [
            item
            for item in coordinator.backlog.list_task_dependencies(project_id=loop["projectId"])
            if item["taskId"] in ids or item["dependsOnTaskId"] in ids
        ],
        key=lambda item: item["id"],
    )


def seal_technical_plan(coordinator, loop, *, tasks, raw_plan):
    """Seal the original planner result before routing can interrupt its stack frame."""
    return {
        "origin": "original",
        "plan": raw_plan,
        "planHash": fingerprint(raw_plan),
        "tasksHash": fingerprint(tasks),
        "dependenciesHash": fingerprint(_planning_dependencies(coordinator, loop, tasks)),
    }


def _resolve_technical_plan(coordinator, loop):
    """Recover missing legacy aggregates only after proving the persisted task graph equivalent."""
    from local_control_center.backlog.technical_lead_planner import TechnicalLeadPlanner
    from local_control_center.team_scheduler.scheduler import schedule_team

    durable = loop["context"]["durableRun"]
    tasks = durable.get("agentTasks") or []
    dependencies = _planning_dependencies(coordinator, loop, tasks)
    checkpoint = durable.get("technicalLeadPlan")
    if isinstance(checkpoint, dict):
        if (
            checkpoint.get("origin") not in {"original", "reconstructed"}
            or not isinstance(checkpoint.get("plan"), dict)
            or checkpoint.get("planHash") != fingerprint(checkpoint["plan"])
            or checkpoint.get("tasksHash") != fingerprint(tasks)
            or checkpoint.get("dependenciesHash") != fingerprint(dependencies)
        ):
            raise ValueError("The persisted TechnicalLead plan or task graph changed.")
        return checkpoint
    po = durable["productOwner"]
    artifact, content = _artifact_bytes(
        coordinator.evidence,
        durable["backlog"]["artifactId"],
        root=coordinator.root,
        project_id=loop["projectId"],
        evidence_id=po.get("evidencePackageId"),
        kind="product_backlog",
    )
    source = json_loads(content.decode("utf-8"))
    backlog = source.get("backlog")
    if (
        source.get("productOwnerOutputId") != po["productOwnerOutputId"]
        or not isinstance(backlog, list)
        or not backlog
    ):
        raise ValueError("The legacy plan has no verified source backlog.")
    for group in backlog:
        if coordinator.backlog.get_epic(group["epic"]["id"]) != group["epic"]:
            raise ValueError("The legacy plan epic changed.")
        for item in group.get("stories") or []:
            story = item["story"]
            if coordinator.backlog.get_user_story(story["id"]) != story or sorted(
                coordinator.backlog.list_acceptance_criteria(story["id"]), key=lambda ac: ac["id"]
            ) != sorted(item["acceptanceCriteria"], key=lambda ac: ac["id"]):
                raise ValueError("The legacy plan story or acceptance criteria changed.")
    output = coordinator.discovery.get_product_owner_output(po["productOwnerOutputId"])
    schedule = durable["teamSchedule"]
    # The final schedule expands from generated tasks (e.g. security adds pentester).
    # The planner originally received the preliminary schedule, before those tasks existed.
    preliminary_scope = coordinator._team_scope(
        message=durable["message"], intent=schedule.get("intent") or {}, output=output, agent_tasks=[]
    )
    preliminary = {
        **schedule_team(scope=preliminary_scope, risk=schedule["risk"], mode=schedule["mode"]),
        "intent": schedule.get("intent") or {},
    }
    payload = {
        "projectId": loop["projectId"],
        "loopId": loop["id"],
        "productOwnerOutputId": output["id"],
        "backlog": backlog,
        "userStories": coordinator._stories_from_backlog(backlog),
        "acceptanceCriteria": coordinator._acceptance_criteria_from_backlog(backlog),
        "productBrief": output.get("productBriefPatch") or output.get("productBrief") or {},
        "projectAssessment": {**(durable.get("assessment") or {}), "changedFiles": []},
        "intentClassification": schedule.get("intent") or {},
        "risk": schedule.get("risk") or (schedule.get("intent") or {}).get("risk"),
        "availableTeam": preliminary["roles"],
        "teamSchedule": preliminary,
    }
    planned = TechnicalLeadPlanner().plan(payload)
    specs = planned["agent_tasks"]
    by_id = {(item.get("metadata") or {}).get("technicalLeadTaskId"): item for item in tasks}
    if len(by_id) != len(tasks) or None in by_id or {item["id"] for item in specs} != set(by_id):
        raise ValueError("The legacy plan cannot reproduce the exact persisted task roles and identities.")
    planner_fields = (
        "goal",
        "scope",
        "filesLikely",
        "acceptanceRefs",
        "outputSchema",
        "requiredTools",
        "runtimePreference",
        "reviewerRole",
        "qualityGates",
        "risk",
    )
    for spec in specs:
        task = by_id[spec["id"]]
        stored = coordinator.backlog.get_agent_task(task["id"])
        expected = {
            "projectId": loop["projectId"],
            "storyId": spec["storyId"],
            "title": spec.get("title") or "Implement user story",
            "description": spec.get("description") or "",
            "role": spec["role"],
            "category": spec.get("category") or "implementation",
            "status": spec.get("status") or "todo",
            "priority": spec.get("priority") or "medium",
            "estimateHours": spec.get("estimateHours"),
            "metadata": {
                **(spec.get("metadata") or {}),
                **{key: spec[key] for key in planner_fields if key in spec},
                "source": "technical_lead",
                "loopId": loop["id"],
                "productOwnerOutputId": output["id"],
                "technicalLeadTaskId": spec["id"],
            },
        }
        if any(stored.get(key) != value or task.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "The legacy plan cannot reproduce a persisted task's complete technical metadata."
            )

    def dependency_key(item):
        return (item["taskId"], item["dependsOnTaskId"], item["type"], item["reason"])

    expected_dependencies = sorted(
        (
            by_id[item["taskId"]]["id"],
            by_id[item["dependsOnTaskId"]]["id"],
            item.get("type") or "blocks",
            item.get("reason") or "TechnicalLeadPlanner task ordering.",
        )
        for item in planned["task_dependencies"]
    )
    if sorted(dependency_key(item) for item in dependencies) != expected_dependencies or any(
        item["metadata"]
        != {"source": "technical_lead", "loopId": loop["id"], "productOwnerOutputId": output["id"]}
        for item in dependencies
    ):
        raise ValueError("The legacy plan cannot reproduce the persisted dependencies.")
    return {
        **seal_technical_plan(coordinator, loop, tasks=tasks, raw_plan=planned),
        "origin": "reconstructed",
        "sourceArtifactId": artifact["id"],
        "sourceArtifactHash": artifact["hash"],
        "inputHash": fingerprint(payload),
    }


def _transaction(connection):
    return nullcontext() if connection.in_transaction else immediate_transaction(connection)


def _effective_metadata(coordinator, loop):
    from local_control_center.remediations.service import BlockerRemediationService

    durable = loop["context"]["durableRun"]
    if coordinator.connection.execute(
        "SELECT 1 FROM product_loops WHERE project_id=? AND rowid>(SELECT rowid FROM product_loops WHERE id=?) "
        "AND json_extract(context,'$.durableRun.thread.projectThreadId')=? LIMIT 1",
        (loop["projectId"], loop["id"], durable["thread"]["projectThreadId"]),
    ).fetchone():
        raise ValueError("A newer Product Loop superseded this runtime review.")
    source = ThreadsRepository(coordinator.connection).get_message(durable["thread"]["messageId"])
    if source["threadId"] != durable["thread"]["projectThreadId"]:
        raise ValueError("The source message no longer belongs to the reviewed thread.")
    metadata = BlockerRemediationService(coordinator.connection)._reseal_operator_cost_decision(
        durable.get("requestMeta") or {},
        project_id=loop["projectId"],
        source_message=source,
    )
    for key in ("jobId", "executionId"):
        metadata.pop(key, None)
    return metadata


def _checkpoint(coordinator, loop):
    """Compute scope from DB only; caller payloads never supply approval authority."""
    connection = coordinator.connection
    durable = loop["context"]["durableRun"]
    research = durable.get("researchResolution") or {}
    snapshot = resolution_snapshot(connection, loop, require_research=bool(research))
    if research:
        if (
            research.get("status") != "consumed"
            or research.get("loopId") != loop["id"]
            or snapshot != research.get("snapshot")
        ):
            raise ValueError("ProductOwner or research decisions changed; this proposal cannot be approved.")
        verified = validate_research(
            connection, root=coordinator.root, snapshot=snapshot, research_run_id=research["researchRunId"]
        )
        if any(research.get(key) != value for key, value in verified.items()):
            raise ValueError("The adopted research evidence changed.")
    elif (durable.get("research") or {}).get("researchStatus") not in {None, "not_required", "skipped"}:
        raise ValueError("The required research has no verified adoption receipt.")
    if (durable.get("productOwner") or {}).get("status") != "backlog_ready":
        raise ValueError("Runtime risk continuation requires a persisted backlog_ready checkpoint.")
    tasks = durable.get("agentTasks") or []
    planner_fields = {
        "goal",
        "scope",
        "filesLikely",
        "acceptanceRefs",
        "outputSchema",
        "requiredTools",
        "runtimePreference",
        "reviewerRole",
        "qualityGates",
        "risk",
    }
    if not tasks:
        raise ValueError("The persisted planning tasks changed or are missing.")
    for item in tasks:
        stored = coordinator.backlog.get_agent_task(item["id"])
        extras = set(item) - set(stored)
        if (
            any(item.get(key) != value for key, value in stored.items())
            or not extras <= planner_fields
            or any(key not in stored["metadata"] or item[key] != stored["metadata"][key] for key in extras)
        ):
            raise ValueError("The persisted planning tasks changed or are missing.")
    schedule = durable.get("teamSchedule") or {}
    roles = schedule.get("roles") or []
    if not 1 <= len(roles) <= 64 or len({item["role"] for item in roles}) != len(roles):
        raise ValueError("The reviewed role schedule is incomplete or ambiguous.")
    metadata = _effective_metadata(coordinator, loop)
    config = resolve_config(connection, loop["projectId"])
    if not config.selects_runtime:
        raise ValueError("Jev runtime selection is no longer enabled.")
    scopes, proposals, expiries = [], [], []
    for role in roles:
        public = role.get("resourceDecision") or {}
        row = connection.execute(
            "SELECT id,project_id,workflow_run_id,task_id,agent_id,task_type,policy_result_json,candidates_json FROM ai_routing_decisions WHERE id=?",
            (public.get("routingDecisionId"),),
        ).fetchone()
        if row is None:
            raise ValueError("A role has no durable routing decision.")
        policy = json_loads(row["policy_result_json"])
        engine = policy.get("decisionEngine") or {}
        request = coordinator._team_resource_request(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            request_meta=metadata,
            team_schedule=schedule,
            role_plan=role,
            agent_tasks=tasks,
        )
        profile = effective_resource_profile(connection, request.agent_profile_id, request.project_id)
        request = apply_profile_limits(request, profile)
        role_policy = connection.execute(
            "SELECT updated_at FROM role_model_policies WHERE id=?", (request.role_policy_id,)
        ).fetchone()
        if (
            row["project_id"] != loop["projectId"]
            or row["workflow_run_id"] != loop["id"]
            or row["task_id"] != request.task_id
            or row["agent_id"] != request.agent_id
            or not request.agent_profile_id
            or row["task_type"] != request.task_type
            or engine.get("agentProfileFingerprint") != profile_fingerprint(profile)
            or role_policy is None
            or engine.get("rolePolicyRevision") != role_policy["updated_at"]
        ):
            raise ValueError("The routing role, profile, task or policy no longer matches its receipt.")
        receipt = DecisionRepository(connection).get(str(engine.get("decisionId") or ""))
        review_required = receipt.get("reasonCode") == "risk_requires_review"
        if review_required:
            risk_review_evidence(receipt, config)
        elif (
            receipt.get("reasonCode") != "recommendation_usable"
            or receipt.get("effectiveDecision") != receipt.get("recommendation")
            or receipt.get("configurationFingerprint") != config.configuration_fingerprint
        ):
            raise ValueError("A role has an unresolved gate other than risk review.")
        if (
            receipt.get("projectId") != loop["projectId"]
            or receipt.get("sourceDecisionId") != row["id"]
            or receipt.get("executionId") != loop["id"]
        ):
            raise ValueError("The Jev receipt belongs to another routing scope.")
        candidates = json_loads(row["candidates_json"], [])
        candidate = next(
            (item for item in candidates if _identity(item) == receipt.get("recommendation")), None
        )
        if candidate is None:
            raise ValueError("Jev proposed a candidate outside AIDO's persisted allowlist.")
        expiry = datetime.fromisoformat(receipt["timestamp"].replace("Z", "+00:00")) + RISK_REVIEW_TTL
        if expiry <= datetime.now(UTC):
            raise ValueError(
                "The runtime risk proposal expired; request an admitted proposal refresh from this planning checkpoint."
            )
        expiries.append(expiry)
        proposal = {
            "role": role["role"],
            "taskId": request.task_id,
            "agentProfileId": request.agent_profile_id,
            "decisionId": receipt["decisionId"],
            "providerId": candidate["providerId"],
            "model": candidate["model"],
            "runtime": candidate["runtime"],
            "risk": receipt["effectiveRisk"],
        }
        if review_required:
            proposals.append(proposal)
        model = connection.execute(
            "SELECT * FROM model_catalog WHERE provider_id=? AND model=?",
            (candidate["providerId"], candidate["model"]),
        ).fetchone()
        scopes.append(
            {
                **proposal,
                "reviewRequired": review_required,
                "requestHash": fingerprint(asdict(request)),
                "routingDecisionId": row["id"],
                "routingHash": fingerprint(dict(row)),
                "receiptHash": fingerprint(receipt),
                "rolePolicyHash": fingerprint(coordinator._resource_role_policy(role["role"])),
                "profileFingerprint": profile_fingerprint(profile),
                "configurationFingerprint": provider_configuration_fingerprint(
                    connection, candidate["providerId"]
                ),
                "modelHash": fingerprint(dict(model) if model else None),
            }
        )
    if not proposals:
        raise ValueError("No runtime proposal requires risk review.")
    return {
        "productOwner": snapshot,
        "researchHash": fingerprint(research),
        "tasksHash": fingerprint(tasks),
        "scheduleHash": fingerprint(schedule),
        "effectiveRequestHash": fingerprint(metadata),
        "backlogHash": fingerprint(durable.get("backlog")),
        "constitutionHash": fingerprint(
            ProjectConstitutionRepository(connection).get_for_project(loop["projectId"])
        ),
        "technicalLeadPlanHash": fingerprint(_resolve_technical_plan(coordinator, loop)),
        "scopes": scopes,
        "proposals": proposals,
        "expiresAt": min(expiries).isoformat().replace("+00:00", "Z"),
    }


def _pending_gate(loop):
    durable = loop["context"]["durableRun"]
    if (
        loop["state"] != "blocked"
        or durable.get("blockedStage") != "resource_manager"
        or durable.get("runActive")
    ):
        raise ValueError("This planning checkpoint is no longer blocked for runtime review.")


def ensure_runtime_risk_review(connection, *, loop_id: str, root: str | Path) -> dict:
    """Materialize one exact approval from durable receipts, including a legacy blocked run."""
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

    coordinator = ProductLoopCoordinator(connection, root=root)
    with _transaction(connection):
        loop = coordinator.get(loop_id)
        _pending_gate(loop)
        durable = loop["context"]["durableRun"]
        existing = durable.get("runtimeRiskReview")
        if isinstance(existing, dict):
            action = JobsRepository(connection).get_action_request(str(existing.get("actionRequestId") or ""))
            if existing.get("status") != "pending" or action["status"] != "pending":
                raise ValueError("The runtime risk review was already decided; it cannot be approved again.")
            _checked_review(coordinator, action, status="pending")
            return existing
        if "technicalLeadPlan" not in durable:
            durable["technicalLeadPlan"] = _resolve_technical_plan(coordinator, loop)
            loop = coordinator.repository.update_loop_context(loop_id, context=loop["context"])
            durable = loop["context"]["durableRun"]
        checkpoint = _checkpoint(coordinator, loop)
        thread_id = checkpoint["productOwner"]["threadId"]
        review = {
            "id": f"runtime-risk-review-{uuid.uuid4()}",
            "status": "pending",
            "loopId": loop_id,
            "loopVersion": loop["version"],
            "threadId": thread_id,
            "snapshot": checkpoint,
            "proposals": checkpoint["proposals"],
            "expiresAt": checkpoint["expiresAt"],
            "root": str(coordinator.root),
        }
        jobs = JobsRepository(connection)
        job = jobs.create_job(
            project_id=loop["projectId"],
            kind="product_loop_runtime_risk_approval",
            status="approval_required",
            payload={"loopId": loop_id, "threadId": thread_id, "reviewId": review["id"]},
        )["job"]
        action = jobs.create_action_request(
            job_id=job["id"],
            project_id=loop["projectId"],
            action_type=ACTION_TYPE,
            risk_level=max(
                (item["risk"] for item in checkpoint["proposals"]),
                key=lambda value: {"low": 0, "medium": 1, "high": 2, "critical": 3}[value],
            ),
            reason="Explicit review of the exact Jev runtime proposals is required; no cost or OS permission is granted.",
            payload={
                "loopId": loop_id,
                "threadId": thread_id,
                "reviewId": review["id"],
                "proposals": checkpoint["proposals"],
                "snapshotHash": fingerprint(checkpoint),
            },
            expires_at=checkpoint["expiresAt"],
            command_argv=[],
        )
        review.update(jobId=job["id"], actionRequestId=action["id"])
        durable["runtimeRiskReview"] = review
        coordinator.repository.update_loop_context(loop_id, context=loop["context"])
        cards = RemediationActionsRepository(connection)
        card = cards.create_action(
            project_id=loop["projectId"],
            thread_id=thread_id,
            loop_id=loop_id,
            stage="resource_manager",
            blocker_type="runtime_risk_review_required",
            action_type="approve_runtime_risk",
            title="Review proposed runtime risk",
            description="Review every proposed role and runtime, then provide an explicit reason. This does not approve costs or OS access.",
            primary=True,
            payload={key: review[key] for key in ("proposals", "actionRequestId", "jobId", "expiresAt")},
        )
        connection.execute(
            "UPDATE remediation_actions SET status='dismissed',resolved_at=? WHERE loop_id=? AND stage='resource_manager' AND status='pending' AND id<>?",
            (utc_now(), loop_id, card["id"]),
        )
        return review


def _checked_review(coordinator, action, *, status: str):
    payload = action.get("payload") or {}
    loop = coordinator.get(str(payload.get("loopId") or ""))
    review = (loop["context"].get("durableRun") or {}).get("runtimeRiskReview") or {}
    if status == "pending":
        job = coordinator.jobs.get_job(action["jobId"])
        if (
            job["kind"] != "product_loop_runtime_risk_approval"
            or job["status"] != "approval_required"
            or coordinator.connection.execute(
                "SELECT 1 FROM process_execution_controls WHERE execution_id=?", (job["id"],)
            ).fetchone()
        ):
            raise ValueError("The runtime risk approval job was cancelled or is no longer pending.")
    if (
        review.get("status") != status
        or review.get("id") != payload.get("reviewId")
        or review.get("actionRequestId") != action["id"]
        or review.get("jobId") != action["jobId"]
        or loop["projectId"] != action["projectId"]
        or review.get("threadId") != payload.get("threadId")
        or payload.get("snapshotHash") != fingerprint(review.get("snapshot"))
        or payload.get("proposals") != review.get("proposals")
        or action.get("expiresAt") != review.get("expiresAt")
        or datetime.fromisoformat(review["expiresAt"].replace("Z", "+00:00")) <= datetime.now(UTC)
    ):
        raise ValueError("The runtime risk approval is stale, expired, consumed or belongs to another scope.")
    if _checkpoint(coordinator, loop) != review["snapshot"]:
        raise ValueError("The reviewed policy, profile, candidate, plan or evidence changed.")
    return loop, review


def approve_runtime_risk(jobs, *, job_id: str, action_id: str, reason: str) -> dict:
    """Accept only a stored risk action, atomically queue its same-loop continuation, and never grant permissions."""
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

    connection = jobs.connection
    reason = str(redact_secrets(reason)).strip()
    if not reason:
        raise ValueError("An explicit review reason is required.")
    with _transaction(connection):
        action = jobs.get_action_request(action_id)
        approval_job = jobs.get_job(job_id)
        if (
            action["actionType"] != ACTION_TYPE
            or action["jobId"] != job_id
            or action["status"] != "pending"
            or approval_job["kind"] != "product_loop_runtime_risk_approval"
            or approval_job["status"] != "approval_required"
            or connection.execute(
                "SELECT 1 FROM process_execution_controls WHERE execution_id=?", (job_id,)
            ).fetchone()
        ):
            raise ValueError("This runtime risk action is not pending for this job.")
        loop = ProductLoopRepository(connection).get_loop(str(action["payload"].get("loopId") or ""))
        review = loop["context"]["durableRun"].get("runtimeRiskReview") or {}
        coordinator = ProductLoopCoordinator(connection, root=review.get("root"))
        loop, review = _checked_review(coordinator, action, status="pending")
        _pending_gate(loop)
        if review["loopVersion"] != loop["version"]:
            raise ValueError("The loop revision changed before consent.")
        if connection.execute(
            "SELECT 1 FROM jobs WHERE project_id=? AND kind='thread.product_loop.run' AND status IN ('queued','running','resource_wait') AND json_extract(payload,'$.threadId')=? LIMIT 1",
            (loop["projectId"], review["threadId"]),
        ).fetchone():
            raise ValueError("Another admitted continuation already owns this thread.")
        for proposal in review["proposals"]:
            rejection = model_validation_rejection(connection, proposal["providerId"], proposal["model"])
            if rejection:
                raise ValueError(f"The proposed model needs current validation: {rejection}.")
        changed = connection.execute(
            "UPDATE action_requests SET status='approved',reason=?,decided_at=?,decided_by='operator' WHERE id=? AND status='pending' AND payload=? AND expires_at=? AND EXISTS(SELECT 1 FROM product_loops WHERE id=? AND version=? AND context=?)",
            (
                reason,
                utc_now(),
                action_id,
                json_dumps(action["payload"]),
                review["expiresAt"],
                loop["id"],
                loop["version"],
                json_dumps(loop["context"]),
            ),
        )
        if changed.rowcount != 1:
            raise ValueError("The runtime risk action changed concurrently.")
        marker = {"loopId": loop["id"], "reviewId": review["id"]}
        durable = loop["context"]["durableRun"]
        job = jobs.create_job(
            project_id=loop["projectId"],
            kind="thread.product_loop.run",
            payload={
                "projectId": loop["projectId"],
                "threadId": review["threadId"],
                "messageId": durable["thread"]["messageId"],
                "message": durable["message"],
                "root": review["root"],
                "title": loop["title"],
                "runMetadata": {"runtimeRiskContinuation": marker},
            },
            idempotency_key=f"runtime-risk-review:{review['id']}",
        )["job"]
        review.update(
            status="queued", continuationJobId=job["id"], approvedAt=utc_now(), approvedBy="operator"
        )
        durable["runtimeRiskReview"] = review
        loop = coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
        jobs.update_job_status(job_id, status="completed")
        audit = jobs.record_audit(
            project_id=loop["projectId"],
            action="runtime_risk.approve",
            actor="operator",
            target=action_id,
            payload={
                "reason": reason.strip(),
                "reviewId": review["id"],
                "continuationJobId": job["id"],
                "effect": "risk_only_no_permissions_or_cost_consent",
            },
        )
        connection.execute(
            "UPDATE remediation_actions SET status='resolved',resolved_at=? WHERE loop_id=? AND action_type='approve_runtime_risk' AND status='pending'",
            (utc_now(), loop["id"]),
        )
        ThreadsRepository(connection).set_status(review["threadId"], "queued")
        ThreadsRepository(connection).record_event(
            thread_id=review["threadId"],
            type="runtime_risk_approved",
            agent_role="aido_lead",
            payload={"status": "queued", "loopId": loop["id"], "jobId": job["id"], "reviewId": review["id"]},
        )
        return {
            "job": jobs.get_job(job_id),
            "actionRequest": jobs.get_action_request(action_id),
            "auditEvent": audit,
            "execution": {"status": "queued", "action": "approve_runtime_risk", "job": job, "loop": loop},
        }


def deny_runtime_risk(jobs, *, job_id: str, action_id: str, reason: str):
    """Deny this review without restarting or cancelling its historical source run."""
    with _transaction(jobs.connection):
        action, job = jobs.get_action_request(action_id), jobs.get_job(job_id)
        if (
            action["actionType"] != ACTION_TYPE
            or action["jobId"] != job_id
            or job["kind"] != "product_loop_runtime_risk_approval"
        ):
            raise ValueError("This job does not own the runtime risk action.")
        result = jobs.deny_action(job_id, action_id, reason=reason)
        loop_id = action.get("payload", {}).get("loopId")
        if loop_id:
            loop = ProductLoopRepository(jobs.connection).get_loop(loop_id)
            review = loop["context"]["durableRun"].get("runtimeRiskReview") or {}
            if review.get("actionRequestId") == action_id:
                review["status"] = "denied"
                ProductLoopRepository(jobs.connection).update_loop_context(loop_id, context=loop["context"])
                jobs.connection.execute(
                    "UPDATE remediation_actions SET status='dismissed',resolved_at=? WHERE loop_id=? AND action_type='approve_runtime_risk' AND status='pending'",
                    (utc_now(), loop_id),
                )
        return result


def approved_runtime_candidate(connection, *, request, review_id, config, revalidate):
    """Consume one exact role's proposal only inside its trusted continuation."""
    context = CURRENT_EXECUTION.get()
    loop = ProductLoopRepository(connection).get_loop(str(request.workflow_run_id or ""))
    review = loop["context"]["durableRun"].get("runtimeRiskReview") or {}
    if (
        context is None
        or context.connection is not connection
        or not context.in_job_runner
        or context.project_id != request.project_id
        or context.execution_id != review.get("continuationJobId")
        or review.get("status") != "consuming"
        or review.get("id") != review_id
        or connection.execute(
            "SELECT 1 FROM process_execution_controls WHERE execution_id=?",
            (context.execution_id if context else None,),
        ).fetchone()
    ):
        raise ValueError("Runtime risk consent requires its admitted, exact continuation.")
    action = JobsRepository(connection).get_action_request(review["actionRequestId"])
    if (
        action["status"] != "approved"
        or action["actionType"] != ACTION_TYPE
        or action["expiresAt"] <= utc_now()
    ):
        raise ValueError("Runtime risk consent is no longer valid.")
    role = request.task_type.split(".", 1)[0]
    matches = [
        item
        for item in review["snapshot"]["scopes"]
        if item["role"] == role
        and item["taskId"] == request.task_id
        and item["agentProfileId"] == request.agent_profile_id
        and item["requestHash"] == fingerprint(asdict(request))
    ]
    if len(matches) != 1:
        raise ValueError("This role/profile/task was not covered by the exact risk consent.")
    scope = matches[0]
    receipt = DecisionRepository(connection).get(scope["decisionId"])
    if fingerprint(receipt) != scope["receiptHash"]:
        raise ValueError("The reviewed decision receipt changed.")
    evidence = (
        risk_review_evidence(receipt, config)
        if scope["reviewRequired"]
        else {
            "mode": "runtime_selection",
            "decisionId": receipt["decisionId"],
            "effectiveRisk": receipt["effectiveRisk"],
        }
    )
    identity = _identity(scope)
    rejection = revalidate(identity)
    if rejection:
        raise ValueError(f"The reviewed candidate is no longer eligible: {rejection}.")
    return identity, {
        **evidence,
        "reviewRequired": False,
        "reasonCode": "risk_review_approved",
        "riskReviewId": review_id,
        "riskReviewActionRequestId": action["id"],
    }


def _active_continuation_job(coordinator, run):
    metadata = run.run_metadata or {}
    marker = metadata.get("runtimeRiskContinuation")
    context = CURRENT_EXECUTION.get()
    if (
        not isinstance(marker, dict)
        or context is None
        or context.connection is not coordinator.connection
        or not context.in_job_runner
        or context.project_id != run.project_id
        or context.execution_id != metadata.get("jobId")
    ):
        raise ValueError("Runtime risk continuation requires its admitted worker identity.")
    job = coordinator.jobs.get_job(context.execution_id)
    if (
        job["kind"] != "thread.product_loop.run"
        or job["status"] != "running"
        or job["projectId"] != run.project_id
        or job["payload"].get("threadId") != run.thread_id
        or job["payload"].get("runMetadata", {}).get("runtimeRiskContinuation") != marker
        or coordinator.connection.execute(
            "SELECT 1 FROM process_execution_controls WHERE execution_id=?", (job["id"],)
        ).fetchone()
    ):
        raise ValueError("The worker job no longer authorizes this risk continuation or was cancelled.")
    return marker, job


def _failed_retry_event(coordinator, loop, review, job):
    """Only a durable, explicit retry after failure may recover an unconsumed checkpoint."""
    events = coordinator.connection.execute(
        "SELECT id,type,created_at FROM events WHERE job_id=? "
        "AND type IN ('job.failed','job.retried','job.cancelled','job.completed') ORDER BY rowid DESC LIMIT 2",
        (job["id"],),
    ).fetchall()
    if (
        loop["state"] not in {"workspace_check", "git_check", "discovery"}
        or review.get("consumedAt")
        or len(events) != 2
        or events[0]["type"] != "job.retried"
        or events[1]["type"] != "job.failed"
        or events[0]["id"] == review.get("recoveryRetryEventId")
        or events[1]["created_at"] < review["approvedAt"]
        or coordinator.connection.execute(
            "SELECT 1 FROM events WHERE job_id=? AND type='job.cancelled' AND created_at>=? LIMIT 1",
            (job["id"], review["approvedAt"]),
        ).fetchone()
    ):
        raise ValueError(
            "This unconsumed runtime review requires an explicit retry of its failed job before planning."
        )
    return events[0]["id"]


def load_runtime_risk_continuation(coordinator, run):
    """Load the queued marker, or one explicit failed-job retry, before rechecking Git."""
    with _transaction(coordinator.connection):
        marker, job = _active_continuation_job(coordinator, run)
        loop = coordinator.get(str(marker.get("loopId") or ""))
        review = loop["context"]["durableRun"].get("runtimeRiskReview") or {}
        action = coordinator.jobs.get_action_request(str(review.get("actionRequestId") or ""))
        recovering = review.get("status") == "consuming"
        loop, review = _checked_review(coordinator, action, status="consuming" if recovering else "queued")
        if (
            action["status"] != "approved"
            or review["id"] != marker.get("reviewId")
            or review["continuationJobId"] != job["id"]
        ):
            raise ValueError("The runtime risk continuation is stale or already consumed.")
        if recovering:
            review["recoveryRetryEventId"] = _failed_retry_event(coordinator, loop, review, job)
            loop = coordinator.transition_in_transaction(
                loop["id"],
                to_state="blocked",
                reason="Recovering the same approved checkpoint after an explicit failed-job retry.",
                trigger="runtime_risk_recovery",
                actor=run.actor,
                expected_version=loop["version"],
                metadata={
                    "reviewId": review["id"],
                    "jobId": job["id"],
                    "retryEventId": review["recoveryRetryEventId"],
                },
            )
        else:
            _pending_gate(loop)
            if review["loopVersion"] != loop["version"]:
                raise ValueError("The runtime risk continuation loop revision changed.")
        durable = loop["context"]["durableRun"]
        review["status"] = "consuming"
        durable.update(runtimeRiskReview=review, runActive=True)
        loop = coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
        research = durable.get("researchResolution") or {}
        hydration = research or {
            "snapshot": review["snapshot"]["productOwner"],
            "evidencePackageId": durable["productOwner"].get("evidencePackageId"),
        }
        hydrate_research_run(coordinator, run, loop=loop, receipt=hydration)
        run.research_resolution = research
        run.runtime_risk_review = review


def resume_runtime_risk_planning(coordinator, run):
    """Revalidate the saved schedule and complete planning without new inference."""
    from local_control_center.product_loop.phases.team_planning import finish_team_planning

    # Runtime inventory may validate CLI authentication; it must never inherit a SQLite write lock.
    assert_external_boundary()
    with _transaction(coordinator.connection):
        marker, job = _active_continuation_job(coordinator, run)
        loop = coordinator.get(run.loop["id"])
        review = loop["context"]["durableRun"]["runtimeRiskReview"]
        action = coordinator.jobs.get_action_request(review["actionRequestId"])
        loop, review = _checked_review(coordinator, action, status="consuming")
        if (
            action["status"] != "approved"
            or marker != {"loopId": loop["id"], "reviewId": review["id"]}
            or review["continuationJobId"] != job["id"]
            or loop["state"] != "discovery"
        ):
            raise ValueError("The runtime risk planning checkpoint is no longer authorized.")
        expected_context, expected_version = json_dumps(loop["context"]), loop["version"]
        durable = loop["context"]["durableRun"]
        tasks, schedule = durable["agentTasks"], durable["teamSchedule"]
    selected_schedule, blockers = coordinator._team_schedule_with_resource_decisions(
        project_id=run.project_id,
        loop_id=loop["id"],
        request_meta=run.request_meta,
        team_schedule=schedule,
        agent_tasks=tasks,
        runtime_risk_review_id=review["id"],
    )
    with _transaction(coordinator.connection):
        # Keep the current context even on rejection, so the caller cannot overwrite an operator edit.
        run.loop = coordinator.get(loop["id"])
        _active_continuation_job(coordinator, run)
        action = coordinator.jobs.get_action_request(review["actionRequestId"])
        loop, review = _checked_review(coordinator, action, status="consuming")
        if action["status"] != "approved":
            raise ValueError("The runtime risk action was revoked during inventory revalidation.")
        durable = loop["context"]["durableRun"]
        review.update(status="consumed", consumedAt=utc_now())
        durable["runtimeRiskReview"] = review
        changed = coordinator.connection.execute(
            "UPDATE product_loops SET context=?,updated_at=? WHERE id=? AND version=? AND context=? AND state='discovery'",
            (json_dumps(loop["context"]), utc_now(), loop["id"], expected_version, expected_context),
        )
        if changed.rowcount != 1:
            raise ValueError("The runtime risk planning checkpoint changed during inventory revalidation.")
        run.loop = coordinator.get(loop["id"])
    if blockers:
        return coordinator._block_run(
            run.loop,
            stage="resource_manager",
            actor=run.actor,
            thread_id=run.thread_id,
            reason="Risk review was accepted; an independent resource or cost approval gate remains.",
            details={"resourceBlockers": blockers, "teamSchedule": selected_schedule},
            durable_context={"agentTasks": tasks, "teamSchedule": selected_schedule},
        )
    artifact = coordinator.evidence.get_artifact_by_id(durable["backlog"]["artifactId"])
    return finish_team_planning(
        coordinator,
        run,
        agent_tasks=tasks,
        team_schedule=selected_schedule,
        backlog_artifact=artifact,
        raw_plan=durable["technicalLeadPlan"]["plan"],
    )
