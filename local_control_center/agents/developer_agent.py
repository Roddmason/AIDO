"""Ejecuta el DeveloperAgent: aplica cambios en un workspace vía runtime y los valida con QA y diff.

Selecciona un runtime CLI o de modelo, ejecuta la instrucción dentro del workspace (a través del broker),
captura el diff git resultante y corre el QAAgent; el run solo se da por completado si el QA lo permite.
Todo queda asentado en un paquete de evidencia con artefactos y hashes; falla cerrado ante brechas.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import (
    artifact_hashes,
    artifact_records_from_ids,
    artifact_ref,
    write_text_artifact,
)
from local_control_center.evidence.quality import evidence_package_contract_errors
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
    DEVELOPER_AGENT_REMOTE_API_RUNTIMES,
    developer_agent_readiness,
)
from .qa_agent import QAAgentRunner, qa_verdict_allows_completion
from .repository import AgentsRepository
from .runtime_registry import (
    RuntimeCommandUnavailableError,
    build_developer_agent_argv,
    developer_agent_prompt,
)
from .runtime_selection import (
    RUNTIME_UNAVAILABLE_STATUS,
    display_command,
    is_ollama_runtime,
    runtime_provider_family,
    runtime_unavailable_result,
)
from .runtime_status import RuntimeStatusService
from .tool_broker import ToolBroker

TERMINAL_STATUSES = {"completed", RUNTIME_UNAVAILABLE_STATUS, "qa_failed", "evidence_ready", "failed"}


def _runtime_mode(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    if is_ollama_runtime(runtime):
        return "ollama"
    if runtime_id in DEVELOPER_AGENT_CLI_RUNTIMES:
        return "cli"
    if runtime_provider_family(runtime) in DEVELOPER_AGENT_MODEL_RUNTIMES:
        return "api"
    return "hybrid"


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
    manifest = (workspace.get("metadata") or {}).get("workspaceManifest") or {}
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
    if any(result.get("status") == "failed" for result in qa_results):
        return "qa_failed", "failed", "QA command failed or was blocked."
    if any(result.get("status") != "passed" for result in qa_results):
        return "evidence_ready", "blocked", "QA command was skipped or did not produce a passing verdict."
    if not qa_verdict_allows_completion("passed", qa_results):
        return (
            "evidence_ready",
            "blocked",
            "QA verdict requires real command execution evidence before completion.",
        )
    if not diff.get("nameOnly"):
        return "evidence_ready", "blocked", "DeveloperAgent produced no file changes."
    if not evidence_created:
        return "evidence_ready", "blocked", "Evidence package was not created."
    if not str(diff.get("patchFull") or diff.get("patch") or "").strip():
        return "evidence_ready", "blocked", "Patch artifact requires a non-empty diff."
    if require_approval:
        return (
            "evidence_ready",
            "needs_human_review",
            "DeveloperAgent evidence is ready and requires approval.",
        )
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


def _developer_model_messages(
    *,
    instruction: str,
    qa_commands: list[list[str]],
    story_specs: str | None = None,
) -> list[dict[str, str]]:
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
            "content": developer_agent_prompt(
                instruction=instruction, qa_commands=qa_commands, story_specs=story_specs
            ),
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
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("content"), str)
        ):
            raise ValueError(f"DeveloperAgent files[{index}] must include path and content strings.")
    return payload


class DeveloperAgentRunner:
    """Orquesta una tarea de desarrollo: ejecuta el runtime, captura el diff y valida con QA."""

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)
        self.evidence = EvidenceRepository(connection)
        self.security = SecurityPolicyRepository(connection)

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        """Devuelve el readiness del DeveloperAgent según los runtimes CLI/modelo disponibles."""
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return developer_agent_readiness(statuses, preferred_runtime=preferred_runtime)

    def _runtime_by_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        if not runtime_id:
            return None
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return next((runtime for runtime in statuses if runtime["id"] == runtime_id), None)

    def _create_profile(self, runtime: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(runtime.get("id") or "")
        ollama_runtime = is_ollama_runtime(runtime)
        provider_family = runtime_provider_family(runtime)
        remote_runtime = provider_family in DEVELOPER_AGENT_REMOTE_API_RUNTIMES or str(
            runtime.get("kind") or ""
        ) in {"api", "gateway"}
        return self.agents.upsert_agent_profile(
            {
                "id": DEVELOPER_AGENT_ID,
                "name": "DeveloperAgent",
                "role": "developer",
                "runtimeMode": _runtime_mode(runtime),
                "permissionProfile": "dev_safe",
                "allowedTools": DEVELOPER_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [runtime_id] if runtime_id else [],
                "allowedRuntimes": [runtime_id] if runtime_id else [],
                "allowRemote": remote_runtime,
                "allowCli": runtime_id in DEVELOPER_AGENT_CLI_RUNTIMES,
                "allowApi": provider_family in DEVELOPER_AGENT_MODEL_RUNTIMES or ollama_runtime,
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
            story_specs=payload.get("storySpecs"),
        )
        runtime_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": "shell",
                "command": display_command(runtime_argv),
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
        ollama_runtime = is_ollama_runtime(runtime)
        provider_family = runtime_provider_family(runtime)
        if ollama_runtime:
            model = model or next(iter(runtime.get("models") or []), None)
        model_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": provider_family,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "developer_agent_model_call",
                "runtimeId": runtime_id,
                "capability": "chat",
                "input": {
                    "providerId": runtime_id,
                    "model": model,
                    "messages": _developer_model_messages(
                        instruction=str(payload["instruction"]),
                        qa_commands=payload.get("qaCommands") or [],
                        story_specs=payload.get("storySpecs"),
                    ),
                    "temperature": 0.2,
                },
                "networkRequired": provider_family in DEVELOPER_AGENT_REMOTE_API_RUNTIMES
                or str(runtime.get("kind") or "") in {"api", "gateway"},
                # Provider credentials are injected by the adapter transport and never enter the prompt.
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
        """Ejecuta la tarea end-to-end y devuelve estado, diff, resultados de QA y evidencia.

        Valida el workspace, ejecuta el runtime seleccionado vía broker, captura el diff git, corre el
        QAAgent y reduce todo a un estado terminal; solo completa si el veredicto de QA lo permite.
        """
        workspace = self.workspaces.get_workspace(str(payload["workspaceId"]))
        if workspace["projectId"] != payload["projectId"]:
            raise ValueError("Workspace does not belong to the requested project.")
        if workspace["status"] == "archived":
            raise ValueError("DeveloperAgent cannot execute in an archived workspace.")
        workflow_run_id = str(payload.get("workflowRunId") or "").strip() or None
        workflow_step_id = str(payload.get("workflowStepId") or "").strip() or None
        qa_workflow_step_id = str(payload.get("qaWorkflowStepId") or "").strip() or workflow_step_id
        job_id = str(payload.get("jobId") or "").strip() or None
        preflight_block_reason = str(payload.get("preflightBlockReason") or "").strip()
        diff_blocker_state = str(payload.get("diffBlockerState") or "").strip() or RUNTIME_UNAVAILABLE_STATUS

        readiness = self.status(preferred_runtime=payload.get("preferredRuntime"))
        runtime = self._runtime_by_id(readiness.get("selectedRuntimeId")) or {
            "id": readiness.get("selectedRuntimeId") or "unresolved",
            "kind": "unknown",
            "executable": False,
            "reason": readiness["reason"],
            "capabilities": [],
        }
        profile = self._create_profile(runtime)
        if job_id:
            job = self.jobs.get_job(job_id)
            if job["projectId"] != payload["projectId"]:
                raise ValueError("DeveloperAgent job does not belong to the requested project.")
        else:
            job_result = self.jobs.create_job(
                project_id=payload["projectId"],
                kind="agent.developer",
                status="running",
                workflow_run_id=workflow_run_id,
                workflow_step_id=workflow_step_id,
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
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            status="running",
        )

        broker = ToolBroker(self.connection, artifact_root=self.root)
        runtime_status = RUNTIME_UNAVAILABLE_STATUS
        runtime_result = runtime_unavailable_result(preflight_block_reason or readiness["reason"])
        if readiness["executable"] and not preflight_block_reason:
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
                elif runtime_provider_family(runtime) in DEVELOPER_AGENT_MODEL_RUNTIMES:
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
                runtime_result = runtime_unavailable_result(str(error))
                runtime_status = RUNTIME_UNAVAILABLE_STATUS

        diff = capture_git_diff(
            Path(workspace["path"]),
            connection=self.connection,
            root=self.root,
            project_id=payload["projectId"],
            workspace_id=workspace["id"],
            task_id=payload["taskId"],
        )
        if runtime_status == RUNTIME_UNAVAILABLE_STATUS:
            diff["blockerState"] = diff_blocker_state

        qa_results: list[dict[str, Any]] = []
        qa_artifact_ids: list[str] = []
        qa_agent_run: dict[str, Any] | None = None
        if runtime_status == "completed":
            qa_summary = QAAgentRunner(self.connection, root=self.root).run_for_context(
                project_id=payload["projectId"],
                workspace_id=workspace["id"],
                task_id=payload["taskId"],
                commands=payload.get("qaCommands") or [],
                workflow_run_id=workflow_run_id,
                workflow_step_id=qa_workflow_step_id,
                job_id=job["id"],
                parent_agent_run_id=agent_run["id"],
                metadata={"source": DEVELOPER_AGENT_ID},
                story_specs=payload.get("storySpecs") if isinstance(payload.get("storySpecs"), str) else None,
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
            final_reason = str(runtime_result.get("reason") or readiness["reason"])

        evidence = self.evidence.create_evidence_package(
            project_id=payload["projectId"],
            workflow_run_id=workflow_run_id,
            workflow_step_id=qa_workflow_step_id,
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
            logs=[
                redact_secrets(
                    {
                        "runtime": runtime,
                        "runtimeResult": runtime_result,
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
                    "mitigation": "Configure a real DeveloperAgent runtime and rerun inside an allocated workspace.",
                }
            ],
            artifact_ids=qa_artifact_ids,
            evidence_source="verified_completion" if qa_verdict == "passed" else "evidence_collected",
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
        diff_summary = _diff_summary(diff)
        artifact_records = artifact_records_from_ids(self.evidence, qa_artifact_ids)
        if patch_artifact:
            diff_summary["patchArtifactId"] = patch_artifact["id"]
            artifact_records.append(patch_artifact)
        related_agent_run_ids = {agent_run["id"]}
        if qa_agent_run:
            related_agent_run_ids.add(str(qa_agent_run["id"]))
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) in related_agent_run_ids
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
                "qaAgentRunId": (qa_agent_run or {}).get("id"),
                "qaResults": qa_results,
                "diffSummary": diff_summary,
                "artifacts": [artifact_ref(artifact) for artifact in artifact_records],
            },
        )
        diff_summary["manifestArtifactId"] = manifest_artifact["id"]
        artifact_records.append(manifest_artifact)
        artifact_refs = [artifact_ref(artifact) for artifact in artifact_records]
        model_call = (
            runtime_result.get("modelCall") if isinstance(runtime_result.get("modelCall"), dict) else None
        )
        approvals = self.jobs.list_action_requests(job["id"])
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            agent_run_id=agent_run["id"],
            artifact_ids=[*qa_artifact_ids, *[str(artifact["id"]) for artifact in artifact_refs]],
            diff_summary=diff_summary,
            runtime_health={
                "id": runtime.get("id"),
                "status": runtime_result.get("status"),
                "available": bool(runtime.get("available", runtime.get("executable", False))),
                "executable": bool(runtime.get("executable", False)),
                "reason": runtime_result.get("reason") or runtime.get("reason"),
            },
            model_calls=[model_call] if model_call else [],
            tool_calls=tool_calls,
            policy_decisions=policy_decisions,
            approvals=approvals,
            artifacts=artifact_refs,
            hashes=artifact_hashes(artifact_records),
        )
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=final_status == "completed",
            require_workflow_run=bool(evidence.get("workflowRunId")),
        )
        if final_status == "completed" and contract_errors:
            final_status = "evidence_ready"
            qa_verdict = "blocked"
            final_reason = "Evidence package contract is incomplete or unverifiable: " + " ".join(
                contract_errors
            )
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_verdict,
                risk_notes=[
                    {
                        "severity": "high",
                        "description": final_reason,
                        "mitigation": "Regenerate the evidence package with runtime, artifact refs, and SHA-256 hashes before completion.",
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
