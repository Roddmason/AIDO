"""Response economy changes only DeveloperAgent's narrative summary instruction."""

from __future__ import annotations

from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.model_gateway import ModelGateway
from local_control_center.agents.runtime_registry import build_developer_agent_argv, developer_agent_prompt
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.registry import descriptor_for
from local_control_center.settings.repository import SettingsRepository
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

STYLE_KEY = "agents.responseStyle"
NORMAL_SUMMARY = (
    "- Produce a concise structured summary with changed files, tests run, blockers, and residual risks."
)
INSTRUCTION = 'Responde en español. Conserva el código completo: print("evidencia") y el error exacto E42.'


@pytest.fixture
def lane(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Style fixture", path=tmp_path / "project"
        )
        workspace = {"id": "style-workspace", "path": str(tmp_path / "workspace")}
        connection.execute(
            """INSERT INTO workspaces
                (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
                 metadata, created_at, updated_at)
               VALUES (?, ?, 'style-task', 'developer_agent', ?, 'ready', 'directory', '{}',
                       '2026-09-21T00:00:00+00:00', '2026-09-21T00:00:00+00:00')""",
            (workspace["id"], project["id"], workspace["path"]),
        )
        yield SimpleNamespace(
            connection=connection,
            project_id=project["id"],
            workspace=workspace,
            settings=SettingsRepository(connection),
            root=tmp_path,
        )


def test_response_style_default_and_project_inheritance(lane):
    descriptor = descriptor_for(STYLE_KEY)
    assert descriptor is not None
    assert descriptor.default == "compact"
    assert descriptor.enum == ("compact", "normal")
    assert descriptor.project_section is not None

    def value(project_id):
        return resolve_setting_value(connection=lane.connection, key=STYLE_KEY, project_id=project_id)

    assert value(lane.project_id) == "compact"
    lane.settings.set_value(STYLE_KEY, "general", None, "normal")
    assert value(lane.project_id) == "normal"
    lane.settings.set_value(STYLE_KEY, "project", lane.project_id, "compact")
    assert value(lane.project_id) == "compact"
    assert value("other-project") == "normal"
    lane.settings.clear_value(STYLE_KEY, "project", lane.project_id)
    assert value(lane.project_id) == "normal"
    lane.settings.clear_value(STYLE_KEY, "general", None)
    assert value(lane.project_id) == "compact"


@pytest.mark.parametrize("route", ["cli", "api"])
@pytest.mark.parametrize(
    ("global_style", "project_style", "compact"),
    [(None, None, True), ("normal", None, False), ("compact", "normal", False), ("normal", "compact", True)],
)
def test_developer_style_resolves_before_command_or_broker(
    lane, monkeypatch, route, global_style, project_style, compact
):
    if global_style:
        lane.settings.set_value(STYLE_KEY, "general", None, global_style)
    if project_style:
        lane.settings.set_value(STYLE_KEY, "project", lane.project_id, project_style)
    qa_commands = [["pytest", "tests/test_exact.py"]]
    story = "Acceptance: all tests pass; otherwise report the exact blocker."
    constitution = "No secret exposure; retain required verification."
    original = developer_agent_prompt(
        instruction=INSTRUCTION, qa_commands=qa_commands, story_specs=story, constitution=constitution
    )
    if route == "cli":
        runtime = Mock()
        runtime.build_command.return_value = ["codex.exe", "captured-prompt"]
        monkeypatch.setattr(
            "local_control_center.agents.runtime_registry.runtime_for", Mock(return_value=runtime)
        )
        argv = build_developer_agent_argv(
            runtime={"id": "codex_cli", "detectedCommand": "codex.exe"},
            workspace_id=lane.workspace["id"],
            workspace_path=lane.workspace["path"],
            instruction=INSTRUCTION,
            qa_commands=qa_commands,
            agent_id="developer_agent",
            connection=lane.connection,
            model="explicit-model",
            story_specs=story,
            constitution=constitution,
        )
        assert argv == ["codex.exe", "captured-prompt"]
        request = runtime.build_command.call_args.args[0]
        prompt = request.prompt
        assert request.model == "explicit-model"
        assert request.effort is None
        assert request.env_policy == {"permissionProfile": "dev_safe", "network": False, "secrets": False}
    else:
        broker = Mock()
        broker.evaluate_tool_call.return_value = {"toolCall": {"status": "blocked", "payload": {}}}
        runner = DeveloperAgentRunner(lane.connection, root=lane.root)
        runner._execute_model_runtime(
            payload={
                "projectId": lane.project_id,
                "model": "explicit-model",
                "instruction": INSTRUCTION,
                "qaCommands": qa_commands,
                "storySpecs": story,
                "constitution": constitution,
            },
            runtime={"id": "ollama", "kind": "local"},
            workspace=lane.workspace,
            agent_run={"id": "style-run"},
            job={"id": "style-job"},
            profile={"id": "developer_agent"},
            broker=broker,
        )
        call = broker.evaluate_tool_call.call_args.kwargs
        assert call["project_id"] == lane.project_id
        request = call["tool_call"]["input"]
        prompt = request["messages"][1]["content"]
        system = request["messages"][0]["content"]
        assert "Return only valid JSON" in system
        assert '"content":"complete UTF-8 file content"' in system
        assert '"tests":["test command or blocker"]' in system
        assert '"risks":["risk or blocker"]' in system
        assert request["model"] == "explicit-model"
        assert request["temperature"] == 0.2
        assert "maxTokens" not in request
    if compact:
        assert "no filler" in prompt.lower()
        assert NORMAL_SUMMARY not in prompt  # Replace, do not pile another instruction on top.
        assert len(prompt) <= len(original)
        summary = next(line for line in prompt.splitlines() if "no filler" in line.lower())
        for retained in (
            "files",
            "tests",
            "blockers",
            "risks",
            "language",
            "format",
            "code",
            "evidence",
            "uncertainty",
        ):
            assert retained in summary.lower()
        assert prompt.replace(summary, NORMAL_SUMMARY) == original
    else:
        assert prompt == original


@pytest.mark.parametrize(
    ("prompt", "metadata"),
    [
        ('Return only this JSON: {"ok":true}', {}),
        ("Return only complete Python code, including exceptions.", {}),
        ("ok", {"purpose": "runtime_preflight"}),
        ("Explain the result in the requested format.", {}),
    ],
)
def test_generic_model_contracts_and_fixed_probe_remain_exact(lane, prompt, metadata):
    gateway = ModelGateway(lane.connection)
    plans = []
    for style in ("normal", "compact"):
        lane.settings.set_value(STYLE_KEY, "project", lane.project_id, style)
        plans.append(
            gateway.plan_model_call(
                project_id=lane.project_id,
                provider="ollama",
                model="explicit-model",
                prompt=prompt,
                metadata=metadata,
                temperature=0.1,
                max_tokens=8192,
                estimated_cost_usd=0.02,
                budget_remaining_usd=0.1,
            )
        )
    assert plans[0] == plans[1]
    assert plans[0]["request"]["messages"] == [{"role": "user", "content": prompt}]


def test_explicit_developer_command_remains_operator_owned(lane):
    command = ["custom.exe", "--prompt", "Return exactly ok"]
    assert (
        build_developer_agent_argv(
            runtime={"id": "codex_cli", "developerAgentArgv": command},
            workspace_id=lane.workspace["id"],
            workspace_path=lane.workspace["path"],
            instruction=INSTRUCTION,
            qa_commands=[],
            agent_id="developer_agent",
            connection=lane.connection,
        )
        == command
    )
