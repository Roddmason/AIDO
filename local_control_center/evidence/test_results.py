"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

MAX_TEST_REPORT_BYTES = 2_000_000
PYTEST_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<status>passed|failed|error|errors|skipped|xfailed|xpassed)"
)
PYTEST_DURATION_RE = re.compile(r"in\s+(?P<seconds>\d+(?:\.\d+)?)s")


class TestReportError(ValueError):
    pass


def normalize_test_result_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for report in reports:
        report_format = str(report.get("format", "")).lower().strip()
        command = str(report.get("command", ""))
        content = report.get("content")
        if not isinstance(content, str):
            raise TestReportError("Test report content must be a string.")
        if len(content.encode("utf-8")) > MAX_TEST_REPORT_BYTES:
            raise TestReportError("Test report content exceeds the maximum accepted size.")
        if report_format == "junit":
            normalized.extend(_parse_junit_report(content=content, command=command))
        elif report_format == "pytest":
            normalized.append(_parse_pytest_summary(content=content, command=command))
        else:
            raise TestReportError(f"Unsupported test report format: {report_format}")
    return normalized


def _parse_junit_report(*, content: str, command: str) -> list[dict[str, Any]]:
    stripped = content.lstrip()
    upper = stripped.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise TestReportError("Unsafe XML declarations are not allowed in JUnit reports.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise TestReportError(f"Invalid JUnit XML: {error}") from error

    suites = root.findall(".//testsuite") if root.tag == "testsuites" else [root]
    results: list[dict[str, Any]] = []
    for suite in suites:
        suite_name = suite.attrib.get("name", "")
        for case in suite.findall("testcase"):
            case_name = case.attrib.get("name", "unknown")
            class_name = case.attrib.get("classname", "")
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            status = "passed"
            failure_message = ""
            if error is not None:
                status = "error"
                failure_message = error.attrib.get("message", "") or (error.text or "").strip()
            elif failure is not None:
                status = "failed"
                failure_message = failure.attrib.get("message", "") or (failure.text or "").strip()
            elif skipped is not None:
                status = "skipped"
                failure_message = skipped.attrib.get("message", "") or (skipped.text or "").strip()

            results.append(
                {
                    "command": _case_command(command, class_name, case_name),
                    "status": status,
                    "durationMs": _duration_ms(case.attrib.get("time")),
                    "metadata": {
                        "format": "junit",
                        "suiteName": suite_name,
                        "className": class_name,
                        "caseName": case_name,
                        "failureMessage": failure_message,
                    },
                }
            )
    if not results:
        results.append(
            {
                "command": command,
                "status": _suite_status(root),
                "durationMs": _duration_ms(root.attrib.get("time")),
                "metadata": {
                    "format": "junit",
                    "suiteName": root.attrib.get("name", ""),
                    "counts": {
                        "tests": _int_attr(root, "tests"),
                        "failures": _int_attr(root, "failures"),
                        "errors": _int_attr(root, "errors"),
                        "skipped": _int_attr(root, "skipped"),
                    },
                },
            }
        )
    return results


def _parse_pytest_summary(*, content: str, command: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for match in PYTEST_COUNT_RE.finditer(content):
        status = match.group("status")
        normalized_status = "errors" if status in {"error", "errors"} else status
        counts[normalized_status] = counts.get(normalized_status, 0) + int(match.group("count"))
    if not counts:
        raise TestReportError("Pytest summary did not contain recognizable result counts.")
    if counts.get("failed", 0) or counts.get("errors", 0):
        status = "failed"
    elif counts.get("passed", 0):
        status = "passed"
    elif counts.get("skipped", 0):
        status = "skipped"
    else:
        status = "unknown"
    duration_match = PYTEST_DURATION_RE.search(content)
    duration_ms = int(float(duration_match.group("seconds")) * 1000) if duration_match else None
    return {
        "command": command,
        "status": status,
        "durationMs": duration_ms,
        "metadata": {"format": "pytest", "counts": counts},
    }


def _case_command(command: str, class_name: str, case_name: str) -> str:
    qualified = ".".join(part for part in (class_name, case_name) if part)
    return f"{command}::{qualified}" if command and qualified else command or qualified


def _duration_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value) * 1000)
    except ValueError:
        return None


def _int_attr(element: ET.Element, name: str) -> int:
    try:
        return int(element.attrib.get(name, "0"))
    except ValueError:
        return 0


def _suite_status(element: ET.Element) -> str:
    if _int_attr(element, "failures") or _int_attr(element, "errors"):
        return "failed"
    if _int_attr(element, "skipped") and _int_attr(element, "skipped") == _int_attr(element, "tests"):
        return "skipped"
    return "passed"
