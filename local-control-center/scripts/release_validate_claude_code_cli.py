from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now
from local_control_center.workflows.issue_to_patch_runner import IssueToPatchRunner


REPORT_SCHEMA = "aido.claude-code-cli-release-validation.v1"
VALIDATION_MARKER = "claude code release validation passed"
DEFAULT_TITLE = "Claude Code CLI release validation patch"
DEFAULT_ISSUE_TEXT = f"""Release validation smoke for the Claude Code CLI adapter.

In this workspace only, create a file named release_patch.txt containing exactly this text:
{VALIDATION_MARKER}

Do not commit, push, install dependencies, edit the test file, or modify files outside this workspace.
Make the existing pytest validation pass.
"""


class ReleaseValidationError(RuntimeError):
    pass


def required_environment_errors(environ: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    if not str(environ.get("AIDO_CLAUDE_COMMAND") or "").strip().strip('"'):
        errors.append("AIDO_CLAUDE_COMMAND is required for Claude Code CLI release validation.")
    if str(environ.get("AIDO_ENABLE_CLI_RUNTIMES") or "").strip().lower() != "true":
        errors.append("AIDO_ENABLE_CLI_RUNTIMES=true is required for Claude Code CLI release validation.")
    return errors


def release_result_contract_errors(result: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    runtime = result.get("runtime") if isinstance(result.get("runtime"), Mapping) else {}
    runtime_result = result.get("runtimeResult") if isinstance(result.get("runtimeResult"), Mapping) else {}
    workspace = result.get("workspace") if isinstance(result.get("workspace"), Mapping) else {}
    diff_summary = result.get("diffSummary") if isinstance(result.get("diffSummary"), Mapping) else {}
    evidence = result.get("evidencePackage") if isinstance(result.get("evidencePackage"), Mapping) else {}
    qa_results = result.get("qaResults") if isinstance(result.get("qaResults"), list) else []

    if result.get("status") != "completed":
        errors.append(f"issue_to_patch did not complete: {result.get('status') or 'unknown'}.")
    if runtime.get("id") != "claude_code_cli" or runtime.get("kind") != "cli":
        errors.append("Runtime must be claude_code_cli with cli kind.")
    if not (runtime.get("safety") or {}).get("workspaceBound"):
        errors.append("Runtime safety must be workspace-bound.")
    if runtime_result.get("status") != "completed" or runtime_result.get("returnCode") != 0:
        errors.append("Claude Code CLI runtime execution did not complete with return code 0.")
    if workspace.get("isolationType") != "git_worktree":
        errors.append("Validation workspace must be a git_worktree.")

    changed_files = diff_summary.get("changedFiles") if isinstance(diff_summary.get("changedFiles"), list) else []
    patch_size = int(diff_summary.get("patchSizeBytes") or 0)
    if not changed_files or patch_size <= 0 or not diff_summary.get("patchArtifactId"):
        errors.append("Claude Code issue_to_patch must produce a non-empty patch artifact.")
    if not diff_summary.get("manifestArtifactId"):
        errors.append("Evidence manifest artifact is required.")

    if not qa_results or any(result.get("status") != "passed" for result in qa_results):
        errors.append("QA results are required and must pass.")
    for index, qa_result in enumerate(qa_results):
        if qa_result.get("exitCode") != 0:
            errors.append(f"QA result {index} did not exit with code 0.")
        hashes = qa_result.get("artifactHashes") if isinstance(qa_result.get("artifactHashes"), Mapping) else {}
        if not hashes.get("stdoutHash") or not hashes.get("stderrHash") or not hashes.get("outputArtifactHash"):
            errors.append(f"QA result {index} is missing stdout/stderr/output artifact hashes.")

    artifacts = evidence.get("artifacts") if isinstance(evidence.get("artifacts"), list) else []
    artifact_names = {artifact.get("name") for artifact in artifacts if isinstance(artifact, Mapping)}
    required_artifacts = {"diff.patch", "qa-results.json", "issue-to-patch-evidence.json"}
    missing_artifacts = sorted(required_artifacts - artifact_names)
    if evidence.get("qaVerdict") != "passed":
        errors.append("Evidence package QA verdict must be passed.")
    if evidence.get("runtimeId") != "claude_code_cli":
        errors.append("Evidence package must be linked to claude_code_cli.")
    if missing_artifacts:
        errors.append("Evidence package is missing required artifacts: " + ", ".join(missing_artifacts) + ".")
    if not artifacts or any(not artifact.get("hash") for artifact in artifacts if isinstance(artifact, Mapping)):
        errors.append("Evidence package artifacts must include SHA-256 hashes.")
    if not evidence.get("hashes"):
        errors.append("Evidence package must include artifact hashes.")
    if not evidence.get("toolCalls"):
        errors.append("Evidence package must include the Claude Code CLI tool call.")
    if not evidence.get("policyDecisions"):
        errors.append("Evidence package must include policy decisions.")
    return errors


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_work_root() -> Path:
    return REPO_ROOT / ".tmp" / "claude-code-cli-release-validation" / _utc_slug()


def _write_report(report: Mapping[str, Any], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json_dumps(redact_secrets(dict(report))), encoding="utf-8")


def _resolve_claude_command(command: str) -> str | None:
    normalized = command.strip().strip('"')
    if not normalized:
        return None
    candidate = Path(normalized)
    if candidate.exists():
        return str(candidate)
    return shutil.which(normalized)


def _run_checked(argv: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, shell=False, check=False)
    if completed.returncode != 0:
        raise ReleaseValidationError(
            f"Command failed ({completed.returncode}): {' '.join(argv)}\n{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _create_validation_git_repo(work_root: Path) -> Path:
    if not shutil.which("git"):
        raise ReleaseValidationError("git CLI is required to create the real temporary validation repository.")
    repo_path = work_root / "source-repo"
    repo_path.mkdir(parents=True, exist_ok=False)
    _run_checked(["git", "init"], cwd=repo_path)
    _run_checked(["git", "config", "user.name", "AIDO Release Validation"], cwd=repo_path)
    _run_checked(["git", "config", "user.email", "aido-release-validation@example.test"], cwd=repo_path)
    (repo_path / "README.md").write_text("# Claude Code CLI Release Validation\n", encoding="utf-8")
    tests_dir = repo_path / "tests_py"
    tests_dir.mkdir()
    (tests_dir / "test_release_patch.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "",
                "def test_claude_release_validation_patch_exists() -> None:",
                "    content = Path('release_patch.txt').read_text(encoding='utf-8').strip()",
                f"    assert content == {VALIDATION_MARKER!r}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _run_checked(["git", "add", "--", "."], cwd=repo_path)
    _run_checked(["git", "commit", "-m", "Initial release validation fixture"], cwd=repo_path)
    return repo_path


def _enable_claude_provider(connection: Any) -> None:
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE provider_accounts
        SET enabled = 1, updated_at = ?
        WHERE provider_id = 'claude_code_cli'
        """,
        (timestamp,),
    )
    connection.execute(
        """
        INSERT INTO runtime_capabilities (id, runtime, capability, enabled, metadata, created_at, updated_at)
        VALUES (?, 'claude_code_cli', 'issue_to_patch', 1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET enabled = 1, metadata = excluded.metadata, updated_at = excluded.updated_at
        """,
        (
            "claude_code_cli:issue_to_patch",
            json_dumps({"workspaceBound": True, "source": "release_validation"}),
            timestamp,
            timestamp,
        ),
    )


def _build_payload(*, project_id: str, title: str, issue_text: str) -> dict[str, Any]:
    return {
        "projectId": project_id,
        "title": title,
        "issueText": issue_text,
        "preferredRuntime": "claude_code_cli",
        "qaCommands": [[sys.executable, "-m", "pytest", "tests_py", "-q"]],
        "requireApproval": False,
    }


def run_release_validation(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    work_root = Path(args.work_root).resolve(strict=False) if args.work_root else _default_work_root()
    report_path = Path(args.report_path).resolve(strict=False) if args.report_path else work_root / "report.json"
    started_at = utc_now()
    env_errors = required_environment_errors(os.environ)
    if env_errors:
        return 1, {
            "schema": REPORT_SCHEMA,
            "status": "failed",
            "reason": "configuration_required",
            "startedAt": started_at,
            "finishedAt": utc_now(),
            "workRoot": str(work_root),
            "reportPath": str(report_path),
            "errors": env_errors,
        }

    claude_command = str(os.environ["AIDO_CLAUDE_COMMAND"]).strip().strip('"')
    resolved_claude = _resolve_claude_command(claude_command)
    if not resolved_claude:
        return 1, {
            "schema": REPORT_SCHEMA,
            "status": "failed",
            "reason": "configuration_required",
            "startedAt": started_at,
            "finishedAt": utc_now(),
            "workRoot": str(work_root),
            "reportPath": str(report_path),
            "claudeCommand": claude_command,
            "errors": [f"Claude Code CLI command not found from AIDO_CLAUDE_COMMAND: {claude_command}"],
        }

    runtime: ControlCenterRuntime | None = None
    try:
        work_root.mkdir(parents=True, exist_ok=False)
        source_repo = _create_validation_git_repo(work_root)
        runtime = ControlCenterRuntime(cwd=work_root, db_path=work_root / "control-center.sqlite3")
        runtime.init()
        _enable_claude_provider(runtime.connection)
        project = ProjectsRepository(runtime.connection).create_project(
            name="Claude Code CLI Release Validation",
            path=source_repo,
            template_id="other",
            create_directory=False,
            source="release_validation",
            metadata={"claudeCommand": resolved_claude, "schema": REPORT_SCHEMA},
        )
        result = IssueToPatchRunner(runtime.connection, root=work_root).run(
            _build_payload(project_id=project["id"], title=args.title, issue_text=args.issue_text)
        )
        contract_errors = release_result_contract_errors(result)
        status = "passed" if not contract_errors else "failed"
        report = {
            "schema": REPORT_SCHEMA,
            "status": status,
            "reason": "passed" if status == "passed" else "contract_failed",
            "startedAt": started_at,
            "finishedAt": utc_now(),
            "workRoot": str(work_root),
            "sourceRepo": str(source_repo),
            "reportPath": str(report_path),
            "claudeCommand": resolved_claude,
            "errors": contract_errors,
            "result": result,
        }
        return (0 if status == "passed" else 1), report
    except Exception as error:
        return 1, {
            "schema": REPORT_SCHEMA,
            "status": "failed",
            "reason": "blocked",
            "startedAt": started_at,
            "finishedAt": utc_now(),
            "workRoot": str(work_root),
            "reportPath": str(report_path),
            "claudeCommand": resolved_claude,
            "errors": [str(error)],
            "traceback": traceback.format_exc(limit=12),
        }
    finally:
        if runtime is not None:
            runtime.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    normalized_argv = argv[1:] if argv[:1] == ["--"] else argv
    parser = argparse.ArgumentParser(description="Run real Claude Code CLI release validation through issue_to_patch.")
    parser.add_argument("--work-root", default="", help="Directory for the temporary DB, source repo, worktree, and artifacts.")
    parser.add_argument("--report-path", default="", help="JSON report path. Defaults to <work-root>/report.json.")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--issue-text", default=DEFAULT_ISSUE_TEXT)
    return parser.parse_args(normalized_argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    exit_code, report = run_release_validation(args)
    report_path = Path(report.get("reportPath") or Path(report["workRoot"]) / "report.json")
    _write_report(report, report_path)
    print(json.dumps(redact_secrets(report), indent=2, sort_keys=True, default=str))
    if exit_code != 0:
        print("Release validation failed. See report: " + str(report_path), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
