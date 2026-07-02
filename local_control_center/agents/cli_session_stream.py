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
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from local_control_center.agents.cli_session_events import CliSessionEventStore
from local_control_center.agents.developer_agent_contract import DEVELOPER_AGENT_ID
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy.sandbox import open_restricted_text_process
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff, run_brokered_git

CLI_SESSION_RUNNING_STATUS = "running"
DEFAULT_SESSION_TIMEOUT_SECONDS = 900
MAX_SESSION_TIMEOUT_SECONDS = 900
_MAX_SESSION_LOG_BYTES = 1_000_000
_TERMINATE_GRACE_SECONDS = 2.0
_SUPPORTED_STREAMING_CLI_RUNTIMES = frozenset(
    {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
)


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
    timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS,
    branch_name: str | None = None,
    worktree_id: str | None = None,
    process_opener: ProcessOpener = open_restricted_text_process,
) -> dict[str, Any]:
    """Inserta la sesión en ``running`` y lanza su ejecución en streaming en un hilo de fondo.

    El INSERT de la fila usa la conexión del caller (visible al retornar el POST); el hilo abre su propia
    conexión sobre ``db_path`` para emitir eventos y cerrar la sesión sin tocar la conexión del request.
    """
    session_id = f"cli-session-{uuid.uuid4()}"
    now = utc_now()
    timeout = _bounded_timeout(timeout_seconds)
    agent_run = _create_agent_run(
        connection,
        project_id=project_id,
        session_id=session_id,
        workspace_id=workspace_id,
        runtime=runtime,
        argv=argv,
        timeout_seconds=timeout,
        branch_name=branch_name,
        worktree_id=worktree_id,
        requested_agent_id=agent_id,
    )
    session_policy = _session_env_policy(
        env_policy=env_policy or {},
        timeout_seconds=timeout,
        agent_run_id=agent_run["id"],
        branch_name=branch_name,
        worktree_id=worktree_id,
        requested_agent_id=agent_id,
    )
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
            DEVELOPER_AGENT_ID,
            json_dumps(redact_secrets(list(argv))),
            json_dumps(redact_secrets(session_policy)),
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
            "agent_run_id": agent_run["id"],
            "timeout_seconds": timeout,
            "branch_name": branch_name,
            "worktree_id": worktree_id,
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
    agent_run_id: str,
    timeout_seconds: int,
    branch_name: str | None,
    worktree_id: str | None,
    handle: _RunningHandle,
    process_opener: ProcessOpener,
) -> None:
    connection = open_sqlite_connection(db_path)
    root = _registered_workspace_root(connection, workspace_id=workspace_id)
    events = CliSessionEventStore(connection, project_id=project_id, artifact_root=root)
    stdout_acc: list[str] = []
    stderr_acc: list[str] = []
    captured_bytes = {"stdout": 0, "stderr": 0}
    log_truncated = {"stdout": False, "stderr": False}
    broker_summary: dict[str, Any] = {}
    changed_files: list[str] = []
    diff_summary: dict[str, Any] = {}
    try:
        events.record_event(
            session_id,
            "started",
            {
                "runtime": runtime,
                "workspaceId": workspace_id,
                "command": list(argv),
                "agentId": DEVELOPER_AGENT_ID,
                "requestedAgentId": agent_id,
                "agentRunId": agent_run_id,
                "timeoutSeconds": timeout_seconds,
                "branch": {"name": branch_name} if branch_name else None,
                "worktree": {"id": worktree_id} if worktree_id else None,
            },
        )
        workspace_error = _registered_workspace_error(
            connection, workspace_id=workspace_id, workspace_path=workspace_path
        )
        if workspace_error:
            finish = _finish(
                connection,
                session_id,
                status="blocked",
                error=workspace_error,
                project_id=project_id,
                root=root,
                runtime=runtime,
                workspace_id=workspace_id,
                argv=argv,
                agent_run_id=agent_run_id,
                timeout_seconds=timeout_seconds,
                branch_name=branch_name,
                worktree_id=worktree_id,
                exit_code=None,
                changed_files=[],
                diff_summary={},
                broker_summary={},
            )
            events.record_event(
                session_id,
                "failed",
                {"status": "blocked", "reason": workspace_error, "blocked": True, **finish},
            )
            _update_agent_run(
                connection,
                agent_run_id,
                status="blocked",
                output_payload={"cliSessionId": session_id, "status": "blocked", "reason": workspace_error, **finish},
            )
            return

        profile = _developer_agent_profile(connection)
        profile_error = _developer_agent_profile_error(profile, runtime=runtime)
        if profile_error:
            finish = _finish(
                connection,
                session_id,
                status="blocked",
                error=profile_error,
                project_id=project_id,
                root=root,
                runtime=runtime,
                workspace_id=workspace_id,
                argv=argv,
                agent_run_id=agent_run_id,
                timeout_seconds=timeout_seconds,
                branch_name=branch_name,
                worktree_id=worktree_id,
                exit_code=None,
                changed_files=[],
                diff_summary={},
                broker_summary={},
            )
            events.record_event(
                session_id,
                "failed",
                {"status": "blocked", "reason": profile_error, "blocked": True, **finish},
            )
            _update_agent_run(
                connection,
                agent_run_id,
                status="blocked",
                output_payload={"cliSessionId": session_id, "status": "blocked", "reason": profile_error, **finish},
            )
            return

        runtime_error = _runtime_readiness_error(
            connection, project_id=project_id, runtime=runtime, argv=argv
        )
        if runtime_error:
            finish = _finish(
                connection,
                session_id,
                status="blocked",
                error=runtime_error,
                project_id=project_id,
                root=root,
                runtime=runtime,
                workspace_id=workspace_id,
                argv=argv,
                agent_run_id=agent_run_id,
                timeout_seconds=timeout_seconds,
                branch_name=branch_name,
                worktree_id=worktree_id,
                exit_code=None,
                changed_files=[],
                diff_summary={},
                broker_summary={},
            )
            events.record_event(
                session_id,
                "failed",
                {"status": "blocked", "reason": runtime_error, "blocked": True, **finish},
            )
            _update_agent_run(
                connection,
                agent_run_id,
                status="blocked",
                output_payload={"cliSessionId": session_id, "status": "blocked", "reason": runtime_error, **finish},
            )
            return

        broker_summary = _authorize_streaming_execution(
            connection,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            runtime=runtime,
            argv=argv,
            agent_run_id=agent_run_id,
            timeout_seconds=timeout_seconds,
            profile=profile,
        )
        events.record_event(
            session_id,
            "tool_action",
            {
                "action": "cli_command",
                "executable": str(argv[0]) if argv else "",
                "runtime": runtime,
                "toolBroker": broker_summary,
            },
        )
        if broker_summary.get("decision") != "allow":
            reason = str(broker_summary.get("reason") or "ToolBroker denied streaming CLI execution.")
            finish = _finish(
                connection,
                session_id,
                status="blocked",
                error=reason,
                project_id=project_id,
                root=root,
                runtime=runtime,
                workspace_id=workspace_id,
                argv=argv,
                agent_run_id=agent_run_id,
                timeout_seconds=timeout_seconds,
                branch_name=branch_name,
                worktree_id=worktree_id,
                exit_code=None,
                changed_files=[],
                diff_summary={},
                broker_summary=broker_summary,
            )
            events.record_event(
                session_id,
                "failed",
                {"status": "blocked", "reason": reason, "blocked": True, **finish},
            )
            _update_agent_run(
                connection,
                agent_run_id,
                status="blocked",
                output_payload={"cliSessionId": session_id, "status": "blocked", "reason": reason, **finish},
            )
            return

        before = _changed_files(
            connection,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            root=root,
            session_id=session_id,
        )
        try:
            process = process_opener(argv=argv, cwd=workspace_path, workspace_path=workspace_path)
        except (PermissionError, OSError) as error:
            reason = str(error)
            finish = _finish(
                connection,
                session_id,
                status="blocked",
                error=reason,
                project_id=project_id,
                root=root,
                runtime=runtime,
                workspace_id=workspace_id,
                argv=argv,
                agent_run_id=agent_run_id,
                timeout_seconds=timeout_seconds,
                branch_name=branch_name,
                worktree_id=worktree_id,
                exit_code=None,
                changed_files=[],
                diff_summary={},
                broker_summary=broker_summary,
            )
            events.record_event(session_id, "failed", {"status": "blocked", "reason": reason, "blocked": True, **finish})
            _update_agent_run(
                connection,
                agent_run_id,
                status="blocked",
                output_payload={"cliSessionId": session_id, "status": "blocked", "reason": reason, **finish},
            )
            return
        with _LOCK:
            handle.process = process

        output: queue.Queue[tuple[str, str | None]] = queue.Queue()
        readers = [
            threading.Thread(target=_reader, args=(process.stdout, "stdout", output), daemon=True),
            threading.Thread(target=_reader, args=(process.stderr, "stderr", output), daemon=True),
        ]
        for reader in readers:
            reader.start()
        ended = 0
        timed_out = False
        termination_requested_at: float | None = None
        killed = False
        deadline = time.monotonic() + timeout_seconds
        while ended < 2:
            now_monotonic = time.monotonic()
            if handle.cancel.is_set() and termination_requested_at is None:
                termination_requested_at = now_monotonic
                _terminate(process)
            elif not timed_out and now_monotonic >= deadline:
                timed_out = True
                termination_requested_at = now_monotonic
                _terminate(process)
            if (
                termination_requested_at is not None
                and not killed
                and process.poll() is None
                and now_monotonic - termination_requested_at >= _TERMINATE_GRACE_SECONDS
            ):
                killed = True
                _kill(process)
            try:
                stream, line = output.get(timeout=0.05)
            except queue.Empty:
                continue
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
        return_code = _wait_for_process(process)
        for reader in readers:
            reader.join(timeout=5)

        if handle.cancel.is_set():
            status, event_type, error = "cancelled", "cancelled", "Cancelled by operator."
        elif timed_out:
            status, event_type = "timed_out", "failed"
            error = f"CLI process timed out after {timeout_seconds} seconds."
        elif return_code == 0:
            status, event_type, error = "completed", "completed", None
        else:
            status, event_type = "runtime_failed", "failed"
            error = f"CLI process exited with code {return_code}."

        after = _changed_files(
            connection,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            root=root,
            session_id=session_id,
        )
        changed_files = sorted(after - before)
        for path in changed_files:
            events.record_event(session_id, "file_changed", {"path": path})
        diff_summary = _capture_diff(
            connection,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            root=root,
            session_id=session_id,
        )
        finish = _finish(
            connection,
            session_id,
            status=status,
            error=error,
            project_id=project_id,
            root=root,
            runtime=runtime,
            workspace_id=workspace_id,
            argv=argv,
            agent_run_id=agent_run_id,
            timeout_seconds=timeout_seconds,
            branch_name=branch_name,
            worktree_id=worktree_id,
            exit_code=return_code,
            changed_files=changed_files,
            diff_summary=diff_summary,
            broker_summary=broker_summary,
            stdout="".join(stdout_acc),
            stderr="".join(stderr_acc),
        )
        payload: dict[str, Any] = {
            "status": status,
            "exitCode": return_code,
            "returnCode": return_code,
            "changedFiles": changed_files,
            "diffState": diff_summary.get("state"),
            **finish,
        }
        if error:
            payload["reason"] = error
        if status == "runtime_failed":
            payload["runtimeFailed"] = True
        events.record_event(session_id, event_type, payload)
        _update_agent_run(
            connection,
            agent_run_id,
            status=status,
            output_payload={"cliSessionId": session_id, "status": status, "reason": error, **payload},
        )
    except Exception as error:
        reason = str(error)
        try:
            finish = _finish(
                connection,
                session_id,
                status="runtime_failed",
                error=reason,
                project_id=project_id,
                root=root,
                runtime=runtime,
                workspace_id=workspace_id,
                argv=argv,
                agent_run_id=agent_run_id,
                timeout_seconds=timeout_seconds,
                branch_name=branch_name,
                worktree_id=worktree_id,
                exit_code=None,
                changed_files=changed_files,
                diff_summary=diff_summary,
                broker_summary=broker_summary,
                stdout="".join(stdout_acc),
                stderr="".join(stderr_acc),
            )
            events.record_event(
                session_id,
                "failed",
                {"status": "runtime_failed", "reason": reason, "runtimeFailed": True, **finish},
            )
            _update_agent_run(
                connection,
                agent_run_id,
                status="runtime_failed",
                output_payload={"cliSessionId": session_id, "status": "runtime_failed", "reason": reason, **finish},
            )
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


def _kill(process: subprocess.Popen[str]) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass


def _wait_for_process(process: subprocess.Popen[str]) -> int | None:
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill(process)
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return process.returncode


def _changed_files(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    workspace_id: str,
    workspace_path: str | None,
    root: Path | None,
    session_id: str,
) -> set[str]:
    if not workspace_path or not workspace_id:
        return set()
    try:
        row = connection.execute(
            "SELECT path, status FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
    except sqlite3.Error:
        return set()
    if not row or row["status"] == "archived":
        return set()
    registered_path = Path(row["path"]).resolve(strict=False)
    if Path(workspace_path).resolve(strict=False) != registered_path:
        return set()
    result = run_brokered_git(
        connection=connection,
        root=root or registered_path,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_path=registered_path,
        cwd=registered_path,
        args=["status", "--porcelain"],
        task_id=f"cli_session.{session_id}.changed_files",
    )
    if result["returnCode"] != 0:
        return set()
    return {line[3:].strip() for line in result["stdout"].splitlines() if line.strip()}


def _finish(
    connection: Any,
    session_id: str,
    *,
    status: str,
    error: str | None,
    project_id: str,
    root: Path | None,
    runtime: str,
    workspace_id: str,
    argv: list[str],
    agent_run_id: str | None,
    timeout_seconds: int,
    branch_name: str | None,
    worktree_id: str | None,
    exit_code: int | None,
    changed_files: list[str],
    diff_summary: dict[str, Any],
    broker_summary: dict[str, Any],
    stdout: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    stdout_artifact_id = _write_log_artifact(connection, project_id, root, session_id, "stdout", stdout)
    stderr_artifact_id = _write_log_artifact(connection, project_id, root, session_id, "stderr", stderr)
    diff_artifact_id = _write_diff_artifact(connection, project_id, root, session_id, diff_summary)
    logs_artifact_id = _write_runtime_log_artifact(
        connection=connection,
        project_id=project_id,
        root=root,
        session_id=session_id,
        runtime=runtime,
        workspace_id=workspace_id,
        argv=argv,
        status=status,
        error=error,
        timeout_seconds=timeout_seconds,
        branch_name=branch_name,
        worktree_id=worktree_id,
        exit_code=exit_code,
        changed_files=changed_files,
        stdout_artifact_id=stdout_artifact_id,
        stderr_artifact_id=stderr_artifact_id,
        diff_artifact_id=diff_artifact_id,
        broker_summary=broker_summary,
    )
    evidence_package_id = _create_evidence_package(
        connection=connection,
        project_id=project_id,
        workspace_id=workspace_id,
        runtime=runtime,
        session_id=session_id,
        argv=argv,
        status=status,
        error=error,
        exit_code=exit_code,
        agent_run_id=agent_run_id,
        artifact_ids=[
            artifact_id
            for artifact_id in (stdout_artifact_id, stderr_artifact_id, logs_artifact_id, diff_artifact_id)
            if artifact_id
        ],
        diff_artifact_id=diff_artifact_id,
        changed_files=changed_files,
        diff_summary=diff_summary,
        broker_summary=broker_summary,
    )
    connection.execute(
        """
        UPDATE cli_sessions
        SET status = ?, finished_at = ?, error = ?, stdout_artifact_id = ?, stderr_artifact_id = ?,
            logs_artifact_id = ?
        WHERE id = ?
        """,
        (
            status,
            utc_now(),
            redact_secrets(error or ""),
            stdout_artifact_id,
            stderr_artifact_id,
            logs_artifact_id,
            session_id,
        ),
    )
    return {
        "stdoutArtifactId": stdout_artifact_id,
        "stderrArtifactId": stderr_artifact_id,
        "logsArtifactId": logs_artifact_id,
        "diffArtifactId": diff_artifact_id,
        "evidencePackageId": evidence_package_id,
    }


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


def _bounded_timeout(value: int | None) -> int:
    try:
        requested = int(value or DEFAULT_SESSION_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        requested = DEFAULT_SESSION_TIMEOUT_SECONDS
    return max(1, min(requested, MAX_SESSION_TIMEOUT_SECONDS))


def _session_env_policy(
    *,
    env_policy: dict[str, Any],
    timeout_seconds: int,
    agent_run_id: str,
    branch_name: str | None,
    worktree_id: str | None,
    requested_agent_id: str | None,
) -> dict[str, Any]:
    payload = dict(env_policy)
    payload["timeoutSeconds"] = timeout_seconds
    payload["agentRunId"] = agent_run_id
    payload["branch"] = {"name": branch_name} if branch_name else None
    payload["worktree"] = {"id": worktree_id} if worktree_id else None
    payload["requestedAgentId"] = requested_agent_id
    payload["executionBoundary"] = "tool_broker_authorized_streaming_subprocess"
    return payload


def _developer_agent_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    repository = AgentsRepository(connection)
    try:
        return repository.get_agent_profile(DEVELOPER_AGENT_ID)
    except KeyError:
        pass
    return repository.upsert_agent_profile(
        {
            "id": DEVELOPER_AGENT_ID,
            "name": "Developer Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
            "allowedRuntimes": sorted(_SUPPORTED_STREAMING_CLI_RUNTIMES),
            "allowedProviders": [],
            "allowRemote": False,
            "allowCli": True,
            "allowApi": False,
            "qualityGates": ["cli_runtime_execution"],
            "outputSchema": {},
        }
    )


def _developer_agent_profile_error(profile: dict[str, Any], *, runtime: str) -> str | None:
    if profile.get("status") != "active":
        return "DeveloperAgent profile is disabled or inactive."
    if profile.get("runtimeMode") not in {"cli", "hybrid"}:
        return "DeveloperAgent profile is not configured for CLI execution."
    if profile.get("permissionProfile") != "dev_safe":
        return "DeveloperAgent profile must use the dev_safe permission profile for CLI execution."
    if not profile.get("allowCli", False):
        return "DeveloperAgent profile does not allow CLI execution."
    allowed_tools = set(profile.get("allowedTools") or [])
    if allowed_tools and "shell" not in allowed_tools and "*" not in allowed_tools:
        return "DeveloperAgent profile does not allow shell execution through ToolBroker."
    allowed_runtimes = set(profile.get("allowedRuntimes") or [])
    if allowed_runtimes and runtime not in allowed_runtimes and "cli" not in allowed_runtimes and "*" not in allowed_runtimes:
        return f"DeveloperAgent profile does not allow runtime {runtime}."
    return None


def _runtime_readiness_error(
    connection: sqlite3.Connection, *, project_id: str | None, runtime: str, argv: list[str]
) -> str | None:
    if runtime not in _SUPPORTED_STREAMING_CLI_RUNTIMES:
        return f"Runtime {runtime} is not supported for streaming CLI execution."

    repository = RuntimeConfigRepository(connection)
    policy_decision = repository.runtime_policy_decision(
        provider_id=runtime, kind="cli", project_id=project_id
    )
    if not policy_decision.get("allowed"):
        return str(policy_decision.get("reason") or "CLI runtime execution is blocked by policy.")
    try:
        installation = repository.get_installation(runtime)
    except KeyError:
        return f"Runtime {runtime} has no registered installation."
    if installation.get("kind") != "cli":
        return f"Runtime {runtime} installation is not a CLI runtime."
    if not installation.get("enabled"):
        return f"Runtime {runtime} installation is disabled."
    if installation.get("healthStatus") != "healthy" or not installation.get("lastValidationAt"):
        return f"Runtime {runtime} installation health has not been validated."
    configured_executable = str(installation.get("executablePath") or "").strip()
    if not configured_executable:
        return f"Runtime {runtime} has no configured executable path."
    if not _argv_matches_configured_executable(argv, configured_executable):
        return f"Runtime {runtime} argv executable does not match the configured executable."

    account = _selected_runtime_account(repository, runtime)
    if account is None:
        return f"Runtime {runtime} has no enabled runtime account selected for execution."
    if account.get("healthStatus") != "healthy" or not account.get("lastValidationAt"):
        return f"Runtime {runtime} native CLI authentication has not been validated."
    if not _runtime_capability_enabled(connection, runtime=runtime, capability="code_edit"):
        return f"Runtime {runtime} does not advertise the code_edit capability required for workspace edits."
    return None


def _selected_runtime_account(
    repository: RuntimeConfigRepository, runtime: str
) -> dict[str, Any] | None:
    accounts = [account for account in repository.list_runtime_accounts(runtime) if account.get("enabled")]
    if not accounts:
        return None
    return next((account for account in accounts if account.get("isDefault")), accounts[0])


def _runtime_capability_enabled(
    connection: sqlite3.Connection, *, runtime: str, capability: str
) -> bool:
    row = connection.execute(
        """
        SELECT enabled FROM runtime_capabilities
        WHERE runtime = ? AND capability = ?
        """,
        (runtime, capability),
    ).fetchone()
    return bool(row and row["enabled"])


def _argv_matches_configured_executable(argv: list[str], configured_executable: str) -> bool:
    if not argv:
        return False
    requested = Path(str(argv[0])).expanduser()
    configured = Path(configured_executable).expanduser()
    requested_text = str(requested).lower()
    configured_text = str(configured).lower()
    if requested_text == configured_text:
        return True
    return requested.name.lower() == configured.name.lower()


def _create_agent_run(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    session_id: str,
    workspace_id: str,
    runtime: str,
    argv: list[str],
    timeout_seconds: int,
    branch_name: str | None,
    worktree_id: str | None,
    requested_agent_id: str | None,
) -> dict[str, Any]:
    profile = _developer_agent_profile(connection)
    return AgentsRepository(connection).create_agent_run(
        project_id=project_id,
        agent_profile_id=profile["id"],
        task_id=f"cli_session.{runtime}",
        input_payload={
            "cliSessionId": session_id,
            "workspaceId": workspace_id,
            "runtime": runtime,
            "argv": argv,
            "timeoutSeconds": timeout_seconds,
            "branch": {"name": branch_name} if branch_name else None,
            "worktree": {"id": worktree_id} if worktree_id else None,
            "requestedAgentId": requested_agent_id,
        },
        output_payload={},
        status=CLI_SESSION_RUNNING_STATUS,
    )


def _registered_workspace_error(
    connection: sqlite3.Connection, *, workspace_id: str, workspace_path: str | None
) -> str | None:
    row = connection.execute("SELECT path, status FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if row is None:
        return "Runtime execution requires a registered workspace."
    if row["status"] == "archived":
        return "Runtime execution workspace is archived."
    registered = Path(str(row["path"])).resolve(strict=False)
    requested = Path(str(workspace_path or "")).resolve(strict=False)
    if requested != registered:
        return "Runtime execution workspacePath does not match the registered workspace."
    if not registered.exists() or not registered.is_dir():
        return "Runtime execution workspace path does not exist."
    return None


def _registered_workspace_root(connection: sqlite3.Connection, *, workspace_id: str) -> Path | None:
    try:
        row = connection.execute("SELECT path, status FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row["status"] == "archived":
        return None
    registered = Path(str(row["path"])).resolve(strict=False)
    if not registered.exists() or not registered.is_dir():
        return None
    return registered


def _authorize_streaming_execution(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    workspace_id: str,
    workspace_path: str | None,
    runtime: str,
    argv: list[str],
    agent_run_id: str,
    timeout_seconds: int,
    profile: dict[str, Any],
) -> dict[str, Any]:
    result = ToolBroker(connection, artifact_root=workspace_path).evaluate_tool_call(
        project_id=project_id,
        agent_run_id=agent_run_id,
        agent_profile=profile,
        tool_call={
            "tool": "shell",
            "command": " ".join(argv),
            "argv": list(argv),
            "workspaceId": workspace_id,
            "workspacePath": workspace_path,
            "path": workspace_path,
            "operation": "developer_agent_runtime",
            "runtimeId": runtime,
            "capability": "cli_session_stream",
            "networkRequired": False,
            "secretsRequired": False,
            "execute": True,
            "authorizeOnly": True,
            "timeoutSeconds": timeout_seconds,
        },
    )
    decision = result["decision"]
    tool_call = result["toolCall"]
    payload = tool_call.get("payload") or {}
    return {
        "decision": decision.get("decision"),
        "reason": decision.get("reason"),
        "permissionDecisionId": decision.get("id"),
        "toolCallId": tool_call.get("id"),
        "toolCallStatus": tool_call.get("status"),
        "execution": payload.get("execution"),
        "riskLevel": decision.get("riskLevel"),
    }


def _capture_diff(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    workspace_id: str,
    workspace_path: str | None,
    root: Path | None,
    session_id: str,
) -> dict[str, Any]:
    if not workspace_path or root is None:
        return {"kind": "git_diff", "state": "degraded_workspace_missing", "status": []}
    return capture_git_diff(
        Path(workspace_path),
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=workspace_id,
        task_id=f"cli_session.{session_id}.diff",
    )


def _write_runtime_log_artifact(
    *,
    connection: sqlite3.Connection,
    project_id: str,
    root: Path | None,
    session_id: str,
    runtime: str,
    workspace_id: str,
    argv: list[str],
    status: str,
    error: str | None,
    timeout_seconds: int,
    branch_name: str | None,
    worktree_id: str | None,
    exit_code: int | None,
    changed_files: list[str],
    stdout_artifact_id: str | None,
    stderr_artifact_id: str | None,
    diff_artifact_id: str | None,
    broker_summary: dict[str, Any],
) -> str | None:
    if root is None:
        return None
    content = json_dumps(
        redact_secrets(
            {
                "sessionId": session_id,
                "runtime": runtime,
                "workspaceId": workspace_id,
                "argv": argv,
                "status": status,
                "reason": error,
                "exitCode": exit_code,
                "timeoutSeconds": timeout_seconds,
                "branch": {"name": branch_name} if branch_name else None,
                "worktree": {"id": worktree_id} if worktree_id else None,
                "changedFiles": changed_files,
                "stdoutArtifactId": stdout_artifact_id,
                "stderrArtifactId": stderr_artifact_id,
                "diffArtifactId": diff_artifact_id,
                "toolBroker": broker_summary,
            }
        )
    )
    artifact_id = f"artifact-{uuid.uuid4()}"
    written = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".runtime.json", content=content)
    EvidenceRepository(connection).create_artifact(
        project_id=project_id,
        evidence_package_id=None,
        kind="cli_runtime_log",
        path=str(Path(written["path"]).resolve(strict=False)),
        content_hash=written["hash"],
        metadata={
            "source": "cli_session_stream",
            "stream": "runtime_log",
            "cliSessionId": session_id,
            "sizeBytes": written["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
        artifact_id=artifact_id,
    )
    return artifact_id


def _write_diff_artifact(
    connection: sqlite3.Connection,
    project_id: str,
    root: Path | None,
    session_id: str,
    diff_summary: dict[str, Any],
) -> str | None:
    patch = str(diff_summary.get("patchFull") or "")
    if root is None or not patch:
        return None
    artifact_id = f"artifact-{uuid.uuid4()}"
    written = write_text_artifact(
        root=root,
        artifact_id=artifact_id,
        suffix=".diff.patch",
        content=redact_secrets(patch),
    )
    EvidenceRepository(connection).create_artifact(
        project_id=project_id,
        evidence_package_id=None,
        kind="git_diff",
        path=str(Path(written["path"]).resolve(strict=False)),
        content_hash=written["hash"],
        metadata={
            "source": "cli_session_stream",
            "cliSessionId": session_id,
            "name": "diff.patch",
            "sizeBytes": written["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
        artifact_id=artifact_id,
    )
    return artifact_id


def _create_evidence_package(
    *,
    connection: sqlite3.Connection,
    project_id: str,
    workspace_id: str,
    runtime: str,
    session_id: str,
    argv: list[str],
    status: str,
    error: str | None,
    exit_code: int | None,
    agent_run_id: str | None,
    artifact_ids: list[str],
    diff_artifact_id: str | None,
    changed_files: list[str],
    diff_summary: dict[str, Any],
    broker_summary: dict[str, Any],
) -> str:
    evidence = EvidenceRepository(connection).create_evidence_package(
        project_id=project_id,
        workflow_run_id=None,
        agent_id=DEVELOPER_AGENT_ID,
        agent_run_id=agent_run_id,
        workspace_id=workspace_id,
        runtime_id=runtime,
        task_id=f"cli_session.{session_id}",
        test_plan="Capture broker-authorized CLI session execution.",
        test_results=[
            {
                "command": " ".join(argv),
                "status": status,
                "returnCode": exit_code,
                "reason": error,
                "metadata": {"cliSessionId": session_id, "toolBroker": broker_summary},
            }
        ],
        logs=[
            {
                "cliSessionId": session_id,
                "status": status,
                "reason": error,
                "exitCode": exit_code,
            }
        ],
        diff_refs=[
            {
                "kind": "git_patch",
                "artifactId": diff_artifact_id,
                "state": diff_summary.get("state"),
                "changedFiles": changed_files,
            }
        ]
        if diff_summary
        else [],
        artifact_ids=artifact_ids,
        diff_summary={
            "state": diff_summary.get("state"),
            "branch": diff_summary.get("branch"),
            "headCommit": diff_summary.get("headCommit"),
            "changedFiles": changed_files,
            "patchSizeBytes": diff_summary.get("patchSizeBytes"),
        },
        tool_calls=[broker_summary] if broker_summary else [],
        hashes={},
        evidence_source="cli_session_stream",
        qa_verdict="passed" if status == "completed" else "failed" if status in {"runtime_failed", "timed_out"} else "blocked",
    )
    for artifact_id in artifact_ids:
        EvidenceRepository(connection).attach_artifact_to_evidence(
            artifact_id=artifact_id, evidence_package_id=evidence["id"]
        )
    return str(evidence["id"])


def _update_agent_run(
    connection: sqlite3.Connection,
    agent_run_id: str | None,
    *,
    status: str,
    output_payload: dict[str, Any],
) -> None:
    if not agent_run_id:
        return
    AgentsRepository(connection).update_agent_run_status(
        agent_run_id,
        status=status,
        output_payload=output_payload,
    )
