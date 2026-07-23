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
import logging
import sqlite3
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from local_control_center.agents.runtime_failure_classifier import classify_runtime_failure
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
from local_control_center.shared.time import utc_now
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
    PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES,
    PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS,
    TECHNICAL_STORY_KEYS,
    product_owner_agent_contract,
    product_owner_agent_readiness,
)
from .repository import AgentsRepository
from .runtime_registry import (
    RuntimeCommandUnavailableError,
    build_product_owner_agent_argv,
    isolated_product_owner_codex_environment,
)
from .runtime_selection import (
    RUNTIME_UNAVAILABLE_STATUS,
    is_ollama_runtime,
    runtime_provider_family,
    runtime_unavailable_result,
)
from .runtime_status import RuntimeStatusService
from .tool_broker import ToolBroker

logger = logging.getLogger(__name__)

FAILED_VALIDATION_STATUS = "failed_validation"
COMPLETED_STATUS = "completed"
BLOCKED_STATUS = "blocked"
BRIEF_READY_STATUS = "brief_ready"
NEEDS_INPUT_STATUS = "needs_input"
SCOPE_IS_CLEAR_STATUS = "scope_is_clear"
CONFIDENCES = {"low", "medium", "high"}
BUSINESS_VALUES = {"low", "medium", "high"}
RUNTIME_OUTPUT_STATUSES = {
    NEEDS_INPUT_STATUS,
    "questions_required",
    BRIEF_READY_STATUS,
    "backlog_ready",
    COMPLETED_STATUS,
    BLOCKED_STATUS,
    SCOPE_IS_CLEAR_STATUS,
}
BLOCKING_DECISION_STATUSES = {"open", "proposed", "resolved", "accepted"}
# Motivo accionable por causa clasificada. Reemplaza al returncode opaco en el blocker que ve el
# cliente; la traducción vive en el catálogo i18n, indexada por la misma causa.
_RUNTIME_FAILURE_REASONS = {
    "auth_expired": "The AI runtime session expired: re-authenticate the CLI and retry.",
    "auth_missing": "The AI runtime is not logged in: sign in to the CLI and retry.",
    "quota_exhausted": "The AI runtime ran out of quota.",
    "rate_limited": "The AI runtime is rate limited: wait a moment and retry.",
    "provider_unreachable": "The AI provider could not be reached.",
    "model_not_found": "The configured model is not available on the AI provider.",
}
QUESTION_CONFIDENCE_PRIORITY = {"low": "high", "medium": "medium", "high": "low"}
DEFAULT_COMPLETENESS_THRESHOLD = 70
BLOCKED_COMPLETENESS_CAP = 60
PROMPT_TEXT_LIMIT_CHARS = 8_000
PROMPT_COLLECTION_LIMIT = 20
RUNTIME_OUTPUT_LIMIT_CHARS = 200_000
ASSESSMENT_SIGNAL_LIMIT = 5
PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS = 2
REPAIR_ERROR_LIMIT_CHARS = 2_000
REPAIR_PREVIOUS_OUTPUT_LIMIT_CHARS = 4_000

REQUIRED_BRIEF_TEXT_FIELDS = ["title", "summary", "problemStatement", "scope", "outOfScope"]
REQUIRED_BRIEF_LIST_FIELDS = ["goals", "targetUsers", "successMetrics"]


class ProductOwnerOutputValidationError(ValueError):
    """Se lanza cuando la salida del runtime no cumple el esquema estricto del ProductOwnerAgent."""


def _runtime_mode(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        return "cli"
    return "ollama" if is_ollama_runtime(runtime) else "api"


def _execution_result_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    completed = tool_call.get("status") == "completed"
    return_code = execution_result.get("returnCode")
    timed_out = bool(execution_result.get("timedOut", False))
    blocked = bool(execution_result.get("blocked", False))
    # El sandbox deja stdout/stderr inline en el payload: ahí está la causa real del fallo
    # ("OAuth access token has expired", "You've hit your usage limit"). Sin clasificarla, el
    # cliente solo recibe el returncode y no puede saber qué arreglar.
    failure = classify_runtime_failure(
        runtime_id=str(execution_result.get("runtimeId") or ""),
        return_code=return_code if isinstance(return_code, int) else None,
        stdout=str(execution_result.get("stdout") or ""),
        stderr=str(execution_result.get("stderr") or ""),
    )
    reason = str(execution_result.get("reason") or "").strip()
    if not reason and timed_out:
        reason = "ProductOwnerAgent runtime execution timed out."
    elif not reason and blocked:
        reason = "ProductOwnerAgent runtime execution was blocked."
    elif not reason and failure is not None and failure.cause != "unknown":
        # La causa clasificada reemplaza al returncode incluso cuando el proceso salió con 0:
        # `codex exec` imprime el error de cuota y termina con código de éxito.
        reason = _RUNTIME_FAILURE_REASONS[failure.cause]
        if failure.retry_after:
            reason = f"{reason} Available again at {failure.retry_after}."
    elif not reason and not completed and return_code not in {None, 0, "0"}:
        reason = f"ProductOwnerAgent runtime process exited with return code {return_code}."
    elif not reason and not completed:
        reason = "ProductOwnerAgent runtime execution failed."
    elif not reason:
        reason = str(payload.get("decisionReason") or "").strip()
    classified = failure.cause if failure is not None and failure.cause != "unknown" else None
    return {
        # Una causa clasificada degrada el resultado aunque el proceso haya salido con 0.
        "status": "failed" if (classified is not None or not completed) else "completed",
        "toolCallId": tool_call.get("id"),
        "execution": payload.get("execution"),
        "returnCode": return_code,
        "timedOut": timed_out,
        "blocked": blocked,
        "reason": reason,
        "failureCause": classified,
        "failureEvidence": failure.evidence if failure is not None else "",
        "failureRetryAfter": failure.retry_after if failure is not None else None,
        "outputArtifactId": execution_result.get("outputArtifactId"),
        "stdoutArtifactId": execution_result.get("stdoutArtifactId"),
        "stderrArtifactId": execution_result.get("stderrArtifactId"),
        "evidencePackageId": execution_result.get("evidencePackageId"),
    }


def _bounded_text(value: Any, limit: int = PROMPT_TEXT_LIMIT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _string_list(value: Any, *, field: str) -> list[str]:
    """Normaliza una lista de strings tolerando ``None`` y descartando entradas vacías/no-string.

    ``None`` (campo omitido por el modelo) se trata como lista vacía; una entrada null o en blanco se
    descarta con auditoría en log en vez de rechazar la salida entera (mismo criterio que
    ``_graded_enum_value``: no bloquear por ruido recuperable). Un tipo que no es lista ni ``None``
    —un dict o un número donde se espera una lista— sí es un error de contrato y falla. La obligación
    de que un campo NO quede vacío (p. ej. ``acceptanceCriteria``) se hace cumplir aparte por el caller.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProductOwnerOutputValidationError(f"{field} must be a string list.")
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(items) != len(value):
        logger.warning("%s dropped %d empty or non-string entries.", field, len(value) - len(items))
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


def _graded_enum_value(item: dict[str, Any], key: str, allowed: set[str], default: str, *, field: str) -> str:
    """Coerce un enum *advisory* (confianza, severidad, valor, reversibilidad) a ``default`` si no valida.

    Estos campos gradúan una prioridad, no deciden un flujo: rechazar un brief entero porque el modelo
    escribió "very high" en vez de "high" es la misma clase de bloqueo que ya trababa el loop. El
    default de cada campo es el lado conservador (``irreversible`` escala en vez de auto-decidir), así
    que degradar nunca abre una puerta que el valor original mantenía cerrada.
    """
    raw = item.get(key)
    value = str(raw or default).strip().lower()
    if value in allowed:
        return value
    logger.warning("%s.%s %r is not one of %s; graded down to %r.", field, key, raw, sorted(allowed), default)
    return default


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


def persist_product_owner_backlog(
    backlog: BacklogRepository,
    *,
    project_id: str,
    output: dict[str, Any],
    product_owner_output_id: str | None = None,
    existing_epic: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Materializa épicas, historias y criterios desde un output PO validado.

    Cuando ``existing_epic`` viene (modo expansión de épica), las historias cuyo
    ``epicTitle`` coincide con esa épica se enlazan a su registro existente en vez de
    crear una épica nueva.
    """
    existing: list[dict[str, Any]] = []
    if product_owner_output_id:
        existing = [
            epic
            for epic in backlog.list_epics(project_id)
            if (epic.get("metadata") or {}).get("productOwnerOutputId") == product_owner_output_id
        ]
    if existing:
        return [{"epic": epic, "stories": []} for epic in existing]

    epics_by_title: dict[str, dict[str, Any]] = {}
    persisted: list[dict[str, Any]] = []
    if existing_epic:
        epics_by_title[existing_epic["title"]] = existing_epic
        persisted.append({"epic": existing_epic, "stories": []})
    for epic in output["epics"]:
        if epic["title"] in epics_by_title:
            continue
        epic_record = backlog.create_epic(
            {
                "projectId": project_id,
                "title": epic["title"],
                "description": epic.get("description", ""),
                "status": "active",
                "owner": PRODUCT_OWNER_AGENT_ID,
                "metadata": {
                    "source": PRODUCT_OWNER_AGENT_ID,
                    "productOwnerOutputId": product_owner_output_id,
                },
            }
        )
        epics_by_title[epic["title"]] = epic_record
        persisted.append({"epic": epic_record, "stories": []})

    persisted_by_epic_id = {item["epic"]["id"]: item for item in persisted}
    for story in output["userStories"]:
        epic_record = epics_by_title.get(story["epicTitle"])
        if not epic_record:
            epic_record = backlog.create_epic(
                {
                    "projectId": project_id,
                    "title": story["epicTitle"],
                    "description": "",
                    "status": "active",
                    "owner": PRODUCT_OWNER_AGENT_ID,
                    "metadata": {
                        "source": PRODUCT_OWNER_AGENT_ID,
                        "productOwnerOutputId": product_owner_output_id,
                    },
                }
            )
            epics_by_title[story["epicTitle"]] = epic_record
            persisted_by_epic_id[epic_record["id"]] = {"epic": epic_record, "stories": []}
            persisted.append(persisted_by_epic_id[epic_record["id"]])
        story_record = backlog.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic_record["id"],
                "title": story["title"],
                "asA": story["asA"],
                "iWant": story["iWant"],
                "soThat": story["soThat"],
                "businessValue": story["businessValue"],
                "owner": PRODUCT_OWNER_AGENT_ID,
                "acceptanceCriteria": story["acceptanceCriteria"],
                "acceptanceCriteriaMetadata": {
                    "source": PRODUCT_OWNER_AGENT_ID,
                    "productOwnerOutputId": product_owner_output_id,
                },
                "metadata": {
                    "source": PRODUCT_OWNER_AGENT_ID,
                    "productOwnerOutputId": product_owner_output_id,
                },
            }
        )
        criteria = backlog.list_acceptance_criteria(story_record["id"])
        persisted_by_epic_id[epic_record["id"]]["stories"].append(
            {"story": story_record, "acceptanceCriteria": criteria}
        )
    return persisted


class ProductOwnerAgent:
    """Lógica pura del ProductOwnerAgent: arma el prompt, valida la salida y calcula la completitud.

    No accede a la base de datos ni a runtimes: traduce idea/assessment a instrucciones, valida el JSON
    estricto del runtime y deriva completitud y decisiones bloqueantes de forma determinista.
    """

    def contract(self) -> dict[str, Any]:
        """Devuelve el contrato (esquemas I/O, tools y runtimes) del ProductOwnerAgent."""
        return product_owner_agent_contract()

    def _assessment_context(
        self,
        *,
        idea: str,
        assessment: dict[str, Any],
        epic_expansion: dict[str, Any] | None = None,
        goal_statement: str | None = None,
    ) -> dict[str, Any]:
        context = {
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
            # Lo ya zanjado es el hecho detectado más fuerte que existe. Sin estos dos bloques el
            # prompt queda MENOS informativo después de responder que antes (la pregunta contestada
            # sale de openQuestions y su respuesta no entra por ningún lado), así que el modelo
            # vuelve a preguntar lo mismo turno tras turno y el loop no converge.
            "resolvedFacts": [
                {
                    "question": _bounded_text(fact.get("question")),
                    "answer": _bounded_text(fact.get("answer")),
                    "answeredBy": str(fact.get("answeredBy") or ""),
                }
                for fact in (assessment.get("resolvedFacts") or [])[:PROMPT_COLLECTION_LIMIT]
            ],
            "settledDecisions": [
                {
                    "title": _bounded_text(decision.get("title")),
                    "decision": _bounded_text(decision.get("decision")),
                    "rationale": _bounded_text(decision.get("rationale")),
                }
                for decision in (assessment.get("settledDecisions") or [])[:PROMPT_COLLECTION_LIMIT]
            ],
        }
        if goal_statement:
            context["projectGoal"] = _bounded_text(goal_statement)
        if epic_expansion:
            context["epicExpansion"] = epic_expansion
        return context

    def model_messages(
        self,
        *,
        idea: str,
        assessment: dict[str, Any],
        epic_expansion: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
        goal_statement: str | None = None,
    ) -> list[dict[str, str]]:
        """Arma los mensajes system/user para el runtime de modelo, exigiendo solo JSON del esquema."""
        messages = [
            {"role": "system", "content": self._system_instruction(epic_expansion=bool(epic_expansion))},
            {
                "role": "user",
                "content": json_dumps(
                    redact_secrets(
                        self._assessment_context(
                            idea=idea,
                            assessment=assessment,
                            epic_expansion=epic_expansion,
                            goal_statement=goal_statement,
                        )
                    )
                ),
            },
        ]
        if repair:
            messages.append({"role": "user", "content": self._repair_instruction(repair)})
        return messages

    def cli_prompt(
        self,
        *,
        idea: str,
        assessment: dict[str, Any],
        epic_expansion: dict[str, Any] | None = None,
        repair: dict[str, Any] | None = None,
        goal_statement: str | None = None,
    ) -> str:
        """Arma el prompt de una sola pieza para un runtime CLI real, exigiendo solo JSON del esquema."""
        context = json_dumps(
            redact_secrets(
                self._assessment_context(
                    idea=idea,
                    assessment=assessment,
                    epic_expansion=epic_expansion,
                    goal_statement=goal_statement,
                )
            )
        )
        instruction = self._system_instruction(epic_expansion=bool(epic_expansion))
        prompt = f"{instruction}\n\nInput context (JSON):\n{context}\n"
        if repair:
            prompt += self._repair_instruction(repair)
        return prompt

    def _repair_instruction(self, repair: dict[str, Any]) -> str:
        """Instrucción de reparación: adjunta el error de validación previo y la salida inválida acotada."""
        error = _bounded_text(repair.get("error"), limit=REPAIR_ERROR_LIMIT_CHARS)
        previous = _bounded_text(repair.get("previousOutput"), limit=REPAIR_PREVIOUS_OUTPUT_LIMIT_CHARS)
        return (
            "\n\nYour previous response FAILED strict validation with this error: "
            + error
            + "\nPrevious output was:\n"
            + previous
            + "\nReturn corrected JSON ONLY (no markdown fences), fixing exactly that error "
            "and keeping every other field valid."
        )

    def _system_instruction(self, *, epic_expansion: bool = False) -> str:
        if epic_expansion:
            return (
                "You are ProductOwnerAgent expanding ONE existing epic into additional user stories. "
                "Return ONLY valid JSON, without markdown fences, matching this schema: "
                + json_dumps(self.contract()["outputSchema"])
                + " Rules: the input context includes epicExpansion with epicTitle, epicDescription and "
                "existingStoryTitles. Every userStories[i].epicTitle MUST equal epicExpansion.epicTitle "
                "exactly; do not invent other epics (epics must be an empty list). Do not duplicate any "
                "title from existingStoryTitles. A user story represents user value (asA/iWant/soThat) "
                "and is never a technical task; each story must include at least one acceptance "
                "criterion. Use status backlog_ready when the stories are ready for approval and "
                "needs_input only when the epic lacks the product facts required to derive stories."
            )
        return (
            "You are ProductOwnerAgent. Analyze the supplied idea or existing assessment and return ONLY "
            "valid JSON, without markdown fences, matching this schema: "
            + json_dumps(self.contract()["outputSchema"])
            + " Rules: status is needs_input when the idea lacks product facts needed for a backlog, "
            "scope_is_clear when a direct technical order in an existing project only needs a mini brief/task "
            "scope, brief_ready when the brief can be reviewed, or backlog_ready when stories are ready for "
            "approval. questions_required is accepted only as a legacy alias for needs_input. "
            "A user story represents user value (asA/iWant/soThat) and is never a technical task or duplicated "
            "per technical role; technical implementation work belongs outside userStories. A userStories item "
            "must therefore not carry any of these keys: " + ", ".join(sorted(TECHNICAL_STORY_KEYS)) + ". "
            "Record product "
            "decisions in decisions with status open when a human/AIDO choice is still needed. Do not invent "
            "acceptance criteria without a story; each generated story must include at least one acceptance "
            "criterion. Every question is an impact-ranked object with category (one of "
            "scope/users/data/integration/compliance/nonfunctional/ux/risk/delivery), question, whyItMatters, "
            "blocking (boolean), options (>=2 strings), recommendation (one of options), defaultDecision (one of "
            "options) and confidence (low/medium/high); do not ask about facts already present in the assessment. "
            "resolvedFacts and settledDecisions are already settled: treat them as binding, never re-ask them "
            "in any rewording, and never emit a question or a decision that contradicts them. "
            "recommendation and defaultDecision must repeat one option verbatim, never an index, a paraphrase or a "
            'new value: for options ["Keep Java 17", "Upgrade to Java 21"] a valid defaultDecision is "Keep Java 17".'
        )

    def validate_epic_expansion_output(self, output: dict[str, Any], *, epic_title: str) -> None:
        """Valida que un output de expansión derive historias solo para la épica objetivo.

        Raises:
            ProductOwnerOutputValidationError: si no hay historias o alguna referencia otra épica.
        """
        stories = output.get("userStories") or []
        if not stories:
            raise ProductOwnerOutputValidationError(
                "Epic expansion output must include at least one user story for the target epic."
            )
        for index, story in enumerate(stories):
            story_epic = str(story.get("epicTitle") or "")
            if story_epic != epic_title:
                raise ProductOwnerOutputValidationError(
                    f"userStories[{index}].epicTitle must equal the target epic title "
                    f"({epic_title!r}), got {story_epic!r}."
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
        status = str(payload["status"]).strip()
        if status not in RUNTIME_OUTPUT_STATUSES:
            raise ProductOwnerOutputValidationError(
                f"status must be one of {sorted(RUNTIME_OUTPUT_STATUSES)}."
            )
        brief = self._validate_brief(payload["productBriefPatch"], field="productBriefPatch")
        epics = self._validate_epic_summaries(payload["epics"])
        user_stories = self._validate_user_stories(payload["userStories"], epics=epics)
        if not isinstance(payload["decisions"], list):
            raise ProductOwnerOutputValidationError("decisions must be a list.")
        decisions_payload = [*payload["decisions"]]
        legacy_decisions = payload.get("blockingDecisions")
        if isinstance(legacy_decisions, list):
            decisions_payload.extend(legacy_decisions)
        unique_decisions: list[dict[str, Any]] = []
        seen_decisions: set[tuple[str, str, str]] = set()
        for item in decisions_payload:
            if not isinstance(item, dict):
                unique_decisions.append(item)
                continue
            key = (
                str(item.get("title") or "").strip(),
                str(item.get("question") or "").strip(),
                str(item.get("status") or "").strip(),
            )
            if key in seen_decisions:
                continue
            seen_decisions.add(key)
            unique_decisions.append(item)
        decisions = self._validate_blocking_decisions(unique_decisions)
        return {
            "status": status,
            "summary": _required_text(payload, "summary", field="output"),
            "confidence": _graded_enum_value(payload, "confidence", CONFIDENCES, "medium", field="output"),
            "questions": self._validate_questions(payload["questions"]),
            "assumptions": self._validate_assumptions(payload["assumptions"]),
            "blockingDecisions": decisions,
            "decisions": decisions,
            "brief": brief,
            "productBriefPatch": brief,
            "epics": epics,
            "userStories": user_stories,
            "risks": self._validate_risks(payload["risks"]),
            "recommendedNextAction": _required_text(payload, "recommendedNextAction", field="output"),
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
                    "confidence": _graded_enum_value(
                        item, "confidence", CONFIDENCES, "medium", field=f"assumptions[{index}]"
                    ),
                    "validation": str(item.get("validation") or "").strip(),
                }
            )
        return assumptions

    def _validate_blocking_decisions(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("decisions must be a list.")
        decisions: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProductOwnerOutputValidationError(f"decisions[{index}] must be an object.")
            options_value = item.get("options")
            options = (
                _string_list(options_value, field=f"decisions[{index}].options")
                if options_value is not None
                else []
            )
            decisions.append(
                {
                    "title": _required_text(item, "title", field=f"decisions[{index}]"),
                    "question": str(item.get("question") or "").strip(),
                    "rationale": str(item.get("rationale") or "").strip(),
                    "status": _enum_value(
                        item,
                        "status",
                        BLOCKING_DECISION_STATUSES,
                        "open",
                        field=f"decisions[{index}]",
                    ),
                    "options": options,
                    "recommendation": str(item.get("recommendation") or "").strip(),
                    "category": str(item.get("category") or item.get("type") or "").strip(),
                    "impact": str(
                        item.get("impact") or item.get("risk") or item.get("severity") or ""
                    ).strip(),
                    "requiresResearch": bool(item.get("requiresResearch", False)),
                    "reversibility": _graded_enum_value(
                        item,
                        "reversibility",
                        REVERSIBILITIES,
                        "irreversible",
                        field=f"decisions[{index}]",
                    ),
                    "confidence": _graded_enum_value(
                        item, "confidence", CONFIDENCES, "low", field=f"decisions[{index}]"
                    ),
                }
            )
        return decisions

    def _validate_brief(self, value: Any, *, field: str = "brief") -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ProductOwnerOutputValidationError(f"{field} must be an object.")
        brief = {
            "title": _required_text(value, "title", field=field),
            "summary": str(value.get("summary") or "").strip(),
            "problemStatement": str(value.get("problemStatement") or "").strip(),
            "scope": str(value.get("scope") or "").strip(),
            "outOfScope": str(value.get("outOfScope") or "").strip(),
        }
        for list_field in REQUIRED_BRIEF_LIST_FIELDS:
            brief[list_field] = _string_list(value.get(list_field, []), field=f"{field}.{list_field}")
        return brief

    def _validate_epic_summaries(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("epics must be a list.")
        epics: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProductOwnerOutputValidationError(f"epics[{index}] must be an object.")
            epics.append(
                {
                    "title": _required_text(item, "title", field=f"epics[{index}]"),
                    "description": str(item.get("description") or "").strip(),
                }
            )
        return epics

    def _validate_user_stories(self, value: Any, *, epics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("userStories must be a list.")
        epic_titles = {epic["title"] for epic in epics}
        stories: list[dict[str, Any]] = []
        for index, story in enumerate(value):
            normalized = self._validate_story(story, field=f"userStories[{index}]")
            epic_title = str(story.get("epicTitle") or "").strip() if isinstance(story, dict) else ""
            if not epic_title:
                raise ProductOwnerOutputValidationError(f"userStories[{index}].epicTitle is required.")
            if epic_titles and epic_title not in epic_titles:
                raise ProductOwnerOutputValidationError(
                    f"userStories[{index}].epicTitle must match one of the generated epics."
                )
            stories.append({**normalized, "epicTitle": epic_title})
        return stories

    def _validate_story(self, item: Any, *, field: str) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ProductOwnerOutputValidationError(f"{field} must be an object.")
        forbidden = sorted(key for key in TECHNICAL_STORY_KEYS if key in item)
        if forbidden:
            raise ProductOwnerOutputValidationError(
                f"{field} must be user value, not a technical task; remove {', '.join(forbidden)}."
            )
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
            "businessValue": _graded_enum_value(
                item, "businessValue", BUSINESS_VALUES, "medium", field=field
            ),
            "acceptanceCriteria": criteria,
        }

    def _validate_risks(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ProductOwnerOutputValidationError("risks must be a list.")
        risks: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProductOwnerOutputValidationError(f"risks[{index}] must be an object.")
            risks.append(
                {
                    "severity": _graded_enum_value(
                        item,
                        "severity",
                        {"low", "medium", "high", "critical"},
                        "medium",
                        field=f"risks[{index}]",
                    ),
                    "description": _required_text(item, "description", field=f"risks[{index}]"),
                    "mitigation": str(item.get("mitigation") or "").strip(),
                }
            )
        return risks

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

    def _ensure_profile(self, runtime: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(runtime.get("id") or "")
        ollama_runtime = is_ollama_runtime(runtime)
        provider_family = runtime_provider_family(runtime)
        remote_runtime = provider_family in PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES or str(
            runtime.get("kind") or ""
        ) in {"api", "gateway"}
        return self.agents.upsert_agent_profile(
            {
                "id": PRODUCT_OWNER_AGENT_ID,
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": _runtime_mode(runtime),
                "permissionProfile": "plan",
                "allowedTools": PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [runtime_id] if runtime_id else [],
                "allowedRuntimes": [runtime_id] if runtime_id else [],
                "allowRemote": remote_runtime,
                "allowCli": runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES,
                "allowApi": provider_family in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES or ollama_runtime,
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
        asked_questions = self.discovery.list_clarification_questions(initiative_id=initiative_id)
        open_questions = [question for question in asked_questions if question["status"] == "open"]
        decisions = self.discovery.list_product_decisions(initiative_id=initiative_id)
        unresolved_decisions = [
            decision
            for decision in decisions
            if decision["status"] == "proposed" and bool((decision.get("metadata") or {}).get("blocking"))
        ]
        return {
            "idea": idea or initiative["summary"] or initiative["title"],
            "initiative": initiative,
            "brief": briefs[0] if briefs else None,
            "openQuestions": open_questions,
            # Toda pregunta ya formulada, respondida o no: una respondida es el hecho detectado más
            # fuerte que existe, así que debe suprimir la repregunta igual que una abierta.
            "askedQuestions": asked_questions,
            "unresolvedDecisions": unresolved_decisions,
            "resolvedFacts": self._resolved_facts(asked_questions),
            "settledDecisions": [
                decision for decision in decisions if decision["status"] in {"accepted", "resolved"}
            ],
        }

    def _resolved_facts(self, asked_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Empareja cada pregunta ya cerrada con su respuesta aceptada más reciente.

        El dedup por texto solo suprime una repregunta idéntica; lo que impide reformularla es que el
        modelo vea la respuesta. Una pregunta cerrada sin respuesta aceptada (p. ej. descartada) no
        aporta un hecho y se omite.

        Se acotan las ``PROMPT_COLLECTION_LIMIT`` cerradas más recientes: son justamente los hilos con
        muchas preguntas acumuladas los que sufren este bug, y una consulta de respuestas por pregunta
        serializaría el control plane sin aportar nada (el prompt tampoco lleva más que ese tope).
        """
        facts: list[dict[str, Any]] = []
        closed = [question for question in asked_questions if question["status"] != "open"]
        for question in closed[-PROMPT_COLLECTION_LIMIT:]:
            accepted = [
                answer
                for answer in self.discovery.list_clarification_answers(question["id"])
                if answer["status"] == "accepted"
            ]
            if not accepted:
                continue
            facts.append(
                {
                    "question": question["question"],
                    "answer": accepted[-1]["answer"],
                    "answeredBy": accepted[-1].get("answeredBy") or "",
                }
            )
        return facts

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
        ollama_runtime = is_ollama_runtime(runtime)
        provider_family = runtime_provider_family(runtime)
        if ollama_runtime:
            model = model or next(iter(runtime.get("models") or []), None)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        resource_decision = (
            metadata.get("resourceSelection") if isinstance(metadata.get("resourceSelection"), dict) else None
        )
        model_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": provider_family,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "product_owner_model_call",
                "runtimeId": runtime_id,
                "capability": "chat",
                "input": {
                    "providerId": runtime_id,
                    "model": model,
                    "messages": messages,
                    "temperature": 0.1,
                },
                # Provider transport is broker-owned. ProductOwnerAgent receives no independent
                # network or secret capability beyond that selected adapter invocation.
                "networkRequired": False,
                # Provider credentials are injected by the adapter transport and never enter the prompt.
                "secretsRequired": False,
                "providerTransportRequired": True,
                "workspaceReadRequired": False,
                "approvalGrantId": payload.get("approvalGrantId"),
                "resourceDecisionId": (resource_decision or {}).get("routingDecisionId"),
                "execute": True,
                "timeoutSeconds": PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS,
            },
            trusted_operation="product_owner_model_call",
            trusted_resource_decision=resource_decision,
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
        execution_workspace = workspace
        ephemeral_prompt_workspace: dict[str, Any] | None = None
        if str(runtime.get("id") or "") in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
            ephemeral_prompt_workspace = self.workspaces.allocate_prompt_workspace(
                project_id=str(payload["projectId"]),
                task_id=f"{workspace['taskId']}:product-owner-cli-prompt:{agent_run['id']}",
                agent_id=PRODUCT_OWNER_AGENT_ID,
                source_workspace_id=workspace["id"],
                reason="Isolated prompt-only cwd for ProductOwnerAgent CLI execution.",
                workflow_run_id=agent_run.get("workflowRunId"),
                workflow_step_id=agent_run.get("workflowStepId"),
            )
            execution_workspace = ephemeral_prompt_workspace

        environment_context = (
            isolated_product_owner_codex_environment()
            if str(runtime.get("id") or "") == "codex_cli"
            else nullcontext(None)
        )
        try:
            with environment_context as subprocess_environment:
                runtime_argv = build_product_owner_agent_argv(
                    runtime=runtime,
                    workspace_id=execution_workspace["id"],
                    workspace_path=execution_workspace["path"],
                    prompt=prompt,
                    model=payload.get("model"),
                    agent_id=PRODUCT_OWNER_AGENT_ID,
                    connection=self.connection,
                )
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                resource_decision = (
                    metadata.get("resourceSelection")
                    if isinstance(metadata.get("resourceSelection"), dict)
                    else None
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
                        "workspaceId": execution_workspace["id"],
                        "workspacePath": execution_workspace["path"],
                        "path": execution_workspace["path"],
                        "operation": "product_owner_runtime",
                        "runtimeId": runtime["id"],
                        "model": payload.get("model"),
                        "capability": "chat",
                        "providerTransportRequired": True,
                        "workspaceReadRequired": False,
                        "networkRequired": False,
                        "secretsRequired": False,
                        "execute": True,
                        "timeoutSeconds": PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS,
                        "captureStdoutArtifact": True,
                        "resourceDecisionId": (resource_decision or {}).get("routingDecisionId"),
                    },
                    trusted_operation="product_owner_runtime",
                    trusted_resource_decision=resource_decision,
                    trusted_subprocess_environment=subprocess_environment,
                )
                return _execution_result_from_tool_call(runtime_eval["toolCall"])
        finally:
            if ephemeral_prompt_workspace is not None:
                self.workspaces.archive_workspace(
                    ephemeral_prompt_workspace["id"],
                    reason="ProductOwnerAgent CLI prompt workspace released after execution.",
                )

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
            if decision["status"] in {"accepted", "resolved"}:
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
                "context": decision.get("question") or decision.get("title", ""),
                "decision": (audit or {}).get("chosen")
                or decision.get("decision")
                or decision.get("recommendation", ""),
                "rationale": decision.get("rationale", ""),
                "decidedBy": PRODUCT_OWNER_AGENT_ID,
                "metadata": {
                    "source": PRODUCT_OWNER_AGENT_ID,
                    "taskId": task_id,
                    "blocking": blocking,
                    # Persist the options so the coordinator can surface the decision as an answerable
                    # thread decision by reusing this row instead of re-inserting a duplicate.
                    "options": list(decision.get("options") or []),
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

    def _persist_product_owner_output(
        self,
        *,
        project_id: str,
        initiative_id: str,
        brief_id: str,
        output: dict[str, Any],
        runtime_id: str,
        output_artifact_id: str | None,
        task_id: str,
    ) -> dict[str, Any]:
        return self.discovery.create_product_owner_output(
            {
                "projectId": project_id,
                "initiativeId": initiative_id,
                "briefId": brief_id,
                "status": output["status"],
                "summary": output["summary"],
                "confidence": output["confidence"],
                "questions": output["questions"],
                "assumptions": output["assumptions"],
                "decisions": output["decisions"],
                "productBriefPatch": output["productBriefPatch"],
                "epics": output["epics"],
                "userStories": output["userStories"],
                "risks": output["risks"],
                "recommendedNextAction": output["recommendedNextAction"],
                "runtimeId": runtime_id,
                "outputArtifactId": output_artifact_id or "",
                "metadata": {
                    "source": PRODUCT_OWNER_AGENT_ID,
                    "taskId": task_id,
                    "questionSelection": output.get("questionSelection") or {},
                    "autonomy": output.get("autonomy") or {},
                },
            }
        )

    def _persist_backlog(
        self,
        *,
        project_id: str,
        output: dict[str, Any],
        product_owner_output_id: str | None = None,
        existing_epic: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return persist_product_owner_backlog(
            self.backlog,
            project_id=project_id,
            output=output,
            product_owner_output_id=product_owner_output_id,
            existing_epic=existing_epic,
        )

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
        epic: dict[str, Any] | None = None
        epic_id = str(payload.get("epicId") or "").strip()
        if epic_id:
            epic = self.backlog.get_epic(epic_id)
            if epic["projectId"] != project_id:
                raise KeyError(f"Epic not found in project: {epic_id}")
            if not idea.strip():
                idea = f"Expand the epic {epic['title']!r} into additional user stories."
        workspace = self._workspace(project_id=project_id, workspace_id=str(payload["workspaceId"]))
        assessment = self._assessment(project_id=project_id, idea=idea, initiative_id=initiative_id)
        readiness = self.status(preferred_runtime=payload.get("preferredRuntime"))
        supplied_assessment = payload.get("assessment")
        assessment["projectAssessment"] = (
            redact_secrets(supplied_assessment)
            if isinstance(supplied_assessment, dict) and supplied_assessment
            else self._project_assessment_signals(project_id)
            if readiness["executable"]
            else None
        )
        runtime = self._runtime_by_id(readiness.get("selectedRuntimeId")) or {
            "id": readiness.get("selectedRuntimeId") or "unresolved",
            "kind": "unknown",
            "executable": False,
            "reason": readiness["reason"],
            "capabilities": [],
        }
        profile = self._ensure_profile(runtime)
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
            epic=epic,
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

    def _mint_repair_resource_decision(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Acuña una decisión de ruteo hermana para autorizar un intento de reparación.

        La frontera anti-replay del ToolBroker consume cada decisión de ruteo exactamente una vez;
        el intento de reparación es una invocación adicional legítima del mismo run (misma
        selección de proveedor/modelo/runtime y mismo workflow), así que se registra una decisión
        nueva auditada como reparación en vez de debilitar el invariante de un solo uso.
        """
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        selection = metadata.get("resourceSelection")
        if not isinstance(selection, dict):
            return None
        original_id = str(selection.get("routingDecisionId") or "").strip()
        if not original_id:
            return None
        row = self.connection.execute(
            "SELECT * FROM ai_routing_decisions WHERE id = ?", (original_id,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["id"] = f"ai-routing-{uuid.uuid4()}"
        record["usage_status"] = "not_executed"
        record["actual_cost_usd"] = None
        record["decision_reason"] = f"Repair attempt re-authorization of routing decision {original_id}."
        record["created_at"] = utc_now()
        columns = ", ".join(record.keys())
        placeholders = ", ".join(["?"] * len(record))
        self.connection.execute(
            f"INSERT INTO ai_routing_decisions ({columns}) VALUES ({placeholders})",
            tuple(record.values()),
        )
        return {
            **selection,
            "routingDecisionId": record["id"],
            "usageStatus": "not_executed",
            "decisionReason": record["decision_reason"],
        }

    def _execute_once(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
        assessment: dict[str, Any],
        detected_facts: set[str],
        epic: dict[str, Any] | None,
        epic_expansion: dict[str, Any] | None,
        repair: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Ejecuta el runtime una vez y valida su salida contra el contrato estricto.

        Returns:
            ``{"kind": "ok", output, selection, runtimeResult, outputArtifactId}`` en éxito;
            ``{"kind": "invalid", reason, runtimeResult, outputText, outputArtifactId}`` si la
            salida no valida (candidata a reparación); ``{"kind": "infra_failed", reason,
            runtimeResult}`` si el runtime no completó (la reparación no aplica).
        """
        runtime_id = str(runtime["id"])
        goal_statement = str(payload.get("goalStatement") or "").strip() or None
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
                    prompt=self.agent.cli_prompt(
                        idea=assessment["idea"],
                        assessment=assessment,
                        epic_expansion=epic_expansion,
                        repair=repair,
                        goal_statement=goal_statement,
                    ),
                )
            except RuntimeCommandUnavailableError as error:
                return {
                    "kind": "infra_failed",
                    "reason": str(error),
                    "runtimeResult": {"status": "failed", "reason": str(error)},
                }
        else:
            runtime_result = self._execute_model_runtime(
                payload=payload,
                runtime=runtime,
                workspace=workspace,
                agent_run=agent_run,
                job=job,
                profile=profile,
                broker=broker,
                messages=self.agent.model_messages(
                    idea=assessment["idea"],
                    assessment=assessment,
                    epic_expansion=epic_expansion,
                    repair=repair,
                    goal_statement=goal_statement,
                ),
            )
        if runtime_result["status"] != "completed":
            return {
                "kind": "infra_failed",
                "reason": str(runtime_result.get("reason") or "ProductOwnerAgent runtime execution failed."),
                "runtimeResult": runtime_result,
            }
        try:
            runtime_output = self._runtime_output_text(runtime_result)
        except ProductOwnerOutputValidationError as error:
            return {
                "kind": "invalid",
                "reason": str(error),
                "runtimeResult": runtime_result,
                "outputText": "",
                "outputArtifactId": None,
            }
        try:
            output = self.agent.validate_output(self._json_object_from_text(runtime_output["text"]))
            if epic:
                self.agent.validate_epic_expansion_output(output, epic_title=epic["title"])
            selection = ImpactQuestionEngine().select(output["questions"], detected_facts=detected_facts)
        except (ProductOwnerOutputValidationError, ImpactQuestionValidationError) as error:
            return {
                "kind": "invalid",
                "reason": str(error),
                "runtimeResult": runtime_result,
                "outputText": runtime_output["text"],
                "outputArtifactId": runtime_output["artifactId"],
            }
        return {
            "kind": "ok",
            "output": output,
            "selection": selection,
            "runtimeResult": runtime_result,
            "outputArtifactId": runtime_output["artifactId"],
        }

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
        epic: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": RUNTIME_UNAVAILABLE_STATUS,
            "reason": readiness["reason"],
            "runtimeResult": runtime_unavailable_result(readiness["reason"]),
            "output": None,
            "completeness": None,
            "initiative": None,
            "discovery": None,
            "backlog": [],
            "outputRecord": None,
            "outputArtifactId": None,
        }
        if not readiness["executable"]:
            return result

        project_id = str(payload["projectId"])
        broker = ToolBroker(self.connection, artifact_root=self.root)
        runtime_id = str(runtime["id"])
        epic_expansion = self._epic_expansion_context(epic) if epic else None
        detected_facts = detected_facts_from_assessment(
            brief=assessment.get("brief"),
            existing_questions=assessment.get("askedQuestions") or assessment.get("openQuestions"),
        )
        repair: dict[str, Any] | None = None
        attempt_payload = payload
        attempt_result: dict[str, Any] = {}
        for _attempt in range(PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS):
            attempt_result = self._execute_once(
                payload=attempt_payload,
                runtime=runtime,
                workspace=workspace,
                agent_run=agent_run,
                job=job,
                profile=profile,
                broker=broker,
                assessment=assessment,
                detected_facts=detected_facts,
                epic=epic,
                epic_expansion=epic_expansion,
                repair=repair,
            )
            result["runtimeResult"] = attempt_result["runtimeResult"]
            if attempt_result.get("outputArtifactId"):
                result["outputArtifactId"] = attempt_result["outputArtifactId"]
            if attempt_result["kind"] == "ok":
                break
            if attempt_result["kind"] == "infra_failed":
                result["status"] = "failed"
                result["reason"] = attempt_result["reason"]
                return result
            repair = {
                "error": attempt_result["reason"],
                "previousOutput": attempt_result.get("outputText", ""),
            }
            repair_selection = self._mint_repair_resource_decision(payload)
            if repair_selection is not None:
                attempt_metadata = dict(payload.get("metadata") or {})
                attempt_metadata["resourceSelection"] = repair_selection
                attempt_payload = {**payload, "metadata": attempt_metadata}
        if attempt_result["kind"] != "ok":
            result["status"] = FAILED_VALIDATION_STATUS
            result["reason"] = attempt_result["reason"]
            return result
        output = attempt_result["output"]
        selection = attempt_result["selection"]
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
        output_record = self._persist_product_owner_output(
            project_id=project_id,
            initiative_id=initiative["id"],
            brief_id=result["discovery"]["brief"]["id"],
            output=output,
            runtime_id=runtime_id,
            output_artifact_id=result.get("outputArtifactId"),
            task_id=task_id,
        )
        result["outputRecord"] = output_record
        if output["status"] in {NEEDS_INPUT_STATUS, "questions_required"}:
            result["status"] = BLOCKED_STATUS
            result["reason"] = (
                output["recommendedNextAction"]
                or output["summary"]
                or "ProductOwnerAgent requires product clarification before backlog generation."
            )
            return result
        if unresolved_count:
            result["status"] = BLOCKED_STATUS
            result["reason"] = (
                f"ProductOwnerAgent withheld backlog generation: {unresolved_count} blocking "
                "decision(s) remain unresolved."
            )
            return result
        if (payload.get("metadata") or {}).get("requireBriefApproval"):
            result["status"] = BRIEF_READY_STATUS
            result["reason"] = (
                "ProductOwnerAgent produced a brief awaiting approval before backlog generation."
            )
            return result
        if output["status"] == SCOPE_IS_CLEAR_STATUS:
            result["status"] = BRIEF_READY_STATUS
            result["reason"] = (
                "ProductOwnerAgent produced a mini brief/task scope for a direct technical order."
            )
            return result
        result["backlog"] = self._persist_backlog(
            project_id=project_id,
            output=output,
            product_owner_output_id=output_record["id"],
            existing_epic=epic,
        )
        result["status"] = COMPLETED_STATUS
        result["reason"] = (
            f"ProductOwnerAgent expanded epic {epic['title']!r} with validated user stories."
            if epic
            else "ProductOwnerAgent completed real analysis and generated a validated backlog."
        )
        return result

    def _epic_expansion_context(self, epic: dict[str, Any]) -> dict[str, Any]:
        existing_titles = [
            str(story.get("title") or "")
            for story in self.backlog.list_user_stories(epic_id=epic["id"])[:PROMPT_COLLECTION_LIMIT]
        ]
        return {
            "epicTitle": epic["title"],
            "epicDescription": _bounded_text(str(epic.get("description") or "")),
            "existingStoryTitles": existing_titles,
        }

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
        output = result.get("output") or {}
        output_record = result.get("outputRecord")
        manifest = self._write_manifest_artifact(
            project_id=project_id,
            manifest={
                "status": final_status,
                "reason": result["reason"],
                "completeness": result["completeness"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
                "productOwnerOutputId": (output_record or {}).get("id"),
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
            BRIEF_READY_STATUS: "brief_ready",
            BLOCKED_STATUS: "blocked_pending_decisions",
            RUNTIME_UNAVAILABLE_STATUS: "blocked",
            FAILED_VALIDATION_STATUS: "failed",
        }.get(final_status, "failed")
        runtime_links_required = final_status in {COMPLETED_STATUS, BRIEF_READY_STATUS, BLOCKED_STATUS}
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
            status="completed"
            if final_status in {COMPLETED_STATUS, BRIEF_READY_STATUS, BLOCKED_STATUS}
            else "failed",
            output_payload={
                "status": final_status,
                "reason": result["reason"],
                "completeness": result["completeness"],
                "runtime": runtime,
                "runtimeResult": result["runtimeResult"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
                "productOwnerOutputId": (output_record or {}).get("id"),
                "epicIds": [item["epic"]["id"] for item in result["backlog"]],
                "evidence_refs": [evidence["id"], *artifact_ids],
                "output": result["output"],
            },
        )
        job = self.jobs.update_job_status(
            job["id"],
            status="completed"
            if final_status in {COMPLETED_STATUS, BRIEF_READY_STATUS, BLOCKED_STATUS}
            else "failed",
            metadata={
                "status": final_status,
                "reason": result["reason"],
                "evidencePackageId": evidence["id"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
                "productOwnerOutputId": (output_record or {}).get("id"),
            },
        )
        return {
            "status": final_status,
            "reason": result["reason"],
            "summary": output.get("summary", ""),
            "confidence": output.get("confidence", "low"),
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
            "decisions": output.get("decisions", []),
            "brief": (result.get("discovery") or {}).get("brief"),
            "productBriefPatch": output.get("productBriefPatch") or output.get("brief"),
            "blockingDecisions": (result.get("discovery") or {}).get("decisions", []),
            "userStories": output.get("userStories", []),
            "risks": output.get("risks", []),
            "recommendedNextAction": output.get("recommendedNextAction", ""),
            "productOwnerOutput": output_record,
            "epics": result["backlog"],
        }
