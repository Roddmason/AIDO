from __future__ import annotations

import pytest

from local_control_center.agents.capability_routing import (
    CAPABILITIES,
    MODEL_PROVIDERS,
    PROVIDER_CAPABILITIES,
    CapabilityRouteError,
    can_cover,
    capabilities_of,
    providers_for,
    route,
)


def test_routes_by_capability_not_by_provider_name() -> None:
    # A code task: only CLI code agents qualify; the most capable is selected, the rest are fallbacks.
    result = route({"code_edit", "code_review"})
    assert result["selected"] == "claude_code_cli"
    assert "codex_cli" in result["candidates"]
    assert "swe_agent" in result["candidates"]
    # Not every provider is returned — only the qualifying subset (don't execute every provider).
    assert len(result["candidates"]) < len(MODEL_PROVIDERS)
    assert result["covered"] is True


def test_vision_plus_large_context_picks_a_capable_api_not_a_cli() -> None:
    result = route({"reasoning", "vision", "large_context"})
    assert result["selected"] == "anthropic_api"  # CLIs lack vision and are rejected
    assert "openrouter" in result["candidates"]
    assert all(cand not in {"codex_cli", "claude_code_cli"} for cand in result["candidates"])


def test_local_private_selects_only_the_truly_local_provider() -> None:
    result = route({"local_private", "reasoning"})
    assert result["selected"] == "ollama_local"
    # nvidia_nim is a remote API and must NOT be treated as local-private.
    assert "nvidia_nim" not in result["candidates"]
    assert "local_private" not in PROVIDER_CAPABILITIES["nvidia_nim"]


def test_low_cost_preference_ranks_cheap_providers_first() -> None:
    result = route({"reasoning"}, prefer_low_cost=True)
    assert result["selected"] in {"ollama_local", "ollama_remote"}


def test_web_research_composes_a_reasoning_model_with_the_mcp_tool_bus() -> None:
    result = route({"web_research", "reasoning"})
    # The model handles reasoning; mcp_tools supplies web_research (a tool, not a model executor).
    assert result["selected"] in MODEL_PROVIDERS
    assert result["selected"] != "mcp_tools"
    assert result["tools"] == ["mcp_tools"]
    assert "tool_use" in result["modelRequired"]  # the model must be able to invoke the web tool
    assert result["covered"] is True


def test_mcp_tools_is_never_selected_as_the_model_executor() -> None:
    for required in ({"tool_use"}, {"reasoning", "tool_use"}, {"web_research"}):
        assert route(required)["selected"] != "mcp_tools"


def test_uncoverable_capability_set_reports_no_single_provider() -> None:
    # code_edit needs a CLI runtime; vision needs an API model — no single provider has both.
    result = route({"code_edit", "vision"})
    assert result["selected"] is None
    assert result["covered"] is False
    assert can_cover({"code_edit", "vision"}) is False
    assert all(set(item["missing"]) for item in result["rejected"])


def test_exclude_removes_a_provider_from_consideration() -> None:
    without_top = route({"code_edit", "code_review"}, exclude={"claude_code_cli"})
    assert without_top["selected"] == "codex_cli"


def test_routing_is_deterministic() -> None:
    a = route({"reasoning", "tool_use"}, prefer_local=True)
    b = route({"tool_use", "reasoning"}, prefer_local=True)
    assert a == b


def test_unknown_capability_is_rejected() -> None:
    with pytest.raises(CapabilityRouteError, match="Unknown capabilities"):
        route({"reasoning", "telepathy"})
    with pytest.raises(CapabilityRouteError, match="Unknown capability"):
        providers_for("telepathy")


def test_registry_helpers_and_matrix_integrity() -> None:
    assert "claude_code_cli" in providers_for("code_edit")
    assert "anthropic_api" in providers_for("vision")
    assert providers_for("web_research") == ["mcp_tools"]
    assert capabilities_of("ollama_local") >= {"local_private", "low_cost"}
    # Every capability declared by any provider is part of the canonical catalogue.
    for caps in PROVIDER_CAPABILITIES.values():
        assert caps <= CAPABILITIES
