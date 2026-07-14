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

@author Rodrigo Mason
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
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.telemetry import record_tool_call

from .repository import AgentsRepository
from .runtime_adapters import (
    RUNTIME_ADAPTER_TOOLS,
    RuntimeAdapterBrokerAdapter,
    RuntimeExecutionAdapter,
    WorkspacePatchBrokerAdapter,
)
from .runtime_registry import (
    validate_product_owner_codex_environment,
    validate_product_owner_runtime_argv,
)

MODEL_PROVIDER_TOOLS = {
    "ollama",
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
}
PRODUCT_OWNER_INTERNAL_OPERATIONS = {"product_owner_runtime", "product_owner_model_call"}
PRODUCT_OWNER_AGENT_PROFILE_ID = "product_owner_agent"


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


def _product_owner_internal_boundary(
    *,
    operation: str | None,
    trusted_operation: str | None,
    agent_profile_id: str,
    tool_call: dict[str, Any],
    workspace_path: str | None,
    trusted_subprocess_environment: dict[str, str] | None,
) -> dict[str, Any] | None:
    if (
        agent_profile_id == PRODUCT_OWNER_AGENT_PROFILE_ID
        and operation not in PRODUCT_OWNER_INTERNAL_OPERATIONS
    ):
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": (
                "ProductOwnerAgent tools can run only through its trusted internal runtime "
                "operations and persisted AI resource decision."
            ),
            "categories": ["product_owner_generic_tool_call_denied"],
        }
    if operation not in PRODUCT_OWNER_INTERNAL_OPERATIONS:
        return None
    if trusted_operation != operation:
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "ProductOwnerAgent internal runtime operations cannot be supplied by generic tool calls.",
            "categories": ["product_owner_internal_operation_denied"],
        }
    if tool_call.get("providerTransportRequired") is not True:
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "ProductOwnerAgent runtime calls must declare their authenticated provider transport.",
            "categories": ["product_owner_transport_contract_denied"],
        }
    if tool_call.get("networkRequired") is True or tool_call.get("secretsRequired") is True:
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": (
                "ProductOwnerAgent cannot receive independent network or secret capabilities; "
                "provider transport must remain broker-owned."
            ),
            "categories": ["product_owner_capability_boundary_denied"],
        }
    if operation == "product_owner_runtime":
        runtime_id = str(tool_call.get("runtimeId") or "")
        validation_error = validate_product_owner_runtime_argv(
            runtime_id=runtime_id,
            argv=tool_call.get("argv"),
            workspace_path=str(workspace_path or ""),
        )
        if validation_error:
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": validation_error,
                "categories": ["product_owner_runtime_argv_denied"],
            }
        if runtime_id == "codex_cli":
            environment_error = validate_product_owner_codex_environment(
                trusted_subprocess_environment,
                workspace_path=str(workspace_path or ""),
            )
            if environment_error:
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": environment_error,
                    "categories": ["product_owner_runtime_environment_denied"],
                }
        elif trusted_subprocess_environment is not None:
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Subprocess environments are reserved for the isolated ProductOwnerAgent Codex runtime.",
                "categories": ["product_owner_runtime_environment_denied"],
            }
    elif trusted_subprocess_environment is not None:
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Subprocess environments are not accepted by ProductOwnerAgent model calls.",
            "categories": ["product_owner_runtime_environment_denied"],
        }
    return None


def _argv_option(argv: Any, option: str) -> str | None:
    if not isinstance(argv, list):
        return None
    matches = [index for index, item in enumerate(argv) if item == option]
    if len(matches) != 1 or matches[0] + 1 >= len(argv):
        return None
    return str(argv[matches[0] + 1])


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
        "openrouter": RuntimeAdapterBrokerAdapter(
            adapter_id="openrouter",
            connection=connection,
            artifact_root=artifact_root,
        ),
        "nvidia_nim": RuntimeAdapterBrokerAdapter(
            adapter_id="nvidia_nim",
            connection=connection,
            artifact_root=artifact_root,
        ),
        "anthropic_api": RuntimeAdapterBrokerAdapter(
            adapter_id="anthropic_api",
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

    @staticmethod
    def _profile_allows_value(agent_profile: dict[str, Any], field: str, value: str) -> bool:
        allowed = set(agent_profile.get(field) or [])
        return not allowed or "*" in allowed or value in allowed

    def _model_provider_binding(
        self,
        *,
        tool_name: str,
        runtime_id: str | None,
        provider_id: str | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if tool_name not in MODEL_PROVIDER_TOOLS:
            return None, None
        if not runtime_id or not provider_id or runtime_id != provider_id:
            return (
                {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Model adapter runtime and provider ids must match.",
                    "categories": ["model_provider_binding_denied"],
                },
                None,
            )
        row = self.connection.execute(
            """
            SELECT api_format, provider_family, enabled
            FROM provider_accounts
            WHERE provider_id = ?
            """,
            (provider_id,),
        ).fetchone()
        if (
            tool_name == "ollama"
            and row
            and str(row["api_format"] or "") == "ollama"
            and bool(row["enabled"])
        ):
            return None, "ollama"
        if (
            row
            and bool(row["enabled"])
            and str(row["provider_family"] or "") == tool_name
        ):
            return None, str(row["provider_family"])
        return (
            {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Named model runtime is not bound to an enabled provider for this adapter.",
                "categories": ["model_provider_binding_denied"],
            },
            None,
        )

    def _product_owner_resource_decision_boundary(
        self,
        *,
        operation: str | None,
        project_id: str,
        agent_run_id: str,
        agent_profile: dict[str, Any],
        tool_call: dict[str, Any],
        resource_decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if operation not in PRODUCT_OWNER_INTERNAL_OPERATIONS:
            return None

        reason = "ProductOwnerAgent runtime does not match its persisted AI resource decision."
        denied = {
            "decision": "deny",
            "riskLevel": "high",
            "reason": reason,
            "categories": ["product_owner_resource_decision_denied"],
        }
        if resource_decision is None:
            return denied
        decision_id = str(resource_decision.get("routingDecisionId") or "").strip()
        if not decision_id or decision_id != str(tool_call.get("resourceDecisionId") or "").strip():
            return denied
        run_row = self.connection.execute(
            "SELECT project_id, workflow_run_id, metadata FROM agent_runs WHERE id = ?",
            (agent_run_id,),
        ).fetchone()
        selected = resource_decision.get("selected")
        if run_row is None or not isinstance(selected, dict):
            return denied
        run_metadata = json_loads(run_row["metadata"], {})

        runtime_id = str(tool_call.get("runtimeId") or "")
        tool_input = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
        tool_model = (
            _argv_option(tool_call.get("argv"), "--model")
            if operation == "product_owner_runtime"
            else str(tool_input.get("model") or "").strip() or None
        )
        expected_runtime = "cli" if operation == "product_owner_runtime" else str(selected.get("runtime") or "")
        common_identity_matches = all(
            (
                str(run_row["project_id"] or "") == project_id,
                str(run_metadata.get("agentProfileId") or "") == str(agent_profile.get("id") or ""),
                runtime_id == str(selected.get("providerId") or ""),
                str(selected.get("model") or "") == str(tool_model or ""),
                str(selected.get("runtime") or "") == expected_runtime,
                self._profile_allows_value(agent_profile, "allowedProviders", runtime_id),
                self._profile_allows_value(agent_profile, "allowedRuntimes", runtime_id),
            )
        )
        if not common_identity_matches:
            return denied

        routing_row = self.connection.execute(
            "SELECT * FROM ai_routing_decisions WHERE id = ?",
            (decision_id,),
        ).fetchone()
        if routing_row is None:
            return denied
        routing_identity_matches = all(
            (
                str(routing_row["project_id"] or "") == project_id,
                str(routing_row["agent_id"] or "") == str(agent_profile.get("id") or ""),
                str(routing_row["task_id"] or "") == str(run_metadata.get("taskId") or ""),
                str(routing_row["selected_provider"] or "") == runtime_id,
                str(routing_row["selected_model"] or "") == str(tool_model or ""),
                str(routing_row["selected_runtime"] or "") == expected_runtime,
                bool(str(routing_row["workflow_run_id"] or "").strip()),
                str(routing_row["workflow_run_id"] or "")
                == str(run_row["workflow_run_id"] or ""),
                bool(routing_row["approval_required"])
                == bool(resource_decision.get("approvalRequired")),
            )
        )
        if not routing_identity_matches:
            return denied
        if str(routing_row["usage_status"] or "") != "not_executed":
            return {
                **denied,
                "reason": "ProductOwnerAgent AI resource decision was already claimed or consumed.",
                "categories": ["product_owner_resource_decision_replay_denied"],
            }
        if not bool(routing_row["approval_required"]):
            return None

        loop_row = self.connection.execute(
            "SELECT context FROM product_loops WHERE id = ? AND project_id = ?",
            (routing_row["workflow_run_id"], project_id),
        ).fetchone()
        context = json_loads(loop_row["context"], {}) if loop_row else {}
        durable = context.get("durableRun") if isinstance(context, dict) else {}
        request_meta = durable.get("requestMeta") if isinstance(durable, dict) else {}
        approvals = request_meta.get("approvedResourceSelections") if isinstance(request_meta, dict) else []
        approved = any(
            isinstance(item, dict)
            and str(item.get("role") or "") == "product_owner"
            and all(
                str(item.get(key) or "") == str(selected.get(key) or "")
                for key in ("providerId", "model", "runtime")
            )
            for item in approvals or []
        )
        return None if approved else denied

    def _claim_product_owner_resource_decision(
        self,
        *,
        operation: str | None,
        resource_decision: dict[str, Any] | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Atomically claim a ProductOwnerAgent routing decision before external execution.

        Identity and approval checks happen in ``_product_owner_resource_decision_boundary``.
        This compare-and-set is the concurrency boundary: only one worker can move a decision
        from ``not_executed`` to ``executing`` even when multiple runs pass the read checks at
        the same time.
        """
        if operation not in PRODUCT_OWNER_INTERNAL_OPERATIONS or resource_decision is None:
            return None, None
        decision_id = str(resource_decision.get("routingDecisionId") or "").strip()
        if not decision_id:
            return None, {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ProductOwnerAgent AI resource decision is missing its durable id.",
                "categories": ["product_owner_resource_decision_denied"],
            }
        claim = self.connection.execute(
            """
            UPDATE ai_routing_decisions
            SET usage_status = 'executing'
            WHERE id = ? AND usage_status = 'not_executed'
            """,
            (decision_id,),
        )
        if claim.rowcount == 1:
            return decision_id, None
        return None, {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "ProductOwnerAgent AI resource decision was already claimed or consumed.",
            "categories": ["product_owner_resource_decision_replay_denied"],
        }

    def evaluate_tool_call(
        self,
        *,
        project_id: str,
        agent_run_id: str,
        agent_profile: dict[str, Any],
        tool_call: dict[str, Any],
        job_id: str | None = None,
        trusted_operation: str | None = None,
        trusted_resource_decision: dict[str, Any] | None = None,
        trusted_subprocess_environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Gate one tool call end to end: decide, persist the decision, then execute if allowed.

        Enforces (in order) the executable-argv boundary, the workspace/path boundary, the
        agent-profile allowlist, and the policy engine; consumes an approval grant when the
        decision requires one. Only an `allow` decision reaches a sandbox/runtime adapter;
        the execution result is redacted and promoted to evidence artifacts. Returns the
        recorded decision, any created action request, and the persisted tool-call record.
        """
        tool_name = str(tool_call.get("tool") or tool_call.get("toolName") or "")
        capture_stdout_artifact = tool_call.get("captureStdoutArtifact") is True
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
        internal_result = _product_owner_internal_boundary(
            operation=operation,
            trusted_operation=trusted_operation,
            agent_profile_id=str(agent_profile.get("id") or ""),
            tool_call=tool_call,
            workspace_path=str(workspace_path or "") or None,
            trusted_subprocess_environment=trusted_subprocess_environment,
        )
        resource_result = self._product_owner_resource_decision_boundary(
            operation=operation,
            project_id=project_id,
            agent_run_id=agent_run_id,
            agent_profile=agent_profile,
            tool_call=tool_call,
            resource_decision=trusted_resource_decision,
        )
        runtime_id = str(tool_call.get("runtimeId") or tool_call.get("sandbox") or "").strip() or None
        tool_input = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
        provider_id = str(tool_input.get("providerId") or "").strip() or None
        binding_result, provider_family = self._model_provider_binding(
            tool_name=tool_name,
            runtime_id=runtime_id,
            provider_id=provider_id,
        )
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
            "providerTransportRequired": tool_call.get("providerTransportRequired"),
            "workspaceReadRequired": tool_call.get("workspaceReadRequired"),
            "resourceDecisionId": tool_call.get("resourceDecisionId"),
            "operation": operation,
            "runtimeId": runtime_id,
            "providerId": provider_id,
            "providerFamily": provider_family,
            "workflowKind": tool_call.get("workflowKind"),
            "jobId": job_id or tool_call.get("jobId"),
            "agentRunId": agent_run_id,
            "capability": tool_call.get("capability"),
        }
        if argv_result is not None:
            result = argv_result
        elif boundary_result is not None:
            result = boundary_result
        elif internal_result is not None:
            result = internal_result
        elif resource_result is not None:
            result = resource_result
        elif not self._profile_allows_tool(agent_profile, tool_name):
            result = {
                "decision": "deny",
                "riskLevel": "medium",
                "reason": f"Tool '{tool_name}' is not allowed by the agent profile.",
                "categories": ["agent_profile_tool_denied"],
            }
        elif binding_result is not None:
            result = binding_result
        elif tool_name in MODEL_PROVIDER_TOOLS and not self._profile_allows_value(
            agent_profile, "allowedProviders", provider_id or ""
        ):
            result = {
                "decision": "deny",
                "riskLevel": "high",
                "reason": f"Provider '{provider_id}' is not allowed by the agent profile.",
                "categories": ["agent_profile_provider_denied"],
            }
        elif tool_name in MODEL_PROVIDER_TOOLS and not self._profile_allows_value(
            agent_profile, "allowedRuntimes", runtime_id or ""
        ):
            result = {
                "decision": "deny",
                "riskLevel": "high",
                "reason": f"Runtime '{runtime_id}' is not allowed by the agent profile.",
                "categories": ["agent_profile_runtime_denied"],
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
        claimed_resource_decision_id: str | None = None
        if result["decision"] == "allow" and operation in PRODUCT_OWNER_INTERNAL_OPERATIONS:
            claimed_resource_decision_id, claim_denial = self._claim_product_owner_resource_decision(
                operation=operation,
                resource_decision=trusted_resource_decision,
            )
            if claim_denial is not None:
                result = claim_denial
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
        authorize_only = tool_call.get("authorizeOnly") is True
        if (
            decision["decision"] == "allow"
            and tool_name == "shell"
            and tool_call.get("execute") is True
            and authorize_only
        ):
            execution = "authorized"
            execution_result = {
                "executed": False,
                "authorized": True,
                "blocked": False,
                "reason": "ToolBroker authorized streaming execution; caller owns live process drainage.",
            }
            status = "allowed"
        elif decision["decision"] == "allow" and tool_name == "shell" and tool_call.get("execute") is True:
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
                    truncate_output=not capture_stdout_artifact,
                    environment=(
                        trusted_subprocess_environment
                        if operation == "product_owner_runtime"
                        else None
                    ),
                )
            if execution_result.get("blocked"):
                status = "denied"
            elif capture_stdout_artifact and execution_result.get("stdoutCaptureTruncated"):
                execution_result["reason"] = "Required stdout exceeded the complete capture limit."
                status = "failed"
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
            "operation": operation,
            "runtimeId": runtime_id,
            "providerId": provider_id,
            "model": tool_input.get("model")
            if operation == "product_owner_model_call"
            else _argv_option(tool_call.get("argv"), "--model"),
            "resourceDecisionId": tool_call.get("resourceDecisionId"),
            "permissionDecisionId": decision["id"],
            "actionRequestId": action_request["id"] if action_request else None,
            "permissionGrantId": approval_grant_id or None,
            "grantValidation": grant_validation,
            "sandboxProfileId": tool_call.get("sandboxProfileId")
            or ("default_docker" if execution == "docker" else None),
            "decision": decision["decision"],
            "decisionReason": decision["reason"],
            "riskLevel": decision["riskLevel"],
            "categories": result.get("categories", []),
        }
        if execution_result is not None:
            persisted_result = redact_secrets(execution_result)
            if self.artifact_root is not None:
                persisted_result, artifact_specs = promote_execution_result_outputs(
                    root=self.artifact_root,
                    execution_result=persisted_result,
                    force_streams={"stdout"} if capture_stdout_artifact else None,
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

        if claimed_resource_decision_id:
            final_usage_status = status if status != "allowed" else "authorized"
            self.connection.execute(
                """
                UPDATE ai_routing_decisions
                SET usage_status = ?
                WHERE id = ? AND usage_status = 'executing'
                """,
                (final_usage_status, claimed_resource_decision_id),
            )
            payload["resourceDecisionUsageStatus"] = final_usage_status

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
