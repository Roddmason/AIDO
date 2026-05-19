from __future__ import annotations

from typing import Any, Protocol


RUNTIME_ADAPTER_TOOLS = {"mcp", "openhands", "swe_agent"}


class RuntimeExecutionAdapter(Protocol):
    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        """Execute a broker-approved runtime tool call."""


class UnavailableRuntimeAdapter:
    def __init__(self, adapter_id: str, reason: str):
        self.adapter_id = adapter_id
        self.reason = reason

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        return {
            "executed": False,
            "blocked": True,
            "adapter": self.adapter_id,
            "reason": self.reason,
        }
