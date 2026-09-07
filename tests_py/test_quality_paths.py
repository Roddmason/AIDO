"""Fail-closed scratch isolation and immutable runner receipts.

@author Rodrigo Mason
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from local_control_center.quality.__main__ import _write_report


def test_runner_refuses_to_replace_existing_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    _write_report(path, {"status": "failed"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        _write_report(path, {"status": "passed"})
    assert path.read_bytes() == before


def test_not_a_repository_requires_exact_git_failure():
    from local_control_center.quality.paths import require_non_repository

    require_non_repository(128, "fatal: not a git repository (or any of the parent directories): .git\n")
    for code, error in [(0, ""), (128, "fatal: permission denied"), (1, "not a git repository")]:
        with pytest.raises(ValueError):
            require_non_repository(code, error)


def test_scratch_rejects_checkout_worktree_evidence_and_reparse(tmp_path, monkeypatch):
    from local_control_center.quality.paths import validate_scratch_parent

    protected = tmp_path / "protected"
    protected.mkdir()
    with pytest.raises(ValueError):
        validate_scratch_parent(protected / "child", [protected])
    with pytest.raises(ValueError):
        validate_scratch_parent(tmp_path, [protected])
    link = tmp_path / "link"
    link.mkdir()
    monkeypatch.setattr(Path, "is_junction", lambda self: self == link)
    with pytest.raises(ValueError, match=r"[Rr]eparse|[Jj]unction"):
        validate_scratch_parent(link / "child", [protected])


def test_invocations_and_steps_use_fresh_separate_paths(tmp_path):
    from local_control_center.quality.paths import QualityPaths

    parent = tmp_path / "scratch con espacios y ñ"
    evidence = tmp_path / "retained"
    evidence.mkdir()
    first = QualityPaths.create(parent, evidence, [evidence])
    second = QualityPaths.create(parent, evidence, [evidence])
    assert first.scratch != second.scratch
    args, env = first.prepare_pytest(("python", "-m", "pytest", "tests_py"), {})
    again, _ = first.prepare_pytest(("python", "-m", "pytest", "tests_py"), {})
    base = Path(args[args.index("--basetemp") + 1])
    assert base != Path(again[again.index("--basetemp") + 1])
    assert not base.exists()
    assert not Path(env["AIDO_QUALITY_FIXTURES"]).is_relative_to(base)
    assert not Path(env["AIDO_ACCEPTANCE_EVIDENCE"]).is_relative_to(first.scratch)
    assert Path(env["PLAYWRIGHT_ARTIFACT_ROOT"]).is_relative_to(first.evidence)
    assert json.loads((first.evidence / "paths.json").read_text())["invocationId"] == first.invocation_id
    with pytest.raises(ValueError):
        first.prepare_pytest(("python", "-m", "pytest"), {"PYTEST_ADDOPTS": "--basetemp=old"})


def test_public_runner_path_preparation_propagates_only_child_settings(tmp_path, monkeypatch):
    from local_control_center.quality import __main__ as runner
    from local_control_center.quality.paths import inherited_paths

    root = tmp_path / "checkout"
    root.mkdir()
    before = dict(os.environ)
    calls = []

    def run(step, **kwargs):
        calls.append((step.name, kwargs))
        if step.name == "worktree-context":
            return {"returnCode": 0, "stdoutCaptureTruncated": False, "stdout": f"worktree {root}\n"}
        return {
            "returnCode": 128,
            "stderr": "fatal: not a git repository (or any of the parent directories): .git",
        }

    monkeypatch.setattr(runner, "_run", run)
    manifest, environment = runner._prepare_paths(
        root, root / "quality.sqlite", root / "retained", tmp_path / "scratch"
    )
    assert environment["AIDO_TEST_QUALITY_DB"] == str(root / "quality.sqlite")
    assert inherited_paths(environment).invocation_id == manifest["invocationId"]
    assert calls[1][1]["environment"]["LC_ALL"] == "C"
    assert dict(os.environ) == before


def test_browser_receipts_never_use_checkout_or_fixed_screenshot_destinations():
    root = Path(__file__).resolve().parents[1]
    for filename in (
        "ide-resizable-shell.spec.js",
        "command-palette.spec.js",
        "operational-hardening.spec.js",
    ):
        content = (root / "tests_web" / filename).read_text(encoding="utf-8")
        screenshots = [line for line in content.splitlines() if "page.screenshot(" in line]
        assert screenshots, filename
        assert all(".outputPath(" in line for line in screenshots), filename
    runner = (root / "scripts/run-web-tests.mjs").read_text(encoding="utf-8")
    assert "path.join(os.tmpdir(), `playwright-control-center-${process.pid}.sqlite`)" in runner


@pytest.mark.skipif(os.name != "nt", reason="Native NTFS junction validation; other OS NOT_RUN")
def test_native_junction_cannot_alias_protected_evidence(tmp_path):
    from local_control_center.quality.paths import validate_scratch_parent

    protected = tmp_path / "protected-evidence"
    protected.mkdir()
    receipt = protected / "receipt.json"
    receipt.write_bytes(b'{"status":"FAIL"}')
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(protected)], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match="Reparse"):
            validate_scratch_parent(junction / "pytest", [protected])
        assert receipt.read_bytes() == b'{"status":"FAIL"}'
    finally:
        # Remove only the junction this test just created, not its target or contents.
        os.rmdir(junction)
