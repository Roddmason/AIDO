"""Persistencia SQLite de seguridad: decisiones, grants, revisiones y perfiles de sandbox.

Encapsula el acceso a las tablas de gobernanza y la maquina de estados de los grants. Sobre
transacciones: cada metodo emite sentencias contra el ``connection`` recibido pero NO hace
commit; confia en que el caller (capa HTTP) cierra la transaccion. La sensibilidad la cubre
``redact_secrets`` sobre comando/razon/payload antes de escribir, de modo que la BD nunca
guarda secretos en claro. Invariante de los grants: el consumo es atomico via
``UPDATE ... WHERE status='active'`` con chequeo de ``rowcount``, lo que impide doble consumo
en condiciones de carrera; un grant inexistente hace que los getters lancen ``KeyError``.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.telemetry import record_policy_decision
from local_control_center.shared.time import add_millis, utc_now

GRANT_TTL_MS = 24 * 60 * 60 * 1000


def row_to_policy(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``permission_policies`` al dict camelCase de la API."""
    return {
        "id": row["id"],
        "name": row["name"],
        "profile": row["profile"],
        "rules": json_loads(row["rules"], []),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``permission_decisions`` al dict camelCase de la API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "workspaceId": row["workspace_id"],
        "agentId": row["agent_id"],
        "role": row["role"],
        "tool": row["tool"],
        "command": row["command"],
        "path": row["path"],
        "decision": row["decision"],
        "riskLevel": row["risk_level"],
        "reason": row["reason"],
        "payload": json_loads(row["payload"]),
        "createdAt": row["created_at"],
    }


def row_to_grant(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``permission_grants`` al dict de la API, tolerando esquemas viejos.

    Si faltan columnas nuevas (commandArgv/workspace/runtime/expiresAt) cae al ``payload`` JSON
    para mantener compatibilidad con grants creados antes de la migracion.
    """
    payload = json_loads(row["payload"])
    row_keys = set(row.keys())
    command_argv = (
        json_loads(row["command_argv"], []) if "command_argv" in row_keys else payload.get("commandArgv", [])
    )
    if not isinstance(command_argv, list):
        command_argv = []
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "jobId": row["job_id"],
        "actionRequestId": row["action_request_id"],
        "permissionDecisionId": row["permission_decision_id"],
        "agentId": row["agent_id"],
        "tool": row["tool"],
        "command": row["command"],
        "commandArgv": [str(item) for item in command_argv],
        "workspaceId": row["workspace_id"] if "workspace_id" in row_keys else payload.get("workspaceId"),
        "runtimeId": row["runtime_id"] if "runtime_id" in row_keys else payload.get("runtimeId"),
        "path": row["path"],
        "status": row["status"],
        "reason": row["reason"],
        "expiresAt": row["expires_at"] if "expires_at" in row_keys else payload.get("expiresAt"),
        "grantedBy": row["granted_by"],
        "grantedAt": row["granted_at"],
        "consumedAt": row["consumed_at"],
        "consumedByAgentRunId": row["consumed_by_agent_run_id"],
        "revokedAt": row["revoked_at"],
        "revokedBy": row["revoked_by"],
        "revokeReason": row["revoke_reason"],
        "payload": payload,
    }


def row_to_sandbox_profile(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``sandbox_profiles`` al dict camelCase de la API."""
    return {
        "id": row["id"],
        "name": row["name"],
        "allowedImages": json_loads(row["allowed_images"], []),
        "allowedNetworks": json_loads(row["allowed_networks"], []),
        "defaultNetwork": row["default_network"],
        "memory": row["memory"],
        "cpus": row["cpus"],
        "timeoutSeconds": row["timeout_seconds"],
        "status": row["status"],
        "revokedAt": row["revoked_at"],
        "revokedBy": row["revoked_by"],
        "revokeReason": row["revoke_reason"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_policy_revision(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``policy_revisions`` al dict camelCase de la API."""
    return {
        "id": row["id"],
        "subjectType": row["subject_type"],
        "subjectId": row["subject_id"],
        "version": row["version"],
        "reason": row["reason"],
        "actor": row["actor"],
        "previous": json_loads(row["previous_json"]),
        "updated": json_loads(row["updated_json"]),
        "changedFields": json_loads(row["changed_fields"], []),
        "createdAt": row["created_at"],
    }


def changed_fields(previous: dict[str, Any], updated: dict[str, Any]) -> list[str]:
    """Lista las claves cuyo valor cambio entre dos versiones, ignorando metadatos de timestamp.

    Sirve para registrar una revision solo cuando hubo un cambio real de configuracion, no por
    diferencias de ``updatedAt`` u otros campos de ciclo de vida.
    """
    ignored = {"createdAt", "updatedAt", "revokedAt", "revokedBy", "revokeReason"}
    keys = sorted((set(previous) | set(updated)) - ignored)
    return [key for key in keys if previous.get(key) != updated.get(key)]


def _paths_match(left: str | None, right: str | None) -> bool:
    if not left:
        return True
    if not right:
        return False
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except OSError:
        return left == right


def _normalized_argv(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _merged_sandbox_body(current: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": current["id"],
        "name": body.get("name", current["name"]),
        "allowedImages": body.get("allowedImages", current["allowedImages"]),
        "allowedNetworks": body.get("allowedNetworks", current["allowedNetworks"]),
        "defaultNetwork": body.get("defaultNetwork", current["defaultNetwork"]),
        "memory": body.get("memory", current["memory"]),
        "cpus": body.get("cpus", current["cpus"]),
        "timeoutSeconds": body.get("timeoutSeconds", current["timeoutSeconds"]),
        "status": body.get("status", current["status"]),
    }


class SecurityPolicyRepository:
    """Acceso a las tablas de seguridad sobre una conexion SQLite cuyo commit gestiona el caller.

    Agrupa lecturas y mutaciones de politicas, decisiones, grants y sandboxes. No abre ni cierra
    transacciones: cada metodo ejecuta sus ``INSERT/UPDATE`` y delega el commit en quien posee la
    conexion. Los getters lanzan ``KeyError`` cuando el recurso no existe.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_policies(self) -> list[dict[str, Any]]:
        """Lista todas las politicas de permisos ordenadas por id."""
        rows = self.connection.execute("SELECT * FROM permission_policies ORDER BY id ASC").fetchall()
        return [row_to_policy(row) for row in rows]

    def record_policy_revision(
        self,
        *,
        subject_type: str,
        subject_id: str,
        previous: dict[str, Any],
        updated: dict[str, Any],
        reason: str,
        actor: str = "operator",
    ) -> dict[str, Any] | None:
        """Inserta una revision de auditoria con el numero de version siguiente para el sujeto.

        Devuelve ``None`` (sin escribir) si no hubo cambios reales entre ``previous`` y ``updated``.
        La version se calcula con ``MAX(version)+1`` para el par (subject_type, subject_id) y el
        ``INSERT`` participa de la transaccion del caller.
        """
        fields = changed_fields(previous, updated)
        if not fields:
            return None
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1 AS next_version
            FROM policy_revisions
            WHERE subject_type = ? AND subject_id = ?
            """,
            (subject_type, subject_id),
        ).fetchone()
        revision_id = f"policy-revision-{uuid.uuid4()}"
        version = int(row["next_version"])
        self.connection.execute(
            """
            INSERT INTO policy_revisions
                (id, subject_type, subject_id, version, reason, actor, previous_json,
                 updated_json, changed_fields, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                subject_type,
                subject_id,
                version,
                reason,
                actor,
                json_dumps(previous),
                json_dumps(updated),
                json_dumps(fields),
                utc_now(),
            ),
        )
        return self.get_policy_revision(revision_id)

    def get_policy_revision(self, revision_id: str) -> dict[str, Any]:
        """Devuelve una revision por id; lanza ``KeyError`` si no existe."""
        row = self.connection.execute(
            "SELECT * FROM policy_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Policy revision not found: {revision_id}")
        return row_to_policy_revision(row)

    def list_policy_revisions(self, subject_id: str | None = None) -> list[dict[str, Any]]:
        """Lista revisiones (de un sujeto si se indica) de la mas reciente a la mas antigua."""
        if subject_id:
            rows = self.connection.execute(
                """
                SELECT * FROM policy_revisions
                WHERE subject_id = ?
                ORDER BY created_at DESC, version DESC
                """,
                (subject_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM policy_revisions ORDER BY created_at DESC, version DESC"
            ).fetchall()
        return [row_to_policy_revision(row) for row in rows]

    def record_decision(
        self,
        *,
        project_id: str | None,
        workspace_id: str | None,
        agent_id: str | None,
        role: str | None,
        tool: str | None,
        command: str | None,
        path: str | None,
        decision: str,
        risk_level: str,
        reason: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Guarda una decision de politica saneando comando, razon y payload.

        Aplica ``redact_secrets`` a comando/razon/payload antes del ``INSERT``, garantizando que
        la BD no almacene secretos. Tras escribir, relee la fila y emite telemetria de la decision.
        """
        decision_id = f"permission-decision-{uuid.uuid4()}"
        clean_command = str(redact_secrets(command or "")) if command is not None else None
        clean_reason = str(redact_secrets(reason))
        clean_payload = redact_secrets(payload)
        self.connection.execute(
            """
            INSERT INTO permission_decisions
                (id, project_id, workspace_id, agent_id, role, tool, command, path,
                 decision, risk_level, reason, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                project_id,
                workspace_id,
                agent_id,
                role,
                tool,
                clean_command,
                path,
                decision,
                risk_level,
                clean_reason,
                json_dumps(clean_payload),
                utc_now(),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM permission_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        decision_record = row_to_decision(row)
        record_policy_decision(self.connection, decision_record)
        return decision_record

    def list_decisions(
        self,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista decisiones (de un proyecto si se indica) de la mas reciente a la mas antigua.

        ``limit`` acota el resultado a las N decisiones mas recientes.
        """
        query = "SELECT * FROM permission_decisions ORDER BY created_at DESC"
        params: tuple[Any, ...] = ()
        if project_id:
            query = "SELECT * FROM permission_decisions WHERE project_id = ? ORDER BY created_at DESC"
            params = (project_id,)
        if limit is not None:
            query = f"{query} LIMIT ?"
            params = (*params, limit)
        rows = self.connection.execute(query, params).fetchall()
        return [row_to_decision(row) for row in rows]

    def upsert_sandbox_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta o actualiza un perfil de sandbox por id (semilla/seed idempotente).

        Usa ``ON CONFLICT(id) DO UPDATE`` con valores por defecto seguros (red ``none``, memoria
        2g, timeout 120s) para campos ausentes.
        """
        profile_id = body["id"]
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO sandbox_profiles
                (id, name, allowed_images, allowed_networks, default_network, memory,
                 cpus, timeout_seconds, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                allowed_images = excluded.allowed_images,
                allowed_networks = excluded.allowed_networks,
                default_network = excluded.default_network,
                memory = excluded.memory,
                cpus = excluded.cpus,
                timeout_seconds = excluded.timeout_seconds,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                body.get("name", profile_id),
                json_dumps(body.get("allowedImages") or []),
                json_dumps(body.get("allowedNetworks") or ["none"]),
                body.get("defaultNetwork", "none"),
                body.get("memory", "2g"),
                body.get("cpus", "2"),
                int(body.get("timeoutSeconds", 120)),
                body.get("status", "active"),
                timestamp,
                timestamp,
            ),
        )
        return self.get_sandbox_profile(profile_id)

    def get_sandbox_profile(self, profile_id: str = "default_docker") -> dict[str, Any]:
        """Devuelve un perfil de sandbox por id; lanza ``KeyError`` si no existe."""
        row = self.connection.execute("SELECT * FROM sandbox_profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            raise KeyError(f"Sandbox profile not found: {profile_id}")
        return row_to_sandbox_profile(row)

    def list_sandbox_profiles(self) -> list[dict[str, Any]]:
        """Lista todos los perfiles de sandbox ordenados por id."""
        rows = self.connection.execute("SELECT * FROM sandbox_profiles ORDER BY id ASC").fetchall()
        return [row_to_sandbox_profile(row) for row in rows]

    def update_sandbox_profile(self, profile_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch parcial a un perfil fusionandolo sobre el actual.

        Solo sobreescribe los campos presentes en ``body``; el resto conserva el valor vigente.
        Lanza ``KeyError`` si el perfil no existe.
        """
        current = self.get_sandbox_profile(profile_id)
        next_body = _merged_sandbox_body(current, body)
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE sandbox_profiles
            SET name = ?,
                allowed_images = ?,
                allowed_networks = ?,
                default_network = ?,
                memory = ?,
                cpus = ?,
                timeout_seconds = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_body["name"],
                json_dumps(next_body["allowedImages"]),
                json_dumps(next_body["allowedNetworks"]),
                next_body["defaultNetwork"],
                next_body["memory"],
                next_body["cpus"],
                int(next_body["timeoutSeconds"]),
                next_body["status"],
                timestamp,
                profile_id,
            ),
        )
        return self.get_sandbox_profile(profile_id)

    def revoke_sandbox_profile(
        self,
        profile_id: str,
        *,
        reason: str,
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Revoca un perfil de sandbox de forma idempotente, registrando actor y razon.

        El ``UPDATE`` esta condicionado a ``status='active'``, asi revocar dos veces no cambia el
        estado ni pisa los metadatos de la primera revocacion. Lanza ``KeyError`` si no existe.
        """
        profile = self.get_sandbox_profile(profile_id)
        timestamp = utc_now()
        if profile["status"] == "active":
            self.connection.execute(
                """
                UPDATE sandbox_profiles
                SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (timestamp, actor, reason, timestamp, profile_id),
            )
        return self.get_sandbox_profile(profile_id)

    def create_grant_from_action_request(
        self,
        *,
        action_request: dict[str, Any],
        reason: str,
        granted_by: str = "operator",
    ) -> dict[str, Any]:
        """Crea un grant ``active`` con TTL a partir de una solicitud de accion aprobada.

        Sanea comando/razon/payload con ``redact_secrets`` y fija el alcance exacto
        (tool/command/argv/workspace/runtime/path) que la futura ejecucion debera igualar. Lanza
        ``ValueError`` si la razon queda vacia; el ``expiresAt`` por defecto es ahora + 24h.
        """
        payload = redact_secrets(action_request.get("payload") or {})
        grant_id = f"permission-grant-{uuid.uuid4()}"
        timestamp = utc_now()
        clean_command = str(redact_secrets(action_request.get("command") or ""))
        clean_reason = str(redact_secrets(reason))
        if not clean_reason.strip():
            raise ValueError("Approval grant reason is required.")
        command_argv = _normalized_argv(action_request.get("commandArgv") or payload.get("commandArgv"))
        workspace_id = action_request.get("workspaceId") or payload.get("workspaceId")
        runtime_id = action_request.get("runtimeId") or payload.get("runtimeId")
        scoped_path = (
            payload.get("path") or action_request.get("workspacePath") or payload.get("workspacePath")
        )
        expires_at = action_request.get("expiresAt") or payload.get("expiresAt") or add_millis(GRANT_TTL_MS)
        self.connection.execute(
            """
            INSERT INTO permission_grants
                (id, project_id, job_id, action_request_id, permission_decision_id,
                 agent_id, tool, command, path, command_argv, workspace_id, runtime_id,
                 expires_at, status, reason, granted_by,
                 granted_at, consumed_at, consumed_by_agent_run_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL, NULL, ?)
            """,
            (
                grant_id,
                action_request["projectId"],
                action_request.get("jobId"),
                action_request["id"],
                payload.get("permissionDecisionId"),
                payload.get("agentId"),
                str(payload.get("tool") or action_request["actionType"]),
                clean_command,
                scoped_path,
                json_dumps(command_argv),
                workspace_id,
                runtime_id,
                expires_at,
                clean_reason,
                granted_by,
                timestamp,
                json_dumps(
                    redact_secrets(
                        {
                            **payload,
                            "actionType": action_request["actionType"],
                            "riskLevel": action_request["riskLevel"],
                            "commandArgv": command_argv,
                            "workspaceId": workspace_id,
                            "runtimeId": runtime_id,
                            "expiresAt": expires_at,
                        }
                    )
                ),
            ),
        )
        return self.get_grant(grant_id)

    def get_grant(self, grant_id: str) -> dict[str, Any]:
        """Devuelve un grant por id; lanza ``KeyError`` si no existe."""
        row = self.connection.execute("SELECT * FROM permission_grants WHERE id = ?", (grant_id,)).fetchone()
        if not row:
            raise KeyError(f"Permission grant not found: {grant_id}")
        return row_to_grant(row)

    def list_grants(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista grants (de un proyecto si se indica) del mas reciente al mas antiguo."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM permission_grants WHERE project_id = ? ORDER BY granted_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM permission_grants ORDER BY granted_at DESC"
            ).fetchall()
        return [row_to_grant(row) for row in rows]

    def validate_and_consume_grant(
        self,
        *,
        grant_id: str,
        project_id: str,
        job_id: str | None,
        agent_id: str | None,
        tool: str,
        command: str,
        command_argv: list[str] | None = None,
        workspace_id: str | None = None,
        runtime_id: str | None = None,
        path: str | None,
        agent_run_id: str,
    ) -> dict[str, Any]:
        """Valida un grant contra el contexto de ejecucion y lo consume atomicamente si calza.

        Invariante de seguridad: solo retorna ``valid=True`` si el grant esta ``active``, no expiro
        y coincide exactamente en proyecto, job, agente, tool, comando, argv, workspace, runtime y
        path; cualquier discrepancia lo rechaza sin consumirlo. El consumo usa
        ``UPDATE ... WHERE status='active'`` y verifica ``rowcount==1``, de modo que dos consumos
        concurrentes del mismo grant no pueden ambos ganar (proteccion contra doble uso).
        """
        try:
            grant = self.get_grant(grant_id)
        except KeyError:
            return {"valid": False, "reason": "Permission grant not found.", "grant": None}

        if grant["status"] != "active":
            return {
                "valid": False,
                "reason": f"Permission grant is already {grant['status']}.",
                "grant": grant,
            }
        if grant.get("expiresAt") and grant["expiresAt"] <= utc_now():
            self.connection.execute(
                "UPDATE permission_grants SET status = 'expired' WHERE id = ? AND status = 'active'",
                (grant_id,),
            )
            return {
                "valid": False,
                "reason": "Permission grant expired.",
                "grant": self.get_grant(grant_id),
            }
        if grant["projectId"] != project_id:
            return {"valid": False, "reason": "Permission grant project mismatch.", "grant": grant}
        if grant.get("jobId") != job_id:
            return {"valid": False, "reason": "Permission grant job mismatch.", "grant": grant}
        if grant.get("agentId") != agent_id:
            return {"valid": False, "reason": "Permission grant agent mismatch.", "grant": grant}
        if grant["tool"] != tool:
            return {"valid": False, "reason": "Permission grant tool mismatch.", "grant": grant}
        if grant["command"] != command:
            return {"valid": False, "reason": "Permission grant command mismatch.", "grant": grant}
        requested_argv = _normalized_argv(command_argv)
        if grant.get("commandArgv") != requested_argv:
            return {"valid": False, "reason": "Permission grant argv mismatch.", "grant": grant}
        if grant.get("workspaceId") and grant.get("workspaceId") != workspace_id:
            return {"valid": False, "reason": "Permission grant workspace mismatch.", "grant": grant}
        if grant.get("runtimeId") and grant.get("runtimeId") != runtime_id:
            return {"valid": False, "reason": "Permission grant runtime mismatch.", "grant": grant}
        if not _paths_match(grant.get("path"), path):
            return {"valid": False, "reason": "Permission grant path mismatch.", "grant": grant}

        timestamp = utc_now()
        cursor = self.connection.execute(
            """
            UPDATE permission_grants
            SET status = 'consumed', consumed_at = ?, consumed_by_agent_run_id = ?
            WHERE id = ? AND status = 'active'
            """,
            (timestamp, agent_run_id, grant_id),
        )
        if cursor.rowcount != 1:
            refreshed = self.get_grant(grant_id)
            return {
                "valid": False,
                "reason": f"Permission grant is already {refreshed['status']}.",
                "grant": refreshed,
            }
        return {"valid": True, "reason": "Permission grant consumed.", "grant": self.get_grant(grant_id)}

    def revoke_grant(
        self,
        grant_id: str,
        *,
        reason: str,
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Revoca un grant de forma idempotente, registrando actor y razon.

        El ``UPDATE`` se condiciona a ``status='active'``, asi revocar un grant ya consumido o ya
        revocado no altera su estado. Lanza ``KeyError`` si el grant no existe.
        """
        grant = self.get_grant(grant_id)
        timestamp = utc_now()
        if grant["status"] == "active":
            self.connection.execute(
                """
                UPDATE permission_grants
                SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_reason = ?
                WHERE id = ? AND status = 'active'
                """,
                (timestamp, actor, reason, grant_id),
            )
        return self.get_grant(grant_id)
