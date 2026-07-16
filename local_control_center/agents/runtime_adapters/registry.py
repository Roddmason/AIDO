"""Adapter registry and broker bridge that dispatch typed runtime execution requests.

`RuntimeAdapterRegistry` maps adapter ids to their implementations (subprocess, CLI version
probes, and every catalogued provider family); `RuntimeAdapterBrokerAdapter` normalizes a raw
broker tool call into a typed request; `UnavailableRuntimeAdapter` is the fail-closed
placeholder for adapters that cannot run.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.shared.time import utc_now

from ..provider_catalog import PROVIDER_CATALOG
from .common import _result
from .models import RuntimeAdapter, RuntimeExecutionRequest, RuntimeExecutionResult
from .provider_factory import ProviderFactoryAdapter
from .subprocess_adapter import CliVersionAdapter, RestrictedSubprocessAdapter


class RuntimeAdapterRegistry:
    """Maps adapter ids to typed `RuntimeAdapter` implementations and dispatches execution."""

    def __init__(
        self,
        adapters: dict[str, RuntimeAdapter] | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        provider_adapters: dict[str, RuntimeAdapter] = {
            entry.provider_family: ProviderFactoryAdapter(
                provider_family=entry.provider_family,
                display_name=entry.display_name,
                connection=connection,
                artifact_root=artifact_root,
            )
            for entry in PROVIDER_CATALOG
        }
        self.adapters: dict[str, RuntimeAdapter] = adapters or {
            "restricted_subprocess": RestrictedSubprocessAdapter(
                connection=connection, artifact_root=artifact_root
            ),
            "codex": CliVersionAdapter(adapter_id="codex"),
            "codex_cli": CliVersionAdapter(adapter_id="codex_cli"),
            "claude": CliVersionAdapter(adapter_id="claude"),
            "claude_code_cli": CliVersionAdapter(adapter_id="claude_code_cli"),
            "openhands": CliVersionAdapter(adapter_id="openhands"),
            "swe-agent": CliVersionAdapter(adapter_id="swe-agent"),
            "swe_agent": CliVersionAdapter(adapter_id="swe_agent"),
            **provider_adapters,
        }

    def register(self, adapter_id: str, adapter: RuntimeAdapter) -> None:
        """Register or replace the adapter bound to an id."""
        self.adapters[adapter_id] = adapter

    def execute(self, adapter_id: str, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Dispatch a request to the named adapter, returning `unavailable` when unregistered."""
        started_at = utc_now()
        adapter = self.adapters.get(adapter_id)
        if adapter is None:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=f"Runtime adapter is not registered: {adapter_id}",
            )
        return adapter.execute(request)


class RuntimeAdapterBrokerAdapter:
    """Bridges a raw broker tool call to a typed runtime adapter and flattens its result."""

    def __init__(
        self,
        *,
        adapter_id: str,
        connection: sqlite3.Connection,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.adapter_id = adapter_id
        self.registry = RuntimeAdapterRegistry(
            connection=connection, artifact_root=artifact_root, environ=environ
        )

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        """Validate the tool call into a typed request, run it, and return a broker-shaped result."""
        input_payload = dict(tool_call.get("input") or {})
        selected_provider_id = str(policy_input.get("providerId") or "").strip()
        if selected_provider_id:
            input_payload["providerId"] = selected_provider_id
        request = RuntimeExecutionRequest.model_validate(
            {
                "projectId": policy_input["projectId"],
                "workflowRunId": tool_call.get("workflowRunId"),
                "workflowStepId": tool_call.get("workflowStepId"),
                "jobId": tool_call.get("jobId"),
                "agentRunId": tool_call.get("agentRunId"),
                "workspaceId": policy_input.get("workspaceId"),
                "workspacePath": policy_input.get("workspacePath"),
                "capability": tool_call.get("capability")
                or tool_call.get("operation")
                or "runtime_execution",
                "argv": tool_call.get("argv") or [],
                "input": input_payload,
                "timeoutSeconds": tool_call.get("timeoutSeconds") or 30,
                "approvalGrantId": tool_call.get("approvalGrantId"),
                "metadata": tool_call.get("metadata") or {},
            }
        )
        result = self.registry.execute(self.adapter_id, request)
        payload = result.model_dump(by_alias=True)
        return {
            "executed": result.status == "completed",
            "blocked": result.status in {"blocked", "configuration_required", "unavailable"},
            "returnCode": result.exit_code,
            "reason": result.reason,
            "timedOut": result.status == "timed_out",
            **payload,
        }


class UnavailableRuntimeAdapter:
    """Placeholder adapter that always reports a fixed unavailable reason in either call shape."""

    def __init__(self, adapter_id: str, reason: str):
        self.adapter_id = adapter_id
        self.reason = reason

    def execute(
        self,
        request: RuntimeExecutionRequest | None = None,
        *,
        tool_call: dict[str, Any] | None = None,
        policy_input: dict[str, Any] | None = None,
    ) -> RuntimeExecutionResult | dict[str, Any]:
        """Return the configured unavailable reason, shaped for the typed or broker call path."""
        if request is not None:
            return _result(status="unavailable", started_at=utc_now(), reason=self.reason)
        return {
            "executed": False,
            "blocked": True,
            "adapter": self.adapter_id,
            "reason": self.reason,
        }
