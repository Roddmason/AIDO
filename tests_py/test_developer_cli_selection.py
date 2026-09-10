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
        self.call = kwargs
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
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "synthetic-auth-source"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-user-data"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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
        broker = CapturedCliTransport()
        result = DeveloperAgentRunner(store.connection, root=tmp_path)._execute_cli_runtime(
            payload={"projectId": project["id"], "instruction": "Test command selection", "model": requested},
            runtime={"id": runtime_id, "detectedCommand": str(tmp_path / binary)},
            workspace=workspace,
            agent_run={"id": "test-agent-run"},
            job={"id": "test-job"},
            profile={},
            broker=broker,
        )
        argv = json.loads(result["stdout"])
        assert argv[argv.index("--model") + 1] == (requested or "profile-default-model")
        if runtime_id == "codex_cli":
            assert broker.call.get("trusted_operation") == "developer_agent_runtime"
            environment = broker.call.get("trusted_subprocess_environment") or {}
            assert "CODEX_HOME" in environment
            assert not Path(environment["CODEX_HOME"]).exists()  # isolated home cleaned after call
            assert "OPENAI_API_KEY" not in environment


def test_developer_codex_has_explicit_isolation_and_native_write_contract(tmp_path, monkeypatch):
    from local_control_center.agents.runtime_registry import build_developer_agent_argv

    monkeypatch.setattr("local_control_center.agents.runtime_registry.sys.platform", "win32")
    argv = build_developer_agent_argv(
        runtime={"id": "codex_cli", "detectedCommand": "codex.exe"},
        workspace_id="test",
        workspace_path=str(tmp_path),
        instruction="test",
        qa_commands=[],
        agent_id="developer_agent",
        connection=None,
        model="explicit-model",
    )
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--ignore-user-config" in argv
    assert 'windows.sandbox="unelevated"' in argv
    assert "mcp_servers={}" in argv
    assert "skills.include_instructions=false" in argv
    assert "project_doc_max_bytes=0" in argv
    assert 'shell_environment_policy.inherit="core"' in argv
    for feature in ("plugins", "multi_agent", "apps", "hooks", "memories"):
        assert any(argv[i : i + 2] == ["--disable", feature] for i in range(len(argv)))
    assert "--ignore-rules" not in argv  # Managed/command policy is not bypassed.
    assert not any(argv[i : i + 2] == ["--disable", "shell_tool"] for i in range(len(argv)))


@pytest.mark.parametrize("module, expected", [("unittest", "test"), ("pytest", "test"), ("subprocess", None)])
def test_qa_python_test_runner_is_not_generic_module_execution(module, expected):
    from local_control_center.security_policy.permissions import ParsedCommand, low_risk_shell_category

    assert (
        low_risk_shell_category(ParsedCommand("python.exe", ("-m", module, "discover", "-s", "tests", "-v")))
        == expected
    )


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "untrusted",
        "no-environment",
        "foreign-home",
        "outside",
        "bypass",
        "duplicate",
        "plan",
        "other-role",
    ],
)
def test_developer_native_boundary_requires_exact_contract_and_isolated_environment(
    tmp_path, monkeypatch, change
):
    from local_control_center.agents.runtime_registry import (
        build_developer_agent_argv,
        isolated_product_owner_codex_environment,
    )
    from local_control_center.agents.tool_broker import _developer_codex_boundary

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "synthetic-source"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-local"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    argv = build_developer_agent_argv(
        runtime={"id": "codex_cli", "detectedCommand": "codex.exe"},
        workspace_id="w",
        workspace_path=str(workspace),
        instruction="synthetic",
        qa_commands=[],
        agent_id="developer_agent",
        connection=None,
        model="explicit-model",
    )
    profile = {"id": "developer_agent", "permissionProfile": "dev_safe"}
    with isolated_product_owner_codex_environment() as environment:
        trusted = "developer_agent_runtime"
        if change == "untrusted":
            trusted = None
        if change == "no-environment":
            environment = None
        if change == "foreign-home":
            environment = {**environment, "CODEX_HOME": str(workspace)}
        if change == "outside":
            argv[argv.index("--cd") + 1] = str(tmp_path)
        if change == "bypass":
            argv.insert(-2, "--dangerously-bypass-approvals-and-sandbox")
        if change == "duplicate":
            argv[1:1] = ["--sandbox", "workspace-write"]
        if change == "plan":
            profile["permissionProfile"] = "plan"
        if change == "other-role":
            profile["id"] = "product_owner_agent"
        result = _developer_codex_boundary(
            operation="developer_agent_runtime",
            trusted_operation=trusted,
            profile=profile,
            tool_call={"tool": "shell", "runtimeId": "codex_cli", "argv": argv},
            workspace_path=str(workspace),
            environment=environment,
        )
        if change == "none":
            assert result is None
        else:
            assert result["decision"] == "deny"
