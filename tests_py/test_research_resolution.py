"""Explicit research adoption reuses durable ProductOwner output without inference."""

from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.research_agent import ResearchAgentRunner
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")
SOURCE_URL = "https://docs.python.org/3/library/asyncio-task.html"
LEGACY_SNAPSHOT_ADDITIONS = {"assessmentHash", "threadDecisionsHash", "executionOptions"}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Research adoption must not invoke ProductOwner, assessment, or provider selection")

    for name in ("_run_discovery_phase", "_run_project_assessment", "_select_product_owner_resources"):
        monkeypatch.setattr(ProductLoopCoordinator, name, forbidden)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)

        def create(
            status="brief_ready",
            autonomy=None,
            source_metadata=None,
            sealed_metadata=None,
            original_job=False,
        ):
            project_path = tmp_path / "project"
            project_path.mkdir(exist_ok=True)
            project = ProjectsRepository(connection).create_project(
                name="Research adoption", path=project_path, template_id="other"
            )
            threads = ThreadsRepository(connection)
            thread = threads.create_thread(
                project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Migration"
            )
            message = threads.append_message(
                thread_id=thread["id"],
                kind="user",
                author="operator",
                content="Determine the React version after discovery.",
                metadata=source_metadata,
            )
            workspace = WorkspacesRepository(connection, root=tmp_path).allocate_workspace(
                project_id=project["id"],
                task_id="research-adoption",
                agent_id="product_owner_agent",
                isolation_type="directory",
            )
            discovery = ProductDiscoveryRepository(connection)
            initiative = discovery.create_initiative({"projectId": project["id"], "title": "Migration"})
            brief = discovery.upsert_product_brief(
                {
                    "projectId": project["id"],
                    "initiativeId": initiative["id"],
                    "title": "Migration",
                    "summary": message["content"],
                }
            )
            decisions = [
                {
                    "title": title,
                    "recommendation": recommendation,
                    "category": "technical",
                    "impact": "high",
                    "requiresResearch": True,
                    "status": "accepted",
                }
                for title, recommendation in (
                    ("React discovery", "Determine the React version after discovery."),
                    ("Migration sequencing", "Inspect compatibility before implementation."),
                )
            ]
            persisted = [
                discovery.create_product_decision(
                    {
                        "projectId": project["id"],
                        "initiativeId": initiative["id"],
                        "briefId": brief["id"],
                        "title": item["title"],
                        "decision": item["recommendation"],
                        "status": "accepted",
                        "metadata": item,
                    }
                )
                for item in decisions
            ]
            output = discovery.create_product_owner_output(
                {
                    "projectId": project["id"],
                    "initiativeId": initiative["id"],
                    "briefId": brief["id"],
                    "status": status,
                    "summary": message["content"],
                    "confidence": "high",
                    "decisions": decisions,
                    "epics": [{"title": "Discovery"}] if status == "backlog_ready" else [],
                    "userStories": [
                        {
                            "epicTitle": "Discovery",
                            "title": "Inspect compatibility",
                            "asA": "maintainer",
                            "iWant": "to inspect compatibility",
                            "soThat": "I choose safely",
                            "businessValue": "high",
                            "acceptanceCriteria": ["Framework version remains undecided until discovery."],
                        }
                    ]
                    if status == "backlog_ready"
                    else [],
                }
            )
            durable = {
                "status": "blocked",
                "blockedStage": "research",
                "blockedReason": "Research required.",
                "thread": {"projectThreadId": thread["id"], "messageId": message["id"]},
                "message": message["content"],
                "runActive": False,
                "assessment": {"status": "completed", "assessment": {"stack": ["Java 8", "Thymeleaf"]}},
                "requestMeta": {
                    **(sealed_metadata or {}),
                    "researchPolicy": {
                        "requireForHighImpactTechnicalDecisions": True,
                        "allowWebSearch": False,
                    },
                },
                "productOwner": {
                    "status": status,
                    "productOwnerOutputId": output["id"],
                    "briefId": brief["id"],
                    "workspaceId": workspace["id"],
                    "productDecisionIds": [item["id"] for item in persisted],
                    "artifactIds": [],
                    "pendingThreadDecisions": [],
                },
                "research": {"researchStatus": "research_required", "decisions": decisions},
            }
            if autonomy:
                durable["requestMeta"]["autonomy"] = autonomy
            if original_job:
                source_job = JobsRepository(connection).create_job(
                    project_id=project["id"],
                    kind="thread.product_loop.run",
                    status="completed",
                    payload={"threadId": thread["id"], "messageId": message["id"]},
                )["job"]
                durable["requestMeta"]["jobId"] = source_job["id"]
            loop = ProductLoopRepository(connection).create_loop(
                {
                    "projectId": project["id"],
                    "initiativeId": initiative["id"],
                    "title": "Migration",
                    "state": "blocked",
                    "status": "blocked",
                    "context": {"durableRun": durable},
                }
            )
            threads.set_status(thread["id"], "blocked")
            service = BlockerRemediationService(connection, root=tmp_path)
            actions = service.create_for_blocked_run(
                project_id=project["id"],
                thread_id=thread["id"],
                loop_id=loop["id"],
                stage="research",
                reason="Research required.",
                details=durable["research"],
            )
            return SimpleNamespace(
                connection=connection,
                root=tmp_path,
                project=project,
                thread=thread,
                message=message,
                workspace=workspace,
                output=output,
                brief=brief,
                loop=loop,
                decisions=persisted,
                service=service,
                action=next(item for item in actions if item["actionType"] == "retry_loop"),
            )

        yield create


def _research(lane, *, technical=True):
    return ResearchAgentRunner(lane.connection, root=lane.root).run(
        {
            "projectId": lane.project["id"],
            "workspaceId": lane.workspace["id"],
            "taskId": "cited-research",
            "sources": [
                {
                    "url": SOURCE_URL,
                    "publisher": "Python Software Foundation",
                    "content": "Inspect compatibility before selecting framework versions.",
                }
            ],
            "technicalDecisions": [
                {"title": item["title"], "decision": item["decision"], "sourceUrls": [SOURCE_URL]}
                for item in lane.decisions
            ]
            if technical
            else [],
            "metadata": {
                "threadId": lane.thread["id"],
                "messageId": lane.message["id"],
                "loopId": lane.loop["id"],
                "productOwnerOutputId": lane.output["id"],
            },
        }
    )


def _execute(lane, research_id=None):
    return lane.service.execute(
        lane.action["id"], platform=object(), payload={"researchRunId": research_id} if research_id else {}
    )["execution"]


def _legacy_snapshot(case):
    current = deepcopy(case.action["payload"]["researchSnapshot"])
    legacy = {key: value for key, value in current.items() if key not in LEGACY_SNAPSHOT_ADDITIONS}
    case.connection.execute(
        "UPDATE remediation_actions SET payload_json=json_set(payload_json,'$.researchSnapshot',json(?)) WHERE id=?",
        (json_dumps(legacy), case.action["id"]),
    )
    return current, legacy


@pytest.mark.parametrize("entrypoint", ["list", "execute"])
def test_legacy_research_snapshot_adds_only_known_fields_with_provenance(lane, entrypoint):
    case = lane()
    research_id = _research(case)["researchRun"]["id"]
    current, legacy = _legacy_snapshot(case)
    if entrypoint == "list":
        actions = case.service.list_for_thread(thread_id=case.thread["id"])
        action = next(item for item in actions if item["id"] == case.action["id"])
        assert action["payload"]["researchCandidates"][0]["eligible"] is True
    else:
        assert _execute(case, research_id)["status"] == "completed"
    payload = case.service.repository.get(case.action["id"])["payload"]
    assert payload["researchSnapshot"] == current
    assert payload["researchLoopVersion"] == case.loop["version"]
    upgrade = payload["researchSnapshotUpgrade"]
    assert upgrade["addedFields"] == sorted(LEGACY_SNAPSHOT_ADDITIONS)
    assert upgrade["previousSnapshot"] == legacy
    assert upgrade["loopVersion"] == case.loop["version"]
    assert upgrade["upgradedAt"]
    case.service.list_for_thread(thread_id=case.thread["id"])
    assert case.service.repository.get(case.action["id"])["payload"]["researchSnapshotUpgrade"] == upgrade


@pytest.mark.parametrize(
    "mismatch", ["old_field", "loop_version", "other_missing", "partial_new_fields", "extra_field"]
)
def test_legacy_research_snapshot_does_not_upgrade_changed_or_unknown_shape(lane, mismatch):
    case = lane()
    research_id = _research(case)["researchRun"]["id"]
    current, legacy = _legacy_snapshot(case)
    if mismatch == "old_field":
        legacy["requestHash"] = "changed"
    elif mismatch == "other_missing":
        legacy.pop("workspaceId")
    elif mismatch == "partial_new_fields":
        legacy["assessmentHash"] = current["assessmentHash"]
    elif mismatch == "extra_field":
        legacy["unknownField"] = "persisted"
    case.connection.execute(
        "UPDATE remediation_actions SET payload_json=json_set(payload_json,'$.researchSnapshot',json(?),"
        "'$.researchLoopVersion',?) WHERE id=?",
        (json_dumps(legacy), case.loop["version"] + (mismatch == "loop_version"), case.action["id"]),
    )
    assert _execute(case, research_id)["status"] == "blocked"
    payload = case.service.repository.get(case.action["id"])["payload"]
    assert payload["researchSnapshot"] == legacy
    assert "researchSnapshotUpgrade" not in payload


def test_legacy_research_first_observation_without_anchor_still_anchors_current_snapshot(lane):
    case = lane()
    research_id = _research(case)["researchRun"]["id"]
    current = case.action["payload"]["researchSnapshot"]
    case.connection.execute(
        "UPDATE remediation_actions SET payload_json=json_remove(payload_json,'$.researchSnapshot',"
        "'$.researchLoopVersion') WHERE id=?",
        (case.action["id"],),
    )
    assert _execute(case, research_id)["status"] == "completed"
    payload = case.service.repository.get(case.action["id"])["payload"]
    assert payload["researchSnapshot"] == current
    assert "researchSnapshotUpgrade" not in payload


@pytest.mark.parametrize("concurrent_change", ["payload", "status", "loop_version"])
def test_legacy_research_snapshot_upgrade_does_not_overwrite_concurrent_change(
    lane, monkeypatch, concurrent_change
):
    from local_control_center.product_loop import research_resolution

    case = lane()
    research_id = _research(case)["researchRun"]["id"]
    _, legacy = _legacy_snapshot(case)
    concurrent = {**legacy, "requestHash": "concurrent-revision"}
    original = research_resolution.resolution_snapshot

    def changed_during_observation(connection, loop):
        snapshot = original(connection, loop)
        if concurrent_change == "payload":
            connection.execute(
                "UPDATE remediation_actions SET payload_json=json_set(payload_json,'$.researchSnapshot',json(?)) WHERE id=?",
                (json_dumps(concurrent), case.action["id"]),
            )
        elif concurrent_change == "status":
            connection.execute(
                "UPDATE remediation_actions SET status='resolved' WHERE id=?", (case.action["id"],)
            )
        else:
            connection.execute("UPDATE product_loops SET version=version+1 WHERE id=?", (case.loop["id"],))
        return snapshot

    monkeypatch.setattr(research_resolution, "resolution_snapshot", changed_during_observation)
    assert _execute(case, research_id)["status"] == "blocked"
    payload = case.service.repository.get(case.action["id"])["payload"]
    assert payload["researchSnapshot"] == (concurrent if concurrent_change == "payload" else legacy)
    assert "researchSnapshotUpgrade" not in payload


def test_research_retry_requires_explicit_choice_and_lists_candidate_without_new_job(lane):
    case = lane()
    research = _research(case)
    before = JobsRepository(case.connection).list_jobs(case.project["id"])
    action = next(
        item
        for item in case.service.list_for_thread(thread_id=case.thread["id"])
        if item["id"] == case.action["id"]
    )
    candidates = action["payload"]["researchCandidates"]
    assert len(candidates) == 1
    assert candidates[0]["researchRunId"] == research["researchRun"]["id"]
    assert candidates[0]["eligible"] is True
    assert research["researchRun"]["completedAt"] in candidates[0]["label"]
    assert _execute(case)["status"] == "blocked"
    assert JobsRepository(case.connection).list_jobs(case.project["id"]) == before


def test_explicit_research_adoption_completes_persisted_brief_without_po_or_new_job(lane):
    case = lane()
    research = _research(case)
    before_jobs = JobsRepository(case.connection).list_jobs(case.project["id"])
    result = _execute(case, research["researchRun"]["id"])
    assert result["status"] == "completed"
    loop = ProductLoopRepository(case.connection).get_loop(case.loop["id"])
    assert loop["state"] == "brief_ready"
    assert ThreadsRepository(case.connection).get_thread(case.thread["id"])["status"] == "open"
    receipt = loop["context"]["durableRun"]["researchResolution"]
    assert receipt["researchRunId"] == research["researchRun"]["id"]
    assert receipt["productOwnerOutputId"] == case.output["id"]
    assert receipt["loopId"] == case.loop["id"]
    assert JobsRepository(case.connection).list_jobs(case.project["id"]) == before_jobs
    assert (
        ProductDiscoveryRepository(case.connection).get_product_owner_output(case.output["id"]) == case.output
    )
    assert [
        ProductDiscoveryRepository(case.connection).get_product_decision(item["id"])
        for item in case.decisions
    ] == case.decisions


@pytest.mark.parametrize(
    "failure",
    [
        "project",
        "thread",
        "workspace",
        "loop",
        "hash",
        "untrusted",
        "recommendation",
        "missing_title",
        "decision_changed",
        "output_changed",
        "conflict",
        "unfinished",
        "different_decision",
    ],
)
def test_research_adoption_rejects_unusable_or_stale_evidence(lane, failure):
    case = lane()
    research = _research(case, technical=failure != "recommendation")
    research_id = research["researchRun"]["id"]
    connection = case.connection
    if failure in {"project", "thread", "workspace"}:
        column = {"project": "project_id", "thread": "thread_id", "workspace": "workspace_id"}[failure]
        # Disable the fixture FK only to simulate a corrupted legacy scope record; adoption must reject it.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(f"UPDATE research_runs SET {column}=? WHERE id=?", ("other-scope", research_id))
        connection.execute("PRAGMA foreign_keys=ON")
    elif failure == "loop":
        loop = ProductLoopRepository(connection).get_loop(case.loop["id"])
        loop["context"]["durableRun"]["requestMeta"]["researchPolicy"]["allowWebSearch"] = True
        ProductLoopRepository(connection).update_loop_context(loop["id"], context=loop["context"])
    elif failure == "hash":
        source = EvidenceRepository(connection).get_artifact_by_id(research["sources"][0]["artifactId"])
        Path(source["path"]).write_text("tampered", encoding="utf-8")
    elif failure == "untrusted":
        connection.execute(
            "UPDATE research_sources SET trust_level='untrusted' WHERE project_id=?", (case.project["id"],)
        )
    elif failure == "missing_title":
        connection.execute(
            "DELETE FROM research_findings WHERE research_run_id=? AND summary=?",
            (research_id, case.decisions[-1]["title"]),
        )
    elif failure == "decision_changed":
        ProductDiscoveryRepository(connection).update_product_decision(
            case.decisions[0]["id"], {"decision": "Choose React now."}
        )
    elif failure == "output_changed":
        connection.execute(
            "UPDATE product_owner_outputs SET summary='Changed' WHERE id=?", (case.output["id"],)
        )
    elif failure == "conflict":
        connection.execute("UPDATE research_runs SET conflict_count=1 WHERE id=?", (research_id,))
    elif failure == "unfinished":
        connection.execute("UPDATE research_runs SET completed_at=NULL WHERE id=?", (research_id,))
    elif failure == "different_decision":
        connection.execute(
            "UPDATE research_findings SET payload_json=json_set(payload_json,'$.decision','Use React 19.3.') "
            "WHERE research_run_id=? AND finding_type='technical_decision'",
            (research_id,),
        )
    before_jobs = JobsRepository(connection).list_jobs(case.project["id"])
    assert _execute(case, research_id)["status"] == "blocked"
    assert ProductLoopRepository(connection).get_loop(case.loop["id"])["state"] == "blocked"
    assert JobsRepository(connection).list_jobs(case.project["id"]) == before_jobs


def test_research_adoption_double_click_does_not_duplicate_transitions_or_jobs(lane):
    case = lane()
    research_id = _research(case)["researchRun"]["id"]
    assert _execute(case, research_id)["status"] == "completed"
    before = ProductLoopRepository(case.connection).list_transitions(case.loop["id"])
    jobs = JobsRepository(case.connection).list_jobs(case.project["id"])
    _execute(case, research_id)
    assert ProductLoopRepository(case.connection).list_transitions(case.loop["id"]) == before
    assert JobsRepository(case.connection).list_jobs(case.project["id"]) == jobs


@pytest.mark.parametrize("changed_after_queue", [None, "decision", "source_hash", "untrusted_caller"])
def test_backlog_research_retry_queues_same_loop_and_consumes_once_without_po(
    lane, monkeypatch, changed_after_queue
):
    case = lane("backlog_ready")
    research = _research(case)
    research_id = research["researchRun"]["id"]
    queued = _execute(case, research_id)
    assert queued["status"] == "queued"
    job = queued["job"]
    assert job["kind"] == "thread.product_loop.run"
    assert ProductLoopRepository(case.connection).get_loop(case.loop["id"])["state"] != "cancelled"
    seen = []

    def planning(self, run):
        seen.append(
            (run.loop["id"], deepcopy(run.output["decisions"]), run.product_owner_output_record["id"])
        )
        return {"status": "plan_ready", "loop": run.loop}

    monkeypatch.setattr(ProductLoopCoordinator, "_plan_team_and_resources", planning)
    monkeypatch.setattr(ProductLoopCoordinator, "_check_workspace_and_git", lambda self, run: None)
    monkeypatch.setattr(ProductLoopCoordinator, "_seal_constitution", lambda self, run: None)
    JobsRepository(case.connection).update_job_status(job["id"], status="running")
    if changed_after_queue == "decision":
        ProductDiscoveryRepository(case.connection).update_product_decision(
            case.decisions[0]["id"], {"decision": "Choose React now."}
        )
    elif changed_after_queue == "source_hash":
        artifact = EvidenceRepository(case.connection).get_artifact_by_id(
            research["sources"][0]["artifactId"]
        )
        Path(artifact["path"]).write_text("changed after enqueue", encoding="utf-8")
    context = ProcessExecutionContext(
        db_path=case.root / "platform.sqlite",
        connection=case.connection,
        execution_id=job["id"] if changed_after_queue != "untrusted_caller" else None,
        project_id=case.project["id"],
        in_job_runner=True,
    )
    with execution_scope(context):
        result = ProductLoopCoordinator(case.connection, root=case.root).run_user_message(
            project_id=case.project["id"],
            thread_id=case.thread["id"],
            message=case.message["content"],
            run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
        )
        if changed_after_queue is None:
            second = ProductLoopCoordinator(case.connection, root=case.root).run_user_message(
                project_id=case.project["id"],
                thread_id=case.thread["id"],
                message=case.message["content"],
                run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
            )
            assert second["status"] == "blocked"
    assert result["status"] == ("blocked" if changed_after_queue else "plan_ready")
    assert seen == (
        [] if changed_after_queue else [(case.loop["id"], case.output["decisions"], case.output["id"])]
    )
    assert len(ProductLoopRepository(case.connection).list_loops(case.project["id"])) == 1


def test_snapshot_rejects_superseding_accepted_decision(lane):
    from local_control_center.product_loop.research_resolution import (
        ResearchResolutionError,
        resolution_snapshot,
    )

    case = lane()
    _research(case)
    previous = case.decisions[0]
    ProductDiscoveryRepository(case.connection).create_product_decision(
        {
            "projectId": case.project["id"],
            "initiativeId": previous["initiativeId"],
            "briefId": case.brief["id"],
            "supersedesId": previous["id"],
            "title": previous["title"],
            "status": "accepted",
            "decision": "Use a different framework now.",
        }
    )
    with pytest.raises(ResearchResolutionError, match="supersed"):
        resolution_snapshot(case.connection, case.loop)


def test_snapshot_changes_when_resolved_thread_decision_is_revised(lane):
    from local_control_center.product_loop.research_resolution import resolution_snapshot

    case = lane()
    threads = ThreadsRepository(case.connection)
    decision = threads.create_decision(
        thread_id=case.thread["id"],
        message_id=case.message["id"],
        title="React version",
        prompt="When?",
        metadata={"productDecisionId": case.decisions[0]["id"]},
    )
    threads.resolve_decision(
        thread_id=case.thread["id"], decision_id=decision["id"], resolution="After discovery"
    )
    before = resolution_snapshot(case.connection, case.loop)
    threads.resolve_decision(thread_id=case.thread["id"], decision_id=decision["id"], resolution="React now")
    assert resolution_snapshot(case.connection, case.loop) != before


@pytest.mark.parametrize("existing_approval", [False, True])
def test_research_brief_preserves_guided_approval_contract(lane, existing_approval):
    case = lane(autonomy="guided")
    coordinator = ProductLoopCoordinator(case.connection, root=case.root)
    approval = None
    if existing_approval:
        approval = coordinator._create_brief_approval(
            project_id=case.project["id"],
            loop_id=case.loop["id"],
            brief=case.brief,
            artifact_ids=[],
        )
        current = coordinator.repository.get_loop(case.loop["id"])
        current["context"]["durableRun"]["productOwner"]["briefApproval"] = approval
        coordinator.repository.update_loop_context(current["id"], context=current["context"])
    research_id = _research(case)["researchRun"]["id"]
    assert _execute(case, research_id)["status"] == "completed"
    current = coordinator.repository.get_loop(case.loop["id"])
    restored = current["context"]["durableRun"]["briefApproval"]
    assert restored["status"] == "approval_required"
    if approval:
        assert restored == approval
    approvals = [
        job
        for job in JobsRepository(case.connection).list_jobs(case.project["id"])
        if job["kind"] == "product_loop_brief_approval"
    ]
    assert len(approvals) == 1
    assert approvals[0]["status"] == "approval_required"


@pytest.mark.parametrize("git_dirty", [False, True])
def test_real_planning_reuses_persisted_backlog_and_assessment_after_current_git_gate(lane, git_dirty):
    from local_control_center.agents.product_owner_agent import persist_product_owner_backlog
    from local_control_center.backlog.repository import BacklogRepository

    case = lane("backlog_ready")
    backlog = BacklogRepository(case.connection)
    persist_product_owner_backlog(
        backlog, project_id=case.project["id"], output=case.output, product_owner_output_id=case.output["id"]
    )
    before_stories = backlog.list_user_stories(case.project["id"])
    research_id = _research(case)["researchRun"]["id"]
    queued = _execute(case, research_id)
    assert queued["status"] == "queued"
    job = queued["job"]
    JobsRepository(case.connection).update_job_status(job["id"], status="running")
    calls = []

    class GitGate:
        def status(self, project_id):
            calls.append("git")
            return {
                "status": "completed",
                "projectId": project_id,
                "dirty": git_dirty,
                "changedFiles": [],
                "remotes": [],
            }

    class Planner:
        def generate_agent_tasks(self, payload):
            calls.append(payload)
            # Stop before resource selection/development; all preceding planning uses real repositories.
            return []

    context = ProcessExecutionContext(
        db_path=case.root / "platform.sqlite",
        connection=case.connection,
        execution_id=job["id"],
        project_id=case.project["id"],
        in_job_runner=True,
    )
    with execution_scope(context):
        result = ProductLoopCoordinator(case.connection, root=case.root).run_user_message(
            project_id=case.project["id"],
            thread_id=case.thread["id"],
            message=case.message["content"],
            run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
            git_service=GitGate(),
            technical_lead_runner=Planner(),
        )
    assert result["status"] == "blocked"
    assert result["loop"]["id"] == case.loop["id"]
    assert result["loop"]["context"]["durableRun"]["blockedStage"] == (
        "git" if git_dirty else "technical_lead"
    )
    assert calls[0] == "git"
    if git_dirty:
        assert calls == ["git"]
    else:
        assert len(calls) == 2
        assert [story["id"] for story in calls[1]["userStories"]] == [story["id"] for story in before_stories]
        assert calls[1]["projectAssessment"]["assessment"]["stack"] == ["Java 8", "Thymeleaf"]
    assert backlog.list_user_stories(case.project["id"]) == before_stories


@pytest.mark.parametrize(
    "policy_change",
    [
        "force_local_before_adoption",
        "force_local_after_queue",
        "project_mode",
        "thread_mode",
        "explicit_mode",
    ],
)
def test_research_continuation_reseals_current_policy_preserving_snapshot_and_job(
    lane, monkeypatch, policy_change
):
    from local_control_center.product_loop.metadata import seal_operator_cost_decision
    from local_control_center.settings.repository import SettingsRepository

    explicit = {"teamMode": "critical"} if policy_change == "explicit_mode" else {}
    case = lane(
        "backlog_ready", source_metadata=explicit, sealed_metadata={"teamMode": "critical"}, original_job=True
    )
    settings = SettingsRepository(case.connection)
    before = deepcopy(
        ProductLoopRepository(case.connection).get_loop(case.loop["id"])["context"]["durableRun"][
            "requestMeta"
        ]
    )
    if policy_change == "force_local_before_adoption":
        settings.set_value("project.routing.forceLocal", "project", case.project["id"], True)
    elif policy_change in {"project_mode", "explicit_mode"}:
        settings.set_value("project.loop.teamMode", "project", case.project["id"], "economy")
    elif policy_change == "thread_mode":
        seal_operator_cost_decision(
            case.connection,
            project_id=case.project["id"],
            metadata={"teamMode": "economy"},
            thread_id=case.thread["id"],
        )
    research_id = _research(case)["researchRun"]["id"]
    queued = _execute(case, research_id)
    assert queued["status"] == "queued"
    job = queued["job"]
    if policy_change == "force_local_after_queue":
        settings.set_value("project.routing.forceLocal", "project", case.project["id"], True)
    JobsRepository(case.connection).update_job_status(job["id"], status="running")
    observed = []

    class GitGate:
        def status(self, project_id):
            return {
                "status": "completed",
                "projectId": project_id,
                "dirty": False,
                "changedFiles": [],
                "remotes": [],
            }

    def planning(self, run):
        observed.append(deepcopy(run.request_meta))
        return {"status": "plan_ready", "loop": run.loop}

    monkeypatch.setattr(ProductLoopCoordinator, "_plan_team_and_resources", planning)
    context = ProcessExecutionContext(
        db_path=case.root / "platform.sqlite",
        connection=case.connection,
        execution_id=job["id"],
        project_id=case.project["id"],
        in_job_runner=True,
    )
    with execution_scope(context):
        result = ProductLoopCoordinator(case.connection, root=case.root).run_user_message(
            project_id=case.project["id"],
            thread_id=case.thread["id"],
            message=case.message["content"],
            run_metadata={**job["payload"]["runMetadata"], "jobId": job["id"]},
            git_service=GitGate(),
        )
    assert result["status"] == "plan_ready", result.get("reason")
    assert len(observed) == 1
    effective = observed[0]
    if policy_change.startswith("force_local"):
        assert effective["privacyLevel"] == "local_private"
    elif policy_change == "explicit_mode":
        assert effective["teamMode"] == "critical"
    else:
        assert effective["teamMode"] == "economy"
    assert effective["jobId"] == job["id"] != before["jobId"]
    current = ProductLoopRepository(case.connection).get_loop(case.loop["id"])["context"]["durableRun"]
    assert current["requestMeta"] == before
    assert current["effectiveRequestMeta"] == effective


def test_research_brief_reseals_force_local_at_explicit_adoption(lane, monkeypatch):
    from local_control_center.settings.repository import SettingsRepository

    case = lane()
    research_id = _research(case)["researchRun"]["id"]
    SettingsRepository(case.connection).set_value(
        "project.routing.forceLocal", "project", case.project["id"], True
    )
    before = deepcopy(case.loop["context"]["durableRun"]["requestMeta"])
    observed = []
    original = ProductLoopCoordinator._requires_brief_approval

    def approval(self, *, request_meta, result, output):
        observed.append(deepcopy(request_meta))
        return original(self, request_meta=request_meta, result=result, output=output)

    monkeypatch.setattr(ProductLoopCoordinator, "_requires_brief_approval", approval)
    assert _execute(case, research_id)["status"] == "completed"
    assert observed[0]["privacyLevel"] == "local_private"
    current = ProductLoopRepository(case.connection).get_loop(case.loop["id"])["context"]["durableRun"]
    assert current["requestMeta"] == before
    assert current["effectiveRequestMeta"] == observed[0]
