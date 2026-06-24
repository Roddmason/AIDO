from __future__ import annotations

from pathlib import Path

from local_control_center.agents.product_owner_agent import ProductOwnerAgent, ProductOwnerAgentRunner
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_project_assessment import build_sample_project


def test_runner_creates_then_reuses_project_assessment_for_grounding(tmp_path: Path) -> None:
    project_root = tmp_path / "sample"
    build_sample_project(project_root)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = ProjectsRepository(connection)
        project = repository.create_project(name="Sample", path=project_root, template_id="other")
        runner = ProductOwnerAgentRunner(connection, root=tmp_path)

        # First call produces the assessment so the agent is grounded before asking the user.
        signals = runner._project_assessment_signals(project["id"])
        assert signals is not None
        assert "Python" in signals["stack"]
        assert signals["riskCount"] >= 1  # the committed .env in the sample project
        assert isinstance(signals["risks"], list)
        assert len(repository.list_project_assessments(project["id"])) == 1

        # A subsequent call reuses the latest assessment instead of re-walking the tree.
        runner._project_assessment_signals(project["id"])
        assert len(repository.list_project_assessments(project["id"])) == 1


def test_runner_returns_none_when_no_assessment_can_be_produced(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        runner = ProductOwnerAgentRunner(connection, root=tmp_path)
        # An unknown project cannot be assessed; the agent degrades gracefully (no grounding).
        assert runner._project_assessment_signals("project-does-not-exist") is None


def test_assessment_context_injects_codebase_signals_into_prompt() -> None:
    agent = ProductOwnerAgent()
    assessment = {
        "idea": "Add a self-serve onboarding flow.",
        "initiative": None,
        "projectAssessment": {
            "stack": ["Python", "TypeScript"],
            "hasTests": True,
            "risks": ["High technical-debt marker density"],
            "gaps": ["No security tooling detected"],
        },
    }

    messages = agent.model_messages(idea=assessment["idea"], assessment=assessment)
    user_payload = messages[1]["content"]

    assert "codebaseSignals" in user_payload
    assert "High technical-debt marker density" in user_payload
    assert "No security tooling detected" in user_payload
