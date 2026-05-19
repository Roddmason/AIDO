from __future__ import annotations

import importlib.util
import shutil
from typing import Any

from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox


def openhands_status() -> dict[str, Any]:
    cli_available = shutil.which("openhands") is not None
    package_available = importlib.util.find_spec("openhands") is not None
    available = package_available or cli_available
    return {
        "id": "openhands",
        "label": "OpenHands Software Agent SDK",
        "required": False,
        "available": available,
        "mode": "optional_adapter",
        "cliAvailable": cli_available,
        "packageAvailable": package_available,
        "notes": "Adapter is optional and must remain behind policy, workspace isolation, and evidence capture.",
    }


class OpenHandsBrokerAdapter:
    def __init__(self) -> None:
        self.sandbox = RestrictedSubprocessSandbox()

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        status = openhands_status()
        if not status["available"]:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "openhands",
                "reason": "OpenHands adapter is not installed or available on PATH.",
            }
        argv = tool_call.get("argv")
        if not isinstance(argv, list) or not argv:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "openhands",
                "reason": "OpenHands execution requires structured argv.",
            }
        executable = str(argv[0]).lower()
        if "openhands" not in executable:
            return {
                "executed": False,
                "blocked": True,
                "adapter": "openhands",
                "reason": "OpenHands adapter can only execute an openhands CLI command.",
            }
        result = self.sandbox.execute(
            argv=argv,
            cwd=policy_input.get("workspacePath"),
            workspace_path=policy_input.get("workspacePath"),
            timeout_seconds=int(tool_call.get("timeoutSeconds") or 900),
        )
        return {"adapter": "openhands", **result}
