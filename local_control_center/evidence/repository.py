from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads

from local_control_center.shared.time import utc_now


def row_to_evidence_package(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "workflowRunId": row["workflow_run_id"],
        "agentId": row["agent_id"],
        "taskId": row["task_id"],
        "testPlan": row["test_plan"],
        "acceptanceChecklist": json_loads(row["acceptance_checklist"], []),
        "testResults": json_loads(row["test_results"], []),
        "logs": json_loads(row["logs"], []),
        "diffRefs": json_loads(row["diff_refs"], []),
        "screenshotRefs": json_loads(row["screenshot_refs"], []),
        "riskNotes": json_loads(row["risk_notes"], []),
        "qaVerdict": row["qa_verdict"],
        "createdAt": row["created_at"],
    }


def row_to_test_result(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "evidencePackageId": row["evidence_package_id"],
        "command": row["command"],
        "status": row["status"],
        "durationMs": row["duration_ms"],
        "outputRef": row["output_ref"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


def row_to_artifact(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "evidencePackageId": row["evidence_package_id"],
        "kind": row["kind"],
        "path": row["path"],
        "hash": row["hash"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


class EvidenceRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_evidence_package(
        self,
        *,
        project_id: str,
        workflow_run_id: str | None,
        agent_id: str | None,
        task_id: str,
        test_plan: str,
        acceptance_checklist: list[Any] | None = None,
        test_results: list[Any] | None = None,
        logs: list[Any] | None = None,
        diff_refs: list[Any] | None = None,
        screenshot_refs: list[Any] | None = None,
        risk_notes: list[Any] | None = None,
        qa_verdict: str = "not_started",
    ) -> dict[str, Any]:
        evidence_id = f"evidence-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO evidence_packages
                (id, project_id, workflow_run_id, agent_id, task_id, test_plan,
                 acceptance_checklist, test_results, logs, diff_refs, screenshot_refs,
                 risk_notes, qa_verdict, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                project_id,
                workflow_run_id,
                agent_id,
                task_id,
                test_plan,
                json_dumps(acceptance_checklist or []),
                json_dumps(test_results or []),
                json_dumps(logs or []),
                json_dumps(diff_refs or []),
                json_dumps(screenshot_refs or []),
                json_dumps(risk_notes or []),
                qa_verdict,
                utc_now(),
            ),
        )
        for result in test_results or []:
            self.connection.execute(
                """
                INSERT INTO test_results
                    (id, project_id, evidence_package_id, command, status, duration_ms,
                     output_ref, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"test-result-{uuid.uuid4()}",
                    project_id,
                    evidence_id,
                    str(result.get("command", "")),
                    str(result.get("status", "unknown")),
                    result.get("durationMs"),
                    result.get("outputRef"),
                    json_dumps(result.get("metadata") or {}),
                    utc_now(),
                ),
            )
        self.connection.execute(
            """
            INSERT INTO qa_verdicts (id, project_id, evidence_package_id, verdict, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"qa-verdict-{uuid.uuid4()}",
                project_id,
                evidence_id,
                qa_verdict,
                "Evidence package created",
                utc_now(),
            ),
        )
        return self.get_evidence_package(evidence_id)

    def get_evidence_package(self, evidence_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM evidence_packages WHERE id = ?", (evidence_id,)).fetchone()
        if not row:
            raise KeyError(f"Evidence package not found: {evidence_id}")
        return row_to_evidence_package(row)

    def list_test_results(self, evidence_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM test_results WHERE evidence_package_id = ? ORDER BY created_at ASC",
            (evidence_id,),
        ).fetchall()
        return [row_to_test_result(row) for row in rows]

    def create_artifact(
        self,
        *,
        project_id: str,
        evidence_package_id: str | None,
        kind: str,
        path: str,
        content_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_id = artifact_id or f"artifact-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO artifacts
                (id, project_id, evidence_package_id, kind, path, hash, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                project_id,
                evidence_package_id,
                kind,
                path,
                content_hash,
                json_dumps(metadata or {}),
                utc_now(),
            ),
        )
        row = self.connection.execute("SELECT * FROM artifacts WHERE id = ?", (resolved_id,)).fetchone()
        return row_to_artifact(row)

    def list_artifacts(self, evidence_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM artifacts WHERE evidence_package_id = ? ORDER BY created_at ASC",
            (evidence_id,),
        ).fetchall()
        return [row_to_artifact(row) for row in rows]

    def list_all_artifacts(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM artifacts ORDER BY created_at ASC").fetchall()
        return [row_to_artifact(row) for row in rows]

    def list_expired_referenced_artifacts(self, *, now_iso: str) -> list[dict[str, Any]]:
        expired: list[dict[str, Any]] = []
        for artifact in self.list_all_artifacts():
            if not artifact.get("evidencePackageId"):
                continue
            metadata = artifact.get("metadata") or {}
            expires_at = metadata.get("expiresAt")
            if isinstance(expires_at, str) and expires_at <= now_iso:
                expired.append({**artifact, "retentionStatus": "expired", "expiresAt": expires_at})
        return expired

    def get_artifact(self, *, evidence_id: str, artifact_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM artifacts WHERE id = ? AND evidence_package_id = ?",
            (artifact_id, evidence_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Artifact not found for evidence package: {artifact_id}")
        return row_to_artifact(row)

    def get_artifact_by_id(self, artifact_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError(f"Artifact not found: {artifact_id}")
        return row_to_artifact(row)

    def update_artifact_metadata(self, *, artifact_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        self.connection.execute(
            "UPDATE artifacts SET metadata = ? WHERE id = ?",
            (json_dumps(metadata), artifact_id),
        )
        return self.get_artifact_by_id(artifact_id)

    def attach_artifact_to_evidence(self, *, artifact_id: str, evidence_package_id: str) -> None:
        self.connection.execute(
            "UPDATE artifacts SET evidence_package_id = ? WHERE id = ?",
            (evidence_package_id, artifact_id),
        )

    def list_all_test_results(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM test_results WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM test_results ORDER BY created_at DESC").fetchall()
        return [row_to_test_result(row) for row in rows]

    def list_evidence_packages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM evidence_packages WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM evidence_packages ORDER BY created_at DESC").fetchall()
        return [row_to_evidence_package(row) for row in rows]

    def list_evidence_for_workflow_runs(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        if not workflow_run_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_run_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM evidence_packages
            WHERE workflow_run_id IN ({placeholders})
            ORDER BY created_at DESC
            """,
            tuple(workflow_run_ids),
        ).fetchall()
        return [row_to_evidence_package(row) for row in rows]


