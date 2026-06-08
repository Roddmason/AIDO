from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"toolCall": tool_call, "policyInput": policy_input})
        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "durationMs": 3,
            "stdout": "adapter completed",
            "stderr": "",
        }


def make_agent_run(tmp_path: Path, *, allowed_tools: list[str], permission_profile: str = "dev_safe"):
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Runtime adapters", path=tmp_path / "runtime-adapters", template_id="other")
    workspace = WorkspacesRepository(store.connection, root=tmp_path).allocate_workspace(
        project_id=project["id"],
        task_id="adapter-call",
        agent_id="implementer",
    )
    agents = AgentsRepository(store.connection)
    profile = agents.upsert_agent_profile(
        {
            "id": "runtime-adapter-agent",
            "name": "Runtime Adapter Agent",
            "role": "implementer",
            "runtimeMode": "hybrid",
            "permissionProfile": permission_profile,
            "allowedTools": allowed_tools,
        }
    )
    run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id="adapter-call",
        input_payload={},
        output_payload={},
        status="running",
    )
    return store, project, profile, run, workspace


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_runtime_adapter_execution_happens_only_after_broker_policy_allows(tmp_path: Path) -> None:
    store, project, profile, run, workspace = make_agent_run(tmp_path, allowed_tools=["mcp"])
    adapter = FakeRuntimeAdapter()
    workspace_path = str(workspace["path"])

    result = ToolBroker(store.connection, runtime_adapters={"mcp": adapter}).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "local-docs",
            "operation": "tools/list",
            "workspaceId": workspace["id"],
            "workspacePath": workspace_path,
            "path": workspace_path,
        },
    )

    assert adapter.calls
    assert adapter.calls[0]["policyInput"]["tool"] == "mcp"
    assert result["decision"]["decision"] == "allow"
    assert result["toolCall"]["status"] == "completed"
    assert result["toolCall"]["payload"]["execution"] == "runtime_adapter:mcp"
    assert result["toolCall"]["payload"]["executionResult"]["stdout"] == "adapter completed"


def test_mcp_non_read_only_operation_requires_approval_before_adapter_execution(tmp_path: Path) -> None:
    store, project, profile, run, workspace = make_agent_run(tmp_path, allowed_tools=["mcp"])
    adapter = FakeRuntimeAdapter()
    workspace_path = str(workspace["path"])

    result = ToolBroker(store.connection, runtime_adapters={"mcp": adapter}).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "local-docs",
            "operation": "tools/call",
            "workspaceId": workspace["id"],
            "workspacePath": workspace_path,
            "path": workspace_path,
        },
    )

    assert adapter.calls == []
    assert result["decision"]["decision"] == "requires_approval"
    assert result["toolCall"]["status"] == "approval_required"


def test_runtime_adapter_is_not_invoked_when_agent_profile_does_not_allow_tool(tmp_path: Path) -> None:
    store, project, profile, run, workspace = make_agent_run(tmp_path, allowed_tools=["shell"])
    adapter = FakeRuntimeAdapter()
    workspace_path = str(workspace["path"])

    result = ToolBroker(store.connection, runtime_adapters={"mcp": adapter}).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "local-docs",
            "operation": "tools/list",
            "workspaceId": workspace["id"],
            "workspacePath": workspace_path,
            "path": workspace_path,
        },
    )

    assert adapter.calls == []
    assert result["decision"]["decision"] == "deny"
    assert result["toolCall"]["status"] == "denied"
    assert "not allowed by the agent profile" in result["decision"]["reason"]


def test_runtime_adapter_is_not_invoked_for_sensitive_command_without_approval(tmp_path: Path) -> None:
    store, project, profile, run, workspace = make_agent_run(tmp_path, allowed_tools=["openhands"])
    adapter = FakeRuntimeAdapter()
    workspace_path = str(workspace["path"])

    result = ToolBroker(store.connection, runtime_adapters={"openhands": adapter}).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "openhands",
            "command": "pnpm add left-pad",
            "execute": True,
            "workspaceId": workspace["id"],
            "workspacePath": workspace_path,
            "path": workspace_path,
        },
    )

    assert adapter.calls == []
    assert result["decision"]["decision"] == "requires_approval"
    assert result["toolCall"]["status"] == "approval_required"
    assert "Runtime adapter command" in result["decision"]["reason"]


def test_agent_and_model_configuration_endpoints_reject_invalid_catalog_values(tmp_path: Path) -> None:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    bad_profile = client.post(
        "/api/v1/agent-profiles",
        headers=headers,
        json={
            "id": "Bad Profile!",
            "name": "Bad Profile",
            "role": "wizard",
            "runtimeMode": "unsafe_runtime",
            "permissionProfile": "admin",
            "allowedTools": ["shell"],
        },
    )
    assert bad_profile.status_code == 422

    bad_policy = client.post(
        "/api/v1/model-gateway/role-policies",
        headers=headers,
        json={
            "id": "bad_policy",
            "role": "bad_policy",
            "routingProfileId": "balanced_best_value",
            "preferred": [{"provider": "unknown_provider", "model": "free-form"}],
            "fallback": [],
            "maxCostPerTaskUsd": -1,
            "maxTokensPerRun": 1,
            "allowRemote": True,
            "allowLocal": True,
        },
    )
    assert bad_policy.status_code == 422
