from __future__ import annotations

from typing import Any


REAL_QA_HASH = "a" * 64


def real_qa_evidence_fields(
    *,
    command: str = "uv run pytest tests_py -q",
    tool_call_id: str = "agent-tool-call-real-qa",
    policy_decision_id: str = "permission-decision-real-qa",
    artifact_id: str = "artifact-real-qa-output",
    duration_ms: int | None = None,
) -> dict[str, Any]:
    test_result: dict[str, Any] = {
        "command": command,
        "status": "passed",
        "execution": "restricted_subprocess",
        "exitCode": 0,
        "returnCode": 0,
        "toolCallId": tool_call_id,
        "outputArtifactId": artifact_id,
        "artifactHashes": {
            "stdoutHash": REAL_QA_HASH,
            "stderrHash": REAL_QA_HASH,
            "outputArtifactHash": REAL_QA_HASH,
        },
        "metadata": {"permissionDecisionId": policy_decision_id},
    }
    if duration_ms is not None:
        test_result["durationMs"] = duration_ms
    return {
        "evidenceSource": "qa_passed_by_command",
        "testResults": [test_result],
        "toolCalls": [{"id": tool_call_id, "status": "completed"}],
        "policyDecisions": [{"id": policy_decision_id, "decision": "allow"}],
        "artifacts": [{"id": artifact_id, "kind": "test_report", "hash": REAL_QA_HASH}],
        "hashes": {artifact_id: REAL_QA_HASH},
    }
