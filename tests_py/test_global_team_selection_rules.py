"""Reglas de selección del equipo de IA global: solo habilitados, manual gana, fallback y orden automático.

Regla del operador: si no elige, se asigna automáticamente; si eligió, manda su elección; solo cuentan
los proveedores habilitados (switch propio, interruptor de grupo y política del proyecto).

@author Rodrigo Mason
"""

from __future__ import annotations

import typing
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_team.configuration import (
    GLOBAL_RUNTIME_TEAM_METADATA_KEY,
    global_role_order,
    global_role_order_is_explicit,
)
from local_control_center.runtime_team.contracts import GlobalTeamSource
from local_control_center.runtime_team.facts import load_runtime_facts
from local_control_center.runtime_team.global_team import TEAM_ROLE_SOURCES, resolve_global_team
from local_control_center.runtime_team.roles import RuntimeFacts
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.team_scheduler.scheduler import team_member_defaults, team_profiles

CLAUDE = RuntimeFacts(
    "claude_code_cli", "Claude Code CLI", "cli", ("product_owner", "developer", "architect")
)
CODEX = RuntimeFacts("codex_cli", "Codex CLI", "cli", ("product_owner", "developer"))
LLAMA = RuntimeFacts(
    "llama_cpp", "llama.cpp", "local", ("product_owner", "developer", "architect", "security")
)
DENIED = RuntimeFacts(
    "gemini", "Gemini", "api", ("product_owner", "developer"), policy_denied_reason="denied"
)
FACTS = {item.provider_id: item for item in (CLAUDE, CODEX, LLAMA, DENIED)}


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Selection rules", path=tmp_path / "project", template_id="other"
        )
        yield connection, project


def _set(connection, key: str, value, *, project_id: str | None = None) -> None:
    scope = "project" if project_id else "general"
    SettingsRepository(connection).set_value(key, scope, project_id, value)


def _fresh(connection, provider_id: str) -> None:
    record_model_execution(connection, provider_id, "default", True, "test_prompt")


# 1. Solo habilitados -------------------------------------------------------------------------------


def _enable(connection, *provider_ids: str) -> None:
    store = ProviderAccountStore(connection)
    for provider_id in provider_ids:
        store.patch_provider_account(provider_id, {"enabled": True})


def test_a_provider_with_its_own_switch_off_is_never_a_candidate(lane) -> None:
    connection, project = lane
    _enable(connection, "codex_cli", "claude_code_cli")
    ProviderAccountStore(connection).patch_provider_account("claude_code_cli", {"enabled": False})
    facts = load_runtime_facts(connection, project_id=project["id"], offline=True)
    assert "codex_cli" in facts
    assert "claude_code_cli" not in facts
    team = resolve_global_team(connection, project_id=project["id"], offline=True)
    assert "claude_code_cli" not in team.allowed_runtimes


@pytest.mark.parametrize(
    ("switch", "provider_id"),
    [
        ("runtime.cli.enabled", "codex_cli"),
        ("runtime.remote.enabled", "openai_api"),
        ("runtime.remote.enabled", "litellm"),
        ("runtime.nvidia.enabled", "nvidia_nim"),
        ("runtime.local.enabled", "ollama"),
        ("runtime.ollama.enabled", "ollama"),
    ],
)
def test_every_group_kill_switch_vetoes_its_providers(lane, switch: str, provider_id: str) -> None:
    connection, project = lane
    _enable(connection, provider_id)
    before = load_runtime_facts(connection, project_id=project["id"], offline=True)
    assert before[provider_id].policy_denied_reason is None
    _set(connection, switch, False)
    after = load_runtime_facts(connection, project_id=project["id"], offline=True)
    assert switch in str(after[provider_id].policy_denied_reason)
    team = resolve_global_team(connection, project_id=project["id"], offline=True)
    assert provider_id not in team.allowed_runtimes
    assert all(provider_id not in item.effective for item in team.roles.values())


def test_the_project_policy_vetoes_providers_outside_its_allowlist(lane) -> None:
    connection, project = lane
    _enable(connection, "codex_cli", "claude_code_cli")
    _set(connection, "project.runtime.allowedProviders", ["codex_cli"], project_id=project["id"])
    facts = load_runtime_facts(connection, project_id=project["id"], offline=True)
    assert "allowedProviders" in str(facts["claude_code_cli"].policy_denied_reason)
    team = resolve_global_team(connection, project_id=project["id"], offline=True)
    assert "claude_code_cli" not in team.allowed_runtimes
    # Con hechos inyectados (sin veto calculado) la allowlist del proyecto igual restringe.
    injected = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert set(injected.allowed_runtimes) <= {"codex_cli"}


# 2. y 3. Manual gana; fallback automático ----------------------------------------------------------


def test_a_manual_list_keeps_the_operator_order_even_when_it_is_stale(lane) -> None:
    connection, project = lane
    _fresh(connection, "codex_cli")
    _set(connection, "team.role.developer", ["llama_cpp", "claude_code_cli", "codex_cli"])
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    developer = team.roles["developer"]
    # Ni reordenado por frescura (codex es el único fresco) ni recortado por vencido.
    assert developer.source == "general"
    assert developer.effective == ("llama_cpp", "claude_code_cli", "codex_cli")
    assert developer.assigned == "llama_cpp"


def test_a_manual_list_drops_only_disabled_or_ineligible_ids(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.developer", ["gemini", "codex_cli", "ghost"], project_id=project["id"])
    developer = resolve_global_team(connection, project_id=project["id"], facts=FACTS).roles["developer"]
    assert developer.source == "project"
    assert developer.effective == ("codex_cli",)
    assert developer.invalid == ("gemini", "ghost")


def test_an_all_disabled_manual_list_falls_back_to_the_automatic_assignment(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.developer", ["gemini", "ghost"])
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    developer = team.roles["developer"]
    assert developer.source == "automatic_fallback"
    assert developer.configured == ("gemini", "ghost")
    assert developer.invalid == ("gemini", "ghost")
    assert developer.effective == ("codex_cli", "claude_code_cli", "llama_cpp")
    assert developer.assigned is not None
    assert team.sealed()["source"]["developer"] == "automatic_fallback"


def test_a_derived_role_with_an_all_disabled_manual_list_falls_back_to_the_product_owner_order(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.technical_lead", ["gemini"])
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    lead = team.roles["technical_lead"]
    assert lead.source == "automatic_fallback"
    assert lead.effective == team.roles["product_owner"].effective
    assert lead.invalid == ("gemini",)


def test_the_new_source_is_published_in_every_contract() -> None:
    assert "automatic_fallback" in TEAM_ROLE_SOURCES
    assert set(typing.get_args(GlobalTeamSource)) == set(TEAM_ROLE_SOURCES)
    openapi = Path("local-control-center/web/src/api/generated/openapi.ts").read_text(encoding="utf-8")
    assert '"automatic_fallback"' in openapi


def test_a_fallback_order_is_automatic_for_routing(lane) -> None:
    """El loop recorre el orden de fallback como uno automático y un rol opcional vacío usa el equipo."""
    connection, project = lane
    _set(connection, "team.role.developer", ["gemini"])
    _set(connection, "team.role.security", ["ghost"])
    facts = {**FACTS, "llama_cpp": RuntimeFacts("llama_cpp", "llama.cpp", "local", ("product_owner",))}
    sealed = resolve_global_team(connection, project_id=project["id"], facts=facts).sealed()
    meta = {GLOBAL_RUNTIME_TEAM_METADATA_KEY: sealed}
    assert sealed["source"]["security"] == "automatic_fallback"
    assert sealed["roleRuntimeOrder"]["security"] == []
    assert global_role_order(meta, "security") == sealed["allowedRuntimes"]
    assert not global_role_order_is_explicit(meta, "developer")
    assert global_role_order(meta, "developer") == sealed["roleRuntimeOrder"]["developer"]


# 4. Orden automático ------------------------------------------------------------------------------


def test_automatic_assignment_prefers_freshly_validated_providers(lane) -> None:
    connection, project = lane
    baseline = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    # Sin evidencia manda el ranking de siempre (el runtimeOrder sembrado pone a codex antes que claude).
    assert baseline.roles["developer"].effective == ("codex_cli", "claude_code_cli", "llama_cpp")
    _fresh(connection, "claude_code_cli")
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert team.roles["developer"].assigned == "claude_code_cli"
    assert team.roles["developer"].effective == ("claude_code_cli", "codex_cli", "llama_cpp")
    # El reparto sigue prefiriendo un proveedor distinto por rol: el PO toma el siguiente.
    assert team.roles["product_owner"].assigned == "codex_cli"


def test_freshness_never_blocks_the_global_team(lane) -> None:
    connection, project = lane
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    for role in ("product_owner", "developer", "architect", "security", "technical_lead", "researcher"):
        assert team.roles[role].assigned is not None, role


def test_quota_suspended_providers_go_last_and_come_back_after_the_window(lane) -> None:
    connection, project = lane
    _fresh(connection, "codex_cli")
    QuotaManager(connection).record_rate_limit(
        provider_id="codex_cli", model="default", retry_after_seconds=3600
    )
    assert "codex_cli" in QuotaManager(connection).providers_in_cooldown()
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    # Fresco pero suspendido: al final, no fuera.
    assert team.roles["developer"].effective[-1] == "codex_cli"
    assert team.roles["developer"].assigned == "claude_code_cli"
    connection.execute("UPDATE provider_limits SET cooldown_until = NULL WHERE provider_id = 'codex_cli'")
    recovered = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert recovered.roles["developer"].assigned == "codex_cli"


def test_a_quota_suspension_never_reorders_a_manual_list(lane) -> None:
    connection, project = lane
    QuotaManager(connection).record_rate_limit(
        provider_id="codex_cli", model="default", retry_after_seconds=3600
    )
    _set(connection, "team.role.developer", ["codex_cli", "claude_code_cli"])
    developer = resolve_global_team(connection, project_id=project["id"], facts=FACTS).roles["developer"]
    assert developer.effective == ("codex_cli", "claude_code_cli")


# 5. Perfiles por defecto del scheduler ------------------------------------------------------------


def test_default_team_profiles_skip_disabled_providers() -> None:
    full = {row["role"]: row for row in team_profiles()}
    assert "claude_code_cli" in full["backend_engineer"]["providerPreference"]
    filtered = {row["role"]: row for row in team_profiles({"claude_code_cli"})}
    for row in filtered.values():
        assert "claude_code_cli" not in row["providerPreference"]
    for member in team_member_defaults({"claude_code_cli"}):
        assert "claude_code_cli" not in member["metadata"]["providerPreference"]
        assert "claude_code_cli" not in member["defaultRuntimePolicy"]["providerCandidates"]
    # Sin proveedores apagados el catálogo sembrado no cambia.
    assert team_member_defaults() == team_member_defaults(())
