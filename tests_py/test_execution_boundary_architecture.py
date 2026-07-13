from __future__ import annotations

import ast
from pathlib import Path

from local_control_center.security_policy.policy_engine import evaluate_action

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT / "local_control_center"

APPROVED_SUBPROCESS_FILES = {
    "local_control_center/integrations/mcp_gateway.py",
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
        if "RestrictedSubprocessSandbox" in source and (
            ".execute(" in source or ".execute_with_input(" in source
        ):
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


def test_security_agent_scanner_policy_allows_only_local_scanner_executables() -> None:
    allowed = evaluate_action(
        {
            "tool": "shell",
            "command": "gitleaks dir . --report-format json",
            "commandArgv": ["gitleaks", "dir", "."],
            "operation": "security_agent_scanner",
            "permissionProfile": "qa",
            "agentId": "security_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "gitleaks",
            "agentRunId": "agent-run-test",
            "networkRequired": False,
            "secretsRequired": False,
        }
    )
    denied = evaluate_action(
        {
            "tool": "shell",
            "command": "python -c pass",
            "commandArgv": ["python", "-c", "pass"],
            "operation": "security_agent_scanner",
            "permissionProfile": "qa",
            "agentId": "security_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "gitleaks",
            "agentRunId": "agent-run-test",
            "networkRequired": False,
            "secretsRequired": False,
        }
    )

    assert allowed["decision"] == "allow"
    assert "security_agent_scanner" in allowed["categories"]
    assert denied["decision"] == "deny"
    assert "security_agent_scanner_executable_denied" in denied["categories"]


def test_git_workspace_policy_allows_only_contextual_patch_apply() -> None:
    base_payload = {
        "tool": "shell",
        "command": "git apply --check H:\\artifact.patch",
        "commandArgv": ["git", "apply", "--check", "H:\\artifact.patch"],
        "operation": "git_workspace_command",
        "permissionProfile": "dev_safe",
        "agentId": "git_workspace_agent",
        "workspaceId": "workspace-test",
        "workspacePath": "H:\\workspace",
        "path": "H:\\workspace",
        "agentRunId": "agent-run-test",
        "networkRequired": False,
        "secretsRequired": False,
    }
    allowed_check = evaluate_action({**base_payload, "gitOperation": "apply_check"})
    allowed_apply = evaluate_action(
        {
            **base_payload,
            "command": "git apply H:\\artifact.patch",
            "commandArgv": ["git", "apply", "H:\\artifact.patch"],
            "gitOperation": "apply_patch",
        }
    )
    missing_context = evaluate_action(base_payload)
    wrong_agent = evaluate_action(
        {**base_payload, "gitOperation": "apply_check", "agentId": "developer_agent"}
    )

    assert allowed_check["decision"] == "allow"
    assert "git_apply_check" in allowed_check["categories"]
    assert allowed_apply["decision"] == "allow"
    assert "git_apply_patch" in allowed_apply["categories"]
    assert missing_context["decision"] == "deny"
    assert "git_workspace_apply_denied" in missing_context["categories"]
    assert wrong_agent["decision"] == "deny"
    assert "git_workspace_agent_denied" in wrong_agent["categories"]


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


def test_devops_agent_command_policy_is_agent_scoped() -> None:
    decision = evaluate_action(
        {
            "tool": "shell",
            "command": "corepack pnpm@10.24.0 run build",
            "operation": "devops_agent_command",
            "permissionProfile": "qa",
            "agentId": "qa_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "agentRunId": "agent-run-test",
        }
    )

    assert decision["decision"] == "deny"
    assert "DevOpsAgent" in decision["reason"]


def test_devops_agent_command_policy_allows_only_low_risk_local_validation() -> None:
    allowed = evaluate_action(
        {
            "tool": "shell",
            "command": "corepack pnpm@10.24.0 run build",
            "operation": "devops_agent_command",
            "permissionProfile": "qa",
            "agentId": "devops_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "agentRunId": "agent-run-test",
        }
    )
    gated = evaluate_action(
        {
            "tool": "shell",
            "command": "corepack pnpm@10.24.0 run deploy",
            "operation": "devops_agent_command",
            "permissionProfile": "qa",
            "agentId": "devops_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "agentRunId": "agent-run-test",
        }
    )

    assert allowed["decision"] == "allow"
    assert "devops_agent_command" in allowed["categories"]
    assert gated["decision"] == "requires_approval"


def test_architect_agent_model_call_policy_is_agent_scoped() -> None:
    decision = evaluate_action(
        {
            "tool": "openai_compatible",
            "operation": "architect_agent_model_call",
            "permissionProfile": "plan",
            "agentId": "developer_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "openai_compatible",
            "agentRunId": "agent-run-test",
        }
    )

    assert decision["decision"] == "deny"
    assert "ArchitectAgent" in decision["reason"]


def test_architect_agent_model_call_policy_allows_only_scoped_model_runtime() -> None:
    allowed = evaluate_action(
        {
            "tool": "openai_compatible",
            "operation": "architect_agent_model_call",
            "permissionProfile": "plan",
            "agentId": "architect_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "openai_compatible",
            "agentRunId": "agent-run-test",
        }
    )
    denied = evaluate_action(
        {
            "tool": "shell",
            "operation": "architect_agent_model_call",
            "permissionProfile": "plan",
            "agentId": "architect_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "codex_cli",
            "agentRunId": "agent-run-test",
        }
    )

    assert allowed["decision"] == "allow"
    assert "architect_agent_model_call" in allowed["categories"]
    assert denied["decision"] == "deny"


def test_security_agent_model_call_policy_is_agent_scoped() -> None:
    decision = evaluate_action(
        {
            "tool": "openai_compatible",
            "operation": "security_agent_model_call",
            "permissionProfile": "qa",
            "agentId": "developer_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "openai_compatible",
            "agentRunId": "agent-run-test",
        }
    )

    assert decision["decision"] == "deny"
    assert "SecurityAgent" in decision["reason"]


def test_security_agent_model_call_policy_allows_only_scoped_model_runtime() -> None:
    allowed = evaluate_action(
        {
            "tool": "openai_compatible",
            "operation": "security_agent_model_call",
            "permissionProfile": "qa",
            "agentId": "security_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "openai_compatible",
            "agentRunId": "agent-run-test",
        }
    )
    denied = evaluate_action(
        {
            "tool": "shell",
            "operation": "security_agent_model_call",
            "permissionProfile": "qa",
            "agentId": "security_agent",
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "codex_cli",
            "agentRunId": "agent-run-test",
        }
    )

    assert allowed["decision"] == "allow"
    assert "security_agent_model_call" in allowed["categories"]
    assert denied["decision"] == "deny"


def test_model_agent_policies_allow_configured_remote_runtime_adapters() -> None:
    cases = [
        ("developer_agent_model_call", "developer_agent", "dev_safe"),
        ("architect_agent_model_call", "architect_agent", "plan"),
        ("security_agent_model_call", "security_agent", "qa"),
    ]
    for operation, agent_id, permission_profile in cases:
        for runtime_id in ("openrouter", "nvidia_nim", "anthropic_api"):
            decision = evaluate_action(
                {
                    "tool": runtime_id,
                    "operation": operation,
                    "permissionProfile": permission_profile,
                    "agentId": agent_id,
                    "workspaceId": "workspace-test",
                    "workspacePath": "H:\\workspace",
                    "path": "H:\\workspace",
                    "runtimeId": runtime_id,
                    "agentRunId": "agent-run-test",
                    "networkRequired": True,
                }
            )
            assert decision["decision"] == "allow", (operation, runtime_id, decision)


def test_product_owner_and_developer_policies_accept_only_verified_named_ollama_binding() -> None:
    cases = [
        ("product_owner_model_call", "product_owner_agent", "plan"),
        ("developer_agent_model_call", "developer_agent", "dev_safe"),
    ]
    for operation, agent_id, permission_profile in cases:
        common = {
            "tool": "ollama",
            "operation": operation,
            "permissionProfile": permission_profile,
            "agentId": agent_id,
            "workspaceId": "workspace-test",
            "workspacePath": "H:\\workspace",
            "path": "H:\\workspace",
            "runtimeId": "team_ollama",
            "agentRunId": "agent-run-test",
            "providerFamily": "ollama",
        }

        allowed = evaluate_action({**common, "providerId": "team_ollama"})
        mismatched = evaluate_action({**common, "providerId": "another_provider"})
        unverified = evaluate_action({**common, "providerId": "team_ollama", "providerFamily": None})

        assert allowed["decision"] == "allow"
        assert mismatched["decision"] == "deny"
        assert unverified["decision"] == "deny"


def test_qa_agent_runner_uses_broker_not_direct_subprocess() -> None:
    source = (PRODUCT_ROOT / "agents" / "qa_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert ".chat_completion(" not in source


def test_cli_session_stream_does_not_bypass_broker_for_git_state() -> None:
    source = (PRODUCT_ROOT / "agents" / "cli_session_stream.py").read_text(encoding="utf-8")

    assert "run_brokered_git" in source
    assert "run_git(" not in source


def test_architect_agent_runner_uses_broker_not_direct_model_execution() -> None:
    source = (PRODUCT_ROOT / "agents" / "architect_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert "RuntimeAdapterRegistry(" not in source
    assert ".chat_completion(" not in source
    assert "urlopen" not in source


def test_devops_agent_runner_uses_broker_not_direct_subprocess_or_docker_execution() -> None:
    source = (PRODUCT_ROOT / "agents" / "devops_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "devops_agent_command" in source
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert ".execute(" not in source
    assert ".execute_with_input(" not in source
    assert ".chat_completion(" not in source
    assert "urlopen" not in source


def test_security_agent_runner_uses_deterministic_checks_and_brokered_optional_model_analysis() -> None:
    source = (PRODUCT_ROOT / "agents" / "security_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "SECRET_PATTERNS" in source
    assert "dependency_file_scan" in source
    assert "path_traversal" in source
    assert "dangerous_command" in source
    assert "policy_violation" in source
    assert "deterministic_controls_available" in source
    assert '"available": True' not in source
    assert '"executable": True' not in source
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "os.system" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert "RuntimeAdapterRegistry(" not in source
    assert ".chat_completion(" not in source
    assert "urlopen" not in source


def test_developer_agent_runner_uses_broker_not_direct_runtime_execution() -> None:
    source = (PRODUCT_ROOT / "agents" / "developer_agent.py").read_text(encoding="utf-8")

    assert "ToolBroker(" in source
    assert "build_developer_agent_argv" in source
    assert "CliRuntime" not in source
    assert "RuntimeAdapterRegistry(" not in source
    assert "RestrictedSubprocessSandbox" not in source
    assert ".chat_completion(" not in source


def test_issue_to_patch_delegates_implementation_execution_to_developer_agent() -> None:
    source = (PRODUCT_ROOT / "workflows" / "issue_to_patch_runner.py").read_text(encoding="utf-8")

    assert "DeveloperAgentRunner(" in source
    assert "build_issue_to_patch_argv" not in source
    assert '"operation": "issue_to_patch_runtime"' not in source
    assert "ToolBroker(" not in source


def test_issue_to_patch_does_not_recreate_developer_implementation_evidence() -> None:
    source = (PRODUCT_ROOT / "workflows" / "issue_to_patch_runner.py").read_text(encoding="utf-8")

    assert "def _complete_run_status(" not in source
    assert "def _write_patch_artifact(" not in source
    assert "patch_artifact = _write_patch_artifact(" not in source
    assert 'diff = capture_git_diff(Path(workspace["path"]))' not in source
