"""Equipo de IA global: settings por rol, resolución sobre proveedores activos y sellado.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team.global_team import (
    DERIVED_TEAM_ROLES,
    GLOBAL_TEAM_ROLES,
    resolve_global_team,
)
from local_control_center.runtime_team.roles import RuntimeFacts
from local_control_center.settings.registry import GLOBAL_TEAM_ROLE_KEYS, descriptor_for, validate_value
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

CATALOG = Path("local_control_center/i18n/default_catalog.json")

CLAUDE = RuntimeFacts(
    "claude_code_cli", "Claude Code CLI", "cli", ("product_owner", "developer", "architect")
)
CODEX = RuntimeFacts("codex_cli", "Codex CLI", "cli", ("product_owner", "developer"))
LLAMA = RuntimeFacts(
    "llama_cpp", "llama.cpp", "local", ("product_owner", "developer", "architect", "security")
)
DENIED = RuntimeFacts(
    "gemini", "Gemini", "api", ("product_owner",), policy_denied_reason="project.runtime.allowedProviders"
)
FACTS = {item.provider_id: item for item in (CLAUDE, CODEX, LLAMA, DENIED)}


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Global team", path=tmp_path / "project", template_id="other"
        )
        yield connection, project


def _set(connection, key: str, value: list[str], *, project_id: str | None = None) -> None:
    scope = "project" if project_id else "general"
    SettingsRepository(connection).set_value(key, scope, project_id, value)


def test_every_global_team_role_has_a_project_overridable_string_list_setting() -> None:
    assert GLOBAL_TEAM_ROLE_KEYS == (
        "product_owner",
        "developer",
        "architect",
        "security",
        "technical_lead",
        "researcher",
    )
    translations = json.loads(CATALOG.read_text(encoding="utf-8"))["translations"]
    for role in GLOBAL_TEAM_ROLE_KEYS:
        descriptor = descriptor_for(f"team.role.{role}")
        assert descriptor is not None
        assert descriptor.type == "string_list"
        assert descriptor.section == "team"
        assert descriptor.project_section == "team"
        assert descriptor.default == []
        assert validate_value(descriptor, ["claude_code_cli", "llama_cpp"]) == [
            "claude_code_cli",
            "llama_cpp",
        ]
        assert translations[descriptor.label_key]["en"] != translations[descriptor.label_key]["es"]


def test_roles_are_the_registered_setting_keys() -> None:
    assert GLOBAL_TEAM_ROLES == GLOBAL_TEAM_ROLE_KEYS
    assert DERIVED_TEAM_ROLES == ("technical_lead", "researcher")


def test_without_configuration_the_team_is_the_automatic_split_with_fallbacks(lane) -> None:
    connection, project = lane
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)

    # Reparto automático (CLI primero, luego el runtimeOrder sembrado por el esquema, que pone a
    # codex_cli antes que claude_code_cli): developer=codex, PO=claude; el resto del orden elegible
    # queda como fallback. Gemini está vetado por la política y nunca aparece.
    assert team.roles["developer"].source == "automatic"
    assert team.roles["developer"].effective == ("codex_cli", "claude_code_cli", "llama_cpp")
    assert team.roles["product_owner"].effective == ("claude_code_cli", "codex_cli", "llama_cpp")
    assert team.roles["security"].effective == ("llama_cpp",)
    assert team.roles["technical_lead"].source == "inherited"
    assert team.roles["technical_lead"].effective == team.roles["product_owner"].effective
    assert "gemini" not in team.allowed_runtimes


def test_general_configuration_orders_the_role_and_project_overrides_it(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.developer", ["llama_cpp", "claude_code_cli"])
    _set(connection, "team.role.developer", ["codex_cli"], project_id=project["id"])
    _set(connection, "team.role.product_owner", ["llama_cpp"])

    general = resolve_global_team(connection, project_id=None, facts=FACTS)
    assert general.roles["developer"].source == "general"
    assert general.roles["developer"].effective == ("llama_cpp", "claude_code_cli")
    assert general.roles["developer"].assigned == "llama_cpp"

    project_team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert project_team.roles["developer"].source == "project"
    assert project_team.roles["developer"].effective == ("codex_cli",)
    assert project_team.roles["product_owner"].source == "general"
    # Unión en orden de roles (PO primero): llama (PO), codex (dev), claude (arquitecto automático).
    assert project_team.allowed_runtimes == ("llama_cpp", "codex_cli", "claude_code_cli")


def test_invalid_ids_are_reported_and_dropped_and_an_empty_list_means_automatic(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.security", ["claude_code_cli", "ghost", "llama_cpp"])
    _set(connection, "team.role.developer", [], project_id=project["id"])
    _set(connection, "team.role.developer", ["codex_cli"])

    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    # Claude no es elegible para security y ghost no existe: quedan reportados, no rompen.
    assert team.roles["security"].effective == ("llama_cpp",)
    assert team.roles["security"].invalid == ("claude_code_cli", "ghost")
    # La lista vacía del proyecto no es un override: hereda la general.
    assert team.roles["developer"].source == "general"
    assert team.roles["developer"].effective == ("codex_cli",)


def test_the_sealed_form_carries_orders_sources_and_the_assigned_runtime(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.developer", ["codex_cli", "llama_cpp"])
    sealed = resolve_global_team(connection, project_id=project["id"], facts=FACTS).sealed()
    assert sealed["roleRuntimeOrder"]["developer"] == ["codex_cli", "llama_cpp"]
    assert sealed["roleRuntimes"]["developer"] == "codex_cli"
    assert sealed["source"]["developer"] == "general"
    assert sealed["source"]["researcher"] == "inherited"
    assert set(sealed) == {"roleRuntimeOrder", "roleRuntimes", "source", "allowedRuntimes"}


def test_the_project_runtime_allowlist_narrows_the_global_team(lane) -> None:
    connection, project = lane
    _set(connection, "project.runtime.allowedProviders", ["llama_cpp"], project_id=project["id"])
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert team.allowed_runtimes == ("llama_cpp",)
    assert team.roles["developer"].effective == ("llama_cpp",)


def test_the_operator_runtime_order_breaks_ties_in_the_automatic_split(lane) -> None:
    connection, project = lane
    # El esquema siembra codex_cli primero; invertirlo debe invertir el reparto automático.
    RuntimeConfigRepository(connection).upsert_preferences(
        {"scope": "global", "runtimeOrder": ["claude_code_cli", "codex_cli"]}
    )
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert team.roles["developer"].effective[0] == "claude_code_cli"
    assert team.roles["product_owner"].effective[0] == "codex_cli"
