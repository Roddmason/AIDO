from __future__ import annotations

from pathlib import Path

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.team_bootstrap import bootstrap_base_team_if_needed
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.team_scheduler.scheduler import ALL_ROLES


def test_a_runtime_singleton_no_longer_suppresses_its_base_profile(tmp_path: Path) -> None:
    """El guard por rol borraba del roster al perfil base cuyo rol ya ocupaba un singleton."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = AgentsRepository(connection)
        repository.upsert_agent_profile(
            {
                "id": "product_owner_agent",
                "name": "Product Owner Agent",
                "role": "product_owner",
                "runtimeMode": "api",
                "allowedProviders": ["gemini"],
            }
        )

        bootstrap_base_team_if_needed(connection)

        profiles = {str(item["id"]): item for item in repository.list_agent_profiles()}

    assert "base-product-owner" in profiles, "the base profile must exist alongside the singleton"
    # The pre-existing singleton is left exactly as the operator/runtime registered it.
    assert profiles["product_owner_agent"]["allowedProviders"] == ["gemini"]


def test_every_scheduler_role_gets_a_model_policy(tmp_path: Path) -> None:
    """Un rol sin política caía al fallback silencioso a developer, enrutando a otro perfil."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        bootstrap_base_team_if_needed(connection)
        roles = {str(policy["role"]) for policy in RoutingProfileStore(connection).list_role_policies()}

    assert set(ALL_ROLES) <= roles


def test_bootstrap_does_not_overwrite_an_operator_edited_policy(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = RoutingProfileStore(connection)
        store.upsert_role_policy({"role": "qa_engineer", "preferred": ["operator-choice"]})

        bootstrap_base_team_if_needed(connection)
        bootstrap_base_team_if_needed(connection)  # idempotent

        policy = store.get_role_policy("qa_engineer")

    assert policy["preferred"] == ["operator-choice"]
