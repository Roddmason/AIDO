"""Security chokepoint: every agent tool call is evaluated, gated, and audited here.

The ToolBroker is the single path through which agents act. Invariants it guarantees:
no execution happens without a `policy_engine` allow decision (or a validated approval
grant consumed exactly once); executable calls must carry a structured argv, a matching
allocated workspace, and a path inside that workspace boundary; the tool must be in the
agent profile's allowlist; Docker runs are confined to allowed images/networks of an
active sandbox profile. Every decision and tool call is persisted for audit and its
execution result is redacted before storage. A denied/unsupported call is recorded but
never executed. This module does not itself raise on policy violations — it returns a
deny decision and a `denied`/`approval_required` status; downstream sandboxes raise.
"""

from __future__ import annotations

import shlex
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import promote_execution_result_outputs
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.security_policy.sandbox import DockerSandbox, RestrictedSubprocessSandbox
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.telemetry import record_tool_call

from .repository import AgentsRepository
from .runtime_adapters import (
    RUNTIME_ADAPTER_TOOLS,
    RuntimeAdapterBrokerAdapter,
    RuntimeExecutionAdapter,
    WorkspacePatchBrokerAdapter,
)


def _valid_argv(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _structured_argv(tool_call: dict[str, Any], command: str, *, executable: bool = False) -> list[str]:
    argv = tool_call.get("argv")
    if _valid_argv(argv):
        return [str(item) for item in argv]
    if executable:
        return []
    command = command.strip()
    if not command:
        return []
    try:
        return [str(item) for item in shlex.split(command)]
    except ValueError:
        return [command]


def _executable_argv_boundary(
    tool_call: dict[str, Any], *, tool_name: str, command: str
) -> dict[str, Any] | None:
    if tool_call.get("execute") is not True:
        return None
    if _valid_argv(tool_call.get("argv")):
        return None
    if tool_name != "shell" and not command.strip():
        return None
    return {
        "decision": "deny",
        "riskLevel": "high",
        "reason": "Executable agent tool calls require a non-empty structured argv list; command strings are audit/display only.",
        "categories": ["structured_argv_required"],
    }


def _normalized_operation(tool_name: str, tool_call: dict[str, Any]) -> str | None:
    operation = tool_call.get("operation")
    if operation:
        return str(operation)
    request = tool_call.get("request")
    if tool_name == "mcp" and isinstance(request, dict) and request.get("method"):
        return str(request["method"])
    return None


def default_runtime_adapters(
    connection: sqlite3.Connection,
    *,
    artifact_root: str | Path | None = None,
) -> dict[str, RuntimeExecutionAdapter]:
    """Build the default runtime-adapter map (MCP, OpenHands, SWE-agent, model, patch) by tool name."""
    from local_control_center.integrations.mcp_gateway import McpBrokerAdapter

    from .openhands_adapter import OpenHandsBrokerAdapter
    from .swe_agent_adapter import SweAgentBrokerAdapter

    return {
        "mcp": McpBrokerAdapter(connection),
        "openhands": OpenHandsBrokerAdapter(),
        "swe_agent": SweAgentBrokerAdapter(),
        "ollama": RuntimeAdapterBrokerAdapter(
            adapter_id="ollama", connection=connection, artifact_root=artifact_root
        ),
        "openai_compatible": RuntimeAdapterBrokerAdapter(
            adapter_id="openai_compatible",
            connection=connection,
            artifact_root=artifact_root,
        ),
        "workspace_patch": WorkspacePatchBrokerAdapter(connection=connection, artifact_root=artifact_root),
    }


class ToolBroker:
    """Evaluates, gates, executes, and audits agent tool calls under security policy."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        artifact_root: str | Path | None = None,
        runtime_adapters: dict[str, RuntimeExecutionAdapter] | None = None,
    ):
        self.connection = connection
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None
        self.agents = AgentsRepository(connection)
        self.policies = SecurityPolicyRepository(connection)
        self.jobs = JobsRepository(connection)
        self.sandbox = RestrictedSubprocessSandbox()
        self.docker_sandbox = DockerSandbox()
        self.runtime_adapters = (
            runtime_adapters
            if runtime_adapters is not None
            else default_runtime_adapters(connection, artifact_root=self.artifact_root)
        )

    def _execution_workspace_boundary(
        self, tool_call: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        if tool_call.get("execute") is not True:
            return None, None
        workspace_id = str(tool_call.get("workspaceId") or "").strip()
        if not workspace_id:
            return (
                {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Executable agent tool calls require an allocated workspaceId.",
                    "categories": ["workspace_required"],
                },
                None,
            )
        row = self.connection.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not row:
            return (
                {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Executable agent tool call references an unknown workspace.",
                    "categories": ["workspace_unknown"],
                },
                None,
            )
        if row["status"] == "archived":
            return (
                {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Executable agent tool call references an archived workspace.",
                    "categories": ["workspace_archived"],
                },
                str(row["path"]),
            )
        registered_path = Path(row["path"]).resolve(strict=False)
        requested_workspace = str(tool_call.get("workspacePath") or "").strip()
        if requested_workspace and Path(requested_workspace).resolve(strict=False) != registered_path:
            return (
                {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Requested workspacePath does not match the allocated workspace.",
                    "categories": ["workspace_path_mismatch"],
                },
                str(registered_path),
            )
        requested_path = str(tool_call.get("path") or "").strip()
        if requested_path:
            try:
                Path(requested_path).resolve(strict=False).relative_to(registered_path)
            except (OSError, ValueError):
                return (
                    {
                        "decision": "deny",
                        "riskLevel": "high",
                        "reason": "Executable agent tool call path is outside the allocated workspace.",
                        "categories": ["path_outside_workspace_boundary"],
                    },
                    str(registered_path),
                )
        return None, str(registered_path)

    def _profile_allows_tool(self, agent_profile: dict[str, Any], tool_name: str) -> bool:
        allowed_tools = agent_profile.get("allowedTools") or []
        return "*" in allowed_tools or tool_name in allowed_tools

    def evaluate_tool_call(
        self,
        *,
        project_id: str,
        agent_run_id: str,
        agent_profile: dict[str, Any],
        tool_call: dict[str, Any],
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Gate one tool call end to end: decide, persist the decision, then execute if allowed.

        Enforces (in order) the executable-argv boundary, the workspace/path boundary, the
        agent-profile allowlist, and the policy engine; consumes an approval grant when the
        decision requires one. Only an `allow` decision reaches a sandbox/runtime adapter;
        the execution result is redacted and promoted to evidence artifacts. Returns the
        recorded decision, any created action request, and the persisted tool-call record.
        """
        tool_name = str(tool_call.get("tool") or tool_call.get("toolName") or "")
        command = str(tool_call.get("command") or "")
        operation = _normalized_operation(tool_name, tool_call)
        normalized_tool_call = {**tool_call, "operation": operation} if operation else tool_call
        if not command and tool_name in RUNTIME_ADAPTER_TOOLS and isinstance(tool_call.get("argv"), list):
            command = " ".join(str(item) for item in tool_call["argv"])
        command_argv = _structured_argv(tool_call, command, executable=tool_call.get("execute") is True)
        path = str(tool_call.get("path") or "") or None
        argv_result = _executable_argv_boundary(tool_call, tool_name=tool_name, command=command)
        boundary_result, registered_workspace_path = self._execution_workspace_boundary(tool_call)
        workspace_path = registered_workspace_path or tool_call.get("workspacePath") or path
        runtime_id = str(tool_call.get("runtimeId") or tool_call.get("sandbox") or "").strip() or None
        policy_input = {
            "projectId": project_id,
            "workspaceId": tool_call.get("workspaceId"),
            "agentId": agent_profile["id"],
            "role": agent_profile.get("role"),
            "permissionProfile": agent_profile.get("permissionProfile"),
            "tool": tool_name,
            "command": command,
            "commandArgv": command_argv,
            "path": path,
            "workspacePath": workspace_path,
            "gitOperation": tool_call.get("gitOperation"),
            "deploymentTarget": tool_call.get("deploymentTarget"),
            "networkRequired": tool_call.get("networkRequired"),
            "secretsRequired": tool_call.get("secretsRequired"),
            "operation": operation,
            "runtimeId": runtime_id,
            "workflowKind": tool_call.get("workflowKind"),
            "jobId": job_id or tool_call.get("jobId"),
            "agentRunId": agent_run_id,
            "capability": tool_call.get("capability"),
        }
        if argv_result is not None:
            result = argv_result
        elif boundary_result is not None:
            result = boundary_result
        elif not self._profile_allows_tool(agent_profile, tool_name):
            result = {
                "decision": "deny",
                "riskLevel": "medium",
                "reason": f"Tool '{tool_name}' is not allowed by the agent profile.",
                "categories": ["agent_profile_tool_denied"],
            }
        else:
            result = evaluate_action(policy_input)
        approval_grant_id = str(tool_call.get("approvalGrantId") or "")
        grant_validation: dict[str, Any] | None = None
        if result["decision"] in {"requires_approval", "requires_human"} and approval_grant_id:
            grant_command = command or tool_name
            grant_validation = self.policies.validate_and_consume_grant(
                grant_id=approval_grant_id,
                project_id=project_id,
                job_id=job_id,
                agent_id=agent_profile["id"],
                tool=tool_name,
                command=grant_command,
                command_argv=command_argv,
                workspace_id=tool_call.get("workspaceId"),
                runtime_id=runtime_id,
                path=path,
                agent_run_id=agent_run_id,
            )
            if grant_validation["valid"]:
                result = {
                    **result,
                    "decision": "allow",
                    "reason": f"Approved by permission grant {approval_grant_id}.",
                    "categories": [*result.get("categories", []), "approval_granted"],
                }
            else:
                result = {
                    **result,
                    "decision": "deny",
                    "reason": f"Approval grant invalid: {grant_validation['reason']}",
                    "categories": [*result.get("categories", []), "approval_grant_invalid"],
                }
        decision = self.policies.record_decision(
            project_id=project_id,
            workspace_id=policy_input.get("workspaceId"),
            agent_id=agent_profile["id"],
            role=agent_profile.get("role"),
            tool=tool_name,
            command=command,
            path=path,
            decision=result["decision"],
            risk_level=result["riskLevel"],
            reason=result["reason"],
            payload={
                **policy_input,
                "categories": result.get("categories", []),
                "permissionGrantId": approval_grant_id or None,
                "grantValidation": grant_validation,
            },
        )
        action_request = None
        status = "allowed"
        if decision["decision"] in {"requires_approval", "requires_human"}:
            status = "approval_required"
            if job_id:
                action_request = self.jobs.create_action_request(
                    job_id=job_id,
                    project_id=project_id,
                    action_type="tool.call",
                    risk_level=decision["riskLevel"],
                    command=command or tool_name,
                    command_argv=command_argv,
                    payload={
                        "agentRunId": agent_run_id,
                        "agentId": agent_profile["id"],
                        "tool": tool_name,
                        "path": path,
                        "commandArgv": command_argv,
                        "workspaceId": tool_call.get("workspaceId"),
                        "workspacePath": policy_input.get("workspacePath"),
                        "workspace": {
                            "id": tool_call.get("workspaceId"),
                            "path": policy_input.get("workspacePath"),
                        },
                        "runtimeId": runtime_id,
                        "runtime": {
                            "id": runtime_id,
                            "tool": tool_name,
                            "sandbox": tool_call.get("sandbox"),
                            "dockerImage": tool_call.get("dockerImage"),
                            "execute": tool_call.get("execute") is True,
                        },
                        "evidenceRefs": tool_call.get("evidenceRefs")
                        if isinstance(tool_call.get("evidenceRefs"), list)
                        else [],
                        "diffRefs": tool_call.get("diffRefs")
                        if isinstance(tool_call.get("diffRefs"), list)
                        else [],
                        "permissionDecisionId": decision["id"],
                        "categories": result.get("categories", []),
                    },
                    reason=decision["reason"],
                )
        elif decision["decision"] == "deny":
            status = "denied"

        execution = "not_executed"
        execution_result = None
        if decision["decision"] == "allow" and tool_name == "shell" and tool_call.get("execute") is True:
            requested_sandbox = str(tool_call.get("sandbox") or "restricted_subprocess")
            if requested_sandbox == "docker":
                execution = "docker"
                image = str(tool_call.get("dockerImage") or "")
                profile_id = str(tool_call.get("sandboxProfileId") or "default_docker")
                try:
                    sandbox_profile = self.policies.get_sandbox_profile(profile_id)
                except KeyError:
                    sandbox_profile = None
                network = str(
                    tool_call.get("network") or (sandbox_profile or {}).get("defaultNetwork") or "none"
                )
                allowed_images = (sandbox_profile or {}).get("allowedImages") or []
                allowed_networks = (sandbox_profile or {}).get("allowedNetworks") or ["none"]
                if not sandbox_profile or sandbox_profile["status"] != "active":
                    execution_result = {
                        "executed": False,
                        "blocked": True,
                        "reason": "Docker sandbox profile is not available or inactive.",
                    }
                elif image not in allowed_images:
                    execution_result = {
                        "executed": False,
                        "blocked": True,
                        "reason": "Docker image is not allowed by the sandbox policy.",
                    }
                elif network not in allowed_networks:
                    execution_result = {
                        "executed": False,
                        "blocked": True,
                        "reason": "Docker network mode is not allowed by the sandbox policy.",
                    }
                else:
                    argv = tool_call.get("argv")
                    if not isinstance(argv, list) or not all(isinstance(item, str) and item for item in argv):
                        execution_result = {
                            "executed": False,
                            "blocked": True,
                            "reason": "Docker sandbox requires a structured argv list.",
                        }
                    else:
                        execution_result = self.docker_sandbox.execute(
                            image=image,
                            argv=argv,
                            workspace_path=policy_input.get("workspacePath") or path or ".",
                            network=network,
                            memory=str(sandbox_profile["memory"]),
                            cpus=str(sandbox_profile["cpus"]),
                            timeout_seconds=int(sandbox_profile["timeoutSeconds"]),
                        )
            else:
                execution = "restricted_subprocess"
                execution_result = self.sandbox.execute(
                    argv=tool_call.get("argv"),
                    cwd=path or policy_input.get("workspacePath"),
                    workspace_path=policy_input.get("workspacePath") or path,
                    timeout_seconds=int(tool_call.get("timeoutSeconds") or 30),
                )
            if execution_result.get("blocked"):
                status = "denied"
            elif execution_result.get("returnCode") == 0:
                status = "completed"
            else:
                status = "failed"
        elif (
            decision["decision"] == "allow"
            and tool_name in RUNTIME_ADAPTER_TOOLS
            and tool_call.get("execute") is True
        ):
            execution = f"runtime_adapter:{tool_name}"
            adapter = self.runtime_adapters.get(tool_name)
            if adapter is None:
                execution_result = {
                    "executed": False,
                    "blocked": True,
                    "reason": f"Runtime adapter '{tool_name}' is not configured.",
                }
                status = "failed"
            else:
                execution_result = adapter.execute(
                    tool_call={**normalized_tool_call, "agentRunId": agent_run_id, "jobId": job_id},
                    policy_input=policy_input,
                )
                adapter_status = str(execution_result.get("status") or "")
                if adapter_status in {
                    "completed",
                    "failed",
                    "blocked",
                    "configuration_required",
                    "unavailable",
                }:
                    status = adapter_status
                elif execution_result.get("blocked"):
                    status = "failed"
                elif execution_result.get("returnCode") == 0:
                    status = "completed"
                else:
                    status = "failed"

        payload = {
            "command": command,
            "path": path,
            "execution": execution,
            "permissionDecisionId": decision["id"],
            "actionRequestId": action_request["id"] if action_request else None,
            "permissionGrantId": approval_grant_id or None,
            "grantValidation": grant_validation,
            "sandboxProfileId": tool_call.get("sandboxProfileId")
            or ("default_docker" if execution == "docker" else None),
            "decision": decision["decision"],
            "decisionReason": decision["reason"],
            "riskLevel": decision["riskLevel"],
        }
        if execution_result is not None:
            persisted_result = redact_secrets(execution_result)
            if self.artifact_root is not None:
                persisted_result, artifact_specs = promote_execution_result_outputs(
                    root=self.artifact_root,
                    execution_result=persisted_result,
                )
                evidence = EvidenceRepository(self.connection)
                for artifact in artifact_specs:
                    evidence.create_artifact(
                        project_id=project_id,
                        evidence_package_id=None,
                        kind=artifact["kind"],
                        path=artifact["path"],
                        content_hash=artifact["hash"],
                        metadata=artifact["metadata"],
                        artifact_id=artifact["id"],
                    )
            payload["executionResult"] = persisted_result

        tool_record = self.agents.record_agent_tool_call(
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            status=status,
            payload=payload,
        )
        record_tool_call(
            self.connection,
            project_id=project_id,
            agent_run_id=agent_run_id,
            tool_call=tool_record,
            decision=decision,
        )
        return {"decision": decision, "actionRequest": action_request, "toolCall": tool_record}

    def evaluate_tool_calls(
        self,
        *,
        project_id: str,
        agent_run_id: str,
        agent_profile: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate a batch of tool calls in order, returning one result per call."""
        return [
            self.evaluate_tool_call(
                project_id=project_id,
                agent_run_id=agent_run_id,
                agent_profile=agent_profile,
                tool_call=tool_call,
                job_id=job_id,
            )
            for tool_call in tool_calls
        ]
