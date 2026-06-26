"""Settings repository: generic two-tier CRUD store over the ``settings_value`` SQLite table.

Provides ``set_value`` (upsert), ``get_value`` (returns ``UNSET`` sentinel when no row exists),
``clear_value`` (returns True if a row was deleted) and ``list_values`` (full table snapshot).
Values are stored as JSON so any JSON-serialisable type round-trips without information loss.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from local_control_center.shared.time import utc_now

# Sentinel that unambiguously signals "no stored value" even when the stored value is falsy.
UNSET: object = object()


class SettingsRepository:
    """Read/write access to the ``settings_value`` table for a single SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # SQLite does not enforce uniqueness on NULL PK columns (NULLs compare unequal to each other
    # even in a PRIMARY KEY). We normalise scope_id=None to '' on write/read so the PK constraint
    # works correctly while the public API still accepts None as "no project scope".
    _NULL_SCOPE_ID = ""

    @staticmethod
    def _encode_scope_id(scope_id: str | None) -> str:
        return SettingsRepository._NULL_SCOPE_ID if scope_id is None else scope_id

    @staticmethod
    def _decode_scope_id(stored: str) -> str | None:
        return None if stored == SettingsRepository._NULL_SCOPE_ID else stored

    def set_value(self, key: str, scope: str, scope_id: str | None, value: Any) -> None:
        """Persist ``value`` for ``(key, scope, scope_id)``, upserting if a row already exists."""
        self.connection.execute(
            """
            INSERT INTO settings_value (key, scope, scope_id, value_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key, scope, scope_id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (
                key,
                scope,
                self._encode_scope_id(scope_id),
                json.dumps(value, ensure_ascii=False),
                utc_now(),
            ),
        )

    def get_value(self, key: str, scope: str, scope_id: str | None) -> Any:
        """Return the stored value for ``(key, scope, scope_id)``, or ``UNSET`` when absent."""
        row = self.connection.execute(
            "SELECT value_json FROM settings_value WHERE key=? AND scope=? AND scope_id=?",
            (key, scope, self._encode_scope_id(scope_id)),
        ).fetchone()
        if row is None:
            return UNSET
        return json.loads(row["value_json"])

    def clear_value(self, key: str, scope: str, scope_id: str | None) -> bool:
        """Delete the row for ``(key, scope, scope_id)``; return True if a row was removed."""
        cursor = self.connection.execute(
            "DELETE FROM settings_value WHERE key=? AND scope=? AND scope_id=?",
            (key, scope, self._encode_scope_id(scope_id)),
        )
        return cursor.rowcount > 0

    def list_values(self) -> dict[tuple[str, str, str | None], Any]:
        """Return every stored value keyed by ``(key, scope, scope_id)`` (scope_id None-decoded)."""
        rows = self.connection.execute(
            "SELECT key, scope, scope_id, value_json FROM settings_value"
        ).fetchall()
        return {
            (row["key"], row["scope"], self._decode_scope_id(row["scope_id"])): json.loads(
                row["value_json"]
            )
            for row in rows
        }
