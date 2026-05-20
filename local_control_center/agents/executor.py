from __future__ import annotations

from typing import Any


def _input_evidence_refs(input_payload: dict[str, Any]) -> list[str]:
    for key in ("evidenceRefs", "evidence_refs", "evidencePackageIds"):
        refs = input_payload.get(key)
        if isinstance(refs, list):
            return [str(ref) for ref in refs if isinstance(ref, str) and ref]
    return []


def run_internal_mock_agent(*, agent_profile: dict[str, Any], task_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": agent_profile["id"],
        "task_id": task_id,
        "verdict": "approved_with_risks",
        "summary": f"Internal mock completed {task_id}.",
        "evidence_refs": _input_evidence_refs(input_payload),
        "risks": [
            {
                "severity": "low",
                "description": "Mock runtime does not execute code.",
                "mitigation": "Use a real sandboxed runtime before accepting implementation output.",
            }
        ],
        "next_actions": [],
        "input": input_payload,
    }
