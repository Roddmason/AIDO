from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-certify.ps1"


def test_release_certification_is_opt_in_package_script_only() -> None:
    package_text = (ROOT / "package.json").read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert SCRIPT.exists()
    assert package_text.count('"release:certify"') == 1
    assert package["scripts"]["release:certify"] == (
        "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release-certify.ps1"
    )
    assert "release:certify" not in package["scripts"]["prestart"]
    assert "release:certify" not in package["scripts"]["start"]
    assert "release:certify" not in package["scripts"]["start:py"]
    assert "release:certify" not in package["scripts"]["start:windows"]


def test_release_certification_runs_quality_and_writes_reports() -> None:
    content = SCRIPT.read_text(encoding="utf-8")

    assert '@("corepack", "pnpm@10.24.0", "run", "quality")' in content
    assert ".tmp" in content
    assert "release-certification" in content
    assert "release-certification-report.json" in content
    assert "quality.log" in content
    assert "logs" in content
    assert "reports" in content
    assert "Write-ReleaseReport" in content
    assert "status = \"passed\"" in content
    assert "status = \"failed\"" in content


def test_release_certification_runs_release_smokes_only_when_configured() -> None:
    content = SCRIPT.read_text(encoding="utf-8")

    assert "configuration_required" in content
    assert "skipped" in content
    assert "AIDO_ENABLE_CLI_RUNTIMES" in content
    assert "AIDO_CODEX_COMMAND" in content
    assert "AIDO_CLAUDE_COMMAND" in content
    assert "AIDO_OPENHANDS_COMMAND" in content
    assert "AIDO_SWE_AGENT_COMMAND" in content
    assert "smoke:codex:release" in content
    assert "smoke:claude:release" in content
    assert "smoke:openhands:release" in content
    assert "smoke:swe-agent:release" in content
    assert "--report-path" in content
    assert "-ReportPath $childReportPath" in content
    assert "release_smoke_report_missing" in content


def test_release_certification_redacts_secret_like_output() -> None:
    content = SCRIPT.read_text(encoding="utf-8")

    assert "Redact-SecretText" in content
    assert "Register-SecretMask" in content
    assert "[redacted]" in content
    assert "SECRET" in content
    assert "TOKEN" in content
    assert "PASSWORD" in content
    assert "CREDENTIAL" in content
    assert "API_KEY" in content
    assert "AUTHORIZATION" in content
    assert "Bearer\\s+" in content
    assert "ghp_" in content
    assert "sk-" in content


def _powershell() -> str:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    assert powershell is not None, "PowerShell is required to execute release-certify.ps1"
    return powershell


def _write_corepack_shim(bin_dir: Path, *, exit_code: int = 0, stream: str = "stdout") -> None:
    bin_dir.mkdir()
    redirect = "1>&2" if stream == "stderr" else ""
    (bin_dir / "corepack.cmd").write_text(
        "\r\n".join(
            [
                "@echo off",
                f"echo quality emitted %AIDO_RELEASE_SECRET_CANARY% {redirect}".rstrip(),
                f"exit /b {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _release_env(bin_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "AIDO_ENABLE_CLI_RUNTIMES",
        "AIDO_CODEX_COMMAND",
        "AIDO_CLAUDE_COMMAND",
        "AIDO_OPENHANDS_COMMAND",
        "AIDO_SWE_AGENT_COMMAND",
    ):
        env.pop(name, None)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["AIDO_RELEASE_SECRET_CANARY"] = "release-secret-canary-12345"
    return env


def test_release_certification_report_preserves_configuration_required_smokes_and_redacts_logs(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    output_root = tmp_path / "out with spaces"
    _write_corepack_shim(bin_dir)

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(output_root),
        ],
        cwd=ROOT,
        env=_release_env(bin_dir),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report_path = output_root / "reports" / "release-certification-report.json"
    quality_log = output_root / "logs" / "quality.log"
    report_text = report_path.read_text(encoding="utf-8-sig")
    quality_log_text = quality_log.read_text(encoding="utf-8-sig")
    combined_process_output = completed.stdout + completed.stderr

    assert "release-secret-canary-12345" not in report_text
    assert "release-secret-canary-12345" not in quality_log_text
    assert "release-secret-canary-12345" not in combined_process_output
    assert "[redacted]" in quality_log_text

    payload = json.loads(report_text)
    assert payload["status"] == "passed"
    assert payload["certificationScope"] == "quality_with_configuration_required_smokes"
    assert payload["quality"]["status"] == "passed"
    assert {smoke["status"] for smoke in payload["releaseSmokes"]} == {"configuration_required"}
    assert {smoke["execution"] for smoke in payload["releaseSmokes"]} == {"skipped"}
    smoke_report_paths = [Path(smoke["reportPath"]) for smoke in payload["releaseSmokes"]]
    smoke_log_paths = [Path(smoke["logPath"]) for smoke in payload["releaseSmokes"]]
    assert all(path.exists() for path in smoke_report_paths)
    assert all(path.exists() for path in smoke_log_paths)
    assert {json.loads(path.read_text(encoding="utf-8-sig"))["status"] for path in smoke_report_paths} == {
        "configuration_required"
    }
    assert {artifact["kind"] for artifact in payload["artifacts"]} >= {
        "release_report",
        "quality_log",
        "release_smoke_report",
        "release_smoke_log",
    }


def test_release_certification_strict_mode_fails_when_smoke_configuration_is_missing(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    output_root = tmp_path / "strict out"
    _write_corepack_shim(bin_dir)

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(output_root),
            "-FailOnSkippedSmokes",
        ],
        cwd=ROOT,
        env=_release_env(bin_dir),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(
        (output_root / "reports" / "release-certification-report.json").read_text(encoding="utf-8-sig")
    )
    assert payload["status"] == "failed"
    assert payload["reason"] == "configuration_required"


def test_release_certification_fails_configured_smoke_that_does_not_write_report(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    output_root = tmp_path / "configured smoke missing report"
    _write_corepack_shim(bin_dir)
    env = _release_env(bin_dir)
    env["AIDO_ENABLE_CLI_RUNTIMES"] = "true"
    env["AIDO_CODEX_COMMAND"] = "codex"

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(output_root),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(
        (output_root / "reports" / "release-certification-report.json").read_text(encoding="utf-8-sig")
    )
    codex = next(smoke for smoke in payload["releaseSmokes"] if smoke["packageScript"] == "smoke:codex:release")
    codex_report_path = Path(codex["reportPath"])
    assert payload["status"] == "failed"
    assert payload["reason"] == "release_smoke_failed"
    assert codex["status"] == "failed"
    assert codex["execution"] == "executed"
    assert codex["reason"] == "release_smoke_report_missing"
    assert codex_report_path.exists()
    assert json.loads(codex_report_path.read_text(encoding="utf-8-sig"))["reason"] == "release_smoke_report_missing"


def test_release_certification_failed_quality_log_preserves_redacted_stderr(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    output_root = tmp_path / "failed quality"
    _write_corepack_shim(bin_dir, exit_code=1, stream="stderr")

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(output_root),
        ],
        cwd=ROOT,
        env=_release_env(bin_dir),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )

    assert completed.returncode == 1
    report_text = (output_root / "reports" / "release-certification-report.json").read_text(
        encoding="utf-8-sig"
    )
    quality_log_text = (output_root / "logs" / "quality.log").read_text(encoding="utf-8-sig")

    assert "quality emitted" in quality_log_text
    assert "release-secret-canary-12345" not in quality_log_text
    assert "release-secret-canary-12345" not in report_text
    payload = json.loads(report_text)
    assert payload["status"] == "failed"
    assert payload["reason"] == "quality_failed"
