from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.product_loop.repository import stable_task_suffix
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.shared.time import iso_after_seconds, utc_now
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workspaces_projects.cleanup_policy import (
    apply_cleanup,
    build_cleanup_plan,
)
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is not available")


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def create_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=path).returncode == 0
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=path).returncode == 0
    commit = run_git(
        ["-c", "user.name=AIDO Tests", "-c", "user.email=aido@example.test", "commit", "-m", "initial"],
        cwd=path,
    )
    assert commit.returncode == 0, commit.stderr


def age_workspace(store: ControlPlaneFixture, workspace_id: str, *, hours: float = 48.0) -> None:
    stamp = iso_after_seconds(utc_now(), -hours * 3600)
    store.connection.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (stamp, workspace_id))


def allocate_thread_workspace(
    store: ControlPlaneFixture,
    tmp_path: Path,
    project: dict,
    thread_id: str,
    *,
    role: str = "loop",
) -> dict:
    suffix = stable_task_suffix(thread_id, "unused-loop")
    task_id = f"product-{'loop' if role == 'loop' else 'owner'}-{suffix}"
    workspace = WorkspacesRepository(store.connection, root=tmp_path).allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="developer",
        branch_name=f"codex/{task_id}",
    )
    store.connection.commit()
    assert workspace["isolationType"] == "git_worktree"
    return workspace


def worktree_paths(repo: Path) -> set[str]:
    listing = run_git(["worktree", "list", "--porcelain"], cwd=repo)
    assert listing.returncode == 0
    return {
        str(Path(line.removeprefix("worktree ").strip()).resolve())
        for line in listing.stdout.splitlines()
        if line.startswith("worktree ")
    }


def test_plan_classifies_archived_thread_and_protects_open_thread_and_control_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup", path=repo, template_id="other")
    threads = ThreadsRepository(store.connection)
    archived_thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-a", title="Hilo archivado"
    )
    open_thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-b", title="Hilo abierto"
    )
    archived_ws = allocate_thread_workspace(store, tmp_path, project, archived_thread["id"])
    open_ws = allocate_thread_workspace(store, tmp_path, project, open_thread["id"])
    threads.archive_thread(archived_thread["id"], "terminado", "tester")
    age_workspace(store, archived_ws["id"])
    age_workspace(store, open_ws["id"])
    # Los control workspaces (path = raiz del repo real) jamas pueden ser candidatos.
    control_rows = store.connection.execute(
        "SELECT id FROM workspaces WHERE path = ?", (str(repo.resolve()),)
    ).fetchall()
    assert control_rows, "allocation should have created a git control workspace"
    for row in control_rows:
        age_workspace(store, row["id"])

    plan = build_cleanup_plan(store.connection, root=tmp_path, project_id=project["id"])

    by_id = {item["workspaceId"]: item for item in plan["candidates"]}
    assert archived_ws["id"] in by_id
    candidate = by_id[archived_ws["id"]]
    assert candidate["reason"] == "thread_archived"
    assert candidate["threadId"] == archived_thread["id"]
    assert candidate["threadTitle"] == "Hilo archivado"
    assert candidate["branch"] == archived_ws["metadata"]["gitWorktree"]["branchName"]
    assert candidate["pathExists"] is True
    assert open_ws["id"] not in by_id
    assert all(row["id"] not in by_id for row in control_rows)
    assert plan["summary"]["candidateCount"] == len(plan["candidates"])


def test_plan_excludes_fresh_workspaces_and_flags_missing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup", path=repo, template_id="other")
    threads = ThreadsRepository(store.connection)
    thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-a", title="Hilo fresco"
    )
    fresh_ws = allocate_thread_workspace(store, tmp_path, project, thread["id"])
    threads.archive_thread(thread["id"], "terminado", "tester")

    missing_ws = WorkspacesRepository(store.connection, root=tmp_path).allocate_workspace(
        project_id=project["id"],
        task_id="story-missing",
        agent_id="developer",
        branch_name="codex/story-missing",
    )
    store.connection.commit()
    # La fila queda activa pero el directorio desaparece (fila rancia).
    removed = run_git(["worktree", "remove", "--force", missing_ws["path"]], cwd=repo)
    assert removed.returncode == 0, removed.stderr
    age_workspace(store, missing_ws["id"])

    plan = build_cleanup_plan(store.connection, root=tmp_path, project_id=project["id"])

    by_id = {item["workspaceId"]: item for item in plan["candidates"]}
    assert fresh_ws["id"] not in by_id, "fresh workspaces stay protected by the grace window"
    assert missing_ws["id"] in by_id
    assert by_id[missing_ws["id"]]["reason"] == "path_missing"
    assert by_id[missing_ws["id"]]["pathExists"] is False


def test_plan_classifies_finished_workflow_and_reports_repo_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup", path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)
    timestamp = utc_now()
    store.connection.execute(
        """
        INSERT INTO workflow_runs (id, workflow_id, project_id, status, started_at, completed_at, metadata)
        VALUES ('run-done', 'wf-1', ?, 'completed', ?, ?, '{}')
        """,
        (project["id"], timestamp, timestamp),
    )
    store.connection.execute(
        """
        INSERT INTO workflow_runs (id, workflow_id, project_id, status, started_at, completed_at, metadata)
        VALUES ('run-live', 'wf-1', ?, 'running', ?, NULL, '{}')
        """,
        (project["id"], timestamp),
    )
    finished_ws = repository.allocate_workspace(
        project_id=project["id"],
        task_id="story-finished",
        agent_id="developer",
        branch_name="codex/story-finished",
        workflow_run_id="run-done",
    )
    live_ws = repository.allocate_workspace(
        project_id=project["id"],
        task_id="story-live",
        agent_id="developer",
        branch_name="codex/story-live",
        workflow_run_id="run-live",
    )
    store.connection.commit()
    age_workspace(store, finished_ws["id"])
    age_workspace(store, live_ws["id"])

    orphan_path = tmp_path / ".tmp" / "workspaces" / "orphan-manual"
    added = run_git(["worktree", "add", "-b", "orphan/manual", str(orphan_path), "HEAD"], cwd=repo)
    assert added.returncode == 0, added.stderr

    plan = build_cleanup_plan(store.connection, root=tmp_path, project_id=project["id"])

    by_id = {item["workspaceId"]: item for item in plan["candidates"]}
    assert finished_ws["id"] in by_id
    assert by_id[finished_ws["id"]]["reason"] == "workflow_finished"
    assert live_ws["id"] not in by_id, "workspaces of unfinished workflow runs stay protected"
    orphan_paths = {item["path"] for item in plan["repoOrphans"]}
    assert str(orphan_path.resolve()) in orphan_paths
    assert all(Path(item["path"]) != repo.resolve() for item in plan["repoOrphans"])


def test_apply_archives_candidates_removes_worktrees_and_prunes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup", path=repo, template_id="other")
    threads = ThreadsRepository(store.connection)
    done_thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-a", title="Terminado"
    )
    open_thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-b", title="Abierto"
    )
    done_ws = allocate_thread_workspace(store, tmp_path, project, done_thread["id"])
    open_ws = allocate_thread_workspace(store, tmp_path, project, open_thread["id"])
    threads.archive_thread(done_thread["id"], "terminado", "tester")
    age_workspace(store, done_ws["id"])
    age_workspace(store, open_ws["id"])
    branch = done_ws["metadata"]["gitWorktree"]["branchName"]

    orphan_path = tmp_path / ".tmp" / "workspaces" / "orphan-manual"
    assert (
        run_git(["worktree", "add", "-b", "orphan/manual", str(orphan_path), "HEAD"], cwd=repo).returncode
        == 0
    )

    result = apply_cleanup(
        store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_ids=[done_ws["id"], open_ws["id"]],
        orphan_worktree_paths=[str(orphan_path.resolve()), str(repo.resolve())],
        delete_branches=False,
        reason="drain terminated workspaces",
    )

    by_id = {item["workspaceId"]: item for item in result["results"]}
    assert by_id[done_ws["id"]]["status"] == "archived"
    assert by_id[open_ws["id"]]["status"] == "skipped_not_candidate"
    row = store.connection.execute("SELECT status FROM workspaces WHERE id = ?", (done_ws["id"],)).fetchone()
    assert row["status"] == "archived"
    allocation = store.connection.execute(
        "SELECT status FROM workspace_allocations WHERE workspace_id = ?", (done_ws["id"],)
    ).fetchone()
    assert allocation["status"] == "released"
    assert not Path(done_ws["path"]).exists()
    assert Path(open_ws["path"]).exists()

    orphan_results = {item["path"]: item for item in result["orphanResults"]}
    assert orphan_results[str(orphan_path.resolve())]["status"] == "removed"
    assert orphan_results[str(repo.resolve())]["status"] == "refused_outside_root"
    assert repo.exists()

    remaining = worktree_paths(repo)
    assert str(Path(done_ws["path"]).resolve()) not in remaining
    assert str(orphan_path.resolve()) not in remaining
    assert str(Path(open_ws["path"]).resolve()) in remaining
    assert result["prune"]["status"] in {"completed", "pruned"}
    # La rama de trabajo sobrevive salvo que se pida borrarla.
    assert run_git(["rev-parse", "--verify", branch], cwd=repo).returncode == 0
    assert result["summary"]["archivedCount"] == 1


def test_apply_can_delete_work_branches_when_opted_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup", path=repo, template_id="other")
    threads = ThreadsRepository(store.connection)
    thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-a", title="Terminado"
    )
    workspace = allocate_thread_workspace(store, tmp_path, project, thread["id"])
    threads.archive_thread(thread["id"], "terminado", "tester")
    age_workspace(store, workspace["id"])
    branch = workspace["metadata"]["gitWorktree"]["branchName"]

    result = apply_cleanup(
        store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_ids=[workspace["id"]],
        orphan_worktree_paths=[],
        delete_branches=True,
        reason="drain with branch deletion",
    )

    by_id = {item["workspaceId"]: item for item in result["results"]}
    assert by_id[workspace["id"]]["status"] == "archived"
    assert run_git(["rev-parse", "--verify", branch], cwd=repo).returncode != 0


def test_cleanup_api_roundtrip_requires_token_and_explicit_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup", path=repo, template_id="other")
    threads = ThreadsRepository(store.connection)
    thread = threads.create_thread(
        project_id=project["id"], owner_type="loop", owner_id="loop-a", title="Terminado"
    )
    workspace = allocate_thread_workspace(store, tmp_path, project, thread["id"])
    threads.archive_thread(thread["id"], "terminado", "tester")
    age_workspace(store, workspace["id"])

    plan_response = client.get(f"/api/v1/projects/{project['id']}/workspaces/cleanup/plan")
    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["projectId"] == project["id"]
    assert any(item["workspaceId"] == workspace["id"] for item in plan["candidates"])

    unknown = client.get("/api/v1/projects/project-missing/workspaces/cleanup/plan")
    assert unknown.status_code == 404

    no_token = client.post(
        f"/api/v1/projects/{project['id']}/workspaces/cleanup",
        json={"workspaceIds": [workspace["id"]]},
    )
    assert no_token.status_code == 403

    empty = client.post(
        f"/api/v1/projects/{project['id']}/workspaces/cleanup",
        json={"workspaceIds": [], "orphanWorktreePaths": []},
        headers=headers,
    )
    assert empty.status_code == 422

    applied = client.post(
        f"/api/v1/projects/{project['id']}/workspaces/cleanup",
        json={"workspaceIds": [workspace["id"]], "reason": "drain from API"},
        headers=headers,
    )
    assert applied.status_code == 200
    payload = applied.json()
    assert payload["summary"]["archivedCount"] == 1
    assert payload["results"][0]["status"] == "archived"
    assert not Path(workspace["path"]).exists()
    event = store.connection.execute(
        "SELECT * FROM events WHERE type = 'workspace.cleanup.applied'"
    ).fetchone()
    assert event is not None
