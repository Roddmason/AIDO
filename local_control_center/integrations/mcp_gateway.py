from __future__ import annotations

import json
import shlex
import uuid
from typing import Any

from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

from .repository import IntegrationsRepository


def mcp_gateway_status() -> dict[str, Any]:
    return {
        "id": "mcp",
        "label": "Model Context Protocol",
        "required": False,
        "available": True,
        "mode": "registry_only",
        "notes": "MCP servers are registered and audited before tool-call execution is enabled.",
    }


class McpBrokerAdapter:
    def __init__(self, connection):
        self.connection = connection
        self.repository = IntegrationsRepository(connection)
        self.sandbox = RestrictedSubprocessSandbox()

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        server_id = str(tool_call.get("serverId") or tool_call.get("mcpServerId") or "")
        if not server_id:
            return {"executed": False, "blocked": True, "reason": "MCP execution requires serverId."}
        try:
            server = self.repository.get_mcp_server(server_id)
        except KeyError as error:
            return {"executed": False, "blocked": True, "reason": str(error)}
        if server["status"] != "registered":
            return {"executed": False, "blocked": True, "reason": "MCP server is not registered."}
        if server["transport"] != "stdio":
            return {"executed": False, "blocked": True, "reason": "Only stdio MCP transport is supported in MVP."}

        request_payload = tool_call.get("request")
        if not isinstance(request_payload, dict):
            request_payload = {
                "jsonrpc": "2.0",
                "id": tool_call.get("requestId") or "aido-mcp-request",
                "method": tool_call.get("operation") or "tools/list",
                "params": tool_call.get("arguments") or {},
            }
        command = str(server["command"])
        argv = shlex.split(command, posix=False)
        if not argv:
            return {"executed": False, "blocked": True, "reason": "MCP server command is empty."}

        result = self.sandbox.execute_with_input(
            argv=argv,
            stdin_text=json.dumps(request_payload, ensure_ascii=False),
            cwd=policy_input.get("workspacePath") or None,
            workspace_path=policy_input.get("workspacePath") or None,
            timeout_seconds=int(tool_call.get("timeoutSeconds") or 30),
        )
        status = "completed" if result.get("returnCode") == 0 else "failed"
        payload: dict[str, Any] = {"adapter": "mcp", "serverId": server_id, **result}

        self.connection.execute(
            """
            INSERT INTO mcp_tool_calls (id, mcp_server_id, tool_name, status, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"mcp-call-{uuid.uuid4()}",
                server_id,
                str(tool_call.get("operation") or "tools/list"),
                status,
                json_dumps(payload),
                utc_now(),
            ),
        )
        return payload
