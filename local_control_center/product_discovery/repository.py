"""Persistencia SQLite del slice de product discovery sobre la conexión del caller.

Cubre las nueve entidades del slice (iniciativas, sesiones de descubrimiento y sus mensajes,
preguntas y respuestas de aclaración, briefs de producto con su historial de versiones, supuestos
y decisiones de producto). Cada entidad tiene su propia tabla con columnas de dominio reales (nunca
embebida en ``metadata``), siempre project-scoped, y enlazada por referencias explícitas
(``initiative_id``, ``session_id``, ``question_id``, ``brief_id``, ``supersedes_id``) para que
todo registro sea trazable hasta su origen. Los mapeadores ``row_to_*`` proyectan cada fila al
dict camelCase del contrato.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver ``shared/db.py``)
y estos métodos NO abren transacciones propias; cada ``execute`` se confirma de inmediato. Las
operaciones de una sola sentencia (``create_*``, ``get_*``, ``list_*``) son atómicas por sí
mismas, pero las de varias sentencias —``append_conversation_message`` (calcula ``MAX(sequence)``
y luego inserta) y ``upsert_product_brief`` (actualiza el brief y anexa su snapshot a
``product_brief_versions``)— solo son atómicas si el caller las agrupa en una única
``immediate_transaction``. Sin esa envoltura, una caída entre sentencias puede dejar el brief y
su historial desincronizados o colisionar en ``UNIQUE(session_id, sequence)``.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_initiative(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``initiatives`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "summary": row["summary"],
        "status": row["status"],
        "priority": row["priority"],
        "owner": row["owner"],
        "version": row["version"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_discovery_session(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``discovery_sessions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "title": row["title"],
        "objective": row["objective"],
        "status": row["status"],
        "facilitator": row["facilitator"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_conversation_message(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``conversation_messages`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "sessionId": row["session_id"],
        "initiativeId": row["initiative_id"],
        "sequence": row["sequence"],
        "role": row["role"],
        "author": row["author"],
        "content": row["content"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


def row_to_clarification_question(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``clarification_questions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "sessionId": row["session_id"],
        "sequence": row["sequence"],
        "question": row["question"],
        "status": row["status"],
        "priority": row["priority"],
        "askedBy": row["asked_by"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_clarification_answer(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``clarification_answers`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "questionId": row["question_id"],
        "initiativeId": row["initiative_id"],
        "answer": row["answer"],
        "status": row["status"],
        "answeredBy": row["answered_by"],
        "supersedesId": row["supersedes_id"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_product_brief(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``product_briefs`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "title": row["title"],
        "status": row["status"],
        "summary": row["summary"],
        "problemStatement": row["problem_statement"],
        "goals": json_loads(row["goals"], []),
        "targetUsers": json_loads(row["target_users"], []),
        "successMetrics": json_loads(row["success_metrics"], []),
        "scope": row["scope"],
        "outOfScope": row["out_of_scope"],
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_product_brief_version(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``product_brief_versions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "briefId": row["brief_id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "version": row["version"],
        "title": row["title"],
        "status": row["status"],
        "summary": row["summary"],
        "problemStatement": row["problem_statement"],
        "goals": json_loads(row["goals"], []),
        "targetUsers": json_loads(row["target_users"], []),
        "successMetrics": json_loads(row["success_metrics"], []),
        "scope": row["scope"],
        "outOfScope": row["out_of_scope"],
        "changeSummary": row["change_summary"],
        "authoredBy": row["authored_by"],
        "createdAt": row["created_at"],
    }


def row_to_assumption(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``assumptions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "briefId": row["brief_id"],
        "sourceQuestionId": row["source_question_id"],
        "statement": row["statement"],
        "status": row["status"],
        "confidence": row["confidence"],
        "validation": row["validation"],
        "owner": row["owner"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_product_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``product_decisions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "initiativeId": row["initiative_id"],
        "briefId": row["brief_id"],
        "supersedesId": row["supersedes_id"],
        "title": row["title"],
        "status": row["status"],
        "context": row["context"],
        "decision": row["decision"],
        "rationale": row["rationale"],
        "consequences": json_loads(row["consequences"], []),
        "linkedAssumptionIds": json_loads(row["linked_assumption_ids"], []),
        "linkedQuestionIds": json_loads(row["linked_question_ids"], []),
        "decidedBy": row["decided_by"],
        "decidedAt": row["decided_at"],
        "version": row["version"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class ProductDiscoveryRepository:
    """Acceso a datos del slice de product discovery sobre la conexión SQLite del caller.

    Concentra la persistencia de las nueve entidades del slice. No abre ni cierra transacciones:
    ejecuta sobre la ``connection`` recibida (autocommit por sentencia) y delega en el caller la
    atomicidad multi-sentencia (ver el docstring del módulo).
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_initiative(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una iniciativa (id ``initiative-<uuid>``, versión 1) y devuelve el registro creado."""
        initiative_id = f"initiative-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO initiatives
                (id, project_id, title, summary, status, priority, owner, version,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                initiative_id,
                body["projectId"],
                body["title"],
                body.get("summary", ""),
                body.get("status", "draft"),
                body.get("priority", "medium"),
                body.get("owner", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_initiative(initiative_id)

    def get_initiative(self, initiative_id: str) -> dict[str, Any]:
        """Recupera una iniciativa por id.

        Raises:
            KeyError: si no existe ninguna iniciativa con ese id.
        """
        row = self.connection.execute("SELECT * FROM initiatives WHERE id = ?", (initiative_id,)).fetchone()
        if not row:
            raise KeyError(f"Initiative not found: {initiative_id}")
        return row_to_initiative(row)

    def list_initiatives(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista iniciativas (todas o por proyecto), más recientes primero."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM initiatives WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM initiatives ORDER BY updated_at DESC").fetchall()
        return [row_to_initiative(row) for row in rows]

    def update_initiative(self, initiative_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una iniciativa, incrementa su versión y devuelve el registro.

        Mezcla ``body`` sobre el estado actual y persiste title, summary, status, priority, owner
        y metadata; ``version`` se incrementa en cada actualización para mantener el historial.

        Raises:
            KeyError: si la iniciativa no existe.
        """
        current = self.get_initiative(initiative_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE initiatives
            SET title = ?, summary = ?, status = ?, priority = ?, owner = ?, version = ?,
                metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["title"],
                next_value["summary"],
                next_value["status"],
                next_value["priority"],
                next_value["owner"],
                current["version"] + 1,
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                initiative_id,
            ),
        )
        return self.get_initiative(initiative_id)

    def create_discovery_session(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una sesión de descubrimiento (id ``discovery-session-<uuid>``) y la devuelve."""
        session_id = f"discovery-session-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO discovery_sessions
                (id, project_id, initiative_id, title, objective, status, facilitator,
                 metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                body["projectId"],
                body["initiativeId"],
                body["title"],
                body.get("objective", ""),
                body.get("status", "active"),
                body.get("facilitator", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_discovery_session(session_id)

    def get_discovery_session(self, session_id: str) -> dict[str, Any]:
        """Recupera una sesión de descubrimiento por id.

        Raises:
            KeyError: si no existe ninguna sesión con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM discovery_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Discovery session not found: {session_id}")
        return row_to_discovery_session(row)

    def list_discovery_sessions(
        self, project_id: str | None = None, initiative_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista sesiones de descubrimiento filtrando por proyecto y/o iniciativa, recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if initiative_id:
            conditions.append("initiative_id = ?")
            params.append(initiative_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM discovery_sessions {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [row_to_discovery_session(row) for row in rows]

    def update_discovery_session(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una sesión (title, objective, status, facilitator, metadata).

        Raises:
            KeyError: si la sesión no existe.
        """
        current = self.get_discovery_session(session_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE discovery_sessions
            SET title = ?, objective = ?, status = ?, facilitator = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["title"],
                next_value["objective"],
                next_value["status"],
                next_value["facilitator"],
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                session_id,
            ),
        )
        return self.get_discovery_session(session_id)

    def append_conversation_message(self, body: dict[str, Any]) -> dict[str, Any]:
        """Anexa un mensaje a una sesión asignándole el siguiente ``sequence`` y lo devuelve.

        El mensaje es inmutable (sin ``updated_at``): su orden y su inmutabilidad son la garantía
        de trazabilidad del log. Calcula ``MAX(sequence)+1`` para la sesión y luego inserta; ambas
        sentencias solo son atómicas si el caller abre una ``immediate_transaction`` (ver módulo).
        """
        message_id = f"conversation-message-{uuid.uuid4()}"
        timestamp = utc_now()
        session_id = body["sessionId"]
        next_sequence = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM conversation_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["next"]
        self.connection.execute(
            """
            INSERT INTO conversation_messages
                (id, project_id, session_id, initiative_id, sequence, role, author, content,
                 metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                body["projectId"],
                session_id,
                body["initiativeId"],
                next_sequence,
                body.get("role", "user"),
                body.get("author", ""),
                body.get("content", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
            ),
        )
        return self.get_conversation_message(message_id)

    def get_conversation_message(self, message_id: str) -> dict[str, Any]:
        """Recupera un mensaje de conversación por id.

        Raises:
            KeyError: si no existe ningún mensaje con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM conversation_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Conversation message not found: {message_id}")
        return row_to_conversation_message(row)

    def list_conversation_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Lista los mensajes de una sesión en orden cronológico estable (por ``sequence``)."""
        rows = self.connection.execute(
            "SELECT * FROM conversation_messages WHERE session_id = ? ORDER BY sequence ASC",
            (session_id,),
        ).fetchall()
        return [row_to_conversation_message(row) for row in rows]

    def create_clarification_question(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una pregunta de aclaración (id ``clarification-question-<uuid>``) y la devuelve.

        Asigna el siguiente ``sequence`` dentro de la iniciativa; el cálculo de ``MAX(sequence)+1``
        y el INSERT solo son atómicos bajo una ``immediate_transaction`` del caller.
        """
        question_id = f"clarification-question-{uuid.uuid4()}"
        timestamp = utc_now()
        initiative_id = body["initiativeId"]
        next_sequence = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM clarification_questions "
            "WHERE initiative_id = ?",
            (initiative_id,),
        ).fetchone()["next"]
        self.connection.execute(
            """
            INSERT INTO clarification_questions
                (id, project_id, initiative_id, session_id, sequence, question, status, priority,
                 asked_by, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                body["projectId"],
                initiative_id,
                body.get("sessionId"),
                next_sequence,
                body["question"],
                body.get("status", "open"),
                body.get("priority", "medium"),
                body.get("askedBy", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_clarification_question(question_id)

    def get_clarification_question(self, question_id: str) -> dict[str, Any]:
        """Recupera una pregunta de aclaración por id.

        Raises:
            KeyError: si no existe ninguna pregunta con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM clarification_questions WHERE id = ?", (question_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Clarification question not found: {question_id}")
        return row_to_clarification_question(row)

    def list_clarification_questions(
        self,
        project_id: str | None = None,
        initiative_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista preguntas de aclaración filtrando por proyecto, iniciativa y/o sesión."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if initiative_id:
            conditions.append("initiative_id = ?")
            params.append(initiative_id)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM clarification_questions {where} ORDER BY sequence ASC", params
        ).fetchall()
        return [row_to_clarification_question(row) for row in rows]

    def update_clarification_question(self, question_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una pregunta (question, status, priority, metadata).

        Raises:
            KeyError: si la pregunta no existe.
        """
        current = self.get_clarification_question(question_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE clarification_questions
            SET question = ?, status = ?, priority = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["question"],
                next_value["status"],
                next_value["priority"],
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                question_id,
            ),
        )
        return self.get_clarification_question(question_id)

    def create_clarification_answer(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una respuesta de aclaración (id ``clarification-answer-<uuid>``) y la devuelve.

        ``supersedesId`` enlaza una respuesta revisada con la anterior para conservar la cadena de
        revisiones; marcar la respuesta previa como ``superseded`` corresponde al caller.
        """
        answer_id = f"clarification-answer-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO clarification_answers
                (id, project_id, question_id, initiative_id, answer, status, answered_by,
                 supersedes_id, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                answer_id,
                body["projectId"],
                body["questionId"],
                body["initiativeId"],
                body["answer"],
                body.get("status", "proposed"),
                body.get("answeredBy", ""),
                body.get("supersedesId"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_clarification_answer(answer_id)

    def get_clarification_answer(self, answer_id: str) -> dict[str, Any]:
        """Recupera una respuesta de aclaración por id.

        Raises:
            KeyError: si no existe ninguna respuesta con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM clarification_answers WHERE id = ?", (answer_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Clarification answer not found: {answer_id}")
        return row_to_clarification_answer(row)

    def list_clarification_answers(self, question_id: str) -> list[dict[str, Any]]:
        """Lista las respuestas de una pregunta en orden cronológico (más antiguas primero)."""
        rows = self.connection.execute(
            "SELECT * FROM clarification_answers WHERE question_id = ? ORDER BY created_at ASC",
            (question_id,),
        ).fetchall()
        return [row_to_clarification_answer(row) for row in rows]

    def update_clarification_answer(self, answer_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una respuesta (answer, status, metadata).

        Raises:
            KeyError: si la respuesta no existe.
        """
        current = self.get_clarification_answer(answer_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE clarification_answers
            SET answer = ?, status = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["answer"],
                next_value["status"],
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                answer_id,
            ),
        )
        return self.get_clarification_answer(answer_id)

    def upsert_product_brief(self, body: dict[str, Any]) -> dict[str, Any]:
        """Crea o actualiza un brief de producto y anexa su snapshot a ``product_brief_versions``.

        Sin ``briefId`` (o si no existe) inserta un brief nuevo en versión 1; si existe, lo
        actualiza e incrementa ``version``. En ambos casos anexa la versión resultante al historial.
        El UPDATE/INSERT del brief y el INSERT de su versión deben confirmarse juntos por el caller
        (``immediate_transaction``) para no desincronizar el brief y su historial.
        """
        timestamp = utc_now()
        brief_id = body.get("briefId")
        existing = (
            self.connection.execute("SELECT * FROM product_briefs WHERE id = ?", (brief_id,)).fetchone()
            if brief_id
            else None
        )
        next_id = brief_id or f"product-brief-{uuid.uuid4()}"
        version = (existing["version"] + 1) if existing else 1
        title = body["title"]
        status = body.get("status", "draft")
        summary = body.get("summary", "")
        problem_statement = body.get("problemStatement", "")
        goals = json_dumps(body.get("goals") or [])
        target_users = json_dumps(body.get("targetUsers") or [])
        success_metrics = json_dumps(body.get("successMetrics") or [])
        scope = body.get("scope", "")
        out_of_scope = body.get("outOfScope", "")
        if existing:
            self.connection.execute(
                """
                UPDATE product_briefs
                SET title = ?, status = ?, summary = ?, problem_statement = ?, goals = ?,
                    target_users = ?, success_metrics = ?, scope = ?, out_of_scope = ?,
                    version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    status,
                    summary,
                    problem_statement,
                    goals,
                    target_users,
                    success_metrics,
                    scope,
                    out_of_scope,
                    version,
                    timestamp,
                    next_id,
                ),
            )
            initiative_id = existing["initiative_id"]
            project_id = existing["project_id"]
        else:
            initiative_id = body["initiativeId"]
            project_id = body["projectId"]
            self.connection.execute(
                """
                INSERT INTO product_briefs
                    (id, project_id, initiative_id, title, status, summary, problem_statement,
                     goals, target_users, success_metrics, scope, out_of_scope, version,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_id,
                    project_id,
                    initiative_id,
                    title,
                    status,
                    summary,
                    problem_statement,
                    goals,
                    target_users,
                    success_metrics,
                    scope,
                    out_of_scope,
                    version,
                    timestamp,
                    timestamp,
                ),
            )
        self.connection.execute(
            """
            INSERT INTO product_brief_versions
                (id, brief_id, project_id, initiative_id, version, title, status, summary,
                 problem_statement, goals, target_users, success_metrics, scope, out_of_scope,
                 change_summary, authored_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"product-brief-version-{uuid.uuid4()}",
                next_id,
                project_id,
                initiative_id,
                version,
                title,
                status,
                summary,
                problem_statement,
                goals,
                target_users,
                success_metrics,
                scope,
                out_of_scope,
                body.get("changeSummary", ""),
                body.get("authoredBy", ""),
                timestamp,
            ),
        )
        return self.get_product_brief(next_id)

    def get_product_brief(self, brief_id: str) -> dict[str, Any]:
        """Recupera un brief de producto por id.

        Raises:
            KeyError: si no existe ningún brief con ese id.
        """
        row = self.connection.execute("SELECT * FROM product_briefs WHERE id = ?", (brief_id,)).fetchone()
        if not row:
            raise KeyError(f"Product brief not found: {brief_id}")
        return row_to_product_brief(row)

    def list_product_briefs(
        self, project_id: str | None = None, initiative_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista briefs de producto filtrando por proyecto y/o iniciativa, recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if initiative_id:
            conditions.append("initiative_id = ?")
            params.append(initiative_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM product_briefs {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [row_to_product_brief(row) for row in rows]

    def get_product_brief_version(self, version_id: str) -> dict[str, Any]:
        """Recupera un snapshot de versión de brief por id.

        Raises:
            KeyError: si no existe ningún snapshot con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM product_brief_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Product brief version not found: {version_id}")
        return row_to_product_brief_version(row)

    def list_product_brief_versions(self, brief_id: str) -> list[dict[str, Any]]:
        """Lista el historial de versiones de un brief, de la más reciente a la más antigua."""
        rows = self.connection.execute(
            "SELECT * FROM product_brief_versions WHERE brief_id = ? ORDER BY version DESC",
            (brief_id,),
        ).fetchall()
        return [row_to_product_brief_version(row) for row in rows]

    def create_assumption(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un supuesto (id ``assumption-<uuid>``) y devuelve el registro creado."""
        assumption_id = f"assumption-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO assumptions
                (id, project_id, initiative_id, brief_id, source_question_id, statement, status,
                 confidence, validation, owner, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assumption_id,
                body["projectId"],
                body["initiativeId"],
                body.get("briefId"),
                body.get("sourceQuestionId"),
                body["statement"],
                body.get("status", "proposed"),
                body.get("confidence", "medium"),
                body.get("validation", ""),
                body.get("owner", ""),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_assumption(assumption_id)

    def get_assumption(self, assumption_id: str) -> dict[str, Any]:
        """Recupera un supuesto por id.

        Raises:
            KeyError: si no existe ningún supuesto con ese id.
        """
        row = self.connection.execute("SELECT * FROM assumptions WHERE id = ?", (assumption_id,)).fetchone()
        if not row:
            raise KeyError(f"Assumption not found: {assumption_id}")
        return row_to_assumption(row)

    def list_assumptions(
        self, project_id: str | None = None, initiative_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista supuestos filtrando por proyecto y/o iniciativa, recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if initiative_id:
            conditions.append("initiative_id = ?")
            params.append(initiative_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM assumptions {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [row_to_assumption(row) for row in rows]

    def update_assumption(self, assumption_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre un supuesto (status, confidence, validation, owner, metadata).

        Raises:
            KeyError: si el supuesto no existe.
        """
        current = self.get_assumption(assumption_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE assumptions
            SET status = ?, confidence = ?, validation = ?, owner = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["status"],
                next_value["confidence"],
                next_value["validation"],
                next_value["owner"],
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                assumption_id,
            ),
        )
        return self.get_assumption(assumption_id)

    def create_product_decision(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una decisión de producto (id ``product-decision-<uuid>``, versión 1) y la devuelve.

        ``supersedesId`` encadena la decisión que reemplaza a otra; ``linkedAssumptionIds`` y
        ``linkedQuestionIds`` la enlazan con los supuestos y preguntas que la originaron.
        """
        decision_id = f"product-decision-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO product_decisions
                (id, project_id, initiative_id, brief_id, supersedes_id, title, status, context,
                 decision, rationale, consequences, linked_assumption_ids, linked_question_ids,
                 decided_by, decided_at, version, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                decision_id,
                body["projectId"],
                body["initiativeId"],
                body.get("briefId"),
                body.get("supersedesId"),
                body["title"],
                body.get("status", "proposed"),
                body.get("context", ""),
                body.get("decision", ""),
                body.get("rationale", ""),
                json_dumps(body.get("consequences") or []),
                json_dumps(body.get("linkedAssumptionIds") or []),
                json_dumps(body.get("linkedQuestionIds") or []),
                body.get("decidedBy", ""),
                body.get("decidedAt"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_product_decision(decision_id)

    def get_product_decision(self, decision_id: str) -> dict[str, Any]:
        """Recupera una decisión de producto por id.

        Raises:
            KeyError: si no existe ninguna decisión con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM product_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Product decision not found: {decision_id}")
        return row_to_product_decision(row)

    def list_product_decisions(
        self, project_id: str | None = None, initiative_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista decisiones de producto filtrando por proyecto y/o iniciativa, recientes primero."""
        conditions: list[str] = []
        params: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if initiative_id:
            conditions.append("initiative_id = ?")
            params.append(initiative_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM product_decisions {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [row_to_product_decision(row) for row in rows]

    def update_product_decision(self, decision_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre una decisión, incrementa su versión y devuelve el registro.

        Persiste status, context, decision, rationale, consequences, los enlaces de trazabilidad,
        decidedBy/decidedAt y metadata; ``version`` se incrementa en cada actualización.

        Raises:
            KeyError: si la decisión no existe.
        """
        current = self.get_product_decision(decision_id)
        next_value = {**current, **body}
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE product_decisions
            SET status = ?, context = ?, decision = ?, rationale = ?, consequences = ?,
                linked_assumption_ids = ?, linked_question_ids = ?, decided_by = ?, decided_at = ?,
                version = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value["status"],
                next_value["context"],
                next_value["decision"],
                next_value["rationale"],
                json_dumps(next_value.get("consequences") or []),
                json_dumps(next_value.get("linkedAssumptionIds") or []),
                json_dumps(next_value.get("linkedQuestionIds") or []),
                next_value["decidedBy"],
                next_value.get("decidedAt"),
                current["version"] + 1,
                json_dumps(next_value.get("metadata") or {}),
                timestamp,
                decision_id,
            ),
        )
        return self.get_product_decision(decision_id)
