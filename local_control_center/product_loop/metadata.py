"""Shared Product Loop metadata helpers.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

RESOURCE_COST_POLICY_METADATA_KEYS = frozenset(
    {
        "allowUnknownCost",
        "allow_unknown_cost",
        "requireApprovalForUnknownCost",
        "require_approval_for_unknown_cost",
    }
)


def strip_untrusted_resource_cost_policy_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Remove caller-supplied cost policy overrides from Product Loop metadata."""
    clean = dict(metadata or {})
    for key in RESOURCE_COST_POLICY_METADATA_KEYS:
        clean.pop(key, None)
    return clean
