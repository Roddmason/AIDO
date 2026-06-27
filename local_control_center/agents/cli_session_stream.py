"""Ejecutor de sesión CLI en streaming: lanza un proceso restringido y emite los nueve eventos en vivo.

Arranca el comando a través del seam Popen validado del sandbox y corre en un hilo de fondo (con su
propia conexión SQLite) para que el POST retorne de inmediato y la UI haga poll de la actividad mientras
el proceso vive. Drena stdout/stderr en hilos lectores hacia una cola y un único escritor registra cada
línea como un evento acotado (``stdout_chunk``/``stderr_chunk``) vía ``CliSessionEventStore``; además
emite ``started``, ``tool_action``, ``file_changed`` (diff git de la sesión), y el terminal
``completed``/``failed``/``cancelled``. La fila de ``cli_sessions`` pasa de ``running`` al estado final
con sus artifacts de stdout/stderr. La cancelación mata el proceso y marca la sesión como ``cancelled``.

@author Rodrigo Mason
"""

from __future__ import annotations

import contextlib
import queue
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from local_control_center.agents.cli_session_events import CliSessionEventStore
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.security_policy.sandbox import open_restricted_text_process
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

CLI_SESSION_RUNNING_STATUS = "running"
_MAX_SESSION_LOG_BYTES = 1_000_000


class ProcessOpener(Protocol):
    """Firma del lanzador de proceso restringido (inyectable para pruebas deterministas)."""

    def __call__(self, *, argv: Any, cwd: str | None, workspace_path: str | None) -> subprocess.Popen[str]:
        """Lanza el proceso de texto restringido y devuelve su ``Popen`` con pipes en vivo."""
        ...


@dataclass
class _RunningHandle:
    """Estado en memoria de una sesión en curso, para cancelarla matando su proceso."""

    cancel: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen[str] | None = None


_LOCK = threading.Lock()
_RUNNING: dict[str, _RunningHandle] = {}


def start_cli_session(
    connection: Any,
    *,
    db_path: str | Path,
    project_id: str,
    workspace_id: str,
    workspace_path: str | None,
    runtime: str,
    executable: str,
    argv: list[str],
    env_policy: dict[str, Any] | None = None,
    agent_id: str | None = None,
    process_opener: ProcessOpener = open_restricted_text_process,
) -> dict[str, Any]:
    """Inserta la sesión en ``running`` y lanza su ejecución en streaming en un hilo de fondo.

    El INSERT de la fila usa la conexión del caller (visible al retornar el POST); el hilo abre su propia
    conexión sobre ``db_path`` para emitir eventos y cerrar la sesión sin tocar la conexión del request.
    """
    session_id = f"cli-session-{uuid.uuid4()}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO cli_sessions
            (id, runtime, executable, workspace_id, workflow_run_id, workflow_step_id, agent_id,
             command_json, env_policy_json, status, started_at, finished_at, usage_ledger_id,
             stdout_artifact_id, stderr_artifact_id, logs_artifact_id, error, created_at)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)
        """,
        (
            session_id,
            runtime,
            executable,
            workspace_id,
            agent_id,
            json_dumps(redact_secrets(list(argv))),
            json_dumps(redact_secrets(env_policy or {})),
            CLI_SESSION_RUNNING_STATUS,
            now,
            now,
        ),
    )
    handle = _RunningHandle()
    with _LOCK:
        _RUNNING[session_id] = handle
    thread = threading.Thread(
        target=_run,
        kwargs={
            "db_path": str(db_path),
            "session_id": session_id,
            "project_id": project_id,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "runtime": runtime,
            "argv": list(argv),
            "agent_id": agent_id,
            "handle": handle,
            "process_opener": process_opener,
        },
        name=f"cli-session-{session_id}",
        daemon=True,
    )
    thread.start()
    return {"id": session_id, "status": CLI_SESSION_RUNNING_STATUS, "startedAt": now}


def cancel_cli_session(session_id: str) -> bool:
    """Solicita cancelar una sesión en curso (marca el flag y mata su proceso); ``False`` si ya no corre."""
    with _LOCK:
        handle = _RUNNING.get(session_id)
    if handle is None:
        return False
    handle.cancel.set()
    if handle.process is not None:
        _terminate(handle.process)
    return True


def is_running(session_id: str) -> bool:
    """Indica si la sesión sigue activa en memoria (su hilo no ha terminado)."""
    with _LOCK:
        return session_id in _RUNNING


def _run(
    *,
    db_path: str,
    session_id: str,
    project_id: str,
    workspace_id: str,
    workspace_path: str | None,
    runtime: str,
    argv: list[str],
    agent_id: str | None,
    handle: _RunningHandle,
    process_opener: ProcessOpener,
) -> None:
    connection = open_sqlite_connection(db_path)
    root = Path(workspace_path) if workspace_path else None
    events = CliSessionEventStore(connection, project_id=project_id, artifact_root=None)
    stdout_acc: list[str] = []
    stderr_acc: list[str] = []
    captured_bytes = {"stdout": 0, "stderr": 0}
    log_truncated = {"stdout": False, "stderr": False}
    try:
        events.record_event(
            session_id,
            "started",
            {"runtime": runtime, "workspaceId": workspace_id, "command": list(argv), "agentId": agent_id},
        )
        before = _changed_files(workspace_path)
        try:
            process = process_opener(argv=argv, cwd=workspace_path, workspace_path=workspace_path)
        except (PermissionError, OSError) as error:
            reason = str(error)
            events.record_event(session_id, "failed", {"reason": reason, "blocked": True})
            _finish(connection, session_id, status="blocked", error=reason, project_id=project_id, root=root)
            return
        with _LOCK:
            handle.process = process
        events.record_event(
            session_id,
            "tool_action",
            {"action": "cli_command", "executable": str(argv[0]) if argv else "", "runtime": runtime},
        )

        output: queue.Queue[tuple[str, str | None]] = queue.Queue()
        readers = [
            threading.Thread(target=_reader, args=(process.stdout, "stdout", output), daemon=True),
            threading.Thread(target=_reader, args=(process.stderr, "stderr", output), daemon=True),
        ]
        for reader in readers:
            reader.start()
        ended = 0
        while ended < 2:
            stream, line = output.get()
            if line is None:
                ended += 1
                continue
            accumulator = stdout_acc if stream == "stdout" else stderr_acc
            if captured_bytes[stream] < _MAX_SESSION_LOG_BYTES:
                accumulator.append(line)
                captured_bytes[stream] += len(line.encode("utf-8"))
            elif not log_truncated[stream]:
                accumulator.append("\n[session log truncated]\n")
                log_truncated[stream] = True
            events.record_stream_chunk(session_id, stream=stream, text=line)
            if handle.cancel.is_set():
                _terminate(process)
        process.wait()
        for reader in readers:
            reader.join(timeout=5)
        return_code = process.returncode

        if handle.cancel.is_set():
            status, error = "cancelled", "Cancelled by operator."
        elif return_code == 0:
            status, error = "completed", None
        else:
            status, error = "failed", f"CLI process exited with code {return_code}."

        for path in sorted(_changed_files(workspace_path) - before):
            events.record_event(session_id, "file_changed", {"path": path})
        payload: dict[str, Any] = {"returnCode": return_code}
        if error:
            payload["reason"] = error
        events.record_event(session_id, status, payload)
        _finish(
            connection,
            session_id,
            status=status,
            error=error,
            project_id=project_id,
            root=root,
            stdout="".join(stdout_acc),
            stderr="".join(stderr_acc),
        )
    except Exception as error:
        reason = str(error)
        try:
            events.record_event(session_id, "failed", {"reason": reason})
            _finish(connection, session_id, status="failed", error=reason, project_id=project_id, root=root)
        except Exception:
            pass
    finally:
        with _LOCK:
            _RUNNING.pop(session_id, None)
        connection.close()


def _reader(pipe: Any, stream: str, output: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        if pipe is not None:
            for line in iter(pipe.readline, ""):
                output.put((stream, line))
    finally:
        output.put((stream, None))
        if pipe is not None:
            with contextlib.suppress(Exception):
                pipe.close()


def _terminate(process: subprocess.Popen[str]) -> None:
    try:
        if process.poll() is None:
            process.terminate()
    except Exception:
        pass


def _changed_files(workspace_path: str | None) -> set[str]:
    if not workspace_path or not git_available():
        return set()
    result = run_git(["-C", str(workspace_path), "status", "--porcelain"])
    if getattr(result, "returncode", 1) != 0:
        return set()
    return {line[3:].strip() for line in (result.stdout or "").splitlines() if line.strip()}


def _finish(
    connection: Any,
    session_id: str,
    *,
    status: str,
    error: str | None,
    project_id: str,
    root: Path | None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    stdout_artifact_id = _write_log_artifact(connection, project_id, root, session_id, "stdout", stdout)
    stderr_artifact_id = _write_log_artifact(connection, project_id, root, session_id, "stderr", stderr)
    connection.execute(
        """
        UPDATE cli_sessions
        SET status = ?, finished_at = ?, error = ?, stdout_artifact_id = ?, stderr_artifact_id = ?
        WHERE id = ?
        """,
        (status, utc_now(), redact_secrets(error or ""), stdout_artifact_id, stderr_artifact_id, session_id),
    )


def _write_log_artifact(
    connection: Any, project_id: str, root: Path | None, session_id: str, stream: str, content: str
) -> str | None:
    if not content or root is None:
        return None
    artifact_id = f"artifact-{uuid.uuid4()}"
    written = write_text_artifact(
        root=root, artifact_id=artifact_id, suffix=f".{stream}.log", content=redact_secrets(content)
    )
    EvidenceRepository(connection).create_artifact(
        project_id=project_id,
        evidence_package_id=None,
        kind="cli_stdout" if stream == "stdout" else "cli_stderr",
        path=str(Path(written["path"]).resolve(strict=False)),
        content_hash=written["hash"],
        metadata={
            "source": "cli_session_stream",
            "stream": stream,
            "cliSessionId": session_id,
            "sizeBytes": written["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
        artifact_id=artifact_id,
    )
    return artifact_id
