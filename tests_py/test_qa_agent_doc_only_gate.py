"""Tests de integracion: QAAgentRunner ejecuta el plan reducido del gate de documentacion.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.qa_agent import QAAgentRunner
from local_control_center.agents.qa_doc_gate import DOC_ONLY_SKIP_REASON
from local_control_center.security_policy.git_command_runner import git_available, run_git
from tests_py.control_plane_fixture import ControlPlaneFixture

pytestmark = [
    pytest.mark.usefixtures("controlled_domain_host"),
    pytest.mark.skipif(not git_available(), reason="git CLI is not available"),
]


@pytest.fixture
def create_store():
    with ExitStack() as _owned_fixture_resources:

        def create_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ControlPlaneFixture:
            monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
            store = _owned_fixture_resources.enter_context(
                closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
            )
            store.init()
            return store

        yield create_owned


def create_git_project(
    store: ControlPlaneFixture, tmp_path: Path, name: str = "QA Doc Gate Project"
) -> dict[str, Any]:
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# QA doc gate project\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=project_path).returncode == 0
    commit = run_git(
        ["-c", "user.name=AIDO Tests", "-c", "user.email=aido@example.test", "commit", "-m", "init"],
        cwd=project_path,
    )
    assert commit.returncode == 0, commit.stderr
    return store.create_project(name=name, path=project_path, template_id="other")


def test_run_for_context_keeps_not_applicable_commands_out_of_results_and_verdict(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-doc-gate",
        agent_id="qa_agent",
        reason="qa doc gate workspace",
        isolation_type="git_worktree",
    )
    workspace_path = Path(workspace["path"])
    (workspace_path / "docs.md").write_text("# doc change\n", encoding="utf-8", newline="\n")
    assert run_git(["add", "--intent-to-add", "--", "."], cwd=workspace_path).returncode == 0

    summary = QAAgentRunner(store.connection, root=tmp_path).run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="qa-doc-gate",
        commands=[{"label": "diff check", "argv": ["git", "diff", "--check"], "critical": True}],
        not_applicable_commands=[
            {
                "label": "Test (pytest tests_py)",
                "argv": ["uv", "run", "pytest", "tests_py", "-q"],
                "reason": DOC_ONLY_SKIP_REASON,
            }
        ],
    )

    # Solo lo ejecutado (git diff --check) decide el veredicto: sin comandos no aplicables
    # mezclados, un unico "passed" produce un veredicto "passed", no "skipped_with_reason".
    assert summary["verdict"] == "passed"
    assert len(summary["results"]) == 1
    assert summary["results"][0]["label"] == "diff check"
    assert summary["results"][0]["status"] == "passed"
    assert summary["results"][0]["toolCallId"]

    not_applicable = summary["notApplicableCommands"]
    assert len(not_applicable) == 1
    assert not_applicable[0]["label"] == "Test (pytest tests_py)"
    assert not_applicable[0]["status"] == "not_applicable"
    assert not_applicable[0]["reason"] == DOC_ONLY_SKIP_REASON
    assert not_applicable[0]["executed"] is False
    assert not_applicable[0]["exitCode"] is None
    assert not_applicable[0]["outputArtifactId"] in summary["artifactIds"]
    assert summary["agentRun"]["input"]["notApplicableCommands"][0]["reason"] == DOC_ONLY_SKIP_REASON
    assert summary["agentRun"]["output"]["notApplicableCommands"][0]["reason"] == DOC_ONLY_SKIP_REASON
    # El agent run del propio QAAgent completa: lo unico que se intento ejecutar paso de verdad.
    assert summary["agentRun"]["status"] == "completed"


def test_run_for_context_without_not_applicable_commands_omits_input_key(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-doc-gate-legacy",
        agent_id="qa_agent",
        reason="qa doc gate legacy workspace",
        isolation_type="git_worktree",
    )

    summary = QAAgentRunner(store.connection, root=tmp_path).run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="qa-doc-gate-legacy",
        commands=[{"label": "diff check", "argv": ["git", "diff", "--check"], "critical": True}],
    )

    assert summary["verdict"] == "passed"
    assert summary["notApplicableCommands"] == []
    assert "notApplicableCommands" not in summary["agentRun"]["input"]
    assert "notApplicableCommands" not in summary["agentRun"]["output"]


def test_git_diff_check_fails_on_trailing_whitespace(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-doc-gate-whitespace",
        agent_id="qa_agent",
        reason="qa doc gate whitespace workspace",
        isolation_type="git_worktree",
    )
    workspace_path = Path(workspace["path"])
    (workspace_path / "docs.md").write_text("# doc change   \n", encoding="utf-8", newline="\n")
    assert run_git(["add", "--intent-to-add", "--", "."], cwd=workspace_path).returncode == 0

    summary = QAAgentRunner(store.connection, root=tmp_path).run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="qa-doc-gate-whitespace",
        commands=[{"label": "diff check", "argv": ["git", "diff", "--check"], "critical": True}],
    )

    assert summary["verdict"] == "failed"
    assert summary["results"][0]["status"] == "failed"
    assert summary["results"][0]["exitCode"] != 0
