"""SQLite repository for the formal plugin catalog and install audit ledger.

Each write method runs as an atomic transaction on the shared platform connection:
install/upsert, status flips, and install events commit together or not at all, so a
failed manifest validation can never leave a partially persisted plugin version.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.plugins.manifest import ValidatedPluginManifest
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def _row_to_permission(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "permission": row["permission"],
        "riskLevel": row["risk_level"],
        "status": row["status"],
        "reason": row["reason"],
        "createdAt": row["created_at"],
    }


def _row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["skill_id"],
        "path": row["path"],
        "contractHash": row["contract_hash"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def _row_to_agent(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["agent_id"],
        "role": row["role"],
        "capabilities": json_loads(row["capabilities_json"], []),
        "schema": json_loads(row["schema_json"], {}),
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def _row_to_install_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "pluginId": row["plugin_id"],
        "pluginVersionId": row["plugin_version_id"],
        "action": row["action"],
        "status": row["status"],
        "reason": row["reason"],
        "manifestHash": row["manifest_hash"],
        "payload": json_loads(row["payload_json"], {}),
        "createdAt": row["created_at"],
    }


def _row_to_tool(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["tool_id"],
        "name": row["name"],
        "brokerTool": row["broker_tool"],
        "policyRequired": bool(row["policy_required"]),
        "policy": json_loads(row["policy_json"], None),
        "schema": json_loads(row["schema_json"], {}),
        "status": row["status"],
        "createdAt": row["created_at"],
    }


class PluginsRepository:
    """Persistence boundary for plugins, versions, contracts, tools, and install events."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record_install_event(
        self,
        *,
        plugin_id: str | None,
        plugin_version_id: str | None,
        action: str,
        status: str,
        reason: str,
        manifest_hash: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one immutable install lifecycle event."""
        event_id = f"plugin-install-event-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO plugin_install_events
                (id, plugin_id, plugin_version_id, action, status, reason, manifest_hash, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                plugin_id,
                plugin_version_id,
                action,
                status,
                reason,
                manifest_hash,
                json_dumps(payload or {}),
                utc_now(),
            ),
        )
        return self.connection.execute(
            "SELECT * FROM plugin_install_events WHERE id = ?", (event_id,)
        ).fetchone()

    def install_validated_manifest(self, validated: ValidatedPluginManifest) -> dict[str, Any]:
        """Persist a validated local plugin manifest and its normalized child contracts."""
        manifest = validated.manifest
        timestamp = utc_now()
        plugin_id = str(manifest["id"])
        existing_plugin = self.connection.execute(
            "SELECT * FROM plugins WHERE id = ?", (plugin_id,)
        ).fetchone()
        existing_version = self.connection.execute(
            "SELECT * FROM plugin_versions WHERE plugin_id = ? AND version = ?",
            (plugin_id, manifest["version"]),
        ).fetchone()
        version_id = existing_version["id"] if existing_version else f"plugin-version-{uuid.uuid4()}"
        status = "disabled" if manifest["trustLevel"] == "third_party" else "enabled"
        plugin_values = (
            plugin_id,
            manifest["name"],
            manifest["publisher"],
            manifest["trustLevel"],
            status if existing_plugin is None else existing_plugin["status"],
            version_id,
            json_dumps(manifest["capabilities"]),
            json_dumps(manifest["permissions"]),
            timestamp,
            timestamp,
        )
        if existing_plugin:
            self.connection.execute(
                """
                UPDATE plugins
                SET name = ?, publisher = ?, trust_level = ?, active_version_id = ?,
                    capabilities_json = ?, permissions_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    manifest["name"],
                    manifest["publisher"],
                    manifest["trustLevel"],
                    version_id,
                    json_dumps(manifest["capabilities"]),
                    json_dumps(manifest["permissions"]),
                    timestamp,
                    plugin_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO plugins
                    (id, name, publisher, trust_level, status, active_version_id,
                     capabilities_json, permissions_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                plugin_values,
            )
        if existing_version:
            self.connection.execute(
                """
                UPDATE plugin_versions
                SET manifest_path = ?, manifest_hash = ?, package_hash = ?, min_aido_version = ?,
                    entrypoints_json = ?, checksums_json = ?, manifest_json = ?, status = ?,
                    validated_at = ?, installed_at = ?
                WHERE id = ?
                """,
                (
                    str(validated.manifest_path),
                    validated.manifest_hash,
                    validated.package_hash,
                    manifest["minAidoVersion"],
                    json_dumps(manifest["entrypoints"]),
                    json_dumps(manifest["checksums"]),
                    json_dumps(manifest),
                    "valid",
                    timestamp,
                    timestamp,
                    version_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO plugin_versions
                    (id, plugin_id, version, manifest_path, manifest_hash, package_hash,
                     min_aido_version, entrypoints_json, checksums_json, manifest_json,
                     status, installed_at, validated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    plugin_id,
                    manifest["version"],
                    str(validated.manifest_path),
                    validated.manifest_hash,
                    validated.package_hash,
                    manifest["minAidoVersion"],
                    json_dumps(manifest["entrypoints"]),
                    json_dumps(manifest["checksums"]),
                    json_dumps(manifest),
                    "valid",
                    timestamp,
                    timestamp,
                ),
            )
        self._replace_version_children(version_id=version_id, validated=validated, timestamp=timestamp)
        self.record_install_event(
            plugin_id=plugin_id,
            plugin_version_id=version_id,
            action="install_local",
            status="installed",
            reason="Local plugin manifest installed after validation.",
            manifest_hash=validated.manifest_hash,
            payload={"path": str(validated.root), "status": status},
        )
        return self.get_plugin(plugin_id)

    def _replace_version_children(
        self,
        *,
        version_id: str,
        validated: ValidatedPluginManifest,
        timestamp: str,
    ) -> None:
        for table in ("plugin_permissions", "plugin_skills", "plugin_agents", "plugin_tools"):
            self.connection.execute(f"DELETE FROM {table} WHERE plugin_version_id = ?", (version_id,))
        for permission in validated.manifest["permissions"]:
            self.connection.execute(
                """
                INSERT INTO plugin_permissions
                    (id, plugin_version_id, permission, risk_level, status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"plugin-permission-{uuid.uuid4()}",
                    version_id,
                    permission,
                    "low",
                    "declared",
                    "Explicit manifest permission.",
                    timestamp,
                ),
            )
        for skill in validated.skills:
            self.connection.execute(
                """
                INSERT INTO plugin_skills
                    (id, plugin_version_id, skill_id, path, contract_hash, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"plugin-skill-{uuid.uuid4()}",
                    version_id,
                    skill["id"],
                    skill["path"],
                    skill["contractHash"],
                    skill["status"],
                    timestamp,
                ),
            )
        for agent in validated.agents:
            self.connection.execute(
                """
                INSERT INTO plugin_agents
                    (id, plugin_version_id, agent_id, role, capabilities_json, schema_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"plugin-agent-{uuid.uuid4()}",
                    version_id,
                    agent["id"],
                    agent["role"],
                    json_dumps(agent["capabilities"]),
                    json_dumps(agent["schema"]),
                    agent["status"],
                    timestamp,
                ),
            )
        for tool in validated.tools:
            self.connection.execute(
                """
                INSERT INTO plugin_tools
                    (id, plugin_version_id, tool_id, name, broker_tool, policy_required,
                     policy_json, schema_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"plugin-tool-{uuid.uuid4()}",
                    version_id,
                    tool["id"],
                    tool["name"],
                    tool["brokerTool"],
                    1 if tool["policyRequired"] else 0,
                    json_dumps(tool["policy"]),
                    json_dumps(tool["schema"]),
                    tool["status"],
                    timestamp,
                ),
            )

    def list_plugins(self) -> list[dict[str, Any]]:
        """List installed plugins with their active version contract."""
        rows = self.connection.execute("SELECT * FROM plugins ORDER BY updated_at DESC, id ASC").fetchall()
        return [self._row_to_plugin(row) for row in rows]

    def get_plugin(self, plugin_id: str) -> dict[str, Any]:
        """Return one plugin or raise ``KeyError``."""
        row = self.connection.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
        if not row:
            raise KeyError(f"Plugin not found: {plugin_id}")
        return self._row_to_plugin(row)

    def active_version(self, plugin_id: str) -> dict[str, Any]:
        """Return the active version for a plugin or raise ``KeyError``."""
        plugin = self.connection.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
        if not plugin or not plugin["active_version_id"]:
            raise KeyError(f"Active plugin version not found: {plugin_id}")
        return self._version_by_id(plugin["active_version_id"])

    def set_plugin_status(self, plugin_id: str, status: str) -> dict[str, Any]:
        """Set plugin enabled/disabled status and append an install lifecycle event."""
        plugin = self.get_plugin(plugin_id)
        timestamp = utc_now()
        self.connection.execute(
            "UPDATE plugins SET status = ?, updated_at = ? WHERE id = ?",
            (status, timestamp, plugin_id),
        )
        self.record_install_event(
            plugin_id=plugin_id,
            plugin_version_id=plugin["activeVersion"]["id"] if plugin.get("activeVersion") else None,
            action=status.removesuffix("d") if status in {"enabled", "disabled"} else status,
            status=status,
            reason=f"Plugin {status} by operator.",
            manifest_hash=plugin["activeVersion"]["manifestHash"] if plugin.get("activeVersion") else None,
            payload={"previousStatus": plugin["status"], "status": status},
        )
        return self.get_plugin(plugin_id)

    def list_install_events(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List install lifecycle events newest first, optionally filtered by status.

        Orders by ``created_at`` with a ``rowid`` tie-break because the ledger is
        append-only and same-millisecond rows would otherwise order randomly.
        """
        if status:
            rows = self.connection.execute(
                """
                SELECT * FROM plugin_install_events WHERE status = ?
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM plugin_install_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_install_event(row) for row in rows]

    def installed_version_statuses(self) -> dict[tuple[str, str], str]:
        """Map ``(plugin id, version)`` to plugin status so scans can mark installed candidates."""
        rows = self.connection.execute(
            """
            SELECT plugin_versions.plugin_id AS plugin_id,
                   plugin_versions.version AS version,
                   plugins.status AS status
            FROM plugin_versions
            JOIN plugins ON plugins.id = plugin_versions.plugin_id
            """
        ).fetchall()
        return {(row["plugin_id"], row["version"]): row["status"] for row in rows}

    def mark_version_validated(self, version_id: str, status: str) -> None:
        """Update active version validation status and timestamp."""
        self.connection.execute(
            "UPDATE plugin_versions SET status = ?, validated_at = ? WHERE id = ?",
            (status, utc_now(), version_id),
        )

    def _version_by_id(self, version_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM plugin_versions WHERE id = ?", (version_id,)).fetchone()
        if not row:
            raise KeyError(f"Plugin version not found: {version_id}")
        return self._row_to_version(row)

    def _row_to_version(self, row: sqlite3.Row) -> dict[str, Any]:
        version_id = row["id"]
        permissions = self.connection.execute(
            "SELECT * FROM plugin_permissions WHERE plugin_version_id = ? ORDER BY permission ASC",
            (version_id,),
        ).fetchall()
        skills = self.connection.execute(
            "SELECT * FROM plugin_skills WHERE plugin_version_id = ? ORDER BY skill_id ASC",
            (version_id,),
        ).fetchall()
        agents = self.connection.execute(
            "SELECT * FROM plugin_agents WHERE plugin_version_id = ? ORDER BY agent_id ASC",
            (version_id,),
        ).fetchall()
        tools = self.connection.execute(
            "SELECT * FROM plugin_tools WHERE plugin_version_id = ? ORDER BY tool_id ASC",
            (version_id,),
        ).fetchall()
        return {
            "id": version_id,
            "pluginId": row["plugin_id"],
            "version": row["version"],
            "manifestPath": row["manifest_path"],
            "manifestHash": row["manifest_hash"],
            "packageHash": row["package_hash"],
            "minAidoVersion": row["min_aido_version"],
            "entrypoints": json_loads(row["entrypoints_json"], {}),
            "checksums": json_loads(row["checksums_json"], {}),
            "manifest": json_loads(row["manifest_json"], {}),
            "status": row["status"],
            "installedAt": row["installed_at"],
            "validatedAt": row["validated_at"],
            "permissions": [_row_to_permission(item) for item in permissions],
            "skills": [_row_to_skill(item) for item in skills],
            "agents": [_row_to_agent(item) for item in agents],
            "tools": [_row_to_tool(item) for item in tools],
        }

    def _row_to_plugin(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "publisher": row["publisher"],
            "trustLevel": row["trust_level"],
            "status": row["status"],
            "activeVersionId": row["active_version_id"],
            "capabilities": json_loads(row["capabilities_json"], []),
            "permissions": json_loads(row["permissions_json"], []),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "activeVersion": self._version_by_id(row["active_version_id"])
            if row["active_version_id"]
            else None,
        }
