"""Persistencia durable de runs y findings del ResearchAgent.

Transacciones: el repositorio escribe sobre la conexión SQLite recibida y no abre transacciones
propias; el caller debe agrupar operaciones multi-tabla con ``immediate_transaction`` cuando
necesite atomicidad.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.research.source_policy import UNTRUSTED
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def _row_value(row: sqlite3.Row, column: str, default: Any = None) -> Any:
    return row[column] if column in row.keys() else default


def row_to_research_run(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de research_runs al contrato camelCase interno."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "threadId": row["thread_id"],
        "messageId": row["message_id"],
        "workspaceId": row["workspace_id"],
        "jobId": row["job_id"],
        "agentRunId": row["agent_run_id"],
        "taskId": row["task_id"],
        "query": row["query"],
        "status": row["status"],
        "reason": row["reason"],
        "recommendation": json_loads(row["recommendation_json"], {}),
        "citationCheck": json_loads(row["citation_check_json"], {}),
        "conflictCount": row["conflict_count"],
        "sourceCount": row["source_count"],
        "trustedSourceCount": row["trusted_source_count"],
        "evidencePackageId": row["evidence_package_id"],
        "reportArtifactId": row["report_artifact_id"],
        "remediation": json_loads(row["remediation_json"], {}),
        "metadata": json_loads(row["metadata_json"], {}),
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_research_finding(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de research_findings al contrato camelCase interno."""
    return {
        "id": row["id"],
        "researchRunId": row["research_run_id"],
        "projectId": row["project_id"],
        "threadId": row["thread_id"],
        "findingType": row["finding_type"],
        "severity": row["severity"],
        "summary": row["summary"],
        "sourceIds": json_loads(row["source_ids_json"], []),
        "citations": json_loads(row["citations_json"], []),
        "payload": json_loads(row["payload_json"], {}),
        "createdAt": row["created_at"],
    }


class ResearchRepository:
    """Acceso canónico a research_runs y research_findings."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_run(
        self,
        *,
        project_id: str,
        workspace_id: str,
        task_id: str,
        query: str,
        status: str,
        reason: str,
        thread_id: str | None = None,
        message_id: str | None = None,
        job_id: str | None = None,
        agent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea un run en estado inicial y devuelve la fila persistida."""
        run_id = f"research-run-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO research_runs
                (id, project_id, thread_id, message_id, workspace_id, job_id, agent_run_id, task_id,
                 query, status, reason, recommendation_json, citation_check_json, conflict_count,
                 source_count, trusted_source_count, evidence_package_id, report_artifact_id,
                 remediation_json, metadata_json, started_at, completed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL, NULL, ?, ?, ?, NULL, ?, ?)
            """,
            (
                run_id,
                project_id,
                thread_id,
                message_id,
                workspace_id,
                job_id,
                agent_run_id,
                task_id,
                query,
                status,
                reason,
                json_dumps({}),
                json_dumps({"valid": False, "uncited": [], "untrustedOnly": []}),
                json_dumps({}),
                json_dumps(redact_secrets(metadata or {})),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return self.get_run(run_id)

    def update_run_result(
        self,
        run_id: str,
        *,
        status: str,
        reason: str,
        recommendation: dict[str, Any],
        citation_check: dict[str, Any],
        sources: list[dict[str, Any]],
        conflict_findings: list[dict[str, Any]],
        evidence_package_id: str,
        report_artifact_id: str,
        remediation: dict[str, Any],
    ) -> dict[str, Any]:
        """Completa un run con veredicto, fuentes, recommendation y remediation."""
        timestamp = utc_now()
        trusted_count = sum(1 for source in sources if source.get("trustLevel") != UNTRUSTED)
        self.connection.execute(
            """
            UPDATE research_runs
            SET status = ?,
                reason = ?,
                recommendation_json = ?,
                citation_check_json = ?,
                conflict_count = ?,
                source_count = ?,
                trusted_source_count = ?,
                evidence_package_id = ?,
                report_artifact_id = ?,
                remediation_json = ?,
                completed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                reason,
                json_dumps(redact_secrets(recommendation)),
                json_dumps(redact_secrets(citation_check)),
                len(conflict_findings),
                len(sources),
                trusted_count,
                evidence_package_id,
                report_artifact_id,
                json_dumps(redact_secrets(remediation)),
                timestamp,
                timestamp,
                run_id,
            ),
        )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Lee un research_run por id."""
        row = self.connection.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Research run not found: {run_id}")
        return row_to_research_run(row)

    def create_finding(
        self,
        *,
        research_run_id: str,
        project_id: str,
        finding_type: str,
        severity: str,
        summary: str,
        thread_id: str | None = None,
        source_ids: list[str] | None = None,
        citations: list[dict[str, Any]] | list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea un finding auditable asociado a un research_run."""
        finding_id = f"research-finding-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO research_findings
                (id, research_run_id, project_id, thread_id, finding_type, severity, summary,
                 source_ids_json, citations_json, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                research_run_id,
                project_id,
                thread_id,
                finding_type,
                severity,
                str(redact_secrets(summary)),
                json_dumps(redact_secrets(source_ids or [])),
                json_dumps(redact_secrets(citations or [])),
                json_dumps(redact_secrets(payload or {})),
                timestamp,
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM research_findings WHERE id = ?", (finding_id,)
        ).fetchone()
        return row_to_research_finding(row)

    def list_findings(self, research_run_id: str) -> list[dict[str, Any]]:
        """Lista los findings de un run en orden de creación."""
        rows = self.connection.execute(
            """
            SELECT * FROM research_findings
            WHERE research_run_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (research_run_id,),
        ).fetchall()
        return [row_to_research_finding(row) for row in rows]
