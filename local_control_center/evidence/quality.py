from __future__ import annotations

from typing import Any


FAILED_TEST_STATUSES = {"blocked", "denied", "error", "failed", "timeout", "timed_out"}
REQUIRED_EVIDENCE_PACKAGE_KEYS = {
    "workflowRunId",
    "jobId",
    "agentRunId",
    "workspaceId",
    "runtimeId",
    "runtimeHealth",
    "modelCalls",
    "toolCalls",
    "policyDecisions",
    "approvals",
    "qaVerdict",
    "artifacts",
    "diffSummary",
    "hashes",
    "createdAt",
}


def failed_test_results(test_results: list[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for result in test_results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "").strip().lower()
        if status in FAILED_TEST_STATUSES:
            failed.append(result)
    return failed


def qa_passed_without_failed_results(evidence: dict[str, Any]) -> bool:
    test_results = evidence.get("testResults") or []
    return (
        str(evidence.get("qaVerdict") or "").lower() == "passed"
        and bool(test_results)
        and not failed_test_results(test_results)
        and bool(evidence.get("artifactIds") or evidence.get("artifacts") or evidence.get("diffRefs") or evidence.get("screenshotRefs"))
    )


def evidence_package_contract_errors(
    evidence: dict[str, Any],
    *,
    require_runtime_links: bool = False,
    require_workflow_run: bool | None = None,
) -> list[str]:
    errors: list[str] = []
    if require_workflow_run is None:
        require_workflow_run = require_runtime_links
    missing = sorted(key for key in REQUIRED_EVIDENCE_PACKAGE_KEYS if key not in evidence)
    if missing:
        errors.append(f"Evidence package contract is missing fields: {', '.join(missing)}.")

    if require_runtime_links:
        runtime_link_keys = ["jobId", "agentRunId", "workspaceId", "runtimeId"]
        if require_workflow_run:
            runtime_link_keys.insert(0, "workflowRunId")
        for key in runtime_link_keys:
            if not evidence.get(key):
                errors.append(f"Evidence package contract requires {key}.")
        if not isinstance(evidence.get("runtimeHealth"), dict) or not evidence.get("runtimeHealth"):
            errors.append("Evidence package contract requires runtimeHealth.")

    if str(evidence.get("qaVerdict") or "").lower() == "passed" and not (evidence.get("testResults") or []):
        errors.append("Evidence package cannot pass without testResults.")

    for key in ("modelCalls", "toolCalls", "policyDecisions", "approvals", "artifacts"):
        if key in evidence and not isinstance(evidence.get(key), list):
            errors.append(f"Evidence package {key} must be a list.")

    hashes = evidence.get("hashes")
    if "hashes" in evidence and not isinstance(hashes, dict):
        errors.append("Evidence package hashes must be an object.")
    if require_runtime_links and not hashes:
        errors.append("Evidence package contract requires artifact hashes.")

    artifacts = evidence.get("artifacts") or []
    if require_runtime_links and not artifacts:
        errors.append("Evidence package contract requires artifact refs.")
    if isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                errors.append(f"Evidence package artifacts[{index}] must be an object.")
                continue
            if not artifact.get("id"):
                errors.append(f"Evidence package artifacts[{index}] requires id.")
            if not artifact.get("kind"):
                errors.append(f"Evidence package artifacts[{index}] requires kind.")
            if not artifact.get("hash"):
                errors.append(f"Evidence package artifacts[{index}] requires hash.")

    return errors


def evidence_package_is_completion_grade(
    evidence: dict[str, Any],
    *,
    require_runtime_links: bool = False,
    require_workflow_run: bool | None = None,
) -> bool:
    return not evidence_package_contract_errors(
        evidence,
        require_runtime_links=require_runtime_links,
        require_workflow_run=require_workflow_run,
    )
