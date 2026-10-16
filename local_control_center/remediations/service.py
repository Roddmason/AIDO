"""Resolve blocked/configuration-required states into concrete user remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import Any

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.local_runtimes.catalog import local_profile_entry
from local_control_center.product_loop.metadata import (
    seal_operator_cost_decision,
    strip_untrusted_resource_cost_policy_metadata,
)
from local_control_center.remediations import question_answers, retry_actions, runtime_recovery
from local_control_center.remediations.action_policy import should_resolve
from local_control_center.remediations.blocker_classification import (
    blocker_type,
)
from local_control_center.remediations.git_remote_policy import remote_url_payload_is_safe
from local_control_center.remediations.local_runtime import local_runtime_specs, uses_local_runtime_specs
from local_control_center.remediations.payloads import (
    PAYLOAD_BUILDERS,
    blocker_runtime_id,
    build_blocker_payload_context,
    decision_engine_failure,
    interrupted_execution_specs,
    resource_selection_constraint_failure,
    runtime_risk_review_failure,
)
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.remediations.resource_approvals import (
    approve_resource_decision_spec,
    approved_resource_selections_from_durable,
    resource_approvals_from_payload,
    same_resource_approval,
)
from local_control_center.remediations.retry_support import (
    backfill_details_from_durable,
)
from local_control_center.remediations.runtime_actions import runtime_status_for, validate_runtime_execution
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workers.leadership import WorkerControlRepository

DESTRUCTIVE_REMEDIATION_ACTION_TYPES = frozenset({"checkout_branch"})
"""Remediation actions that can discard or overwrite local work; execute() refuses them until the caller
explicitly confirms, and their persisted records are flagged ``confirmationRequired``.
"""
PRODUCT_LOOP_DEPENDENT_REMEDIATION_ACTION_TYPES = frozenset(
    {"approve_resource_decision", "continue_plan_only", "retry_loop"}
)
LOCAL_WORKER_RECOVERABLE_STAGES = frozenset({"gitleaks", "research", "runtime", "worker"})

ACTION_HANDLERS: dict[str, str] = {
    "open_settings_section": "_handle_open_settings_section",
    "validate_runtime": "_handle_validate_runtime",
    "switch_runtime": "_handle_switch_runtime",
    "run_worker_once": "_handle_run_worker_once",
    "check_network_access": "_handle_check_network_access",
    "git_init": "_handle_git_action",
    "add_remote": "_handle_git_action",
    "create_branch": "_handle_git_action",
    "checkout_branch": "_handle_git_action",
    "view_diff": "_handle_git_action",
    "run_gitleaks": "_handle_git_action",
    "save_patch": "_handle_save_patch",
    "retry_loop": "_handle_retry_loop",
    "answer_question": "_handle_answer_question",
    "approve_resource_decision": "_handle_approve_resource_decision",
    "continue_plan_only": "_handle_continue_plan_only",
    "revalidate_runtime": "_handle_revalidate_runtime",
}
GIT_ACTION_HANDLERS: dict[str, str] = {
    "git_init": "_git_init",
    "add_remote": "_git_add_remote",
    "create_branch": "_git_create_branch",
    "checkout_branch": "_git_checkout_branch",
    "view_diff": "_git_view_diff",
    "run_gitleaks": "_git_run_gitleaks",
}


CoordinatorFactory = Callable[[sqlite3.Connection, Path | None], Any]


def default_coordinator_factory(connection: sqlite3.Connection, root: Path | None) -> Any:
    """Build the real ProductLoopCoordinator; imported lazily because the coordinator imports this module."""
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

    return ProductLoopCoordinator(connection, root=root)


class BlockerRemediationService:
    """Maps blocker evidence to persisted, user-executable remediation actions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        root: str | Path | None = None,
        coordinator_factory: CoordinatorFactory | None = None,
    ):
        self.connection = connection
        self.root = Path(root).resolve(strict=False) if root is not None else None
        self.repository = RemediationActionsRepository(connection)
        self._coordinator_factory = coordinator_factory or default_coordinator_factory

    def _coordinator(self) -> Any:
        """Build a Product Loop coordinator bound to this service's connection and root."""
        return self._coordinator_factory(self.connection, self.root)

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
        if stage == "worker" and clean_details.get("interruptedExecutionId") and loop_id:
            blocker_type = "runtime_execution_failed"
            specs = interrupted_execution_specs(clean_details)
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
        actions = [
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
        return self._research_actions(actions)

    def _research_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from local_control_center.product_loop.repository import ProductLoopRepository
        from local_control_center.product_loop.research_resolution import (
            ResearchResolutionError,
            research_candidates,
            resolution_snapshot,
        )

        enriched = []
        for action in actions:
            if (
                action.get("stage") != "research"
                or action.get("actionType") != "retry_loop"
                or action.get("status") != "pending"
                or not action.get("loopId")
                or self.root is None
            ):
                enriched.append(action)
                continue
            payload = dict(action["payload"])
            try:
                loop = ProductLoopRepository(self.connection).get_loop(action["loopId"])
                anchor = payload.get("researchSnapshot")
                additions = {"assessmentHash", "threadDecisionsHash", "executionOptions"}
                if not isinstance(anchor, dict) or not additions.intersection(anchor):
                    with (
                        nullcontext()
                        if self.connection.in_transaction
                        else immediate_transaction(self.connection)
                    ):
                        observed = self.connection.execute(
                            "SELECT payload_json FROM remediation_actions WHERE id=?", (action["id"],)
                        ).fetchone()["payload_json"]
                        stored = json_loads(observed, {})
                        anchor = stored.get("researchSnapshot")
                        loop = ProductLoopRepository(self.connection).get_loop(action["loopId"])
                        current = resolution_snapshot(self.connection, loop)
                        upgrade = None
                        first_observation = anchor is None
                        if (
                            isinstance(anchor, dict)
                            and set(current) - set(anchor) == additions
                            and set(anchor) <= set(current)
                            and all(current[key] == value for key, value in anchor.items())
                            and stored.get("researchLoopVersion") == loop["version"]
                            and "researchSnapshotUpgrade" not in stored
                        ):
                            upgrade = {
                                "addedFields": sorted(additions),
                                "previousSnapshot": anchor,
                                "loopVersion": loop["version"],
                                "upgradedAt": utc_now(),
                            }
                        if first_observation or upgrade:
                            updated = {
                                **stored,
                                "researchSnapshot": current,
                                "researchLoopVersion": loop["version"],
                            }
                            if upgrade:
                                updated["researchSnapshotUpgrade"] = upgrade
                            self.connection.execute(
                                "UPDATE remediation_actions SET payload_json=? WHERE id=? AND payload_json=? "
                                "AND status='pending' AND stage='research' AND action_type='retry_loop' "
                                "AND project_id=? AND thread_id=? AND loop_id=? "
                                "AND EXISTS (SELECT 1 FROM product_loops WHERE id=? AND version=?)",
                                (
                                    json_dumps(updated),
                                    action["id"],
                                    observed,
                                    action["projectId"],
                                    action["threadId"],
                                    loop["id"],
                                    loop["id"],
                                    loop["version"],
                                ),
                            )
                        stored_action = self.repository.get(action["id"])
                        action = {**action, "status": stored_action["status"]}
                        stored = stored_action["payload"]
                        anchor = stored.get("researchSnapshot")
                        for key in ("researchSnapshot", "researchLoopVersion", "researchSnapshotUpgrade"):
                            if key in stored:
                                payload[key] = stored[key]
                            else:
                                payload.pop(key, None)
                        loop = ProductLoopRepository(self.connection).get_loop(action["loopId"])
                if action["status"] != "pending":
                    payload["researchCandidates"] = []
                    enriched.append({**action, "payload": payload})
                    continue
                payload["researchCandidates"] = research_candidates(
                    self.connection,
                    root=self.root,
                    loop=loop,
                    anchor=anchor,
                )
            except (ResearchResolutionError, KeyError, OSError, ValueError, TypeError) as error:
                payload["researchCandidates"] = []
                payload["researchReason"] = str(error)
            enriched.append({**action, "payload": payload})
        return enriched

    @staticmethod
    def _spec_is_destructive(spec: dict[str, Any]) -> bool:
        """Treat remediations as destructive when they can discard or overwrite local work."""
        return bool(spec.get("destructive", spec["actionType"] in DESTRUCTIVE_REMEDIATION_ACTION_TYPES))

    def ensure_worker_remediation(
        self,
        *,
        thread_id: str,
        worker_status: dict[str, Any],
        summary: bool = False,
    ) -> list[dict[str, Any]]:
        """Persist ``run_worker_once`` when a queued thread has no active worker processing it."""
        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        if thread["status"] != "queued" or bool(worker_status.get("running")):
            self.connection.execute(
                "UPDATE remediation_actions SET status = 'resolved', resolved_at = ? "
                "WHERE thread_id = ? AND stage = 'worker' "
                "AND blocker_type = 'worker_not_running' "
                "AND action_type = 'run_worker_once' AND status = 'pending'",
                (utc_now(), thread_id),
            )
            return self.repository.list_for_thread(thread_id, summary=summary)
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
        return self.repository.list_for_thread(thread_id, summary=summary)

    def list_for_thread(
        self,
        *,
        thread_id: str,
        worker_status: dict[str, Any] | None = None,
        summary: bool = False,
    ) -> list[dict[str, Any]]:
        """List remediations, materializing worker recovery and blocked-thread backfill on demand."""
        from local_control_center.executions.timeout_reconciliation import reconcile_thread_timeout

        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        reconcile_thread_timeout(self.connection, thread_id=thread_id, project_id=thread["projectId"])
        self._materialize_runtime_risk_review(thread_id)
        self.repository.resolve_pending_from_terminal_loops(thread_id, include_actions=not summary)
        self.repository.resolve_pending_from_terminal_jobs(thread_id, include_actions=not summary)
        self.repository.dismiss_pending_from_superseded_loops(thread_id, include_actions=not summary)
        if worker_status is not None:
            actions = self.ensure_worker_remediation(
                thread_id=thread_id, worker_status=worker_status, summary=summary
            )
        else:
            actions = self.repository.list_for_thread(thread_id, summary=summary)
        actions = self._refresh_resource_selection_remediations(
            thread_id=thread_id, actions=actions, summary=summary
        )
        actions = self.ensure_awaiting_user_remediations(
            thread_id=thread_id, existing=actions, summary=summary
        )
        return self._research_actions(
            self.ensure_blocked_thread_remediation(thread_id=thread_id, existing=actions, summary=summary)
        )

    def _materialize_runtime_risk_review(self, thread_id: str) -> None:
        if self.root is None:
            return
        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        loop = self._latest_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
        if not loop:
            return
        durable = loop["context"].get("durableRun") or {}
        if (
            loop["state"] == "blocked"
            and durable.get("blockedStage") == "resource_manager"
            and (durable.get("resource_manager") or {}).get("runtimeRiskReviewInvalid")
        ):
            return
        if (durable.get("runtimeRiskReview") or {}).get("status") in {"queued", "consuming", "consumed"}:
            self.connection.execute(
                "UPDATE remediation_actions SET status='dismissed',resolved_at=? WHERE loop_id=? "
                "AND blocker_type='runtime_risk_review_required' AND action_type='open_settings_section' "
                "AND status='pending'",
                (utc_now(), loop["id"]),
            )
            self._materialize_approved_runtime_failure(thread_id, loop, durable["runtimeRiskReview"])
            return
        if loop["state"] != "blocked":
            return
        if durable.get("blockedStage") != "resource_manager" or not runtime_risk_review_failure(
            durable.get("resource_manager") or {}
        ):
            return
        from local_control_center.product_loop.runtime_risk_review import ensure_runtime_risk_review

        try:
            ensure_runtime_risk_review(self.connection, loop_id=loop["id"], root=self.root)
        except (KeyError, ValueError, TypeError, OSError) as error:
            card = self.repository.create_action(
                project_id=loop["projectId"],
                thread_id=thread_id,
                loop_id=loop["id"],
                stage="resource_manager",
                blocker_type="runtime_risk_review_required",
                action_type="open_settings_section",
                title="Runtime risk proposal requires review",
                description=str(error),
                technical_reason=str(error),
                primary=True,
                payload={"section": "routing", "reason": str(error), "loopId": loop["id"]},
            )
            self.connection.execute(
                "UPDATE remediation_actions SET status='dismissed',resolved_at=? WHERE loop_id=? AND stage='resource_manager' AND status='pending' AND id<>?",
                (utc_now(), loop["id"], card["id"]),
            )

    def _materialize_approved_runtime_failure(self, thread_id: str, loop: dict, review: dict) -> None:
        if review.get("status") != "consuming" or loop["state"] not in {
            "workspace_check",
            "git_check",
            "discovery",
            "blocked",
        }:
            return
        try:
            job = JobsRepository(self.connection).get_job(str(review.get("continuationJobId") or ""))
        except KeyError:
            return
        marker = (job["payload"].get("runMetadata") or {}).get("runtimeRiskContinuation")
        if (
            job["status"] != "failed"
            or job["kind"] != "thread.product_loop.run"
            or job["projectId"] != loop["projectId"]
            or job["payload"].get("threadId") != thread_id
            or marker != {"loopId": loop["id"], "reviewId": review.get("id")}
        ):
            return
        failure = self.connection.execute(
            "SELECT payload FROM thread_agent_events WHERE thread_id=? AND type='worker_failed' "
            "AND json_extract(payload,'$.jobId')=? ORDER BY sequence DESC LIMIT 1",
            (thread_id, job["id"]),
        ).fetchone()
        reason = str(
            redact_secrets(
                json_loads(failure["payload"]).get("reason")
                if failure
                else "The approved continuation stopped before completing runtime selection."
            )
        )
        self.repository.create_action(
            project_id=loop["projectId"],
            thread_id=thread_id,
            loop_id=loop["id"],
            stage="worker",
            blocker_type="runtime_execution_failed",
            action_type="run_worker_once",
            title="Resume approved continuation",
            description=reason,
            technical_reason=reason,
            primary=True,
            payload={"jobId": job["id"], "loopId": loop["id"], "reviewId": review["id"]},
        )

    def _refresh_resource_selection_remediations(
        self, *, thread_id: str, actions: list[dict[str, Any]], summary: bool = False
    ) -> list[dict[str, Any]]:
        """Replace legacy diagnoses only for a specific persisted selection failure."""
        changed = False
        for action in actions:
            if (
                action.get("status") != "pending"
                or action.get("stage") != "resource_manager"
                or action.get("actionType")
                in {"answer_question", "approve_resource_decision", "continue_plan_only"}
            ):
                continue
            payload = action.get("payload") or {}
            if summary and (
                action.get("blockerType") == "decision_engine_unavailable"
                or (
                    action.get("blockerType") == "resource_manager_unconfigured"
                    and payload.get("section") == "routing"
                )
            ):
                continue
            details = payload.get("details")
            if summary and "detailsEvidence" in payload:
                thread = ThreadsRepository(self.connection).get_thread(thread_id)
                loop = self._latest_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
                if (
                    loop is None
                    or loop["id"] != action.get("loopId")
                    or loop["state"] != "blocked"
                    or action.get("projectId") != thread["projectId"]
                ):
                    continue
                details = (self.repository.get(action["id"]).get("payload") or {}).get("details")
            if not isinstance(details, dict):
                continue
            failure = decision_engine_failure(details)
            corrected_type = "decision_engine_unavailable"
            if failure is None:
                failure = resource_selection_constraint_failure(details)
                corrected_type = "resource_manager_unconfigured"
            wrong_policy_settings_section = (
                corrected_type == "resource_manager_unconfigured"
                and action.get("actionType") == "open_settings_section"
                and payload.get("section") != "routing"
            )
            if failure is None or (
                action.get("blockerType") == corrected_type and not wrong_policy_settings_section
            ):
                continue
            with immediate_transaction(self.connection):
                thread = ThreadsRepository(self.connection).get_thread(thread_id)
                loop = self._latest_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
                if (
                    loop is None
                    or loop["id"] != action.get("loopId")
                    or loop["state"] != "blocked"
                    or action.get("projectId") != thread["projectId"]
                    or self.repository.get(action["id"])["status"] != "pending"
                ):
                    continue
                self.create_for_blocked_run(
                    project_id=thread["projectId"],
                    thread_id=thread_id,
                    loop_id=loop["id"],
                    stage="resource_manager",
                    reason=failure.get("decisionReason")
                    or action.get("technicalReason")
                    or payload.get("reason")
                    or "AI resource selection blocked.",
                    details=details,
                )
                self.repository.mark_status_in_transaction(action["id"], "dismissed")
                changed = True
        return self.repository.list_for_thread(thread_id, summary=summary) if changed else actions

    def ensure_awaiting_user_remediations(
        self,
        *,
        thread_id: str,
        existing: list[dict[str, Any]] | None = None,
        summary: bool = False,
    ) -> list[dict[str, Any]]:
        """Backfill one actionable remediation per still-pending ProductOwner decision."""
        actions = (
            existing if existing is not None else self.repository.list_for_thread(thread_id, summary=summary)
        )
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
        return self.repository.list_for_thread(thread_id, summary=summary)

    def _awaiting_user_loop_for_thread(
        self,
        *,
        thread_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        coordinator = self._coordinator()
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
        summary: bool = False,
    ) -> list[dict[str, Any]]:
        """Backfill: un hilo bloqueado sin acciones pendientes siempre recibe una salida ejecutable.

        Cubre bloqueos previos a la creación automática de remediaciones (o cuyas filas se
        perdieron): reconstruye las tarjetas específicas del blocker desde el estado durable del
        loop y garantiza un ``retry_loop`` real, para que el operador nunca quede frente a un
        bloqueo sin acción posible.
        """
        actions = (
            existing if existing is not None else self.repository.list_for_thread(thread_id, summary=summary)
        )
        thread = ThreadsRepository(self.connection).get_thread(thread_id)
        if thread["status"] not in {"blocked", "waiting_decision"}:
            return actions
        loop = self._latest_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
        if loop is None:
            return actions
        if any(
            action.get("status") == "pending" and action.get("loopId") == loop["id"] for action in actions
        ):
            return actions
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        stage = str(durable.get("blockedStage") or "").strip() or "worker"
        reason = str(
            durable.get("blockedReason")
            or "The Product Loop is blocked and its stored remediation actions are missing."
        )
        details = backfill_details_from_durable(durable)
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
        return self.repository.list_for_thread(thread_id, summary=summary)

    def _latest_loop_for_thread(self, *, thread_id: str, project_id: str) -> dict[str, Any] | None:
        """Backfill only the current run, including a thread/loop state divergence."""
        from local_control_center.product_loop.repository import ProductLoopRepository

        loop_id = self.repository.latest_loop_id_for_thread(project_id=project_id, thread_id=thread_id)
        return ProductLoopRepository(self.connection).get_loop(loop_id) if loop_id else None

    def dismiss(self, action_id: str) -> dict[str, Any]:
        """Dismiss one pending remediation action."""
        return self.repository.mark_status(action_id, "dismissed")

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
        self._research_actions([action])
        self._refresh_resource_selection_remediations(thread_id=action["threadId"], actions=[action])
        action = self.repository.get(action_id)
        if action["loopId"]:
            self.repository.dismiss_pending_from_superseded_loops(action["threadId"])
        else:
            self.repository.resolve_pending_from_terminal_jobs(action["threadId"])
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
        if action_type == "approve_runtime_risk":
            from local_control_center.jobs_approvals.commands import approve_action

            stored = action["payload"]
            result = approve_action(
                JobsRepository(self.connection),
                str(stored.get("jobId") or ""),
                str(stored.get("actionRequestId") or ""),
                {"reason": (payload or {}).get("reason")},
            )
            return {"remediation": self.repository.get(action_id), "execution": result["execution"]}
        execution = self._dispatch_action(
            action, payload=payload or {}, execution_payload=execution_payload, platform=platform
        )

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

    def _dispatch_action(
        self,
        action: dict[str, Any],
        *,
        payload: dict[str, Any],
        execution_payload: dict[str, Any],
        platform: Any,
    ) -> dict[str, Any]:
        """Resolve the handler for ``action['actionType']`` at call time so it stays patchable."""
        action_type = action["actionType"]
        handler_name = ACTION_HANDLERS.get(action_type)
        if handler_name is None:
            return {
                "status": "blocked",
                "action": action_type,
                "reason": "Unsupported remediation action.",
            }
        return getattr(self, handler_name)(
            action, payload=payload, execution_payload=execution_payload, platform=platform
        )

    def _handle_open_settings_section(self, action, *, payload, execution_payload, platform):
        """Report the settings section the client should open."""
        return {
            "status": "completed",
            "action": action["actionType"],
            "section": execution_payload.get("section")
            or execution_payload.get("settingsSection")
            or "runtime",
        }

    def _handle_validate_runtime(self, action, *, payload, execution_payload, platform):
        """Validate the requested runtime against the current provider statuses."""
        project_id = str(execution_payload.get("projectId") or action.get("projectId") or "").strip() or None
        providers = RuntimeStatusService(self.connection).list_provider_statuses(project_id=project_id)
        return validate_runtime_execution(
            runtime_id=str(execution_payload.get("runtimeId") or "").strip(),
            providers=providers,
            product_owner=action.get("stage") in {"product_owner", "product_owner_runtime"},
        )

    def _handle_switch_runtime(self, action, *, payload, execution_payload, platform):
        """Switch the default runtime to an executable target."""
        action_type = action["actionType"]
        runtime_id = str(execution_payload.get("runtimeId") or "").strip()
        if not runtime_id:
            return {
                "status": "blocked",
                "action": action_type,
                "reason": "runtimeId is required to switch runtime.",
            }
        project_id = str(execution_payload.get("projectId") or action.get("projectId") or "").strip() or None
        providers = RuntimeStatusService(self.connection).list_provider_statuses(project_id=project_id)
        target = runtime_status_for(providers, runtime_id)
        if target is None:
            return {
                "status": "blocked",
                "action": action_type,
                "runtimeId": runtime_id,
                "providers": redact_secrets(providers),
                "reason": f"Runtime {runtime_id} is not available in runtime status.",
            }
        if target.get("executable") is not True:
            return {
                "status": "blocked",
                "action": action_type,
                "runtimeId": runtime_id,
                "target": redact_secrets(target),
                "providers": redact_secrets(providers),
                "reason": str(
                    target.get("reason") or f"Runtime {runtime_id} is not executable and cannot be selected."
                ),
            }
        preferences = self._set_default_runtime(str(target.get("id") or runtime_id))
        return {
            "status": "completed",
            "action": action_type,
            "runtimeId": str(target.get("id") or runtime_id),
            "target": redact_secrets(target),
            "preferences": preferences,
            "reason": "Default runtime preference updated to an executable runtime.",
        }

    def _handle_run_worker_once(self, action, *, payload, execution_payload, platform):
        """Request one bounded worker batch."""
        return self._request_worker_batch(execution_payload)

    def _handle_check_network_access(self, action, *, payload, execution_payload, platform):
        """Probe network access for the action's project."""
        return self._check_network_access(
            project_id=str(execution_payload.get("projectId") or action.get("projectId") or "").strip()
            or None
        )

    def _handle_git_action(self, action, *, payload, execution_payload, platform):
        """Delegate every Git remediation to ``_execute_git_action``."""
        return self._execute_git_action(
            action["actionType"], action=action, payload=execution_payload, platform=platform
        )

    def _handle_save_patch(self, action, *, payload, execution_payload, platform):
        """Persist the workspace diff as a patch artifact."""
        return self._save_patch(action=action, payload=execution_payload, platform=platform)

    def _handle_retry_loop(self, action, *, payload, execution_payload, platform):
        """Retry the blocked Product Loop with the caller-supplied payload only."""
        return self._retry_loop(action=action, payload=payload)

    def _handle_answer_question(self, action, *, payload, execution_payload, platform):
        """Record the answer to a pending thread question."""
        return self._answer_question(action=action, payload=execution_payload)

    def _handle_approve_resource_decision(self, action, *, payload, execution_payload, platform):
        """Approve the pending resource decision."""
        return self._approve_resource_decision(action=action, payload=execution_payload)

    def _handle_continue_plan_only(self, action, *, payload, execution_payload, platform):
        """Continue the loop in plan-only mode."""
        return self._continue_plan_only(action=action)

    def _handle_revalidate_runtime(self, action, *, payload, execution_payload, platform):
        """Revalidate the runtime whose evidence went stale."""
        return self._revalidate_runtime(action=action, platform=platform)

    def resolve_thread_decision(
        self,
        *,
        thread_id: str,
        decision_id: str,
        resolution: str,
        decided_by: str | None,
        platform: Any,
        selected_options: list[str] | None = None,
        free_text: str | None = None,
        suppress_message: bool = False,
    ) -> dict[str, Any]:
        """Resolve a thread decision through its durable remediation when one exists.

        ``selected_options``/``free_text`` carry the structured answer through to
        ``_answer_question`` (Product Owner decisions accept several options and free text even
        with options offered); omit them (default ``None``) for the legacy single-``resolution``
        path. ``suppress_message`` skips the "user answered" chat message this call would otherwise
        append — the batch endpoint uses it to append one combined message for the whole batch.
        """
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
        answer_payload: dict[str, Any] = {
            "threadId": thread_id,
            "decisionId": decision_id,
            "resolution": resolution,
            "decidedBy": decided_by or "user",
        }
        if selected_options is not None:
            answer_payload["selectedOptions"] = selected_options
        if free_text is not None:
            answer_payload["freeText"] = free_text
        if suppress_message:
            answer_payload["suppressMessage"] = True
        pending_actions = [action for action in matching_actions if action.get("status") == "pending"]
        if pending_actions:
            outcome = self.execute(
                pending_actions[-1]["id"],
                platform=platform,
                payload=answer_payload,
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
                payload=answer_payload,
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
            append_message=not suppress_message,
        )

    def _decision_requires_product_loop_remediation(
        self,
        *,
        thread_id: str,
        project_id: str,
        decision_id: str,
    ) -> bool:
        for loop in self._coordinator().list_loops(project_id):
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

    def _request_worker_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Solicita un batch durable al worker separado sin ejecutarlo desde la API."""
        retry = self._retry_failed_worker_job_if_needed(payload=payload)
        if retry["status"] == "blocked":
            return retry
        if retry["status"] == "completed":
            return {"status": "completed", "action": "run_worker_once", "jobRetry": retry}
        control = WorkerControlRepository(self.connection).request_run_once(
            reason="Worker batch requested from remediation.", requested_by="operator"
        )
        return {
            "status": "queued",
            "action": "run_worker_once",
            "control": control,
            "jobRetry": retry,
            "reason": "Waiting for the separate worker to claim a bounded batch.",
        }

    def _should_resolve(self, *, action_type: str, execution: dict[str, Any]) -> bool:
        return should_resolve(action_type=action_type, execution=execution)

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

    def _check_network_access(self, *, project_id: str | None) -> dict[str, Any]:
        """Probe the web-search provider ResearchAgent is configured to use (SearXNG or DuckDuckGo)."""
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        from local_control_center.research.web_search import SEARCH_USER_AGENT, configured_search_probe_url

        try:
            endpoint = configured_search_probe_url(self.connection, project_id=project_id)
        except ValueError as error:
            return {
                "status": "blocked",
                "action": "check_network_access",
                "reason": f"Research web-search provider is misconfigured: {error}",
            }
        request = Request(endpoint, headers={"User-Agent": SEARCH_USER_AGENT})
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

    @staticmethod
    def _approved_resource_selections_from_durable(durable: dict[str, Any]) -> list[dict[str, Any]]:
        return approved_resource_selections_from_durable(durable)

    def _execute_git_action(
        self,
        action_type: str,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
        platform: Any,
    ) -> dict[str, Any]:
        if action_type == "view_diff" and action["payload"].get("interruptedExecutionId"):
            return {"action": action_type, **self._view_interrupted_workspace(action)}
        handler_name = GIT_ACTION_HANDLERS.get(action_type)
        if handler_name is None:
            return {"status": "blocked", "reason": f"Unsupported git remediation: {action_type}."}
        project_id = str(payload.get("projectId") or action["projectId"])
        service = GitWorkspaceService(self.connection, root=Path(getattr(platform, "cwd", self.root or ".")))
        return getattr(self, handler_name)(
            action_type, action=action, payload=payload, project_id=project_id, service=service
        )

    def _git_init(self, action_type, *, action, payload, project_id, service):
        """Initialise the project repository."""
        return {"action": action_type, **service.init_repository(project_id)}

    def _git_add_remote(self, action_type, *, action, payload, project_id, service):
        """Add a named remote when both name and url are supplied."""
        name = str(payload.get("name") or payload.get("remoteName") or "").strip()
        url = str(payload.get("url") or payload.get("remoteUrl") or "").strip()
        if not name or not url:
            return {
                "status": "blocked",
                "action": action_type,
                "reason": "name and url are required to add a Git remote.",
            }
        return {"action": action_type, **service.add_remote(project_id, name=name, url=url)}

    def _git_create_branch(self, action_type, *, action, payload, project_id, service):
        """Create a branch, defaulting to a remediation-scoped name."""
        branch = str(
            payload.get("branchName") or payload.get("branch") or f"codex/remediation-{action['id'][-8:]}"
        )
        return {
            "action": action_type,
            **service.create_branch(project_id, name=branch, base=payload.get("base")),
        }

    def _git_checkout_branch(self, action_type, *, action, payload, project_id, service):
        """Check out an explicit branch."""
        branch = str(payload.get("branchName") or payload.get("branch") or "")
        if not branch:
            return {"status": "blocked", "reason": "branchName is required for checkout_branch."}
        result = service.checkout(
            project_id, branch=branch, allow_dirty=bool(payload.get("allowDirty", False))
        )
        return {"action": action_type, **result}

    def _git_view_diff(self, action_type, *, action, payload, project_id, service):
        """Return the workspace diff."""
        return {"action": action_type, **service.diff(project_id)}

    def _git_run_gitleaks(self, action_type, *, action, payload, project_id, service):
        """Run gitleaks, resuming a gitleaks-blocked loop when the scan is clean."""
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
            return {"action": action_type, **result}
        return self._retry_gitleaks_approval(
            action=action,
            coordinator=recovery["coordinator"],
            loop=recovery["loop"],
            durable=recovery["durable"],
            thread_id=recovery["threadId"],
            threads=recovery["threads"],
            gitleaks=result,
        )

    def _view_interrupted_workspace(self, action: dict[str, Any]) -> dict[str, Any]:
        """Read partial changes from the persisted loop workspace, never request-body paths."""
        from local_control_center.workspaces_projects.git_worktrees import run_brokered_git

        project_id, thread_id = action["projectId"], action["threadId"]
        workspace_id = str(action["payload"].get("workspaceId") or "")
        loop = self._latest_loop_for_thread(thread_id=thread_id, project_id=project_id)
        workspace = self.connection.execute(
            "SELECT path FROM workspaces WHERE id=? AND project_id=? AND archived_at IS NULL",
            (workspace_id, project_id),
        ).fetchone()
        if (
            not loop
            or loop["id"] != action["loopId"]
            or loop["projectId"] != project_id
            or loop["state"] != "blocked"
            or (loop["context"].get("durableRun") or {}).get("workspaceId") != workspace_id
            or not workspace
        ):
            return {"status": "blocked", "reason": "The interrupted workspace no longer matches this run."}
        path = Path(workspace["path"]).resolve(strict=False)
        if not path.is_dir():
            return {"status": "blocked", "reason": "The preserved workspace is unavailable."}
        results = []
        for args in (
            ["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"],
            ["status", "--porcelain=v1", "--untracked-files=all", "-z"],
        ):
            result = run_brokered_git(
                connection=self.connection,
                root=self.root or Path.cwd(),
                project_id=project_id,
                workspace_id=workspace_id,
                workspace_path=path,
                cwd=path,
                args=args,
                task_id=f"{action['id']}.{args[0]}",
            )
            if result.get("returnCode") != 0:
                return {"status": "blocked", "reason": result.get("reason") or "Workspace diff unavailable."}
            results.append(result)
        untracked_files = []
        entries = iter(results[1]["stdout"].split("\0"))
        for entry in entries:
            status = entry[:2]
            if status == "??":
                untracked_files.append(entry[3:])
            elif "R" in status or "C" in status:
                next(entries, None)
        return {
            "status": "completed",
            "partialExecution": True,
            "projectId": project_id,
            "workspaceId": workspace_id,
            "workspacePath": str(path),
            "diff": results[0]["stdout"],
            "untrackedFiles": untracked_files,
            "reason": "Tracked changes collected; untracked files are listed separately. Execution remains stopped.",
        }

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

    def _reseal_operator_cost_decision(
        self,
        metadata: dict[str, Any],
        *,
        project_id: str,
        source_message: dict[str, Any] | None,
        refresh_global_team: bool = False,
    ) -> dict[str, Any]:
        """Re-sella teamMode/privacyLevel de un retry con la configuración vigente del proyecto.

        ``refresh_global_team`` recalcula el equipo de IA global (solo el retry explícito del operador,
        que es como aplica un cambio de equipo); los demás re-sellados conservan el snapshot del run para
        que la revisión de riesgo y las continuaciones vean exactamente la misma metadata.

        Los valores sellados en el requestMeta original son indistinguibles de una elección del
        llamador, así que la intención se restaura desde la metadata del mensaje fuente (que nunca
        se sella) y el re-sellado aplica la config actual (p. ej. un forceLocal ya apagado).
        """
        resealed = dict(metadata)
        source_meta = source_message.get("metadata") if isinstance(source_message, dict) else None
        caller_meta = source_meta if isinstance(source_meta, dict) else {}
        for key in ("teamMode", "privacyLevel"):
            caller_value = str(caller_meta.get(key) or "").strip()
            if caller_value:
                resealed[key] = caller_value
            else:
                resealed.pop(key, None)
        return seal_operator_cost_decision(
            self.connection,
            project_id=project_id,
            metadata=resealed,
            refresh_global_team=refresh_global_team,
            thread_id=str(caller_meta.get("threadId") or "")
            or (str(source_message.get("threadId") or "") if isinstance(source_message, dict) else "")
            or None,
        )

    def _revalidate_runtime(self, *, action: dict[str, Any], platform: Any) -> dict[str, Any]:
        return runtime_recovery.revalidate_runtime(self, action=action, platform=platform)

    def _stale_validation_targets(self, stored: dict[str, Any]) -> list[tuple[str, str | None]]:
        return runtime_recovery.stale_validation_targets(self, stored)

    def resume_after_runtime_validation(self, provider_id: str) -> list[dict[str, Any]]:
        """Re-ejecuta las re-pruebas que el operador lanzó para ``provider_id`` y ya tienen todo validado."""
        return runtime_recovery.resume_after_runtime_validation(self, provider_id)

    def resume_cooldown_expired_blocks(self) -> list[dict[str, Any]]:
        """Reanuda automáticamente los bloqueos de recursos cuyo cooldown de cuota ya cedió."""
        return runtime_recovery.resume_cooldown_expired_blocks(self)

    def _retry_loop(self, *, action: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return retry_actions.retry_loop(self, action=action, payload=payload)

    def _blocked_gitleaks_context(
        self,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        return retry_actions.blocked_gitleaks_context(self, action=action, payload=payload)

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
        return retry_actions.retry_gitleaks_approval(
            self,
            action=action,
            coordinator=coordinator,
            loop=loop,
            durable=durable,
            thread_id=thread_id,
            threads=threads,
            gitleaks=gitleaks,
        )

    def _continue_plan_only(self, *, action: dict[str, Any]) -> dict[str, Any]:
        return retry_actions.continue_plan_only(self, action=action)

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
        new_approvals = resource_approvals_from_payload(action=action, payload=action_payload)
        if not new_approvals:
            return {
                "status": "blocked",
                "action": "approve_resource_decision",
                "reason": "A selected AI resource is required before approval.",
            }
        coordinator = self._coordinator()
        transaction = (
            nullcontext(self.connection)
            if self.connection.in_transaction
            else immediate_transaction(self.connection)
        )
        with transaction:
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
                and not any(same_resource_approval(item, approval) for approval in new_approvals)
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

    def _answer_question(
        self,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
        allow_dismissed: bool = False,
    ) -> dict[str, Any]:
        return question_answers.answer_question(
            self, action=action, payload=payload, allow_dismissed=allow_dismissed
        )

    def _remaining_product_owner_decision_ids(
        self,
        *,
        action: dict[str, Any],
        current_decision_id: str,
        threads: ThreadsRepository,
    ) -> list[str]:
        return question_answers.remaining_product_owner_decision_ids(
            self, action=action, current_decision_id=current_decision_id, threads=threads
        )

    def validate_thread_decision_answer(
        self,
        *,
        thread_id: str,
        decision_id: str,
        selected_options: list[str],
        free_text: str | None,
    ) -> None:
        """Comprueba, sin mutar nada, que ``resolve_thread_decision`` aceptaría esta respuesta."""
        return question_answers.validate_thread_decision_answer(
            self,
            thread_id=thread_id,
            decision_id=decision_id,
            selected_options=selected_options,
            free_text=free_text,
        )

    def _record_clarification_answer(
        self,
        *,
        decision: dict[str, Any],
        payload: dict[str, Any],
        answer: str,
    ) -> dict[str, Any]:
        return question_answers.record_clarification_answer(
            self, decision=decision, payload=payload, answer=answer
        )

    def _set_default_runtime(self, runtime_id: str) -> dict[str, Any]:
        return runtime_recovery.set_default_runtime(self, runtime_id)

    def _blocker_type(self, *, stage: str, reason: str, details: dict[str, Any]) -> str:
        return blocker_type(stage=stage, reason=reason, details=details)

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
            if not name or not url or not remote_url_payload_is_safe(url):
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
            local_account=self._local_profile_account(blocker_runtime_id(details)),
        )
        resource_approval_spec = approve_resource_decision_spec(details)
        builder = PAYLOAD_BUILDERS.get(blocker_type)
        if uses_local_runtime_specs(blocker_type, context.local_runtime_payload):
            specs = local_runtime_specs(
                setup=context.local_runtime_payload,
                runtime_settings_payload=context.runtime_settings_payload,
                runtime_recovery_payload=context.runtime_recovery_payload,
            )
        elif builder is not None:
            specs = builder(context)
        else:
            specs = [
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": reason or "Retry after resolving the blocker.",
                    "payload": {},
                }
            ]
        if blocker_type.startswith("resource_manager_") and resource_approval_spec:
            return [resource_approval_spec, *specs]
        return specs

    def _local_profile_account(self, runtime_id: str) -> dict[str, Any] | None:
        """Cuenta del runtime local con perfil de catálogo; ``None`` para Ollama, CLI, API o ids desconocidos."""
        if not runtime_id:
            return None
        try:
            account = ProviderAccountStore(self.connection).get_provider_account(runtime_id)
        except KeyError:
            return None
        return account if local_profile_entry(account) is not None else None
