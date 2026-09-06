"""Regresiones del canal diagnóstico: pérdida visible, privacidad y correlación real.

@author Rodrigo Mason
"""

import errno
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_control_center.shared import telemetry


def diagnostics_module():
    # RED explícito: falta el canal independiente, no un fallo de import accidental.
    assert hasattr(telemetry, "diagnostic_event"), "Falta el canal JSONL independiente de SQLite"
    from local_control_center.shared import diagnostics

    return diagnostics


def read_events(root):
    return [
        json.loads(line)
        for path in root.glob("diag-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_context_is_explicit_and_does_not_leak_between_threads(tmp_path):
    diag = diagnostics_module()
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    sink = diag.configure_diagnostics(tmp_path)
    barrier = threading.Barrier(2)

    def run(number):
        context = ProcessExecutionContext(
            db_path=tmp_path / "unused.sqlite",
            execution_id=f"job-{number}",
            request_id=f"corr-{number}",
            attempt_id=f"attempt-{number}",
        )
        with execution_scope(context):
            barrier.wait(timeout=5)
            telemetry.diagnostic_event("test.concurrent", component="test")
        telemetry.diagnostic_event("test.outside", component="test")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, [1, 2]))
    finally:
        sink.close()
    events = read_events(tmp_path)
    inside = [e for e in events if e["event"] == "test.concurrent"]
    assert {(e["executionId"], e["requestId"], e["attemptId"]) for e in inside} == {
        ("job-1", "corr-1", "attempt-1"),
        ("job-2", "corr-2", "attempt-2"),
    }
    assert all("executionId" not in e for e in events if e["event"] == "test.outside")
    assert len({e["sequence"] for e in events}) == len(events)
    assert all(e["schemaVersion"] == 1 and e["monotonicNs"] > 0 for e in events)


def test_sqlite_failure_keeps_original_chain_without_database_or_secrets(tmp_path):
    diag = diagnostics_module()
    sink = diag.configure_diagnostics(tmp_path)
    try:
        try:
            try:
                raise ValueError("Bearer synthetic-private-token")
            except ValueError as cause:
                raise sqlite3.OperationalError("password=private-value database unavailable") from cause
        except sqlite3.Error as error:
            telemetry.diagnostic_event(
                "sqlite.error",
                component="sqlite",
                error=error,
                operation="begin",
                connectionId="conn-1",
                transactionId="txn-1",
                prompt="NEVER STORE THIS",
                fencingToken=42,
            )
    finally:
        sink.close()
    event = next(e for e in read_events(tmp_path) if e["event"] == "sqlite.error")
    assert [e["type"] for e in event["exceptionChain"]] == ["OperationalError", "ValueError"]
    assert event["fencingToken"] == 42
    text = json.dumps(event)
    assert "private-value" not in text and "synthetic-private-token" not in text and "NEVER STORE" not in text
    assert event["causeStatus"] == "UNKNOWN"


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES])
def test_disk_failures_and_full_queue_do_not_block_producers(tmp_path, monkeypatch, code):
    diag = diagnostics_module()
    entered, release = threading.Event(), threading.Event()
    sink = diag.configure_diagnostics(tmp_path, queue_count=2, queue_bytes=16384)

    def fail_write(data):
        entered.set()
        release.wait(timeout=5)
        raise OSError(code, "synthetic disk failure")

    monkeypatch.setattr(sink, "_write", fail_write)
    telemetry.diagnostic_event("test.disk", component="test")
    assert entered.wait(3)
    start = time.monotonic()
    for _ in range(100):
        telemetry.diagnostic_event("test.full", component="test")
    assert time.monotonic() - start < 1
    assert sink.status()["droppedEvents"] > 0
    assert sink.status()["diagnosticsDegraded"]
    release.set()
    sink.close()
    assert sink.status()["reason"] in {"ENOSPC", "EACCES"}


def test_utf8_rotation_global_budget_and_protected_evidence(tmp_path):
    diag = diagnostics_module()
    protected = tmp_path / "historical-P0.jsonl"
    protected.write_text("historical evidence", encoding="utf-8")
    sink = diag.configure_diagnostics(tmp_path, file_bytes=8192, global_bytes=32768)
    for n in range(60):
        telemetry.diagnostic_event(
            "test.unicode",
            component="test",
            phase="ruta con espacios / árbol " + "é" * 500,
            executionId=f"job-{n}",
        )
    sink.close()
    first = list(tmp_path.glob("diag-*.jsonl"))
    assert len(first) > 1
    assert sum(p.stat().st_size for p in first) <= 32768
    assert protected.read_text(encoding="utf-8") == "historical evidence"
    assert all(
        len(line.encode("utf-8")) <= 8192
        for p in first
        for line in p.read_text(encoding="utf-8").splitlines()
    )
    assert any("árbol" in e.get("phase", "") for e in read_events(tmp_path))


def test_oversize_event_retains_identity_and_marks_truncation(tmp_path):
    diag = diagnostics_module()
    sink = diag.configure_diagnostics(tmp_path)
    telemetry.diagnostic_event("test.large", component="test", executionId="job-large", phase="é" * 20000)
    sink.close()
    event = next(e for e in read_events(tmp_path) if e["event"] == "test.large")
    assert event["executionId"] == "job-large" and event["truncated"]
    assert len(json.dumps(event, ensure_ascii=False).encode()) <= 8192


def test_activation_is_selected_expiring_and_export_never_copies_native(tmp_path):
    diag = diagnostics_module()
    diag.activate_attempt(tmp_path, "job-selected", ttl_seconds=60)
    assert diag.attempt_options(tmp_path, "job-selected")["enabled"]
    assert not diag.attempt_options(tmp_path, "job-other")["enabled"]
    sink = diag.configure_diagnostics(tmp_path)
    telemetry.diagnostic_event(
        "test.export",
        component="test",
        executionId="job-selected",
        evidenceRefs=[{"classification": "SENSITIVE_NATIVE", "path": "private.dmp"}],
        error=RuntimeError("Bearer synthetic-token"),
    )
    sink.close()
    (tmp_path / "private.dmp").write_bytes(b"SENSITIVE MEMORY")
    output = tmp_path / "export.zip"
    diag.export_incident(tmp_path, "job-selected", output)
    import zipfile

    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["incident.jsonl", "manifest.json"]
        assert b"SENSITIVE MEMORY" not in archive.read("incident.jsonl")
        assert b"synthetic-token" not in archive.read("incident.jsonl")
        assert b"private.dmp" not in archive.read("incident.jsonl")
    with pytest.raises(ValueError):
        diag.activate_attempt(tmp_path, "../outside", ttl_seconds=60)


def test_failed_transaction_emits_original_error_without_rollback_caller(tmp_path):
    diag = diagnostics_module()
    from local_control_center.shared.db import immediate_transaction

    sink = diag.configure_diagnostics(tmp_path)
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError), immediate_transaction(connection):
            pytest.fail("nested BEGIN must fail")
        assert connection.in_transaction
    finally:
        connection.close()
        sink.close()
    event = next((e for e in read_events(tmp_path) if e["event"] == "sqlite.transaction.error"), None)
    assert event is not None, "SQLite causal error is missing from the independent channel"
    assert event["operation"] == "begin" and event["connectionId"] and event["transactionId"]
    assert event["exceptionChain"][0]["sqlite_errorname"] == "SQLITE_ERROR"


def test_rust_log_only_survives_for_the_selected_expiring_context(tmp_path, monkeypatch):
    from local_control_center.agents.runtime_registry import _product_owner_codex_environment
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    monkeypatch.setenv("RUST_LOG", "trace")
    assert "RUST_LOG" not in _product_owner_codex_environment(tmp_path, native_auth_copied=True)
    with execution_scope(
        ProcessExecutionContext(
            db_path=tmp_path / "unused", execution_id="selected", diagnostics_expires_at=time.time() + 30
        )
    ):
        assert (
            _product_owner_codex_environment(tmp_path, native_auth_copied=True).get("RUST_LOG")
            == "error,codex_exec=info"
        )
    with execution_scope(
        ProcessExecutionContext(
            db_path=tmp_path / "unused", execution_id="expired", diagnostics_expires_at=time.time() - 1
        )
    ):
        assert "RUST_LOG" not in _product_owner_codex_environment(tmp_path, native_auth_copied=True)


def test_diagnostic_cli_has_working_enable_incident_and_sanitized_export(tmp_path):
    import subprocess
    import sys

    command = [sys.executable, "-m", "local_control_center.diagnostics", "--root", str(tmp_path)]
    for arguments in (
        ["enable", "--execution-id", "cli-test", "--ttl-seconds", "30"],
        ["incident", "--execution-id", "cli-test"],
        ["export", "--execution-id", "cli-test", "--output", str(tmp_path / "safe.zip")],
        ["capabilities"],
    ):
        result = subprocess.run([*command, *arguments], capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)


def test_ui_error_endpoint_and_degraded_status_have_correlation_without_body(tmp_path):
    from fastapi.testclient import TestClient

    from local_control_center.api import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "ui.sqlite")
    sink = diagnostics_module().configure_diagnostics(tmp_path / "logs")
    try:
        with TestClient(create_app(runtime=runtime)) as client:
            token = client.get("/api/v1/security/handshake").json()["token"]
            response = client.post(
                "/api/v1/telemetry/ui-error",
                headers={"X-Local-Control-Token": token, "X-Correlation-ID": "corr-ui-test"},
            )
            assert response.status_code == 200
            assert response.headers["X-Correlation-ID"] == "corr-ui-test"
            assert "diagnostics" in client.get("/api/v1/telemetry/status").json()
    finally:
        runtime.close()
        sink.close()
    event = next(e for e in read_events(tmp_path / "logs") if e["event"] == "ui.render.error")
    assert event["requestId"] == "corr-ui-test"


def test_utf16_stderr_is_redacted_before_artifact_write(tmp_path):
    from local_control_center.process_supervision.capture import ArtifactCapture

    capture = ArtifactCapture(tmp_path, "stderr", threading.Event(), encoding="utf-16-le")
    raw = "Error en árbol: Bearer synthetic-secret-value".encode("utf-16-le")
    for offset in range(0, len(raw), 3):
        capture.write(raw[offset : offset + 3])
    artifact = capture.finish()
    assert "synthetic-secret" not in capture.path.read_text(encoding="utf-8")
    assert "árbol" in capture.path.read_text(encoding="utf-8")
    assert artifact["totalBytes"] == len(raw)


def test_independent_process_writers_share_global_budget_and_abrupt_exit(tmp_path):
    import subprocess
    import sys

    # Actual OS processes, common directory, no shared handler or mock writer.
    code = """import os,sys,time
from pathlib import Path
from local_control_center.shared.diagnostics import configure_diagnostics,diagnostic_event
s=configure_diagnostics(Path(sys.argv[1]),file_bytes=8192,global_bytes=32768)
for i in range(100):
 diagnostic_event('test.multiprocess',component='test',executionId=sys.argv[2],phase='x'*500)
s.close()
if sys.argv[2]=='abrupt': os._exit(9)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path), name], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for name in ("normal", "abrupt")
    ]
    for process, exit_code in zip(processes, [0, 9], strict=True):
        output, error = process.communicate(timeout=15)
        assert process.returncode == exit_code, error
        assert not output  # Logging must not corrupt stdout protocols.
    files = list(tmp_path.glob("diag-*.jsonl"))
    assert sum(p.stat().st_size for p in files) <= 32768
    events = read_events(tmp_path)
    assert len({e["processInstanceId"] for e in events}) == 2


def test_logging_observed_cost_and_bounded_queue(tmp_path):
    import statistics

    import psutil

    from tests_py.operational_acceptance_support import evidence

    diag = diagnostics_module()
    sink = diag.configure_diagnostics(tmp_path)
    process = psutil.Process()
    before = process.memory_info().rss
    times = []
    for _ in range(300):
        start = time.perf_counter_ns()
        telemetry.diagnostic_event("test.cost", component="test", executionId="logging-cost")
        times.append((time.perf_counter_ns() - start) / 1000)
        time.sleep(0.001)  # bounded representative rate, not host saturation
    sink.close()
    assert max(times) < 100000 and sink.status()["droppedEvents"] == 0
    evidence(
        "diagnostics-logging-cost",
        {
            "status": "PASS",
            "events": len(times),
            "producerMedianUs": statistics.median(times),
            "producerP95Us": sorted(times)[284],
            "producerMaxUs": max(times),
            "rssDeltaBytes": process.memory_info().rss - before,
            "loggingBytes": sum(p.stat().st_size for p in tmp_path.glob("diag-*.jsonl")),
            **sink.status(),
        },
    )


def test_optional_null_fields_do_not_erase_restored_execution_context(tmp_path):
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    diag = diagnostics_module()
    sink = diag.configure_diagnostics(tmp_path)
    with execution_scope(ProcessExecutionContext(db_path=tmp_path / "unused", execution_id="restored-job")):
        telemetry.diagnostic_event("stage.finished", component="test", executionId=None)
    sink.close()
    assert read_events(tmp_path)[0]["executionId"] == "restored-job"


def test_labelled_sensitive_content_in_stderr_and_exception_is_removed(tmp_path):
    from local_control_center.process_supervision.capture import ArtifactCapture

    text = 'Authorization: Basic synthetic-private-header\nprompt: synthetic private request\n{"refresh_token":"synthetic-private-session"}\n'
    sink = diagnostics_module().configure_diagnostics(tmp_path / "logs")
    telemetry.diagnostic_event("test.error", component="test", error=RuntimeError(text))
    sink.close()
    capture = ArtifactCapture(tmp_path, "stderr", threading.Event())
    for offset in range(0, len(text), 3):
        capture.write(text[offset : offset + 3].encode())
    capture.finish()
    combined = json.dumps(read_events(tmp_path / "logs")) + capture.path.read_text(encoding="utf-8")
    for sensitive in ("synthetic-private-header", "synthetic private request", "synthetic-private-session"):
        assert sensitive not in combined
