"""Runner del project assessment: ejecuta la inspección estática a través de una decisión de política.

A diferencia de ``projects.assessment`` (que es lógica pura de solo lectura), este runner es el
camino *policy-gated*: registra job y agent run para auditoría, somete una única llamada de tool al
``ToolBroker`` con ``operation="project_assessment"`` y ``execute=False`` —de modo que el broker
deja una decisión de política y su fila de auditoría, sin ejecutar— y solo cuando esa decisión es
``allow`` corre ``run_project_assessment`` sobre la ruta exacta que autorizó la decisión. Persiste el
assessment, sus hallazgos (ya redactados) y un artefacto JSON descargable dentro de una sola
``immediate_transaction``, y falla cerrado (status ``blocked``) si la política no concede el permiso.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.assessment import run_project_assessment
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.serialization import json_dumps

from .repository import AgentsRepository
from .tool_broker import ToolBroker

PROJECT_ASSESSMENT_AGENT_ID = "project_assessment_agent"
PROJECT_ASSESSMENT_TASK_ID = "project_assessment"
PROJECT_ASSESSMENT_TOOL = "project_assessment"
PROJECT_ASSESSMENT_JOB_KIND = "agent.project_assessment"


class ProjectAssessmentRunner:
    """Orquesta el project assessment como capacidad de solo lectura gobernada por política.

    Mantiene la invariante del ToolBroker: la inspección no ocurre sin una decisión ``allow`` del
    ``policy_engine`` (registrada y auditada), está confinada a la ruta del proyecto que la decisión
    autorizó, y deniega red y secretos. El assessment + hallazgos + artefacto se confirman de forma
    atómica; ante un ``deny`` no se persiste ningún assessment.
    """

    def __init__(self, connection: sqlite3.Connection, *, root: str | Path):
        self.connection = connection
        self.root = Path(root)
        self.projects = ProjectsRepository(connection)
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)

    def run(self, project_id: str) -> dict[str, Any]:
        """Ejecuta el assessment policy-gated del proyecto y devuelve estado, assessment, hallazgos y artefacto.

        Crea job + agent run, somete la llamada de tool gateada (``execute=False``) y, solo si la
        decisión es ``allow``, corre el assessment sobre la ruta autorizada, lo persiste con su
        artefacto JSON y finaliza. Falla cerrado con status ``blocked`` si la política no concede.

        Raises:
            KeyError: si el proyecto no existe.
        """
        project = self.projects.get_project(project_id)
        root_path = str(project["path"])
        profile = self._ensure_profile()
        job = self.jobs.create_job(
            project_id=project_id,
            kind=PROJECT_ASSESSMENT_JOB_KIND,
            status="running",
            payload={"taskId": PROJECT_ASSESSMENT_TASK_ID, "rootPath": root_path},
        )["job"]
        agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id=PROJECT_ASSESSMENT_TASK_ID,
            input_payload={"projectId": project_id, "rootPath": root_path},
            output_payload={},
            job_id=job["id"],
            status="running",
        )

        gate = self._gate(
            project_id=project_id,
            agent_run_id=agent_run["id"],
            profile=profile,
            job_id=job["id"],
            root_path=root_path,
        )
        decision = gate["decision"]
        if decision["decision"] != "allow":
            return self._finalize_blocked(job=job, agent_run=agent_run, decision=decision)

        with immediate_transaction(self.connection):
            run = run_project_assessment(self.projects, project_id=project_id, root_path=root_path)
            artifact = self._write_artifact(
                project_id=project_id, assessment=run["assessment"], findings=run["findings"]
            )
        assessment = run["assessment"]
        output = {
            "status": "completed",
            "assessmentId": assessment["id"],
            "findingsCount": assessment["findingsCount"],
            "artifactId": artifact["id"],
            "decision": decision["decision"],
        }
        finished_run = self.agents.update_agent_run_status(
            agent_run["id"], status="completed", output_payload=output
        )
        finished_job = self.jobs.update_job_status(job["id"], status="completed", metadata=output)
        return {
            "status": "completed",
            "reason": "Project assessment completed through a policy-gated read-only inspection.",
            "assessment": assessment,
            "findings": run["findings"],
            "artifact": artifact,
            "decision": decision,
            "job": finished_job,
            "agentRun": finished_run,
        }

    def _ensure_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": PROJECT_ASSESSMENT_AGENT_ID,
                "name": "ProjectAssessmentAgent",
                "role": "assessor",
                "runtimeMode": "manual",
                "permissionProfile": "plan",
                "allowedTools": [PROJECT_ASSESSMENT_TOOL],
            }
        )

    def _gate(
        self, *, project_id: str, agent_run_id: str, profile: dict[str, Any], job_id: str, root_path: str
    ) -> dict[str, Any]:
        broker = ToolBroker(self.connection, artifact_root=self.root)
        return broker.evaluate_tool_call(
            project_id=project_id,
            agent_run_id=agent_run_id,
            agent_profile=profile,
            job_id=job_id,
            tool_call={
                "tool": PROJECT_ASSESSMENT_TOOL,
                "operation": PROJECT_ASSESSMENT_TOOL,
                "path": root_path,
                "workspacePath": root_path,
                "capability": "read_only",
                "networkRequired": False,
                "secretsRequired": False,
                "execute": False,
            },
        )

    def _write_artifact(
        self, *, project_id: str, assessment: dict[str, Any], findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps({"summary": assessment["summary"], "findings": findings})
        written = write_text_artifact(
            root=self.root, artifact_id=artifact_id, suffix=".json", content=content
        )
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="project_assessment",
            path=written["path"],
            content_hash=written["hash"],
            metadata={
                "name": "project-assessment.json",
                "source": PROJECT_ASSESSMENT_AGENT_ID,
                "assessmentId": assessment["id"],
                "mimeType": "application/json",
                "hashAlgorithm": "sha256",
            },
        )

    def _finalize_blocked(
        self, *, job: dict[str, Any], agent_run: dict[str, Any], decision: dict[str, Any]
    ) -> dict[str, Any]:
        reason = f"Project assessment was not permitted by policy: {decision['reason']}"
        output = {"status": "blocked", "reason": reason, "decision": decision["decision"]}
        finished_run = self.agents.update_agent_run_status(
            agent_run["id"], status="failed", output_payload=output
        )
        finished_job = self.jobs.update_job_status(job["id"], status="failed", metadata=output)
        return {
            "status": "blocked",
            "reason": reason,
            "assessment": None,
            "findings": [],
            "artifact": None,
            "decision": decision,
            "job": finished_job,
            "agentRun": finished_run,
        }
