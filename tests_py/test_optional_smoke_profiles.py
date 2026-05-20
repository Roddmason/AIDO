from __future__ import annotations

from pathlib import Path

from local_control_center.security_policy.command_classifier import classify_command
from local_control_center.security_policy.sandbox import ALLOWED_EXECUTABLES


ROOT = Path(__file__).resolve().parents[1]


def test_optional_runtime_smoke_script_is_explicitly_opt_in() -> None:
    script = ROOT / "local-control-center" / "scripts" / "smoke-runtime-adapters.ps1"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "AIDO_RUNTIME_SMOKE" in content
    assert "internal_mock" in content
    assert "openhands" in content
    assert "swe_agent" in content
    assert "mcp" in content
    assert "Resolve-OptionalCommand" in content
    assert "openhands --version" in content
    assert "swe-agent --version" in content
    assert "X-Local-Control-Token" in content
    assert "exit 0" in content


def test_optional_otel_collector_smoke_script_does_not_require_docker_by_default() -> None:
    script = ROOT / "local-control-center" / "scripts" / "smoke-otel-exporter.ps1"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "AIDO_OTEL_SMOKE" in content
    assert "AIDO_OTEL_EXPORTER" in content
    assert "otlp_http" in content
    assert "docker" in content.lower()
    assert "Skip" in content
    assert "exit 0" in content


def test_optional_runtime_cli_executables_are_sandbox_allowlisted_for_approved_runs() -> None:
    assert {"openhands", "openhands.exe", "sweagent", "sweagent.exe", "swe-agent", "swe-agent.exe"} <= ALLOWED_EXECUTABLES


def test_optional_runtime_version_commands_are_low_risk_for_smoke_profiles() -> None:
    assert classify_command("openhands --version")["riskLevel"] == "low"
    assert classify_command("swe-agent --version")["riskLevel"] == "low"
