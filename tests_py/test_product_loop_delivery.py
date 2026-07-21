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


def _loop_with_verdict(qa_verdict: str) -> dict[str, Any]:
    return {
        "id": "product-loop-test",
        "context": {
            "durableRun": {
                "review": {"changedFiles": [{"path": "x.py"}]},
                "runtimeResult": {"evidencePackage": {"qaVerdict": qa_verdict}},
            }
        },
    }


def _auto_pr_repo(store: Any, tmp_path: Path, name: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    repo = tmp_path / name
    create_git_repo(repo)
    assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
    bare = tmp_path / f"{name}-remote.git"
    assert run_git(["init", "--bare", str(bare)], cwd=tmp_path).returncode == 0
    assert run_git(["remote", "add", "origin", str(bare)], cwd=repo).returncode == 0
    project = store.create_project(name=name, path=repo, template_id="other")
    _configure_git(store.connection, project["id"], integrationMode="auto_pr", baseBranch="devbase")
    workspace = _landed_workspace(store, tmp_path, repo, project, task=f"{name}-hu")
    return repo, project, workspace


def _stub_github(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, Any], *, ci_state: str = "success"
) -> None:
    module = "local_control_center.product_loop.delivery"

    class _Config:
        repository = "owner/repo"

    def _record(name: str, response: dict[str, Any]):
        def _call(config: Any, **kwargs: Any) -> dict[str, Any]:
            calls[name] = kwargs
            return response

        return _call

    monkeypatch.setattr(f"{module}.github_pull_request_config_from_env", lambda: _Config())
    monkeypatch.setattr(
        f"{module}.create_github_pull_request",
        _record("created", {"status": "created", "number": 7, "htmlUrl": "https://github.test/pr/7"}),
    )
    monkeypatch.setattr(
        f"{module}.get_github_combined_status", _record("ci", {"status": "ok", "state": ci_state})
    )
    monkeypatch.setattr(
        f"{module}.merge_github_pull_request", _record("merged", {"status": "merged", "sha": "abc123"})
    )
    monkeypatch.setattr(f"{module}.delete_github_branch", _record("deletedRemote", {"status": "deleted"}))


def test_auto_pr_lets_the_technical_lead_approve_merge_and_clean_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo, project, workspace = _auto_pr_repo(store, tmp_path, "autopr")
    calls: dict[str, Any] = {}
    _stub_github(monkeypatch, calls)

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
        title="HU auto",
        loop=_loop_with_verdict("passed"),
    )
    store.connection.commit()

    assert result["status"] == "landed", result
    assert result["technicalLeadGate"]["approve"] is True
    assert calls["created"]["head"] == "codex/autopr-hu"
    assert calls["created"]["base"] == "devbase"
    assert calls["merged"]["number"] == 7
    assert calls["deletedRemote"]["branch"] == "codex/autopr-hu"
    # GC local: rama borrada y workspace archivado.
    assert run_git(["rev-parse", "--verify", "codex/autopr-hu"], cwd=repo).returncode != 0
    archived = store.connection.execute(
        "SELECT status FROM workspaces WHERE id = ?", (workspace["id"],)
    ).fetchone()
    assert archived["status"] == "archived"


def test_auto_pr_lt_rejects_failed_qa_and_keeps_the_branch_for_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo, project, workspace = _auto_pr_repo(store, tmp_path, "autopr-reject")
    calls: dict[str, Any] = {}
    _stub_github(monkeypatch, calls)

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
        loop=_loop_with_verdict("failed"),
    )

    assert result["status"] == "lt_rejected", result
    assert result["technicalLeadGate"]["approve"] is False
    assert "created" not in calls  # sin PR: se itera sobre la misma rama publicada
    assert run_git(["rev-parse", "--verify", "codex/autopr-reject-hu"], cwd=repo).returncode == 0


def test_auto_pr_waits_for_green_ci_before_merging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    _repo, project, workspace = _auto_pr_repo(store, tmp_path, "autopr-ci")
    _configure_git(store.connection, project["id"], requireCiGreen=True)
    calls: dict[str, Any] = {}
    _stub_github(monkeypatch, calls, ci_state="pending")

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
        loop=_loop_with_verdict("passed"),
    )

    assert result["status"] == "pr_created_ci_pending", result
    assert "created" in calls
    assert "merged" not in calls  # el PR queda abierto esperando el verde


def test_auto_pr_without_github_credentials_waits_for_a_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    _repo, project, workspace = _auto_pr_repo(store, tmp_path, "autopr-nocreds")
    monkeypatch.delenv("AIDO_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("AIDO_GITHUB_REMOTE", raising=False)

    result = ProductLoopDeliveryService(store.connection, root=tmp_path).land(
        project_id=project["id"],
        workspace_id=workspace["id"],
        loop_id="product-loop-test",
        loop=_loop_with_verdict("passed"),
    )

    assert result["status"] == "pending_manual_pr", result
    assert "GitHub" in str(result.get("reason") or "")


def test_accept_feedback_lands_direct_push_work_on_the_base_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E integrado: aceptar la entrega aterriza los commits de la HU en la base del proyecto.

    Recorre el ciclo real del equipo: loop en ``awaiting_approval`` con su acción de aprobación,
    workspace git con commits reales de la iteración, y el ``accept`` del operador dispara el
    aterrizaje ``direct_push`` (merge a la base + GC de la rama) como efecto de la entrega.
    """
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema
    from tests_py.test_product_loop_coordinator import _approval_loop_with_action

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, coordinator, loop, _job, _action = _approval_loop_with_action(
            connection, tmp_path, "landing-e2e"
        )
        repo = Path(project["path"])
        create_git_repo(repo)
        assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
        _configure_git(connection, project["id"], integrationMode="direct_push", baseBranch="devbase")

        repository = WorkspacesRepository(connection, root=tmp_path)
        workspace = repository.allocate_workspace(
            project_id=project["id"],
            task_id="landing-hu",
            agent_id="developer",
            branch_name="codex/landing-hu",
            base_branch="devbase",
        )
        workspace_path = Path(workspace["path"])
        (workspace_path / "landing.py").write_text('"""HU."""\n\nx = 1\n', encoding="utf-8")
        committed = commit_workspace_changes(
            workspace_path=workspace_path,
            message="Feature: landing HU",
            connection=connection,
            root=tmp_path,
            project_id=project["id"],
            workspace_id=workspace["id"],
        )
        assert committed["status"] == "committed", committed

        durable = dict(loop["context"].get("durableRun") or {})
        loop = coordinator.repository.update_loop_context(
            loop["id"],
            context={
                **loop["context"],
                "durableRun": {
                    **durable,
                    "workspaceId": workspace["id"],
                    "review": {"changedFiles": [{"path": "landing.py"}]},
                    "runtimeResult": {"evidencePackage": {"qaVerdict": "passed"}},
                },
            },
        )

        accepted = coordinator.apply_feedback(
            loop["id"],
            action="accept",
            feedback="QA, diff and gitleaks evidence accepted.",
            actor="operator",
        )

        assert accepted["loop"]["state"] == "delivered"
        # El trabajo de la HU quedó en la base y la rama de trabajo se recicló.
        show = run_git(["show", "devbase:landing.py"], cwd=repo)
        assert show.returncode == 0 and "x = 1" in show.stdout
        assert run_git(["rev-parse", "--verify", "codex/landing-hu"], cwd=repo).returncode != 0
        landing_evidence = (accepted["loop"]["context"].get("durableRun") or {}).get("landing") or {}
        assert landing_evidence.get("status") == "landed"
