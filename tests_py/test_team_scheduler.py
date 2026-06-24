from __future__ import annotations

import pytest

from local_control_center.team_scheduler.scheduler import (
    SCHEDULER_VERSION,
    TeamScheduleError,
    schedule_team,
    select_roles,
)


def _roles(plan: dict) -> set[str]:
    return {assignment["role"] for assignment in plan["roles"]}


def _by_role(plan: dict, role: str) -> dict:
    return next(assignment for assignment in plan["roles"] if assignment["role"] == role)


def test_economy_low_risk_backend_picks_only_the_minimal_team() -> None:
    plan = schedule_team(scope=["backend"], risk="low", mode="economy")

    # Only the roles the task actually needs: core + the backend engineer. No QA/security/coordination.
    assert _roles(plan) == {"product_owner", "technical_lead", "backend_engineer"}
    assert plan["schedulerVersion"] == SCHEDULER_VERSION
    assert plan["modelTier"] == "economy"

    engineer = _by_role(plan, "backend_engineer")
    assert engineer["permissionProfile"] == "dev_safe"  # resolved from policy_engine PROFILE_DEFAULTS
    assert engineer["providerKind"] == "local"
    assert engineer["runtime"] == "ollama"
    assert engineer["modelTier"] == "economy"
    assert engineer["tools"] == ["shell", "workspace_patch"]
    assert engineer["qualityGates"] == ["lint", "test"]
    assert engineer["budgetUsd"] == 1.0  # build weight 1.0 x economy budget 1.0
    # Self-review is acceptable for low-stakes economy work.
    assert engineer["reviewer"] is None
    assert _by_role(plan, "product_owner")["budgetUsd"] == 0.5  # reason weight 0.5


def test_role_resolution_carries_all_eight_attributes() -> None:
    plan = schedule_team(scope=["backend"], risk="medium", mode="balanced")
    assignment = _by_role(plan, "backend_engineer")
    for attribute in (
        "providerKind",
        "modelTier",
        "runtime",
        "skills",
        "tools",
        "budgetUsd",
        "reviewer",
        "qualityGates",
    ):
        assert attribute in assignment
    assert assignment["reviewer"] == "technical_lead"  # peer review outside economy/low-risk
    assert assignment["qualityGates"] == ["lint", "test", "typecheck", "build"]


def test_scope_pulls_in_exactly_the_matching_engineers() -> None:
    plan = schedule_team(scope=["frontend", "mobile", "database", "infra"], risk="medium", mode="balanced")
    roles = _roles(plan)
    assert {"frontend_engineer", "mobile_engineer", "database_engineer", "devops_engineer"} <= roles
    assert "backend_engineer" not in roles  # backend not in scope
    # database engineer carries its role-specific gates on top of the mode base gates.
    assert "migration_check" in _by_role(plan, "database_engineer")["qualityGates"]


def test_payments_in_economy_still_forces_a_real_security_reviewer() -> None:
    # Critic regression: a security-sensitive task must never leave a role self-reviewing, even in economy.
    plan = schedule_team(scope=["payments", "backend"], risk="medium", mode="economy")
    assert "security_engineer" in _roles(plan)
    security = _by_role(plan, "security_engineer")
    assert security["reviewer"] not in (None, "security_engineer")
    assert security["reviewer"] == "technical_lead"
    assert "gitleaks" in security["qualityGates"] and "semgrep" in security["qualityGates"]
    # The backend engineer on a payments task is also peer-reviewed despite economy mode.
    assert _by_role(plan, "backend_engineer")["reviewer"] == "technical_lead"


def test_high_risk_adds_architect_and_security_regardless_of_scope() -> None:
    plan = schedule_team(scope=["backend"], risk="high", mode="balanced")
    assert {"architect", "security_engineer"} <= _roles(plan)


def test_pentester_only_in_critical_or_maximum_with_security_or_critical_risk() -> None:
    assert "pentester" not in _roles(schedule_team(scope=["security"], risk="medium", mode="balanced"))
    assert "pentester" in _roles(schedule_team(scope=["security"], risk="medium", mode="critical"))
    assert "pentester" in _roles(schedule_team(scope=["backend"], risk="critical", mode="maximum"))


def test_coordination_roles_only_in_maximum_or_critical_large_team() -> None:
    # Maximum always staffs the coordination + release roles and the most rigorous gates.
    maximum = schedule_team(scope=["backend", "frontend", "data"], risk="medium", mode="maximum")
    assert {"project_manager", "scrum_master", "release_manager", "architect"} <= _roles(maximum)
    assert _by_role(maximum, "backend_engineer")["modelTier"] == "frontier"
    assert "performance" in _by_role(maximum, "backend_engineer")["qualityGates"]
    # A small balanced task gets no coordination overhead.
    small = schedule_team(scope=["backend"], risk="low", mode="balanced")
    assert {"project_manager", "scrum_master"}.isdisjoint(_roles(small))


def test_mode_tiers_escalate_model_budget_and_runtime() -> None:
    tiers = {
        mode: schedule_team(scope=["backend"], risk="medium", mode=mode)
        for mode in ("economy", "balanced", "critical", "maximum")
    }
    assert [tiers[m]["modelTier"] for m in ("economy", "balanced", "critical", "maximum")] == [
        "economy",
        "standard",
        "high",
        "frontier",
    ]
    # Economy runs local; maximum runs the CLI runtime.
    assert _by_role(tiers["economy"], "backend_engineer")["runtime"] == "ollama"
    assert _by_role(tiers["maximum"], "backend_engineer")["runtime"] == "claude_code_cli"
    economy_budget = _by_role(tiers["economy"], "backend_engineer")["budgetUsd"]
    maximum_budget = _by_role(tiers["maximum"], "backend_engineer")["budgetUsd"]
    assert maximum_budget > economy_budget


def test_scheduler_is_deterministic() -> None:
    a = schedule_team(scope=["backend", "data"], risk="high", mode="critical")
    b = schedule_team(scope=["data", "backend"], risk="high", mode="critical")  # order-independent scope
    assert a == b


def test_unknown_mode_or_risk_is_rejected() -> None:
    with pytest.raises(TeamScheduleError, match="Unknown team mode"):
        schedule_team(scope=["backend"], risk="low", mode="turbo")
    with pytest.raises(TeamScheduleError, match="Unknown task risk"):
        schedule_team(scope=["backend"], risk="extreme", mode="balanced")


def test_select_roles_is_pure_and_excludes_unrequested_roles() -> None:
    roles = select_roles(scope={"backend"}, risk="low", mode="economy")
    assert "mobile_engineer" not in roles and "data_engineer" not in roles and "researcher" not in roles
