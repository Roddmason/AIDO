"""Persistencia SQLite de conexiones IDE, servidores MCP e integraciones del slice.

Traduce filas (``sqlite3.Row``) a dicts camelCase para la API y ejecuta los upsert/insert.
Transacciones: cada método emite los ``INSERT/UPDATE`` sobre la conexión recibida pero NO
hace ``commit``; el control de transacción queda en manos del caller dueño de la conexión.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_ide_connection(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``ide_connections`` al dict camelCase de la API, deserializando los JSON."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "editor": row["editor"],
        "workspaceRoot": row["workspace_root"],
        "status": row["status"],
        "openFiles": json_loads(row["open_files"], []),
        "diagnostics": json_loads(row["diagnostics"], []),
        "selection": json_loads(row["selection"]),
        "terminalContext": json_loads(row["terminal_context"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_mcp_server(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``mcp_servers`` al dict camelCase de la API, deserializando ``metadata``."""
    return {
        "id": row["id"],
        "command": row["command"],
        "transport": row["transport"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_integration(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``integrations`` al dict camelCase de la API, deserializando ``config``."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "config": json_loads(row["config"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_n8n_webhook_target(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta un target n8n al contrato público sin exponer valores de credencial."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "url": row["url"],
        "credentialRef": row["credential_ref"],
        "enabled": bool(row["enabled"]),
        "allowedEventTypes": json_loads(row["allowed_event_types"], []),
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_n8n_event_delivery(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una entrega n8n al contrato público, con payload y response ya saneados."""
    return {
        "id": row["id"],
        "targetId": row["target_id"],
        "projectId": row["project_id"],
        "eventType": row["event_type"],
        "subjectId": row["subject_id"],
        "status": row["status"],
        "statusCode": row["status_code"],
        "requestPayload": json_loads(row["request_payload"], {}),
        "responseBody": json_loads(row["response_body"], {}),
        "error": row["error"],
        "createdAt": row["created_at"],
    }


class IntegrationsRepository:
    """Acceso a datos del slice de integraciones sobre una conexión SQLite del caller.

    No abre ni cierra la conexión ni hace ``commit``: cada operación deja la transacción
    abierta para que el caller (dueño de la conexión) decida cuándo confirmarla o revertirla.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert_ide_connection(
        self,
        *,
        project_id: str,
        editor: str,
        workspace_root: str,
        status: str = "connected",
        open_files: list[Any] | None = None,
        diagnostics: list[Any] | None = None,
        selection: dict[str, Any] | None = None,
        terminal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inserta o actualiza la conexión IDE única por (proyecto, editor, workspace_root).

        Emite un único ``UPDATE`` (si ya existe la tripleta) o ``INSERT`` y devuelve la fila
        resultante releída. La escritura no se confirma aquí: el commit queda al caller.
        """
        timestamp = utc_now()
        row = self.connection.execute(
            "SELECT * FROM ide_connections WHERE project_id = ? AND editor = ? AND workspace_root = ?",
            (project_id, editor, workspace_root),
        ).fetchone()
        connection_id = row["id"] if row else f"ide-{uuid.uuid4()}"
        if row:
            self.connection.execute(
                """
                UPDATE ide_connections
                SET status = ?, open_files = ?, diagnostics = ?, selection = ?, terminal_context = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json_dumps(open_files or []),
                    json_dumps(diagnostics or []),
                    json_dumps(selection or {}),
                    json_dumps(terminal_context or {}),
                    timestamp,
                    connection_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO ide_connections
                    (id, project_id, editor, workspace_root, status, open_files, diagnostics,
                     selection, terminal_context, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    project_id,
                    editor,
                    workspace_root,
                    status,
                    json_dumps(open_files or []),
                    json_dumps(diagnostics or []),
                    json_dumps(selection or {}),
                    json_dumps(terminal_context or {}),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_ide_connection(connection_id)

    def get_ide_connection(self, connection_id: str) -> dict[str, Any]:
        """Devuelve la conexión IDE por id.

        Raises:
            KeyError: si no existe ninguna conexión con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM ide_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"IDE connection not found: {connection_id}")
        return row_to_ide_connection(row)

    def list_ide_connections(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista conexiones IDE (todas o filtradas por proyecto) ordenadas por actualización descendente."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM ide_connections WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM ide_connections ORDER BY updated_at DESC"
            ).fetchall()
        return [row_to_ide_connection(row) for row in rows]

    def list_integrations(self) -> list[dict[str, Any]]:
        """Lista las integraciones configuradas ordenadas por actualización descendente."""
        rows = self.connection.execute("SELECT * FROM integrations ORDER BY updated_at DESC").fetchall()
        return [row_to_integration(row) for row in rows]

    def register_mcp_server(
        self,
        *,
        server_id: str,
        command: str,
        transport: str = "stdio",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Registra el servidor MCP o reescribe el existente por id, dejándolo en estado 'registered'.

        Un solo ``INSERT ... ON CONFLICT(id) DO UPDATE`` actualiza comando/transport/metadata y
        reafirma el estado. La escritura no se confirma aquí: el commit queda al caller.
        """
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO mcp_servers (id, command, transport, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, 'registered', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                command = excluded.command,
                transport = excluded.transport,
                status = 'registered',
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (server_id, command, transport, json_dumps(metadata or {}), timestamp, timestamp),
        )
        return self.get_mcp_server(server_id)

    def get_mcp_server(self, server_id: str) -> dict[str, Any]:
        """Devuelve el servidor MCP por id.

        Raises:
            KeyError: si no hay ningún servidor registrado con ese id.
        """
        row = self.connection.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
        if not row:
            raise KeyError(f"MCP server not found: {server_id}")
        return row_to_mcp_server(row)

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        """Lista los servidores MCP registrados ordenados por actualización descendente."""
        rows = self.connection.execute("SELECT * FROM mcp_servers ORDER BY updated_at DESC").fetchall()
        return [row_to_mcp_server(row) for row in rows]

    def upsert_n8n_webhook_target(
        self,
        *,
        project_id: str,
        url: str,
        credential_ref: str,
        enabled: bool,
        allowed_event_types: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea o actualiza el target n8n único por proyecto+URL.

        La tabla guarda solo ``credential_ref``; el token real vive en el CredentialResolver/secret
        backend. ``allowed_event_types`` es la allowlist efectiva usada por ``emit-event``.
        """
        timestamp = utc_now()
        row = self.connection.execute(
            "SELECT id FROM n8n_webhook_targets WHERE project_id = ? AND url = ?",
            (project_id, url),
        ).fetchone()
        target_id = row["id"] if row else f"n8n-target-{uuid.uuid4()}"
        if row:
            self.connection.execute(
                """
                UPDATE n8n_webhook_targets
                SET credential_ref = ?, enabled = ?, allowed_event_types = ?, metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    credential_ref,
                    int(enabled),
                    json_dumps(allowed_event_types),
                    json_dumps(metadata or {}),
                    timestamp,
                    target_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO n8n_webhook_targets
                    (id, project_id, url, credential_ref, enabled, allowed_event_types, metadata,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    project_id,
                    url,
                    credential_ref,
                    int(enabled),
                    json_dumps(allowed_event_types),
                    json_dumps(metadata or {}),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_n8n_webhook_target(target_id)

    def get_n8n_webhook_target(self, target_id: str) -> dict[str, Any]:
        """Devuelve un target n8n por id.

        Raises:
            KeyError: si no existe.
        """
        row = self.connection.execute(
            "SELECT * FROM n8n_webhook_targets WHERE id = ?", (target_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"n8n webhook target not found: {target_id}")
        return row_to_n8n_webhook_target(row)

    def list_n8n_webhook_targets(
        self,
        *,
        project_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Lista targets n8n, opcionalmente por proyecto y solo habilitados."""
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM n8n_webhook_targets {where} ORDER BY updated_at DESC, rowid DESC",
            params,
        ).fetchall()
        return [row_to_n8n_webhook_target(row) for row in rows]

    def record_n8n_event_delivery(
        self,
        *,
        target_id: str,
        project_id: str,
        event_type: str,
        subject_id: str | None,
        status: str,
        status_code: int | None,
        request_payload: dict[str, Any],
        response_body: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        """Registra una entrega outbound hacia n8n con request/response ya seguros para persistencia."""
        delivery_id = f"n8n-delivery-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO n8n_event_deliveries
                (id, target_id, project_id, event_type, subject_id, status, status_code,
                 request_payload, response_body, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery_id,
                target_id,
                project_id,
                event_type,
                subject_id,
                status,
                status_code,
                json_dumps(request_payload),
                json_dumps(response_body or {}),
                error,
                utc_now(),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM n8n_event_deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()
        return row_to_n8n_event_delivery(row)
