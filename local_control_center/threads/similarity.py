"""Servicio de similitud lexical para detectar trabajo ya tratado en otros threads.

Mantiene un índice SQLite derivado de ``project_threads`` + mensajes/artifacts recientes y calcula
similitud inicial sin proveedores externos: normalización lexical, keywords y overlap de tokens.
Embeddings/memory_retrieval quedan fuera del camino crítico hasta que exista un contrato operativo
disponible; el índice relacional sigue siendo la fuente auditable.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads, stable_hash
from local_control_center.shared.time import utc_now

SIMILARITY_ACTIONS = (
    "continue_existing",
    "improve_existing",
    "performance_pass",
    "create_new_anyway",
)
HIGH_SIMILARITY_THRESHOLD = 0.6
MAX_INDEX_TEXT_CHARS = 8_000
MAX_KEYWORDS = 24
MAX_CANDIDATES = 20

_STOPWORDS = {
    "a",
    "al",
    "and",
    "are",
    "as",
    "con",
    "de",
    "del",
    "el",
    "en",
    "for",
    "in",
    "is",
    "la",
    "las",
    "lo",
    "los",
    "of",
    "or",
    "para",
    "por",
    "que",
    "sin",
    "the",
    "to",
    "un",
    "una",
    "use",
    "y",
}


def row_to_similarity_event(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea un evento de similitud a la forma camelCase de API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "sourceThreadId": row["source_thread_id"],
        "candidateThreadId": row["candidate_thread_id"],
        "score": float(row["score"]),
        "reason": row["reason"],
        "action": row["action"],
        "createdAt": row["created_at"],
    }


class ThreadSimilarityService:
    """Indexa threads y calcula candidatos similares dentro del mismo proyecto."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def index_thread(self, thread_id: str) -> dict[str, Any]:
        """Reconstruye el índice de un thread desde su header, últimos mensajes y artifacts."""
        thread = self._thread_row(thread_id)
        title = str(redact_secrets(thread["title"] or "")).strip()
        summary = str(redact_secrets(thread["summary"] or "")).strip()
        artifact_refs, artifact_text = self._artifact_context(thread_id)
        material = " ".join(
            part
            for part in [
                title,
                summary,
                self._recent_message_text(thread_id),
                self._recent_event_text(thread_id),
                artifact_text,
            ]
            if part
        )[:MAX_INDEX_TEXT_CHARS]
        normalized_goal = normalize_text(material)
        keywords = extract_keywords(normalized_goal)
        timestamp = utc_now()
        existing = self.connection.execute(
            "SELECT id FROM thread_memory_index WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        index_id = existing["id"] if existing else f"thread-index-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO thread_memory_index
                (id, project_id, thread_id, title, summary, normalized_goal, fingerprint,
                 keywords_json, artifact_refs_json, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                project_id = excluded.project_id,
                title = excluded.title,
                summary = excluded.summary,
                normalized_goal = excluded.normalized_goal,
                fingerprint = excluded.fingerprint,
                keywords_json = excluded.keywords_json,
                artifact_refs_json = excluded.artifact_refs_json,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                index_id,
                thread["project_id"],
                thread_id,
                title,
                summary,
                normalized_goal,
                stable_hash(normalized_goal),
                json_dumps(keywords),
                json_dumps(artifact_refs),
                thread["status"],
                timestamp,
            ),
        )
        return self.get_index(thread_id)

    def get_index(self, thread_id: str) -> dict[str, Any]:
        """Devuelve la fila indexada de un thread o lanza ``KeyError`` si no existe."""
        row = self.connection.execute(
            "SELECT * FROM thread_memory_index WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Thread index not found: {thread_id}")
        return _row_to_index(row)

    def index_project(self, project_id: str) -> None:
        """Backfill idempotente de todos los threads del proyecto para evitar falsos negativos."""
        rows = self.connection.execute(
            "SELECT id FROM project_threads WHERE project_id = ? ORDER BY updated_at ASC",
            (project_id,),
        ).fetchall()
        for row in rows:
            self.index_thread(row["id"])

    def find_similar(
        self,
        *,
        project_id: str,
        query: str | None = None,
        source_thread_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Busca candidatos similares por texto libre o por el propio índice de un thread fuente."""
        self.index_project(project_id)
        source_index: dict[str, Any] | None = None
        if source_thread_id:
            source_index = self.index_thread(source_thread_id)
        query_text = str(query or "").strip()
        if not query_text and source_index is not None:
            query_text = " ".join(
                [
                    str(source_index["title"]),
                    str(source_index["summary"]),
                    str(source_index["normalizedGoal"]),
                ]
            )
        if not query_text.strip():
            raise ValueError("Similarity query is required")

        query_normalized = normalize_text(str(redact_secrets(query_text)))
        query_tokens = set(query_normalized.split())
        if not query_tokens:
            return []

        clauses = ["project_id = ?"]
        params: list[Any] = [project_id]
        if source_thread_id:
            clauses.append("thread_id <> ?")
            params.append(source_thread_id)
        if not include_deleted:
            clauses.append("status <> 'deleted'")
        bounded_limit = max(1, min(int(limit), MAX_CANDIDATES))
        rows = self.connection.execute(
            f"""
            SELECT * FROM thread_memory_index
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, rowid DESC
            """,
            params,
        ).fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidate = _row_to_index(row)
            score, reason = _score_candidate(query_tokens, query_normalized, candidate)
            if score <= 0:
                continue
            candidates.append(
                {
                    "threadId": candidate["threadId"],
                    "projectId": candidate["projectId"],
                    "title": candidate["title"],
                    "summary": candidate["summary"],
                    "status": candidate["status"],
                    "score": round(score, 3),
                    "reason": reason,
                    "keywords": candidate["keywords"],
                    "artifactRefs": candidate["artifactRefs"],
                    "updatedAt": candidate["updatedAt"],
                }
            )
        candidates.sort(key=lambda item: (item["score"], item["updatedAt"]), reverse=True)
        return candidates[:bounded_limit]

    def mark_similarity(
        self,
        *,
        project_id: str,
        source_thread_id: str,
        candidate_thread_id: str,
        score: float,
        reason: str,
        action: str,
    ) -> dict[str, Any]:
        """Registra la decisión tomada sobre un candidato similar y la enlaza al thread fuente."""
        if action not in SIMILARITY_ACTIONS:
            raise ValueError(f"Unknown similarity action: {action}")
        source = self._thread_row(source_thread_id)
        candidate = self._thread_row(candidate_thread_id)
        if source["project_id"] != project_id or candidate["project_id"] != project_id:
            raise ValueError("Similarity candidates must belong to the same project")
        event_id = f"thread-similarity-{uuid.uuid4()}"
        clean_reason = str(redact_secrets(reason or "")).strip()
        bounded_score = max(0.0, min(float(score), 1.0))
        self.connection.execute(
            """
            INSERT INTO thread_similarity_events
                (id, project_id, source_thread_id, candidate_thread_id, score, reason, action, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                project_id,
                source_thread_id,
                candidate_thread_id,
                bounded_score,
                clean_reason,
                action,
                utc_now(),
            ),
        )
        from local_control_center.threads.repository import ThreadsRepository

        ThreadsRepository(self.connection).record_event(
            thread_id=source_thread_id,
            type="similarity_marked",
            agent_role="aido_lead",
            payload={
                "candidateThreadId": candidate_thread_id,
                "score": bounded_score,
                "reason": clean_reason,
                "action": action,
            },
        )
        row = self.connection.execute(
            "SELECT * FROM thread_similarity_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return row_to_similarity_event(row)

    def list_similarity_events(self, *, source_thread_id: str) -> list[dict[str, Any]]:
        """Lista decisiones de similitud registradas para un thread fuente."""
        rows = self.connection.execute(
            """
            SELECT * FROM thread_similarity_events
            WHERE source_thread_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (source_thread_id,),
        ).fetchall()
        return [row_to_similarity_event(row) for row in rows]

    def _thread_row(self, thread_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM project_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Thread not found: {thread_id}")
        return row

    def _recent_message_text(self, thread_id: str) -> str:
        rows = self.connection.execute(
            """
            SELECT content FROM thread_messages
            WHERE thread_id = ?
            ORDER BY sequence DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        text = " ".join(str(redact_secrets(row["content"] or "")) for row in rows)
        return text[:MAX_INDEX_TEXT_CHARS]

    def _recent_event_text(self, thread_id: str) -> str:
        rows = self.connection.execute(
            """
            SELECT type, payload, metadata FROM thread_agent_events
            WHERE thread_id = ?
            ORDER BY sequence DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        text_parts: list[str] = []
        for row in rows:
            payload = redact_secrets(json_loads(row["payload"], {}))
            metadata = redact_secrets(json_loads(row["metadata"], {}))
            text_parts.append(
                " ".join([str(row["type"] or ""), _flatten_text(payload), _flatten_text(metadata)])
            )
        return " ".join(text_parts)[:MAX_INDEX_TEXT_CHARS]

    def _artifact_context(self, thread_id: str) -> tuple[list[dict[str, str]], str]:
        rows = self.connection.execute(
            """
            SELECT artifact_id, kind, title, metadata FROM thread_artifacts
            WHERE thread_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        refs: list[dict[str, str]] = []
        text_parts: list[str] = []
        for row in rows:
            title = str(redact_secrets(row["title"] or "")).strip()
            refs.append(
                {
                    "artifactId": str(row["artifact_id"]),
                    "kind": str(row["kind"]),
                    "title": title,
                }
            )
            metadata = redact_secrets(json_loads(row["metadata"], {}))
            text_parts.append(" ".join([title, _flatten_text(metadata)]))
        return refs, " ".join(text_parts)[:MAX_INDEX_TEXT_CHARS]


def normalize_text(value: str) -> str:
    """Normaliza texto para comparación lexical estable y tokenizable."""
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    decomposed = unicodedata.normalize("NFKD", expanded)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [_stem_token(token) for token in cleaned.split() if len(token) >= 3 and token not in _STOPWORDS]
    return " ".join(tokens)


def extract_keywords(normalized_text: str) -> list[str]:
    """Extrae keywords determinísticas por frecuencia y orden alfabético para desempate."""
    counts = Counter(token for token in normalized_text.split() if token)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _count in ranked[:MAX_KEYWORDS]]


def _row_to_index(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "threadId": row["thread_id"],
        "title": row["title"],
        "summary": row["summary"],
        "normalizedGoal": row["normalized_goal"],
        "fingerprint": row["fingerprint"],
        "keywords": json_loads(row["keywords_json"], []),
        "artifactRefs": json_loads(row["artifact_refs_json"], []),
        "status": row["status"],
        "updatedAt": row["updated_at"],
    }


def _score_candidate(
    query_tokens: set[str],
    query_normalized: str,
    candidate: dict[str, Any],
) -> tuple[float, str]:
    candidate_tokens = set(str(candidate["normalizedGoal"]).split())
    if not candidate_tokens:
        return 0.0, "No indexable candidate text."
    shared = query_tokens & candidate_tokens
    if not shared:
        return 0.0, "No lexical overlap."
    union = query_tokens | candidate_tokens
    jaccard = len(shared) / len(union)
    coverage = len(shared) / len(query_tokens)
    query_keywords = set(extract_keywords(query_normalized))
    candidate_keywords = {str(item) for item in candidate.get("keywords", [])}
    keyword_basis = max(1, min(len(query_keywords), len(candidate_keywords)))
    keyword_overlap = len(query_keywords & candidate_keywords) / keyword_basis
    title_tokens = set(normalize_text(str(candidate.get("title") or "")).split())
    title_basis = max(1, min(len(query_tokens), len(title_tokens)))
    title_overlap = len(query_tokens & title_tokens) / title_basis
    score = min(1.0, (coverage * 0.45) + (jaccard * 0.25) + (keyword_overlap * 0.2) + (title_overlap * 0.1))
    shared_keywords = sorted(shared)[:8]
    reason = f"Lexical overlap on {len(shared)} tokens: {', '.join(shared_keywords)}."
    return score, reason


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _stem_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token
