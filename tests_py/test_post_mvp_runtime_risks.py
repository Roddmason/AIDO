from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.integrations.mcp_gateway import McpBrokerAdapter, mcp_gateway_status
from local_control_center.integrations.repository import IntegrationsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository
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


def write_test_mcp_server(path: Path, *, respond_to_list: bool = True) -> None:
    list_handler = (
        "    elif method == 'tools/list':\n"
        "        send({'jsonrpc': '2.0', 'id': request.get('id'), 'result': {'tools': [echo_tool]}})\n"
        if respond_to_list
        else "    elif method == 'tools/list':\n        time.sleep(10)\n"
    )
    path.write_text(
        "import json\n"
        "import sys\n"
        "import time\n\n"
        "echo_tool = {\n"
        "    'name': 'echo',\n"
        "    'description': 'Echo text for MCP adapter tests.',\n"
        "    'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}},\n"
        "}\n\n"
        "def send(message):\n"
        "    sys.stdout.write(json.dumps(message) + '\\n')\n"
        "    sys.stdout.flush()\n\n"
        "for line in sys.stdin:\n"
        "    if not line.strip():\n"
        "        continue\n"
        "    request = json.loads(line)\n"
        "    method = request.get('method')\n"
        "    if method == 'initialize':\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': request.get('id'),\n"
        "            'result': {\n"
        "                'protocolVersion': '2025-06-18',\n"
        "                'capabilities': {'tools': {'listChanged': False}},\n"
        "                'serverInfo': {'name': 'test-mcp', 'version': '0.1.0'},\n"
        "            },\n"
        "        })\n"
        "    elif method == 'notifications/initialized':\n"
        "        continue\n"
        f"{list_handler}"
        "    elif method == 'tools/call':\n"
        "        params = request.get('params') or {}\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': request.get('id'),\n"
        "            'result': {\n"
        "                'content': [{'type': 'text', 'text': params.get('arguments', {}).get('text', '')}],\n"
        "                'isError': False,\n"
        "            },\n"
        "        })\n"
        "    else:\n"
        "        send({'jsonrpc': '2.0', 'id': request.get('id'), 'error': {'code': -32601, 'message': method}})\n",
        encoding="utf-8",
    )


def mcp_server_command(script: Path) -> str:
    return subprocess.list2cmdline([sys.executable, str(script)])


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


def test_mcp_gateway_status_does_not_claim_registry_only_available() -> None:
    status = mcp_gateway_status()

    assert status["available"] is False
    assert status["mode"] != "registry_only"
    assert status["status"] in {"configuration_required", "unavailable"}
    assert status["reason"]


def test_mcp_tools_list_executes_registered_stdio_server_and_records_evidence(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    runtime.init()
    server_script = tmp_path / "test_mcp_server.py"
    write_test_mcp_server(server_script)
    IntegrationsRepository(runtime.connection).register_mcp_server(
        server_id="test-mcp",
        command=mcp_server_command(server_script),
    )

    result = McpBrokerAdapter(runtime.connection).execute(
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "test-mcp",
            "operation": "tools/list",
            "timeoutSeconds": 5,
        },
        policy_input={"workspacePath": str(tmp_path)},
    )

    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["operation"] == "tools/list"
    assert result["initializeResponse"]["result"]["capabilities"]["tools"]
    assert result["operationResponse"]["result"]["tools"][0]["name"] == "echo"
    calls = runtime.connection.execute("SELECT * FROM mcp_tool_calls WHERE mcp_server_id = ?", ("test-mcp",)).fetchall()
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "tools/list"
    assert calls[0]["status"] == "completed"
    payload = json.loads(calls[0]["payload"])
    assert payload["operationResponse"]["result"]["tools"][0]["name"] == "echo"


def test_mcp_server_command_blocks_dangerous_subprocess_args(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    runtime.init()
    server_script = tmp_path / "test_mcp_server.py"
    write_test_mcp_server(server_script)
    IntegrationsRepository(runtime.connection).register_mcp_server(
        server_id="unsafe-mcp",
        command=subprocess.list2cmdline([sys.executable, "--no-sandbox", str(server_script)]),
    )

    result = McpBrokerAdapter(runtime.connection).execute(
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "unsafe-mcp",
            "operation": "tools/list",
            "timeoutSeconds": 5,
        },
        policy_input={"workspacePath": str(tmp_path)},
    )

    assert result["status"] == "blocked"
    assert result["executed"] is False
    assert "restricted process validation" in result["reason"]
    assert "Dangerous subprocess flag" in result["reason"]
    call = runtime.connection.execute("SELECT * FROM mcp_tool_calls WHERE mcp_server_id = ?", ("unsafe-mcp",)).fetchone()
    assert call["status"] == "blocked"


def test_mcp_registered_server_without_protocol_response_is_unavailable(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    runtime.init()
    server_script = tmp_path / "silent_mcp_server.py"
    write_test_mcp_server(server_script, respond_to_list=False)
    IntegrationsRepository(runtime.connection).register_mcp_server(
        server_id="silent-mcp",
        command=mcp_server_command(server_script),
    )

    result = McpBrokerAdapter(runtime.connection).execute(
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "silent-mcp",
            "operation": "tools/list",
            "timeoutSeconds": 1,
        },
        policy_input={"workspacePath": str(tmp_path)},
    )

    assert result["status"] == "unavailable"
    assert result["executed"] is True
    assert "response" in result["reason"].lower()
    call = runtime.connection.execute("SELECT * FROM mcp_tool_calls WHERE mcp_server_id = ?", ("silent-mcp",)).fetchone()
    assert call["status"] == "unavailable"


def test_mcp_tools_list_agent_run_creates_execution_evidence(tmp_path: Path) -> None:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="MCP Evidence", path=tmp_path / "mcp-evidence", template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="mcp-list",
        agent_id="mcp_agent",
    )
    server_script = tmp_path / "test_mcp_server.py"
    write_test_mcp_server(server_script)
    store.integrations.register_mcp_server(server_id="test-mcp", command=mcp_server_command(server_script))
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "mcp_agent",
            "name": "MCP Agent",
            "role": "implementer",
            "runtimeMode": "hybrid",
            "permissionProfile": "dev_safe",
            "allowedTools": ["mcp"],
        },
        headers=headers,
    )

    response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "mcp_agent",
            "taskId": "mcp-list",
            "input": {
                "toolCalls": [
                    {
                        "tool": "mcp",
                        "execute": True,
                        "serverId": "test-mcp",
                        "operation": "tools/list",
                        "workspaceId": workspace["id"],
                        "workspacePath": str(workspace["path"]),
                        "path": str(workspace["path"]),
                        "timeoutSeconds": 5,
                    }
                ]
            },
        },
        headers=headers,
    )

    assert response.status_code == 202
    agent_run = response.json()["agentRun"]
    assert agent_run["status"] == "completed"
    assert agent_run["output"]["evidence_refs"]
    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["status"] == "completed"
    assert tool_call["payload"]["execution"] == "runtime_adapter:mcp"
    assert tool_call["payload"]["executionResult"]["operationResponse"]["result"]["tools"][0]["name"] == "echo"
    evidence = client.get("/api/v1/evidence").json()["evidencePackages"]
    package = next(item for item in evidence if item["id"] == agent_run["output"]["evidence_refs"][0])
    assert package["agentId"] == "mcp_agent"
    assert package["testResults"][0]["execution"] == "runtime_adapter:mcp"
    assert package["testResults"][0]["command"] == "tools/list"
    assert package["testResults"][0]["status"] == "completed"


def test_mcp_agent_run_is_runtime_unavailable_when_server_does_not_respond(tmp_path: Path) -> None:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="MCP Unavailable", path=tmp_path / "mcp-unavailable", template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="mcp-timeout",
        agent_id="mcp_agent",
    )
    server_script = tmp_path / "silent_mcp_server.py"
    write_test_mcp_server(server_script, respond_to_list=False)
    store.integrations.register_mcp_server(server_id="silent-mcp", command=mcp_server_command(server_script))
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "mcp_agent",
            "name": "MCP Agent",
            "role": "implementer",
            "runtimeMode": "hybrid",
            "permissionProfile": "dev_safe",
            "allowedTools": ["mcp"],
        },
        headers=headers,
    )

    response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "mcp_agent",
            "taskId": "mcp-timeout",
            "input": {
                "toolCalls": [
                    {
                        "tool": "mcp",
                        "execute": True,
                        "serverId": "silent-mcp",
                        "operation": "tools/list",
                        "workspaceId": workspace["id"],
                        "workspacePath": str(workspace["path"]),
                        "path": str(workspace["path"]),
                        "timeoutSeconds": 1,
                    }
                ]
            },
        },
        headers=headers,
    )

    assert response.status_code == 202
    agent_run = response.json()["agentRun"]
    assert agent_run["status"] == "runtime_unavailable"
    assert agent_run["output"]["evidence_refs"]
    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["status"] == "unavailable"
    assert tool_call["payload"]["executionResult"]["status"] == "unavailable"
    evidence = client.get("/api/v1/evidence").json()["evidencePackages"]
    package = next(item for item in evidence if item["id"] == agent_run["output"]["evidence_refs"][0])
    assert package["qaVerdict"] == "failed"
    assert package["testResults"][0]["status"] == "skipped_with_reason"
    assert package["testResults"][0]["metadata"]["runtimeStatus"] == "unavailable"


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


def test_mcp_request_method_tools_call_is_policy_gated_when_operation_is_omitted(tmp_path: Path) -> None:
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
            "workspaceId": workspace["id"],
            "workspacePath": workspace_path,
            "path": workspace_path,
            "request": {
                "jsonrpc": "2.0",
                "id": "mcp-call-1",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            },
        },
    )

    assert adapter.calls == []
    assert result["decision"]["decision"] == "requires_approval"
    assert result["toolCall"]["status"] == "approval_required"


def test_mcp_tools_call_executes_only_with_consumable_permission_grant(tmp_path: Path) -> None:
    store, project, profile, run, workspace = make_agent_run(tmp_path, allowed_tools=["mcp"])
    adapter = FakeRuntimeAdapter()
    workspace_path = str(workspace["path"])
    job = store.jobs.create_job(project_id=project["id"], kind="agent.tool", payload={"tool": "mcp"})["job"]

    requested = ToolBroker(store.connection, runtime_adapters={"mcp": adapter}).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        job_id=job["id"],
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
    assert requested["decision"]["decision"] == "requires_approval"
    assert adapter.calls == []
    grant = store.jobs.approve_action(
        job["id"],
        requested["actionRequest"]["id"],
        reason="Allow one audited MCP tool call for this job.",
    )["permissionGrant"]

    executed = ToolBroker(store.connection, runtime_adapters={"mcp": adapter}).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        job_id=job["id"],
        tool_call={
            "tool": "mcp",
            "execute": True,
            "serverId": "local-docs",
            "operation": "tools/call",
            "workspaceId": workspace["id"],
            "workspacePath": workspace_path,
            "path": workspace_path,
            "approvalGrantId": grant["id"],
        },
    )

    assert executed["decision"]["decision"] == "allow"
    assert adapter.calls
    assert executed["toolCall"]["status"] == "completed"
    consumed_grant = SecurityPolicyRepository(store.connection).get_grant(grant["id"])
    assert consumed_grant["status"] == "consumed"
    assert consumed_grant["consumedByAgentRunId"] == run["id"]


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


def test_runtime_adapter_command_string_without_argv_is_denied_before_approval(tmp_path: Path) -> None:
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
    assert result["decision"]["decision"] == "deny"
    assert result["toolCall"]["status"] == "denied"
    assert result["actionRequest"] is None
    assert "structured argv" in result["decision"]["reason"]


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
