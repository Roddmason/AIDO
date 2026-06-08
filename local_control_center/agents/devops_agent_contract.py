from __future__ import annotations

from typing import Any


DEVOPS_AGENT_ID = "devops_agent"
DEVOPS_AGENT_ALLOWED_TOOLS = ["shell"]
DEVOPS_AGENT_VERDICTS = {"passed", "risk", "failed", "blocked"}


def devops_agent_contract() -> dict[str, Any]:
    return {
        "id": DEVOPS_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "buildScripts": {"type": "array", "items": {"type": "string"}},
                "dockerHealthcheck": {"type": "boolean"},
                "metadata": {"type": "object"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["status", "verdict", "commands", "configFindings", "configArtifact", "evidencePackage"],
            "properties": {
                "status": {"type": "string", "enum": sorted(DEVOPS_AGENT_VERDICTS)},
                "verdict": {"type": "string", "enum": sorted(DEVOPS_AGENT_VERDICTS)},
                "commands": {"type": "array", "items": {"type": "object"}},
                "versions": {"type": "object"},
                "configFindings": {"type": "array", "items": {"type": "object"}},
                "filesScanned": {"type": "array", "items": {"type": "object"}},
                "docker": {"type": "object"},
                "configArtifact": {"type": "object"},
                "evidencePackage": {"type": "object"},
            },
        },
        "allowedTools": DEVOPS_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": ["deterministic_config_checks", "brokered_command_execution"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "files_commands_and_artifacts_only",
    }


def devops_agent_status() -> dict[str, Any]:
    return {
        "id": DEVOPS_AGENT_ID,
        "executable": True,
        "status": "executable",
        "reason": "DevOpsAgent deterministic file checks are executable; Docker health is optional.",
        "contract": devops_agent_contract(),
    }
