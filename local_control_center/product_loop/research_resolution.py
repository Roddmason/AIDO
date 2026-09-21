"""Verify explicit research adoption against durable decisions and source artifacts.

This module never performs research or inference. Metadata supplied by callers is not authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from local_control_center.agents.repository import AgentsRepository
from local_control_center.evidence.artifacts import resolved_artifact_root
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.research.repository import ResearchRepository, row_to_research_finding
from local_control_center.research.source_policy import TRUST_LEVELS
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository


class ResearchResolutionError(ValueError):
    """The selected evidence cannot discharge this particular research gate."""


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def resolution_snapshot(connection, loop: dict[str, Any], *, require_research: bool = True) -> dict[str, Any]:
    """Anchor the PO output, scope, policy and current accepted decision revisions."""
    durable = loop.get("context", {}).get("durableRun", {})
    po = durable.get("productOwner") or {}
    discovery = ProductDiscoveryRepository(connection)
    output = discovery.get_product_owner_output(str(po.get("productOwnerOutputId") or ""))
    brief = discovery.get_product_brief(str(po.get("briefId") or ""))
    thread_id = str((durable.get("thread") or {}).get("projectThreadId") or "")
    thread = ThreadsRepository(connection).get_thread(thread_id)
    workspace = connection.execute(
        "SELECT project_id FROM workspaces WHERE id=?", (po.get("workspaceId"),)
    ).fetchone()
    if (
        output["projectId"] != loop["projectId"]
        or brief["projectId"] != loop["projectId"]
        or thread["projectId"] != loop["projectId"]
        or output["briefId"] != brief["id"]
        or workspace is None
        or workspace["project_id"] != loop["projectId"]
    ):
        raise ResearchResolutionError("Research scope no longer matches the persisted ProductOwner output.")
    if output["status"] not in {"brief_ready", "backlog_ready"} or output["status"] != po.get("status"):
        raise ResearchResolutionError(
            "Only persisted brief_ready or backlog_ready output can resume research."
        )
    thread_decisions = ThreadsRepository(connection).list_decisions(thread_id)
    if any(item["status"] == "pending" for item in thread_decisions):
        raise ResearchResolutionError("Resolve the pending thread decisions before adopting research.")
    required = (durable.get("research") or {}).get("decisions") or []
    records = [discovery.get_product_decision(str(item)) for item in po.get("productDecisionIds") or []]
    expected = []
    for decision in required:
        title = _text(decision.get("title"))
        recommendation = _text(decision.get("decision") or decision.get("recommendation"))
        matches = [item for item in records if _text(item["title"]) == title]
        outputs = [item for item in output["decisions"] if _text(item.get("title")) == title]
        if (
            not title
            or not recommendation
            or len(matches) != 1
            or len(outputs) != 1
            or _text(outputs[0].get("decision") or outputs[0].get("recommendation")) != recommendation
        ):
            raise ResearchResolutionError(
                "The required technical decisions no longer match ProductOwner output."
            )
        record = matches[0]
        superseded = connection.execute(
            "WITH RECURSIVE successors(id,status) AS ("
            "SELECT id,status FROM product_decisions WHERE supersedes_id=? AND project_id=? UNION "
            "SELECT next.id,next.status FROM product_decisions next JOIN successors old ON next.supersedes_id=old.id "
            "WHERE next.project_id=?) SELECT 1 FROM successors WHERE status IN ('accepted','resolved') LIMIT 1",
            (record["id"], loop["projectId"], loop["projectId"]),
        ).fetchone()
        if superseded:
            raise ResearchResolutionError(
                "An accepted technical decision was superseded; review the current decision."
            )
        if (
            record["projectId"] != loop["projectId"]
            or record["briefId"] != brief["id"]
            or record["status"] not in {"accepted", "resolved"}
            or _text(record["decision"]) != recommendation
        ):
            raise ResearchResolutionError("An accepted technical decision changed; review research again.")
        expected.append(
            {
                "id": record["id"],
                "title": title,
                "decision": recommendation,
                "version": record["version"],
                "hash": _digest(record),
            }
        )
    if (require_research and not expected) or len({_text(item["title"]) for item in expected}) != len(
        expected
    ):
        raise ResearchResolutionError("The research gate has no unambiguous persisted technical decisions.")
    request = durable.get("requestMeta") or {}
    options = {key: request.get(key) for key in ("preferredRuntime", "qaCommands")}
    if request.get("jobId"):
        source_job = JobsRepository(connection).get_job(str(request["jobId"]))
        if source_job["projectId"] != loop["projectId"] or source_job["payload"].get("threadId") != thread_id:
            raise ResearchResolutionError("The original execution options belong to another thread.")
        options = {key: source_job["payload"].get(key) for key in options}
    relevant_thread_decisions = [
        item
        for item in thread_decisions
        if item.get("messageId") == (durable.get("thread") or {}).get("messageId")
        or (item.get("metadata") or {}).get("productDecisionId") in po.get("productDecisionIds", [])
        or (item.get("metadata") or {}).get("clarificationQuestionId")
        in po.get("clarificationQuestionIds", [])
    ]
    snapshot = {
        "loopId": loop["id"],
        "projectId": loop["projectId"],
        "threadId": thread_id,
        "messageId": (durable.get("thread") or {}).get("messageId"),
        "workspaceId": po.get("workspaceId"),
        "productOwnerOutputId": output["id"],
        "outputHash": _digest(output),
        "briefId": brief["id"],
        "briefHash": _digest(brief),
        "requestHash": _digest(request),
        "assessmentHash": _digest(durable.get("assessment")),
        "threadDecisionsHash": _digest(sorted(relevant_thread_decisions, key=lambda item: item["id"])),
        "executionOptions": options,
        "decisions": expected,
    }
    if not require_research:
        snapshot["productDecisionsHash"] = _digest(
            [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM product_decisions WHERE project_id=? AND brief_id=? ORDER BY id",
                    (loop["projectId"], brief["id"]),
                ).fetchall()
            ]
        )
    return snapshot


def _artifact_bytes(
    evidence, artifact_id: str, *, root: Path, project_id: str, evidence_id: str, kind: str
) -> tuple[dict[str, Any], bytes]:
    artifact = evidence.get_artifact_by_id(artifact_id)
    path = Path(artifact["path"]).resolve(strict=True)
    if (
        artifact["projectId"] != project_id
        or artifact["evidencePackageId"] != evidence_id
        or artifact["kind"] != kind
        or not path.is_relative_to(resolved_artifact_root(root))
        or not path.is_file()
        or path.stat().st_size > 8_000_000
    ):
        raise ResearchResolutionError("Research artifact scope, path, or size is invalid.")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != artifact["hash"]:
        raise ResearchResolutionError(
            "A research artifact hash changed; collect or review the evidence again."
        )
    return artifact, content


def validate_research(
    connection, *, root: Path, snapshot: dict[str, Any], research_run_id: str
) -> dict[str, Any]:
    """Validate a completed report, exact conclusions, persisted citations and actual file hashes."""
    research = ResearchRepository(connection).get_run(research_run_id)
    if any(research[key] != snapshot[key] for key in ("projectId", "threadId", "workspaceId")):
        raise ResearchResolutionError(
            "Research must belong to this project, thread and ProductOwner workspace."
        )
    if (
        research["status"] != "research_ready"
        or not research["completedAt"]
        or research["conflictCount"] != 0
        or research["citationCheck"].get("valid") is not True
    ):
        raise ResearchResolutionError("Research is unfinished, conflicting, or has invalid citations.")
    agent = AgentsRepository(connection).get_agent_run(str(research["agentRunId"] or ""))
    job = JobsRepository(connection).get_job(str(research["jobId"] or ""))
    provenance = agent["input"].get("metadata") or {}
    if (
        agent["status"] != "completed"
        or job["status"] != "completed"
        or agent["projectId"] != snapshot["projectId"]
        or job["projectId"] != snapshot["projectId"]
        or agent["jobId"] != job["id"]
        or any(
            provenance.get(key) != snapshot[key]
            for key in ("threadId", "messageId", "loopId", "productOwnerOutputId")
        )
    ):
        raise ResearchResolutionError("Research lacks completed, exact loop/message/output provenance.")
    evidence = EvidenceRepository(connection)
    package = evidence.get_evidence_package(str(research["evidencePackageId"] or ""))
    if package["projectId"] != snapshot["projectId"] or package["workspaceId"] != snapshot["workspaceId"]:
        raise ResearchResolutionError("Research evidence package does not match this workspace.")
    report_artifact, report_content = _artifact_bytes(
        evidence,
        research["reportArtifactId"],
        root=root,
        project_id=snapshot["projectId"],
        evidence_id=package["id"],
        kind="research_report",
    )
    report = json.loads(report_content)
    findings = [
        row_to_research_finding(row)
        for row in connection.execute(
            "SELECT * FROM research_findings WHERE research_run_id=? AND finding_type='technical_decision'",
            (research_run_id,),
        ).fetchall()
    ]
    hashes = {report_artifact["id"]: report_artifact["hash"]}
    adopted = []
    for decision in snapshot["decisions"]:
        matches = [item for item in findings if _text(item["payload"].get("title")) == decision["title"]]
        if len(matches) != 1 or _text(matches[0]["payload"].get("decision")) != decision["decision"]:
            raise ResearchResolutionError(
                f"Research needs the exact accepted technical decision: {decision['title']}."
            )
        finding = matches[0]
        if (
            finding["projectId"] != snapshot["projectId"]
            or finding["threadId"] != snapshot["threadId"]
            or finding["payload"] not in report.get("technicalDecisions", [])
        ):
            raise ResearchResolutionError("The technical finding differs from the verified research report.")
        citations = finding["citations"]
        if not citations or citations != finding["payload"].get("sourceCitations"):
            raise ResearchResolutionError("Every technical decision needs persisted trusted citations.")
        for citation in citations:
            source = connection.execute(
                "SELECT * FROM research_sources WHERE id=?", (citation.get("sourceId"),)
            ).fetchone()
            if (
                source is None
                or source["trust_level"] not in TRUST_LEVELS
                or source["project_id"] != snapshot["projectId"]
                or source["thread_id"] != snapshot["threadId"]
                or source["agent_run_id"] != agent["id"]
                or source["evidence_package_id"] != package["id"]
                or source["id"] not in finding["sourceIds"]
                or any(
                    citation.get(key) != source[column]
                    for key, column in (
                        ("artifactId", "artifact_id"),
                        ("hash", "content_hash"),
                        ("url", "url"),
                        ("trustLevel", "trust_level"),
                        ("fetchedAt", "fetched_at"),
                    )
                )
            ):
                raise ResearchResolutionError(
                    "A technical citation is untrusted or no longer matches its persisted source."
                )
            artifact, _ = _artifact_bytes(
                evidence,
                source["artifact_id"],
                root=root,
                project_id=snapshot["projectId"],
                evidence_id=package["id"],
                kind="research_source",
            )
            if artifact["hash"] != source["content_hash"]:
                raise ResearchResolutionError("The source and artifact hashes disagree.")
            hashes[artifact["id"]] = artifact["hash"]
        adopted.append(
            {
                "decisionId": decision["id"],
                "findingId": finding["id"],
                "findingHash": _digest(finding),
                "citations": citations,
            }
        )
    return {
        "researchRunId": research_run_id,
        "evidencePackageId": package["id"],
        "reportArtifactId": report_artifact["id"],
        "artifactHashes": hashes,
        "findings": adopted,
    }


def research_candidates(
    connection, *, root: Path, loop: dict[str, Any], anchor: dict[str, Any]
) -> list[dict[str, Any]]:
    """Bounded, read-only choices; execution always revalidates every selected record."""
    current = resolution_snapshot(connection, loop)
    rows = connection.execute(
        "SELECT id,query,completed_at FROM research_runs WHERE project_id=? AND thread_id=? AND workspace_id=? "
        "ORDER BY created_at DESC LIMIT 12",
        (current["projectId"], current["threadId"], current["workspaceId"]),
    ).fetchall()
    candidates = []
    for row in rows:
        label = f"{_text(row['query'])[:160] or 'Research report'} — {row['completed_at'] or 'Unfinished'}"
        candidate = {"researchRunId": row["id"], "label": label, "eligible": False}
        try:
            if current != anchor:
                raise ResearchResolutionError(
                    "The decision snapshot changed; review this research gate again."
                )
            validate_research(connection, root=root, snapshot=current, research_run_id=row["id"])
            candidate["eligible"] = True
        except (ResearchResolutionError, KeyError, OSError, ValueError, TypeError) as error:
            candidate["reason"] = str(error)
        candidates.append(candidate)
    return candidates


def hydrate_research_run(coordinator, run, *, loop: dict[str, Any], receipt: dict[str, Any]) -> None:
    """Restore only trusted persisted PO fields; do not rerun inference or persist decisions twice."""
    from local_control_center.process_supervision.context import CURRENT_EXECUTION
    from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
    from local_control_center.remediations.service import BlockerRemediationService

    durable = loop["context"]["durableRun"]
    snapshot = receipt["snapshot"]
    output = coordinator.discovery.get_product_owner_output(snapshot["productOwnerOutputId"])
    run.loop = loop
    run.thread_id = snapshot["threadId"]
    run.thread = durable["thread"]
    run.message_text = durable["message"]
    run.resolved_title = loop["title"]
    run.effective_root = coordinator.root
    source_message = ThreadsRepository(coordinator.connection).get_message(snapshot["messageId"])
    if source_message["threadId"] != snapshot["threadId"]:
        raise ResearchResolutionError("The research source message belongs to another thread.")
    run.request_meta = BlockerRemediationService(coordinator.connection)._reseal_operator_cost_decision(
        strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta")),
        project_id=run.project_id,
        source_message=source_message,
    )
    # The old job remains provenance in requestMeta, never the identity of this continuation.
    run.request_meta.pop("jobId", None)
    run.request_meta.pop("executionId", None)
    context = CURRENT_EXECUTION.get()
    if context is not None and context.project_id == run.project_id and context.execution_id:
        run.request_meta["jobId"] = context.execution_id
    run.request_meta.update({"threadId": snapshot["threadId"], "messageId": snapshot["messageId"]})
    run.loop = coordinator.repository.update_loop_context(
        loop["id"],
        context={
            **loop["context"],
            **coordinator._durable_run_patch(loop, {"requestMeta": run.request_meta}),
        },
    )
    run.plan_only = bool(durable.get("planOnly") or run.request_meta.get("planOnly"))
    run.product_owner_context = dict(durable["productOwner"])
    run.output = {
        key: output[key]
        for key in (
            "status",
            "summary",
            "confidence",
            "questions",
            "assumptions",
            "decisions",
            "productBriefPatch",
            "epics",
            "userStories",
            "risks",
            "recommendedNextAction",
        )
    }
    for key in ("autonomy", "questionSelection"):
        if key in output["metadata"]:
            run.output[key] = output["metadata"][key]
    run.product_owner_output_record = output
    run.product_owner_status = output["status"]
    run.product_owner_result = {
        "status": output["status"],
        "output": run.output,
        "reason": run.product_owner_context.get("reason") or output["summary"],
    }
    run.product_owner_context.setdefault("reason", run.product_owner_result["reason"])
    run.product_owner_workspace = WorkspacesRepository(
        coordinator.connection, root=coordinator.root
    ).get_workspace(snapshot["workspaceId"])
    run.product_owner_task_id = run.product_owner_workspace["taskId"]
    run.assessment_result = durable.get("assessment") or None
    run.preferred_runtime = snapshot["executionOptions"].get("preferredRuntime")
    run.qa_commands = snapshot["executionOptions"].get("qaCommands")
    existing = [
        epic
        for epic in coordinator.backlog.list_epics(run.project_id)
        if (epic.get("metadata") or {}).get("productOwnerOutputId") == output["id"]
    ]
    if existing:
        run.product_owner_result["epics"] = [
            {
                "epic": epic,
                "stories": [
                    {
                        "story": story,
                        "acceptanceCriteria": coordinator.backlog.list_acceptance_criteria(story["id"]),
                    }
                    for story in coordinator.backlog.list_user_stories(run.project_id, epic["id"])
                ],
            }
            for epic in existing
        ]
    run.brief = coordinator.discovery.get_product_brief(snapshot["briefId"])
    run.po_artifact_ids = run.product_owner_context.get("artifactIds") or []
    evidence_id = run.product_owner_context.get("evidencePackageId") or receipt["evidencePackageId"]
    run.po_evidence = coordinator.evidence.get_evidence_package(evidence_id)
    run.evidence_ids = list(
        dict.fromkeys([*(durable.get("evidencePackageIds") or []), receipt["evidencePackageId"]])
    )
    run.research_resolution = receipt
