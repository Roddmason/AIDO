"""Concise narrative instructions for DeveloperAgent, independent of model and execution policy.

Only the summary rule changes; code, schemas, verification and runtime selection stay with their callers.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

from local_control_center.settings.resolver import resolve_setting_value

NORMAL_SUMMARY_INSTRUCTION = (
    "- Produce a concise structured summary with changed files, tests run, blockers, and residual risks."
)
COMPACT_SUMMARY_INSTRUCTION = (
    "- Summary: files/tests/blockers/risks; no filler. Keep language/format/code/evidence/uncertainty."
)


def resolve_response_style(connection: sqlite3.Connection | None, *, project_id: str | None) -> str:
    """Use the existing project > general > default setting; threads inherit their project."""
    if connection is None:
        return "compact"
    value = resolve_setting_value(connection=connection, key="agents.responseStyle", project_id=project_id)
    return "compact" if value == "compact" else "normal"


def developer_summary_instruction(style: str) -> str:
    """Replace only the narrative summary rule, leaving structured output and code contracts alone."""
    return COMPACT_SUMMARY_INSTRUCTION if style == "compact" else NORMAL_SUMMARY_INSTRUCTION
