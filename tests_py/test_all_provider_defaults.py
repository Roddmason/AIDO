from __future__ import annotations

from typing import Any

from local_control_center.agents.provider_catalog import PROVIDER_CATALOG
from local_control_center.agents.team_availability import runtime_availability
from local_control_center.team_scheduler.scheduler import team_member_defaults

ORIGINAL_PROVIDER_PREFERENCES = {
    "aido_lead": ["claude_code_cli", "codex_cli", "openai_compatible"],
    "project_manager": ["openai_compatible", "ollama"],
    "scrum_master": ["openai_compatible", "ollama"],
    "architect": ["claude_code_cli", "openai_compatible", "nvidia_nim"],
    "technical_lead": ["codex_cli", "claude_code_cli", "openai_compatible"],
    "backend_engineer": ["codex_cli", "claude_code_cli", "openhands", "swe_agent"],
    "frontend_engineer": ["codex_cli", "claude_code_cli", "openhands", "swe_agent"],
    "mobile_engineer": ["codex_cli", "claude_code_cli"],
    "database_engineer": ["codex_cli", "claude_code_cli", "openai_compatible"],
    "data_engineer": ["codex_cli", "openai_compatible", "nvidia_nim"],
    "qa_engineer": ["codex_cli", "claude_code_cli", "openai_compatible"],
    "security_engineer": ["claude_code_cli", "openai_compatible"],
    "pentester": ["claude_code_cli", "openai_compatible"],
    "devops_engineer": ["codex_cli", "claude_code_cli"],
    "researcher": ["openai_compatible", "openrouter", "nvidia_nim", "anthropic_api"],
    "release_manager": ["claude_code_cli", "codex_cli"],
}
TECHNICAL_PROVIDER_IDS = {
    "codex_cli",
    "claude_code_cli",
    "openhands",
    "swe_agent",
    "manual",
}


def _status(
    provider_id: str,
    *,
    capabilities: list[str] | None = None,
    can_run_prompt: bool = True,
    can_edit_workspace: bool = False,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "executable": True,
        "capabilities": capabilities or ["chat"],
        "canRunPrompt": can_run_prompt,
        "canEditWorkspace": can_edit_workspace,
    }


def test_all_default_roles_allow_the_dynamic_provider_catalog_with_ordered_preferences() -> None:
    profiles = team_member_defaults()
    known_provider_ids = {entry.id for entry in PROVIDER_CATALOG} | TECHNICAL_PROVIDER_IDS

    for profile in profiles:
        candidates = profile["defaultRuntimePolicy"]["providerCandidates"]
        assert profile["allowedProviders"] == ["*"]
        assert "*" not in candidates
        assert known_provider_ids <= set(candidates)
        assert profile["allowedRuntimes"] == ["*"]

    product_owner = next(profile for profile in profiles if profile["role"] == "product_owner")
    assert product_owner["defaultRuntimePolicy"]["providerCandidates"][:4] == [
        "gemini",
        "ollama",
        "codex_cli",
        "claude_code_cli",
    ]


def test_non_product_owner_roles_keep_their_previous_provider_order() -> None:
    profiles = {profile["role"]: profile for profile in team_member_defaults()}

    for role, original in ORIGINAL_PROVIDER_PREFERENCES.items():
        candidates = profiles[role]["defaultRuntimePolicy"]["providerCandidates"]
        assert candidates[: len(original)] == original


def test_wildcard_permission_uses_concrete_runtime_policy_candidates() -> None:
    profile = {
        "status": "active",
        "allowedProviders": ["*"],
        "defaultRuntimePolicy": {
            "providerCandidates": ["gemini", "nvidia_nim"],
            "requiredCapabilities": ["chat"],
        },
    }

    availability = runtime_availability(
        profile,
        [_status("nvidia_nim"), _status("gemini")],
    )

    assert availability["selectedProviderId"] == "gemini"
    assert availability["candidateProviderIds"] == ["gemini", "nvidia_nim"]
    assert "*" not in availability["candidateProviderIds"]


def test_wildcard_appends_named_provider_instances_after_canonical_preferences() -> None:
    profile = {
        "status": "active",
        "allowedProviders": ["*"],
        "defaultRuntimePolicy": {
            "providerCandidates": ["gemini", "ollama"],
            "requiredCapabilities": ["chat"],
        },
    }

    availability = runtime_availability(
        profile,
        [_status("gemini-team"), _status("gemini"), _status("ollama")],
    )

    assert availability["candidateProviderIds"] == ["gemini", "ollama", "gemini-team"]


def test_wildcard_without_explicit_candidates_expands_catalog_deterministically() -> None:
    profile = {
        "status": "active",
        "allowedProviders": ["*"],
        "defaultRuntimePolicy": {"requiredCapabilities": ["chat"]},
    }

    availability = runtime_availability(profile, [_status("zeta"), _status("alpha")])

    assert availability["selectedProviderId"] == "alpha"
    assert availability["candidateProviderIds"] == ["alpha", "zeta"]


def test_chat_provider_permission_does_not_bypass_code_edit_capability() -> None:
    profile = {
        "status": "active",
        "allowedProviders": ["*"],
        "defaultRuntimePolicy": {
            "providerCandidates": ["gemini", "codex_cli"],
            "requiredCapabilities": ["code_edit"],
        },
    }
    statuses = [
        _status("gemini", capabilities=["chat"]),
        _status(
            "codex_cli",
            capabilities=["chat", "code_edit"],
            can_edit_workspace=True,
        ),
    ]

    availability = runtime_availability(profile, statuses)

    assert availability["selectedProviderId"] == "codex_cli"
    assert availability["requiredCapabilities"] == ["code_edit"]


def test_profile_api_validation_accepts_provider_and_runtime_wildcards() -> None:
    from local_control_center.agents.api import validate_agent_profile_body

    body = next(profile for profile in team_member_defaults() if profile["role"] == "product_owner")

    assert validate_agent_profile_body(body)["allowedProviders"] == ["*"]
    assert validate_agent_profile_body(body)["allowedRuntimes"] == ["*"]
