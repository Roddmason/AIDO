from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from local_control_center.agents.cli_session_events import CliSessionEventStore
from local_control_center.agents.cli_session_stream import (
    _MAX_SESSION_LOG_BYTES,
    cancel_cli_session,
    is_running,
    start_cli_session,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


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
        self, stdout: list[str], stderr: list[str], return_code: int, stop: threading.Event, block: bool
    ):
        self.stdout = _FakePipe(stdout, stop, block)
        self.stderr = _FakePipe(stderr, stop, block)
        self.stdin = None
        self.returncode: int | None = None
        self._return_code = return_code
        self._stop = stop
        self._terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = -15 if self._terminated else self._return_code
        return self.returncode

    def terminate(self) -> None:
        self._terminated = True
        self._stop.set()

    def kill(self) -> None:
        self.terminate()


def _opener(stdout: list[str], stderr: list[str], return_code: int, *, block: bool = False):
    stop = threading.Event()

    def opener(*, argv: Any, cwd: str | None, workspace_path: str | None) -> _FakePopen:
        return _FakePopen(stdout, stderr, return_code, stop, block)

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


def test_streaming_session_records_lifecycle_chunks_and_finishes(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
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


def test_streaming_session_can_be_cancelled(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
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
        row = connection.execute("SELECT status FROM cli_sessions WHERE id = ?", (session_id,)).fetchone()
        assert row["status"] == "cancelled"
        assert cancel_cli_session(session_id) is False  # already finished
    finally:
        connection.close()


def test_streaming_session_blocked_when_executable_is_denied(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:

        def denying_opener(*, argv: Any, cwd: str | None, workspace_path: str | None):
            raise PermissionError("Executable is not allowlisted.")

        result = start_cli_session(
            connection,
            db_path=tmp_path / "platform.sqlite",
            project_id="p1",
            workspace_id="w1",
            workspace_path=str(tmp_path),
            runtime="codex_cli",
            executable="nope",
            argv=["nope"],
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
