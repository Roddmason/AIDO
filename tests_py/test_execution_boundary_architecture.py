from __future__ import annotations

import ast
from pathlib import Path

from local_control_center.security_policy.policy_engine import evaluate_action


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT / "local_control_center"

APPROVED_SUBPROCESS_FILES = {
    "local_control_center/security_policy/git_command_runner.py",
    "local_control_center/security_policy/sandbox.py",
}

APPROVED_SANDBOX_EXECUTION_FILES = {
    "local_control_center/agents/cli_runtimes/base.py",
    "local_control_center/agents/openhands_adapter.py",
    "local_control_center/agents/runtime_adapters.py",
    "local_control_center/agents/swe_agent_adapter.py",
    "local_control_center/agents/tool_broker.py",
    "local_control_center/integrations/mcp_gateway.py",
    "local_control_center/security_policy/sandbox.py",
}

APPROVED_RUNTIME_FACTORY_FILES = {
    "local_control_center/agents/runtime_registry.py",
}


def _product_python_files() -> list[Path]:
    return sorted(PRODUCT_ROOT.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _call_has_shell_true(node: ast.Call) -> bool:
    return any(
        keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
        for keyword in node.keywords
    )


def test_product_subprocess_execution_is_only_in_approved_policy_boundaries() -> None:
    dangerous_calls = {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "exec",
    }
    violations: list[str] = []

    for path in _product_python_files():
        rel = _relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node.func) in dangerous_calls and rel not in APPROVED_SUBPROCESS_FILES:
                violations.append(f"{rel}:{node.lineno}:{_call_name(node.func)}")

    assert violations == []


def test_product_subprocess_never_uses_shell_true() -> None:
    violations: list[str] = []

    for path in _product_python_files():
        rel = _relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _call_name(node.func)
            if call.startswith("subprocess.") and _call_has_shell_true(node):
                violations.append(f"{rel}:{node.lineno}:{call}")

    assert violations == []


def test_product_workflows_do_not_bypass_tool_broker_with_direct_sandbox_execution() -> None:
    violations: list[str] = []

    for path in _product_python_files():
        rel = _relative(path)
        source = path.read_text(encoding="utf-8")
        if rel in APPROVED_SANDBOX_EXECUTION_FILES:
            continue
        if "RestrictedSubprocessSandbox" in source and (".execute(" in source or ".execute_with_input(" in source):
            violations.append(rel)

    assert violations == []


def test_product_workflows_do_not_call_runtime_registry_directly() -> None:
    violations: list[str] = []

    for path in _product_python_files():
        rel = _relative(path)
        source = path.read_text(encoding="utf-8")
        if rel in APPROVED_RUNTIME_FACTORY_FILES:
            continue
        if "runtime_for(" in source:
            violations.append(rel)

    assert violations == []


def test_security_policy_documents_required_execution_chain() -> None:
    policy = (ROOT / "docs" / "security-policy.md").read_text(encoding="utf-8")

    assert "ToolBroker -> PolicyEngine -> Approval/Grant -> RuntimeAdapter -> Evidence" in policy
    assert "command string" in policy
    assert "shell=True" in policy
    assert "cwd outside the workspace" in policy
    assert "arbitrary mounts" in policy


def test_issue_to_patch_runtime_policy_operation_is_runner_scoped() -> None:
    decision = evaluate_action(
        {
            "tool": "shell",
            "command": "python -c pass",
            "operation": "issue_to_patch_runtime",
            "permissionProfile": "dev_safe",
            "agentId": "generic_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "workflowKind": "issue_to_patch",
            "runtimeId": "codex_cli",
        }
    )

    assert decision["decision"] == "deny"
    assert "workflow runner" in decision["reason"]


def test_developer_agent_runtime_policy_operation_is_agent_scoped() -> None:
    decision = evaluate_action(
        {
            "tool": "shell",
            "command": "python -c pass",
            "operation": "developer_agent_runtime",
            "permissionProfile": "dev_safe",
            "agentId": "generic_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "codex_cli",
        }
    )

    assert decision["decision"] == "deny"
    assert "DeveloperAgent" in decision["reason"]


def test_developer_agent_runtime_policy_allows_only_scoped_cli_runtime() -> None:
    decision = evaluate_action(
        {
            "tool": "shell",
            "command": "python -c pass",
            "operation": "developer_agent_runtime",
            "permissionProfile": "dev_safe",
            "agentId": "developer_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "codex_cli",
            "agentRunId": "agent-run-test",
        }
    )

    assert decision["decision"] == "allow"
    assert "DeveloperAgent CLI runtime" in decision["reason"]


def test_qa_agent_command_policy_is_agent_scoped() -> None:
    decision = evaluate_action(
        {
            "tool": "shell",
            "command": "python --version",
            "operation": "qa_agent_command",
            "permissionProfile": "qa",
            "agentId": "developer_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "agentRunId": "agent-run-test",
        }
    )

    assert decision["decision"] == "deny"
    assert "QAAgent" in decision["reason"]


def test_qa_agent_command_policy_allows_only_low_risk_qa_commands() -> None:
    allowed = evaluate_action(
        {
            "tool": "shell",
            "command": "python --version",
            "operation": "qa_agent_command",
            "permissionProfile": "qa",
            "agentId": "qa_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "agentRunId": "agent-run-test",
        }
    )
    gated = evaluate_action(
        {
            "tool": "shell",
            "command": "python -c pass",
            "operation": "qa_agent_command",
            "permissionProfile": "qa",
            "agentId": "qa_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "agentRunId": "agent-run-test",
        }
    )

    assert allowed["decision"] == "allow"
    assert "qa_agent_command" in allowed["categories"]
    assert gated["decision"] == "requires_approval"


def test_qa_agent_runner_uses_broker_not_direct_subprocess() -> None:
    source = (PRODUCT_ROOT / "agents" / "qa_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert ".chat_completion(" not in source


def test_developer_agent_runner_uses_broker_not_direct_runtime_execution() -> None:
    source = (PRODUCT_ROOT / "agents" / "developer_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "build_developer_agent_argv" in source
    assert "CliRuntime" not in source
    assert "RuntimeAdapterRegistry(" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert ".chat_completion(" not in source
