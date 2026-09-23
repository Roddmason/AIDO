"""Equipo de runtimes guardado en el hilo: validación, estrechamiento, sellado y readiness.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_team.configuration import (
    RUNTIME_TEAM_DISCARDED_METADATA_KEY,
    RUNTIME_TEAM_METADATA_KEY,
    RuntimeTeamNotReadyError,
    assess_runtime_team,
    assigned_runtime,
    ensure_thread_runtime_team_ready,
    read_thread_runtime_team,
    restrict_to_allowlist,
    role_allowlist,
    runtime_team_of,
    seal_thread_runtime_team,
    write_thread_runtime_team,
)
from local_control_center.runtime_team.validation import RUNTIME_TEAM_FRESHNESS_SECONDS
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.threads.repository import ThreadsRepository

ROLES = {"developer": "codex_cli", "product_owner": "ollama", "architect": "ollama", "security": "ollama"}


def grant_review_capability(connection, runtime_id: str) -> None:
    """Ollama solo es elegible para Seguridad si anuncia una capacidad de revisión (rol ``review``)."""
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """INSERT INTO runtime_capabilities (id, runtime, capability, enabled, metadata, created_at, updated_at)
           VALUES (?, ?, 'code_review', 1, '{}', ?, ?)
           ON CONFLICT(runtime, capability) DO UPDATE SET enabled = 1""",
        (f"{runtime_id}:code_review", runtime_id, now, now),
    )


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Runtime team", path=tmp_path / "project", template_id="other"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Team"
        )
        store = ProviderAccountStore(connection)
        store.patch_provider_account("codex_cli", {"enabled": True})
        store.patch_provider_account("ollama", {"enabled": True})
        grant_review_capability(connection, "ollama")
        yield connection, project, thread


def _save(connection, project, thread, roles=ROLES):
    return write_thread_runtime_team(
        connection,
        thread_id=thread["id"],
        project_id=project["id"],
        allowed_runtimes=["codex_cli", "ollama"],
        role_runtimes=roles,
    )


def _validate(connection, provider_id: str, model: str, minutes_ago: int = 1) -> None:
    started = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="microseconds")
    record_model_execution(connection, provider_id, model, True, "test_prompt", started_at=started)


def test_a_saved_team_is_read_back_and_keeps_the_remembered_team_mode(lane):
    connection, project, thread = lane
    connection.execute(
        "UPDATE project_threads SET metadata = ? WHERE id = ?",
        (json_dumps({"runConfiguration": {"teamMode": "critical"}}), thread["id"]),
    )
    saved = _save(connection, project, thread)
    assert saved == {"allowedRuntimes": ["codex_cli", "ollama"], "roleRuntimes": ROLES}
    assert read_thread_runtime_team(connection, thread["id"]) == saved
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread["id"],)).fetchone()
    assert json_loads(row["metadata"], {})["runConfiguration"]["teamMode"] == "critical"


def test_an_empty_selection_clears_the_team(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    cleared = write_thread_runtime_team(
        connection, thread_id=thread["id"], project_id=project["id"], allowed_runtimes=[], role_runtimes={}
    )
    assert cleared is None
    assert read_thread_runtime_team(connection, thread["id"]) is None


@pytest.mark.parametrize(
    ("allowed", "roles", "message"),
    [
        (["claude_code_cli"], {}, "claude_code_cli"),
        (["codex_cli"], {"product_owner": "ollama"}, "not selected"),
        (["codex_cli", "ollama"], {"security": "codex_cli"}, "not eligible"),
    ],
)
def test_an_invalid_team_is_rejected(lane, allowed, roles, message):
    connection, project, thread = lane
    with pytest.raises(ValueError, match=message):
        write_thread_runtime_team(
            connection,
            thread_id=thread["id"],
            project_id=project["id"],
            allowed_runtimes=allowed,
            role_runtimes=roles,
        )


def test_an_unknown_thread_is_a_key_error(lane):
    connection, project, _thread = lane
    with pytest.raises(KeyError):
        write_thread_runtime_team(
            connection,
            thread_id="thread-missing",
            project_id=project["id"],
            allowed_runtimes=[],
            role_runtimes={},
        )


def test_the_project_allowlist_can_only_narrow_the_sealed_team(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default")
    SettingsRepository(connection).set_value(
        "project.runtime.allowedProviders", "project", project["id"], ["codex_cli"]
    )
    sealed = seal_thread_runtime_team(
        connection,
        project_id=project["id"],
        thread_id=thread["id"],
        metadata={RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": ["claude_code_cli"]}},
    )
    assert sealed[RUNTIME_TEAM_METADATA_KEY] == {
        "allowedRuntimes": ["codex_cli"],
        "roleRuntimes": {"developer": "codex_cli"},
    }
    assert [(item["providerId"], item["status"]) for item in sealed[RUNTIME_TEAM_DISCARDED_METADATA_KEY]] == [
        ("ollama", "policy_denied")
    ]


def test_sealing_without_a_team_drops_any_forged_value(lane):
    connection, project, thread = lane
    sealed = seal_thread_runtime_team(
        connection,
        project_id=project["id"],
        thread_id=thread["id"],
        metadata={
            RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": ["claude_code_cli"]},
            RUNTIME_TEAM_DISCARDED_METADATA_KEY: [{"providerId": "codex_cli"}],
            "teamMode": "economy",
        },
    )
    assert sealed == {"teamMode": "economy"}


def test_the_send_gate_requires_a_fresh_runtime_for_po_and_developer(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    with pytest.raises(RuntimeTeamNotReadyError, match="codex_cli"):
        ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default", minutes_ago=31)
    with pytest.raises(RuntimeTeamNotReadyError, match=r"product_owner.*ollama"):
        ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    _validate(connection, "ollama", "local_default")
    ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])


def test_a_stale_optional_runtime_is_dropped_from_the_sealed_team_instead_of_blocking(lane):
    connection, project, thread = lane
    _save(
        connection,
        project,
        thread,
        roles={"developer": "codex_cli", "product_owner": "codex_cli", "security": "ollama"},
    )
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default", minutes_ago=31)
    ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    sealed = seal_thread_runtime_team(
        connection, project_id=project["id"], thread_id=thread["id"], metadata={}
    )
    assert sealed[RUNTIME_TEAM_METADATA_KEY] == {
        "allowedRuntimes": ["codex_cli"],
        "roleRuntimes": {"product_owner": "codex_cli", "developer": "codex_cli"},
    }
    assert sealed[RUNTIME_TEAM_DISCARDED_METADATA_KEY] == [
        {"providerId": "ollama", "status": "stale", "reason": "runtime_validation_expired"}
    ]


def test_a_reseal_that_would_leave_po_or_developer_uncovered_keeps_the_assigned_runtimes(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    _validate(connection, "codex_cli", "gpt-5.5", minutes_ago=31)
    _validate(connection, "ollama", "local_default", minutes_ago=31)
    sealed = seal_thread_runtime_team(
        connection, project_id=project["id"], thread_id=thread["id"], metadata={}
    )
    assert sealed[RUNTIME_TEAM_METADATA_KEY] == {
        "allowedRuntimes": ["codex_cli", "ollama"],
        "roleRuntimes": ROLES,
    }
    assert RUNTIME_TEAM_DISCARDED_METADATA_KEY not in sealed


def test_a_team_narrowed_to_nothing_by_the_project_cannot_send(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default")
    SettingsRepository(connection).set_value(
        "project.runtime.allowedProviders", "project", project["id"], ["claude_code_cli"]
    )
    with pytest.raises(RuntimeTeamNotReadyError, match="product_owner, developer"):
        ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    sealed = seal_thread_runtime_team(
        connection, project_id=project["id"], thread_id=thread["id"], metadata={}
    )
    assert sealed[RUNTIME_TEAM_METADATA_KEY] == {"allowedRuntimes": [], "roleRuntimes": {}}


def test_a_missing_required_role_blocks_even_with_fresh_runtimes(lane):
    connection, project, thread = lane
    _save(connection, project, thread, roles={"developer": "codex_cli"})
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default")
    readiness = assess_runtime_team(
        connection,
        read_thread_runtime_team(connection, thread["id"]),
        max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
    )
    assert readiness.missing_roles == ("product_owner",)
    assert readiness.ready is False
    assert readiness.details()["runtimeIds"] == []


def test_a_single_runtime_covering_po_and_developer_can_send(lane):
    connection, project, thread = lane
    write_thread_runtime_team(
        connection,
        thread_id=thread["id"],
        project_id=project["id"],
        allowed_runtimes=["codex_cli"],
        role_runtimes={"developer": "codex_cli", "product_owner": "codex_cli"},
    )
    _validate(connection, "codex_cli", "gpt-5.5")
    readiness = assess_runtime_team(
        connection,
        read_thread_runtime_team(connection, thread["id"]),
        max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
    )
    assert readiness.missing_roles == ()
    assert readiness.ready is True
    ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])


def test_a_runtime_denied_by_the_project_policy_cannot_be_selected(lane):
    connection, project, thread = lane
    SettingsRepository(connection).set_value(
        "project.runtime.allowedProviders", "project", project["id"], ["ollama"]
    )
    with pytest.raises(ValueError, match="denied by the project runtime policy: codex_cli"):
        _save(connection, project, thread)
    assert read_thread_runtime_team(connection, thread["id"]) is None


def test_the_execution_scope_only_checks_runtimes_with_a_role(lane):
    connection, _project, _thread = lane
    team = {
        "allowedRuntimes": ["codex_cli", "ollama"],
        "roleRuntimes": dict.fromkeys(("product_owner", "developer", "architect", "security"), "codex_cli"),
    }
    _validate(connection, "codex_cli", "gpt-5.5")
    selected = assess_runtime_team(connection, team, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS)
    assigned = assess_runtime_team(
        connection, team, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS, only_assigned=True
    )
    assert selected.details()["runtimeIds"] == ["ollama"]
    assert assigned.ready is True


def test_request_meta_helpers_scope_each_role():
    meta = {
        RUNTIME_TEAM_METADATA_KEY: {
            "allowedRuntimes": ["codex_cli", "ollama"],
            "roleRuntimes": {"developer": "codex_cli"},
        }
    }
    assert runtime_team_of({}) is None
    assert role_allowlist({}, "developer") is None
    assert role_allowlist(meta, "developer") == ["codex_cli"]
    assert role_allowlist(meta, "architect") == ["codex_cli", "ollama"]
    assert role_allowlist(meta, None) == ["codex_cli", "ollama"]
    assert assigned_runtime(meta, "security") is None
    assert restrict_to_allowlist(["claude_code_cli", "codex_cli", "ollama"], ["ollama"]) == ["ollama"]
    assert restrict_to_allowlist(["codex_cli"], None) == ["codex_cli"]
    narrowed_to_nothing = {RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": [], "roleRuntimes": {}}}
    assert role_allowlist(narrowed_to_nothing, "developer") == []
