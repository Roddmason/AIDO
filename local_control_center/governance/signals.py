"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any

from .repository import GovernanceRepository


def record_governance_risk(
    connection: Any,
    *,
    project_id: str | None,
    title: str,
    source_type: str,
    source_id: str,
    severity: str = "medium",
    status: str = "open",
    description: str = "",
    mitigation: str = "",
    owner: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not project_id:
        return None
    risk_metadata = {
        **(metadata or {}),
        "sourceType": source_type,
        "sourceId": source_id,
    }
    return GovernanceRepository(connection).create_risk(
        {
            "projectId": project_id,
            "title": title,
            "severity": severity,
            "status": status,
            "description": description,
            "mitigation": mitigation,
            "owner": owner,
            "metadata": risk_metadata,
        }
    )
