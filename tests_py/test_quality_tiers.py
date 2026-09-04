from pathlib import Path

import pytest

from local_control_center.quality.plans import build_plan, iteration_scripts

ROOT = Path(__file__).resolve().parents[1]


def test_fast_never_selects_full_suites_and_checks_modified_python():
    steps = build_plan(
        ROOT,
        "fast",
        changed_files=["local_control_center/workers/api.py"],
        python_tests=["tests_py/test_worker_leadership.py"],
    )
    args = [item for step in steps for item in step.argv]
    assert "tests_py" not in args
    assert "scripts/run-web-tests.mjs" not in args
    assert "local_control_center/workers/api.py" in args
    assert "tests_py/test_worker_leadership.py" in args
    assert {step.name for step in steps} >= {"diff-check", "secrets-working", "secrets-staged"}


def test_story_selects_slice_and_focused_browser_only():
    steps = build_plan(
        ROOT,
        "story",
        changed_files=["local-control-center/web/src/features/settings/OperationsPanel.tsx"],
        python_tests=["tests_py/test_worker_leadership.py"],
        web_tests=["tests_web/operational-hardening.spec.js"],
    )
    assert "tests_py/test_worker_leadership.py" in [arg for step in steps for arg in step.argv]
    browser = next(step for step in steps if step.name == "web-focused")
    assert browser.workload_class == "browser_test"
    assert "tests_web/operational-hardening.spec.js" in browser.argv
    assert "tests_py" not in [arg for step in steps for arg in step.argv]


def test_pr_keeps_every_gate_and_runs_sequential_commands():
    steps = build_plan(ROOT, "pr")
    assert {step.name for step in steps} >= {
        "productive-truth",
        "python",
        "web",
        "build",
        "typecheck",
        "ruff",
        "format",
        "biome",
        "architecture",
        "secrets",
        "semgrep",
        "diff-check",
    }
    assert next(step for step in steps if step.name == "build").workload_class == "build_heavy"
    assert next(step for step in steps if step.name == "web").workload_class == "browser_test"
    assert next(step for step in steps if step.name == "semgrep").workload_class == "qa_light"
    assert all(not {"-n", "auto", "--parallel", "--workers=4"}.intersection(step.argv) for step in steps)


def test_iteration_only_replaces_full_gate_aliases_and_preserves_custom_checks():
    assert iteration_scripts(["quality", "lint:custom"], tier="story") == ["quality:story", "lint:custom"]
    assert iteration_scripts(["quality:release"], tier="fast") == ["quality:fast"]


def test_focused_tests_must_stay_inside_test_directories():
    with pytest.raises(ValueError):
        build_plan(ROOT, "fast", python_tests=["../private.py"])


def test_quality_output_cannot_abort_gate_on_windows_console_encoding(tmp_path, monkeypatch):
    import io
    import sys

    from local_control_center.quality import __main__ as runner
    from local_control_center.quality.plans import QualityStep

    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(runner.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        runner,
        "run_supervised_capture",
        lambda *args, **kwargs: {"returnCode": 0, "stdout": "○ secret scan complete", "stderr": ""},
    )
    result = runner._run(
        QualityStep("scan", ("gitleaks",)), root=tmp_path, db_path=tmp_path / "unused.sqlite"
    )
    assert result["returnCode"] == 0
    assert result["stdout"].startswith("○")
