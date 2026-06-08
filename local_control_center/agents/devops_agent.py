from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tomllib
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import artifact_hashes, artifact_records_from_ids, artifact_ref, write_text_artifact
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.security_policy.sandbox import DockerSandbox
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .devops_agent_contract import (
    DEVOPS_AGENT_ALLOWED_TOOLS,
    DEVOPS_AGENT_ID,
    devops_agent_contract,
    devops_agent_status,
)
from .qa_agent import _display_command, _hash_text, _stream_hash
from .repository import AgentsRepository
from .tool_broker import ToolBroker


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
CONFIG_FILENAMES = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "uv.lock",
}
MAX_FILE_BYTES = 512 * 1024
MAX_FILES_SCANNED = 2000
DEFAULT_BUILD_SCRIPT_CANDIDATES = ("build:control-center", "build:web", "build")


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _relative_path(path: Path, workspace: Path) -> str:
    return path.resolve(strict=False).relative_to(workspace.resolve(strict=False)).as_posix()


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


def _command_result_artifact_payload(result: dict[str, Any]) -> dict[str, Any]:
    return redact_secrets({"kind": "devops_command_result", **result})


def _corepack_argv(script: str) -> list[str]:
    executable = shutil.which("corepack.cmd") or shutil.which("corepack") or "corepack"
    return [executable, "pnpm@10.24.0", "run", script]


def _corepack_policy_command(script: str) -> str:
    return f"corepack pnpm@10.24.0 run {script}"


def _status_from_findings_and_commands(findings: list[dict[str, Any]], commands: list[dict[str, Any]]) -> tuple[str, str]:
    if any(finding["severity"] == "critical" for finding in findings):
        return "blocked", "Critical DevOps configuration finding blocks validation."
    if any(command.get("status") == "failed" and command.get("critical") for command in commands):
        return "failed", "At least one critical DevOps command failed or was blocked."
    if findings or any(command.get("status") == "skipped_with_reason" for command in commands):
        return "risk", "DevOps validation found configuration or command risks."
    return "passed", "DevOps file and command checks passed."


class DevOpsAgentRunner:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.security = SecurityPolicyRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)

    def status(self) -> dict[str, Any]:
        return devops_agent_status()

    def _ensure_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": DEVOPS_AGENT_ID,
                "name": "AIDO DevOps Agent",
                "role": "devops",
                "runtimeMode": "manual",
                "permissionProfile": "qa",
                "allowedTools": DEVOPS_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [],
                "allowedRuntimes": [],
                "allowRemote": False,
                "allowCli": True,
                "allowApi": False,
                "outputSchema": devops_agent_contract()["outputSchema"],
            }
        )

    def _workspace(self, *, project_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if workspace["projectId"] != project_id:
            raise ValueError("DevOpsAgent workspace does not belong to the project.")
        if workspace["status"] == "archived":
            raise ValueError("DevOpsAgent cannot validate an archived workspace.")
        return workspace

    def _scan_powershell(self, *, rel: str, text: str, content_hash: str, findings: list[dict[str, Any]]) -> None:
        legacy_patterns = (
            ("legacy_script", re.compile(r"\bnpm\s+(run\s+)?(build|test|start|install)\b", re.I), "PowerShell script uses legacy npm invocation."),
            ("local_first", re.compile(r"\b(Invoke-WebRequest|curl|wget)\b", re.I), "PowerShell script performs network access and must be reviewed for local-first constraints."),
            ("local_first", re.compile(r"\b0\.0\.0\.0\b"), "PowerShell script binds to 0.0.0.0 instead of loopback."),
            ("privilege_escalation", re.compile(r"\bStart-Process\b.*\b-Verb\s+RunAs\b", re.I), "PowerShell script requests elevated execution."),
        )
        for check_id, pattern, message in legacy_patterns:
            if pattern.search(text):
                findings.append(
                    _finding(
                        check_id=check_id,
                        severity="critical" if check_id == "privilege_escalation" else "medium",
                        message=message,
                        location={"path": rel},
                        evidence={"contentHash": content_hash},
                    )
                )

    def _scan_docker_file(self, *, rel: str, text: str, content_hash: str, findings: list[dict[str, Any]]) -> None:
        checks = (
            ("dockerfile_policy", re.compile(r"(?im)^\s*ADD\s+https?://"), "Dockerfile uses remote ADD; prefer explicit verified fetch steps."),
            ("dockerfile_policy", re.compile(r"(?im)\bcurl\b.*\|\s*(sh|bash)"), "Dockerfile pipes network content into a shell."),
            ("local_first", re.compile(r"\b--network=host\b|\bnetwork_mode:\s*host\b", re.I), "Docker configuration requests host networking."),
        )
        for check_id, pattern, message in checks:
            if pattern.search(text):
                findings.append(
                    _finding(
                        check_id=check_id,
                        severity="high",
                        message=message,
                        location={"path": rel},
                        evidence={"contentHash": content_hash},
                    )
                )

    def _scan_package_json(
        self,
        *,
        path: Path,
        rel: str,
        content: bytes,
        content_hash: str,
        findings: list[dict[str, Any]],
        versions: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            package = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            findings.append(
                _finding(
                    check_id="package_consistency",
                    severity="high",
                    message="package.json is not valid JSON.",
                    location={"path": rel},
                    evidence={"contentHash": content_hash},
                )
            )
            return None
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if not isinstance(scripts, dict):
            scripts = {}
        package_manager = str(package.get("packageManager") or "") if isinstance(package, dict) else ""
        if package_manager:
            versions["packageManager"] = package_manager
        else:
            findings.append(
                _finding(
                    check_id="package_consistency",
                    severity="medium",
                    message="package.json does not declare packageManager.",
                    location={"path": rel},
                    evidence={"contentHash": content_hash},
                )
            )
        if package_manager and not package_manager.startswith("pnpm@"):
            findings.append(
                _finding(
                    check_id="package_consistency",
                    severity="medium",
                    message="packageManager is not pnpm; local tooling expects pnpm.",
                    location={"path": rel},
                    evidence={"packageManager": package_manager},
                )
            )
        engines = package.get("engines") if isinstance(package, dict) else None
        if isinstance(engines, dict) and engines.get("node"):
            versions["nodeEngine"] = str(engines["node"])
        for name, command in scripts.items():
            text = str(command)
            if re.search(r"\bnpm\s+(run\s+)?(build|test|start|install)\b", text, re.I):
                findings.append(
                    _finding(
                        check_id="legacy_script",
                        severity="medium",
                        message=f"package script '{name}' invokes npm instead of pnpm/corepack.",
                        location={"path": rel, "script": str(name)},
                        evidence={"contentHash": content_hash},
                    )
                )
            if re.search(r"\b0\.0\.0\.0\b", text):
                findings.append(
                    _finding(
                        check_id="local_first",
                        severity="medium",
                        message=f"package script '{name}' binds to 0.0.0.0 instead of loopback.",
                        location={"path": rel, "script": str(name)},
                        evidence={"contentHash": content_hash},
                    )
                )
        return {"path": str(path), "scripts": scripts}

    def _scan_pyproject(
        self,
        *,
        rel: str,
        content: bytes,
        content_hash: str,
        findings: list[dict[str, Any]],
        versions: dict[str, Any],
    ) -> None:
        try:
            data = tomllib.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            findings.append(
                _finding(
                    check_id="pyproject_consistency",
                    severity="high",
                    message="pyproject.toml is not valid TOML.",
                    location={"path": rel},
                    evidence={"contentHash": content_hash},
                )
            )
            return
        project = data.get("project") if isinstance(data, dict) else None
        if isinstance(project, dict) and project.get("requires-python"):
            versions["pythonRequires"] = str(project["requires-python"])

    def _scan_workspace(
        self,
        workspace_path: str,
        findings: list[dict[str, Any]],
        versions: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        workspace = Path(workspace_path).resolve(strict=False)
        files_scanned: list[dict[str, Any]] = []
        package_info: dict[str, Any] | None = None
        scanned = 0
        for path in sorted(workspace.rglob("*")):
            if scanned >= MAX_FILES_SCANNED:
                findings.append(
                    _finding(
                        check_id="workspace_scan_limit",
                        severity="high",
                        message=f"DevOpsAgent reached file scan limit: {MAX_FILES_SCANNED}.",
                    )
                )
                break
            rel_parts = path.relative_to(workspace).parts
            if any(part in IGNORED_PARTS for part in rel_parts):
                continue
            if not path.is_file():
                continue
            name = path.name
            if name not in CONFIG_FILENAMES and path.suffix.lower() != ".ps1":
                continue
            content = path.read_bytes()
            content_hash = _hash_bytes(content)
            rel = _relative_path(path, workspace)
            files_scanned.append({"path": rel, "hash": content_hash, "sizeBytes": len(content)})
            scanned += 1
            if len(content) > MAX_FILE_BYTES:
                findings.append(
                    _finding(
                        check_id="file_scan_limit",
                        severity="medium",
                        message=f"DevOps config file exceeds scan limit: {MAX_FILE_BYTES} bytes.",
                        location={"path": rel},
                        evidence={"contentHash": content_hash, "sizeBytes": len(content)},
                    )
                )
                continue
            text = ""
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                pass
            if name == "package.json":
                package_info = self._scan_package_json(
                    path=path,
                    rel=rel,
                    content=content,
                    content_hash=content_hash,
                    findings=findings,
                    versions=versions,
                )
            elif name == "pyproject.toml":
                self._scan_pyproject(rel=rel, content=content, content_hash=content_hash, findings=findings, versions=versions)
            elif path.suffix.lower() == ".ps1" and text:
                self._scan_powershell(rel=rel, text=text, content_hash=content_hash, findings=findings)
            elif name in {"Dockerfile", "docker-compose.yml", "docker-compose.yaml"} and text:
                self._scan_docker_file(rel=rel, text=text, content_hash=content_hash, findings=findings)
        return files_scanned, package_info

    def _requested_build_scripts(self, payload: dict[str, Any], package_info: dict[str, Any] | None) -> list[str]:
        requested = payload.get("buildScripts")
        if requested:
            return [str(script) for script in requested if isinstance(script, str) and script.strip()]
        scripts = (package_info or {}).get("scripts") or {}
        return [candidate for candidate in DEFAULT_BUILD_SCRIPT_CANDIDATES if candidate in scripts][:1]

    def _output_artifact(self, *, project_id: str, result: dict[str, Any]) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps(_command_result_artifact_payload(result))
        artifact = write_text_artifact(root=self.root, artifact_id=artifact_id, suffix=".devops-command.json", content=content)
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="devops_command_report",
            path=artifact["path"],
            content_hash=artifact["hash"],
            metadata={
                "name": f"devops-command-{result['index']}.json",
                "source": DEVOPS_AGENT_ID,
                "mimeType": "application/json",
                "sizeBytes": artifact["sizeBytes"],
                "hashAlgorithm": "sha256",
            },
        )

    def _result_from_tool_call(
        self,
        *,
        project_id: str,
        index: int,
        label: str,
        argv: list[str],
        critical: bool,
        tool_call: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        payload = tool_call.get("payload") or {}
        execution_result = payload.get("executionResult") or {}
        return_code = execution_result.get("returnCode")
        timed_out = bool(execution_result.get("timedOut", False))
        if tool_call.get("status") == "completed" and return_code == 0 and not timed_out:
            status = "passed"
        elif critical:
            status = "failed"
        else:
            status = "skipped_with_reason"
        result = {
            "index": index,
            "label": label,
            "command": _display_command(argv),
            "argv": argv,
            "critical": critical,
            "status": status,
            "toolCallStatus": tool_call.get("status"),
            "execution": payload.get("execution"),
            "exitCode": return_code,
            "returnCode": return_code,
            "timedOut": timed_out,
            "blocked": bool(execution_result.get("blocked", False)) or tool_call.get("status") in {"denied", "approval_required"},
            "reason": execution_result.get("reason") or payload.get("decisionReason"),
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
                "operation": "devops_agent_command",
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
        result["outputRef"] = artifact_ids[0] if artifact_ids else output_artifact["id"]
        result["outputRefs"] = artifact_ids
        return result, artifact_ids

    def _execute_build_commands(
        self,
        *,
        project_id: str,
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        build_scripts: list[str],
        package_info: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        package_scripts = ((package_info or {}).get("scripts") or {}) if package_info else {}
        broker = ToolBroker(self.connection, artifact_root=self.root)
        results: list[dict[str, Any]] = []
        artifact_ids: list[str] = []
        for index, script in enumerate(build_scripts):
            label = f"Build script: {script}"
            if script not in package_scripts:
                results.append(
                    {
                        "index": index,
                        "label": label,
                        "command": _corepack_policy_command(script),
                        "argv": _corepack_argv(script),
                        "critical": False,
                        "status": "skipped_with_reason",
                        "exitCode": None,
                        "reason": f"Requested build script is missing from package.json: {script}",
                        "artifactHashes": {
                            "stdoutHash": _hash_text(""),
                            "stderrHash": _hash_text(""),
                            "outputArtifactHash": _hash_text(f"missing:{script}"),
                        },
                    }
                )
                continue
            argv = _corepack_argv(script)
            tool_result = broker.evaluate_tool_call(
                project_id=project_id,
                agent_run_id=agent_run["id"],
                agent_profile=profile,
                job_id=job["id"],
                tool_call={
                    "tool": "shell",
                    "command": _corepack_policy_command(script),
                    "argv": argv,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "path": workspace["path"],
                    "operation": "devops_agent_command",
                    "execute": True,
                    "timeoutSeconds": 120,
                },
            )
            result, command_artifacts = self._result_from_tool_call(
                project_id=project_id,
                index=index,
                label=label,
                argv=argv,
                critical=True,
                tool_call=tool_result["toolCall"],
            )
            results.append(result)
            artifact_ids.extend(command_artifacts)
        return results, artifact_ids

    def _docker_health(
        self,
        *,
        project_id: str,
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        requested: bool,
    ) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
        docker = DockerSandbox().status()
        health = {"status": "not_requested", "reason": "Docker healthcheck was not requested."}
        if not requested:
            return {**docker, "healthcheck": health}, [], []
        if not docker.get("available"):
            health = {"status": "skipped_with_reason", "reason": "Docker executable is not available; Docker is optional."}
            return {**docker, "healthcheck": health}, [], []
        try:
            sandbox_profile = self.security.get_sandbox_profile("default_docker")
        except KeyError:
            sandbox_profile = None
        if not sandbox_profile or sandbox_profile["status"] != "active":
            health = {"status": "skipped_with_reason", "reason": "Default Docker sandbox profile is not active."}
            return {**docker, "healthcheck": health}, [], []
        allowed_images = sandbox_profile.get("allowedImages") or []
        image = next((item for item in allowed_images if item == "python:3.13-slim"), None)
        if not image:
            health = {"status": "skipped_with_reason", "reason": "Default Docker profile does not allow the healthcheck image."}
            return {**docker, "healthcheck": health}, [], []
        broker = ToolBroker(self.connection, artifact_root=self.root)
        tool_result = broker.evaluate_tool_call(
            project_id=project_id,
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": "shell",
                "command": "python --version",
                "argv": ["python", "--version"],
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "devops_agent_command",
                "execute": True,
                "sandbox": "docker",
                "dockerImage": image,
                "sandboxProfileId": "default_docker",
                "network": "none",
                "timeoutSeconds": 60,
            },
        )
        result, artifact_ids = self._result_from_tool_call(
            project_id=project_id,
            index=0,
            label="Docker healthcheck",
            argv=["python", "--version"],
            critical=False,
            tool_call=tool_result["toolCall"],
        )
        health = {
            "status": result["status"],
            "reason": result.get("reason") or "Docker healthcheck executed through the broker.",
            "toolCallId": result.get("toolCallId"),
            "exitCode": result.get("exitCode"),
        }
        return {**docker, "healthcheck": health}, artifact_ids, [result]

    def _write_config_artifact(self, *, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps(redact_secrets(payload))
        artifact_file = write_text_artifact(root=self.root, artifact_id=artifact_id, suffix=".devops.json", content=content)
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="devops_report",
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={
                "name": "devops-agent-report.json",
                "source": DEVOPS_AGENT_ID,
                "mimeType": "application/json",
                "sizeBytes": artifact_file["sizeBytes"],
                "hashAlgorithm": "sha256",
            },
        )

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload["projectId"])
        workspace = self._workspace(project_id=project_id, workspace_id=str(payload["workspaceId"]))
        task_id = str(payload.get("taskId") or "devops_agent")
        profile = self._ensure_profile()
        job_result = self.jobs.create_job(
            project_id=project_id,
            kind="agent.devops",
            status="running",
            payload={"workspaceId": workspace["id"], "taskId": task_id},
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
        versions: dict[str, Any] = {}
        files_scanned, package_info = self._scan_workspace(workspace["path"], findings, versions)
        build_scripts = self._requested_build_scripts(payload, package_info)
        commands, command_artifacts = self._execute_build_commands(
            project_id=project_id,
            workspace=workspace,
            agent_run=agent_run,
            job=job,
            profile=profile,
            build_scripts=build_scripts,
            package_info=package_info,
        )
        docker, docker_artifacts, docker_commands = self._docker_health(
            project_id=project_id,
            workspace=workspace,
            agent_run=agent_run,
            job=job,
            profile=profile,
            requested=bool(payload.get("dockerHealthcheck", False)),
        )
        commands.extend(docker_commands)
        artifact_ids = [*command_artifacts, *docker_artifacts]
        status, reason = _status_from_findings_and_commands(findings, commands)
        report_payload = {
            "status": status,
            "verdict": status,
            "reason": reason,
            "commands": commands,
            "versions": versions,
            "configFindings": findings,
            "filesScanned": files_scanned,
            "docker": docker,
        }
        config_artifact = self._write_config_artifact(project_id=project_id, payload=report_payload)
        artifact_ids.append(config_artifact["id"])
        qa_verdict = {
            "passed": "devops_passed",
            "risk": "devops_risk",
            "failed": "devops_failed",
            "blocked": "devops_blocked",
        }[status]
        artifact_records = artifact_records_from_ids(self.evidence, artifact_ids)
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) == agent_run["id"]
        ]
        permission_decision_ids = {
            str((tool_call.get("payload") or {}).get("permissionDecisionId"))
            for tool_call in tool_calls
            if (tool_call.get("payload") or {}).get("permissionDecisionId")
        }
        policy_decisions = [
            decision
            for decision in self.security.list_decisions(project_id=project_id)
            if not permission_decision_ids or decision["id"] in permission_decision_ids
        ]
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=None,
            agent_id=DEVOPS_AGENT_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id=f"{DEVOPS_AGENT_ID}.deterministic_checks",
            task_id=task_id,
            test_plan="Run deterministic DevOpsAgent checks over local config files and brokered build/Docker commands.",
            acceptance_checklist=[
                "PowerShell scripts scanned with hashes.",
                "Docker files scanned when present.",
                "package.json and pyproject.toml parsed when present.",
                "Build commands execute through ToolBroker when configured.",
                "Docker remains optional and missing Docker is recorded as skipped_with_reason.",
                "Evidence contains command outputs, versions, config findings, and artifact hashes.",
            ],
            test_results=[
                *commands,
                {
                    "command": "devops_agent.config_scan",
                    "status": status,
                    "findings": len(findings),
                    "filesScanned": len(files_scanned),
                    "outputRef": config_artifact["id"],
                    "metadata": {"versions": versions, "docker": docker},
                },
            ],
            logs=[redact_secrets({"source": DEVOPS_AGENT_ID, "reason": reason})],
            risk_notes=[
                {
                    "severity": "low" if status == "passed" else "high" if status in {"failed", "blocked"} else "medium",
                    "description": reason,
                    "mitigation": "Fix DevOpsAgent config findings or missing/failing build validation commands.",
                }
            ],
            artifact_ids=artifact_ids,
            diff_summary={"configArtifactId": config_artifact["id"], "commandArtifactIds": command_artifacts, "dockerArtifactIds": docker_artifacts},
            runtime_health={
                "id": f"{DEVOPS_AGENT_ID}.deterministic_checks",
                "status": status,
                "available": True,
                "executable": True,
                "commands": len(commands),
                "filesScanned": len(files_scanned),
                "dockerAvailable": bool(docker.get("available")),
                "dockerHealthcheck": docker.get("healthcheck"),
            },
            model_calls=[],
            tool_calls=tool_calls,
            policy_decisions=policy_decisions,
            approvals=self.jobs.list_action_requests(job["id"]),
            artifacts=[artifact_ref(artifact) for artifact in artifact_records],
            hashes=artifact_hashes(artifact_records),
            qa_verdict=qa_verdict,
        )
        for artifact_id in sorted(set(artifact_ids)):
            self.evidence.attach_artifact_to_evidence(artifact_id=artifact_id, evidence_package_id=evidence["id"])
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=status == "passed",
            require_workflow_run=False,
        )
        if status == "passed" and contract_errors:
            status = "blocked"
            qa_verdict = "devops_blocked"
            reason = "Evidence package contract is incomplete or unverifiable: " + " ".join(contract_errors)
            report_payload["status"] = status
            report_payload["verdict"] = status
            report_payload["reason"] = reason
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_verdict,
                risk_notes=[
                    {
                        "severity": "high",
                        "description": reason,
                        "mitigation": "Regenerate DevOpsAgent evidence with artifact refs and SHA-256 hashes before completion.",
                    }
                ],
            )
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status="completed" if status == "passed" else "failed",
            output_payload={**report_payload, "configArtifactId": config_artifact["id"], "evidence_refs": [evidence["id"], config_artifact["id"]]},
        )
        job = self.jobs.update_job_status(
            job["id"],
            status="completed" if status == "passed" else "failed",
            metadata={"status": status, "reason": reason, "evidencePackageId": evidence["id"], "configArtifactId": config_artifact["id"]},
        )
        return {
            "status": status,
            "verdict": status,
            "reason": reason,
            "contract": devops_agent_contract(),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "commands": commands,
            "versions": versions,
            "configFindings": findings,
            "filesScanned": files_scanned,
            "docker": docker,
            "configArtifact": config_artifact,
        }
