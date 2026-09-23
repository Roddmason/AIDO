"""Elegibilidad por rol y reparto automático del equipo de runtimes por hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

import pytest

from local_control_center.runtime_team.roles import (
    OPTIONAL_TEAM_ROLES,
    REQUIRED_TEAM_ROLES,
    TEAM_ROLES,
    RuntimeFacts,
    auto_assign_roles,
    eligible_team_roles,
    missing_required_roles,
    team_role_for,
)

CLAUDE = RuntimeFacts(
    "claude_code_cli", "Claude Code CLI", "cli", ("product_owner", "developer", "architect")
)
CODEX = RuntimeFacts("codex_cli", "Codex CLI", "cli", ("product_owner", "developer"))
OMNIROUTE = RuntimeFacts(
    "omniroute", "OmniRoute", "gateway", ("product_owner", "developer", "architect", "security")
)
OLLAMA = RuntimeFacts("ollama", "Ollama", "local", ("product_owner", "architect", "security"))
CLI_CAPABILITIES = ["chat", "code_edit", "issue_to_patch", "review"]


def _status(runtime_id: str, family: str | None, capabilities: list[str], **extra) -> dict:
    return {"id": runtime_id, "providerFamily": family, "capabilities": capabilities, **extra}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_status("claude_code_cli", None, CLI_CAPABILITIES), ("product_owner", "developer", "architect")),
        (_status("codex_cli", None, CLI_CAPABILITIES), ("product_owner", "developer")),
        (
            _status("omniroute", "openai_compatible", ["chat", "code_edit", "code_review"]),
            ("product_owner", "developer", "architect", "security"),
        ),
        (
            _status("ollama", "ollama", ["chat", "local", "private", "streaming"], models=["local_default"]),
            ("product_owner", "architect"),
        ),
        (
            _status("ollama", "ollama", ["chat", "code_review"], models=["local_default"]),
            ("product_owner", "architect", "security"),
        ),
        (_status("ollama", "ollama", ["chat", "code_review"], models=[]), ()),
        (_status("gemini", "gemini", ["chat", "json", "tools"]), ("architect",)),
        (_status("openhands", "openhands", ["chat"]), ()),
    ],
)
def test_eligibility_follows_each_runner_and_the_scheduler_capability(status, expected):
    assert eligible_team_roles(status) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_status("codex_cli", None, ["chat", "issue_to_patch"]), ("product_owner",)),
        (_status("claude_code_cli", None, ["code_edit"]), ("developer",)),
        (_status("custom_gateway", "openai_compatible", ["code_edit", "code_review"]), ()),
        (
            _status("custom_gateway", "openai_compatible", ["chat", "review"]),
            ("product_owner", "architect", "security"),
        ),
    ],
)
def test_runtimes_the_runners_reject_are_never_eligible(status, expected):
    assert eligible_team_roles(status) == expected


@pytest.mark.parametrize(
    ("role", "kind", "capabilities", "expected"),
    [
        ("product_owner", "reason", ["product_discovery"], "product_owner"),
        ("architect", "reason", ["system_design"], "architect"),
        ("developer", "", [], "developer"),
        ("backend_engineer", "build", ["code_edit"], "developer"),
        ("database_engineer", "build", ["schema_design"], "developer"),
        ("security_engineer", "review", ["security_review"], "security"),
        ("qa_engineer", "review", ["test_design"], None),
        ("technical_lead", "reason", ["code_review"], None),
    ],
)
def test_scheduler_roles_map_to_team_roles(role, kind, capabilities, expected):
    assert team_role_for(role, kind=kind, capabilities=capabilities) == expected


def test_a_single_runtime_takes_every_role_it_can_fill():
    assert auto_assign_roles([CLAUDE]) == {
        "developer": "claude_code_cli",
        "product_owner": "claude_code_cli",
        "architect": "claude_code_cli",
    }


def test_two_runtimes_put_the_cli_on_development_and_spread_the_rest():
    assert auto_assign_roles([OMNIROUTE, CODEX]) == {
        "developer": "codex_cli",
        "product_owner": "omniroute",
        "architect": "omniroute",
        "security": "omniroute",
    }


def test_four_runtimes_follow_runtime_order_before_recycling():
    assignment = auto_assign_roles([OMNIROUTE, OLLAMA, CODEX, CLAUDE], ["claude_code_cli", "ollama"])
    assert assignment == {
        "developer": "claude_code_cli",
        "product_owner": "codex_cli",
        "architect": "ollama",
        "security": "omniroute",
    }


def test_a_single_runtime_for_po_and_developer_is_a_complete_team():
    assignment = auto_assign_roles([CODEX])
    assert assignment == {"developer": "codex_cli", "product_owner": "codex_cli"}
    assert missing_required_roles(assignment) == []
    assert missing_required_roles({"developer": "codex_cli"}) == ["product_owner"]
    assert missing_required_roles({"architect": "ollama", "security": "ollama"}) == [
        "product_owner",
        "developer",
    ]
    assert missing_required_roles(auto_assign_roles([OLLAMA])) == ["developer"]


def test_ties_break_alphabetically_for_determinism():
    first = RuntimeFacts("aaa_gateway", "AAA", "gateway", ("developer",))
    last = RuntimeFacts("zzz_gateway", "ZZZ", "gateway", ("developer",))
    assert auto_assign_roles([last, first]) == {"developer": "aaa_gateway"}


def test_only_product_owner_and_developer_are_required():
    assert TEAM_ROLES == ("product_owner", "developer", "architect", "security")
    assert REQUIRED_TEAM_ROLES == ("product_owner", "developer")
    assert OPTIONAL_TEAM_ROLES == ("architect", "security")
