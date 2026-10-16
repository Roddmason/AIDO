"""Policy for which executed remediation actions resolve their record.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any


def should_resolve(*, action_type: str, execution: dict[str, Any]) -> bool:
    """Return true when an executed action succeeded and its record should be resolved."""
    if execution.get("status") not in {"completed", "queued", "awaiting_approval"}:
        return False
    return action_type in {
        "validate_runtime",
        "switch_runtime",
        "continue_plan_only",
        "git_init",
        "add_remote",
        "create_branch",
        "checkout_branch",
        "run_gitleaks",
        "run_worker_once",
        "check_network_access",
        "answer_question",
        "approve_resource_decision",
        "retry_loop",
        "revalidate_runtime",
    }
