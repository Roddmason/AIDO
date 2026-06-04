from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


FORBIDDEN_RUNTIME_ARGS = {
    "--allow-host-write",
    "--dangerously-ignore-permissions",
    "--mount",
    "--network=host",
    "--no-sandbox",
    "--privileged",
    "--volume",
}

RUNTIME_CONTRACTS: dict[str, dict[str, Any]] = {
    "openhands": {
        "id": "openhands",
        "contractVersion": 1,
        "supportedOperations": ["version_check", "issue_to_patch"],
        "executableContains": ["openhands"],
        "requiredIssueToPatchFields": ["issueText"],
        "requiresWorkspacePath": True,
        "forbiddenArgs": sorted(FORBIDDEN_RUNTIME_ARGS),
    },
    "swe_agent": {
        "id": "swe_agent",
        "contractVersion": 1,
        "supportedOperations": ["version_check", "issue_to_patch"],
        "executableContains": ["swe", "agent"],
        "requiredIssueToPatchFields": ["issueText"],
        "requiresWorkspacePath": True,
        "forbiddenArgs": sorted(FORBIDDEN_RUNTIME_ARGS),
    },
}


def get_runtime_contract(adapter_id: str) -> dict[str, Any]:
    if adapter_id not in RUNTIME_CONTRACTS:
        raise KeyError(f"Runtime contract is not registered: {adapter_id}")
    return deepcopy(RUNTIME_CONTRACTS[adapter_id])


def _operation_for(tool_call: dict[str, Any]) -> str:
    operation = str(tool_call.get("operation") or "").strip()
    argv = tool_call.get("argv")
    if not operation and isinstance(argv, list) and any(str(item) == "--version" for item in argv):
        return "version_check"
    return operation or "issue_to_patch"


def _valid_workspace_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        Path(value).resolve(strict=False)
    except OSError:
        return False
    return True


def _forbidden_runtime_arg(argv: list[str], forbidden_args: set[str]) -> str | None:
    for index, item in enumerate(argv):
        if item in forbidden_args:
            return item
        if item == "--network" and index + 1 < len(argv) and argv[index + 1].lower() == "host":
            return "--network host"
    return None


def validate_runtime_tool_call(
    adapter_id: str,
    tool_call: dict[str, Any],
    policy_input: dict[str, Any],
) -> dict[str, Any]:
    contract = get_runtime_contract(adapter_id)
    operation = _operation_for(tool_call)
    if operation not in contract["supportedOperations"]:
        return {"valid": False, "operation": operation, "reason": f"Unsupported runtime operation: {operation}"}

    argv = tool_call.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        return {"valid": False, "operation": operation, "reason": "Runtime execution requires structured argv strings."}

    executable = argv[0].lower()
    if not all(token in executable for token in contract["executableContains"]):
        return {
            "valid": False,
            "operation": operation,
            "reason": f"{adapter_id} contract requires its own CLI executable.",
        }

    forbidden = _forbidden_runtime_arg(argv, set(contract["forbiddenArgs"]))
    if forbidden:
        return {
            "valid": False,
            "operation": operation,
            "reason": f"Runtime argv contains forbidden flag: {forbidden}",
        }

    workspace_path = tool_call.get("workspacePath") or policy_input.get("workspacePath")
    if contract["requiresWorkspacePath"] and not _valid_workspace_path(workspace_path):
        return {"valid": False, "operation": operation, "reason": "Runtime execution requires workspacePath."}

    if operation == "issue_to_patch":
        for field in contract["requiredIssueToPatchFields"]:
            if not str(tool_call.get(field) or "").strip():
                return {"valid": False, "operation": operation, "reason": f"issue_to_patch requires {field}."}

    return {"valid": True, "operation": operation, "contract": contract}
