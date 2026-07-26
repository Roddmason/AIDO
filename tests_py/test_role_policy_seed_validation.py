"""Toda política de rol sembrada por migraciones debe pasar el validador de la API.

El seed de fase 12 creó ``technical_lead`` con ``maxTokensPerRun = 240000`` mientras el
validador del gateway rechazaba valores sobre 200000. Como el PATCH revalida el estado
fusionado (existente + payload), la política quedó inmodificable por API y por la UI:
cualquier edición parcial devolvía 422 aunque el cliente no tocara ese campo. Este test
pasa cada fila sembrada de ``role_model_policies`` por ``_validate_role_policy_payload``
para que un seed fuera de rango nunca vuelva a bloquear la edición de una política.

@author Rodrigo Mason
"""

from __future__ import annotations

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
