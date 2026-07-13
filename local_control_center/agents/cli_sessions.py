"""Persiste sesiones de runtime CLI con sus artefactos de evidencia y consumo redactados.

Por cada ejecución de un runtime CLI graba la fila en cli_sessions, escribe artefactos de stdout/stderr/log
saneados con redact_secrets y registra el uso en el UsageLedger (real o estimado). Garantiza que ningún
secreto del comando, entorno o salida quede en disco o en la base sin redactar.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.usage_ledger import UsageLedger
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now


class CliSessionStore:
    """Almacena el resultado de una sesión CLI: fila en cli_sessions, artefactos y registro de uso."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record_result(
        self,
        *,
        runtime: str,
        executable: str,
        workspace_id: str,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
        agent_id: str | None = None,
        command: list[str],
        env_policy: dict[str, Any],
        status: str,
        model: str | None = None,
        role: str | None = None,
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
        usage: Any = None,
    ) -> dict[str, Any]:
        """Graba la sesión CLI completa (artefactos + uso, todo redactado) y devuelve la fila persistida.

        Escribe la fila en cli_sessions, crea sus artefactos de stdout/stderr/log, registra el consumo
        y enlaza el usage_ledger_id resultante en una única transacción de la conexión del caller.
        """
        session_id = f"cli-session-{uuid.uuid4()}"
        now = utc_now()
        artifact_ids = self._write_artifacts(
            session_id=session_id,
            runtime=runtime,
            executable=executable,
            workspace_id=workspace_id,
            command=command,
            env_policy=env_policy,
            status=status,
            stdout=stdout,
            stderr=stderr,
            error=error,
        )
        self.connection.execute(
            """
            INSERT INTO cli_sessions
                (id, runtime, executable, workspace_id, workflow_run_id, workflow_step_id, agent_id,
                 command_json, env_policy_json, status, started_at, finished_at, usage_ledger_id,
                 stdout_artifact_id, stderr_artifact_id, logs_artifact_id, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                runtime,
                executable,
                workspace_id,
                workflow_run_id,
                workflow_step_id,
                agent_id,
                json_dumps(redact_secrets(command)),
                json_dumps(redact_secrets(env_policy)),
                status,
                now,
                now,
                artifact_ids.get("stdout"),
                artifact_ids.get("stderr"),
                artifact_ids.get("logs"),
                redact_secrets(error or ""),
                now,
            ),
        )
        usage_record = self._record_usage(
            session_id=session_id,
            runtime=runtime,
            model=model,
            role=role,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            agent_id=agent_id,
            status=status,
            stdout=stdout,
            stderr=stderr,
            usage=usage,
        )
        self.connection.execute(
            "UPDATE cli_sessions SET usage_ledger_id = ? WHERE id = ?",
            (usage_record["id"], session_id),
        )
        row = self.connection.execute("SELECT * FROM cli_sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row)

    def _write_artifacts(
        self,
        *,
        session_id: str,
        runtime: str,
        executable: str,
        workspace_id: str,
        command: list[str],
        env_policy: dict[str, Any],
        status: str,
        stdout: str,
        stderr: str,
        error: str | None,
    ) -> dict[str, str | None]:
        project_id, root = self._artifact_context(workspace_id)
        artifacts: dict[str, str | None] = {"stdout": None, "stderr": None, "logs": None}
        if stdout:
            artifacts["stdout"] = self._create_text_artifact(
                project_id=project_id,
                root=root,
                artifact_id=f"artifact-{uuid.uuid4()}",
                kind="cli_stdout",
                suffix=".stdout.log",
                content=stdout,
                metadata={
                    "source": "cli_runtime",
                    "stream": "stdout",
                    "sessionId": session_id,
                    "runtime": runtime,
                    "workspaceId": workspace_id,
                },
            )
        if stderr:
            artifacts["stderr"] = self._create_text_artifact(
                project_id=project_id,
                root=root,
                artifact_id=f"artifact-{uuid.uuid4()}",
                kind="cli_stderr",
                suffix=".stderr.log",
                content=stderr,
                metadata={
                    "source": "cli_runtime",
                    "stream": "stderr",
                    "sessionId": session_id,
                    "runtime": runtime,
                    "workspaceId": workspace_id,
                },
            )
        logs_payload = {
            "sessionId": session_id,
            "runtime": runtime,
            "executable": executable,
            "workspaceId": workspace_id,
            "status": status,
            "command": command,
            "envPolicy": env_policy,
            "error": error,
            "stdoutBytes": len(stdout.encode("utf-8")),
            "stderrBytes": len(stderr.encode("utf-8")),
        }
        artifacts["logs"] = self._create_text_artifact(
            project_id=project_id,
            root=root,
            artifact_id=f"artifact-{uuid.uuid4()}",
            kind="cli_runtime_log",
            suffix=".runtime.json",
            content=json_dumps(redact_secrets(logs_payload)),
            metadata={
                "source": "cli_runtime",
                "stream": "runtime_log",
                "sessionId": session_id,
                "runtime": runtime,
                "workspaceId": workspace_id,
            },
        )
        return artifacts

    def _artifact_context(self, workspace_id: str) -> tuple[str, Path]:
        row = self.connection.execute(
            "SELECT project_id, path FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row:
            return str(row["project_id"]), Path(row["path"]).resolve(strict=False)
        return "model-gateway", self._database_root()

    def _database_root(self) -> Path:
        row = self.connection.execute("PRAGMA database_list").fetchone()
        if row and row["file"]:
            return Path(str(row["file"])).resolve(strict=False).parent
        return Path.cwd().resolve(strict=False)

    def _create_text_artifact(
        self,
        *,
        project_id: str,
        root: Path,
        artifact_id: str,
        kind: str,
        suffix: str,
        content: str,
        metadata: dict[str, Any],
    ) -> str:
        sanitized_content = redact_secrets(content)
        artifact = write_text_artifact(
            root=root,
            artifact_id=artifact_id,
            suffix=suffix,
            content=sanitized_content,
        )
        sanitized_metadata = redact_secrets(
            {
                **metadata,
                "sizeBytes": artifact["sizeBytes"],
                "hashAlgorithm": "sha256",
            }
        )
        EvidenceRepository(self.connection).create_artifact(
            project_id=project_id,
            evidence_package_id=None,
            kind=kind,
            path=str(Path(artifact["path"]).resolve(strict=False)),
            content_hash=artifact["hash"] or hashlib.sha256(sanitized_content.encode("utf-8")).hexdigest(),
            metadata=sanitized_metadata,
            artifact_id=artifact_id,
        )
        return artifact_id

    def _record_usage(
        self,
        *,
        session_id: str,
        runtime: str,
        model: str | None,
        role: str | None,
        workflow_run_id: str | None,
        workflow_step_id: str | None,
        agent_id: str | None,
        status: str,
        stdout: str,
        stderr: str,
        usage: Any,
    ) -> dict[str, Any]:
        if usage is not None:
            return UsageLedger(self.connection).record_usage(
                provider_id=runtime,
                model=model or "cli",
                runtime_type="cli",
                agent_id=agent_id,
                role=role,
                workflow_run_id=workflow_run_id,
                workflow_step_id=workflow_step_id,
                session_id=session_id,
                input_tokens=int(getattr(usage, "input_tokens", 0)),
                cached_input_tokens=int(getattr(usage, "cached_input_tokens", 0)),
                output_tokens=int(getattr(usage, "output_tokens", 0)),
                reasoning_tokens=int(getattr(usage, "reasoning_tokens", 0)),
                tool_tokens=int(getattr(usage, "tool_tokens", 0)),
                total_tokens=int(getattr(usage, "total_tokens", 0)),
                raw_usage=getattr(usage, "raw_usage", None) or {"usage_source": "cli_output"},
                usage_source="actual",
            )
        return UsageLedger(self.connection).record_usage(
            provider_id=runtime,
            model=model or "cli",
            runtime_type="cli",
            agent_id=agent_id,
            role=role,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            session_id=session_id,
            raw_usage={"usage_source": "unavailable", "status": status},
            usage_source="unavailable",
        )
