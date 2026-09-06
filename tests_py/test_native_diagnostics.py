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
