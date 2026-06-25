"""Contrato y readiness del ResearchAgent: política de fuentes y evidencia de research.

Declara el contrato estable del agente de investigación técnica. El agente no depende de un
runtime de modelo: normaliza fuentes verificables, aplica la jerarquía de confianza, exige citas
para conclusiones basadas en web, detecta conflictos y persiste cada fuente como artefacto con hash.
"""

from __future__ import annotations

from typing import Any

from local_control_center.research.source_policy import TRUST_LEVELS

RESEARCH_AGENT_ID = "research_agent"
RESEARCH_AGENT_ALLOWED_TOOLS: list[str] = []
RESEARCH_AGENT_VERDICTS = {"completed", "blocked", "needs_human_review"}


def research_agent_contract() -> dict[str, Any]:
    """Describe el contrato del ResearchAgent y su política de fuentes."""
    return {
        "id": RESEARCH_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId", "sources"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "object"}},
                "conclusions": {"type": "array", "items": {"type": "object"}},
                "claims": {"type": "array", "items": {"type": "object"}},
                "metadata": {"type": "object"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "status",
                "verdict",
                "sources",
                "citationCheck",
                "conflictFindings",
                "reportArtifact",
                "evidencePackage",
            ],
            "properties": {
                "status": {"type": "string", "enum": sorted(RESEARCH_AGENT_VERDICTS)},
                "verdict": {"type": "string", "enum": sorted(RESEARCH_AGENT_VERDICTS)},
                "sources": {"type": "array", "items": {"type": "object"}},
                "conclusions": {"type": "array", "items": {"type": "object"}},
                "citationCheck": {"type": "object"},
                "conflictFindings": {"type": "array", "items": {"type": "object"}},
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
        ],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "source_policy_citations_conflict_findings_and_artifact_hashes",
        "sourcePolicy": {
            "trustLevels": list(TRUST_LEVELS),
            "untrustedBehavior": "persist_for_audit_but_not_valid_for_web_conclusion_citations",
            "conflictBehavior": "recommend_highest_trust_source_or_require_human_review_on_top_tier_disagreement",
        },
    }


def research_agent_status() -> dict[str, Any]:
    """Reporta el readiness del ResearchAgent."""
    return {
        "id": RESEARCH_AGENT_ID,
        "executable": True,
        "status": "executable",
        "reason": (
            "ResearchAgent deterministic source policy, citation validation and artifact persistence are "
            "available without a model runtime."
        ),
        "contract": research_agent_contract(),
    }
