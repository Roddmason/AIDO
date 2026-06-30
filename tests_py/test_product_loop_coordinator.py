from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_control_center.product_loop.coordinator import (
    ProductLoopCoordinator,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

LOOP_TABLES = {"product_loops", "product_loop_transitions", "product_loop_feedback"}
# The canonical happy path: goal_received → … → delivered (delivery only via awaiting_approval).
HAPPY_PATH = [
    "discovering",
    "brief_ready",
    "architecture_review",
    "backlog_ready",
    "iteration_planning",
    "executing",
    "quality_review",
    "awaiting_approval",
    "delivered",
]


class _RuntimeUnavailable:
    def __init__(self) -> None:
        self.run_payloads: list[dict[str, Any]] = []

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        return {
            "executable": False,
            "selectedRuntimeId": preferred_runtime or "codex_cli",
            "reason": "No executable runtime is configured.",
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        raise AssertionError("runtime must not execute when readiness is unavailable")


class _ControlledRuntime:
    def __init__(self, *, status: str = "completed") -> None:
        self.status_value = status
        self.run_payloads: list[dict[str, Any]] = []

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        return {
            "executable": True,
            "selectedRuntimeId": preferred_runtime or "controlled_test_runtime",
            "reason": "Controlled test runtime is executable.",
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        return {
            "status": self.status_value,
            "reason": f"controlled runtime returned {self.status_value}",
            "runtime": {"id": "controlled_test_runtime", "executable": True},
            "runtimeResult": {"status": "completed"},
            "agentRun": {"id": "agent-run-controlled"},
            "job": {"id": "job-controlled"},
            "evidencePackage": {"id": "evidence-controlled", "qaVerdict": "passed"},
            "qaResults": [{"command": "controlled qa", "status": "passed"}],
            "diffSummary": {"changedFiles": ["src/app.py"]},
        }


class _GitGate:
    def __init__(self, *, dirty: bool = False, gitleaks_status: str = "completed") -> None:
        self.dirty = dirty
        self.gitleaks_status = gitleaks_status
        self.status_calls = 0
        self.gitleaks_calls = 0

    def status(self, project_id: str) -> dict[str, Any]:
        self.status_calls += 1
        return {
            "status": "completed",
            "reason": "git status collected",
            "projectId": project_id,
            "dirty": self.dirty,
            "changedFiles": ["README.md"] if self.dirty else [],
            "untrackedFiles": [],
            "stagedFiles": [],
            "toolCalls": [],
            "policyDecisionIds": [],
        }

    def gitleaks_scan(self, project_id: str) -> dict[str, Any]:
        self.gitleaks_calls += 1
        blocked = self.gitleaks_status != "completed"
        return {
            "status": self.gitleaks_status,
            "reason": "gitleaks blocked delivery" if blocked else "gitleaks passed",
            "projectId": project_id,
            "deliveryBlocked": blocked,
            "gitleaks": {
                "status": "blocked" if blocked else "passed",
                "findingCount": 1 if blocked else 0,
            },
        }


def _workspace_project(connection, tmp_path: Path, name: str) -> dict:
    project_path = tmp_path / name
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    return ProjectsRepository(connection).create_project(name=name, path=project_path, template_id="other")


def _project(connection, tmp_path: Path, name: str) -> dict:
    return ProjectsRepository(connection).create_project(name=name, path=tmp_path / name, template_id="other")


def test_product_loop_schema_adds_tables_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase19_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 19"
        ).fetchone()["total"]

    assert tables >= LOOP_TABLES
    assert phase19_rows == 1


def test_product_loop_walks_the_happy_path_to_delivered(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "happy")
        coordinator = ProductLoopCoordinator(connection)

        loop = coordinator.start(project_id=project["id"], title="Onboarding")
        assert loop["state"] == "goal_received"
        assert loop["status"] == "active"
        assert loop["version"] == 1

        for state in HAPPY_PATH:
            loop = coordinator.transition(loop["id"], to_state=state)

        assert loop["state"] == "delivered"
        assert loop["status"] == "delivered"
        assert loop["version"] == 1 + len(HAPPY_PATH)
        resume = coordinator.resume(loop["id"])
        assert resume["resumable"] is False
        assert resume["allowedNextStates"] == []
        assert [t["toState"] for t in coordinator.list_transitions(loop["id"])] == [
            "goal_received",
            *HAPPY_PATH,
        ]


def test_product_loop_is_durable_and_resumes_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"

    # --- AIDO session 1: start the loop (with governance) and advance it, then "shut down". ---
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "durable")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(
            project_id=project_id,
            title="Self-serve",
            context={"idea": "self-serve onboarding"},
            correlation_id="corr-7",
            budget={"agentRuns": 9},
            max_rework_rounds=2,
        )
        coordinator.transition(loop["id"], to_state="discovering", trigger="discovery_started")
        coordinator.transition(loop["id"], to_state="awaiting_user", context_patch={"openQuestions": 3})
        loop_id = loop["id"]

    # --- Restart AIDO: a brand-new connection and coordinator, nothing kept in memory. ---
    with open_sqlite_connection(db_path) as connection:
        coordinator = ProductLoopCoordinator(connection)
        resumed = coordinator.resume(loop_id)
        assert resumed["loop"]["state"] == "awaiting_user"  # recovered straight from the database
        assert resumed["loop"]["version"] == 3
        # Domain context and governance both survive the restart.
        assert resumed["loop"]["context"]["idea"] == "self-serve onboarding"
        assert resumed["loop"]["context"]["openQuestions"] == 3
        assert resumed["loop"]["context"]["fsm"]["correlationId"] == "corr-7"
        assert resumed["loop"]["context"]["fsm"]["policy"]["budget"] == {"agentRuns": 9}
        assert resumed["loop"]["context"]["fsm"]["policy"]["maxReworkRounds"] == 2
        assert resumed["resumable"] is True
        assert "discovering" in resumed["allowedNextStates"]

        # The loop continues exactly where it left off before the restart.
        coordinator.transition(loop_id, to_state="discovering", trigger="user_answered")
        loop = coordinator.transition(loop_id, to_state="brief_ready", trigger="discovery_completed")
        assert loop["state"] == "brief_ready"
        assert [t["toState"] for t in coordinator.list_transitions(loop_id)] == [
            "goal_received",
            "discovering",
            "awaiting_user",
            "discovering",
            "brief_ready",
        ]
        assert coordinator.list_loops(project_id)[0]["id"] == loop_id


def test_run_user_message_blocks_new_loop_when_runtime_is_not_executable(tmp_path: Path) -> None:
    runtime = _RuntimeUnavailable()
    git = _GitGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "no-runtime")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard.",
            runtime_runner=runtime,
            git_service=git,
        )

        assert result["status"] == "blocked"
        assert "runtime" in result["reason"].lower()
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedReason"] == result["reason"]
        assert result["loop"]["context"]["durableRun"]["thread"]["sessionId"].startswith("session-")
        assert result["loop"]["context"]["durableRun"]["evidencePackageIds"]
        assert runtime.run_payloads == []
        assert [item["toState"] for item in result["transitions"]] == [
            "goal_received",
            "workspace_check",
            "runtime_check",
            "blocked",
        ]


def test_run_user_message_with_controlled_runtime_executes_and_awaits_approval(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "controlled-runtime")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement an auditable onboarding dashboard.",
            preferred_runtime="controlled_test_runtime",
            qa_commands=[["python", "--version"]],
            runtime_runner=runtime,
            git_service=git,
        )

        assert result["status"] == "awaiting_approval"
        assert result["loop"]["state"] == "awaiting_approval"
        assert result["loop"]["context"]["durableRun"]["workspaceId"].startswith("workspace-")
        assert result["loop"]["context"]["durableRun"]["agentAssignments"]
        assert runtime.run_payloads[0]["projectId"] == project["id"]
        assert runtime.run_payloads[0]["workspaceId"].startswith("workspace-")
        assert runtime.run_payloads[0]["instruction"] == "Implement an auditable onboarding dashboard."
        assert git.gitleaks_calls == 1
        assert [item["toState"] for item in result["transitions"]] == [
            "goal_received",
            "workspace_check",
            "runtime_check",
            "git_check",
            "discovery",
            "planning",
            "backlog_ready",
            "branch_ready",
            "executing",
            "qa_running",
            "security_running",
            "review_ready",
            "awaiting_approval",
        ]


def test_run_user_message_blocks_dirty_git_before_runtime_execution(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate(dirty=True)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "dirty")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement on a dirty repository.",
            runtime_runner=runtime,
            git_service=git,
        )

        assert result["status"] == "blocked"
        assert "dirty" in result["reason"].lower()
        assert result["loop"]["state"] == "blocked"
        assert runtime.run_payloads == []
        assert [item["toState"] for item in result["transitions"]] == [
            "goal_received",
            "workspace_check",
            "runtime_check",
            "git_check",
            "blocked",
        ]


def test_run_user_message_blocks_when_gitleaks_fails(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate(gitleaks_status="blocked")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "gitleaks")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement with a secret leak.",
            runtime_runner=runtime,
            git_service=git,
        )

        assert result["status"] == "blocked"
        assert "gitleaks" in result["reason"].lower()
        assert result["loop"]["state"] == "blocked"
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1
        assert result["loop"]["context"]["durableRun"]["gitleaks"]["deliveryBlocked"] is True


def test_run_user_message_opens_rework_when_qa_fails(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(status="qa_failed")
    git = _GitGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "qa-fail")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement but QA fails.",
            runtime_runner=runtime,
            git_service=git,
        )

        assert result["status"] == "reworking"
        assert result["loop"]["state"] == "reworking"
        assert result["loop"]["context"]["durableRun"]["rework"]["source"] == "qa"
        assert result["loop"]["context"]["durableRun"]["rework"]["reason"] == result["reason"]
        assert "reworking" in [item["toState"] for item in result["transitions"]]


def test_run_user_message_recovers_persisted_state_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    runtime = _ControlledRuntime()
    git = _GitGate()
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "restart")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        result = coordinator.run_user_message(
            project_id=project_id,
            message="Implement and persist state.",
            runtime_runner=runtime,
            git_service=git,
        )
        loop_id = result["loop"]["id"]
        expected_context = result["loop"]["context"]["durableRun"]

    with open_sqlite_connection(db_path) as connection:
        resumed = ProductLoopCoordinator(connection, root=tmp_path).resume(loop_id)

    assert resumed["loop"]["state"] == "awaiting_approval"
    assert resumed["loop"]["context"]["durableRun"]["thread"] == expected_context["thread"]
    assert resumed["loop"]["context"]["durableRun"]["workspaceId"] == expected_context["workspaceId"]
    assert resumed["loop"]["context"]["durableRun"]["evidencePackageIds"]
    assert resumed["transitions"][-1]["toState"] == "awaiting_approval"


def test_invalid_and_unknown_transitions_are_rejected(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "invalid")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.transition(loop["id"], to_state="delivered")
        with pytest.raises(ProductLoopTransitionError, match="Unknown product loop state"):
            coordinator.transition(loop["id"], to_state="not_a_state")
        # The loop never moved off its initial state after the rejected transitions.
        assert coordinator.get(loop["id"])["state"] == "goal_received"
        assert coordinator.get(loop["id"])["version"] == 1


def test_optimistic_version_guard_blocks_stale_writes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "version")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        with pytest.raises(ProductLoopTransitionError, match="version mismatch"):
            coordinator.transition(loop["id"], to_state="discovering", expected_version=99)
        moved = coordinator.transition(loop["id"], to_state="discovering", expected_version=1)
        assert moved["version"] == 2


def test_block_unblock_and_cancel_paths(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "block")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")
        coordinator.transition(loop["id"], to_state="discovering")

        blocked = coordinator.block(loop["id"], reason="Missing repository access")
        assert blocked["state"] == "blocked"
        assert blocked["status"] == "blocked"
        assert blocked["previousState"] == "discovering"

        # A blocked loop can be cancelled outright (terminal), without unblocking first.
        cancelled = coordinator.cancel(loop["id"], reason="Deprioritised")
        assert cancelled["state"] == "cancelled"
        assert cancelled["status"] == "cancelled"
        assert coordinator.resume(loop["id"])["resumable"] is False
        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.transition(loop["id"], to_state="discovering")


def test_unblock_resumes_the_pre_block_state(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "unblock")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")
        coordinator.transition(loop["id"], to_state="discovering")
        coordinator.block(loop["id"], reason="paused")

        resumed = coordinator.unblock(loop["id"])
        assert resumed["state"] == "discovering"
        assert resumed["status"] == "active"


def test_cancel_is_reachable_from_an_active_state_and_is_terminal(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "cancel")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        cancelled = coordinator.cancel(loop["id"], reason="Out of scope", actor="operator")
        assert cancelled["state"] == "cancelled"
        # A delivered/cancelled loop is terminal.
        loop2 = coordinator.start(project_id=project["id"], title="Y")
        for state in HAPPY_PATH:
            loop2 = coordinator.transition(loop2["id"], to_state=state)
        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.cancel(loop2["id"], reason="too late")


def test_correlation_id_is_persisted_on_loop_and_transitions(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "correlation")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", correlation_id="loop-corr")

        coordinator.transition(loop["id"], to_state="discovering", correlation_id="event-1")
        coordinator.transition(loop["id"], to_state="brief_ready")  # falls back to the loop correlation id

        transitions = coordinator.list_transitions(loop["id"])
        assert transitions[0]["metadata"]["correlationId"] == "loop-corr"  # initial transition
        assert transitions[1]["metadata"]["correlationId"] == "event-1"  # explicit per-event id
        assert transitions[2]["metadata"]["correlationId"] == "loop-corr"  # inherited from the loop


def test_budget_consumption_is_metered_off_the_fsm_and_stops_the_loop(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "budget")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", budget={"agentRuns": 2})

        coordinator.record_usage(loop["id"], {"agentRuns": 1})
        metered = coordinator.record_usage(loop["id"], {"agentRuns": 1})

        # Metering does NOT advance the FSM version nor add transitions (the log stays state-only).
        assert metered["version"] == 1
        assert metered["context"]["fsm"]["usage"]["consumed"] == {"agentRuns": 2}
        assert [t["toState"] for t in coordinator.list_transitions(loop["id"])] == ["goal_received"]

        fired = coordinator.evaluate_stop_conditions(loop["id"])
        assert any(item["condition"] == "budget_exhausted" for item in fired)

        result = coordinator.enforce_stop_conditions(loop["id"])
        assert result["loop"]["state"] == "blocked"
        assert any(item["condition"] == "budget_exhausted" for item in result["fired"])
        assert "budget_exhausted" in result["loop"]["context"]["fsm"]["usage"]["stoppedReason"]


def test_usage_optimistic_guard_rejects_stale_metering(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "usageseq")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", budget={"costUsd": 10})

        coordinator.record_usage(loop["id"], {"costUsd": 1}, expected_usage_seq=0)
        with pytest.raises(ProductLoopTransitionError, match="usage version mismatch"):
            coordinator.record_usage(loop["id"], {"costUsd": 1}, expected_usage_seq=0)


def test_state_timeout_stops_the_loop_when_the_deadline_passes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "timeout")
        coordinator = ProductLoopCoordinator(connection)
        start = "2026-01-01T00:00:00.000Z"
        loop = coordinator.start(
            project_id=project["id"], title="X", timeouts={"goal_received": 60}, now=start
        )
        assert loop["context"]["fsm"]["usage"]["stateDeadline"] == "2026-01-01T00:01:00.000Z"

        # Before the deadline: nothing fires.
        assert coordinator.evaluate_stop_conditions(loop["id"], now="2026-01-01T00:00:30.000Z") == []
        # After the deadline: the state timeout fires and enforcing blocks the loop.
        result = coordinator.enforce_stop_conditions(loop["id"], now="2026-01-01T00:02:00.000Z")
        assert result["loop"]["state"] == "blocked"
        assert any(item["condition"] == "state_timeout" for item in result["fired"])


def test_loop_deadline_stops_the_loop(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "deadline")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(
            project_id=project["id"],
            title="X",
            deadline="2026-01-01T00:00:00.000Z",
            now="2025-12-31T23:00:00.000Z",
        )
        result = coordinator.enforce_stop_conditions(loop["id"], now="2026-01-02T00:00:00.000Z")
        assert result["loop"]["state"] == "blocked"
        assert any(item["condition"] == "deadline_exceeded" for item in result["fired"])


def test_maximum_rework_rounds_are_enforced(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "rework")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", max_rework_rounds=1)
        loop_id = loop["id"]
        # Walk to the first review.
        for state in [
            "discovering",
            "brief_ready",
            "architecture_review",
            "backlog_ready",
            "iteration_planning",
            "executing",
            "quality_review",
        ]:
            coordinator.transition(loop_id, to_state=state)

        reworked = coordinator.transition(loop_id, to_state="reworking")  # round 1 (allowed)
        assert reworked["context"]["fsm"]["usage"]["reworkRounds"] == 1
        assert any(
            item["condition"] == "max_rework_reached"
            for item in coordinator.evaluate_stop_conditions(loop_id)
        )

        # A second rework round would exceed the maximum and is rejected.
        coordinator.transition(loop_id, to_state="executing")
        coordinator.transition(loop_id, to_state="quality_review")
        with pytest.raises(ProductLoopStopConditionError, match="Maximum rework rounds"):
            coordinator.transition(loop_id, to_state="reworking")


def test_enforce_stop_conditions_is_idempotent_when_nothing_fires(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "noop")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        result = coordinator.enforce_stop_conditions(loop["id"])
        assert result["fired"] == []
        assert result["loop"]["state"] == "goal_received"
        assert result["loop"]["version"] == 1  # no transition recorded


def test_feedback_actions_are_classified_applied_and_traceable(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "feedback")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        backlog = coordinator.backlog
        discovery = coordinator.discovery

        epic = backlog.create_epic({"projectId": project_id, "title": "Checkout"})
        story = backlog.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Guest checkout",
                "acceptanceCriteria": ["Guest checkout validates payment fields."],
            }
        )
        task = backlog.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "Fix checkout validation",
                "role": "backend_engineer",
                "status": "completed",
            }
        )
        initiative = discovery.create_initiative(
            {"projectId": project_id, "title": "Checkout initiative", "summary": "Improve checkout."}
        )
        decision = discovery.create_product_decision(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "title": "Use synchronous payment capture",
                "status": "accepted",
                "context": "Initial architecture tradeoff.",
                "decision": "Capture synchronously.",
                "rationale": "Simpler rollout.",
                "decidedBy": "architect_agent",
            }
        )

        approval_loop = coordinator.start(project_id=project_id, title="Approval")
        for state in HAPPY_PATH[:-1]:
            approval_loop = coordinator.transition(approval_loop["id"], to_state=state)
        accepted = coordinator.apply_feedback(
            approval_loop["id"],
            action="accept",
            feedback="QA evidence accepted.",
            actor="operator",
            target_type="brief",
            target_id=approval_loop["id"],
        )
        assert accepted["feedback"]["classification"] == "brief_revision"
        assert accepted["loop"]["state"] == "delivered"
        assert accepted["feedback"]["effects"][0]["type"] == "transition"

        rework_loop = coordinator.start(project_id=project_id, title="Rework")
        for state in HAPPY_PATH[:-1]:
            rework_loop = coordinator.transition(rework_loop["id"], to_state=state)
        reworked = coordinator.apply_feedback(
            rework_loop["id"],
            action="request_changes",
            feedback="Validation needs another pass.",
            actor="operator",
            target_type="task",
            target_id=task["id"],
        )
        assert reworked["feedback"]["classification"] == "rework_task"
        assert reworked["loop"]["state"] == "reworking"
        task_after_rework = backlog.get_agent_task(task["id"])
        assert task_after_rework["status"] == "todo"
        assert task_after_rework["metadata"]["feedbackIds"] == [reworked["feedback"]["id"]]

        new_story = coordinator.apply_feedback(
            rework_loop["id"],
            action="change_scope",
            feedback="Add guest email receipt as follow-up scope.",
            actor="operator",
            target_type="epic",
            target_id=epic["id"],
            payload={
                "story": {
                    "title": "Guest email receipt",
                    "asA": "shopper",
                    "acceptanceCriteria": ["The shopper receives an email receipt after checkout."],
                }
            },
        )
        assert new_story["feedback"]["classification"] == "new_story"
        assert new_story["feedback"]["effects"][0]["type"] == "create_user_story"
        assert (
            backlog.get_user_story(new_story["feedback"]["effects"][0]["id"])["metadata"]["feedbackId"]
            == new_story["feedback"]["id"]
        )

        new_epic = coordinator.apply_feedback(
            rework_loop["id"],
            action="change_scope",
            feedback="Add a post-purchase automation epic.",
            actor="operator",
            payload={"epic": {"title": "Post-purchase automation"}},
        )
        assert new_epic["feedback"]["classification"] == "new_epic"
        assert new_epic["feedback"]["effects"][0]["type"] == "create_epic"

        revised_priority = coordinator.apply_feedback(
            rework_loop["id"],
            action="reprioritize",
            feedback="Escalate the checkout story.",
            actor="operator",
            target_type="story",
            target_id=story["id"],
            payload={"priority": "high"},
        )
        assert revised_priority["feedback"]["classification"] == "brief_revision"
        assert backlog.get_user_story(story["id"])["priority"] == "high"

        rejected_decision = coordinator.apply_feedback(
            rework_loop["id"],
            action="reject_decision",
            feedback="Synchronous capture is too risky.",
            actor="operator",
            target_type="decision",
            target_id=decision["id"],
        )
        assert rejected_decision["feedback"]["classification"] == "architecture_revision"
        assert discovery.get_product_decision(decision["id"])["status"] == "rejected"

        reopened = coordinator.apply_feedback(
            rework_loop["id"],
            action="reopen_story",
            feedback="Story needs another implementation pass.",
            actor="operator",
            target_type="story",
            target_id=story["id"],
        )
        assert reopened["feedback"]["classification"] == "rework_task"
        assert backlog.get_user_story(story["id"])["status"] == "reopened"

        pause_loop = coordinator.start(project_id=project_id, title="Pause")
        paused = coordinator.apply_feedback(
            pause_loop["id"],
            action="pause_loop",
            feedback="Pause while the brief is clarified.",
            actor="operator",
            target_type="brief",
            target_id=pause_loop["id"],
        )
        assert paused["feedback"]["classification"] == "brief_revision"
        assert paused["loop"]["state"] == "blocked"

        cancel_loop = coordinator.start(project_id=project_id, title="Cancel")
        cancelled = coordinator.apply_feedback(
            cancel_loop["id"],
            action="cancel_loop",
            feedback="Cancel after architecture decision rejection.",
            actor="operator",
            target_type="decision",
            target_id=decision["id"],
        )
        assert cancelled["feedback"]["classification"] == "architecture_revision"
        assert cancelled["loop"]["state"] == "cancelled"

        feedback_records = coordinator.list_feedback(project_id=project_id)
        assert {item["action"] for item in feedback_records} >= {
            "accept",
            "request_changes",
            "change_scope",
            "reprioritize",
            "reject_decision",
            "reopen_story",
            "pause_loop",
            "cancel_loop",
        }
        transition = coordinator.list_transitions(rework_loop["id"])[-1]
        assert transition["metadata"]["feedbackId"] == reworked["feedback"]["id"]
        assert reworked["feedback"]["effects"][0]["transitionId"] == transition["id"]


def test_feedback_rejects_unknown_actions_and_untraceable_targets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "feedback-invalid")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="Invalid")

        with pytest.raises(ProductLoopTransitionError, match="Unknown feedback action"):
            coordinator.apply_feedback(loop["id"], action="defer", feedback="not supported")

        with pytest.raises(ProductLoopTransitionError, match="requires a traceable target"):
            coordinator.apply_feedback(loop["id"], action="request_changes", feedback="missing target")
