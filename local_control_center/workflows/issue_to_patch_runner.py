from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.qa_agent import QAAgentRunner, qa_verdict_allows_completion
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_registry import RuntimeCommandUnavailableError, build_issue_to_patch_argv
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.evidence.artifacts import artifact_hashes, artifact_records_from_ids, artifact_ref, write_text_artifact
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workflows.repository import ISSUE_TO_PATCH_STEPS, WorkflowsRepository
from local_control_center.workspaces_projects.cleanup import capture_workspace_snapshot
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff
from local_control_center.workspaces_projects.repository import WorkspacesRepository


CLI_RUNTIME_IDS = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
RUNTIME_UNAVAILABLE_STATUS = "runtime_unavailable"
TERMINAL_STATUSES = {"completed", RUNTIME_UNAVAILABLE_STATUS, "qa_failed", "evidence_ready", "failed"}


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
                "safety": {"workspaceBound": True, "shell": False, "structuredArgv": True, "network": "unknown"},
            },
        )
    return next(
        (
            status
            for status in statuses
            if status.get("executable") and set(status.get("capabilities") or []) & {"issue_to_patch", "code_edit"}
        ),
        {
            "id": "unresolved",
            "kind": "unknown",
            "displayName": "No executable runtime",
            "configured": False,
            "available": False,
            "executable": False,
            "requiresApproval": True,
            "reason": "No executable issue_to_patch/code_edit runtime is configured.",
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
    manifest = ((workspace.get("metadata") or {}).get("workspaceManifest") or {})
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


def _write_patch_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    diff: dict[str, Any],
    repo: EvidenceRepository,
) -> dict[str, Any] | None:
    patch = str(diff.get("patchFull") or "")
    if not patch:
        return None
    artifact_id = f"artifact-{uuid.uuid4()}"
    artifact_file = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".patch", content=patch)
    return repo.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=evidence_id,
        kind="git_patch",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"] or hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        metadata={
            "name": "diff.patch",
            "source": "issue_to_patch",
            "mimeType": "text/x-diff",
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
    )


def _write_manifest_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    manifest: dict[str, Any],
    repo: EvidenceRepository,
) -> dict[str, Any]:
    artifact_id = f"artifact-{uuid.uuid4()}"
    content = json_dumps(redact_secrets(manifest))
    artifact_file = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".json", content=content)
    return repo.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=evidence_id,
        kind="evidence_manifest",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"] or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        metadata={
            "name": "issue-to-patch-evidence.json",
            "source": "issue_to_patch",
            "mimeType": "application/json",
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
    )


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
            "source": "issue_to_patch",
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
    )


def _write_git_status_artifacts(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    diff: dict[str, Any],
    repo: EvidenceRepository,
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
        ),
        _write_json_evidence_artifact(
            root=root,
            project_id=project_id,
            evidence_id=evidence_id,
            name="git-status.json",
            kind="git_status",
            payload=status_payload,
            repo=repo,
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


def _complete_run_status(
    *,
    runtime_status: str,
    require_approval: bool,
    qa_results: list[dict[str, Any]],
    diff: dict[str, Any],
    evidence_created: bool,
    evidence_package_valid: bool = True,
) -> tuple[str, str, str]:
    if runtime_status == RUNTIME_UNAVAILABLE_STATUS:
        return RUNTIME_UNAVAILABLE_STATUS, "blocked", "No executable runtime was available."
    if runtime_status != "completed":
        return "failed", "failed", "Runtime execution failed."
    if not qa_results:
        return "evidence_ready", "blocked", "QA results are required before issue_to_patch can complete."
    if any(result.get("status") == "failed" for result in qa_results):
        return "qa_failed", "failed", "QA command failed or was blocked."
    if any(result.get("status") != "passed" for result in qa_results):
        return "evidence_ready", "blocked", "QA command was skipped or did not produce a passing verdict."
    if not qa_verdict_allows_completion("passed", qa_results):
        return "evidence_ready", "blocked", "QA verdict requires real command execution evidence before completion."
    if not diff.get("nameOnly"):
        return "evidence_ready", "blocked", "Patch workflow produced no file changes."
    if not evidence_created:
        return "evidence_ready", "blocked", "Evidence package was not created."
    if not evidence_package_valid:
        return "evidence_ready", "blocked", "Evidence package contract is incomplete or unverifiable."
    if not str(diff.get("patchFull") or diff.get("patch") or "").strip():
        return "evidence_ready", "blocked", "Patch artifact requires a non-empty diff."
    if require_approval:
        return "evidence_ready", "needs_human_review", "Patch evidence is ready and requires approval."
    return "completed", "passed", "Patch evidence and QA passed."


class IssueToPatchRunner:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.workflows = WorkflowsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.agents = AgentsRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)
        self.evidence = EvidenceRepository(connection)
        self.security = SecurityPolicyRepository(connection)

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        profile = self.agents.upsert_agent_profile(
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

        agent_run = self.agents.create_agent_run(
            project_id=payload["projectId"],
            agent_profile_id=profile_id,
            task_id="issue_to_patch",
            input_payload=redact_secrets(
                {
                    **payload,
                    "workflowRunId": workflow_run["id"],
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                }
            ),
            output_payload={},
            job_id=job_result["job"]["id"],
            workflow_run_id=workflow_run["id"],
            workflow_step_id=steps.get("implementation", {}).get("id"),
            status="running",
        )

        workspace_auditable = workspace["isolationType"] == "git_worktree"
        runtime_status, reason = _status_for_failure(runtime)
        runtime_result: dict[str, Any] = _runtime_unavailable_result(reason)
        broker = ToolBroker(self.connection, artifact_root=self.root)
        if runtime.get("executable") and str(runtime["id"]) in CLI_RUNTIME_IDS and not workspace_auditable:
            reason = "issue_to_patch requires a Git worktree workspace before executing a productive runtime."
            runtime_result = _runtime_unavailable_result(reason)
        elif runtime.get("executable"):
            try:
                runtime_argv = build_issue_to_patch_argv(
                    runtime=runtime,
                    workspace_id=workspace["id"],
                    workspace_path=workspace["path"],
                    title=title,
                    issue_text=issue_text,
                    workflow_run_id=workflow_run["id"],
                    workflow_step_id=steps.get("implementation", {}).get("id"),
                    agent_id=profile_id,
                    connection=self.connection,
                )
            except RuntimeCommandUnavailableError as error:
                reason = str(error)
                runtime_result = _runtime_unavailable_result(reason)
            else:
                runtime_eval = broker.evaluate_tool_call(
                    project_id=payload["projectId"],
                    agent_run_id=agent_run["id"],
                    agent_profile=profile,
                    job_id=job_result["job"]["id"],
                    tool_call={
                        "tool": "shell",
                        "command": _display_command(runtime_argv),
                        "argv": runtime_argv,
                        "workspaceId": workspace["id"],
                        "workspacePath": workspace["path"],
                        "path": workspace["path"],
                        "operation": "issue_to_patch_runtime",
                        "runtimeId": runtime["id"],
                        "workflowKind": "issue_to_patch",
                        "execute": True,
                        "timeoutSeconds": 900,
                    },
                )
                runtime_result = _execution_result_from_tool_call(runtime_eval["toolCall"])
                runtime_status = str(runtime_result["status"])

        diff = capture_git_diff(Path(workspace["path"]))
        if runtime_status == RUNTIME_UNAVAILABLE_STATUS:
            diff["blockerState"] = "workspace_not_auditable" if runtime.get("executable") and not workspace_auditable else RUNTIME_UNAVAILABLE_STATUS
        qa_results: list[dict[str, Any]] = []
        qa_artifact_ids: list[str] = []
        qa_agent_run: dict[str, Any] | None = None
        if runtime_status == "completed":
            qa_summary = QAAgentRunner(self.connection, root=self.root).run_for_context(
                project_id=payload["projectId"],
                workspace_id=workspace["id"],
                task_id="issue_to_patch",
                commands=payload.get("qaCommands") or [],
                workflow_run_id=workflow_run["id"],
                workflow_step_id=steps.get("qa_validation", {}).get("id"),
                job_id=job_result["job"]["id"],
                parent_agent_run_id=agent_run["id"],
                metadata={"source": "issue_to_patch"},
            )
            qa_results = qa_summary["results"]
            qa_artifact_ids = qa_summary["artifactIds"]
            qa_agent_run = qa_summary["agentRun"]

        final_status, qa_verdict, final_reason = _complete_run_status(
            runtime_status=runtime_status,
            require_approval=bool(payload.get("requireApproval", True)),
            qa_results=qa_results,
            diff=diff,
            evidence_created=True,
        )
        if final_status == RUNTIME_UNAVAILABLE_STATUS:
            final_reason = reason
        evidence = self.evidence.create_evidence_package(
            project_id=payload["projectId"],
            workflow_run_id=workflow_run["id"],
            workflow_step_id=steps.get("qa_validation", {}).get("id"),
            agent_id=profile_id,
            agent_run_id=agent_run["id"],
            job_id=job_result["job"]["id"],
            workspace_id=workspace["id"],
            runtime_id=str(runtime["id"]),
            task_id="issue_to_patch",
            test_plan="Execute issue_to_patch through a configured runtime, capture diff, and run QA commands.",
            acceptance_checklist=[
                "Runtime is executable.",
                "Workspace is isolated.",
                "Patch diff is non-empty.",
                "QA commands pass.",
                "Approval gate is satisfied when required.",
            ],
            test_results=qa_results,
            logs=[
                redact_secrets(
                    {
                        "runtime": runtime,
                        "runtimeResult": runtime_result,
                        "jobId": job_result["job"]["id"],
                        "workspaceId": workspace["id"],
                        "qaAgentRunId": (qa_agent_run or {}).get("id"),
                    }
                )
            ],
            diff_refs=_final_diff_refs(workspace, diff),
            diff_summary=_diff_summary(diff),
            risk_notes=[
                {
                    "severity": "medium" if final_status != "completed" else "low",
                    "description": final_reason,
                    "mitigation": "Configure and approve a real coding runtime, then retry the workflow.",
                }
            ],
            artifact_ids=qa_artifact_ids,
            qa_verdict=qa_verdict,
        )
        if qa_artifact_ids:
            QAAgentRunner(self.connection, root=self.root).attach_artifacts_to_evidence(
                evidence_id=evidence["id"],
                artifact_ids=qa_artifact_ids,
            )
        patch_artifact = _write_patch_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            diff=diff,
            repo=self.evidence,
        )
        status_artifacts = _write_git_status_artifacts(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            diff=diff,
            repo=self.evidence,
        )
        qa_results_artifact = _write_json_evidence_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            name="qa-results.json",
            kind="qa_report",
            payload={"results": qa_results, "qaAgentRunId": (qa_agent_run or {}).get("id")},
            repo=self.evidence,
        )
        diff_summary = _diff_summary(diff)
        if patch_artifact:
            diff_summary["patchArtifactId"] = patch_artifact["id"]
        diff_summary["gitStatusArtifactIds"] = [artifact["id"] for artifact in status_artifacts]
        diff_summary["qaResultsArtifactId"] = qa_results_artifact["id"]

        related_agent_run_ids = {agent_run["id"]}
        if qa_agent_run:
            related_agent_run_ids.add(str(qa_agent_run["id"]))
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) in related_agent_run_ids
        ]
        model_calls = [
            model_call for model_call in self.agents.list_model_calls() if model_call.get("agentRunId") == agent_run["id"]
        ]
        if final_status == "evidence_ready" and qa_verdict == "needs_human_review":
            self.jobs.create_action_request(
                job_id=job_result["job"]["id"],
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
        approvals = self.jobs.list_action_requests(job_result["job"]["id"])
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
        model_call_artifact = (
            _write_json_evidence_artifact(
                root=self.root,
                project_id=payload["projectId"],
                evidence_id=evidence["id"],
                name="model-call.json",
                kind="model_call",
                payload={"modelCalls": model_calls},
                repo=self.evidence,
            )
            if model_calls
            else None
        )
        qa_artifacts = artifact_records_from_ids(self.evidence, qa_artifact_ids)
        artifact_records = [
            *qa_artifacts,
            *status_artifacts,
            qa_results_artifact,
            security_findings_artifact,
        ]
        if patch_artifact:
            artifact_records.append(patch_artifact)
        if model_call_artifact:
            artifact_records.append(model_call_artifact)
        artifact_refs = [artifact_ref(artifact) for artifact in artifact_records]
        runtime_health = redact_secrets(
            {
                **runtime,
                "resultStatus": runtime_result.get("status"),
                "resultReason": runtime_result.get("reason") or runtime.get("reason"),
            }
        )
        manifest_artifact = _write_manifest_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            repo=self.evidence,
            manifest={
                "workflow": {"id": workflow["id"], "kind": workflow["kind"], "status": final_status},
                "workflowRun": {"id": workflow_run["id"], "status": final_status},
                "job": {"id": job_result["job"]["id"], "kind": job_result["job"]["kind"], "status": final_status},
                "agentRun": {"id": agent_run["id"], "status": final_status},
                "workspace": {
                    "id": workspace["id"],
                    "path": workspace["path"],
                    "isolationType": workspace["isolationType"],
                    "status": workspace["status"],
                },
                "runtime": runtime,
                "policyDecisions": policy_decisions,
                "approvals": approvals,
                "modelCalls": model_calls,
                "toolCalls": tool_calls,
                "qaAgentRunId": (qa_agent_run or {}).get("id"),
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "artifacts": artifact_refs,
                "hashes": artifact_hashes(artifact_records),
            },
        )
        diff_summary["manifestArtifactId"] = manifest_artifact["id"]
        artifact_records.append(manifest_artifact)
        artifact_refs = [artifact_ref(artifact) for artifact in artifact_records]
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
        )
        contract_errors = evidence_package_contract_errors(evidence, require_runtime_links=final_status == "completed")
        if final_status == "completed" and contract_errors:
            final_status, qa_verdict, final_reason = _complete_run_status(
                runtime_status=runtime_status,
                require_approval=bool(payload.get("requireApproval", True)),
                qa_results=qa_results,
                diff=diff,
                evidence_created=True,
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
                "qaAgentRunId": (qa_agent_run or {}).get("id"),
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
                output={"qaResults": qa_results, "qaAgentRunId": (qa_agent_run or {}).get("id")},
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
                "jobId": job_result["job"]["id"],
                "workspaceId": workspace["id"],
                "agentRunId": agent_run["id"],
                "evidencePackageId": evidence["id"],
                "qaVerdict": qa_verdict,
                "diffSummary": diff_summary,
                "artifacts": artifact_refs,
            },
            completed=final_status in TERMINAL_STATUSES,
        )
        workflow = self.workflows.update_workflow_status(workflow["id"], status=final_status, reason=final_reason)
        job_status = (
            "completed"
            if final_status == "completed"
            else "approval_required"
            if final_status == "evidence_ready" and qa_verdict == "needs_human_review"
            else "failed"
        )
        job = self.jobs.update_job_status(
            job_result["job"]["id"],
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
