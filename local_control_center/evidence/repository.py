"""Persistencia SQLite de paquetes de evidencia, resultados de test y artefactos.

Traduce filas a dicts con claves camelCase y agrupa las escrituras de cada paquete. Las
mutaciones usan la `connection` recibida del caller y NO hacen commit: el caller delimita la
transacción (típicamente un INSERT de paquete + sus test_results + su qa_verdict deben confirmarse
o revertirse juntos). Toda entrada se pasa por `redact_secrets` antes de serializarse a JSON.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def _redact_diff_refs(diff_refs: list[Any]) -> list[Any]:
    redacted: list[Any] = []
    for diff_ref in diff_refs:
        if not isinstance(diff_ref, dict):
            redacted.append(redact_secrets(diff_ref))
            continue
        patch_fields = {key: value for key, value in diff_ref.items() if key in {"patch", "patchFull"}}
        metadata_fields = {key: value for key, value in diff_ref.items() if key not in patch_fields}
        redacted.append({**redact_secrets(metadata_fields), **patch_fields})
    return redacted


def row_to_evidence_package(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de `evidence_packages` al dict de API, tolerando columnas aún no migradas."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"] if "workflow_step_id" in row.keys() else None,
        "agentId": row["agent_id"],
        "agentRunId": row["agent_run_id"] if "agent_run_id" in row.keys() else None,
        "jobId": row["job_id"] if "job_id" in row.keys() else None,
        "workspaceId": row["workspace_id"] if "workspace_id" in row.keys() else None,
        "runtimeId": row["runtime_id"] if "runtime_id" in row.keys() else None,
        "taskId": row["task_id"],
        "testPlan": row["test_plan"],
        "acceptanceChecklist": json_loads(row["acceptance_checklist"], []),
        "testResults": json_loads(row["test_results"], []),
        "logs": json_loads(row["logs"], []),
        "diffRefs": json_loads(row["diff_refs"], []),
        "screenshotRefs": json_loads(row["screenshot_refs"], []),
        "riskNotes": json_loads(row["risk_notes"], []),
        "artifactIds": json_loads(row["artifact_ids"], []) if "artifact_ids" in row.keys() else [],
        "diffSummary": json_loads(row["diff_summary"], {}) if "diff_summary" in row.keys() else {},
        "runtimeHealth": json_loads(row["runtime_health"], {}) if "runtime_health" in row.keys() else {},
        "modelCalls": json_loads(row["model_calls"], []) if "model_calls" in row.keys() else [],
        "toolCalls": json_loads(row["tool_calls"], []) if "tool_calls" in row.keys() else [],
        "policyDecisions": json_loads(row["policy_decisions"], [])
        if "policy_decisions" in row.keys()
        else [],
        "approvals": json_loads(row["approvals"], []) if "approvals" in row.keys() else [],
        "artifacts": json_loads(row["artifact_refs"], []) if "artifact_refs" in row.keys() else [],
        "hashes": json_loads(row["hashes"], {}) if "hashes" in row.keys() else {},
        "evidenceSource": row["evidence_source"] if "evidence_source" in row.keys() else "operator_attested",
        "qaVerdict": row["qa_verdict"],
        "createdAt": row["created_at"],
    }


def row_to_test_result(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de `test_results` al dict de API con su metadata deserializada."""
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
    """Mapea una fila de `artifacts` al dict de API con su metadata deserializada."""
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
    """Acceso de datos a paquetes de evidencia y sus tablas satélite sobre una conexión SQLite."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_evidence_package(
        self,
        *,
        project_id: str,
        workflow_run_id: str | None,
        workflow_step_id: str | None = None,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        job_id: str | None = None,
        workspace_id: str | None = None,
        runtime_id: str | None = None,
        task_id: str = "task",
        test_plan: str = "",
        acceptance_checklist: list[Any] | None = None,
        test_results: list[Any] | None = None,
        logs: list[Any] | None = None,
        diff_refs: list[Any] | None = None,
        screenshot_refs: list[Any] | None = None,
        risk_notes: list[Any] | None = None,
        artifact_ids: list[str] | None = None,
        diff_summary: dict[str, Any] | None = None,
        runtime_health: dict[str, Any] | None = None,
        model_calls: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        policy_decisions: list[dict[str, Any]] | None = None,
        approvals: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        hashes: dict[str, str] | None = None,
        evidence_source: str = "operator_attested",
        qa_verdict: str = "not_started",
    ) -> dict[str, Any]:
        """Inserta el paquete, una fila por testResult y su qa_verdict en la misma conexión.

        Las tres escrituras forman una unidad lógica que el caller debe confirmar/revertir
        atómicamente (este método no hace commit). Toda entrada se redacta antes de serializarse.
        Devuelve el paquete recién leído.
        """
        evidence_id = f"evidence-{uuid.uuid4()}"
        clean_acceptance = redact_secrets(acceptance_checklist or [])
        clean_test_results = redact_secrets(test_results or [])
        clean_logs = redact_secrets(logs or [])
        clean_diff_refs = _redact_diff_refs(diff_refs or [])
        clean_screenshot_refs = redact_secrets(screenshot_refs or [])
        clean_risk_notes = redact_secrets(risk_notes or [])
        clean_artifact_ids = [str(item) for item in artifact_ids or [] if isinstance(item, str)]
        clean_diff_summary = redact_secrets(diff_summary or {})
        clean_runtime_health = redact_secrets(runtime_health or {})
        clean_model_calls = redact_secrets(model_calls or [])
        clean_tool_calls = redact_secrets(tool_calls or [])
        clean_policy_decisions = redact_secrets(policy_decisions or [])
        clean_approvals = redact_secrets(approvals or [])
        clean_artifacts = redact_secrets(artifacts or [])
        clean_hashes = {
            str(key): str(value) for key, value in (hashes or {}).items() if isinstance(value, str)
        }
        clean_evidence_source = str(evidence_source or "operator_attested")
        self.connection.execute(
            """
            INSERT INTO evidence_packages
                (id, project_id, workflow_run_id, workflow_step_id, agent_id, agent_run_id,
                 job_id, workspace_id, runtime_id, task_id, test_plan,
                 acceptance_checklist, test_results, logs, diff_refs, screenshot_refs,
                 risk_notes, artifact_ids, diff_summary, runtime_health, model_calls,
                 tool_calls, policy_decisions, approvals, artifact_refs, hashes, evidence_source,
                 qa_verdict, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                project_id,
                workflow_run_id,
                workflow_step_id,
                agent_id,
                agent_run_id,
                job_id,
                workspace_id,
                runtime_id,
                task_id,
                test_plan,
                json_dumps(clean_acceptance),
                json_dumps(clean_test_results),
                json_dumps(clean_logs),
                json_dumps(clean_diff_refs),
                json_dumps(clean_screenshot_refs),
                json_dumps(clean_risk_notes),
                json_dumps(clean_artifact_ids),
                json_dumps(clean_diff_summary),
                json_dumps(clean_runtime_health),
                json_dumps(clean_model_calls),
                json_dumps(clean_tool_calls),
                json_dumps(clean_policy_decisions),
                json_dumps(clean_approvals),
                json_dumps(clean_artifacts),
                json_dumps(clean_hashes),
                clean_evidence_source,
                qa_verdict,
                utc_now(),
            ),
        )
        for result in clean_test_results:
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
                    json_dumps(redact_secrets(result.get("metadata") or {})),
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
        """Lee un paquete por id.

        Raises:
            KeyError: si no existe ningún paquete con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM evidence_packages WHERE id = ?", (evidence_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Evidence package not found: {evidence_id}")
        return row_to_evidence_package(row)

    def update_evidence_links(
        self,
        evidence_id: str,
        *,
        agent_run_id: str | None = None,
        artifact_ids: list[str] | None = None,
        diff_summary: dict[str, Any] | None = None,
        runtime_health: dict[str, Any] | None = None,
        model_calls: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        policy_decisions: list[dict[str, Any]] | None = None,
        approvals: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        hashes: dict[str, str] | None = None,
        qa_verdict: str | None = None,
        risk_notes: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Aplica un UPDATE parcial sobre un paquete: los argumentos None conservan el valor actual.

        Lee el estado vigente, mezcla solo los campos provistos y reescribe la fila en un único
        UPDATE sobre la conexión del caller (sin commit). Devuelve el paquete actualizado.
        """
        current = self.get_evidence_package(evidence_id)
        next_agent_run_id = agent_run_id if agent_run_id is not None else current.get("agentRunId")
        next_artifact_ids = artifact_ids if artifact_ids is not None else current.get("artifactIds", [])
        next_diff_summary = diff_summary if diff_summary is not None else current.get("diffSummary", {})
        next_runtime_health = (
            runtime_health if runtime_health is not None else current.get("runtimeHealth", {})
        )
        next_model_calls = model_calls if model_calls is not None else current.get("modelCalls", [])
        next_tool_calls = tool_calls if tool_calls is not None else current.get("toolCalls", [])
        next_policy_decisions = (
            policy_decisions if policy_decisions is not None else current.get("policyDecisions", [])
        )
        next_approvals = approvals if approvals is not None else current.get("approvals", [])
        next_artifacts = artifacts if artifacts is not None else current.get("artifacts", [])
        next_hashes = hashes if hashes is not None else current.get("hashes", {})
        next_qa_verdict = qa_verdict if qa_verdict is not None else current.get("qaVerdict", "not_started")
        next_risk_notes = risk_notes if risk_notes is not None else current.get("riskNotes", [])
        self.connection.execute(
            """
            UPDATE evidence_packages
            SET agent_run_id = ?, artifact_ids = ?, diff_summary = ?, runtime_health = ?,
                model_calls = ?, tool_calls = ?, policy_decisions = ?, approvals = ?,
                artifact_refs = ?, hashes = ?, qa_verdict = ?, risk_notes = ?
            WHERE id = ?
            """,
            (
                next_agent_run_id,
                json_dumps([str(item) for item in next_artifact_ids if isinstance(item, str)]),
                json_dumps(redact_secrets(next_diff_summary or {})),
                json_dumps(redact_secrets(next_runtime_health or {})),
                json_dumps(redact_secrets(next_model_calls or [])),
                json_dumps(redact_secrets(next_tool_calls or [])),
                json_dumps(redact_secrets(next_policy_decisions or [])),
                json_dumps(redact_secrets(next_approvals or [])),
                json_dumps(redact_secrets(next_artifacts or [])),
                json_dumps(
                    {
                        str(key): str(value)
                        for key, value in (next_hashes or {}).items()
                        if isinstance(value, str)
                    }
                ),
                next_qa_verdict,
                json_dumps(redact_secrets(next_risk_notes or [])),
                evidence_id,
            ),
        )
        return self.get_evidence_package(evidence_id)

    def list_test_results(self, evidence_id: str) -> list[dict[str, Any]]:
        """Lista los resultados de test de un paquete en orden cronológico de creación."""
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
        """Inserta un artefacto (generando id si falta) y devuelve la fila persistida.

        Un `evidence_package_id` None deja el artefacto sin enlazar, listo para adjuntarse luego.
        """
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
                json_dumps(redact_secrets(metadata or {})),
                utc_now(),
            ),
        )
        row = self.connection.execute("SELECT * FROM artifacts WHERE id = ?", (resolved_id,)).fetchone()
        return row_to_artifact(row)

    def list_artifacts(self, evidence_id: str) -> list[dict[str, Any]]:
        """Lista los artefactos enlazados a un paquete en orden cronológico de creación."""
        rows = self.connection.execute(
            "SELECT * FROM artifacts WHERE evidence_package_id = ? ORDER BY created_at ASC",
            (evidence_id,),
        ).fetchall()
        return [row_to_artifact(row) for row in rows]

    def list_all_artifacts(self) -> list[dict[str, Any]]:
        """Lista todos los artefactos del store, enlazados o no, en orden de creación."""
        rows = self.connection.execute("SELECT * FROM artifacts ORDER BY created_at ASC").fetchall()
        return [row_to_artifact(row) for row in rows]

    def list_expired_referenced_artifacts(self, *, now_iso: str) -> list[dict[str, Any]]:
        """Devuelve los artefactos enlazados cuyo `expiresAt` ya pasó respecto de `now_iso`.

        Anota cada uno con `retentionStatus='expired'` y `expiresAt` para alimentar el plan de
        retención. Los artefactos sin paquete o sin fecha de expiración se excluyen.
        """
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
        """Lee un artefacto exigiendo que pertenezca al paquete indicado.

        Raises:
            KeyError: si el artefacto no existe o no está enlazado a ese paquete.
        """
        row = self.connection.execute(
            "SELECT * FROM artifacts WHERE id = ? AND evidence_package_id = ?",
            (artifact_id, evidence_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Artifact not found for evidence package: {artifact_id}")
        return row_to_artifact(row)

    def get_artifact_by_id(self, artifact_id: str) -> dict[str, Any]:
        """Lee un artefacto por id sin importar el paquete al que esté enlazado.

        Raises:
            KeyError: si no existe ningún artefacto con ese id.
        """
        row = self.connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError(f"Artifact not found: {artifact_id}")
        return row_to_artifact(row)

    def get_project_artifact(self, *, project_id: str, artifact_id: str) -> dict[str, Any]:
        """Read one artifact only when both its id and owning project match."""
        row = self.connection.execute(
            "SELECT * FROM artifacts WHERE id = ? AND project_id = ?",
            (artifact_id, project_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Artifact not found for project: {artifact_id}")
        return row_to_artifact(row)

    def update_artifact_metadata(self, *, artifact_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Reemplaza la metadata de un artefacto (redactada) y devuelve la fila actualizada."""
        self.connection.execute(
            "UPDATE artifacts SET metadata = ? WHERE id = ?",
            (json_dumps(redact_secrets(metadata)), artifact_id),
        )
        return self.get_artifact_by_id(artifact_id)

    def attach_artifact_to_evidence(self, *, artifact_id: str, evidence_package_id: str) -> None:
        """Enlaza un artefacto existente a un paquete reescribiendo su `evidence_package_id`."""
        self.connection.execute(
            "UPDATE artifacts SET evidence_package_id = ? WHERE id = ?",
            (evidence_package_id, artifact_id),
        )

    def list_all_test_results(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista resultados de test (todos o de un proyecto) del más reciente al más antiguo."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM test_results WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM test_results ORDER BY created_at DESC").fetchall()
        return [row_to_test_result(row) for row in rows]

    def list_evidence_packages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista paquetes de evidencia (todos o de un proyecto) del más reciente al más antiguo."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM evidence_packages WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM evidence_packages ORDER BY created_at DESC"
            ).fetchall()
        return [row_to_evidence_package(row) for row in rows]

    def list_evidence_for_workflow_runs(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        """Lista los paquetes asociados a un conjunto de workflow runs; vacío si no se pide ninguno."""
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
