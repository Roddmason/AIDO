"""Aterrizaje del product loop: direct_push, degradación sin remoto y publicación para PR.

Ejercita ``ProductLoopDeliveryService`` sobre repos git reales (worktree + commits reales),
verificando que el modo de integración configurado por proyecto gobierna el aterrizaje: merge
local a la base con GC de rama (``direct_push``), degradación explícita cuando los modos de PR
no tienen remoto, y publicación de la rama de trabajo cuando sí lo hay.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_control_center.product_loop.delivery import ProductLoopDeliveryService
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.settings.repository import SettingsRepository
from local_control_center.workspaces_projects.git_worktrees import commit_workspace_changes
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.test_workspace_isolation_contract import create_git_repo, make_app

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is not available")


def _configure_git(connection: Any, project_id: str, **values: Any) -> None:
    settings = SettingsRepository(connection)
    for suffix, value in values.items():
        settings.set_value(f"project.git.{suffix}", "project", project_id, value)


def _landed_workspace(
    store: Any, tmp_path: Path, repo: Path, project: dict[str, Any], *, task: str
) -> dict[str, Any]:
    repository = WorkspacesRepository(store.connection, root=tmp_path)
    workspace = repository.allocate_workspace(
        project_id=project["id"],
        task_id=task,
        agent_id="developer",
        branch_name=f"codex/{task}",
        base_branch="devbase",
    )
    store.connection.commit()
    path = Path(workspace["path"])
    (path / f"{task}.py").write_text('"""Feature."""\n\nx = 1\n', encoding="utf-8")
    committed = commit_workspace_changes(
        workspace_path=path,
        message=f"Feature: {task}",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )
    store.connection.commit()
    assert committed["status"] == "committed", committed
    return workspace


def test_direct_push_lands_on_base_and_deletes_the_work_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "delivery-direct"
    create_git_repo(repo)
    assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
    project = store.create_project(name="DeliveryDirect", path=repo, template_id="other")
    _configure_git(store.connection, project["id"], integrationMode="direct_push", baseBranch="devbase")
    workspace = _landed_workspace(store, tmp_path, repo, project, task="direct-hu")

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
        title="HU directa",
    )
    store.connection.commit()

    assert result["status"] == "landed", result
    assert result["effectiveMode"] == "direct_push"
    assert result["degradedFrom"] is None
    show = run_git(["show", "devbase:direct-hu.py"], cwd=repo)
    assert show.returncode == 0 and "x = 1" in show.stdout
    # GC: la rama de trabajo se borró y el worktree quedó archivado.
    assert run_git(["rev-parse", "--verify", "codex/direct-hu"], cwd=repo).returncode != 0
    archived = store.connection.execute(
        "SELECT status FROM workspaces WHERE id = ?", (workspace["id"],)
    ).fetchone()
    assert archived["status"] == "archived"


def test_pr_modes_degrade_to_direct_push_when_no_remote_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "delivery-degrade"
    create_git_repo(repo)
    assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
    project = store.create_project(name="DeliveryDegrade", path=repo, template_id="other")
    _configure_git(store.connection, project["id"], integrationMode="manual_pr", baseBranch="devbase")
    workspace = _landed_workspace(store, tmp_path, repo, project, task="degrade-hu")

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
    )
    store.connection.commit()

    assert result["status"] == "landed", result
    assert result["effectiveMode"] == "direct_push"
    assert result["degradedFrom"] == "manual_pr"
    assert run_git(["show", "devbase:degrade-hu.py"], cwd=repo).returncode == 0


def test_manual_pr_with_remote_publishes_the_work_branch_and_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "delivery-pr"
    create_git_repo(repo)
    assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
    bare = tmp_path / "delivery-remote.git"
    assert run_git(["init", "--bare", str(bare)], cwd=tmp_path).returncode == 0
    assert run_git(["remote", "add", "origin", str(bare)], cwd=repo).returncode == 0
    project = store.create_project(name="DeliveryPr", path=repo, template_id="other")
    _configure_git(store.connection, project["id"], integrationMode="manual_pr", baseBranch="devbase")
    workspace = _landed_workspace(store, tmp_path, repo, project, task="pr-hu")

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
    )
    store.connection.commit()

    assert result["status"] == "pending_manual_pr", result
    assert result["push"]["status"] == "pushed"
    # La rama quedó publicada en el remoto y NO se mergeó a la base (el PR decide).
    assert run_git(["rev-parse", "--verify", "codex/pr-hu"], cwd=bare).returncode == 0
    assert run_git(["show", "devbase:pr-hu.py"], cwd=repo).returncode != 0
    # El workspace sigue activo: el flujo de PR itera sobre la misma rama si hay hallazgos.
    active = store.connection.execute(
        "SELECT status FROM workspaces WHERE id = ?", (workspace["id"],)
    ).fetchone()
    assert active["status"] != "archived"
