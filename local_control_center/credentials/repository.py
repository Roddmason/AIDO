"""Persistencia SQLite del metadato de credenciales: referencias, fingerprints y auditoría.

Guarda en ``credential_refs`` dónde vive cada secreto (backend + locator), su fingerprint (hash con
sal, nunca el secreto) y su estado/rotación, y en ``credential_audit`` la bitácora append-only de
operaciones. NUNCA hay una columna con el valor del secreto. Este repositorio es la capa interna que
ve fingerprint y sal; el ``CredentialManager`` es la frontera que jamás los expone por su API pública.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver ``shared/db.py``) y
estos métodos NO abren transacciones propias; cada ``execute`` se confirma de inmediato. Una operación
del manager (escribir en el backend + registrar metadato + auditar) abarca varias sentencias en varias
capas: si se requiere atomicidad entre ellas, el caller debe envolverlas en ``immediate_transaction``.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_credential_ref(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``credential_refs`` al dict interno (incluye fingerprint/sal de uso interno)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "backendKind": row["backend_kind"],
        "locator": row["locator"],
        "fingerprint": row["fingerprint"],
        "fingerprintAlgo": row["fingerprint_algo"],
        "salt": row["salt"],
        "authMode": row["auth_mode"],
        "status": row["status"],
        "enabled": bool(row["enabled"]),
        "rotatedAt": row["rotated_at"],
        "lastValidatedAt": row["last_validated_at"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_credential_audit(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``credential_audit`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "credentialId": row["credential_id"],
        "name": row["name"],
        "action": row["action"],
        "outcome": row["outcome"],
        "actor": row["actor"],
        "backendKind": row["backend_kind"],
        "detail": row["detail"],
        "createdAt": row["created_at"],
    }


class CredentialRepository:
    """Acceso al metadato de credenciales sobre la conexión SQLite del caller (autocommit por statement)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_credential_ref(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta la referencia de una credencial (id ``credential-<uuid>``) y la devuelve.

        Persiste backend, locator, fingerprint y sal; nunca el valor del secreto.
        """
        credential_id = f"credential-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO credential_refs
                (id, name, backend_kind, locator, fingerprint, fingerprint_algo, salt, auth_mode,
                 status, enabled, rotated_at, last_validated_at, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                credential_id,
                str(body["name"]),
                str(body["backendKind"]),
                str(body["locator"]),
                body.get("fingerprint"),
                body.get("fingerprintAlgo", "hmac-sha256"),
                body.get("salt"),
                body.get("authMode", "token"),
                body.get("status", "active"),
                int(bool(body.get("enabled", True))),
                body.get("rotatedAt"),
                body.get("lastValidatedAt"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_credential(credential_id)

    def get_credential(self, credential_id: str) -> dict[str, Any]:
        """Recupera una referencia de credencial por id.

        Raises:
            KeyError: si no existe ninguna credencial con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM credential_refs WHERE id = ?", (credential_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Credential not found: {credential_id}")
        return row_to_credential_ref(row)

    def find_credential_by_name(self, name: str) -> dict[str, Any] | None:
        """Devuelve la referencia por nombre, o ``None`` si no existe (sin lanzar)."""
        row = self.connection.execute("SELECT * FROM credential_refs WHERE name = ?", (name,)).fetchone()
        return row_to_credential_ref(row) if row else None

    def get_credential_by_name(self, name: str) -> dict[str, Any]:
        """Recupera una referencia de credencial por nombre.

        Raises:
            KeyError: si no existe ninguna credencial con ese nombre.
        """
        record = self.find_credential_by_name(name)
        if record is None:
            raise KeyError(f"Credential not found: {name}")
        return record

    def list_credential_refs(self) -> list[dict[str, Any]]:
        """Lista las referencias de credenciales, las más recientes primero."""
        rows = self.connection.execute("SELECT * FROM credential_refs ORDER BY created_at DESC").fetchall()
        return [row_to_credential_ref(row) for row in rows]

    def update_credential(self, credential_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una referencia (fingerprint/sal/estado/rotación/validación/metadata).

        Raises:
            KeyError: si la credencial no existe.
        """
        current = self.get_credential(credential_id)
        next_value = {**current, **patch}
        self.connection.execute(
            """
            UPDATE credential_refs
            SET backend_kind = ?, locator = ?, fingerprint = ?, salt = ?, auth_mode = ?, status = ?,
                enabled = ?, rotated_at = ?, last_validated_at = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["backendKind"],
                next_value["locator"],
                next_value.get("fingerprint"),
                next_value.get("salt"),
                next_value["authMode"],
                next_value["status"],
                int(bool(next_value.get("enabled", True))),
                next_value.get("rotatedAt"),
                next_value.get("lastValidatedAt"),
                json_dumps(next_value.get("metadata") or {}),
                utc_now(),
                credential_id,
            ),
        )
        return self.get_credential(credential_id)

    def delete_credential_ref(self, credential_id: str) -> None:
        """Elimina la referencia de una credencial (la bitácora de auditoría se conserva)."""
        self.connection.execute("DELETE FROM credential_refs WHERE id = ?", (credential_id,))

    def append_audit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Anexa una entrada a la bitácora de auditoría (append-only; sin secretos) y la devuelve."""
        audit_id = f"credential-audit-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO credential_audit
                (id, credential_id, name, action, outcome, actor, backend_kind, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                str(body["credentialId"]),
                str(body["name"]),
                str(body["action"]),
                str(body["outcome"]),
                str(body.get("actor", "operator")),
                str(body["backendKind"]),
                str(body.get("detail", "")),
                utc_now(),
            ),
        )
        row = self.connection.execute("SELECT * FROM credential_audit WHERE id = ?", (audit_id,)).fetchone()
        return row_to_credential_audit(row)

    def list_audit(self, credential_id: str | None = None) -> list[dict[str, Any]]:
        """Lista la auditoría (toda o por credencial), en orden cronológico estable.

        Desempata por ``rowid`` (orden de inserción monótono), no por ``id`` (un uuid aleatorio): así
        dos entradas del mismo milisegundo conservan su orden real de escritura y ``list_audit()[-1]``
        es siempre la última acción registrada.
        """
        if credential_id:
            rows = self.connection.execute(
                "SELECT * FROM credential_audit WHERE credential_id = ? ORDER BY created_at ASC, rowid ASC",
                (credential_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM credential_audit ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        return [row_to_credential_audit(row) for row in rows]
