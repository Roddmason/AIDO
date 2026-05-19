from __future__ import annotations

from pathlib import Path
from typing import Any

from .command_classifier import classify_command


PROFILE_DEFAULTS: dict[str, str] = {
    "product_owner": "plan",
    "technical_lead": "plan",
    "technical_lead_shadow": "plan",
    "implementer": "dev_safe",
    "backend_engineer": "dev_safe",
    "frontend_engineer": "dev_safe",
    "qa_reviewer": "qa",
    "qa_engineer": "qa",
    "security_reviewer": "qa",
    "release_manager": "release",
}


def is_path_inside(path: str | None, root: str | None) -> bool:
    if not path or not root:
        return True
    try:
        candidate = Path(path).resolve(strict=False)
        workspace_root = Path(root).resolve(strict=False)
        candidate.relative_to(workspace_root)
        return True
    except (OSError, ValueError):
        return False


def permission_profile_for(input_payload: dict[str, Any]) -> str:
    explicit = input_payload.get("permissionProfile") or input_payload.get("permission_profile")
    if explicit:
        return str(explicit)
    role = str(input_payload.get("role") or "")
    return PROFILE_DEFAULTS.get(role, "plan")


def allowlisted_shell_categories(profile: str, categories: list[str]) -> list[str]:
    allowed: list[str] = []
    if profile == "dev_safe":
        if "test" in categories:
            allowed.append("allowlisted_test")
        if "build" in categories:
            allowed.append("allowlisted_build")
        if "lint" in categories:
            allowed.append("allowlisted_lint")
        if "interpreter_version" in categories:
            allowed.append("allowlisted_diagnostic")
        if "read_only" in categories:
            allowed.append("allowlisted_read")
    elif profile == "qa":
        if "test" in categories:
            allowed.append("allowlisted_test")
        if "interpreter_version" in categories:
            allowed.append("allowlisted_diagnostic")
        if "read_only" in categories:
            allowed.append("allowlisted_read")
    return allowed


def evaluate_action(input_payload: dict[str, Any]) -> dict[str, Any]:
    command = str(input_payload.get("command") or "")
    git_operation = str(input_payload.get("gitOperation") or input_payload.get("git_operation") or "")
    deployment_target = str(input_payload.get("deploymentTarget") or input_payload.get("deployment_target") or "")
    tool = str(input_payload.get("tool") or "")
    operation = str(input_payload.get("operation") or "")
    permission_profile = permission_profile_for(input_payload)
    classification = classify_command(command)
    categories = list(classification["categories"])
    if not is_path_inside(input_payload.get("path"), input_payload.get("workspacePath")):
        categories.append("path_outside_workspace")
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "Action path is outside the allocated workspace.",
            "categories": categories,
        }

    if deployment_target.lower() == "prod":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Production deployment requires explicit human approval.",
            "categories": categories,
        }
    if git_operation in {"force_push", "push_main"} or classification["riskLevel"] == "critical":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Dangerous git or destructive shell action requires human review.",
            "categories": categories,
        }

    if tool == "shell" and command:
        if permission_profile == "plan":
            categories.append("profile_shell_denied")
            return {
                "decision": "deny",
                "riskLevel": "medium",
                "reason": "Plan profile cannot execute shell commands.",
                "categories": categories,
            }
        if permission_profile == "release":
            categories.append("profile_release_shell_gated")
            return {
                "decision": "requires_approval",
                "riskLevel": "medium",
                "reason": "Release profile shell actions require explicit approval.",
                "categories": categories,
            }
        allowed = allowlisted_shell_categories(permission_profile, categories)
        if allowed and classification["riskLevel"] == "low":
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": f"{permission_profile} profile allows this shell command category.",
                "categories": categories + allowed,
            }
        if permission_profile == "qa":
            categories.append("profile_test_only")
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "Shell command is not in the permission profile allowlist.",
            "categories": categories,
        }

    if tool in {"mcp", "openhands", "swe_agent"} and command:
        if permission_profile == "plan":
            categories.append("profile_runtime_adapter_denied")
            return {
                "decision": "deny",
                "riskLevel": "medium",
                "reason": "Plan profile cannot execute runtime adapter commands.",
                "categories": categories,
            }
        if classification["riskLevel"] == "critical":
            return {
                "decision": "requires_human",
                "riskLevel": "critical",
                "reason": "Runtime adapter command is critical and requires explicit human review.",
                "categories": categories,
            }
        if classification["riskLevel"] != "low":
            return {
                "decision": "requires_approval",
                "riskLevel": "medium",
                "reason": "Runtime adapter command is not in the low-risk allowlist and requires approval.",
                "categories": categories,
            }
    if tool == "mcp" and operation and not command and operation not in {"tools/list", "resources/list", "prompts/list"}:
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "MCP tool execution beyond read-only discovery requires approval.",
            "categories": categories + ["mcp_operation_gated"],
        }

    if classification["riskLevel"] == "low":
        return {
            "decision": "allow",
            "riskLevel": "low",
            "reason": "Low-risk test/build/read-only action allowed by internal policy.",
            "categories": categories,
        }
    return {
        "decision": "allow",
        "riskLevel": "low",
        "reason": "Read-only or low-risk action allowed by internal policy.",
        "categories": categories,
    }
