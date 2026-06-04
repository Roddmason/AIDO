from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.cli_runtimes.base import RuntimeRequest
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_registry import runtime_for
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.serialization import json_dumps
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.workflows.repository import ISSUE_TO_PATCH_STEPS, WorkflowsRepository
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff
from local_control_center.workspaces_projects.repository import WorkspacesRepository


CLI_RUNTIME_IDS = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
TERMINAL_STATUSES = {"completed", "unavailable", "qa_failed", "evidence_ready"}


def _runtime_mode(runtime_id: str) -> str:
    if runtime_id == "manual":
        return "manual"
    if runtime_id == "ollama":
        return "ollama"
    if runtime_id in CLI_RUNTIME_IDS:
        return "cli"
    return "hybrid"


def _patch_prompt(*, title: str, issue_text: str, target_path: str | None) -> str:
    target = f"\nTarget path: {target_path}" if target_path else ""
    return (
        "You are running inside an AIDO isolated workspace. "
        "Implement the requested code change, keep edits minimal, and do not commit or push.\n\n"
        f"Title: {title}{target}\n\nIssue:\n{issue_text}"
    )


def _status_for_failure(runtime: dict[str, Any]) -> tuple[str, str]:
    return "unavailable", str(runtime.get("reason") or "No executable runtime is configured for issue_to_patch.")


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


def _qa_results(qa_commands: list[list[str]], workspace_path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    sandbox = RestrictedSubprocessSandbox()
    for argv in qa_commands:
        result = sandbox.execute(
            argv=argv,
            cwd=str(workspace_path),
            workspace_path=str(workspace_path),
            timeout_seconds=120,
        )
        status = "passed" if result.get("executed") and not result.get("blocked") and result.get("returnCode") == 0 else "failed"
        results.append(
            {
                "command": " ".join(argv),
                "argv": argv,
                "status": status,
                "returnCode": result.get("returnCode"),
                "durationMs": result.get("durationMs"),
                "timedOut": bool(result.get("timedOut", False)),
                "blocked": bool(result.get("blocked", False)),
                "reason": result.get("reason"),
                "metadata": redact_secrets(
                    {
                        "stdout": result.get("stdout"),
                        "stderr": result.get("stderr"),
                        "execution": "restricted_subprocess",
                    }
                ),
            }
        )
    return results


def _diff_summary(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": diff.get("state"),
        "changedFiles": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
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
            "name": "issue-to-patch.diff",
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


def _complete_run_status(
    *,
    runtime_status: str,
    require_approval: bool,
    qa_results: list[dict[str, Any]],
    diff: dict[str, Any],
    evidence_created: bool,
) -> tuple[str, str, str]:
    if runtime_status == "unavailable":
        return "unavailable", "blocked", "No executable runtime was available."
    if not qa_results:
        return "evidence_ready", "blocked", "QA results are required before issue_to_patch can complete."
    if any(result["status"] != "passed" for result in qa_results):
        return "qa_failed", "failed", "QA command failed or was blocked."
    if not diff.get("nameOnly"):
        return "evidence_ready", "blocked", "Patch workflow produced no file changes."
    if require_approval:
        return "evidence_ready", "needs_human_review", "Patch evidence is ready and requires approval."
    if evidence_created:
        return "completed", "passed", "Patch evidence and QA passed."
    return "evidence_ready", "blocked", "Evidence package was not created."


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
        initial_status, reason = _status_for_failure(runtime)
        runtime_result: dict[str, Any] = {"status": initial_status, "reason": reason}
        if runtime.get("executable") and str(runtime["id"]) in CLI_RUNTIME_IDS and not workspace_auditable:
            initial_status = "unavailable"
            reason = "issue_to_patch requires a Git worktree workspace before executing a productive runtime."
            runtime_result = {"status": initial_status, "reason": reason}
        elif runtime.get("executable") and str(runtime["id"]) in CLI_RUNTIME_IDS:
            result = runtime_for(str(runtime["id"]), connection=self.connection).run(
                RuntimeRequest(
                    runtime=str(runtime["id"]),
                    workspaceId=workspace["id"],
                    workspacePath=workspace["path"],
                    prompt=_patch_prompt(
                        title=title,
                        issue_text=issue_text,
                        target_path=payload.get("targetPath"),
                    ),
                    envPolicy={"permissionProfile": "dev_safe", "issueToPatch": True},
                    role="implementer",
                    agentId=profile_id,
                    workflowRunId=workflow_run["id"],
                    workflowStepId=steps.get("implementation", {}).get("id"),
                )
            )
            runtime_result = result.model_dump(by_alias=True)
            initial_status = "running" if result.status == "completed" else "unavailable"
            reason = result.error or ("Runtime execution completed." if result.status == "completed" else "Runtime execution failed.")

        diff = (
            capture_git_diff(Path(workspace["path"]))
            if initial_status != "unavailable"
            else {
                "kind": "git_diff",
                "state": (
                    "workspace_not_auditable"
                    if runtime.get("executable") and not workspace_auditable
                    else "blocked_no_executable_runtime"
                ),
                "status": [],
                "nameOnly": [],
                "diffStat": "",
                "patch": "",
                "patchFull": "",
                "patchSizeBytes": 0,
                "truncated": False,
            }
        )
        qa_results = (
            _qa_results(payload.get("qaCommands") or [], Path(workspace["path"]))
            if initial_status != "unavailable"
            else []
        )
        final_status, qa_verdict, final_reason = _complete_run_status(
            runtime_status=initial_status,
            require_approval=bool(payload.get("requireApproval", True)),
            qa_results=qa_results,
            diff=diff,
            evidence_created=True,
        )
        if final_status == "unavailable":
            final_reason = reason
        evidence = self.evidence.create_evidence_package(
            project_id=payload["projectId"],
            workflow_run_id=workflow_run["id"],
            workflow_step_id=steps.get("qa_validation", {}).get("id"),
            agent_id=profile_id,
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
                    }
                )
            ],
            diff_refs=[_diff_summary(diff)],
            diff_summary=_diff_summary(diff),
            risk_notes=[
                {
                    "severity": "medium" if final_status != "completed" else "low",
                    "description": final_reason,
                    "mitigation": "Configure and approve a real coding runtime, then retry the workflow.",
                }
            ],
            qa_verdict=qa_verdict,
        )
        patch_artifact = _write_patch_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            diff=diff,
            repo=self.evidence,
        )
        diff_summary = _diff_summary(diff)
        if patch_artifact:
            diff_summary["patchArtifactId"] = patch_artifact["id"]

        agent_run_status = {
            "completed": "completed",
            "unavailable": "failed",
            "qa_failed": "failed",
            "evidence_ready": "awaiting_permission" if payload.get("requireApproval", True) else "failed",
        }[final_status]
        agent_run = self.agents.create_agent_run(
            project_id=payload["projectId"],
            agent_profile_id=profile_id,
            task_id="issue_to_patch",
            input_payload=redact_secrets(payload),
            output_payload={
                "verdict": final_status,
                "summary": final_reason,
                "runtime": runtime,
                "runtimeResult": runtime_result,
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "evidence_refs": [evidence["id"]],
            },
            job_id=job_result["job"]["id"],
            workflow_run_id=workflow_run["id"],
            workflow_step_id=steps.get("implementation", {}).get("id"),
            status=agent_run_status,
        )
        tool_calls = [
            tool_call for tool_call in self.agents.list_agent_tool_calls() if tool_call.get("agentRunId") == agent_run["id"]
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
        artifact_refs = []
        if patch_artifact:
            artifact_refs.append({"id": patch_artifact["id"], "kind": patch_artifact["kind"], "hash": patch_artifact["hash"]})
        manifest_artifact = _write_manifest_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            repo=self.evidence,
            manifest={
                "workflow": {"id": workflow["id"], "kind": workflow["kind"], "status": final_status},
                "workflowRun": {"id": workflow_run["id"], "status": final_status},
                "job": {"id": job_result["job"]["id"], "kind": job_result["job"]["kind"], "status": final_status},
                "agentRun": {"id": agent_run["id"], "status": agent_run["status"]},
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
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "artifacts": artifact_refs,
            },
        )
        diff_summary["manifestArtifactId"] = manifest_artifact["id"]
        artifact_refs.append(
            {"id": manifest_artifact["id"], "kind": manifest_artifact["kind"], "hash": manifest_artifact["hash"]}
        )
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            agent_run_id=agent_run["id"],
            artifact_ids=[str(artifact["id"]) for artifact in artifact_refs],
            diff_summary=diff_summary,
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
                status="completed" if qa_results and all(item["status"] == "passed" for item in qa_results) else "blocked",
                output={"qaResults": qa_results},
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
            "qaResults": qa_results,
            "diffSummary": diff_summary,
        }
