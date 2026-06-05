from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workspaces_projects.cleanup import capture_workspace_snapshot
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .developer_agent_contract import (
    DEVELOPER_AGENT_ALLOWED_TOOLS,
    DEVELOPER_AGENT_CLI_RUNTIMES,
    DEVELOPER_AGENT_ID,
    DEVELOPER_AGENT_MODEL_RUNTIMES,
    developer_agent_readiness,
)
from .repository import AgentsRepository
from .runtime_registry import RuntimeCommandUnavailableError, build_developer_agent_argv, developer_agent_prompt
from .runtime_status import RuntimeStatusService
from .tool_broker import ToolBroker


RUNTIME_UNAVAILABLE_STATUS = "runtime_unavailable"
TERMINAL_STATUSES = {"completed", RUNTIME_UNAVAILABLE_STATUS, "qa_failed", "evidence_ready", "failed"}


def _runtime_mode(runtime_id: str) -> str:
    if runtime_id == "ollama":
        return "ollama"
    if runtime_id in DEVELOPER_AGENT_CLI_RUNTIMES:
        return "cli"
    if runtime_id in DEVELOPER_AGENT_MODEL_RUNTIMES:
        return "api"
    return "hybrid"


def _display_command(argv: list[str]) -> str:
    if not argv:
        return ""
    executable = Path(argv[0]).name or str(argv[0])
    if executable.lower() in {"python.exe", "python3.exe", "py.exe"}:
        executable = "python"
    return subprocess.list2cmdline([executable, *[str(item) for item in argv[1:]]])


def _diff_summary(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "changedFiles": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
    }


def _workspace_manifest_ref(workspace: dict[str, Any]) -> dict[str, Any] | None:
    manifest = ((workspace.get("metadata") or {}).get("workspaceManifest") or {})
    if not manifest:
        return None
    return {"kind": "workspace_manifest", "status": "captured", **manifest}


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


def _final_diff_refs(workspace: dict[str, Any], diff: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [_evidence_git_diff_ref(diff)]
    manifest = _workspace_manifest_ref(workspace)
    if manifest:
        refs.append(manifest)
    refs.append(capture_workspace_snapshot(workspace["path"]))
    return refs


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
        "stdout": execution_result.get("stdout") or "",
        "stderr": execution_result.get("stderr") or "",
        "stdoutArtifactId": execution_result.get("stdoutArtifactId"),
        "stderrArtifactId": execution_result.get("stderrArtifactId"),
        "outputArtifactId": execution_result.get("outputArtifactId"),
        "evidencePackageId": execution_result.get("evidencePackageId"),
    }


def _qa_result_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    passed = tool_call.get("status") == "completed" and execution_result.get("returnCode") == 0
    output_refs = [
        artifact_id
        for artifact_id in (execution_result.get("stdoutArtifactId"), execution_result.get("stderrArtifactId"))
        if artifact_id
    ]
    return {
        "command": payload.get("command") or tool_call.get("toolName"),
        "status": "passed" if passed else "failed",
        "toolCallStatus": tool_call.get("status"),
        "execution": payload.get("execution"),
        "returnCode": execution_result.get("returnCode"),
        "timedOut": bool(execution_result.get("timedOut", False)),
        "durationMs": execution_result.get("durationMs"),
        "blocked": bool(execution_result.get("blocked", False)),
        "reason": execution_result.get("reason") or payload.get("decisionReason"),
        "toolCallId": tool_call.get("id"),
        "outputRef": output_refs[0] if output_refs else None,
        "outputRefs": output_refs,
    }


def _runtime_unavailable_result(reason: str) -> dict[str, Any]:
    return {"status": RUNTIME_UNAVAILABLE_STATUS, "reason": reason, "execution": "not_executed"}


def _complete_run_status(
    *,
    runtime_status: str,
    require_approval: bool,
    qa_results: list[dict[str, Any]],
    diff: dict[str, Any],
    evidence_created: bool,
) -> tuple[str, str, str]:
    if runtime_status == RUNTIME_UNAVAILABLE_STATUS:
        return RUNTIME_UNAVAILABLE_STATUS, "blocked", "No executable DeveloperAgent runtime was available."
    if runtime_status != "completed":
        return "failed", "failed", "DeveloperAgent runtime execution failed."
    if not qa_results:
        return "evidence_ready", "blocked", "QA results are required before DeveloperAgent can complete."
    if any(result["status"] != "passed" for result in qa_results):
        return "qa_failed", "failed", "QA command failed or was blocked."
    if not diff.get("nameOnly"):
        return "evidence_ready", "blocked", "DeveloperAgent produced no file changes."
    if not evidence_created:
        return "evidence_ready", "blocked", "Evidence package was not created."
    if not str(diff.get("patchFull") or diff.get("patch") or "").strip():
        return "evidence_ready", "blocked", "Patch artifact requires a non-empty diff."
    if require_approval:
        return "evidence_ready", "needs_human_review", "DeveloperAgent evidence is ready and requires approval."
    return "completed", "passed", "DeveloperAgent runtime, diff, QA, and evidence passed."


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
            "name": "developer-agent.diff",
            "source": "developer_agent",
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
            "name": "developer-agent-evidence.json",
            "source": "developer_agent",
            "mimeType": "application/json",
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
    )


def _developer_model_messages(*, instruction: str, qa_commands: list[list[str]]) -> list[dict[str, str]]:
    schema = (
        '{"summary":"string","files":[{"path":"relative/path","content":"complete UTF-8 file content"}],'
        '"tests":["test command or blocker"],"risks":["risk or blocker"]}'
    )
    return [
        {
            "role": "system",
            "content": (
                "You are DeveloperAgent. Return only valid JSON. Do not include markdown fences. "
                "The JSON must match this schema: " + schema
            ),
        },
        {
            "role": "user",
            "content": developer_agent_prompt(instruction=instruction, qa_commands=qa_commands),
        },
    ]


def _parse_model_patch(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("DeveloperAgent model output is not valid JSON patch output.") from error
    if not isinstance(payload, dict):
        raise ValueError("DeveloperAgent model output must be a JSON object.")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("DeveloperAgent model output must include a non-empty files list.")
    for index, item in enumerate(files):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise ValueError(f"DeveloperAgent files[{index}] must include path and content strings.")
    return payload


class DeveloperAgentRunner:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)
        self.evidence = EvidenceRepository(connection)
        self.security = SecurityPolicyRepository(connection)

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return developer_agent_readiness(statuses, preferred_runtime=preferred_runtime)

    def _runtime_by_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        if not runtime_id:
            return None
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return next((runtime for runtime in statuses if runtime["id"] == runtime_id), None)

    def _create_profile(self, runtime_id: str) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": DEVELOPER_AGENT_ID,
                "name": "DeveloperAgent",
                "role": "developer",
                "runtimeMode": _runtime_mode(runtime_id),
                "permissionProfile": "dev_safe",
                "allowedTools": DEVELOPER_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [runtime_id] if runtime_id else [],
                "allowedRuntimes": [runtime_id] if runtime_id else [],
                "allowRemote": runtime_id == "openai_compatible",
                "allowCli": runtime_id in DEVELOPER_AGENT_CLI_RUNTIMES,
                "allowApi": runtime_id in DEVELOPER_AGENT_MODEL_RUNTIMES,
                "outputSchema": developer_agent_readiness([])["contract"]["outputSchema"],
            }
        )

    def _model_output_text(self, artifact_id: str | None) -> str:
        if not artifact_id:
            raise ValueError("DeveloperAgent model execution did not produce an output artifact.")
        artifact = self.evidence.get_artifact_by_id(artifact_id)
        return Path(artifact["path"]).read_text(encoding="utf-8")

    def _execute_cli_runtime(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
    ) -> dict[str, Any]:
        runtime_argv = build_developer_agent_argv(
            runtime=runtime,
            workspace_id=workspace["id"],
            workspace_path=workspace["path"],
            instruction=str(payload["instruction"]),
            qa_commands=payload.get("qaCommands") or [],
            agent_id=DEVELOPER_AGENT_ID,
            connection=self.connection,
        )
        runtime_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": "shell",
                "command": _display_command(runtime_argv),
                "argv": runtime_argv,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "developer_agent_runtime",
                "runtimeId": runtime["id"],
                "capability": "code_edit",
                "execute": True,
                "timeoutSeconds": 900,
            },
        )
        return _execution_result_from_tool_call(runtime_eval["toolCall"])

    def _execute_model_runtime(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
    ) -> dict[str, Any]:
        runtime_id = str(runtime["id"])
        model = payload.get("model")
        if runtime_id == "ollama":
            model = model or next(iter(runtime.get("models") or []), None)
        model_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": runtime_id,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "developer_agent_model_call",
                "runtimeId": runtime_id,
                "capability": "chat",
                "input": {
                    "model": model,
                    "messages": _developer_model_messages(
                        instruction=str(payload["instruction"]),
                        qa_commands=payload.get("qaCommands") or [],
                    ),
                    "temperature": 0.2,
                },
                "networkRequired": runtime_id == "openai_compatible",
                "secretsRequired": False,
                "approvalGrantId": payload.get("approvalGrantId"),
                "execute": True,
                "timeoutSeconds": 900,
            },
        )
        model_result = _execution_result_from_tool_call(model_eval["toolCall"])
        if model_result["status"] != "completed":
            return {"status": "failed", "modelCall": model_result, "reason": model_result.get("reason")}
        patch_payload = _parse_model_patch(self._model_output_text(model_result.get("outputArtifactId")))
        patch_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": "workspace_patch",
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "developer_agent_patch_apply",
                "runtimeId": runtime_id,
                "capability": "code_edit",
                "input": patch_payload,
                "execute": True,
                "timeoutSeconds": 30,
            },
        )
        patch_result = _execution_result_from_tool_call(patch_eval["toolCall"])
        return {
            "status": "completed" if patch_result["status"] == "completed" else "failed",
            "modelCall": model_result,
            "patchApply": patch_result,
            "reason": patch_result.get("reason"),
            "outputArtifactId": model_result.get("outputArtifactId"),
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(str(payload["workspaceId"]))
        if workspace["projectId"] != payload["projectId"]:
            raise ValueError("Workspace does not belong to the requested project.")
        if workspace["status"] == "archived":
            raise ValueError("DeveloperAgent cannot execute in an archived workspace.")

        readiness = self.status(preferred_runtime=payload.get("preferredRuntime"))
        runtime = self._runtime_by_id(readiness.get("selectedRuntimeId")) or {
            "id": readiness.get("selectedRuntimeId") or "unresolved",
            "kind": "unknown",
            "executable": False,
            "reason": readiness["reason"],
            "capabilities": [],
        }
        profile = self._create_profile(str(runtime.get("id") or "unresolved"))
        job_result = self.jobs.create_job(
            project_id=payload["projectId"],
            kind="agent.developer",
            status="running",
            payload={
                "taskId": payload["taskId"],
                "runtime": runtime,
                "workspaceId": workspace["id"],
                "qaCommands": payload.get("qaCommands") or [],
                "maxCostUsd": payload.get("maxCostUsd"),
            },
        )
        job = job_result["job"]
        agent_run = self.agents.create_agent_run(
            project_id=payload["projectId"],
            agent_profile_id=profile["id"],
            task_id=payload["taskId"],
            input_payload=redact_secrets({**payload, "workspacePath": workspace["path"]}),
            output_payload={},
            job_id=job["id"],
            status="running",
        )

        broker = ToolBroker(self.connection, artifact_root=self.root)
        runtime_status = RUNTIME_UNAVAILABLE_STATUS
        runtime_result = _runtime_unavailable_result(readiness["reason"])
        if readiness["executable"]:
            try:
                if str(runtime["id"]) in DEVELOPER_AGENT_CLI_RUNTIMES:
                    runtime_result = self._execute_cli_runtime(
                        payload=payload,
                        runtime=runtime,
                        workspace=workspace,
                        agent_run=agent_run,
                        job=job,
                        profile=profile,
                        broker=broker,
                    )
                elif str(runtime["id"]) in DEVELOPER_AGENT_MODEL_RUNTIMES:
                    runtime_result = self._execute_model_runtime(
                        payload=payload,
                        runtime=runtime,
                        workspace=workspace,
                        agent_run=agent_run,
                        job=job,
                        profile=profile,
                        broker=broker,
                    )
                runtime_status = str(runtime_result.get("status") or "failed")
            except (RuntimeCommandUnavailableError, ValueError) as error:
                runtime_result = _runtime_unavailable_result(str(error))
                runtime_status = RUNTIME_UNAVAILABLE_STATUS

        diff = capture_git_diff(Path(workspace["path"]))
        if runtime_status == RUNTIME_UNAVAILABLE_STATUS:
            diff["blockerState"] = RUNTIME_UNAVAILABLE_STATUS

        qa_results: list[dict[str, Any]] = []
        if runtime_status == "completed":
            for qa_argv in payload.get("qaCommands") or []:
                qa_eval = broker.evaluate_tool_call(
                    project_id=payload["projectId"],
                    agent_run_id=agent_run["id"],
                    agent_profile=profile,
                    job_id=job["id"],
                    tool_call={
                        "tool": "shell",
                        "command": _display_command(qa_argv),
                        "argv": qa_argv,
                        "workspaceId": workspace["id"],
                        "workspacePath": workspace["path"],
                        "path": workspace["path"],
                        "operation": "developer_agent_qa",
                        "execute": True,
                        "timeoutSeconds": 120,
                    },
                )
                qa_results.append(_qa_result_from_tool_call(qa_eval["toolCall"]))

        final_status, qa_verdict, final_reason = _complete_run_status(
            runtime_status=runtime_status,
            require_approval=bool(payload.get("requireApproval", True)),
            qa_results=qa_results,
            diff=diff,
            evidence_created=True,
        )
        if final_status == RUNTIME_UNAVAILABLE_STATUS:
            final_reason = str(runtime_result.get("reason") or readiness["reason"])

        evidence = self.evidence.create_evidence_package(
            project_id=payload["projectId"],
            workflow_run_id=None,
            agent_id=DEVELOPER_AGENT_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id=str(runtime["id"]),
            task_id=payload["taskId"],
            test_plan="Execute DeveloperAgent through a configured runtime, capture diff, and run QA commands.",
            acceptance_checklist=[
                "Runtime is executable.",
                "Workspace is allocated.",
                "Runtime output is real.",
                "Diff is non-empty.",
                "QA commands pass.",
                "Evidence package is linked.",
            ],
            test_results=qa_results,
            logs=[redact_secrets({"runtime": runtime, "runtimeResult": runtime_result, "workspaceId": workspace["id"]})],
            diff_refs=_final_diff_refs(workspace, diff),
            diff_summary=_diff_summary(diff),
            risk_notes=[
                {
                    "severity": "medium" if final_status != "completed" else "low",
                    "description": final_reason,
                    "mitigation": "Configure a real DeveloperAgent runtime and rerun inside an allocated workspace.",
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
        artifact_refs: list[dict[str, Any]] = []
        if patch_artifact:
            diff_summary["patchArtifactId"] = patch_artifact["id"]
            artifact_refs.append({"id": patch_artifact["id"], "kind": patch_artifact["kind"], "hash": patch_artifact["hash"]})
        tool_calls = [
            tool_call for tool_call in self.agents.list_agent_tool_calls() if tool_call.get("agentRunId") == agent_run["id"]
        ]
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
        manifest_artifact = _write_manifest_artifact(
            root=self.root,
            project_id=payload["projectId"],
            evidence_id=evidence["id"],
            repo=self.evidence,
            manifest={
                "status": final_status,
                "job": {"id": job["id"], "kind": job["kind"], "status": final_status},
                "agentRun": {"id": agent_run["id"], "status": final_status},
                "workspace": {
                    "id": workspace["id"],
                    "path": workspace["path"],
                    "isolationType": workspace["isolationType"],
                    "status": workspace["status"],
                },
                "runtime": runtime,
                "runtimeResult": runtime_result,
                "policyDecisions": policy_decisions,
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
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "evidence_refs": [evidence["id"]],
            },
        )

        if final_status == "evidence_ready" and qa_verdict == "needs_human_review":
            self.jobs.create_action_request(
                job_id=job["id"],
                project_id=payload["projectId"],
                action_type="agent.developer.approve_patch",
                risk_level="medium",
                reason="Review DeveloperAgent patch evidence and QA before accepting output.",
                payload={
                    "agentRunId": agent_run["id"],
                    "workspaceId": workspace["id"],
                    "runtimeId": runtime["id"],
                    "evidencePackageId": evidence["id"],
                    "diffSummary": diff_summary,
                },
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
            "developerAgent": readiness,
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "runtime": runtime,
            "runtimeResult": runtime_result,
            "qaResults": qa_results,
            "diffSummary": diff_summary,
        }
