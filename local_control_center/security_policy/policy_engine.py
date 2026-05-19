from __future__ import annotations

import re
from typing import Any


CRITICAL_PATTERNS = [
    re.compile(r"\bgit\s+push\b.*\b--force\b", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b", re.I),
]


def evaluate_action(input_payload: dict[str, Any]) -> dict[str, Any]:
    command = str(input_payload.get("command") or "")
    git_operation = str(input_payload.get("gitOperation") or input_payload.get("git_operation") or "")
    deployment_target = str(input_payload.get("deploymentTarget") or input_payload.get("deployment_target") or "")
    tool = str(input_payload.get("tool") or "")

    if deployment_target.lower() == "prod":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Production deployment requires explicit human approval.",
        }
    if git_operation in {"force_push", "push_main"} or any(pattern.search(command) for pattern in CRITICAL_PATTERNS):
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Dangerous git or destructive shell action requires human review.",
        }
    if tool == "shell" and command:
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "Shell execution requires granular approval until command policy is richer.",
        }
    return {
        "decision": "allow",
        "riskLevel": "low",
        "reason": "Read-only or low-risk action allowed by internal policy.",
    }
