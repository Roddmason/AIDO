"""Ejecuta el ProductOwnerAgent: analiza una idea o assessment y produce brief y backlog validados.

Selecciona un runtime real (CLI codex/claude preferido, modelo como camino que devuelve JSON),
le envía la idea o el assessment existente acotados, valida que la salida cumpla el esquema estricto,
calcula la completitud de forma determinista y NO genera épicas/HU/criterios si quedan decisiones
bloqueantes sin resolver. Solo persiste discovery y backlog tras validar; falla cerrado si falta runtime
o la salida es inválida.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.artifacts import (
    artifact_hashes,
    artifact_records_from_ids,
    artifact_ref,
    write_text_artifact,
)
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .assessment_runner import ProjectAssessmentRunner
from .autonomy_profiles import REVERSIBILITIES, AutonomyEngine, AutonomyProfile
from .impact_question_engine import (
    ImpactQuestionEngine,
    ImpactQuestionValidationError,
    detected_facts_from_assessment,
    validate_impact_question,
)
from .product_owner_agent_contract import (
    PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
    PRODUCT_OWNER_AGENT_CLI_RUNTIMES,
    PRODUCT_OWNER_AGENT_ID,
    PRODUCT_OWNER_AGENT_MODEL_RUNTIMES,
    product_owner_agent_contract,
    product_owner_agent_readiness,
)
from .repository import AgentsRepository
from .runtime_registry import RuntimeCommandUnavailableError, build_product_owner_agent_argv
from .runtime_status import RuntimeStatusService
from .tool_broker import ToolBroker

RUNTIME_UNAVAILABLE_STATUS = "runtime_unavailable"
FAILED_VALIDATION_STATUS = "failed_validation"
COMPLETED_STATUS = "completed"
BLOCKED_STATUS = "blocked"
CONFIDENCES = {"low", "medium", "high"}
BUSINESS_VALUES = {"low", "medium", "high"}
BLOCKING_DECISION_STATUSES = {"open", "resolved"}
QUESTION_CONFIDENCE_PRIORITY = {"low": "high", "medium": "medium", "high": "low"}
DEFAULT_COMPLETENESS_THRESHOLD = 70
BLOCKED_COMPLETENESS_CAP = 60
PROMPT_TEXT_LIMIT_CHARS = 8_000
PROMPT_COLLECTION_LIMIT = 20
RUNTIME_OUTPUT_LIMIT_CHARS = 200_000
ASSESSMENT_SIGNAL_LIMIT = 5

REQUIRED_BRIEF_TEXT_FIELDS = ["title", "summary", "problemStatement", "scope", "outOfScope"]
REQUIRED_BRIEF_LIST_FIELDS = ["goals", "targetUsers", "successMetrics"]


class ProductOwnerOutputValidationError(ValueError):
    """Se lanza cuando la salida del runtime no cumple el esquema estricto del ProductOwnerAgent."""


def _runtime_mode(runtime_id: str) -> str:
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        return "cli"
    return "ollama" if runtime_id == "ollama" else "api"


def _runtime_unavailable_result(reason: str) -> dict[str, Any]:
    return {"status": RUNTIME_UNAVAILABLE_STATUS, "reason": reason, "execution": "not_executed"}


def _execution_result_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    return {
        "status": "completed" if tool_call.get("status") == "completed" else "failed",
        "toolCallId": tool_call.get("id"),
        "execution": payload.get("execution"),
        "returnCode": execution_result.get("returnCode"),
        "timedOut": bool(execution_result.get("timedOut", False)),
        "blocked": bool(execution_result.get("blocked", False)),
        "reason": execution_result.get("reason") or payload.get("decisionReason"),
        "outputArtifactId": execution_result.get("outputArtifactId"),
        "stdoutArtifactId": execution_result.get("stdoutArtifactId"),
        "evidencePackageId": execution_result.get("evidencePackageId"),
    }


def _bounded_text(value: Any, limit: int = PROMPT_TEXT_LIMIT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ProductOwnerOutputValidationError(f"{field} must be a string list.")
    items = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
    if len(items) != len(value):
        raise ProductOwnerOutputValidationError(f"{field} must contain only non-empty strings.")
    return items


def _required_text(item: dict[str, Any], key: str, *, field: str) -> str:
    text = str(item.get(key) or "").strip()
    if not text:
        raise ProductOwnerOutputValidationError(f"{field}.{key} is required.")
    return text


def _enum_value(item: dict[str, Any], key: str, allowed: set[str], default: str, *, field: str) -> str:
    value = str(item.get(key) or default).strip().lower()
    if value not in allowed:
        raise ProductOwnerOutputValidationError(f"{field}.{key} must be one of {sorted(allowed)}.")
    return value


def _question_priority(question: dict[str, Any]) -> str:
    if question.get("blocking"):
        return "high"
    return QUESTION_CONFIDENCE_PRIORITY.get(str(question.get("confidence")), "medium")


def _autonomy_candidate(decision: dict[str, Any]) -> dict[str, Any] | None:
    """Mapea una decisión bloqueante a una candidata de autonomía (categoría ``product``).

    Devuelve ``None`` si la decisión no trae alternativas suficientes o una recomendación válida, en
    cuyo caso no puede resolverse automáticamente y debe escalarse.
    """
    options = decision.get("options") or []
    recommendation = decision.get("recommendation") or ""
    if len(options) < 2 or recommendation not in options:
        return None
    return {
        "category": "product",
        "decision": decision["title"],
        "options": options,
        "chosen": recommendation,
        "reason": decision.get("rationale") or decision.get("question") or decision["title"],
        "confidence": decision.get("confidence") or "low",
        "reversibility": decision.get("reversibility") or "irreversible",
        "blocking": True,
    }


def _codebase_signals(assessment: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume un project assessment como señales acotadas y decision-relevant para el ProductOwnerAgent.

    Surface solo lo que cambia la dirección a recomendar (stack, presencia de tests/seguridad/cobertura,
    deuda, riesgos y gaps, notas de arquitectura) y descarta el ruido de implementación (módulos,
    endpoints por archivo, historia de commits) para no inflar el prompt.
    """
    summary = assessment.get("summary") or {}

    def titles(category: str) -> list[str]:
        return [finding["title"] for finding in findings if finding.get("category") == category][
            :ASSESSMENT_SIGNAL_LIMIT
        ]

    return {
        "assessmentId": assessment.get("id"),
        "stack": summary.get("stack", []),
        "hasTests": summary.get("hasTests"),
        "hasCoverage": summary.get("hasCoverage"),
        "hasSecurityTooling": summary.get("hasSecurityTooling"),
        "totalEndpoints": summary.get("totalEndpoints"),
        "debtMarkers": summary.get("debtMarkers"),
        "riskCount": summary.get("riskCount"),
        "gapCount": summary.get("gapCount"),
        "risks": titles("risk"),
        "gaps": titles("gap"),
        "architectureNotes": titles("architecture"),
    }


class ProductOwnerAgent:
    """Lógica pura del ProductOwnerAgent: arma el prompt, valida la salida y calcula la completitud.

    No accede a la base de datos ni a runtimes: traduce idea/assessment a instrucciones, valida el JSON
    estricto del runtime y deriva completitud y decisiones bloqueantes de forma determinista.
    """

    def contract(self) -> dict[str, Any]:
        """Devuelve el contrato (esquemas I/O, tools y runtimes) del ProductOwnerAgent."""
        return product_owner_agent_contract()

    def _assessment_context(self, *, idea: str, assessment: dict[str, Any]) -> dict[str, Any]:
        return {
            "idea": _bounded_text(idea),
            "codebaseSignals": assessment.get("projectAssessment"),
            "existingInitiative": assessment.get("initiative"),
            "existingBrief": assessment.get("brief"),
            "existingOpenQuestions": [
                _bounded_text(question.get("question"))
                for question in (assessment.get("openQuestions") or [])[:PROMPT_COLLECTION_LIMIT]
            ],
            "existingUnresolvedDecisions": [
                _bounded_text(decision.get("title"))
                for decision in (assessment.get("unresolvedDecisions") or [])[:PROMPT_COLLECTION_LIMIT]
            ],
        }

    def model_messages(self, *, idea: str, assessment: dict[str, Any]) -> list[dict[str, str]]:
        """Arma los mensajes system/user para el runtime de modelo, exigiendo solo JSON del esquema."""
        return [
            {"role": "system", "content": self._system_instruction()},
            {
                "role": "user",
                "content": json_dumps(
                    redact_secrets(self._assessment_context(idea=idea, assessment=assessment))
                ),
            },
        ]

    def cli_prompt(self, *, idea: str, assessment: dict[str, Any]) -> str:
        """Arma el prompt de una sola pieza para un runtime CLI real, exigiendo solo JSON del esquema."""
        context = json_dumps(redact_secrets(self._assessment_context(idea=idea, assessment=assessment)))
        return f"{self._system_instruction()}\n\nInput context (JSON):\n{context}\n"

    def _system_instruction(self) -> str:
        return (
            "You are ProductOwnerAgent. Analyze the supplied idea or existing assessment and return ONLY "
            "valid JSON, without markdown fences, matching this schema: "
            + json_dumps(self.contract()["outputSchema"])
            + " Rules: a user story represents user value (asA/iWant/soThat) and is never duplicated per "
            "technical role; record any decision that must be made before building as a blockingDecision with "
            "status open; do not invent acceptance criteria without a story; each generated story must include "
            "at least one acceptance criterion. Every question is an impact-ranked object with category (one of "
            "scope/users/data/integration/compliance/nonfunctional/ux/risk/delivery), question, whyItMatters, "
            "blocking (boolean), options (>=2 strings), recommendation (one of options), defaultDecision (one of "
            "options) and confidence (low/medium/high); do not ask about facts already present in the assessment."
        )

    def validate_output(self, payload: Any) -> dict[str, Any]:
        """Valida la salida del runtime contra el esquema estricto y la normaliza.

        Raises:
            ProductOwnerOutputValidationError: si falta un campo requerido, un tipo no coincide o un
                enum es inválido (incluye historias sin criterios de aceptación).
        """
        if not isinstance(payload, dict):
            raise ProductOwnerOutputValidationError("ProductOwnerAgent output must be a JSON object.")
        missing = [field for field in self.contract()["outputSchema"]["required"] if field not in payload]
        if missing:
            raise ProductOwnerOutputValidationError(
                "ProductOwnerAgent output is missing required fields: " + ", ".join(missing)
            )
        return {
            "questions": self._validate_questions(payload["questions"]),
            "assumptions": self._validate_assumptions(payload["assumptions"]),
            "blockingDecisions": self._validate_blocking_decisions(payload["blockingDecisions"]),
            "brief": self._validate_brief(payload["brief"]),
            "epics": self._validate_epics(payload["epics"]),
            "modelCompleteness": payload["completeness"] if isinstance(payload["completeness"], dict) else {},
        }

    def _validate_questions(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("questions must be a list.")
        try:
            return [validate_impact_question(item, index=index) for index, item in enumerate(value)]
        except ImpactQuestionValidationError as error:
            raise ProductOwnerOutputValidationError(str(error)) from error

    def _validate_assumptions(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("assumptions must be a list.")
        assumptions: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProductOwnerOutputValidationError(f"assumptions[{index}] must be an object.")
            assumptions.append(
                {
                    "statement": _required_text(item, "statement", field=f"assumptions[{index}]"),
                    "confidence": _enum_value(
                        item, "confidence", CONFIDENCES, "medium", field=f"assumptions[{index}]"
                    ),
                    "validation": str(item.get("validation") or "").strip(),
                }
            )
        return assumptions

    def _validate_blocking_decisions(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("blockingDecisions must be a list.")
        decisions: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProductOwnerOutputValidationError(f"blockingDecisions[{index}] must be an object.")
            options_value = item.get("options")
            options = (
                _string_list(options_value, field=f"blockingDecisions[{index}].options")
                if options_value is not None
                else []
            )
            decisions.append(
                {
                    "title": _required_text(item, "title", field=f"blockingDecisions[{index}]"),
                    "question": str(item.get("question") or "").strip(),
                    "rationale": str(item.get("rationale") or "").strip(),
                    "status": _enum_value(
                        item,
                        "status",
                        BLOCKING_DECISION_STATUSES,
                        "open",
                        field=f"blockingDecisions[{index}]",
                    ),
                    "options": options,
                    "recommendation": str(item.get("recommendation") or "").strip(),
                    "reversibility": _enum_value(
                        item,
                        "reversibility",
                        REVERSIBILITIES,
                        "irreversible",
                        field=f"blockingDecisions[{index}]",
                    ),
                    "confidence": _enum_value(
                        item, "confidence", CONFIDENCES, "low", field=f"blockingDecisions[{index}]"
                    ),
                }
            )
        return decisions

    def _validate_brief(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ProductOwnerOutputValidationError("brief must be an object.")
        brief = {
            "title": _required_text(value, "title", field="brief"),
            "summary": str(value.get("summary") or "").strip(),
            "problemStatement": str(value.get("problemStatement") or "").strip(),
            "scope": str(value.get("scope") or "").strip(),
            "outOfScope": str(value.get("outOfScope") or "").strip(),
        }
        for field in REQUIRED_BRIEF_LIST_FIELDS:
            brief[field] = _string_list(value.get(field, []), field=f"brief.{field}")
        return brief

    def _validate_epics(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("epics must be a list.")
        return [self._validate_epic(item, index=index) for index, item in enumerate(value)]

    def _validate_epic(self, item: Any, *, index: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ProductOwnerOutputValidationError(f"epics[{index}] must be an object.")
        stories_value = item.get("stories")
        if not isinstance(stories_value, list):
            raise ProductOwnerOutputValidationError(f"epics[{index}].stories must be a list.")
        return {
            "title": _required_text(item, "title", field=f"epics[{index}]"),
            "description": str(item.get("description") or "").strip(),
            "stories": [
                self._validate_story(story, field=f"epics[{index}].stories[{story_index}]")
                for story_index, story in enumerate(stories_value)
            ],
        }

    def _validate_story(self, item: Any, *, field: str) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ProductOwnerOutputValidationError(f"{field} must be an object.")
        criteria = _string_list(item.get("acceptanceCriteria", []), field=f"{field}.acceptanceCriteria")
        if not criteria:
            raise ProductOwnerOutputValidationError(
                f"{field}.acceptanceCriteria must include at least one item."
            )
        return {
            "title": _required_text(item, "title", field=field),
            "asA": _required_text(item, "asA", field=field),
            "iWant": _required_text(item, "iWant", field=field),
            "soThat": _required_text(item, "soThat", field=field),
            "businessValue": _enum_value(item, "businessValue", BUSINESS_VALUES, "medium", field=field),
            "acceptanceCriteria": criteria,
        }

    def calculate_completeness(
        self, *, brief: dict[str, Any], unresolved_blocking: int, open_questions: int
    ) -> dict[str, Any]:
        """Calcula la completitud autoritativa (0-100) según campos del brief y bloqueos pendientes."""
        total = len(REQUIRED_BRIEF_TEXT_FIELDS) + len(REQUIRED_BRIEF_LIST_FIELDS)
        missing = [field for field in REQUIRED_BRIEF_TEXT_FIELDS if not str(brief.get(field) or "").strip()]
        missing += [field for field in REQUIRED_BRIEF_LIST_FIELDS if not brief.get(field)]
        filled = total - len(missing)
        score = round(filled / total * 100)
        if unresolved_blocking:
            missing.append("unresolvedBlockingDecisions")
            score = min(score, BLOCKED_COMPLETENESS_CAP)
        return {
            "score": score,
            "filledFields": filled,
            "totalFields": total,
            "missing": missing,
            "openQuestions": open_questions,
            "unresolvedBlockingDecisions": unresolved_blocking,
        }


class ProductOwnerAgentRunner:
    """Orquesta el ProductOwnerAgent: prepara entrada, ejecuta el runtime y persiste discovery y backlog."""

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agent = ProductOwnerAgent()
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.discovery = ProductDiscoveryRepository(connection)
        self.backlog = BacklogRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)
        self.projects = ProjectsRepository(connection)

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        """Devuelve el readiness del ProductOwnerAgent según los runtimes CLI/modelo disponibles."""
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return product_owner_agent_readiness(statuses, preferred_runtime=preferred_runtime)

    def _runtime_by_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        if not runtime_id:
            return None
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return next((runtime for runtime in statuses if runtime["id"] == runtime_id), None)

    def _ensure_profile(self, runtime_id: str) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": PRODUCT_OWNER_AGENT_ID,
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": _runtime_mode(runtime_id),
                "permissionProfile": "plan",
                "allowedTools": PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [runtime_id] if runtime_id else [],
                "allowedRuntimes": [runtime_id] if runtime_id else [],
                "allowRemote": runtime_id == "openai_compatible",
                "allowCli": runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES,
                "allowApi": runtime_id in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES,
                "outputSchema": product_owner_agent_contract()["outputSchema"],
            }
        )

    def _workspace(self, *, project_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if workspace["projectId"] != project_id:
            raise ValueError("ProductOwnerAgent workspace does not belong to the project.")
        if workspace["status"] == "archived":
            raise ValueError("ProductOwnerAgent cannot execute against an archived workspace.")
        return workspace

    def _assessment(self, *, project_id: str, idea: str, initiative_id: str | None) -> dict[str, Any]:
        if not initiative_id:
            if not idea.strip():
                raise ValueError("ProductOwnerAgent requires an idea or an existing initiativeId.")
            return {"idea": idea, "initiative": None}
        initiative = self.discovery.get_initiative(initiative_id)
        if initiative["projectId"] != project_id:
            raise ValueError("ProductOwnerAgent initiative does not belong to the project.")
        briefs = self.discovery.list_product_briefs(initiative_id=initiative_id)
        open_questions = [
            question
            for question in self.discovery.list_clarification_questions(initiative_id=initiative_id)
            if question["status"] == "open"
        ]
        unresolved_decisions = [
            decision
            for decision in self.discovery.list_product_decisions(initiative_id=initiative_id)
            if decision["status"] == "proposed" and bool((decision.get("metadata") or {}).get("blocking"))
        ]
        return {
            "idea": idea or initiative["summary"] or initiative["title"],
            "initiative": initiative,
            "brief": briefs[0] if briefs else None,
            "openQuestions": open_questions,
            "unresolvedDecisions": unresolved_decisions,
        }

    def _project_assessment_signals(self, project_id: str) -> dict[str, Any] | None:
        """Carga (o produce, si falta) el último project assessment del proyecto como señales acotadas.

        Garantiza que el ProductOwnerAgent disponga del assessment estático *antes* de preguntarle al
        usuario qué dirección tomar: reutiliza el assessment más reciente si existe y, si no hay
        ninguno, lo produce vía el ``ProjectAssessmentRunner`` (policy-gated). Es best-effort: ante
        cualquier fallo del assessment devuelve ``None`` y el agente continúa sin esa fundamentación.
        """
        assessments = self.projects.list_project_assessments(project_id)
        if not assessments:
            try:
                ProjectAssessmentRunner(self.connection, root=self.root).run(project_id)
            except (KeyError, ValueError, sqlite3.Error):
                return None
            assessments = self.projects.list_project_assessments(project_id)
        if not assessments:
            return None
        latest = assessments[0]
        findings = self.projects.list_project_findings(assessment_id=latest["id"])
        return _codebase_signals(latest, findings)

    def _runtime_output_text(self, result: dict[str, Any]) -> dict[str, Any]:
        artifact_id = result.get("outputArtifactId") or result.get("stdoutArtifactId")
        if not artifact_id:
            raise ProductOwnerOutputValidationError(
                "ProductOwnerAgent runtime execution did not produce an output artifact."
            )
        artifact = self.evidence.get_artifact_by_id(artifact_id)
        content = Path(artifact["path"]).read_text(encoding="utf-8")
        if len(content) > RUNTIME_OUTPUT_LIMIT_CHARS:
            raise ProductOwnerOutputValidationError(
                "ProductOwnerAgent runtime output exceeds the size limit."
            )
        return {"artifactId": artifact_id, "text": content}

    @staticmethod
    def _json_object_from_text(content: str) -> Any:
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as error:
            raise ProductOwnerOutputValidationError(
                "ProductOwnerAgent runtime output is not valid JSON."
            ) from error

    def _execute_model_runtime(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        runtime_id = str(runtime["id"])
        model = payload.get("model")
        if runtime_id == "ollama":
            model = model or next(iter(runtime.get("models") or []), None)
        model_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": runtime_id,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "product_owner_model_call",
                "runtimeId": runtime_id,
                "capability": "chat",
                "input": {"model": model, "messages": messages, "temperature": 0.1},
                "networkRequired": runtime_id == "openai_compatible",
                "secretsRequired": False,
                "approvalGrantId": payload.get("approvalGrantId"),
                "execute": True,
                "timeoutSeconds": 900,
            },
        )
        return _execution_result_from_tool_call(model_eval["toolCall"])

    def _execute_cli_runtime(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
        prompt: str,
    ) -> dict[str, Any]:
        runtime_argv = build_product_owner_agent_argv(
            runtime=runtime,
            workspace_id=workspace["id"],
            workspace_path=workspace["path"],
            prompt=prompt,
            agent_id=PRODUCT_OWNER_AGENT_ID,
            connection=self.connection,
        )
        runtime_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": "shell",
                "command": " ".join(runtime_argv),
                "argv": runtime_argv,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "product_owner_runtime",
                "runtimeId": runtime["id"],
                "capability": "code_edit",
                "execute": True,
                "timeoutSeconds": 900,
            },
        )
        return _execution_result_from_tool_call(runtime_eval["toolCall"])

    def _persist_initiative(
        self, *, project_id: str, assessment: dict[str, Any], brief: dict[str, Any]
    ) -> dict[str, Any]:
        if assessment.get("initiative"):
            return assessment["initiative"]
        return self.discovery.create_initiative(
            {
                "projectId": project_id,
                "title": brief["title"],
                "summary": brief.get("summary") or assessment.get("idea") or "",
                "status": "exploring",
                "owner": PRODUCT_OWNER_AGENT_ID,
            }
        )

    def _route_decisions(
        self, blocking_decisions: list[dict[str, Any]], *, engine: AutonomyEngine
    ) -> dict[str, list[dict[str, Any]]]:
        """Separa las decisiones bloqueantes en resueltas, automáticas (auto) y escaladas (recommend/ask).

        Las ya resueltas pasan directo; el resto se mapea a candidatas de autonomía y se resuelve con el
        motor: solo las acciones ``auto`` cuentan como automáticas; el resto se escala y retiene el backlog.
        """
        resolved: list[dict[str, Any]] = []
        automatic: list[dict[str, Any]] = []
        escalated: list[dict[str, Any]] = []
        for decision in blocking_decisions:
            if decision["status"] == "resolved":
                resolved.append({"decision": decision, "record": None})
                continue
            candidate = _autonomy_candidate(decision)
            if candidate is None:
                escalated.append({"decision": decision, "record": None})
                continue
            resolution = engine.resolve(candidate)
            target = automatic if resolution["action"] == "auto" else escalated
            target.append({"decision": decision, "record": resolution["decision"]})
        return {"resolved": resolved, "automatic": automatic, "escalated": escalated}

    def _create_product_decision(
        self,
        *,
        project_id: str,
        initiative_id: str,
        brief_id: str,
        decision: dict[str, Any],
        task_id: str,
        status: str,
        blocking: bool,
        audit: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self.discovery.create_product_decision(
            {
                "projectId": project_id,
                "initiativeId": initiative_id,
                "briefId": brief_id,
                "title": decision["title"],
                "status": status,
                "context": decision["question"],
                "decision": (audit or {}).get("chosen", ""),
                "rationale": decision["rationale"],
                "decidedBy": PRODUCT_OWNER_AGENT_ID,
                "metadata": {
                    "source": PRODUCT_OWNER_AGENT_ID,
                    "taskId": task_id,
                    "blocking": blocking,
                    "autonomy": audit,
                },
            }
        )

    def _persist_discovery(
        self,
        *,
        project_id: str,
        initiative_id: str,
        output: dict[str, Any],
        task_id: str,
        routing: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        questions = [
            self.discovery.create_clarification_question(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "question": question["question"],
                    "priority": _question_priority(question),
                    "askedBy": PRODUCT_OWNER_AGENT_ID,
                    "metadata": {
                        "source": PRODUCT_OWNER_AGENT_ID,
                        "taskId": task_id,
                        "category": question["category"],
                        "whyItMatters": question["whyItMatters"],
                        "blocking": question["blocking"],
                        "options": question["options"],
                        "recommendation": question["recommendation"],
                        "defaultDecision": question["defaultDecision"],
                        "confidence": question["confidence"],
                    },
                }
            )
            for question in output["questions"]
        ]
        assumptions = [
            self.discovery.create_assumption(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "statement": assumption["statement"],
                    "confidence": assumption["confidence"],
                    "validation": assumption["validation"],
                    "owner": PRODUCT_OWNER_AGENT_ID,
                    "metadata": {"source": PRODUCT_OWNER_AGENT_ID, "taskId": task_id},
                }
            )
            for assumption in output["assumptions"]
        ]
        brief = self.discovery.upsert_product_brief(
            {
                "projectId": project_id,
                "initiativeId": initiative_id,
                "title": output["brief"]["title"],
                "status": "in_review",
                "summary": output["brief"]["summary"],
                "problemStatement": output["brief"]["problemStatement"],
                "goals": output["brief"]["goals"],
                "targetUsers": output["brief"]["targetUsers"],
                "successMetrics": output["brief"]["successMetrics"],
                "scope": output["brief"]["scope"],
                "outOfScope": output["brief"]["outOfScope"],
                "changeSummary": f"ProductOwnerAgent brief for task {task_id}.",
                "authoredBy": PRODUCT_OWNER_AGENT_ID,
            }
        )
        decisions: list[dict[str, Any]] = []
        decisions.extend(
            self._create_product_decision(
                project_id=project_id,
                initiative_id=initiative_id,
                brief_id=brief["id"],
                decision=item["decision"],
                task_id=task_id,
                status="accepted",
                blocking=False,
                audit=item["record"],
            )
            for item in [*routing["resolved"], *routing["automatic"]]
        )
        decisions.extend(
            self._create_product_decision(
                project_id=project_id,
                initiative_id=initiative_id,
                brief_id=brief["id"],
                decision=item["decision"],
                task_id=task_id,
                status="proposed",
                blocking=True,
                audit=item["record"],
            )
            for item in routing["escalated"]
        )
        return {"questions": questions, "assumptions": assumptions, "brief": brief, "decisions": decisions}

    def _persist_backlog(self, *, project_id: str, output: dict[str, Any]) -> list[dict[str, Any]]:
        epics: list[dict[str, Any]] = []
        for epic in output["epics"]:
            epic_record = self.backlog.create_epic(
                {
                    "projectId": project_id,
                    "title": epic["title"],
                    "description": epic["description"],
                    "status": "active",
                    "owner": PRODUCT_OWNER_AGENT_ID,
                }
            )
            stories: list[dict[str, Any]] = []
            for story in epic["stories"]:
                story_record = self.backlog.create_user_story(
                    {
                        "projectId": project_id,
                        "epicId": epic_record["id"],
                        "title": story["title"],
                        "asA": story["asA"],
                        "iWant": story["iWant"],
                        "soThat": story["soThat"],
                        "businessValue": story["businessValue"],
                        "owner": PRODUCT_OWNER_AGENT_ID,
                    }
                )
                criteria = [
                    self.backlog.create_acceptance_criterion(
                        {
                            "projectId": project_id,
                            "storyId": story_record["id"],
                            "criterion": criterion,
                        }
                    )
                    for criterion in story["acceptanceCriteria"]
                ]
                stories.append({"story": story_record, "acceptanceCriteria": criteria})
            epics.append({"epic": epic_record, "stories": stories})
        return epics

    def _write_manifest_artifact(self, *, project_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps(redact_secrets(manifest))
        artifact_file = write_text_artifact(
            root=self.root, artifact_id=artifact_id, suffix=".json", content=content
        )
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="product_owner_manifest",
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={
                "name": "product-owner-agent.json",
                "source": PRODUCT_OWNER_AGENT_ID,
                "mimeType": "application/json",
                "hashAlgorithm": "sha256",
            },
        )

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta el análisis end-to-end y devuelve estado, completitud, brief, backlog y evidencia.

        Crea job y agent run, ejecuta el runtime real si lo hay, valida la salida estricta, calcula la
        completitud, persiste discovery siempre tras validar y backlog solo si no quedan decisiones
        bloqueantes. Falla cerrado (runtime_unavailable/failed_validation) ante cualquier brecha.
        """
        project_id = str(payload["projectId"])
        task_id = str(payload.get("taskId") or "product_owner_agent")
        idea = str(payload.get("idea") or "")
        initiative_id = payload.get("initiativeId")
        threshold = payload.get("completenessThreshold")
        threshold = int(threshold) if isinstance(threshold, (int, float)) else DEFAULT_COMPLETENESS_THRESHOLD
        workspace = self._workspace(project_id=project_id, workspace_id=str(payload["workspaceId"]))
        assessment = self._assessment(project_id=project_id, idea=idea, initiative_id=initiative_id)
        assessment["projectAssessment"] = self._project_assessment_signals(project_id)

        readiness = self.status(preferred_runtime=payload.get("preferredRuntime"))
        runtime = self._runtime_by_id(readiness.get("selectedRuntimeId")) or {
            "id": readiness.get("selectedRuntimeId") or "unresolved",
            "kind": "unknown",
            "executable": False,
            "reason": readiness["reason"],
            "capabilities": [],
        }
        runtime_id = str(runtime.get("id") or "unresolved")
        profile = self._ensure_profile(runtime_id)
        workflow_context = payload.get("workflowContext") or {}
        job = self.jobs.create_job(
            project_id=project_id,
            kind="agent.product_owner",
            status="running",
            workflow_run_id=workflow_context.get("workflowRunId"),
            workflow_step_id=workflow_context.get("workflowStepId"),
            payload={"taskId": task_id, "workspaceId": workspace["id"], "runtime": runtime},
        )["job"]
        agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=redact_secrets({**payload, "workspacePath": workspace["path"]}),
            output_payload={},
            job_id=job["id"],
            workflow_run_id=workflow_context.get("workflowRunId"),
            workflow_step_id=workflow_context.get("workflowStepId"),
            status="running",
        )

        result = self._execute_and_persist(
            payload=payload,
            runtime=runtime,
            workspace=workspace,
            agent_run=agent_run,
            job=job,
            profile=profile,
            readiness=readiness,
            assessment=assessment,
            task_id=task_id,
            threshold=threshold,
        )
        return self._finalize(
            project_id=project_id,
            task_id=task_id,
            workspace=workspace,
            job=job,
            agent_run=agent_run,
            runtime=runtime,
            readiness=readiness,
            workflow_context=workflow_context,
            result=result,
        )

    def _execute_and_persist(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        readiness: dict[str, Any],
        assessment: dict[str, Any],
        task_id: str,
        threshold: int,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": RUNTIME_UNAVAILABLE_STATUS,
            "reason": readiness["reason"],
            "runtimeResult": _runtime_unavailable_result(readiness["reason"]),
            "output": None,
            "completeness": None,
            "initiative": None,
            "discovery": None,
            "backlog": [],
            "outputArtifactId": None,
        }
        if not readiness["executable"]:
            return result

        project_id = str(payload["projectId"])
        broker = ToolBroker(self.connection, artifact_root=self.root)
        runtime_id = str(runtime["id"])
        if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
            try:
                runtime_result = self._execute_cli_runtime(
                    payload=payload,
                    runtime=runtime,
                    workspace=workspace,
                    agent_run=agent_run,
                    job=job,
                    profile=profile,
                    broker=broker,
                    prompt=self.agent.cli_prompt(idea=assessment["idea"], assessment=assessment),
                )
            except RuntimeCommandUnavailableError as error:
                result["status"] = "failed"
                result["reason"] = str(error)
                result["runtimeResult"] = {"status": "failed", "reason": str(error)}
                return result
        else:
            runtime_result = self._execute_model_runtime(
                payload=payload,
                runtime=runtime,
                workspace=workspace,
                agent_run=agent_run,
                job=job,
                profile=profile,
                broker=broker,
                messages=self.agent.model_messages(idea=assessment["idea"], assessment=assessment),
            )
        result["runtimeResult"] = runtime_result
        if runtime_result["status"] != "completed":
            result["status"] = "failed"
            result["reason"] = str(
                runtime_result.get("reason") or "ProductOwnerAgent runtime execution failed."
            )
            return result

        try:
            runtime_output = self._runtime_output_text(runtime_result)
            result["outputArtifactId"] = runtime_output["artifactId"]
            output = self.agent.validate_output(self._json_object_from_text(runtime_output["text"]))
            selection = ImpactQuestionEngine().select(
                output["questions"],
                detected_facts=detected_facts_from_assessment(
                    brief=assessment.get("brief"),
                    existing_questions=assessment.get("openQuestions"),
                ),
            )
        except (ProductOwnerOutputValidationError, ImpactQuestionValidationError) as error:
            result["status"] = FAILED_VALIDATION_STATUS
            result["reason"] = str(error)
            return result
        output["questions"] = selection["turn"]
        output["deferredQuestions"] = selection["deferred"]
        output["suppressedQuestions"] = selection["suppressed"]
        output["questionSelection"] = selection["counts"]

        engine = AutonomyEngine(AutonomyProfile.from_dict(payload.get("autonomy")))
        routing = self._route_decisions(output["blockingDecisions"], engine=engine)
        existing_unresolved = assessment.get("unresolvedDecisions") or []
        unresolved_count = len(routing["escalated"]) + len(existing_unresolved)
        completeness = self.agent.calculate_completeness(
            brief=output["brief"],
            unresolved_blocking=unresolved_count,
            open_questions=len(output["questions"]),
        )
        completeness["threshold"] = threshold
        completeness["meetsThreshold"] = completeness["score"] >= threshold
        output["autonomy"] = {
            "profile": engine.profile.to_dict(),
            "automatic": [item["record"] for item in routing["automatic"]],
            "escalated": [item["decision"]["title"] for item in routing["escalated"]],
            "counts": {
                "automatic": len(routing["automatic"]),
                "escalated": len(routing["escalated"]),
                "resolved": len(routing["resolved"]),
            },
        }
        result["output"] = output
        result["completeness"] = completeness

        initiative = self._persist_initiative(
            project_id=project_id, assessment=assessment, brief=output["brief"]
        )
        result["initiative"] = initiative
        result["discovery"] = self._persist_discovery(
            project_id=project_id,
            initiative_id=initiative["id"],
            output=output,
            task_id=task_id,
            routing=routing,
        )
        if unresolved_count:
            result["status"] = BLOCKED_STATUS
            result["reason"] = (
                f"ProductOwnerAgent withheld backlog generation: {unresolved_count} blocking "
                "decision(s) remain unresolved."
            )
            return result
        result["backlog"] = self._persist_backlog(project_id=project_id, output=output)
        result["status"] = COMPLETED_STATUS
        result["reason"] = "ProductOwnerAgent completed real analysis and generated a validated backlog."
        return result

    def _finalize(
        self,
        *,
        project_id: str,
        task_id: str,
        workspace: dict[str, Any],
        job: dict[str, Any],
        agent_run: dict[str, Any],
        runtime: dict[str, Any],
        readiness: dict[str, Any],
        workflow_context: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        final_status = result["status"]
        runtime_id = str(runtime.get("id") or "unresolved")
        manifest = self._write_manifest_artifact(
            project_id=project_id,
            manifest={
                "status": final_status,
                "reason": result["reason"],
                "completeness": result["completeness"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
                "epicIds": [item["epic"]["id"] for item in result["backlog"]],
                "runtimeResult": result["runtimeResult"],
            },
        )
        artifact_ids = [manifest["id"]]
        if result.get("outputArtifactId"):
            artifact_ids.append(str(result["outputArtifactId"]))
        artifact_records = artifact_records_from_ids(self.evidence, artifact_ids)
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) == agent_run["id"]
        ]
        qa_verdict = {
            COMPLETED_STATUS: "backlog_generated",
            BLOCKED_STATUS: "blocked_pending_decisions",
            RUNTIME_UNAVAILABLE_STATUS: "blocked",
            FAILED_VALIDATION_STATUS: "failed",
        }.get(final_status, "failed")
        runtime_links_required = final_status in {COMPLETED_STATUS, BLOCKED_STATUS}
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=workflow_context.get("workflowRunId"),
            workflow_step_id=workflow_context.get("workflowStepId"),
            agent_id=PRODUCT_OWNER_AGENT_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id=runtime_id,
            task_id=task_id,
            test_plan="Run ProductOwnerAgent through a configured runtime and validate the JSON brief and backlog.",
            acceptance_checklist=[
                "Runtime is executable.",
                "Runtime output validates against the ProductOwnerAgent schema.",
                "Completeness is computed deterministically.",
                "Backlog is generated only when no blocking decisions remain.",
                "Discovery and backlog are persisted only after validation.",
            ],
            test_results=[],
            logs=[redact_secrets({"status": final_status, "reason": result["reason"]})],
            diff_refs=[],
            risk_notes=[
                {
                    "severity": "low" if final_status == COMPLETED_STATUS else "medium",
                    "description": result["reason"],
                    "mitigation": "Resolve blocking decisions or configure a real runtime, then re-run.",
                }
            ],
            artifact_ids=artifact_ids,
            diff_summary={
                "manifestArtifactId": manifest["id"],
                "outputArtifactId": result.get("outputArtifactId"),
            },
            runtime_health={
                "id": runtime_id,
                "status": result["runtimeResult"].get("status"),
                "available": bool(runtime.get("available", runtime.get("executable", False))),
                "executable": bool(runtime.get("executable", False)),
                "reason": result["runtimeResult"].get("reason") or runtime.get("reason"),
            },
            model_calls=[],
            tool_calls=tool_calls,
            policy_decisions=[],
            approvals=self.jobs.list_action_requests(job["id"]),
            artifacts=[artifact_ref(artifact) for artifact in artifact_records],
            hashes=artifact_hashes(artifact_records),
            evidence_source="evidence_collected",
            qa_verdict=qa_verdict,
        )
        for artifact_id in artifact_ids:
            self.evidence.attach_artifact_to_evidence(
                artifact_id=artifact_id, evidence_package_id=evidence["id"]
            )

        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=runtime_links_required,
            require_workflow_run=bool(evidence.get("workflowRunId")),
        )
        if runtime_links_required and contract_errors:
            final_status = FAILED_VALIDATION_STATUS
            result["reason"] = "Evidence package contract is incomplete or unverifiable: " + " ".join(
                contract_errors
            )
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict="failed",
                risk_notes=[
                    {
                        "severity": "high",
                        "description": result["reason"],
                        "mitigation": "Regenerate ProductOwnerAgent evidence with runtime links and artifact hashes.",
                    }
                ],
            )

        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status="completed" if final_status in {COMPLETED_STATUS, BLOCKED_STATUS} else "failed",
            output_payload={
                "status": final_status,
                "reason": result["reason"],
                "completeness": result["completeness"],
                "runtime": runtime,
                "runtimeResult": result["runtimeResult"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
                "epicIds": [item["epic"]["id"] for item in result["backlog"]],
                "evidence_refs": [evidence["id"], *artifact_ids],
                "output": result["output"],
            },
        )
        job = self.jobs.update_job_status(
            job["id"],
            status="completed" if final_status in {COMPLETED_STATUS, BLOCKED_STATUS} else "failed",
            metadata={
                "status": final_status,
                "reason": result["reason"],
                "evidencePackageId": evidence["id"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
            },
        )
        return {
            "status": final_status,
            "reason": result["reason"],
            "productOwnerAgent": readiness,
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "runtime": runtime,
            "runtimeResult": result["runtimeResult"],
            "output": result["output"],
            "completeness": result["completeness"],
            "initiative": result.get("initiative"),
            "questions": (result.get("discovery") or {}).get("questions", []),
            "assumptions": (result.get("discovery") or {}).get("assumptions", []),
            "brief": (result.get("discovery") or {}).get("brief"),
            "blockingDecisions": (result.get("discovery") or {}).get("decisions", []),
            "epics": result["backlog"],
        }
