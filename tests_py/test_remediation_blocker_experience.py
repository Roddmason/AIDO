"""Verifica que cada bloqueo de Product Loop produzca acciones reparadoras accionables.

Cubre los tipos de bloqueo que ``test_product_loop_coordinator.py`` no ejercita todavía
(worker detenido, gitleaks ausente, remote Git faltante), el gate de confirmación para
acciones destructivas y la metadata explícita (primary/destructive/technicalReason) que
cada remediation debe exponer al frontend.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.contracts import BLOCKER_TYPES, REMEDIATION_ACTION_TYPES
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository


def _pending_action_types(connection, thread_id: str) -> set[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT blocker_type, action_type
        FROM remediation_actions
        WHERE thread_id = ? AND status = 'pending'
        ORDER BY created_at ASC, rowid ASC
        """,
        (thread_id,),
    ).fetchall()
    return {(row["blocker_type"], row["action_type"]) for row in rows}


def _project_and_thread(connection, tmp_path: Path, name: str) -> tuple[dict, dict]:
    project = ProjectsRepository(connection).create_project(
        name=name, path=tmp_path / name, template_id="other"
    )
    threads = ThreadsRepository(connection)
    thread = threads.create_thread(
        project_id=project["id"],
        owner_type="workspace",
        owner_id=project["id"],
        title=f"{name} thread",
    )
    return project, thread


def test_terminal_loop_resolves_its_pending_actions_without_hiding_loopless_recovery(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "terminal-remediation-lifecycle")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        loop = coordinator.start(project_id=project["id"], title="Terminal remediation lifecycle")
        service = BlockerRemediationService(connection, root=tmp_path)
        loop_actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="workspace_check",
            reason="Project root is required.",
            details={"status": "configuration_required"},
        )
        loopless = service.repository.create_action(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="",
            stage="thread_intake",
            blocker_type="thread_intake_decision_required",
            title="Answer intake question",
            description="Choose how this thread should proceed.",
            action_type="answer_question",
            payload={"decisionId": "thread-decision-loopless"},
        )
        row_count_before = connection.execute(
            "SELECT COUNT(*) AS total FROM remediation_actions WHERE thread_id = ?",
            (thread["id"],),
        ).fetchone()["total"]
        assert service.repository.resolve_pending_for_loop(loop["id"]) == []
        assert all(service.repository.get(action["id"])["status"] == "pending" for action in loop_actions)

        coordinator.cancel(loop["id"], reason="Superseded by a newer Product Loop attempt.")

        terminal_actions = [service.repository.get(action["id"]) for action in loop_actions]
        assert all(action["status"] == "resolved" for action in terminal_actions)
        assert all(action["resolvedAt"] is not None for action in terminal_actions)
        assert service.repository.get(loopless["id"])["status"] == "pending"
        assert (
            connection.execute(
                "SELECT COUNT(*) AS total FROM remediation_actions WHERE thread_id = ?",
                (thread["id"],),
            ).fetchone()["total"]
            == row_count_before
        )

        legacy_pending = service.repository.create_action(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="workspace_check",
            blocker_type="workspace_root_missing",
            title="Legacy stale action",
            description="Simulates a pending action persisted before lifecycle reconciliation.",
            action_type="open_settings_section",
            payload={"section": "workspaces", "legacy": True},
        )
        assert legacy_pending["status"] == "pending"

        service.list_for_thread(thread_id=thread["id"])

        repaired = service.repository.get(legacy_pending["id"])
        assert repaired["status"] == "resolved"
        assert repaired["resolvedAt"] is not None
        assert service.repository.get(loopless["id"])["status"] == "pending"


def test_po_needs_input_persists_one_idempotent_action_per_decision(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "multiple-product-decisions")
        service = BlockerRemediationService(connection, root=tmp_path)
        details = {
            "status": "needs_input",
            "pendingDecisions": [
                {
                    "decisionId": "thread-decision-one",
                    "title": "Choose JDK target",
                    "prompt": "Which JDK should the upgrade target?",
                    "options": ["current JDK", "latest LTS"],
                },
                {
                    "decisionId": "thread-decision-two",
                    "title": "Choose migration strategy",
                    "prompt": "Should the frontend migration be incremental?",
                    "options": ["incremental", "full rewrite"],
                },
            ],
        }

        first = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-needs-input",
            stage="product_owner",
            reason="Product decisions are required.",
            details=details,
        )
        second = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-needs-input",
            stage="product_owner",
            reason="Product decisions are required.",
            details=details,
        )

        actions = [
            action
            for action in service.repository.list_for_thread(thread["id"])
            if action["status"] == "pending" and action["actionType"] == "answer_question"
        ]
        assert len(actions) == 2
        assert {action["payload"]["decisionId"] for action in actions} == {
            "thread-decision-one",
            "thread-decision-two",
        }
        assert len({action["id"] for action in first + second}) == 2


def test_list_backfills_missing_actions_for_current_awaiting_user_decisions(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "awaiting-user-backfill")
        ThreadsRepository(connection).set_status(thread["id"], "waiting_decision")
        threads = ThreadsRepository(connection)
        decision_specs = [
            (
                "Choose JDK target",
                "Which JDK should the upgrade target?",
                ["current JDK", "latest LTS"],
            ),
            (
                "Choose migration strategy",
                "Should the frontend migration be incremental?",
                ["incremental", "full rewrite"],
            ),
        ]
        pending_decisions = []
        for title, prompt, options in decision_specs:
            decision = threads.create_decision(
                thread_id=thread["id"],
                title=title,
                prompt=prompt,
                options=options,
            )
            pending_decisions.append(
                {
                    "decisionId": decision["id"],
                    "title": title,
                    "prompt": prompt,
                    "options": options,
                }
            )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        loop = coordinator.start(
            project_id=project["id"],
            title="Awaiting product decisions",
            context={
                "durableRun": {
                    "status": "awaiting_user",
                    "thread": {"projectThreadId": thread["id"]},
                    "productOwner": {
                        "status": "needs_input",
                        "reason": "Product decisions are required.",
                        "pendingThreadDecisions": pending_decisions,
                    },
                }
            },
        )
        coordinator.transition(loop["id"], to_state="discovery")
        coordinator.transition(loop["id"], to_state="awaiting_user")
        service = BlockerRemediationService(connection, root=tmp_path)
        service.repository.create_action(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="product_owner",
            blocker_type="po_needs_input",
            title="Choose JDK target",
            description="Provide the missing product decision or clarification.",
            action_type="answer_question",
            payload=pending_decisions[0],
        )

        actions = service.list_for_thread(thread_id=thread["id"])

        current_answers = [
            action
            for action in actions
            if action["loopId"] == loop["id"]
            and action["status"] == "pending"
            and action["actionType"] == "answer_question"
        ]
        assert {action["payload"]["decisionId"] for action in current_answers} == {
            decision["decisionId"] for decision in pending_decisions
        }
        assert not any(
            action["loopId"] == loop["id"] and action["actionType"] == "retry_loop" for action in actions
        )
        dismissed = next(
            action
            for action in current_answers
            if action["payload"]["decisionId"] == pending_decisions[0]["decisionId"]
        )
        service.repository.mark_status(dismissed["id"], "dismissed")

        after_dismiss = service.list_for_thread(thread_id=thread["id"])

        same_decision_actions = [
            action
            for action in after_dismiss
            if action["loopId"] == loop["id"]
            and action["actionType"] == "answer_question"
            and action["payload"].get("decisionId") == pending_decisions[0]["decisionId"]
        ]
        assert len(same_decision_actions) == 1
        assert same_decision_actions[0]["status"] == "dismissed"


def test_execute_rejects_non_pending_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "non-pending-execution")
        service = BlockerRemediationService(connection, root=tmp_path)
        action = service.repository.create_action(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-old",
            stage="runtime",
            blocker_type="runtime_not_executable",
            title="Open runtime settings",
            description="Review runtime settings.",
            action_type="open_settings_section",
            payload={"section": "providers-cli"},
        )
        service.repository.mark_status(action["id"], "resolved")

        result = service.execute(action["id"], platform=object())

        assert result["execution"]["status"] == "blocked"
        assert result["execution"]["remediationStatus"] == "resolved"
        assert result["remediation"]["status"] == "resolved"


def test_all_contract_blocker_types_have_specific_repair_actions(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        service = BlockerRemediationService(connection, root=tmp_path)
        details = {
            "runtimeId": "openrouter",
            "decisionId": "decision-1",
            "clarificationQuestionId": "clarification-1",
            "functionalityId": "functionality-1",
            "sourceThreadId": "thread-source-1",
            "pendingDecisions": [
                {
                    "decisionId": "decision-1",
                    "title": "Choose execution path",
                    "options": ["continue", "plan_only"],
                }
            ],
            "resourceBlockers": [
                {
                    "role": "developer",
                    "taskId": "task-1",
                    "decision": {
                        "decisionReason": "Cost policy requires approval.",
                        "selected": {
                            "providerId": "openrouter",
                            "model": "anthropic/claude-sonnet-4",
                            "runtime": "api",
                        },
                    },
                }
            ],
            "remediation": {"action": "check_network_access"},
        }

        for blocker_type in BLOCKER_TYPES:
            specs = service._action_specs(blocker_type, reason="contract coverage", details=details)

            assert specs, blocker_type
            assert any(spec["actionType"] != "retry_loop" for spec in specs), blocker_type
            for spec in specs:
                assert spec["actionType"] in REMEDIATION_ACTION_TYPES
                assert str(spec["title"]).strip()
                assert str(spec["description"]).strip()


def test_retry_loop_remediations_declare_explicit_retry_target(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        service = BlockerRemediationService(connection, root=tmp_path)
        details = {
            "runtimeId": "openrouter",
            "decisionId": "decision-1",
            "clarificationQuestionId": "clarification-1",
            "functionalityId": "functionality-1",
            "sourceThreadId": "thread-source-1",
            "taskId": "task-1",
            "status": "failed",
            "pendingDecisions": [
                {
                    "decisionId": "decision-1",
                    "title": "Choose execution path",
                    "options": ["continue", "plan_only"],
                }
            ],
            "resourceBlockers": [
                {
                    "role": "developer",
                    "taskId": "task-1",
                    "decision": {
                        "decisionReason": "Cost policy requires approval.",
                        "selected": {
                            "providerId": "openrouter",
                            "model": "anthropic/claude-sonnet-4",
                            "runtime": "api",
                        },
                    },
                }
            ],
            "remediation": {"action": "check_network_access"},
        }

        for blocker_type in BLOCKER_TYPES:
            specs = service._action_specs(blocker_type, reason="contract coverage", details=details)

            for spec in specs:
                if spec["actionType"] != "retry_loop":
                    continue
                assert str(spec["payload"].get("retryTarget") or "").strip(), blocker_type


def test_worker_not_running_creates_run_worker_once_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _project, thread = _project_and_thread(connection, tmp_path, "worker-stopped")
        ThreadsRepository(connection).set_status(thread["id"], "queued")
        service = BlockerRemediationService(connection, root=tmp_path)

        service.list_for_thread(thread_id=thread["id"], worker_status={"running": False})

        actions = _pending_action_types(connection, thread["id"])
        assert ("worker_not_running", "run_worker_once") in actions
        worker_action = next(
            item
            for item in service.repository.list_for_thread(thread["id"])
            if item["actionType"] == "run_worker_once"
        )
        assert worker_action["primary"] is True
        assert worker_action["destructive"] is False
        assert worker_action["confirmationRequired"] is False


def test_gitleaks_missing_block_creates_setup_and_run_gitleaks_remediations(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "gitleaks-missing")
        service = BlockerRemediationService(connection, root=tmp_path)

        service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-gitleaks",
            stage="gitleaks",
            reason="gitleaks executable not found on PATH.",
            details={
                "status": "configuration_required",
                "reason": "gitleaks executable not found on PATH.",
            },
        )

        actions = _pending_action_types(connection, thread["id"])
        assert ("gitleaks_missing", "open_settings_section") in actions
        assert ("gitleaks_missing", "run_gitleaks") in actions
        assert ("gitleaks_missing", "retry_loop") in actions
        created = service.repository.list_for_thread(thread["id"])
        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        run_scan = next(action for action in created if action["actionType"] == "run_gitleaks")
        retry = next(action for action in created if action["actionType"] == "retry_loop")
        for action in (open_settings, run_scan, retry):
            assert action["payload"]["status"] == "configuration_required"
            assert action["payload"]["gitleaksStatus"] == "configuration_required"
            assert "not found on PATH" in action["payload"]["reason"]
        assert open_settings["payload"]["section"] == "security-tools"
        assert retry["payload"]["retryTarget"] == "gitleaks"


def test_runtime_not_executable_opens_runtime_setup_with_ollama_defaults(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "runtime-ollama")
        service = BlockerRemediationService(connection, root=tmp_path)

        local_actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-ollama-local",
            stage="runtime",
            reason="Ollama local is not executable.",
            details={
                "status": "failed",
                "reason": "Ollama local is not executable.",
                "runtimeId": "ollama",
                "executable": False,
            },
        )
        remote_actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-ollama-remote",
            stage="runtime",
            reason="Ollama remote is not configured.",
            details={
                "status": "configuration_required",
                "reason": "Ollama remote is not configured.",
                "runtimeId": "ollama_remote",
                "executable": False,
            },
        )

        local_settings = next(
            action for action in local_actions if action["actionType"] == "open_settings_section"
        )
        local_validate = next(
            action for action in local_actions if action["actionType"] == "validate_runtime"
        )
        local_switch = next(action for action in local_actions if action["actionType"] == "switch_runtime")
        local_retry = next(action for action in local_actions if action["actionType"] == "retry_loop")
        remote_settings = next(
            action for action in remote_actions if action["actionType"] == "open_settings_section"
        )
        remote_retry = next(action for action in remote_actions if action["actionType"] == "retry_loop")

        assert local_settings["payload"]["section"] == "providers-cli"
        assert local_settings["payload"]["providerId"] == "ollama"
        assert local_settings["payload"]["providerSetup"] == {
            "providerId": "ollama",
            "displayName": "Ollama Local/Remote",
            "kind": "local",
            "knownProvider": True,
            "baseUrl": "http://localhost:11434",
            "baseUrlSource": "known_provider_default",
            "requiresManualBaseUrl": False,
            "authFields": [],
            "requiredFields": ["baseUrl"],
        }
        for action in (local_settings, local_validate, local_switch, local_retry):
            assert action["payload"]["runtimeId"] == "ollama"
            assert action["payload"]["status"] == "failed"
            assert action["payload"]["executable"] is False
            assert "Ollama local is not executable" in action["payload"]["reason"]
        assert local_validate["payload"]["settingsSection"] == "providers-cli"
        assert local_switch["payload"]["settingsSection"] == "providers-cli"
        assert local_retry["payload"]["retryTarget"] == "runtime"
        assert remote_settings["payload"]["section"] == "providers-cli"
        assert remote_settings["payload"]["providerId"] == "ollama_remote"
        assert remote_settings["payload"]["providerSetup"] == {
            "providerId": "ollama_remote",
            "displayName": "Ollama remote",
            "kind": "local",
            "knownProvider": True,
            "requiresManualBaseUrl": True,
            "authFields": ["apiKey"],
            "requiredFields": ["baseUrl"],
        }
        assert remote_retry["payload"]["runtimeId"] == "ollama_remote"
        assert remote_retry["payload"]["status"] == "configuration_required"
        assert remote_retry["payload"]["executable"] is False
        assert remote_retry["payload"]["retryTarget"] == "runtime"


def test_runtime_auth_missing_remediation_carries_auth_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "runtime-auth")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-runtime-auth",
            stage="runtime",
            reason="Runtime auth missing: Codex CLI is not authenticated.",
            details={
                "status": "failed",
                "reason": "Runtime auth missing: Codex CLI is not authenticated.",
                "runtimeId": "codex_cli",
                "executable": False,
            },
        )

        open_credentials = next(
            action for action in created if action["actionType"] == "open_settings_section"
        )
        validate = next(action for action in created if action["actionType"] == "validate_runtime")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (open_credentials, validate, retry):
            assert action["blockerType"] == "runtime_auth_missing"
            assert action["payload"]["runtimeId"] == "codex_cli"
            assert action["payload"]["status"] == "failed"
            assert action["payload"]["executable"] is False
            assert "not authenticated" in action["payload"]["reason"]
        assert open_credentials["payload"]["section"] == "credentials"
        assert validate["payload"]["runtimeId"] == "codex_cli"
        assert retry["payload"]["retryTarget"] == "runtime_auth"


def test_workspace_allocation_remediation_carries_repair_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "workspace-allocation")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-workspace",
            stage="workspace",
            reason="Workspace allocation failed: branch codex/product-loop is locked.",
            details={
                "status": "failed",
                "taskId": "product-loop-task-1",
                "workspaceId": "workspace-1",
                "workspacePath": str(tmp_path / "workspace-1"),
                "reason": "branch codex/product-loop is locked",
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert open_settings["blockerType"] == "workspace_allocation_failed"
        assert open_settings["payload"]["section"] == "workspaces"
        assert open_settings["payload"]["status"] == "failed"
        assert open_settings["payload"]["taskId"] == "product-loop-task-1"
        assert open_settings["payload"]["workspaceId"] == "workspace-1"
        assert open_settings["payload"]["workspacePath"] == str(tmp_path / "workspace-1")
        assert "branch codex/product-loop is locked" in open_settings["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "workspace_allocation"
        assert retry["payload"]["taskId"] == "product-loop-task-1"


def test_git_remote_missing_block_creates_remote_remediations(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "git-remote-missing")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO git_remotes
                (id, project_id, name, url, scheme, host, path, created_at, updated_at,
                 last_tested_at, last_test_status, last_test_reason, metadata)
            VALUES
                ('git-remote-origin', ?, 'origin', 'git@github.com:aido/example.git',
                 'ssh', 'github.com', 'aido/example.git', ?, ?, NULL, NULL, NULL, '{}')
            """,
            (project["id"], now, now),
        )
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-remote",
            stage="git",
            reason="The project has a configured Git remote, but the repository exposes none.",
            details={
                "status": "remote_missing",
                "reason": "The project has a configured Git remote, but the repository exposes none.",
                "remoteMissing": True,
                "configuredRemotes": 1,
                "dirty": False,
                "branch": "dev",
                "changedFiles": [],
                "stagedFiles": [],
                "untrackedFiles": [],
                "remotes": [],
            },
        )

        actions = _pending_action_types(connection, thread["id"])
        assert ("git_remote_missing", "open_settings_section") in actions
        assert ("git_remote_missing", "add_remote") in actions
        assert ("git_remote_missing", "retry_loop") in actions
        add_remote = next(action for action in created if action["actionType"] == "add_remote")
        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")
        assert add_remote["payload"]["name"] == "origin"
        assert add_remote["payload"]["url"] == "git@github.com:aido/example.git"
        for action in (open_settings, retry):
            assert action["payload"]["section"] == "workspaces"
            assert action["payload"]["remoteMissing"] is True
            assert action["payload"]["configuredRemotes"] == 1
            assert action["payload"]["dirty"] is False
            assert action["payload"]["branch"] == "dev"
            assert action["payload"]["dirtyFileCount"] == 0
        assert retry["payload"]["retryTarget"] == "git_remote"


def test_git_remote_missing_add_remote_remediation_readds_persisted_remote(
    tmp_path: Path,
) -> None:
    if not git_available():
        return
    project_path = tmp_path / "git-remote-readd"
    project_path.mkdir()
    assert run_git(["init", "--initial-branch", "dev"], cwd=project_path).returncode == 0
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="git-remote-readd",
            path=project_path,
            template_id="other",
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="remote readd thread",
        )
        now = utc_now()
        connection.execute(
            """
            INSERT INTO git_remotes
                (id, project_id, name, url, scheme, host, path, created_at, updated_at,
                 last_tested_at, last_test_status, last_test_reason, metadata)
            VALUES
                ('git-remote-origin', ?, 'origin', 'git@github.com:aido/example.git',
                 'ssh', 'github.com', 'aido/example.git', ?, ?, NULL, NULL, NULL, '{}')
            """,
            (project["id"], now, now),
        )
        service = BlockerRemediationService(connection, root=tmp_path)
        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-remote",
            stage="git",
            reason="The project has a configured Git remote, but the repository exposes none.",
            details={"remoteMissing": True},
        )
        add_remote = next(action for action in created if action["actionType"] == "add_remote")

        execution = service.execute(add_remote["id"], platform=type("Platform", (), {"cwd": tmp_path})())

        assert execution["execution"]["status"] == "completed"
        assert execution["execution"]["action"] == "add_remote"
        assert execution["remediation"]["status"] == "resolved"
        assert run_git(["remote", "get-url", "origin"], cwd=project_path).stdout.strip() == (
            "git@github.com:aido/example.git"
        )


def test_git_branch_missing_remediation_carries_git_status_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "git-branch-missing")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-branch",
            stage="git",
            reason="Working branch required before execution.",
            details={
                "status": "branch_missing",
                "reason": "Working branch required before execution.",
                "dirty": False,
                "changedFiles": [],
                "stagedFiles": [],
                "untrackedFiles": [],
                "remotes": [{"name": "origin", "url": "git@example.invalid:aido/aido.git"}],
            },
        )

        create_branch = next(action for action in created if action["actionType"] == "create_branch")
        checkout_branch = next(action for action in created if action["actionType"] == "checkout_branch")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (create_branch, checkout_branch, retry):
            assert action["blockerType"] == "git_branch_missing"
            assert action["payload"]["status"] == "branch_missing"
            assert action["payload"]["dirty"] is False
            assert action["payload"]["remoteNames"] == ["origin"]
            assert action["payload"]["dirtyFileCount"] == 0
        assert retry["payload"]["retryTarget"] == "git_branch_missing"


def test_resource_manager_missing_api_key_opens_credentials_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-manager-api-key")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-resource-api-key",
            stage="resource_manager",
            reason="AIResourceManager could not select a resource because provider API key is missing.",
            details={
                "resourceBlockers": [
                    {
                        "role": "product_owner",
                        "reason": "Provider nvidia_nim API key is missing.",
                        "decision": {
                            "selected": None,
                            "decisionReason": "Provider nvidia_nim API key is missing.",
                            "rejected": [
                                {
                                    "providerId": "nvidia_nim",
                                    "model": "nvidia/nemotron-coder",
                                    "runtime": "api",
                                    "reason": "API key is missing.",
                                }
                            ],
                        },
                    }
                ]
            },
        )

        action_types = {action["actionType"] for action in created}
        blocker_actions = {(action["blockerType"], action["actionType"]) for action in created}
        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        validate = next(action for action in created if action["actionType"] == "validate_runtime")
        retry = next(action for action in created if action["actionType"] == "retry_loop")
        assert {
            ("provider_missing_credentials", "open_settings_section"),
            ("provider_missing_credentials", "retry_loop"),
        } <= blocker_actions
        assert open_settings["payload"]["section"] == "providers-cli"
        assert open_settings["payload"]["providerId"] == "nvidia_nim"
        assert open_settings["payload"]["providerSetup"] == {
            "providerId": "nvidia_nim",
            "displayName": "NVIDIA NIM / Build",
            "kind": "api",
            "knownProvider": True,
            "baseUrl": "https://integrate.api.nvidia.com/v1",
            "baseUrlSource": "known_provider_default",
            "requiresManualBaseUrl": False,
            "authFields": ["apiKey"],
            "requiredFields": ["apiKey", "model"],
        }
        assert "validate_runtime" in action_types
        assert validate["blockerType"] == "provider_missing_credentials"
        assert validate["payload"]["runtimeId"] == "nvidia_nim"
        assert retry["payload"]["retryTarget"] == "provider_credentials"
        assert retry["payload"]["providerId"] == "nvidia_nim"
        assert retry["payload"]["providerSetup"] == open_settings["payload"]["providerSetup"]


def test_resource_manager_privacy_blocked_offers_local_runtime_then_policy_review(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-privacy-blocked")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-resource-privacy-blocked",
            stage="resource_manager",
            reason="No AI resource satisfied policy and capability filters.",
            details={
                "resourceBlockers": [
                    {
                        "role": "product_owner",
                        "taskId": "product-owner-1",
                        "decision": {
                            "selected": None,
                            "decisionReason": ("Remote resources are blocked by the project privacy policy."),
                            "rejected": [
                                {
                                    "providerId": "codex_cli",
                                    "model": "gpt-5-codex",
                                    "runtime": "cli",
                                    "reason": "privacy_blocks_remote",
                                },
                                {
                                    "providerId": "claude_code_cli",
                                    "model": "claude-opus-4-1",
                                    "runtime": "cli",
                                    "reason": "privacy_blocks_remote",
                                },
                            ],
                        },
                    }
                ]
            },
        )

        assert {action["blockerType"] for action in created} == {"resource_manager_privacy_blocked"}
        assert [action["actionType"] for action in created] == [
            "open_settings_section",
            "open_settings_section",
            "retry_loop",
        ]
        provider_settings, routing_settings, retry = created
        assert provider_settings["id"] != routing_settings["id"]
        assert provider_settings["primary"] is True
        assert provider_settings["payload"]["section"] == "providers-cli"
        assert routing_settings["primary"] is False
        assert routing_settings["payload"]["section"] == "routing"
        assert retry["primary"] is False
        assert retry["payload"]["retryTarget"] == "resource_manager"
        assert "disable" not in " ".join(action["description"].lower() for action in created)


def test_resource_manager_privacy_blocked_requires_nonempty_rejections(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        service = BlockerRemediationService(connection, root=tmp_path)

        blocker_type = service._blocker_type(
            stage="resource_manager",
            reason="No AI resource satisfied policy and capability filters.",
            details={
                "resourceBlockers": [
                    {
                        "decision": {
                            "selected": None,
                            "rejected": [],
                        }
                    }
                ]
            },
        )

        assert blocker_type == "resource_manager_unconfigured"


def test_resource_manager_privacy_blocked_preserves_credential_precedence(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        service = BlockerRemediationService(connection, root=tmp_path)

        blocker_type = service._blocker_type(
            stage="resource_manager",
            reason="No AI resource satisfied policy and capability filters.",
            details={
                "resourceBlockers": [
                    {
                        "decision": {
                            "selected": None,
                            "rejected": [
                                {
                                    "providerId": "codex_cli",
                                    "reason": "privacy_blocks_remote",
                                },
                                {
                                    "providerId": "nvidia_nim",
                                    "reason": "Provider API key credential is missing.",
                                },
                            ],
                        }
                    }
                ]
            },
        )

        assert blocker_type == "provider_missing_credentials"


def test_resource_manager_unconfigured_remediation_carries_blocked_role_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-manager-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-resource-context",
            stage="resource_manager",
            reason=(
                "AIResourceManager could not select an approved AI resource for role "
                "backend_engineer: No selectable candidates."
            ),
            details={
                "resourceBlockers": [
                    {
                        "role": "backend_engineer",
                        "taskId": "agent_task_1",
                        "reason": "No enabled model satisfies required capabilities: code, review.",
                        "decision": {
                            "selected": None,
                            "decisionReason": "No selectable candidates.",
                            "rejected": [
                                {
                                    "providerId": "ollama",
                                    "model": "llama3.1",
                                    "runtime": "local",
                                    "reason": "missing_required_capability:code",
                                }
                            ],
                            "policyResult": {
                                "scoring": "deterministic_explainable",
                                "opaqueMlUsed": False,
                            },
                        },
                    }
                ],
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 1},
                },
                "agentTaskIds": ["agent_task_1"],
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert open_settings["blockerType"] == "resource_manager_unconfigured"
        assert open_settings["payload"]["section"] == "providers-cli"
        assert "AI resource profile" not in open_settings["description"]
        assert open_settings["payload"]["blockedRoles"] == ["backend_engineer"]
        assert open_settings["payload"]["agentTaskIds"] == ["agent_task_1"]
        assert open_settings["payload"]["teamScheduleSummary"] == {
            "schedulerVersion": 2,
            "roleCount": 1,
            "resourceDecisionBlockedCount": 1,
        }
        assert open_settings["payload"]["resourceBlockers"] == [
            {
                "role": "backend_engineer",
                "taskId": "agent_task_1",
                "reason": "No enabled model satisfies required capabilities: code, review.",
                "selected": None,
                "decisionReason": "No selectable candidates.",
                "rejected": [
                    {
                        "providerId": "ollama",
                        "model": "llama3.1",
                        "runtime": "local",
                        "reason": "missing_required_capability:code",
                    }
                ],
                "policyResult": {
                    "scoring": "deterministic_explainable",
                    "opaqueMlUsed": False,
                },
            }
        ]
        assert retry["blockerType"] == "resource_manager_unconfigured"
        assert retry["payload"]["retryTarget"] == "resource_manager"
        assert retry["payload"]["blockedRoles"] == ["backend_engineer"]
        assert retry["payload"]["agentTaskIds"] == ["agent_task_1"]
        assert retry["payload"]["resourceBlockers"] == open_settings["payload"]["resourceBlockers"]


def test_resource_manager_runtime_mapping_block_does_not_offer_approval(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-runtime-mapping")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-resource-runtime-mapping",
            stage="resource_manager",
            reason=(
                "AIResourceManager selected resources, but none of the execution roles maps "
                "to a DeveloperAgent runtime."
            ),
            details={
                "resourceBlockers": [
                    {
                        "role": "backend_engineer",
                        "taskId": "agent_task_1",
                        "reason": "Selected AI resource does not map to a DeveloperAgent runtime.",
                        "decision": {
                            "selected": {
                                "providerId": "custom_gateway",
                                "model": "custom/chat",
                                "runtime": "api",
                            },
                            "approvalRequired": False,
                            "decisionReason": (
                                "Selected AI resource does not map to a DeveloperAgent runtime."
                            ),
                            "estimatedCostUsd": 0.01,
                            "usageStatus": "not_executed",
                            "policyResult": {
                                "scoring": "deterministic_explainable",
                                "opaqueMlUsed": False,
                            },
                        },
                    }
                ],
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 1},
                },
                "agentTaskIds": ["agent_task_1"],
            },
        )

        action_types = {action["actionType"] for action in created}
        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert "approve_resource_decision" not in action_types
        assert open_settings["blockerType"] == "resource_manager_unconfigured"
        assert open_settings["payload"]["section"] == "providers-cli"
        assert open_settings["payload"]["blockedRoles"] == ["backend_engineer"]
        assert open_settings["payload"]["agentTaskIds"] == ["agent_task_1"]
        assert open_settings["payload"]["teamScheduleSummary"] == {
            "schedulerVersion": 2,
            "roleCount": 1,
            "resourceDecisionBlockedCount": 1,
        }
        assert open_settings["payload"]["resourceBlockers"] == [
            {
                "role": "backend_engineer",
                "taskId": "agent_task_1",
                "reason": "Selected AI resource does not map to a DeveloperAgent runtime.",
                "selected": {
                    "providerId": "custom_gateway",
                    "model": "custom/chat",
                    "runtime": "api",
                },
                "decisionReason": "Selected AI resource does not map to a DeveloperAgent runtime.",
                "policyResult": {
                    "scoring": "deterministic_explainable",
                    "opaqueMlUsed": False,
                },
            }
        ]
        assert retry["blockerType"] == "resource_manager_unconfigured"
        assert retry["payload"]["retryTarget"] == "resource_manager"
        assert retry["payload"]["resourceBlockers"] == open_settings["payload"]["resourceBlockers"]


def test_resource_manager_legacy_unknown_cost_policy_is_approval_required(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-legacy-approval")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-resource-legacy-approval",
            stage="resource_manager",
            reason="AIResourceManager requires an operator decision before continuing.",
            details={
                "resourceBlockers": [
                    {
                        "role": "product_owner",
                        "taskId": "po_task_1",
                        "reason": "Unknown remote cost requires operator approval.",
                        "decision": {
                            "selected": {
                                "providerId": "nvidia_nim",
                                "model": "nvidia/nemotron-coder",
                                "runtime": "api",
                            },
                            "decisionReason": "Unknown remote cost requires operator approval.",
                            "estimatedCostUsd": None,
                            "usageStatus": "not_executed",
                            "policyResult": {
                                "scoring": "deterministic_explainable",
                                "opaqueMlUsed": False,
                                "unknownCostPolicy": {
                                    "action": "require_approval",
                                    "reason": "unknown_remote_cost_requires_approval",
                                },
                            },
                        },
                    }
                ],
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "roles": [{"role": "product_owner"}],
                    "summary": {"resourceDecisionBlockedCount": 1},
                },
                "agentTaskIds": ["po_task_1"],
            },
        )

        blocker_actions = {(action["blockerType"], action["actionType"]) for action in created}
        approval = next(action for action in created if action["actionType"] == "approve_resource_decision")

        assert {action["blockerType"] for action in created} == {"resource_manager_approval_required"}
        assert ("resource_manager_approval_required", "approve_resource_decision") in blocker_actions
        assert ("resource_manager_approval_required", "open_settings_section") in blocker_actions
        assert ("resource_manager_approval_required", "retry_loop") in blocker_actions
        assert approval["payload"]["resourceApprovals"][0]["policyResult"]["unknownCostPolicy"] == {
            "action": "require_approval",
            "reason": "unknown_remote_cost_requires_approval",
        }


def test_team_scheduler_failed_remediation_carries_planning_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "team-scheduler-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-team-context",
            stage="team_scheduler",
            reason="TeamScheduler failed to persist agent assignments: missing backend profile.",
            details={
                "status": "assignment_persistence_failed",
                "productOwnerOutputId": "product-owner-output-1",
                "backlogArtifactId": "artifact-backlog-1",
                "agentTaskIds": ["agent_task_1", "agent_task_2"],
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "final",
                    "mode": "balanced",
                    "risk": "medium",
                    "roles": [
                        {"role": "backend_engineer"},
                        {"role": "qa_engineer"},
                    ],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert open_settings["blockerType"] == "team_scheduler_failed"
        assert open_settings["payload"]["section"] == "team"
        assert open_settings["payload"]["status"] == "assignment_persistence_failed"
        assert open_settings["payload"]["productOwnerOutputId"] == "product-owner-output-1"
        assert open_settings["payload"]["backlogArtifactId"] == "artifact-backlog-1"
        assert open_settings["payload"]["agentTaskIds"] == ["agent_task_1", "agent_task_2"]
        assert open_settings["payload"]["scheduledRoles"] == [
            "backend_engineer",
            "qa_engineer",
        ]
        assert open_settings["payload"]["teamScheduleSummary"] == {
            "schedulerVersion": 2,
            "roleCount": 2,
            "resourceDecisionBlockedCount": 0,
            "phase": "final",
            "mode": "balanced",
            "risk": "medium",
        }
        assert retry["blockerType"] == "team_scheduler_failed"
        assert retry["payload"]["retryTarget"] == "team_scheduler"
        assert retry["payload"]["section"] == "team"
        assert retry["payload"]["status"] == "assignment_persistence_failed"
        assert retry["payload"]["agentTaskIds"] == ["agent_task_1", "agent_task_2"]
        assert retry["payload"]["scheduledRoles"] == ["backend_engineer", "qa_engineer"]


def test_technical_lead_planning_failed_remediation_carries_planning_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "technical-lead-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-technical-lead-context",
            stage="technical_lead",
            reason="TechnicalLeadPlanner failed to generate agent_tasks: missing story ids.",
            details={
                "status": "planning_failed",
                "productOwnerOutputId": "product-owner-output-2",
                "backlogArtifactId": "artifact-backlog-2",
                "agentTaskIds": ["agent_task_3"],
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "preliminary",
                    "mode": "critical",
                    "risk": "high",
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (open_settings, retry):
            assert action["blockerType"] == "technical_lead_planning_failed"
            assert action["payload"]["section"] == "team"
            assert action["payload"]["status"] == "planning_failed"
            assert action["payload"]["productOwnerOutputId"] == "product-owner-output-2"
            assert action["payload"]["backlogArtifactId"] == "artifact-backlog-2"
            assert action["payload"]["agentTaskIds"] == ["agent_task_3"]
            assert action["payload"]["scheduledRoles"] == ["backend_engineer"]
            assert action["payload"]["teamScheduleSummary"] == {
                "schedulerVersion": 2,
                "roleCount": 1,
                "resourceDecisionBlockedCount": 0,
                "phase": "preliminary",
                "mode": "critical",
                "risk": "high",
            }
        assert retry["payload"]["retryTarget"] == "technical_lead"


def test_product_owner_output_invalid_remediation_carries_output_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "product-owner-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-product-owner-context",
            stage="product_owner",
            reason=(
                "ProductOwnerAgent returned needs_input but did not create a pending "
                "actionable thread decision with options."
            ),
            details={
                "status": "failed_validation",
                "outputStatus": "needs_input",
                "workspaceId": "workspace-po-1",
                "productOwnerOutputId": "po-output-1",
                "briefId": "brief-1",
                "artifactIds": ["artifact-po-output", "artifact-brief"],
                "clarificationQuestionIds": ["question-1"],
                "productDecisionIds": ["decision-1"],
                "pendingThreadDecisions": [],
                "reason": (
                    "ProductOwnerAgent returned needs_input but did not create a pending "
                    "actionable thread decision with options."
                ),
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert open_settings["blockerType"] == "product_owner_output_invalid"
        assert open_settings["payload"]["section"] == "team"
        assert open_settings["payload"]["status"] == "failed_validation"
        assert open_settings["payload"]["outputStatus"] == "needs_input"
        assert open_settings["payload"]["workspaceId"] == "workspace-po-1"
        assert open_settings["payload"]["productOwnerOutputId"] == "po-output-1"
        assert open_settings["payload"]["briefId"] == "brief-1"
        assert open_settings["payload"]["artifactIds"] == ["artifact-po-output", "artifact-brief"]
        assert open_settings["payload"]["clarificationQuestionIds"] == ["question-1"]
        assert open_settings["payload"]["productDecisionIds"] == ["decision-1"]
        assert open_settings["payload"]["pendingThreadDecisionCount"] == 0
        assert "actionable thread decision" in open_settings["payload"]["reason"]
        assert retry["blockerType"] == "product_owner_output_invalid"
        assert retry["payload"]["retryTarget"] == "product_owner"
        assert retry["payload"]["section"] == "team"
        assert retry["payload"]["status"] == "failed_validation"
        assert retry["payload"]["outputStatus"] == "needs_input"
        assert retry["payload"]["productOwnerOutputId"] == "po-output-1"
        assert retry["payload"]["artifactIds"] == ["artifact-po-output", "artifact-brief"]


def test_product_owner_needs_input_without_options_is_invalid_output(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "product-owner-empty-options")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-product-owner-empty-options",
            stage="product_owner",
            reason="ProductOwnerAgent returned needs_input without actionable options.",
            details={
                "status": "needs_input",
                "outputStatus": "needs_input",
                "productOwnerOutputId": "po-output-empty-options",
                "pendingDecisions": [
                    {
                        "decisionId": "thread-decision-empty-options",
                        "title": "Choose delivery path",
                        "prompt": "Choose delivery path.",
                        "options": [],
                    }
                ],
            },
        )

        action_types = {action["actionType"] for action in created}
        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert "answer_question" not in action_types
        assert {action["blockerType"] for action in created} == {"product_owner_output_invalid"}
        assert open_settings["payload"]["section"] == "team"
        assert open_settings["payload"]["status"] == "needs_input"
        assert open_settings["payload"]["outputStatus"] == "needs_input"
        assert open_settings["payload"]["productOwnerOutputId"] == "po-output-empty-options"
        assert open_settings["payload"]["pendingThreadDecisionCount"] == 1
        assert retry["payload"]["retryTarget"] == "product_owner"


def test_resource_learning_failed_remediation_carries_retry_evidence_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-learning-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-resource-learning-context",
            stage="resource_learning",
            reason="AI resource learning persistence failed: controlled storage failure.",
            details={
                "status": "persistence_failed",
                "reason": "AI resource learning persistence failed: controlled storage failure.",
                "workspaceId": "workspace_resource_learning",
                "evidenceRef": "evidence_resource_learning",
                "agentTaskIds": ["agent_task_1"],
                "review": {
                    "changedFiles": ["src/aido.py"],
                    "diffRefs": [{"kind": "git_diff", "path": "evidence/diff.patch"}],
                },
                "gitleaks": {"status": "completed", "findingCount": 0},
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "final",
                    "mode": "balanced",
                    "risk": "medium",
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        assert open_settings["blockerType"] == "resource_learning_failed"
        assert open_settings["payload"]["section"] == "routing"
        assert open_settings["payload"]["status"] == "persistence_failed"
        assert open_settings["payload"]["workspaceId"] == "workspace_resource_learning"
        assert open_settings["payload"]["evidenceRef"] == "evidence_resource_learning"
        assert open_settings["payload"]["agentTaskIds"] == ["agent_task_1"]
        assert open_settings["payload"]["scheduledRoles"] == ["backend_engineer"]
        assert open_settings["payload"]["changedFiles"] == ["src/aido.py"]
        assert open_settings["payload"]["gitleaksStatus"] == "completed"
        assert open_settings["payload"]["teamScheduleSummary"] == {
            "schedulerVersion": 2,
            "roleCount": 1,
            "resourceDecisionBlockedCount": 0,
            "phase": "final",
            "mode": "balanced",
            "risk": "medium",
        }
        assert "controlled storage failure" in open_settings["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "resource_learning"
        assert retry["payload"]["section"] == "routing"
        assert retry["payload"]["workspaceId"] == "workspace_resource_learning"
        assert retry["payload"]["evidenceRef"] == "evidence_resource_learning"
        assert retry["payload"]["agentTaskIds"] == ["agent_task_1"]
        assert retry["payload"]["scheduledRoles"] == ["backend_engineer"]
        assert retry["payload"]["changedFiles"] == ["src/aido.py"]
        assert retry["payload"]["gitleaksStatus"] == "completed"


def test_approval_unavailable_remediation_carries_delivery_evidence_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "approval-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-approval-context",
            stage="approval",
            reason="Product Loop delivery approval persistence failed: controlled storage failure.",
            details={
                "status": "persistence_failed",
                "reason": "Product Loop delivery approval persistence failed: controlled storage failure.",
                "workspaceId": "workspace_approval",
                "evidenceRefs": ["evidence_runtime", "evidence_gitleaks"],
                "diffRefs": [{"kind": "git_diff", "path": "evidence/delivery.diff"}],
                "changedFiles": ["src/aido.py", "tests/test_aido.py"],
                "gitleaks": {"status": "completed", "findingCount": 0},
                "resourceLearning": {
                    "status": "recorded",
                    "evidenceRef": "evidence_gitleaks",
                    "observationCount": 1,
                },
            },
        )

        view_diff = next(action for action in created if action["actionType"] == "view_diff")
        save_patch = next(action for action in created if action["actionType"] == "save_patch")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (view_diff, save_patch, retry):
            assert action["blockerType"] == "approval_unavailable"
            assert action["payload"]["workspaceId"] == "workspace_approval"
            assert action["payload"]["evidenceRefs"] == ["evidence_runtime", "evidence_gitleaks"]
            assert action["payload"]["diffRefs"] == [{"kind": "git_diff", "path": "evidence/delivery.diff"}]
            assert action["payload"]["changedFiles"] == ["src/aido.py", "tests/test_aido.py"]
            assert action["payload"]["gitleaksStatus"] == "completed"
            assert action["payload"]["resourceLearning"] == {
                "status": "recorded",
                "evidenceRef": "evidence_gitleaks",
                "observationCount": 1,
            }
            assert "controlled storage failure" in action["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "delivery_approval"


def test_review_diff_unavailable_remediation_carries_review_recovery_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "review-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-review-context",
            stage="review",
            reason="Runtime completed but review diff capture returned no changed files.",
            details={
                "status": "diff_unavailable",
                "reason": "Runtime completed but review diff capture returned no changed files.",
                "workspaceId": "workspace_review",
                "workspacePath": "C:/workspace/review",
                "runtimeStatus": "completed",
                "agentTaskIds": ["agent_task_1"],
                "runtimeResult": {
                    "status": "completed",
                    "qaResults": [{"command": "uv run pytest", "status": "passed"}],
                    "review": {"changedFiles": []},
                },
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "final",
                    "mode": "balanced",
                    "risk": "medium",
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        view_diff = next(action for action in created if action["actionType"] == "view_diff")
        save_patch = next(action for action in created if action["actionType"] == "save_patch")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (view_diff, save_patch, retry):
            assert action["blockerType"] == "review_diff_unavailable"
            assert action["payload"]["status"] == "diff_unavailable"
            assert action["payload"]["workspaceId"] == "workspace_review"
            assert action["payload"]["workspacePath"] == "C:/workspace/review"
            assert action["payload"]["runtimeStatus"] == "completed"
            assert action["payload"]["agentTaskIds"] == ["agent_task_1"]
            assert action["payload"]["scheduledRoles"] == ["backend_engineer"]
            assert action["payload"]["qaResultCount"] == 1
            assert action["payload"]["changedFiles"] == []
            assert action["payload"]["teamScheduleSummary"] == {
                "schedulerVersion": 2,
                "roleCount": 1,
                "resourceDecisionBlockedCount": 0,
                "phase": "final",
                "mode": "balanced",
                "risk": "medium",
            }
            assert "no changed files" in action["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "review_diff"


def test_gitleaks_failed_remediation_carries_security_recovery_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "gitleaks-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-gitleaks-context",
            stage="gitleaks",
            reason="gitleaks blocked delivery after detecting a secret.",
            details={
                "status": "blocked",
                "reason": "gitleaks blocked delivery after detecting a secret.",
                "workspaceId": "workspace_gitleaks",
                "workspacePath": "C:/workspace/gitleaks",
                "runtimeStatus": "completed",
                "agentTaskIds": ["agent_task_1"],
                "runtimeResult": {
                    "status": "completed",
                    "qaResults": [{"command": "uv run pytest", "status": "passed"}],
                },
                "review": {
                    "changedFiles": ["src/aido.py"],
                    "diffRefs": [{"kind": "git_diff", "path": "evidence/gitleaks.diff"}],
                },
                "gitleaks": {"status": "blocked", "findingCount": 1},
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "final",
                    "mode": "balanced",
                    "risk": "high",
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        run_gitleaks = next(action for action in created if action["actionType"] == "run_gitleaks")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (run_gitleaks, retry):
            assert action["blockerType"] == "gitleaks_failed"
            assert action["payload"]["status"] == "blocked"
            assert action["payload"]["workspaceId"] == "workspace_gitleaks"
            assert action["payload"]["workspacePath"] == "C:/workspace/gitleaks"
            assert action["payload"]["runtimeStatus"] == "completed"
            assert action["payload"]["agentTaskIds"] == ["agent_task_1"]
            assert action["payload"]["scheduledRoles"] == ["backend_engineer"]
            assert action["payload"]["changedFiles"] == ["src/aido.py"]
            assert action["payload"]["diffRefs"] == [{"kind": "git_diff", "path": "evidence/gitleaks.diff"}]
            assert action["payload"]["gitleaksStatus"] == "blocked"
            assert action["payload"]["gitleaksFindingCount"] == 1
            assert action["payload"]["qaResultCount"] == 1
            assert action["payload"]["teamScheduleSummary"] == {
                "schedulerVersion": 2,
                "roleCount": 1,
                "resourceDecisionBlockedCount": 0,
                "phase": "final",
                "mode": "balanced",
                "risk": "high",
            }
            assert "detecting a secret" in action["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "gitleaks"


def test_qa_failed_remediation_carries_qa_recovery_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "qa-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-qa-context",
            stage="qa",
            reason="QA results must all pass before Product Loop delivery approval.",
            details={
                "status": "qa_blocked",
                "reason": "QA results must all pass before Product Loop delivery approval.",
                "workspaceId": "workspace_qa",
                "workspacePath": "C:/workspace/qa",
                "runtimeStatus": "evidence_ready",
                "qaVerdict": "blocked",
                "qaResults": [
                    {"command": "uv run pytest", "status": "passed"},
                    {
                        "command": "uv run ruff check",
                        "status": "failed",
                        "reason": "F401 unused import",
                    },
                ],
                "nonPassingQaResults": [
                    {
                        "command": "uv run ruff check",
                        "status": "failed",
                        "reason": "F401 unused import",
                    }
                ],
                "agentTaskIds": ["agent_task_1"],
                "review": {"changedFiles": ["src/aido.py"]},
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "final",
                    "mode": "balanced",
                    "risk": "medium",
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        continue_plan = next(action for action in created if action["actionType"] == "continue_plan_only")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (continue_plan, retry):
            assert action["blockerType"] == "qa_failed"
            assert action["payload"]["status"] == "qa_blocked"
            assert action["payload"]["workspaceId"] == "workspace_qa"
            assert action["payload"]["workspacePath"] == "C:/workspace/qa"
            assert action["payload"]["runtimeStatus"] == "evidence_ready"
            assert action["payload"]["qaVerdict"] == "blocked"
            assert action["payload"]["qaResultCount"] == 2
            assert action["payload"]["nonPassingQaResultCount"] == 1
            assert action["payload"]["nonPassingQaResults"] == [
                {
                    "command": "uv run ruff check",
                    "status": "failed",
                    "reason": "F401 unused import",
                }
            ]
            assert action["payload"]["agentTaskIds"] == ["agent_task_1"]
            assert action["payload"]["scheduledRoles"] == ["backend_engineer"]
            assert action["payload"]["changedFiles"] == ["src/aido.py"]
            assert action["payload"]["teamScheduleSummary"] == {
                "schedulerVersion": 2,
                "roleCount": 1,
                "resourceDecisionBlockedCount": 0,
                "phase": "final",
                "mode": "balanced",
                "risk": "medium",
            }
            assert "QA results must all pass" in action["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "qa"


def test_runtime_output_invalid_remediation_carries_runtime_recovery_context(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "runtime-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-runtime-context",
            stage="runtime",
            reason="Runtime ended with status failed.",
            details={
                "status": "failed",
                "reason": "Runtime ended with status failed.",
                "workspaceId": "workspace_runtime",
                "workspacePath": "C:/workspace/runtime",
                "runtimeStatus": "failed",
                "selectedRuntimeId": "codex_cli",
                "providerId": "ollama",
                "model": "qwen2.5-coder",
                "runtime": {"id": "controlled_test_runtime", "executable": True},
                "agentTaskIds": ["agent_task_1"],
                "qaResults": [{"command": "uv run pytest", "status": "passed"}],
                "review": {"changedFiles": ["src/aido.py"]},
                "teamSchedule": {
                    "schedulerVersion": 2,
                    "phase": "final",
                    "mode": "balanced",
                    "risk": "medium",
                    "roles": [{"role": "backend_engineer"}],
                    "summary": {"resourceDecisionBlockedCount": 0},
                },
            },
        )

        continue_plan = next(action for action in created if action["actionType"] == "continue_plan_only")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (continue_plan, retry):
            assert action["blockerType"] == "runtime_output_invalid"
            assert action["payload"]["status"] == "failed"
            assert action["payload"]["workspaceId"] == "workspace_runtime"
            assert action["payload"]["workspacePath"] == "C:/workspace/runtime"
            assert action["payload"]["runtimeStatus"] == "failed"
            assert action["payload"]["selectedRuntimeId"] == "codex_cli"
            assert action["payload"]["providerId"] == "ollama"
            assert action["payload"]["model"] == "qwen2.5-coder"
            assert action["payload"]["runtimeId"] == "controlled_test_runtime"
            assert action["payload"]["agentTaskIds"] == ["agent_task_1"]
            assert action["payload"]["scheduledRoles"] == ["backend_engineer"]
            assert action["payload"]["qaResultCount"] == 1
            assert action["payload"]["changedFiles"] == ["src/aido.py"]
            assert action["payload"]["teamScheduleSummary"] == {
                "schedulerVersion": 2,
                "roleCount": 1,
                "resourceDecisionBlockedCount": 0,
                "phase": "final",
                "mode": "balanced",
                "risk": "medium",
            }
            assert "Runtime ended with status failed" in action["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "runtime"


def test_git_dirty_tree_remediation_carries_git_status_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "git-dirty-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-git-dirty-context",
            stage="git",
            reason="Project git tree is dirty; Product Loop execution requires a clean base.",
            details={
                "status": "completed",
                "reason": "git status collected",
                "dirty": True,
                "branch": "dev",
                "changedFiles": ["README.md"],
                "stagedFiles": ["src/staged.py"],
                "untrackedFiles": ["notes.local.md"],
                "remotes": [{"name": "origin", "url": "git@example.invalid:aido/aido.git"}],
            },
        )

        view_diff = next(action for action in created if action["actionType"] == "view_diff")
        create_branch = next(action for action in created if action["actionType"] == "create_branch")
        save_patch = next(action for action in created if action["actionType"] == "save_patch")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (view_diff, create_branch, save_patch, retry):
            assert action["blockerType"] == "git_dirty_tree"
            assert action["payload"]["status"] == "completed"
            assert action["payload"]["dirty"] is True
            assert action["payload"]["branch"] == "dev"
            assert action["payload"]["changedFiles"] == ["README.md"]
            assert action["payload"]["stagedFiles"] == ["src/staged.py"]
            assert action["payload"]["untrackedFiles"] == ["notes.local.md"]
            assert action["payload"]["remoteNames"] == ["origin"]
            assert action["payload"]["dirtyFileCount"] == 3
            assert "git status collected" in action["payload"]["reason"]
        assert create_branch["payload"]["branchName"] == "codex/remediate-dirty-tree"
        assert retry["payload"]["retryTarget"] == "git_dirty_tree"


def test_git_not_initialized_remediation_carries_init_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "git-init-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-git-init-context",
            stage="git",
            reason="Project path is not a Git repository.",
            details={
                "status": "configuration_required",
                "reason": "Project path is not a Git repository.",
                "projectId": project["id"],
                "dirty": False,
                "changedFiles": [],
                "stagedFiles": [],
                "untrackedFiles": [],
            },
        )

        git_init = next(action for action in created if action["actionType"] == "git_init")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (git_init, retry):
            assert action["blockerType"] == "git_not_initialized"
            assert action["payload"]["status"] == "configuration_required"
            assert action["payload"]["projectId"] == project["id"]
            assert action["payload"]["dirty"] is False
            assert action["payload"]["changedFiles"] == []
            assert action["payload"]["stagedFiles"] == []
            assert action["payload"]["untrackedFiles"] == []
            assert action["payload"]["dirtyFileCount"] == 0
            assert "not a Git repository" in action["payload"]["reason"]
        assert git_init["payload"]["defaultBranch"] == "dev"
        assert retry["payload"]["retryTarget"] == "git_not_initialized"


def test_git_status_failed_remediation_carries_status_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "git-status-failed-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-git-status-failed-context",
            stage="git",
            reason="Git workspace status check failed: controlled git status crashed",
            details={
                "status": "failed",
                "reason": "Git workspace status check failed: controlled git status crashed",
                "projectId": project["id"],
                "dirty": False,
                "changedFiles": [],
                "stagedFiles": [],
                "untrackedFiles": [],
            },
        )

        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (open_settings, retry):
            assert action["blockerType"] == "git_status_failed"
            assert action["payload"]["status"] == "failed"
            assert action["payload"]["projectId"] == project["id"]
            assert action["payload"]["dirty"] is False
            assert action["payload"]["changedFiles"] == []
            assert action["payload"]["stagedFiles"] == []
            assert action["payload"]["untrackedFiles"] == []
            assert action["payload"]["dirtyFileCount"] == 0
            assert "controlled git status crashed" in action["payload"]["reason"]
        assert open_settings["payload"]["section"] == "workspaces"
        assert retry["payload"]["retryTarget"] == "git_status_failed"


def test_resource_manager_unhealthy_provider_opens_provider_health_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "resource-manager-provider-health")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-resource-health",
            stage="resource_manager",
            reason="AIResourceManager could not select a resource because provider health is unhealthy.",
            details={
                "resourceBlockers": [
                    {
                        "role": "backend_engineer",
                        "reason": "Provider openrouter health is unhealthy.",
                        "decision": {
                            "selected": None,
                            "decisionReason": "Provider openrouter health is unhealthy.",
                            "rejected": [
                                {
                                    "providerId": "openrouter",
                                    "model": "anthropic/claude-sonnet-4",
                                    "runtime": "api",
                                    "reason": "Provider health is unhealthy.",
                                }
                            ],
                        },
                    }
                ]
            },
        )

        action_types = {action["actionType"] for action in created}
        validate = next(action for action in created if action["actionType"] == "validate_runtime")
        switch = next(action for action in created if action["actionType"] == "switch_runtime")
        retry = next(action for action in created if action["actionType"] == "retry_loop")
        assert {"validate_runtime", "switch_runtime", "retry_loop"} <= action_types
        assert validate["blockerType"] == "provider_health_failed"
        assert validate["payload"]["runtimeId"] == "openrouter"
        assert switch["payload"]["runtimeId"] == "openrouter"
        assert retry["payload"]["runtimeId"] == "openrouter"
        assert retry["payload"]["retryTarget"] == "provider_health"


def test_research_network_block_creates_network_access_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "research-network")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-research-network",
            stage="research",
            reason="ResearchAgent web search failed: urlopen timed out.",
            details={
                "status": "research_blocked",
                "remediation": {"action": "check_network_access"},
            },
        )

        blocker_actions = {(action["blockerType"], action["actionType"]) for action in created}
        network_check = next(action for action in created if action["actionType"] == "check_network_access")
        assert {
            ("research_required", "check_network_access"),
            ("research_required", "run_worker_once"),
            ("research_required", "retry_loop"),
        } <= blocker_actions
        assert network_check["primary"] is True
        assert network_check["destructive"] is False
        assert network_check["confirmationRequired"] is False
        assert network_check["payload"]["section"] == "internet"


def test_research_required_remediation_carries_job_and_policy_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "research-context")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-research-context",
            stage="research",
            reason="ResearchAgent evidence is required before accepting high-impact decisions.",
            details={
                "status": "research_required",
                "jobId": "job-research-1",
                "researchStatus": "research_required",
                "decisions": [
                    {
                        "title": "Database migration strategy",
                        "category": "technical",
                        "impact": "high",
                    }
                ],
                "researchPolicy": {
                    "requireForHighImpactTechnicalDecisions": True,
                    "allowWebSearch": True,
                },
            },
        )

        run_worker = next(action for action in created if action["actionType"] == "run_worker_once")
        retry = next(action for action in created if action["actionType"] == "retry_loop")

        for action in (run_worker, retry):
            assert action["blockerType"] == "research_required"
            assert action["payload"]["jobId"] == "job-research-1"
            assert action["payload"]["researchStatus"] == "research_required"
            assert action["payload"]["decisionCount"] == 1
            assert action["payload"]["researchPolicy"] == {
                "requireForHighImpactTechnicalDecisions": True,
                "allowWebSearch": True,
            }
        assert retry["payload"]["retryTarget"] == "research"


def test_research_required_without_product_loop_does_not_create_dead_retry_action(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "research-no-loop")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=None,
            stage="research",
            reason="ResearchAgent web search failed: urlopen timed out.",
            details={
                "status": "research_blocked",
                "jobId": "job-research-no-loop",
                "remediation": {"action": "check_network_access"},
            },
        )

        action_types = {action["actionType"] for action in created}
        assert {"check_network_access", "run_worker_once"} <= action_types
        assert "retry_loop" not in action_types


def test_checkout_branch_remediation_requires_confirmation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "checkout-confirm")
        service = BlockerRemediationService(connection, root=tmp_path)
        service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-checkout",
            stage="git",
            reason="Working branch required before execution.",
            details={},
        )
        checkout = next(
            item
            for item in service.repository.list_for_thread(thread["id"])
            if item["actionType"] == "checkout_branch"
        )
        assert checkout["destructive"] is True
        assert checkout["confirmationRequired"] is True

        blocked = service.execute(checkout["id"], platform=None)
        assert blocked["execution"]["status"] == "blocked"
        assert blocked["execution"]["confirmationRequired"] is True
        assert blocked["remediation"]["status"] == "pending"

        confirmed = service.execute(checkout["id"], platform=None, payload={"confirmed": True})
        # Passed the confirmation gate; now blocked only because no branch name was supplied.
        assert confirmed["execution"].get("confirmationRequired") is not True
        assert "branchname" in str(confirmed["execution"].get("reason", "")).lower()


def test_product_owner_output_invalid_offers_validate_and_switch_runtime(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "product-owner-runtime")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-product-owner-runtime",
            stage="product_owner",
            reason="ProductOwnerAgent returned a brief that failed schema validation.",
            details={
                "status": "failed_validation",
                "outputStatus": "failed_validation",
                "runtimeId": "ollama",
            },
        )

        actions = _pending_action_types(connection, thread["id"])
        assert ("product_owner_output_invalid", "validate_runtime") in actions
        assert ("product_owner_output_invalid", "switch_runtime") in actions

        validate = next(action for action in created if action["actionType"] == "validate_runtime")
        switch = next(action for action in created if action["actionType"] == "switch_runtime")
        open_settings = next(action for action in created if action["actionType"] == "open_settings_section")

        # The runtime behind ProductOwnerAgent is the likely culprit, so revalidating it leads the card.
        assert validate["primary"] is True
        assert open_settings["primary"] is False
        for action in (validate, switch):
            assert action["payload"]["runtimeId"] == "ollama"
            assert action["payload"]["settingsSection"] == "providers-cli"
            assert action["technicalReason"] == (
                "ProductOwnerAgent returned a brief that failed schema validation."
            )


def test_remediation_records_carry_primary_destructive_and_technical_reason(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "remediation-fields")
        service = BlockerRemediationService(connection, root=tmp_path)

        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-fields",
            stage="runtime",
            reason="Runtime is not executable.",
            details={"executable": False},
        )

        assert created
        for record in created:
            assert "technicalReason" in record
            assert "primary" in record
            assert "destructive" in record
            assert "confirmationRequired" in record
            assert record["technicalReason"] == "Runtime is not executable."
            assert record["destructive"] is False
        assert created[0]["primary"] is True


def test_blocked_thread_without_a_blocked_loop_still_gets_a_retry_action(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "blocked-thread-no-blocked-loop")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        loop = coordinator.start(project_id=project["id"], title="Diverged state loop")

        # Divergencia: el loop dejó de estar "blocked" (cascada de reintentos + worker detenido),
        # pero conserva el durable del bloqueo y el hilo sigue marcado como blocked.
        durable = {
            "durableRun": {
                "thread": {"projectThreadId": thread["id"]},
                "status": "blocked",
                "blockedStage": "product_owner",
                "blockedReason": (
                    "ProductOwnerAgent output must be a JSON object: questions[0].category "
                    "must be one of ['compliance', 'data', 'delivery']."
                ),
            }
        }
        connection.execute(
            "UPDATE product_loops SET state = 'cancelled', context = ? WHERE id = ?",
            (json.dumps(durable), loop["id"]),
        )
        ThreadsRepository(connection).set_status(thread["id"], "blocked")

        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.list_for_thread(thread_id=thread["id"])

        assert any(action["actionType"] == "retry_loop" for action in actions)
