"""State machine that drives an issue from agent-generated patch to merged pull request.

Orchestrates the issue_to_patch vertical slice and its downstream integration transitions:
select a runtime, run the developer/QA agents in an isolated workspace, build a contract-checked
evidence package, then gate the manual transitions evidence_ready -> approved_for_integration
-> promoted_to_branch -> pr_created. Each transition validates linked evidence/approvals and
re-checks QA and security before mutating git, so no patch reaches a branch or PR unapproved;
git operations refuse force-push and direct edits to main. Reused by the issue_to_pr runner.
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.developer_agent_contract import is_developer_runtime
from local_control_center.agents.qa_agent import QAAgentRunner, qa_verdict_allows_completion
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_registry import issue_to_patch_prompt
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import (
    artifact_hashes,
    artifact_records_from_ids,
    artifact_ref,
    write_text_artifact,
)
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now
from local_control_center.workflows.github_pull_requests import (
    GitHubPullRequestConfigError,
    create_github_pull_request,
    github_pull_request_config_from_env,
)
from local_control_center.workflows.repository import ISSUE_TO_PATCH_STEPS, WorkflowsRepository
from local_control_center.workspaces_projects.cleanup import capture_workspace_snapshot
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff, slugify_branch_segment
from local_control_center.workspaces_projects.repository import WorkspacesRepository

CLI_RUNTIME_IDS = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
RUNTIME_UNAVAILABLE_STATUS = "runtime_unavailable"
APPROVED_FOR_INTEGRATION_STATUS = "approved_for_integration"
PROMOTED_TO_BRANCH_STATUS = "promoted_to_branch"
PROMOTION_FAILED_STATUS = "promotion_failed"
PR_CREATED_STATUS = "pr_created"
PR_FAILED_STATUS = "pr_failed"
PR_UNAVAILABLE_STATUS = "pr_unavailable"
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


def _status_for_failure(runtime: dict[str, Any]) -> tuple[str, str]:
    return RUNTIME_UNAVAILABLE_STATUS, str(
        runtime.get("reason") or "No executable runtime is configured for issue_to_patch."
    )


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


def _diff_summary(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "changedFiles": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
    }


def _evidence_git_diff_ref(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": diff.get("kind", "git_diff"),
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "branch": diff.get("branch"),
        "headCommit": diff.get("headCommit"),
        "statusRaw": diff.get("statusRaw", ""),
        "status": diff.get("status") or [],
        "nameOnly": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
        "patch": diff.get("patch") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
    }


def _workspace_manifest_ref(workspace: dict[str, Any]) -> dict[str, Any] | None:
    manifest = (workspace.get("metadata") or {}).get("workspaceManifest") or {}
    if not manifest:
        return None
    return {"kind": "workspace_manifest", "status": "captured", **manifest}


def _final_diff_refs(workspace: dict[str, Any], diff: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [_evidence_git_diff_ref(diff)]
    manifest = _workspace_manifest_ref(workspace)
    if manifest:
        refs.append(manifest)
    refs.append(capture_workspace_snapshot(workspace["path"]))
    return refs


def _display_command(argv: list[str]) -> str:
    if not argv:
        return ""
    executable = Path(argv[0]).name or str(argv[0])
    lowered = executable.lower()
    if lowered in {"python.exe", "python3.exe", "py.exe"}:
        executable = "python"
    return subprocess.list2cmdline([executable, *[str(item) for item in argv[1:]]])


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


def _runtime_unavailable_result(reason: str) -> dict[str, Any]:
    return {
        "status": RUNTIME_UNAVAILABLE_STATUS,
        "reason": reason,
        "execution": "not_executed",
        "blockedBy": RUNTIME_UNAVAILABLE_STATUS,
    }


def _write_text_evidence_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    name: str,
    kind: str,
    suffix: str,
    content: str,
    mime_type: str,
    repo: EvidenceRepository,
    source: str = "issue_to_patch",
) -> dict[str, Any]:
    artifact_id = f"artifact-{uuid.uuid4()}"
    artifact_file = write_text_artifact(root=root, artifact_id=artifact_id, suffix=suffix, content=content)
    return repo.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=evidence_id,
        kind=kind,
        path=artifact_file["path"],
        content_hash=artifact_file["hash"] or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        metadata={
            "name": name,
            "source": source,
            "mimeType": mime_type,
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
    )


def _write_json_evidence_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    name: str,
    kind: str,
    payload: Any,
    repo: EvidenceRepository,
    source: str = "issue_to_patch",
) -> dict[str, Any]:
    return _write_text_evidence_artifact(
        root=root,
        project_id=project_id,
        evidence_id=evidence_id,
        name=name,
        kind=kind,
        suffix=".json",
        content=json_dumps(redact_secrets(payload)),
        mime_type="application/json",
        repo=repo,
        source=source,
    )


def _dedupe_artifact_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        record_id = str(record.get("id") or "")
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        deduped.append(record)
    return deduped


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


def _artifact_content_bytes(artifact: dict[str, Any]) -> bytes:
    path = Path(str(artifact.get("path") or ""))
    if not path.exists() or not path.is_file():
        raise ValueError(f"Artifact file is missing: {artifact.get('id')}")
    content = path.read_bytes()
    expected_hash = str(artifact.get("hash") or "")
    if not expected_hash:
        raise ValueError(f"Artifact hash is missing: {artifact.get('id')}")
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"Artifact hash mismatch: {artifact.get('id')}")
    return content


def _find_linked_artifact(
    artifacts: list[dict[str, Any]],
    *,
    artifact_id: str | None = None,
    kind: str | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    for artifact in artifacts:
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        if artifact_id and artifact.get("id") != artifact_id:
            continue
        if kind and artifact.get("kind") != kind:
            continue
        if name and metadata.get("name") != name:
            continue
        if artifact_id or kind or name:
            return artifact
    return None


def _validate_patch_artifact(evidence: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    diff_summary = evidence.get("diffSummary") if isinstance(evidence.get("diffSummary"), dict) else {}
    patch_artifact = _find_linked_artifact(
        artifacts,
        artifact_id=diff_summary.get("patchArtifactId"),
        kind="git_patch",
    ) or _find_linked_artifact(artifacts, kind="git_patch", name="diff.patch")
    if not patch_artifact:
        raise ValueError("issue_to_patch approval requires a git_patch artifact.")
    patch_content = _artifact_content_bytes(patch_artifact)
    if not patch_content.strip():
        raise ValueError("issue_to_patch approval requires a non-empty patch artifact.")
    return patch_artifact


def _validate_security_findings(evidence: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    diff_summary = evidence.get("diffSummary") if isinstance(evidence.get("diffSummary"), dict) else {}
    findings_artifact = _find_linked_artifact(
        artifacts,
        artifact_id=diff_summary.get("securityFindingsArtifactId"),
        kind="security_findings",
    ) or _find_linked_artifact(artifacts, kind="security_findings", name="security-findings.json")
    if not findings_artifact:
        raise ValueError("issue_to_patch approval requires a security findings artifact.")
    payload = json_loads(_artifact_content_bytes(findings_artifact).decode("utf-8"), {})
    if not isinstance(payload, dict):
        raise ValueError("Security findings artifact must contain a JSON object.")
    if str(payload.get("status") or "").lower() == "blocked":
        raise ValueError("Security findings are blocking; patch cannot be approved for integration.")
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("decision") or "").lower() in {"deny", "requires_human", "requires_approval"}:
            raise ValueError("Security findings include a blocking policy decision.")
    return payload


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


def _verify_base_commit(repo_path: Path, base_commit: str) -> None:
    if not git_available():
        raise ValueError("promote_patch_to_branch requires the git CLI.")
    result = run_git(["-C", str(repo_path), "cat-file", "-e", f"{base_commit}^{{commit}}"])
    if result.returncode != 0:
        raise ValueError("promote_patch_to_branch base commit is not present in the project repository.")


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


def _git_operation_result(args: list[str], *, cwd: Path) -> dict[str, Any]:
    display = subprocess.list2cmdline(["git", *args])
    result = run_git(args, cwd=cwd)
    return {
        "command": display,
        "argv": ["git", *args],
        "cwd": str(cwd),
        "returnCode": result.returncode,
        "stdout": (result.stdout or "")[:4000],
        "stderr": (result.stderr or "")[:4000],
        "status": "passed" if result.returncode == 0 else "failed",
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
        command = str(result.get("command") or _display_command(result.get("argv") or []) or "qa_command")
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


def _validate_qa_for_patch_approval(evidence: dict[str, Any], *, action_approved: bool) -> None:
    qa_verdict = str(evidence.get("qaVerdict") or "").lower()
    if qa_verdict == "passed":
        if not qa_verdict_allows_completion("passed", evidence.get("testResults") or []):
            raise ValueError("Passed QA verdict requires real passing QA command evidence.")
        return
    if qa_verdict == "needs_human_review" and action_approved:
        if not qa_verdict_allows_completion("passed", evidence.get("testResults") or []):
            raise ValueError("Human-reviewed patch approval still requires real passing QA command evidence.")
        return
    raise ValueError("issue_to_patch approval requires passed QA or needs_human_review with approval.")


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


def _write_git_status_artifacts(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    diff: dict[str, Any],
    repo: EvidenceRepository,
    source: str = "issue_to_patch",
) -> list[dict[str, Any]]:
    status_payload = {
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "branch": diff.get("branch"),
        "headCommit": diff.get("headCommit"),
        "statusRaw": diff.get("statusRaw", ""),
        "status": diff.get("status") or [],
        "nameOnly": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
    }
    return [
        _write_text_evidence_artifact(
            root=root,
            project_id=project_id,
            evidence_id=evidence_id,
            name="git-status.txt",
            kind="git_status",
            suffix=".git-status.txt",
            content=str(diff.get("statusRaw") or ""),
            mime_type="text/plain",
            repo=repo,
            source=source,
        ),
        _write_json_evidence_artifact(
            root=root,
            project_id=project_id,
            evidence_id=evidence_id,
            name="git-status.json",
            kind="git_status",
            payload=status_payload,
            repo=repo,
            source=source,
        ),
    ]


def _security_findings_from_policy(policy_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    blocked = False
    for decision in policy_decisions:
        decision_value = str(decision.get("decision") or "").lower()
        risk_level = str(decision.get("riskLevel") or decision.get("risk_level") or "").lower()
        if decision_value in {"deny", "requires_human"}:
            blocked = True
        if decision_value not in {"allow", "allowed"} or risk_level in {"high", "critical"}:
            findings.append(
                {
                    "policyDecisionId": decision.get("id"),
                    "decision": decision.get("decision"),
                    "riskLevel": decision.get("riskLevel") or decision.get("risk_level"),
                    "reason": decision.get("reason"),
                    "agentId": decision.get("agentId"),
                    "tool": decision.get("tool"),
                }
            )
    return {
        "status": "blocked" if blocked else "risk" if findings else "passed",
        "source": "policy_decisions",
        "findings": redact_secrets(findings),
        "policyDecisionIds": [decision.get("id") for decision in policy_decisions if decision.get("id")],
    }


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

        job_id = str(evidence.get("jobId") or run_metadata.get("jobId") or "")
        agent_run_id = str(evidence.get("agentRunId") or run_metadata.get("agentRunId") or "")
        workspace_id = str(evidence.get("workspaceId") or run_metadata.get("workspaceId") or "")
        if not job_id or not agent_run_id or not workspace_id:
            raise ValueError("issue_to_patch approval requires linked job, agent run, and workspace records.")
        job = self.jobs.get_job(job_id)
        agent_run = self.agents.get_agent_run(agent_run_id)
        workspace = self.workspaces.get_workspace(workspace_id)

        action_requests = self.jobs.list_action_requests(job_id)
        approval_actions = [
            action
            for action in action_requests
            if action["actionType"] == ISSUE_TO_PATCH_APPROVAL_ACTION
            and (action.get("payload") or {}).get("workflowRunId") == run_id
            and (action.get("payload") or {}).get("evidencePackageId") == evidence_id
        ]
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

        latest_approvals = self.jobs.list_action_requests(job_id)
        evidence = self.evidence.update_evidence_links(evidence_id, approvals=latest_approvals)
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=True,
            require_workflow_run=True,
        )
        if contract_errors:
            raise ValueError("Evidence package contract is incomplete: " + "; ".join(contract_errors))
        artifacts = self.evidence.list_artifacts(evidence_id)
        _validate_patch_artifact(evidence, artifacts)
        _validate_qa_for_patch_approval(evidence, action_approved=True)
        security_findings = _validate_security_findings(evidence, artifacts)

        approved_at = utc_now()
        transition_payload = {
            "status": APPROVED_FOR_INTEGRATION_STATUS,
            "reason": clean_reason,
            "approvedAt": approved_at,
            "approvedBy": actor,
            "actionRequestId": approved_action["id"],
            "evidencePackageId": evidence_id,
            "jobId": job_id,
            "agentRunId": agent_run_id,
            "workspaceId": workspace_id,
            "securityFindingsStatus": security_findings.get("status"),
        }
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=APPROVED_FOR_INTEGRATION_STATUS,
            metadata={**run_metadata, **transition_payload},
            completed=False,
            clear_completed=True,
        )
        workflow = self.workflows.update_workflow_status(
            workflow["id"],
            status=APPROVED_FOR_INTEGRATION_STATUS,
            reason=clean_reason,
        )
        job = self.jobs.update_job_status(
            job_id,
            status="approved",
            metadata=transition_payload,
        )
        agent_output = dict(agent_run.get("output") or {})
        agent_output.update(
            {
                "verdict": APPROVED_FOR_INTEGRATION_STATUS,
                "summary": clean_reason,
                "approvedForIntegration": True,
                "approvalActionRequestId": approved_action["id"],
                "evidence_refs": sorted({*agent_output.get("evidence_refs", []), evidence_id}),
            }
        )
        agent_run = self.agents.update_agent_run_status(
            agent_run_id, status="approved", output_payload=agent_output
        )

        for step in self.workflows.list_workflow_steps(workflow_run_id=run_id):
            if step["name"] == "qa_validation":
                self.workflows.update_workflow_step(
                    step["id"],
                    status="completed",
                    output={
                        **(step.get("output") or {}),
                        "qaVerdict": evidence["qaVerdict"],
                        "evidencePackageId": evidence_id,
                        "approvalActionRequestId": approved_action["id"],
                    },
                )
            if step["name"] == "technical_review":
                self.workflows.update_workflow_step(
                    step["id"],
                    status="completed",
                    output={"approvedForIntegration": True, "reason": clean_reason},
                    metadata={**(step.get("metadata") or {}), "gateState": APPROVED_FOR_INTEGRATION_STATUS},
                )

        self.workflows.record_workflow_event(
            workflow_id=workflow["id"],
            workflow_run_id=run_id,
            project_id=workflow["projectId"],
            event_type=f"workflow.issue_to_patch.{APPROVED_FOR_INTEGRATION_STATUS}",
            payload=transition_payload,
        )
        EventBus(self.connection).record_event(
            project_id=workflow["projectId"],
            job_id=job_id,
            event_type=f"workflow.issue_to_patch.{APPROVED_FOR_INTEGRATION_STATUS}",
            payload=transition_payload,
        )
        EventBus(self.connection).record_audit(
            project_id=workflow["projectId"],
            action=f"workflow.issue_to_patch.{APPROVED_FOR_INTEGRATION_STATUS}",
            actor=actor,
            target=run_id,
            payload=transition_payload,
        )

        return {
            "status": APPROVED_FOR_INTEGRATION_STATUS,
            "reason": clean_reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run_id),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": self.evidence.get_evidence_package(evidence_id),
            "runtime": evidence.get("runtimeHealth") or (run_metadata.get("runtime") or {}),
            "runtimeResult": (agent_run.get("output") or {}).get("runtimeResult") or {},
            "qaResults": evidence.get("testResults") or [],
            "diffSummary": evidence.get("diffSummary") or {},
        }

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
        approval_action_type = (
            ISSUE_TO_PR_APPROVAL_ACTION
            if workflow["kind"] == "issue_to_pr"
            else ISSUE_TO_PATCH_APPROVAL_ACTION
        )

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

        original_job_id = str(approved_evidence.get("jobId") or run_metadata.get("jobId") or "")
        original_agent_run_id = str(
            approved_evidence.get("agentRunId") or run_metadata.get("agentRunId") or ""
        )
        original_workspace_id = str(
            approved_evidence.get("workspaceId") or run_metadata.get("workspaceId") or ""
        )
        if not original_job_id or not original_agent_run_id or not original_workspace_id:
            raise ValueError(
                "promote_patch_to_branch requires linked approved job, agent run, and workspace records."
            )
        original_workspace = self.workspaces.get_workspace(original_workspace_id)

        approval_actions = [
            action
            for action in self.jobs.list_action_requests(original_job_id)
            if action["actionType"] == approval_action_type
            and action["status"] == "approved"
            and (action.get("payload") or {}).get("workflowRunId") == run_id
            and (action.get("payload") or {}).get("evidencePackageId") == approved_evidence_id
        ]
        if not approval_actions:
            raise ValueError(
                "promote_patch_to_branch requires approved patch evidence before branch promotion."
            )

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

        project_id = workflow["projectId"]
        project_path = _project_path(self.connection, project_id)
        base_commit = _approved_evidence_base_commit(approved_evidence, original_workspace)
        _verify_base_commit(project_path, base_commit)
        target_branch = _promotion_branch_name(requested=branch_name, workflow=workflow, run_id=run_id)
        steps = {step["name"]: step for step in self.workflows.list_workflow_steps(workflow_run_id=run_id)}

        profile_id = "aido_patch_promoter"
        profile = self.agents.upsert_agent_profile(
            {
                "id": profile_id,
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
            project_id=project_id,
            kind="workflow.issue_to_patch.promote_patch_to_branch",
            workflow_run_id=run_id,
            workflow_step_id=steps.get("technical_review", {}).get("id"),
            status="running",
            payload={
                "command": "promote_patch_to_branch",
                "workflowRunId": run_id,
                "approvedEvidencePackageId": approved_evidence_id,
                "patchArtifactId": patch_artifact["id"],
                "patchArtifactHash": patch_artifact["hash"],
                "baseCommit": base_commit,
                "branchName": target_branch,
            },
        )["job"]
        promoter_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id="promote_patch_to_branch",
            input_payload={
                "workflowRunId": run_id,
                "approvedEvidencePackageId": approved_evidence_id,
                "patchArtifactId": patch_artifact["id"],
                "patchArtifactHash": patch_artifact["hash"],
                "baseCommit": base_commit,
                "branchName": target_branch,
            },
            output_payload={},
            job_id=promotion_job["id"],
            workflow_run_id=run_id,
            workflow_step_id=steps.get("technical_review", {}).get("id"),
            status="running",
        )

        workspace = self.workspaces.allocate_workspace(
            project_id=project_id,
            task_id=f"promote-patch-{run_id}-{uuid.uuid4().hex[:8]}",
            agent_id=profile_id,
            reason="promote_patch_to_branch local branch",
            isolation_type="git_worktree",
            workflow_run_id=run_id,
            workflow_step_id=steps.get("technical_review", {}).get("id"),
            base_branch=base_commit,
            branch_name=target_branch,
        )
        workspace_path = Path(workspace["path"])
        patch_path = Path(str(patch_artifact["path"])).resolve(strict=False)
        apply_check = _git_operation_result(["apply", "--check", str(patch_path)], cwd=workspace_path)
        apply_result: dict[str, Any] | None = None
        qa_summary: dict[str, Any] | None = None
        if apply_check["returnCode"] == 0:
            apply_result = _git_operation_result(["apply", str(patch_path)], cwd=workspace_path)
            if apply_result["returnCode"] == 0:
                qa_summary = QAAgentRunner(self.connection, root=self.root).run_for_context(
                    project_id=project_id,
                    workspace_id=workspace["id"],
                    task_id="promote_patch_to_branch",
                    commands=_promotion_qa_commands(approved_evidence, qa_commands),
                    workflow_run_id=run_id,
                    workflow_step_id=steps.get("qa_validation", {}).get("id"),
                    job_id=promotion_job["id"],
                    parent_agent_run_id=promoter_run["id"],
                    metadata={
                        "source": "promote_patch_to_branch",
                        "approvedEvidencePackageId": approved_evidence_id,
                        "patchArtifactId": patch_artifact["id"],
                    },
                )
        qa_results = (qa_summary or {}).get("results") or []
        qa_artifact_ids = (qa_summary or {}).get("artifactIds") or []
        diff = capture_git_diff(workspace_path)
        final_status, qa_verdict, final_reason = _promotion_status_from_results(
            apply_check=apply_check,
            apply_result=apply_result,
            qa_results=qa_results,
            diff=diff,
        )

        diff_summary = _diff_summary(diff)
        diff_summary.update(
            {
                "branch": target_branch,
                "baseCommit": base_commit,
                "patchArtifactId": patch_artifact["id"],
                "patchArtifactHash": patch_artifact["hash"],
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionCommand": "promote_patch_to_branch",
            }
        )
        promotion_logs = [
            {
                "source": "promote_patch_to_branch",
                "reason": final_reason,
                "approvedEvidencePackageId": approved_evidence_id,
                "patchArtifactId": patch_artifact["id"],
                "patchArtifactHash": patch_artifact["hash"],
                "patchSizeBytes": len(patch_content),
                "baseCommit": base_commit,
                "branchName": target_branch,
                "gitApplyCheck": apply_check,
                "gitApply": apply_result,
                "qaAgentRunId": (qa_summary or {}).get("agentRun", {}).get("id"),
            }
        ]
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=run_id,
            workflow_step_id=steps.get("qa_validation", {}).get("id"),
            agent_id=profile_id,
            agent_run_id=promoter_run["id"],
            job_id=promotion_job["id"],
            workspace_id=workspace["id"],
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
            test_results=qa_results,
            logs=promotion_logs,
            diff_refs=_final_diff_refs(workspace, diff),
            diff_summary=diff_summary,
            runtime_health={
                "id": "git.cli",
                "status": final_status,
                "available": git_available(),
                "executable": git_available(),
                "baseCommit": base_commit,
                "branchName": target_branch,
                "reason": final_reason,
            },
            approvals=self.jobs.list_action_requests(original_job_id),
            evidence_source="verified_completion" if qa_verdict == "passed" else "evidence_collected",
            qa_verdict=qa_verdict,
            risk_notes=[
                {
                    "severity": "low" if final_status == PROMOTED_TO_BRANCH_STATUS else "high",
                    "description": final_reason,
                    "mitigation": "Fix the branch promotion blocker and retry from the approved evidence package.",
                }
            ],
        )
        if qa_artifact_ids:
            QAAgentRunner(self.connection, root=self.root).attach_artifacts_to_evidence(
                evidence_id=evidence["id"],
                artifact_ids=qa_artifact_ids,
            )
        status_artifacts = _write_git_status_artifacts(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            diff=diff,
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
                "gitApplyCheck": apply_check,
                "gitApply": apply_result,
                "qaResults": qa_results,
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
            payload={"results": qa_results, "qaAgentRunId": (qa_summary or {}).get("agentRun", {}).get("id")},
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
                "status": final_status,
                "reason": final_reason,
                "workflowRunId": run_id,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "jobId": promotion_job["id"],
                "agentRunId": promoter_run["id"],
                "workspaceId": workspace["id"],
                "branchName": target_branch,
                "baseCommit": base_commit,
                "patchArtifact": artifact_ref(patch_artifact),
                "diffSummary": diff_summary,
                "qaResults": qa_results,
            },
            repo=self.evidence,
            source="promote_patch_to_branch",
        )
        qa_artifacts = artifact_records_from_ids(self.evidence, qa_artifact_ids)
        promotion_artifacts = [
            patch_artifact,
            *qa_artifacts,
            *status_artifacts,
            command_artifact,
            qa_results_artifact,
            manifest_artifact,
        ]
        diff_summary["gitStatusArtifactIds"] = [artifact["id"] for artifact in status_artifacts]
        diff_summary["qaResultsArtifactId"] = qa_results_artifact["id"]
        diff_summary["manifestArtifactId"] = manifest_artifact["id"]
        related_agent_run_ids = {promoter_run["id"]}
        if qa_summary and (qa_summary.get("agentRun") or {}).get("id"):
            related_agent_run_ids.add(str(qa_summary["agentRun"]["id"]))
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) in related_agent_run_ids
        ]
        evidence = self.evidence.update_evidence_links(
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
        completed = final_status == PROMOTED_TO_BRANCH_STATUS
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=completed,
            require_workflow_run=True,
        )
        if completed and contract_errors:
            final_status = PROMOTION_FAILED_STATUS
            qa_verdict = "blocked"
            final_reason = "Promotion evidence package contract is incomplete: " + "; ".join(contract_errors)
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_verdict,
                risk_notes=[
                    {
                        "severity": "high",
                        "description": final_reason,
                        "mitigation": "Regenerate promotion evidence with artifact refs and SHA-256 hashes before marking promoted.",
                    }
                ],
            )
            completed = False

        promoter_run = self.agents.update_agent_run_status(
            promoter_run["id"],
            status="completed" if completed else "failed",
            output_payload={
                "verdict": final_status,
                "summary": final_reason,
                "branchName": target_branch,
                "baseCommit": base_commit,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "patchArtifactId": patch_artifact["id"],
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "evidence_refs": [approved_evidence_id, evidence["id"]],
            },
        )
        promotion_job = self.jobs.update_job_status(
            promotion_job["id"],
            status="completed" if completed else "failed",
            metadata={
                "status": final_status,
                "reason": final_reason,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": evidence["id"],
                "branchName": target_branch,
                "baseCommit": base_commit,
            },
        )
        workflow_metadata = {
            **run_metadata,
            "originalEvidencePackageId": approved_evidence_id,
            "promotionEvidencePackageId": evidence["id"],
            "promotionJobId": promotion_job["id"],
            "promotionAgentRunId": promoter_run["id"],
            "promotionWorkspaceId": workspace["id"],
            "promotedBranchName": target_branch if completed else None,
            "promotionStatus": final_status,
            "promotionReason": final_reason,
            "promotionDiffSummary": diff_summary,
        }
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=final_status,
            metadata=workflow_metadata,
            completed=completed,
            clear_completed=not completed,
        )
        workflow = self.workflows.update_workflow_status(
            workflow["id"], status=final_status, reason=final_reason
        )
        if "local_tests" in steps:
            self.workflows.update_workflow_step(
                steps["local_tests"]["id"],
                status="completed" if qa_verdict_allows_completion(qa_verdict, qa_results) else "blocked",
                output={
                    **(steps["local_tests"].get("output") or {}),
                    "promotionQaResults": qa_results,
                    "promotionEvidencePackageId": evidence["id"],
                },
            )
        if "qa_validation" in steps:
            self.workflows.update_workflow_step(
                steps["qa_validation"]["id"],
                status="completed" if completed else "blocked",
                output={
                    **(steps["qa_validation"].get("output") or {}),
                    "promotionQaVerdict": qa_verdict,
                    "promotionEvidencePackageId": evidence["id"],
                },
            )
        if "technical_review" in steps:
            self.workflows.update_workflow_step(
                steps["technical_review"]["id"],
                status="completed" if completed else "blocked",
                output={
                    **(steps["technical_review"].get("output") or {}),
                    "promotionStatus": final_status,
                    "branchName": target_branch,
                    "promotionEvidencePackageId": evidence["id"],
                    "reason": final_reason,
                },
                metadata={
                    **(steps["technical_review"].get("metadata") or {}),
                    "gateState": final_status,
                },
            )
        event_payload = {
            "workflowId": workflow["id"],
            "workflowRunId": run_id,
            "approvedEvidencePackageId": approved_evidence_id,
            "promotionEvidencePackageId": evidence["id"],
            "jobId": promotion_job["id"],
            "agentRunId": promoter_run["id"],
            "workspaceId": workspace["id"],
            "branchName": target_branch,
            "baseCommit": base_commit,
            "status": final_status,
            "reason": final_reason,
        }
        self.workflows.record_workflow_event(
            workflow_id=workflow["id"],
            workflow_run_id=run_id,
            project_id=project_id,
            event_type=f"workflow.issue_to_patch.{final_status}",
            payload=event_payload,
            severity="info" if completed else "warning",
        )
        EventBus(self.connection).record_event(
            project_id=project_id,
            job_id=promotion_job["id"],
            event_type=f"workflow.issue_to_patch.{final_status}",
            payload=event_payload,
        )
        EventBus(self.connection).record_audit(
            project_id=project_id,
            action=f"workflow.issue_to_patch.{final_status}",
            actor=actor,
            target=run_id,
            payload=event_payload,
        )
        return {
            "status": final_status,
            "reason": final_reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run_id),
            "workspace": workspace,
            "job": promotion_job,
            "agentRun": promoter_run,
            "evidencePackage": evidence,
            "runtime": evidence.get("runtimeHealth") or {},
            "runtimeResult": {"gitApplyCheck": apply_check, "gitApply": apply_result},
            "qaResults": qa_results,
            "diffSummary": diff_summary,
        }

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
        approval_action_type = (
            ISSUE_TO_PR_APPROVAL_ACTION
            if workflow["kind"] == "issue_to_pr"
            else ISSUE_TO_PATCH_APPROVAL_ACTION
        )

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
            for action in self.jobs.list_action_requests(original_job_id)
            if action["actionType"] == approval_action_type
            and action["status"] == "approved"
            and (action.get("payload") or {}).get("workflowRunId") == run_id
            and (action.get("payload") or {}).get("evidencePackageId") == approved_evidence_id
        ]
        if not approval_actions:
            raise ValueError("create_pull_request requires approved patch evidence before PR creation.")
        approval_reason = _approval_reason_summary(
            approval_actions=approval_actions, run_metadata=run_metadata
        )
        pr_title = str(title or "").strip() or _default_pull_request_title(workflow, promoted_branch_name)
        pr_body = _build_pull_request_body(
            approved_evidence=approved_evidence,
            promotion_evidence=promotion_evidence,
            security_findings=security_findings,
            approval_reason=approval_reason,
            request_reason=clean_reason,
            branch_name=promoted_branch_name,
            base_branch=resolved_base_branch,
        )

        project_id = workflow["projectId"]
        profile_id = "aido_pull_request_creator"
        profile = self.agents.upsert_agent_profile(
            {
                "id": profile_id,
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
            workflow_step_id=workflow_step_id,
            status="running",
            payload={
                "command": "create_pull_request",
                "workflowRunId": run_id,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": promotion_evidence_id,
                "branchName": promoted_branch_name,
                "baseBranch": resolved_base_branch,
            },
        )["job"]
        pr_agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id="create_pull_request",
            input_payload={
                "workflowRunId": run_id,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": promotion_evidence_id,
                "branchName": promoted_branch_name,
                "baseBranch": resolved_base_branch,
                "title": pr_title,
            },
            output_payload={},
            job_id=pr_job["id"],
            workflow_run_id=run_id,
            workflow_step_id=workflow_step_id,
            status="running",
        )

        github_result: dict[str, Any]
        pull_request: dict[str, Any] | None
        try:
            github_config = github_pull_request_config_from_env()
        except GitHubPullRequestConfigError as error:
            final_status = PR_UNAVAILABLE_STATUS
            qa_verdict = "blocked"
            final_reason = error.reason
            github_result = {
                "status": "unavailable",
                "reason": error.reason,
                "missing": error.missing,
                "request": None,
                "response": {},
            }
            pull_request = None
            configured = False
        else:
            configured = True
            github_result = create_github_pull_request(
                github_config,
                title=pr_title,
                head=promoted_branch_name,
                base=resolved_base_branch,
                body=pr_body,
            )
            if github_result.get("status") == "created":
                final_status = PR_CREATED_STATUS
                qa_verdict = "evidence_collected"
                final_reason = "GitHub pull request created from promoted branch."
                pull_request = {
                    "status": "created",
                    "number": github_result.get("number"),
                    "id": github_result.get("id"),
                    "url": github_result.get("url"),
                    "htmlUrl": github_result.get("htmlUrl"),
                    "repository": github_result.get("repository"),
                    "head": promoted_branch_name,
                    "base": resolved_base_branch,
                }
            else:
                final_status = PR_FAILED_STATUS
                qa_verdict = "failed"
                final_reason = str(github_result.get("reason") or "GitHub pull request creation failed.")
                pull_request = {
                    "status": "failed",
                    "httpStatus": github_result.get("httpStatus"),
                    "repository": github_config.repository,
                    "head": promoted_branch_name,
                    "base": resolved_base_branch,
                    "reason": final_reason,
                }

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
                if final_status == PR_CREATED_STATUS
                else "blocked"
                if final_status == PR_UNAVAILABLE_STATUS
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
            "branch": promoted_branch_name,
            "baseBranch": resolved_base_branch,
            "approvedEvidencePackageId": approved_evidence_id,
            "promotionEvidencePackageId": promotion_evidence_id,
            "pullRequestStatus": final_status,
            "pullRequest": pull_request,
        }
        pr_logs = [
            {
                "source": "create_pull_request",
                "status": final_status,
                "reason": final_reason,
                "configured": configured,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": promotion_evidence_id,
                "branchName": promoted_branch_name,
                "baseBranch": resolved_base_branch,
                "request": request_payload,
                "response": github_result.get("response") or {},
            }
        ]
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=run_id,
            workflow_step_id=workflow_step_id,
            agent_id=profile_id,
            agent_run_id=pr_agent_run["id"],
            job_id=pr_job["id"],
            workspace_id=promotion_workspace["id"],
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
            diff_refs=promotion_evidence.get("diffRefs") or [],
            diff_summary=diff_summary,
            runtime_health={
                "id": "github.rest",
                "status": final_status,
                "configured": configured,
                "available": final_status == PR_CREATED_STATUS,
                "reason": final_reason,
                "remote": (request_payload or {}).get("repository"),
            },
            approvals=approval_actions,
            evidence_source="evidence_collected",
            qa_verdict=qa_verdict,
            risk_notes=[
                {
                    "severity": "low" if final_status == PR_CREATED_STATUS else "high",
                    "description": final_reason,
                    "mitigation": "Configure GitHub or resolve the GitHub API error, then retry from the promoted branch.",
                }
            ],
        )
        request_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=project_id,
            evidence_id=evidence["id"],
            name="pull-request-request.json",
            kind="evidence_manifest",
            payload={
                "status": final_status,
                "title": pr_title,
                "body": pr_body,
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
                "status": final_status,
                "reason": final_reason,
                "pullRequest": pull_request,
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
                "status": final_status,
                "reason": final_reason,
                "workflowRunId": run_id,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "jobId": pr_job["id"],
                "agentRunId": pr_agent_run["id"],
                "workspaceId": promotion_workspace["id"],
                "branchName": promoted_branch_name,
                "baseBranch": resolved_base_branch,
                "pullRequest": pull_request,
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
        if final_status == PR_CREATED_STATUS and pull_request:
            _record_pull_request(
                self.connection,
                project_id=project_id,
                workspace_id=promotion_workspace["id"],
                status="created",
                pull_request=pull_request,
                metadata={
                    "workflowRunId": run_id,
                    "approvedEvidencePackageId": approved_evidence_id,
                    "promotionEvidencePackageId": promotion_evidence_id,
                    "pullRequestEvidencePackageId": evidence["id"],
                    "branchName": promoted_branch_name,
                    "baseBranch": resolved_base_branch,
                },
            )

        pr_agent_run = self.agents.update_agent_run_status(
            pr_agent_run["id"],
            status="completed"
            if final_status == PR_CREATED_STATUS
            else "blocked"
            if final_status == PR_UNAVAILABLE_STATUS
            else "failed",
            output_payload={
                "verdict": final_status,
                "summary": final_reason,
                "pullRequest": pull_request,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "evidence_refs": [approved_evidence_id, promotion_evidence_id, evidence["id"]],
            },
        )
        pr_job = self.jobs.update_job_status(
            pr_job["id"],
            status="completed" if final_status == PR_CREATED_STATUS else "failed",
            metadata={
                "status": final_status,
                "reason": final_reason,
                "approvedEvidencePackageId": approved_evidence_id,
                "promotionEvidencePackageId": promotion_evidence_id,
                "pullRequestEvidencePackageId": evidence["id"],
                "pullRequest": pull_request,
            },
        )
        workflow_metadata = {
            **run_metadata,
            "pullRequestStatus": final_status,
            "pullRequestReason": final_reason,
            "pullRequestEvidencePackageId": evidence["id"],
            "pullRequestJobId": pr_job["id"],
            "pullRequestAgentRunId": pr_agent_run["id"],
            "pullRequest": pull_request,
        }
        workflow_completed = final_status == PR_CREATED_STATUS
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=PR_CREATED_STATUS if workflow_completed else PROMOTED_TO_BRANCH_STATUS,
            metadata=workflow_metadata,
            completed=workflow_completed,
            clear_completed=not workflow_completed,
        )
        workflow = self.workflows.update_workflow_status(
            workflow["id"],
            status=PR_CREATED_STATUS if workflow_completed else PROMOTED_TO_BRANCH_STATUS,
            reason=final_reason,
        )
        if "pr_creation" in steps:
            self.workflows.update_workflow_step(
                steps["pr_creation"]["id"],
                status="completed" if workflow_completed else "blocked",
                output={
                    **(steps["pr_creation"].get("output") or {}),
                    "pullRequestStatus": final_status,
                    "pullRequestEvidencePackageId": evidence["id"],
                    "pullRequest": pull_request,
                },
                metadata={
                    **(steps["pr_creation"].get("metadata") or {}),
                    "gateState": final_status,
                },
            )
        event_payload = {
            "workflowId": workflow["id"],
            "workflowRunId": run_id,
            "approvedEvidencePackageId": approved_evidence_id,
            "promotionEvidencePackageId": promotion_evidence_id,
            "pullRequestEvidencePackageId": evidence["id"],
            "jobId": pr_job["id"],
            "agentRunId": pr_agent_run["id"],
            "branchName": promoted_branch_name,
            "baseBranch": resolved_base_branch,
            "status": final_status,
            "reason": final_reason,
            "pullRequest": pull_request,
        }
        self.workflows.record_workflow_event(
            workflow_id=workflow["id"],
            workflow_run_id=run_id,
            project_id=project_id,
            event_type=f"workflow.issue_to_patch.{final_status}",
            payload=event_payload,
            severity="info" if workflow_completed else "warning",
        )
        EventBus(self.connection).record_event(
            project_id=project_id,
            job_id=pr_job["id"],
            event_type=f"workflow.issue_to_patch.{final_status}",
            payload=event_payload,
        )
        EventBus(self.connection).record_audit(
            project_id=project_id,
            action=f"workflow.issue_to_patch.{final_status}",
            actor=actor,
            target=run_id,
            payload=event_payload,
        )
        return {
            "status": final_status,
            "reason": final_reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=run_id),
            "workspace": promotion_workspace,
            "job": pr_job,
            "agentRun": pr_agent_run,
            "evidencePackage": evidence,
            "runtime": evidence.get("runtimeHealth") or {},
            "runtimeResult": github_result,
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
        workspace = self.workspaces.allocate_workspace(
            project_id=payload["projectId"],
            task_id=f"issue-to-patch-{workflow_run['id']}",
            agent_id=profile_id,
            reason="issue_to_patch isolated workspace",
            isolation_type="git_worktree",
            workflow_run_id=workflow_run["id"],
            workflow_step_id=steps.get("workspace_create", {}).get("id"),
        )
        if "workspace_create" in steps:
            self.workflows.update_workflow_step(
                steps["workspace_create"]["id"],
                status="completed",
                output={"workspaceId": workspace["id"], "path": workspace["path"]},
            )

        workspace_auditable = workspace["isolationType"] == "git_worktree"
        preflight_block_reason = ""
        diff_blocker_state = RUNTIME_UNAVAILABLE_STATUS
        if runtime.get("executable") and not workspace_auditable:
            preflight_block_reason = (
                "issue_to_patch requires a Git worktree workspace before executing a productive runtime."
            )
            diff_blocker_state = "workspace_not_auditable"

        developer_result = DeveloperAgentRunner(self.connection, root=self.root).run(
            {
                "projectId": payload["projectId"],
                "workspaceId": workspace["id"],
                "taskId": "issue_to_patch",
                "instruction": _developer_instruction_for_issue(
                    title=title,
                    issue_text=issue_text,
                    target_path=payload.get("targetPath"),
                ),
                "preferredRuntime": preferred_runtime,
                "qaCommands": payload.get("qaCommands") or [],
                "requireApproval": False,
                "maxCostUsd": payload.get("maxCostUsd"),
                "workflowRunId": workflow_run["id"],
                "workflowStepId": steps.get("implementation", {}).get("id"),
                "qaWorkflowStepId": steps.get("qa_validation", {}).get("id"),
                "jobId": job_result["job"]["id"],
                "preflightBlockReason": preflight_block_reason,
                "diffBlockerState": diff_blocker_state,
            }
        )

        workspace = developer_result["workspace"]
        job = developer_result["job"]
        agent_run = developer_result["agentRun"]
        runtime = developer_result["runtime"]
        runtime_result: dict[str, Any] = developer_result["runtimeResult"]
        reason = str(runtime_result.get("reason") or developer_result.get("reason") or "")
        qa_results = developer_result["qaResults"]
        qa_agent_run_id = str((agent_run.get("output") or {}).get("qaAgentRunId") or "")
        evidence = developer_result["evidencePackage"]
        diff_summary = dict(developer_result.get("diffSummary") or evidence.get("diffSummary") or {})
        final_status, qa_verdict, final_reason = _status_from_developer_result(
            developer_result,
            require_approval=bool(payload.get("requireApproval", True)),
        )
        if final_status == RUNTIME_UNAVAILABLE_STATUS:
            final_reason = reason or final_reason

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
        if final_status == "evidence_ready" and qa_verdict == "needs_human_review":
            self.jobs.create_action_request(
                job_id=job["id"],
                project_id=payload["projectId"],
                action_type="workflow.issue_to_patch.approve_patch",
                risk_level="medium",
                reason="Review patch evidence and QA before accepting issue_to_patch output.",
                payload={
                    "workflowRunId": workflow_run["id"],
                    "workflowStepId": steps.get("qa_validation", {}).get("id"),
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
        hashes = artifact_hashes(artifact_records)
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
            hashes=hashes,
            qa_verdict=qa_verdict,
            risk_notes=[
                {
                    "severity": "medium" if final_status != "completed" else "low",
                    "description": final_reason,
                    "mitigation": "Configure and approve a real DeveloperAgent runtime, then retry the workflow.",
                }
            ],
        )
        contract_errors = evidence_package_contract_errors(
            evidence, require_runtime_links=final_status == "completed"
        )
        if final_status == "completed" and contract_errors:
            final_status, qa_verdict, final_reason = _status_from_developer_result(
                developer_result,
                require_approval=bool(payload.get("requireApproval", True)),
                evidence_package_valid=False,
            )
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_verdict,
                risk_notes=[
                    {
                        "severity": "medium",
                        "description": final_reason,
                        "mitigation": "Repair evidence package contract before marking workflow completed.",
                        "contractErrors": contract_errors,
                    }
                ],
            )
        agent_run_status = {
            "completed": "completed",
            RUNTIME_UNAVAILABLE_STATUS: "failed",
            "failed": "failed",
            "qa_failed": "failed",
            "evidence_ready": "awaiting_permission" if payload.get("requireApproval", True) else "failed",
        }[final_status]
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status=agent_run_status,
            output_payload={
                "verdict": final_status,
                "summary": final_reason,
                "runtime": runtime,
                "runtimeResult": runtime_result,
                "qaAgentRunId": qa_agent_run_id or None,
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "evidence_refs": [evidence["id"]],
            },
        )
        if "implementation" in steps:
            self.workflows.update_workflow_step(
                steps["implementation"]["id"],
                status="completed" if final_status == "completed" else "blocked",
                output={"agentRunId": agent_run["id"], "runtime": runtime, "reason": final_reason},
            )
        if "local_tests" in steps:
            self.workflows.update_workflow_step(
                steps["local_tests"]["id"],
                status="completed" if qa_verdict_allows_completion(qa_verdict, qa_results) else "blocked",
                output={"qaResults": qa_results, "qaAgentRunId": qa_agent_run_id or None},
            )
        if "qa_validation" in steps:
            self.workflows.update_workflow_step(
                steps["qa_validation"]["id"],
                status="completed" if qa_verdict == "passed" else "blocked",
                output={"qaVerdict": qa_verdict, "evidencePackageId": evidence["id"]},
            )

        workflow_run = self.workflows.update_workflow_run_status(
            workflow_run["id"],
            status=final_status,
            metadata={
                **workflow_run["metadata"],
                "runtime": runtime,
                "jobId": job["id"],
                "workspaceId": workspace["id"],
                "agentRunId": agent_run["id"],
                "evidencePackageId": evidence["id"],
                "qaVerdict": qa_verdict,
                "diffSummary": diff_summary,
                "artifacts": artifact_refs,
            },
            completed=final_status in TERMINAL_STATUSES,
        )
        workflow = self.workflows.update_workflow_status(
            workflow["id"], status=final_status, reason=final_reason
        )
        job_status = (
            "completed"
            if final_status == "completed"
            else "approval_required"
            if final_status == "evidence_ready" and qa_verdict == "needs_human_review"
            else "failed"
        )
        job = self.jobs.update_job_status(
            job["id"],
            status=job_status,
            metadata={"status": final_status, "reason": final_reason, "evidencePackageId": evidence["id"]},
        )
        return {
            "status": final_status,
            "reason": final_reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=workflow_run["id"]),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "runtime": runtime,
            "runtimeResult": runtime_result,
            "qaResults": qa_results,
            "diffSummary": diff_summary,
        }
