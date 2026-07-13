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

from local_control_center.agents.runtime_provider_config import (
    DEFAULT_OLLAMA_BASE_URL,
    RUNTIME_PROVIDER_CONFIG_SPECS,
    known_provider_default_base_url,
)
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository, is_ollama_runtime_id
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
OLLAMA_REMOTE_PROVIDER_IDS = frozenset({"ollama_remote"})


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
        if worker_status is not None:
            actions = self.ensure_worker_remediation(thread_id=thread_id, worker_status=worker_status)
        else:
            actions = self.repository.list_for_thread(thread_id)
        if any(action.get("status") == "pending" for action in actions):
            return actions
        return self.ensure_blocked_thread_remediation(thread_id=thread_id, existing=actions)

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
        loop = self._blocked_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
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
                "section": execution_payload.get("section") or execution_payload.get("settingsSection") or "runtime",
            }
        elif action_type == "validate_runtime":
            project_id = str(execution_payload.get("projectId") or action.get("projectId") or "").strip() or None
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
                project_id = str(execution_payload.get("projectId") or action.get("projectId") or "").strip() or None
                providers = RuntimeStatusService(self.connection).list_provider_statuses(project_id=project_id)
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
                execution = {"status": "blocked", "action": action_type, "reason": "Local worker runtime is unavailable."}
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
            execution = self._execute_git_action(action_type, action=action, payload=execution_payload, platform=platform)
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
            execution = {"status": "blocked", "action": action_type, "reason": "Unsupported remediation action."}

        if self._should_resolve(action_type=action_type, execution=execution):
            remediation = self.repository.mark_status(action_id, "resolved")
        else:
            remediation = self.repository.get(action_id)
        return {"remediation": remediation, "execution": redact_secrets(execution)}

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

    def _validate_runtime_execution(self, *, runtime_id: str, providers: list[dict[str, Any]]) -> dict[str, Any]:
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
        executable_provider = next((provider for provider in providers if provider.get("executable") is True), None)
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
            durable.get("resourceApproval")
            if isinstance(durable.get("resourceApproval"), dict)
            else {}
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
            branch = str(payload.get("branchName") or payload.get("branch") or f"codex/remediation-{action['id'][-8:]}")
            return with_action(service.create_branch(project_id, name=branch, base=payload.get("base")))
        if action_type == "checkout_branch":
            branch = str(payload.get("branchName") or payload.get("branch") or "")
            if not branch:
                return {"status": "blocked", "reason": "branchName is required for checkout_branch."}
            return with_action(service.checkout(project_id, branch=branch, allow_dirty=bool(payload.get("allowDirty", False))))
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

    def _save_patch(self, *, action: dict[str, Any], payload: dict[str, Any], platform: Any) -> dict[str, Any]:
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
        artifact_file = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".patch", content=patch)
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
    def _resolved_similarity_decision_mode(
        *, threads: ThreadsRepository, decision_id: str
    ) -> str | None:
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
        loop = coordinator.get(loop_id)
        if loop["state"] != "blocked":
            return {"status": "blocked", "action": "retry_loop", "reason": "Product Loop is not blocked."}

        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        remediation_stage = str(action.get("stage") or "").strip()
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
        thread_id = str(action.get("threadId") or thread_ref.get("projectThreadId") or "").strip()
        if not thread_id:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Blocked Product Loop does not reference a project thread.",
            }

        threads = ThreadsRepository(self.connection)
        thread = threads.get_thread(thread_id)
        if thread["projectId"] != loop["projectId"]:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Thread project does not match the blocked Product Loop project.",
            }
        if thread["status"] in {"queued", "running"}:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": f"Thread is already {thread['status']}; retry would create concurrent execution.",
            }
        if durable.get("blockedStage") == "approval":
            return self._retry_delivery_approval(
                action=action,
                coordinator=coordinator,
                loop=loop,
                durable=durable,
                thread_id=thread_id,
                threads=threads,
            )
        if durable.get("blockedStage") == "resource_learning" and self._has_delivery_resource_learning_evidence(
            durable
        ):
            return self._retry_resource_learning_approval(
                action=action,
                coordinator=coordinator,
                loop=loop,
                durable=durable,
                thread_id=thread_id,
                threads=threads,
            )

        functionality_decision_mode: str | None = None
        if durable.get("blockedStage") == "functionality_memory":
            functionality_decision_mode = self._resolved_similarity_decision_mode(
                threads=threads, decision_id=str(durable.get("decisionId") or "")
            )
            if functionality_decision_mode is None:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": "Resolve the existing-functionality decision on this thread before retrying the loop.",
                    "decisionId": durable.get("decisionId"),
                }

        message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
        message = str(durable.get("message") or "").strip()
        source_message = self._source_retry_message(threads=threads, thread_id=thread_id, message_id=message_id)
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

        plan_only = bool(durable.get("planOnly") or request_meta.get("planOnly") or request_meta.get("plan_only"))
        approved_resource_selections = self._approved_resource_selections_from_durable(durable)
        retry_metadata = {
            **request_meta,
            "messageId": message_id,
            "planOnly": plan_only,
            "approvedResourceSelections": approved_resource_selections,
            "retryOfLoopId": loop_id,
            "retryStage": durable.get("blockedStage"),
            "retryReason": durable.get("blockedReason"),
            "remediationActionId": action["id"],
        }
        if functionality_decision_mode:
            # Sin la elección resuelta el rerun volvería a bloquearse en el gate de memoria de
            # funcionalidad con el mismo mensaje, en un ciclo sin salida para el operador.
            retry_metadata["functionalityDecision"] = functionality_decision_mode
        jobs = JobsRepository(self.connection)
        existing_job = self._existing_retry_job(
            jobs=jobs,
            project_id=loop["projectId"],
            loop_id=loop_id,
            action_id=action["id"],
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
                    "remediationActionId": action["id"],
                    "retryQueuedAt": queued_at,
                },
                idempotency_key=f"product-loop-retry:{loop_id}:{action['id']}",
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
            "remediationActionId": action["id"],
            "queuedAt": queued_at,
        }
        durable = {
            **durable,
            "status": "retry_queued",
            "retry": retry_ref,
            "updatedAt": queued_at,
        }
        superseded = coordinator.transition(
            loop_id,
            to_state="cancelled",
            reason="Blocked Product Loop superseded by a queued retry job.",
            actor="remediation",
            trigger="retry_loop",
            context_patch={"durableRun": durable},
            metadata={"remediationActionId": action["id"], "retryJobId": retry_job["id"]},
        )
        thread, thread_status_update = self._mark_thread_run_queued_best_effort(
            threads=threads,
            thread_id=thread_id,
            event_payload={
                "jobId": retry_job["id"],
                "messageId": message_id,
                "status": retry_job["status"],
                "retryOfLoopId": loop_id,
                "remediationActionId": action["id"],
            },
        )
        return {
            "status": "queued",
            "action": "retry_loop",
            "reason": "Queued a real Product Loop retry job.",
            "job": retry_job,
            "loop": superseded,
            "thread": thread,
            "threadStatusUpdate": thread_status_update,
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
        review_ready = coordinator.transition(
            loop["id"],
            to_state="review_ready",
            reason="Delivery approval was recreated from existing QA, gitleaks, and diff evidence.",
            actor="remediation",
            trigger="retry_delivery_approval",
            context_patch={"durableRun": review_durable},
            metadata={"remediationActionId": action["id"], "approvalJobId": approval_job["id"]},
        )
        awaiting_durable = {
            **dict(review_ready["context"].get("durableRun") or {}),
            "status": "awaiting_approval",
            "approval": next_approval,
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
                payload={"loopId": awaiting["id"], **next_approval, "remediationActionId": action["id"]},
            )
        return {
            "status": "awaiting_approval",
            "action": "retry_loop",
            "reason": "Recreated delivery approval from existing QA, gitleaks, and diff evidence.",
            "job": approval_job,
            "approval": next_approval,
            "actionRequest": approval_action,
            "loop": awaiting,
            "thread": threads.get_thread(thread_id),
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
        thread_id = str(payload.get("threadId") or action.get("threadId") or thread_ref.get("projectThreadId") or "").strip()
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
        qa_results = runtime_result.get("qaResults") if isinstance(runtime_result.get("qaResults"), list) else []
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

        evidence_refs = [
            str(item)
            for item in (durable.get("evidencePackageIds") or [])
            if str(item).strip()
        ]
        if security_evidence["id"] not in evidence_refs:
            evidence_refs.append(security_evidence["id"])
        changed_files = [
            str(item)
            for item in (review.get("changedFiles") or [])
            if str(item).strip()
        ]
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
            durable.get("resource_learning")
            if isinstance(durable.get("resource_learning"), dict)
            else {}
        )
        review = durable.get("review") if isinstance(durable.get("review"), dict) else resource_details.get("review")
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
        from local_control_center.product_loop.coordinator import _diff_ref_from_review

        resource_details = (
            durable.get("resource_learning")
            if isinstance(durable.get("resource_learning"), dict)
            else {}
        )
        failed_learning = (
            durable.get("resourceLearning") if isinstance(durable.get("resourceLearning"), dict) else {}
        )
        team_schedule = durable.get("teamSchedule") if isinstance(durable.get("teamSchedule"), dict) else None
        if team_schedule is None and isinstance(resource_details.get("teamSchedule"), dict):
            team_schedule = resource_details["teamSchedule"]
        runtime_result = durable.get("runtimeResult") if isinstance(durable.get("runtimeResult"), dict) else None
        review = durable.get("review") if isinstance(durable.get("review"), dict) else None
        if review is None and isinstance(resource_details.get("review"), dict):
            review = resource_details["review"]
        gitleaks = durable.get("gitleaks") if isinstance(durable.get("gitleaks"), dict) else None
        if gitleaks is None and isinstance(resource_details.get("gitleaks"), dict):
            gitleaks = resource_details["gitleaks"]
        evidence_ref = str(failed_learning.get("evidenceRef") or resource_details.get("evidenceRef") or "").strip()
        workspace_id = str(resource_details.get("workspaceId") or durable.get("workspaceId") or "").strip()
        changed_files = [
            str(item)
            for item in ((review or {}).get("changedFiles") or [])
            if str(item).strip()
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
        evidence_refs = [
            str(item)
            for item in (durable.get("evidencePackageIds") or [])
            if str(item).strip()
        ]
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
        review_ready = coordinator.transition(
            loop["id"],
            to_state="review_ready",
            reason="AI resource learning was recorded from existing QA, gitleaks, and diff evidence.",
            actor="remediation",
            trigger="retry_resource_learning",
            context_patch={"durableRun": review_durable},
            metadata={"remediationActionId": action["id"], "approvalJobId": approval_job["id"]},
        )
        awaiting_durable = {
            **dict(review_ready["context"].get("durableRun") or {}),
            "status": "awaiting_approval",
            "resourceLearning": resource_learning,
            "approval": next_approval,
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
                    **next_approval,
                    "remediationActionId": action["id"],
                },
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
            "thread": threads.get_thread(thread_id),
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
        source_message = self._source_retry_message(threads=threads, thread_id=thread_id, message_id=message_id)
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

    def _approve_resource_decision(self, *, action: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
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
        policy_result = self._resource_policy_summary(payload.get("policyResult"))
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
        policy_result = BlockerRemediationService._resource_policy_summary(
            decision.get("policyResult")
        )
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
            policy_result = BlockerRemediationService._resource_policy_summary(
                decision.get("policyResult")
            )
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

    def _answer_question(self, *, action: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
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

        result = ThreadCoordinator(self.connection, root=self.root).resolve_decision(
            thread_id=thread_id,
            decision_id=decision_id,
            resolution=answer,
            decided_by=str(payload.get("decidedBy") or "remediation"),
        )
        clarification_payload = {
            **action_payload,
            "answer": answer,
            "resolution": answer,
            "decidedBy": str(payload.get("decidedBy") or "remediation"),
        }
        clarification = self._record_clarification_answer(
            decision=result.get("decision") or {},
            payload=clarification_payload,
            answer=answer,
        )
        loop_supersede = self._supersede_answered_decision_loop(
            action=action,
            result=result,
            answer=answer,
            decision_id=decision_id,
        )
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

    def _supersede_answered_decision_loop(
        self,
        *,
        action: dict[str, Any],
        result: dict[str, Any],
        answer: str,
        decision_id: str,
    ) -> dict[str, Any]:
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
        superseded = coordinator.transition(
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
        if "credential" in text or "api key" in text:
            return "provider_missing_credentials"
        if "provider" in text and ("health" in text or "unhealthy" in text):
            return "provider_health_failed"
        if stage == "resource_manager":
            resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
            if isinstance(resource_blockers, list) and any(
                isinstance(item, dict)
                and (item.get("decision") or {}).get("selected") is None
                for item in resource_blockers
            ):
                return "resource_manager_unconfigured"
            if "requires approval" in reason_text or (
                isinstance(resource_blockers, list)
                and any(
                    isinstance(item, dict)
                    and self._resource_decision_requires_approval(item.get("decision"))
                    for item in resource_blockers
                )
            ):
                return "resource_manager_approval_required"
            return "resource_manager_unconfigured"
        if stage == "team_scheduler":
            return "team_scheduler_failed"
        if stage == "technical_lead":
            return "technical_lead_planning_failed"
        if stage == "resource_learning":
            return "resource_learning_failed"
        if "auth" in text or "login" in text or "not authenticated" in text:
            return "runtime_auth_missing"
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
            status == "needs_input" or bool(details.get("pendingDecisions")) or "question" in text or "input" in text
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
    def _provider_id_from_resource_blockers(details: dict[str, Any]) -> str:
        resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
        if not isinstance(resource_blockers, list):
            return ""
        for blocker in resource_blockers:
            if not isinstance(blocker, dict):
                continue
            for source in (blocker, blocker.get("decision")):
                if not isinstance(source, dict):
                    continue
                provider_id = str(source.get("providerId") or source.get("provider_id") or "").strip()
                if provider_id:
                    return provider_id
                selected = source.get("selected")
                if isinstance(selected, dict):
                    provider_id = str(selected.get("providerId") or selected.get("provider_id") or "").strip()
                    if provider_id:
                        return provider_id
                for key in ("rejected", "candidates"):
                    values = source.get(key)
                    if not isinstance(values, list):
                        continue
                    for value in values:
                        if not isinstance(value, dict):
                            continue
                        provider_id = str(value.get("providerId") or value.get("provider_id") or "").strip()
                        if provider_id:
                            return provider_id
        return ""

    @staticmethod
    def _provider_credentials_setup_payload(provider_id: str) -> dict[str, Any]:
        normalized_provider_id = str(provider_id or "").strip()
        if not normalized_provider_id:
            return {}
        if is_ollama_runtime_id(normalized_provider_id) or normalized_provider_id in OLLAMA_REMOTE_PROVIDER_IDS:
            remote = normalized_provider_id in OLLAMA_REMOTE_PROVIDER_IDS or normalized_provider_id.startswith(
                "ollama-remote"
            )
            payload = {
                "providerId": normalized_provider_id,
                "displayName": "Ollama remote" if remote else "Ollama Local/Remote",
                "kind": "local",
                "knownProvider": True,
                "requiresManualBaseUrl": remote,
                "authFields": ["apiKey"] if remote else [],
                "requiredFields": ["baseUrl"],
            }
            if not remote:
                payload["baseUrl"] = DEFAULT_OLLAMA_BASE_URL
                payload["baseUrlSource"] = "known_provider_default"
            return payload
        spec = next(
            (
                item
                for item in RUNTIME_PROVIDER_CONFIG_SPECS
                if item.provider_id == normalized_provider_id
            ),
            None,
        )
        default_base_url = known_provider_default_base_url(normalized_provider_id)
        variables = spec.variables if spec is not None else ()
        auth_fields = [variable.key for variable in variables if variable.secret]
        required_fields = [variable.key for variable in variables if variable.required]
        if spec is None:
            auth_fields = ["apiKey"]
            required_fields = ["apiKey"]
        payload = {
            "providerId": normalized_provider_id,
            "displayName": spec.display_name if spec is not None else normalized_provider_id,
            "kind": spec.kind if spec is not None else "api",
            "knownProvider": bool(spec is not None or default_base_url),
            "requiresManualBaseUrl": "baseUrl" in required_fields and not bool(default_base_url),
            "authFields": auth_fields,
            "requiredFields": required_fields,
        }
        if default_base_url:
            payload["baseUrl"] = default_base_url
            payload["baseUrlSource"] = "known_provider_default"
        return payload

    @classmethod
    def _resource_manager_settings_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"section": "routing"}
        resource_blockers = cls._resource_blocker_summaries(details)
        if resource_blockers:
            payload["resourceBlockers"] = resource_blockers
            blocked_roles: list[str] = []
            for blocker in resource_blockers:
                role = str(blocker.get("role") or "").strip()
                if role and role not in blocked_roles:
                    blocked_roles.append(role)
            if blocked_roles:
                payload["blockedRoles"] = blocked_roles

        agent_task_ids = details.get("agentTaskIds") if isinstance(details, dict) else None
        if isinstance(agent_task_ids, list):
            payload["agentTaskIds"] = [
                str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()
            ]

        team_schedule_summary = cls._team_schedule_repair_summary(details)
        if team_schedule_summary:
            payload["teamScheduleSummary"] = team_schedule_summary
        return redact_secrets(payload)

    @classmethod
    def _resource_blocker_summaries(cls, details: dict[str, Any]) -> list[dict[str, Any]]:
        raw_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
        if not isinstance(raw_blockers, list):
            return []
        summaries: list[dict[str, Any]] = []
        for blocker in raw_blockers[:8]:
            if not isinstance(blocker, dict):
                continue
            decision = blocker.get("decision") if isinstance(blocker.get("decision"), dict) else {}
            summary: dict[str, Any] = {
                "role": str(blocker.get("role") or "").strip(),
                "taskId": str(blocker.get("taskId") or "").strip(),
                "reason": str(
                    blocker.get("reason")
                    or decision.get("decisionReason")
                    or "AI resource selection blocked."
                ).strip(),
                "selected": cls._resource_candidate_summary(decision.get("selected")),
            }
            decision_reason = str(decision.get("decisionReason") or "").strip()
            if decision_reason:
                summary["decisionReason"] = decision_reason
            rejected = cls._resource_candidate_summaries(decision.get("rejected"))
            if rejected:
                summary["rejected"] = rejected
            policy_result = cls._resource_policy_summary(decision.get("policyResult"))
            if policy_result:
                summary["policyResult"] = policy_result
            summaries.append(summary)
        return summaries

    @classmethod
    def _resource_candidate_summaries(cls, candidates: Any) -> list[dict[str, Any]]:
        if not isinstance(candidates, list):
            return []
        return [
            summary
            for candidate in candidates[:8]
            for summary in [cls._resource_candidate_summary(candidate)]
            if summary is not None
        ]

    @staticmethod
    def _resource_candidate_summary(candidate: Any) -> dict[str, Any] | None:
        if not isinstance(candidate, dict):
            return None
        summary = {
            key: candidate.get(key)
            for key in ("providerId", "model", "runtime", "reason")
            if candidate.get(key) is not None
        }
        return summary or None

    @staticmethod
    def _resource_policy_summary(policy_result: Any) -> dict[str, Any]:
        if not isinstance(policy_result, dict):
            return {}
        return {
            key: policy_result[key]
            for key in ("scoring", "opaqueMlUsed", "unknownCostPolicy")
            if key in policy_result
        }

    @staticmethod
    def _team_schedule_repair_summary(details: dict[str, Any]) -> dict[str, Any]:
        team_schedule = details.get("teamSchedule") if isinstance(details, dict) else None
        if not isinstance(team_schedule, dict):
            return {}
        roles = team_schedule.get("roles") if isinstance(team_schedule.get("roles"), list) else []
        summary = team_schedule.get("summary") if isinstance(team_schedule.get("summary"), dict) else {}
        repair_summary: dict[str, Any] = {}
        if team_schedule.get("schedulerVersion") is not None:
            repair_summary["schedulerVersion"] = team_schedule.get("schedulerVersion")
        repair_summary["roleCount"] = len(roles)
        if summary.get("resourceDecisionBlockedCount") is not None:
            repair_summary["resourceDecisionBlockedCount"] = summary.get(
                "resourceDecisionBlockedCount"
            )
        for key in ("phase", "mode", "risk", "status"):
            value = str(team_schedule.get(key) or "").strip()
            if value:
                repair_summary[key] = value
        return repair_summary

    @classmethod
    def _team_scheduler_settings_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"section": "team"}
        for key in ("status", "productOwnerOutputId", "backlogArtifactId"):
            value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
            if value:
                payload[key] = value

        agent_task_ids = details.get("agentTaskIds") if isinstance(details, dict) else None
        if isinstance(agent_task_ids, list):
            payload["agentTaskIds"] = [
                str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()
            ]

        scheduled_roles = cls._team_schedule_role_names(details)
        if scheduled_roles:
            payload["scheduledRoles"] = scheduled_roles

        for key in ("unscheduledRoles", "scheduledRoles"):
            values = details.get(key) if isinstance(details, dict) else None
            if isinstance(values, list):
                payload[key] = [str(value).strip() for value in values if str(value).strip()]

        team_schedule_summary = cls._team_schedule_repair_summary(details)
        if team_schedule_summary:
            payload["teamScheduleSummary"] = team_schedule_summary
        return redact_secrets(payload)

    @staticmethod
    def _team_schedule_role_names(details: dict[str, Any]) -> list[str]:
        team_schedule = details.get("teamSchedule") if isinstance(details, dict) else None
        if not isinstance(team_schedule, dict):
            return []
        roles = team_schedule.get("roles") if isinstance(team_schedule.get("roles"), list) else []
        names: list[str] = []
        for role in roles:
            if not isinstance(role, dict):
                continue
            role_name = str(role.get("role") or "").strip()
            if role_name and role_name not in names:
                names.append(role_name)
        return names

    @staticmethod
    def _product_owner_settings_payload(details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"section": "team"}
        scalar_fields = (
            "status",
            "outputStatus",
            "workspaceId",
            "productOwnerOutputId",
            "briefId",
            "reason",
        )
        for key in scalar_fields:
            value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
            if value:
                payload[key] = value

        list_fields = (
            "artifactIds",
            "clarificationQuestionIds",
            "productDecisionIds",
        )
        for key in list_fields:
            values = details.get(key) if isinstance(details, dict) else None
            if isinstance(values, list):
                payload[key] = [str(value).strip() for value in values if str(value).strip()]

        pending_decisions = (
            details.get("pendingThreadDecisions") if isinstance(details, dict) else None
        )
        if not isinstance(pending_decisions, list) and isinstance(details, dict):
            pending_decisions = details.get("pendingDecisions")
        if isinstance(pending_decisions, list):
            payload["pendingThreadDecisionCount"] = len(pending_decisions)
        return redact_secrets(payload)

    @staticmethod
    def _workspace_recovery_payload(details: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"section": "workspaces"}
        scalar_fields = (
            "status",
            "taskId",
            "workspaceId",
            "workspacePath",
            "projectPath",
            "workspaceRoot",
            "branchName",
        )
        for key in scalar_fields:
            value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
            if value:
                payload[key] = value

        reason_text = str(reason or "").strip()
        if isinstance(details, dict) and str(details.get("reason") or "").strip():
            reason_text = str(details.get("reason") or "").strip()
        if reason_text:
            payload["reason"] = reason_text
        return redact_secrets(payload)

    @classmethod
    def _resource_learning_settings_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"section": "routing"}
        for key in ("status", "reason", "workspaceId", "evidenceRef"):
            value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
            if value:
                payload[key] = value

        agent_task_ids = details.get("agentTaskIds") if isinstance(details, dict) else None
        if isinstance(agent_task_ids, list):
            payload["agentTaskIds"] = [
                str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()
            ]

        review = details.get("review") if isinstance(details, dict) else None
        if isinstance(review, dict):
            changed_files = [
                str(path).strip() for path in review.get("changedFiles") or [] if str(path).strip()
            ]
            if changed_files:
                payload["changedFiles"] = changed_files

        gitleaks = details.get("gitleaks") if isinstance(details, dict) else None
        if isinstance(gitleaks, dict):
            gitleaks_status = str(gitleaks.get("status") or "").strip()
            if gitleaks_status:
                payload["gitleaksStatus"] = gitleaks_status

        scheduled_roles = cls._team_schedule_role_names(details)
        if scheduled_roles:
            payload["scheduledRoles"] = scheduled_roles

        team_schedule_summary = cls._team_schedule_repair_summary(details)
        if team_schedule_summary:
            payload["teamScheduleSummary"] = team_schedule_summary
        return redact_secrets(payload)

    @classmethod
    def _review_diff_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if not isinstance(details, dict):
            return payload

        for key in ("status", "reason", "workspaceId", "workspacePath", "runtimeStatus"):
            value = str(details.get(key) or "").strip()
            if value:
                payload[key] = value

        agent_task_ids = details.get("agentTaskIds")
        if isinstance(agent_task_ids, list):
            payload["agentTaskIds"] = [
                str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()
            ]

        runtime_result = details.get("runtimeResult") if isinstance(details.get("runtimeResult"), dict) else {}
        if not payload.get("runtimeStatus"):
            runtime_status = str(runtime_result.get("status") or "").strip()
            if runtime_status:
                payload["runtimeStatus"] = runtime_status

        review = details.get("review") if isinstance(details.get("review"), dict) else None
        if review is None and isinstance(runtime_result.get("review"), dict):
            review = runtime_result["review"]
        if isinstance(review, dict):
            changed_files = review.get("changedFiles")
            if isinstance(changed_files, list):
                payload["changedFiles"] = [
                    str(path).strip() for path in changed_files if str(path).strip()
                ]

            diff_refs = review.get("diffRefs")
            if isinstance(diff_refs, list):
                payload["diffRefs"] = [item for item in diff_refs if item]

        qa_results = details.get("qaResults")
        if not isinstance(qa_results, list):
            qa_results = runtime_result.get("qaResults")
        if isinstance(qa_results, list):
            payload["qaResultCount"] = len(qa_results)

        scheduled_roles = cls._team_schedule_role_names(details)
        if scheduled_roles:
            payload["scheduledRoles"] = scheduled_roles

        team_schedule_summary = cls._team_schedule_repair_summary(details)
        if team_schedule_summary:
            payload["teamScheduleSummary"] = team_schedule_summary
        return redact_secrets(payload)

    @classmethod
    def _gitleaks_recovery_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload = cls._review_diff_payload(details)
        if not isinstance(details, dict):
            return payload

        gitleaks = details.get("gitleaks") if isinstance(details.get("gitleaks"), dict) else details
        gitleaks_status = str(gitleaks.get("status") or "").strip()
        if gitleaks_status:
            payload["gitleaksStatus"] = gitleaks_status

        finding_count = gitleaks.get("findingCount")
        if finding_count is not None:
            payload["gitleaksFindingCount"] = finding_count

        if gitleaks.get("deliveryBlocked") is not None:
            payload["deliveryBlocked"] = bool(gitleaks.get("deliveryBlocked"))
        return redact_secrets(payload)

    @classmethod
    def _qa_recovery_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload = cls._review_diff_payload(details)
        if not isinstance(details, dict):
            return payload

        qa_verdict = str(details.get("qaVerdict") or "").strip()
        if not qa_verdict:
            runtime_result = (
                details.get("runtimeResult") if isinstance(details.get("runtimeResult"), dict) else {}
            )
            evidence_package = (
                runtime_result.get("evidencePackage")
                if isinstance(runtime_result.get("evidencePackage"), dict)
                else {}
            )
            qa_verdict = str(evidence_package.get("qaVerdict") or "").strip()
        if qa_verdict:
            payload["qaVerdict"] = qa_verdict

        qa_results = details.get("qaResults") if isinstance(details.get("qaResults"), list) else []
        non_passing = (
            details.get("nonPassingQaResults")
            if isinstance(details.get("nonPassingQaResults"), list)
            else [
                result
                for result in qa_results
                if not isinstance(result, dict)
                or str(result.get("status") or "").strip().lower() != "passed"
            ]
        )
        payload["nonPassingQaResultCount"] = len(non_passing)
        summaries = cls._qa_result_summaries(non_passing)
        if summaries:
            payload["nonPassingQaResults"] = summaries
        return redact_secrets(payload)

    @staticmethod
    def _qa_result_summaries(results: Any) -> list[dict[str, Any]]:
        if not isinstance(results, list):
            return []
        summaries: list[dict[str, Any]] = []
        for result in results[:8]:
            if not isinstance(result, dict):
                summaries.append({"status": "invalid"})
                continue
            summary = {
                key: result[key]
                for key in ("command", "status", "reason", "artifactId")
                if result.get(key) is not None
            }
            if summary:
                summaries.append(summary)
        return summaries

    @classmethod
    def _runtime_recovery_payload(cls, details: dict[str, Any]) -> dict[str, Any]:
        payload = cls._review_diff_payload(details)
        if not isinstance(details, dict):
            return payload

        for key in ("selectedRuntimeId", "providerId", "model"):
            value = str(details.get(key) or "").strip()
            if value:
                payload[key] = value
        if details.get("executable") is not None:
            payload["executable"] = bool(details.get("executable"))

        runtime = details.get("runtime") if isinstance(details.get("runtime"), dict) else {}
        runtime_id = str(
            details.get("runtimeId")
            or runtime.get("id")
            or details.get("selectedRuntimeId")
            or ""
        ).strip()
        if runtime_id:
            payload["runtimeId"] = runtime_id
        return redact_secrets(payload)

    @staticmethod
    def _git_dirty_tree_payload(details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if not isinstance(details, dict):
            return payload

        for key in ("status", "reason", "projectId", "workspaceId", "projectPath", "branch", "currentBranch"):
            value = str(details.get(key) or "").strip()
            if value:
                payload[key] = value
        if details.get("dirty") is not None:
            payload["dirty"] = bool(details.get("dirty"))
        if details.get("remoteMissing") is not None:
            payload["remoteMissing"] = bool(details.get("remoteMissing"))
        configured_remotes = details.get("configuredRemotes")
        if isinstance(configured_remotes, int):
            payload["configuredRemotes"] = configured_remotes

        dirty_file_count = 0
        for key in ("changedFiles", "stagedFiles", "untrackedFiles"):
            values = details.get(key)
            if isinstance(values, list):
                clean_values = [str(value).strip() for value in values if str(value).strip()]
                payload[key] = clean_values
                dirty_file_count += len(clean_values)
        payload["dirtyFileCount"] = dirty_file_count

        remotes = details.get("remotes")
        if isinstance(remotes, list):
            remote_names = [
                str(remote.get("name") or "").strip()
                for remote in remotes
                if isinstance(remote, dict) and str(remote.get("name") or "").strip()
            ]
            if remote_names:
                payload["remoteNames"] = remote_names
        return redact_secrets(payload)

    @staticmethod
    def _delivery_approval_payload(details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("status", "reason", "workspaceId"):
            value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
            if value:
                payload[key] = value

        for key in ("evidenceRefs", "changedFiles"):
            values = details.get(key) if isinstance(details, dict) else None
            if isinstance(values, list):
                payload[key] = [str(value).strip() for value in values if str(value).strip()]

        diff_refs = details.get("diffRefs") if isinstance(details, dict) else None
        if isinstance(diff_refs, list):
            payload["diffRefs"] = [item for item in diff_refs if item]

        gitleaks = details.get("gitleaks") if isinstance(details, dict) else None
        if isinstance(gitleaks, dict):
            gitleaks_status = str(gitleaks.get("status") or "").strip()
            if gitleaks_status:
                payload["gitleaksStatus"] = gitleaks_status

        resource_learning = details.get("resourceLearning") if isinstance(details, dict) else None
        if isinstance(resource_learning, dict):
            payload["resourceLearning"] = {
                key: resource_learning[key]
                for key in ("status", "evidenceRef", "observationCount")
                if key in resource_learning
            }
        return redact_secrets(payload)

    @staticmethod
    def _research_recovery_payload(details: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("status", "jobId", "researchStatus"):
            value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
            if value:
                payload[key] = value

        decisions = details.get("decisions") if isinstance(details, dict) else None
        if isinstance(decisions, list):
            payload["decisionCount"] = len([item for item in decisions if isinstance(item, dict)])
            payload["decisions"] = [
                {
                    field: item[field]
                    for field in ("title", "category", "impact", "status")
                    if field in item
                }
                for item in decisions[:8]
                if isinstance(item, dict)
            ]

        policy = details.get("researchPolicy") if isinstance(details, dict) else None
        if isinstance(policy, dict):
            payload["researchPolicy"] = policy
        return redact_secrets(payload)

    @staticmethod
    def _research_requires_network_check(reason: str, details: dict[str, Any]) -> bool:
        remediation = details.get("remediation") if isinstance(details, dict) else None
        action = remediation.get("action") if isinstance(remediation, dict) else None
        if action == "check_network_access":
            return True
        text = f"{reason} {details}".lower()
        return any(token in text for token in ("network", "urlopen", "timed out", "timeout", "offline", "internet"))

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
        runtime_id = (
            details.get("selectedRuntimeId")
            or details.get("runtimeId")
            or details.get("providerId")
            or self._provider_id_from_resource_blockers(details)
        )
        runtime_id_text = str(runtime_id or "").strip()
        runtime_payload = {"runtimeId": runtime_id_text} if runtime_id_text else {}
        provider_setup_payload = (
            self._provider_credentials_setup_payload(runtime_id_text)
            if runtime_id_text
            else {}
        )
        provider_settings_payload = {"section": "providers-cli"}
        if provider_setup_payload:
            provider_settings_payload["providerId"] = provider_setup_payload["providerId"]
            provider_settings_payload["providerSetup"] = provider_setup_payload
        resource_manager_settings_payload = self._resource_manager_settings_payload(details)
        team_scheduler_settings_payload = self._team_scheduler_settings_payload(details)
        product_owner_settings_payload = self._product_owner_settings_payload(details)
        resource_learning_settings_payload = self._resource_learning_settings_payload(details)
        workspace_recovery_payload = self._workspace_recovery_payload(details, reason=reason)
        research_recovery_payload = self._research_recovery_payload(details)
        review_diff_payload = self._review_diff_payload(details)
        gitleaks_recovery_payload = self._gitleaks_recovery_payload(details)
        qa_recovery_payload = self._qa_recovery_payload(details)
        runtime_recovery_payload = self._runtime_recovery_payload(details)
        runtime_settings_payload = {**provider_settings_payload, **runtime_recovery_payload}
        if runtime_id_text:
            runtime_settings_payload["runtimeId"] = runtime_id_text
        git_dirty_tree_payload = self._git_dirty_tree_payload(details)
        git_init_payload = {**git_dirty_tree_payload, "defaultBranch": "dev"}
        delivery_approval_payload = self._delivery_approval_payload(details)
        if blocker_type == "po_needs_input":
            pending_decisions = details.get("pendingDecisions") if isinstance(details, dict) else None
            specs = [
                {
                    "actionType": "answer_question",
                    "title": str(item.get("title") or "Answer ProductOwnerAgent question"),
                    "description": "Provide the missing product decision or clarification.",
                    "payload": {
                        "decisionId": item.get("decisionId"),
                        "prompt": item.get("prompt") or item.get("title"),
                        "clarificationQuestionId": item.get("clarificationQuestionId"),
                        "productDecisionId": item.get("productDecisionId"),
                        "initiativeId": item.get("initiativeId"),
                        "options": item.get("options") or [],
                    },
                }
                for item in (pending_decisions or [])
                if isinstance(item, dict) and str(item.get("decisionId") or "").strip()
            ]
            if specs:
                return specs
        resource_approval_spec = self._approve_resource_decision_spec(details)
        if blocker_type == "resource_manager_approval_required" and resource_approval_spec:
            return [
                resource_approval_spec,
                {
                    "actionType": "open_settings_section",
                    "title": "Open AI resources settings",
                    "description": "Review the selected AI resource, cost policy, and approval requirement.",
                    "payload": resource_manager_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after approving or changing the AI resource policy.",
                    "payload": {**resource_manager_settings_payload, "retryTarget": "resource_manager"},
                },
            ]
        if blocker_type == "research_required" and self._research_requires_network_check(reason, details):
            return [
                {
                    "actionType": "check_network_access",
                    "title": "Check network access",
                    "description": "Verify ResearchAgent can reach the web-search endpoint before retrying.",
                    "payload": {"section": "internet", **research_recovery_payload},
                    "primary": True,
                },
                {
                    "actionType": "run_worker_once",
                    "title": "Run research worker once",
                    "description": "Process the queued ResearchAgent job after connectivity is restored.",
                    "payload": research_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the required research evidence is available.",
                    "payload": {**research_recovery_payload, "retryTarget": "research"},
                },
            ]
        git_remote_add_specs = self._configured_git_remote_add_specs(project_id)
        mapping: dict[str, list[dict[str, Any]]] = {
            "runtime_not_executable": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open runtime settings",
                    "description": "Configure an executable CLI, API, Ollama, or local runtime before retrying.",
                    "payload": runtime_settings_payload,
                },
                {
                    "actionType": "validate_runtime",
                    "title": "Validate runtime",
                    "description": "Re-check local runtime availability after fixing installation or PATH.",
                    "payload": {**runtime_settings_payload, "settingsSection": "providers-cli"},
                },
                {
                    "actionType": "switch_runtime",
                    "title": "Switch runtime",
                    "description": "Select a different executable runtime for this blocked run.",
                    "payload": {**runtime_settings_payload, "settingsSection": "providers-cli"},
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after configuring or selecting an executable runtime.",
                    "payload": {**runtime_settings_payload, "retryTarget": "runtime"},
                },
            ],
            "runtime_auth_missing": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open runtime credentials",
                    "description": "Open provider/runtime credentials settings.",
                    "payload": {**runtime_settings_payload, "section": "credentials"},
                },
                {
                    "actionType": "validate_runtime",
                    "title": "Validate runtime",
                    "description": "Validate runtime authentication after updating credentials.",
                    "payload": runtime_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after fixing runtime authentication.",
                    "payload": {**runtime_settings_payload, "retryTarget": "runtime_auth"},
                },
            ],
            "runtime_output_invalid": [
                {
                    "actionType": "continue_plan_only",
                    "title": "Continue plan-only",
                    "description": "Continue without executing code while runtime output is invalid.",
                    "payload": runtime_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry the blocked Product Loop after correcting the runtime output problem.",
                    "payload": {**runtime_recovery_payload, "retryTarget": "runtime"},
                },
            ],
            "git_not_initialized": [
                {
                    "actionType": "git_init",
                    "title": "Initialize Git repository",
                    "description": "Create Git metadata in the project folder before Product Loop execution.",
                    "payload": git_init_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry the blocked Product Loop after Git has been initialized.",
                    "payload": {**git_dirty_tree_payload, "retryTarget": "git_not_initialized"},
                },
            ],
            "git_dirty_tree": [
                {
                    "actionType": "view_diff",
                    "title": "View current diff",
                    "description": "Inspect the dirty tree before Product Loop execution continues.",
                    "payload": git_dirty_tree_payload,
                },
                {
                    "actionType": "create_branch",
                    "title": "Create branch",
                    "description": "Create a branch to isolate the current dirty work.",
                    "payload": {
                        **git_dirty_tree_payload,
                        "branchName": "codex/remediate-dirty-tree",
                    },
                },
                {
                    "actionType": "save_patch",
                    "title": "Save patch",
                    "description": "Capture the current patch so the user can preserve dirty changes.",
                    "payload": git_dirty_tree_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the dirty tree is preserved, committed, stashed, or otherwise cleaned.",
                    "payload": {**git_dirty_tree_payload, "retryTarget": "git_dirty_tree"},
                },
            ],
            "git_status_failed": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open workspace settings",
                    "description": "Review the project path and Git workspace settings before retrying status.",
                    "payload": {"section": "workspaces", **git_dirty_tree_payload},
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after Git status can be collected from the project workspace.",
                    "payload": {**git_dirty_tree_payload, "retryTarget": "git_status_failed"},
                },
            ],
            "git_branch_missing": [
                {
                    "actionType": "create_branch",
                    "title": "Create branch",
                    "description": "Create the missing branch required for execution.",
                    "payload": git_dirty_tree_payload,
                },
                {
                    "actionType": "checkout_branch",
                    "title": "Checkout branch",
                    "description": "Switch to an existing branch before continuing.",
                    "payload": git_dirty_tree_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after creating or checking out the branch required for execution.",
                    "payload": {**git_dirty_tree_payload, "retryTarget": "git_branch_missing"},
                },
            ],
            "git_remote_missing": [
                *git_remote_add_specs,
                {
                    "actionType": "open_settings_section",
                    "title": "Open Git remote settings",
                    "description": "Reconnect or re-add the project Git remote before delivery continues.",
                    "payload": {"section": "workspaces", **git_dirty_tree_payload},
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the project Git remote is reachable again.",
                    "payload": {
                        "section": "workspaces",
                        **git_dirty_tree_payload,
                        "retryTarget": "git_remote",
                    },
                },
            ],
            "gitleaks_missing": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open security tools settings",
                    "description": "Configure the gitleaks executable required by the security gate.",
                    "payload": {"section": "security-tools", **gitleaks_recovery_payload},
                },
                {
                    "actionType": "run_gitleaks",
                    "title": "Run gitleaks",
                    "description": "Retry the gitleaks gate after installing the executable.",
                    "payload": gitleaks_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the gitleaks executable is installed and the gate passes.",
                    "payload": {**gitleaks_recovery_payload, "retryTarget": "gitleaks"},
                },
            ],
            "gitleaks_failed": [
                {
                    "actionType": "run_gitleaks",
                    "title": "Run gitleaks",
                    "description": "Retry the gitleaks gate after removing detected secrets.",
                    "payload": gitleaks_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after detected secrets have been removed and gitleaks passes.",
                    "payload": {**gitleaks_recovery_payload, "retryTarget": "gitleaks"},
                },
            ],
            "qa_failed": [
                {
                    "actionType": "continue_plan_only",
                    "title": "Continue plan-only",
                    "description": "Continue planning while QA failures are reviewed.",
                    "payload": qa_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry the loop after addressing QA failures.",
                    "payload": {**qa_recovery_payload, "retryTarget": "qa"},
                },
            ],
            "po_needs_input": [
                {
                    "actionType": "answer_question",
                    "title": "Answer ProductOwnerAgent question",
                    "description": "Provide the missing product decision or clarification.",
                    "payload": {
                        "decisionId": details.get("decisionId"),
                        "prompt": details.get("prompt") or reason,
                        "clarificationQuestionId": details.get("clarificationQuestionId"),
                        "productDecisionId": details.get("productDecisionId"),
                        "initiativeId": details.get("initiativeId"),
                        "options": details.get("options") or [],
                    },
                }
            ],
            "worker_not_running": [
                {
                    "actionType": "run_worker_once",
                    "title": "Run worker once",
                    "description": "Run one bounded worker batch to process queued thread work.",
                    "payload": {},
                }
            ],
            "provider_missing_credentials": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open credentials settings",
                    "description": "Configure the provider API key using the known provider defaults.",
                    "payload": provider_settings_payload,
                },
                {
                    "actionType": "validate_runtime",
                    "title": "Validate provider",
                    "description": "Validate provider health after updating credentials.",
                    "payload": runtime_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after provider credentials are configured and validated.",
                    "payload": {**provider_settings_payload, "retryTarget": "provider_credentials"},
                },
            ],
            "provider_health_failed": [
                {
                    "actionType": "validate_runtime",
                    "title": "Validate provider health",
                    "description": "Re-check provider health.",
                    "payload": runtime_payload,
                },
                {
                    "actionType": "switch_runtime",
                    "title": "Switch runtime",
                    "description": "Switch away from the unhealthy provider.",
                    "payload": runtime_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after provider health recovers or a healthy runtime is selected.",
                    "payload": {**runtime_payload, "retryTarget": "provider_health"},
                },
            ],
            "resource_manager_unconfigured": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open AI resources settings",
                    "description": "Configure at least one AI resource profile so ResourceManager can select a runtime.",
                    "payload": resource_manager_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after configuring AI resource profiles.",
                    "payload": {**resource_manager_settings_payload, "retryTarget": "resource_manager"},
                },
            ],
            "resource_manager_approval_required": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open AI resources settings",
                    "description": "Review the selected AI resource, cost policy, and approval requirement.",
                    "payload": resource_manager_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after approving or changing the AI resource policy.",
                    "payload": {**resource_manager_settings_payload, "retryTarget": "resource_manager"},
                },
            ],
            "technical_lead_planning_failed": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open team planning settings",
                    "description": "Review TeamScheduler and TechnicalLead planning configuration before execution.",
                    "payload": team_scheduler_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after TechnicalLead can generate required role tasks.",
                    "payload": {**team_scheduler_settings_payload, "retryTarget": "technical_lead"},
                },
            ],
            "team_scheduler_failed": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open team planning settings",
                    "description": "Review TeamScheduler scope, risk, and mode configuration before execution.",
                    "payload": team_scheduler_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after TeamScheduler can produce a valid role schedule.",
                    "payload": {**team_scheduler_settings_payload, "retryTarget": "team_scheduler"},
                },
            ],
            # A brief/backlog that fails validation almost always means the runtime behind
            # ProductOwnerAgent answered badly, so the runtime repairs lead the settings navigation.
            "product_owner_output_invalid": [
                {
                    "actionType": "validate_runtime",
                    "title": "Validate runtime",
                    "description": "Re-check the runtime that ProductOwnerAgent used before retrying the loop.",
                    "payload": {**runtime_payload, "settingsSection": "providers-cli"},
                    "primary": True,
                },
                {
                    "actionType": "switch_runtime",
                    "title": "Switch runtime",
                    "description": "Select a different executable runtime for ProductOwnerAgent.",
                    "payload": {**runtime_payload, "settingsSection": "providers-cli"},
                },
                {
                    "actionType": "open_settings_section",
                    "title": "Open ProductOwner settings",
                    "description": "Review ProductOwnerAgent runtime and output configuration before backlog generation.",
                    "payload": product_owner_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after ProductOwnerAgent can produce a validated brief or backlog.",
                    "payload": {**product_owner_settings_payload, "retryTarget": "product_owner"},
                },
            ],
            "research_required": [
                {
                    "actionType": "run_worker_once",
                    "title": "Run research worker once",
                    "description": "Process the queued ResearchAgent job required before this loop can continue.",
                    "payload": research_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the required research evidence is available.",
                    "payload": {**research_recovery_payload, "retryTarget": "research"},
                },
            ],
            "workspace_root_missing": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open project workspace settings",
                    "description": "Configure the project workspace root required for isolated execution.",
                    "payload": workspace_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after configuring a valid workspace root.",
                    "payload": {**workspace_recovery_payload, "retryTarget": "workspace_root"},
                },
            ],
            "workspace_allocation_failed": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open workspace isolation settings",
                    "description": "Review worktree, branch, and workspace isolation settings before execution.",
                    "payload": workspace_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after resolving the workspace allocation blocker.",
                    "payload": {**workspace_recovery_payload, "retryTarget": "workspace_allocation"},
                },
            ],
            "review_diff_unavailable": [
                {
                    "actionType": "view_diff",
                    "title": "View current diff",
                    "description": "Inspect the available project diff before retrying review capture.",
                    "payload": review_diff_payload,
                },
                {
                    "actionType": "save_patch",
                    "title": "Save patch",
                    "description": "Persist the available patch as evidence before retrying or requesting changes.",
                    "payload": review_diff_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the runtime produces real changed files in the assigned workspace.",
                    "payload": {**review_diff_payload, "retryTarget": "review_diff"},
                },
            ],
            "approval_unavailable": [
                {
                    "actionType": "view_diff",
                    "title": "View delivery diff",
                    "description": "Inspect the diff and evidence that could not be attached to an approval request.",
                    "payload": delivery_approval_payload,
                },
                {
                    "actionType": "save_patch",
                    "title": "Save patch",
                    "description": "Persist the available patch before retrying approval creation.",
                    "payload": delivery_approval_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after delivery approval persistence is available.",
                    "payload": {**delivery_approval_payload, "retryTarget": "delivery_approval"},
                },
            ],
            "resource_learning_failed": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open AI resource routing",
                    "description": "Review AI resource routing and metrics persistence before approving delivery.",
                    "payload": resource_learning_settings_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after AI resource learning can persist cost and quality observations.",
                    "payload": {**resource_learning_settings_payload, "retryTarget": "resource_learning"},
                },
            ],
            "project_assessment_failed": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open project assessment settings",
                    "description": "Review project path, assessment inputs, and workspace access before ProductOwnerAgent runs.",
                    "payload": workspace_recovery_payload,
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry after the project assessment can read the existing workspace.",
                    "payload": {**workspace_recovery_payload, "retryTarget": "project_assessment"},
                },
            ],
            "functionality_memory_decision_required": [
                {
                    "actionType": "answer_question",
                    "title": "Choose existing functionality action",
                    "description": "Choose whether to continue, improve, run a performance pass, or create a new thread anyway.",
                    "payload": {
                        "decisionId": details.get("decisionId"),
                        "prompt": details.get("prompt") or reason,
                        "options": details.get("options") or [],
                        "functionalityId": details.get("functionalityId"),
                        "sourceThreadId": details.get("sourceThreadId"),
                    },
                }
            ],
            "thread_similarity_decision_required": [
                {
                    "actionType": "answer_question",
                    "title": "Choose similar thread action",
                    "description": "Choose whether to continue the existing thread, improve it, run a performance pass, or create a new thread anyway.",
                    "payload": {
                        "decisionId": details.get("decisionId"),
                        "prompt": details.get("prompt") or reason,
                        "options": details.get("options") or [],
                        "candidateThreadId": details.get("candidateThreadId"),
                        "candidateTitle": details.get("candidateTitle"),
                        "score": details.get("score"),
                    },
                }
            ],
            "thread_intake_decision_required": [
                {
                    "actionType": "answer_question",
                    "title": "Answer intake decision",
                    "description": "Choose one of the offered intake options so AIDO can continue safely.",
                    "payload": {
                        "decisionId": details.get("decisionId"),
                        "prompt": details.get("prompt") or reason,
                        "options": details.get("options") or [],
                        "planMode": details.get("planMode"),
                        "sourceMessageId": details.get("sourceMessageId"),
                    },
                }
            ],
        }
        specs = mapping.get(
            blocker_type,
            [
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": reason or "Retry after resolving the blocker.",
                    "payload": {},
                }
            ],
        )
        if blocker_type.startswith("resource_manager_") and resource_approval_spec:
            return [resource_approval_spec, *specs]
        return specs
