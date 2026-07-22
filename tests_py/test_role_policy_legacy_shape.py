"""Las role policies con formato legacy (strings) no deben reventar el listado.

Una fila vieja de ``role_model_policies`` guardaba ``preferred_json`` como lista de strings
(ids de provider). El response model del gateway exige ``list[dict]``, así que UNA fila legacy
devolvía 500 ResponseValidationError en ``GET /role-policies`` y vaciaba el panel de la UI
(cascada del ``Promise.all``). El mapper debe normalizar el formato viejo, no propagarlo.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps


def test_legacy_string_model_refs_are_normalized_to_dicts(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = RoutingProfileStore(connection)
        store.upsert_role_policy({"role": "aido_lead", "preferred": [], "fallback": []})
        connection.execute(
            "UPDATE role_model_policies SET preferred_json = ?, fallback_json = ? WHERE role = 'aido_lead'",
            (json_dumps(["claude_code_cli", "codex_cli"]), json_dumps(["ollama"])),
        )

        policies = store.list_role_policies()

    lead = next(p for p in policies if p["role"] == "aido_lead")
    assert all(isinstance(item, dict) for item in lead["preferred"]), lead["preferred"]
    assert lead["preferred"][0]["provider"] == "claude_code_cli"
    assert "model" in lead["preferred"][0]
    assert all(isinstance(item, dict) for item in lead["fallback"])
    # Las filas ya en formato nuevo (dicts) pasan intactas.
    store_rows = [p for p in policies if p["role"] != "aido_lead"]
    for p in store_rows:
        assert all(isinstance(item, dict) for item in p["preferred"] + p["fallback"])
