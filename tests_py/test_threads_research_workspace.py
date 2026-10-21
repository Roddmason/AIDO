"""Tests de la resolución del workspace al encolar investigación de hilos no ligados a un workspace.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.coordinator import ThreadCoordinator
from local_control_center.threads.repository import ThreadsRepository

RUNTIME_AVAILABLE = {
    "runtimeStatus": {
        "providers": [{"id": "codex_cli", "executable": True, "available": True, "canEditWorkspace": True}]
    }
}
RESEARCH_PROMPT = "Research official Python documentation and cite sources before deciding."


def _research_job_payload(
    tmp_path: Path, *, owner_type: str, owner_id: str, with_root_workspace: bool = True
):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="threads-research-workspace",
            path=tmp_path / "workspace",
            template_id="other",
            create_directory=True,
            source="runtime",
        )
        if with_root_workspace:
            connection.execute(
                "INSERT INTO workspaces (id,project_id,task_id,owner_agent_id,path,status,isolation_type,metadata,"
                "created_at,updated_at) VALUES (?,?,?,?,?,'ready','project_root','{}',?,?)",
                (
                    "root-workspace",
                    project["id"],
                    "task",
                    "developer",
                    str(tmp_path / "workspace"),
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        root_workspace = connection.execute(
            "SELECT id FROM workspaces WHERE project_id = ?", (project["id"],)
        ).fetchone()
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"], owner_type=owner_type, owner_id=owner_id, title="Research thread"
        )
        ThreadCoordinator(connection, root=tmp_path).post_message(
            thread_id=thread["id"], content=RESEARCH_PROMPT, project_assessment=RUNTIME_AVAILABLE
        )
        jobs = JobsRepository(connection).list_jobs(project["id"])
        assert [job["kind"] for job in jobs] == ["thread.research.run"]
        return jobs[0]["payload"], (root_workspace["id"] if root_workspace else None)


def test_research_job_for_loop_owned_thread_uses_project_root_workspace(tmp_path: Path) -> None:
    payload, root_workspace_id = _research_job_payload(tmp_path, owner_type="loop", owner_id="loop-1")

    assert root_workspace_id is not None
    assert payload["workspaceId"] == root_workspace_id


def test_research_job_for_workspace_owned_thread_keeps_owner_workspace(tmp_path: Path) -> None:
    payload, _ = _research_job_payload(tmp_path, owner_type="workspace", owner_id="workspace-1")

    assert payload["workspaceId"] == "workspace-1"


def test_research_job_without_resolvable_workspace_stays_unbound(tmp_path: Path) -> None:
    payload, root_workspace_id = _research_job_payload(
        tmp_path, owner_type="loop", owner_id="loop-1", with_root_workspace=False
    )

    assert root_workspace_id is None
    assert payload["workspaceId"] is None
