from __future__ import annotations

from pathlib import Path

from local_control_center.agents.openhands_adapter import OpenHandsBrokerAdapter
from local_control_center.agents.runtime_contracts import get_runtime_contract, validate_runtime_tool_call
from local_control_center.agents.swe_agent_adapter import SweAgentBrokerAdapter
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
    assert "AIDO_RUNTIME_ISSUE_TO_PATCH_SMOKE" in content
    assert "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON" in content
    assert "AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON" in content
    assert 'operation = "issue_to_patch"' in content
    assert "issueText" in content
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


def test_optional_issue_to_patch_contracts_are_explicit_and_validated(tmp_path: Path) -> None:
    openhands_contract = get_runtime_contract("openhands")
    swe_contract = get_runtime_contract("swe_agent")
    assert openhands_contract["contractVersion"] == 1
    assert swe_contract["contractVersion"] == 1
    assert "issue_to_patch" in openhands_contract["supportedOperations"]
    assert "issue_to_patch" in swe_contract["supportedOperations"]

    missing_issue = validate_runtime_tool_call(
        "openhands",
        {"operation": "issue_to_patch", "argv": ["openhands", "run"]},
        {"workspacePath": str(tmp_path)},
    )
    assert missing_issue["valid"] is False
    assert "issueText" in missing_issue["reason"]

    dangerous_flag = validate_runtime_tool_call(
        "swe_agent",
        {"operation": "issue_to_patch", "argv": ["swe-agent", "--no-sandbox"], "issueText": "fix failing tests"},
        {"workspacePath": str(tmp_path)},
    )
    assert dangerous_flag["valid"] is False
    assert "--no-sandbox" in dangerous_flag["reason"]

    version_check = validate_runtime_tool_call(
        "openhands",
        {"argv": ["openhands", "--version"]},
        {"workspacePath": str(tmp_path)},
    )
    assert version_check["valid"] is True
    assert version_check["operation"] == "version_check"


def test_optional_adapters_block_invalid_issue_to_patch_contracts_before_install_detection(tmp_path: Path) -> None:
    openhands = OpenHandsBrokerAdapter().execute(
        tool_call={"operation": "issue_to_patch", "argv": ["openhands", "run"]},
        policy_input={"workspacePath": str(tmp_path)},
    )
    assert openhands["blocked"] is True
    assert "issueText" in openhands["reason"]

    swe_agent = SweAgentBrokerAdapter().execute(
        tool_call={"operation": "issue_to_patch", "argv": ["swe-agent", "--no-sandbox"], "issueText": "fix failing tests"},
        policy_input={"workspacePath": str(tmp_path)},
    )
    assert swe_agent["blocked"] is True
    assert "--no-sandbox" in swe_agent["reason"]
