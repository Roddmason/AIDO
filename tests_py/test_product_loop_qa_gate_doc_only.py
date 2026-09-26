"""Story loop: una historia de solo documentacion avanza sin bloqueo por el gate de QA.

Verifica el contrato acordado con el gate de QA proporcional (`agents/qa_doc_gate.py`,
`agents/qa_agent.py`) desde el lado del story loop: los comandos que no aplican a un cambio de
solo documentacion NO son resultados de QA y no deben bloquear `evaluate_qa_gate`, mientras que un
`skipped_with_reason` genuino (comando que si debia correr y no pudo) sigue bloqueando igual que
antes, y un `git diff --check` fallido sigue mandando la historia a rework.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from local_control_center.product_loop.coordinator import DEFAULT_AUTO_REWORK_ROUNDS, ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_product_loop_coordinator import (
    _AssessmentRunner,
    _ControlledRuntime,
    _GitGate,
    _product_owner_result,
    _ProductOwnerRunner,
    _SecurityGate,
    _seed_ai_resource,
    _TechnicalLeadPlanner,
    _workspace_project,
)

DOC_ONLY_SKIP_REASON = "cambio solo de documentación"


class _DocOnlyRuntime(_ControlledRuntime):
    """Runtime controlado cuyo resultado imita al DeveloperAgent real tras el gate de docs.

    `qaResults` trae unicamente el comando que se ejecuto de verdad (`git diff --check`); los
    comandos de codigo que no aplican viajan aparte, en `runtimeResult.notApplicableCommands`,
    igual que hace `developer_agent.py` tras separarlos de `qaResults`.
    """

    def __init__(self, *, not_applicable: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.not_applicable = not_applicable or [
            {
                "label": "Test (pytest tests_py)",
                "status": "not_applicable",
                "executed": False,
                "reason": DOC_ONLY_SKIP_REASON,
            }
        ]

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = super().run(payload)
        result["runtimeResult"] = {"status": "completed", "notApplicableCommands": self.not_applicable}
        return result


def _run_one_story(
    coordinator: ProductLoopCoordinator, project: dict[str, Any], runtime: _ControlledRuntime
) -> dict[str, Any]:
    return coordinator.run_user_message(
        project_id=project["id"],
        message="Update the onboarding documentation.",
        preferred_runtime="controlled_test_runtime",
        runtime_runner=runtime,
        git_service=_GitGate(),
        product_owner_runner=_ProductOwnerRunner(_product_owner_result("backlog_ready")),
        assessment_runner=_AssessmentRunner(),
        technical_lead_runner=_TechnicalLeadPlanner(),
        security_runner=_SecurityGate(),
    )


def _seeded_project(tmp_path: Path, connection: Any, name: str) -> dict[str, Any]:
    initialize_platform_schema(connection)
    _seed_ai_resource(connection)
    return _workspace_project(connection, tmp_path, name)


def test_doc_only_story_advances_without_blocking_with_omissions_visible(tmp_path: Path) -> None:
    runtime = _DocOnlyRuntime(
        qa_verdict="needs_human_review",
        qa_results=[{"command": "git diff --check", "status": "passed"}],
        changed_files=["docs/guide.md"],
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        project = _seeded_project(tmp_path, connection, "doc-only-story-advances")

        result = _run_one_story(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        durable = result["loop"]["context"]["durableRun"]
        assert "blockedStage" not in durable
        runtime_result = durable["runtimeResult"]
        assert runtime_result["qaResults"] == [{"command": "git diff --check", "status": "passed"}]
        not_applicable = runtime_result["runtimeResult"]["notApplicableCommands"]
        assert not_applicable[0]["reason"] == DOC_ONLY_SKIP_REASON


def test_code_story_with_a_genuinely_skipped_command_still_blocks(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(
        qa_verdict="blocked",
        qa_results=[{"command": "pytest tests_py", "status": "skipped_with_reason"}],
        changed_files=["local_control_center/agents/qa_agent.py"],
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        project = _seeded_project(tmp_path, connection, "code-story-genuine-skip-blocks")

        result = _run_one_story(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "qa"


def test_failing_git_diff_check_on_a_doc_only_story_goes_to_rework_not_passed(tmp_path: Path) -> None:
    runtime = _DocOnlyRuntime(
        status="qa_failed",
        qa_verdict="failed",
        qa_results=[{"command": "git diff --check", "status": "failed", "exitCode": 2}],
        changed_files=["docs/guide.md"],
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        project = _seeded_project(tmp_path, connection, "doc-only-diff-check-fails")

        result = _run_one_story(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "blocked"
        durable = result["loop"]["context"]["durableRun"]
        assert durable["blockedStage"] == "qa_rework"
        # Se reintento hasta agotar el presupuesto de rework: nunca se dio por completada.
        assert len(runtime.run_payloads) == 1 + DEFAULT_AUTO_REWORK_ROUNDS
