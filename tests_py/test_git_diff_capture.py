"""Captura de diff con intent-to-add denegado: el estado degradado debe propagarse.

La rama brokered de ``capture_git_diff`` ejecuta ``git add --intent-to-add -- .`` para que
los archivos nuevos aparezcan en el patch. Cuando la política deniega ese comando (regresión
real: la rama ``add`` duplicada del policy engine) el patch queda sin los archivos ``??``,
pero la captura reportaba ``captured`` igual y enmascaraba la pérdida de evidencia. Estos
tests fijan el contrato fail-closed: denegación + untracked presentes => estado degradado
distinguible de "no hay cambios" para los consumidores (gate del DeveloperAgent incluido).

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents import tool_broker
from local_control_center.agents.developer_agent import _complete_run_status
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.test_workspace_isolation_contract import create_git_repo, make_app

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is not available")


def _workspace_with_repo(store: Any, tmp_path: Path, *, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    repo = tmp_path / name
    create_git_repo(repo)
    assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
    project = store.create_project(name=name, path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)
    workspace = repository.allocate_workspace(
        project_id=project["id"],
        task_id=f"{name}-hu",
        agent_id="developer",
        branch_name=f"codex/{name}",
        base_branch="devbase",
    )
    store.connection.commit()
    assert workspace["isolationType"] == "git_worktree", workspace
    return project, workspace


def _deny_intent_to_add(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduce la regresión: el policy engine deniega solo la forma intent-to-add."""

    def denying_evaluate_action(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("gitOperation") == "diff_capture_intent_to_add":
            return evaluate_action({**payload, "gitOperation": None})
        return evaluate_action(payload)

    monkeypatch.setattr(tool_broker, "evaluate_action", denying_evaluate_action)


def _capture(
    store: Any, tmp_path: Path, project: dict[str, Any], workspace: dict[str, Any]
) -> dict[str, Any]:
    return capture_git_diff(
        Path(workspace["path"]),
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )


def test_denied_intent_to_add_with_untracked_files_degrades_the_capture_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="deny-intent")
    workspace_path = Path(workspace["path"])
    (workspace_path / "brand_new.py").write_text('"""HU."""\n\nx = 1\n', encoding="utf-8")
    _deny_intent_to_add(monkeypatch)

    diff = _capture(store, tmp_path, project, workspace)

    assert diff["state"] == "captured_intent_to_add_blocked", diff
    assert diff["intentToAddBlocked"] is True
    assert diff["intentToAddReason"]
    assert any(item["status"] == "??" for item in diff["status"])
    assert "brand_new.py" in diff["nameOnly"]


def test_denied_intent_to_add_without_untracked_files_keeps_captured_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="deny-tracked")
    workspace_path = Path(workspace["path"])
    (workspace_path / "README.md").write_text("initial\nmodified\n", encoding="utf-8")
    _deny_intent_to_add(monkeypatch)

    diff = _capture(store, tmp_path, project, workspace)

    assert diff["state"] == "captured", diff
    assert diff.get("intentToAddBlocked") is not True
    assert "README.md" in diff["patchFull"]


def test_allowed_intent_to_add_reports_captured_with_untracked_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="allow-intent")
    workspace_path = Path(workspace["path"])
    (workspace_path / "brand_new.py").write_text('"""HU."""\n\nx = 1\n', encoding="utf-8")

    diff = _capture(store, tmp_path, project, workspace)

    assert diff["state"] == "captured", diff
    assert diff.get("intentToAddBlocked") is not True
    assert "brand_new.py" in diff["patchFull"]


def _passing_qa_result() -> dict[str, Any]:
    return {
        "status": "passed",
        "exitCode": 0,
        "execution": "restricted_subprocess",
        "toolCallId": "tool-call-test",
        "artifactHashes": {
            "stdoutHash": "hash",
            "stderrHash": "hash",
            "outputArtifactHash": "hash",
        },
    }


def test_complete_run_status_distinguishes_blocked_intent_to_add_from_no_changes() -> None:
    degraded_diff = {
        "state": "captured_intent_to_add_blocked",
        "intentToAddBlocked": True,
        "intentToAddReason": "Policy denied git intent-to-add.",
        "nameOnly": ["brand_new.py"],
        "patch": "",
        "patchFull": "",
    }

    status, verdict, reason = _complete_run_status(
        runtime_status="completed",
        require_approval=True,
        qa_results=[_passing_qa_result()],
        diff=degraded_diff,
        evidence_created=True,
    )

    assert status == "evidence_ready"
    assert verdict == "blocked"
    assert "intent-to-add" in reason
