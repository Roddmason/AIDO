"""Recall de memoria de threads: agrega lo que AIDO ya recuerda sobre trabajo similar.

Compone, sin tablas nuevas, las seis categorías del panel de memoria a partir de datos reales:
candidatos del índice lexical (``thread_memory_index``), decisiones resueltas y artifacts de esos
hilos similares, lecciones registradas como ``memory_items`` (kind='lesson'), pasadas de
performance ya solicitadas (``thread_similarity_events`` con action='performance_pass' y mensajes
con metadata.mode='performance_pass') y los hilos similares ya resueltos como funcionalidad
existente. Solo lecturas sobre la conexión del caller; el índice se refresca vía
``ThreadSimilarityService`` antes de buscar para no devolver falsos negativos. Todo texto que sale
al cliente pasa por ``redact_secrets``.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .similarity import ThreadSimilarityService, normalize_text

MAX_RECALL_LIMIT = 20
PERFORMANCE_MODE = "performance_pass"
NOTE_EXCERPT_CHARS = 240
LESSON_SCAN_ROWS = 50


class ThreadMemoryRecallService:
    """Agrega el recuerdo operacional de un hilo: similares, decisiones, evidencia y lecciones."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.similarity = ThreadSimilarityService(connection)

    def recall(self, *, thread_id: str, limit: int = 5) -> dict[str, Any]:
        """Devuelve las seis categorías de memoria del hilo, acotadas a ``limit`` por categoría.

        Lanza ``KeyError`` si el hilo no existe (el router lo mapea a 404).
        """
        bounded_limit = max(1, min(int(limit), MAX_RECALL_LIMIT))
        source_index = self.similarity.index_thread(thread_id)
        candidates = self.similarity.find_similar(
            project_id=str(source_index["projectId"]),
            source_thread_id=thread_id,
            limit=bounded_limit,
        )
        candidate_ids = [str(candidate["threadId"]) for candidate in candidates]
        return {
            "sourceThreadId": thread_id,
            "generatedAt": utc_now(),
            "similarThreads": candidates,
            "previousDecisions": self._previous_decisions(candidate_ids, bounded_limit),
            "relatedEvidence": self._related_evidence(candidate_ids, bounded_limit),
            "lessonsLearned": self._lessons(source_index, bounded_limit),
            "performanceIssues": self._performance_issues(thread_id, candidate_ids, bounded_limit),
            "implementedFunctionality": _implemented_from(candidates, bounded_limit),
        }

    def _previous_decisions(self, candidate_ids: list[str], limit: int) -> list[dict[str, Any]]:
        """Decisiones ya resueltas en los hilos similares, la más reciente primero."""
        if not candidate_ids:
            return []
        placeholders = ", ".join("?" for _ in candidate_ids)
        rows = self.connection.execute(
            f"""
            SELECT d.id, d.thread_id, d.title, d.prompt, d.resolution, d.decided_at, d.created_at,
                   pt.title AS thread_title
            FROM thread_decisions d
            LEFT JOIN project_threads pt ON pt.id = d.thread_id
            WHERE d.thread_id IN ({placeholders}) AND d.status = 'resolved'
            ORDER BY COALESCE(d.decided_at, d.created_at) DESC, d.rowid DESC
            LIMIT ?
            """,
            [*candidate_ids, limit],
        ).fetchall()
        return [
            {
                "id": row["id"],
                "threadId": row["thread_id"],
                "threadTitle": row["thread_title"] or "",
                "title": str(redact_secrets(row["title"] or "")),
                "prompt": str(redact_secrets(row["prompt"] or "")),
                "resolution": str(redact_secrets(row["resolution"] or "")),
                "decidedAt": row["decided_at"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def _related_evidence(self, candidate_ids: list[str], limit: int) -> list[dict[str, Any]]:
        """Artifacts adjuntados en los hilos similares (reportes, logs, parches), como evidencia."""
        if not candidate_ids:
            return []
        placeholders = ", ".join("?" for _ in candidate_ids)
        rows = self.connection.execute(
            f"""
            SELECT a.id, a.thread_id, a.artifact_id, a.kind, a.title, a.created_at,
                   pt.title AS thread_title
            FROM thread_artifacts a
            LEFT JOIN project_threads pt ON pt.id = a.thread_id
            WHERE a.thread_id IN ({placeholders})
            ORDER BY a.created_at DESC, a.rowid DESC
            LIMIT ?
            """,
            [*candidate_ids, limit],
        ).fetchall()
        return [
            {
                "id": row["id"],
                "threadId": row["thread_id"],
                "threadTitle": row["thread_title"] or "",
                "artifactId": row["artifact_id"],
                "kind": row["kind"],
                "title": str(redact_secrets(row["title"] or "")),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def _lessons(self, source_index: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        """Lecciones vigentes del proyecto (``memory_items`` kind='lesson'), relevantes primero.

        La relevancia es el overlap lexical entre la lección y el objetivo normalizado del hilo;
        las lecciones sin overlap igual se listan (recencia) para no ocultar memoria registrada.
        """
        rows = self.connection.execute(
            """
            SELECT rowid, id, content, source_ref, created_at
            FROM memory_items
            WHERE project_id = ? AND kind = 'lesson' AND deleted_at IS NULL
              AND (expires_at IS NULL OR expires_at = '' OR expires_at > ?)
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (source_index["projectId"], utc_now(), LESSON_SCAN_ROWS),
        ).fetchall()
        source_tokens = set(str(source_index["normalizedGoal"]).split())
        lessons: list[tuple[int, str, int, dict[str, Any]]] = []
        for row in rows:
            content = str(redact_secrets(row["content"] or ""))
            matched = sorted(source_tokens & set(normalize_text(content).split()))[:8]
            lessons.append(
                (
                    len(matched),
                    str(row["created_at"]),
                    int(row["rowid"]),
                    {
                        "id": row["id"],
                        "content": content[:NOTE_EXCERPT_CHARS],
                        "sourceRef": row["source_ref"],
                        "matchedKeywords": matched,
                        "createdAt": row["created_at"],
                    },
                )
            )
        lessons.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in lessons[:limit]]

    def _performance_issues(
        self,
        thread_id: str,
        candidate_ids: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Pasadas de performance ya pedidas sobre este trabajo o sus hilos similares."""
        related_ids = [*candidate_ids, thread_id]
        placeholders = ", ".join("?" for _ in related_ids)
        event_rows = self.connection.execute(
            f"""
            SELECT e.id, e.candidate_thread_id, e.reason, e.created_at,
                   pt.title AS thread_title
            FROM thread_similarity_events e
            LEFT JOIN project_threads pt ON pt.id = e.candidate_thread_id
            WHERE e.action = ?
              AND (e.source_thread_id IN ({placeholders})
                   OR e.candidate_thread_id IN ({placeholders}))
            ORDER BY e.created_at DESC, e.rowid DESC
            LIMIT ?
            """,
            [PERFORMANCE_MODE, *related_ids, *related_ids, limit],
        ).fetchall()
        issues = [
            {
                "id": row["id"],
                "threadId": row["candidate_thread_id"],
                "threadTitle": row["thread_title"] or "",
                "note": str(redact_secrets(row["reason"] or ""))[:NOTE_EXCERPT_CHARS],
                "source": "similarity_event",
                "createdAt": row["created_at"],
            }
            for row in event_rows
        ]
        if candidate_ids:
            message_placeholders = ", ".join("?" for _ in candidate_ids)
            message_rows = self.connection.execute(
                f"""
                SELECT m.id, m.thread_id, m.content, m.created_at,
                       pt.title AS thread_title
                FROM thread_messages m
                LEFT JOIN project_threads pt ON pt.id = m.thread_id
                WHERE m.thread_id IN ({message_placeholders})
                  AND json_extract(m.metadata, '$.mode') = ?
                ORDER BY m.created_at DESC, m.rowid DESC
                LIMIT ?
                """,
                [*candidate_ids, PERFORMANCE_MODE, limit],
            ).fetchall()
            issues.extend(
                {
                    "id": row["id"],
                    "threadId": row["thread_id"],
                    "threadTitle": row["thread_title"] or "",
                    "note": str(redact_secrets(row["content"] or ""))[:NOTE_EXCERPT_CHARS],
                    "source": "thread_message",
                    "createdAt": row["created_at"],
                }
                for row in message_rows
            )
        issues.sort(key=lambda item: (str(item["createdAt"]), str(item["id"])), reverse=True)
        return issues[:limit]


def _implemented_from(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Hilos similares ya resueltos: funcionalidad que el proyecto ya implementó antes."""
    implemented = [
        {
            "threadId": candidate["threadId"],
            "title": candidate["title"],
            "summary": candidate["summary"],
            "score": candidate["score"],
            "updatedAt": candidate["updatedAt"],
        }
        for candidate in candidates
        if candidate["status"] == "resolved"
    ]
    return implemented[:limit]
