from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.agents.cli_session_events import (
    CLI_SESSION_EVENT_TYPES,
    MAX_EVENTS_PER_SESSION,
    CliSessionEventError,
    CliSessionEventStore,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps


def _store(tmp_path: Path):
    connection = open_sqlite_connection(tmp_path / "platform.sqlite")
    initialize_platform_schema(connection)
    return connection, CliSessionEventStore(connection, project_id="p1", artifact_root=tmp_path)


def test_phase23_registers_table_and_accepts_only_the_nine_event_types(tmp_path: Path) -> None:
    connection, store = _store(tmp_path)
    try:
        assert {row[0] for row in connection.execute("SELECT version FROM schema_migrations")} >= {23}
        for event_type in sorted(CLI_SESSION_EVENT_TYPES):
            event = store.record_event("sess-1", event_type, {"k": "v"})
            assert event["type"] == event_type
            assert event["cliSessionId"] == "sess-1"
            assert event["projectId"] == "p1"
        with pytest.raises(CliSessionEventError):
            store.record_event("sess-1", "not_a_type", {})

        # seq is a per-session monotonic counter; list is oldest-first for incremental UI append.
        seqs = [event["seq"] for event in store.list_events("sess-1")]
        assert seqs == list(range(1, len(CLI_SESSION_EVENT_TYPES) + 1))
    finally:
        connection.close()


def test_per_session_cap_keeps_only_the_newest_events(tmp_path: Path) -> None:
    connection, store = _store(tmp_path)
    try:
        total = MAX_EVENTS_PER_SESSION + 5
        for _ in range(total):
            store.record_stream_chunk("sess-cap", stream="stdout", text="line")

        summary = connection.execute(
            "SELECT COUNT(*) AS c, MIN(seq) AS lo, MAX(seq) AS hi FROM cli_session_events "
            "WHERE cli_session_id = ?",
            ("sess-cap",),
        ).fetchone()
        assert summary["c"] == MAX_EVENTS_PER_SESSION
        assert summary["hi"] == total  # newest never evicted
        assert summary["lo"] == total - MAX_EVENTS_PER_SESSION + 1  # oldest five dropped
    finally:
        connection.close()


def test_large_chunk_is_truncated_inline_and_promoted_to_an_artifact(tmp_path: Path) -> None:
    connection, store = _store(tmp_path)
    try:
        big = "X" * 10_000
        event = store.record_stream_chunk("sess-big", stream="stdout", text=big)

        # Inline payload is bounded; the full content lives in a downloadable artifact.
        assert event["payload"]["truncated"] is True
        assert event["payload"]["originalBytes"] >= 10_000
        assert len(json_dumps(event["payload"]).encode("utf-8")) <= 6_000
        artifact_id = event["artifactId"]
        assert artifact_id and event["payload"]["artifactId"] == artifact_id

        row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        assert row["kind"] == "execution_log"
        assert big in Path(row["path"]).read_text(encoding="utf-8")
    finally:
        connection.close()


def test_small_event_stays_inline_without_an_artifact(tmp_path: Path) -> None:
    connection, store = _store(tmp_path)
    try:
        event = store.record_stream_chunk("sess-small", stream="stderr", text="short line")
        assert event["type"] == "stderr_chunk"
        assert event["payload"]["text"] == "short line"
        assert event["artifactId"] is None
    finally:
        connection.close()


def test_incremental_read_after_seq_supports_polling(tmp_path: Path) -> None:
    connection, store = _store(tmp_path)
    try:
        for index in range(5):
            store.record_event("sess-inc", "stdout_chunk", {"text": f"l{index}"})

        first_two = store.list_events("sess-inc", after_seq=0, limit=2)
        assert [event["seq"] for event in first_two] == [1, 2]
        rest = store.list_events("sess-inc", after_seq=2)
        assert [event["seq"] for event in rest] == [3, 4, 5]
        assert store.list_events("sess-inc", after_seq=5) == []
        assert store.latest_seq("sess-inc") == 5
    finally:
        connection.close()
