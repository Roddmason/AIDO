from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from local_control_center.agents.cli_runtimes.base import RuntimeRequest
from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now
from local_control_center.workflows.issue_to_patch_runner import _execution_result_from_tool_call

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "local-control-center" / "scripts" / "release_validate_claude_code_cli.py"


def load_release_validation_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_validate_claude_code_cli", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claude_release_validation_script_is_opt_in_package_command() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert SCRIPT.exists()
    assert package["scripts"]["smoke:claude:release"] == (
        "uv run python local-control-center/scripts/release_validate_claude_code_cli.py"
    )


def test_claude_release_validation_requires_explicit_env_without_detecting_claude() -> None:
    module = load_release_validation_script()

    missing = module.required_environment_errors({})
    assert any("AIDO_CLAUDE_COMMAND" in error for error in missing)
    assert any("AIDO_ENABLE_CLI_RUNTIMES=true" in error for error in missing)

    disabled = module.required_environment_errors(
        {"AIDO_CLAUDE_COMMAND": "claude", "AIDO_ENABLE_CLI_RUNTIMES": "false"}
    )
    assert disabled == ["AIDO_ENABLE_CLI_RUNTIMES=true is required for Claude Code CLI release validation."]

    assert (
        module.required_environment_errors(
            {"AIDO_CLAUDE_COMMAND": "claude", "AIDO_ENABLE_CLI_RUNTIMES": "true"}
        )
        == []
    )


def test_claude_release_validation_parse_args_accepts_pnpm_separator() -> None:
    module = load_release_validation_script()

    args = module.parse_args(["--", "--report-path", "out/report.json"])

    assert args.report_path == "out/report.json"


def test_claude_release_validation_direct_execution_reports_configuration_required(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AIDO_CLAUDE_COMMAND", "AIDO_ENABLE_CLI_RUNTIMES"}
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
    assert any("AIDO_CLAUDE_COMMAND" in error for error in payload["errors"])


def test_claude_release_validation_rejects_empty_patch_or_non_workspace_bound_result() -> None:
    module = load_release_validation_script()

    errors = module.release_result_contract_errors(
        {
            "status": "completed",
            "runtime": {"id": "claude_code_cli", "kind": "cli", "safety": {"workspaceBound": False}},
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


def test_claude_release_validation_accepts_completed_real_contract_result() -> None:
    module = load_release_validation_script()

    errors = module.release_result_contract_errors(
        {
            "status": "completed",
            "runtime": {"id": "claude_code_cli", "kind": "cli", "safety": {"workspaceBound": True}},
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
                    "execution": "restricted_subprocess",
                    "exitCode": 0,
                    "toolCallId": "tool-call-1",
                    "artifactHashes": {
                        "stdoutHash": "stdout-sha",
                        "stderrHash": "stderr-sha",
                        "outputArtifactHash": "output-sha",
                    },
                    "metadata": {"permissionDecisionId": "policy-1"},
                }
            ],
            "evidencePackage": {
                "qaVerdict": "passed",
                "evidenceSource": "qa_passed_by_command",
                "runtimeId": "claude_code_cli",
                "toolCalls": [{"id": "tool-call-1"}],
                "policyDecisions": [{"id": "policy-1", "decision": "allow"}],
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


def test_claude_code_runtime_uses_supported_workspace_edit_syntax(tmp_path: Path) -> None:
    command = ClaudeCodeCliRuntime(executable="claude").build_command(
        RuntimeRequest(
            runtime="claude_code_cli",
            workspaceId="workspace-test",
            workspacePath=str(tmp_path),
            prompt="Create a patch",
        )
    )

    assert command[:2] == ["claude", "--print"]
    assert "--permission-mode" in command
    assert "acceptEdits" in command
    assert "--add-dir" in command
    assert str(tmp_path) in command
    assert "--cwd" not in command
    assert command[-1] == "Create a patch"


def test_claude_code_is_not_executable_when_configured_command_does_not_match_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_CLAUDE_COMMAND", sys.executable)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1
            WHERE provider_id = 'claude_code_cli'
            """
        )
        # Bring the runtime through W's installation lifecycle (enabled installation + validated native
        # account) so the only remaining gate is the command mismatch this test verifies. Without this,
        # the earlier runtime_installations.enabled gate would mask the command-mismatch reason.
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.cli.enabled", True)
        repo.upsert_installation(
            {
                "runtimeId": "claude_code_cli",
                "kind": "cli",
                "executablePath": sys.executable,
                "enabled": True,
                "configurationSource": "manual",
            }
        )
        account = next(item for item in repo.list_runtime_accounts("claude_code_cli") if item["isDefault"])
        # utc_now() y no una fecha ancla: pasada la TTL de 300s la cuenta se re-sondea, el probe
        # real degrada a "not logged in" y esa razón enmascara el command-mismatch bajo prueba.
        repo.update_runtime_account(
            account["id"],
            {"healthStatus": "healthy", "lastValidationAt": utc_now()},
        )
        status = {
            provider["id"]: provider for provider in RuntimeStatusService(connection).list_provider_statuses()
        }["claude_code_cli"]

    assert status["detected"] is True
    assert status["available"] is True
    assert status["executable"] is False
    assert "declared runtime command" in status["reason"]


def test_claude_cli_syntax_failure_maps_to_runtime_unavailable() -> None:
    result = _execution_result_from_tool_call(
        {
            "status": "failed",
            "payload": {
                "execution": "restricted_subprocess",
                "executionResult": {
                    "returnCode": 2,
                    "timedOut": False,
                    "blocked": False,
                    "stderr": "error: unknown option '--cwd'",
                },
            },
        }
    )

    assert result["status"] == "runtime_unavailable"
    assert "syntax" in result["reason"].lower()
