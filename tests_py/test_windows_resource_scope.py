"""Bounded Windows counterexamples. Synthetic processes only; never a runtime smoke."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from local_control_center.process_supervision.models import ProcessLaunchSpec
from tests_py.operational_acceptance_support import evidence, quality_envelope

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native capability; POSIX NOT_RUN")
MIB = 1024**2


@pytest.fixture(scope="module")
def allocation_probe(tmp_path_factory):
    target = tmp_path_factory.mktemp("resource-probe") / "asignación acotada.exe"
    result = subprocess.run(
        [
            "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
            "/nologo",
            "/platform:x64",
            f"/out:{target}",
            str(Path("tests_py/fixtures/watchdog/resource_probe.cs").resolve()),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout
    return target


def spec(name, target, memory, cpu):
    return ProcessLaunchSpec(
        name, name, [str(target)], str(target.parent), "qa_light", "synthetic", memory, 8, cpu, False
    )


class Probe:
    def __init__(self, target, jobs):
        import win32api
        import win32con
        import win32job

        from local_control_center.process_supervision.windows_job import _resume_root_thread

        self.process = subprocess.Popen(
            [str(target)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=4,
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(
            target=lambda: [self.lines.put(line) for line in self.process.stdout], daemon=True
        )
        self.reader.start()
        handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, self.process.pid)
        try:
            for job in jobs:
                win32job.AssignProcessToJobObject(job, handle)
                assert win32job.IsProcessInJob(handle, job)
            _resume_root_thread(self.process.pid)
        except BaseException:
            self.process.kill()
            self.close()
            raise
        finally:
            handle.Close()

    def command(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        return json.loads(self.lines.get(timeout=8))

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write("exit\n")
            self.process.stdin.flush()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        self.process._handle.Close()


def job_snapshot(job):
    import win32api
    import win32job

    from local_control_center.process_supervision.windows_job import _readback

    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    return {
        "timestamp": time.time(),
        "handle": int(job),
        "limits": info,
        "readback": _readback(job, win32api.GetCurrentProcess()),
        "pids": list(win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList)),
        "nativeCtypes": extended_ctypes(job),
    }


def extended_ctypes(job):
    """Independent SDK layout, bytes/SIZE_T; catches a pywin32 field/packing mismatch."""
    import ctypes as c

    class Basic(c.Structure):
        _fields_ = [
            ("processTime", c.c_int64),
            ("jobTime", c.c_int64),
            ("flags", c.c_uint32),
            ("minWorkingSet", c.c_size_t),
            ("maxWorkingSet", c.c_size_t),
            ("active", c.c_uint32),
            ("affinity", c.c_size_t),
            ("priority", c.c_uint32),
            ("scheduling", c.c_uint32),
        ]

    class Extended(c.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", c.c_uint64 * 6),
            ("processMemory", c.c_size_t),
            ("jobMemory", c.c_size_t),
            ("peakProcess", c.c_size_t),
            ("peakJob", c.c_size_t),
        ]

    value = Extended()
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.QueryInformationJobObject.argtypes = [c.c_void_p, c.c_int, c.c_void_p, c.c_uint32, c.c_void_p]
    if not kernel.QueryInformationJobObject(int(job), 9, c.byref(value), c.sizeof(value), None):
        raise c.WinError(c.get_last_error())
    return {
        "class": 9,
        "sizeBytes": c.sizeof(value),
        "peakJobOffset": Extended.peakJob.offset,
        "limitFlags": value.basic.flags,
        "jobMemoryLimitBytes": value.jobMemory,
        "processMemoryLimitBytes": value.processMemory,
        "peakJobMemoryBytes": value.peakJob,
    }


@pytest.mark.parametrize("parent_mib", [64, 128])
def test_native_ancestor_memory_and_cpu_are_not_independent(allocation_probe, parent_mib):
    """Catches the assumption that a child's declared 256 MiB / 40% is independently usable."""
    import win32job

    from local_control_center.process_supervision.windows_job import _configure_job

    parent, child = win32job.CreateJobObject(None, ""), win32job.CreateJobObject(None, "")
    probes = []
    before_handles = psutil.Process().num_handles()
    receipt = {"inference": False, "status": "FAIL", "samples": [], "qualityEnvelope": quality_envelope()}
    try:
        _configure_job(parent, spec("parent", allocation_probe, parent_mib * MIB, 20))
        _configure_job(child, spec("child", allocation_probe, 256 * MIB, 40))
        # All processes are explicitly associated with the concrete parent; two also with child.
        probes = [
            Probe(allocation_probe, [parent]),
            Probe(allocation_probe, [parent, child]),
            Probe(allocation_probe, [parent, child]),
        ]
        receipt["before"] = [job_snapshot(parent), job_snapshot(child)]
        reservation = probes[2].command("reserve")
        assert reservation["success"] and reservation["committedByFixture"] == 0
        receipt["addressReservation"] = reservation
        # Fixed small round-robin requests across parent/dispatcher/child, never host exhaustion.
        failed = None
        for index in range(18):
            allocation = probes[index % 3].command("commit")
            receipt["samples"].append(
                {"allocation": allocation, "jobs": [job_snapshot(parent), job_snapshot(child)]}
            )
            if not allocation["success"]:
                failed = allocation
                break
        if parent_mib == 64:
            assert failed is not None, "The ancestor must reject commit well below the child's 256 MiB"
            assert failed["win32Error"] == 1455
        else:
            assert failed is None, "Same bounded allocation sequence must fit the 128 MiB ancestor"
        receipt["parentMiB"] = parent_mib
        receipt["cpuContract"] = {
            "parentRate": 20,
            "childRelativeRate": 40,
            "childPercentOfOuter": 8,
            "measuredCpu": False,
        }
        # Processes are paused on stdin now, not allocating. Read both bindings on same handles.
        for job in (parent, child):
            first = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            second = extended_ctypes(job)
            assert first["PeakJobMemoryUsed"] == second["peakJobMemoryBytes"]
            assert first["JobMemoryLimit"] == second["jobMemoryLimitBytes"]
            assert second["sizeBytes"] == 144 and second["peakJobOffset"] == 136
            assert second["limitFlags"] & 0x200 and not second["limitFlags"] & 0x100
        receipt["status"] = "PASS"
    finally:
        for probe in reversed(probes):
            probe.close()
        receipt["after"] = [job_snapshot(parent), job_snapshot(child)]
        child.Close()
        parent.Close()
        receipt["handlesAfter"] = psutil.Process().num_handles()
        receipt["handlesBeforeWithJobs"] = before_handles
        evidence(f"resource-scope-native-counterexample-{parent_mib}", receipt)
    assert all(not value["pids"] for value in receipt["after"])


@pytest.mark.parametrize("mode", ["independent", "borrow", "capture", "quantized"])
def test_productive_supervisor_reconciles_nested_reservations(tmp_path, mode):
    """No mock backend: prevent an independent budget inside control-plane, and CPU multiplication."""
    from contextlib import closing

    from local_control_center.process_supervision.service import ProcessSupervisorService
    from local_control_center.shared.db import open_sqlite_connection

    if mode == "capture" and not Path(os.environ.get("AIDO_TEST_PROCDUMP", "")).is_file():
        pytest.skip("Native collector not configured")

    db, output = tmp_path / "scope.sqlite", tmp_path / "result.json"
    # This DB models nested lease policy, not a second independent host allocation.
    # Under the public runner, retain its real admitted native envelope and remove
    # the unrelated instantaneous CPU sample from this fixture's domain admission.
    # Outside that runner retain real admission; never claim mocked OS containment.
    snapshot = None
    if os.environ.get("AIDO_TEST_QUALITY_DB"):
        from local_control_center.host_resources.models import ResourceSnapshot

        assert quality_envelope()["status"] == "PASS"
        snapshot = ResourceSnapshot.test_snapshot()
    service = ProcessSupervisorService(db_path=db, resource_snapshot=snapshot)
    parent = service.start(
        argv=[
            sys.executable,
            "-m",
            "tests_py.fixtures.watchdog.resource_scope_runner",
            str(db),
            mode,
            str(output),
        ],
        cwd=Path.cwd(),
        execution_id="scope-parent",
        workload_class="control_plane" if mode == "independent" else "qa_light",
        cpu_limit_percent=23 if mode == "quantized" else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = parent.process.communicate(timeout=30)
        assert parent.process.returncode == 0, (stdout, stderr)
        result = json.loads(output.read_text(encoding="utf-8"))
        if mode in {"independent", "capture"}:
            assert result["rejected"], "Independent reservation spawned below control-plane budget"
            assert "resource_scope_conflict" in result["reason"]
            with closing(open_sqlite_connection(db)) as connection:
                assert (
                    connection.execute(
                        "SELECT count(*) FROM managed_processes WHERE execution_id='scope-child'"
                    ).fetchone()[0]
                    == 0
                )
        elif mode == "quantized":
            assert not result["rejected"]
            assert result["containment"]["verified"]["cpuPercent"] == 30.43
            assert not result["leaf"]["rejected"], result["leaf"]
            leaf = result["leaf"]["containment"]
            assert leaf["verified"]["cpuPercent"] == 100
            assert leaf["scope"]["cpuPercentOfAidoRoot"] == 7
            assert leaf["scope"]["effectiveCpuPercentOfAidoRoot"] == pytest.approx(6.9989)
            assert leaf["scope"]["cpuQuantizationLossPercent"] == pytest.approx(0.0011)
            assert all(row["member"] for row in leaf["scope"]["ancestors"])
        else:
            assert not result["rejected"]
            assert result["containment"]["verified"]["cpuPercent"] == 100
            assert result["containment"]["scope"]["cpuPercentOfAidoRoot"] == 25
            assert result["containment"]["scope"]["sharedReservation"] is True
            events = [
                json.loads(line)
                for path in (tmp_path / "diagnostics").glob("diag-*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            contained = next(event for event in events if event["event"] == "process.contained")
            assert contained["effectiveConfig"]["resourceScope"] == result["containment"]["scope"]
        evidence(
            f"resource-scope-supervisor-{mode}",
            {"status": "PASS", "qualityEnvelope": quality_envelope(), **result},
        )
    finally:
        service.complete(parent, exit_code=parent.process.poll(), termination_reason="fixture_complete")


def test_release_closes_owned_process_handle_before_object_collection(tmp_path):
    """A finished receipt/managed object must not retain its OS process handle indefinitely."""
    import win32api

    from local_control_center.process_supervision.service import ProcessSupervisorService

    service = ProcessSupervisorService(db_path=tmp_path / "handles.sqlite")
    process = service.start(argv=[sys.executable, "-c", "pass"], cwd=tmp_path, workload_class="qa_light")
    process.process.wait(timeout=10)
    handle = int(process.process._handle)
    service.complete(process, exit_code=process.process.returncode)
    with pytest.raises(win32api.error):
        win32api.GetHandleInformation(handle)


def test_job_name_collision_cannot_modify_or_take_ownership_of_existing_job(allocation_probe):
    import uuid

    import win32job

    from local_control_center.process_supervision.windows_job import (
        WindowsJobObjectProcessSupervisor,
        job_name,
    )

    launch = spec(str(uuid.uuid4()), allocation_probe, 64 * MIB, 20)
    job = win32job.CreateJobObject(None, job_name(launch.managed_process_id))
    backend, managed = WindowsJobObjectProcessSupervisor(), None
    try:
        with pytest.raises(OSError, match="already exists"):
            managed = backend.start(
                launch, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        assert (
            win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)[
                "JobMemoryLimit"
            ]
            == 0
        )
    finally:
        if managed is not None:
            backend.terminate_tree(managed, grace_seconds=0, reason="fixture_cleanup")
            backend.release(managed)
            for stream in (managed.process.stdout, managed.process.stderr):
                stream.close()
        job.Close()
