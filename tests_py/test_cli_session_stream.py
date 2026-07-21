from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.cli_session_events import CliSessionEventStore
from local_control_center.agents.cli_session_stream import (
    _MAX_SESSION_LOG_BYTES,
    cancel_cli_session,
    is_running,
    start_cli_session,
)
from local_control_center.agents.developer_agent_contract import DEVELOPER_AGENT_ID
from local_control_center.agents.repository import AgentsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now


class _FakePipe:
    def __init__(self, lines: list[str], stop: threading.Event, block_at_eof: bool):
        self._lines = list(lines)
        self._index = 0
        self._stop = stop
        self._block_at_eof = block_at_eof

    def readline(self) -> str:
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        if self._block_at_eof:
            self._stop.wait(5)
        return ""

    def close(self) -> None:
        return None


class _FakePopen:
    def __init__(
        self,
        stdout: list[str],
        stderr: list[str],
        return_code: int,
        stop: threading.Event,
        block: bool,
        ignore_terminate: bool,
    ):
        self.stdout = _FakePipe(stdout, stop, block)
        self.stderr = _FakePipe(stderr, stop, block)
        self.stdin = None
        self.returncode: int | None = None
        self._return_code = return_code
        self._stop = stop
        self._terminated = False
        self._ignore_terminate = ignore_terminate

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = -15 if self._terminated else self._return_code
        return self.returncode

    def terminate(self) -> None:
        self._terminated = True
        if not self._ignore_terminate:
            self._stop.set()

    def kill(self) -> None:
        self._terminated = True
        self._stop.set()


def _opener(
    stdout: list[str],
    stderr: list[str],
    return_code: int,
    *,
    block: bool = False,
    ignore_terminate: bool = False,
):
    stop = threading.Event()

    def opener(*, argv: Any, cwd: str | None, workspace_path: str | None) -> _FakePopen:
        return _FakePopen(stdout, stderr, return_code, stop, block, ignore_terminate)

    return opener


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _connection(tmp_path: Path):
    connection = open_sqlite_connection(tmp_path / "platform.sqlite")
    initialize_platform_schema(connection)
    return connection


@pytest.fixture(autouse=True)
def _enable_cli_runtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")


def _runtime(
    connection,
    runtime_id: str,
    executable: str,
    *,
    code_edit: bool = True,
) -> None:
    now = utc_now()
    repo = RuntimeConfigRepository(connection)
    repo.set_runtime_setting("runtime.cli.enabled", True)
    repo.upsert_installation(
        {
            "runtimeId": runtime_id,
            "kind": "cli",
            "executablePath": executable,
            "enabled": True,
            "detectedVersion": f"{runtime_id} test",
            "healthStatus": "healthy",
            "lastValidationAt": now,
        }
    )
    accounts = repo.list_runtime_accounts(runtime_id)
    if accounts:
        repo.update_runtime_account(
            accounts[0]["id"],
            {"enabled": True, "isDefault": True, "healthStatus": "healthy", "lastValidationAt": now},
        )
    else:
        repo.create_runtime_account(
            {
                "runtimeId": runtime_id,
                "accountLabel": f"{runtime_id} native",
                "enabled": True,
                "isDefault": True,
                "healthStatus": "healthy",
                "lastValidationAt": now,
            }
        )
    if code_edit:
        connection.execute(
            """
            INSERT OR REPLACE INTO runtime_capabilities
                (id, runtime, capability, enabled, metadata, created_at, updated_at)
            VALUES (?, ?, 'code_edit', 1, '{}', ?, ?)
            """,
            (f"{runtime_id}:code_edit", runtime_id, now, now),
        )


def _seed_runtimes(connection) -> None:
    _runtime(connection, "codex_cli", "codex")
    _runtime(connection, "claude_code_cli", "claude")
    _runtime(connection, "openhands", "openhands")
    _runtime(connection, "swe_agent", "sweagent")


def _workspace(
    connection,
    tmp_path: Path,
    *,
    workspace_id: str = "w1",
    project_id: str = "p1",
    seed_runtimes: bool = True,
) -> str:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces
            (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
             metadata, created_at, updated_at)
        VALUES (?, ?, 'cli-session-test', 'developer_agent', ?, 'ready', 'git_worktree', '{}', ?, ?)
        """,
        (workspace_id, project_id, str(tmp_path.resolve(strict=False)), now, now),
    )
    if seed_runtimes:
        _seed_runtimes(connection)
    return workspace_id


def _row(connection, session_id: str):
    return connection.execute("SELECT * FROM cli_sessions WHERE id = ?", (session_id,)).fetchone()


def test_streaming_session_records_lifecycle_chunks_and_finishes(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=_opener(["hello\n", "world\n"], ["warn\n"], 0),
        )
        session_id = result["id"]
        assert result["status"] == "running"
        assert _wait_until(lambda: not is_running(session_id))

        types = [event["type"] for event in CliSessionEventStore(connection).list_events(session_id)]
        assert types[0] == "started"
        assert "tool_action" in types
        assert types.count("stdout_chunk") == 2
        assert types.count("stderr_chunk") == 1
        assert types[-1] == "completed"

        row = connection.execute("SELECT * FROM cli_sessions WHERE id = ?", (session_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["finished_at"] is not None
        assert row["stdout_artifact_id"] is not None  # full stdout promoted to a downloadable artifact
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("runtime", "argv"),
    [
        ("codex_cli", ["codex", "exec"]),
        ("claude_code_cli", ["claude", "--print", "work"]),
        ("openhands", ["openhands", "--headless", "--json", "-t", "work"]),
        ("swe_agent", ["sweagent", "run", "--problem_statement.text=work"]),
    ],
)
def test_streaming_session_supports_all_cli_runtimes(tmp_path: Path, runtime: str, argv: list[str]) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime=runtime,
            executable=argv[0],
            argv=argv,
            process_opener=_opener(["ok\n"], [], 0),
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        row = _row(connection, session_id)
        assert row["status"] == "completed"
        started = CliSessionEventStore(connection).list_events(session_id)[0]
        assert started["payload"]["runtime"] == runtime
    finally:
        connection.close()


def test_streaming_session_can_be_cancelled(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=_opener(["tick\n"], [], 0, block=True),  # blocks at EOF until cancelled
        )
        session_id = result["id"]
        assert _wait_until(lambda: is_running(session_id))
        # started + tool_action + the first chunk land before we cancel.
        assert _wait_until(lambda: CliSessionEventStore(connection).latest_seq(session_id) >= 3)
        assert cancel_cli_session(session_id) is True
        assert _wait_until(lambda: not is_running(session_id))

        types = [event["type"] for event in CliSessionEventStore(connection).list_events(session_id)]
        assert types[-1] == "cancelled"
        row = connection.execute(
            "SELECT status, logs_artifact_id FROM cli_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        assert row["status"] == "cancelled"
        assert row["logs_artifact_id"] is not None
        assert cancel_cli_session(session_id) is False  # already finished
    finally:
        connection.close()


def test_streaming_session_blocked_when_executable_is_denied(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)

        def denying_opener(*, argv: Any, cwd: str | None, workspace_path: str | None):
            raise PermissionError("Executable is not allowlisted.")

        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=denying_opener,
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        types = [event["type"] for event in CliSessionEventStore(connection).list_events(session_id)]
        assert "failed" in types
        row = connection.execute(
            "SELECT status, error FROM cli_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        assert row["status"] == "blocked"
        assert "allowlist" in (row["error"] or "")
    finally:
        connection.close()


def test_streaming_session_caps_the_final_log_artifact_under_a_flood(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        chunk = ("A" * 400_000) + "\n"  # ~400 KB per line; four lines is ~1.6 MB total
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=_opener([chunk, chunk, chunk, chunk], [], 0),
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id), timeout=10)

        row = connection.execute(
            "SELECT stdout_artifact_id FROM cli_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        artifact = connection.execute(
            "SELECT path FROM artifacts WHERE id = ?", (row["stdout_artifact_id"],)
        ).fetchone()
        content = Path(artifact["path"]).read_text(encoding="utf-8")

        # The full output (~1.6 MB) is capped: the artifact is bounded and carries a truncation marker.
        assert "[session log truncated]" in content
        total_input = 4 * len(chunk.encode("utf-8"))
        assert len(content.encode("utf-8")) < total_input
        assert len(content.encode("utf-8")) <= _MAX_SESSION_LOG_BYTES + len(chunk.encode("utf-8")) + 100
    finally:
        connection.close()


def test_streaming_session_marks_exit_one_as_runtime_failed_with_exit_code_and_evidence(
    tmp_path: Path,
) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=_opener(["partial log\n"], ["boom\n"], 1),
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        events = CliSessionEventStore(connection).list_events(session_id)
        terminal = events[-1]
        row = _row(connection, session_id)

        assert terminal["type"] == "failed"
        assert terminal["payload"]["status"] == "runtime_failed"
        assert terminal["payload"]["exitCode"] == 1
        assert terminal["payload"]["evidencePackageId"].startswith("evidence-")
        assert row["status"] == "runtime_failed"
        assert row["stdout_artifact_id"] is not None
        assert row["stderr_artifact_id"] is not None
        assert row["logs_artifact_id"] is not None
        assert "exited with code 1" in row["error"]
    finally:
        connection.close()


def test_streaming_session_times_out_and_preserves_partial_logs(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            timeout_seconds=1,
            process_opener=_opener(["before-timeout\n"], [], 0, block=True),
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id), timeout=5)

        row = _row(connection, session_id)
        artifact = connection.execute(
            "SELECT path FROM artifacts WHERE id = ?", (row["stdout_artifact_id"],)
        ).fetchone()
        content = Path(artifact["path"]).read_text(encoding="utf-8")
        terminal = CliSessionEventStore(connection).list_events(session_id)[-1]

        assert row["status"] == "timed_out"
        assert "timed out" in row["error"]
        assert "before-timeout" in content
        assert terminal["type"] == "failed"
        assert terminal["payload"]["status"] == "timed_out"
    finally:
        connection.close()


def test_streaming_session_kills_timeout_that_ignores_terminate(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            timeout_seconds=1,
            process_opener=_opener(["before-timeout\n"], [], 0, block=True, ignore_terminate=True),
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id), timeout=8)

        row = _row(connection, session_id)
        terminal = CliSessionEventStore(connection).list_events(session_id)[-1]
        assert row["status"] == "timed_out"
        assert terminal["payload"]["status"] == "timed_out"
        assert row["logs_artifact_id"] is not None
    finally:
        connection.close()


def test_streaming_session_blocks_runtime_without_code_edit_before_popen(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path, seed_runtimes=False)
        _runtime(connection, "openhands", "openhands", code_edit=False)
        called = False

        def opener(*, argv: Any, cwd: str | None, workspace_path: str | None):
            nonlocal called
            called = True
            return _FakePopen([], [], 0, threading.Event(), False, False)

        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="openhands",
            executable="openhands",
            argv=["openhands", "--headless", "--json", "-t", "work"],
            process_opener=opener,
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        row = _row(connection, session_id)
        assert called is False
        assert row["status"] == "blocked"
        assert "code_edit" in row["error"]
    finally:
        connection.close()


def test_streaming_session_does_not_reactivate_disabled_developer_profile(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        AgentsRepository(connection).upsert_agent_profile(
            {
                "id": DEVELOPER_AGENT_ID,
                "name": "Developer Agent",
                "role": "implementer",
                "runtimeMode": "cli",
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell"],
                "allowRemote": False,
                "allowCli": True,
                "allowApi": False,
                "status": "disabled",
            }
        )
        called = False

        def opener(*, argv: Any, cwd: str | None, workspace_path: str | None):
            nonlocal called
            called = True
            return _FakePopen([], [], 0, threading.Event(), False, False)

        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=opener,
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        row = _row(connection, session_id)
        profile = AgentsRepository(connection).get_agent_profile(DEVELOPER_AGENT_ID)
        assert called is False
        assert row["status"] == "blocked"
        assert "disabled" in row["error"]
        assert profile["status"] == "disabled"
    finally:
        connection.close()


def test_streaming_session_rejects_workspace_path_mismatch_before_popen(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path, workspace_id="w1")
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        called = False

        def opener(*, argv: Any, cwd: str | None, workspace_path: str | None):
            nonlocal called
            called = True
            return _FakePopen([], [], 0, threading.Event(), False, False)

        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(outside),
            runtime="codex_cli",
            executable="codex",
            argv=["codex", "exec"],
            process_opener=opener,
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        row = _row(connection, session_id)
        terminal = CliSessionEventStore(connection).list_events(session_id)[-1]
        assert called is False
        assert row["status"] == "blocked"
        assert "workspacePath does not match" in row["error"]
        assert terminal["payload"]["blocked"] is True
        artifact = connection.execute(
            "SELECT path FROM artifacts WHERE id = ?", (row["logs_artifact_id"],)
        ).fetchone()
        assert Path(artifact["path"]).resolve(strict=False).is_relative_to(tmp_path.resolve(strict=False))
    finally:
        connection.close()


def test_streaming_session_records_toolbroker_audit_and_assigned_branch_worktree(
    tmp_path: Path,
) -> None:
    connection = _connection(tmp_path)
    try:
        _workspace(connection, tmp_path)
        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="claude_code_cli",
            executable="claude",
            argv=["claude", "--print", "work"],
            branch_name="codex/cli-session-hardening",
            worktree_id="worktree-123",
            process_opener=_opener(["ok\n"], [], 0),
        )
        session_id = result["id"]
        assert _wait_until(lambda: not is_running(session_id))

        row = _row(connection, session_id)
        env_policy = json_loads(row["env_policy_json"], {})
        events = CliSessionEventStore(connection).list_events(session_id)
        tool_action = next(event for event in events if event["type"] == "tool_action")
        agent_runs = AgentsRepository(connection).list_agent_runs()

        assert env_policy["branch"]["name"] == "codex/cli-session-hardening"
        assert env_policy["worktree"]["id"] == "worktree-123"
        assert tool_action["payload"]["toolBroker"]["toolCallId"].startswith("agent-tool-call-")
        assert tool_action["payload"]["toolBroker"]["permissionDecisionId"].startswith("permission-decision-")
        assert tool_action["payload"]["toolBroker"]["toolCallStatus"] == "allowed"
        assert any(run["id"] == env_policy["agentRunId"] for run in agent_runs)
    finally:
        connection.close()
