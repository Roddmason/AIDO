"""Orchestrator evidence must not change a read-only agent workspace."""

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.cli_sessions import CliSessionStore
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_read_only_session_preserves_workspace_and_keeps_evidence(tmp_path: Path, status: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("unchanged", encoding="utf-8")
    database = tmp_path / "control" / "platform.sqlite"
    database.parent.mkdir()
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    with closing(open_sqlite_connection(database)) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """INSERT INTO workspaces
               (id,project_id,task_id,owner_agent_id,path,status,isolation_type,metadata,created_at,updated_at)
               VALUES ('read-only','project','task','agent',?,'active','filesystem','{}','2026','2026')""",
            (str(workspace),),
        )
        session = CliSessionStore(connection).record_result(
            runtime="codex_cli",
            executable="codex",
            workspace_id="read-only",
            command=["codex", "exec", "--sandbox", "read-only", "--", "private prompt"],
            env_policy={"permissionProfile": "plan", "network": False, "secrets": False},
            status=status,
            stdout="retained stdout",
            stderr="retained stderr",
        )
        after = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
        assert after == before
        artifacts = connection.execute("SELECT * FROM artifacts").fetchall()
        assert len(artifacts) == 3
        assert session["logs_artifact_id"] in {row["id"] for row in artifacts}
        for artifact in artifacts:
            path = Path(artifact["path"])
            assert path.is_relative_to(database.parent / ".tmp" / "evidence-artifacts")
            assert path.is_file()
            assert artifact["project_id"] == "project"
            assert "private prompt" not in path.read_text(encoding="utf-8")
