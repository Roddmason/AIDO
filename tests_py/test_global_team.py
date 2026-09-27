"""Equipo de IA global: settings por rol, resolución sobre proveedores activos y sellado.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

from local_control_center.settings.registry import GLOBAL_TEAM_ROLE_KEYS, descriptor_for, validate_value

CATALOG = Path("local_control_center/i18n/default_catalog.json")


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
