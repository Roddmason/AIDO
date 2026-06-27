"""Persistencia SQLite de la configuración de runtimes: instalaciones, cuentas y preferencias.

Guarda la instalación de cada runtime (ruta del ejecutable, versión detectada, enabled, estado de
salud), las cuentas de runtime (modo de auth y clase de almacén de credenciales — NUNCA el token) y las
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
        "capabilities": json_loads(row["capabilities"], []),
        "preferredRoles": json_loads(row["preferred_roles"], []),
        "healthStatus": row["health_status"],
        "lastValidationAt": row["last_validation_at"],
        "lastHealthCheckAt": row["last_health_check_at"],
        "lastError": row["last_error"],
        "configurationSource": row["configuration_source"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_runtime_account(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``runtime_accounts`` al dict camelCase del contrato (sin exponer secretos)."""
    return {
        "id": row["id"],
        "runtimeId": row["runtime_id"],
        "accountLabel": row["account_label"],
        "authMode": row["auth_mode"],
        "credentialStoreKind": row["credential_store_kind"],
        "credentialRef": row["credential_ref"],
        "enabled": bool(row["enabled"]),
        "isDefault": bool(row["is_default"]),
        "capabilities": json_loads(row["capabilities"], []),
        "preferredRoles": json_loads(row["preferred_roles"], []),
        "healthStatus": row["health_status"],
        "lastValidationAt": row["last_validation_at"],
        "configurationSource": row["configuration_source"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


row_to_cli_account = row_to_runtime_account


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

        Guarda ruta del ejecutable, versión detectada, capacidades, roles preferidos, enabled y estado
        de salud. La lectura previa y la escritura son dos sentencias: el caller las agrupa en
        ``immediate_transaction`` si necesita atomicidad.
        """
        runtime_id = str(body["runtimeId"])
        timestamp = utc_now()
        existing_row = self.connection.execute(
            "SELECT * FROM runtime_installations WHERE runtime_id = ?", (runtime_id,)
        ).fetchone()
        current = row_to_runtime_installation(existing_row) if existing_row else {}
        metadata = body.get("metadata") if "metadata" in body else current.get("metadata", {})
        configuration_source = str(
            body.get("configurationSource")
            or (metadata or {}).get("source")
            or current.get("configurationSource")
            or "manual"
        )
        last_validation_at = body.get("lastValidationAt")
        if last_validation_at is None and "lastHealthCheckAt" in body:
            last_validation_at = body.get("lastHealthCheckAt")
        if last_validation_at is None:
            last_validation_at = current.get("lastValidationAt")
        last_health_check_at = (
            body.get("lastHealthCheckAt") if "lastHealthCheckAt" in body else current.get("lastHealthCheckAt")
        )
        if existing_row:
            self.connection.execute(
                """
                UPDATE runtime_installations
                SET kind = ?, executable_path = ?, detected_version = ?, enabled = ?, capabilities = ?,
                    preferred_roles = ?, health_status = ?, last_validation_at = ?, last_health_check_at = ?,
                    last_error = ?, configuration_source = ?, metadata = ?, updated_at = ?
                WHERE runtime_id = ?
                """,
                (
                    body.get("kind", current.get("kind", "cli")),
                    body.get("executablePath", current.get("executablePath")),
                    body.get("detectedVersion", current.get("detectedVersion")),
                    int(bool(body.get("enabled", current.get("enabled", False)))),
                    json_dumps(body.get("capabilities", current.get("capabilities", [])) or []),
                    json_dumps(body.get("preferredRoles", current.get("preferredRoles", [])) or []),
                    body.get("healthStatus", current.get("healthStatus", "unknown")),
                    last_validation_at,
                    last_health_check_at,
                    body.get("lastError", current.get("lastError")),
                    configuration_source,
                    json_dumps(metadata or {}),
                    timestamp,
                    runtime_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO runtime_installations
                    (id, runtime_id, kind, executable_path, detected_version, enabled, capabilities,
                     preferred_roles, health_status, last_validation_at, last_health_check_at, last_error,
                     configuration_source, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"runtime-installation-{runtime_id}",
                    runtime_id,
                    body.get("kind", "cli"),
                    body.get("executablePath"),
                    body.get("detectedVersion"),
                    int(bool(body.get("enabled", False))),
                    json_dumps(body.get("capabilities") or []),
                    json_dumps(body.get("preferredRoles") or []),
                    body.get("healthStatus", "unknown"),
                    last_validation_at,
                    last_health_check_at,
                    body.get("lastError"),
                    configuration_source,
                    json_dumps(metadata or {}),
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

    # -- Cuentas de runtime (sin tokens) -------------------------------------------

    def create_runtime_account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una cuenta de runtime y la devuelve.

        El default de CLI es ``provider_native_cli``: AIDO usa el login/sesión nativo del CLI en vez
        de copiar secretos. ``credentialRef`` es opcional y solo puede ser un puntero no secreto.
        """
        account_id = str(body.get("id") or f"runtime-account-{uuid.uuid4()}")
        runtime_id = str(body["runtimeId"])
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO runtime_accounts
                (id, runtime_id, account_label, auth_mode, credential_store_kind, credential_ref,
                 enabled, is_default, capabilities, preferred_roles, health_status, last_validation_at,
                 configuration_source, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                runtime_id,
                str(body["accountLabel"]),
                str(body.get("authMode") or "provider_native_cli"),
                str(body.get("credentialStoreKind") or "provider_native_cli"),
                body.get("credentialRef"),
                int(bool(body.get("enabled", True))),
                int(bool(body.get("isDefault", False))),
                json_dumps(body.get("capabilities") or []),
                json_dumps(body.get("preferredRoles") or []),
                body.get("healthStatus", "unknown"),
                body.get("lastValidationAt"),
                body.get("configurationSource", "manual"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        if body.get("isDefault"):
            self.connection.execute(
                "UPDATE runtime_accounts SET is_default = 0 WHERE runtime_id = ? AND id <> ?",
                (runtime_id, account_id),
            )
        return self.get_runtime_account(account_id)

    def get_runtime_account(self, account_id: str) -> dict[str, Any]:
        """Recupera una cuenta de runtime por id.

        Raises:
            KeyError: si no existe ninguna cuenta con ese id.
        """
        row = self.connection.execute("SELECT * FROM runtime_accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise KeyError(f"Runtime account not found: {account_id}")
        return row_to_runtime_account(row)

    def list_runtime_accounts(self, runtime_id: str | None = None) -> list[dict[str, Any]]:
        """Lista las cuentas de runtime (todas o por runtime), priorizando la default."""
        if runtime_id:
            rows = self.connection.execute(
                """
                SELECT * FROM runtime_accounts
                WHERE runtime_id = ?
                ORDER BY is_default DESC, created_at ASC
                """,
                (runtime_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM runtime_accounts ORDER BY runtime_id ASC, is_default DESC, created_at ASC"
            ).fetchall()
        return [row_to_runtime_account(row) for row in rows]

    def update_runtime_account(self, account_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una cuenta de runtime; nunca toca ni persiste secretos.

        Raises:
            KeyError: si la cuenta no existe.
        """
        current = self.get_runtime_account(account_id)
        next_value = {**current, **body}
        self.connection.execute(
            """
            UPDATE runtime_accounts
            SET auth_mode = ?, credential_store_kind = ?, credential_ref = ?, enabled = ?,
                is_default = ?, capabilities = ?, preferred_roles = ?, health_status = ?,
                last_validation_at = ?, configuration_source = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value.get("authMode") or "provider_native_cli",
                next_value.get("credentialStoreKind") or "provider_native_cli",
                next_value.get("credentialRef"),
                int(bool(next_value.get("enabled", True))),
                int(bool(next_value.get("isDefault", False))),
                json_dumps(next_value.get("capabilities") or []),
                json_dumps(next_value.get("preferredRoles") or []),
                next_value.get("healthStatus", "unknown"),
                next_value.get("lastValidationAt"),
                next_value.get("configurationSource", "manual"),
                json_dumps(next_value.get("metadata") or {}),
                utc_now(),
                account_id,
            ),
        )
        if next_value.get("isDefault"):
            self.connection.execute(
                """
                UPDATE runtime_accounts
                SET is_default = 0
                WHERE runtime_id = ? AND id <> ?
                """,
                (current["runtimeId"], account_id),
            )
        return self.get_runtime_account(account_id)

    def create_cli_account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Alias compatible: usa ``create_runtime_account``."""
        return self.create_runtime_account(body)

    def get_cli_account(self, account_id: str) -> dict[str, Any]:
        """Alias compatible: usa ``get_runtime_account``."""
        return self.get_runtime_account(account_id)

    def list_cli_accounts(self, runtime_id: str | None = None) -> list[dict[str, Any]]:
        """Alias compatible: usa ``list_runtime_accounts``."""
        return self.list_runtime_accounts(runtime_id)

    def update_cli_account(self, account_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Alias compatible: usa ``update_runtime_account``."""
        return self.update_runtime_account(account_id, body)

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
