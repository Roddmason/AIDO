"""Captura Windows real de una excepción sintética, sin red ni credenciales.

@author Rodrigo Mason
"""

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.service import ProcessSupervisorService
from local_control_center.shared import diagnostics

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Native Windows capability; other OS NOT_RUN")


def test_job_limits_are_read_back_not_only_requested(tmp_path):
    service = ProcessSupervisorService(db_path=tmp_path / "limits.sqlite")
    process = service.start(
        argv=[sys.executable, "-c", "import time; time.sleep(2)"], cwd=tmp_path, workload_class="qa_light"
    )
    try:
        assert hasattr(process, "containment_evidence"), "Missing requested/applied/native readback receipt"
        evidence = process.containment_evidence
        assert evidence["verified"]["member"] is True
        assert evidence["verified"]["memoryBytes"] == 4 * 1024**3
        assert evidence["applied"]["memoryProcessKillOnClose"] is True
        assert evidence["verified"]["killOnClose"] is True
        assert evidence["verified"]["cpuPercent"] == 25
    finally:
        service.complete(process, exit_code=process.process.poll(), termination_reason="test_complete")


def test_native_collector_captures_one_exact_synthetic_exception(tmp_path, monkeypatch):
    collector = Path(os.environ.get("AIDO_TEST_PROCDUMP", ""))
    if not collector.is_file():
        pytest.skip("ProcDump not explicitly configured for native validation")
    assert hasattr(diagnostics, "native_capabilities"), "Missing bounded native capture integration"
    monkeypatch.setenv("AIDO_DIAGNOSTICS_DIR", str(tmp_path / "diagnostics"))
    from local_control_center.process_supervision.native_diagnostics import inspect_minidump

    source = Path(__file__).parent / "fixtures/watchdog/native_crash.cs"
    target = tmp_path / "crash sintético.exe"
    compiler = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    compiled = subprocess.run(
        [str(compiler), "/nologo", "/platform:x64", f"/out:{target}", str(source.resolve())],
        capture_output=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    diagnostics.activate_attempt(
        tmp_path / "diagnostics",
        "synthetic-capture",
        ttl_seconds=120,
        native_collector=str(collector),
        executable_sha256=digest,
    )
    sink = diagnostics.configure_diagnostics(tmp_path / "diagnostics")
    with execution_scope(
        ProcessExecutionContext(
            db_path=tmp_path / "capture.sqlite",
            execution_id="synthetic-capture",
            diagnostics_expires_at=time.time() + 120,
        )
    ):
        service = ProcessSupervisorService()
        managed = service.start(
            argv=[str(target)],
            cwd=tmp_path,
            workload_class="qa_light",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            managed.process.communicate(timeout=30)
            record = service.complete(managed, exit_code=managed.process.returncode)
        finally:
            if not managed.released:
                service.complete(managed, exit_code=managed.process.poll(), termination_reason="test_cleanup")
            sink.close()
    dumps = list((tmp_path / "diagnostics/native").rglob("*.dmp"))
    assert len(dumps) == 1
    info = inspect_minidump(dumps[0])
    assert info["pid"] == managed.process.pid
    assert info["exceptionCode"] != 0 and info["threadId"] > 0
    # ProcDump may report OS exit 0 after handling the fatal second-chance exception.
    assert record.termination_reason == "native_exception_captured"
    assert info["exceptionCode"] == 0xC0000602
    assert managed.native_capture.receipt["collectorReadyBeforeResume"] is True
    assert managed.native_capture.receipt["targetCreationTime"] == record.root_create_time
    assert managed.native_capture.receipt["collectorClosed"] is True
    assert managed.containment_evidence["verified"]["member"] is True
    cdb = Path(os.environ.get("AIDO_TEST_CDB", ""))
    if cdb.is_file():
        from local_control_center.process_supervision.service import run_supervised_capture

        # Native output is private too: its artifact DB/root inherits the dump directory ACL.
        analysis = run_supervised_capture(
            [str(cdb), "-sins", "-y", str(tmp_path), "-z", str(dumps[0]), "-c", ".ecxr; k 12; lm; q"],
            cwd=dumps[0].parent,
            db_path=dumps[0].parent / "analysis.sqlite",
            timeout_seconds=45,
            workload_class="qa_light",
        )
        assert analysis["returnCode"] == 0
        assert "c0000602" in analysis["stdout"].lower()
        assert "RaiseFailFastException" in analysis["stdout"]
        # Only a sanitized summary goes into ordinary test evidence, never registers or memory.
        from tests_py.operational_acceptance_support import evidence

        evidence(
            "diagnostics-synthetic-capture",
            {
                "status": "PASS",
                "inference": False,
                "dumpSha256": managed.native_capture.receipt["sha256"],
                "nativeExceptionCode": info["exceptionCode"],
                "pid": info["pid"],
                "processCreationTime": managed.native_capture.receipt["targetCreationTime"],
                "collectorReadyBeforeResume": True,
                "collectorClosed": True,
                "cdbExitCode": analysis["returnCode"],
                "nativeStackDiscriminating": True,
                "privateNativeDirectory": str(dumps[0].parent),
            },
        )


def test_collector_closed_when_target_resume_fails(tmp_path, monkeypatch):
    collector = Path(os.environ.get("AIDO_TEST_PROCDUMP", ""))
    if not collector.is_file():
        pytest.skip("ProcDump not explicitly configured for native validation")
    from local_control_center.process_supervision import windows_job
    from local_control_center.process_supervision.native_diagnostics import NativeCapture

    monkeypatch.setenv("AIDO_DIAGNOSTICS_DIR", str(tmp_path / "diagnostics"))
    diagnostics.activate_attempt(
        tmp_path / "diagnostics",
        "resume-failure",
        ttl_seconds=120,
        native_collector=str(collector),
        executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
    )
    captures = []
    original_init, original_resume = NativeCapture.__init__, windows_job._resume_root_thread

    def capture_init(self, **kwargs):
        original_init(self, **kwargs)
        captures.append(self)

    def fail_target_resume(pid):
        if captures and pid == captures[0].target.process.pid:
            raise OSError("synthetic target resume failure")
        original_resume(pid)

    monkeypatch.setattr(NativeCapture, "__init__", capture_init)
    monkeypatch.setattr(windows_job, "_resume_root_thread", fail_target_resume)
    with execution_scope(
        ProcessExecutionContext(db_path=tmp_path / "resume.sqlite", execution_id="resume-failure")
    ):
        service = ProcessSupervisorService()
        try:
            with pytest.raises(OSError, match="synthetic target resume failure"):
                service.start(argv=[sys.executable, "-c", "pass"], cwd=tmp_path, workload_class="qa_light")
            assert captures and captures[0].closed and captures[0].collector.released
        finally:
            for capture in captures:
                capture.finish()


def test_failed_collector_without_dump_is_fail_not_not_run(tmp_path):
    """Unit fault injection only; not a native OS certification."""
    from types import SimpleNamespace

    from local_control_center.process_supervision.native_diagnostics import NativeCapture

    capture = object.__new__(NativeCapture)
    capture.closed, capture.readers, capture.directory = False, [], tmp_path
    capture.target = SimpleNamespace(
        execution_id="failed-collector",
        managed_process_id="fixture-target",
        process=SimpleNamespace(pid=42, poll=lambda: 0xC0000409),
    )
    capture.collector = SimpleNamespace(
        released=True, captures={}, process=SimpleNamespace(wait=lambda **kwargs: 1, poll=lambda: 1)
    )
    capture.service = SimpleNamespace(complete=lambda *args, **kwargs: None)
    capture.receipt = {"classification": "SENSITIVE_NATIVE"}
    with pytest.raises(RuntimeError, match="Native capture failed"):
        capture.finish()
    assert capture.receipt["outcome"] == "FAIL"


@pytest.fixture(scope="module")
def native_failfast_targets(tmp_path_factory):
    toolchain = Path(
        "C:/Program Files/Microsoft Visual Studio/18/Community/VC/Tools/MSVC/14.50.35717/bin/Hostx64/x64"
    )
    kernel = Path("C:/Program Files (x86)/Windows Kits/10/Lib/10.0.26100.0/um/x64/kernel32.lib")
    if not (toolchain / "cl.exe").is_file() or not kernel.is_file():
        pytest.skip("Explicit local MSVC/Windows SDK fixture toolchain unavailable")
    folder = tmp_path_factory.mktemp("native-failfast")
    binaries = {}
    for pressure in (False, True):
        stem = "pressure" if pressure else "fastfail"
        target, obj = folder / f"{stem}.exe", folder / f"{stem}.obj"
        compile_result = subprocess.run(
            [
                str(toolchain / "cl.exe"),
                "/nologo",
                "/c",
                "/Zi",
                f"/Fd{folder / (stem + '.compile.pdb')}",
                *(["/DPRESSURE"] if pressure else []),
                f"/Fo{obj}",
                str(Path("tests_py/fixtures/watchdog/native_fastfail.cpp").resolve()),
            ],
            cwd=folder,
            capture_output=True,
            timeout=30,
        )
        assert compile_result.returncode == 0, compile_result.stdout
        linked = subprocess.run(
            [
                str(toolchain / "link.exe"),
                "/NOLOGO",
                "/NODEFAULTLIB",
                "/ENTRY:mainCRTStartup",
                "/SUBSYSTEM:CONSOLE",
                "/DEBUG",
                f"/OUT:{target}",
                str(obj),
                str(kernel),
            ],
            cwd=folder,
            capture_output=True,
            timeout=30,
        )
        assert linked.returncode == 0, linked.stdout
        binaries[pressure] = target
    return binaries


@pytest.mark.parametrize("condition", ["fastfail", "pressure", "abrupt"])
def test_native_capture_lifecycle_under_bounded_pressure(
    tmp_path, monkeypatch, native_failfast_targets, condition
):
    """Real ProcDump, native __fastfail(7), finite VirtualAlloc and separate target/collector Jobs."""
    import queue
    import shutil
    import threading
    from contextlib import closing

    import win32api
    import win32con
    import win32job

    from local_control_center.process_supervision.native_diagnostics import private_directory
    from local_control_center.process_supervision.service import run_supervised_capture
    from local_control_center.shared.db import open_sqlite_connection
    from tests_py.operational_acceptance_support import evidence, native_readback, quality_envelope

    collector, cdb = Path(os.environ.get("AIDO_TEST_PROCDUMP", "")), Path(os.environ.get("AIDO_TEST_CDB", ""))
    if not collector.is_file() or not cdb.is_file():
        pytest.skip("Native capture and analysis must both be explicitly configured")
    target = native_failfast_targets[condition == "pressure"]
    root = tmp_path / "diagnostics"
    monkeypatch.setenv("AIDO_DIAGNOSTICS_DIR", str(root))
    diagnostics.activate_attempt(
        root,
        f"native-{condition}",
        ttl_seconds=120,
        native_collector=str(collector),
        executable_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    service = ProcessSupervisorService(db_path=tmp_path / "native.sqlite")
    managed = service.start(
        argv=[str(target)],
        cwd=target.parent,
        execution_id=f"native-{condition}",
        workload_class="qa_light",
        memory_limit_bytes=32 * 1024**2,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    lines, observed = queue.Queue(), []
    reader = threading.Thread(
        target=lambda: [lines.put(line) for line in managed.process.stdout], daemon=True
    )
    reader.start()
    receipt = {"status": "FAIL", "inference": False, "condition": condition, "dumpStatus": "NOT_RUN"}
    try:
        while True:
            line = lines.get(timeout=10)
            observed.append(line.decode("utf-8").strip())
            if line.strip() == b"ready":
                break
        receipt["stdoutSignals"] = observed
        capture = managed.native_capture
        assert capture.receipt["collectorReadyBeforeResume"]
        receipt["targetJob"] = native_readback(managed)
        receipt["collectorJob"] = native_readback(capture.collector)
        receipt["qualityEnvelope"] = quality_envelope()
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION, False, capture.collector.process.pid
        )
        try:
            assert not win32job.IsProcessInJob(handle, managed.native_handle)
            receipt["collectorIndependentOfTargetJob"] = True
        finally:
            handle.Close()
        with closing(open_sqlite_connection(service.db_path)) as connection:
            receipt["reservations"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,memory_limit_bytes,cpu_limit_percent FROM resource_leases WHERE released_at IS NULL"
                )
            ]
        assert len(receipt["reservations"]) == 2
        if condition == "pressure":
            assert any(value.startswith("allocationFailedWin32=") for value in observed)
        managed.process.stdin.write(b"x" if condition == "abrupt" else b"f")
        managed.process.stdin.close()
        managed.process.wait(timeout=25)
        record = service.complete(managed, exit_code=managed.process.returncode)
        reader.join(timeout=2)
        assert not reader.is_alive()
        receipt.update(
            nativeExitCode=record.exit_code,
            terminationReason=record.termination_reason,
            capture=capture.receipt,
            collectorClosed=capture.closed,
        )
        dumps = list(capture.directory.glob("*.dmp"))
        if condition == "abrupt":
            assert not dumps
            assert record.exit_code == 123 and capture.closed
            receipt["status"] = "PASS"
        elif dumps:
            assert len(dumps) == 1 and capture.receipt["exceptionCode"] == 0xC0000409
            # Keep private binary evidence out of pytest retention and out of Git.
            retained = collector.parents[2] / "native-validation" / managed.managed_process_id
            private_directory(retained)
            for file in capture.directory.iterdir():
                if file.is_file():
                    shutil.copy2(file, retained / file.name)
            analysis = run_supervised_capture(
                [
                    str(cdb),
                    "-sins",
                    "-y",
                    str(target.parent),
                    "-z",
                    str(retained / dumps[0].name),
                    "-c",
                    ".ecxr; k 12; lm; q",
                ],
                cwd=retained,
                db_path=retained / "analysis.sqlite",
                workload_class="qa_light",
                timeout_seconds=45,
            )
            receipt.update(privateNativeDirectory=str(retained), cdbExitCode=analysis["returnCode"])
            assert analysis["returnCode"] == 0
            assert "c0000409" in analysis["stdout"].lower()
            assert "mainCRTStartup" in analysis["stdout"]
            receipt.update(status="PASS", dumpStatus="PASS", symbolMatchedFrame="mainCRTStartup")
        else:
            receipt["dumpStatus"] = "FAIL"
            pytest.fail("Synthetic capture produced no valid dump; see bounded private receipt")
    finally:
        if not managed.released:
            service.complete(managed, exit_code=managed.process.poll(), termination_reason="fixture_cleanup")
        reader.join(timeout=2)
        for stream in (managed.process.stdin, managed.process.stdout, managed.process.stderr):
            stream.close()
        receipt["collectorGone"] = managed.native_capture.collector.process.poll() is not None
        evidence(f"resource-scope-capture-{condition}", receipt)
