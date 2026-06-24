from __future__ import annotations

from pathlib import Path

from local_control_center.agents.assessment_runner import ProjectAssessmentRunner
from local_control_center.agents.repository import AgentsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_project_assessment import EXPECTED_CATEGORIES, build_sample_project


def _project(connection, root: Path) -> dict:
    repository = ProjectsRepository(connection)
    return repository.create_project(name="Sample", path=root, template_id="other")


def test_runner_produces_policy_gated_assessment_and_artifact(tmp_path: Path) -> None:
    project_root = tmp_path / "sample"
    build_sample_project(project_root)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, project_root)

        result = ProjectAssessmentRunner(connection, root=tmp_path).run(project["id"])

        # The inspection is gated by a recorded allow decision, then produced and persisted.
        assert result["status"] == "completed"
        assert result["decision"]["decision"] == "allow"
        assert result["decision"]["riskLevel"] == "low"

        assessment = result["assessment"]
        assert assessment["source"] == "static_analysis"
        assert assessment["findingsCount"] == len(result["findings"])
        assert {finding["category"] for finding in result["findings"]} >= EXPECTED_CATEGORIES

        repository = ProjectsRepository(connection)
        stored = repository.list_project_assessments(project["id"])
        assert [item["id"] for item in stored] == [assessment["id"]]
        assert len(repository.list_project_findings(project_id=project["id"])) == assessment["findingsCount"]

        # A downloadable artifact is produced on disk and registered as evidence.
        artifact = result["artifact"]
        assert artifact["kind"] == "project_assessment"
        assert Path(artifact["path"]).exists()

        # The capability went through the ToolBroker chokepoint and was audited as allowed.
        tool_calls = AgentsRepository(connection).list_agent_tool_calls()
        assert any(
            call["toolName"] == "project_assessment" and call["status"] == "allowed" for call in tool_calls
        )


def test_runner_redacts_secrets_in_persisted_findings(tmp_path: Path) -> None:
    project_root = tmp_path / "sample"
    build_sample_project(project_root)
    # A leaked token inside a commit message must never reach the persisted findings in clear text.
    leaked = "ghp_ABCDEFGHIJKL0123456789"
    zero, sha1, sha2 = "0" * 40, "a" * 40, "b" * 40
    (project_root / ".git" / "logs" / "HEAD").write_text(
        f"{zero} {sha1} Dev <d@x.com> 1718000000 +0000\tcommit (initial): Initial commit\n"
        f"{sha1} {sha2} Dev <d@x.com> 1718000100 +0000\tcommit: Add token {leaked} by mistake\n",
        encoding="utf-8",
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, project_root)

        result = ProjectAssessmentRunner(connection, root=tmp_path).run(project["id"])

        git_findings = ProjectsRepository(connection).list_project_findings(
            project_id=project["id"], category="git_history"
        )
        serialized = " ".join(f"{f['title']} {f['detail']}" for f in git_findings)
        assert leaked not in serialized
        assert "[redacted]" in serialized
        # The artifact file is built from the same redacted findings.
        assert leaked not in Path(result["artifact"]["path"]).read_text(encoding="utf-8")


def _assessment_policy_input(**overrides) -> dict:
    base = {
        "operation": "project_assessment",
        "agentId": "project_assessment_agent",
        "permissionProfile": "plan",
        "path": "/projects/sample",
        "workspacePath": "/projects/sample",
        "agentRunId": "agent-run-1",
        "tool": "project_assessment",
        "networkRequired": False,
        "secretsRequired": False,
    }
    base.update(overrides)
    return base


def test_policy_branch_allows_the_assessment_agent() -> None:
    decision = evaluate_action(_assessment_policy_input())
    assert decision["decision"] == "allow"
    assert decision["riskLevel"] == "low"
    assert "project_assessment" in decision["categories"]


def test_policy_branch_denies_other_agents_profiles_and_remote_or_secrets() -> None:
    assert evaluate_action(_assessment_policy_input(agentId="developer_agent"))["decision"] == "deny"
    assert evaluate_action(_assessment_policy_input(permissionProfile="dev_safe"))["decision"] == "deny"
    assert evaluate_action(_assessment_policy_input(agentRunId=None))["decision"] == "deny"
    assert evaluate_action(_assessment_policy_input(path=""))["decision"] == "deny"
    assert evaluate_action(_assessment_policy_input(networkRequired=True))["decision"] == "deny"
    assert evaluate_action(_assessment_policy_input(secretsRequired=True))["decision"] == "deny"
