"""The developer CLI must use the explicitly requested model, not a profile default."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py import test_developer_agent_real_runtime as developer_fixtures

create_developer_client = developer_fixtures.create_client


@pytest.mark.usefixtures("controlled_domain_host")
@pytest.mark.parametrize(
    "mode", ["accept", "missing_patch", "failed_qa", "wrong_scope", "audit_failure", "deny"]
)
def test_patch_acceptance_completes_existing_run_without_requeue_or_new_runtime(
    create_developer_client, tmp_path, monkeypatch, mode
):
    import sys

    store, client, headers = create_developer_client(tmp_path, monkeypatch)
    project = developer_fixtures.create_git_project(store, tmp_path, name="Patch Acceptance")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="acceptance",
        agent_id="developer_agent",
        reason="test",
        isolation_type="git_worktree",
    )
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: developer_fixtures.controlled_developer_runtime_status(),
    )
    run = client.post(
        "/api/v1/agents/developer/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "acceptance",
            "instruction": "Synthetic fixture only",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "requireApproval": True,
        },
    ).json()
    assert run["status"] == "evidence_ready"
    action = next(
        a
        for a in store.jobs.list_action_requests(run["job"]["id"])
        if a["actionType"] == "agent.developer.approve_patch"
    )
    count = len(store.agents.list_agent_tool_calls())
    evidence_id = run["evidencePackage"]["id"]
    if mode == "missing_patch":
        store.evidence.update_evidence_links(evidence_id, hashes={})
    elif mode == "failed_qa":
        results = run["evidencePackage"]["testResults"]
        results[0]["exitCode"] = 1
        store.connection.execute(
            "UPDATE evidence_packages SET test_results=? WHERE id=?", (json.dumps(results), evidence_id)
        )
    elif mode == "wrong_scope":
        store.connection.execute("UPDATE evidence_packages SET workspace_id=NULL WHERE id=?", (evidence_id,))
    elif mode == "audit_failure":

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("injected durable audit failure")

        monkeypatch.setattr(
            "local_control_center.jobs_approvals.repository.JobsRepository.record_audit", fail_audit
        )
        with pytest.raises(RuntimeError, match="injected durable audit failure"):
            store.jobs.approve_action(run["job"]["id"], action["id"], reason="Fixture acceptance")
        assert store.jobs.get_action_request(action["id"])["status"] == "pending"
        assert store.jobs.get_job(run["job"]["id"])["status"] == "approval_required"
        assert store.agents.get_agent_run(run["agentRun"]["id"])["status"] == "awaiting_permission"
        assert store.evidence.get_evidence_package(evidence_id)["qaVerdict"] == "needs_human_review"
        return
    if mode == "deny":
        rejected = client.post(
            f"/api/v1/jobs/{run['job']['id']}/actions/{action['id']}/deny",
            headers=headers,
            json={"reason": "Fixture patch rejected"},
        )
        assert rejected.status_code == 202
        assert rejected.json()["job"]["status"] == "cancelled"
        assert store.agents.get_agent_run(run["agentRun"]["id"])["status"] == "cancelled"
        assert len(store.agents.list_agent_tool_calls()) == count
        return
    response = client.post(
        f"/api/v1/jobs/{run['job']['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": "Accept inspected fixture patch and real QA"},
    )
    if mode != "accept":
        assert response.status_code == 409
        assert store.jobs.get_action_request(action["id"])["status"] == "pending"
        assert store.jobs.get_job(run["job"]["id"])["status"] == "approval_required"
        assert len(store.agents.list_agent_tool_calls()) == count
        return
    assert response.status_code == 202
    assert response.json()["job"]["status"] == "completed"
    assert store.agents.get_agent_run(run["agentRun"]["id"])["status"] == "completed"
    assert len(store.agents.list_agent_tool_calls()) == count
    assert not store.jobs._pending_actions(run["job"]["id"])
    from local_control_center.evidence.quality import evidence_has_real_qa_pass

    assert evidence_has_real_qa_pass(store.evidence.get_evidence_package(evidence_id))
    assert response.json()["permissionGrant"] is None  # accepting output grants no future execution
    duplicate = client.post(
        f"/api/v1/jobs/{run['job']['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": "Duplicate fixture acceptance"},
    )
    assert duplicate.status_code == 409
    assert store.jobs.get_job(run["job"]["id"])["status"] == "completed"


class CapturedCliTransport:
    """No subprocess: return the actual command assembled by the production runner."""

    def evaluate_tool_call(self, **kwargs):
        self.call = kwargs
        call = kwargs["tool_call"]
        return {
            "toolCall": {
                "id": "test-transport",
                "status": "completed",
                "payload": {
                    "executionResult": {
                        "returnCode": 0,
                        "stdout": json.dumps(call["argv"]),
                    }
                },
            }
        }


@pytest.mark.parametrize("runtime_id", ["codex_cli", "claude_code_cli"])
@pytest.mark.parametrize("requested", ["explicit-model", None])
def test_developer_cli_preserves_requested_model_without_changing_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_id: str, requested: str | None
) -> None:
    module = "codex_cli" if runtime_id == "codex_cli" else "claude_code_cli"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "synthetic-auth-source"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-user-data"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        f"local_control_center.agents.cli_runtimes.{module}.resolve_model_alias",
        lambda *_args, **_kwargs: "profile-default-model",
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Model selection", path=tmp_path / "project"
        )
        workspace = WorkspacesRepository(connection, root=tmp_path).allocate_workspace(
            project_id=project["id"], task_id="selection", agent_id="developer_agent", reason="test"
        )
        binary = "codex.exe" if runtime_id == "codex_cli" else "claude.exe"
        broker = CapturedCliTransport()
        result = DeveloperAgentRunner(connection, root=tmp_path)._execute_cli_runtime(
            payload={"projectId": project["id"], "instruction": "Test command selection", "model": requested},
            runtime={"id": runtime_id, "detectedCommand": str(tmp_path / binary)},
            workspace=workspace,
            agent_run={"id": "test-agent-run"},
            job={"id": "test-job"},
            profile={},
            broker=broker,
        )
        argv = json.loads(result["stdout"])
        assert argv[argv.index("--model") + 1] == (requested or "profile-default-model")
        if runtime_id == "codex_cli":
            assert broker.call.get("trusted_operation") == "developer_agent_runtime"
            environment = broker.call.get("trusted_subprocess_environment") or {}
            assert "CODEX_HOME" in environment
            assert not Path(environment["CODEX_HOME"]).exists()  # isolated home cleaned after call
            assert "OPENAI_API_KEY" not in environment


def test_developer_codex_has_explicit_isolation_and_native_write_contract(tmp_path, monkeypatch):
    from local_control_center.agents.runtime_registry import build_developer_agent_argv

    monkeypatch.setattr("local_control_center.agents.runtime_registry.sys.platform", "win32")
    argv = build_developer_agent_argv(
        runtime={"id": "codex_cli", "detectedCommand": "codex.exe"},
        workspace_id="test",
        workspace_path=str(tmp_path),
        instruction="test",
        qa_commands=[],
        agent_id="developer_agent",
        connection=None,
        model="explicit-model",
    )
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--ignore-user-config" in argv
    assert 'windows.sandbox="unelevated"' in argv
    assert "mcp_servers={}" in argv
    assert "skills.include_instructions=false" in argv
    assert "project_doc_max_bytes=0" in argv
    assert 'shell_environment_policy.inherit="core"' in argv
    for feature in ("plugins", "multi_agent", "apps", "hooks", "memories"):
        assert any(argv[i : i + 2] == ["--disable", feature] for i in range(len(argv)))
    assert "--ignore-rules" not in argv  # Managed/command policy is not bypassed.
    assert not any(argv[i : i + 2] == ["--disable", "shell_tool"] for i in range(len(argv)))


@pytest.mark.parametrize("module, expected", [("unittest", "test"), ("pytest", "test"), ("subprocess", None)])
def test_qa_python_test_runner_is_not_generic_module_execution(module, expected):
    from local_control_center.security_policy.permissions import ParsedCommand, low_risk_shell_category

    assert (
        low_risk_shell_category(ParsedCommand("python.exe", ("-m", module, "discover", "-s", "tests", "-v")))
        == expected
    )


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "untrusted",
        "no-environment",
        "foreign-home",
        "outside",
        "bypass",
        "duplicate",
        "plan",
        "other-role",
    ],
)
def test_developer_native_boundary_requires_exact_contract_and_isolated_environment(
    tmp_path, monkeypatch, change
):
    from local_control_center.agents.runtime_registry import (
        build_developer_agent_argv,
        isolated_product_owner_codex_environment,
    )
    from local_control_center.agents.tool_broker import _developer_codex_boundary

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "synthetic-source"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-local"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    argv = build_developer_agent_argv(
        runtime={"id": "codex_cli", "detectedCommand": "codex.exe"},
        workspace_id="w",
        workspace_path=str(workspace),
        instruction="synthetic",
        qa_commands=[],
        agent_id="developer_agent",
        connection=None,
        model="explicit-model",
    )
    profile = {"id": "developer_agent", "permissionProfile": "dev_safe"}
    with isolated_product_owner_codex_environment() as environment:
        trusted = "developer_agent_runtime"
        if change == "untrusted":
            trusted = None
        if change == "no-environment":
            environment = None
        if change == "foreign-home":
            environment = {**environment, "CODEX_HOME": str(workspace)}
        if change == "outside":
            argv[argv.index("--cd") + 1] = str(tmp_path)
        if change == "bypass":
            argv.insert(-2, "--dangerously-bypass-approvals-and-sandbox")
        if change == "duplicate":
            argv[1:1] = ["--sandbox", "workspace-write"]
        if change == "plan":
            profile["permissionProfile"] = "plan"
        if change == "other-role":
            profile["id"] = "product_owner_agent"
        result = _developer_codex_boundary(
            operation="developer_agent_runtime",
            trusted_operation=trusted,
            profile=profile,
            tool_call={"tool": "shell", "runtimeId": "codex_cli", "argv": argv},
            workspace_path=str(workspace),
            environment=environment,
        )
        if change == "none":
            assert result is None
        else:
            assert result["decision"] == "deny"
