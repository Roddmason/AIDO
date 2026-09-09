"""The developer CLI must use the explicitly requested model, not a profile default."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


class CapturedCliTransport:
    """No subprocess: return the actual command assembled by the production runner."""

    def evaluate_tool_call(self, **kwargs):
        call = kwargs["tool_call"]
        return {
            "toolCall": {
                "id": "test-transport",
                "status": "completed",
                "payload": {
                    "executionResult": {
                        "returnCode": 0,
                        "stdout": json.dumps(call["argv"]),
                    }
                },
            }
        }


@pytest.mark.parametrize("runtime_id", ["codex_cli", "claude_code_cli"])
@pytest.mark.parametrize("requested", ["explicit-model", None])
def test_developer_cli_preserves_requested_model_without_changing_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_id: str, requested: str | None
) -> None:
    module = "codex_cli" if runtime_id == "codex_cli" else "claude_code_cli"
    monkeypatch.setattr(
        f"local_control_center.agents.cli_runtimes.{module}.resolve_model_alias",
        lambda *_args, **_kwargs: "profile-default-model",
    )
    with closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")) as store:
        store.init()
        project = store.create_project(name="Model selection", path=tmp_path / "project", template_id="other")
        workspace = WorkspacesRepository(store.connection, root=tmp_path).allocate_workspace(
            project_id=project["id"], task_id="selection", agent_id="developer_agent", reason="test"
        )
        binary = "codex.exe" if runtime_id == "codex_cli" else "claude.exe"
        result = DeveloperAgentRunner(store.connection, root=tmp_path)._execute_cli_runtime(
            payload={"projectId": project["id"], "instruction": "Test command selection", "model": requested},
            runtime={"id": runtime_id, "detectedCommand": str(tmp_path / binary)},
            workspace=workspace,
            agent_run={"id": "test-agent-run"},
            job={"id": "test-job"},
            profile={},
            broker=CapturedCliTransport(),
        )
        argv = json.loads(result["stdout"])
        assert argv[argv.index("--model") + 1] == (requested or "profile-default-model")
