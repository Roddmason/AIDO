"""Regresión nativa del redirector Python de Windows en el cierre de servidores de QA.

@author Rodrigo Mason
"""

import os
import subprocess
import sys

import psutil
import pytest

from local_control_center.quality.owned_tree import CREATE_TIME_TOLERANCE_SECONDS, owned_root, stop_owned_tree


def test_owned_tree_rejects_wrong_parent_and_recycled_identity():
    with pytest.raises(RuntimeError, match="ownership"):
        owned_root(os.getpid(), -1)
    with pytest.raises(RuntimeError, match="ownership"):
        owned_root(os.getpid(), os.getppid(), 1.0)
    assert psutil.Process(os.getpid()).is_running()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Python redirector regression")
def test_qa_runner_closes_redirector_and_server_descendants():
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import os,time; print(os.getpid(),flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE,
        text=True,
    )
    created_at = owned_root(process.pid, os.getpid()).create_time()
    server = psutil.Process(int(process.stdout.readline().strip()))
    try:
        assert server.pid != process.pid, "This regression requires the project venv redirector."
        # Killing only ChildProcess.pid cannot prove that the actual server was stopped.
        assert server in psutil.Process(process.pid).children(recursive=True)
        result = stop_owned_tree(process.pid, os.getpid(), created_at)
        assert result["descendantCount"] >= 1
        assert result["remainingCount"] == 0
        assert not server.is_running()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            stop_owned_tree(process.pid, os.getpid(), created_at)
        if server.is_running():
            server.kill()
            server.wait(timeout=5)
        process.stdout.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX zombie semantics")
def test_owned_tree_treats_unreaped_zombie_root_as_stopped():
    # run-web-tests.mjs invoca el stop con spawnSync: Node no cosecha al servidor mientras espera,
    # así que el root terminado queda zombie y sigue "existiendo" para psutil.
    parent = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(child.pid,flush=True); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        root_pid = int(parent.stdout.readline().strip())
        created_at = owned_root(root_pid, parent.pid).create_time()
        result = stop_owned_tree(root_pid, parent.pid, created_at)
        assert result == {"status": "stopped", "descendantCount": 0, "remainingCount": 0}
        assert psutil.Process(root_pid).status() == psutil.STATUS_ZOMBIE
    finally:
        parent.kill()
        parent.wait(timeout=5)
        parent.stdout.close()


def test_owned_root_tolerates_boot_time_rounding_but_not_a_different_process():
    actual = psutil.Process(os.getpid()).create_time()
    drift = 1.0 if sys.platform != "win32" else 0.0
    assert owned_root(os.getpid(), os.getppid(), actual + drift).pid == os.getpid()
    with pytest.raises(RuntimeError, match="ownership"):
        owned_root(os.getpid(), os.getppid(), actual + CREATE_TIME_TOLERANCE_SECONDS + 1.0)
