"""Resolve blocked/configuration-required states into concrete user remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.payloads import (
    PAYLOAD_BUILDERS,
    build_blocker_payload_context,
    resource_policy_summary,
)
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository, is_ollama_runtime_id
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import SIMILARITY_ACTIONS

# Remediation actions that can discard or overwrite local work; execute() refuses them until the
# caller explicitly confirms, and their persisted records are flagged ``confirmationRequired``.
DESTRUCTIVE_REMEDIATION_ACTION_TYPES = frozenset({"checkout_branch"})
PRODUCT_LOOP_DEPENDENT_REMEDIATION_ACTION_TYPES = frozenset(
    {"approve_resource_decision", "continue_plan_only", "retry_loop"}
)
LOCAL_WORKER_RECOVERABLE_STAGES = frozenset({"gitleaks", "research", "runtime", "worker"})


class BlockerRemediationService:
    """Maps blocker evidence to persisted, user-executable remediation actions."""

    def __init__(self, connection: sqlite3.Connection, *, root: str | Path | None = None):
        self.connection = connection
        self.root = Path(root).resolve(strict=False) if root is not None else None
        self.repository = RemediationActionsRepository(connection)

    def create_for_blocked_run(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        loop_id: str | None,
        stage: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Persist actions for a Product Loop ``_block_run`` result."""
        clean_details = redact_secrets(details or {})
        blocker_type = self._blocker_type(stage=stage, reason=reason, details=clean_details)
        specs = self._action_specs(
            blocker_type,
            reason=reason,
            details=clean_details,
            project_id=project_id,
        )
        if not str(loop_id or "").strip():
            specs = [
                spec
                for spec in specs
                if spec["actionType"] not in PRODUCT_LOOP_DEPENDENT_REMEDIATION_ACTION_TYPES
            ]
            if stage in LOCAL_WORKER_RECOVERABLE_STAGES and not any(
                spec["actionType"] == "run_worker_once" for spec in specs
            ):
                specs.append(
                    {
                        "actionType": "run_worker_once",
                        "title": "Run worker once",
                        "description": "Run one bounded worker batch after resolving this local worker blocker.",
                        "payload": {},
                    }
                )
        return [
            self.repository.create_action(
                project_id=project_id,
                thread_id=thread_id,
                loop_id=loop_id,
                stage=stage,
                blocker_type=blocker_type,
                title=spec["title"],
                description=spec["description"],
                action_type=spec["actionType"],
                technical_reason=reason,
                primary=bool(spec.get("primary", index == 0)),
                destructive=self._spec_is_destructive(spec),
                confirmation_required=self._spec_is_destructive(spec),
                payload={
                    "projectId": project_id,
                    "threadId": thread_id,
                    "loopId": loop_id,
                    "stage": stage,
                    "blockerType": blocker_type,
                    "reason": reason,
                    **spec.get("payload", {}),
                    "details": clean_details,
                },
            )
            for index, spec in enumerate(specs)
        ]

    @staticmethod
    def _spec_is_destructive(spec: dict[str, Any]) -> bool:
        """Treat remediations as destructive when they can discard or overwrite local work."""
        return bool(spec.get("destructive", spec["actionType"] in DESTRUCTIVE_REMEDIATION_ACTION_TYPES))

    def ensure_worker_remediation(
        self,
        *,
        thread_id: str,
        worker_status: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Persist ``run_worker_once`` when a queued thread has no active worker processing it."""
        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        if thread["status"] != "queued" or bool(worker_status.get("running")):
            return self.repository.list_for_thread(thread_id)
        self.repository.create_action(
            project_id=thread["projectId"],
            thread_id=thread_id,
            loop_id="",
            stage="worker",
            blocker_type="worker_not_running",
            title="Run worker once",
            description="This thread is queued, but the local worker is not running.",
            action_type="run_worker_once",
            technical_reason="worker_not_running",
            primary=True,
            destructive=False,
            confirmation_required=False,
            payload={
                "projectId": thread["projectId"],
                "threadId": thread_id,
                "workerStatus": redact_secrets(worker_status),
            },
        )
        return self.repository.list_for_thread(thread_id)

    def list_for_thread(
        self,
        *,
        thread_id: str,
        worker_status: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """List remediations, materializing worker recovery and blocked-thread backfill on demand."""
        self.repository.resolve_pending_from_terminal_loops(thread_id)
        if worker_status is not None:
            actions = self.ensure_worker_remediation(thread_id=thread_id, worker_status=worker_status)
        else:
            actions = self.repository.list_for_thread(thread_id)
        actions = self.ensure_awaiting_user_remediations(thread_id=thread_id, existing=actions)
        if any(action.get("status") == "pending" for action in actions):
            return actions
        return self.ensure_blocked_thread_remediation(thread_id=thread_id, existing=actions)

    def ensure_awaiting_user_remediations(
        self,
        *,
        thread_id: str,
        existing: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Backfill one actionable remediation per still-pending ProductOwner decision."""
        actions = existing if existing is not None else self.repository.list_for_thread(thread_id)
        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        loop = self._awaiting_user_loop_for_thread(
            thread_id=thread_id,
            project_id=thread["projectId"],
        )
        if loop is None:
            return actions

        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        product_owner = durable.get("productOwner") if isinstance(durable.get("productOwner"), dict) else {}
        pending_decisions = product_owner.get("pendingThreadDecisions")
        if not isinstance(pending_decisions, list):
            return actions
        existing_decision_ids = {
            str((action.get("payload") or {}).get("decisionId") or "").strip()
            for action in actions
            if action.get("loopId") == loop["id"] and action.get("actionType") == "answer_question"
        }
        threads = ThreadsRepository(self.connection)
        missing_decisions: list[dict[str, Any]] = []
        for item in pending_decisions:
            if not isinstance(item, dict):
                continue
            decision_id = str(item.get("decisionId") or "").strip()
            options = [str(option).strip() for option in item.get("options") or [] if str(option).strip()]
            if not decision_id or decision_id in existing_decision_ids or len(options) < 2:
                continue
            with suppress(KeyError):
                decision = threads.get_decision(decision_id)
                if decision["threadId"] == thread_id and decision["status"] == "pending":
                    missing_decisions.append({**item, "options": options})
        if not missing_decisions:
            return actions

        with suppress(Exception):
            self.create_for_blocked_run(
                project_id=thread["projectId"],
                thread_id=thread_id,
                loop_id=loop["id"],
                stage="product_owner",
                reason=str(product_owner.get("reason") or "Product decisions are required."),
                details={
                    **product_owner,
                    "status": "needs_input",
                    "pendingDecisions": missing_decisions,
                },
            )
        return self.repository.list_for_thread(thread_id)

    def _awaiting_user_loop_for_thread(
        self,
        *,
        thread_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        for loop in coordinator.list_loops(project_id):
            if str(loop.get("state") or "") != "awaiting_user":
                continue
            durable = dict((loop.get("context") or {}).get("durableRun") or {})
            thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
            if str(thread_ref.get("projectThreadId") or "") == thread_id:
                return loop
        return None

    def ensure_blocked_thread_remediation(
        self,
        *,
        thread_id: str,
        existing: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Backfill: un hilo bloqueado sin acciones pendientes siempre recibe una salida ejecutable.

        Cubre bloqueos previos a la creación automática de remediaciones (o cuyas filas se
        perdieron): reconstruye las tarjetas específicas del blocker desde el estado durable del
        loop y garantiza un ``retry_loop`` real, para que el operador nunca quede frente a un
        bloqueo sin acción posible.
        """
        actions = existing if existing is not None else self.repository.list_for_thread(thread_id)
        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        if thread["status"] not in {"blocked", "waiting_decision"}:
            return actions
        loop = self._blocked_loop_for_thread(
            thread_id=thread_id, project_id=thread["projectId"]
        ) or self._fallback_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
        if loop is None:
            return actions
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        stage = str(durable.get("blockedStage") or "").strip() or "worker"
        reason = str(
            durable.get("blockedReason")
            or "The Product Loop is blocked and its stored remediation actions are missing."
        )
        details = self._backfill_details_from_durable(durable)
        blocker_type = self._blocker_type(stage=stage, reason=reason, details=details)
        with suppress(Exception):
            self.create_for_blocked_run(
                project_id=thread["projectId"],
                thread_id=thread_id,
                loop_id=loop["id"],
                stage=stage,
                reason=reason,
                details=details,
            )
        with suppress(Exception):
            self.repository.create_action(
                project_id=thread["projectId"],
                thread_id=thread_id,
                loop_id=loop["id"],
                stage=stage,
                blocker_type=blocker_type,
                title="Retry loop",
                description="Queue a real retry of the blocked run from its original message.",
                action_type="retry_loop",
                technical_reason=reason,
                payload={
                    "projectId": thread["projectId"],
                    "threadId": thread_id,
                    "loopId": loop["id"],
                    "stage": stage,
                    "reason": reason,
                    "backfilled": True,
                },
            )
        return self.repository.list_for_thread(thread_id)

    def _blocked_loop_for_thread(self, *, thread_id: str, project_id: str) -> dict[str, Any] | None:
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        for loop in coordinator.list_loops(project_id):
            if str(loop.get("state") or "") != "blocked":
                continue
            durable = dict((loop.get("context") or {}).get("durableRun") or {})
            thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
            if str(thread_ref.get("projectThreadId") or "") == thread_id:
                return loop
        return None

    def _fallback_loop_for_thread(self, *, thread_id: str, project_id: str) -> dict[str, Any] | None:
        """Loop más reciente del hilo cuando ninguno está en estado exactamente ``"blocked"``.

        Cubre la divergencia hilo-``blocked``/loop-en-otro-estado (cascada de reintentos + worker
        detenido): prioriza el loop más reciente cuyo durable registró un ``blockedReason`` (fue
        bloqueado alguna vez) y, si ninguno lo tiene, cae al loop más reciente del hilo. Orden
        determinista por ``createdAt``/``id`` para no depender de un orden inestable.
        """
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        candidates: list[dict[str, Any]] = []
        for loop in coordinator.list_loops(project_id):
            durable = dict((loop.get("context") or {}).get("durableRun") or {})
            thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
            if str(thread_ref.get("projectThreadId") or "") == thread_id:
                candidates.append(loop)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (str(item.get("createdAt") or ""), str(item.get("id") or "")))
        blocked_once = [
            loop
            for loop in candidates
            if str(((loop.get("context") or {}).get("durableRun") or {}).get("blockedReason") or "").strip()
        ]
        return (blocked_once or candidates)[-1]

    @staticmethod
    def _backfill_details_from_durable(durable: dict[str, Any]) -> dict[str, Any]:
        functionality = (
            durable.get("existingFunctionality")
            if isinstance(durable.get("existingFunctionality"), dict)
            else {}
        )
        return {
            "decisionId": durable.get("decisionId"),
            "functionalityId": functionality.get("id"),
            "sourceThreadId": functionality.get("sourceThreadId"),
            "score": functionality.get("score"),
            "options": list(SIMILARITY_ACTIONS),
        }

    def dismiss(self, action_id: str) -> dict[str, Any]:
        """Dismiss one pending remediation action."""
        return self.repository.mark_status(action_id, "dismissed")

    def _mark_thread_run_queued_best_effort(
        self,
        *,
        threads: ThreadsRepository,
        thread_id: str,
        event_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        status_update: dict[str, Any] = {"status": "queued"}
        try:
            thread = threads.set_status(thread_id, "queued")
        except Exception as error:
            thread = threads.get_thread(thread_id)
            status_update = {
                "status": "failed",
                "reason": str(redact_secrets(str(error))),
                "threadId": thread_id,
            }
        event_update: dict[str, Any] = {"status": "recorded"}
        try:
            threads.record_event(
                thread_id=thread_id,
                type="run_queued",
                agent_role="aido_lead",
                payload=event_payload,
            )
        except Exception as error:
            event_update = {
                "status": "failed",
                "reason": str(redact_secrets(str(error))),
                "threadId": thread_id,
            }
            with suppress(Exception):
                threads.record_event(
                    thread_id=thread_id,
                    type="run_queue_event_failed",
                    agent_role="aido_lead",
                    payload={
                        **event_payload,
                        "reason": event_update["reason"],
                    },
                )
        update_summary = {"status": status_update["status"], "thread": status_update, "event": event_update}
        if reason := status_update.get("reason") or event_update.get("reason"):
            update_summary["reason"] = reason
        return thread, update_summary

    @staticmethod
    def _requires_confirmation(action: dict[str, Any]) -> bool:
        """Destructive remediations must be confirmed before ``execute`` runs their side effect."""
        return bool(
            action.get("confirmationRequired")
            or action.get("destructive")
            or action.get("actionType") in DESTRUCTIVE_REMEDIATION_ACTION_TYPES
        )

    @staticmethod
    def _confirmation_acknowledged(payload: dict[str, Any]) -> bool:
        """Return true only when the caller explicitly confirmed a destructive action."""
        return any(bool(payload.get(key)) for key in ("confirmed", "confirm", "confirmationAcknowledged"))

    def execute(
        self,
        action_id: str,
        *,
        platform: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a supported remediation action and resolve it only on a successful side effect."""
        action = self.repository.get(action_id)
        if action["status"] != "pending":
            return {
                "remediation": action,
                "execution": {
                    "status": "blocked",
                    "action": action["actionType"],
                    "remediationStatus": action["status"],
                    "reason": "Only pending remediation actions can be executed.",
                },
            }
        execution_payload = {**action["payload"], **redact_secrets(payload or {})}
        action_type = action["actionType"]
        if self._requires_confirmation(action) and not self._confirmation_acknowledged(payload or {}):
            execution = {
                "status": "blocked",
                "action": action_type,
                "confirmationRequired": True,
                "reason": "This action can discard local work; re-run it with an explicit confirmation.",
            }
            return {"remediation": self.repository.get(action_id), "execution": redact_secrets(execution)}
        if action_type == "open_settings_section":
            execution = {
                "status": "completed",
                "action": action_type,
                "section": execution_payload.get("section")
                or execution_payload.get("settingsSection")
                or "runtime",
            }
        elif action_type == "validate_runtime":
            project_id = (
                str(execution_payload.get("projectId") or action.get("projectId") or "").strip() or None
            )
            providers = RuntimeStatusService(self.connection).list_provider_statuses(project_id=project_id)
            execution = self._validate_runtime_execution(
                runtime_id=str(execution_payload.get("runtimeId") or "").strip(),
                providers=providers,
            )
        elif action_type == "switch_runtime":
            runtime_id = str(execution_payload.get("runtimeId") or "").strip()
            if not runtime_id:
                execution = {
                    "status": "blocked",
                    "action": action_type,
                    "reason": "runtimeId is required to switch runtime.",
                }
            else:
                project_id = (
                    str(execution_payload.get("projectId") or action.get("projectId") or "").strip() or None
                )
                providers = RuntimeStatusService(self.connection).list_provider_statuses(
                    project_id=project_id
                )
                target = self._runtime_status_for(providers, runtime_id)
                if target is None:
                    execution = {
                        "status": "blocked",
                        "action": action_type,
                        "runtimeId": runtime_id,
                        "providers": redact_secrets(providers),
                        "reason": f"Runtime {runtime_id} is not available in runtime status.",
                    }
                elif target.get("executable") is not True:
                    execution = {
                        "status": "blocked",
                        "action": action_type,
                        "runtimeId": runtime_id,
                        "target": redact_secrets(target),
                        "providers": redact_secrets(providers),
                        "reason": str(
                            target.get("reason")
                            or f"Runtime {runtime_id} is not executable and cannot be selected."
                        ),
                    }
                else:
                    preferences = self._set_default_runtime(str(target.get("id") or runtime_id))
                    execution = {
                        "status": "completed",
                        "action": action_type,
                        "runtimeId": str(target.get("id") or runtime_id),
                        "target": redact_secrets(target),
                        "preferences": preferences,
                        "reason": "Default runtime preference updated to an executable runtime.",
                    }
        elif action_type == "run_worker_once":
            worker = getattr(platform, "local_worker_runtime", None)
            if worker is None:
                execution = {
                    "status": "blocked",
                    "action": action_type,
                    "reason": "Local worker runtime is unavailable.",
                }
            else:
                job_retry = self._retry_failed_worker_job_if_needed(payload=execution_payload)
                if job_retry["status"] == "blocked":
                    execution = job_retry
                elif job_retry["status"] == "completed":
                    execution = {
                        "status": "completed",
                        "action": action_type,
                        "reason": "Referenced worker job is already completed.",
                        "jobRetry": job_retry,
                    }
                else:
                    execution = {
                        **redact_secrets(worker.run_once()),
                        "action": action_type,
                    }
                    if job_retry["status"] != "not_applicable":
                        execution["jobRetry"] = job_retry
        elif action_type == "check_network_access":
            execution = self._check_network_access()
        elif action_type in {
            "git_init",
            "add_remote",
            "create_branch",
            "checkout_branch",
            "view_diff",
            "run_gitleaks",
        }:
            execution = self._execute_git_action(
                action_type, action=action, payload=execution_payload, platform=platform
            )
        elif action_type == "save_patch":
            execution = self._save_patch(action=action, payload=execution_payload, platform=platform)
        elif action_type == "retry_loop":
            execution = self._retry_loop(action=action)
        elif action_type == "answer_question":
            execution = self._answer_question(action=action, payload=execution_payload)
        elif action_type == "approve_resource_decision":
            execution = self._approve_resource_decision(action=action, payload=execution_payload)
        elif action_type == "continue_plan_only":
            execution = self._continue_plan_only(action=action)
        else:
            execution = {
                "status": "blocked",
                "action": action_type,
                "reason": "Unsupported remediation action.",
            }

        if self._should_resolve(action_type=action_type, execution=execution):
            current_action = self.repository.get(action_id)
            remediation = (
                current_action
                if current_action["status"] == "resolved"
                else self.repository.mark_status(action_id, "resolved")
            )
        else:
            remediation = self.repository.get(action_id)
        return {"remediation": remediation, "execution": redact_secrets(execution)}

    def resolve_thread_decision(
        self,
        *,
        thread_id: str,
        decision_id: str,
        resolution: str,
        decided_by: str | None,
        platform: Any,
    ) -> dict[str, Any]:
        """Resolve a thread decision through its durable remediation when one exists."""
        threads = ThreadsRepository(self.connection)
        decision = threads.get_decision(decision_id)
        if decision["threadId"] != thread_id:
            raise KeyError(f"Decision not found: {decision_id}")
        matching_actions = [
            action
            for action in self.list_for_thread(thread_id=thread_id)
            if action.get("actionType") == "answer_question"
            and str((action.get("payload") or {}).get("decisionId") or "").strip() == decision_id
        ]
        pending_actions = [action for action in matching_actions if action.get("status") == "pending"]
        if pending_actions:
            outcome = self.execute(
                pending_actions[-1]["id"],
                platform=platform,
                payload={
                    "threadId": thread_id,
                    "decisionId": decision_id,
                    "resolution": resolution,
                    "decidedBy": decided_by or "user",
                },
            )
            execution = outcome["execution"]
            if execution.get("status") != "completed":
                raise ValueError(str(execution.get("reason") or "Decision resolution was blocked."))
            result = {
                "thread": execution["thread"],
                "decision": execution["decision"],
            }
            if isinstance(execution.get("job"), dict):
                result["job"] = execution["job"]
            return result
        dismissed_actions = [action for action in matching_actions if action.get("status") == "dismissed"]
        if dismissed_actions:
            execution = self._answer_question(
                action=dismissed_actions[-1],
                payload={
                    "threadId": thread_id,
                    "decisionId": decision_id,
                    "resolution": resolution,
                    "decidedBy": decided_by or "user",
                },
                allow_dismissed=True,
            )
            if execution.get("status") != "completed":
                raise ValueError(str(execution.get("reason") or "Decision resolution was blocked."))
            result = {
                "thread": execution["thread"],
                "decision": execution["decision"],
            }
            if isinstance(execution.get("job"), dict):
                result["job"] = execution["job"]
            return result
        if matching_actions:
            raise ValueError("Decision remediation is no longer pending.")
        thread = threads.get_thread(thread_id)
        if self._decision_requires_product_loop_remediation(
            thread_id=thread_id,
            project_id=thread["projectId"],
            decision_id=decision_id,
        ):
            raise ValueError(
                "Product Loop decision remediation is unavailable; resolution is blocked to avoid "
                "starting an incomplete decision batch."
            )

        from local_control_center.threads.coordinator import ThreadCoordinator

        return ThreadCoordinator(self.connection, root=self.root).resolve_decision(
            thread_id=thread_id,
            decision_id=decision_id,
            resolution=resolution,
            decided_by=decided_by,
        )

    def _decision_requires_product_loop_remediation(
        self,
        *,
        thread_id: str,
        project_id: str,
        decision_id: str,
    ) -> bool:
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        for loop in ProductLoopCoordinator(self.connection, root=self.root).list_loops(project_id):
            durable = dict((loop.get("context") or {}).get("durableRun") or {})
            thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
            if str(thread_ref.get("projectThreadId") or "") != thread_id:
                continue
            product_owner = (
                durable.get("productOwner") if isinstance(durable.get("productOwner"), dict) else {}
            )
            if loop.get("state") == "awaiting_user" and any(
                isinstance(item, dict) and item.get("decisionId") == decision_id
                for item in product_owner.get("pendingThreadDecisions") or []
            ):
                return True
            if (
                loop.get("state") == "blocked"
                and durable.get("blockedStage") == "functionality_memory"
                and durable.get("decisionId") == decision_id
            ):
                return True
        return False

    def _should_resolve(self, *, action_type: str, execution: dict[str, Any]) -> bool:
        if execution.get("status") not in {"completed", "queued", "awaiting_approval"}:
            return False
        return action_type in {
            "validate_runtime",
            "switch_runtime",
            "continue_plan_only",
            "git_init",
            "add_remote",
            "create_branch",
            "checkout_branch",
            "run_gitleaks",
            "run_worker_once",
            "check_network_access",
            "answer_question",
            "approve_resource_decision",
            "retry_loop",
        }

    def _retry_failed_worker_job_if_needed(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        job_id = str(payload.get("jobId") or details.get("jobId") or "").strip()
        if not job_id:
            return {"status": "not_applicable"}
        jobs = JobsRepository(self.connection)
        try:
            job = jobs.get_job(job_id)
        except KeyError:
            return {
                "status": "blocked",
                "action": "run_worker_once",
                "jobId": job_id,
                "reason": "Worker remediation references a job that no longer exists.",
            }
        if job["status"] == "failed":
            return {
                "status": "queued",
                **jobs.retry_job(
                    job_id,
                    reason="Retrying failed thread worker job from remediation.",
                    actor="remediation",
                ),
            }
        if job["status"] == "queued":
            return {"status": "queued", "job": job}
        if job["status"] == "completed":
            return {"status": "completed", "job": job}
        return {
            "status": "blocked",
            "action": "run_worker_once",
            "jobId": job_id,
            "jobStatus": job["status"],
            "reason": f"Worker remediation cannot run job {job_id} while it is {job['status']}.",
        }

    @staticmethod
    def _check_network_access() -> dict[str, Any]:
        """Probe the same public HTTPS endpoint ResearchAgent uses for web-search discovery."""
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        endpoint = "https://duckduckgo.com/html/"
        request = Request(endpoint, headers={"User-Agent": "AIDO-ResearchAgent/1.0"})
        try:
            with urlopen(request, timeout=5) as response:
                return {
                    "status": "completed",
                    "action": "check_network_access",
                    "endpoint": endpoint,
                    "httpStatus": int(response.status),
                    "reason": "Research web-search endpoint is reachable.",
                }
        except HTTPError as error:
            return {
                "status": "blocked",
                "action": "check_network_access",
                "endpoint": endpoint,
                "httpStatus": int(error.code),
                "reason": f"Research web-search endpoint returned HTTP {int(error.code)}.",
            }
        except (TimeoutError, URLError, OSError) as error:
            return {
                "status": "blocked",
                "action": "check_network_access",
                "endpoint": endpoint,
                "reason": f"Research web-search endpoint is unreachable: {error}",
            }

    def _validate_runtime_execution(
        self, *, runtime_id: str, providers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if runtime_id:
            target = self._runtime_status_for(providers, runtime_id)
            if target is None:
                return {
                    "status": "blocked",
                    "action": "validate_runtime",
                    "runtimeId": runtime_id,
                    "providers": redact_secrets(providers),
                    "reason": f"Runtime {runtime_id} is not available in runtime status.",
                }
            executable = target.get("executable") is True
            return {
                "status": "completed" if executable else "blocked",
                "action": "validate_runtime",
                "runtimeId": str(target.get("id") or runtime_id),
                "target": redact_secrets(target),
                "providers": redact_secrets(providers),
                "reason": "Target runtime is executable."
                if executable
                else str(target.get("reason") or f"Runtime {runtime_id} is not executable."),
            }
        executable_provider = next(
            (provider for provider in providers if provider.get("executable") is True), None
        )
        return {
            "status": "completed" if executable_provider is not None else "blocked",
            "action": "validate_runtime",
            "runtimeId": str((executable_provider or {}).get("id") or ""),
            "target": redact_secrets(executable_provider) if executable_provider is not None else None,
            "providers": redact_secrets(providers),
            "reason": "At least one runtime is executable."
            if executable_provider is not None
            else "No executable runtime is currently available.",
        }

    @staticmethod
    def _runtime_status_for(providers: list[dict[str, Any]], runtime_id: str) -> dict[str, Any] | None:
        normalized = str(runtime_id or "").strip()
        if not normalized:
            return None
        for provider in providers:
            provider_id = str(provider.get("id") or "").strip()
            if provider_id == normalized:
                return provider
            if is_ollama_runtime_id(normalized) and is_ollama_runtime_id(provider_id):
                return provider
        return None

    @staticmethod
    def _approved_resource_selections_from_durable(durable: dict[str, Any]) -> list[dict[str, Any]]:
        resource_approval = (
            durable.get("resourceApproval") if isinstance(durable.get("resourceApproval"), dict) else {}
        )
        approvals = resource_approval.get("approvedResourceSelections")
        if resource_approval.get("status") != "approved" or not isinstance(approvals, list):
            return []
        return [approval for approval in approvals if isinstance(approval, dict)]

    def _execute_git_action(
        self,
        action_type: str,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
        platform: Any,
    ) -> dict[str, Any]:
        def with_action(result: dict[str, Any]) -> dict[str, Any]:
            return {"action": action_type, **result}

        project_id = str(payload.get("projectId") or action["projectId"])
        service = GitWorkspaceService(self.connection, root=Path(getattr(platform, "cwd", self.root or ".")))
        if action_type == "git_init":
            return with_action(service.init_repository(project_id))
        if action_type == "add_remote":
            name = str(payload.get("name") or payload.get("remoteName") or "").strip()
            url = str(payload.get("url") or payload.get("remoteUrl") or "").strip()
            if not name or not url:
                return {
                    "status": "blocked",
                    "action": action_type,
                    "reason": "name and url are required to add a Git remote.",
                }
            return with_action(service.add_remote(project_id, name=name, url=url))
        if action_type == "create_branch":
            branch = str(
                payload.get("branchName") or payload.get("branch") or f"codex/remediation-{action['id'][-8:]}"
            )
            return with_action(service.create_branch(project_id, name=branch, base=payload.get("base")))
        if action_type == "checkout_branch":
            branch = str(payload.get("branchName") or payload.get("branch") or "")
            if not branch:
                return {"status": "blocked", "reason": "branchName is required for checkout_branch."}
            return with_action(
                service.checkout(
                    project_id, branch=branch, allow_dirty=bool(payload.get("allowDirty", False))
                )
            )
        if action_type == "view_diff":
            return with_action(service.diff(project_id))
        if action_type == "run_gitleaks":
            recovery = self._blocked_gitleaks_context(action=action, payload=payload)
            scan_kwargs: dict[str, Any] = {}
            if recovery is not None:
                durable = recovery["durable"]
                workspace_id = str(durable.get("workspaceId") or "").strip()
                workspace_path = str(durable.get("workspacePath") or "").strip()
                if workspace_id:
                    scan_kwargs["workspace_id"] = workspace_id
                if workspace_path:
                    scan_kwargs["workspace_path"] = workspace_path
            try:
                try:
                    result = (
                        service.gitleaks_scan(project_id, **scan_kwargs)
                        if scan_kwargs
                        else service.gitleaks_scan(project_id)
                    )
                except TypeError as error:
                    if "unexpected keyword" not in str(error):
                        raise
                    result = service.gitleaks_scan(project_id)
            except Exception as error:
                return {
                    "status": "blocked",
                    "action": action_type,
                    "projectId": project_id,
                    "reason": f"gitleaks remediation failed to run: {redact_secrets(str(error))}",
                }
            if recovery is None or result.get("status") != "completed" or bool(result.get("deliveryBlocked")):
                return with_action(result)
            return self._retry_gitleaks_approval(
                action=action,
                coordinator=recovery["coordinator"],
                loop=recovery["loop"],
                durable=recovery["durable"],
                thread_id=recovery["threadId"],
                threads=recovery["threads"],
                gitleaks=result,
            )
        return {"status": "blocked", "reason": f"Unsupported git remediation: {action_type}."}

    def _save_patch(
        self, *, action: dict[str, Any], payload: dict[str, Any], platform: Any
    ) -> dict[str, Any]:
        project_id = str(payload.get("projectId") or action["projectId"])
        root = Path(getattr(platform, "cwd", self.root or "."))
        diff = GitWorkspaceService(
            self.connection,
            root=root,
        ).diff(project_id)
        if diff.get("status") != "completed":
            return diff
        patch = str(diff.get("diff") or "")
        if not patch.strip():
            return {
                "status": "blocked",
                "action": "save_patch",
                "projectId": project_id,
                "changedFiles": diff.get("changedFiles") or [],
                "reason": "No diff is available to save as a patch artifact.",
            }
        artifact_id = f"artifact-{uuid.uuid4()}"
        artifact_file = write_text_artifact(
            root=root, artifact_id=artifact_id, suffix=".patch", content=patch
        )
        artifact = EvidenceRepository(self.connection).create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="git_patch",
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={
                "name": f"{action['id']}.patch",
                "source": "remediation.save_patch",
                "remediationActionId": action["id"],
                "threadId": action.get("threadId"),
                "loopId": action.get("loopId"),
                "sizeBytes": artifact_file["sizeBytes"],
                "changedFiles": diff.get("changedFiles") or [],
                "mimeType": "text/x-diff",
            },
        )
        return {
            "status": "completed",
            "action": "save_patch",
            "projectId": project_id,
            "changedFiles": diff.get("changedFiles") or [],
            "artifactId": artifact["id"],
            "patchHash": artifact["hash"],
            "patchSizeBytes": artifact_file["sizeBytes"],
            "reason": "Patch saved as a local evidence artifact.",
        }

    @staticmethod
    def _resolved_similarity_decision_mode(*, threads: ThreadsRepository, decision_id: str) -> str | None:
        """Devuelve el modo elegido por el usuario si la decisión está resuelta y es válido."""
        if not decision_id:
            return None
        try:
            decision = threads.get_decision(decision_id)
        except KeyError:
            return None
        if str(decision.get("status") or "") != "resolved":
            return None
        mode = str(decision.get("resolution") or "").strip().lower().replace(" ", "_").replace("-", "_")
        return mode if mode in SIMILARITY_ACTIONS else None

    def _retry_loop(self, *, action: dict[str, Any]) -> dict[str, Any]:
        loop_id = str(action.get("loopId") or "").strip()
        if not loop_id:
            return {"status": "blocked", "action": "retry_loop", "reason": "loopId is required."}
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        threads = ThreadsRepository(self.connection)
        jobs = JobsRepository(self.connection)
        with immediate_transaction(self.connection):
            current_action = self.repository.get(action["id"])
            if current_action["status"] != "pending":
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "remediationStatus": current_action["status"],
                    "reason": "Only pending remediation actions can be executed.",
                }
            loop = coordinator.get(loop_id)
            if loop["state"] != "blocked":
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Product Loop is not blocked.",
                }
            if (
                current_action.get("loopId") != loop_id
                or current_action.get("projectId") != loop["projectId"]
                or current_action.get("actionType") != "retry_loop"
            ):
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Remediation action no longer matches the blocked Product Loop.",
                }

            durable = dict((loop.get("context") or {}).get("durableRun") or {})
            remediation_stage = str(current_action.get("stage") or "").strip()
            blocked_stage = str(durable.get("blockedStage") or "").strip()
            if remediation_stage and blocked_stage and remediation_stage != blocked_stage:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Remediation stage no longer matches the current Product Loop blocker.",
                    "remediationStage": remediation_stage,
                    "blockedStage": blocked_stage,
                }
            thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
            request_meta = (
                strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta"))
                if isinstance(durable.get("requestMeta"), dict)
                else {}
            )
            action_thread_id = str(current_action.get("threadId") or "").strip()
            durable_thread_id = str(thread_ref.get("projectThreadId") or "").strip()
            if action_thread_id and durable_thread_id and action_thread_id != durable_thread_id:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Remediation thread no longer matches the blocked Product Loop thread.",
                }
            thread_id = action_thread_id or durable_thread_id
            if not thread_id:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Blocked Product Loop does not reference a project thread.",
                }

            thread = threads.get_thread(thread_id)
            if thread["projectId"] != loop["projectId"]:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Thread project does not match the blocked Product Loop project.",
                }
            retryable_thread_statuses = {"open", "blocked"}
            if blocked_stage == "functionality_memory":
                retryable_thread_statuses.add("waiting_decision")
            if thread["status"] not in retryable_thread_statuses:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": (f"Thread status {thread['status']} cannot be superseded by a retry."),
                }

            if blocked_stage == "approval":
                return self._retry_delivery_approval(
                    action=current_action,
                    coordinator=coordinator,
                    loop=loop,
                    durable=durable,
                    thread_id=thread_id,
                    threads=threads,
                )
            if blocked_stage == "resource_learning" and self._has_delivery_resource_learning_evidence(
                durable
            ):
                return self._retry_resource_learning_approval(
                    action=current_action,
                    coordinator=coordinator,
                    loop=loop,
                    durable=durable,
                    thread_id=thread_id,
                    threads=threads,
                )

            functionality_decision_mode: str | None = None
            if blocked_stage == "functionality_memory":
                functionality_decision_mode = self._resolved_similarity_decision_mode(
                    threads=threads,
                    decision_id=str(durable.get("decisionId") or ""),
                )
                if functionality_decision_mode is None:
                    return {
                        "status": "blocked",
                        "action": "retry_loop",
                        "reason": (
                            "Resolve the existing-functionality decision on this thread before "
                            "retrying the loop."
                        ),
                        "decisionId": durable.get("decisionId"),
                    }

            message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
            message = str(durable.get("message") or "").strip()
            source_message = self._source_retry_message(
                threads=threads,
                thread_id=thread_id,
                message_id=message_id,
            )
            if source_message is not None:
                message_id = source_message["id"]
                message = message or str(source_message["content"] or "").strip()
            if not message_id or not message:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Blocked Product Loop is missing the original thread message.",
                }

            root = self._retry_root(loop["projectId"])
            if not root:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Project root is required to queue a real Product Loop retry.",
                }

            plan_only = bool(
                durable.get("planOnly") or request_meta.get("planOnly") or request_meta.get("plan_only")
            )
            approved_resource_selections = self._approved_resource_selections_from_durable(durable)
            retry_metadata = {
                **request_meta,
                "messageId": message_id,
                "planOnly": plan_only,
                "approvedResourceSelections": approved_resource_selections,
                "retryOfLoopId": loop_id,
                "retryStage": blocked_stage,
                "retryReason": durable.get("blockedReason"),
                "remediationActionId": current_action["id"],
            }
            if functionality_decision_mode:
                # Sin la elección resuelta el rerun volvería a bloquearse en el gate de memoria de
                # funcionalidad con el mismo mensaje, en un ciclo sin salida para el operador.
                retry_metadata["functionalityDecision"] = functionality_decision_mode

            existing_job = self._existing_retry_job(
                jobs=jobs,
                project_id=loop["projectId"],
                loop_id=loop_id,
                action_id=current_action["id"],
            )
            if existing_job is None:
                queued_at = utc_now()
                retry_metadata["retryQueuedAt"] = queued_at
                created = jobs.create_job(
                    project_id=loop["projectId"],
                    kind="thread.product_loop.run",
                    payload={
                        "threadId": thread_id,
                        "messageId": message_id,
                        "projectId": loop["projectId"],
                        "message": message,
                        "title": thread["title"] or loop["title"],
                        "root": root,
                        "decision": request_meta.get("decision"),
                        "teamPlan": request_meta.get("teamPlan"),
                        "planOnly": plan_only,
                        "approvedResourceSelections": approved_resource_selections,
                        "runMetadata": retry_metadata,
                        "retryOfLoopId": loop_id,
                        "retryStage": durable.get("blockedStage"),
                        "retryReason": durable.get("blockedReason"),
                        "remediationActionId": current_action["id"],
                        "retryQueuedAt": queued_at,
                    },
                    idempotency_key=f"product-loop-retry:{loop_id}:{current_action['id']}",
                )
                retry_job = created["job"]
            else:
                queued_at = utc_now()
                retry_job = existing_job

            retry_ref = {
                "status": "queued",
                "jobId": retry_job["id"],
                "threadId": thread_id,
                "messageId": message_id,
                "remediationActionId": current_action["id"],
                "queuedAt": queued_at,
            }
            durable = {
                **durable,
                "status": "retry_queued",
                "retry": retry_ref,
                "updatedAt": queued_at,
            }
            thread = threads.set_status(thread_id, "queued")
            threads.record_event(
                thread_id=thread_id,
                type="run_queued",
                agent_role="aido_lead",
                payload={
                    "jobId": retry_job["id"],
                    "messageId": message_id,
                    "status": retry_job["status"],
                    "retryOfLoopId": loop_id,
                    "remediationActionId": current_action["id"],
                },
            )
            superseded = coordinator.transition_in_transaction(
                loop_id,
                to_state="cancelled",
                reason="Blocked Product Loop superseded by a queued retry job.",
                actor="remediation",
                trigger="retry_loop",
                context_patch={"durableRun": durable},
                metadata={
                    "remediationActionId": current_action["id"],
                    "retryJobId": retry_job["id"],
                },
                expected_version=loop["version"],
            )
        return {
            "status": "queued",
            "action": "retry_loop",
            "reason": "Queued a real Product Loop retry job.",
            "job": retry_job,
            "loop": superseded,
            "thread": thread,
            "threadStatusUpdate": {
                "status": "queued",
                "thread": {"status": "queued"},
                "event": {"status": "recorded"},
            },
            "retryOfLoopId": loop_id,
            "threadId": thread_id,
            "messageId": message_id,
        }

    def _retry_delivery_approval(
        self,
        *,
        action: dict[str, Any],
        coordinator: Any,
        loop: dict[str, Any],
        durable: dict[str, Any],
        thread_id: str,
        threads: ThreadsRepository,
    ) -> dict[str, Any]:
        if not self.connection.in_transaction:
            raise RuntimeError("_retry_delivery_approval requires an active transaction.")
        if action.get("stage") != "approval" or action.get("blockerType") != "approval_unavailable":
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Remediation action does not match the delivery approval blocker.",
            }
        approval = durable.get("approval") if isinstance(durable.get("approval"), dict) else {}
        evidence_refs = [str(item) for item in approval.get("evidenceRefs") or [] if str(item).strip()]
        diff_refs = [item for item in approval.get("diffRefs") or [] if item]
        changed_files = [str(item) for item in approval.get("changedFiles") or [] if str(item).strip()]
        workspace_id = str(approval.get("workspaceId") or durable.get("workspaceId") or "").strip()
        if not evidence_refs or not diff_refs or not changed_files:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Blocked approval retry is missing evidenceRefs, diffRefs, or changedFiles.",
                "loopId": loop["id"],
            }

        approval_payload = {
            "loopId": loop["id"],
            "workspaceId": workspace_id,
            "evidenceRefs": evidence_refs,
            "diffRefs": diff_refs,
            "changedFiles": changed_files,
            "retryOfApprovalUnavailableLoopId": loop["id"],
            "remediationActionId": action["id"],
        }
        jobs = JobsRepository(self.connection)
        approval_job = jobs.create_job(
            project_id=loop["projectId"],
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload=approval_payload,
        )["job"]
        approval_action = jobs.create_action_request(
            job_id=approval_job["id"],
            project_id=loop["projectId"],
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload=approval_payload,
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
        next_approval = {
            "status": "available",
            "jobId": approval_job["id"],
            "actionRequestId": approval_action["id"],
            "evidenceRefs": evidence_refs,
            "diffRefs": diff_refs,
        }
        now = utc_now()
        review_durable = {
            **durable,
            "status": "review_ready",
            "approval": next_approval,
            "updatedAt": now,
        }
        review_ready = coordinator.transition_in_transaction(
            loop["id"],
            to_state="review_ready",
            reason="Delivery approval was recreated from existing QA, gitleaks, and diff evidence.",
            actor="remediation",
            trigger="retry_delivery_approval",
            context_patch={"durableRun": review_durable},
            metadata={"remediationActionId": action["id"], "approvalJobId": approval_job["id"]},
            expected_version=loop["version"],
        )
        awaiting_durable = {
            **dict(review_ready["context"].get("durableRun") or {}),
            "status": "awaiting_approval",
            "approval": next_approval,
            "updatedAt": now,
        }
        awaiting = coordinator.transition_in_transaction(
            loop["id"],
            to_state="awaiting_approval",
            reason="Evidence-backed Product Loop result awaits operator approval.",
            actor="remediation",
            trigger="awaiting_approval",
            context_patch={"durableRun": awaiting_durable},
            metadata={"remediationActionId": action["id"], "approvalActionRequestId": approval_action["id"]},
            expected_version=review_ready["version"],
        )
        thread = threads.set_status(thread_id, "awaiting_approval")
        threads.record_event(
            thread_id=thread_id,
            type="approval_required",
            agent_role="aido_lead",
            payload={"loopId": awaiting["id"], **next_approval, "remediationActionId": action["id"]},
        )
        self.repository.resolve_pending_for_blocker_in_transaction(
            loop["id"],
            stage="approval",
            blocker_type="approval_unavailable",
        )
        return {
            "status": "awaiting_approval",
            "action": "retry_loop",
            "reason": "Recreated delivery approval from existing QA, gitleaks, and diff evidence.",
            "job": approval_job,
            "approval": next_approval,
            "actionRequest": approval_action,
            "loop": awaiting,
            "thread": thread,
            "retryOfLoopId": loop["id"],
            "threadId": thread_id,
        }

    @staticmethod
    def _has_delivery_gitleaks_evidence(durable: dict[str, Any]) -> bool:
        review = durable.get("review") if isinstance(durable.get("review"), dict) else {}
        return (
            isinstance(durable.get("runtimeResult"), dict)
            and isinstance(durable.get("teamSchedule"), dict)
            and isinstance(review, dict)
            and bool(review.get("changedFiles"))
            and bool(str(durable.get("workspaceId") or "").strip())
            and bool(str(durable.get("workspacePath") or "").strip())
        )

    def _blocked_gitleaks_context(
        self,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        loop_id = str(payload.get("loopId") or action.get("loopId") or "").strip()
        if not loop_id:
            return None
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        try:
            loop = coordinator.get(loop_id)
        except KeyError:
            return None
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        if loop.get("state") != "blocked" or durable.get("blockedStage") != "gitleaks":
            return None
        thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
        thread_id = str(
            payload.get("threadId") or action.get("threadId") or thread_ref.get("projectThreadId") or ""
        ).strip()
        if not thread_id:
            return None
        return {
            "coordinator": coordinator,
            "loop": loop,
            "durable": durable,
            "threadId": thread_id,
            "threads": ThreadsRepository(self.connection),
        }

    def _retry_gitleaks_approval(
        self,
        *,
        action: dict[str, Any],
        coordinator: Any,
        loop: dict[str, Any],
        durable: dict[str, Any],
        thread_id: str,
        threads: ThreadsRepository,
        gitleaks: dict[str, Any],
    ) -> dict[str, Any]:
        from local_control_center.product_loop.coordinator import (
            _diff_ref_from_review,
            _diff_summary_from_review,
        )

        if not self._has_delivery_gitleaks_evidence(durable):
            return {
                "status": "blocked",
                "action": "run_gitleaks",
                "reason": (
                    "Blocked gitleaks remediation is missing runtimeResult, teamSchedule, "
                    "review diff, workspaceId, or workspacePath."
                ),
                "loopId": loop["id"],
            }
        thread = threads.get_thread(thread_id)
        if thread["projectId"] != loop["projectId"]:
            return {
                "status": "blocked",
                "action": "run_gitleaks",
                "reason": "Thread project does not match the blocked Product Loop project.",
                "loopId": loop["id"],
            }
        if thread["status"] in {"queued", "running"}:
            return {
                "status": "blocked",
                "action": "run_gitleaks",
                "reason": f"Thread is already {thread['status']}; gitleaks recovery would create concurrent delivery state.",
                "loopId": loop["id"],
            }

        team_schedule = durable["teamSchedule"]
        runtime_result = durable["runtimeResult"]
        review = durable["review"]
        workspace_id = str(durable.get("workspaceId") or "").strip()
        qa_results = (
            runtime_result.get("qaResults") if isinstance(runtime_result.get("qaResults"), list) else []
        )
        diff_ref = _diff_ref_from_review(review)
        diff_summary = _diff_summary_from_review(review)
        security_evidence = coordinator._record_run_evidence(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            stage="gitleaks",
            status="completed",
            reason=str(gitleaks.get("reason") or "gitleaks passed."),
            details=gitleaks,
            workspace_id=workspace_id,
            diff_refs=[diff_ref],
            diff_summary=diff_summary,
            test_results=[
                *qa_results,
                {
                    "command": "gitleaks",
                    "status": "passed",
                    "metadata": {
                        "workspaceId": workspace_id,
                        "findingCount": (gitleaks.get("gitleaks") or {}).get("findingCount", 0),
                    },
                },
            ],
            tool_calls=gitleaks.get("toolCalls") or [],
            policy_decisions=[
                {"id": item} for item in (gitleaks.get("policyDecisionIds") or []) if str(item).strip()
            ],
        )
        try:
            resource_learning = coordinator._record_resource_learning(
                project_id=loop["projectId"],
                loop_id=loop["id"],
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                evidence_ref=security_evidence["id"],
                success=True,
                rework=False,
                quality_score=1.0,
            )
        except Exception as error:
            return {
                "status": "blocked",
                "action": "run_gitleaks",
                "reason": f"AI resource learning persistence failed: {redact_secrets(str(error))}",
                "loopId": loop["id"],
                "evidencePackage": security_evidence,
            }

        evidence_refs = [str(item) for item in (durable.get("evidencePackageIds") or []) if str(item).strip()]
        if security_evidence["id"] not in evidence_refs:
            evidence_refs.append(security_evidence["id"])
        changed_files = [str(item) for item in (review.get("changedFiles") or []) if str(item).strip()]
        approval_payload = {
            "loopId": loop["id"],
            "workspaceId": workspace_id,
            "evidenceRefs": evidence_refs,
            "diffRefs": [diff_ref],
            "changedFiles": changed_files,
            "retryOfGitleaksLoopId": loop["id"],
            "remediationActionId": action["id"],
        }
        jobs = JobsRepository(self.connection)
        approval_job = jobs.create_job(
            project_id=loop["projectId"],
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload=approval_payload,
        )["job"]
        approval_action = jobs.create_action_request(
            job_id=approval_job["id"],
            project_id=loop["projectId"],
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload=approval_payload,
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
        next_approval = {
            "status": "available",
            "jobId": approval_job["id"],
            "actionRequestId": approval_action["id"],
            "evidenceRefs": evidence_refs,
            "diffRefs": approval_payload["diffRefs"],
        }
        now = utc_now()
        review_durable = {
            **durable,
            "status": "review_ready",
            "gitleaks": gitleaks,
            "resourceLearning": resource_learning,
            "approval": next_approval,
            "evidencePackageIds": evidence_refs,
            "updatedAt": now,
        }
        review_ready = coordinator.transition(
            loop["id"],
            to_state="review_ready",
            reason="gitleaks remediation passed and delivery evidence is ready for review.",
            actor="remediation",
            trigger="run_gitleaks",
            context_patch={"durableRun": review_durable},
            metadata={"remediationActionId": action["id"], "evidencePackageId": security_evidence["id"]},
        )
        awaiting_durable = {
            **dict(review_ready["context"].get("durableRun") or {}),
            "status": "awaiting_approval",
            "gitleaks": gitleaks,
            "resourceLearning": resource_learning,
            "approval": next_approval,
            "evidencePackageIds": evidence_refs,
            "updatedAt": now,
        }
        awaiting = coordinator.transition(
            loop["id"],
            to_state="awaiting_approval",
            reason="Evidence-backed Product Loop result awaits operator approval.",
            actor="remediation",
            trigger="awaiting_approval",
            context_patch={"durableRun": awaiting_durable},
            metadata={"remediationActionId": action["id"], "approvalActionRequestId": approval_action["id"]},
        )
        with suppress(Exception):
            threads.set_status(thread_id, "awaiting_approval")
        with suppress(Exception):
            threads.record_event(
                thread_id=thread_id,
                type="approval_required",
                agent_role="aido_lead",
                payload={
                    "loopId": awaiting["id"],
                    "resourceLearning": resource_learning,
                    "evidencePackageId": security_evidence["id"],
                    **next_approval,
                    "remediationActionId": action["id"],
                },
            )
        return {
            "status": "awaiting_approval",
            "action": "run_gitleaks",
            "reason": "Recreated delivery approval after gitleaks remediation passed.",
            "resourceLearning": resource_learning,
            "evidencePackage": security_evidence,
            "job": approval_job,
            "approval": next_approval,
            "actionRequest": approval_action,
            "loop": awaiting,
            "thread": threads.get_thread(thread_id),
            "retryOfLoopId": loop["id"],
            "threadId": thread_id,
        }

    @staticmethod
    def _has_delivery_resource_learning_evidence(durable: dict[str, Any]) -> bool:
        resource_details = (
            durable.get("resource_learning") if isinstance(durable.get("resource_learning"), dict) else {}
        )
        review = (
            durable.get("review")
            if isinstance(durable.get("review"), dict)
            else resource_details.get("review")
        )
        gitleaks = (
            durable.get("gitleaks")
            if isinstance(durable.get("gitleaks"), dict)
            else resource_details.get("gitleaks")
        )
        return (
            isinstance(durable.get("runtimeResult"), dict)
            and (
                isinstance(durable.get("teamSchedule"), dict)
                or isinstance(resource_details.get("teamSchedule"), dict)
            )
            and isinstance(review, dict)
            and bool(review.get("changedFiles"))
            and isinstance(gitleaks, dict)
            and gitleaks.get("status") == "completed"
        )

    def _retry_resource_learning_approval(
        self,
        *,
        action: dict[str, Any],
        coordinator: Any,
        loop: dict[str, Any],
        durable: dict[str, Any],
        thread_id: str,
        threads: ThreadsRepository,
    ) -> dict[str, Any]:
        if not self.connection.in_transaction:
            raise RuntimeError("_retry_resource_learning_approval requires an active transaction.")
        if (
            action.get("stage") != "resource_learning"
            or action.get("blockerType") != "resource_learning_failed"
        ):
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Remediation action does not match the resource learning blocker.",
            }
        from local_control_center.product_loop.coordinator import _diff_ref_from_review

        resource_details = (
            durable.get("resource_learning") if isinstance(durable.get("resource_learning"), dict) else {}
        )
        failed_learning = (
            durable.get("resourceLearning") if isinstance(durable.get("resourceLearning"), dict) else {}
        )
        team_schedule = durable.get("teamSchedule") if isinstance(durable.get("teamSchedule"), dict) else None
        if team_schedule is None and isinstance(resource_details.get("teamSchedule"), dict):
            team_schedule = resource_details["teamSchedule"]
        runtime_result = (
            durable.get("runtimeResult") if isinstance(durable.get("runtimeResult"), dict) else None
        )
        review = durable.get("review") if isinstance(durable.get("review"), dict) else None
        if review is None and isinstance(resource_details.get("review"), dict):
            review = resource_details["review"]
        gitleaks = durable.get("gitleaks") if isinstance(durable.get("gitleaks"), dict) else None
        if gitleaks is None and isinstance(resource_details.get("gitleaks"), dict):
            gitleaks = resource_details["gitleaks"]
        evidence_ref = str(
            failed_learning.get("evidenceRef") or resource_details.get("evidenceRef") or ""
        ).strip()
        workspace_id = str(resource_details.get("workspaceId") or durable.get("workspaceId") or "").strip()
        changed_files = [
            str(item) for item in ((review or {}).get("changedFiles") or []) if str(item).strip()
        ]
        if (
            team_schedule is None
            or runtime_result is None
            or review is None
            or not evidence_ref
            or not workspace_id
            or not changed_files
            or not isinstance(gitleaks, dict)
            or gitleaks.get("status") != "completed"
        ):
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": (
                    "Blocked resource learning retry is missing runtimeResult, teamSchedule, "
                    "completed gitleaks, review diff, workspaceId, or evidenceRef."
                ),
                "loopId": loop["id"],
            }

        resource_learning = coordinator._record_resource_learning(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=evidence_ref,
            success=True,
            rework=False,
            quality_score=1.0,
        )
        evidence_refs = [str(item) for item in (durable.get("evidencePackageIds") or []) if str(item).strip()]
        if evidence_ref not in evidence_refs:
            evidence_refs.append(evidence_ref)
        approval_payload = {
            "loopId": loop["id"],
            "workspaceId": workspace_id,
            "evidenceRefs": evidence_refs,
            "diffRefs": [_diff_ref_from_review(review)],
            "changedFiles": changed_files,
            "retryOfResourceLearningLoopId": loop["id"],
            "remediationActionId": action["id"],
        }
        jobs = JobsRepository(self.connection)
        approval_job = jobs.create_job(
            project_id=loop["projectId"],
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload=approval_payload,
        )["job"]
        approval_action = jobs.create_action_request(
            job_id=approval_job["id"],
            project_id=loop["projectId"],
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload=approval_payload,
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
        next_approval = {
            "status": "available",
            "jobId": approval_job["id"],
            "actionRequestId": approval_action["id"],
            "evidenceRefs": evidence_refs,
            "diffRefs": approval_payload["diffRefs"],
        }
        now = utc_now()
        review_durable = {
            **durable,
            "status": "review_ready",
            "resourceLearning": resource_learning,
            "approval": next_approval,
            "updatedAt": now,
        }
        review_ready = coordinator.transition_in_transaction(
            loop["id"],
            to_state="review_ready",
            reason="AI resource learning was recorded from existing QA, gitleaks, and diff evidence.",
            actor="remediation",
            trigger="retry_resource_learning",
            context_patch={"durableRun": review_durable},
            metadata={"remediationActionId": action["id"], "approvalJobId": approval_job["id"]},
            expected_version=loop["version"],
        )
        awaiting_durable = {
            **dict(review_ready["context"].get("durableRun") or {}),
            "status": "awaiting_approval",
            "resourceLearning": resource_learning,
            "approval": next_approval,
            "updatedAt": now,
        }
        awaiting = coordinator.transition_in_transaction(
            loop["id"],
            to_state="awaiting_approval",
            reason="Evidence-backed Product Loop result awaits operator approval.",
            actor="remediation",
            trigger="awaiting_approval",
            context_patch={"durableRun": awaiting_durable},
            metadata={"remediationActionId": action["id"], "approvalActionRequestId": approval_action["id"]},
            expected_version=review_ready["version"],
        )
        thread = threads.set_status(thread_id, "awaiting_approval")
        threads.record_event(
            thread_id=thread_id,
            type="approval_required",
            agent_role="aido_lead",
            payload={
                "loopId": awaiting["id"],
                "resourceLearning": resource_learning,
                **next_approval,
                "remediationActionId": action["id"],
            },
        )
        self.repository.resolve_pending_for_blocker_in_transaction(
            loop["id"],
            stage="resource_learning",
            blocker_type="resource_learning_failed",
        )
        return {
            "status": "awaiting_approval",
            "action": "retry_loop",
            "reason": "Recorded AI resource learning and recreated delivery approval from existing evidence.",
            "resourceLearning": resource_learning,
            "job": approval_job,
            "approval": next_approval,
            "actionRequest": approval_action,
            "loop": awaiting,
            "thread": thread,
            "retryOfLoopId": loop["id"],
            "threadId": thread_id,
        }

    def _retry_root(self, project_id: str) -> str | None:
        if self.root is not None:
            return str(self.root)
        try:
            project = ProjectsRepository(self.connection).get_project(project_id)
        except KeyError:
            return None
        path = str(project.get("path") or "").strip()
        if not path:
            return None
        return str(Path(path).resolve(strict=False))

    @staticmethod
    def _source_retry_message(
        *,
        threads: ThreadsRepository,
        thread_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        if message_id:
            return threads.get_message(message_id)
        user_messages = [message for message in threads.list_messages(thread_id) if message["kind"] == "user"]
        return user_messages[-1] if user_messages else None

    @staticmethod
    def _existing_retry_job(
        *,
        jobs: JobsRepository,
        project_id: str,
        loop_id: str,
        action_id: str,
    ) -> dict[str, Any] | None:
        active_statuses = {"approval_required", "queued", "running"}
        for job in jobs.list_jobs(project_id=project_id):
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if (
                job["kind"] == "thread.product_loop.run"
                and payload.get("retryOfLoopId") == loop_id
                and payload.get("remediationActionId") == action_id
                and job["status"] in active_statuses
            ):
                return job
        return None

    @staticmethod
    def _existing_plan_only_job(
        *,
        jobs: JobsRepository,
        project_id: str,
        loop_id: str,
        action_id: str,
    ) -> dict[str, Any] | None:
        active_statuses = {"approval_required", "queued", "running"}
        for job in jobs.list_jobs(project_id=project_id):
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if (
                job["kind"] == "thread.product_loop.run"
                and payload.get("planOnlyOfLoopId") == loop_id
                and payload.get("remediationActionId") == action_id
                and job["status"] in active_statuses
            ):
                return job
        return None

    def _continue_plan_only(self, *, action: dict[str, Any]) -> dict[str, Any]:
        loop_id = str(action.get("loopId") or "").strip()
        if not loop_id:
            return {"status": "blocked", "action": "continue_plan_only", "reason": "loopId is required."}
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        loop = coordinator.get(loop_id)
        if loop["state"] != "blocked":
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": "Product Loop is not blocked.",
            }

        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        remediation_stage = str(action.get("stage") or "").strip()
        blocked_stage = str(durable.get("blockedStage") or "").strip()
        if remediation_stage and blocked_stage and remediation_stage != blocked_stage:
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": "Remediation stage no longer matches the current Product Loop blocker.",
                "remediationStage": remediation_stage,
                "blockedStage": blocked_stage,
            }
        thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
        request_meta = (
            strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta"))
            if isinstance(durable.get("requestMeta"), dict)
            else {}
        )
        thread_id = str(action.get("threadId") or thread_ref.get("projectThreadId") or "").strip()
        if not thread_id:
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": "Blocked Product Loop does not reference a project thread.",
            }

        threads = ThreadsRepository(self.connection)
        thread = threads.get_thread(thread_id)
        if thread["projectId"] != loop["projectId"]:
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": "Thread project does not match the blocked Product Loop project.",
            }
        if thread["status"] in {"queued", "running"}:
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": f"Thread is already {thread['status']}; plan-only retry would create concurrent execution.",
            }

        message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
        message = str(durable.get("message") or "").strip()
        source_message = self._source_retry_message(
            threads=threads, thread_id=thread_id, message_id=message_id
        )
        if source_message is not None:
            message_id = source_message["id"]
            message = message or str(source_message["content"] or "").strip()
        if not message_id or not message:
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": "Blocked Product Loop is missing the original thread message.",
            }

        root = self._retry_root(loop["projectId"])
        if not root:
            return {
                "status": "blocked",
                "action": "continue_plan_only",
                "reason": "Project root is required to queue a plan-only Product Loop retry.",
            }

        jobs = JobsRepository(self.connection)
        existing_job = self._existing_plan_only_job(
            jobs=jobs,
            project_id=loop["projectId"],
            loop_id=loop_id,
            action_id=action["id"],
        )
        if existing_job is None:
            queued_at = utc_now()
            approved_resource_selections = self._approved_resource_selections_from_durable(durable)
            run_metadata = {
                **request_meta,
                "messageId": message_id,
                "planOnly": True,
                "approvedResourceSelections": approved_resource_selections,
                "planOnlyOfLoopId": loop_id,
                "planOnlyStage": durable.get("blockedStage"),
                "planOnlyReason": durable.get("blockedReason"),
                "remediationActionId": action["id"],
                "planOnlyQueuedAt": queued_at,
            }
            created = jobs.create_job(
                project_id=loop["projectId"],
                kind="thread.product_loop.run",
                payload={
                    "threadId": thread_id,
                    "messageId": message_id,
                    "projectId": loop["projectId"],
                    "message": message,
                    "title": thread["title"] or loop["title"],
                    "root": root,
                    "decision": request_meta.get("decision"),
                    "teamPlan": request_meta.get("teamPlan"),
                    "planOnly": True,
                    "approvedResourceSelections": approved_resource_selections,
                    "runMetadata": run_metadata,
                    "planOnlyOfLoopId": loop_id,
                    "planOnlyStage": durable.get("blockedStage"),
                    "planOnlyReason": durable.get("blockedReason"),
                    "remediationActionId": action["id"],
                    "planOnlyQueuedAt": queued_at,
                },
                idempotency_key=f"product-loop-plan-only:{loop_id}:{action['id']}",
            )
            plan_job = created["job"]
        else:
            queued_at = utc_now()
            plan_job = existing_job

        plan_ref = {
            "status": "queued",
            "jobId": plan_job["id"],
            "threadId": thread_id,
            "messageId": message_id,
            "remediationActionId": action["id"],
            "queuedAt": queued_at,
        }
        durable = {
            **durable,
            "status": "plan_only_queued",
            "planOnly": True,
            "planOnlyRetry": plan_ref,
            "updatedAt": queued_at,
        }
        superseded = coordinator.transition(
            loop_id,
            to_state="cancelled",
            reason="Blocked Product Loop superseded by a queued plan-only job.",
            actor="remediation",
            trigger="continue_plan_only",
            context_patch={"durableRun": durable},
            metadata={"remediationActionId": action["id"], "planOnlyJobId": plan_job["id"]},
        )
        thread, thread_status_update = self._mark_thread_run_queued_best_effort(
            threads=threads,
            thread_id=thread_id,
            event_payload={
                "jobId": plan_job["id"],
                "messageId": message_id,
                "status": plan_job["status"],
                "planOnlyOfLoopId": loop_id,
                "remediationActionId": action["id"],
            },
        )
        return {
            "status": "queued",
            "action": "continue_plan_only",
            "reason": "Queued a plan-only Product Loop job.",
            "job": plan_job,
            "loop": superseded,
            "thread": thread,
            "threadStatusUpdate": thread_status_update,
            "planOnlyOfLoopId": loop_id,
            "threadId": thread_id,
            "messageId": message_id,
        }

    def _approve_resource_decision(
        self, *, action: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        loop_id = str(action.get("loopId") or payload.get("loopId") or "").strip()
        if not loop_id:
            return {
                "status": "blocked",
                "action": "approve_resource_decision",
                "reason": "loopId is required.",
            }
        action_payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        new_approvals = self._resource_approvals_from_payload(action=action, payload=action_payload)
        if not new_approvals:
            return {
                "status": "blocked",
                "action": "approve_resource_decision",
                "reason": "A selected AI resource is required before approval.",
            }
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        loop = coordinator.get(loop_id)
        if loop["state"] != "blocked":
            return {
                "status": "blocked",
                "action": "approve_resource_decision",
                "reason": "Product Loop is not blocked.",
            }
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        if durable.get("blockedStage") != "resource_manager":
            return {
                "status": "blocked",
                "action": "approve_resource_decision",
                "reason": "Product Loop is not blocked at resource_manager.",
            }
        request_meta = strip_untrusted_resource_cost_policy_metadata(
            durable.get("requestMeta") if isinstance(durable.get("requestMeta"), dict) else {}
        )
        approvals = [
            item
            for item in request_meta.get("approvedResourceSelections") or []
            if isinstance(item, dict)
            and not any(self._same_resource_approval(item, approval) for approval in new_approvals)
        ]
        approvals.extend(new_approvals)
        request_meta["approvedResourceSelections"] = approvals
        approved_at = new_approvals[-1]["approvedAt"]
        durable = {
            **durable,
            "requestMeta": request_meta,
            "resourceApproval": {
                "status": "approved",
                "latest": new_approvals[-1],
                "approvedResourceSelections": approvals,
            },
            "updatedAt": approved_at,
        }
        updated = coordinator.repository.update_loop_context(
            loop_id,
            context={**loop["context"], "durableRun": durable},
        )
        return {
            "status": "completed",
            "action": "approve_resource_decision",
            "reason": "Approved the selected AI resource for the next Product Loop retry.",
            "approvals": redact_secrets(new_approvals),
            "loop": updated,
        }

    def _resource_approvals_from_payload(
        self, *, action: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        items = payload.get("resourceApprovals")
        if isinstance(items, list):
            approvals = [
                approval
                for item in items
                if isinstance(item, dict)
                for approval in [self._single_resource_approval(action=action, payload=item)]
                if approval is not None
            ]
            if approvals:
                return approvals
        approval = self._single_resource_approval(action=action, payload=payload)
        return [approval] if approval is not None else []

    def _single_resource_approval(
        self, *, action: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
        role = str(payload.get("role") or "").strip()
        provider_id = str(payload.get("providerId") or selected.get("providerId") or "").strip()
        model = str(payload.get("model") or selected.get("model") or "").strip()
        runtime = str(payload.get("runtime") or selected.get("runtime") or "").strip()
        if not role or not provider_id or not model or not runtime:
            return None
        if not self._resource_decision_requires_approval(payload):
            return None
        approval = {
            "role": role,
            "providerId": provider_id,
            "model": model,
            "runtime": runtime,
            "taskId": str(payload.get("taskId") or "").strip(),
            "reason": str(payload.get("reason") or action.get("description") or "").strip(),
            "approvedBy": str(payload.get("approvedBy") or "remediation"),
            "approvedAt": utc_now(),
            "remediationActionId": action["id"],
        }
        for key in (
            "approvalRequired",
            "estimatedCostUsd",
            "usageStatus",
            "localVsRemote",
            "costTier",
            "budgetStop",
            "maxTokens",
        ):
            if key in payload:
                approval[key] = payload.get(key)
        policy_result = resource_policy_summary(payload.get("policyResult"))
        if policy_result:
            approval["policyResult"] = policy_result
        score_breakdown = payload.get("scoreBreakdown")
        if isinstance(score_breakdown, dict) and score_breakdown:
            approval["scoreBreakdown"] = score_breakdown
        return redact_secrets(approval)

    @staticmethod
    def _same_resource_approval(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
        return all(
            str(current.get(key) or "") == str(candidate.get(key) or "")
            for key in ("role", "providerId", "model", "runtime")
        )

    @staticmethod
    def _resource_decision_requires_approval(decision: Any) -> bool:
        if not isinstance(decision, dict):
            return False
        approval_required = decision.get("approvalRequired")
        if approval_required is True:
            return True
        if approval_required is False:
            return False
        policy_result = resource_policy_summary(decision.get("policyResult"))
        unknown_cost_policy = policy_result.get("unknownCostPolicy")
        return (
            isinstance(unknown_cost_policy, dict)
            and str(unknown_cost_policy.get("action") or "").strip() == "require_approval"
        )

    @staticmethod
    def _resource_approvals_from_blockers(details: dict[str, Any]) -> list[dict[str, Any]]:
        resource_approvals: list[dict[str, Any]] = []
        resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else []
        for item in resource_blockers or []:
            if not isinstance(item, dict):
                continue
            decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
            selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
            if not selected:
                continue
            policy_result = resource_policy_summary(decision.get("policyResult"))
            if not BlockerRemediationService._resource_decision_requires_approval(decision):
                continue
            approval = {
                "role": item.get("role"),
                "taskId": item.get("taskId"),
                "providerId": selected.get("providerId"),
                "model": selected.get("model"),
                "runtime": selected.get("runtime"),
                "selected": selected,
                "decisionReason": decision.get("decisionReason"),
            }
            for key in (
                "approvalRequired",
                "estimatedCostUsd",
                "usageStatus",
                "localVsRemote",
                "costTier",
                "budgetStop",
                "maxTokens",
            ):
                if key in decision:
                    approval[key] = decision.get(key)
            if policy_result:
                approval["policyResult"] = policy_result
            score_breakdown = decision.get("scoreBreakdown")
            if isinstance(score_breakdown, dict) and score_breakdown:
                approval["scoreBreakdown"] = score_breakdown
            resource_approvals.append(approval)
        return resource_approvals

    @classmethod
    def _approve_resource_decision_spec(cls, details: dict[str, Any]) -> dict[str, Any] | None:
        resource_approvals = cls._resource_approvals_from_blockers(details)
        if not resource_approvals:
            return None
        return {
            "actionType": "approve_resource_decision",
            "title": "Approve selected AI resources",
            "description": "Approve the blocked model/runtime selections for the next Product Loop retry.",
            "payload": {"resourceApprovals": resource_approvals},
        }

    def _answer_question(
        self,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
        allow_dismissed: bool = False,
    ) -> dict[str, Any]:
        action_payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        persisted_thread_id = str(action.get("threadId") or action_payload.get("threadId") or "").strip()
        payload_thread_id = str(payload.get("threadId") or "").strip()
        if persisted_thread_id and payload_thread_id and payload_thread_id != persisted_thread_id:
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": "threadId does not match the persisted remediation action.",
            }
        persisted_decision_id = str(action_payload.get("decisionId") or "").strip()
        payload_decision_id = str(payload.get("decisionId") or "").strip()
        if persisted_decision_id and payload_decision_id and payload_decision_id != persisted_decision_id:
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": "decisionId does not match the persisted remediation action.",
            }
        thread_id = persisted_thread_id or payload_thread_id
        decision_id = persisted_decision_id or payload_decision_id
        answer = str(payload.get("answer") or payload.get("resolution") or "").strip()
        if not thread_id or not decision_id or not answer:
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": "threadId, decisionId and answer/resolution are required.",
            }
        threads = ThreadsRepository(self.connection)
        decision = threads.get_decision(decision_id)
        if decision["threadId"] != thread_id:
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": f"Decision {decision_id} does not belong to thread {thread_id}.",
            }
        stale_loop_block = self._answer_question_loop_staleness(action=action)
        if stale_loop_block:
            return stale_loop_block
        thread = threads.get_thread(thread_id)
        thread_status = str(thread.get("status") or "").strip()
        if thread_status in {"queued", "running"}:
            return {
                "status": "blocked",
                "action": "answer_question",
                "threadId": thread_id,
                "decisionId": decision_id,
                "threadStatus": thread_status,
                "reason": (
                    f"Thread is already {thread_status}; answer_question remediation cannot "
                    "safely resolve this decision until the active run finishes."
                ),
            }
        options = self._answer_options(decision=decision, action_payload=action_payload)
        matched_answer = self._matched_option(answer, options)
        if options and matched_answer is None:
            return {
                "status": "blocked",
                "action": "answer_question",
                "decisionId": decision_id,
                "answer": answer,
                "options": options,
                "reason": "Answer must match one of the offered decision options.",
            }
        if matched_answer is not None:
            answer = matched_answer
        from local_control_center.threads.coordinator import ThreadCoordinator

        clarification_payload = {
            **action_payload,
            "answer": answer,
            "resolution": answer,
            "decidedBy": str(payload.get("decidedBy") or "remediation"),
        }
        with immediate_transaction(self.connection):
            current_action = self.repository.get(action["id"])
            executable_statuses = {"pending", "dismissed"} if allow_dismissed else {"pending"}
            if current_action["status"] not in executable_statuses:
                return {
                    "status": "blocked",
                    "action": "answer_question",
                    "remediationStatus": current_action["status"],
                    "reason": "Only pending remediation actions can be executed.",
                }
            stale_loop_block = self._answer_question_loop_staleness(action=current_action)
            if stale_loop_block:
                return stale_loop_block
            current_thread = threads.get_thread(thread_id)
            current_thread_status = str(current_thread.get("status") or "").strip()
            if current_thread_status in {"queued", "running"}:
                return {
                    "status": "blocked",
                    "action": "answer_question",
                    "threadId": thread_id,
                    "decisionId": decision_id,
                    "threadStatus": current_thread_status,
                    "reason": (
                        f"Thread is already {current_thread_status}; answer_question remediation "
                        "cannot safely resolve this decision until the active run finishes."
                    ),
                }
            remaining_decision_ids = self._remaining_product_owner_decision_ids(
                action=current_action,
                current_decision_id=decision_id,
                threads=threads,
            )
            result = ThreadCoordinator(self.connection, root=self.root).resolve_decision_in_transaction(
                thread_id=thread_id,
                decision_id=decision_id,
                resolution=answer,
                decided_by=str(payload.get("decidedBy") or "remediation"),
                defer_followup=bool(remaining_decision_ids),
            )
            clarification = self._record_clarification_answer(
                decision=result.get("decision") or {},
                payload=clarification_payload,
                answer=answer,
            )
            if remaining_decision_ids:
                loop_supersede = self._defer_answered_decision_batch_in_transaction(
                    action=current_action,
                    decision_id=decision_id,
                    remaining_decision_ids=remaining_decision_ids,
                    threads=threads,
                )
            else:
                loop_supersede = self._supersede_answered_decision_loop_in_transaction(
                    action=current_action,
                    result=result,
                    answer=answer,
                    decision_id=decision_id,
                )
            if self.repository.get(current_action["id"])["status"] != "resolved":
                self.repository.mark_status_in_transaction(current_action["id"], "resolved")
        return {
            "status": "completed",
            "action": "answer_question",
            **result,
            **clarification,
            **loop_supersede,
        }

    def _answer_question_loop_staleness(self, *, action: dict[str, Any]) -> dict[str, Any] | None:
        loop_id = str(action.get("loopId") or "").strip()
        if not loop_id:
            return None
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        try:
            loop = ProductLoopCoordinator(self.connection, root=self.root).get(loop_id)
        except KeyError:
            return None
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        blocker_type = str(action.get("blockerType") or "").strip()
        if blocker_type == "po_needs_input" and loop["state"] != "awaiting_user":
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": "Product Loop is not awaiting this ProductOwnerAgent question.",
                "loopId": loop_id,
                "loopState": loop["state"],
                "blockedStage": durable.get("blockedStage"),
            }
        if blocker_type == "functionality_memory_decision_required" and (
            loop["state"] != "blocked" or durable.get("blockedStage") != "functionality_memory"
        ):
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": "Product Loop is no longer blocked on this functionality decision.",
                "loopId": loop_id,
                "loopState": loop["state"],
                "blockedStage": durable.get("blockedStage"),
            }
        return None

    def _remaining_product_owner_decision_ids(
        self,
        *,
        action: dict[str, Any],
        current_decision_id: str,
        threads: ThreadsRepository,
    ) -> list[str]:
        if action.get("blockerType") != "po_needs_input":
            return []
        loop_id = str(action.get("loopId") or "").strip()
        thread_id = str(action.get("threadId") or "").strip()
        if not loop_id or not thread_id:
            return []
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        loop = ProductLoopCoordinator(self.connection, root=self.root).get(loop_id)
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        product_owner = durable.get("productOwner") if isinstance(durable.get("productOwner"), dict) else {}
        pending_specs = product_owner.get("pendingThreadDecisions")
        candidate_ids = [
            str(item.get("decisionId") or "").strip()
            for item in pending_specs or []
            if isinstance(item, dict) and str(item.get("decisionId") or "").strip()
        ]
        if not candidate_ids:
            candidate_ids = [
                str((item.get("payload") or {}).get("decisionId") or "").strip()
                for item in self.repository.list_for_thread(thread_id)
                if item.get("loopId") == loop_id
                and item.get("blockerType") == "po_needs_input"
                and item.get("actionType") == "answer_question"
            ]
        remaining: list[str] = []
        for candidate_id in dict.fromkeys(candidate_ids):
            if not candidate_id or candidate_id == current_decision_id:
                continue
            with suppress(KeyError):
                decision = threads.get_decision(candidate_id)
                if decision["threadId"] == thread_id and decision["status"] == "pending":
                    remaining.append(candidate_id)
        return remaining

    def _defer_answered_decision_batch_in_transaction(
        self,
        *,
        action: dict[str, Any],
        decision_id: str,
        remaining_decision_ids: list[str],
        threads: ThreadsRepository,
    ) -> dict[str, Any]:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "_defer_answered_decision_batch_in_transaction requires an active transaction."
            )
        loop_id = str(action.get("loopId") or "").strip()
        thread_id = str(action.get("threadId") or "").strip()
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        loop = coordinator.get(loop_id)
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        product_owner = durable.get("productOwner") if isinstance(durable.get("productOwner"), dict) else {}
        remaining_set = set(remaining_decision_ids)
        pending_specs = [
            item
            for item in product_owner.get("pendingThreadDecisions") or []
            if isinstance(item, dict) and str(item.get("decisionId") or "") in remaining_set
        ]
        answered = [
            item
            for item in product_owner.get("answeredThreadDecisions") or []
            if isinstance(item, dict) and item.get("decisionId") != decision_id
        ]
        answered_at = utc_now()
        answered.append(
            {
                "decisionId": decision_id,
                "remediationActionId": action["id"],
                "answeredAt": answered_at,
            }
        )
        updated_loop = coordinator.repository.update_loop_context(
            loop_id,
            context={
                **loop["context"],
                "durableRun": {
                    **durable,
                    "status": "awaiting_user",
                    "productOwner": {
                        **product_owner,
                        "status": "needs_input",
                        "pendingThreadDecisions": pending_specs,
                        "answeredThreadDecisions": answered,
                    },
                    "updatedAt": answered_at,
                },
            },
        )
        threads.record_event(
            thread_id=thread_id,
            type="decision_batch_progress",
            agent_role="product_owner",
            payload={
                "loopId": loop_id,
                "decisionId": decision_id,
                "remainingDecisionIds": remaining_decision_ids,
                "remainingDecisionCount": len(remaining_decision_ids),
                "remediationActionId": action["id"],
            },
        )
        self.repository.mark_status_in_transaction(action["id"], "resolved")
        return {
            "loop": updated_loop,
            "loopSupersede": {
                "status": "deferred",
                "decisionId": decision_id,
                "remainingDecisionIds": remaining_decision_ids,
                "remainingDecisionCount": len(remaining_decision_ids),
            },
        }

    def _supersede_answered_decision_loop_in_transaction(
        self,
        *,
        action: dict[str, Any],
        result: dict[str, Any],
        answer: str,
        decision_id: str,
    ) -> dict[str, Any]:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "_supersede_answered_decision_loop_in_transaction requires an active transaction."
            )
        loop_id = str(action.get("loopId") or "").strip()
        queued_job = result.get("job") if isinstance(result.get("job"), dict) else None
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = str(action.get("threadId") or thread.get("id") or "").strip()
        if not loop_id or queued_job is None:
            return {}

        from local_control_center.product_loop.coordinator import ProductLoopCoordinator, allowed_next_states

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        loop = coordinator.get(loop_id)
        if loop["state"] == "cancelled":
            return {"loop": loop, "loopSupersede": {"status": "already_cancelled"}}
        if "cancelled" not in allowed_next_states(loop["state"]):
            return {
                "loop": loop,
                "loopSupersede": {
                    "status": "skipped",
                    "reason": f"Product Loop state {loop['state']} cannot be superseded.",
                },
            }
        queued_at = utc_now()
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        decision_answer = {
            "status": "queued",
            "jobId": queued_job["id"],
            "threadId": thread_id,
            "decisionId": decision_id,
            "answer": answer,
            "remediationActionId": action["id"],
            "queuedAt": queued_at,
        }
        durable = {
            **durable,
            "status": "decision_answer_queued",
            "decisionAnswer": decision_answer,
            "updatedAt": queued_at,
        }
        superseded = coordinator.transition_in_transaction(
            loop_id,
            to_state="cancelled",
            reason="Product Loop awaiting a decision was superseded by a queued answered-decision job.",
            actor="remediation",
            trigger="answer_question",
            context_patch={"durableRun": durable},
            metadata={
                "remediationActionId": action["id"],
                "decisionId": decision_id,
                "queuedJobId": queued_job["id"],
            },
            expected_version=loop["version"],
        )
        return {
            "loop": superseded,
            "loopSupersede": {
                "status": "cancelled",
                "jobId": queued_job["id"],
                "threadId": thread_id,
                "decisionId": decision_id,
            },
        }

    @staticmethod
    def _answer_options(*, decision: dict[str, Any], action_payload: dict[str, Any]) -> list[str]:
        raw_options = decision.get("options") or action_payload.get("options") or []
        return [str(option).strip() for option in raw_options if str(option).strip()]

    @classmethod
    def _matched_option(cls, answer: str, options: list[str]) -> str | None:
        if not options:
            return None
        answer_key = cls._option_key(answer)
        return next((option for option in options if cls._option_key(option) == answer_key), None)

    @staticmethod
    def _option_key(value: str) -> str:
        return "_".join(str(value).replace("_", " ").strip().lower().split())

    def _record_clarification_answer(
        self,
        *,
        decision: dict[str, Any],
        payload: dict[str, Any],
        answer: str,
    ) -> dict[str, Any]:
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        question_id = str(
            payload.get("clarificationQuestionId") or metadata.get("clarificationQuestionId") or ""
        ).strip()
        if not question_id:
            return {}
        discovery = ProductDiscoveryRepository(self.connection)
        try:
            question = discovery.get_clarification_question(question_id)
        except KeyError:
            return {
                "clarificationAnswer": {
                    "status": "skipped",
                    "reason": f"Clarification question not found: {question_id}",
                }
            }
        existing_answer = next(
            (
                item
                for item in discovery.list_clarification_answers(question_id)
                if item["status"] == "accepted" and item["answer"] == answer
            ),
            None,
        )
        if existing_answer is None:
            existing_answer = discovery.create_clarification_answer(
                {
                    "projectId": question["projectId"],
                    "questionId": question_id,
                    "initiativeId": question.get("initiativeId") or payload.get("initiativeId") or "",
                    "answer": answer,
                    "status": "accepted",
                    "answeredBy": str(payload.get("decidedBy") or "remediation"),
                    "metadata": {
                        "source": "blocker_remediation",
                        "threadDecisionId": decision.get("id"),
                        "options": metadata.get("options") or payload.get("options") or [],
                    },
                }
            )
        question_metadata = question.get("metadata") if isinstance(question.get("metadata"), dict) else {}
        updated_question = discovery.update_clarification_question(
            question_id,
            {
                "status": "answered",
                "metadata": {
                    **question_metadata,
                    "remediationAnswer": answer,
                    "remediationAnsweredAt": utc_now(),
                    "remediationDecisionId": decision.get("id"),
                },
            },
        )
        return {"clarificationAnswer": existing_answer, "clarificationQuestion": updated_question}

    def _set_default_runtime(self, runtime_id: str) -> dict[str, Any]:
        repository = RuntimeConfigRepository(self.connection)
        try:
            current = repository.get_preferences()
        except KeyError:
            current = {
                "scope": "global",
                "scopeId": "",
                "runtimeOrder": [],
                "defaultProfiles": {},
                "enabled": True,
                "metadata": {},
            }
        runtime_order = [str(item) for item in current.get("runtimeOrder") or [] if str(item).strip()]
        if runtime_id not in runtime_order:
            runtime_order.insert(0, runtime_id)
        return repository.upsert_preferences(
            {
                "scope": "global",
                "scopeId": "",
                "defaultRuntime": runtime_id,
                "runtimeOrder": runtime_order,
                "defaultProfiles": current.get("defaultProfiles") or {},
                "enabled": current.get("enabled", True),
                "metadata": {
                    **dict(current.get("metadata") or {}),
                    "updatedBy": "blocker_remediation",
                },
            }
        )

    def _blocker_type(self, *, stage: str, reason: str, details: dict[str, Any]) -> str:
        text = f"{stage} {reason} {details}".lower()
        reason_text = str(reason or "").lower()
        status = str(details.get("status") or "").lower()
        if stage == "review":
            return "review_diff_unavailable"
        if stage == "gitleaks":
            if status == "configuration_required" or "not found" in text:
                return "gitleaks_missing"
            return "gitleaks_failed"
        if stage == "qa":
            return "qa_failed"
        if stage == "runtime":
            if any(token in reason_text for token in ("auth", "login", "not authenticated")):
                return "runtime_auth_missing"
            if details.get("executable") is False:
                return "runtime_not_executable"
            return "runtime_output_invalid"
        if stage == "resource_manager":
            resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
            blockers = [
                item
                for item in resource_blockers or []
                if isinstance(item, dict) and isinstance(item.get("decision"), dict)
            ]
            if any(item["decision"].get("selected") is None for item in blockers):
                rejected = [
                    rejected_item
                    for item in blockers
                    for rejected_item in item["decision"].get("rejected") or []
                    if isinstance(rejected_item, dict)
                ]
                explicit_rejected = [item for item in rejected if item.get("profileSource") == "explicit"]
                relevant_rejected = explicit_rejected or rejected
                rejection_text = f"{reason_text} {relevant_rejected}".lower()
                if "credential" in rejection_text or "api key" in rejection_text:
                    return "provider_missing_credentials"
                if "runtime_not_executable:" in rejection_text:
                    return "runtime_not_executable"
                if "provider" in rejection_text and (
                    "health" in rejection_text or "unhealthy" in rejection_text
                ):
                    return "provider_health_failed"
                if relevant_rejected and all(
                    item.get("reason") == "privacy_blocks_remote" for item in relevant_rejected
                ):
                    return "resource_manager_privacy_blocked"
                return "resource_manager_unconfigured"
            if "requires approval" in reason_text or any(
                self._resource_decision_requires_approval(item["decision"]) for item in blockers
            ):
                return "resource_manager_approval_required"
            return "resource_manager_unconfigured"
        if stage == "team_scheduler":
            return "team_scheduler_failed"
        if stage == "technical_lead":
            return "technical_lead_planning_failed"
        if stage == "resource_learning":
            return "resource_learning_failed"
        if stage == "product_owner_runtime":
            return "runtime_not_executable"
        if stage == "git":
            if details.get("remoteMissing") is True or "configured git remote" in reason_text:
                return "git_remote_missing"
            if status == "failed" or "status check failed" in reason_text:
                return "git_status_failed"
            if details.get("dirty") is True or "dirty" in reason_text:
                return "git_dirty_tree"
            if status == "configuration_required" or "not a git repository" in text:
                return "git_not_initialized"
            return "git_branch_missing"
        if stage == "worker":
            return "worker_not_running"
        if stage == "product_owner" and status in {"persistence_failed", "failed_validation"}:
            return "product_owner_output_invalid"
        if stage == "product_owner" and (
            status == "needs_input"
            or bool(details.get("pendingDecisions"))
            or "question" in text
            or "input" in text
        ):
            if not self._has_actionable_pending_decision(details):
                return "product_owner_output_invalid"
            return "po_needs_input"
        if stage == "product_owner":
            return "product_owner_output_invalid"
        if stage == "backlog":
            return "product_owner_output_invalid"
        if stage == "research":
            return "research_required"
        if stage == "workspace_check":
            return "workspace_root_missing"
        if stage in {"product_owner_workspace", "workspace"}:
            return "workspace_allocation_failed"
        if stage == "approval":
            return "approval_unavailable"
        if stage == "project_assessment":
            return "project_assessment_failed"
        if stage == "functionality_memory":
            return "functionality_memory_decision_required"
        if stage == "thread_similarity":
            return "thread_similarity_decision_required"
        if stage == "thread_intake":
            return "thread_intake_decision_required"
        if "credential" in text or "api key" in text:
            return "provider_missing_credentials"
        if "provider" in text and ("health" in text or "unhealthy" in text):
            return "provider_health_failed"
        if "auth" in text or "login" in text or "not authenticated" in text:
            return "runtime_auth_missing"
        return "runtime_output_invalid"

    @staticmethod
    def _has_actionable_pending_decision(details: dict[str, Any]) -> bool:
        pending_decisions = details.get("pendingDecisions") if isinstance(details, dict) else None
        if not isinstance(pending_decisions, list):
            return False
        for item in pending_decisions:
            if not isinstance(item, dict) or not str(item.get("decisionId") or "").strip():
                continue
            options = [str(option).strip() for option in item.get("options") or [] if str(option).strip()]
            if len(options) >= 2:
                return True
        return False

    @staticmethod
    def _remote_url_payload_is_safe(url: str) -> bool:
        text = url.strip()
        if not text or text != url:
            return False
        parsed = urlparse(text)
        if parsed.scheme:
            if parsed.scheme not in {"https", "ssh"}:
                return False
            if parsed.username or parsed.password:
                return False
            return bool(parsed.hostname and parsed.path.strip("/"))
        if " " in text or "\t" in text or "\n" in text:
            return False
        user_host, separator, path = text.partition(":")
        user, at, host = user_host.partition("@")
        return bool(separator and at and user and host and path.strip("/"))

    def _configured_git_remote_add_specs(self, project_id: str | None) -> list[dict[str, Any]]:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return []
        rows = self.connection.execute(
            """
            SELECT name, url
            FROM git_remotes
            WHERE project_id = ?
            ORDER BY updated_at DESC, created_at DESC, name ASC
            """,
            (normalized_project_id,),
        ).fetchall()
        specs: list[dict[str, Any]] = []
        for row in rows:
            name = str(row["name"] or "").strip()
            url = str(row["url"] or "").strip()
            if not name or not url or not self._remote_url_payload_is_safe(url):
                continue
            specs.append(
                {
                    "actionType": "add_remote",
                    "title": f"Re-add {name} remote",
                    "description": "Re-add the persisted Git remote metadata to the project repository.",
                    "payload": {"name": name, "url": url},
                }
            )
        return specs

    def _action_specs(
        self,
        blocker_type: str,
        *,
        reason: str,
        details: dict[str, Any],
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        context = build_blocker_payload_context(
            reason=reason,
            details=details,
            git_remote_add_specs=self._configured_git_remote_add_specs(project_id),
        )
        resource_approval_spec = self._approve_resource_decision_spec(details)
        builder = PAYLOAD_BUILDERS.get(blocker_type)
        specs = (
            builder(context)
            if builder is not None
            else [
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": reason or "Retry after resolving the blocker.",
                    "payload": {},
                }
            ]
        )
        if blocker_type.startswith("resource_manager_") and resource_approval_spec:
            return [resource_approval_spec, *specs]
        return specs
