"""Persistencia SQLite del slice de backlog sobre la conexión del caller.

Cubre las siete entidades del slice: epics, user stories, criterios de aceptación, dependencias
entre historias, agent tasks, dependencias entre tareas y asignaciones de agente. La user story
modela el **valor de usuario** y es agnóstica al rol (no tiene columna de rol: no se duplica una
HU por disciplina); el rol vive en ``agent_tasks``, que representan el trabajo técnico de los
agentes, y el reparto de ese trabajo vive en ``agent_assignments``. Cada entidad tiene su propia
tabla con columnas de dominio reales (nunca embebida en ``metadata``), siempre project-scoped, y
enlazada por referencias explícitas (``epic_id``, ``story_id``, ``task_id``, ``depends_on_*_id``,
``agent_id``) para que todo registro sea trazable. Los mapeadores ``row_to_*`` proyectan cada fila
al dict camelCase del contrato.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver ``shared/db.py``)
y estos métodos NO abren transacciones propias; cada ``execute`` se confirma de inmediato. Las
operaciones de una sola sentencia (``create_*``, ``get_*``, ``list_*``, ``delete_*``) son atómicas
por sí mismas; las que calculan el siguiente ``sequence`` antes de insertar
(``create_acceptance_criterion``) solo son atómicas si el caller las agrupa en una única
``immediate_transaction``, que además evita colisiones en ``UNIQUE(story_id, sequence)``.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import nullcontext
from typing import Any

from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

ASSIGNMENT_START_STATUSES = {"active", "in_progress", "running"}
IMPLEMENTATION_REVIEW_ROLES = {
    "backend_engineer",
    "frontend_engineer",
    "developer",
    "implementer",
    "devops",
    "release_manager",
}
ACCEPTED_HANDOFF_STATUSES = {"accepted", "resolved", "completed"}
APPROVED_REVIEW_STATUSES = {"approved", "accepted"}
TECHNICAL_USER_STORY_KEYS = {
    "agentId",
    "agentIds",
    "agentRole",
    "agentTask",
    "implementationTask",
    "role",
    "taskRole",
    "technicalTask",
}


def _as_bool(value: Any) -> bool:
    return value is True or value == 1 or (isinstance(value, str) and value.lower() in {"1", "true", "yes"})


def _normalize_schema(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("type") == "object":
        return value
    return default


def _default_assignment_input_schema(task: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["projectId", "taskId", "storyId", "role", "acceptanceCriteria"],
        "properties": {
            "projectId": {"type": "string", "const": task["projectId"]},
            "taskId": {"type": "string", "const": task["id"]},
            "storyId": {"type": "string", "const": task["storyId"]},
            "role": {"type": "string", "const": role},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "acceptanceCriteria": {"type": "array", "items": {"type": "string"}},
            "upstreamArtifacts": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def _default_assignment_output_schema(role: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["artifactId", "summary", "status", "evidenceRefs"],
        "properties": {
            "artifactId": {"type": "string"},
            "summary": {"type": "string"},
            "status": {"type": "string", "enum": ["completed", "blocked", "failed"]},
            "role": {"type": "string", "const": role},
            "changes": {"type": "array", "items": {"type": "string"}},
            "evidenceRefs": {"type": "array", "items": {"type": "string"}},
            "riskNotes": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def _reject_free_form_prompt(body: dict[str, Any]) -> None:
    blocked_keys = {"prompt", "freeFormPrompt", "sharedPrompt", "shared_prompt"}
    if any(str(body.get(key) or "").strip() for key in blocked_keys):
        raise ValueError(
            "Agent assignments cannot collaborate through a shared free-form prompt; "
            "use inputSchema, outputSchema and canonical artifacts."
        )
    metadata = body.get("metadata")
    if isinstance(metadata, dict) and any(str(metadata.get(key) or "").strip() for key in blocked_keys):
        raise ValueError(
            "Agent assignments cannot collaborate through a shared free-form prompt; "
            "use inputSchema, outputSchema and canonical artifacts."
        )


def _reject_technical_user_story_fields(body: dict[str, Any]) -> None:
    forbidden = sorted(key for key in TECHNICAL_USER_STORY_KEYS if key in body)
    if forbidden:
        raise ValueError(
            "User stories must describe user value, not agent work; "
            f"move {', '.join(forbidden)} to agent_tasks."
        )


def _criteria_from_body(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("acceptanceCriteria must be a list.")
    criteria: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            criterion = str(item.get("criterion") or "").strip()
        else:
            criterion = str(item or "").strip()
        if not criterion:
            raise ValueError(f"acceptanceCriteria[{index}] must be a non-empty string.")
        criteria.append(criterion)
    return criteria


def row_to_epic(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``epics`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "priority": row["priority"],
        "owner": row["owner"],
        "version": row["version"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_user_story(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``user_stories`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "epicId": row["epic_id"],
        "title": row["title"],
        "asA": row["as_a"],
        "iWant": row["i_want"],
        "soThat": row["so_that"],
        "description": row["description"],
        "status": row["status"],
        "priority": row["priority"],
        "businessValue": row["business_value"],
        "storyPoints": row["story_points"],
        "owner": row["owner"],
        "version": row["version"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_acceptance_criterion(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``acceptance_criteria`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "storyId": row["story_id"],
        "sequence": row["sequence"],
        "criterion": row["criterion"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_story_dependency(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``story_dependencies`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "storyId": row["story_id"],
        "dependsOnStoryId": row["depends_on_story_id"],
        "type": row["type"],
        "reason": row["reason"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


def row_to_agent_task(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``agent_tasks`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "storyId": row["story_id"],
        "title": row["title"],
        "description": row["description"],
        "role": row["role"],
        "category": row["category"],
        "status": row["status"],
        "priority": row["priority"],
        "estimateHours": row["estimate_hours"],
        "version": row["version"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_task_dependency(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``task_dependencies`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "taskId": row["task_id"],
        "dependsOnTaskId": row["depends_on_task_id"],
        "type": row["type"],
        "reason": row["reason"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


def row_to_agent_assignment(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``agent_assignments`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "taskId": row["task_id"],
        "agentId": row["agent_id"],
        "role": row["role"],
        "status": row["status"],
        "assignedBy": row["assigned_by"],
        "assignedAt": row["assigned_at"],
        "releasedAt": row["released_at"],
        "inputSchema": json_loads(row["input_schema"], {}) if "input_schema" in row.keys() else {},
        "outputSchema": json_loads(row["output_schema"], {}) if "output_schema" in row.keys() else {},
        "canonicalArtifactId": row["canonical_artifact_id"] if "canonical_artifact_id" in row.keys() else "",
        "handoffId": row["handoff_id"] if "handoff_id" in row.keys() else "",
        "reviewRequired": _as_bool(row["review_required"]) if "review_required" in row.keys() else False,
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_iteration(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``iterations`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "briefId": row["brief_id"],
        "title": row["title"],
        "goal": row["goal"],
        "status": row["status"],
        "storyIds": json_loads(row["story_ids"], []),
        "workspaceStrategy": row["workspace_strategy"],
        "qualityGates": json_loads(row["quality_gates"], []),
        "securityGates": json_loads(row["security_gates"], []),
        "estimatedCost": json_loads(row["estimated_cost"]),
        "runtimes": json_loads(row["runtimes"], []),
        "taskCount": row["task_count"],
        "assignmentCount": row["assignment_count"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_assignment_handoff(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``assignment_handoffs`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "assignmentId": row["assignment_id"],
        "artifactId": row["artifact_id"],
        "fromAgentId": row["from_agent_id"],
        "toAgentId": row["to_agent_id"],
        "status": row["status"],
        "reviewRequired": _as_bool(row["review_required"]),
        "blockedReason": row["blocked_reason"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_assignment_review(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``assignment_reviews`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "assignmentId": row["assignment_id"],
        "handoffId": row["handoff_id"],
        "reviewerAgentId": row["reviewer_agent_id"],
        "policyRequired": _as_bool(row["policy_required"]),
        "status": row["status"],
        "decision": row["decision"],
        "findings": json_loads(row["findings"], []),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "resolvedAt": row["resolved_at"],
    }


def row_to_assignment_conflict(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``assignment_conflicts`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "assignmentId": row["assignment_id"],
        "handoffId": row["handoff_id"],
        "status": row["status"],
        "raisedBy": row["raised_by"],
        "disagreement": row["disagreement"],
        "finalResolution": row["final_resolution"],
        "resolvedBy": row["resolved_by"],
        "resolvedAt": row["resolved_at"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class BacklogRepository:
    """Acceso a datos del slice de backlog sobre la conexión SQLite del caller.

    Concentra la persistencia de las siete entidades del slice. No abre ni cierra transacciones:
    ejecuta sobre la ``connection`` recibida (autocommit por sentencia) y delega en el caller la
    atomicidad multi-sentencia (ver el docstring del módulo).
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _transaction(self):
        return nullcontext() if self.connection.in_transaction else immediate_transaction(self.connection)

    def _assignment_requires_review(self, body: dict[str, Any], role: str) -> bool:
        if "reviewRequired" in body:
            return _as_bool(body.get("reviewRequired"))
        metadata = body.get("metadata")
        if isinstance(metadata, dict):
            policy = metadata.get("policy")
            if isinstance(policy, dict) and "requiresReview" in policy:
                return _as_bool(policy.get("requiresReview"))
            if "requiresReview" in metadata:
                return _as_bool(metadata.get("requiresReview"))
        return role in IMPLEMENTATION_REVIEW_ROLES

    def _insert_assignment_handoff(
        self,
        *,
        project_id: str,
        assignment_id: str,
        artifact_id: str,
        from_agent_id: str,
        to_agent_id: str,
        review_required: bool,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handoff_id = f"assignment-handoff-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO assignment_handoffs
                (id, project_id, assignment_id, artifact_id, from_agent_id, to_agent_id, status,
                 review_required, blocked_reason, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, '', ?, ?, ?)
            """,
            (
                handoff_id,
                project_id,
                assignment_id,
                artifact_id,
                from_agent_id,
                to_agent_id,
                1 if review_required else 0,
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO agent_handoffs
                (id, project_id, assignment_id, artifact_id, from_agent_id, to_agent_id, status,
                 review_required, blocked_reason, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, '', ?, ?, ?)
            """,
            (
                handoff_id,
                project_id,
                assignment_id,
                artifact_id,
                from_agent_id,
                to_agent_id,
                1 if review_required else 0,
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_assignment_handoff(handoff_id)

    def _insert_assignment_review(
        self,
        *,
        project_id: str,
        assignment_id: str,
        handoff_id: str,
        reviewer_agent_id: str,
        policy_required: bool,
    ) -> dict[str, Any]:
        review_id = f"assignment-review-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO assignment_reviews
                (id, project_id, assignment_id, handoff_id, reviewer_agent_id, policy_required,
                 status, decision, findings, created_at, updated_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', '', '[]', ?, ?, NULL)
            """,
            (
                review_id,
                project_id,
                assignment_id,
                handoff_id,
                reviewer_agent_id,
                1 if policy_required else 0,
                timestamp,
                timestamp,
            ),
        )
        return self.get_assignment_review(review_id)

    def create_epic(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un epic (id ``epic-<uuid>``, versión 1) y devuelve el registro creado."""
        epic_id = f"epic-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO epics
                (id, project_id, title, description, status, priority, owner, version,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                epic_id,
                body["projectId"],
                body["title"],
                body.get("description", ""),
                body.get("status", "draft"),
                body.get("priority", "medium"),
                body.get("owner", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_epic(epic_id)

    def get_epic(self, epic_id: str) -> dict[str, Any]:
        """Recupera un epic por id.

        Raises:
            KeyError: si no existe ningún epic con ese id.
        """
        row = self.connection.execute("SELECT * FROM epics WHERE id = ?", (epic_id,)).fetchone()
        if not row:
            raise KeyError(f"Epic not found: {epic_id}")
        return row_to_epic(row)

    def list_epics(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista epics (todos o por proyecto), más recientes primero."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM epics WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM epics ORDER BY updated_at DESC").fetchall()
        return [row_to_epic(row) for row in rows]

    def update_epic(self, epic_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre un epic, incrementa su versión y devuelve el registro.

        Raises:
            KeyError: si el epic no existe.
        """
        current = self.get_epic(epic_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE epics
            SET title = ?, description = ?, status = ?, priority = ?, owner = ?, version = ?,
                metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["title"],
                next_value["description"],
                next_value["status"],
                next_value["priority"],
                next_value["owner"],
                current["version"] + 1,
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                epic_id,
            ),
        )
        return self.get_epic(epic_id)

    def create_user_story(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una user story (id ``user-story-<uuid>``, versión 1) y devuelve el registro.

        Modela valor de usuario con ``asA``/``iWant``/``soThat``; no lleva rol técnico: el trabajo
        por disciplina se descompone en ``agent_tasks``, evitando duplicar la HU por cada rol.
        """
        _reject_technical_user_story_fields(body)
        criteria = _criteria_from_body(body.get("acceptanceCriteria"))
        if not criteria:
            raise ValueError("User story requires at least one acceptance criterion.")
        story_id = f"user-story-{uuid.uuid4()}"
        timestamp = utc_now()
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO user_stories
                    (id, project_id, epic_id, title, as_a, i_want, so_that, description, status,
                     priority, business_value, story_points, owner, version, metadata,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    story_id,
                    body["projectId"],
                    body["epicId"],
                    body["title"],
                    body.get("asA", ""),
                    body.get("iWant", ""),
                    body.get("soThat", ""),
                    body.get("description", ""),
                    body.get("status", "draft"),
                    body.get("priority", "medium"),
                    body.get("businessValue", "medium"),
                    body.get("storyPoints"),
                    body.get("owner", ""),
                    json_dumps(body.get("metadata") or {}),
                    timestamp,
                    timestamp,
                ),
            )
            for criterion in criteria:
                self.create_acceptance_criterion(
                    {
                        "projectId": body["projectId"],
                        "storyId": story_id,
                        "criterion": criterion,
                        "metadata": body.get("acceptanceCriteriaMetadata") or {},
                    }
                )
        return self.get_user_story(story_id)

    def get_user_story(self, story_id: str) -> dict[str, Any]:
        """Recupera una user story por id.

        Raises:
            KeyError: si no existe ninguna user story con ese id.
        """
        row = self.connection.execute("SELECT * FROM user_stories WHERE id = ?", (story_id,)).fetchone()
        if not row:
            raise KeyError(f"User story not found: {story_id}")
        return row_to_user_story(row)

    def list_user_stories(
        self, project_id: str | None = None, epic_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista user stories filtrando por proyecto y/o epic, más recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if epic_id:
            conditions.append("epic_id = ?")
            params.append(epic_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM user_stories {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [row_to_user_story(row) for row in rows]

    def update_user_story(self, story_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una user story, incrementa su versión y devuelve el registro.

        Raises:
            KeyError: si la user story no existe.
        """
        _reject_technical_user_story_fields(body)
        current = self.get_user_story(story_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE user_stories
            SET title = ?, as_a = ?, i_want = ?, so_that = ?, description = ?, status = ?,
                priority = ?, business_value = ?, story_points = ?, owner = ?, version = ?,
                metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["title"],
                next_value["asA"],
                next_value["iWant"],
                next_value["soThat"],
                next_value["description"],
                next_value["status"],
                next_value["priority"],
                next_value["businessValue"],
                next_value.get("storyPoints"),
                next_value["owner"],
                current["version"] + 1,
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                story_id,
            ),
        )
        return self.get_user_story(story_id)

    def create_acceptance_criterion(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un criterio de aceptación asignándole el siguiente ``sequence`` de su historia.

        El cálculo de ``MAX(sequence)+1`` y el INSERT solo son atómicos bajo una
        ``immediate_transaction`` del caller, que además evita colisiones en ``UNIQUE(story_id,
        sequence)``.
        """
        criterion_id = f"acceptance-criterion-{uuid.uuid4()}"
        timestamp = utc_now()
        story_id = body["storyId"]
        story = self.get_user_story(story_id)
        if story["projectId"] != body["projectId"]:
            raise ValueError("Acceptance criterion projectId must match its user story.")
        criterion = str(body["criterion"]).strip()
        if not criterion:
            raise ValueError("Acceptance criterion requires a non-empty criterion.")
        next_sequence = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM acceptance_criteria WHERE story_id = ?",
            (story_id,),
        ).fetchone()["next"]
        self.connection.execute(
            """
            INSERT INTO acceptance_criteria
                (id, project_id, story_id, sequence, criterion, status, metadata,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                criterion_id,
                body["projectId"],
                story_id,
                next_sequence,
                criterion,
                body.get("status", "pending"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_acceptance_criterion(criterion_id)

    def get_acceptance_criterion(self, criterion_id: str) -> dict[str, Any]:
        """Recupera un criterio de aceptación por id.

        Raises:
            KeyError: si no existe ningún criterio con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM acceptance_criteria WHERE id = ?", (criterion_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Acceptance criterion not found: {criterion_id}")
        return row_to_acceptance_criterion(row)

    def list_acceptance_criteria(
        self, story_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista criterios de aceptación por historia y/o proyecto en orden estable."""
        conditions: list[str] = []
        params: list[Any] = []
        if story_id:
            conditions.append("story_id = ?")
            params.append(story_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM acceptance_criteria {where} ORDER BY story_id ASC, sequence ASC",
            params,
        ).fetchall()
        return [row_to_acceptance_criterion(row) for row in rows]

    def update_acceptance_criterion(self, criterion_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre un criterio (criterion, status, metadata).

        Raises:
            KeyError: si el criterio no existe.
        """
        current = self.get_acceptance_criterion(criterion_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE acceptance_criteria
            SET criterion = ?, status = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["criterion"],
                next_value["status"],
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                criterion_id,
            ),
        )
        return self.get_acceptance_criterion(criterion_id)

    def create_story_dependency(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una arista de dependencia entre dos historias y la devuelve.

        Raises:
            ValueError: si una historia se declara dependiente de sí misma.
        """
        story_id = body["storyId"]
        depends_on_story_id = body["dependsOnStoryId"]
        if story_id == depends_on_story_id:
            raise ValueError(f"Story cannot depend on itself: {story_id}")
        story = self.get_user_story(story_id)
        depends_on = self.get_user_story(depends_on_story_id)
        if story["projectId"] != body["projectId"] or depends_on["projectId"] != body["projectId"]:
            raise ValueError("Story dependency projectId must match both stories.")
        dependency_id = f"story-dependency-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO story_dependencies
                (id, project_id, story_id, depends_on_story_id, type, reason, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dependency_id,
                body["projectId"],
                story_id,
                depends_on_story_id,
                body.get("type", "blocks"),
                body.get("reason", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
            ),
        )
        return self.get_story_dependency(dependency_id)

    def get_story_dependency(self, dependency_id: str) -> dict[str, Any]:
        """Recupera una dependencia entre historias por id.

        Raises:
            KeyError: si no existe ninguna dependencia con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM story_dependencies WHERE id = ?", (dependency_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Story dependency not found: {dependency_id}")
        return row_to_story_dependency(row)

    def list_story_dependencies(
        self, story_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista dependencias entre historias filtrando por historia origen y/o proyecto."""
        conditions: list[str] = []
        params: list[Any] = []
        if story_id:
            conditions.append("story_id = ?")
            params.append(story_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM story_dependencies {where} ORDER BY created_at ASC", params
        ).fetchall()
        return [row_to_story_dependency(row) for row in rows]

    def delete_story_dependency(self, dependency_id: str) -> None:
        """Elimina una dependencia entre historias por id (idempotente: no falla si no existe)."""
        self.connection.execute("DELETE FROM story_dependencies WHERE id = ?", (dependency_id,))

    def create_agent_task(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una agent task (id ``agent-task-<uuid>``, versión 1) y devuelve el registro.

        ``role`` captura la disciplina del trabajo técnico (frontend, backend, qa, …) a nivel de
        tarea; descomponer una historia en varias tareas con distinto rol sustituye a duplicar la HU.
        """
        story = self.get_user_story(body["storyId"])
        if story["projectId"] != body["projectId"]:
            raise ValueError("Agent task projectId must match its user story.")
        task_id = f"agent-task-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO agent_tasks
                (id, project_id, story_id, title, description, role, category, status, priority,
                 estimate_hours, version, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                task_id,
                body["projectId"],
                body["storyId"],
                body["title"],
                body.get("description", ""),
                body["role"],
                body.get("category", "implementation"),
                body.get("status", "todo"),
                body.get("priority", "medium"),
                body.get("estimateHours"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_agent_task(task_id)

    def get_agent_task(self, task_id: str) -> dict[str, Any]:
        """Recupera una agent task por id.

        Raises:
            KeyError: si no existe ninguna agent task con ese id.
        """
        row = self.connection.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Agent task not found: {task_id}")
        return row_to_agent_task(row)

    def list_agent_tasks(
        self,
        project_id: str | None = None,
        story_id: str | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista agent tasks filtrando por proyecto, historia y/o rol, más recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if story_id:
            conditions.append("story_id = ?")
            params.append(story_id)
        if role:
            conditions.append("role = ?")
            params.append(role)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM agent_tasks {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [row_to_agent_task(row) for row in rows]

    def update_agent_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una agent task, incrementa su versión y devuelve el registro.

        Raises:
            KeyError: si la agent task no existe.
        """
        current = self.get_agent_task(task_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE agent_tasks
            SET title = ?, description = ?, role = ?, category = ?, status = ?, priority = ?,
                estimate_hours = ?, version = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["title"],
                next_value["description"],
                next_value["role"],
                next_value["category"],
                next_value["status"],
                next_value["priority"],
                next_value.get("estimateHours"),
                current["version"] + 1,
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                task_id,
            ),
        )
        return self.get_agent_task(task_id)

    def create_task_dependency(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una arista de dependencia entre dos tareas y la devuelve.

        Raises:
            ValueError: si una tarea se declara dependiente de sí misma.
        """
        task_id = body["taskId"]
        depends_on_task_id = body["dependsOnTaskId"]
        if task_id == depends_on_task_id:
            raise ValueError(f"Task cannot depend on itself: {task_id}")
        task = self.get_agent_task(task_id)
        depends_on = self.get_agent_task(depends_on_task_id)
        if task["projectId"] != body["projectId"] or depends_on["projectId"] != body["projectId"]:
            raise ValueError("Task dependency projectId must match both tasks.")
        dependency_id = f"task-dependency-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO task_dependencies
                (id, project_id, task_id, depends_on_task_id, type, reason, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dependency_id,
                body["projectId"],
                task_id,
                depends_on_task_id,
                body.get("type", "blocks"),
                body.get("reason", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
            ),
        )
        return self.get_task_dependency(dependency_id)

    def get_task_dependency(self, dependency_id: str) -> dict[str, Any]:
        """Recupera una dependencia entre tareas por id.

        Raises:
            KeyError: si no existe ninguna dependencia con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM task_dependencies WHERE id = ?", (dependency_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Task dependency not found: {dependency_id}")
        return row_to_task_dependency(row)

    def list_task_dependencies(
        self, task_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista dependencias entre tareas filtrando por tarea origen y/o proyecto."""
        conditions: list[str] = []
        params: list[Any] = []
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM task_dependencies {where} ORDER BY created_at ASC", params
        ).fetchall()
        return [row_to_task_dependency(row) for row in rows]

    def delete_task_dependency(self, dependency_id: str) -> None:
        """Elimina una dependencia entre tareas por id (idempotente: no falla si no existe)."""
        self.connection.execute("DELETE FROM task_dependencies WHERE id = ?", (dependency_id,))

    def create_agent_assignment(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una asignación de un agente a una tarea (id ``agent-assignment-<uuid>``) y la devuelve.

        Reparte el trabajo técnico: vincula un ``agentId`` con una ``taskId`` en un ``role`` dado.
        Una tarea admite varias asignaciones (p. ej. ejecutor y revisor) a lo largo de su ciclo.
        """
        _reject_free_form_prompt(body)
        task = self.get_agent_task(body["taskId"])
        assignment_id = f"agent-assignment-{uuid.uuid4()}"
        role = str(body.get("role") or task["role"])
        input_schema = _normalize_schema(
            body.get("inputSchema"), _default_assignment_input_schema(task, role)
        )
        output_schema = _normalize_schema(body.get("outputSchema"), _default_assignment_output_schema(role))
        review_required = self._assignment_requires_review(body, role)
        timestamp = utc_now()
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO agent_assignments
                    (id, project_id, task_id, agent_id, role, status, assigned_by, assigned_at,
                     released_at, input_schema, output_schema, canonical_artifact_id, handoff_id,
                     review_required, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)
                """,
                (
                    assignment_id,
                    body["projectId"],
                    body["taskId"],
                    body["agentId"],
                    role,
                    body.get("status", "proposed"),
                    body.get("assignedBy", ""),
                    body.get("assignedAt") or timestamp,
                    body.get("releasedAt"),
                    json_dumps(input_schema),
                    json_dumps(output_schema),
                    1 if review_required else 0,
                    json_dumps(body.get("metadata") or {}),
                    timestamp,
                    timestamp,
                ),
            )
            artifact_metadata = {
                "artifactContract": "agent_assignment_canonical",
                "assignmentId": assignment_id,
                "projectId": body["projectId"],
                "taskId": body["taskId"],
                "storyId": task["storyId"],
                "agentId": body["agentId"],
                "role": role,
                "inputSchema": input_schema,
                "outputSchema": output_schema,
            }
            artifact_hash = hashlib.sha256(json_dumps(artifact_metadata).encode("utf-8")).hexdigest()
            artifact = EvidenceRepository(self.connection).create_artifact(
                project_id=body["projectId"],
                evidence_package_id=None,
                kind="generic_artifact",
                path=f"aido://assignments/{assignment_id}/canonical-artifact",
                content_hash=artifact_hash,
                metadata=artifact_metadata,
            )
            handoff = self._insert_assignment_handoff(
                project_id=body["projectId"],
                assignment_id=assignment_id,
                artifact_id=artifact["id"],
                from_agent_id=str(body.get("assignedBy") or "system"),
                to_agent_id=str(body["agentId"]),
                review_required=review_required,
                metadata={"taskId": body["taskId"], "role": role},
            )
            if review_required:
                self._insert_assignment_review(
                    project_id=body["projectId"],
                    assignment_id=assignment_id,
                    handoff_id=handoff["id"],
                    reviewer_agent_id=str(body.get("reviewerAgentId") or "policy_reviewer"),
                    policy_required=True,
                )
            self.connection.execute(
                """
                UPDATE agent_assignments
                SET canonical_artifact_id = ?, handoff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (artifact["id"], handoff["id"], utc_now(), assignment_id),
            )
            return self.get_agent_assignment(assignment_id)

    def get_agent_assignment(self, assignment_id: str) -> dict[str, Any]:
        """Recupera una asignación de agente por id.

        Raises:
            KeyError: si no existe ninguna asignación con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM agent_assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Agent assignment not found: {assignment_id}")
        return row_to_agent_assignment(row)

    def list_agent_assignments(
        self,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista asignaciones de agente filtrando por tarea, agente y/o proyecto, recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM agent_assignments {where} ORDER BY created_at DESC", params
        ).fetchall()
        return [row_to_agent_assignment(row) for row in rows]

    def update_agent_assignment(self, assignment_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una asignación (status, role, releasedAt, metadata).

        ``assignedBy`` y ``assignedAt`` son inmutables tras la creación; liberar la asignación se
        expresa fijando ``status`` y ``releasedAt`` en el patch.

        Raises:
            KeyError: si la asignación no existe.
        """
        current = self.get_agent_assignment(assignment_id)
        next_value = {**current, **body}
        if str(next_value["status"]) in ASSIGNMENT_START_STATUSES:
            self._assert_assignment_can_begin(current)
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE agent_assignments
            SET role = ?, status = ?, released_at = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["role"],
                next_value["status"],
                next_value.get("releasedAt"),
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                assignment_id,
            ),
        )
        return self.get_agent_assignment(assignment_id)

    def get_assignment_handoff(self, handoff_id: str) -> dict[str, Any]:
        """Recupera un handoff de asignación por id."""
        row = self.connection.execute(
            "SELECT * FROM assignment_handoffs WHERE id = ?", (handoff_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Assignment handoff not found: {handoff_id}")
        return row_to_assignment_handoff(row)

    def list_assignment_handoffs(
        self,
        *,
        assignment_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista handoffs de asignación filtrando por asignación y/o proyecto."""
        conditions: list[str] = []
        params: list[Any] = []
        if assignment_id:
            conditions.append("assignment_id = ?")
            params.append(assignment_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM assignment_handoffs {where} ORDER BY created_at ASC", params
        ).fetchall()
        return [row_to_assignment_handoff(row) for row in rows]

    def update_assignment_handoff(self, handoff_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Actualiza estado, motivo de bloqueo y metadata de un handoff."""
        current = self.get_assignment_handoff(handoff_id)
        next_value = {**current, **body}
        self.connection.execute(
            """
            UPDATE assignment_handoffs
            SET status = ?, blocked_reason = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["status"],
                next_value.get("blockedReason") or "",
                json_dumps(next_value.get("metadata") or {}),
                utc_now(),
                handoff_id,
            ),
        )
        return self.get_assignment_handoff(handoff_id)

    def get_assignment_review(self, review_id: str) -> dict[str, Any]:
        """Recupera un review de asignación por id."""
        row = self.connection.execute(
            "SELECT * FROM assignment_reviews WHERE id = ?", (review_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Assignment review not found: {review_id}")
        return row_to_assignment_review(row)

    def list_assignment_reviews(
        self,
        *,
        assignment_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista reviews de asignación filtrando por asignación y/o proyecto."""
        conditions: list[str] = []
        params: list[Any] = []
        if assignment_id:
            conditions.append("assignment_id = ?")
            params.append(assignment_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM assignment_reviews {where} ORDER BY created_at ASC", params
        ).fetchall()
        return [row_to_assignment_review(row) for row in rows]

    def record_assignment_review(self, review_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Registra la decisión y hallazgos de un review de handoff."""
        current = self.get_assignment_review(review_id)
        next_status = str(body.get("status") or current["status"])
        resolved_at = utc_now() if next_status != "pending" else current.get("resolvedAt")
        self.connection.execute(
            """
            UPDATE assignment_reviews
            SET reviewer_agent_id = ?, status = ?, decision = ?, findings = ?,
                updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (
                str(body.get("reviewerAgentId") or current["reviewerAgentId"]),
                next_status,
                str(body.get("decision") or current.get("decision") or ""),
                json_dumps(body.get("findings") or current.get("findings") or []),
                utc_now(),
                resolved_at,
                review_id,
            ),
        )
        return self.get_assignment_review(review_id)

    def create_assignment_conflict(self, body: dict[str, Any]) -> dict[str, Any]:
        """Registra un desacuerdo abierto ligado a un handoff de asignación."""
        conflict_id = f"assignment-conflict-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO assignment_conflicts
                (id, project_id, assignment_id, handoff_id, status, raised_by, disagreement,
                 final_resolution, resolved_by, resolved_at, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', NULL, NULL, ?, ?, ?)
            """,
            (
                conflict_id,
                body["projectId"],
                body["assignmentId"],
                body["handoffId"],
                body.get("status", "open"),
                body.get("raisedBy", ""),
                body["disagreement"],
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_assignment_conflict(conflict_id)

    def get_assignment_conflict(self, conflict_id: str) -> dict[str, Any]:
        """Recupera un conflicto de asignación por id."""
        row = self.connection.execute(
            "SELECT * FROM assignment_conflicts WHERE id = ?", (conflict_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Assignment conflict not found: {conflict_id}")
        return row_to_assignment_conflict(row)

    def list_assignment_conflicts(
        self,
        *,
        assignment_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista conflictos de asignación filtrando por asignación y/o proyecto."""
        conditions: list[str] = []
        params: list[Any] = []
        if assignment_id:
            conditions.append("assignment_id = ?")
            params.append(assignment_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM assignment_conflicts {where} ORDER BY created_at ASC", params
        ).fetchall()
        return [row_to_assignment_conflict(row) for row in rows]

    def resolve_assignment_conflict(self, conflict_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Cierra un desacuerdo guardando la resolución final y quién la resolvió."""
        final_resolution = str(body.get("finalResolution") or "").strip()
        if not final_resolution:
            raise ValueError("Assignment conflict resolution requires finalResolution.")
        self.get_assignment_conflict(conflict_id)
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE assignment_conflicts
            SET status = 'resolved', final_resolution = ?, resolved_by = ?, resolved_at = ?,
                metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                final_resolution,
                body.get("resolvedBy"),
                timestamp,
                json_dumps(body.get("metadata") or {}),
                timestamp,
                conflict_id,
            ),
        )
        return self.get_assignment_conflict(conflict_id)

    def _assert_assignment_can_begin(self, assignment: dict[str, Any]) -> None:
        if not assignment.get("canonicalArtifactId") or not assignment.get("handoffId"):
            raise ValueError(
                f"Assignment {assignment['id']} cannot begin without a canonical artifact and handoff."
            )
        upstream_dependencies = self.list_task_dependencies(task_id=assignment["taskId"])
        for dependency in upstream_dependencies:
            upstream_assignments = self.list_agent_assignments(task_id=dependency["dependsOnTaskId"])
            for upstream in upstream_assignments:
                self._assert_upstream_assignment_resolved(upstream)

    def _assert_upstream_assignment_resolved(self, assignment: dict[str, Any]) -> None:
        handoff_id = str(assignment.get("handoffId") or "")
        if not handoff_id:
            raise ValueError(f"Assignment {assignment['id']} has no upstream handoff.")
        handoff = self.get_assignment_handoff(handoff_id)
        if handoff["status"] not in ACCEPTED_HANDOFF_STATUSES:
            raise ValueError(
                f"Assignment {assignment['id']} has an unresolved upstream handoff: {handoff['status']}."
            )
        if assignment.get("reviewRequired"):
            reviews = self.list_assignment_reviews(assignment_id=assignment["id"])
            if not any(review["status"] in APPROVED_REVIEW_STATUSES for review in reviews):
                raise ValueError(f"Assignment {assignment['id']} has no approved required review.")
        unresolved_conflicts = [
            conflict
            for conflict in self.list_assignment_conflicts(assignment_id=assignment["id"])
            if conflict["status"] != "resolved"
        ]
        if unresolved_conflicts:
            raise ValueError(f"Assignment {assignment['id']} has unresolved conflict records.")

    def create_iteration(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una iteración (id ``iteration-<uuid>``) con su plan y devuelve el registro creado."""
        iteration_id = f"iteration-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO iterations
                (id, project_id, brief_id, title, goal, status, story_ids, workspace_strategy,
                 quality_gates, security_gates, estimated_cost, runtimes, task_count, assignment_count,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                iteration_id,
                body["projectId"],
                body["briefId"],
                body["title"],
                body.get("goal", ""),
                body.get("status", "planned"),
                json_dumps(body.get("storyIds") or []),
                body["workspaceStrategy"],
                json_dumps(body.get("qualityGates") or []),
                json_dumps(body.get("securityGates") or []),
                json_dumps(body.get("estimatedCost") or {}),
                json_dumps(body.get("runtimes") or []),
                int(body.get("taskCount") or 0),
                int(body.get("assignmentCount") or 0),
                timestamp,
                timestamp,
            ),
        )
        return self.get_iteration(iteration_id)

    def get_iteration(self, iteration_id: str) -> dict[str, Any]:
        """Recupera una iteración por id.

        Raises:
            KeyError: si no existe ninguna iteración con ese id.
        """
        row = self.connection.execute("SELECT * FROM iterations WHERE id = ?", (iteration_id,)).fetchone()
        if not row:
            raise KeyError(f"Iteration not found: {iteration_id}")
        return row_to_iteration(row)

    def list_iterations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista iteraciones (todas o por proyecto), la más recientemente actualizada primero."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM iterations WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM iterations ORDER BY updated_at DESC").fetchall()
        return [row_to_iteration(row) for row in rows]
