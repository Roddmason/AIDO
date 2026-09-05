from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def launcher():
    path = Path("local-control-center/scripts/start_control_center.py").resolve()
    spec = importlib.util.spec_from_file_location("aido_test_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_defaults_to_one_worker_and_rejects_parallel_count(launcher, monkeypatch):
    monkeypatch.setattr("sys.argv", ["launcher"])
    assert launcher.parse_args().worker_count == 1
    monkeypatch.setattr("sys.argv", ["launcher", "--worker-count", "2"])
    with pytest.raises(SystemExit):
        launcher.parse_args()


def test_missing_dashboard_build_uses_supervisor_and_selected_database(launcher, monkeypatch, tmp_path):
    from local_control_center.process_supervision import service

    calls = []

    def build(argv, **kwargs):
        calls.append((argv, kwargs))
        (tmp_path / "index.html").touch()
        return {"returnCode": 0, "timedOut": False, "cancelled": False}

    monkeypatch.setattr(service, "run_supervised_capture", build)
    database = tmp_path / "platform.sqlite"
    launcher.ensure_dashboard(tmp_path, no_build=False, db_path=database)
    assert len(calls) == 1
    assert calls[0][1]["db_path"] == database
    assert calls[0][1]["workload_class"] == "build_heavy"
    launcher.ensure_dashboard(tmp_path, no_build=False, db_path=database)
    assert len(calls) == 1


def test_powershell_delegates_to_canonical_launcher_without_another_supervisor():
    script = Path("local-control-center/scripts/start-control-center.ps1").read_text(encoding="utf-8")
    assert '"start_control_center.py"' in script
    assert "Start-Process" not in script
    assert "Stop-Process" not in script
    assert "exit $LASTEXITCODE" in script


def test_worker_cli_does_not_load_http_routers_or_numerical_backend():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import local_control_center.cli; "
            "assert 'local_control_center.api' not in sys.modules; "
            "assert 'faiss' not in sys.modules; assert 'numpy' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_launcher_shutdown_does_not_leave_its_python_descendant(launcher):
    import subprocess
    import sys

    import psutil

    child = None
    owned_tree = []
    # No provider or operator process: both processes below belong exclusively to this test.
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "print(p.pid,flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        child = psutil.Process(int(process.stdout.readline().strip()))
        owned_tree = psutil.Process(process.pid).children(recursive=True)
        launcher._stop_child(process)
        _, alive = psutil.wait_procs([child], timeout=2)
        assert not alive, "The owned launcher child survived shutdown."
    finally:
        for owned in reversed(owned_tree):
            try:
                if owned.is_running():
                    owned.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(owned_tree, timeout=3)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
