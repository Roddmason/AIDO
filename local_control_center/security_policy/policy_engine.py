from __future__ import annotations

from typing import Any

from .command_classifier import classify_command


def evaluate_action(input_payload: dict[str, Any]) -> dict[str, Any]:
    command = str(input_payload.get("command") or "")
    git_operation = str(input_payload.get("gitOperation") or input_payload.get("git_operation") or "")
    deployment_target = str(input_payload.get("deploymentTarget") or input_payload.get("deployment_target") or "")
    tool = str(input_payload.get("tool") or "")
    classification = classify_command(command)

    if deployment_target.lower() == "prod":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Production deployment requires explicit human approval.",
            "categories": classification["categories"],
        }
    if git_operation in {"force_push", "push_main"} or classification["riskLevel"] == "critical":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Dangerous git or destructive shell action requires human review.",
            "categories": classification["categories"],
        }
    if classification["riskLevel"] == "low":
        return {
            "decision": "allow",
            "riskLevel": "low",
            "reason": "Low-risk test/build/read-only action allowed by internal policy.",
            "categories": classification["categories"],
        }
    if tool == "shell" and command:
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "Shell execution requires granular approval until command policy is richer.",
            "categories": classification["categories"],
        }
    return {
        "decision": "allow",
        "riskLevel": "low",
        "reason": "Read-only or low-risk action allowed by internal policy.",
        "categories": classification["categories"],
    }
