from __future__ import annotations

import json

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository


def test_compatibility_migration_preserves_operator_models_and_converts_legacy_profiles(tmp_path):
    from local_control_center.shared.migrations import init_phase67_schema

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        connection = runtime.connection
        connection.execute("DELETE FROM schema_migrations WHERE version=67")
        connection.execute("UPDATE model_catalog SET enabled=1 WHERE source='manual_seed'")
        models = ProviderAccountStore(connection)
        models.patch_model("codex_cli:gpt-5.5", {"enabled": True})
        RuntimeConfigRepository(connection).upsert_preferences(
            {
                "scope": "global",
                "defaultProfiles": {
                    "developer": "codex_gpt55_developer",
                    "reviewer": "claude_sonnet_qa",
                    "permissionProfile": "plan",
                    "custom": "operator_profile",
                },
            }
        )
        init_phase67_schema(connection)
        init_phase67_schema(connection)
        assert models.get_model("codex_cli:gpt-5.5")["enabled"] is True
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM model_catalog WHERE provider_id IN ('codex_cli', 'claude_code_cli') AND source='manual_seed' AND enabled=1"
            ).fetchone()[0]
            == 0
        )
        profiles = json.loads(
            connection.execute(
                "SELECT default_profiles FROM runtime_preferences WHERE scope='global'"
            ).fetchone()[0]
        )
        assert profiles == {
            "developer": "deep_coding",
            "reviewer": "review",
            "permissionProfile": "plan",
            "custom": "operator_profile",
        }
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        runtime.close()
