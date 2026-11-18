"""Regresión: un hijo suspendido no sobrevive a la muerte de su creador antes de entrar al Job.

@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from contextlib import suppress

import psutil
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")

# El creador muere (TerminateProcess) después de CREATE_SUSPENDED y antes de AssignProcessToJobObject,
# como un fixture matado por timeout de Playwright. Sin protección, el hijo queda suspendido para siempre.
_CREATOR = r"""
import subprocess, sys, time
from local_control_center.process_supervision.models import ProcessLaunchSpec
from local_control_center.process_supervision.windows_job import WindowsJobObjectProcessSupervisor

def stalled_popen(*args, **kwargs):
    child = subprocess.Popen(*args, **kwargs)
    print(child.pid, flush=True)
    time.sleep(60)

spec = ProcessLaunchSpec(
    managed_process_id=sys.argv[1], execution_id="orphan-test", argv=[sys.executable, "-c", "pass"],
    cwd=".", workload_class="light", command_fingerprint="x", memory_limit_bytes=256 * 1024 * 1024,
    process_limit=4, cpu_limit_percent=50, below_normal_priority=False,
)
WindowsJobObjectProcessSupervisor().start(spec, popen_factory=stalled_popen)
"""


def test_suspended_child_dies_when_creator_dies_before_job_assignment() -> None:
    creator = subprocess.Popen(
        [sys.executable, "-c", _CREATOR, f"orphan-{uuid.uuid4().hex}"],
        stdout=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    try:
        child_pid = int(creator.stdout.readline())
        child = psutil.Process(child_pid)
        assert child.status() == psutil.STATUS_STOPPED
        creator.kill()
        creator.wait(timeout=10)
        deadline = time.monotonic() + 10
        while child.is_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not child.is_running(), "el hijo suspendido quedó huérfano tras morir su creador"
    finally:
        creator.kill()
        creator.wait(timeout=10)
        creator.stdout.close()
        if child_pid is not None:
            with suppress(psutil.Error):
                psutil.Process(child_pid).kill()
