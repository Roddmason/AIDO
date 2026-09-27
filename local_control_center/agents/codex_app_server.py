"""Lectura acotada de los límites de uso de Codex por su app-server oficial (JSON-RPC sobre stdio).

``codex app-server`` habla JSON-RPC 2.0 en líneas JSON (JSONL) por stdin/stdout, sin la cabecera
``"jsonrpc"``. El cliente abre la sesión con ``initialize`` (``clientInfo``), avisa ``initialized`` y
pide ``account/rateLimits/read``; la respuesta trae ``rateLimits.primary``/``secondary`` con
``usedPercent``, ``windowDurationMins`` y ``resetsAt`` (epoch en segundos), que interpreta
``provider_usage.parse_codex_usage``. Fuentes: ``codex-rs/app-server/README.md`` y
``codex-rs/app-server-protocol`` (``protocol/common.rs``, ``protocol/v1.rs``, ``protocol/v2/account.rs``)
del repositorio ``openai/codex``.

Frontera de ejecución: argv fijo ``[<codex>, "app-server"]`` sin ``shell``, sin argumentos del
llamador, plazo total de ``APP_SERVER_TIMEOUT_SECONDS`` (el proceso y sus hijos se matan al vencer y
también al terminar, porque el app-server es de larga vida), stdout acotado y stderr descartado. Nunca
corre dentro de una transacción SQLite (``assert_external_boundary``). El ejecutable sale de
``AIDO_CODEX_COMMAND``/``CODEX_CLI_PATH`` (como el runtime Codex) o de ``codex`` en el ``PATH`` del
entorno dado; sin ``PATH`` solo se acepta una ruta absoluta.

@author Rodrigo Mason
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from typing import Any

from local_control_center.process_supervision.context import assert_external_boundary

APP_SERVER_TIMEOUT_SECONDS = 10.0
MAX_LINE_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 4 * MAX_LINE_BYTES
_INITIALIZE_ID = 1
_RATE_LIMITS_ID = 2
_CLIENT_INFO = {"name": "aido", "title": "AIDO", "version": "1.0.0"}


class CodexAppServerError(RuntimeError):
    """El app-server no respondió los límites (sin ejecutable, plazo vencido, error o respuesta inválida)."""


def resolve_codex_command(env: Mapping[str, str]) -> str | None:
    """Ejecutable de Codex del entorno dado, o ``None`` si no se encuentra."""
    command = str(env.get("AIDO_CODEX_COMMAND") or env.get("CODEX_CLI_PATH") or "codex").strip()
    if not command:
        return None
    if os.path.isabs(command):
        return command if os.path.isfile(command) and os.access(command, os.X_OK) else None
    search_path = env.get("PATH")
    return shutil.which(command, path=search_path) if search_path else None


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Mata el proceso y sus descendientes (el ``codex`` de npm lanza el binario nativo como hijo)."""
    try:
        import psutil

        children = psutil.Process(process.pid).children(recursive=True)
    except Exception:
        children = []
    for child in children:
        with contextlib.suppress(Exception):
            child.kill()
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()


def _pump(stream: Any, lines: queue.Queue[bytes | None]) -> None:
    """Lee líneas acotadas hacia la cola; ``None`` marca fin de salida o tope alcanzado."""
    total = 0
    try:
        while total < MAX_OUTPUT_BYTES:
            line = stream.readline(MAX_LINE_BYTES)
            if not line:
                break
            total += len(line)
            lines.put(line)
    except (OSError, ValueError):
        pass
    lines.put(None)


def _send(process: subprocess.Popen[bytes], message: Mapping[str, Any]) -> None:
    if process.stdin is None:
        raise CodexAppServerError("app-server stdin is not available")
    try:
        process.stdin.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")
        process.stdin.flush()
    except OSError as error:
        raise CodexAppServerError(f"app-server closed its input: {error.__class__.__name__}") from error


def _await_response(lines: queue.Queue[bytes | None], request_id: int, deadline: float) -> dict[str, Any]:
    """Espera la respuesta a ``request_id``; ignora notificaciones y pedidos del servidor."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAppServerError("app-server did not answer in time")
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty as error:
            raise CodexAppServerError("app-server did not answer in time") from error
        if line is None:
            raise CodexAppServerError("app-server exited before answering")
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(message, dict) or message.get("id") != request_id or "method" in message:
            continue
        if message.get("error") is not None:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise CodexAppServerError(f"app-server error {error.get('code', '')}: {error.get('message', '')}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise CodexAppServerError("app-server answered without a result object")
        return result


def read_codex_rate_limits(
    *, env: Mapping[str, str], timeout: float = APP_SERVER_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Resultado de ``account/rateLimits/read`` (``{"rateLimits": {...}, ...}``) del app-server.

    Raises:
        RuntimeError: dentro de una transacción SQLite (``assert_external_boundary``); no es un fallo
            de la fuente y no debe caer a otra fuente de red.
        CodexAppServerError: cualquier otro fallo; el llamador cae a la fuente HTTP.
    """
    assert_external_boundary()
    executable = resolve_codex_command(env)
    if executable is None:
        raise CodexAppServerError("codex executable not found")
    deadline = time.monotonic() + min(float(timeout), APP_SERVER_TIMEOUT_SECONDS)
    try:
        process = subprocess.Popen(
            [executable, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            shell=False,
            close_fds=True,
        )
    except OSError as error:
        raise CodexAppServerError(f"codex app-server could not start: {error.__class__.__name__}") from error
    lines: queue.Queue[bytes | None] = queue.Queue()
    threading.Thread(target=_pump, args=(process.stdout, lines), daemon=True).start()
    try:
        _send(process, {"method": "initialize", "id": _INITIALIZE_ID, "params": {"clientInfo": _CLIENT_INFO}})
        _await_response(lines, _INITIALIZE_ID, deadline)
        _send(process, {"method": "initialized"})
        _send(process, {"method": "account/rateLimits/read", "id": _RATE_LIMITS_ID})
        return _await_response(lines, _RATE_LIMITS_ID, deadline)
    finally:
        _kill_tree(process)
