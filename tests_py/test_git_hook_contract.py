"""Native Git/security-hook integration, without bypasses or original repository edits."""

import hashlib
import json
import os
import re
import shutil
import subprocess
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


def _gitleaks_policy_scan(tmp_path, relative_path, content, *, absolute_target=False):
    """Exercise the installed scanner against one controlled, non-credential canary."""
    executable = shutil.which("gitleaks")
    assert executable, "Gitleaks is required to validate the secrets policy"
    source = Path(__file__).resolve().parents[1]
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [
            executable,
            "dir",
            str(tmp_path) if absolute_target else ".",
            "--config",
            str(source / ".gitleaks.toml"),
            "--redact=100",
            "--ignore-gitleaks-allow",
            "--no-banner",
            "--no-color",
            "--exit-code=10",
            "--report-format=json",
            "--report-path=-",
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode in {0, 10}, "Gitleaks failed before completing the policy scan"
    findings = json.loads(result.stdout or b"[]")
    return result.returncode, {finding["RuleID"] for finding in findings}


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/security-canary.md",
        ".env.example",
        "tests_py/test_redaction.py",
        "tests_py/test_n8n_integration.py",
        ".claude/worktrees/privacy-canary/docs/security-canary.md",
        ".tmp/evidence-artifacts/artifact-privacy-canary.json",
    ],
)
def test_gitleaks_policy_does_not_exclude_source_or_local_evidence(tmp_path, relative_path):
    # Constructed at runtime, so this regression test is not a stored credential look-alike.
    canary = "gh" + "p_" + "aB3dE6fG9hJ2kL5mN8pQ1rS4tU7vW0xY3zA6"
    content = f"api_key = {canary}\n"
    if relative_path.startswith(".tmp/evidence-artifacts/"):
        artifact_canary = "Q7nB2vL9rT4mX8kD1pF6sJ3wH5" + "yC0aE9uR2zN7gV4bM8"
        content = json.dumps({"apiKey": artifact_canary})
    code, rules = _gitleaks_policy_scan(tmp_path, relative_path, content)
    assert code == 10
    assert rules
    if relative_path.startswith(".tmp/evidence-artifacts/"):
        assert "generic-api-key" in rules


def _redaction_pem_fixture():
    source = Path(__file__).resolve().parents[1] / "tests_py/test_redaction.py"
    header = "-----BEGIN RSA " + "PRIVATE KEY-----"
    footer = "-----END RSA " + "PRIVATE KEY-----"
    match = re.search(
        re.escape(header) + r"[\s\S]*?" + re.escape(footer),
        source.read_text(encoding="utf-8"),
    )
    assert match is not None
    return match.group()


@pytest.mark.parametrize("absolute_target", [False, True], ids=["relative-target", "absolute-target"])
@pytest.mark.parametrize(
    ("relative_path", "mutated", "expected"),
    [
        ("tests_py/test_redaction.py", False, 0),
        ("tests_py/test_redaction.py", True, 10),
        ("docs/redaction-example.md", False, 10),
    ],
)
def test_gitleaks_policy_limits_pem_exception_to_exact_fixture(
    tmp_path, relative_path, mutated, expected, absolute_target
):
    content = _redaction_pem_fixture()
    if mutated:
        changed = re.sub("fake", "different", content, count=1, flags=re.IGNORECASE)
        assert changed != content
        content = changed
    code, _ = _gitleaks_policy_scan(tmp_path, relative_path, content, absolute_target=absolute_target)
    assert code == expected


def test_gitleaks_policy_ignores_inline_allow_comments_in_strict_scan(tmp_path):
    canary = "gh" + "p_" + "aB3dE6fG9hJ2kL5mN8pQ1rS4tU7vW0xY3zA6"
    code, _ = _gitleaks_policy_scan(tmp_path, "README.md", f"api_key = {canary} # gitleaks:allow\n")
    assert code == 10


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


@pytest.mark.parametrize(
    ("secret", "inline_allow"),
    [(False, False), (True, False), (True, True)],
    ids=["clean", "synthetic-secret", "inline-allow-canary"],
)
def test_clean_commit_under_real_security_hook(tmp_path, monkeypatch, secret, inline_allow):
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
        suffix = " # gitleaks:allow" if inline_allow else ""
        (repository / "README.md").write_text(
            "github_token=" + "ghp_" + "aB3dE6fG9hJ2kL5mN8pQ1rS4tU7vW0xY3zA6" + suffix + "\n",
            encoding="utf-8",
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


@pytest.mark.parametrize(
    ("relative_path", "field", "known_digest", "expected"),
    [
        (".tmp/aido-runtime-evidence/2026-09-20/jev-selection-copy-restart.json", "credential_refs", True, 0),
        (
            ".tmp/aido-runtime-evidence/2026-09-20/jev-selection-copy-restart.json",
            "credential_refs",
            False,
            10,
        ),
        (".tmp/aido-runtime-evidence/2026-09-20/jev-selection-copy-restart.json", "api_key", True, 10),
        (".tmp/aido-runtime-evidence/2026-09-20/other.json", "credential_refs", True, 10),
    ],
)
def test_gitleaks_policy_limits_snapshot_hash_to_field_value_and_path(
    tmp_path, relative_path, field, known_digest, expected
):
    # This is an audited, non-secret snapshot checksum, not a provider credential.
    snapshot_digest = "b8907969d135aea3b6d793ec88f6a12e19b4432cf5b29307be2807ea02d58101"
    canary = "Q7nB2vL9rT4mX8kD1pF6sJ3wH5yC0aE9uR2zN7gV4bM8"
    content = json.dumps({field: snapshot_digest if known_digest else canary})
    code, rules = _gitleaks_policy_scan(tmp_path, relative_path, content)
    assert code == expected
    if expected:
        assert "generic-api-key" in rules


def test_gitleaks_policy_rejects_unknown_hex_value_in_snapshot_credential_field(tmp_path):
    canary = hashlib.sha256(b"synthetic-api-key-not-an-audited-snapshot-checksum").hexdigest()
    code, rules = _gitleaks_policy_scan(
        tmp_path,
        ".tmp/aido-runtime-evidence/2026-09-20/jev-selection-copy-restart.json",
        json.dumps({"credential_refs": canary}),
    )
    assert code == 10
    assert "generic-api-key" in rules


@pytest.mark.parametrize(
    ("relative_path", "field"),
    [
        (".tmp/aido-runtime-evidence/2026-09-21/automatic-defaults-review.json", "model_gateway_api.py"),
        (".tmp/aido-runtime-evidence/2026-09-21/timeout-historical-reconciliation.json", "JwtTokenUtil.java"),
        (
            ".tmp/aido-runtime-evidence/2026-09-21/ece351502b094565a9c4a4637ff570b6/"
            "local-gpu-admission-red.json",
            "idempotencyKey",
        ),
    ],
)
def test_gitleaks_policy_rejects_new_secret_in_allowed_digest_or_identifier_path(
    tmp_path, relative_path, field
):
    canary = "Q7nB2vL9rT4mX8kD1pF6sJ3wH5yC0aE9uR2zN7gV4bM8"
    code, rules = _gitleaks_policy_scan(tmp_path, relative_path, json.dumps({field: canary}))
    assert code == 10
    assert "generic-api-key" in rules
