"""Persistencia SQLite de la configuración de runtimes: instalaciones, cuentas CLI y preferencias.

Guarda la instalación de cada runtime (ruta del ejecutable, versión detectada, enabled, estado de
salud), las cuentas de CLI (modo de auth y clase de almacén de credenciales — NUNCA el token) y las
preferencias (runtime predeterminado, orden y perfiles por defecto). Los mapeadores ``row_to_*``
proyectan cada fila al dict camelCase del contrato; ninguno expone secretos.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver ``shared/db.py``) y
estos métodos NO abren transacciones propias; cada ``execute`` se confirma de inmediato. Los upserts
(``upsert_installation``/``upsert_preferences``) hacen lectura-luego-escritura (SELECT + INSERT/UPDATE),
dos sentencias que solo son atómicas si el caller las agrupa en una única ``immediate_transaction``.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_runtime_installation(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``runtime_installations`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "runtimeId": row["runtime_id"],
        "kind": row["kind"],
        "executablePath": row["executable_path"],
        "detectedVersion": row["detected_version"],
        "enabled": bool(row["enabled"]),
        "healthStatus": row["health_status"],
        "lastHealthCheckAt": row["last_health_check_at"],
        "lastError": row["last_error"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_cli_account(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``cli_accounts`` al dict camelCase del contrato (sin exponer secretos)."""
    return {
        "id": row["id"],
        "runtimeId": row["runtime_id"],
        "accountLabel": row["account_label"],
        "authMode": row["auth_mode"],
        "credentialStoreKind": row["credential_store_kind"],
        "credentialRef": row["credential_ref"],
        "enabled": bool(row["enabled"]),
        "isDefault": bool(row["is_default"]),
        "healthStatus": row["health_status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_runtime_preference(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``runtime_preferences`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "scope": row["scope"],
        "scopeId": row["scope_id"],
        "defaultRuntime": row["default_runtime"],
        "runtimeOrder": json_loads(row["runtime_order"], []),
        "defaultProfiles": json_loads(row["default_profiles"]),
        "enabled": bool(row["enabled"]),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class RuntimeConfigRepository:
    """Acceso a la configuración de runtimes sobre la conexión SQLite del caller (autocommit por statement)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    # -- Instalaciones de runtime ---------------------------------------------------

    def upsert_installation(self, body: dict[str, Any]) -> dict[str, Any]:
        """Crea o actualiza la instalación de un runtime por ``runtimeId`` y la devuelve.

        Guarda ruta del ejecutable, versión detectada, enabled y estado de salud. La lectura previa y
        la escritura son dos sentencias: el caller las agrupa en ``immediate_transaction`` si necesita
        atomicidad.
        """
        runtime_id = str(body["runtimeId"])
        timestamp = utc_now()
        existing = self.connection.execute(
            "SELECT id FROM runtime_installations WHERE runtime_id = ?", (runtime_id,)
        ).fetchone()
        if existing:
            self.connection.execute(
                """
                UPDATE runtime_installations
                SET kind = ?, executable_path = ?, detected_version = ?, enabled = ?, health_status = ?,
                    last_health_check_at = ?, last_error = ?, metadata = ?, updated_at = ?
                WHERE runtime_id = ?
                """,
                (
                    body.get("kind", "cli"),
                    body.get("executablePath"),
                    body.get("detectedVersion"),
                    int(bool(body.get("enabled", False))),
                    body.get("healthStatus", "unknown"),
                    body.get("lastHealthCheckAt"),
                    body.get("lastError"),
                    json_dumps(body.get("metadata") or {}),
                    timestamp,
                    runtime_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO runtime_installations
                    (id, runtime_id, kind, executable_path, detected_version, enabled, health_status,
                     last_health_check_at, last_error, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"runtime-installation-{runtime_id}",
                    runtime_id,
                    body.get("kind", "cli"),
                    body.get("executablePath"),
                    body.get("detectedVersion"),
                    int(bool(body.get("enabled", False))),
                    body.get("healthStatus", "unknown"),
                    body.get("lastHealthCheckAt"),
                    body.get("lastError"),
                    json_dumps(body.get("metadata") or {}),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_installation(runtime_id)

    def get_installation(self, runtime_id: str) -> dict[str, Any]:
        """Recupera la instalación de un runtime por su ``runtimeId``.

        Raises:
            KeyError: si no hay instalación registrada para ese runtime.
        """
        row = self.connection.execute(
            "SELECT * FROM runtime_installations WHERE runtime_id = ?", (runtime_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Runtime installation not found: {runtime_id}")
        return row_to_runtime_installation(row)

    def list_installations(self) -> list[dict[str, Any]]:
        """Lista las instalaciones de runtime ordenadas por ``runtimeId``."""
        rows = self.connection.execute(
            "SELECT * FROM runtime_installations ORDER BY runtime_id ASC"
        ).fetchall()
        return [row_to_runtime_installation(row) for row in rows]

    # -- Cuentas de CLI (sin tokens) ------------------------------------------------

    def create_cli_account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una cuenta de CLI (id ``cli-account-<uuid>``) y la devuelve.

        Solo persiste ``authMode`` y ``credentialStoreKind`` (más un ``credentialRef`` no secreto que
        apunta al almacén); nunca el token ni el secreto.
        """
        account_id = f"cli-account-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO cli_accounts
                (id, runtime_id, account_label, auth_mode, credential_store_kind, credential_ref,
                 enabled, is_default, health_status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                str(body["runtimeId"]),
                str(body["accountLabel"]),
                str(body["authMode"]),
                str(body["credentialStoreKind"]),
                body.get("credentialRef"),
                int(bool(body.get("enabled", True))),
                int(bool(body.get("isDefault", False))),
                body.get("healthStatus", "unknown"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_cli_account(account_id)

    def get_cli_account(self, account_id: str) -> dict[str, Any]:
        """Recupera una cuenta de CLI por id.

        Raises:
            KeyError: si no existe ninguna cuenta con ese id.
        """
        row = self.connection.execute("SELECT * FROM cli_accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise KeyError(f"CLI account not found: {account_id}")
        return row_to_cli_account(row)

    def list_cli_accounts(self, runtime_id: str | None = None) -> list[dict[str, Any]]:
        """Lista las cuentas de CLI (todas o por runtime), en orden de creación."""
        if runtime_id:
            rows = self.connection.execute(
                "SELECT * FROM cli_accounts WHERE runtime_id = ? ORDER BY created_at ASC",
                (runtime_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM cli_accounts ORDER BY created_at ASC").fetchall()
        return [row_to_cli_account(row) for row in rows]

    def update_cli_account(self, account_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una cuenta de CLI (auth/almacén/enabled/default/salud); nunca toca tokens.

        Raises:
            KeyError: si la cuenta no existe.
        """
        current = self.get_cli_account(account_id)
        next_value = {**current, **body}
        self.connection.execute(
            """
            UPDATE cli_accounts
            SET auth_mode = ?, credential_store_kind = ?, credential_ref = ?, enabled = ?,
                is_default = ?, health_status = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["authMode"],
                next_value["credentialStoreKind"],
                next_value.get("credentialRef"),
                int(bool(next_value.get("enabled", True))),
                int(bool(next_value.get("isDefault", False))),
                next_value.get("healthStatus", "unknown"),
                json_dumps(next_value.get("metadata") or {}),
                utc_now(),
                account_id,
            ),
        )
        return self.get_cli_account(account_id)

    # -- Preferencias de runtime ----------------------------------------------------

    def upsert_preferences(self, body: dict[str, Any]) -> dict[str, Any]:
        """Crea o actualiza las preferencias de un scope (global/role/agent) y las devuelve.

        Guarda el runtime predeterminado, el orden de runtimes y los perfiles por defecto. La lectura
        previa y la escritura son dos sentencias; agrúpalas en ``immediate_transaction`` si requieres
        atomicidad.
        """
        scope = str(body.get("scope") or "global")
        scope_id = str(body.get("scopeId") or "")
        timestamp = utc_now()
        existing = self.connection.execute(
            "SELECT id FROM runtime_preferences WHERE scope = ? AND scope_id = ?", (scope, scope_id)
        ).fetchone()
        runtime_order = json_dumps(body.get("runtimeOrder") or [])
        default_profiles = json_dumps(body.get("defaultProfiles") or {})
        if existing:
            self.connection.execute(
                """
                UPDATE runtime_preferences
                SET default_runtime = ?, runtime_order = ?, default_profiles = ?, enabled = ?,
                    metadata = ?, updated_at = ?
                WHERE scope = ? AND scope_id = ?
                """,
                (
                    body.get("defaultRuntime"),
                    runtime_order,
                    default_profiles,
                    int(bool(body.get("enabled", True))),
                    json_dumps(body.get("metadata") or {}),
                    timestamp,
                    scope,
                    scope_id,
                ),
            )
        else:
            preference_id = (
                "runtime-preference-global"
                if scope == "global" and not scope_id
                else f"runtime-preference-{scope}-{scope_id or uuid.uuid4()}"
            )
            self.connection.execute(
                """
                INSERT INTO runtime_preferences
                    (id, scope, scope_id, default_runtime, runtime_order, default_profiles, enabled,
                     metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preference_id,
                    scope,
                    scope_id,
                    body.get("defaultRuntime"),
                    runtime_order,
                    default_profiles,
                    int(bool(body.get("enabled", True))),
                    json_dumps(body.get("metadata") or {}),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_preferences(scope=scope, scope_id=scope_id)

    def get_preferences(self, *, scope: str = "global", scope_id: str = "") -> dict[str, Any]:
        """Recupera las preferencias de un scope (por defecto, las globales).

        Raises:
            KeyError: si no hay preferencias registradas para ese scope.
        """
        row = self.connection.execute(
            "SELECT * FROM runtime_preferences WHERE scope = ? AND scope_id = ?", (scope, scope_id)
        ).fetchone()
        if not row:
            raise KeyError(f"Runtime preferences not found: {scope}:{scope_id}")
        return row_to_runtime_preference(row)

    def list_preferences(self) -> list[dict[str, Any]]:
        """Lista todas las preferencias de runtime registradas, en orden de creación."""
        rows = self.connection.execute("SELECT * FROM runtime_preferences ORDER BY created_at ASC").fetchall()
        return [row_to_runtime_preference(row) for row in rows]
