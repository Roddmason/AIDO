"""State machine that drives an issue from agent-generated patch to merged pull request.

Orchestrates the issue_to_patch vertical slice and its downstream integration transitions:
select a runtime, run the developer/QA agents in an isolated workspace, build a contract-checked
evidence package, then gate the manual transitions evidence_ready -> approved_for_integration
-> promoted_to_branch -> pr_created. Each transition validates linked evidence/approvals and
re-checks QA and security before mutating git, so no patch reaches a branch or PR unapproved;
git operations refuse force-push and direct edits to main. Reused by the issue_to_pr runner.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import subprocess
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.developer_agent_contract import is_developer_runtime
from local_control_center.agents.qa_agent import QAAgentRunner, qa_verdict_allows_completion
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_registry import issue_to_patch_prompt
from local_control_center.agents.runtime_selection import RUNTIME_UNAVAILABLE_STATUS, display_command
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import (
    artifact_hashes,
    artifact_records_from_ids,
    artifact_ref,
)
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.security_policy.git_command_runner import git_available
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now
from local_control_center.workflows.evidence_artifacts import (
    _artifact_content_bytes,
    _dedupe_artifact_records,
    _diff_summary,
    _final_diff_refs,
    _security_findings_from_policy,
    _validate_patch_artifact,
    _validate_qa_for_patch_approval,
    _validate_security_findings,
    _write_git_status_artifacts,
    _write_json_evidence_artifact,
)
from local_control_center.workflows.github_pull_requests import (
    GitHubPullRequestConfigError,
    create_github_pull_request,
    github_pull_request_config_from_env,
)
from local_control_center.workflows.repository import ISSUE_TO_PATCH_STEPS, WorkflowsRepository
from local_control_center.workspaces_projects.git_worktrees import (
    capture_git_diff,
    git_remote_exists,
    push_branch_to_remote,
    run_brokered_git,
    slugify_branch_segment,
)
from local_control_center.workspaces_projects.repository import WorkspacesRepository

CLI_RUNTIME_IDS = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
APPROVED_FOR_INTEGRATION_STATUS = "approved_for_integration"
PROMOTED_TO_BRANCH_STATUS = "promoted_to_branch"
PROMOTION_FAILED_STATUS = "promotion_failed"
PR_CREATED_STATUS = "pr_created"
PR_FAILED_STATUS = "pr_failed"
PR_UNAVAILABLE_STATUS = "pr_unavailable"
PATCH_PROMOTER_PROFILE_ID = "aido_patch_promoter"
PULL_REQUEST_PROFILE_ID = "aido_pull_request_creator"
ISSUE_TO_PATCH_APPROVAL_ACTION = "workflow.issue_to_patch.approve_patch"
ISSUE_TO_PR_APPROVAL_ACTION = "workflow.issue_to_pr.approve_issue_to_pr"
TERMINAL_STATUSES = {"completed", RUNTIME_UNAVAILABLE_STATUS, "qa_failed", "evidence_ready", "failed"}
CLI_SYNTAX_ERROR_MARKERS = (
    "unknown option",
    "unknown flag",
    "unrecognized option",
    "unrecognized arguments",
    "invalid option",
    "did you mean",
    "usage:",
)


def _runtime_mode(runtime_id: str) -> str:
    if runtime_id == "manual":
        return "manual"
    if runtime_id == "ollama":
        return "ollama"
    if runtime_id in CLI_RUNTIME_IDS:
        return "cli"
    return "hybrid"


def _select_runtime(
    statuses: list[dict[str, Any]],
    *,
    preferred_runtime: str | None,
) -> dict[str, Any]:
    if preferred_runtime:
        return next(
            (status for status in statuses if status["id"] == preferred_runtime),
            {
                "id": preferred_runtime,
                "kind": "unknown",
                "displayName": preferred_runtime,
                "configured": False,
                "available": False,
                "executable": False,
                "requiresApproval": True,
                "reason": f"Runtime provider is not catalogued: {preferred_runtime}",
                "capabilities": [],
                "safety": {
                    "workspaceBound": True,
                    "shell": False,
                    "structuredArgv": True,
                    "network": "unknown",
                },
            },
        )
    return next(
        (status for status in statuses if is_developer_runtime(status)),
        {
            "id": "unresolved",
            "kind": "unknown",
            "displayName": "No executable runtime",
            "configured": False,
            "available": False,
            "executable": False,
            "requiresApproval": True,
            "reason": "No executable DeveloperAgent runtime is configured for issue_to_patch.",
            "capabilities": [],
            "safety": {"workspaceBound": True, "shell": False, "structuredArgv": True, "network": "unknown"},
        },
    )


def _execution_result_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    raw_output = f"{execution_result.get('stderr') or ''}\n{execution_result.get('stdout') or ''}".lower()
    syntax_error = bool(
        tool_call.get("status") == "failed"
        and execution_result.get("returnCode") not in {0, None}
        and any(marker in raw_output for marker in CLI_SYNTAX_ERROR_MARKERS)
    )
    if syntax_error:
        return {
            "status": RUNTIME_UNAVAILABLE_STATUS,
            "toolCallId": tool_call.get("id"),
            "execution": payload.get("execution"),
            "returnCode": execution_result.get("returnCode"),
            "timedOut": bool(execution_result.get("timedOut", False)),
            "blocked": bool(execution_result.get("blocked", False)),
            "reason": "CLI syntax mismatch for configured issue_to_patch runtime: "
            + str(execution_result.get("stderr") or execution_result.get("stdout") or "").strip()[:500],
            "stdoutArtifactId": execution_result.get("stdoutArtifactId"),
            "stderrArtifactId": execution_result.get("stderrArtifactId"),
        }
    return {
        "status": "completed" if tool_call.get("status") == "completed" else "failed",
        "toolCallId": tool_call.get("id"),
        "execution": payload.get("execution"),
        "returnCode": execution_result.get("returnCode"),
        "timedOut": bool(execution_result.get("timedOut", False)),
        "blocked": bool(execution_result.get("blocked", False)),
        "reason": execution_result.get("reason") or payload.get("decisionReason"),
        "stdoutArtifactId": execution_result.get("stdoutArtifactId"),
        "stderrArtifactId": execution_result.get("stderrArtifactId"),
    }


def _developer_instruction_for_issue(
    *,
    title: str,
    issue_text: str,
    target_path: str | None,
) -> str:
    instruction = issue_to_patch_prompt(title=title, issue_text=issue_text)
    if target_path:
        instruction = f"{instruction}\nTarget path constraint:\n{target_path}\n"
    return instruction


def _project_path(connection: sqlite3.Connection, project_id: str) -> Path:
    row = connection.execute("SELECT path FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise KeyError(f"Project not found: {project_id}")
    return Path(row["path"])


def _approved_evidence_base_commit(evidence: dict[str, Any], workspace: dict[str, Any]) -> str:
    for diff_ref in evidence.get("diffRefs") or []:
        if not isinstance(diff_ref, dict) or diff_ref.get("kind") != "git_diff":
            continue
        head_commit = str(diff_ref.get("headCommit") or "").strip()
        if head_commit:
            return head_commit
    workspace_manifest = (workspace.get("metadata") or {}).get("workspaceManifest") or {}
    source_commit = str(workspace_manifest.get("sourceCommit") or "").strip()
    if source_commit:
        return source_commit
    git_worktree = (workspace.get("metadata") or {}).get("gitWorktree") or {}
    source_commit = str(git_worktree.get("sourceCommit") or "").strip()
    if source_commit:
        return source_commit
    raise ValueError("promote_patch_to_branch requires a base commit captured in approved evidence.")


def _promotion_branch_name(*, requested: str | None, workflow: dict[str, Any], run_id: str) -> str:
    if requested and requested.strip():
        return requested.strip()
    title = slugify_branch_segment(str(workflow.get("title") or "issue-to-patch"))
    suffix = run_id.removeprefix("workflow-run-")[:8] or uuid.uuid4().hex[:8]
    return f"aido/promote/{title}/{suffix}"


def _promotion_qa_commands(
    original_evidence: dict[str, Any], requested: list[list[str]] | None
) -> list[list[str]]:
    if requested is not None:
        return requested
    commands: list[list[str]] = []
    for result in original_evidence.get("testResults") or []:
        argv = result.get("argv") if isinstance(result, dict) else None
        if isinstance(argv, list) and argv and all(isinstance(item, str) and item for item in argv):
            commands.append([str(item) for item in argv])
    return commands


def _git_operation_result(
    args: list[str],
    *,
    cwd: Path,
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
    workspace_id: str,
    task_id: str,
    git_operation: str,
) -> dict[str, Any]:
    display = subprocess.list2cmdline(["git", *args])
    result = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_path=cwd,
        cwd=cwd,
        args=args,
        task_id=task_id,
        git_operation=git_operation,
    )
    return_code = result["returnCode"]
    trace = result.get("trace") or {}
    return {
        "command": display,
        "argv": ["git", *args],
        "cwd": str(cwd),
        "returnCode": return_code,
        "stdout": (result.get("stdout") or "")[:4000],
        "stderr": (result.get("stderr") or "")[:4000],
        "status": "passed" if return_code == 0 else str(result.get("status") or "failed"),
        "reason": str(result.get("reason") or ""),
        "toolCallId": trace.get("toolCallId"),
        "toolCallStatus": trace.get("toolCallStatus"),
        "permissionDecisionId": trace.get("permissionDecisionId"),
        "execution": trace.get("execution"),
        "agentRunId": trace.get("agentRunId") or result.get("agentRunId"),
    }


def _approval_reason_summary(
    *,
    approval_actions: list[dict[str, Any]],
    run_metadata: dict[str, Any],
) -> dict[str, str]:
    approved = next((action for action in approval_actions if action.get("status") == "approved"), {})
    return {
        "integrationApprovalReason": str(run_metadata.get("reason") or "").strip(),
        "approvalActionReason": str(approved.get("decisionReason") or approved.get("reason") or "").strip(),
    }


def _pr_qa_summary_lines(results: list[dict[str, Any]]) -> list[str]:
    if not results:
        return ["- No QA results were captured in the promotion evidence."]
    lines: list[str] = []
    for result in results:
        command = str(result.get("command") or display_command(result.get("argv") or []) or "qa_command")
        status = str(result.get("status") or "unknown")
        duration = result.get("durationMs")
        duration_text = f" ({duration} ms)" if isinstance(duration, int) else ""
        lines.append(f"- {status}: `{command}`{duration_text}")
    return lines


def _security_findings_lines(security_findings: dict[str, Any]) -> list[str]:
    status = str(security_findings.get("status") or "unknown")
    findings = (
        security_findings.get("findings") if isinstance(security_findings.get("findings"), list) else []
    )
    lines = [f"- status: {status}", f"- findings: {len(findings)}"]
    for index, finding in enumerate(findings[:10], start=1):
        if not isinstance(finding, dict):
            continue
        decision = str(finding.get("decision") or finding.get("status") or "not_reported")
        summary = str(
            finding.get("summary") or finding.get("description") or finding.get("rule") or "finding"
        )
        lines.append(f"- {index}. {decision}: {summary}")
    if len(findings) > 10:
        lines.append(f"- truncated: {len(findings) - 10} additional findings omitted from PR body")
    return lines


def _hash_lines(label: str, hashes: dict[str, str]) -> list[str]:
    if not hashes:
        return [f"- {label}: no artifact hashes recorded"]
    lines = [f"### {label}"]
    for name, value in sorted(hashes.items()):
        lines.append(f"- `{name}`: `{value}`")
    return lines


def _default_pull_request_title(workflow: dict[str, Any], branch_name: str) -> str:
    title = str(workflow.get("title") or "").strip()
    if title:
        return title[:180]
    return f"AIDO patch promotion: {branch_name}"[:180]


def _pull_request_base_branch(*, requested: str | None, promotion_workspace: dict[str, Any]) -> str:
    if requested and requested.strip():
        return requested.strip()
    metadata = promotion_workspace.get("metadata") or {}
    git_worktree = metadata.get("gitWorktree") if isinstance(metadata.get("gitWorktree"), dict) else {}
    source_branch = str(git_worktree.get("sourceBranch") or "").strip()
    if source_branch:
        return source_branch
    workspace_manifest = (
        metadata.get("workspaceManifest") if isinstance(metadata.get("workspaceManifest"), dict) else {}
    )
    manifest_branch = str(workspace_manifest.get("sourceBranch") or "").strip()
    if manifest_branch:
        return manifest_branch
    raise ValueError(
        "create_pull_request requires baseBranch because the promoted workspace did not capture a source branch."
    )


def _build_pull_request_body(
    *,
    approved_evidence: dict[str, Any],
    promotion_evidence: dict[str, Any],
    security_findings: dict[str, Any],
    approval_reason: dict[str, str],
    request_reason: str,
    branch_name: str,
    base_branch: str,
) -> str:
    lines = [
        "# AIDO Promoted Patch",
        "",
        "## Evidence Package",
        f"- approved evidence package id: `{approved_evidence['id']}`",
        f"- promotion evidence package id: `{promotion_evidence['id']}`",
        f"- branch: `{branch_name}`",
        f"- base branch: `{base_branch}`",
        "",
        "## QA Summary",
        *_pr_qa_summary_lines(promotion_evidence.get("testResults") or []),
        "",
        "## Security Findings",
        *_security_findings_lines(security_findings),
        "",
        "## Artifact Hashes",
        *_hash_lines("Approved Evidence", approved_evidence.get("hashes") or {}),
        *_hash_lines("Promotion Evidence", promotion_evidence.get("hashes") or {}),
        "",
        "## Approval Reason",
        f"- integration approval: {approval_reason.get('integrationApprovalReason') or 'not recorded'}",
        f"- approval action: {approval_reason.get('approvalActionReason') or 'not recorded'}",
        f"- PR request: {request_reason}",
    ]
    return "\n".join(lines).strip() + "\n"


def _promotion_status_from_results(
    *,
    apply_check: dict[str, Any],
    apply_result: dict[str, Any] | None,
    qa_results: list[dict[str, Any]],
    diff: dict[str, Any],
) -> tuple[str, str, str]:
    if apply_check["returnCode"] != 0:
        return PROMOTION_FAILED_STATUS, "blocked", "git apply --check failed for the approved patch artifact."
    if not apply_result or apply_result["returnCode"] != 0:
        return PROMOTION_FAILED_STATUS, "blocked", "git apply failed for the approved patch artifact."
    if not diff.get("nameOnly"):
        return PROMOTION_FAILED_STATUS, "blocked", "Promotion applied no auditable file changes."
    if not qa_results:
        return PROMOTION_FAILED_STATUS, "blocked", "Promotion requires QA commands after patch application."
    if any(result.get("status") == "failed" for result in qa_results):
        return PROMOTION_FAILED_STATUS, "failed", "Promotion QA failed after applying the approved patch."
    if any(result.get("status") != "passed" for result in qa_results):
        return PROMOTION_FAILED_STATUS, "blocked", "Promotion QA did not produce a passing verdict."
    if not qa_verdict_allows_completion("passed", qa_results):
        return PROMOTION_FAILED_STATUS, "blocked", "Promotion QA requires real command execution evidence."
    return PROMOTED_TO_BRANCH_STATUS, "passed", "Approved patch was applied to a local branch and QA passed."


def _record_pull_request(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    workspace_id: str | None,
    status: str,
    pull_request: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> None:
    url = str((pull_request or {}).get("htmlUrl") or (pull_request or {}).get("url") or "")
    connection.execute(
        """
        INSERT INTO pull_requests (id, workspace_id, project_id, provider, url, status, metadata, created_at, updated_at)
        VALUES (?, ?, ?, 'github', ?, ?, ?, ?, ?)
        """,
        (
            f"pull-request-{uuid.uuid4()}",
            workspace_id,
            project_id,
            url,
            status,
            json_dumps(redact_secrets(metadata)),
            utc_now(),
            utc_now(),
        ),
    )


def _status_from_developer_result(
    developer_result: dict[str, Any],
    *,
    require_approval: bool,
    evidence_package_valid: bool = True,
) -> tuple[str, str, str]:
    developer_status = str(developer_result.get("status") or "").strip()
    developer_reason = str(developer_result.get("reason") or "").strip()
    evidence = (
        developer_result.get("evidencePackage")
        if isinstance(developer_result.get("evidencePackage"), dict)
        else {}
    )
    qa_verdict = str(evidence.get("qaVerdict") or "blocked").strip() or "blocked"

    if developer_status == "completed" and not evidence:
        return "evidence_ready", "blocked", "Evidence package was not created."
    if not evidence_package_valid:
        return "evidence_ready", "blocked", "Evidence package contract is incomplete or unverifiable."

    if developer_status == "completed":
        if require_approval:
            return "evidence_ready", "needs_human_review", "Patch evidence is ready and requires approval."
        return "completed", "passed", "Patch evidence and QA passed."
    if developer_status == RUNTIME_UNAVAILABLE_STATUS:
        return (
            RUNTIME_UNAVAILABLE_STATUS,
            "blocked",
            developer_reason or "No executable DeveloperAgent runtime was available.",
        )
    if developer_status == "qa_failed":
        return "qa_failed", "failed", developer_reason or "DeveloperAgent QA command failed or was blocked."
    if developer_status == "evidence_ready":
        return (
            "evidence_ready",
            qa_verdict,
            developer_reason or "DeveloperAgent evidence is not ready for completion.",
        )
    if developer_status == "failed":
        return "failed", "failed", developer_reason or "DeveloperAgent runtime execution failed."
    if require_approval and qa_verdict == "needs_human_review":
        return "evidence_ready", "needs_human_review", "Patch evidence is ready and requires approval."
    return (
        "failed",
        "failed",
        developer_reason or f"DeveloperAgent returned unsupported status: {developer_status or 'missing'}.",
    )


def _approval_action_type(workflow: dict[str, Any]) -> str:
    return (
        ISSUE_TO_PR_APPROVAL_ACTION if workflow["kind"] == "issue_to_pr" else ISSUE_TO_PATCH_APPROVAL_ACTION
    )


def _linked_record_ids(
    evidence: dict[str, Any], run_metadata: dict[str, Any], *, missing_message: str
) -> tuple[str, str, str]:
    job_id = str(evidence.get("jobId") or run_metadata.get("jobId") or "")
    agent_run_id = str(evidence.get("agentRunId") or run_metadata.get("agentRunId") or "")
    workspace_id = str(evidence.get("workspaceId") or run_metadata.get("workspaceId") or "")
    if not job_id or not agent_run_id or not workspace_id:
        raise ValueError(missing_message)
    return job_id, agent_run_id, workspace_id


def _qa_agent_run_id(developer_result: dict[str, Any]) -> str:
    return str((developer_result["agentRun"].get("output") or {}).get("qaAgentRunId") or "")


@dataclass(frozen=True)
class _Verdict:
    """Terminal status, QA verdict and reason computed for one workflow transition."""

    status: str
    qa_verdict: str
    reason: str


@dataclass(frozen=True)
class _PatchApprovalContext:
    """Records loaded and validated before an evidence_ready run is approved for integration."""

    workflow: dict[str, Any]
    workflow_run: dict[str, Any]
    run_metadata: dict[str, Any]
    evidence_id: str
    job_id: str
    agent_run_id: str
    agent_run: dict[str, Any]
    workspace: dict[str, Any]
    approved_action: dict[str, Any]


@dataclass(frozen=True)
class _PromotionPlan:
    """Validated inputs of a branch promotion: approved evidence, verified patch and target branch."""

    workflow: dict[str, Any]
    run_metadata: dict[str, Any]
    approved_evidence_id: str
    approved_evidence: dict[str, Any]
    original_job_id: str
    patch_artifact: dict[str, Any]
    patch_content: bytes
    base_commit: str
    target_branch: str
    steps: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class _PromotionAttempt:
    """Everything produced while applying the approved patch in a fresh branch workspace."""

    job: dict[str, Any]
    agent_run: dict[str, Any]
    workspace: dict[str, Any]
    apply_check: dict[str, Any]
    apply_result: dict[str, Any] | None
    qa_summary: dict[str, Any] | None
    diff: dict[str, Any]

    @property
    def qa_results(self) -> list[dict[str, Any]]:
        return (self.qa_summary or {}).get("results") or []

    @property
    def qa_artifact_ids(self) -> list[str]:
        return (self.qa_summary or {}).get("artifactIds") or []

    @property
    def qa_agent_run_id(self) -> Any:
        return ((self.qa_summary or {}).get("agentRun") or {}).get("id")


@dataclass(frozen=True)
class _PullRequestPlan:
    """Validated inputs of a pull request creation from an already promoted branch."""

    workflow: dict[str, Any]
    run_metadata: dict[str, Any]
    promoted_branch_name: str
    promotion_evidence_id: str
    approved_evidence_id: str
    promotion_evidence: dict[str, Any]
    approved_evidence: dict[str, Any]
    promotion_workspace: dict[str, Any]
    base_branch: str
    steps: dict[str, dict[str, Any]]
    workflow_step_id: str | None
    approval_actions: list[dict[str, Any]]
    title: str
    body: str


@dataclass(frozen=True)
class _PullRequestSubmission:
    """Outcome of pushing the promoted branch and asking GitHub to open the pull request."""

    verdict: _Verdict
    github_result: dict[str, Any]
    pull_request: dict[str, Any] | None
    configured: bool


@dataclass(frozen=True)
class _IssueToPatchRun:
    """Records created when an issue_to_patch run starts, shared by the steps of ``run``."""

    payload: dict[str, Any]
    title: str
    issue_text: str
    runtime: dict[str, Any]
    profile_id: str
    workflow: dict[str, Any]
    workflow_run: dict[str, Any]
    steps: dict[str, dict[str, Any]]
    job_id: str

    @property
    def require_approval(self) -> bool:
        return bool(self.payload.get("requireApproval", True))


class IssueToPatchRunner:
    """Runs issue_to_patch and the patch->branch->PR integration transitions for one project.

    Holds the repositories it coordinates (workflows, jobs, agents, workspaces, evidence,
    security) over a shared connection rooted at ``root``. Methods are the workflow transitions;
    they validate the current run status, evidence and approvals before each side effect.
    """

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.workflows = WorkflowsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.agents = AgentsRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)
        self.evidence = EvidenceRepository(connection)
        self.security = SecurityPolicyRepository(connection)

    def _record_transition(
        self,
        *,
        workflow: dict[str, Any],
        run_id: str,
        job_id: str,
        status: str,
        payload: dict[str, Any],
        actor: str,
        severity: str = "info",
    ) -> None:
        """Record the workflow event, bus event and audit entry of one issue_to_patch transition."""
        project_id = workflow["projectId"]
        event_type = f"workflow.issue_to_patch.{status}"
        self.workflows.record_workflow_event(
            workflow_id=workflow["id"],
            workflow_run_id=run_id,
            project_id=project_id,
            event_type=event_type,
            payload=payload,
            severity=severity,
        )
        EventBus(self.connection).record_event(
            project_id=project_id,
            job_id=job_id,
            event_type=event_type,
            payload=payload,
        )
        EventBus(self.connection).record_audit(
            project_id=project_id,
            action=event_type,
            actor=actor,
            target=run_id,
            payload=payload,
        )

    def _approval_actions(
        self, job_id: str, *, action_type: str, run_id: str, evidence_id: str
    ) -> list[dict[str, Any]]:
        """Return the ``action_type`` requests of ``job_id`` aimed at this run and evidence package."""
        return [
            action
            for action in self.jobs.list_action_requests(job_id)
            if action["actionType"] == action_type
            and (action.get("payload") or {}).get("workflowRunId") == run_id
            and (action.get("payload") or {}).get("evidencePackageId") == evidence_id
        ]

    def approve_patch(self, run_id: str, *, reason: str, actor: str = "operator") -> dict[str, Any]:
        """Transition an evidence_ready run to approved_for_integration after revalidating evidence.

        Requires a non-empty reason and an evidence package linked to a job, agent run and
        workspace; re-checks QA before approving. Does not touch git.

        Raises:
            ValueError: if the reason is empty, the run is the wrong kind/status, or the
                evidence/links are missing or do not belong to this run.
        """
        clean_reason = str(redact_secrets(reason or "")).strip()
        if not clean_reason:
            raise ValueError("Approval reason is required.")

        context = self._load_patch_approval_context(run_id)
        evidence, security_findings = self._revalidate_evidence_for_approval(context)
        return self._persist_patch_approval(
            run_id,
            context,
            evidence=evidence,
            security_findings=security_findings,
            reason=clean_reason,
            actor=actor,
        )

    def _load_patch_approval_context(self, run_id: str) -> _PatchApprovalContext:
        """Load the run, evidence links and approved action request an approval depends on."""
        workflow_run = self.workflows.get_workflow_run(run_id)
        workflow = self.workflows.get_workflow(workflow_run["workflowId"])
        if workflow["kind"] != "issue_to_patch":
            raise ValueError("Only issue_to_patch workflow runs can use this approval transition.")
        if workflow_run["status"] == APPROVED_FOR_INTEGRATION_STATUS:
            raise ValueError("issue_to_patch workflow run is already approved for integration.")
        if workflow_run["status"] != "evidence_ready":
            raise ValueError("issue_to_patch approval requires an evidence_ready workflow run.")

        run_metadata = workflow_run.get("metadata") or {}
        evidence_id = str(run_metadata.get("evidencePackageId") or "")
        if not evidence_id:
            evidence_packages = self.evidence.list_evidence_for_workflow_runs([run_id])
            evidence_id = str(evidence_packages[0]["id"]) if evidence_packages else ""
        if not evidence_id:
            raise ValueError("issue_to_patch approval requires an evidence package.")
        evidence = self.evidence.get_evidence_package(evidence_id)
        if evidence["workflowRunId"] != run_id:
            raise ValueError("Evidence package does not belong to this workflow run.")

        job_id, agent_run_id, workspace_id = _linked_record_ids(
            evidence,
            run_metadata,
            missing_message="issue_to_patch approval requires linked job, agent run, and workspace records.",
        )
        self.jobs.get_job(job_id)
        agent_run = self.agents.get_agent_run(agent_run_id)
        workspace = self.workspaces.get_workspace(workspace_id)

        approval_actions = self._approval_actions(
            job_id, action_type=ISSUE_TO_PATCH_APPROVAL_ACTION, run_id=run_id, evidence_id=evidence_id
        )
        if not approval_actions:
            raise ValueError(
                "issue_to_patch approval requires an approved action request for this evidence package."
            )
        approved_action = next(
            (action for action in approval_actions if action["status"] == "approved"), None
        )
        if not approved_action:
            raise ValueError(
                "issue_to_patch approval requires an approved action request before workflow transition."
            )
        return _PatchApprovalContext(
            workflow=workflow,
            workflow_run=workflow_run,
            run_metadata=run_metadata,
            evidence_id=evidence_id,
            job_id=job_id,
            agent_run_id=agent_run_id,
            agent_run=agent_run,
            workspace=workspace,
            approved_action=approved_action,
        )

    def _revalidate_evidence_for_approval(
        self, context: _PatchApprovalContext
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Refresh approval links on the evidence and re-check contract, patch, QA and security."""
        latest_approvals = self.jobs.list_action_requests(context.job_id)
        evidence = self.evidence.update_evidence_links(context.evidence_id, approvals=latest_approvals)
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=True,
            require_workflow_run=True,
        )
        if contract_errors:
            raise ValueError("Evidence package contract is incomplete: " + "; ".join(contract_errors))
        artifacts = self.evidence.list_artifacts(context.evidence_id)
        _validate_patch_artifact(evidence, artifacts)
        _validate_qa_for_patch_approval(evidence, action_approved=True)
        security_findings = _validate_security_findings(evidence, artifacts)
        return evidence, security_findings

    def _persist_patch_approval(
        self,
        run_id: str,
        context: _PatchApprovalContext,
        *,
        evidence: dict[str, Any],
        security_findings: dict[str, Any],
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        """Write the approved_for_integration transition across run, workflow, job and agent run."""
        transition_payload = {
            "status": APPROVED_FOR_INTEGRATION_STATUS,
            "reason": reason,
            "approvedAt": utc_now(),
            "approvedBy": actor,
            "actionRequestId": context.approved_action["id"],
            "evidencePackageId": context.evidence_id,
            "jobId": context.job_id,
            "agentRunId": context.agent_run_id,
            "workspaceId": context.workspace["id"],
            "securityFindingsStatus": security_findings.get("status"),
        }
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=APPROVED_FOR_INTEGRATION_STATUS,
            metadata={**context.run_metadata, **transition_payload},
            completed=False,
            clear_completed=True,
        )
        workflow = self.workflows.update_workflow_status(
            context.workflow["id"],
            status=APPROVED_FOR_INTEGRATION_STATUS,
            reason=reason,
        )
        job = self.jobs.update_job_status(
            context.job_id,
            status="approved",
            metadata=transition_payload,
        )
        agent_output = dict(context.agent_run.get("output") or {})
        agent_output.update(
            {
                "verdict": APPROVED_FOR_INTEGRATION_STATUS,
                "summary": reason,
                "approvedForIntegration": True,
                "approvalActionRequestId": context.approved_action["id"],
                "evidence_refs": sorted({*agent_output.get("evidence_refs", []), context.evidence_id}),
            }
        )
        agent_run = self.agents.update_agent_run_status(
            context.agent_run_id, status="approved", output_payload=agent_output
        )
        self._complete_approval_steps(run_id, context, evidence=evidence, reason=reason)
        self._record_transition(
            workflow=workflow,
            run_id=run_id,
            job_id=context.job_id,
            status=APPROVED_FOR_INTEGRATION_STATUS,
            payload=transition_payload,
            actor=actor,
        )
        return {
            "status": APPROVED_FOR_INTEGRATION_STATUS,
            "reason": reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run_id),
            "workspace": context.workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": self.evidence.get_evidence_package(context.evidence_id),
            "runtime": evidence.get("runtimeHealth") or (context.run_metadata.get("runtime") or {}),
            "runtimeResult": (agent_run.get("output") or {}).get("runtimeResult") or {},
            "qaResults": evidence.get("testResults") or [],
            "diffSummary": evidence.get("diffSummary") or {},
        }

    def _complete_approval_steps(
        self, run_id: str, context: _PatchApprovalContext, *, evidence: dict[str, Any], reason: str
    ) -> None:
        """Mark the qa_validation and technical_review steps as completed by the approval."""
        for step in self.workflows.list_workflow_steps(workflow_run_id=run_id):
            if step["name"] == "qa_validation":
                self.workflows.update_workflow_step(
                    step["id"],
                    status="completed",
                    output={
                        **(step.get("output") or {}),
                        "qaVerdict": evidence["qaVerdict"],
                        "evidencePackageId": context.evidence_id,
                        "approvalActionRequestId": context.approved_action["id"],
                    },
                )
            if step["name"] == "technical_review":
                self.workflows.update_workflow_step(
                    step["id"],
                    status="completed",
                    output={"approvedForIntegration": True, "reason": reason},
                    metadata={**(step.get("metadata") or {}), "gateState": APPROVED_FOR_INTEGRATION_STATUS},
                )

    def promote_patch_to_branch(
        self,
        run_id: str,
        *,
        reason: str,
        branch_name: str | None = None,
        evidence_package_id: str | None = None,
        qa_commands: list[list[str]] | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Promote an approved patch onto a working branch, re-running QA and recording evidence.

        Accepted for issue_to_patch and issue_to_pr runs in approved_for_integration (or after a
        prior promotion_failed retry). Verifies the approval action and approved evidence belong to
        the run, applies the patch on a fresh branch (never main, never force-push), and returns
        ``promoted_to_branch`` or ``promotion_failed``.

        Raises:
            ValueError: if the reason is empty, the run is the wrong kind/status, or the approved
                evidence/approval is missing or mismatched.
        """
        plan = self._plan_promotion(
            run_id, reason=reason, branch_name=branch_name, evidence_package_id=evidence_package_id
        )
        promotion_job, promoter_run = self._start_promotion_run(run_id, plan)
        attempt = self._apply_patch_and_run_qa(
            run_id, plan, job=promotion_job, agent_run=promoter_run, qa_commands=qa_commands
        )
        verdict = _Verdict(
            *_promotion_status_from_results(
                apply_check=attempt.apply_check,
                apply_result=attempt.apply_result,
                qa_results=attempt.qa_results,
                diff=attempt.diff,
            )
        )
        evidence, diff_summary = self._write_promotion_evidence(run_id, plan, attempt, verdict)
        evidence, verdict = self._enforce_promotion_contract(evidence, verdict)
        return self._persist_promotion_outcome(
            run_id,
            plan,
            attempt,
            verdict=verdict,
            evidence=evidence,
            diff_summary=diff_summary,
            actor=actor,
        )

    def _plan_promotion(
        self,
        run_id: str,
        *,
        reason: str,
        branch_name: str | None,
        evidence_package_id: str | None,
    ) -> _PromotionPlan:
        """Validate the run, approved evidence, approval action and patch before touching git."""
        clean_reason = str(redact_secrets(reason or "")).strip()
        if not clean_reason:
            raise ValueError("Promotion reason is required.")

        workflow_run = self.workflows.get_workflow_run(run_id)
        workflow = self.workflows.get_workflow(workflow_run["workflowId"])
        if workflow["kind"] not in {"issue_to_patch", "issue_to_pr"}:
            raise ValueError(
                "Only issue_to_patch or issue_to_pr workflow runs can use promote_patch_to_branch."
            )
        if workflow_run["status"] not in {APPROVED_FOR_INTEGRATION_STATUS, PROMOTION_FAILED_STATUS}:
            raise ValueError("promote_patch_to_branch requires an approved_for_integration workflow run.")

        run_metadata = workflow_run.get("metadata") or {}
        approved_evidence_id = str(
            evidence_package_id
            or run_metadata.get("originalEvidencePackageId")
            or run_metadata.get("evidencePackageId")
            or ""
        )
        if not approved_evidence_id:
            raise ValueError("promote_patch_to_branch requires an approved evidence package.")
        approved_evidence = self.evidence.get_evidence_package(approved_evidence_id)
        if approved_evidence["workflowRunId"] != run_id:
            raise ValueError("Approved evidence package does not belong to this workflow run.")

        original_job_id, _agent_run_id, original_workspace_id = _linked_record_ids(
            approved_evidence,
            run_metadata,
            missing_message=(
                "promote_patch_to_branch requires linked approved job, agent run, and workspace records."
            ),
        )
        original_workspace = self.workspaces.get_workspace(original_workspace_id)

        approval_actions = [
            action
            for action in self._approval_actions(
                original_job_id,
                action_type=_approval_action_type(workflow),
                run_id=run_id,
                evidence_id=approved_evidence_id,
            )
            if action["status"] == "approved"
        ]
        if not approval_actions:
            raise ValueError(
                "promote_patch_to_branch requires approved patch evidence before branch promotion."
            )

        approved_evidence, patch_artifact, patch_content = self._verify_approved_patch(
            approved_evidence_id, original_job_id
        )
        base_commit = _approved_evidence_base_commit(approved_evidence, original_workspace)
        target_branch = _promotion_branch_name(requested=branch_name, workflow=workflow, run_id=run_id)
        steps = {step["name"]: step for step in self.workflows.list_workflow_steps(workflow_run_id=run_id)}
        return _PromotionPlan(
            workflow=workflow,
            run_metadata=run_metadata,
            approved_evidence_id=approved_evidence_id,
            approved_evidence=approved_evidence,
            original_job_id=original_job_id,
            patch_artifact=patch_artifact,
            patch_content=patch_content,
            base_commit=base_commit,
            target_branch=target_branch,
            steps=steps,
        )

    def _verify_approved_patch(
        self, approved_evidence_id: str, original_job_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], bytes]:
        """Refresh approval links and verify contract, patch hash, QA and security of the evidence."""
        approved_evidence = self.evidence.update_evidence_links(
            approved_evidence_id,
            approvals=self.jobs.list_action_requests(original_job_id),
        )
        contract_errors = evidence_package_contract_errors(
            approved_evidence,
            require_runtime_links=True,
            require_workflow_run=True,
        )
        if contract_errors:
            raise ValueError(
                "Approved evidence package contract is incomplete: " + "; ".join(contract_errors)
            )
        approved_artifacts = self.evidence.list_artifacts(approved_evidence_id)
        patch_artifact = _validate_patch_artifact(approved_evidence, approved_artifacts)
        patch_content = _artifact_content_bytes(patch_artifact)
        _validate_qa_for_patch_approval(approved_evidence, action_approved=True)
        _validate_security_findings(approved_evidence, approved_artifacts)
        return approved_evidence, patch_artifact, patch_content

    def _start_promotion_run(
        self, run_id: str, plan: _PromotionPlan
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create the running promotion job and promoter agent run."""
        technical_review_id = plan.steps.get("technical_review", {}).get("id")
        profile = self.agents.upsert_agent_profile(
            {
                "id": PATCH_PROMOTER_PROFILE_ID,
                "name": "AIDO Patch Promoter",
                "role": "release_manager",
                "runtimeMode": "manual",
                "permissionProfile": "release",
                "allowedTools": [],
                "allowedProviders": [],
                "allowedRuntimes": [],
                "allowRemote": False,
                "allowCli": True,
                "allowApi": False,
            }
        )
        promotion_job = self.jobs.create_job(
            project_id=plan.workflow["projectId"],
            kind="workflow.issue_to_patch.promote_patch_to_branch",
            workflow_run_id=run_id,
            workflow_step_id=technical_review_id,
            status="running",
            payload={
                "command": "promote_patch_to_branch",
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "patchArtifactId": plan.patch_artifact["id"],
                "patchArtifactHash": plan.patch_artifact["hash"],
                "baseCommit": plan.base_commit,
                "branchName": plan.target_branch,
            },
        )["job"]
        promoter_run = self.agents.create_agent_run(
            project_id=plan.workflow["projectId"],
            agent_profile_id=profile["id"],
            task_id="promote_patch_to_branch",
            input_payload={
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "patchArtifactId": plan.patch_artifact["id"],
                "patchArtifactHash": plan.patch_artifact["hash"],
                "baseCommit": plan.base_commit,
                "branchName": plan.target_branch,
            },
            output_payload={},
            job_id=promotion_job["id"],
            workflow_run_id=run_id,
            workflow_step_id=technical_review_id,
            status="running",
        )
        return promotion_job, promoter_run

    def _apply_patch_and_run_qa(
        self,
        run_id: str,
        plan: _PromotionPlan,
        *,
        job: dict[str, Any],
        agent_run: dict[str, Any],
        qa_commands: list[list[str]] | None,
    ) -> _PromotionAttempt:
        """Apply the verified patch in a new branch worktree, rerun QA and capture the final diff."""
        project_id = plan.workflow["projectId"]
        workspace = self.workspaces.allocate_workspace(
            project_id=project_id,
            task_id=f"promote-patch-{run_id}-{uuid.uuid4().hex[:8]}",
            agent_id=PATCH_PROMOTER_PROFILE_ID,
            reason="promote_patch_to_branch local branch",
            isolation_type="git_worktree",
            workflow_run_id=run_id,
            workflow_step_id=plan.steps.get("technical_review", {}).get("id"),
            base_branch=plan.base_commit,
            branch_name=plan.target_branch,
        )
        workspace_path = Path(workspace["path"])
        patch_path = Path(str(plan.patch_artifact["path"])).resolve(strict=False)
        apply_check = _git_operation_result(
            ["apply", "--check", str(patch_path)],
            cwd=workspace_path,
            connection=self.connection,
            root=self.root,
            project_id=project_id,
            workspace_id=workspace["id"],
            task_id="promote_patch_to_branch.apply_check",
            git_operation="apply_check",
        )
        apply_result: dict[str, Any] | None = None
        qa_summary: dict[str, Any] | None = None
        if apply_check["returnCode"] == 0:
            apply_result = _git_operation_result(
                ["apply", str(patch_path)],
                cwd=workspace_path,
                connection=self.connection,
                root=self.root,
                project_id=project_id,
                workspace_id=workspace["id"],
                task_id="promote_patch_to_branch.apply_patch",
                git_operation="apply_patch",
            )
            if apply_result["returnCode"] == 0:
                qa_summary = QAAgentRunner(self.connection, root=self.root).run_for_context(
                    project_id=project_id,
                    workspace_id=workspace["id"],
                    task_id="promote_patch_to_branch",
                    commands=_promotion_qa_commands(plan.approved_evidence, qa_commands),
                    workflow_run_id=run_id,
                    workflow_step_id=plan.steps.get("qa_validation", {}).get("id"),
                    job_id=job["id"],
                    parent_agent_run_id=agent_run["id"],
                    metadata={
                        "source": "promote_patch_to_branch",
                        "approvedEvidencePackageId": plan.approved_evidence_id,
                        "patchArtifactId": plan.patch_artifact["id"],
                    },
                )
        diff = capture_git_diff(
            workspace_path,
            connection=self.connection,
            root=self.root,
            project_id=project_id,
            workspace_id=workspace["id"],
            task_id="promote_patch_to_branch",
        )
        return _PromotionAttempt(
            job=job,
            agent_run=agent_run,
            workspace=workspace,
            apply_check=apply_check,
            apply_result=apply_result,
            qa_summary=qa_summary,
            diff=diff,
        )

    def _write_promotion_evidence(
        self, run_id: str, plan: _PromotionPlan, attempt: _PromotionAttempt, verdict: _Verdict
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create the promotion evidence package, its artifacts and links; return it with the diff summary."""
        project_id = plan.workflow["projectId"]
        diff_summary = _diff_summary(attempt.diff)
        diff_summary.update(
            {
                "branch": plan.target_branch,
                "baseCommit": plan.base_commit,
                "patchArtifactId": plan.patch_artifact["id"],
                "patchArtifactHash": plan.patch_artifact["hash"],
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionCommand": "promote_patch_to_branch",
            }
        )
        promotion_logs = [
            {
                "source": "promote_patch_to_branch",
                "reason": verdict.reason,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "patchArtifactId": plan.patch_artifact["id"],
                "patchArtifactHash": plan.patch_artifact["hash"],
                "patchSizeBytes": len(plan.patch_content),
                "baseCommit": plan.base_commit,
                "branchName": plan.target_branch,
                "gitApplyCheck": attempt.apply_check,
                "gitApply": attempt.apply_result,
                "qaAgentRunId": attempt.qa_agent_run_id,
            }
        ]
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=run_id,
            workflow_step_id=plan.steps.get("qa_validation", {}).get("id"),
            agent_id=PATCH_PROMOTER_PROFILE_ID,
            agent_run_id=attempt.agent_run["id"],
            job_id=attempt.job["id"],
            workspace_id=attempt.workspace["id"],
            runtime_id="git.cli",
            task_id="promote_patch_to_branch",
            test_plan="Create a local branch from the approved base commit, apply the verified patch artifact, and rerun QA.",
            acceptance_checklist=[
                "Approved evidence exists.",
                "Patch artifact SHA-256 matches before application.",
                "Branch is created from the captured base commit.",
                "Patch applies with git apply.",
                "QA commands pass after patch application.",
            ],
            test_results=attempt.qa_results,
            logs=promotion_logs,
            diff_refs=_final_diff_refs(attempt.workspace, attempt.diff),
            diff_summary=diff_summary,
            runtime_health={
                "id": "git.cli",
                "status": verdict.status,
                "available": git_available(),
                "executable": git_available(),
                "baseCommit": plan.base_commit,
                "branchName": plan.target_branch,
                "reason": verdict.reason,
            },
            approvals=self.jobs.list_action_requests(plan.original_job_id),
            evidence_source="verified_completion" if verdict.qa_verdict == "passed" else "evidence_collected",
            qa_verdict=verdict.qa_verdict,
            risk_notes=[
                {
                    "severity": "low" if verdict.status == PROMOTED_TO_BRANCH_STATUS else "high",
                    "description": verdict.reason,
                    "mitigation": "Fix the branch promotion blocker and retry from the approved evidence package.",
                }
            ],
        )
        if attempt.qa_artifact_ids:
            QAAgentRunner(self.connection, root=self.root).attach_artifacts_to_evidence(
                evidence_id=evidence["id"],
                artifact_ids=attempt.qa_artifact_ids,
            )
        evidence = self._link_promotion_artifacts(
            run_id, plan, attempt, verdict, evidence=evidence, diff_summary=diff_summary
        )
        return evidence, diff_summary

    def _link_promotion_artifacts(
        self,
        run_id: str,
        plan: _PromotionPlan,
        attempt: _PromotionAttempt,
        verdict: _Verdict,
        *,
        evidence: dict[str, Any],
        diff_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Write the git status, command, QA and manifest artifacts and link them to the evidence."""
        project_id = plan.workflow["projectId"]
        status_artifacts = _write_git_status_artifacts(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            diff=attempt.diff,
            repo=self.evidence,
            source="promote_patch_to_branch",
        )
        command_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="promotion-git-commands.json",
            kind="qa_report",
            payload={
                "command": "promote_patch_to_branch",
                "gitApplyCheck": attempt.apply_check,
                "gitApply": attempt.apply_result,
                "qaResults": attempt.qa_results,
            },
            repo=self.evidence,
            source="promote_patch_to_branch",
        )
        qa_results_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="promotion-qa-results.json",
            kind="qa_report",
            payload={"results": attempt.qa_results, "qaAgentRunId": attempt.qa_agent_run_id},
            repo=self.evidence,
            source="promote_patch_to_branch",
        )
        manifest_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="promotion-evidence.json",
            kind="evidence_manifest",
            payload={
                "command": "promote_patch_to_branch",
                "status": verdict.status,
                "reason": verdict.reason,
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "jobId": attempt.job["id"],
                "agentRunId": attempt.agent_run["id"],
                "workspaceId": attempt.workspace["id"],
                "branchName": plan.target_branch,
                "baseCommit": plan.base_commit,
                "patchArtifact": artifact_ref(plan.patch_artifact),
                "diffSummary": diff_summary,
                "qaResults": attempt.qa_results,
            },
            repo=self.evidence,
            source="promote_patch_to_branch",
        )
        qa_artifacts = artifact_records_from_ids(self.evidence, attempt.qa_artifact_ids)
        promotion_artifacts = [
            plan.patch_artifact,
            *qa_artifacts,
            *status_artifacts,
            command_artifact,
            qa_results_artifact,
            manifest_artifact,
        ]
        diff_summary["gitStatusArtifactIds"] = [artifact["id"] for artifact in status_artifacts]
        diff_summary["qaResultsArtifactId"] = qa_results_artifact["id"]
        diff_summary["manifestArtifactId"] = manifest_artifact["id"]
        related_agent_run_ids = {attempt.agent_run["id"]}
        for git_result in [attempt.apply_check, attempt.apply_result]:
            if git_result and git_result.get("agentRunId"):
                related_agent_run_ids.add(str(git_result["agentRunId"]))
        for git_trace in attempt.diff.get("toolCalls") or []:
            if isinstance(git_trace, dict) and git_trace.get("agentRunId"):
                related_agent_run_ids.add(str(git_trace["agentRunId"]))
        if attempt.qa_agent_run_id:
            related_agent_run_ids.add(str(attempt.qa_agent_run_id))
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) in related_agent_run_ids
        ]
        return self.evidence.update_evidence_links(
            evidence["id"],
            artifact_ids=[
                str(artifact["id"])
                for artifact in promotion_artifacts
                if artifact.get("id") and artifact.get("evidencePackageId") == evidence["id"]
            ],
            diff_summary=diff_summary,
            tool_calls=tool_calls,
            artifacts=[artifact_ref(artifact) for artifact in promotion_artifacts],
            hashes=artifact_hashes(promotion_artifacts),
        )

    def _enforce_promotion_contract(
        self, evidence: dict[str, Any], verdict: _Verdict
    ) -> tuple[dict[str, Any], _Verdict]:
        """Downgrade a promoted verdict to promotion_failed when the evidence contract is incomplete."""
        completed = verdict.status == PROMOTED_TO_BRANCH_STATUS
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=completed,
            require_workflow_run=True,
        )
        if not (completed and contract_errors):
            return evidence, verdict
        reason = "Promotion evidence package contract is incomplete: " + "; ".join(contract_errors)
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            qa_verdict="blocked",
            risk_notes=[
                {
                    "severity": "high",
                    "description": reason,
                    "mitigation": "Regenerate promotion evidence with artifact refs and SHA-256 hashes before marking promoted.",
                }
            ],
        )
        return evidence, _Verdict(PROMOTION_FAILED_STATUS, "blocked", reason)

    def _persist_promotion_outcome(
        self,
        run_id: str,
        plan: _PromotionPlan,
        attempt: _PromotionAttempt,
        *,
        verdict: _Verdict,
        evidence: dict[str, Any],
        diff_summary: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """Record the promotion verdict on agent run, job, workflow, steps and events; build the response."""
        completed = verdict.status == PROMOTED_TO_BRANCH_STATUS
        promoter_run = self.agents.update_agent_run_status(
            attempt.agent_run["id"],
            status="completed" if completed else "failed",
            output_payload={
                "verdict": verdict.status,
                "summary": verdict.reason,
                "branchName": plan.target_branch,
                "baseCommit": plan.base_commit,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "patchArtifactId": plan.patch_artifact["id"],
                "qaResults": attempt.qa_results,
                "diffSummary": diff_summary,
                "evidence_refs": [plan.approved_evidence_id, evidence["id"]],
            },
        )
        promotion_job = self.jobs.update_job_status(
            attempt.job["id"],
            status="completed" if completed else "failed",
            metadata={
                "status": verdict.status,
                "reason": verdict.reason,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "branchName": plan.target_branch,
                "baseCommit": plan.base_commit,
            },
        )
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=verdict.status,
            metadata={
                **plan.run_metadata,
                "originalEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "promotionJobId": promotion_job["id"],
                "promotionAgentRunId": promoter_run["id"],
                "promotionWorkspaceId": attempt.workspace["id"],
                "promotedBranchName": plan.target_branch if completed else None,
                "promotionStatus": verdict.status,
                "promotionReason": verdict.reason,
                "promotionDiffSummary": diff_summary,
            },
            completed=completed,
            clear_completed=not completed,
        )
        workflow = self.workflows.update_workflow_status(
            plan.workflow["id"], status=verdict.status, reason=verdict.reason
        )
        self._update_promotion_steps(plan, attempt, verdict, evidence_id=evidence["id"])
        event_payload = {
            "workflowId": workflow["id"],
            "workflowRunId": run_id,
            "approvedEvidencePackageId": plan.approved_evidence_id,
            "promotionEvidencePackageId": evidence["id"],
            "jobId": promotion_job["id"],
            "agentRunId": promoter_run["id"],
            "workspaceId": attempt.workspace["id"],
            "branchName": plan.target_branch,
            "baseCommit": plan.base_commit,
            "status": verdict.status,
            "reason": verdict.reason,
        }
        self._record_transition(
            workflow=workflow,
            run_id=run_id,
            job_id=promotion_job["id"],
            status=verdict.status,
            payload=event_payload,
            actor=actor,
            severity="info" if completed else "warning",
        )
        return {
            "status": verdict.status,
            "reason": verdict.reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run_id),
            "workspace": attempt.workspace,
            "job": promotion_job,
            "agentRun": promoter_run,
            "evidencePackage": evidence,
            "runtime": evidence.get("runtimeHealth") or {},
            "runtimeResult": {"gitApplyCheck": attempt.apply_check, "gitApply": attempt.apply_result},
            "qaResults": attempt.qa_results,
            "diffSummary": diff_summary,
        }

    def _update_promotion_steps(
        self, plan: _PromotionPlan, attempt: _PromotionAttempt, verdict: _Verdict, *, evidence_id: str
    ) -> None:
        """Reflect the promotion verdict on the local_tests, qa_validation and technical_review steps."""
        steps = plan.steps
        completed = verdict.status == PROMOTED_TO_BRANCH_STATUS
        if "local_tests" in steps:
            self.workflows.update_workflow_step(
                steps["local_tests"]["id"],
                status="completed"
                if qa_verdict_allows_completion(verdict.qa_verdict, attempt.qa_results)
                else "blocked",
                output={
                    **(steps["local_tests"].get("output") or {}),
                    "promotionQaResults": attempt.qa_results,
                    "promotionEvidencePackageId": evidence_id,
                },
            )
        if "qa_validation" in steps:
            self.workflows.update_workflow_step(
                steps["qa_validation"]["id"],
                status="completed" if completed else "blocked",
                output={
                    **(steps["qa_validation"].get("output") or {}),
                    "promotionQaVerdict": verdict.qa_verdict,
                    "promotionEvidencePackageId": evidence_id,
                },
            )
        if "technical_review" in steps:
            self.workflows.update_workflow_step(
                steps["technical_review"]["id"],
                status="completed" if completed else "blocked",
                output={
                    **(steps["technical_review"].get("output") or {}),
                    "promotionStatus": verdict.status,
                    "branchName": plan.target_branch,
                    "promotionEvidencePackageId": evidence_id,
                    "reason": verdict.reason,
                },
                metadata={
                    **(steps["technical_review"].get("metadata") or {}),
                    "gateState": verdict.status,
                },
            )

    def _push_promoted_branch_for_pr(self, *, project_id: str, branch: str) -> dict[str, Any]:
        """Publica la rama promovida en el remoto git del proyecto antes del POST del PR.

        Devuelve ``pushed``/``skipped_no_remote`` (sin remoto local se conserva el comportamiento
        previo) o el fallo del push; nunca lanza.
        """
        try:
            project = ProjectsRepository(self.connection).get_project(project_id)
            repo_path = Path(str(project.get("path") or "")).resolve(strict=False)
            remote = (
                str(
                    resolve_setting_value(
                        connection=self.connection, key="project.git.remoteName", project_id=project_id
                    )
                    or "origin"
                ).strip()
                or "origin"
            )
            if not git_remote_exists(
                repo_path=repo_path,
                remote=remote,
                connection=self.connection,
                root=self.root,
                project_id=project_id,
            ):
                return {"status": "skipped_no_remote", "remote": remote}
            return push_branch_to_remote(
                repo_path=repo_path,
                remote=remote,
                branch=branch,
                connection=self.connection,
                root=self.root,
                project_id=project_id,
            )
        except Exception as error:
            return {"status": "push_failed", "reason": str(redact_secrets(str(error)))}

    def create_pull_request_from_promoted_branch(
        self,
        run_id: str,
        *,
        reason: str,
        title: str | None = None,
        base_branch: str | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Open a GitHub PR from a previously promoted branch for an approved run.

        Requires a successful prior promotion (promoted branch, promotion evidence and workspace);
        idempotency-guards against double creation. Returns ``pr_created``, or ``pr_unavailable``
        when GitHub is not configured / ``pr_failed`` when the API call fails.

        Raises:
            ValueError: if the reason is empty, the run is the wrong kind, a PR already exists, or
                the promotion prerequisites are absent.
        """
        plan = self._plan_pull_request(run_id, reason=reason, title=title, base_branch=base_branch)
        pr_job, pr_agent_run = self._start_pull_request_run(run_id, plan)
        submission = self._submit_pull_request(plan)
        evidence, diff_summary, qa_results = self._write_pull_request_evidence(
            run_id, plan, submission, job=pr_job, agent_run=pr_agent_run
        )
        return self._persist_pull_request_outcome(
            run_id,
            plan,
            submission,
            job=pr_job,
            agent_run=pr_agent_run,
            evidence=evidence,
            diff_summary=diff_summary,
            qa_results=qa_results,
            actor=actor,
        )

    def _plan_pull_request(
        self, run_id: str, *, reason: str, title: str | None, base_branch: str | None
    ) -> _PullRequestPlan:
        """Validate the promoted run, its evidence and approvals, and build the PR title and body."""
        clean_reason = str(redact_secrets(reason or "")).strip()
        if not clean_reason:
            raise ValueError("Pull request creation reason is required.")

        workflow_run = self.workflows.get_workflow_run(run_id)
        workflow = self.workflows.get_workflow(workflow_run["workflowId"])
        if workflow["kind"] not in {"issue_to_patch", "issue_to_pr"}:
            raise ValueError(
                "Only issue_to_patch or issue_to_pr workflow runs can create pull requests from promoted branches."
            )
        if workflow_run["status"] == PR_CREATED_STATUS:
            raise ValueError("Pull request was already created for this workflow run.")

        run_metadata = workflow_run.get("metadata") or {}
        promoted_branch_name = str(run_metadata.get("promotedBranchName") or "").strip()
        promotion_evidence_id = str(run_metadata.get("promotionEvidencePackageId") or "").strip()
        approved_evidence_id = str(
            run_metadata.get("originalEvidencePackageId") or run_metadata.get("evidencePackageId") or ""
        ).strip()
        promotion_workspace_id = str(run_metadata.get("promotionWorkspaceId") or "").strip()
        if (
            run_metadata.get("promotionStatus") != PROMOTED_TO_BRANCH_STATUS
            or not promoted_branch_name
            or not promotion_evidence_id
            or not approved_evidence_id
            or not promotion_workspace_id
        ):
            raise ValueError("create_pull_request requires a promoted_to_branch workflow run.")

        promotion_evidence = self.evidence.get_evidence_package(promotion_evidence_id)
        approved_evidence = self.evidence.get_evidence_package(approved_evidence_id)
        if promotion_evidence["workflowRunId"] != run_id or approved_evidence["workflowRunId"] != run_id:
            raise ValueError("create_pull_request requires evidence packages from the target workflow run.")
        if promotion_evidence.get("qaVerdict") != "passed" or not qa_verdict_allows_completion(
            "passed",
            promotion_evidence.get("testResults") or [],
        ):
            raise ValueError("create_pull_request requires passed promotion QA evidence.")

        approved_artifacts = self.evidence.list_artifacts(approved_evidence_id)
        _validate_patch_artifact(approved_evidence, approved_artifacts)
        security_findings = _validate_security_findings(approved_evidence, approved_artifacts)
        _validate_qa_for_patch_approval(approved_evidence, action_approved=True)
        promotion_workspace = self.workspaces.get_workspace(promotion_workspace_id)
        resolved_base_branch = _pull_request_base_branch(
            requested=base_branch,
            promotion_workspace=promotion_workspace,
        )
        steps = {step["name"]: step for step in self.workflows.list_workflow_steps(workflow_run_id=run_id)}
        workflow_step_id = (steps.get("pr_creation") or steps.get("technical_review") or {}).get("id")
        original_job_id = str(approved_evidence.get("jobId") or run_metadata.get("jobId") or "")
        approval_actions = [
            action
            for action in self._approval_actions(
                original_job_id,
                action_type=_approval_action_type(workflow),
                run_id=run_id,
                evidence_id=approved_evidence_id,
            )
            if action["status"] == "approved"
        ]
        if not approval_actions:
            raise ValueError("create_pull_request requires approved patch evidence before PR creation.")
        approval_reason = _approval_reason_summary(
            approval_actions=approval_actions, run_metadata=run_metadata
        )
        return _PullRequestPlan(
            workflow=workflow,
            run_metadata=run_metadata,
            promoted_branch_name=promoted_branch_name,
            promotion_evidence_id=promotion_evidence_id,
            approved_evidence_id=approved_evidence_id,
            promotion_evidence=promotion_evidence,
            approved_evidence=approved_evidence,
            promotion_workspace=promotion_workspace,
            base_branch=resolved_base_branch,
            steps=steps,
            workflow_step_id=workflow_step_id,
            approval_actions=approval_actions,
            title=str(title or "").strip() or _default_pull_request_title(workflow, promoted_branch_name),
            body=_build_pull_request_body(
                approved_evidence=approved_evidence,
                promotion_evidence=promotion_evidence,
                security_findings=security_findings,
                approval_reason=approval_reason,
                request_reason=clean_reason,
                branch_name=promoted_branch_name,
                base_branch=resolved_base_branch,
            ),
        )

    def _start_pull_request_run(
        self, run_id: str, plan: _PullRequestPlan
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create the running pull request job and agent run."""
        project_id = plan.workflow["projectId"]
        profile = self.agents.upsert_agent_profile(
            {
                "id": PULL_REQUEST_PROFILE_ID,
                "name": "AIDO Pull Request Creator",
                "role": "release_manager",
                "runtimeMode": "api",
                "permissionProfile": "release",
                "allowedTools": [],
                "allowedProviders": ["github"],
                "allowedRuntimes": ["github.rest"],
                "allowRemote": True,
                "allowCli": False,
                "allowApi": True,
            }
        )
        pr_job = self.jobs.create_job(
            project_id=project_id,
            kind="workflow.issue_to_patch.create_pull_request",
            workflow_run_id=run_id,
            workflow_step_id=plan.workflow_step_id,
            status="running",
            payload={
                "command": "create_pull_request",
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "branchName": plan.promoted_branch_name,
                "baseBranch": plan.base_branch,
            },
        )["job"]
        pr_agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id="create_pull_request",
            input_payload={
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "branchName": plan.promoted_branch_name,
                "baseBranch": plan.base_branch,
                "title": plan.title,
            },
            output_payload={},
            job_id=pr_job["id"],
            workflow_run_id=run_id,
            workflow_step_id=plan.workflow_step_id,
            status="running",
        )
        return pr_job, pr_agent_run

    def _submit_pull_request(self, plan: _PullRequestPlan) -> _PullRequestSubmission:
        """Push the promoted branch and open the GitHub PR, mapping each outcome to a verdict."""
        try:
            github_config = github_pull_request_config_from_env()
        except GitHubPullRequestConfigError as error:
            return _PullRequestSubmission(
                verdict=_Verdict(PR_UNAVAILABLE_STATUS, "blocked", error.reason),
                github_result={
                    "status": "unavailable",
                    "reason": error.reason,
                    "missing": error.missing,
                    "request": None,
                    "response": {},
                },
                pull_request=None,
                configured=False,
            )
        branch_push = self._push_promoted_branch_for_pr(
            project_id=plan.workflow["projectId"], branch=plan.promoted_branch_name
        )
        if branch_push.get("status") not in {"pushed", "skipped_no_remote"}:
            push_failure = "Promoted branch push failed before PR creation: " + str(
                branch_push.get("stderr") or branch_push.get("reason") or branch_push["status"]
            )
            github_result = {
                "status": "failed",
                "reason": push_failure,
                "branchPush": branch_push,
                "request": None,
                "response": {},
            }
            github_result_status = "push_failed"
        else:
            github_result = create_github_pull_request(
                github_config,
                title=plan.title,
                head=plan.promoted_branch_name,
                base=plan.base_branch,
                body=plan.body,
            )
            github_result = {**github_result, "branchPush": branch_push}
            github_result_status = str(github_result.get("status") or "")
        if github_result_status == "created":
            return _PullRequestSubmission(
                verdict=_Verdict(
                    PR_CREATED_STATUS,
                    "evidence_collected",
                    "GitHub pull request created from promoted branch.",
                ),
                github_result=github_result,
                pull_request={
                    "status": "created",
                    "number": github_result.get("number"),
                    "id": github_result.get("id"),
                    "url": github_result.get("url"),
                    "htmlUrl": github_result.get("htmlUrl"),
                    "repository": github_result.get("repository"),
                    "head": plan.promoted_branch_name,
                    "base": plan.base_branch,
                },
                configured=True,
            )
        failure_reason = str(github_result.get("reason") or "GitHub pull request creation failed.")
        return _PullRequestSubmission(
            verdict=_Verdict(PR_FAILED_STATUS, "failed", failure_reason),
            github_result=github_result,
            pull_request={
                "status": "failed",
                "httpStatus": github_result.get("httpStatus"),
                "repository": github_config.repository,
                "head": plan.promoted_branch_name,
                "base": plan.base_branch,
                "reason": failure_reason,
            },
            configured=True,
        )

    def _write_pull_request_evidence(
        self,
        run_id: str,
        plan: _PullRequestPlan,
        submission: _PullRequestSubmission,
        *,
        job: dict[str, Any],
        agent_run: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Create the PR evidence package and artifacts; return it with the diff summary and QA results."""
        verdict = submission.verdict
        github_result = submission.github_result
        project_id = plan.workflow["projectId"]
        request_payload = (
            github_result.get("request") if isinstance(github_result.get("request"), dict) else None
        )
        request_path = str((request_payload or {}).get("url") or "github_config")
        qa_results = [
            {
                "command": "POST /repos/{owner}/{repo}/pulls"
                if request_payload
                else "github_pull_request_config",
                "status": "passed"
                if verdict.status == PR_CREATED_STATUS
                else "blocked"
                if verdict.status == PR_UNAVAILABLE_STATUS
                else "failed",
                "durationMs": None,
                "metadata": {
                    "url": request_path,
                    "httpStatus": github_result.get("httpStatus"),
                    "status": github_result.get("status"),
                },
            }
        ]
        diff_summary = {
            "branch": plan.promoted_branch_name,
            "baseBranch": plan.base_branch,
            "approvedEvidencePackageId": plan.approved_evidence_id,
            "promotionEvidencePackageId": plan.promotion_evidence_id,
            "pullRequestStatus": verdict.status,
            "pullRequest": submission.pull_request,
        }
        pr_logs = [
            {
                "source": "create_pull_request",
                "status": verdict.status,
                "reason": verdict.reason,
                "configured": submission.configured,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "branchName": plan.promoted_branch_name,
                "baseBranch": plan.base_branch,
                "request": request_payload,
                "response": github_result.get("response") or {},
            }
        ]
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=run_id,
            workflow_step_id=plan.workflow_step_id,
            agent_id=PULL_REQUEST_PROFILE_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=plan.promotion_workspace["id"],
            runtime_id="github.rest",
            task_id="create_pull_request",
            test_plan="Create a GitHub pull request only after approved evidence, branch promotion, and passed promotion QA.",
            acceptance_checklist=[
                "Approved issue_to_patch evidence exists.",
                "Promotion evidence exists and QA passed after applying the patch.",
                "Pull request is created only from the promoted branch.",
                "GitHub configuration is read only when PR creation is requested.",
                "GitHub API result is recorded without fabricating success.",
            ],
            test_results=qa_results,
            logs=pr_logs,
            diff_refs=plan.promotion_evidence.get("diffRefs") or [],
            diff_summary=diff_summary,
            runtime_health={
                "id": "github.rest",
                "status": verdict.status,
                "configured": submission.configured,
                "available": verdict.status == PR_CREATED_STATUS,
                "reason": verdict.reason,
                "remote": (request_payload or {}).get("repository"),
            },
            approvals=plan.approval_actions,
            evidence_source="evidence_collected",
            qa_verdict=verdict.qa_verdict,
            risk_notes=[
                {
                    "severity": "low" if verdict.status == PR_CREATED_STATUS else "high",
                    "description": verdict.reason,
                    "mitigation": "Configure GitHub or resolve the GitHub API error, then retry from the promoted branch.",
                }
            ],
        )
        evidence = self._link_pull_request_artifacts(
            run_id,
            plan,
            submission,
            job=job,
            agent_run=agent_run,
            evidence=evidence,
            diff_summary=diff_summary,
        )
        return evidence, diff_summary, qa_results

    def _link_pull_request_artifacts(
        self,
        run_id: str,
        plan: _PullRequestPlan,
        submission: _PullRequestSubmission,
        *,
        job: dict[str, Any],
        agent_run: dict[str, Any],
        evidence: dict[str, Any],
        diff_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Write the PR request, response and manifest artifacts, link them and record the PR row."""
        verdict = submission.verdict
        github_result = submission.github_result
        project_id = plan.workflow["projectId"]
        request_payload = (
            github_result.get("request") if isinstance(github_result.get("request"), dict) else None
        )
        request_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="pull-request-request.json",
            kind="evidence_manifest",
            payload={
                "status": verdict.status,
                "title": plan.title,
                "body": plan.body,
                "request": request_payload,
                "missingConfig": github_result.get("missing") or [],
            },
            repo=self.evidence,
            source="create_pull_request",
        )
        response_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="pull-request-response.json",
            kind="evidence_manifest",
            payload={
                "status": verdict.status,
                "reason": verdict.reason,
                "pullRequest": submission.pull_request,
                "httpStatus": github_result.get("httpStatus"),
                "response": github_result.get("response") or {},
            },
            repo=self.evidence,
            source="create_pull_request",
        )
        manifest_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="pull-request-evidence.json",
            kind="evidence_manifest",
            payload={
                "command": "create_pull_request",
                "status": verdict.status,
                "reason": verdict.reason,
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "jobId": job["id"],
                "agentRunId": agent_run["id"],
                "workspaceId": plan.promotion_workspace["id"],
                "branchName": plan.promoted_branch_name,
                "baseBranch": plan.base_branch,
                "pullRequest": submission.pull_request,
            },
            repo=self.evidence,
            source="create_pull_request",
        )
        pr_artifacts = [request_artifact, response_artifact, manifest_artifact]
        diff_summary["requestArtifactId"] = request_artifact["id"]
        diff_summary["responseArtifactId"] = response_artifact["id"]
        diff_summary["manifestArtifactId"] = manifest_artifact["id"]
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            artifact_ids=[artifact["id"] for artifact in pr_artifacts],
            diff_summary=diff_summary,
            artifacts=[artifact_ref(artifact) for artifact in pr_artifacts],
            hashes=artifact_hashes(pr_artifacts),
        )
        if verdict.status == PR_CREATED_STATUS and submission.pull_request:
            _record_pull_request(
                self.connection,
                project_id=project_id,
                workspace_id=plan.promotion_workspace["id"],
                status="created",
                pull_request=submission.pull_request,
                metadata={
                    "workflowRunId": run_id,
                    "approvedEvidencePackageId": plan.approved_evidence_id,
                    "promotionEvidencePackageId": plan.promotion_evidence_id,
                    "pullRequestEvidencePackageId": evidence["id"],
                    "branchName": plan.promoted_branch_name,
                    "baseBranch": plan.base_branch,
                },
            )
        return evidence

    def _persist_pull_request_outcome(
        self,
        run_id: str,
        plan: _PullRequestPlan,
        submission: _PullRequestSubmission,
        *,
        job: dict[str, Any],
        agent_run: dict[str, Any],
        evidence: dict[str, Any],
        diff_summary: dict[str, Any],
        qa_results: list[dict[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        """Record the PR verdict on agent run, job, workflow, step and events; build the response."""
        verdict = submission.verdict
        pull_request = submission.pull_request
        workflow_completed = verdict.status == PR_CREATED_STATUS
        pr_agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status="completed"
            if verdict.status == PR_CREATED_STATUS
            else "blocked"
            if verdict.status == PR_UNAVAILABLE_STATUS
            else "failed",
            output_payload={
                "verdict": verdict.status,
                "summary": verdict.reason,
                "pullRequest": pull_request,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "evidence_refs": [plan.approved_evidence_id, plan.promotion_evidence_id, evidence["id"]],
            },
        )
        pr_job = self.jobs.update_job_status(
            job["id"],
            status="completed" if workflow_completed else "failed",
            metadata={
                "status": verdict.status,
                "reason": verdict.reason,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "pullRequest": pull_request,
            },
        )
        run_status = PR_CREATED_STATUS if workflow_completed else PROMOTED_TO_BRANCH_STATUS
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=run_status,
            metadata={
                **plan.run_metadata,
                "pullRequestStatus": verdict.status,
                "pullRequestReason": verdict.reason,
                "pullRequestEvidencePackageId": evidence["id"],
                "pullRequestJobId": pr_job["id"],
                "pullRequestAgentRunId": pr_agent_run["id"],
                "pullRequest": pull_request,
            },
            completed=workflow_completed,
            clear_completed=not workflow_completed,
        )
        workflow = self.workflows.update_workflow_status(
            plan.workflow["id"],
            status=run_status,
            reason=verdict.reason,
        )
        if "pr_creation" in plan.steps:
            pr_step = plan.steps["pr_creation"]
            self.workflows.update_workflow_step(
                pr_step["id"],
                status="completed" if workflow_completed else "blocked",
                output={
                    **(pr_step.get("output") or {}),
                    "pullRequestStatus": verdict.status,
                    "pullRequestEvidencePackageId": evidence["id"],
                    "pullRequest": pull_request,
                },
                metadata={
                    **(pr_step.get("metadata") or {}),
                    "gateState": verdict.status,
                },
            )
        self._record_transition(
            workflow=workflow,
            run_id=run_id,
            job_id=pr_job["id"],
            status=verdict.status,
            payload={
                "workflowId": workflow["id"],
                "workflowRunId": run_id,
                "approvedEvidencePackageId": plan.approved_evidence_id,
                "promotionEvidencePackageId": plan.promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "jobId": pr_job["id"],
                "agentRunId": pr_agent_run["id"],
                "branchName": plan.promoted_branch_name,
                "baseBranch": plan.base_branch,
                "status": verdict.status,
                "reason": verdict.reason,
                "pullRequest": pull_request,
            },
            actor=actor,
            severity="info" if workflow_completed else "warning",
        )
        return {
            "status": verdict.status,
            "reason": verdict.reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run_id),
            "workspace": plan.promotion_workspace,
            "job": pr_job,
            "agentRun": pr_agent_run,
            "evidencePackage": evidence,
            "runtime": evidence.get("runtimeHealth") or {},
            "runtimeResult": submission.github_result,
            "qaResults": qa_results,
            "diffSummary": diff_summary,
            "pullRequest": pull_request,
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the issue_to_patch slice end-to-end and return its terminal status and evidence.

        Selects an executable runtime, creates and starts the workflow, runs the developer agent
        in an isolated workspace, runs QA on the resulting diff and assembles an evidence package.
        Returns ``evidence_ready`` on success or ``runtime_unavailable``/``qa_failed``/``failed``;
        stops before approval (the manual transitions take over from there).

        Raises:
            ValueError: if a requested ``preferredRuntime`` is not in the product catalog.
        """
        run = self._start_issue_to_patch_run(payload)
        workspace = self._allocate_run_workspace(run)
        developer_result = self._execute_developer_agent(run, workspace)
        verdict = self._developer_verdict(run, developer_result)
        evidence, diff_summary, artifact_refs = self._link_run_evidence(run, developer_result, verdict)
        verdict, evidence = self._enforce_run_contract(run, developer_result, verdict, evidence)
        return self._persist_run_outcome(
            run,
            developer_result,
            verdict=verdict,
            evidence=evidence,
            diff_summary=diff_summary,
            artifact_refs=artifact_refs,
        )

    def _start_issue_to_patch_run(self, payload: dict[str, Any]) -> _IssueToPatchRun:
        """Select the runtime, register the runner profile and create the workflow, run and job."""
        title = str(payload["title"]).strip()
        issue_text = str(payload["issueText"]).strip()
        preferred_runtime = payload.get("preferredRuntime")
        status_service = RuntimeStatusService(self.connection)
        runtime_statuses = status_service.list_provider_statuses()
        if preferred_runtime and not any(status["id"] == preferred_runtime for status in runtime_statuses):
            raise ValueError(f"Runtime provider is not in the product catalog: {preferred_runtime}")
        runtime = _select_runtime(runtime_statuses, preferred_runtime=preferred_runtime)
        profile_id = "aido_issue_to_patch_runner"
        runtime_mode = _runtime_mode(str(runtime["id"]))
        self.agents.upsert_agent_profile(
            {
                "id": profile_id,
                "name": "AIDO Issue-to-Patch Runner",
                "role": "implementer",
                "runtimeMode": runtime_mode,
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell", "openhands", "swe_agent"],
                "allowedProviders": [str(runtime["id"])],
                "allowedRuntimes": [str(runtime["id"])],
                "allowRemote": runtime["kind"] in {"api", "gateway"},
                "allowCli": runtime["kind"] == "cli",
                "allowApi": runtime["kind"] in {"api", "gateway"},
            }
        )
        workflow = self.workflows.create_workflow(
            project_id=payload["projectId"],
            kind="issue_to_patch",
            title=title,
            metadata={
                "issueText": issue_text,
                "targetPath": payload.get("targetPath"),
                "preferredRuntime": preferred_runtime,
                "runtimeId": runtime["id"],
                "maxCostUsd": payload.get("maxCostUsd"),
                "requireApproval": payload.get("requireApproval", True),
                "steps": [{"name": name} for name in ISSUE_TO_PATCH_STEPS],
            },
        )
        started = self.workflows.start_workflow(workflow["id"], reason="issue_to_patch vertical slice")
        workflow_run = started["workflowRun"]
        steps = {step["name"]: step for step in started["workflowSteps"]}
        job_result = self.jobs.create_job(
            project_id=payload["projectId"],
            kind="workflow.issue_to_patch",
            workflow_run_id=workflow_run["id"],
            workflow_step_id=steps.get("implementation", {}).get("id"),
            status="running",
            payload={
                "workflowId": workflow["id"],
                "workflowRunId": workflow_run["id"],
                "runtime": runtime,
                "issueText": issue_text,
                "qaCommands": payload.get("qaCommands") or [],
            },
        )
        return _IssueToPatchRun(
            payload=payload,
            title=title,
            issue_text=issue_text,
            runtime=runtime,
            profile_id=profile_id,
            workflow=workflow,
            workflow_run=workflow_run,
            steps=steps,
            job_id=job_result["job"]["id"],
        )

    def _allocate_run_workspace(self, run: _IssueToPatchRun) -> dict[str, Any]:
        """Allocate the isolated git worktree and mark the workspace_create step completed."""
        workspace = self.workspaces.allocate_workspace(
            project_id=run.payload["projectId"],
            task_id=f"issue-to-patch-{run.workflow_run['id']}",
            agent_id=run.profile_id,
            reason="issue_to_patch isolated workspace",
            isolation_type="git_worktree",
            workflow_run_id=run.workflow_run["id"],
            workflow_step_id=run.steps.get("workspace_create", {}).get("id"),
        )
        if "workspace_create" in run.steps:
            self.workflows.update_workflow_step(
                run.steps["workspace_create"]["id"],
                status="completed",
                output={"workspaceId": workspace["id"], "path": workspace["path"]},
            )
        return workspace

    def _execute_developer_agent(self, run: _IssueToPatchRun, workspace: dict[str, Any]) -> dict[str, Any]:
        """Delegate implementation and QA to the DeveloperAgent, blocking non-auditable workspaces."""
        payload = run.payload
        workspace_auditable = workspace["isolationType"] == "git_worktree"
        preflight_block_reason = ""
        diff_blocker_state = RUNTIME_UNAVAILABLE_STATUS
        if run.runtime.get("executable") and not workspace_auditable:
            preflight_block_reason = (
                "issue_to_patch requires a Git worktree workspace before executing a productive runtime."
            )
            diff_blocker_state = "workspace_not_auditable"

        return DeveloperAgentRunner(self.connection, root=self.root).run(
            {
                "projectId": payload["projectId"],
                "workspaceId": workspace["id"],
                "taskId": "issue_to_patch",
                "instruction": _developer_instruction_for_issue(
                    title=run.title,
                    issue_text=run.issue_text,
                    target_path=payload.get("targetPath"),
                ),
                "preferredRuntime": payload.get("preferredRuntime"),
                "qaCommands": payload.get("qaCommands") or [],
                "requireApproval": False,
                "maxCostUsd": payload.get("maxCostUsd"),
                "workflowRunId": run.workflow_run["id"],
                "workflowStepId": run.steps.get("implementation", {}).get("id"),
                "qaWorkflowStepId": run.steps.get("qa_validation", {}).get("id"),
                "jobId": run.job_id,
                "preflightBlockReason": preflight_block_reason,
                "diffBlockerState": diff_blocker_state,
            }
        )

    def _developer_verdict(self, run: _IssueToPatchRun, developer_result: dict[str, Any]) -> _Verdict:
        """Map the DeveloperAgent result to the run status, QA verdict and reason."""
        runtime_result: dict[str, Any] = developer_result["runtimeResult"]
        reason = str(runtime_result.get("reason") or developer_result.get("reason") or "")
        verdict = _Verdict(
            *_status_from_developer_result(developer_result, require_approval=run.require_approval)
        )
        if verdict.status == RUNTIME_UNAVAILABLE_STATUS:
            return replace(verdict, reason=reason or verdict.reason)
        return verdict

    def _link_run_evidence(
        self, run: _IssueToPatchRun, developer_result: dict[str, Any], verdict: _Verdict
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Request approval when needed, write security findings and link calls, artifacts and hashes.

        Returns the updated evidence package, the diff summary and the artifact references.
        """
        payload = run.payload
        workspace = developer_result["workspace"]
        job = developer_result["job"]
        agent_run = developer_result["agentRun"]
        runtime = developer_result["runtime"]
        runtime_result: dict[str, Any] = developer_result["runtimeResult"]
        qa_agent_run_id = _qa_agent_run_id(developer_result)
        evidence = developer_result["evidencePackage"]
        diff_summary = dict(developer_result.get("diffSummary") or evidence.get("diffSummary") or {})

        related_agent_run_ids = {agent_run["id"]}
        if qa_agent_run_id:
            related_agent_run_ids.add(qa_agent_run_id)
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) in related_agent_run_ids
        ] or list(evidence.get("toolCalls") or [])
        model_calls = [
            model_call
            for model_call in self.agents.list_model_calls()
            if model_call.get("agentRunId") == agent_run["id"]
        ] or list(evidence.get("modelCalls") or [])
        if verdict.status == "evidence_ready" and verdict.qa_verdict == "needs_human_review":
            self.jobs.create_action_request(
                job_id=job["id"],
                project_id=payload["projectId"],
                action_type="workflow.issue_to_patch.approve_patch",
                risk_level="medium",
                reason="Review patch evidence and QA before accepting issue_to_patch output.",
                payload={
                    "workflowRunId": run.workflow_run["id"],
                    "workflowStepId": run.steps.get("qa_validation", {}).get("id"),
                    "agentRunId": agent_run["id"],
                    "workspaceId": workspace["id"],
                    "runtimeId": runtime["id"],
                    "evidencePackageId": evidence["id"],
                    "diffSummary": diff_summary,
                },
            )
        approvals = self.jobs.list_action_requests(job["id"])
        permission_decision_ids = {
            str((tool_call.get("payload") or {}).get("permissionDecisionId"))
            for tool_call in tool_calls
            if (tool_call.get("payload") or {}).get("permissionDecisionId")
        }
        policy_decisions = [
            decision
            for decision in self.security.list_decisions(project_id=payload["projectId"])
            if not permission_decision_ids or decision["id"] in permission_decision_ids
        ]
        security_findings_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            name="security-findings.json",
            kind="security_findings",
            payload=_security_findings_from_policy(policy_decisions),
            repo=self.evidence,
        )
        diff_summary["securityFindingsArtifactId"] = security_findings_artifact["id"]
        runtime_artifact_ids = [
            str(artifact_id)
            for artifact_id in (
                runtime_result.get("stdoutArtifactId"),
                runtime_result.get("stderrArtifactId"),
                runtime_result.get("outputArtifactId"),
            )
            if artifact_id
        ]
        runtime_artifacts = artifact_records_from_ids(self.evidence, runtime_artifact_ids)
        existing_artifacts = artifact_records_from_ids(self.evidence, list(evidence.get("artifactIds") or []))
        artifact_records = _dedupe_artifact_records(
            [
                *existing_artifacts,
                *runtime_artifacts,
                security_findings_artifact,
            ]
        )
        artifact_records = _dedupe_artifact_records(artifact_records)
        artifact_refs = [artifact_ref(artifact) for artifact in artifact_records]
        runtime_health = redact_secrets(
            {
                **runtime,
                "resultStatus": runtime_result.get("status"),
                "resultReason": runtime_result.get("reason") or runtime.get("reason"),
            }
        )
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            agent_run_id=agent_run["id"],
            artifact_ids=[str(artifact["id"]) for artifact in artifact_records if artifact.get("id")],
            diff_summary=diff_summary,
            runtime_health=runtime_health,
            model_calls=model_calls,
            tool_calls=tool_calls,
            policy_decisions=policy_decisions,
            approvals=approvals,
            artifacts=artifact_refs,
            hashes=artifact_hashes(artifact_records),
            qa_verdict=verdict.qa_verdict,
            risk_notes=[
                {
                    "severity": "medium" if verdict.status != "completed" else "low",
                    "description": verdict.reason,
                    "mitigation": "Configure and approve a real DeveloperAgent runtime, then retry the workflow.",
                }
            ],
        )
        return evidence, diff_summary, artifact_refs

    def _enforce_run_contract(
        self,
        run: _IssueToPatchRun,
        developer_result: dict[str, Any],
        verdict: _Verdict,
        evidence: dict[str, Any],
    ) -> tuple[_Verdict, dict[str, Any]]:
        """Downgrade a completed run to evidence_ready when the evidence contract is incomplete."""
        contract_errors = evidence_package_contract_errors(
            evidence, require_runtime_links=verdict.status == "completed"
        )
        if verdict.status != "completed" or not contract_errors:
            return verdict, evidence
        downgraded = _Verdict(
            *_status_from_developer_result(
                developer_result,
                require_approval=run.require_approval,
                evidence_package_valid=False,
            )
        )
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            qa_verdict=downgraded.qa_verdict,
            risk_notes=[
                {
                    "severity": "medium",
                    "description": downgraded.reason,
                    "mitigation": "Repair evidence package contract before marking workflow completed.",
                    "contractErrors": contract_errors,
                }
            ],
        )
        return downgraded, evidence

    def _persist_run_outcome(
        self,
        run: _IssueToPatchRun,
        developer_result: dict[str, Any],
        *,
        verdict: _Verdict,
        evidence: dict[str, Any],
        diff_summary: dict[str, Any],
        artifact_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Record the final status on agent run, steps, run, workflow and job; build the response."""
        workspace = developer_result["workspace"]
        job = developer_result["job"]
        runtime = developer_result["runtime"]
        runtime_result: dict[str, Any] = developer_result["runtimeResult"]
        qa_results = developer_result["qaResults"]
        agent_run = self._finish_run_agent_and_steps(
            run, developer_result, verdict=verdict, evidence=evidence, diff_summary=diff_summary
        )
        workflow_run = self.workflows.update_workflow_run_status(
            run.workflow_run["id"],
            status=verdict.status,
            metadata={
                **run.workflow_run["metadata"],
                "runtime": runtime,
                "jobId": job["id"],
                "workspaceId": workspace["id"],
                "agentRunId": agent_run["id"],
                "evidencePackageId": evidence["id"],
                "qaVerdict": verdict.qa_verdict,
                "diffSummary": diff_summary,
                "artifacts": artifact_refs,
            },
            completed=verdict.status in TERMINAL_STATUSES,
        )
        workflow = self.workflows.update_workflow_status(
            run.workflow["id"], status=verdict.status, reason=verdict.reason
        )
        job_status = (
            "completed"
            if verdict.status == "completed"
            else "approval_required"
            if verdict.status == "evidence_ready" and verdict.qa_verdict == "needs_human_review"
            else "failed"
        )
        job = self.jobs.update_job_status(
            job["id"],
            status=job_status,
            metadata={
                "status": verdict.status,
                "reason": verdict.reason,
                "evidencePackageId": evidence["id"],
            },
        )
        return {
            "status": verdict.status,
            "reason": verdict.reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run.workflow_run["id"]),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "runtime": runtime,
            "runtimeResult": runtime_result,
            "qaResults": qa_results,
            "diffSummary": diff_summary,
        }

    def _finish_run_agent_and_steps(
        self,
        run: _IssueToPatchRun,
        developer_result: dict[str, Any],
        *,
        verdict: _Verdict,
        evidence: dict[str, Any],
        diff_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Set the developer agent run status and the implementation, local_tests and qa_validation steps."""
        agent_run = developer_result["agentRun"]
        runtime = developer_result["runtime"]
        qa_results = developer_result["qaResults"]
        qa_agent_run_id = _qa_agent_run_id(developer_result)
        steps = run.steps
        agent_run_status = {
            "completed": "completed",
            RUNTIME_UNAVAILABLE_STATUS: "failed",
            "failed": "failed",
            "qa_failed": "failed",
            "evidence_ready": "awaiting_permission" if run.require_approval else "failed",
        }[verdict.status]
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status=agent_run_status,
            output_payload={
                "verdict": verdict.status,
                "summary": verdict.reason,
                "runtime": runtime,
                "runtimeResult": developer_result["runtimeResult"],
                "qaAgentRunId": qa_agent_run_id or None,
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "evidence_refs": [evidence["id"]],
            },
        )
        if "implementation" in steps:
            self.workflows.update_workflow_step(
                steps["implementation"]["id"],
                status="completed" if verdict.status == "completed" else "blocked",
                output={"agentRunId": agent_run["id"], "runtime": runtime, "reason": verdict.reason},
            )
        if "local_tests" in steps:
            self.workflows.update_workflow_step(
                steps["local_tests"]["id"],
                status="completed"
                if qa_verdict_allows_completion(verdict.qa_verdict, qa_results)
                else "blocked",
                output={"qaResults": qa_results, "qaAgentRunId": qa_agent_run_id or None},
            )
        if "qa_validation" in steps:
            self.workflows.update_workflow_step(
                steps["qa_validation"]["id"],
                status="completed" if verdict.qa_verdict == "passed" else "blocked",
                output={"qaVerdict": verdict.qa_verdict, "evidencePackageId": evidence["id"]},
            )
        return agent_run
