from pathlib import Path

import pytest

from local_control_center.quality.plans import build_plan, iteration_scripts

ROOT = Path(__file__).resolve().parents[1]


def test_pr_rejects_biome_warnings_without_hiding_them():
    step = next(step for step in build_plan(ROOT, "pr") if step.name == "biome")
    assert "--error-on-warnings" in step.argv


def test_native_http_pipeline_has_capacity_without_escalating_small_regressions():
    from local_control_center.host_resources.profiles import workload_profile

    pipeline = "tests_py/test_watchdog_http_pipeline.py"
    light = "tests_py/test_transaction_begin_failure.py"
    plan = build_plan(ROOT, "fast", python_tests=[pipeline, light])
    heavy_step = next(step for step in plan if pipeline in step.argv)
    light_step = next(step for step in plan if light in step.argv)
    # API + native worker + dispatcher + pytest reached 8,724,848,640 bytes and
    # raised MemoryError under the old 8 GiB aggregate Job (receipt 1803dac5).
    profile = workload_profile(heavy_step.workload_class)
    assert profile.memory_limit_bytes > 8724848640
    assert profile.heavy and profile.process_limit > 0 and profile.cpu_limit_percent < 100
    assert light_step.workload_class == "qa_light"
    assert light not in heavy_step.argv


def test_pr_keeps_capture_cases_in_their_joint_profile_not_inside_a_smaller_ancestor():
    from local_control_center.host_resources.profiles import workload_profile

    plan = build_plan(ROOT, "pr")
    python = next(step for step in plan if step.name == "python")
    capture = next(step for step in plan if step.name == "python-capture-session")
    assert "--ignore=tests_py/test_launcher_capture_http.py" in python.argv
    assert "tests_py/test_launcher_capture_http.py" in capture.argv
    assert capture.workload_class == "capture_session"
    assert workload_profile(capture.workload_class).memory_limit_bytes == 18 * 1024**3


def test_release_wrappers_can_contain_the_complete_pr_capture_budget(tmp_path):
    from local_control_center.host_resources.profiles import workload_profile
    from local_control_center.quality.verify import verification_plan

    outer = next(step for step in build_plan(ROOT, "release") if step.name == "operational-verification")
    inner = next(step for step in verification_plan(tmp_path) if step.name == "quality-pr")
    for step in (outer, inner):
        profile = workload_profile(step.workload_class)
        assert profile.memory_limit_bytes >= 18 * 1024**3
        assert profile.cpu_limit_percent == 65


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


def test_release_respects_the_host_script_execution_policy():
    import json

    steps = build_plan(ROOT, "release")
    verifier = next(step for step in steps if step.name == "operational-verification")
    assert "scripts/verify-operational-hardening.ps1" in verifier.argv
    assert "-ExecutionPolicy" not in verifier.argv
    assert "Bypass" not in verifier.argv
    scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    for tier in ("fast", "story", "pr", "release"):
        assert "-ExecutionPolicy" not in scripts[f"quality:{tier}"]


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


def test_full_python_suite_has_capacity_for_native_git_security_hook_tree(tmp_path):
    from local_control_center.quality.plans import build_plan

    python = next(step for step in build_plan(tmp_path, "pr") if step.name == "python")
    assert python.workload_class == "build_heavy"


def test_focused_git_hook_suite_uses_the_full_pr_envelope():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for test in ("tests_py/test_aido_real_runtime_slice.py", "tests_py/test_git_hook_contract.py"):
        step = next(s for s in build_plan(root, "fast", python_tests=[test]) if s.name == "python-focused")
        assert step.workload_class == "build_heavy"
    step = next(
        s
        for s in build_plan(root, "fast", python_tests=["tests_py/test_quality_paths.py"])
        if s.name == "python-focused"
    )
    assert step.workload_class == "qa_light"


def test_semgrep_is_serial_offline_and_findings_fail_the_gate(tmp_path):
    import sys

    scanner = next(step for step in build_plan(tmp_path, "pr") if step.name == "semgrep")
    assert scanner.argv[scanner.argv.index("--jobs") + 1] == "1"
    assert "--error" in scanner.argv
    assert scanner.argv[scanner.argv.index("--metrics") + 1] == "off"
    assert "--disable-version-check" in scanner.argv
    assert ("--legacy" in scanner.argv) is (sys.platform == "win32")


def test_quality_waits_for_admission_but_never_retries_an_executed_gate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from local_control_center.quality import __main__ as runner
    from local_control_center.quality.plans import QualityStep

    elapsed = [0]
    attempts = []
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=lambda: elapsed[0], sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)
        ),
        raising=False,
    )
    monkeypatch.setattr(runner.shutil, "which", lambda name: name)

    def capture(*args, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise runner.ResourceWaitError("resource_wait: host_cpu_saturated")
        return {"returnCode": 1, "stdout": "gate failed", "stderr": ""}

    monkeypatch.setattr(runner, "run_supervised_capture", capture)
    result = runner._run(QualityStep("check", ("check",)), root=tmp_path, db_path=tmp_path / "unused.sqlite")
    assert result["returnCode"] == 1
    assert len(attempts) == 2
    assert elapsed[0] == 5
    assert attempts[0] == attempts[1]


def test_quality_admission_wait_is_bounded_and_still_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from local_control_center.quality import __main__ as runner
    from local_control_center.quality.plans import QualityStep

    elapsed = [0]
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=lambda: elapsed[0], sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)
        ),
        raising=False,
    )
    monkeypatch.setattr(runner.shutil, "which", lambda name: name)

    def refuse(*args, **kwargs):
        raise runner.ResourceWaitError("resource_wait: host_cpu_saturated")

    monkeypatch.setattr(runner, "run_supervised_capture", refuse)
    with pytest.raises(runner.ResourceWaitError):
        runner._run(QualityStep("check", ("check",)), root=tmp_path, db_path=tmp_path / "unused.sqlite")
    assert elapsed[0] == 120


def test_quality_failed_finalization_keeps_native_exit_without_passing_gate(tmp_path, monkeypatch):
    import json
    import sqlite3

    from local_control_center.quality import __main__ as runner
    from local_control_center.quality.plans import QualityStep

    error = sqlite3.OperationalError("synthetic finalization error")
    error.supervision_outcome = {"returnCode": 0, "durableFinalization": "pending", "cancelled": False}

    def fail(*args, **kwargs):
        raise error

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "_monitor", lambda *args: None)
    monkeypatch.setattr(runner, "_prepare_paths", lambda *args: ({}, {}))
    monkeypatch.setattr(runner, "build_plan", lambda *args, **kwargs: [QualityStep("fixture", ("unused",))])
    monkeypatch.setattr(runner, "_run", fail)
    assert runner.main(["--tier", "pr", "--db-path", str(tmp_path / "isolated.sqlite")]) == 1
    (report_path,) = (tmp_path / ".tmp/operational-hardening-p0").glob("quality-pr-*.json")
    report = json.loads(report_path.read_text(encoding="utf8"))
    assert report["status"] == "failed" and report["steps"] == []
    assert report["failedProcessOutcome"]["returnCode"] == 0
    assert report["failedProcessOutcome"]["durableFinalization"] == "pending"
