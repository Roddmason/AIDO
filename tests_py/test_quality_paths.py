"""Fail-closed scratch isolation and immutable runner receipts.

@author Rodrigo Mason
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.quality.__main__ import _write_report


@pytest.mark.parametrize("source", ["omitted", "conflicting", "inherited"])
def test_explicit_runner_database_reaches_child_and_grandchild(tmp_path, monkeypatch, source):
    from local_control_center.quality.__main__ import _run
    from local_control_center.quality.plans import QualityStep

    home = tmp_path / "señuelo home"
    decoy = home / ".claude" / "local-control-center" / "platform.sqlite"
    decoy.parent.mkdir(parents=True)
    with closing(sqlite3.connect(decoy)) as connection:
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('unchanged')")
        connection.commit()
    before = hashlib.sha256(decoy.read_bytes()).hexdigest()
    database = tmp_path / "explicit-quality.sqlite"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(decoy))
    environment = dict(os.environ)
    if source == "omitted":
        environment.pop("LOCAL_CONTROL_CENTER_DB")
    parent_environment = dict(os.environ)
    program = tmp_path / "database_scope.py"
    program.write_text(
        "import json, os, sqlite3, subprocess, sys\n"
        "from contextlib import closing\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from local_control_center.shared.settings import default_db_path\n"
        "database = default_db_path().resolve()\n"
        "with closing(sqlite3.connect(database)) as connection:\n"
        "    connection.execute('CREATE TABLE IF NOT EXISTS scope_probe (role TEXT)')\n"
        "    connection.execute('INSERT INTO scope_probe VALUES (?)', (sys.argv[1],))\n"
        "    connection.commit()\n"
        "print(json.dumps({'role': sys.argv[1], 'database': str(database), 'pid': os.getpid(),\n"
        "                  'qualityDatabase': os.environ.get('AIDO_QUALITY_DB_PATH')}), flush=True)\n"
        "if sys.argv[1] == 'child':\n"
        "    raise SystemExit(subprocess.run([sys.executable, __file__, 'grandchild'], timeout=20).returncode)\n",
        encoding="utf-8",
    )
    result = _run(
        QualityStep("database-scope", (sys.executable, str(program), "child"), "qa_light", 30),
        root=tmp_path,
        db_path=database,
        environment=None if source == "inherited" else environment,
        display_output=False,
    )
    assert result["returnCode"] == 0, result
    records = [json.loads(line) for line in result["stdout"].splitlines()]
    assert [row["role"] for row in records] == ["child", "grandchild"], records
    assert all(Path(row["database"]) == database.resolve() for row in records), records
    assert all(Path(row["qualityDatabase"]) == database.resolve() for row in records), records
    assert hashlib.sha256(decoy.read_bytes()).hexdigest() == before
    assert dict(os.environ) == parent_environment
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT role FROM scope_probe ORDER BY rowid").fetchall() == [
            ("child",),
            ("grandchild",),
        ]


@pytest.mark.parametrize(
    "variables,expected",
    [
        ({"NO_COLOR": "1", "FORCE_COLOR": "1"}, "0"),
        ({"NO_COLOR": "1"}, "0"),
        ({"FORCE_COLOR": "2"}, "2"),
        ({}, "0"),
    ],
)
def test_quality_uses_one_child_only_color_contract(tmp_path, variables, expected):
    from local_control_center.quality.paths import QualityPaths

    source = {k: v for k, v in os.environ.items() if k not in {"NO_COLOR", "FORCE_COLOR"}}
    source.update(variables)
    before = dict(source)
    environment = QualityPaths("test", tmp_path / "scratch", tmp_path / "evidence").environment(source)
    assert source == before
    assert "NO_COLOR" not in environment
    assert environment["FORCE_COLOR"] == expected
    for child_force in (expected, "1"):  # Playwright 1.60's worker explicitly forces ANSI.
        result = subprocess.run(
            ["node", "-e", "require('node:tty').WriteStream.prototype.getColorDepth.call({}, process.env)"],
            env={**environment, "FORCE_COLOR": child_force},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0 and not result.stderr, result.stderr


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
    assert Path(env["AIDO_DIAGNOSTICS_DIR"]).is_relative_to(first.evidence)
    assert json.loads((first.evidence / "paths.json").read_text())["invocationId"] == first.invocation_id
    with pytest.raises(ValueError):
        first.prepare_pytest(("python", "-m", "pytest"), {"PYTEST_ADDOPTS": "--basetemp=old"})


def test_public_runner_path_preparation_propagates_only_child_settings(tmp_path, monkeypatch):
    from local_control_center.quality import __main__ as runner
    from local_control_center.quality.paths import inherited_paths
    from local_control_center.shared.diagnostics import ensure_diagnostics

    root = tmp_path / "checkout"
    root.mkdir()
    before = dict(os.environ)
    calls = []

    def run(step, **kwargs):
        from local_control_center.shared.diagnostics import ensure_diagnostics

        assert ensure_diagnostics().root == root / "retained" / "runner-diagnostics"
        calls.append((step.name, kwargs))
        if step.name == "worktree-context":
            return {"returnCode": 0, "stdoutCaptureTruncated": False, "stdout": f"worktree {root}\n"}
        return {
            "returnCode": 128,
            "stderr": "fatal: not a git repository (or any of the parent directories): .git",
        }

    monkeypatch.setattr(runner, "_run", run)
    try:
        manifest, environment = runner._prepare_paths(
            root, root / "quality.sqlite", root / "retained", tmp_path / "scratch"
        )
    finally:
        ensure_diagnostics().close()
    assert environment["AIDO_TEST_QUALITY_DB"] == str(root / "quality.sqlite")
    assert inherited_paths(environment).invocation_id == manifest["invocationId"]
    assert manifest["runnerDiagnostics"] == str(root / "retained" / "runner-diagnostics")
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
    # La invariante es donde vive la base, no el literal: fuera del checkout y con identidad
    # propia por corrida. Antes se anclaba a `os.tmpdir()` directo; ahora cuelga del scratch de
    # calidad, que es el mismo destino aislado y ademas se limpia con la sesion.
    assert "path.join(qualityScratch, `playwright-control-center-${process.pid}.sqlite`)" in runner
    assert "`aido-web-tests-${process.pid}`" in runner
    assert "path.join(__dirname" not in runner


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
