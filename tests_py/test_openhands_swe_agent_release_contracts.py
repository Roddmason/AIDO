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
from local_control_center.agents.cli_runtimes.openhands import OpenHandsRuntime
from local_control_center.agents.cli_runtimes.swe_agent import SweAgentRuntime
from local_control_center.agents.runtime_provider_config import list_runtime_provider_configurations
from local_control_center.agents.runtime_registry import RuntimeCommandUnavailableError, build_issue_to_patch_argv
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "local-control-center" / "scripts" / "release_validate_optional_cli_runtime.py"


def init_git_workspace(path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True, shell=False, check=True)
    subprocess.run(
        ["git", "config", "user.name", "AIDO Test"],
        cwd=path,
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "aido-test@example.test"],
        cwd=path,
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    (path / "README.md").write_text("# Test workspace\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "."], cwd=path, capture_output=True, text=True, shell=False, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial test workspace"],
        cwd=path,
        capture_output=True,
        text=True,
        shell=False,
        check=True,
    )
    return path


def load_release_validation_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_validate_optional_cli_runtime", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_optional_cli_release_validation_is_opt_in_for_openhands_and_swe_agent() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert SCRIPT.exists()
    assert package["scripts"]["smoke:openhands:release"] == (
        "uv run python local-control-center/scripts/release_validate_optional_cli_runtime.py --runtime openhands"
    )
    assert package["scripts"]["smoke:swe-agent:release"] == (
        "uv run python local-control-center/scripts/release_validate_optional_cli_runtime.py --runtime swe_agent"
    )


def test_optional_cli_release_validation_requires_runtime_command_and_cli_gate() -> None:
    module = load_release_validation_script()

    openhands_missing = module.required_environment_errors("openhands", {})
    assert any("AIDO_OPENHANDS_COMMAND" in error for error in openhands_missing)
    assert any("AIDO_ENABLE_CLI_RUNTIMES=true" in error for error in openhands_missing)

    swe_missing = module.required_environment_errors("swe_agent", {})
    assert any("AIDO_SWE_AGENT_COMMAND" in error for error in swe_missing)
    assert any("AIDO_ENABLE_CLI_RUNTIMES=true" in error for error in swe_missing)
    invalid_override = module.required_environment_errors(
        "openhands",
        {
            "AIDO_OPENHANDS_COMMAND": "openhands",
            "AIDO_ENABLE_CLI_RUNTIMES": "true",
            "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON": '["python","-c","print(1)"]',
        },
    )
    assert any("openhands argv" in error.lower() and "python" in error.lower() for error in invalid_override)

    assert module.required_environment_errors(
        "openhands",
        {"AIDO_OPENHANDS_COMMAND": "openhands", "AIDO_ENABLE_CLI_RUNTIMES": "true"},
    ) == []
    assert module.required_environment_errors(
        "swe_agent",
        {"AIDO_SWE_AGENT_COMMAND": "sweagent", "AIDO_ENABLE_CLI_RUNTIMES": "true"},
    ) == []


def test_optional_cli_release_validation_direct_execution_reports_configuration_required(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "AIDO_OPENHANDS_COMMAND",
            "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON",
            "AIDO_ENABLE_CLI_RUNTIMES",
        }
    }

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--runtime", "openhands", "--report-path", str(report)],
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
    assert any("AIDO_OPENHANDS_COMMAND" in error for error in payload["errors"])


def test_optional_cli_release_validation_parse_args_accepts_pnpm_separator() -> None:
    module = load_release_validation_script()

    args = module.parse_args(["--", "--runtime", "swe_agent", "--report-path", "out/report.json"])
    package_args = module.parse_args(["--runtime", "openhands", "--", "--report-path", "out/openhands.json"])

    assert args.runtime == "swe_agent"
    assert args.report_path == "out/report.json"
    assert package_args.runtime == "openhands"
    assert package_args.report_path == "out/openhands.json"


def test_openhands_runtime_uses_documented_headless_workspace_syntax(tmp_path: Path) -> None:
    workspace = init_git_workspace(tmp_path)
    command = OpenHandsRuntime(executable="openhands").build_command(
        RuntimeRequest(
            runtime="openhands",
            workspaceId="workspace-test",
            workspacePath=str(workspace),
            prompt="Create a patch",
        )
    )

    assert command[:3] == ["openhands", "--headless", "--json"]
    assert "-t" in command
    assert "Create a patch" in command
    assert "run" not in command
    assert "--workspace" not in command


def test_swe_agent_runtime_uses_documented_repo_and_problem_statement_syntax(tmp_path: Path) -> None:
    workspace = init_git_workspace(tmp_path)
    command = SweAgentRuntime(executable="sweagent").build_command(
        RuntimeRequest(
            runtime="swe_agent",
            workspaceId="workspace-test",
            workspacePath=str(workspace),
            prompt="Create a patch",
        )
    )

    assert command[:2] == ["sweagent", "run"]
    assert f"--env.repo.path={workspace}" in command
    assert "--problem_statement.text=Create a patch" in command
    assert "--actions.apply_patch_locally" in command
    assert "--repo" not in command


def test_optional_cli_configuration_exposes_command_and_explicit_argv_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_OPENHANDS_COMMAND", "openhands")
    monkeypatch.setenv("AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON", '["openhands","--headless","-t","fix"]')
    monkeypatch.setenv("AIDO_SWE_AGENT_COMMAND", "sweagent")
    monkeypatch.setenv(
        "AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON",
        '["sweagent","run","--env.repo.path=/repo","--problem_statement.text=fix"]',
    )

    providers = {provider["id"]: provider for provider in list_runtime_provider_configurations()}

    assert providers["openhands"]["configured"] is True
    assert providers["swe_agent"]["configured"] is True
    serialized = str(providers)
    assert "openhands\",\"--headless" not in serialized
    assert "--env.repo.path=/repo" not in serialized
    assert all(variable["fingerprint"] for variable in providers["openhands"]["variables"])
    assert all(variable["fingerprint"] for variable in providers["swe_agent"]["variables"])


def test_seed_does_not_enable_openhands_or_swe_agent_issue_to_patch_capabilities(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        rows = connection.execute(
            """
            SELECT runtime, capability, enabled
            FROM runtime_capabilities
            WHERE runtime IN ('openhands', 'swe_agent') AND capability = 'issue_to_patch'
            ORDER BY runtime
            """
        ).fetchall()

    assert rows == []


def test_openhands_and_swe_agent_are_not_executable_without_developer_agent_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_OPENHANDS_COMMAND", sys.executable)
    monkeypatch.setenv("AIDO_SWE_AGENT_COMMAND", sys.executable)
    monkeypatch.setenv("AIDO_ENABLE_CLI_RUNTIMES", "true")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE provider_accounts SET enabled = 1 WHERE provider_id IN ('openhands', 'swe_agent')")
        statuses = {
            provider["id"]: provider
            for provider in RuntimeStatusService(connection).list_provider_statuses()
            if provider["id"] in {"openhands", "swe_agent"}
        }

    assert statuses["openhands"]["available"] is True
    assert statuses["swe_agent"]["available"] is True
    assert statuses["openhands"]["executable"] is False
    assert statuses["swe_agent"]["executable"] is False
    assert "code_edit" not in statuses["openhands"]["capabilities"]
    assert "code_edit" not in statuses["swe_agent"]["capabilities"]
    assert "code_edit" in statuses["openhands"]["reason"]
    assert "code_edit" in statuses["swe_agent"]["reason"]


def test_optional_cli_explicit_argv_must_match_declared_runtime(tmp_path: Path) -> None:
    with pytest.raises(RuntimeCommandUnavailableError, match="OpenHands"):
        build_issue_to_patch_argv(
            runtime={
                "id": "openhands",
                "detectedCommand": "openhands",
                "issueToPatchArgv": [sys.executable, "-c", "print('not openhands')"],
            },
            workspace_id="workspace-test",
            workspace_path=str(tmp_path),
            title="Patch",
            issue_text="Create a patch",
            workflow_run_id="workflow-run-test",
            workflow_step_id=None,
            agent_id="agent-test",
            connection=None,  # type: ignore[arg-type]
        )


def test_optional_cli_release_contract_requires_stdout_and_stderr_artifacts() -> None:
    module = load_release_validation_script()

    errors = module.release_result_contract_errors(
        {
            "status": "completed",
            "runtime": {"id": "openhands", "kind": "cli", "safety": {"workspaceBound": True}},
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
                "runtimeId": "openhands",
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

    assert any("stdout" in error.lower() for error in errors)
    assert any("stderr" in error.lower() for error in errors)
