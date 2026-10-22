"""Caracterizacion de las ramas de guarda de IssueToPatchRunner (validaciones previas a cualquier efecto).

Fija los mensajes y el orden de las validaciones de approve_patch, promote_patch_to_branch,
create_pull_request_from_promoted_branch y run, y el comportamiento de la publicacion de la rama,
para poder descomponer esos metodos sin cambiar su contrato.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workflows.issue_to_patch_runner import IssueToPatchRunner
from local_control_center.workflows.repository import WorkflowsRepository


@dataclass
class GuardStore:
    """Conexión SQLite inicializada y raíz de trabajo, sin levantar el control plane HTTP."""

    connection: sqlite3.Connection
    cwd: Path

    def create_project(self, **kwargs: Any) -> dict[str, Any]:
        """Crea el proyecto directo en el repositorio."""
        project = ProjectsRepository(self.connection).create_project(**kwargs)
        project.pop("_created", None)
        return project


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GuardStore]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield GuardStore(connection=connection, cwd=tmp_path)


def _project(store: GuardStore, tmp_path: Path) -> dict[str, Any]:
    project_path = tmp_path / "guard-project"
    project_path.mkdir(parents=True, exist_ok=True)
    return store.create_project(name="Guard Project", path=project_path, template_id="other")


def _run(
    store: GuardStore,
    tmp_path: Path,
    *,
    kind: str = "issue_to_patch",
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[IssueToPatchRunner, dict[str, Any], dict[str, Any]]:
    project = _project(store, tmp_path)
    workflows = WorkflowsRepository(store.connection)
    workflow = workflows.create_workflow(project_id=project["id"], kind=kind, title="Guard run")
    workflow_run = workflows.start_workflow(workflow["id"])["workflowRun"]
    if status is not None or metadata is not None:
        workflow_run = workflows.update_workflow_run_status(
            workflow_run["id"], status=status or workflow_run["status"], metadata=metadata
        )
    runner = IssueToPatchRunner(store.connection, root=store.cwd)
    return runner, project, workflow_run


def _evidence(
    runner: IssueToPatchRunner, project: dict[str, Any], run_id: str | None, **links: str
) -> dict[str, Any]:
    return runner.evidence.create_evidence_package(project_id=project["id"], workflow_run_id=run_id, **links)


def test_approve_patch_requires_reason(store: GuardStore, tmp_path: Path) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path)

    with pytest.raises(ValueError, match="Approval reason is required"):
        runner.approve_patch(workflow_run["id"], reason="   ")


def test_approve_patch_rejects_other_workflow_kinds(store: GuardStore, tmp_path: Path) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path, kind="issue_to_pr")

    with pytest.raises(ValueError, match="Only issue_to_patch workflow runs"):
        runner.approve_patch(workflow_run["id"], reason="ok")


def test_approve_patch_rejects_already_approved_run(store: GuardStore, tmp_path: Path) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path, status="approved_for_integration")

    with pytest.raises(ValueError, match="already approved for integration"):
        runner.approve_patch(workflow_run["id"], reason="ok")


def test_approve_patch_requires_evidence_ready_status(store: GuardStore, tmp_path: Path) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path)

    with pytest.raises(ValueError, match="requires an evidence_ready workflow run"):
        runner.approve_patch(workflow_run["id"], reason="ok")


def test_approve_patch_requires_an_evidence_package(store: GuardStore, tmp_path: Path) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path, status="evidence_ready")

    with pytest.raises(ValueError, match="requires an evidence package"):
        runner.approve_patch(workflow_run["id"], reason="ok")


def test_approve_patch_rejects_evidence_from_another_run(store: GuardStore, tmp_path: Path) -> None:
    runner, project, workflow_run = _run(store, tmp_path, status="evidence_ready")
    foreign = _evidence(runner, project, "workflow-run-other")
    runner.workflows.update_workflow_run_status(
        workflow_run["id"], status="evidence_ready", metadata={"evidencePackageId": foreign["id"]}
    )

    with pytest.raises(ValueError, match="does not belong to this workflow run"):
        runner.approve_patch(workflow_run["id"], reason="ok")


def test_approve_patch_finds_evidence_by_run_and_requires_runtime_links(
    store: GuardStore, tmp_path: Path
) -> None:
    runner, project, workflow_run = _run(store, tmp_path, status="evidence_ready")
    _evidence(runner, project, workflow_run["id"])

    with pytest.raises(ValueError, match="requires linked job, agent run, and workspace records"):
        runner.approve_patch(workflow_run["id"], reason="ok")


def test_promote_requires_reason_kind_and_status(store: GuardStore, tmp_path: Path) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path)
    with pytest.raises(ValueError, match="Promotion reason is required"):
        runner.promote_patch_to_branch(workflow_run["id"], reason="")
    with pytest.raises(ValueError, match="requires an approved_for_integration workflow run"):
        runner.promote_patch_to_branch(workflow_run["id"], reason="ok")

    other, _project_row, other_run = _run(store, tmp_path, kind="release_gate")
    with pytest.raises(ValueError, match="Only issue_to_patch or issue_to_pr workflow runs"):
        other.promote_patch_to_branch(other_run["id"], reason="ok")


def test_promote_requires_approved_evidence_that_belongs_to_the_run(
    store: GuardStore, tmp_path: Path
) -> None:
    runner, project, workflow_run = _run(store, tmp_path, status="approved_for_integration")
    with pytest.raises(ValueError, match="requires an approved evidence package"):
        runner.promote_patch_to_branch(workflow_run["id"], reason="ok")

    foreign = _evidence(runner, project, "workflow-run-other")
    with pytest.raises(ValueError, match="does not belong to this workflow run"):
        runner.promote_patch_to_branch(workflow_run["id"], reason="ok", evidence_package_id=foreign["id"])

    own = _evidence(runner, project, workflow_run["id"])
    with pytest.raises(ValueError, match="requires linked approved job, agent run, and workspace records"):
        runner.promote_patch_to_branch(workflow_run["id"], reason="ok", evidence_package_id=own["id"])


def test_create_pull_request_requires_reason_kind_and_unfinished_run(
    store: GuardStore, tmp_path: Path
) -> None:
    runner, _project_row, workflow_run = _run(store, tmp_path)
    with pytest.raises(ValueError, match="Pull request creation reason is required"):
        runner.create_pull_request_from_promoted_branch(workflow_run["id"], reason="")

    other, _project_row, other_run = _run(store, tmp_path, kind="release_gate")
    with pytest.raises(ValueError, match="Only issue_to_patch or issue_to_pr workflow runs"):
        other.create_pull_request_from_promoted_branch(other_run["id"], reason="ok")

    done, _project_row, done_run = _run(store, tmp_path, status="pr_created")
    with pytest.raises(ValueError, match="already created for this workflow run"):
        done.create_pull_request_from_promoted_branch(done_run["id"], reason="ok")


def test_create_pull_request_rejects_evidence_from_another_run(store: GuardStore, tmp_path: Path) -> None:
    runner, project, workflow_run = _run(store, tmp_path)
    foreign = _evidence(runner, project, "workflow-run-other")
    runner.workflows.update_workflow_run_status(
        workflow_run["id"],
        status="promoted_to_branch",
        metadata={
            "promotionStatus": "promoted_to_branch",
            "promotedBranchName": "aido/promote/x",
            "promotionEvidencePackageId": foreign["id"],
            "originalEvidencePackageId": foreign["id"],
            "promotionWorkspaceId": "workspace-x",
        },
    )

    with pytest.raises(ValueError, match="evidence packages from the target workflow run"):
        runner.create_pull_request_from_promoted_branch(workflow_run["id"], reason="ok")


def test_create_pull_request_requires_passed_promotion_qa(store: GuardStore, tmp_path: Path) -> None:
    runner, project, workflow_run = _run(store, tmp_path)
    own = _evidence(runner, project, workflow_run["id"])
    runner.workflows.update_workflow_run_status(
        workflow_run["id"],
        status="promoted_to_branch",
        metadata={
            "promotionStatus": "promoted_to_branch",
            "promotedBranchName": "aido/promote/x",
            "promotionEvidencePackageId": own["id"],
            "originalEvidencePackageId": own["id"],
            "promotionWorkspaceId": "workspace-x",
        },
    )

    with pytest.raises(ValueError, match="requires passed promotion QA evidence"):
        runner.create_pull_request_from_promoted_branch(workflow_run["id"], reason="ok")


def test_run_rejects_runtime_outside_the_product_catalog(
    store: GuardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(store, tmp_path)
    monkeypatch.setattr(
        "local_control_center.workflows.issue_to_patch_runner.RuntimeStatusService.list_provider_statuses",
        lambda _service: [],
    )
    runner = IssueToPatchRunner(store.connection, root=store.cwd)

    with pytest.raises(ValueError, match="not in the product catalog: ghost_runtime"):
        runner.run(
            {
                "projectId": project["id"],
                "title": "Ghost runtime",
                "issueText": "Anything.",
                "preferredRuntime": "ghost_runtime",
            }
        )


def test_push_promoted_branch_reports_pushed_skipped_and_failed(
    store: GuardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(store, tmp_path)
    runner = IssueToPatchRunner(store.connection, root=store.cwd)
    module = "local_control_center.workflows.issue_to_patch_runner"
    calls: list[str] = []

    monkeypatch.setattr(f"{module}.git_remote_exists", lambda **_kwargs: False)
    assert runner._push_promoted_branch_for_pr(project_id=project["id"], branch="b") == {
        "status": "skipped_no_remote",
        "remote": "origin",
    }

    monkeypatch.setattr(f"{module}.git_remote_exists", lambda **_kwargs: True)
    monkeypatch.setattr(
        f"{module}.push_branch_to_remote",
        lambda **kwargs: calls.append(kwargs["branch"]) or {"status": "pushed"},
    )
    assert runner._push_promoted_branch_for_pr(project_id=project["id"], branch="b") == {"status": "pushed"}
    assert calls == ["b"]

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("remote said no")

    monkeypatch.setattr(f"{module}.push_branch_to_remote", _boom)
    failed = runner._push_promoted_branch_for_pr(project_id=project["id"], branch="b")
    assert failed["status"] == "push_failed"
    assert "remote said no" in failed["reason"]


def test_promotion_attempt_tolerates_qa_summary_without_agent_run() -> None:
    from local_control_center.workflows.issue_to_patch_runner import _PromotionAttempt

    attempt = _PromotionAttempt(
        job={},
        agent_run={},
        workspace={},
        apply_check={},
        apply_result=None,
        qa_summary={"agentRun": None, "results": []},
        diff={},
    )

    assert attempt.qa_agent_run_id is None
