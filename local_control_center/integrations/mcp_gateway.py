"""Broker que ejecuta herramientas MCP por stdio bajo la política de sandbox y deja traza auditada.

Habla JSON-RPC con un servidor MCP registrado (initialize -> notificación -> operación) restringido
a un conjunto de métodos permitidos. Invariantes de seguridad: el comando se valida contra el sandbox
(``validate_restricted_process``) y se arranca con ``open_restricted_text_process``; cada llamada se
persiste en ``mcp_tool_calls`` con secretos redactados (``redact_secrets``) y la salida se trunca.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import queue
import shlex
import threading
import time
import uuid
from subprocess import TimeoutExpired
from typing import Any

from local_control_center.security_policy.sandbox import (
    open_restricted_text_process,
    validate_restricted_process,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

from .repository import IntegrationsRepository

MCP_PROTOCOL_VERSION = "2025-06-18"
READ_ONLY_METHODS = {"tools/list", "resources/list", "prompts/list"}
SUPPORTED_METHODS = READ_ONLY_METHODS | {"tools/call"}
MAX_CAPTURE_CHARS = 4000


def mcp_gateway_status() -> dict[str, Any]:
    """Describe el adaptador MCP como opcional y no disponible hasta probarlo por servidor.

    La disponibilidad real no se infiere del registro: se prueba por cada servidor mediante una
    llamada ``tools/list`` brokerada. Este estado declara esa política, no ejecuta ningún proceso.
    """
    return {
        "id": "mcp",
        "label": "Model Context Protocol",
        "required": False,
        "available": False,
        "status": "configuration_required",
        "mode": "stdio_adapter",
        "reason": "MCP availability is proven per registered server by a brokered tools/list call.",
        "notes": "Registry entries alone are not runtime availability evidence.",
    }


def _truncate(value: str) -> str:
    if len(value) <= MAX_CAPTURE_CHARS:
        return value
    return value[:MAX_CAPTURE_CHARS] + "\n[truncated]"


def _windows_command_line_to_argv(command: str) -> list[str]:
    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    argv = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("Unable to parse MCP server command.")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def _command_to_argv(command: str) -> list[str]:
    if os.name == "nt":
        return _windows_command_line_to_argv(command)
    return shlex.split(command)


def _validate_process_boundary(argv: list[str], workspace_path: str | None) -> str | None:
    error = validate_restricted_process(argv, cwd=workspace_path, workspace_path=workspace_path)
    if error:
        return f"MCP server command failed restricted process validation: {error}"
    return None


def _request_for_tool_call(method: str, tool_call: dict[str, Any]) -> dict[str, Any] | str:
    request = tool_call.get("request")
    if isinstance(request, dict):
        request_method = str(request.get("method") or "")
        if request_method != method:
            return "MCP request method must match the policy-evaluated operation."
        return request

    request_id = tool_call.get("requestId") or f"aido-mcp-{uuid.uuid4()}"
    if method == "tools/call":
        name = str(
            tool_call.get("mcpToolName")
            or tool_call.get("name")
            or tool_call.get("targetTool")
            or tool_call.get("toolName")
            or ""
        ).strip()
        if not name or name == "mcp":
            return "MCP tools/call requires the target tool name."
        params = {"name": name, "arguments": tool_call.get("arguments") or {}}
    else:
        params = tool_call.get("arguments") or {}
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _reader(stream: Any, output: queue.Queue[str]) -> None:
    for line in iter(stream.readline, ""):
        output.put(line)


class McpStdioSession:
    """Sesión JSON-RPC efímera sobre el stdio de un proceso MCP arrancado en el sandbox.

    Context manager: al entrar lanza el proceso restringido y drena stdout/stderr en hilos;
    al salir cierra stdin y termina/mata el proceso. El timeout se acota a [1, 120] segundos.
    """

    def __init__(self, *, argv: list[str], cwd: str | None, timeout_seconds: int):
        self.argv = argv
        self.cwd = cwd
        self.timeout_seconds = max(1, min(timeout_seconds, 120))
        self.stdout_lines: queue.Queue[str] = queue.Queue()
        self.stderr_lines: queue.Queue[str] = queue.Queue()
        self.stderr: list[str] = []
        self.process: Any | None = None

    def __enter__(self) -> McpStdioSession:
        self.process = open_restricted_text_process(
            argv=self.argv,
            cwd=self.cwd,
            workspace_path=self.cwd,
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        threading.Thread(target=_reader, args=(self.process.stdout, self.stdout_lines), daemon=True).start()
        threading.Thread(target=_reader, args=(self.process.stderr, self.stderr_lines), daemon=True).start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin:
            with contextlib.suppress(OSError):
                process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except TimeoutExpired:
                process.kill()

    def send(self, message: dict[str, Any]) -> None:
        """Serializa el mensaje JSON-RPC y lo escribe como una línea en el stdin del proceso.

        Raises:
            RuntimeError: si el proceso o su stdin no están disponibles.
        """
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("MCP process is not running.")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def wait_response(self, request_id: Any) -> dict[str, Any] | str:
        """Espera hasta el timeout la respuesta JSON-RPC cuyo ``id`` coincide con ``request_id``.

        Returns:
            El mensaje decodificado si llega a tiempo, o un string de error legible (proceso
            terminado antes de responder, JSON inválido en stdout, o timeout sin coincidencia).
        """
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            self._drain_stderr()
            if self.process and self.process.poll() is not None and self.stdout_lines.empty():
                return "MCP server exited before sending a matching response."
            try:
                line = self.stdout_lines.get(timeout=0.05)
            except queue.Empty:
                continue
            clean_line = line.strip()
            if not clean_line:
                continue
            try:
                message = json.loads(clean_line)
            except json.JSONDecodeError:
                return "MCP server wrote invalid JSON to stdout."
            if message.get("id") == request_id:
                return message
        self._drain_stderr()
        return "MCP server did not send a matching response before timeout."

    def _drain_stderr(self) -> None:
        while True:
            try:
                self.stderr.append(self.stderr_lines.get_nowait())
            except queue.Empty:
                return

    def stderr_text(self) -> str:
        """Devuelve el stderr acumulado del proceso, truncado para acotar el tamaño capturado."""
        self._drain_stderr()
        return _truncate("".join(self.stderr))


class McpBrokerAdapter:
    """Media la ejecución de una operación MCP contra un servidor registrado y la persiste auditada.

    Aplica una cadena de guardas (serverId presente, servidor 'registered', transport stdio,
    método permitido, comando que pasa el sandbox) y solo entonces abre la sesión stdio. Toda
    salida —bloqueada, no disponible, fallida o completada— se registra en ``mcp_tool_calls``.
    """

    def __init__(self, connection):
        self.connection = connection
        self.repository = IntegrationsRepository(connection)

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta la operación MCP solicitada respetando las guardas de política y sandbox.

        Valida el servidor y la operación, valida el comando contra el sandbox, abre la sesión
        stdio (initialize -> notificación -> operación) y devuelve un payload de resultado con el
        estado (blocked/unavailable/failed/completed). Todo intento queda persistido y redactado.
        """
        server_id = str(tool_call.get("serverId") or tool_call.get("mcpServerId") or "")
        if not server_id:
            return {
                "status": "blocked",
                "executed": False,
                "blocked": True,
                "reason": "MCP execution requires serverId.",
            }
        try:
            server = self.repository.get_mcp_server(server_id)
        except KeyError as error:
            return {"status": "blocked", "executed": False, "blocked": True, "reason": str(error)}
        if server["status"] != "registered":
            return {
                "status": "blocked",
                "executed": False,
                "blocked": True,
                "reason": "MCP server is not registered.",
            }
        if server["transport"] != "stdio":
            return {
                "status": "blocked",
                "executed": False,
                "blocked": True,
                "reason": "Only stdio MCP transport is supported in MVP.",
            }

        operation = str(tool_call.get("operation") or "tools/list")
        if operation not in SUPPORTED_METHODS:
            return self._record_call(
                server_id=server_id,
                operation=operation,
                status="blocked",
                payload={
                    "adapter": "mcp",
                    "serverId": server_id,
                    "operation": operation,
                    "status": "blocked",
                    "executed": False,
                    "blocked": True,
                    "reason": "MCP adapter supports tools/list, resources/list, prompts/list, and tools/call.",
                },
            )
        request_payload = _request_for_tool_call(operation, tool_call)
        if isinstance(request_payload, str):
            return self._record_call(
                server_id=server_id,
                operation=operation,
                status="blocked",
                payload={
                    "adapter": "mcp",
                    "serverId": server_id,
                    "operation": operation,
                    "status": "blocked",
                    "executed": False,
                    "blocked": True,
                    "reason": request_payload,
                },
            )
        command = str(server["command"])
        try:
            argv = _command_to_argv(command)
        except ValueError as error:
            return self._record_call(
                server_id=server_id,
                operation=operation,
                status="configuration_required",
                payload={
                    "adapter": "mcp",
                    "serverId": server_id,
                    "operation": operation,
                    "status": "configuration_required",
                    "executed": False,
                    "blocked": True,
                    "reason": str(error),
                },
            )
        if not argv:
            return {
                "status": "blocked",
                "executed": False,
                "blocked": True,
                "reason": "MCP server command is empty.",
            }
        cwd = policy_input.get("workspacePath") or None
        boundary_error = _validate_process_boundary(argv, str(cwd) if cwd else None)
        if boundary_error:
            return self._record_call(
                server_id=server_id,
                operation=operation,
                status="blocked",
                payload={
                    "adapter": "mcp",
                    "serverId": server_id,
                    "operation": operation,
                    "status": "blocked",
                    "executed": False,
                    "blocked": True,
                    "reason": boundary_error,
                },
            )

        started = time.perf_counter()
        initialize_request = {
            "jsonrpc": "2.0",
            "id": f"aido-mcp-init-{uuid.uuid4()}",
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aido-local-control-center", "version": "0.1.0"},
            },
        }
        try:
            with McpStdioSession(
                argv=argv,
                cwd=str(cwd) if cwd else None,
                timeout_seconds=int(tool_call.get("timeoutSeconds") or 30),
            ) as session:
                session.send(initialize_request)
                initialize_response = session.wait_response(initialize_request["id"])
                if isinstance(initialize_response, str):
                    return self._unavailable(
                        server_id=server_id,
                        operation=operation,
                        started=started,
                        reason=initialize_response,
                        stderr=session.stderr_text(),
                    )
                if initialize_response.get("error"):
                    return self._failed(
                        server_id=server_id,
                        operation=operation,
                        started=started,
                        reason="MCP server rejected initialize.",
                        initialize_response=initialize_response,
                        stderr=session.stderr_text(),
                    )
                session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                session.send(request_payload)
                operation_response = session.wait_response(request_payload["id"])
                if isinstance(operation_response, str):
                    return self._unavailable(
                        server_id=server_id,
                        operation=operation,
                        started=started,
                        reason=operation_response,
                        initialize_response=initialize_response,
                        stderr=session.stderr_text(),
                    )
                if operation_response.get("error"):
                    return self._failed(
                        server_id=server_id,
                        operation=operation,
                        started=started,
                        reason="MCP server returned a JSON-RPC error.",
                        initialize_response=initialize_response,
                        operation_response=operation_response,
                        stderr=session.stderr_text(),
                    )
                payload = {
                    "adapter": "mcp",
                    "serverId": server_id,
                    "operation": operation,
                    "status": "completed",
                    "executed": True,
                    "blocked": False,
                    "returnCode": 0,
                    "durationMs": int((time.perf_counter() - started) * 1000),
                    "protocolVersion": initialize_response.get("result", {}).get("protocolVersion"),
                    "request": redact_secrets(request_payload),
                    "initializeResponse": redact_secrets(initialize_response),
                    "operationResponse": redact_secrets(operation_response),
                    "stderr": session.stderr_text(),
                }
                return self._record_call(
                    server_id=server_id,
                    operation=operation,
                    status="completed",
                    payload=payload,
                )
        except PermissionError as error:
            return self._record_call(
                server_id=server_id,
                operation=operation,
                status="blocked",
                payload={
                    "adapter": "mcp",
                    "serverId": server_id,
                    "operation": operation,
                    "status": "blocked",
                    "executed": False,
                    "blocked": True,
                    "reason": str(error),
                },
            )
        except OSError as error:
            return self._unavailable(
                server_id=server_id,
                operation=operation,
                started=started,
                reason=str(error),
            )

    def _unavailable(
        self,
        *,
        server_id: str,
        operation: str,
        started: float,
        reason: str,
        initialize_response: dict[str, Any] | None = None,
        stderr: str = "",
    ) -> dict[str, Any]:
        payload = {
            "adapter": "mcp",
            "serverId": server_id,
            "operation": operation,
            "status": "unavailable",
            "executed": True,
            "blocked": False,
            "returnCode": None,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "reason": reason,
            "initializeResponse": redact_secrets(initialize_response) if initialize_response else None,
            "stderr": stderr,
        }
        return self._record_call(
            server_id=server_id, operation=operation, status="unavailable", payload=payload
        )

    def _failed(
        self,
        *,
        server_id: str,
        operation: str,
        started: float,
        reason: str,
        initialize_response: dict[str, Any] | None = None,
        operation_response: dict[str, Any] | None = None,
        stderr: str = "",
    ) -> dict[str, Any]:
        payload = {
            "adapter": "mcp",
            "serverId": server_id,
            "operation": operation,
            "status": "failed",
            "executed": True,
            "blocked": False,
            "returnCode": 1,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "reason": reason,
            "initializeResponse": redact_secrets(initialize_response) if initialize_response else None,
            "operationResponse": redact_secrets(operation_response) if operation_response else None,
            "stderr": stderr,
        }
        return self._record_call(server_id=server_id, operation=operation, status="failed", payload=payload)

    def _record_call(
        self,
        *,
        server_id: str,
        operation: str,
        status: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.connection.execute(
            """
            INSERT INTO mcp_tool_calls (id, mcp_server_id, tool_name, status, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"mcp-call-{uuid.uuid4()}",
                server_id,
                operation,
                status,
                json_dumps(redact_secrets(payload)),
                utc_now(),
            ),
        )
        return payload
