"""AIDO-68: real OS contention is not a denied diagnostic destination.

@author Rodrigo Mason
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from local_control_center.shared.diagnostics import budget_lock


def _events(root):
    return [
        json.loads(line)
        for p in root.glob("diag-*.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
    ]


def _read_ready(child):
    observed = []
    reader = threading.Thread(target=lambda: observed.append(child.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout=10)
    assert not reader.is_alive(), "Synthetic child did not report readiness within its deadline"
    return observed[0].strip()


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock regression")
def test_transient_windows_budget_lock_preserves_event(tmp_path):
    # Observe the real failed native lock, then release it. No sleeps or fake writer.
    code = """
import json, msvcrt, sys
from pathlib import Path
from local_control_center.shared.diagnostics import configure_diagnostics, diagnostic_event
native = msvcrt.locking
def observe(fd, mode, size):
    try:
        return native(fd, mode, size)
    except OSError as error:
        print(json.dumps({'phase': 'lock', 'errno': error.errno, 'winerror': getattr(error, 'winerror', None)}), flush=True)
        raise
msvcrt.locking = observe
sink = configure_diagnostics(Path(sys.argv[1]))
diagnostic_event('test.contended', component='test', executionId='synthetic')
sink.close()
print(json.dumps(sink.status()), flush=True)
"""
    process = None
    try:
        with budget_lock(tmp_path):
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", code, str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            observed = []
            reader = threading.Thread(target=lambda: observed.append(process.stdout.readline()))
            reader.start()
            reader.join(timeout=10)
            assert not reader.is_alive(), "Native contention was not observed"
            assert json.loads(observed[0])["errno"] == 13
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
        assert json.loads(output.splitlines()[-1])["droppedEvents"] == 0
        events = _events(tmp_path)
        assert [event["event"] for event in events] == ["test.contended"]
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_budget_lock_deadline_preserves_original_cause(tmp_path):
    from local_control_center.shared.diagnostics import configure_diagnostics, diagnostic_event

    with budget_lock(tmp_path):
        sink = configure_diagnostics(tmp_path)
        start = time.monotonic()
        diagnostic_event("test.timeout", component="test")
        sink.close()
    status = sink.status()
    assert time.monotonic() - start < 2
    assert status["droppedEvents"] == 1 and status["diagnosticsDegraded"]
    failure = status["diagnosticFailure"]
    assert failure["phase"] == "budget_lock_acquire"
    assert failure["processCreationTime"] > 0 and failure["pid"] == os.getpid()
    assert len(failure["exceptionChain"]) == 2
    assert failure["exceptionChain"][0]["type"] == "TimeoutError"
    assert not _events(tmp_path)


def test_denied_open_is_not_retried_as_contention(tmp_path, monkeypatch):
    from local_control_center.shared.diagnostics import configure_diagnostics, diagnostic_event

    calls = []
    original = Path.open

    def denied(path, *args, **kwargs):
        if path.name == ".diagnostic-budget.lock":
            calls.append(path)
            raise PermissionError(13, "synthetic denied open")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    sink = configure_diagnostics(tmp_path)
    diagnostic_event("test.denied", component="test")
    sink.close()
    assert len(calls) == 1
    status = sink.status()
    assert status["droppedEvents"] == 1 and status["reason"] == "EACCES"
    assert status["diagnosticFailure"]["phase"] == "budget_lock_open"
    assert status["diagnosticFailure"]["path"] == str(tmp_path / ".diagnostic-budget.lock")


def test_two_processes_rotate_without_unexpected_loss_and_export_exclusively(tmp_path):
    from local_control_center.process_supervision.native_diagnostics import private_directory
    from local_control_center.shared.diagnostics import export_incident

    tmp_path = tmp_path / "private-diagnostics"
    if os.name == "nt":
        private_directory(tmp_path)
    else:
        tmp_path.mkdir()

    code = """
import json,sys
from pathlib import Path
from local_control_center.shared.diagnostics import configure_diagnostics, diagnostic_event
sink=configure_diagnostics(Path(sys.argv[1]),file_bytes=8192,global_bytes=262144)
print('ready',flush=True)
assert sys.stdin.readline().strip()=='start'
for number in range(64):
    diagnostic_event('test.normal',component='test',executionId='multiprocess',phase='árbol '*80,reason='token=synthetic-private')
sink.close()
print(json.dumps(sink.status()),flush=True)
"""
    children = []
    try:
        for _ in range(2):
            children.append(
                subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-u", "-c", code, str(tmp_path)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
            )
        for child in children:
            assert _read_ready(child) == "ready"
        for child in children:
            child.stdin.write("start\n")
            child.stdin.flush()
        statuses = []
        for child in children:
            output, error = child.communicate(timeout=15)
            assert child.returncode == 0, error
            statuses.append(json.loads(output))
        assert all(s["droppedEvents"] == 0 and not s["diagnosticsDegraded"] for s in statuses)
        events = _events(tmp_path)
        assert len(events) == 128 and len({e["processInstanceId"] for e in events}) == 2
        assert list(tmp_path.glob("*.closed.jsonl"))
        export = tmp_path / "sanitized.zip"
        assert export_incident(tmp_path, "multiprocess", export)["events"] == 128
        before = export.read_bytes()
        with pytest.raises(FileExistsError):
            export_incident(tmp_path, "multiprocess", export)
        assert export.read_bytes() == before
        assert all("synthetic-private" not in json.dumps(event) for event in events)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


@pytest.mark.parametrize("termination", ["normal", "crash", "cancel"])
def test_owner_exit_releases_native_budget_lock(tmp_path, termination):
    from local_control_center.shared.diagnostics import configure_diagnostics, diagnostic_event

    code = """
import os,sys
from pathlib import Path
from local_control_center.shared.diagnostics import budget_lock
with budget_lock(Path(sys.argv[1])):
    print('locked',flush=True)
    action=sys.stdin.readline().strip()
    if action=='crash': os._exit(9)
"""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _read_ready(child) == "locked"
        if termination == "cancel":
            child.terminate()
            child.communicate(timeout=5)
            assert child.returncode != 0
        else:
            _, error = child.communicate(input=termination + "\n", timeout=5)
            assert child.returncode == (9 if termination == "crash" else 0), error
        sink = configure_diagnostics(tmp_path)
        diagnostic_event("test.recovered", component="test")
        sink.close()
        assert sink.status()["droppedEvents"] == 0
        assert len(_events(tmp_path)) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
