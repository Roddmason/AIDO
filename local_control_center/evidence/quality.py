from __future__ import annotations

from typing import Any


FAILED_TEST_STATUSES = {"blocked", "denied", "error", "failed", "timeout", "timed_out"}


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
    return str(evidence.get("qaVerdict") or "").lower() == "passed" and not failed_test_results(
        evidence.get("testResults") or []
    )
