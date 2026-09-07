"""Native Git/security-hook integration, without bypasses or original repository edits."""

import os
import shutil
import threading
import time
from contextlib import suppress
from pathlib import Path

import psutil
import pytest

from local_control_center.process_supervision.context import execution_scope
from local_control_center.process_supervision.service import run_supervised_capture
from local_control_center.quality.__main__ import _inherited_context
from local_control_center.security_policy.git_command_runner import run_git
from tests_py.operational_acceptance_support import evidence, quality_envelope


@pytest.fixture(autouse=True)
def admitted_quality_scope():
    """Use the runner's verified native ancestor lease, not a second admission in a fixture DB."""
    path = os.environ.get("AIDO_TEST_QUALITY_DB")
    if path:
        context = _inherited_context(Path(path))
        assert context.resource_lease_id, "Native integration must retain the actual quality reservation"
        with execution_scope(context):
            yield
    else:
        yield


@pytest.mark.parametrize(("tool_exit", "expected"), [(2, "analysis_error"), (124, "timeout_or_cancelled")])
def test_hook_tool_failures_are_not_secret_findings(tmp_path, tool_exit, expected):
    repository = tmp_path / "repository"
    repository.mkdir()
    assert run_git(["init"], cwd=repository).returncode == 0
    executable = tmp_path / "bin"
    executable.mkdir()
    fake = executable / "gitleaks"
    fake.write_text(f"#!/bin/sh\nexit {tool_exit}\n", encoding="utf-8")
    fake.chmod(0o755)
    hook = Path(__file__).resolve().parents[1] / ".githooks/pre-commit"
    result = run_supervised_capture(
        [shutil.which("sh"), str(hook)],
        cwd=repository,
        workload_class="qa_light",
        timeout_seconds=15,
        environment={**os.environ, "PATH": str(executable) + os.pathsep + os.environ["PATH"]},
    )
    evidence("git-hook-tool-error", {"syntheticExit": tool_exit, **result})
    assert result["returnCode"] != 0
    assert expected in result["stderr"]
    assert "secret_found" not in result["stderr"]


@pytest.mark.parametrize("secret", [False, True], ids=["clean", "synthetic-secret"])
def test_clean_commit_under_real_security_hook(tmp_path, monkeypatch, secret):
    repository = tmp_path / "repository"
    repository.mkdir()
    assert run_git(["init"], cwd=repository).returncode == 0
    source = Path(__file__).resolve().parents[1]
    hooks = tmp_path / "hooks"
    shutil.copytree(source / ".githooks", hooks)
    shutil.copyfile(source / ".gitleaks.toml", repository / ".gitleaks.toml")
    assert run_git(["config", "core.hooksPath", str(hooks)], cwd=repository).returncode == 0
    (repository / "README.md").write_text("# Clean synthetic fixture\n", encoding="utf-8")
    if secret:
        # Non-credential canary constructed at runtime; never an allowlist or a real key.
        (repository / "README.md").write_text(
            "github_token=" + "ghp_" + "aB3dE6fG9hJ2kL5mN8pQ1rS4tU7vW0xY3zA6" + "\n", encoding="utf-8"
        )
    assert run_git(["add", "README.md"], cwd=repository).returncode == 0
    samples = []
    stopped = threading.Event()

    def sample():
        parent = psutil.Process()
        while not stopped.wait(0.02):
            members = []
            for process in [parent, *parent.children(recursive=True)]:
                with suppress(psutil.NoSuchProcess):
                    members.append(
                        {"pid": process.pid, "created": process.create_time(), "name": process.name()}
                    )
            samples.append({"monotonic": time.monotonic(), "members": members})

    sampler = threading.Thread(target=sample)
    sampler.start()
    envelope = quality_envelope() if os.name == "nt" else {"status": "NOT_RUN"}
    try:
        result = run_git(
            [
                "-c",
                "user.name=AIDO Tests",
                "-c",
                "user.email=aido@example.test",
                "commit",
                "-m",
                "clean fixture",
            ],
            cwd=repository,
        )
    finally:
        stopped.set()
        sampler.join(timeout=2)
    evidence(
        "git-hook-clean",
        {
            "exitCode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "envelope": envelope,
            "samples": samples,
        },
    )
    if secret:
        assert result.returncode != 0
        assert "secret_found" in result.stderr
        assert "analysis_clean" not in result.stderr
        assert run_git(["rev-parse", "--verify", "HEAD"], cwd=repository).returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert "analysis_clean" in result.stderr
