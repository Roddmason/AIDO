"""Toda política de rol sembrada por migraciones debe pasar el validador de la API.

El seed de fase 12 creó ``technical_lead`` con ``maxTokensPerRun = 240000`` mientras el
validador del gateway rechazaba valores sobre 200000. Como el PATCH revalida el estado
fusionado (existente + payload), la política quedó inmodificable por API y por la UI:
cualquier edición parcial devolvía 422 aunque el cliente no tocara ese campo. Este test
pasa cada fila sembrada de ``role_model_policies`` por ``_validate_role_policy_payload``
para que un seed fuera de rango nunca vuelva a bloquear la edición de una política, y
ancla la misma invariante por la ruta HTTP real (PATCH vacío por política sembrada más
``maxTokensPerRun=0`` como valor legítimo de "sin límite").

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

from local_control_center.agents.model_gateway_api import _validate_role_policy_payload
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def test_seeded_role_policies_pass_gateway_validator(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        provider_ids = {
            item["providerId"] for item in ProviderAccountStore(connection).list_provider_accounts()
        }
        policies = RoutingProfileStore(connection).list_role_policies()

    assert policies, "el seed debe crear políticas de rol"
    failures: list[str] = []
    for policy in policies:
        try:
            _validate_role_policy_payload(policy, provider_ids=provider_ids)
        except HTTPException as error:
            failures.append(f"{policy['id']}: {error.detail}")
    assert failures == []


def test_every_seeded_role_policy_accepts_a_noop_patch_via_the_api(tmp_path: Path) -> None:
    sys.modules["faiss"] = None

    from fastapi.testclient import TestClient

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        client = TestClient(create_app(runtime=runtime, static_dir=None))
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token}

        policies = client.get("/api/v1/model-gateway/role-policies").json()["rolePolicies"]
        assert policies
        failures: list[str] = []
        for policy in policies:
            response = client.patch(
                f"/api/v1/model-gateway/role-policies/{policy['id']}", json={}, headers=headers
            )
            if response.status_code != 200:
                failures.append(f"{policy['id']}: {response.status_code} {response.text}")
        assert failures == []

        # 0 es un valor legítimo del backend ("sin límite": el router lo convierte a None);
        # ninguna capa debe imponer un piso mayor.
        unlimited = client.patch(
            "/api/v1/model-gateway/role-policies/analyst",
            json={"maxTokensPerRun": 0},
            headers=headers,
        )
        assert unlimited.status_code == 200, unlimited.text
        assert unlimited.json()["rolePolicy"]["maxTokensPerRun"] == 0
    finally:
        runtime.close()
