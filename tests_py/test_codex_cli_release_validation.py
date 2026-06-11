from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "local-control-center" / "scripts" / "release_validate_codex_cli.py"


def load_release_validation_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_validate_codex_cli", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_release_validation_script_is_opt_in_package_command() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert SCRIPT.exists()
    assert package["scripts"]["smoke:codex:release"] == (
        "uv run python local-control-center/scripts/release_validate_codex_cli.py"
    )


def test_codex_release_validation_requires_explicit_env_without_detecting_codex() -> None:
    module = load_release_validation_script()

    missing = module.required_environment_errors({})
    assert any("AIDO_CODEX_COMMAND" in error for error in missing)
    assert any("AIDO_ENABLE_CLI_RUNTIMES=true" in error for error in missing)

    disabled = module.required_environment_errors(
        {"AIDO_CODEX_COMMAND": "codex", "AIDO_ENABLE_CLI_RUNTIMES": "false"}
    )
    assert disabled == ["AIDO_ENABLE_CLI_RUNTIMES=true is required for Codex CLI release validation."]

    assert module.required_environment_errors(
        {"AIDO_CODEX_COMMAND": "codex", "AIDO_ENABLE_CLI_RUNTIMES": "true"}
    ) == []


def test_codex_release_validation_parse_args_accepts_pnpm_separator() -> None:
    module = load_release_validation_script()

    args = module.parse_args(["--", "--report-path", "out/report.json"])

    assert args.report_path == "out/report.json"


def test_codex_release_validation_direct_execution_reports_configuration_required(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AIDO_CODEX_COMMAND", "AIDO_ENABLE_CLI_RUNTIMES"}
    }

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--report-path", str(report)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )

    assert completed.returncode == 1
    assert "ModuleNotFoundError" not in completed.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["reason"] == "configuration_required"
    assert any("AIDO_CODEX_COMMAND" in error for error in payload["errors"])


def test_codex_release_validation_rejects_empty_patch_or_non_workspace_bound_result() -> None:
    module = load_release_validation_script()

    errors = module.release_result_contract_errors(
        {
            "status": "completed",
            "runtime": {"id": "codex_cli", "kind": "cli", "safety": {"workspaceBound": False}},
            "runtimeResult": {"status": "completed", "returnCode": 0},
            "workspace": {"isolationType": "directory"},
            "diffSummary": {"changedFiles": [], "patchSizeBytes": 0},
            "qaResults": [],
            "evidencePackage": {"qaVerdict": "passed", "artifacts": [], "hashes": {}},
        }
    )

    assert any("git_worktree" in error for error in errors)
    assert any("workspace-bound" in error for error in errors)
    assert any("non-empty patch" in error for error in errors)
    assert any("QA" in error for error in errors)
    assert any("hash" in error.lower() for error in errors)


def test_codex_release_validation_accepts_completed_real_contract_result() -> None:
    module = load_release_validation_script()

    errors = module.release_result_contract_errors(
        {
            "status": "completed",
            "runtime": {"id": "codex_cli", "kind": "cli", "safety": {"workspaceBound": True}},
            "runtimeResult": {"status": "completed", "returnCode": 0},
            "workspace": {"isolationType": "git_worktree"},
            "diffSummary": {
                "changedFiles": ["release_patch.txt"],
                "patchSizeBytes": 123,
                "patchArtifactId": "artifact-patch",
                "manifestArtifactId": "artifact-manifest",
            },
            "qaResults": [
                {
                    "status": "passed",
                    "exitCode": 0,
                    "artifactHashes": {
                        "stdoutHash": "stdout-sha",
                        "stderrHash": "stderr-sha",
                        "outputArtifactHash": "output-sha",
                    },
                }
            ],
            "evidencePackage": {
                "qaVerdict": "passed",
                "runtimeId": "codex_cli",
                "toolCalls": [{"id": "tool-call-1"}],
                "policyDecisions": [{"id": "policy-1"}],
                "artifacts": [
                    {"id": "artifact-patch", "name": "diff.patch", "hash": "patch-sha"},
                    {"id": "artifact-qa", "name": "qa-results.json", "hash": "qa-sha"},
                    {
                        "id": "artifact-manifest",
                        "name": "issue-to-patch-evidence.json",
                        "hash": "manifest-sha",
                    },
                ],
                "hashes": {"artifact-patch": "patch-sha", "artifact-qa": "qa-sha"},
            },
        }
    )

    assert errors == []
