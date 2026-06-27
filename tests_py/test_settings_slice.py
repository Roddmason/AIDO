"""Tests for the settings vertical slice: migration, registry, repository, resolver, models, API.

Covers the two-tier settings store (general defaults + per-project overrides) with
inheritance resolution, Pydantic contracts, and the FastAPI routes for GET/PUT/DELETE.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path):
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


# ---------------------------------------------------------------------------
# Task 1: Phase-26 migration
# ---------------------------------------------------------------------------


def test_phase26_creates_settings_value_table(tmp_path: Path) -> None:
    runtime, _client_unused = _client(tmp_path)
    try:
        conn = runtime.connection
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings_value'"
        ).fetchone()
        assert row is not None, "settings_value table must exist after phase-26 migration"

        version_row = conn.execute("SELECT version FROM schema_migrations WHERE version=26").fetchone()
        assert version_row is not None
        assert version_row["version"] == 26
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Task 2: Registry + value validation
# ---------------------------------------------------------------------------


def test_registry_descriptor_for_known_key(tmp_path: Path) -> None:
    from local_control_center.settings.registry import REGISTRY, descriptor_for

    desc = descriptor_for("autonomy.level")
    assert desc is not None
    assert desc.default == "guided"
    assert desc.key == "autonomy.level"
    assert desc in REGISTRY


def test_registry_descriptor_for_unknown_key(tmp_path: Path) -> None:
    from local_control_center.settings.registry import descriptor_for

    assert descriptor_for("nope") is None


def test_validate_value_enum_accepts_valid(tmp_path: Path) -> None:
    from local_control_center.settings.registry import descriptor_for, validate_value

    desc = descriptor_for("autonomy.level")
    assert validate_value(desc, "guided") == "guided"
    assert validate_value(desc, "recommended") == "recommended"
    assert validate_value(desc, "autonomous") == "autonomous"


def test_validate_value_enum_rejects_invalid(tmp_path: Path) -> None:
    import pytest

    from local_control_center.settings.registry import descriptor_for, validate_value

    desc = descriptor_for("autonomy.level")
    with pytest.raises(ValueError):
        validate_value(desc, "yolo")


def test_validate_value_number_accepts_float_and_string(tmp_path: Path) -> None:
    from local_control_center.settings.registry import descriptor_for, validate_value

    desc = descriptor_for("budget.maxCostUsd")
    assert validate_value(desc, 10.5) == 10.5
    assert validate_value(desc, "10.5") == 10.5


def test_validate_value_number_rejects_non_numeric_string(tmp_path: Path) -> None:
    import pytest

    from local_control_center.settings.registry import descriptor_for, validate_value

    desc = descriptor_for("budget.maxCostUsd")
    with pytest.raises(ValueError):
        validate_value(desc, "abc")


# ---------------------------------------------------------------------------
# Task 3: SettingsRepository
# ---------------------------------------------------------------------------


def test_repository_round_trip(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.repository import SettingsRepository

        repo = SettingsRepository(runtime.connection)
        repo.set_value("autonomy.level", "general", None, "recommended")
        val = repo.get_value("autonomy.level", "general", None)
        assert val == "recommended"
    finally:
        runtime.close()


def test_repository_unset_key_returns_sentinel(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.repository import UNSET, SettingsRepository

        repo = SettingsRepository(runtime.connection)
        val = repo.get_value("autonomy.level", "general", None)
        assert val is UNSET
    finally:
        runtime.close()


def test_repository_upsert_does_not_error(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.repository import SettingsRepository

        repo = SettingsRepository(runtime.connection)
        repo.set_value("autonomy.level", "general", None, "recommended")
        repo.set_value("autonomy.level", "general", None, "autonomous")
        val = repo.get_value("autonomy.level", "general", None)
        assert val == "autonomous"
    finally:
        runtime.close()


def test_repository_clear_value(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.repository import UNSET, SettingsRepository

        repo = SettingsRepository(runtime.connection)
        repo.set_value("autonomy.level", "general", None, "recommended")
        existed = repo.clear_value("autonomy.level", "general", None)
        assert existed is True
        val = repo.get_value("autonomy.level", "general", None)
        assert val is UNSET
        not_existed = repo.clear_value("autonomy.level", "general", None)
        assert not_existed is False
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Task 4: Pure resolver
# ---------------------------------------------------------------------------


def test_resolver_default_when_nothing_set(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.resolver import resolve_settings

        result = resolve_settings(connection=runtime.connection, project_id="proj-1")
        general = {item["key"]: item for item in result["general"]}
        entry = general["autonomy.level"]
        assert entry["value"] == "guided"
        assert entry["origin"] == "default"
        assert entry["inherited"] is False
        assert entry["source"] == "default"
    finally:
        runtime.close()


def test_resolver_general_override_propagates_to_project_as_inherited(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.repository import SettingsRepository
        from local_control_center.settings.resolver import resolve_settings

        repo = SettingsRepository(runtime.connection)
        repo.set_value("autonomy.level", "general", None, "recommended")

        result = resolve_settings(connection=runtime.connection, project_id="proj-1")
        general = {item["key"]: item for item in result["general"]}
        assert general["autonomy.level"]["value"] == "recommended"
        assert general["autonomy.level"]["origin"] == "general"
        assert general["autonomy.level"]["source"] == "general"
        assert general["autonomy.level"]["inherited"] is False

        project = {item["key"]: item for item in result["project"]}
        assert project["autonomy.level"]["value"] == "recommended"
        assert project["autonomy.level"]["origin"] == "general"
        assert project["autonomy.level"]["inherited"] is True
        assert project["autonomy.level"]["source"] == "general"
    finally:
        runtime.close()


def test_resolver_project_override_wins(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.repository import SettingsRepository
        from local_control_center.settings.resolver import resolve_settings

        repo = SettingsRepository(runtime.connection)
        repo.set_value("autonomy.level", "general", None, "recommended")
        repo.set_value("autonomy.level", "project", "proj-1", "autonomous")

        result = resolve_settings(connection=runtime.connection, project_id="proj-1")
        project = {item["key"]: item for item in result["project"]}
        assert project["autonomy.level"]["value"] == "autonomous"
        assert project["autonomy.level"]["origin"] == "project"
        assert project["autonomy.level"]["inherited"] is False
        assert project["autonomy.level"]["source"] == "project"
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Task 5: Pydantic contracts
# ---------------------------------------------------------------------------


def test_models_settings_response_round_trips(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.models import SettingsResponse
        from local_control_center.settings.resolver import resolve_settings

        raw = resolve_settings(connection=runtime.connection, project_id="proj-1")
        resp = SettingsResponse(**raw)
        dumped = resp.model_dump(by_alias=True)
        assert "general" in dumped
        assert "project" in dumped
        # Check camelCase keys in first general entry
        first = dumped["general"][0]
        assert "key" in first
        assert "origin" in first
        assert "inherited" in first
        assert "source" in first
        assert "editableScopes" in first
    finally:
        runtime.close()


def test_models_set_setting_request_optional_scope_id(tmp_path: Path) -> None:
    from local_control_center.settings.models import SetSettingRequest

    req = SetSettingRequest.model_validate({"scope": "general", "value": "guided"})
    assert req.scope == "general"
    assert req.scope_id is None
    assert req.value == "guided"


# ---------------------------------------------------------------------------
# Task 6: API router
# ---------------------------------------------------------------------------


def test_api_get_settings_returns_general_and_project(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        resp = client.get("/api/v1/settings?projectId=proj-abc")
        assert resp.status_code == 200
        body = resp.json()
        assert "general" in body
        assert "project" in body
        general_keys = [item["key"] for item in body["general"]]
        assert "autonomy.level" in general_keys
        default_entry = next(item for item in body["general"] if item["key"] == "autonomy.level")
        assert default_entry["value"] == "guided"
    finally:
        runtime.close()


def test_api_put_setting_general_and_get_reflects_change(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        resp = client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "general", "value": "recommended"},
            headers={"X-Local-Control-Token": token},
        )
        assert resp.status_code == 204

        body = client.get("/api/v1/settings?projectId=proj-abc").json()
        general = {item["key"]: item for item in body["general"]}
        project = {item["key"]: item for item in body["project"]}
        assert general["autonomy.level"]["value"] == "recommended"
        assert project["autonomy.level"]["inherited"] is True
    finally:
        runtime.close()


def test_api_put_setting_project_override(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        pid = "proj-abc"
        client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "project", "scopeId": pid, "value": "autonomous"},
            headers={"X-Local-Control-Token": token},
        )
        body = client.get(f"/api/v1/settings?projectId={pid}").json()
        project = {item["key"]: item for item in body["project"]}
        assert project["autonomy.level"]["origin"] == "project"
        assert project["autonomy.level"]["inherited"] is False
    finally:
        runtime.close()


def test_api_delete_setting_reverts_to_inherited(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        pid = "proj-abc"
        client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "project", "scopeId": pid, "value": "autonomous"},
            headers={"X-Local-Control-Token": token},
        )
        del_resp = client.delete(
            f"/api/v1/settings/autonomy.level?scope=project&scopeId={pid}",
            headers={"X-Local-Control-Token": token},
        )
        assert del_resp.status_code == 204

        body = client.get(f"/api/v1/settings?projectId={pid}").json()
        project = {item["key"]: item for item in body["project"]}
        assert project["autonomy.level"]["inherited"] is True
    finally:
        runtime.close()


def test_api_put_invalid_value_returns_422(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        resp = client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "general", "value": "yolo"},
            headers={"X-Local-Control-Token": token},
        )
        assert resp.status_code == 422
    finally:
        runtime.close()


def test_api_put_without_token_returns_403(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        resp = client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "general", "value": "guided"},
        )
        assert resp.status_code == 403
    finally:
        runtime.close()


def test_api_put_unknown_key_returns_404(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        resp = client.put(
            "/api/v1/settings/nope.key",
            json={"scope": "general", "value": "anything"},
            headers={"X-Local-Control-Token": token},
        )
        assert resp.status_code == 404
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Fix review: auth-first, DELETE 404, scope validation, string type guard
# ---------------------------------------------------------------------------


def test_api_delete_without_token_returns_403(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        resp = client.delete("/api/v1/settings/autonomy.level?scope=general")
        assert resp.status_code == 403
    finally:
        runtime.close()


def test_api_delete_unknown_key_returns_404(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        resp = client.delete(
            "/api/v1/settings/nope.key?scope=general",
            headers={"X-Local-Control-Token": token},
        )
        assert resp.status_code == 404
    finally:
        runtime.close()


def test_api_put_project_scope_missing_scope_id_returns_422(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        # scope="project" with no scopeId in body
        resp = client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "project", "value": "autonomous"},
            headers={"X-Local-Control-Token": token},
        )
        assert resp.status_code == 422
        # scope="project" with empty string scopeId
        resp2 = client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "project", "scopeId": "", "value": "autonomous"},
            headers={"X-Local-Control-Token": token},
        )
        assert resp2.status_code == 422
    finally:
        runtime.close()


def test_api_put_invalid_scope_returns_422(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = runtime.get_handshake()["token"]
        resp = client.put(
            "/api/v1/settings/autonomy.level",
            json={"scope": "workspace", "value": "guided"},
            headers={"X-Local-Control-Token": token},
        )
        assert resp.status_code == 422
    finally:
        runtime.close()


def test_validate_value_string_rejects_non_string(tmp_path: Path) -> None:
    import pytest

    from local_control_center.settings.registry import descriptor_for, validate_value

    desc = descriptor_for("security.sandboxProfileId")
    assert desc is not None
    # valid: empty string is allowed
    assert validate_value(desc, "") == ""
    assert validate_value(desc, "my-profile") == "my-profile"
    # invalid: dict, list, number
    with pytest.raises(ValueError):
        validate_value(desc, {"key": "val"})
    with pytest.raises(ValueError):
        validate_value(desc, ["a", "b"])
    with pytest.raises(ValueError):
        validate_value(desc, 42)


# ---------------------------------------------------------------------------
# C-1: resolver emits labelKey; Pydantic model round-trips it
# ---------------------------------------------------------------------------


def test_resolver_emits_label_key(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.resolver import resolve_settings

        result = resolve_settings(connection=runtime.connection, project_id="proj-1")
        general = {item["key"]: item for item in result["general"]}
        assert general["autonomy.level"]["labelKey"] == "app.settings.autonomy.level"
        assert general["security.sandboxProfileId"]["labelKey"] == "app.settings.security.sandboxProfile"
        assert general["budget.maxCostUsd"]["labelKey"] == "app.settings.budget.maxCostUsd"
    finally:
        runtime.close()


def test_models_resolved_setting_round_trips_label_key(tmp_path: Path) -> None:
    runtime, _c = _client(tmp_path)
    try:
        from local_control_center.settings.models import SettingsResponse
        from local_control_center.settings.resolver import resolve_settings

        raw = resolve_settings(connection=runtime.connection, project_id="proj-1")
        resp = SettingsResponse(**raw)
        dumped = resp.model_dump(by_alias=True)
        first = next(s for s in dumped["general"] if s["key"] == "autonomy.level")
        assert first["labelKey"] == "app.settings.autonomy.level"
    finally:
        runtime.close()


def test_api_get_settings_includes_label_key(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        resp = client.get("/api/v1/settings?projectId=proj-abc")
        assert resp.status_code == 200
        body = resp.json()
        entry = next(s for s in body["general"] if s["key"] == "autonomy.level")
        assert entry["labelKey"] == "app.settings.autonomy.level"
    finally:
        runtime.close()
