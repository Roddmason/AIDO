"""Coordinator for AIDO self-improvement work.

It converts proposed changes into auditable Product Loop/backlog/workspace/workflow records.
The source installation is never edited here: changes are only represented as isolated
workspaces and queued PR-oriented workflows that still require their normal evidence gates.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.workflows.repository import WorkflowsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .repository import SelfImprovementRepository, new_proposal_id

SELF_IMPROVEMENT_PROJECT_NAME = "AIDO Self-Improvement"
SELF_IMPROVEMENT_PROJECT_PURPOSE = "aido_self_improvement"
SELF_IMPROVEMENT_AGENT_ID = "aido-self-improvement"


class SelfImprovementCoordinator:
    """Application service for self-improvement proposals, lessons and performance evidence."""

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = Path(root)
        self.repository = SelfImprovementRepository(connection)
        self.projects = ProjectsRepository(connection)
        self.backlog = BacklogRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.jobs = JobsRepository(connection)
        self.events = EventBus(connection)
        self.workflows = WorkflowsRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=self.root)

    def ensure_self_improvement_project(self) -> dict[str, Any]:
        """Return the dedicated logical project for AIDO self-improvement, creating it if needed."""
        existing = self.find_self_improvement_project()
        if existing:
            return existing
        project_path = self.root / ".tmp" / "aido-self-improvement"
        project = self.projects.create_project(
            name=SELF_IMPROVEMENT_PROJECT_NAME,
            path=project_path,
            template_id="other",
            create_directory=True,
            source="self_improvement",
            metadata={
                "purpose": SELF_IMPROVEMENT_PROJECT_PURPOSE,
                "writePolicy": "proposals_only_isolated_workspaces_required",
                "runningInstallationAutoModify": False,
            },
        )
        project.pop("_created", None)
        return project

    def find_self_improvement_project(self) -> dict[str, Any] | None:
        """Return the self-improvement project if it exists without creating it."""
        for project in self.projects.list_projects():
            if (project.get("metadata") or {}).get("purpose") == SELF_IMPROVEMENT_PROJECT_PURPOSE:
                return project
        return None

    def _source_project(self, project_id: str) -> dict[str, Any]:
        project = self.projects.get_project(project_id)
        source_path = Path(project["path"])
        if not source_path.exists() or not source_path.is_dir():
            raise ValueError(f"Source project path does not exist: {project['path']}")
        return project

    def _validate_evidence_packages(
        self, evidence_package_ids: list[str], *, source_project_id: str, self_project_id: str
    ) -> list[dict[str, Any]]:
        packages: list[dict[str, Any]] = []
        for evidence_id in evidence_package_ids:
            package = self.evidence.get_evidence_package(evidence_id)
            if package["projectId"] not in {source_project_id, self_project_id}:
                raise ValueError(
                    f"Evidence package {evidence_id} is not scoped to the source or self-improvement project."
                )
            packages.append(package)
        return packages

    def propose_change(
        self,
        *,
        source_project_id: str,
        title: str,
        summary: str,
        proposed_by: str = "operator",
        qa_commands: list[list[str]] | None = None,
        target_paths: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert a proposed AIDO change into goal, story, workspace and PR-oriented workflow records."""
        source_project = self._source_project(source_project_id)
        self_project = self.ensure_self_improvement_project()
        clean_title = str(redact_secrets(title or "")).strip()
        clean_summary = str(redact_secrets(summary or "")).strip()
        if not clean_title:
            raise ValueError("Self-improvement proposal title is required.")
        if not clean_summary:
            raise ValueError("Self-improvement proposal summary is required.")
        clean_qa_commands = [
            [str(part) for part in command if str(part).strip()] for command in (qa_commands or [])
        ]
        clean_qa_commands = [command for command in clean_qa_commands if command]
        clean_target_paths = [str(path).strip() for path in (target_paths or []) if str(path).strip()]
        proposal_id = new_proposal_id()
        shared_metadata = {
            "proposalId": proposal_id,
            "sourceProjectId": source_project["id"],
            "sourceProjectPath": source_project["path"],
            "selfProjectId": self_project["id"],
            "proposedBy": proposed_by,
            "autoExecute": False,
            "writePolicy": "isolated_workspace_only",
        }
        goal = ProductLoopCoordinator(self.connection).start(
            project_id=self_project["id"],
            title=clean_title,
            context={"selfImprovement": shared_metadata, "summary": clean_summary},
            actor=proposed_by,
            reason="AIDO self-improvement goal recorded from a proposed change.",
        )
        epic = self.backlog.create_epic(
            {
                "projectId": self_project["id"],
                "title": "AIDO Self-Improvement",
                "description": "Safe improvements to AIDO itself, routed through isolated workspaces and PRs.",
                "status": "active",
                "priority": "high",
                "owner": "operator",
                "metadata": shared_metadata,
            }
        )
        story = self.backlog.create_user_story(
            {
                "projectId": self_project["id"],
                "epicId": epic["id"],
                "title": clean_title,
                "asA": "AIDO operator",
                "iWant": clean_summary,
                "soThat": "AIDO improves through reviewed, test-backed changes instead of live mutation.",
                "description": clean_summary,
                "status": "ready",
                "priority": "high",
                "businessValue": "high",
                "owner": proposed_by,
                "acceptanceCriteria": [
                    "The proposed change is implemented in an isolated workspace.",
                    "The requested QA commands complete before promotion.",
                    "The proposal keeps audit links to the source project and generated workflow.",
                ],
                "metadata": shared_metadata,
            }
        )
        task = self.backlog.create_agent_task(
            {
                "projectId": self_project["id"],
                "storyId": story["id"],
                "title": f"Implement: {clean_title}",
                "description": clean_summary,
                "role": "developer",
                "category": "self_improvement",
                "status": "todo",
                "priority": "high",
                "metadata": {
                    **shared_metadata,
                    "qaCommands": clean_qa_commands,
                    "targetPaths": clean_target_paths,
                },
            }
        )
        branch_name = f"aido/self-improvement/{proposal_id.removeprefix('self-improvement-proposal-')[:8]}"
        workspace = self.workspaces.allocate_workspace(
            project_id=source_project["id"],
            task_id=task["id"],
            agent_id=SELF_IMPROVEMENT_AGENT_ID,
            reason="AIDO self-improvement proposal workspace.",
            isolation_type="git_worktree",
            branch_name=branch_name,
        )
        workflow_metadata = {
            "selfImprovement": {
                **shared_metadata,
                "storyId": story["id"],
                "taskId": task["id"],
                "workspaceId": workspace["id"],
            },
            "issueToPr": {
                "issueText": clean_summary,
                "qaCommands": clean_qa_commands,
                "targetPaths": clean_target_paths,
                "createPullRequest": True,
                "autoExecute": False,
            },
        }
        workflow = self.workflows.create_workflow(
            project_id=source_project["id"],
            kind="issue_to_pr",
            title=clean_title,
            metadata=workflow_metadata,
        )
        proposal = self.repository.create_proposal(
            {
                "id": proposal_id,
                "selfProjectId": self_project["id"],
                "sourceProjectId": source_project["id"],
                "title": clean_title,
                "summary": clean_summary,
                "status": "queued_for_pr",
                "goalLoopId": goal["id"],
                "epicId": epic["id"],
                "storyId": story["id"],
                "taskId": task["id"],
                "workspaceId": workspace["id"],
                "workflowId": workflow["id"],
                "targetPaths": clean_target_paths,
                "qaCommands": clean_qa_commands,
                "metadata": {
                    **redact_secrets(metadata or {}),
                    "sourceProjectPath": source_project["path"],
                    "workspacePath": workspace["path"],
                    "workflowKind": workflow["kind"],
                    "runningInstallationAutoModify": False,
                },
            }
        )
        audit = self.events.record_audit(
            project_id=self_project["id"],
            action="self_improvement.proposal.create",
            actor=proposed_by,
            target=proposal["id"],
            payload={
                "sourceProjectId": source_project["id"],
                "storyId": story["id"],
                "taskId": task["id"],
                "workspaceId": workspace["id"],
                "workflowId": workflow["id"],
            },
        )
        self.events.record_event(
            project_id=self_project["id"],
            event_type="self_improvement.proposal.created",
            payload={"proposalId": proposal["id"], "workflowId": workflow["id"]},
        )
        return {
            "selfImprovementProject": self_project,
            "sourceProject": source_project,
            "proposal": proposal,
            "goal": goal,
            "epic": epic,
            "story": story,
            "task": task,
            "workspace": workspace,
            "workflow": workflow,
            "auditEvent": audit,
        }

    def record_lesson(
        self,
        *,
        source_project_id: str,
        scope: str,
        title: str,
        lesson: str,
        evidence_package_ids: list[str] | None = None,
        proposal_id: str | None = None,
        proposed_by: str = "operator",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a lesson; global lessons require evidence and create a promotion approval request."""
        source_project = self._source_project(source_project_id)
        self_project = self.ensure_self_improvement_project()
        normalized_scope = str(scope or "").strip().lower()
        if normalized_scope not in {"project", "global"}:
            raise ValueError("Lesson scope must be project or global.")
        clean_title = str(redact_secrets(title or "")).strip()
        clean_lesson = str(redact_secrets(lesson or "")).strip()
        if not clean_title or not clean_lesson:
            raise ValueError("Lesson title and body are required.")
        evidence_ids = [str(item) for item in evidence_package_ids or [] if str(item).strip()]
        if normalized_scope == "global" and not evidence_ids:
            raise ValueError("Global lessons require at least one evidence package.")
        self._validate_evidence_packages(
            evidence_ids, source_project_id=source_project["id"], self_project_id=self_project["id"]
        )
        if proposal_id:
            proposal = self.repository.get_proposal(proposal_id)
            if proposal["sourceProjectId"] != source_project["id"]:
                raise ValueError("Lesson proposal is not scoped to the source project.")
        status = "pending_promotion" if normalized_scope == "global" else "recorded"
        promotion_status = "approval_required" if normalized_scope == "global" else "not_requested"
        lesson_record = self.repository.create_lesson(
            {
                "selfProjectId": self_project["id"],
                "sourceProjectId": source_project["id"],
                "proposalId": proposal_id,
                "scope": normalized_scope,
                "title": clean_title,
                "lesson": clean_lesson,
                "status": status,
                "promotionStatus": promotion_status,
                "evidencePackageIds": evidence_ids,
                "metadata": redact_secrets(metadata or {}),
            }
        )
        promotion_job = None
        promotion_action = None
        if normalized_scope == "global":
            job_result = self.jobs.create_job(
                project_id=self_project["id"],
                kind="self_improvement.promote_global_lesson",
                status="approval_required",
                payload={
                    "lessonId": lesson_record["id"],
                    "sourceProjectId": source_project["id"],
                    "evidenceRefs": evidence_ids,
                    "approvalRequired": False,
                },
            )
            promotion_job = job_result["job"]
            promotion_action = self.jobs.create_action_request(
                job_id=promotion_job["id"],
                project_id=self_project["id"],
                action_type="self_improvement.promote_global_lesson",
                risk_level="high",
                command="promote global self-improvement lesson",
                payload={
                    "lessonId": lesson_record["id"],
                    "sourceProjectId": source_project["id"],
                    "evidenceRefs": evidence_ids,
                },
                reason="Global self-improvement lessons require evidence-backed promotion approval.",
            )
            lesson_record = self.repository.update_lesson_promotion_request(
                lesson_record["id"],
                job_id=promotion_job["id"],
                action_request_id=promotion_action["id"],
            )
        audit = self.events.record_audit(
            project_id=self_project["id"],
            action="self_improvement.lesson.record",
            actor=proposed_by,
            target=lesson_record["id"],
            payload={
                "scope": normalized_scope,
                "evidencePackageIds": evidence_ids,
                "promotionActionRequestId": lesson_record["promotionActionRequestId"],
            },
        )
        return {
            "lesson": lesson_record,
            "promotionJob": promotion_job,
            "promotionActionRequest": promotion_action,
            "auditEvent": audit,
        }

    def promote_global_lesson(
        self, lesson_id: str, *, actor: str = "operator", reason: str
    ) -> dict[str, Any]:
        """Promote a global lesson only after its action request was explicitly approved."""
        lesson = self.repository.get_lesson(lesson_id)
        if lesson["scope"] != "global":
            raise ValueError("Only global lessons can be promoted.")
        if not lesson["evidencePackageIds"]:
            raise ValueError("Global lesson promotion requires evidence.")
        action_id = lesson["promotionActionRequestId"]
        if not action_id:
            raise ValueError("Global lesson promotion requires a promotion action request.")
        action = self.jobs.get_action_request(action_id)
        if action["status"] != "approved":
            raise ValueError("Global lesson promotion requires an approved promotion action request.")
        metadata = {
            **lesson["metadata"],
            "promotionReason": str(redact_secrets(reason or "")),
            "approvedActionRequestId": action_id,
            "promotionEvidencePackageIds": lesson["evidencePackageIds"],
        }
        promoted = self.repository.promote_lesson(
            lesson_id,
            actor=actor,
            approved_action_request_id=action_id,
            metadata=metadata,
        )
        audit = self.events.record_audit(
            project_id=lesson["selfProjectId"],
            action="self_improvement.lesson.promote",
            actor=actor,
            target=lesson_id,
            payload={
                "reason": reason,
                "approvedActionRequestId": action_id,
                "evidencePackageIds": lesson["evidencePackageIds"],
            },
        )
        return {"lesson": promoted, "auditEvent": audit}

    def record_performance(
        self,
        *,
        source_project_id: str,
        metric_name: str,
        value: float,
        unit: str,
        evidence_package_id: str,
        proposal_id: str | None = None,
        baseline_value: float | None = None,
        target_value: float | None = None,
        recorded_by: str = "operator",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record measured performance for a proposal or source project, backed by evidence."""
        source_project = self._source_project(source_project_id)
        self_project = self.ensure_self_improvement_project()
        if not str(evidence_package_id or "").strip():
            raise ValueError("Performance records require evidence.")
        self._validate_evidence_packages(
            [evidence_package_id], source_project_id=source_project["id"], self_project_id=self_project["id"]
        )
        if proposal_id:
            proposal = self.repository.get_proposal(proposal_id)
            if proposal["sourceProjectId"] != source_project["id"]:
                raise ValueError("Performance proposal is not scoped to the source project.")
        clean_metric = str(redact_secrets(metric_name or "")).strip()
        if not clean_metric:
            raise ValueError("Performance metric name is required.")
        record = self.repository.create_performance_record(
            {
                "selfProjectId": self_project["id"],
                "sourceProjectId": source_project["id"],
                "proposalId": proposal_id,
                "metricName": clean_metric,
                "value": float(value),
                "unit": str(redact_secrets(unit or "")),
                "baselineValue": baseline_value,
                "targetValue": target_value,
                "evidencePackageId": evidence_package_id,
                "recordedBy": recorded_by,
                "metadata": metadata or {},
            }
        )
        audit = self.events.record_audit(
            project_id=self_project["id"],
            action="self_improvement.performance.record",
            actor=recorded_by,
            target=record["id"],
            payload={
                "metricName": record["metricName"],
                "proposalId": proposal_id,
                "evidencePackageId": evidence_package_id,
            },
        )
        return {"performanceRecord": record, "auditEvent": audit}

    def state(self, *, source_project_id: str | None = None) -> dict[str, Any]:
        """Return the self-improvement aggregate state."""
        self_project = self.find_self_improvement_project()
        return {
            "selfImprovementProject": self_project,
            "proposals": self.repository.list_proposals(source_project_id=source_project_id),
            "lessons": self.repository.list_lessons(source_project_id=source_project_id),
            "performanceRecords": self.repository.list_performance_records(
                source_project_id=source_project_id
            ),
        }
