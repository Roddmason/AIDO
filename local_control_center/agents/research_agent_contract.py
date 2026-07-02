"""Contrato y readiness del ResearchAgent: política de fuentes y evidencia de research.

Declara el contrato estable del agente de investigación técnica. El agente no depende de un
runtime de modelo: normaliza fuentes verificables, aplica la jerarquía de confianza, exige citas
para conclusiones basadas en web, detecta conflictos y persiste cada fuente como artefacto con hash.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.research.source_policy import TRUST_LEVELS

RESEARCH_AGENT_ID = "research_agent"
RESEARCH_AGENT_ALLOWED_TOOLS: list[str] = ["web_search"]
RESEARCH_AGENT_STATES = {"research_required", "research_running", "research_ready", "research_blocked"}


def research_agent_contract() -> dict[str, Any]:
    """Describe el contrato del ResearchAgent y su política de fuentes."""
    return {
        "id": RESEARCH_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "query": {"type": "string"},
                "maxSources": {"type": "integer", "minimum": 1, "maximum": 50},
                "sources": {"type": "array", "items": {"type": "object"}},
                "conclusions": {"type": "array", "items": {"type": "object"}},
                "claims": {"type": "array", "items": {"type": "object"}},
                "technicalDecisions": {"type": "array", "items": {"type": "object"}},
                "metadata": {"type": "object"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "status",
                "verdict",
                "sources",
                "recommendation",
                "citationCheck",
                "conflictFindings",
                "reportArtifact",
                "evidencePackage",
            ],
            "properties": {
                "status": {"type": "string", "enum": sorted(RESEARCH_AGENT_STATES)},
                "verdict": {"type": "string", "enum": sorted(RESEARCH_AGENT_STATES)},
                "sources": {"type": "array", "items": {"type": "object"}},
                "conclusions": {"type": "array", "items": {"type": "object"}},
                "technicalDecisions": {"type": "array", "items": {"type": "object"}},
                "recommendation": {"type": "object"},
                "citationCheck": {"type": "object"},
                "conflictFindings": {"type": "array", "items": {"type": "object"}},
                "discrepancies": {"type": "array", "items": {"type": "object"}},
                "reportArtifact": {"type": "object"},
                "evidencePackage": {"type": "object"},
            },
        },
        "allowedTools": RESEARCH_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": [
            "source_policy_enforcement",
            "source_provenance_persistence",
            "technical_citation_validation",
            "conflict_detection",
            "policy_gated_web_search",
            "downloaded_code_execution_denied",
        ],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "source_policy_citations_conflict_findings_and_artifact_hashes",
        "sourcePolicy": {
            "trustLevels": list(TRUST_LEVELS),
            "untrustedBehavior": "persist_for_audit_but_not_valid_for_web_conclusion_citations",
            "conflictBehavior": "recommend_highest_trust_source_or_require_human_review_on_top_tier_disagreement",
            "downloadedCodeExecution": "denied",
        },
    }


def research_agent_status() -> dict[str, Any]:
    """Reporta el readiness del ResearchAgent."""
    return {
        "id": RESEARCH_AGENT_ID,
        "executable": True,
        "status": "research_required",
        "reason": (
            "ResearchAgent deterministic source policy, citation validation and artifact persistence are "
            "available without a model runtime."
        ),
        "contract": research_agent_contract(),
    }
