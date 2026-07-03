"""Resolve blocked/configuration-required states into concrete user remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.threads.repository import ThreadsRepository


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
            for spec in self._action_specs(blocker_type, reason=reason, details=clean_details)
        ]

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
        """List remediations and opportunistically materialize worker recovery for queued threads."""
        if worker_status is not None:
            return self.ensure_worker_remediation(thread_id=thread_id, worker_status=worker_status)
        return self.repository.list_for_thread(thread_id)

    def dismiss(self, action_id: str) -> dict[str, Any]:
        """Dismiss one pending remediation action."""
        return self.repository.mark_status(action_id, "dismissed")

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
        if action_type == "open_settings_section":
            execution = {
                "status": "completed",
                "action": action_type,
                "section": execution_payload.get("section") or execution_payload.get("settingsSection") or "runtime",
            }
        elif action_type == "validate_runtime":
            providers = RuntimeStatusService(self.connection).list_provider_statuses()
            executable = any(provider.get("executable") is True for provider in providers)
            execution = {
                "status": "completed" if executable else "blocked",
                "action": action_type,
                "providers": redact_secrets(providers),
                "reason": "At least one runtime is executable."
                if executable
                else "No executable runtime is currently available.",
            }
        elif action_type == "switch_runtime":
            runtime_id = str(execution_payload.get("runtimeId") or "").strip()
            if not runtime_id:
                execution = {
                    "status": "blocked",
                    "action": action_type,
                    "reason": "runtimeId is required to switch runtime.",
                }
            else:
                preferences = self._set_default_runtime(runtime_id)
                execution = {
                    "status": "completed",
                    "action": action_type,
                    "runtimeId": runtime_id,
                    "preferences": preferences,
                    "reason": "Default runtime preference updated.",
                }
        elif action_type == "run_worker_once":
            worker = getattr(platform, "local_worker_runtime", None)
            if worker is None:
                execution = {"status": "blocked", "action": action_type, "reason": "Local worker runtime is unavailable."}
            else:
                execution = redact_secrets(worker.run_once())
        elif action_type in {"git_init", "create_branch", "checkout_branch", "view_diff", "run_gitleaks"}:
            execution = self._execute_git_action(action_type, action=action, payload=execution_payload, platform=platform)
        elif action_type == "save_patch":
            execution = self._save_patch(action=action, payload=execution_payload, platform=platform)
        elif action_type == "retry_loop":
            execution = self._retry_loop(action=action)
        elif action_type == "answer_question":
            execution = self._answer_question(action=action, payload=execution_payload)
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
            "create_branch",
            "checkout_branch",
            "run_gitleaks",
            "run_worker_once",
            "answer_question",
            "retry_loop",
        }

    def _execute_git_action(
        self,
        action_type: str,
        *,
        action: dict[str, Any],
        payload: dict[str, Any],
        platform: Any,
    ) -> dict[str, Any]:
        project_id = str(payload.get("projectId") or action["projectId"])
        service = GitWorkspaceService(self.connection, root=Path(getattr(platform, "cwd", self.root or ".")))
        if action_type == "git_init":
            return service.init_repository(project_id)
        if action_type == "create_branch":
            branch = str(payload.get("branchName") or payload.get("branch") or f"codex/remediation-{action['id'][-8:]}")
            return service.create_branch(project_id, name=branch, base=payload.get("base"))
        if action_type == "checkout_branch":
            branch = str(payload.get("branchName") or payload.get("branch") or "")
            if not branch:
                return {"status": "blocked", "reason": "branchName is required for checkout_branch."}
            return service.checkout(project_id, branch=branch, allow_dirty=bool(payload.get("allowDirty", False)))
        if action_type == "view_diff":
            return service.diff(project_id)
        if action_type == "run_gitleaks":
            return service.gitleaks_scan(project_id)
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

    def _retry_loop(self, *, action: dict[str, Any]) -> dict[str, Any]:
        loop_id = str(action.get("loopId") or "").strip()
        if not loop_id:
            return {"status": "blocked", "action": "retry_loop", "reason": "loopId is required."}
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        loop = ProductLoopCoordinator(self.connection, root=self.root).unblock(loop_id, actor="remediation")
        return {"status": "completed", "action": "retry_loop", "loop": loop}

    def _continue_plan_only(self, *, action: dict[str, Any]) -> dict[str, Any]:
        loop_id = str(action.get("loopId") or "").strip()
        if not loop_id:
            return {"status": "blocked", "action": "continue_plan_only", "reason": "loopId is required."}
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        loop = coordinator.get(loop_id)
        durable = {
            **dict((loop.get("context") or {}).get("durableRun") or {}),
            "status": "planning",
            "planOnly": True,
        }
        planned = coordinator.transition(
            loop_id,
            to_state="planning",
            reason="Operator selected plan-only remediation.",
            actor="remediation",
            trigger="continue_plan_only",
            context_patch={"durableRun": durable},
            metadata={"remediationAction": "continue_plan_only"},
        )
        return {"status": "completed", "action": "continue_plan_only", "loop": planned}

    def _answer_question(self, *, action: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("threadId") or action.get("threadId") or "").strip()
        decision_id = str(payload.get("decisionId") or "").strip()
        answer = str(payload.get("answer") or payload.get("resolution") or "").strip()
        if not thread_id or not decision_id or not answer:
            return {
                "status": "blocked",
                "action": "answer_question",
                "reason": "threadId, decisionId and answer/resolution are required.",
            }
        from local_control_center.threads.coordinator import ThreadCoordinator

        result = ThreadCoordinator(self.connection, root=self.root).resolve_decision(
            thread_id=thread_id,
            decision_id=decision_id,
            resolution=answer,
            decided_by=str(payload.get("decidedBy") or "remediation"),
        )
        return {"status": "completed", "action": "answer_question", **result}

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
        if "credential" in text or "api key" in text:
            return "provider_missing_credentials"
        if "auth" in text or "login" in text or "not authenticated" in text:
            return "runtime_auth_missing"
        if stage in {"product_owner_runtime", "runtime"}:
            return "runtime_not_executable"
        if stage == "git":
            if details.get("dirty") is True or "dirty" in reason_text:
                return "git_dirty_tree"
            if status == "configuration_required" or "not a git repository" in text:
                return "git_not_initialized"
            return "git_branch_missing"
        if stage == "gitleaks":
            if status == "configuration_required" or "not found" in text:
                return "gitleaks_missing"
            return "gitleaks_failed"
        if stage == "qa":
            return "qa_failed"
        if stage == "worker":
            return "worker_not_running"
        if stage == "product_owner" and ("question" in text or "input" in text):
            return "po_needs_input"
        if "provider" in text and ("health" in text or "unhealthy" in text):
            return "provider_health_failed"
        return "runtime_output_invalid"

    def _action_specs(self, blocker_type: str, *, reason: str, details: dict[str, Any]) -> list[dict[str, Any]]:
        runtime_id = details.get("selectedRuntimeId") or details.get("runtimeId")
        mapping: dict[str, list[dict[str, Any]]] = {
            "runtime_not_executable": [
                {
                    "actionType": "validate_runtime",
                    "title": "Validate runtime",
                    "description": "Re-check local runtime availability after fixing installation or PATH.",
                    "payload": {"runtimeId": runtime_id, "settingsSection": "runtimes"},
                },
                {
                    "actionType": "switch_runtime",
                    "title": "Switch runtime",
                    "description": "Select a different executable runtime for this blocked run.",
                    "payload": {"runtimeId": runtime_id, "settingsSection": "runtimes"},
                },
            ],
            "runtime_auth_missing": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open runtime credentials",
                    "description": "Open provider/runtime credentials settings.",
                    "payload": {"section": "credentials"},
                },
                {
                    "actionType": "validate_runtime",
                    "title": "Validate runtime",
                    "description": "Validate runtime authentication after updating credentials.",
                    "payload": {"runtimeId": runtime_id},
                },
            ],
            "runtime_output_invalid": [
                {
                    "actionType": "continue_plan_only",
                    "title": "Continue plan-only",
                    "description": "Continue without executing code while runtime output is invalid.",
                    "payload": {},
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry the blocked Product Loop after correcting the runtime output problem.",
                    "payload": {},
                },
            ],
            "git_not_initialized": [
                {
                    "actionType": "git_init",
                    "title": "Initialize Git repository",
                    "description": "Create Git metadata in the project folder before Product Loop execution.",
                    "payload": {},
                }
            ],
            "git_dirty_tree": [
                {
                    "actionType": "view_diff",
                    "title": "View current diff",
                    "description": "Inspect the dirty tree before Product Loop execution continues.",
                    "payload": {},
                },
                {
                    "actionType": "create_branch",
                    "title": "Create branch",
                    "description": "Create a branch to isolate the current dirty work.",
                    "payload": {"branchName": "codex/remediate-dirty-tree"},
                },
                {
                    "actionType": "save_patch",
                    "title": "Save patch",
                    "description": "Capture the current patch so the user can preserve dirty changes.",
                    "payload": {},
                },
            ],
            "git_branch_missing": [
                {
                    "actionType": "create_branch",
                    "title": "Create branch",
                    "description": "Create the missing branch required for execution.",
                    "payload": {},
                },
                {
                    "actionType": "checkout_branch",
                    "title": "Checkout branch",
                    "description": "Switch to an existing branch before continuing.",
                    "payload": {},
                },
            ],
            "gitleaks_missing": [
                {
                    "actionType": "open_settings_section",
                    "title": "Open security tools settings",
                    "description": "Configure the gitleaks executable required by the security gate.",
                    "payload": {"section": "security-tools"},
                },
                {
                    "actionType": "run_gitleaks",
                    "title": "Run gitleaks",
                    "description": "Retry the gitleaks gate after installing the executable.",
                    "payload": {},
                },
            ],
            "gitleaks_failed": [
                {
                    "actionType": "run_gitleaks",
                    "title": "Run gitleaks",
                    "description": "Retry the gitleaks gate after removing detected secrets.",
                    "payload": {},
                }
            ],
            "qa_failed": [
                {
                    "actionType": "continue_plan_only",
                    "title": "Continue plan-only",
                    "description": "Continue planning while QA failures are reviewed.",
                    "payload": {},
                },
                {
                    "actionType": "retry_loop",
                    "title": "Retry loop",
                    "description": "Retry the loop after addressing QA failures.",
                    "payload": {},
                },
            ],
            "po_needs_input": [
                {
                    "actionType": "answer_question",
                    "title": "Answer ProductOwnerAgent question",
                    "description": "Provide the missing product decision or clarification.",
                    "payload": {},
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
                    "description": "Configure missing provider credentials.",
                    "payload": {"section": "credentials"},
                },
                {
                    "actionType": "validate_runtime",
                    "title": "Validate provider",
                    "description": "Validate provider health after updating credentials.",
                    "payload": {},
                },
            ],
            "provider_health_failed": [
                {
                    "actionType": "validate_runtime",
                    "title": "Validate provider health",
                    "description": "Re-check provider health.",
                    "payload": {},
                },
                {
                    "actionType": "switch_runtime",
                    "title": "Switch runtime",
                    "description": "Switch away from the unhealthy provider.",
                    "payload": {},
                },
            ],
        }
        return mapping.get(
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
