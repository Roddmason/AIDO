from __future__ import annotations

import importlib.util
import shutil
from typing import Any

from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox

from .runtime_contracts import get_runtime_contract, validate_runtime_tool_call


def swe_agent_status() -> dict[str, Any]:
    cli_available = shutil.which("sweagent") is not None or shutil.which("swe-agent") is not None
    package_available = importlib.util.find_spec("sweagent") is not None or importlib.util.find_spec("swe_agent") is not None
    available = package_available or cli_available
    return {
        "id": "swe_agent",
        "label": "SWE-agent",
        "required": False,
        "available": available,
        "mode": "optional_adapter",
        "cliAvailable": cli_available,
        "packageAvailable": package_available,
        "contract": get_runtime_contract("swe_agent"),
        "notes": "Issue-to-patch execution is optional and must run in an isolated workspace.",
    }


class SweAgentBrokerAdapter:
    def __init__(self) -> None:
        self.sandbox = RestrictedSubprocessSandbox()

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        validation = validate_runtime_tool_call("swe_agent", tool_call, policy_input)
        if not validation["valid"]:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "swe_agent",
                "operation": validation["operation"],
                "reason": validation["reason"],
            }
        status = swe_agent_status()
        if not status["available"]:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "swe_agent",
                "operation": validation["operation"],
                "reason": "SWE-agent adapter is not installed or available on PATH.",
            }
        argv = tool_call.get("argv")
        if not isinstance(argv, list) or not argv:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "swe_agent",
                "reason": "SWE-agent execution requires structured argv.",
            }
        executable = str(argv[0]).lower()
        if "swe" not in executable:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "swe_agent",
                "reason": "SWE-agent adapter can only execute a SWE-agent CLI command.",
            }
        result = self.sandbox.execute(
            argv=argv,
            cwd=policy_input.get("workspacePath"),
            workspace_path=policy_input.get("workspacePath"),
            timeout_seconds=int(tool_call.get("timeoutSeconds") or 900),
        )
        return {"adapter": "swe_agent", **result}
