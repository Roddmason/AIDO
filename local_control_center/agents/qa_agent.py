"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import artifact_hashes, artifact_records_from_ids, artifact_ref, write_text_artifact
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .repository import AgentsRepository
from .tool_broker import ToolBroker


QA_AGENT_ID = "qa_agent"
QA_AGENT_ALLOWED_TOOLS = ["shell"]
QA_EXECUTION_MODES = {"restricted_subprocess", "docker"}
QA_TERMINAL_VERDICTS = {"passed", "failed", "skipped_with_reason", "blocked"}
QA_DEFAULT_TIMEOUT_SECONDS = 120
QA_MAX_TIMEOUT_SECONDS = 300


def qa_agent_contract() -> dict[str, Any]:
    return {
        "id": QA_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["argv"],
                        "properties": {
                            "label": {"type": "string"},
                            "argv": {"type": "array", "items": {"type": "string"}},
                            "critical": {"type": "boolean"},
                            "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": QA_MAX_TIMEOUT_SECONDS},
                        },
                    },
                },
                "metadata": {"type": "object"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["status", "verdict", "results", "evidencePackage"],
            "properties": {
                "status": {"type": "string", "enum": sorted(QA_TERMINAL_VERDICTS)},
                "verdict": {"type": "string", "enum": sorted(QA_TERMINAL_VERDICTS)},
                "results": {"type": "array", "items": {"type": "object"}},
                "evidencePackage": {"type": "object"},
            },
        },
        "allowedTools": QA_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": ["command_execution"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "exit_codes_and_artifacts_only",
    }


def _display_command(argv: list[str]) -> str:
    if not argv:
        return ""
    executable = Path(argv[0]).name or str(argv[0])
    normalized = "python" if executable.lower() in {"python.exe", "python3.exe", "py.exe"} else executable
    return " ".join([normalized, *[str(item) for item in argv[1:]]])


def _bounded_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = QA_DEFAULT_TIMEOUT_SECONDS
    return max(1, min(timeout, QA_MAX_TIMEOUT_SECONDS))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stream_hash(execution_result: dict[str, Any], stream: str) -> str:
    promoted_hash = execution_result.get(f"{stream}Hash")
    if isinstance(promoted_hash, str) and promoted_hash:
        return promoted_hash
    content = execution_result.get(stream)
    return _hash_text(content if isinstance(content, str) else "")


def _normalize_commands(commands: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        if isinstance(command, list):
            argv = command
            label = _display_command([str(item) for item in command])
            critical = True
            timeout_seconds = QA_DEFAULT_TIMEOUT_SECONDS
        elif isinstance(command, dict):
            if isinstance(command.get("command"), str):
                raise ValueError(f"commands[{index}] must use structured argv, not a command string.")
            argv = command.get("argv")
            label = str(command.get("label") or "").strip()
            critical = bool(command.get("critical", True))
            timeout_seconds = _bounded_timeout(command.get("timeoutSeconds"))
        else:
            raise ValueError(f"commands[{index}] must be an object or structured argv list.")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError(f"commands[{index}].argv must be a non-empty structured argv list.")
        resolved_argv = [str(item) for item in argv]
        normalized.append(
            {
                "label": label or _display_command(resolved_argv),
                "argv": resolved_argv,
                "critical": critical,
                "timeoutSeconds": timeout_seconds,
            }
        )
    return normalized


def discover_qa_commands(workspace_path: str | Path) -> list[dict[str, Any]]:
    workspace = Path(workspace_path)
    commands: list[dict[str, Any]] = []
    tests_py = workspace / "tests_py"
    tests = workspace / "tests"
    if tests_py.exists() and tests_py.is_dir():
        commands.append({"label": "Python tests", "argv": ["uv", "run", "pytest", "tests_py", "-q"], "critical": True})
    elif tests.exists() and tests.is_dir():
        commands.append({"label": "Python tests", "argv": ["uv", "run", "pytest", "tests", "-q"], "critical": True})

    package_json = workspace / "package.json"
    if package_json.exists() and package_json.is_file():
        try:
            scripts = (json.loads(package_json.read_text(encoding="utf-8")).get("scripts") or {}).keys()
        except (OSError, json.JSONDecodeError):
            scripts = []
        script_names = set(str(item) for item in scripts)
        for label, candidates in (
            ("Web tests", ("test:web", "test")),
            ("Build", ("build:control-center", "build:web", "build")),
            ("Typecheck", ("typecheck:web", "typecheck")),
            ("Lint", ("lint:py", "lint:web", "lint")),
        ):
            script = next((candidate for candidate in candidates if candidate in script_names), None)
            if script:
                commands.append(
                    {
                        "label": label,
                        "argv": ["corepack", "pnpm@10.24.0", "run", script],
                        "critical": True,
                    }
                )
    return commands


def qa_verdict_allows_completion(verdict: str, results: list[dict[str, Any]]) -> bool:
    if verdict != "passed" or not results:
        return False
    for result in results:
        if result.get("status") != "passed":
            return False
        if result.get("exitCode") != 0:
            return False
        if result.get("execution") not in QA_EXECUTION_MODES:
            return False
        if not result.get("toolCallId"):
            return False
        hashes = result.get("artifactHashes") or {}
        if not hashes.get("stdoutHash") or not hashes.get("stderrHash") or not hashes.get("outputArtifactHash"):
            return False
    return True


def qa_verdict_from_results(results: list[dict[str, Any]]) -> tuple[str, str]:
    if not results:
        return "blocked", "No QA commands were available to execute."
    if any(result.get("status") == "failed" for result in results):
        return "failed", "At least one critical QA command failed or was blocked."
    if any(result.get("status") == "skipped_with_reason" for result in results):
        return "skipped_with_reason", "At least one QA command was skipped with a technical reason."
    if all(result.get("status") == "passed" for result in results):
        return "passed", "All QA commands passed by exit code."
    return "blocked", "QA results were incomplete."


class QAAgentRunner:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)

    def contract(self) -> dict[str, Any]:
        return qa_agent_contract()

    def _ensure_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": QA_AGENT_ID,
                "name": "AIDO QA Agent",
                "role": "qa_reviewer",
                "runtimeMode": "manual",
                "permissionProfile": "qa",
                "allowedTools": QA_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [],
                "allowedRuntimes": [],
                "allowRemote": False,
                "allowCli": True,
                "allowApi": False,
                "outputSchema": qa_agent_contract()["outputSchema"],
            }
        )

    def _workspace(self, *, project_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if workspace["projectId"] != project_id:
            raise ValueError("QAAgent workspace does not belong to the project.")
        if workspace["status"] == "archived":
            raise ValueError("QAAgent cannot execute against an archived workspace.")
        return workspace

    def _output_artifact(
        self,
        *,
        project_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps(redact_secrets({"kind": "qa_command_result", **result}))
        artifact = write_text_artifact(root=self.root, artifact_id=artifact_id, suffix=".qa.json", content=content)
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="test_report",
            path=artifact["path"],
            content_hash=artifact["hash"],
            metadata={
                "name": f"qa-command-{result['index']}.json",
                "source": QA_AGENT_ID,
                "mimeType": "application/json",
                "sizeBytes": artifact["sizeBytes"],
                "hashAlgorithm": "sha256",
            },
        )

    def _result_from_tool_call(
        self,
        *,
        project_id: str,
        command: dict[str, Any],
        index: int,
        tool_call: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        payload = tool_call.get("payload") or {}
        execution_result = payload.get("executionResult") or {}
        execution = payload.get("execution")
        executed = bool(execution_result.get("executed", False))
        return_code = execution_result.get("returnCode")
        blocked = bool(execution_result.get("blocked", False))
        timed_out = bool(execution_result.get("timedOut", False))
        if tool_call.get("status") == "completed" and return_code == 0 and not timed_out:
            status = "passed"
        elif not command["critical"] and not executed:
            status = "skipped_with_reason"
        else:
            status = "failed"

        reason = execution_result.get("reason") or payload.get("decisionReason")
        result = {
            "index": index,
            "label": command["label"],
            "command": _display_command(command["argv"]),
            "argv": command["argv"],
            "critical": command["critical"],
            "status": status,
            "toolCallStatus": tool_call.get("status"),
            "execution": execution,
            "executed": executed,
            "exitCode": return_code,
            "returnCode": return_code,
            "timedOut": timed_out,
            "blocked": blocked or tool_call.get("status") in {"denied", "approval_required"},
            "reason": reason,
            "durationMs": execution_result.get("durationMs"),
            "toolCallId": tool_call.get("id"),
            "stdout": execution_result.get("stdout") if isinstance(execution_result.get("stdout"), str) else "",
            "stderr": execution_result.get("stderr") if isinstance(execution_result.get("stderr"), str) else "",
            "stdoutArtifactId": execution_result.get("stdoutArtifactId"),
            "stderrArtifactId": execution_result.get("stderrArtifactId"),
            "artifactHashes": {
                "stdoutHash": _stream_hash(execution_result, "stdout"),
                "stderrHash": _stream_hash(execution_result, "stderr"),
            },
            "metadata": {
                "operation": "qa_agent_command",
                "decision": payload.get("decision"),
                "decisionReason": payload.get("decisionReason"),
                "permissionDecisionId": payload.get("permissionDecisionId"),
            },
        }
        output_artifact = self._output_artifact(project_id=project_id, result=result)
        result["outputArtifactId"] = output_artifact["id"]
        result["artifactHashes"]["outputArtifactHash"] = output_artifact["hash"]
        artifact_ids = [
            artifact_id
            for artifact_id in (
                execution_result.get("stdoutArtifactId"),
                execution_result.get("stderrArtifactId"),
                output_artifact["id"],
            )
            if isinstance(artifact_id, str) and artifact_id
        ]
        output_refs = [artifact_id for artifact_id in artifact_ids if artifact_id != output_artifact["id"]]
        result["outputRef"] = output_refs[0] if output_refs else output_artifact["id"]
        result["outputRefs"] = artifact_ids
        return result, artifact_ids

    def run_for_context(
        self,
        *,
        project_id: str,
        workspace_id: str,
        task_id: str,
        commands: list[Any],
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
        job_id: str | None = None,
        parent_agent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(project_id=project_id, workspace_id=workspace_id)
        normalized = _normalize_commands(commands) if commands else discover_qa_commands(workspace["path"])
        profile = self._ensure_profile()
        agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=QA_AGENT_ID,
            task_id=f"{task_id}:qa",
            input_payload={
                "workspaceId": workspace_id,
                "commands": normalized,
                "parentAgentRunId": parent_agent_run_id,
                "metadata": metadata or {},
            },
            output_payload={},
            job_id=job_id,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            status="running",
        )
        broker = ToolBroker(self.connection, artifact_root=self.root)
        results: list[dict[str, Any]] = []
        artifact_ids: list[str] = []
        policy_decisions: list[dict[str, Any]] = []
        for index, command in enumerate(normalized):
            tool_result = broker.evaluate_tool_call(
                project_id=project_id,
                agent_run_id=agent_run["id"],
                agent_profile=profile,
                job_id=job_id,
                tool_call={
                    "tool": "shell",
                    "command": _display_command(command["argv"]),
                    "argv": command["argv"],
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "path": workspace["path"],
                    "operation": "qa_agent_command",
                    "execute": True,
                    "timeoutSeconds": command["timeoutSeconds"],
                },
            )
            if isinstance(tool_result.get("decision"), dict):
                policy_decisions.append(tool_result["decision"])
            result, command_artifacts = self._result_from_tool_call(
                project_id=project_id,
                command=command,
                index=index,
                tool_call=tool_result["toolCall"],
            )
            results.append(result)
            artifact_ids.extend(command_artifacts)

        verdict, reason = qa_verdict_from_results(results)
        completed = qa_verdict_allows_completion(verdict, results)
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status="completed" if completed else "failed",
            output_payload={
                "status": verdict,
                "verdict": verdict,
                "reason": reason,
                "results": results,
                "artifactIds": sorted(set(artifact_ids)),
                "policyDecisions": policy_decisions,
            },
        )
        return {
            "status": verdict,
            "verdict": verdict,
            "reason": reason,
            "workspace": workspace,
            "agentRun": agent_run,
            "results": results,
            "artifactIds": sorted(set(artifact_ids)),
            "policyDecisions": policy_decisions,
            "contract": qa_agent_contract(),
        }

    def attach_artifacts_to_evidence(self, *, evidence_id: str, artifact_ids: list[str]) -> None:
        for artifact_id in sorted(set(artifact_ids)):
            self.evidence.attach_artifact_to_evidence(artifact_id=artifact_id, evidence_package_id=evidence_id)

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload["projectId"])
        workspace_id = str(payload["workspaceId"])
        task_id = str(payload.get("taskId") or "qa_agent")
        workflow_run_id = str(payload.get("workflowRunId") or "").strip() or None
        workflow_step_id = str(payload.get("workflowStepId") or "").strip() or None
        job_result = self.jobs.create_job(
            project_id=project_id,
            kind="agent.qa",
            status="running",
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            payload={"workspaceId": workspace_id, "taskId": task_id},
        )
        qa_run = self.run_for_context(
            project_id=project_id,
            workspace_id=workspace_id,
            task_id=task_id,
            commands=payload.get("commands") or [],
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            job_id=job_result["job"]["id"],
            metadata=payload.get("metadata") or {},
        )
        artifact_records = artifact_records_from_ids(self.evidence, qa_run["artifactIds"])
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) == qa_run["agentRun"]["id"]
        ]
        qa_runtime_available = bool(qa_run["results"])
        qa_runtime_executable = any(
            bool(result.get("executed")) and not bool(result.get("blocked"))
            for result in qa_run["results"]
        )
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            agent_id=QA_AGENT_ID,
            agent_run_id=qa_run["agentRun"]["id"],
            job_id=job_result["job"]["id"],
            workspace_id=workspace_id,
            runtime_id=f"{QA_AGENT_ID}.tool_broker",
            task_id=task_id,
            test_plan="Execute QAAgent commands through ToolBroker and compute verdicts from exit codes and artifacts.",
            acceptance_checklist=[
                "Commands use structured argv.",
                "Commands execute inside the allocated workspace.",
                "Verdict derives from exit codes and artifacts.",
                "Skipped commands include a technical reason.",
            ],
            test_results=qa_run["results"],
            logs=[redact_secrets({"source": QA_AGENT_ID, "reason": qa_run["reason"]})],
            risk_notes=[
                {
                    "severity": "low" if qa_run["verdict"] == "passed" else "medium",
                    "description": qa_run["reason"],
                    "mitigation": "Fix failing QA commands or configure a real command that exists in the workspace.",
                }
            ],
            artifact_ids=qa_run["artifactIds"],
            diff_summary={"artifactIds": qa_run["artifactIds"]},
            runtime_health={
                "id": f"{QA_AGENT_ID}.tool_broker",
                "status": qa_run["verdict"],
                "available": qa_runtime_available,
                "executable": qa_runtime_executable,
                "commands": len(qa_run["results"]),
                "reason": qa_run["reason"],
            },
            model_calls=[],
            tool_calls=tool_calls,
            policy_decisions=qa_run["policyDecisions"],
            approvals=self.jobs.list_action_requests(job_result["job"]["id"]),
            artifacts=[artifact_ref(artifact) for artifact in artifact_records],
            hashes=artifact_hashes(artifact_records),
            evidence_source="qa_passed_by_command" if qa_run["verdict"] == "passed" else "evidence_collected",
            qa_verdict=qa_run["verdict"],
        )
        self.attach_artifacts_to_evidence(evidence_id=evidence["id"], artifact_ids=qa_run["artifactIds"])
        completed = qa_verdict_allows_completion(qa_run["verdict"], qa_run["results"])
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=completed,
            require_workflow_run=bool(workflow_run_id) if completed else False,
        )
        if completed and contract_errors:
            completed = False
            qa_run["verdict"] = "blocked"
            qa_run["reason"] = "Evidence package contract is incomplete or unverifiable: " + " ".join(contract_errors)
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_run["verdict"],
                risk_notes=[
                    {
                        "severity": "high",
                        "description": qa_run["reason"],
                        "mitigation": "Regenerate QAAgent evidence with artifact refs and SHA-256 hashes before completion.",
                    }
                ],
            )
            qa_run["agentRun"] = self.agents.update_agent_run_status(
                qa_run["agentRun"]["id"],
                status="failed",
                output_payload={
                    "status": qa_run["verdict"],
                    "verdict": qa_run["verdict"],
                    "reason": qa_run["reason"],
                    "results": qa_run["results"],
                    "artifactIds": sorted(set(qa_run["artifactIds"])),
                    "evidence_refs": [evidence["id"], *sorted(set(qa_run["artifactIds"]))],
                },
            )
        else:
            qa_run["agentRun"] = self.agents.update_agent_run_status(
                qa_run["agentRun"]["id"],
                status="completed" if completed else "failed",
                output_payload={
                    **(qa_run["agentRun"].get("output") or {}),
                    "evidence_refs": [evidence["id"], *sorted(set(qa_run["artifactIds"]))],
                },
            )
        job = self.jobs.update_job_status(
            job_result["job"]["id"],
            status="completed" if completed else "failed",
            metadata={"qaVerdict": qa_run["verdict"], "evidencePackageId": evidence["id"]},
        )
        return {
            "status": qa_run["verdict"],
            "verdict": qa_run["verdict"],
            "reason": qa_run["reason"],
            "contract": qa_agent_contract(),
            "workspace": qa_run["workspace"],
            "job": job,
            "agentRun": qa_run["agentRun"],
            "evidencePackage": evidence,
            "results": qa_run["results"],
        }
