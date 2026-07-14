"""ThreadCoordinator: ejecuta una coordinación real y determinista sobre cada mensaje de usuario.

Cuando el usuario publica un mensaje en un hilo, el coordinator corre el ``IntentClassifier``
determinista (sin proveedores externos por defecto) y, según su ``plan_mode``, **responde** con un
mensaje ``aido_lead`` (intents/riesgo/roles/gates/plan) o **bloquea** levantando una ``decision_request``
con su ``thread_decision`` pendiente. Cada ejecución deja traza: un evento ``coordinator_run`` y un
artifact ``intake_classification`` en el hilo. Todo el paso se confirma en una única
``immediate_transaction`` para que mensaje, evento, artifact y cambio de estado sean atómicos.

Al encolar un Product Loop sella además la decisión de costo vigente del operador (modo de equipo y
force-local) sobre la metadata del run, para que los controles de Settings gobiernen la ejecución
siguiente en vez de solo describirla.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.intent_classifier import (
    IntentClassification,
    IntentClassificationInput,
    IntentClassifier,
)
from local_control_center.product_loop.metadata import RESOURCE_COST_POLICY_METADATA_KEYS
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.team_scheduler.scheduler import MODES, schedule_team
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import (
    HIGH_SIMILARITY_THRESHOLD,
    SIMILARITY_ACTIONS,
    ThreadSimilarityService,
)

# plan_mode values that mean the coordinator must stop and ask the operator before proceeding.
BLOCKING_PLAN_MODES = ("ask", "blocked")
PLAN_ONLY_RESOLUTION_MODES = frozenset({"continue_in_plan_only_mode", "continue_plan_only", "plan_only"})
THREAD_RESEARCH_JOB_KIND = "thread.research.run"
_SUMMARY_LIMIT = 140
# Thread statuses with a live run: a new user message here would spawn a concurrent loop, so
# `post_message` refuses one. The operator adds a note (`add_operator_note`) or cancels first.
ACTIVE_EXECUTION_STATUSES = ("queued", "running")
# Job statuses still worth cancelling when the operator stops a thread's execution.
_CANCELLABLE_JOB_STATUSES = ("queued", "running", "approval_required")
_PUBLIC_MESSAGE_PROTECTED_PRODUCT_LOOP_METADATA_KEYS = frozenset(
    {
        "approvedResourceSelections",
        "retryOfLoopId",
        "retryStage",
        "retryReason",
        "retryQueuedAt",
        "planOnlyOfLoopId",
        "planOnlyStage",
        "planOnlyReason",
        "planOnlyQueuedAt",
        "continueOfLoopId",
        "continueReason",
        "continueQueuedAt",
        "feedbackId",
        "remediationActionId",
        *RESOURCE_COST_POLICY_METADATA_KEYS,
    }
)


class ThreadBusyError(RuntimeError):
    """Raised when a normal message would start a second run on a thread already executing."""


class ThreadCoordinator:
    """Orquesta el ciclo mensaje→clasificación→(respuesta|bloqueo) sobre un hilo real."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        repository: ThreadsRepository | None = None,
        classifier: IntentClassifier | None = None,
        root: str | Path | None = None,
    ):
        self.connection = connection
        self.repository = repository or ThreadsRepository(connection)
        self.classifier = classifier or IntentClassifier()
        self.jobs = JobsRepository(connection)
        self.root = Path(root).resolve(strict=False) if root is not None else None

    @staticmethod
    def _public_message_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        clean = dict(metadata or {})
        for key in _PUBLIC_MESSAGE_PROTECTED_PRODUCT_LOOP_METADATA_KEYS:
            clean.pop(key, None)
        return clean

    def post_message(
        self,
        *,
        thread_id: str,
        content: str,
        author: str = "user",
        project_assessment: dict[str, Any] | None = None,
        git_state: dict[str, Any] | None = None,
        changed_files: list[str] | None = None,
        user_mode: str = "aido_decide",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publica el mensaje, clasifica rápido y encola ejecución real cuando es seguro hacerlo."""
        if not content.strip():
            raise ValueError("Message content is required")
        # Resolve the thread up front so a missing id fails before any write.
        existing_thread = self.repository.get_thread(thread_id)
        # Fail closed on a thread that is already executing: a normal message would queue a second,
        # concurrent product-loop run. The operator must add a note or cancel the current run first.
        if existing_thread["status"] in ACTIVE_EXECUTION_STATUSES:
            raise ThreadBusyError(
                f"Thread {thread_id} is {existing_thread['status']}: add a note or cancel the current "
                "execution before starting a new one."
            )
        message_metadata = self._public_message_metadata(metadata)
        effective_user_mode = str(
            message_metadata.get("userMode") or message_metadata.get("user_mode") or user_mode or "aido_decide"
        )
        # A `mode` equal to a similarity action means the operator already took the deduplication
        # decision in the new-thread intake; re-blocking here would ask the same question twice.
        similarity_resolved = str(message_metadata.get("mode") or "") in SIMILARITY_ACTIONS

        decision_input = IntentClassificationInput(
            prompt=content,
            project_assessment=project_assessment or {},
            changed_files=tuple(changed_files or ()),
            git_state=git_state or {},
            user_mode=effective_user_mode,
        )

        with immediate_transaction(self.connection):
            user_message = self.repository.append_message(
                thread_id=thread_id,
                kind="user",
                author=author,
                content=content,
                metadata=message_metadata or None,
            )
            self._seed_summary(thread_id, content)
            self.repository.record_event(
                thread_id=thread_id,
                type="message_received",
                agent_role="user",
                payload={"messageId": user_message["id"], "author": author},
            )
            similar_candidate = (
                None
                if similarity_resolved
                else self._high_similarity_candidate(
                    thread=existing_thread,
                    source_thread_id=thread_id,
                    content=content,
                )
            )
            if similar_candidate is not None:
                lead_message, decision_record = self._block_for_similarity(
                    thread_id,
                    user_message["id"],
                    similar_candidate,
                    project_id=existing_thread["projectId"],
                )
                self.repository.record_event(
                    thread_id=thread_id,
                    type="similarity_detected",
                    agent_role="aido_lead",
                    payload={
                        "candidateThreadId": similar_candidate["threadId"],
                        "score": similar_candidate["score"],
                        "reason": similar_candidate["reason"],
                    },
                )
                thread = self.repository.set_status(thread_id, "waiting_decision")
                return {
                    "thread": thread,
                    "blocked": True,
                    "run": {
                        "status": "blocked",
                        "jobId": None,
                        "loopId": None,
                        "reason": lead_message["content"],
                    },
                    "messages": [user_message, lead_message],
                    "decision": decision_record,
                    "artifacts": self.repository.list_artifacts(thread_id),
                    "events": self.repository.list_events(thread_id),
                }

            decision = self.classifier.classify(decision_input)
            self.repository.record_event(
                thread_id=thread_id,
                type="classification_completed",
                agent_role="aido_lead",
                payload=decision.to_dict(),
            )
            team_plan = self._team_plan(decision)
            self.repository.record_event(
                thread_id=thread_id,
                type="team_planned",
                agent_role="aido_lead",
                payload=team_plan,
            )
            self.repository.attach_artifact(
                thread_id=thread_id,
                kind="intake_classification",
                title=f"Intake classification ({decision.plan_mode})",
                artifact_id=f"thread-intake-{uuid.uuid4()}",
                payload=decision.to_dict(),
            )

            blocked = decision.plan_mode in BLOCKING_PLAN_MODES
            if blocked:
                lead_message, decision_record = self._block(
                    thread_id,
                    user_message["id"],
                    decision,
                    project_id=existing_thread["projectId"],
                    project_assessment=project_assessment or {},
                )
                self.repository.record_event(
                    thread_id=thread_id,
                    type="decision_required",
                    agent_role="aido_lead",
                    payload={
                        "messageId": lead_message["id"],
                        "reason": decision.questions[0] if decision.questions else decision.plan_mode,
                    },
                )
                thread = self.repository.set_status(thread_id, "waiting_decision")
                run = {"status": "blocked", "jobId": None, "loopId": None, "reason": lead_message["content"]}
            elif "research" in decision.intents:
                decision_record = None
                job = self._queue_research_run(
                    thread=existing_thread,
                    message=user_message,
                    content=content,
                    decision=decision,
                    team_plan=team_plan,
                )
                self.repository.record_event(
                    thread_id=thread_id,
                    type="research_running",
                    agent_role="researcher",
                    payload={
                        "jobId": job["id"],
                        "messageId": user_message["id"],
                        "status": job["status"],
                    },
                )
                thread = self.repository.set_status(thread_id, "queued")
                run = {
                    "status": "queued",
                    "jobId": job["id"],
                    "loopId": None,
                    "reason": "ResearchAgent job queued.",
                }
            else:
                decision_record = None
                job = self._queue_product_loop_run(
                    thread=existing_thread,
                    message=user_message,
                    content=content,
                    decision=decision,
                    team_plan=team_plan,
                    run_metadata={**message_metadata, "userMode": effective_user_mode},
                )
                self.repository.record_event(
                    thread_id=thread_id,
                    type="run_queued",
                    agent_role="aido_lead",
                    payload={
                        "jobId": job["id"],
                        "messageId": user_message["id"],
                        "status": job["status"],
                    },
                )
                thread = self.repository.set_status(thread_id, "queued")
                run = {
                    "status": "queued",
                    "jobId": job["id"],
                    "loopId": None,
                    "reason": "Product Loop job queued.",
                }

        return {
            "thread": thread,
            "blocked": blocked,
            "run": run,
            "messages": [user_message] if not blocked else [user_message, lead_message],
            "decision": decision_record,
            "artifacts": self.repository.list_artifacts(thread_id),
            "events": self.repository.list_events(thread_id),
        }

    def resolve_decision(
        self,
        *,
        thread_id: str,
        decision_id: str,
        resolution: str,
        decided_by: str | None = None,
        defer_followup: bool = False,
    ) -> dict[str, Any]:
        """Resuelve una decisión pendiente y continúa la ejecución del mensaje bloqueado."""
        if not resolution.strip():
            raise ValueError("Decision resolution is required")
        queued_job: dict[str, Any] | None = None
        transaction = (
            nullcontext(self.connection)
            if self.connection.in_transaction
            else immediate_transaction(self.connection)
        )
        with transaction:
            pending_decision = self.repository.get_decision(decision_id)
            if pending_decision["threadId"] != thread_id:
                raise KeyError(f"Decision not found: {decision_id}")
            if pending_decision["status"] != "pending":
                raise ValueError(f"Decision is already {pending_decision['status']}")
            source_message = self._source_message_for_decision(pending_decision)
            decision = self.repository.resolve_decision(
                thread_id=thread_id,
                decision_id=decision_id,
                resolution=resolution,
                decided_by=decided_by,
            )
            self.repository.append_message(
                thread_id=thread_id,
                kind="system_event",
                author=decided_by or "user",
                content=f"Decision resolved: {resolution}",
                metadata={"decisionId": decision_id},
            )
            self.repository.record_event(
                thread_id=thread_id,
                type="decision_resolved",
                payload={"decisionId": decision_id, "resolution": resolution},
            )
            current = self.repository.get_thread(thread_id)
            decision_metadata = (
                pending_decision.get("metadata") if isinstance(pending_decision.get("metadata"), dict) else {}
            )
            similarity_candidate_id = _metadata_text(decision_metadata, "similarityCandidateId", "")
            # La decisión del gate de funcionalidad existente puede quedar con el hilo en 'blocked'
            # (la transición del run pisa 'waiting_decision'); resolverla debe poder reanudar el
            # loop igualmente, o el hilo queda bloqueado para siempre sin salida para el operador.
            functionality_decision = (
                _metadata_text(decision_metadata, "source", "") == "functionality_registry"
            )
            resolution_mode = self._resolution_mode(resolution)
            can_resume_decision = current["status"] in {"waiting_decision", "awaiting_user", "open"} or (
                functionality_decision and current["status"] == "blocked"
            )
            if defer_followup:
                thread = current
            elif (
                can_resume_decision
                and similarity_candidate_id
                and resolution_mode != "create_new_anyway"
            ):
                if resolution_mode not in SIMILARITY_ACTIONS:
                    raise ValueError(f"Unknown similarity action: {resolution}")
                metadata = (
                    pending_decision.get("metadata")
                    if isinstance(pending_decision.get("metadata"), dict)
                    else {}
                )
                ThreadSimilarityService(self.connection).mark_similarity(
                    project_id=current["projectId"],
                    source_thread_id=thread_id,
                    candidate_thread_id=similarity_candidate_id,
                    score=float(metadata.get("similarityScore") or 0.0),
                    reason=str(metadata.get("similarityReason") or "Similarity decision resolved."),
                    action=resolution_mode,
                )
                thread = self.repository.set_status(thread_id, "resolved")
            elif can_resume_decision and source_message is not None:
                forced_decision = self._decision_for_resolution(decision, resolution)
                team_plan = self._team_plan(forced_decision)
                plan_only = resolution_mode in PLAN_ONLY_RESOLUTION_MODES
                source_metadata = (
                    dict(source_message.get("metadata") or {})
                    if isinstance(source_message.get("metadata"), dict)
                    else {}
                )
                run_metadata = {**source_metadata, "userMode": forced_decision.user_mode}
                if functionality_decision:
                    # El rerun debe llevar la elección del usuario o el gate de memoria de
                    # funcionalidad volvería a bloquear el mismo mensaje en un ciclo sin fin.
                    run_metadata["functionalityDecision"] = resolution_mode
                if plan_only:
                    run_metadata["planOnly"] = True
                    run_metadata["planOnlyReason"] = resolution
                queued_job = self._queue_product_loop_run(
                    thread=current,
                    message=source_message,
                    content=source_message["content"],
                    decision=forced_decision,
                    team_plan=team_plan,
                    decision_payload={
                        **forced_decision.to_dict(),
                        "resolution": resolution,
                        "decisionId": decision_id,
                    },
                    run_metadata=run_metadata,
                )
                self.repository.record_event(
                    thread_id=thread_id,
                    type="run_queued",
                    agent_role="aido_lead",
                    payload={
                        "jobId": queued_job["id"],
                        "messageId": source_message["id"],
                        "decisionId": decision_id,
                        "resolution": resolution,
                        "status": queued_job["status"],
                    },
                )
                thread = self.repository.set_status(thread_id, "queued")
            else:
                # Only reopen a thread that was actually waiting on this decision; never override an
                # archived/blocked status set by another flow.
                thread = (
                    self.repository.set_status(thread_id, "open")
                    if current["status"] == "waiting_decision"
                    else current
                )
        result = {"thread": thread, "decision": decision}
        if queued_job is not None:
            result["job"] = queued_job
        return result

    def resolve_decision_in_transaction(
        self,
        *,
        thread_id: str,
        decision_id: str,
        resolution: str,
        decided_by: str | None = None,
        defer_followup: bool = False,
    ) -> dict[str, Any]:
        """Resolve and queue a decision inside the caller's active write transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("resolve_decision_in_transaction requires an active transaction.")
        return self.resolve_decision(
            thread_id=thread_id,
            decision_id=decision_id,
            resolution=resolution,
            decided_by=decided_by,
            defer_followup=defer_followup,
        )

    def add_operator_note(
        self,
        *,
        thread_id: str,
        content: str,
        author: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Adjunta una nota del operador al hilo sin clasificar, sin encolar job ni cambiar el estado.

        Es la escritura segura mientras el hilo ejecuta: deja constancia auditable
        (``operator_note`` + evento ``operator_note_added``) sin arriesgar un loop concurrente.
        """
        if not content.strip():
            raise ValueError("Note content is required")
        # Resolve up front so a missing thread fails before any write.
        thread = self.repository.get_thread(thread_id)
        with immediate_transaction(self.connection):
            note = self.repository.append_message(
                thread_id=thread_id,
                kind="operator_note",
                author=author or "operator",
                content=content,
                metadata=dict(metadata or {}) or None,
            )
            self.repository.record_event(
                thread_id=thread_id,
                type="operator_note_added",
                agent_role="operator",
                payload={"messageId": note["id"], "author": note["author"]},
            )
        return {"thread": thread, "message": note}

    def cancel_execution(
        self,
        *,
        thread_id: str,
        reason: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Cancela los jobs en vuelo del hilo, deja traza y lo reabre en ``open`` (idempotente).

        Cancelar libera el lease y marca el job ``cancelled``. Un job aún ``queued`` nunca se reclama;
        uno ya reclamado por un worker termina su iteración en curso, pero el finalizador del worker
        detecta la cancelación y **no** resucita el estado del hilo (ver ``_thread_job_was_cancelled``).
        Si no hay nada activo que cancelar y el hilo no estaba ejecutando, es un no-op silencioso.
        """
        thread = self.repository.get_thread(thread_id)
        clean_actor = (actor or "operator").strip() or "operator"
        clean_reason = (reason or "Operator cancelled the execution.").strip()
        was_executing = thread["status"] in ACTIVE_EXECUTION_STATUSES
        cancelled_job_ids: list[str] = []
        with immediate_transaction(self.connection):
            for job in self.jobs.list_jobs(project_id=thread["projectId"]):
                payload = job.get("payload")
                if not isinstance(payload, dict) or payload.get("threadId") != thread_id:
                    continue
                if job["status"] not in _CANCELLABLE_JOB_STATUSES:
                    continue
                self.jobs.cancel_job(job["id"], reason=clean_reason, actor=clean_actor)
                cancelled_job_ids.append(job["id"])
            # Only mutate the thread when there was something to stop; a stray click after the run
            # already finished must not append a misleading "cancelled" note or reopen a closed thread.
            if cancelled_job_ids or was_executing:
                self.repository.append_message(
                    thread_id=thread_id,
                    kind="system_event",
                    author=clean_actor,
                    content=f"Execution cancelled by operator: {clean_reason}",
                    metadata={"cancelledJobIds": cancelled_job_ids},
                )
                self.repository.record_event(
                    thread_id=thread_id,
                    type="execution_cancelled",
                    agent_role="operator",
                    payload={"cancelledJobIds": cancelled_job_ids, "reason": clean_reason},
                )
                thread = self.repository.set_status(thread_id, "open")
        return {"thread": thread, "cancelledJobIds": cancelled_job_ids}

    # -- internals -------------------------------------------------------------
    def _high_similarity_candidate(
        self,
        *,
        thread: dict[str, Any],
        source_thread_id: str,
        content: str,
    ) -> dict[str, Any] | None:
        candidates = ThreadSimilarityService(self.connection).find_similar(
            project_id=thread["projectId"],
            query=content,
            source_thread_id=source_thread_id,
            include_deleted=False,
            limit=1,
        )
        if not candidates:
            return None
        candidate = candidates[0]
        return candidate if float(candidate["score"]) >= HIGH_SIMILARITY_THRESHOLD else None

    def _block_for_similarity(
        self,
        thread_id: str,
        message_id: str,
        candidate: dict[str, Any],
        *,
        project_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        prompt = (
            f"Esto parece relacionado con {candidate['title']}. "
            "¿Quieres continuar ese hilo, mejorar lo existente o crear hilo nuevo?"
        )
        metadata = {
            "sourceMessageId": message_id,
            "similarityCandidateId": candidate["threadId"],
            "similarityScore": candidate["score"],
            "similarityReason": candidate["reason"],
            "similarityStatus": candidate["status"],
        }
        request_message = self.repository.append_message(
            thread_id=thread_id,
            kind="decision_request",
            author="aido_lead",
            content=prompt,
            metadata=metadata,
        )
        decision_record = self.repository.create_decision(
            thread_id=thread_id,
            message_id=request_message["id"],
            title="Similar thread detected",
            prompt=prompt,
            options=list(SIMILARITY_ACTIONS),
            metadata=metadata,
        )
        BlockerRemediationService(self.connection, root=self.root).create_for_blocked_run(
            project_id=project_id,
            thread_id=thread_id,
            loop_id="",
            stage="thread_similarity",
            reason=prompt,
            details={
                "decisionId": decision_record["id"],
                "candidateThreadId": candidate["threadId"],
                "candidateTitle": candidate["title"],
                "score": candidate["score"],
                "reason": candidate["reason"],
                "options": list(SIMILARITY_ACTIONS),
            },
        )
        return request_message, decision_record

    def _block(
        self,
        thread_id: str,
        message_id: str,
        decision: IntentClassification,
        *,
        project_id: str,
        project_assessment: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        questions = list(decision.questions) or ["AIDO needs more detail before it can proceed safely."]
        prompt = " ".join(questions)
        options = self._decision_options(decision)
        request_metadata = {**decision.to_dict(), "sourceMessageId": message_id}
        request_message = self.repository.append_message(
            thread_id=thread_id,
            kind="decision_request",
            author="aido_lead",
            content=prompt,
            metadata=request_metadata,
        )
        decision_record = self.repository.create_decision(
            thread_id=thread_id,
            message_id=request_message["id"],
            title=f"Decision needed ({decision.plan_mode})",
            prompt=prompt,
            options=options,
            metadata=request_metadata,
        )
        remediation_service = BlockerRemediationService(self.connection, root=self.root)
        remediation_service.create_for_blocked_run(
            project_id=project_id,
            thread_id=thread_id,
            loop_id="",
            stage="thread_intake",
            reason=prompt,
            details={
                "decisionId": decision_record["id"],
                "planMode": decision.plan_mode,
                "prompt": prompt,
                "options": options,
                "sourceMessageId": message_id,
            },
        )
        if "runtime_configuration" in decision.required_gates:
            remediation_service.create_for_blocked_run(
                project_id=project_id,
                thread_id=thread_id,
                loop_id="",
                stage="runtime",
                reason=prompt,
                details=self._runtime_blocker_details(
                    project_assessment=project_assessment or {},
                    decision=decision,
                    decision_id=decision_record["id"],
                    prompt=prompt,
                    options=options,
                    source_message_id=message_id,
                ),
            )
        return request_message, decision_record

    @staticmethod
    def _resolution_mode(resolution: str) -> str:
        return resolution.strip().lower().replace(" ", "_").replace("-", "_") or "implementation"

    @staticmethod
    def _decision_options(decision: IntentClassification) -> list[str]:
        if decision.plan_mode == "blocked":
            return ["Continue in plan-only mode"]
        return ["Diagnosis", "Implementation", "Research"]

    @staticmethod
    def _runtime_blocker_details(
        *,
        project_assessment: dict[str, Any],
        decision: IntentClassification,
        decision_id: str,
        prompt: str,
        options: list[str],
        source_message_id: str,
    ) -> dict[str, Any]:
        runtime_status = project_assessment.get("runtimeStatus")
        if not isinstance(runtime_status, dict):
            runtime_status = {}
        raw_providers = runtime_status.get("providers")
        providers = [
            dict(provider)
            for provider in raw_providers
            if isinstance(provider, dict)
        ] if isinstance(raw_providers, list) else []
        selected_provider = next(
            (
                provider
                for provider in providers
                if not bool(provider.get("executable") or provider.get("canEditWorkspace"))
            ),
            providers[0] if providers else {},
        )
        runtime_id = (
            runtime_status.get("selectedRuntimeId")
            or runtime_status.get("activeRuntimeId")
            or selected_provider.get("id")
            or selected_provider.get("runtimeId")
        )
        details: dict[str, Any] = {
            "status": "configuration_required",
            "executable": False,
            "decisionId": decision_id,
            "planMode": decision.plan_mode,
            "requiredGates": list(decision.required_gates),
            "prompt": prompt,
            "options": list(options),
            "sourceMessageId": source_message_id,
        }
        if providers:
            details["providers"] = providers
        if runtime_status:
            details["runtimeStatus"] = runtime_status
        if runtime_id:
            details["runtimeId"] = str(runtime_id)
            details["providerId"] = str(runtime_id)
        return details

    def _seed_summary(self, thread_id: str, content: str) -> None:
        thread = self.repository.get_thread(thread_id)
        if thread["summary"]:
            return
        # Redact before persisting: the summary is surfaced in list_threads and the overview snapshot.
        snippet = redact_secrets(content.strip().replace("\n", " "))[:_SUMMARY_LIMIT]
        self.connection.execute("UPDATE project_threads SET summary = ? WHERE id = ?", (snippet, thread_id))

    def _with_operator_cost_decision(self, *, project_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Sella la decisión de costo vigente del operador sobre la metadata del run.

        ``project.loop.teamMode`` (economy/balanced/critical/maximum) es un *default*: una metadata
        de mensaje con un modo válido lo respeta, porque elegir el modo por ejecución es legítimo.
        ``project.routing.forceLocal`` NO es un default sino un control de privacidad: cuando está
        activo fuerza ``privacyLevel=local_private`` por encima de cualquier metadata, de modo que
        un campo por mensaje jamás pueda relajar una restricción que el operador dejó puesta.
        Sellarlos aquí deja la decisión en el payload del job, auditable junto al run que la usó.
        """
        stamped = dict(metadata)
        requested_mode = str(stamped.get("teamMode") or stamped.get("team_mode") or "").strip().lower()
        if requested_mode not in MODES:
            resolved_mode = resolve_setting_value(
                connection=self.connection,
                key="project.loop.teamMode",
                project_id=project_id,
            )
            if resolved_mode in MODES:
                stamped["teamMode"] = resolved_mode
        if resolve_setting_value(
            connection=self.connection,
            key="project.routing.forceLocal",
            project_id=project_id,
        ):
            stamped["privacyLevel"] = "local_private"
        return stamped

    def _queue_product_loop_run(
        self,
        *,
        thread: dict[str, Any],
        message: dict[str, Any],
        content: str,
        decision: IntentClassification,
        team_plan: dict[str, Any],
        decision_payload: dict[str, Any] | None = None,
        run_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_run_metadata = self._with_operator_cost_decision(
            project_id=thread["projectId"],
            metadata=redact_secrets(run_metadata or {}),
        )
        plan_only = bool(clean_run_metadata.get("planOnly") or clean_run_metadata.get("plan_only"))
        payload = {
            "threadId": thread["id"],
            "messageId": message["id"],
            "projectId": thread["projectId"],
            "message": content,
            "title": thread["title"],
            "root": str(self.root) if self.root is not None else None,
            "decision": decision_payload or decision.to_dict(),
            "teamPlan": team_plan,
            "runMetadata": clean_run_metadata,
        }
        if plan_only:
            payload["planOnly"] = True
        created = self.jobs.create_job(
            project_id=thread["projectId"],
            kind="thread.product_loop.run",
            payload=payload,
            idempotency_key=f"thread-message:{message['id']}",
        )
        return created["job"]

    def _queue_research_run(
        self,
        *,
        thread: dict[str, Any],
        message: dict[str, Any],
        content: str,
        decision: IntentClassification,
        team_plan: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "threadId": thread["id"],
            "messageId": message["id"],
            "projectId": thread["projectId"],
            "workspaceId": thread["ownerId"] if thread["ownerType"] == "workspace" else None,
            "taskId": f"thread-research-{message['id']}",
            "query": content,
            "maxSources": 5,
            "sources": [],
            "conclusions": [],
            "claims": [],
            "technicalDecisions": [],
            "root": str(self.root) if self.root is not None else None,
            "decision": decision.to_dict(),
            "teamPlan": team_plan,
            "metadata": {
                "threadId": thread["id"],
                "messageId": message["id"],
                "allowWebSearch": True,
                "researchPolicy": {
                    "webSearchAllowed": True,
                    "preferredSourceTypes": ["official_documentation"],
                },
            },
        }
        created = self.jobs.create_job(
            project_id=thread["projectId"],
            kind=THREAD_RESEARCH_JOB_KIND,
            payload=payload,
            idempotency_key=f"thread-research-message:{message['id']}",
        )
        return created["job"]

    def _source_message_for_decision(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        source_message_id = str(metadata.get("sourceMessageId") or "").strip()
        if source_message_id:
            return self.repository.get_message(source_message_id)
        request_message_id = str(decision.get("messageId") or "").strip()
        if request_message_id:
            request_message = self.repository.get_message(request_message_id)
            request_metadata = (
                request_message.get("metadata") if isinstance(request_message.get("metadata"), dict) else {}
            )
            request_source_id = str(request_metadata.get("sourceMessageId") or "").strip()
            if request_source_id:
                return self.repository.get_message(request_source_id)
            request_sequence = int(request_message["sequence"])
            previous_user_messages = [
                message
                for message in self.repository.list_messages(decision["threadId"])
                if message["kind"] == "user" and int(message["sequence"]) < request_sequence
            ]
            return previous_user_messages[-1] if previous_user_messages else None
        return None

    @staticmethod
    def _decision_for_resolution(decision: dict[str, Any], resolution: str) -> IntentClassification:
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        resolution_mode = ThreadCoordinator._resolution_mode(resolution)
        return IntentClassification(
            intents=_metadata_list(metadata, "intents", ["feature"]),
            risk=_metadata_text(metadata, "risk", "low"),
            required_roles=_metadata_list(metadata, "requiredRoles", ["product_owner", "technical_lead"]),
            required_gates=_metadata_list(metadata, "requiredGates", ["implementation_plan"]),
            suggested_branch_name=_metadata_text(metadata, "suggestedBranchName", "codex/thread-decision"),
            plan_mode="execute",
            confidence=max(float(metadata.get("confidence") or 0.0), 0.95),
            questions=[],
            user_mode=resolution_mode,
        )

    @staticmethod
    def _team_plan(decision: IntentClassification) -> dict[str, Any]:
        scope = _scope_for_decision(decision)
        mode = "critical" if decision.risk in {"high", "critical"} else "balanced"
        try:
            return schedule_team(scope=scope, risk=decision.risk, mode=mode)
        except ValueError:
            return {
                "schedulerVersion": None,
                "mode": mode,
                "risk": decision.risk,
                "scope": scope,
                "roles": [{"role": role} for role in decision.required_roles],
                "summary": {
                    "roles": list(decision.required_roles),
                    "roleCount": len(decision.required_roles),
                },
            }


def _scope_for_decision(decision: IntentClassification) -> list[str]:
    scopes: list[str] = []
    role_scope = {
        "frontend_engineer": "frontend",
        "backend_engineer": "backend",
        "database_engineer": "database",
        "software_architect": "architecture",
        "security_reviewer": "security",
        "pentester": "security",
        "researcher": "research",
        "qa_reviewer": "tests",
    }
    intent_scope = {
        "security": "security",
        "research": "research",
        "migration": "database",
        "architecture": "architecture",
        "tests": "tests",
        "docs": "docs",
        "refactor": "backend",
        "bugfix": "backend",
        "feature": "backend",
    }
    for role in decision.required_roles:
        scope = role_scope.get(role)
        if scope and scope not in scopes:
            scopes.append(scope)
    for intent in decision.intents:
        scope = intent_scope.get(intent)
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes or ["backend"]


def _metadata_list(metadata: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return list(default)
    items = [str(item) for item in value if str(item).strip()]
    return items or list(default)


def _metadata_text(metadata: dict[str, Any], key: str, default: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if isinstance(value, str) and value.strip() else default
