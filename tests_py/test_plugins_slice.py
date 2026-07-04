from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.api import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.plugins.manifest import permission_risk_level


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token}


def sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def write_manifest(plugin_dir: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    skill_path = plugin_dir / "skills" / "example" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        "---\n"
        "name: example-plugin-skill\n"
        "description: Test skill contract.\n"
        "---\n\n"
        "# Example Skill\n\n"
        "Contract body.\n",
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {
        "id": "example.plugin",
        "name": "Example Plugin",
        "version": "1.0.0",
        "publisher": "Acme",
        "trustLevel": "third_party",
        "capabilities": ["skill:example", "tool:example"],
        "permissions": ["workspace.read"],
        "entrypoints": {
            "skills": [{"id": "example-skill", "path": "skills/example/SKILL.md"}],
            "agents": [
                {
                    "id": "example-agent",
                    "role": "reviewer",
                    "capabilities": ["code_review"],
                    "schema": {"type": "object", "properties": {}},
                }
            ],
            "tools": [
                {
                    "id": "example-tool",
                    "name": "Example Tool",
                    "brokerTool": "shell",
                    "execute": False,
                    "schema": {"type": "object", "properties": {}},
                    "policy": {"decision": "deny_by_default"},
                }
            ],
        },
        "checksums": {"skills/example/SKILL.md": sha256_file(skill_path)},
        "minAidoVersion": "0.1.0",
    }
    if overrides:
        manifest.update(overrides)
    (plugin_dir / "aido.plugin.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def plugin_rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f"SELECT * FROM {table}").fetchall()


def test_valid_local_plugin_installs_and_persists_formal_contract(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    headers = auth_headers(client)
    plugin_dir = tmp_path / "plugins" / "example"
    manifest = write_manifest(plugin_dir)

    response = client.post(
        "/api/v1/plugins/install-local",
        json={"path": str(plugin_dir)},
        headers=headers,
    )

    assert response.status_code == 201
    plugin = response.json()["plugin"]
    assert plugin["id"] == manifest["id"]
    assert plugin["status"] == "disabled"
    assert plugin["trustLevel"] == "third_party"
    assert plugin["activeVersion"]["manifestHash"].startswith("sha256:")

    listed = client.get("/api/v1/plugins").json()["plugins"]
    assert [item["id"] for item in listed] == ["example.plugin"]

    validation = client.post(
        "/api/v1/plugins/example.plugin/validate",
        headers=headers,
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True

    table_names = {
        row["name"]
        for row in runtime.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "plugins",
        "plugin_versions",
        "plugin_permissions",
        "plugin_skills",
        "plugin_agents",
        "plugin_tools",
        "plugin_install_events",
    }.issubset(table_names)
    assert plugin_rows(runtime.connection, "plugin_permissions")[0]["permission"] == "workspace.read"
    assert plugin_rows(runtime.connection, "plugin_skills")[0]["contract_hash"].startswith("sha256:")
    assert plugin_rows(runtime.connection, "plugin_agents")[0]["role"] == "reviewer"
    assert plugin_rows(runtime.connection, "plugin_tools")[0]["broker_tool"] == "shell"
    assert plugin_rows(runtime.connection, "plugin_tools")[0]["policy_required"] == 1
    assert plugin_rows(runtime.connection, "plugin_install_events")[0]["action"] == "install_local"


def test_dangerous_plugin_permissions_are_blocked(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    headers = auth_headers(client)
    plugin_dir = tmp_path / "plugins" / "dangerous"
    write_manifest(plugin_dir, {"permissions": ["filesystem.write:/"]})

    response = client.post(
        "/api/v1/plugins/install-local",
        json={"path": str(plugin_dir)},
        headers=headers,
    )

    assert response.status_code == 422
    assert "dangerous" in response.json()["detail"]
    assert plugin_rows(runtime.connection, "plugins") == []
    event = plugin_rows(runtime.connection, "plugin_install_events")[0]
    assert event["action"] == "install_local"
    assert event["status"] == "blocked"


def test_plugin_enable_generates_audit_event(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    headers = auth_headers(client)
    plugin_dir = tmp_path / "plugins" / "example"
    write_manifest(plugin_dir)
    install = client.post("/api/v1/plugins/install-local", json={"path": str(plugin_dir)}, headers=headers)
    assert install.status_code == 201

    response = client.post("/api/v1/plugins/example.plugin/enable", headers=headers)

    assert response.status_code == 202
    assert response.json()["plugin"]["status"] == "enabled"
    audits = runtime.connection.execute(
        "SELECT * FROM audit_events WHERE action = 'plugin.enable'"
    ).fetchall()
    assert len(audits) == 1
    assert audits[0]["target"] == "example.plugin"
    assert plugin_rows(runtime.connection, "plugin_install_events")[-1]["action"] == "enable"


def test_executable_plugin_tool_is_blocked_without_policy(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    headers = auth_headers(client)
    plugin_dir = tmp_path / "plugins" / "unsafe-tool"
    write_manifest(
        plugin_dir,
        {
            "entrypoints": {
                "skills": [{"id": "example-skill", "path": "skills/example/SKILL.md"}],
                "agents": [
                    {
                        "id": "example-agent",
                        "role": "reviewer",
                        "capabilities": ["code_review"],
                        "schema": {"type": "object", "properties": {}},
                    }
                ],
                "tools": [
                    {
                        "id": "unsafe-tool",
                        "name": "Unsafe Tool",
                        "brokerTool": "shell",
                        "execute": True,
                        "schema": {"type": "object", "properties": {}},
                    }
                ],
            }
        },
    )

    response = client.post(
        "/api/v1/plugins/install-local",
        json={"path": str(plugin_dir)},
        headers=headers,
    )

    assert response.status_code == 422
    assert "policy" in response.json()["detail"]
    assert plugin_rows(runtime.connection, "agent_tool_calls") == []
    event = plugin_rows(runtime.connection, "plugin_install_events")[0]
    assert event["status"] == "blocked"


def test_permission_risk_level_classifies_read_and_mutation_scopes() -> None:
    # Read-only scopes stay low; exact-token matching keeps plural nouns off the verb list.
    assert permission_risk_level("workspace.read") == "low"
    assert permission_risk_level("catalog.list") == "low"
    assert permission_risk_level("config.read") == "low"
    assert permission_risk_level("runs.read") == "low"
    # Mutation or egress capable scopes read as elevated so the console can flag them,
    # regardless of whether the verb sits in the prefix, an inner token, or before a target.
    assert permission_risk_level("filesystem.write:reports") == "medium"
    assert permission_risk_level("network.http:api.example.com") == "medium"
    assert permission_risk_level("process.status") == "medium"
    assert permission_risk_level("exec.command") == "medium"
    assert permission_risk_level("command.run") == "medium"
    assert permission_risk_level("spawn.subprocess") == "medium"
    assert permission_risk_level("fetch.url") == "medium"
    assert permission_risk_level("filesystem.remove:reports") == "medium"
    assert permission_risk_level("http.post:https://example.com") == "medium"
    assert permission_risk_level("filesystem.write:/") == "high"


def test_installed_permissions_persist_classified_risk_levels(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    headers = auth_headers(client)
    plugin_dir = tmp_path / "plugins" / "elevated"
    # filesystem.write:reports is permitted (scoped) but mutation-capable, so it must
    # persist as an elevated risk the operator can review before enabling.
    write_manifest(plugin_dir, {"permissions": ["workspace.read", "filesystem.write:reports"]})

    response = client.post(
        "/api/v1/plugins/install-local",
        json={"path": str(plugin_dir)},
        headers=headers,
    )

    assert response.status_code == 201
    persisted = {
        row["permission"]: row["risk_level"] for row in plugin_rows(runtime.connection, "plugin_permissions")
    }
    assert persisted["workspace.read"] == "low"
    assert persisted["filesystem.write:reports"] == "medium"
