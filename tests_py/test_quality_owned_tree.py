"""Regresión nativa del redirector Python de Windows en el cierre de servidores de QA.

@author Rodrigo Mason
"""

import os
import subprocess
import sys

import psutil
import pytest

from local_control_center.quality.owned_tree import owned_root, stop_owned_tree


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
