"""Risk consent is scoped, durable and independent of execution/cost permissions."""

from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.product_owner_agent import persist_product_owner_backlog
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.agents.team_bootstrap import bootstrap_base_team_if_needed
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.decision_engine.models import fingerprint
from local_control_center.jobs_approvals.commands import approve_action
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun
from local_control_center.product_loop.runtime_risk_review import (
    _durable_product_owner_selected_resource,
)
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now
from tests_py.test_jev_resource_selection import ControlledJev
from tests_py.test_research_resolution import _execute, _research
from tests_py.test_research_resolution import lane as lane


@pytest.fixture
def risk_lane(lane, monkeypatch):
    case = lane("backlog_ready", sealed_metadata={"planOnly": True})
    connection = case.connection
    backlog = BacklogRepository(connection)
    persist_product_owner_backlog(
        backlog,
        project_id=case.project["id"],
        output=case.output,
        product_owner_output_id=case.output["id"],
    )
    research = _research(case)
    queued = _execute(case, research["researchRun"]["id"])
    job = queued["job"]
    JobsRepository(connection).update_job_status(job["id"], status="running")
    coordinator = ProductLoopCoordinator(connection, root=case.root)
    run = _UserMessageRun(
        project_id=case.project["id"],
        message=case.message["content"],
        actor="operator",
        thread_id=case.thread["id"],
        run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
    )
    with execution_scope(
        ProcessExecutionContext(
            db_path=case.root / "platform.sqlite",
            connection=connection,
            execution_id=job["id"],
            project_id=case.project["id"],
            in_job_runner=True,
        )
    ):
        assert coordinator._load_research_continuation(run) is None
    JobsRepository(connection).update_job_status(job["id"], status="completed")
    coordinator._seal_constitution(run)
    settings = SettingsRepository(connection)
    for key, value in {
        "decision_engine.enabled": True,
        "decision_engine.mode": "runtime_selection",
        "decision_engine.jev.enabled": True,
        "decision_engine.max_risk": "medium",
        "runtime.remote.enabled": True,
        "project.runtime.remote.enabled": True,
        "project.routing.forceLocal": False,
    }.items():
        settings.set_value(key, "general", None, value)
    monkeypatch.setattr("local_control_center.decision_engine.service.real_jev_calls_enabled", lambda: True)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("AIDO_TEST_API_KEY", "offline-fixture")
    connection.execute("UPDATE model_catalog SET enabled=0")
    connection.execute("UPDATE ai_model_performance SET enabled=0")
    store = ProviderAccountStore(connection)
    statuses = []
    for provider in ("openai_compatible", "openrouter"):
        store.upsert_provider_account(
            {
                "providerId": provider,
                "displayName": provider,
                "providerType": "api",
                "providerFamily": provider,
                "apiFormat": "openai_compatible",
                "baseUrl": f"https://{provider.replace('_', '-')}.example.invalid/v1",
                "credentialRef": "env:AIDO_TEST_API_KEY",
                "enabled": True,
                "healthStatus": "healthy",
                "lastHealthCheckAt": utc_now(),
            }
        )
        store.upsert_model(
            {
                "providerId": provider,
                "model": "review-model",
                "contextWindow": 128000,
                "maxOutputTokens": 4096,
                "inputPricePerMtok": 1.0,
                "outputPricePerMtok": 1.0,
                "enabled": True,
                "capabilities": ["chat", "code", "review"],
            }
        )
        statuses.append(
            {
                "id": provider,
                "kind": "api",
                "providerFamily": provider,
                "configured": True,
                "available": True,
                "executable": True,
                "productOwnerExecutable": True,
                "healthStatus": "healthy",
                "healthCheckedAt": utc_now(),
                "models": ["review-model"],
                "capabilities": ["chat", "code", "review"],
            }
        )
        record_model_execution(
            connection, provider_id=provider, model="review-model", success=True, source="test_prompt"
        )
    monkeypatch.setattr(
        RuntimeStatusService, "list_provider_statuses", lambda _self, *, project_id=None: statuses
    )
    jev = ControlledJev(connection)
    monkeypatch.setattr(
        "local_control_center.decision_engine.service.JevDecisionProvider", lambda _config: jev
    )
    bootstrap_base_team_if_needed(connection)
    profiles = coordinator._profile_by_role(case.project["id"])
    roles = list(profiles)[:12]
    assert len(roles) == 12
    for role in roles:
        RoutingProfileStore(connection).upsert_role_policy(
            {
                "role": role,
                "allowApi": True,
                "allowRemote": True,
                "allowLocal": True,
                "allowCli": True,
                "allowUnknownCost": True,
                "requireApprovalForUnknownCost": False,
                "maxCostPerTaskUsd": 10.0,
            }
        )
    story = backlog.list_user_stories(case.project["id"])[0]
    task = backlog.create_agent_task(
        {
            "projectId": case.project["id"],
            "storyId": story["id"],
            "title": "Inspect compatibility",
            "role": roles[0],
            "metadata": {"productOwnerOutputId": case.output["id"]},
        }
    )
    schedule = {
        "risk": "high",
        "mode": "balanced",
        "schedulerVersion": "fixture",
        "summary": {},
        "roles": [
            {
                "role": role,
                "kind": "reason",
                "budgetUsd": 1.0,
                "maxTokens": 256,
                "requiredInputArtifacts": [],
                "expectedOutputArtifacts": [],
                "reviewerPolicy": {},
                "outputArtifactSchema": {"type": "object"},
                "reviewer": None,
                "providerPreference": [],
                "runtimePreference": [],
                "qualityGates": [],
            }
            for role in roles
        ],
    }
    schedule, blockers = coordinator._team_schedule_with_resource_decisions(
        project_id=case.project["id"],
        loop_id=case.loop["id"],
        request_meta=run.request_meta,
        team_schedule=schedule,
        agent_tasks=[task],
    )
    assert len(blockers) == 12
    assert all(
        item["decision"]["policyResult"]["decisionEngine"]["reasonCode"] == "risk_requires_review"
        for item in blockers
    )
    artifact = coordinator._write_json_artifact(
        root=case.root,
        project_id=case.project["id"],
        name="backlog.json",
        kind="product_backlog",
        payload={"productOwnerOutputId": case.output["id"]},
    )
    loop = coordinator.repository.get_loop(case.loop["id"])
    durable = loop["context"]["durableRun"]
    durable.update(
        {
            "runActive": False,
            "blockedStage": "resource_manager",
            "blockedReason": "Risk review required.",
            "agentTasks": [task],
            "teamSchedule": schedule,
            "backlog": {"artifactId": artifact["id"]},
            "resource_manager": {"resourceBlockers": blockers, "teamSchedule": schedule},
            "planOnly": True,
        }
    )
    raw_plan = {
        "quality_gates": [{"id": "compatibility-proof"}],
        "assignment_handoffs": [{"fromRole": "technical_lead", "toRole": "qa_engineer"}],
        "branch_worktree_plan": {"isolationType": "git_worktree"},
    }
    durable["technicalLeadPlan"] = {
        "origin": "original",
        "plan": raw_plan,
        "planHash": fingerprint(raw_plan),
        "tasksHash": fingerprint([task]),
        "dependenciesHash": fingerprint([]),
    }
    case.loop = coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    case.coordinator, case.jev, case.settings, case.store = coordinator, jev, settings, store
    case.schedule, case.task, case.research = schedule, task, research
    return case


def _review(case):
    from local_control_center.product_loop.runtime_risk_review import ensure_runtime_risk_review

    return ensure_runtime_risk_review(case.connection, loop_id=case.loop["id"], root=case.root)


def _approve(case, review):
    return approve_action(
        JobsRepository(case.connection),
        review["jobId"],
        review["actionRequestId"],
        {"reason": "I accept this exact risk-reviewed runtime plan."},
    )


def test_runtime_risk_review_materializes_twelve_role_scopes_without_selecting_or_inference(risk_lane):
    case = risk_lane
    calls = len(case.jev.requests)
    review = _review(case)
    assert len(review["proposals"]) == 12
    assert (
        len({(p["role"], p["agentProfileId"], p["taskId"], p["decisionId"]) for p in review["proposals"]})
        == 12
    )
    assert len({p["taskId"] for p in review["proposals"]}) == 1
    assert all(p["risk"] == "high" for p in review["proposals"])
    assert len(case.jev.requests) == calls
    assert _review(case)["actionRequestId"] == review["actionRequestId"]
    assert (
        case.connection.execute(
            "SELECT COUNT(*) FROM ai_routing_decisions WHERE selected_provider IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    actions = case.service.list_for_thread(thread_id=case.thread["id"])
    card = next(item for item in actions if item["actionType"] == "approve_runtime_risk")
    assert card["blockerType"] == "runtime_risk_review_required"
    assert not any(item["status"] == "pending" and item["actionType"] == "retry_loop" for item in actions)


@pytest.mark.parametrize(
    "change",
    [
        "expiry",
        "schedule",
        "task",
        "policy",
        "profile",
        "configuration",
        "source",
        "loop_version",
        "low_confidence",
        "low_margin",
    ],
)
def test_runtime_risk_review_rejects_stale_or_insufficient_consent(risk_lane, change):
    from fastapi import HTTPException

    case = risk_lane
    review = _review(case)
    first = review["proposals"][0]
    if change == "expiry":
        case.connection.execute(
            "UPDATE action_requests SET expires_at='2000-01-01T00:00:00Z' WHERE id=?",
            (review["actionRequestId"],),
        )
    elif change == "schedule":
        loop = case.coordinator.get(case.loop["id"])
        loop["context"]["durableRun"]["teamSchedule"]["roles"][0]["maxTokens"] += 1
        case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    elif change == "task":
        case.connection.execute("UPDATE agent_tasks SET description='Changed' WHERE id=?", (case.task["id"],))
    elif change == "policy":
        policy = case.coordinator._resource_role_policy(first["role"])
        case.connection.execute(
            "UPDATE role_model_policies SET allow_remote=0 WHERE id=?", (policy["rolePolicyId"],)
        )
    elif change == "profile":
        case.connection.execute(
            "UPDATE agent_profiles SET allow_remote=0 WHERE id=?", (first["agentProfileId"],)
        )
    elif change == "configuration":
        case.settings.set_value("decision_engine.max_risk", "general", None, "high")
    elif change == "source":
        from local_control_center.evidence.repository import EvidenceRepository

        source = EvidenceRepository(case.connection).get_artifact_by_id(
            case.research["sources"][0]["artifactId"]
        )
        Path(source["path"]).write_text("changed", encoding="utf-8")
    elif change == "loop_version":
        case.connection.execute("UPDATE product_loops SET version=version+1 WHERE id=?", (case.loop["id"],))
    else:
        key = "confidence" if change == "low_confidence" else "margin"
        case.connection.execute(
            f"UPDATE decision_receipts SET payload=json_set(payload,'$.{key}',0.01) WHERE id=?",
            (first["decisionId"],),
        )
    with pytest.raises((HTTPException, ValueError)):
        _approve(case, review)
    assert (
        JobsRepository(case.connection).get_action_request(review["actionRequestId"])["status"] == "pending"
    )
    assert case.connection.execute("SELECT COUNT(*) FROM permission_grants").fetchone()[0] == 0


def test_runtime_risk_approval_queues_once_without_grants_cost_consent_or_new_loop(risk_lane):
    from fastapi import HTTPException

    case = risk_lane
    review = _review(case)
    result = _approve(case, review)
    assert result["execution"]["status"] == "queued"
    job = result["execution"]["job"]
    assert job["kind"] == "thread.product_loop.run"
    assert job["payload"]["runMetadata"]["runtimeRiskContinuation"]["loopId"] == case.loop["id"]
    assert case.connection.execute("SELECT COUNT(*) FROM product_loops").fetchone()[0] == 1
    assert case.connection.execute("SELECT COUNT(*) FROM permission_grants").fetchone()[0] == 0
    assert "approvedResourceSelections" not in json_dumps(case.coordinator.get(case.loop["id"])["context"])
    with pytest.raises((HTTPException, ValueError)):
        _approve(case, review)


def test_malformed_runtime_risk_action_never_falls_through_to_permission_grant(risk_lane):
    from fastapi import HTTPException

    case = risk_lane
    jobs = JobsRepository(case.connection)
    job = jobs.create_job(
        project_id=case.project["id"], kind="product_loop_runtime_risk_approval", status="approval_required"
    )["job"]
    action = jobs.create_action_request(
        job_id=job["id"],
        project_id=case.project["id"],
        action_type="product_loop.approve_runtime_risk",
        risk_level="high",
        reason="Review",
        payload={},
    )
    with pytest.raises(HTTPException):
        approve_action(jobs, job["id"], action["id"], {"reason": "Explicit but invalid request"})
    assert case.connection.execute("SELECT COUNT(*) FROM permission_grants").fetchone()[0] == 0


@pytest.mark.parametrize(
    "after_queue", [None, "git_dirty", "manual_disabled", "unknown_cost", "no_research", "inventory_boundary"]
)
def test_runtime_risk_continuation_reuses_planning_and_revalidates_execution_gates(
    risk_lane, monkeypatch, after_queue
):
    case = risk_lane
    if after_queue == "no_research":
        po_evidence = case.coordinator.evidence.create_evidence_package(
            project_id=case.project["id"],
            workflow_run_id=case.loop["id"],
            workspace_id=case.workspace["id"],
            agent_id="product_owner_agent",
        )
        case.connection.execute(
            "UPDATE product_owner_outputs SET decisions='[]' WHERE id=?", (case.output["id"],)
        )
        loop = case.coordinator.get(case.loop["id"])
        durable = loop["context"]["durableRun"]
        durable["productOwner"]["evidencePackageId"] = po_evidence["id"]
        durable.pop("researchResolution")
        durable["research"] = {"researchStatus": "not_required", "decisions": []}
        case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    review = _review(case)
    job = _approve(case, review)["execution"]["job"]
    JobsRepository(case.connection).update_job_status(job["id"], status="running")
    if after_queue == "inventory_boundary":
        from local_control_center.process_supervision.context import assert_external_boundary

        inventory = RuntimeStatusService.list_provider_statuses

        def checked_inventory(service, *, project_id=None):
            assert_external_boundary()
            return inventory(service, project_id=project_id)

        monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", checked_inventory)
    if after_queue == "manual_disabled":
        case.connection.execute("UPDATE model_catalog SET enabled=0")
    if after_queue == "unknown_cost":
        case.connection.execute(
            "UPDATE model_catalog SET input_price_per_mtok=NULL,output_price_per_mtok=NULL"
        )
        case.connection.execute("UPDATE role_model_policies SET require_approval_for_unknown_cost=1")

    def forbidden(*args, **kwargs):
        pytest.fail(
            "Approved continuation must not repeat planning, PO, assessment, research or Jev inference"
        )

    monkeypatch.setattr(case.jev, "decide", forbidden)
    monkeypatch.setattr(ProductLoopCoordinator, "_generate_agent_tasks", forbidden)
    git_calls = []

    class Git:
        def status(self, project_id):
            git_calls.append(project_id)
            return {
                "status": "completed",
                "dirty": after_queue == "git_dirty",
                "changedFiles": [],
                "remotes": [],
            }

    context = ProcessExecutionContext(
        db_path=case.root / "platform.sqlite",
        connection=case.connection,
        execution_id=job["id"],
        project_id=case.project["id"],
        in_job_runner=True,
    )
    with execution_scope(context):
        result = case.coordinator.run_user_message(
            project_id=case.project["id"],
            thread_id=case.thread["id"],
            message=case.message["content"],
            run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
            git_service=Git(),
        )
    assert result["loop"]["id"] == case.loop["id"]
    durable = result["loop"]["context"].get("durableRun") or {}
    assert result["status"] == (
        "plan_ready" if after_queue in {None, "no_research", "inventory_boundary"} else "blocked"
    ), {
        "reason": result.get("reason"),
        "blockedReason": durable.get("blockedReason"),
        "blockedStage": durable.get("blockedStage"),
    }
    if after_queue in {None, "git_dirty", "no_research", "inventory_boundary"}:
        assert git_calls == [case.project["id"]]
    if after_queue in {None, "no_research", "inventory_boundary"}:
        assert len(result["loop"]["context"]["durableRun"]["agentAssignments"]) == 12
        assert all(
            role["resourceDecision"]["policyResult"]["decisionEngine"]["effectiveRisk"] == "high"
            for role in result["loop"]["context"]["durableRun"]["teamSchedule"]["roles"]
        )
        technical_plan = result["loop"]["context"]["durableRun"].get("technicalPlan")
        assert technical_plan, "Risk continuation lost the persisted TechnicalLead plan"
        assert technical_plan["qualityGates"] == [{"id": "compatibility-proof"}]
        assert technical_plan["assignmentHandoffs"] == [
            {"fromRole": "technical_lead", "toRole": "qa_engineer"}
        ]
        assert technical_plan["branchWorktreePlan"] == {"isolationType": "git_worktree"}


def _run_risk_job(case, job, git_calls):
    class Git:
        def status(self, project_id):
            git_calls.append(project_id)
            return {"status": "completed", "dirty": False, "changedFiles": [], "remotes": []}

    with execution_scope(
        ProcessExecutionContext(
            db_path=case.root / "platform.sqlite",
            connection=case.connection,
            execution_id=job["id"],
            project_id=case.project["id"],
            in_job_runner=True,
        )
    ):
        return case.coordinator.run_user_message(
            project_id=case.project["id"],
            thread_id=case.thread["id"],
            message=case.message["content"],
            run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
            git_service=Git(),
        )


@pytest.mark.parametrize("concurrent_change", ["cancel", "context", "revision"])
def test_risk_inventory_revalidation_cannot_consume_changed_continuation(
    risk_lane, monkeypatch, concurrent_change
):
    case = risk_lane
    review = _review(case)
    job = _approve(case, review)["execution"]["job"]
    jobs = JobsRepository(case.connection)
    jobs.update_job_status(job["id"], status="running")
    select = case.coordinator._team_schedule_with_resource_decisions
    assignments_before_selection = []

    def changed_while_revalidating(**kwargs):
        assignments_before_selection.extend(
            case.coordinator.get(case.loop["id"])["context"]["durableRun"].get("agentAssignments") or []
        )
        result = select(**kwargs)
        if concurrent_change == "cancel":
            jobs.cancel_job(job["id"], reason="Operator cancelled during runtime inventory")
        elif concurrent_change == "context":
            loop = case.coordinator.get(case.loop["id"])
            loop["context"]["operatorRevision"] = "changed during inventory"
            case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
        else:
            case.connection.execute(
                "UPDATE product_loops SET version=version+1 WHERE id=?", (case.loop["id"],)
            )
        return result

    monkeypatch.setattr(
        case.coordinator, "_team_schedule_with_resource_decisions", changed_while_revalidating
    )
    result = _run_risk_job(case, job, [])
    assert result["status"] == "blocked", result.get("reason")
    durable = case.coordinator.get(case.loop["id"])["context"]["durableRun"]
    assert durable["runtimeRiskReview"]["status"] == "consuming"
    assert durable.get("agentAssignments") == assignments_before_selection
    if concurrent_change == "cancel":
        assert jobs.get_job(job["id"])["status"] == "cancelled"
    if concurrent_change == "context":
        assert (
            case.coordinator.get(case.loop["id"])["context"]["operatorRevision"] == "changed during inventory"
        )


@pytest.mark.parametrize(
    "recovery", ["failed_retry", "without_retry", "cancelled_retry", "cancelled_failed_retry", "changed_plan"]
)
def test_risk_recovery_requires_failed_same_job_retry_before_consumption(risk_lane, monkeypatch, recovery):
    from local_control_center.product_loop import runtime_risk_review

    case = risk_lane
    review = _review(case)
    job = _approve(case, review)["execution"]["job"]
    jobs = JobsRepository(case.connection)
    jobs.update_job_status(job["id"], status="running")
    git_calls = []
    resume = runtime_risk_review.resume_runtime_risk_planning

    def interrupted_before_selection(*_args):
        raise RuntimeError("Synthetic external boundary failure before consent consumption")

    monkeypatch.setattr(runtime_risk_review, "resume_runtime_risk_planning", interrupted_before_selection)
    with pytest.raises(RuntimeError, match="Synthetic external boundary"):
        _run_risk_job(case, job, git_calls)
    monkeypatch.setattr(runtime_risk_review, "resume_runtime_risk_planning", resume)
    loop = case.coordinator.get(case.loop["id"])
    assert loop["state"] == "discovery"
    assert loop["context"]["durableRun"]["runtimeRiskReview"]["status"] == "consuming"
    if recovery in {"cancelled_retry", "cancelled_failed_retry"}:
        jobs.cancel_job(job["id"], reason="Explicitly cancelled, not a recoverable failure")
        if recovery == "cancelled_failed_retry":
            jobs.update_job_status(job["id"], status="failed")
    else:
        jobs.update_job_status(job["id"], status="failed")
    if recovery != "without_retry":
        jobs.retry_job(job["id"])
    jobs.update_job_status(job["id"], status="running")
    if recovery == "changed_plan":
        loop["context"]["durableRun"]["technicalLeadPlan"]["plan"]["quality_gates"] = []
        case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    jobs_before = case.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    jev_calls = len(case.jev.requests)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Recovery must not repeat ProductOwner, assessment, research, TechnicalLead or Jev")

    monkeypatch.setattr(case.jev, "decide", forbidden)
    monkeypatch.setattr(ProductLoopCoordinator, "_generate_agent_tasks", forbidden)
    result = _run_risk_job(case, job, git_calls)
    assert result["status"] == ("plan_ready" if recovery == "failed_retry" else "blocked"), {
        "reason": result.get("reason"),
        "blockedReason": (result["loop"]["context"].get("durableRun") or {}).get("blockedReason"),
    }
    assert case.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs_before
    assert len(case.jev.requests) == jev_calls
    assert jobs.get_action_request(review["actionRequestId"])["status"] == "approved"
    if recovery == "failed_retry":
        assert git_calls == [case.project["id"], case.project["id"]]
        receipt = result["loop"]["context"]["durableRun"]["runtimeRiskReview"]
        assert receipt["status"] == "consumed"
        assert receipt["continuationJobId"] == job["id"]
        assert receipt["recoveryRetryEventId"]
        assert _run_risk_job(case, job, git_calls)["status"] == "blocked"


def test_newer_blocked_loop_invalidates_review_without_an_active_job(risk_lane):
    from fastapi import HTTPException

    case = risk_lane
    review = _review(case)
    case.coordinator.repository.create_loop(
        {
            "projectId": case.project["id"],
            "title": "Newer request",
            "state": "blocked",
            "status": "blocked",
            "context": {"durableRun": {"thread": {"projectThreadId": case.thread["id"]}}},
        }
    )
    with pytest.raises(ValueError, match="superseded"):
        _review(case)
    with pytest.raises(HTTPException):
        _approve(case, review)
    assert (
        JobsRepository(case.connection).get_action_request(review["actionRequestId"])["status"] == "pending"
    )


def test_cancelled_approval_job_cannot_be_revived_by_risk_consent(risk_lane):
    from fastapi import HTTPException

    case = risk_lane
    review = _review(case)
    jobs = JobsRepository(case.connection)
    jobs.cancel_job(review["jobId"], reason="Operator cancelled review")
    with pytest.raises(ValueError, match="cancelled"):
        _review(case)
    with pytest.raises(HTTPException):
        _approve(case, review)
    assert jobs.get_job(review["jobId"])["status"] == "cancelled"
    assert jobs.get_action_request(review["actionRequestId"])["status"] == "pending"


def test_risk_approval_reason_redacts_secrets_in_action_and_audit(risk_lane):
    case = risk_lane
    review = _review(case)
    secret = "sk-" + "syntheticTestSecret" * 4
    result = approve_action(
        JobsRepository(case.connection),
        review["jobId"],
        review["actionRequestId"],
        {"reason": f"Reviewed all proposals; key {secret}"},
    )
    assert secret not in json_dumps(result["actionRequest"])
    assert secret not in json_dumps(result["auditEvent"])
    assert "[redacted]" in result["actionRequest"]["reason"]


@pytest.mark.parametrize("alter_extra", [False, True])
def test_planner_enriched_task_fields_must_match_canonical_metadata(risk_lane, alter_extra):
    case = risk_lane
    metadata = {**case.task["metadata"], "goal": "Preserve accepted compatibility decisions"}
    case.connection.execute(
        "UPDATE agent_tasks SET metadata=? WHERE id=?", (json_dumps(metadata), case.task["id"])
    )
    task = BacklogRepository(case.connection).get_agent_task(case.task["id"])
    loop = case.coordinator.get(case.loop["id"])
    loop["context"]["durableRun"]["agentTasks"] = [
        {**task, "goal": "Changed goal" if alter_extra else metadata["goal"]}
    ]
    loop["context"]["durableRun"]["technicalLeadPlan"]["tasksHash"] = fingerprint(
        loop["context"]["durableRun"]["agentTasks"]
    )
    case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    if alter_extra:
        with pytest.raises(ValueError, match="planning tasks changed"):
            _review(case)
    else:
        assert len(_review(case)["proposals"]) == 12


def test_risk_approval_compare_and_swap_rejects_concurrent_loop_revision(risk_lane, monkeypatch):
    from fastapi import HTTPException

    from local_control_center.product_loop import runtime_risk_review

    case = risk_lane
    review = _review(case)
    checkpoint = runtime_risk_review._checkpoint

    def changed(coordinator, loop):
        result = checkpoint(coordinator, loop)
        case.connection.execute("UPDATE product_loops SET version=version+1 WHERE id=?", (loop["id"],))
        return result

    monkeypatch.setattr(runtime_risk_review, "_checkpoint", changed)
    with pytest.raises(HTTPException, match="concurrently"):
        _approve(case, review)
    assert (
        JobsRepository(case.connection).get_action_request(review["actionRequestId"])["status"] == "pending"
    )


def test_deny_runtime_risk_cancels_only_review_job(risk_lane):
    from local_control_center.jobs_approvals.commands import deny_action

    case = risk_lane
    review = _review(case)
    before = case.coordinator.get(case.loop["id"])
    result = deny_action(
        JobsRepository(case.connection),
        review["jobId"],
        review["actionRequestId"],
        {"reason": "Risk not accepted"},
    )
    assert result["job"]["status"] == "cancelled"
    assert result["actionRequest"]["status"] == "denied"
    assert case.coordinator.get(case.loop["id"])["state"] == before["state"]
    assert (
        case.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind='thread.product_loop.run' AND status='queued'"
        ).fetchone()[0]
        == 0
    )
    assert case.connection.execute("SELECT COUNT(*) FROM permission_grants").fetchone()[0] == 0


def test_legacy_retry_cannot_restart_runtime_risk_block_without_consent(risk_lane):
    case = risk_lane
    from local_control_center.remediations.repository import RemediationActionsRepository

    card = RemediationActionsRepository(case.connection).create_action(
        project_id=case.project["id"],
        thread_id=case.thread["id"],
        loop_id=case.loop["id"],
        stage="resource_manager",
        blocker_type="runtime_not_executable",
        action_type="retry_loop",
        title="Legacy retry",
        description="Old classification",
        primary=True,
        payload={},
    )
    result = case.service._retry_loop(action=card)
    assert result["status"] == "blocked"
    assert "explicit review" in result["reason"]
    assert case.coordinator.get(case.loop["id"])["state"] == "blocked"


def test_risk_consent_cannot_cross_roles_sharing_the_same_task(risk_lane):
    from dataclasses import replace

    from local_control_center.agents.agent_resource_policy import (
        apply_profile_limits,
        effective_resource_profile,
    )
    from local_control_center.decision_engine.config import resolve_config
    from local_control_center.product_loop.runtime_risk_review import (
        approved_runtime_candidate,
        load_runtime_risk_continuation,
    )

    case = risk_lane
    review = _review(case)
    job = _approve(case, review)["execution"]["job"]
    JobsRepository(case.connection).update_job_status(job["id"], status="running")
    run = _UserMessageRun(
        project_id=case.project["id"],
        message=case.message["content"],
        actor="operator",
        thread_id=case.thread["id"],
        run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
    )
    with execution_scope(
        ProcessExecutionContext(
            db_path=case.root / "platform.sqlite",
            connection=case.connection,
            execution_id=job["id"],
            project_id=case.project["id"],
            in_job_runner=True,
        )
    ):
        load_runtime_risk_continuation(case.coordinator, run)
        request = case.coordinator._team_resource_request(
            project_id=case.project["id"],
            loop_id=case.loop["id"],
            request_meta=run.request_meta,
            team_schedule=case.schedule,
            role_plan=case.schedule["roles"][0],
            agent_tasks=[case.task],
        )
        request = apply_profile_limits(
            request, effective_resource_profile(case.connection, request.agent_profile_id, request.project_id)
        )
        request = replace(request, agent_profile_id=review["proposals"][1]["agentProfileId"])
        with pytest.raises(ValueError, match="role/profile/task"):
            approved_runtime_candidate(
                case.connection,
                request=request,
                review_id=review["id"],
                config=resolve_config(case.connection, case.project["id"]),
                revalidate=lambda _identity: pytest.fail(
                    "Cross-role consent must fail before candidate execution"
                ),
            )


def test_planning_persists_raw_plan_before_resource_gate(risk_lane, monkeypatch):
    from local_control_center.product_loop.research_resolution import hydrate_research_run

    case = risk_lane
    case.connection.execute(
        "UPDATE product_loops SET state='discovery',status='running' WHERE id=?", (case.loop["id"],)
    )
    loop = case.coordinator.get(case.loop["id"])
    raw_plan = loop["context"]["durableRun"].pop("technicalLeadPlan")["plan"]
    case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    run = _UserMessageRun(project_id=case.project["id"], message=case.message["content"], actor="operator")
    hydrate_research_run(
        case.coordinator, run, loop=loop, receipt=loop["context"]["durableRun"]["researchResolution"]
    )

    def generate(_self, **kwargs):
        kwargs["plan_sink"].update(raw_plan)
        return [case.task]

    monkeypatch.setattr(ProductLoopCoordinator, "_generate_agent_tasks", generate)
    monkeypatch.setattr(ProductLoopCoordinator, "_team_schedule", lambda _self, **_kwargs: case.schedule)
    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_team_schedule_with_resource_decisions",
        lambda _self, **_kwargs: (case.schedule, [{"role": "aido_lead", "reason": "Risk requires review"}]),
    )
    result = case.coordinator._plan_team_and_resources(run)
    assert result["status"] == "blocked"
    checkpoint = result["loop"]["context"]["durableRun"].get("technicalLeadPlan")
    assert checkpoint and checkpoint["origin"] == "original"
    assert checkpoint["plan"] == raw_plan
    assert checkpoint["planHash"] == fingerprint(raw_plan)


def _legacy_planner_checkpoint(case):
    from local_control_center.team_scheduler.scheduler import schedule_team

    coordinator = case.coordinator
    backlog = [
        {
            "epic": epic,
            "stories": [
                {
                    "story": story,
                    "acceptanceCriteria": coordinator.backlog.list_acceptance_criteria(story["id"]),
                }
                for story in coordinator.backlog.list_user_stories(case.project["id"], epic["id"])
            ],
        }
        for epic in coordinator.backlog.list_epics(case.project["id"])
    ]
    case.connection.execute("DELETE FROM agent_tasks WHERE id=?", (case.task["id"],))
    raw_plan = {}
    loop = coordinator.get(case.loop["id"])
    durable = loop["context"]["durableRun"]
    intent = case.schedule.get("intent") or {}
    scope = coordinator._team_scope(
        message=durable["message"], intent=intent, output=case.output, agent_tasks=[]
    )
    preliminary = {
        **schedule_team(scope=scope, risk=case.schedule["risk"], mode=case.schedule["mode"]),
        "intent": intent,
    }
    tasks = coordinator._generate_agent_tasks(
        project_id=case.project["id"],
        loop_id=loop["id"],
        backlog=backlog,
        product_owner_output_id=case.output["id"],
        technical_lead_runner=None,
        team_schedule=preliminary,
        product_owner_output=case.output,
        assessment_result=durable["assessment"],
        git_state={"changedFiles": []},
        plan_sink=raw_plan,
    )
    artifact = coordinator._write_json_artifact(
        root=case.root,
        project_id=case.project["id"],
        name="legacy-backlog",
        kind="product_backlog",
        payload={"backlog": backlog, "productOwnerOutputId": case.output["id"]},
    )
    evidence = coordinator.evidence.create_evidence_package(
        project_id=case.project["id"], workflow_run_id=loop["id"], workspace_id=case.workspace["id"]
    )
    coordinator.evidence.attach_artifact_to_evidence(
        artifact_id=artifact["id"], evidence_package_id=evidence["id"]
    )
    durable["productOwner"]["evidencePackageId"] = evidence["id"]
    final_scope = coordinator._team_scope(
        message=durable["message"], intent=intent, output=case.output, agent_tasks=tasks
    )
    durable["teamSchedule"] = {
        **schedule_team(scope=final_scope, risk=case.schedule["risk"], mode=case.schedule["mode"]),
        "intent": intent,
    }
    durable.update(agentTasks=tasks, backlog={"artifactId": artifact["id"]})
    durable.pop("technicalLeadPlan", None)
    loop = coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    return loop, raw_plan, artifact


@pytest.mark.parametrize("change", [None, "task", "dependencies", "artifact"])
def test_legacy_plan_reconstruction_requires_exact_task_dependency_and_source_evidence(risk_lane, change):
    from local_control_center.product_loop.runtime_risk_review import _resolve_technical_plan

    case = risk_lane
    loop, raw_plan, artifact = _legacy_planner_checkpoint(case)
    task = loop["context"]["durableRun"]["agentTasks"][0]
    if change == "task":
        case.connection.execute(
            "UPDATE agent_tasks SET description='Different task' WHERE id=?", (task["id"],)
        )
    elif change == "dependencies":
        assert case.connection.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0] > 0
        case.connection.execute("DELETE FROM task_dependencies")
    elif change == "artifact":
        Path(artifact["path"]).write_text("changed", encoding="utf-8")
    before = [
        tuple(row) for row in case.connection.execute("SELECT * FROM agent_tasks ORDER BY id").fetchall()
    ]
    if change:
        with pytest.raises(ValueError):
            _resolve_technical_plan(case.coordinator, loop)
    else:
        checkpoint = _resolve_technical_plan(case.coordinator, loop)
        assert checkpoint["origin"] == "reconstructed"
        assert checkpoint["plan"] == raw_plan
        assert checkpoint["planHash"] == fingerprint(raw_plan)
    assert [
        tuple(row) for row in case.connection.execute("SELECT * FROM agent_tasks ORDER BY id").fetchall()
    ] == before


def test_technical_plan_change_after_risk_consent_invalidates_approval(risk_lane):
    from fastapi import HTTPException

    case = risk_lane
    review = _review(case)
    loop = case.coordinator.get(case.loop["id"])
    plan = loop["context"]["durableRun"]["technicalLeadPlan"]
    plan["plan"]["quality_gates"] = []
    plan["planHash"] = fingerprint(plan["plan"])
    case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    with pytest.raises(HTTPException):
        _approve(case, review)


def test_legacy_plan_uses_preliminary_roles_instead_of_expanding_final_schedule(risk_lane):
    from local_control_center.product_loop.runtime_risk_review import _resolve_technical_plan

    case = risk_lane
    loop, raw_plan, _artifact = _legacy_planner_checkpoint(case)
    assert "pentester" in {item["role"] for item in loop["context"]["durableRun"]["teamSchedule"]["roles"]}
    assert "pentester" not in {item["role"] for item in raw_plan["agent_tasks"]}
    recovered = _resolve_technical_plan(case.coordinator, loop)
    assert recovered["plan"] == raw_plan


def test_legacy_plan_materialization_persists_one_review_and_reuses_action_and_job(risk_lane):
    case = risk_lane
    loop, _raw_plan, _artifact = _legacy_planner_checkpoint(case)
    durable = loop["context"]["durableRun"]
    for role in durable["teamSchedule"]["roles"]:
        RoutingProfileStore(case.connection).upsert_role_policy(
            {
                "role": role["role"],
                "allowApi": True,
                "allowRemote": True,
                "allowLocal": True,
                "allowCli": True,
                "allowUnknownCost": True,
                "requireApprovalForUnknownCost": False,
                "maxCostPerTaskUsd": 10.0,
            }
        )
    schedule, blockers = case.coordinator._team_schedule_with_resource_decisions(
        project_id=case.project["id"],
        loop_id=loop["id"],
        request_meta=durable.get("effectiveRequestMeta") or durable["requestMeta"],
        team_schedule=durable["teamSchedule"],
        agent_tasks=durable["agentTasks"],
    )
    durable["teamSchedule"] = schedule
    durable["resource_manager"] = {"resourceBlockers": blockers, "teamSchedule": schedule}
    case.coordinator.repository.update_loop_context(loop["id"], context=loop["context"])
    first = _review(case)
    saved = case.coordinator.get(loop["id"])["context"]["durableRun"]
    assert saved.get("runtimeRiskReview", {}).get("id") == first["id"]
    assert saved["technicalLeadPlan"]["origin"] == "reconstructed"
    second = _review(case)
    assert (second["id"], second["actionRequestId"], second["jobId"]) == (
        first["id"],
        first["actionRequestId"],
        first["jobId"],
    )
    assert (
        case.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind='product_loop_runtime_risk_approval'"
        ).fetchone()[0]
        == 1
    )


def test_durable_product_owner_selected_resource_reads_the_persisted_decision():
    """``_checkpoint`` y ``resume_runtime_risk_planning`` heredan el proveedor del PO desde el mismo
    lugar durable, para que el request reconstruido siga dando el mismo ``requestHash``."""
    durable = {
        "productOwner": {
            "status": "backlog_ready",
            "resourceDecision": {
                "selected": {"providerId": "llama_cpp", "model": "gemma-4-26b-a4b", "runtime": "local"}
            },
        }
    }
    assert _durable_product_owner_selected_resource(durable) == {
        "providerId": "llama_cpp",
        "model": "gemma-4-26b-a4b",
        "runtime": "local",
    }
    assert _durable_product_owner_selected_resource({}) == {}
    assert _durable_product_owner_selected_resource({"productOwner": {"resourceDecision": {}}}) == {}
    assert (
        _durable_product_owner_selected_resource({"productOwner": {"resourceDecision": {"selected": None}}})
        == {}
    )
