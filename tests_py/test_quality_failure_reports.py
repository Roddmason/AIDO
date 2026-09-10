"""Failure-phase receipts survive a later hard exit; no database or provider needed.

@author Rodrigo Mason
"""

import json
import os
import sys
from pathlib import Path

import pytest

from local_control_center.process_supervision.service import run_supervised_capture


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_failed_phase_is_retained_before_later_abrupt_exit(tmp_path, phase):
    fixture = tmp_path / "isolated suite"
    fixture.mkdir()
    (tmp_path / "child-fixtures").mkdir()
    retained = tmp_path / "retained"
    when = {
        "setup": "    raise RuntimeError('phase sentinel; password=synthetic-report-secret')\n    yield\n",
        "call": "    yield\n",
        "teardown": "    yield\n    raise RuntimeError('phase sentinel; password=synthetic-report-secret')\n",
    }[phase]
    test = fixture / "test_phases.py"
    test.write_text(
        "import os, pytest\n@pytest.fixture\ndef broken():\n"
        + when
        + "def test_a(broken):\n"
        + (
            "    raise RuntimeError('phase sentinel; password=synthetic-report-secret')\n"
            if phase == "call"
            else "    pass\n"
        )
        + "def test_b():\n    os._exit(23)\n",
        encoding="utf8",
    )
    result = run_supervised_capture(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests_py.conftest",
            str(test),
            "--confcutdir",
            str(fixture),
        ],
        cwd=fixture,
        db_path=tmp_path / "nested.sqlite",
        timeout_seconds=45,
        workload_class="qa_light",
        environment={
            **os.environ,
            "PYTHONPATH": str(Path.cwd()),
            "AIDO_ACCEPTANCE_EVIDENCE": str(retained),
            "AIDO_QUALITY_FIXTURES": str(tmp_path / "child-fixtures"),
            "LOCAL_CONTROL_CENTER_DB": str(tmp_path / "nested.sqlite"),
        },
    )
    assert result["returnCode"] == 23, result["stdout"] + result["stderr"]
    receipts = list(retained.glob("pytest-failure-*.json"))
    assert len(receipts) == 1, result["stdout"] + result["stderr"]
    report = json.loads(receipts[0].read_text(encoding="utf8"))
    assert report["phase"] == phase and report["nodeId"].endswith("::test_a")
    assert "phase sentinel" in report["traceback"]
    assert "synthetic-report-secret" not in receipts[0].read_text(encoding="utf8")
    assert "[redacted]" in report["traceback"]
    assert "locals" not in report


def test_report_publication_error_preserves_original_failure(tmp_path):
    fixture = tmp_path / "isolated"
    fixture.mkdir()
    (tmp_path / "child-fixtures").mkdir()
    test = fixture / "test_error.py"
    test.write_text("def test_error():\n    raise RuntimeError('original sentinel')\n", encoding="utf8")
    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("preserved", encoding="utf8")
    result = run_supervised_capture(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests_py.conftest",
            str(test),
            "--confcutdir",
            str(fixture),
        ],
        cwd=fixture,
        db_path=tmp_path / "nested.sqlite",
        timeout_seconds=45,
        workload_class="qa_light",
        environment={
            **os.environ,
            "PYTHONPATH": str(Path.cwd()),
            "AIDO_ACCEPTANCE_EVIDENCE": str(unavailable),
            "AIDO_QUALITY_FIXTURES": str(tmp_path / "child-fixtures"),
            "LOCAL_CONTROL_CENTER_DB": str(tmp_path / "nested.sqlite"),
        },
    )
    assert result["returnCode"] == 1 and "original sentinel" in result["stdout"]
    assert "failure report unavailable" in result["stderr"]
    assert unavailable.read_text(encoding="utf8") == "preserved"
