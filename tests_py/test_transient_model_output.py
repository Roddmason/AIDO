"""Canal transitorio en proceso: lectura única, TTL, topes y entrega desde el adapter."""

from __future__ import annotations

import json
import uuid
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.runtime_adapters import RuntimeAdapterBrokerAdapter, transient_output
from local_control_center.agents.runtime_adapters.transient_output import (
    TRANSIENT_OUTPUT_MAX_BYTES,
    TRANSIENT_OUTPUT_MAX_ENTRIES,
    TRANSIENT_OUTPUT_TTL_SECONDS,
    prefer_transient_output,
    put_transient_output,
    take_transient_output,
)
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import ScriptedChatReply, reasoning_llm_server, sqlite_text_dump
from tests_py.test_local_model_execution_path import MESSAGES, MODEL, PROVIDER, register_local_account

THINK_MARKER = "reasoning-marker-7f3a9c"


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "lane.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def unique_key() -> str:
    return f"agent-tool-call-{uuid.uuid4()}"


def run_broker_adapter(connection, tmp_path: Path, key: str) -> dict:
    return RuntimeAdapterBrokerAdapter(
        adapter_id="openai_compatible", connection=connection, artifact_root=tmp_path
    ).execute(
        tool_call={
            "capability": "chat",
            "timeoutSeconds": 60,
            "transientOutputKey": key,
            "input": {"model": MODEL, "messages": MESSAGES},
        },
        policy_input={
            "projectId": "project-local",
            "workspaceId": "workspace-local",
            "workspacePath": str(tmp_path),
            "providerId": PROVIDER,
        },
    )


def test_take_is_a_single_read() -> None:
    key = unique_key()
    put_transient_output(key, "raw")

    assert take_transient_output(key) == "raw"
    assert take_transient_output(key) is None


def test_expired_entries_are_never_returned(monkeypatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(transient_output, "_clock", lambda: now[0])
    key = unique_key()
    put_transient_output(key, "raw")
    now[0] += TRANSIENT_OUTPUT_TTL_SECONDS + 1

    assert take_transient_output(key) is None


def test_oversized_text_is_not_stored_and_empty_key_is_rejected() -> None:
    key = unique_key()
    put_transient_output(key, "x" * (TRANSIENT_OUTPUT_MAX_BYTES + 1))

    assert take_transient_output(key) is None
    with pytest.raises(ValueError):
        put_transient_output("", "raw")


def test_entry_cap_evicts_the_oldest_entries() -> None:
    keys = [unique_key() for _ in range(TRANSIENT_OUTPUT_MAX_ENTRIES + 1)]
    for key in keys:
        put_transient_output(key, "raw")

    assert take_transient_output(keys[0]) is None
    assert take_transient_output(keys[-1]) == "raw"


def test_prefer_transient_output_falls_back_to_the_artifact_reader() -> None:
    key = unique_key()
    put_transient_output(key, "raw")
    reads: list[str] = []

    assert prefer_transient_output(key, lambda: reads.append("artifact") or "redacted") == "raw"
    assert prefer_transient_output(key, lambda: reads.append("artifact") or "redacted") == "redacted"
    assert prefer_transient_output(None, lambda: "redacted") == "redacted"
    assert reads == ["artifact"]


def test_adapter_hands_the_unredacted_reply_to_the_channel_and_persists_only_redacted_text(lane, tmp_path):
    raw_json = '{"note": "prompt: keep this line intact", "ok": true}'
    reply = ScriptedChatReply(
        content=f"<think>{THINK_MARKER} weighing</think>\n{raw_json}",
        reasoning_content=f"{THINK_MARKER} hidden chain",
    )
    key = unique_key()
    with reasoning_llm_server([reply]) as server:
        register_local_account(lane, server.base_url)
        result = run_broker_adapter(lane, tmp_path, key)

    assert result["status"] == "completed"
    assert take_transient_output(key) == raw_json
    artifact = EvidenceRepository(lane).get_artifact_by_id(result["outputArtifactId"])
    artifact_text = Path(artifact["path"]).read_text(encoding="utf-8")
    assert "[redacted]" in artifact_text
    assert THINK_MARKER not in artifact_text
    assert THINK_MARKER not in sqlite_text_dump(lane)
    assert THINK_MARKER not in json.dumps(result)
    assert raw_json not in json.dumps(result)


def test_adapter_records_usage_and_latency_with_the_real_usage_source(lane, tmp_path):
    replies = [
        ScriptedChatReply(content='{"ok": true}'),
        ScriptedChatReply(content='{"ok": true}', usage=None),
    ]
    with reasoning_llm_server(replies) as server:
        register_local_account(lane, server.base_url)
        reported = run_broker_adapter(lane, tmp_path, unique_key())
        missing = run_broker_adapter(lane, tmp_path, unique_key())

    rows = {
        row["id"]: row
        for row in lane.execute("SELECT * FROM usage_ledger WHERE provider_id = ?", (PROVIDER,)).fetchall()
    }
    first = rows[reported["usageLedgerId"]]
    assert first["usage_source"] == "actual"
    assert (first["input_tokens"], first["output_tokens"], first["total_tokens"]) == (11, 7, 18)
    assert first["runtime_type"] == "local"
    assert first["latency_ms"] is not None and first["latency_ms"] >= 0
    assert first["actual_cost_usd"] == 0.0
    assert reported["latencyMs"] >= 0
    second = rows[missing["usageLedgerId"]]
    assert second["usage_source"] == "unknown"
    assert second["total_tokens"] is None
