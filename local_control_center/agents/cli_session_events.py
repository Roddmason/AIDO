"""Bitácora acotada de eventos de streaming de una sesión CLI, con promoción de chunks grandes a artifacts.

Persiste cada evento del ciclo de una sesión CLI (los nueve tipos del contrato) con un ``seq`` monótono
por sesión. Cada payload se redacta con ``redact_secrets`` y se acota: si serializado excede el límite
inline se conserva solo una vista previa truncada en la fila y el contenido completo se promueve a un
artifact (reutilizando el almacén de evidencia), referenciado por id. Un cap por sesión elimina los
eventos más antiguos, de modo que la tabla nunca crece sin límite por mucho stdout/stderr que llegue.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

CLI_SESSION_EVENT_TYPES = frozenset(
    {
        "started",
        "stdout_chunk",
        "stderr_chunk",
        "tool_action",
        "file_changed",
        "approval_requested",
        "completed",
        "failed",
        "cancelled",
    }
)
TERMINAL_EVENT_TYPES = frozenset({"completed", "failed", "cancelled"})

MAX_EVENTS_PER_SESSION = 500
MAX_EVENT_PAYLOAD_BYTES = 4_000


class CliSessionEventError(ValueError):
    """Se lanza ante un tipo de evento de sesión CLI desconocido (fuera del contrato de nueve tipos)."""


def row_to_cli_session_event(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``cli_session_events`` al dict camelCase del contrato (payload deserializado)."""
    return {
        "id": row["id"],
        "cliSessionId": row["cli_session_id"],
        "projectId": row["project_id"],
        "seq": row["seq"],
        "type": row["type"],
        "payload": json_loads(row["payload"]),
        "artifactId": row["artifact_id"],
        "createdAt": row["created_at"],
    }


class CliSessionEventStore:
    """Recorder acotado de eventos de una sesión CLI: trunca payloads, promueve chunks grandes y limita el total."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str = "model-gateway",
        artifact_root: Path | None = None,
    ):
        self.connection = connection
        self.project_id = project_id
        self.artifact_root = artifact_root

    def record_event(
        self, cli_session_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Registra un evento del contrato para la sesión, redactado y acotado, y devuelve la fila persistida.

        Raises:
            CliSessionEventError: si ``event_type`` no es uno de los nueve tipos del contrato.
        """
        if event_type not in CLI_SESSION_EVENT_TYPES:
            raise CliSessionEventError(f"Unknown CLI session event type: {event_type}")
        body: dict[str, Any] = redact_secrets(dict(payload or {}))
        artifact_id: str | None = None
        if len(json_dumps(body).encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            body, artifact_id = self._bound_payload(cli_session_id, event_type, body)
        seq = self._next_seq(cli_session_id)
        event_id = f"cli-session-event-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO cli_session_events
                (id, cli_session_id, project_id, seq, type, payload, artifact_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                cli_session_id,
                self.project_id,
                seq,
                event_type,
                json_dumps(body),
                artifact_id,
                utc_now(),
            ),
        )
        self._enforce_cap(cli_session_id)
        return self.get_event(event_id)

    def record_stream_chunk(self, cli_session_id: str, *, stream: str, text: str) -> dict[str, Any]:
        """Registra un chunk de stdout/stderr como evento acotado (promueve a artifact si es grande)."""
        event_type = "stdout_chunk" if stream == "stdout" else "stderr_chunk"
        return self.record_event(cli_session_id, event_type, {"stream": stream, "text": text})

    def get_event(self, event_id: str) -> dict[str, Any]:
        """Devuelve un evento por id.

        Raises:
            KeyError: si no existe ningún evento con ese id.
        """
        row = self.connection.execute("SELECT * FROM cli_session_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise KeyError(f"CLI session event not found: {event_id}")
        return row_to_cli_session_event(row)

    def list_events(
        self, cli_session_id: str, *, after_seq: int = 0, limit: int = MAX_EVENTS_PER_SESSION
    ) -> list[dict[str, Any]]:
        """Lista los eventos de la sesión con ``seq`` mayor que ``after_seq``, del más antiguo al más nuevo.

        Pensado para poll incremental de la UI: el cliente recuerda el último ``seq`` visto y pide solo
        los nuevos. ``limit`` se acota a ``MAX_EVENTS_PER_SESSION`` para no devolver más que la ventana viva.
        """
        bounded_limit = max(1, min(int(limit), MAX_EVENTS_PER_SESSION))
        rows = self.connection.execute(
            """
            SELECT * FROM cli_session_events
            WHERE cli_session_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (cli_session_id, int(after_seq), bounded_limit),
        ).fetchall()
        return [row_to_cli_session_event(row) for row in rows]

    def latest_seq(self, cli_session_id: str) -> int:
        """Devuelve el ``seq`` más alto registrado para la sesión (0 si no tiene eventos)."""
        row = self.connection.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM cli_session_events WHERE cli_session_id = ?",
            (cli_session_id,),
        ).fetchone()
        return int(row["m"])

    def _next_seq(self, cli_session_id: str) -> int:
        return self.latest_seq(cli_session_id) + 1

    def _enforce_cap(self, cli_session_id: str) -> None:
        threshold = self.latest_seq(cli_session_id) - MAX_EVENTS_PER_SESSION
        if threshold > 0:
            self.connection.execute(
                "DELETE FROM cli_session_events WHERE cli_session_id = ? AND seq <= ?",
                (cli_session_id, threshold),
            )

    def _bound_payload(
        self, cli_session_id: str, event_type: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        full_text = json_dumps(body)
        full_bytes = len(full_text.encode("utf-8"))
        preview = full_text.encode("utf-8")[:MAX_EVENT_PAYLOAD_BYTES].decode("utf-8", "ignore")
        bounded: dict[str, Any] = {"truncated": True, "originalBytes": full_bytes, "preview": preview}
        if "stream" in body:
            bounded["stream"] = body["stream"]
        artifact_id = self._promote(cli_session_id, event_type, full_text) if self.artifact_root else None
        if artifact_id:
            bounded["artifactId"] = artifact_id
        return bounded, artifact_id

    def _promote(self, cli_session_id: str, event_type: str, content: str) -> str:
        artifact_id = f"artifact-{uuid.uuid4()}"
        written = write_text_artifact(
            root=self.artifact_root,
            artifact_id=artifact_id,
            suffix=f".{event_type}.log",
            content=content,
        )
        EvidenceRepository(self.connection).create_artifact(
            project_id=self.project_id,
            evidence_package_id=None,
            kind="execution_log",
            path=str(Path(written["path"]).resolve(strict=False)),
            content_hash=written["hash"],
            metadata={
                "source": "cli_session_event",
                "cliSessionId": cli_session_id,
                "eventType": event_type,
                "sizeBytes": written["sizeBytes"],
                "hashAlgorithm": "sha256",
            },
            artifact_id=artifact_id,
        )
        return artifact_id
