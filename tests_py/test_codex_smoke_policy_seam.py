"""Exercise the real runtime/security seam; only native transport is replaced."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("approved", [False, True])
def test_authorized_smoke_reaches_transport_without_opening_generic_plan_shell(
    tmp_path, monkeypatch, approved
):
    from local_control_center.agents import codex_compatibility, codex_smoke, runtime_registry
    from local_control_center.agents.cli_runtimes.base import RuntimeRequest
    from local_control_center.agents.provider_accounts import ProviderAccountStore
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
    from local_control_center.security_policy import sandbox
    from tests_py.test_model_runtime_gateway import register_workspace

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"test executable; never launched")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private_root = tmp_path / "private"
    private_home = private_root / "one"
    private_home.mkdir(parents=True)
    environment = {"CODEX_HOME": str(private_home)}
    monkeypatch.setattr(runtime_registry, "_product_owner_codex_home_root", lambda: private_root)

    @contextmanager
    def isolated():
        yield environment

    monkeypatch.setattr(codex_smoke, "isolated_product_owner_codex_environment", isolated)
    monkeypatch.setattr(
        codex_compatibility,
        "run_probe_command",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="codex-cli 0.149.0"
            if argv[-1] == "--version"
            else " ".join(codex_compatibility.REQUIRED_FLAGS),
        ),
    )
    monkeypatch.setattr(
        codex_smoke.CodexCliRuntime,
        "validate_native_auth",
        lambda self: SimpleNamespace(status="authenticated"),
    )
    calls = []
    monkeypatch.setattr(
        sandbox.RestrictedSubprocessSandbox,
        "execute",
        lambda self, **kwargs: calls.append(kwargs) or {"returnCode": 0, "stdout": ""},
    )
    try:
        conn = runtime.connection
        register_workspace(conn, "smoke", workspace)
        RuntimeConfigRepository(conn).upsert_installation(
            {"runtimeId": "codex_cli", "enabled": True, "executablePath": str(binary)}
        )
        conn.execute("UPDATE provider_accounts SET enabled=1 WHERE provider_id='codex_cli'")
        conn.execute("UPDATE runtime_accounts SET enabled=1 WHERE runtime_id='codex_cli'")
        ProviderAccountStore(conn).upsert_model(
            {
                "providerId": "codex_cli",
                "model": "test-current",
                "displayName": "test",
                "source": "manual",
                "enabled": True,
            }
        )
        result = codex_smoke.run_codex_smoke(
            conn,
            codex_smoke.CodexSmokeRequest(
                workspaceId="smoke", approved=approved, reason="Owner-authorized test"
            ),
        )
        assert len(calls) == int(approved), result
        # A transport double is never sufficient to validate a real smoke receipt.
        assert result["status"] != "validated"
        request = RuntimeRequest(
            runtime="codex_cli",
            workspaceId="smoke",
            workspacePath=str(workspace),
            prompt=f"Respond with exactly {codex_smoke.SMOKE_MARKER}. Do not call tools or modify files.",
            model="test-current",
            role="product_owner",
            envPolicy={
                "permissionProfile": "plan",
                "operation": "codex_compatibility_smoke",
                "approved": True,
            },
            extraArgs=[*runtime_registry.PRODUCT_OWNER_CODEX_EXTRA_ARGS, "--json"],
        )
        cli = codex_smoke.CodexCliRuntime(executable=str(binary), connection=conn)
        generic = cli.run(request, trusted_subprocess_environment=environment)
        assert generic.status == "blocked" and "Plan profile" in generic.error
        assert len(calls) == int(approved)
        if approved:
            approval_id = result["approvalAuditId"]
            canonical = request.model_copy(
                update={"env_policy": {"permissionProfile": "plan", "network": False, "secrets": False}}
            )
            # A claimed approval cannot launch a second CLI, even with the same exact command.
            replay = cli.run(
                canonical, trusted_subprocess_environment=environment, trusted_smoke_audit_id=approval_id
            )
            assert replay.status == "blocked" and len(calls) == 1
            for changed in [
                canonical.model_copy(update={"prompt": "Do something else"}),
                canonical.model_copy(update={"env_policy": {"permissionProfile": "dev_safe"}}),
            ]:
                denied = cli.run(
                    changed, trusted_subprocess_environment=environment, trusted_smoke_audit_id=approval_id
                )
                assert denied.status == "blocked" and len(calls) == 1
            unisolated = cli.run(canonical, trusted_smoke_audit_id=approval_id)
            assert unisolated.status == "blocked" and len(calls) == 1
    finally:
        runtime.close()


def test_generic_tool_broker_cannot_claim_smoke_operation():
    from local_control_center.agents.tool_broker import _product_owner_internal_boundary

    for agent_id in ["product_owner_agent", "developer_agent", "custom"]:
        result = _product_owner_internal_boundary(
            operation="codex_compatibility_smoke",
            trusted_operation="codex_compatibility_smoke",
            agent_profile_id=agent_id,
            tool_call={"smokeApproved": True},
            workspace_path="fixture",
            trusted_subprocess_environment=None,
        )
        assert result["decision"] == "deny"


@pytest.mark.parametrize(
    "field,value",
    [
        ("smokeApproved", False),
        ("tool", "ollama"),
        ("permissionProfile", "dev_safe"),
        ("runtimeId", "claude_code_cli"),
        ("workspaceId", None),
        ("networkRequired", True),
        ("secretsRequired", True),
    ],
)
def test_smoke_policy_fails_closed_on_mismatched_context(field, value):
    from local_control_center.security_policy.policy_engine import evaluate_action

    payload = {
        "operation": "codex_compatibility_smoke",
        "smokeApproved": True,
        "tool": "shell",
        "permissionProfile": "plan",
        "runtimeId": "codex_cli",
        "workspaceId": "fixture",
        "workspacePath": ".",
        "path": ".",
    }
    assert evaluate_action({**payload, field: value})["decision"] == "deny"
