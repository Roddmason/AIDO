from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import artifact_hashes, artifact_records_from_ids, artifact_ref, write_text_artifact
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .repository import AgentsRepository
from .runtime_status import RuntimeStatusService
from .security_agent_contract import (
    SECURITY_AGENT_ALLOWED_TOOLS,
    SECURITY_AGENT_ID,
    SECURITY_AGENT_MODEL_RUNTIMES,
    security_agent_contract,
    security_agent_status,
)
from .tool_broker import ToolBroker


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("generic_assignment", re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}")),
)
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "test-results",
}
DEPENDENCY_FILENAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "poetry.lock",
    "uv.lock",
}
DANGEROUS_FLAGS = {
    "--allow-host-write",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-ignore-permissions",
    "--mount",
    "--network=host",
    "--no-sandbox",
    "--privileged",
    "--volume",
}
DOCKER_CRITICAL_FLAGS = {"--mount", "--network=host", "--privileged", "--volume", "-v"}
MAX_FILE_BYTES = 512 * 1024
MAX_FILES_SCANNED = 5000


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _relative_path(path: Path, workspace: Path) -> str:
    return path.resolve(strict=False).relative_to(workspace.resolve(strict=False)).as_posix()


def _redacted_location(path: str, line: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": path}
    if line is not None:
        payload["line"] = line
    return payload


def _finding(
    *,
    check_id: str,
    severity: str,
    message: str,
    location: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "checkId": check_id,
        "severity": severity,
        "message": message,
        "location": location or {},
        "evidence": redact_secrets(evidence or {}),
    }


def _command_display(argv: list[str]) -> str:
    if not argv:
        return ""
    return " ".join(argv)


def _dangerous_command_flags(argv: list[str]) -> list[str]:
    findings: list[str] = []
    for index, arg in enumerate(argv):
        lowered = arg.strip().lower()
        if lowered == "--network" and index + 1 < len(argv) and argv[index + 1].strip().lower() == "host":
            findings.append("--network host")
            continue
        if lowered in DANGEROUS_FLAGS or any(lowered.startswith(f"{flag}=") for flag in DANGEROUS_FLAGS):
            findings.append(arg)
        if lowered in {"-v", "--cap-add", "--pid=host", "--ipc=host"}:
            findings.append(arg)
    return findings


def _is_docker_command(argv: list[str]) -> bool:
    return bool(argv and Path(argv[0]).name.lower() in {"docker", "docker.exe"})


class SecurityAgentRunner:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.security = SecurityPolicyRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)

    def status(self) -> dict[str, Any]:
        return security_agent_status(RuntimeStatusService(self.connection).list_provider_statuses())

    def _ensure_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": SECURITY_AGENT_ID,
                "name": "AIDO Security Agent",
                "role": "security_reviewer",
                "runtimeMode": "manual",
                "permissionProfile": "qa",
                "allowedTools": SECURITY_AGENT_ALLOWED_TOOLS,
                "allowedProviders": list(SECURITY_AGENT_MODEL_RUNTIMES),
                "allowedRuntimes": list(SECURITY_AGENT_MODEL_RUNTIMES),
                "allowRemote": True,
                "allowCli": False,
                "allowApi": True,
                "outputSchema": security_agent_contract()["outputSchema"],
            }
        )

    def _workspace(self, *, project_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if workspace["projectId"] != project_id:
            raise ValueError("SecurityAgent workspace does not belong to the project.")
        if workspace["status"] == "archived":
            raise ValueError("SecurityAgent cannot scan an archived workspace.")
        return workspace

    def _diff_artifact(self, *, project_id: str, artifact_id: str | None) -> dict[str, Any] | None:
        if not artifact_id:
            return None
        artifact = self.evidence.get_artifact_by_id(artifact_id)
        if artifact["projectId"] != project_id:
            raise ValueError("SecurityAgent diff artifact does not belong to the project.")
        return artifact

    def _scan_text_for_secrets(
        self,
        *,
        text: str,
        path: str,
        content_hash: str,
        findings: list[dict[str, Any]],
    ) -> None:
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern_id, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        _finding(
                            check_id="secret_scan",
                            severity="critical",
                            message=f"Secret-like token detected by {pattern_id}.",
                            location=_redacted_location(path, line_number),
                            evidence={"contentHash": content_hash, "patternId": pattern_id},
                        )
                    )

    def _scan_workspace_files(self, workspace_path: str, findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        workspace = Path(workspace_path).resolve(strict=False)
        files_scanned: list[dict[str, Any]] = []
        dependency_files: list[dict[str, Any]] = []
        scanned = 0
        for path in sorted(workspace.rglob("*")):
            if scanned >= MAX_FILES_SCANNED:
                findings.append(
                    _finding(
                        check_id="workspace_scan_limit",
                        severity="high",
                        message=f"Workspace scan reached file limit: {MAX_FILES_SCANNED}.",
                        evidence={"maxFiles": MAX_FILES_SCANNED},
                    )
                )
                break
            if any(part in IGNORED_PARTS for part in path.relative_to(workspace).parts):
                continue
            if path.is_symlink():
                findings.append(
                    _finding(
                        check_id="path_traversal",
                        severity="critical",
                        message="Workspace scan found a symlink; symlink targets are not accepted as trusted scan input.",
                        location=_redacted_location(_relative_path(path, workspace)),
                    )
                )
                continue
            if not path.is_file():
                continue
            content = path.read_bytes()
            content_hash = _hash_bytes(content)
            rel = _relative_path(path, workspace)
            files_scanned.append({"path": rel, "hash": content_hash, "sizeBytes": len(content)})
            scanned += 1
            if path.name in DEPENDENCY_FILENAMES:
                dependency_entry = {"path": rel, "hash": content_hash, "sizeBytes": len(content), "kind": path.name}
                dependency_files.append(dependency_entry)
                if path.name == "package.json":
                    try:
                        json.loads(content.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        findings.append(
                            _finding(
                                check_id="dependency_file_scan",
                                severity="high",
                                message="package.json is not valid JSON.",
                                location=_redacted_location(rel),
                                evidence={"contentHash": content_hash},
                            )
                        )
            if len(content) > MAX_FILE_BYTES:
                findings.append(
                    _finding(
                        check_id="file_scan_limit",
                        severity="medium",
                        message=f"File exceeds SecurityAgent text scan limit: {MAX_FILE_BYTES} bytes.",
                        location=_redacted_location(rel),
                        evidence={"contentHash": content_hash, "sizeBytes": len(content)},
                    )
                )
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            self._scan_text_for_secrets(text=text, path=rel, content_hash=content_hash, findings=findings)
        return files_scanned, dependency_files

    def _scan_diff_artifact(self, artifact: dict[str, Any] | None, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if artifact is None:
            return []
        content = Path(artifact["path"]).read_bytes()
        content_hash = _hash_bytes(content)
        artifact_entry = {
            "artifactId": artifact["id"],
            "path": artifact["path"],
            "hash": content_hash,
            "sizeBytes": len(content),
        }
        if len(content) <= MAX_FILE_BYTES:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text:
                self._scan_text_for_secrets(
                    text=text,
                    path=f"artifact:{artifact['id']}",
                    content_hash=content_hash,
                    findings=findings,
                )
        return [artifact_entry]

    def _scan_paths(self, *, workspace_path: str, paths_to_check: list[Any], findings: list[dict[str, Any]]) -> None:
        workspace = Path(workspace_path).resolve(strict=False)
        for index, raw_path in enumerate(paths_to_check):
            if not isinstance(raw_path, str) or not raw_path.strip():
                findings.append(
                    _finding(
                        check_id="path_traversal",
                        severity="critical",
                        message=f"pathsToCheck[{index}] must be a non-empty path string.",
                    )
                )
                continue
            candidate = Path(raw_path)
            resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (workspace / candidate).resolve(strict=False)
            if not _inside(resolved, workspace):
                findings.append(
                    _finding(
                        check_id="path_traversal",
                        severity="critical",
                        message="Path candidate resolves outside the allocated workspace.",
                        location={"path": raw_path},
                    )
                )

    def _scan_commands(self, command_candidates: list[Any], findings: list[dict[str, Any]]) -> None:
        for index, candidate in enumerate(command_candidates):
            if isinstance(candidate, list):
                argv = candidate
                label = _command_display([str(item) for item in candidate])
            elif isinstance(candidate, dict):
                argv = candidate.get("argv")
                label = str(candidate.get("label") or f"command[{index}]")
            else:
                findings.append(
                    _finding(
                        check_id="dangerous_command",
                        severity="high",
                        message=f"commandCandidates[{index}] must be a structured argv list or object.",
                    )
                )
                continue
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
                findings.append(
                    _finding(
                        check_id="dangerous_command",
                        severity="high",
                        message=f"{label} must use non-empty structured argv strings.",
                    )
                )
                continue
            dangerous = _dangerous_command_flags(argv)
            if not dangerous:
                continue
            docker = _is_docker_command(argv)
            for flag in dangerous:
                critical = docker and (flag in DOCKER_CRITICAL_FLAGS or flag == "--network host")
                findings.append(
                    _finding(
                        check_id="dangerous_command",
                        severity="critical" if critical else "high",
                        message=f"Dangerous command flag is not allowed: {flag}",
                        evidence={
                            "label": label,
                            "argvDigest": hashlib.sha256(json_dumps(argv).encode("utf-8")).hexdigest(),
                            "docker": docker,
                        },
                    )
                )

    def _scan_policy_decisions(self, *, project_id: str, workspace_id: str, findings: list[dict[str, Any]]) -> None:
        for decision in self.security.list_decisions(project_id=project_id):
            if decision.get("workspaceId") not in {None, workspace_id}:
                continue
            policy_decision = str(decision.get("decision") or "")
            if policy_decision not in {"deny", "requires_approval", "requires_human"}:
                continue
            severity = "critical" if policy_decision in {"deny", "requires_human"} else "medium"
            findings.append(
                _finding(
                    check_id="policy_violation",
                    severity=severity,
                    message=f"Policy decision requires security review: {policy_decision}.",
                    evidence={
                        "permissionDecisionId": decision["id"],
                        "riskLevel": decision.get("riskLevel"),
                        "reason": decision.get("reason"),
                    },
                )
            )

    def _verdict(self, findings: list[dict[str, Any]]) -> tuple[str, str]:
        if any(finding["severity"] == "critical" for finding in findings):
            return "blocked", "Critical deterministic security finding blocks completion."
        if findings:
            return "risk", "Deterministic security controls found reviewable risk."
        return "passed", "Deterministic security controls passed."

    def _write_findings_artifact(
        self,
        *,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps(redact_secrets(payload))
        artifact_file = write_text_artifact(root=self.root, artifact_id=artifact_id, suffix=".security.json", content=content)
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="security_report",
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={
                "name": "security-agent-findings.json",
                "source": SECURITY_AGENT_ID,
                "mimeType": "application/json",
                "sizeBytes": artifact_file["sizeBytes"],
                "hashAlgorithm": "sha256",
            },
        )

    def _model_runtime(self, preferred_runtime: str | None) -> dict[str, Any] | None:
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        eligible = [runtime for runtime in statuses if str(runtime.get("id") or "") in SECURITY_AGENT_MODEL_RUNTIMES]
        if preferred_runtime:
            return next((runtime for runtime in eligible if runtime.get("id") == preferred_runtime), None)
        status = security_agent_status(statuses)
        selected = status.get("selectedRuntimeId")
        return next((runtime for runtime in eligible if runtime.get("id") == selected), None)

    def _execute_optional_model_analysis(
        self,
        *,
        project_id: str,
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        payload: dict[str, Any],
        findings_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not payload.get("runModelAnalysis"):
            return None
        runtime = self._model_runtime(payload.get("preferredRuntime"))
        if not runtime or not runtime.get("executable"):
            return {
                "status": "unavailable",
                "reason": "Optional SecurityAgent model analysis skipped because no executable model runtime is configured.",
            }
        runtime_id = str(runtime["id"])
        model = payload.get("model")
        if runtime_id == "ollama":
            model = model or next(iter(runtime.get("models") or []), None)
        broker = ToolBroker(self.connection, artifact_root=self.root)
        result = broker.evaluate_tool_call(
            project_id=project_id,
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": runtime_id,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "security_agent_model_call",
                "runtimeId": runtime_id,
                "capability": "chat",
                "input": {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are SecurityAgent. Provide optional analysis only. "
                                "Do not override deterministic verdicts. Return concise JSON."
                            ),
                        },
                        {"role": "user", "content": json_dumps(redact_secrets(findings_payload))},
                    ],
                    "temperature": 0.1,
                },
                "networkRequired": runtime_id == "openai_compatible",
                "secretsRequired": False,
                "approvalGrantId": payload.get("approvalGrantId"),
                "execute": True,
                "timeoutSeconds": 900,
            },
        )
        tool_call = result["toolCall"]
        execution_result = (tool_call.get("payload") or {}).get("executionResult") or {}
        return {
            "status": "completed" if tool_call.get("status") == "completed" else "failed",
            "runtimeId": runtime_id,
            "toolCallId": tool_call["id"],
            "outputArtifactId": execution_result.get("outputArtifactId"),
            "reason": execution_result.get("reason") or (tool_call.get("payload") or {}).get("decisionReason"),
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload["projectId"])
        workspace = self._workspace(project_id=project_id, workspace_id=str(payload["workspaceId"]))
        diff_artifact = self._diff_artifact(project_id=project_id, artifact_id=payload.get("diffArtifactId"))
        task_id = str(payload.get("taskId") or "security_agent")
        profile = self._ensure_profile()
        job_result = self.jobs.create_job(
            project_id=project_id,
            kind="agent.security",
            status="running",
            payload={"taskId": task_id, "workspaceId": workspace["id"], "diffArtifactId": payload.get("diffArtifactId")},
        )
        job = job_result["job"]
        agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=redact_secrets({**payload, "workspacePath": workspace["path"]}),
            output_payload={},
            job_id=job["id"],
            status="running",
        )

        findings: list[dict[str, Any]] = []
        files_scanned, dependency_files = self._scan_workspace_files(workspace["path"], findings)
        diff_artifacts = self._scan_diff_artifact(diff_artifact, findings)
        self._scan_paths(workspace_path=workspace["path"], paths_to_check=payload.get("pathsToCheck") or [], findings=findings)
        self._scan_commands(payload.get("commandCandidates") or [], findings)
        self._scan_policy_decisions(project_id=project_id, workspace_id=workspace["id"], findings=findings)
        verdict, reason = self._verdict(findings)
        findings_payload = {
            "status": verdict,
            "verdict": verdict,
            "reason": reason,
            "findings": findings,
            "filesScanned": files_scanned,
            "dependencyFiles": dependency_files,
            "diffArtifacts": diff_artifacts,
        }
        model_analysis = self._execute_optional_model_analysis(
            project_id=project_id,
            workspace=workspace,
            agent_run=agent_run,
            job=job,
            profile=profile,
            payload=payload,
            findings_payload=findings_payload,
        )
        if model_analysis:
            findings_payload["modelAnalysis"] = model_analysis
        findings_artifact = self._write_findings_artifact(project_id=project_id, payload=findings_payload)
        artifact_ids = [findings_artifact["id"], *[item["artifactId"] for item in diff_artifacts]]
        if model_analysis and isinstance(model_analysis.get("outputArtifactId"), str):
            artifact_ids.append(str(model_analysis["outputArtifactId"]))
        qa_verdict = {
            "passed": "security_passed",
            "risk": "security_risk",
            "blocked": "security_blocked",
        }[verdict]
        artifact_records = artifact_records_from_ids(self.evidence, artifact_ids)
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) == agent_run["id"]
        ]
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=None,
            agent_id=SECURITY_AGENT_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id=f"{SECURITY_AGENT_ID}.deterministic_controls",
            task_id=task_id,
            test_plan="Run deterministic SecurityAgent controls over workspace, commands, paths, dependencies, and policy decisions.",
            acceptance_checklist=[
                "Workspace files scanned with hashes.",
                "Secret-like tokens are blocked.",
                "Dangerous command flags are detected.",
                "Path traversal candidates are blocked.",
                "Policy violations are included in findings.",
                "Model analysis is optional and cannot replace deterministic verdicts.",
            ],
            test_results=[
                {
                    "command": "security_agent.deterministic_controls",
                    "status": verdict,
                    "findings": len(findings),
                    "filesScanned": len(files_scanned),
                    "dependencyFiles": len(dependency_files),
                    "outputRef": findings_artifact["id"],
                    "metadata": {"checks": ["secret_scan", "dangerous_command", "dependency_file_scan", "policy_violation", "path_traversal"]},
                }
            ],
            logs=[redact_secrets({"source": SECURITY_AGENT_ID, "reason": reason, "modelAnalysis": model_analysis})],
            risk_notes=[
                {
                    "severity": "low" if verdict == "passed" else "critical" if verdict == "blocked" else "high",
                    "description": reason,
                    "mitigation": "Review SecurityAgent findings and remove blocked secrets, traversal, dangerous flags, or policy violations.",
                }
            ],
            artifact_ids=artifact_ids,
            diff_summary={"diffArtifactId": diff_artifact["id"] if diff_artifact else None},
            runtime_health={
                "id": f"{SECURITY_AGENT_ID}.deterministic_controls",
                "status": "completed",
                "available": True,
                "executable": True,
                "filesScanned": len(files_scanned),
                "dependencyFiles": len(dependency_files),
                "modelAnalysisStatus": (model_analysis or {}).get("status") if model_analysis else "not_requested",
            },
            model_calls=[],
            tool_calls=tool_calls,
            policy_decisions=[],
            approvals=self.jobs.list_action_requests(job["id"]),
            artifacts=[artifact_ref(artifact) for artifact in artifact_records],
            hashes=artifact_hashes(artifact_records),
            qa_verdict=qa_verdict,
        )
        for artifact_id in artifact_ids:
            self.evidence.attach_artifact_to_evidence(artifact_id=artifact_id, evidence_package_id=evidence["id"])
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=verdict == "passed",
            require_workflow_run=False,
        )
        if verdict == "passed" and contract_errors:
            verdict = "blocked"
            qa_verdict = "security_blocked"
            reason = "Evidence package contract is incomplete or unverifiable: " + " ".join(contract_errors)
            findings_payload["status"] = verdict
            findings_payload["verdict"] = verdict
            findings_payload["reason"] = reason
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_verdict,
                risk_notes=[
                    {
                        "severity": "high",
                        "description": reason,
                        "mitigation": "Regenerate SecurityAgent evidence with artifact refs and SHA-256 hashes before completion.",
                    }
                ],
            )
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status="completed" if verdict == "passed" else "failed",
            output_payload={
                **findings_payload,
                "findingsArtifactId": findings_artifact["id"],
                "evidence_refs": [evidence["id"], findings_artifact["id"]],
            },
        )
        job = self.jobs.update_job_status(
            job["id"],
            status="completed" if verdict == "passed" else "failed",
            metadata={"status": verdict, "reason": reason, "evidencePackageId": evidence["id"], "findingsArtifactId": findings_artifact["id"]},
        )
        return {
            "status": verdict,
            "verdict": verdict,
            "reason": reason,
            "contract": security_agent_contract(),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "findings": findings,
            "filesScanned": files_scanned,
            "dependencyFiles": dependency_files,
            "findingsArtifact": findings_artifact,
            "modelAnalysis": model_analysis,
        }
