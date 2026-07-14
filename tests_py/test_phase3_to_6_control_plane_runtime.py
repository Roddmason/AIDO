from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.api import _create_execution_evidence
from local_control_center.agents.model_gateway import ModelGateway
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_registry import build_product_owner_agent_argv
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.security_policy import sandbox as sandbox_module
from local_control_center.security_policy.command_classifier import classify_command
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.security_policy.sandbox import (
    DockerSandbox,
    RestrictedSubprocessSandbox,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.evidence_helpers import real_qa_evidence_fields


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def allocate_test_workspace(
    connection, root: Path, project: dict[str, object], *, task_id: str
) -> dict[str, object]:
    return WorkspacesRepository(connection, root=root).allocate_workspace(
        project_id=str(project["id"]),
        task_id=task_id,
        agent_id="implementer",
    )


def create_sensitive_shell_approval(
    tmp_path: Path,
    monkeypatch,
    *,
    profile_id: str = "cli_contextual_approval",
    command: str = "pnpm add left-pad",
    argv: list[str] | None = None,
    runtime_id: str = "docker",
    evidence_refs: list[str] | None = None,
    diff_refs: list[dict[str, object]] | None = None,
) -> SimpleNamespace:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / f"{profile_id}-project"
    project = store.create_project(name=f"Approval {profile_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=f"{profile_id}-task",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    job = store.jobs.create_job(
        project_id=project["id"], kind="chat.route", payload={"prompt": "install package"}
    )["job"]
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": profile_id,
            "name": f"CLI {profile_id}",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    requested = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "jobId": job["id"],
            "agentProfileId": profile_id,
            "taskId": f"{profile_id}-request",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": command,
                        "argv": argv or ["pnpm", "add", "left-pad"],
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                        "runtimeId": runtime_id,
                        "sandbox": runtime_id,
                        "dockerImage": "node:22-alpine",
                        "execute": True,
                        "evidenceRefs": evidence_refs or ["evidence-approval-context"],
                        "diffRefs": diff_refs
                        or [{"kind": "git_patch", "artifactId": "artifact-diff-context"}],
                    }
                ]
            },
        },
        headers=headers,
    )
    assert requested.status_code == 202
    overview = client.get("/api/v1/overview").json()
    action = next(item for item in overview["actionRequests"] if item["jobId"] == job["id"])
    return SimpleNamespace(
        store=store,
        client=client,
        headers=headers,
        project=project,
        workspace=workspace,
        workspace_path=workspace_path,
        job=job,
        profile_id=profile_id,
        action=action,
    )


def test_phase3_to_6_schema_adds_workspaces_runtime_skills_and_evidence_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 3 in migrations
    assert {
        "workspaces",
        "workspace_allocations",
        "workspace_files",
        "workspace_sessions",
        "git_branches",
        "pull_requests",
        "skills",
        "skill_versions",
        "skill_bindings",
        "artifacts",
        "test_results",
        "qa_verdicts",
        "model_providers",
    } <= tables


def test_phase3_schema_upgrades_legacy_workspace_tables_before_indexes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        connection.executescript(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                owner_agent_id TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                isolation_type TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            );
            CREATE TABLE workspace_allocations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                released_at TEXT
            );
            """
        )

        initialize_platform_schema(connection)
        workspace_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
        }
        allocation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(workspace_allocations)").fetchall()
        }
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(workspaces)").fetchall()}

    assert "task_id" in workspace_columns
    assert "task_id" in allocation_columns
    assert "status" in allocation_columns
    assert "idx_workspaces_task_active" in indexes


def test_command_classifier_and_policy_engine_gate_sensitive_actions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Policy", path=tmp_path / "policy", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    destructive = classify_command("Remove-Item C:\\Users\\Rodd -Recurse -Force")
    assert destructive["riskLevel"] == "critical"
    assert "destructive_delete" in destructive["categories"]

    safe_test = classify_command("uv run pytest tests_py -q")
    assert safe_test["riskLevel"] == "low"
    assert "test" in safe_test["categories"]

    decision = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "role": "implementer",
            "tool": "shell",
            "command": "uv run pytest tests_py -q",
            "path": str(tmp_path / "policy"),
            "workspaceId": "workspace-safe",
        },
        headers=headers,
    )
    assert decision.status_code == 200
    assert decision.json()["decision"]["decision"] == "allow"

    denied = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "role": "implementer",
            "tool": "shell",
            "command": "Remove-Item C:\\Users\\Rodd -Recurse -Force",
            "path": "C:\\Users\\Rodd",
            "workspaceId": "workspace-unsafe",
        },
        headers=headers,
    )
    assert denied.status_code == 200
    assert denied.json()["decision"]["decision"] == "requires_human"
    assert denied.json()["decision"]["riskLevel"] == "critical"


def test_permission_profiles_apply_argument_level_shell_allowlists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    plan_denied = evaluate_action(
        {
            "permissionProfile": "plan",
            "role": "technical_lead",
            "tool": "shell",
            "command": "uv run pytest tests_py -q",
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert plan_denied["decision"] == "deny"
    assert "profile_shell_denied" in plan_denied["categories"]

    dev_safe_allowed = evaluate_action(
        {
            "permissionProfile": "dev_safe",
            "role": "implementer",
            "tool": "shell",
            "command": "corepack pnpm@10.24.0 run build:control-center",
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert dev_safe_allowed["decision"] == "allow"
    assert "allowlisted_build" in dev_safe_allowed["categories"]

    dev_safe_install = evaluate_action(
        {
            "permissionProfile": "dev_safe",
            "role": "implementer",
            "tool": "shell",
            "command": "pnpm add left-pad",
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert dev_safe_install["decision"] == "requires_approval"
    assert "install" in dev_safe_install["categories"]

    qa_build_allowed = evaluate_action(
        {
            "permissionProfile": "qa",
            "role": "qa_reviewer",
            "tool": "shell",
            "command": "corepack pnpm@10.24.0 run build:control-center",
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert qa_build_allowed["decision"] == "allow"
    assert "allowlisted_build" in qa_build_allowed["categories"]

    echoed_test_is_not_allowlisted = evaluate_action(
        {
            "permissionProfile": "dev_safe",
            "role": "implementer",
            "tool": "shell",
            "command": 'echo "uv run pytest tests_py -q"',
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert echoed_test_is_not_allowlisted["decision"] == "requires_approval"
    assert "unknown" in echoed_test_is_not_allowlisted["categories"]

    frontend_lint_allowed = evaluate_action(
        {
            "permissionProfile": "dev_safe",
            "role": "implementer",
            "tool": "shell",
            "command": "corepack pnpm@10.24.0 run lint:web",
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert frontend_lint_allowed["decision"] == "allow"
    assert "allowlisted_lint" in frontend_lint_allowed["categories"]

    package_script_hook_blocked = evaluate_action(
        {
            "permissionProfile": "dev_safe",
            "role": "implementer",
            "tool": "shell",
            "command": "pnpm run postinstall",
            "path": str(workspace),
            "workspacePath": str(workspace),
        }
    )
    assert package_script_hook_blocked["decision"] == "requires_approval"
    assert "package_script_hook" in package_script_hook_blocked["categories"]


@pytest.mark.parametrize(
    "tool",
    ["openai_compatible", "openrouter", "nvidia_nim", "anthropic_api", "workspace_patch"],
)
def test_plan_profile_denies_unscoped_execution_adapters_without_command(tool: str) -> None:
    result = evaluate_action(
        {
            "permissionProfile": "plan",
            "role": "product_owner",
            "agentId": "generic-plan-agent",
            "agentRunId": "agent-run-plan-adapter",
            "tool": tool,
            "command": "",
            "runtimeId": tool,
            "providerId": tool,
            "workspaceId": "workspace-plan-adapter",
            "workspacePath": "H:/workspace-plan-adapter",
        }
    )

    assert result["decision"] == "deny"
    assert "profile_runtime_adapter_denied" in result["categories"]


def test_cli_agent_tool_calls_go_through_policy_and_create_action_requests(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "runtime-project"
    project = store.create_project(name="Runtime", path=project_path, template_id="other")
    job = store.jobs.create_job(
        project_id=project["id"], kind="chat.route", payload={"prompt": "run install"}
    )["job"]
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    profile_response = client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_implementer",
            "name": "CLI Implementer",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    assert profile_response.status_code == 201

    run_response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "jobId": job["id"],
            "agentProfileId": "cli_implementer",
            "taskId": "install-check",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "path": str(project_path),
                    }
                ]
            },
        },
        headers=headers,
    )
    assert run_response.status_code == 202
    agent_run = run_response.json()["agentRun"]
    assert agent_run["status"] == "awaiting_permission"
    assert agent_run["output"]["verdict"] == "awaiting_permission"

    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["toolName"] == "shell"
    assert tool_call["status"] == "approval_required"
    assert tool_call["payload"]["permissionDecisionId"].startswith("permission-decision-")
    assert tool_call["payload"]["actionRequestId"].startswith("action-")
    assert any(
        action["jobId"] == job["id"] and action["actionType"] == "tool.call"
        for action in overview["actionRequests"]
    )


def test_allowed_cli_agent_tool_call_without_execution_does_not_complete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "safe-runtime"
    project = store.create_project(name="Safe Runtime", path=project_path, template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_safe",
            "name": "CLI Safe",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    run_response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "cli_safe",
            "taskId": "safe-test",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "uv run pytest tests_py -q",
                        "path": str(project_path),
                    }
                ]
            },
        },
        headers=headers,
    )
    assert run_response.status_code == 202
    agent_run = run_response.json()["agentRun"]
    assert agent_run["status"] == "failed"
    assert agent_run["output"]["verdict"] == "blocked"
    assert "not executed" in agent_run["output"]["summary"]
    assert agent_run["output"]["evidence_refs"] == []

    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["status"] == "allowed"
    assert tool_call["payload"]["execution"] == "not_executed"
    assert any(decision["decision"] == "allow" for decision in overview["permissionDecisions"])


def test_allowed_cli_tool_call_executes_only_structured_argv_in_restricted_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "sandbox-runtime"
    project = store.create_project(name="Sandbox Runtime", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="safe-version-check",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_sandbox",
            "name": "CLI Sandbox",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    run_response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "cli_sandbox",
            "taskId": "safe-version-check",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "argv": [sys.executable, "--version"],
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                        "execute": True,
                    }
                ]
            },
        },
        headers=headers,
    )
    assert run_response.status_code == 202
    agent_run = run_response.json()["agentRun"]
    assert agent_run["status"] == "completed"

    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["status"] == "completed"
    assert tool_call["payload"]["execution"] == "restricted_subprocess"
    assert tool_call["payload"]["executionResult"]["returnCode"] == 0
    assert "Python" in (
        tool_call["payload"]["executionResult"]["stdout"] + tool_call["payload"]["executionResult"]["stderr"]
    )


def test_allowed_cli_tool_call_can_execute_in_docker_and_records_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    executed: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        executed.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 12,
            "stdout": "Python 3.13.0\n",
            "stderr": "",
            "command": ["docker", "run", "--rm", "python:3.13-slim", "python", "--version"],
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "docker-runtime"
    project = store.create_project(name="Docker Runtime", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="docker-version-check",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_docker",
            "name": "CLI Docker",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    run_response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "cli_docker",
            "taskId": "docker-version-check",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "argv": ["python", "--version"],
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                        "execute": True,
                        "sandbox": "docker",
                        "dockerImage": "python:3.13-slim",
                    }
                ]
            },
        },
        headers=headers,
    )

    assert run_response.status_code == 202
    agent_run = run_response.json()["agentRun"]
    assert agent_run["status"] == "completed"
    assert executed and executed[0]["image"] == "python:3.13-slim"

    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["status"] == "completed"
    assert tool_call["payload"]["execution"] == "docker"
    assert tool_call["payload"]["executionResult"]["returnCode"] == 0
    assert agent_run["output"]["evidence_refs"]

    evidence = client.get("/api/v1/evidence").json()["evidencePackages"]
    package = next(item for item in evidence if item["id"] == agent_run["output"]["evidence_refs"][0])
    assert package["agentId"] == "cli_docker"
    assert package["taskId"] == "docker-version-check"
    assert package["qaVerdict"] == "evidence_collected"
    assert package["testResults"][0]["command"] == "python --version"
    assert package["testResults"][0]["execution"] == "docker"


def test_large_tool_execution_output_is_promoted_to_evidence_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    large_stdout = "\n".join(f"stdout line {index}" for index in range(2000))
    large_stderr = "\n".join(f"stderr line {index}" for index in range(2000))

    def fake_docker_execute(self, **kwargs):
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 25,
            "stdout": large_stdout,
            "stderr": large_stderr,
            "command": ["docker", "run", "--rm", "python:3.13-slim", "python", "--version"],
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project_path = tmp_path / "large-tool-output"
        project = ProjectsRepository(connection).create_project(
            name="Large Tool Output",
            path=project_path,
            template_id="other",
        )
        workspace = allocate_test_workspace(connection, tmp_path, project, task_id="large-output")
        workspace_path = str(workspace["path"])
        agents = AgentsRepository(connection)
        profile = agents.upsert_agent_profile(
            {
                "id": "cli_large_output",
                "name": "CLI Large Output",
                "role": "implementer",
                "runtimeMode": "cli",
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell"],
            }
        )
        run = agents.create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="large-output",
            input_payload={"toolCalls": []},
            output_payload={},
            status="running",
        )
        broker_results = ToolBroker(connection, artifact_root=tmp_path).evaluate_tool_calls(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_calls=[
                {
                    "tool": "shell",
                    "command": "python --version",
                    "argv": ["python", "--version"],
                    "workspaceId": workspace["id"],
                    "path": workspace_path,
                    "workspacePath": workspace_path,
                    "execute": True,
                    "sandbox": "docker",
                    "dockerImage": "python:3.13-slim",
                }
            ],
        )
        evidence_refs = _create_execution_evidence(
            platform=SimpleNamespace(connection=connection),
            project_id=project["id"],
            workflow_run_id=None,
            agent_id=profile["id"],
            task_id="large-output",
            tool_calls=[broker_results[0]["toolCall"]],
        )

        tool_call = broker_results[0]["toolCall"]
        assert tool_call["status"] == "completed"
        execution_result = tool_call["payload"]["executionResult"]
        assert "stdout" not in execution_result
        assert "stderr" not in execution_result
        assert execution_result["stdoutArtifactId"].startswith("artifact-")
        assert execution_result["stderrArtifactId"].startswith("artifact-")
        assert execution_result["stdoutSizeBytes"] == len(large_stdout.encode("utf-8"))
        assert execution_result["stderrSizeBytes"] == len(large_stderr.encode("utf-8"))

        evidence_id = evidence_refs[0]
        artifacts = EvidenceRepository(connection).list_artifacts(evidence_id)
        artifact_ids = {artifact["id"] for artifact in artifacts}
        assert {execution_result["stdoutArtifactId"], execution_result["stderrArtifactId"]} <= artifact_ids
        for artifact in artifacts:
            if artifact["id"] in {execution_result["stdoutArtifactId"], execution_result["stderrArtifactId"]}:
                assert artifact["kind"] == "execution_log"
                assert artifact["hash"]
                assert Path(artifact["path"]).exists()


def test_tool_execution_can_force_complete_stdout_into_an_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    execution_calls: list[dict[str, object]] = []
    stdout = '{"status":"completed"}'

    def fake_execute(self, **kwargs):
        execution_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 12,
            "stdout": stdout,
            "stderr": "",
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        fake_execute,
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Forced Output Artifact",
            path=tmp_path / "forced-output-artifact",
            template_id="other",
        )
        workspace = allocate_test_workspace(connection, tmp_path, project, task_id="forced-output")
        profile = AgentsRepository(connection).upsert_agent_profile(
            {
                "id": "cli_forced_output",
                "name": "CLI Forced Output",
                "role": "implementer",
                "runtimeMode": "cli",
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell"],
            }
        )
        run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="forced-output",
            input_payload={"toolCalls": []},
            output_payload={},
            status="running",
        )

        tool_call = ToolBroker(connection, artifact_root=tmp_path).evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={
                "tool": "shell",
                "command": "python --version",
                "argv": ["python", "--version"],
                "workspaceId": workspace["id"],
                "path": workspace["path"],
                "workspacePath": workspace["path"],
                "execute": True,
                "captureStdoutArtifact": True,
            },
        )["toolCall"]

        execution_result = tool_call["payload"]["executionResult"]
        artifact = EvidenceRepository(connection).get_artifact_by_id(
            execution_result["stdoutArtifactId"]
        )

    assert execution_calls[0]["truncate_output"] is False
    assert "stdout" not in execution_result
    assert execution_result["stdoutTruncated"] is False
    assert Path(artifact["path"]).read_text(encoding="utf-8") == stdout


def test_restricted_subprocess_complete_capture_is_bounded_and_reports_overflow(tmp_path: Path) -> None:
    emitted_bytes = 1_200_000
    result = RestrictedSubprocessSandbox().execute(
        argv=[
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.stdout.write('o' * {emitted_bytes}); "
                f"sys.stderr.write('e' * {emitted_bytes})"
            ),
        ],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=20,
        truncate_output=False,
    )

    assert result["returnCode"] == 0
    assert len(result["stdout"].encode("utf-8")) <= 1_048_576
    assert len(result["stderr"].encode("utf-8")) <= 1_048_576
    assert result["stdoutCaptureTruncated"] is True
    assert result["stderrCaptureTruncated"] is True
    assert result["stdoutTotalBytes"] == emitted_bytes
    assert result["stderrTotalBytes"] == emitted_bytes


def test_restricted_subprocess_complete_capture_preserves_output_below_limit(tmp_path: Path) -> None:
    emitted_bytes = 50_000
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "-c", f"import sys; sys.stdout.write('x' * {emitted_bytes})"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=20,
        truncate_output=False,
    )

    assert result["returnCode"] == 0
    assert result["stdout"] == "x" * emitted_bytes
    assert result["stdoutCaptureTruncated"] is False
    assert result["stdoutTotalBytes"] == emitted_bytes


def test_restricted_subprocess_timeout_preserves_bounded_stream_prefixes(tmp_path: Path) -> None:
    result = RestrictedSubprocessSandbox().execute(
        argv=[
            sys.executable,
            "-c",
            (
                "import sys,time; "
                "sys.stdout.write('stdout-before-timeout'); sys.stdout.flush(); "
                "sys.stderr.write('stderr-before-timeout'); sys.stderr.flush(); "
                "time.sleep(10)"
            ),
        ],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=1,
        truncate_output=False,
    )

    assert result["timedOut"] is True
    assert result["returnCode"] is None
    assert result["stdout"] == "stdout-before-timeout"
    assert result["stderr"] == "stderr-before-timeout"
    assert result["stdoutCaptureTruncated"] is False
    assert result["stderrCaptureTruncated"] is False


def test_restricted_subprocess_timeout_terminates_descendant_tree(tmp_path: Path) -> None:
    started = time.perf_counter()
    result = RestrictedSubprocessSandbox().execute(
        argv=[
            sys.executable,
            "-c",
            (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
                "print('parent-started', flush=True); "
                "time.sleep(10)"
            ),
        ],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=1,
        truncate_output=False,
    )
    elapsed_seconds = time.perf_counter() - started

    assert result["timedOut"] is True
    assert result["returnCode"] is None
    assert "parent-started" in result["stdout"]
    assert elapsed_seconds < 5


def test_generic_tool_call_cannot_claim_product_owner_internal_runtime_operation(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-be-created.txt"
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Product owner internal boundary",
            path=tmp_path / "product-owner-internal-boundary",
            template_id="other",
        )
        workspace = allocate_test_workspace(
            connection,
            tmp_path,
            project,
            task_id="product-owner-internal-boundary",
        )
        profile = AgentsRepository(connection).upsert_agent_profile(
            {
                "id": "product_owner_agent",
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": "hybrid",
                "permissionProfile": "plan",
                "allowedTools": ["shell"],
            }
        )
        run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="product-owner-internal-boundary",
            input_payload={"toolCalls": []},
            output_payload={},
            status="running",
        )

        result = ToolBroker(connection, artifact_root=tmp_path).evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={
                "tool": "shell",
                "command": "python -c write-marker",
                "argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')"],
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "product_owner_runtime",
                "runtimeId": "codex_cli",
                "providerTransportRequired": True,
                "execute": True,
            },
        )

    assert result["decision"]["decision"] == "deny"
    assert "product_owner_internal_operation_denied" in result["decision"]["payload"]["categories"]
    assert result["toolCall"]["status"] == "denied"
    assert marker.exists() is False


def test_generic_agent_run_cannot_use_product_owner_profile_model_adapters(tmp_path: Path) -> None:
    adapter_calls: list[dict[str, object]] = []

    class UnexpectedAdapter:
        def execute(self, **kwargs):
            adapter_calls.append(kwargs)
            raise AssertionError("Generic ProductOwnerAgent tool calls must not reach model adapters.")

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Product owner generic adapter boundary",
            path=tmp_path / "product-owner-generic-adapter-boundary",
            template_id="other",
        )
        workspace = allocate_test_workspace(
            connection,
            tmp_path,
            project,
            task_id="product-owner-generic-adapter-boundary",
        )
        profile = AgentsRepository(connection).upsert_agent_profile(
            {
                "id": "product_owner_agent",
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": "hybrid",
                "permissionProfile": "plan",
                "allowedTools": ["nvidia_nim"],
                "allowedProviders": ["nvidia_nim"],
                "allowedRuntimes": ["nvidia_nim"],
            }
        )
        run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="product-owner-generic-adapter-boundary",
            input_payload={},
            output_payload={},
            status="running",
        )

        result = ToolBroker(
            connection,
            artifact_root=tmp_path,
            runtime_adapters={"nvidia_nim": UnexpectedAdapter()},
        ).evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={
                "tool": "nvidia_nim",
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "runtimeId": "nvidia_nim",
                "input": {"providerId": "nvidia_nim", "model": "test-model", "messages": []},
                "execute": True,
            },
        )

    assert result["decision"]["decision"] == "deny"
    assert result["toolCall"]["status"] == "denied"
    assert "product_owner_generic_tool_call_denied" in result["decision"]["payload"][
        "categories"
    ]
    assert adapter_calls == []


def test_product_owner_runtime_must_match_persisted_resource_decision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    execution_calls: list[dict[str, object]] = []
    controlled_local_app_data = tmp_path / "local-app-data"
    controlled_codex_home = (
        controlled_local_app_data / "AIDO" / "product-owner-codex-homes" / "binding-test"
    )
    controlled_codex_home.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(controlled_local_app_data))
    trusted_environment = {"CODEX_HOME": str(controlled_codex_home)}

    def fake_execute(self, **kwargs):
        execution_calls.append(kwargs)
        return {"executed": True, "blocked": False, "returnCode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        fake_execute,
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Product owner resource binding",
            path=tmp_path / "product-owner-resource-binding",
            template_id="other",
        )
        workspace = allocate_test_workspace(
            connection,
            tmp_path,
            project,
            task_id="product-owner-resource-binding",
        )
        profile = AgentsRepository(connection).upsert_agent_profile(
            {
                "id": "product_owner_agent",
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": "hybrid",
                "permissionProfile": "plan",
                "allowedTools": ["shell"],
            }
        )
        run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="product-owner-resource-binding",
            input_payload={},
            output_payload={},
            workflow_run_id="product-loop-resource-binding",
            status="running",
        )
        resource_decision = {
            "routingDecisionId": "ai-routing-product-owner-binding",
            "selected": {"providerId": "codex_cli", "model": "gpt-5.5", "runtime": "cli"},
            "approvalRequired": False,
            "usageStatus": "not_executed",
            "policyResult": {},
        }
        AIResourceManager(connection)._record_routing_decision(
            request=AIResourceRequest(
                project_id=project["id"],
                workflow_run_id="product-loop-resource-binding",
                agent_id=profile["id"],
                task_id="product-owner-resource-binding",
                task_type="product_owner.discovery",
            ),
            decision=resource_decision,
        )
        command = build_product_owner_agent_argv(
            runtime={
                "id": "codex_cli",
                "detectedCommand": "codex",
                "version": "codex-cli 0.142.2",
                "versionVerified": True,
            },
            workspace_id=workspace["id"],
            workspace_path=workspace["path"],
            prompt="Return JSON only",
            model="gpt-5.6",
            agent_id=profile["id"],
            connection=connection,
        )

        tool_call = {
            "tool": "shell",
            "command": " ".join(command),
            "argv": command,
            "workspaceId": workspace["id"],
            "workspacePath": workspace["path"],
            "path": workspace["path"],
            "operation": "product_owner_runtime",
            "runtimeId": "codex_cli",
            "model": "gpt-5.6",
            "providerTransportRequired": True,
            "workspaceReadRequired": False,
            "networkRequired": False,
            "secretsRequired": False,
            "resourceDecisionId": resource_decision["routingDecisionId"],
            "execute": True,
        }
        broker = ToolBroker(connection, artifact_root=tmp_path)

        missing_decision_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call=tool_call,
            trusted_operation="product_owner_runtime",
            trusted_subprocess_environment=trusted_environment,
        )

        missing_environment_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call=tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
        )

        untrusted_environment_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call=tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment={
                **trusted_environment,
                "AIDO_PRODUCT_OWNER_SECRET_CANARY": "not-allowlisted",
            },
        )

        result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call=tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment=trusted_environment,
        )

        matching_command = build_product_owner_agent_argv(
            runtime={
                "id": "codex_cli",
                "detectedCommand": "codex",
                "version": "codex-cli 0.142.2",
                "versionVerified": True,
            },
            workspace_id=workspace["id"],
            workspace_path=workspace["path"],
            prompt="Return JSON only",
            model="gpt-5.5",
            agent_id=profile["id"],
            connection=connection,
        )
        matching_tool_call = {
            **tool_call,
            "command": " ".join(matching_command),
            "argv": matching_command,
            "model": "gpt-5.5",
        }
        network_capability_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={**matching_tool_call, "networkRequired": True},
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment=trusted_environment,
        )
        secret_capability_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={**matching_tool_call, "secretsRequired": True},
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment=trusted_environment,
        )
        matching_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call=matching_tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment=trusted_environment,
        )

        loop_repository = ProductLoopRepository(connection)
        loop = loop_repository.create_loop(
            {
                "projectId": project["id"],
                "title": "Approval-bound product owner run",
                "state": "resource_manager",
                "status": "active",
                "context": {},
            }
        )
        approval_decision = {
            **resource_decision,
            "routingDecisionId": "ai-routing-product-owner-approval",
            "approvalRequired": True,
        }
        AIResourceManager(connection)._record_routing_decision(
            request=AIResourceRequest(
                project_id=project["id"],
                workflow_run_id=loop["id"],
                agent_id=profile["id"],
                task_id="product-owner-resource-binding",
                task_type="product_owner.discovery",
            ),
            decision=approval_decision,
        )
        approval_run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="product-owner-resource-binding",
            input_payload={},
            output_payload={},
            workflow_run_id=loop["id"],
            status="running",
        )
        approval_tool_call = {
            **tool_call,
            "command": " ".join(matching_command),
            "argv": matching_command,
            "model": "gpt-5.5",
            "resourceDecisionId": approval_decision["routingDecisionId"],
        }
        missing_approval_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=approval_run["id"],
            agent_profile=profile,
            tool_call=approval_tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=approval_decision,
            trusted_subprocess_environment=trusted_environment,
        )
        loop_repository.update_loop_state(
            loop["id"],
            state="resource_manager",
            previous_state=None,
            status="active",
            context={
                "durableRun": {
                    "requestMeta": {
                        "approvedResourceSelections": [
                            {"role": "product_owner", **approval_decision["selected"]}
                        ]
                    }
                }
            },
            version=2,
        )
        approved_result = broker.evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=approval_run["id"],
            agent_profile=profile,
            tool_call=approval_tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=approval_decision,
            trusted_subprocess_environment=trusted_environment,
        )

    assert missing_decision_result["decision"]["decision"] == "deny"
    assert "product_owner_resource_decision_denied" in missing_decision_result["decision"]["payload"][
        "categories"
    ]
    assert missing_environment_result["decision"]["decision"] == "deny"
    assert "product_owner_runtime_environment_denied" in missing_environment_result["decision"][
        "payload"
    ]["categories"]
    assert untrusted_environment_result["decision"]["decision"] == "deny"
    assert "product_owner_runtime_environment_denied" in untrusted_environment_result["decision"][
        "payload"
    ]["categories"]
    assert result["decision"]["decision"] == "deny"
    assert "product_owner_resource_decision_denied" in result["decision"]["payload"]["categories"]
    assert result["toolCall"]["status"] == "denied"
    assert network_capability_result["decision"]["decision"] == "deny"
    assert secret_capability_result["decision"]["decision"] == "deny"
    assert "product_owner_capability_boundary_denied" in network_capability_result["decision"][
        "payload"
    ]["categories"]
    assert matching_result["decision"]["decision"] == "allow"
    assert matching_result["toolCall"]["status"] == "completed"
    assert missing_approval_result["decision"]["decision"] == "deny"
    assert approved_result["decision"]["decision"] == "allow"
    assert approved_result["toolCall"]["status"] == "completed"
    assert len(execution_calls) == 2


def test_product_owner_resource_decision_claim_is_atomic_across_agent_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "platform.sqlite"
    execution_started = threading.Event()
    release_execution = threading.Event()
    execution_calls: list[dict[str, object]] = []
    controlled_local_app_data = tmp_path / "local-app-data"
    controlled_codex_home = (
        controlled_local_app_data / "AIDO" / "product-owner-codex-homes" / "atomic-test"
    )
    controlled_codex_home.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(controlled_local_app_data))
    trusted_environment = {"CODEX_HOME": str(controlled_codex_home)}

    def blocking_execute(self, **kwargs):
        execution_calls.append(kwargs)
        execution_started.set()
        assert release_execution.wait(timeout=10)
        return {"executed": True, "blocked": False, "returnCode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        blocking_execute,
    )
    with open_sqlite_connection(db_path) as setup_connection:
        initialize_platform_schema(setup_connection)
        project = ProjectsRepository(setup_connection).create_project(
            name="Atomic Product Owner decision",
            path=tmp_path / "atomic-product-owner-decision",
            template_id="other",
        )
        workspace = allocate_test_workspace(
            setup_connection,
            tmp_path,
            project,
            task_id="atomic-product-owner-decision",
        )
        profile = AgentsRepository(setup_connection).upsert_agent_profile(
            {
                "id": "product_owner_agent",
                "name": "ProductOwnerAgent",
                "role": "product_owner",
                "runtimeMode": "hybrid",
                "permissionProfile": "plan",
                "allowedTools": ["shell"],
            }
        )
        runs = [
            AgentsRepository(setup_connection).create_agent_run(
                project_id=project["id"],
                agent_profile_id=profile["id"],
                task_id="atomic-product-owner-decision",
                input_payload={},
                output_payload={},
                workflow_run_id="product-loop-atomic-decision",
                status="running",
            )
            for _ in range(2)
        ]
        resource_decision = {
            "routingDecisionId": "ai-routing-product-owner-atomic",
            "selected": {"providerId": "codex_cli", "model": "gpt-5.5", "runtime": "cli"},
            "approvalRequired": False,
            "usageStatus": "not_executed",
            "policyResult": {},
        }
        AIResourceManager(setup_connection)._record_routing_decision(
            request=AIResourceRequest(
                project_id=project["id"],
                workflow_run_id="product-loop-atomic-decision",
                agent_id=profile["id"],
                task_id="atomic-product-owner-decision",
                task_type="product_owner.discovery",
            ),
            decision=resource_decision,
        )
        command = build_product_owner_agent_argv(
            runtime={
                "id": "codex_cli",
                "detectedCommand": "codex",
                "version": "codex-cli 0.142.2",
                "versionVerified": True,
            },
            workspace_id=workspace["id"],
            workspace_path=workspace["path"],
            prompt="Return JSON only",
            model="gpt-5.5",
            agent_id=profile["id"],
            connection=setup_connection,
        )
    tool_call = {
        "tool": "shell",
        "command": " ".join(command),
        "argv": command,
        "workspaceId": workspace["id"],
        "workspacePath": workspace["path"],
        "path": workspace["path"],
        "operation": "product_owner_runtime",
        "runtimeId": "codex_cli",
        "providerTransportRequired": True,
        "workspaceReadRequired": False,
        "networkRequired": False,
        "secretsRequired": False,
        "resourceDecisionId": resource_decision["routingDecisionId"],
        "execute": True,
    }

    with (
        open_sqlite_connection(db_path) as first_connection,
        open_sqlite_connection(db_path) as second_connection,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        first_future = executor.submit(
            ToolBroker(first_connection, artifact_root=tmp_path).evaluate_tool_call,
            project_id=project["id"],
            agent_run_id=runs[0]["id"],
            agent_profile=profile,
            tool_call=tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment=trusted_environment,
        )
        assert execution_started.wait(timeout=10)
        second_result = ToolBroker(second_connection, artifact_root=tmp_path).evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=runs[1]["id"],
            agent_profile=profile,
            tool_call=tool_call,
            trusted_operation="product_owner_runtime",
            trusted_resource_decision=resource_decision,
            trusted_subprocess_environment=trusted_environment,
        )
        release_execution.set()
        first_result = first_future.result(timeout=10)
        routing_row = second_connection.execute(
            "SELECT usage_status FROM ai_routing_decisions WHERE id = ?",
            (resource_decision["routingDecisionId"],),
        ).fetchone()

    assert first_result["decision"]["decision"] == "allow"
    assert first_result["toolCall"]["status"] == "completed"
    assert second_result["decision"]["decision"] == "deny"
    assert second_result["toolCall"]["status"] == "denied"
    assert "product_owner_resource_decision_replay_denied" in second_result["decision"]["payload"][
        "categories"
    ]
    assert routing_row is not None
    assert routing_row["usage_status"] == "completed"
    assert len(execution_calls) == 1


@pytest.mark.parametrize(
    "timeout_seconds",
    [0, 901],
)
def test_restricted_subprocess_rejects_timeout_outside_contract_before_launch(
    tmp_path: Path,
    monkeypatch,
    timeout_seconds: int,
) -> None:
    popen_calls = 0
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        nonlocal popen_calls
        popen_calls += 1
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.subprocess.Popen",
        recording_popen,
    )

    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "-c", "print('must-not-run')"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=timeout_seconds,
    )

    assert result["executed"] is False
    assert result["blocked"] is True
    assert sandbox_module.MAX_RESTRICTED_SUBPROCESS_TIMEOUT_SECONDS == 900
    assert result["reason"] == (
        "Restricted subprocess timeout_seconds must be an integer between 1 and "
        f"{sandbox_module.MAX_RESTRICTED_SUBPROCESS_TIMEOUT_SECONDS}."
    )
    assert popen_calls == 0


def test_tool_execution_fails_closed_when_required_stdout_capture_overflows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stdout_prefix = '{"status":"partial"}\n[truncated]'

    def fake_execute(self, **kwargs):
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 12,
            "stdout": stdout_prefix,
            "stderr": "",
            "stdoutCaptureTruncated": True,
            "stderrCaptureTruncated": False,
            "stdoutTotalBytes": 2_000_000,
            "stderrTotalBytes": 0,
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        fake_execute,
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Overflowing Required Output",
            path=tmp_path / "overflowing-required-output",
            template_id="other",
        )
        workspace = allocate_test_workspace(connection, tmp_path, project, task_id="overflow-output")
        profile = AgentsRepository(connection).upsert_agent_profile(
            {
                "id": "cli_overflow_output",
                "name": "CLI Overflow Output",
                "role": "implementer",
                "runtimeMode": "cli",
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell"],
            }
        )
        run = AgentsRepository(connection).create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id="overflow-output",
            input_payload={"toolCalls": []},
            output_payload={},
            status="running",
        )

        tool_call = ToolBroker(connection, artifact_root=tmp_path).evaluate_tool_call(
            project_id=project["id"],
            agent_run_id=run["id"],
            agent_profile=profile,
            tool_call={
                "tool": "shell",
                "command": "python --version",
                "argv": ["python", "--version"],
                "workspaceId": workspace["id"],
                "path": workspace["path"],
                "workspacePath": workspace["path"],
                "execute": True,
                "captureStdoutArtifact": True,
            },
        )["toolCall"]

        execution_result = tool_call["payload"]["executionResult"]
        artifact = EvidenceRepository(connection).get_artifact_by_id(
            execution_result["stdoutArtifactId"]
        )

    assert tool_call["status"] == "failed"
    assert execution_result["reason"] == "Required stdout exceeded the complete capture limit."
    assert execution_result["stdoutTruncated"] is True
    assert Path(artifact["path"]).read_text(encoding="utf-8") == stdout_prefix


def test_approved_sensitive_tool_call_requires_and_consumes_permission_grant(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 20,
            "stdout": "installed in disposable container\n",
            "stderr": "",
            "command": ["docker", "run", "--rm", "node:22-alpine", "pnpm", "add", "left-pad"],
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "approved-install"
    project = store.create_project(name="Approved Install", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="install-package",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    job = store.jobs.create_job(
        project_id=project["id"], kind="chat.route", payload={"prompt": "install package"}
    )["job"]
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_install",
            "name": "CLI Install",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    requested = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "jobId": job["id"],
            "agentProfileId": "cli_install",
            "taskId": "request-install",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                    }
                ]
            },
        },
        headers=headers,
    )
    assert requested.status_code == 202
    assert requested.json()["agentRun"]["status"] == "awaiting_permission"
    action = client.get("/api/v1/overview").json()["actionRequests"][0]

    approved = client.post(
        f"/api/v1/jobs/{job['id']}/actions/{action['id']}/approve",
        json={"reason": "Allow one disposable container install for dependency audit."},
        headers=headers,
    )
    assert approved.status_code == 202
    grant = approved.json()["permissionGrant"]
    assert grant["status"] == "active"
    assert grant["actionRequestId"] == action["id"]

    executed = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "jobId": job["id"],
            "agentProfileId": "cli_install",
            "taskId": "execute-install",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "argv": ["pnpm", "add", "left-pad"],
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                        "execute": True,
                        "sandbox": "docker",
                        "dockerImage": "node:22-alpine",
                        "approvalGrantId": grant["id"],
                    }
                ]
            },
        },
        headers=headers,
    )
    assert executed.status_code == 202
    executed_run = executed.json()["agentRun"]
    assert executed_run["status"] == "completed"
    assert docker_calls and docker_calls[0]["image"] == "node:22-alpine"

    overview = client.get("/api/v1/overview").json()
    consumed_grant = next(item for item in overview["permissionGrants"] if item["id"] == grant["id"])
    assert consumed_grant["status"] == "consumed"
    assert consumed_grant["consumedByAgentRunId"] == executed_run["id"]
    executed_call = next(
        call
        for call in overview["agentToolCalls"]
        if call["agentRunId"] == executed_run["id"] and call["payload"]["execution"] == "docker"
    )
    assert executed_call["payload"]["permissionGrantId"] == grant["id"]
    assert executed_run["output"]["evidence_refs"]

    reused = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "jobId": job["id"],
            "agentProfileId": "cli_install",
            "taskId": "reuse-install",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "argv": ["pnpm", "add", "left-pad"],
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                        "execute": True,
                        "sandbox": "docker",
                        "dockerImage": "node:22-alpine",
                        "approvalGrantId": grant["id"],
                    }
                ]
            },
        },
        headers=headers,
    )
    assert reused.status_code == 202
    reused_run = reused.json()["agentRun"]
    assert reused_run["status"] == "failed"
    reused_overview = client.get("/api/v1/overview").json()
    reused_call = next(
        call for call in reused_overview["agentToolCalls"] if call["agentRunId"] == reused_run["id"]
    )
    assert reused_call["status"] == "denied"
    assert "already consumed" in reused_call["payload"]["grantValidation"]["reason"]


def test_action_request_exposes_contextual_scope_and_expiration(tmp_path: Path, monkeypatch) -> None:
    context = create_sensitive_shell_approval(
        tmp_path,
        monkeypatch,
        profile_id="cli_context_scope",
        evidence_refs=["evidence-context-1"],
        diff_refs=[{"kind": "git_patch", "artifactId": "artifact-diff-1", "hash": "hash-diff-1"}],
    )

    action = context.action
    assert action["actionType"] == "tool.call"
    assert action["riskLevel"] in {"medium", "high", "critical"}
    assert action["command"] == "pnpm add left-pad"
    assert action["commandArgv"] == ["pnpm", "add", "left-pad"]
    assert action["workspaceId"] == context.workspace["id"]
    assert action["workspacePath"] == context.workspace_path
    assert action["runtimeId"] == "docker"
    assert action["evidenceRefs"] == ["evidence-context-1"]
    assert action["diffRefs"] == [
        {"kind": "git_patch", "artifactId": "artifact-diff-1", "hash": "hash-diff-1"}
    ]
    assert action["reason"].strip()
    assert action["expiresAt"]


def test_permission_grant_is_scoped_to_command_argv(tmp_path: Path, monkeypatch) -> None:
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "durationMs": 1,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    context = create_sensitive_shell_approval(tmp_path, monkeypatch, profile_id="cli_grant_argv")
    approved = context.client.post(
        f"/api/v1/jobs/{context.job['id']}/actions/{context.action['id']}/approve",
        json={"reason": "Allow exactly pnpm add left-pad once."},
        headers=context.headers,
    )
    assert approved.status_code == 202
    grant = approved.json()["permissionGrant"]
    assert grant["commandArgv"] == ["pnpm", "add", "left-pad"]

    mismatched = context.client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": context.project["id"],
            "jobId": context.job["id"],
            "agentProfileId": context.profile_id,
            "taskId": "execute-other-argv",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "argv": ["pnpm", "add", "is-number"],
                        "workspaceId": context.workspace["id"],
                        "path": context.workspace_path,
                        "workspacePath": context.workspace_path,
                        "execute": True,
                        "sandbox": "docker",
                        "dockerImage": "node:22-alpine",
                        "approvalGrantId": grant["id"],
                    }
                ]
            },
        },
        headers=context.headers,
    )

    assert mismatched.status_code == 202
    mismatched_run = mismatched.json()["agentRun"]
    assert mismatched_run["status"] == "failed"
    overview = context.client.get("/api/v1/overview").json()
    mismatched_call = next(
        call for call in overview["agentToolCalls"] if call["agentRunId"] == mismatched_run["id"]
    )
    assert mismatched_call["status"] == "denied"
    assert "argv mismatch" in mismatched_call["payload"]["grantValidation"]["reason"]
    assert not docker_calls
    active_grant = next(item for item in overview["permissionGrants"] if item["id"] == grant["id"])
    assert active_grant["status"] == "active"


def test_permission_grant_is_scoped_to_exact_command(tmp_path: Path, monkeypatch) -> None:
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "durationMs": 1,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    context = create_sensitive_shell_approval(tmp_path, monkeypatch, profile_id="cli_grant_command")
    approved = context.client.post(
        f"/api/v1/jobs/{context.job['id']}/actions/{context.action['id']}/approve",
        json={"reason": "Allow exactly pnpm add left-pad once."},
        headers=context.headers,
    )
    assert approved.status_code == 202
    grant = approved.json()["permissionGrant"]

    mismatched = context.client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": context.project["id"],
            "jobId": context.job["id"],
            "agentProfileId": context.profile_id,
            "taskId": "execute-other-command",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add is-number",
                        "argv": ["pnpm", "add", "is-number"],
                        "workspaceId": context.workspace["id"],
                        "path": context.workspace_path,
                        "workspacePath": context.workspace_path,
                        "execute": True,
                        "sandbox": "docker",
                        "dockerImage": "node:22-alpine",
                        "approvalGrantId": grant["id"],
                    }
                ]
            },
        },
        headers=context.headers,
    )

    assert mismatched.status_code == 202
    mismatched_run = mismatched.json()["agentRun"]
    assert mismatched_run["status"] == "failed"
    overview = context.client.get("/api/v1/overview").json()
    mismatched_call = next(
        call for call in overview["agentToolCalls"] if call["agentRunId"] == mismatched_run["id"]
    )
    assert mismatched_call["status"] == "denied"
    assert "command mismatch" in mismatched_call["payload"]["grantValidation"]["reason"]
    assert not docker_calls
    active_grant = next(item for item in overview["permissionGrants"] if item["id"] == grant["id"])
    assert active_grant["status"] == "active"


def test_permission_grant_expiration_blocks_execution(tmp_path: Path, monkeypatch) -> None:
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "durationMs": 1,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    context = create_sensitive_shell_approval(tmp_path, monkeypatch, profile_id="cli_grant_expired")
    approved = context.client.post(
        f"/api/v1/jobs/{context.job['id']}/actions/{context.action['id']}/approve",
        json={"reason": "Allow exactly one command before expiry."},
        headers=context.headers,
    )
    assert approved.status_code == 202
    grant = approved.json()["permissionGrant"]
    assert grant["expiresAt"]
    context.store.connection.execute(
        "UPDATE permission_grants SET expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00.000Z", grant["id"]),
    )

    expired = context.client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": context.project["id"],
            "jobId": context.job["id"],
            "agentProfileId": context.profile_id,
            "taskId": "execute-expired-grant",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "argv": ["pnpm", "add", "left-pad"],
                        "workspaceId": context.workspace["id"],
                        "path": context.workspace_path,
                        "workspacePath": context.workspace_path,
                        "execute": True,
                        "sandbox": "docker",
                        "dockerImage": "node:22-alpine",
                        "approvalGrantId": grant["id"],
                    }
                ]
            },
        },
        headers=context.headers,
    )

    assert expired.status_code == 202
    expired_run = expired.json()["agentRun"]
    assert expired_run["status"] == "failed"
    overview = context.client.get("/api/v1/overview").json()
    expired_call = next(
        call for call in overview["agentToolCalls"] if call["agentRunId"] == expired_run["id"]
    )
    assert expired_call["status"] == "denied"
    assert "expired" in expired_call["payload"]["grantValidation"]["reason"]
    assert not docker_calls
    expired_grant = next(item for item in overview["permissionGrants"] if item["id"] == grant["id"])
    assert expired_grant["status"] == "expired"


def test_rejected_action_blocks_later_approval_and_grant_creation(tmp_path: Path, monkeypatch) -> None:
    context = create_sensitive_shell_approval(tmp_path, monkeypatch, profile_id="cli_reject_blocks")

    blank = context.client.post(
        f"/api/v1/jobs/{context.job['id']}/actions/{context.action['id']}/deny",
        json={"reason": " "},
        headers=context.headers,
    )
    assert blank.status_code == 422
    assert "reason" in blank.json()["detail"].lower()

    rejected = context.client.post(
        f"/api/v1/jobs/{context.job['id']}/actions/{context.action['id']}/deny",
        json={"reason": "Command scope is too broad."},
        headers=context.headers,
    )
    assert rejected.status_code == 202
    assert rejected.json()["actionRequest"]["status"] == "denied"
    assert rejected.json()["job"]["status"] == "cancelled"

    approve_after_reject = context.client.post(
        f"/api/v1/jobs/{context.job['id']}/actions/{context.action['id']}/approve",
        json={"reason": "Trying to approve after rejection."},
        headers=context.headers,
    )
    assert approve_after_reject.status_code == 409
    grants = context.client.get("/api/v1/overview").json()["permissionGrants"]
    assert not any(grant.get("actionRequestId") == context.action["id"] for grant in grants)


def test_action_approval_requires_non_empty_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "approval-reason"
    project = store.create_project(name="Approval Reason", path=project_path, template_id="other")
    job = store.jobs.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "install"})[
        "job"
    ]
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_reason",
            "name": "CLI Reason",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "jobId": job["id"],
            "agentProfileId": "cli_reason",
            "taskId": "request-install",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "pnpm add left-pad",
                        "path": str(project_path),
                        "workspacePath": str(project_path),
                    }
                ]
            },
        },
        headers=headers,
    )
    action = client.get("/api/v1/overview").json()["actionRequests"][0]

    blank = client.post(
        f"/api/v1/jobs/{job['id']}/actions/{action['id']}/approve",
        json={"reason": "   "},
        headers=headers,
    )
    assert blank.status_code == 422
    assert "reason" in blank.json()["detail"].lower()


def test_docker_execution_uses_configured_sandbox_policy_not_tool_broker_constants(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 15,
            "stdout": "configured sandbox\n",
            "stderr": "",
            "command": ["docker", "run", "--rm", "aido/custom:local", "pnpm", "add", "left-pad"],
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        policies = SecurityPolicyRepository(connection)
        policies.upsert_sandbox_profile(
            {
                "id": "default_docker",
                "name": "Configured Docker",
                "allowedImages": ["aido/custom:local"],
                "allowedNetworks": ["none"],
                "defaultNetwork": "none",
                "memory": "512m",
                "cpus": "0.75",
                "timeoutSeconds": 31,
            }
        )
        project_path = tmp_path / "sandbox-policy"
        project = ProjectsRepository(connection).create_project(
            name="Sandbox Policy",
            path=project_path,
            template_id="other",
        )
        workspace = allocate_test_workspace(connection, tmp_path, project, task_id="policy-install")
        workspace_path = str(workspace["path"])
        jobs = JobsRepository(connection)
        job = jobs.create_job(
            project_id=project["id"],
            kind="chat.route",
            payload={"prompt": "install package"},
        )["job"]
        agents = AgentsRepository(connection)
        profile = agents.upsert_agent_profile(
            {
                "id": "cli_policy_config",
                "name": "CLI Policy Config",
                "role": "implementer",
                "runtimeMode": "cli",
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell"],
            }
        )
        request_run = agents.create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            job_id=job["id"],
            task_id="request-policy-install",
            input_payload={"toolCalls": []},
            output_payload={},
            status="running",
        )
        ToolBroker(connection).evaluate_tool_calls(
            project_id=project["id"],
            agent_run_id=request_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_calls=[
                {
                    "tool": "shell",
                    "command": "pnpm add left-pad",
                    "workspaceId": workspace["id"],
                    "path": workspace_path,
                    "workspacePath": workspace_path,
                }
            ],
        )
        action = jobs.list_action_requests(job_id=job["id"])[0]
        grant = jobs.approve_action(
            job["id"],
            action["id"],
            reason="Allow configured Docker image once.",
        )["permissionGrant"]
        execute_run = agents.create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            job_id=job["id"],
            task_id="execute-policy-install",
            input_payload={"toolCalls": []},
            output_payload={},
            status="running",
        )
        result = ToolBroker(connection).evaluate_tool_calls(
            project_id=project["id"],
            agent_run_id=execute_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_calls=[
                {
                    "tool": "shell",
                    "command": "pnpm add left-pad",
                    "argv": ["pnpm", "add", "left-pad"],
                    "workspaceId": workspace["id"],
                    "path": workspace_path,
                    "workspacePath": workspace_path,
                    "execute": True,
                    "sandbox": "docker",
                    "dockerImage": "aido/custom:local",
                    "approvalGrantId": grant["id"],
                }
            ],
        )

        assert result[0]["toolCall"]["status"] == "completed"
        assert docker_calls
        assert docker_calls[0]["image"] == "aido/custom:local"
        assert docker_calls[0]["memory"] == "512m"
        assert docker_calls[0]["cpus"] == "0.75"
        assert docker_calls[0]["timeout_seconds"] == 31

        sandbox_profile = policies.get_sandbox_profile("default_docker")
        assert sandbox_profile["allowedImages"] == ["aido/custom:local"]
        assert sandbox_profile["memory"] == "512m"


def test_permission_grant_revocation_is_audited_and_blocks_later_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "durationMs": 1,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "grant-revoke"
    project = store.create_project(name="Grant Revoke", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="grant-revoke",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    job = store.jobs.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "install"})[
        "job"
    ]
    agents = AgentsRepository(store.connection)
    profile = agents.upsert_agent_profile(
        {
            "id": "cli_revoke",
            "name": "CLI Revoke",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    request_run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        job_id=job["id"],
        task_id="request-revoked-install",
        input_payload={"toolCalls": []},
        output_payload={},
        status="running",
    )
    ToolBroker(store.connection).evaluate_tool_calls(
        project_id=project["id"],
        agent_run_id=request_run["id"],
        agent_profile=profile,
        job_id=job["id"],
        tool_calls=[
            {
                "tool": "shell",
                "command": "pnpm add left-pad",
                "workspaceId": workspace["id"],
                "path": workspace_path,
                "workspacePath": workspace_path,
            }
        ],
    )
    action = store.jobs.list_action_requests(job_id=job["id"])[0]
    grant = store.jobs.approve_action(job["id"], action["id"], reason="Allow once for audit.")[
        "permissionGrant"
    ]
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    blank = client.post(
        f"/api/v1/permissions/grants/{grant['id']}/revoke", json={"reason": " "}, headers=headers
    )
    assert blank.status_code == 422

    revoked = client.post(
        f"/api/v1/permissions/grants/{grant['id']}/revoke",
        json={"reason": "Dependency audit cancelled."},
        headers=headers,
    )
    assert revoked.status_code == 202
    assert revoked.json()["permissionGrant"]["status"] == "revoked"
    assert revoked.json()["permissionGrant"]["revokeReason"] == "Dependency audit cancelled."

    execute_run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        job_id=job["id"],
        task_id="execute-revoked-install",
        input_payload={"toolCalls": []},
        output_payload={},
        status="running",
    )
    result = ToolBroker(store.connection).evaluate_tool_calls(
        project_id=project["id"],
        agent_run_id=execute_run["id"],
        agent_profile=profile,
        job_id=job["id"],
        tool_calls=[
            {
                "tool": "shell",
                "command": "pnpm add left-pad",
                "argv": ["pnpm", "add", "left-pad"],
                "workspaceId": workspace["id"],
                "path": workspace_path,
                "workspacePath": workspace_path,
                "execute": True,
                "sandbox": "docker",
                "dockerImage": "node:22-alpine",
                "approvalGrantId": grant["id"],
            }
        ],
    )

    assert result[0]["toolCall"]["status"] == "denied"
    assert "revoked" in result[0]["toolCall"]["payload"]["grantValidation"]["reason"]
    assert docker_calls == []
    assert any(
        event["type"] == "permission.grant.revoked"
        for event in store.events.list_events(project_id=project["id"])
    )
    assert any(
        event["action"] == "permission.grant.revoke"
        for event in store.events.list_audit_events(project_id=project["id"])
    )


def test_sandbox_profile_revocation_is_audited_and_blocks_docker_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    docker_calls: list[dict[str, object]] = []

    def fake_docker_execute(self, **kwargs):
        docker_calls.append(kwargs)
        return {
            "executed": True,
            "blocked": False,
            "returnCode": 0,
            "durationMs": 1,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.DockerSandbox.execute", fake_docker_execute
    )
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    denied = client.post(
        "/api/v1/sandbox/profiles/default_docker/revoke", json={"reason": " "}, headers=headers
    )
    assert denied.status_code == 422

    revoked = client.post(
        "/api/v1/sandbox/profiles/default_docker/revoke",
        json={"reason": "Disable Docker while reviewing image catalog."},
        headers=headers,
    )
    assert revoked.status_code == 202
    assert revoked.json()["sandboxProfile"]["status"] == "revoked"

    project_path = tmp_path / "profile-revoke"
    project = store.create_project(name="Profile Revoke", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="docker-after-profile-revoke",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    agents = AgentsRepository(store.connection)
    profile = agents.upsert_agent_profile(
        {
            "id": "cli_profile_revoke",
            "name": "CLI Profile Revoke",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id="docker-after-profile-revoke",
        input_payload={"toolCalls": []},
        output_payload={},
        status="running",
    )
    result = ToolBroker(store.connection).evaluate_tool_calls(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_calls=[
            {
                "tool": "shell",
                "command": "python --version",
                "argv": ["python", "--version"],
                "workspaceId": workspace["id"],
                "path": workspace_path,
                "workspacePath": workspace_path,
                "execute": True,
                "sandbox": "docker",
                "dockerImage": "python:3.13-slim",
            }
        ],
    )

    assert result[0]["toolCall"]["status"] == "denied"
    assert result[0]["toolCall"]["payload"]["executionResult"]["blocked"] is True
    assert "profile" in result[0]["toolCall"]["payload"]["executionResult"]["reason"]
    assert docker_calls == []
    assert any(event["type"] == "sandbox.profile.revoked" for event in store.events.list_events())
    assert any(event["action"] == "sandbox.profile.revoke" for event in store.events.list_audit_events())


def test_cli_tool_call_execute_true_with_command_string_and_no_argv_is_denied_before_policy_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project_path = tmp_path / "sandbox-denied"
    project = store.create_project(name="Sandbox Denied", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="unsafe-string-exec",
        agent_id="implementer",
    )
    workspace_path = str(workspace["path"])
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "cli_no_argv",
            "name": "CLI No Argv",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )
    run_response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "cli_no_argv",
            "taskId": "unsafe-string-exec",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "workspaceId": workspace["id"],
                        "path": workspace_path,
                        "workspacePath": workspace_path,
                        "execute": True,
                    }
                ]
            },
        },
        headers=headers,
    )
    assert run_response.status_code == 202
    agent_run = run_response.json()["agentRun"]
    assert agent_run["status"] == "failed"

    overview = client.get("/api/v1/overview").json()
    tool_call = next(call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"])
    assert tool_call["status"] == "denied"
    assert tool_call["payload"]["execution"] == "not_executed"
    assert "executionResult" not in tool_call["payload"]
    assert tool_call["payload"]["decision"] == "deny"
    assert "structured argv" in tool_call["payload"]["decisionReason"]
    assert tool_call["payload"]["command"] == "python --version"
    assert any(
        decision["decision"] == "deny"
        and decision["command"] == "python --version"
        and "structured argv" in decision["reason"]
        for decision in overview["permissionDecisions"]
    )


def test_action_request_does_not_derive_argv_from_command_string_for_executable_actions(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        jobs = JobsRepository(connection)
        job = jobs.create_job(project_id="project-action", kind="chat.route", payload={"prompt": "install"})[
            "job"
        ]

        action = jobs.create_action_request(
            job_id=job["id"],
            project_id="project-action",
            action_type="tool.call",
            risk_level="medium",
            command="pnpm add left-pad",
            payload={"runtime": {"execute": True}},
            reason="Sensitive install requires approval.",
        )

        assert action["command"] == "pnpm add left-pad"
        assert action["commandArgv"] == []


def test_action_request_keeps_explicit_argv_for_executable_actions(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        jobs = JobsRepository(connection)
        job = jobs.create_job(project_id="project-action", kind="chat.route", payload={"prompt": "install"})[
            "job"
        ]

        action = jobs.create_action_request(
            job_id=job["id"],
            project_id="project-action",
            action_type="tool.call",
            risk_level="medium",
            command="pnpm add left-pad",
            command_argv=["pnpm", "add", "left-pad"],
            payload={"runtime": {"execute": True}},
            reason="Sensitive install requires approval.",
        )

        assert action["command"] == "pnpm add left-pad"
        assert action["commandArgv"] == ["pnpm", "add", "left-pad"]


def test_docker_sandbox_is_optional_and_builds_locked_down_container_args(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("local_control_center.security_policy.sandbox.shutil.which", lambda _name: None)
    sandbox = DockerSandbox(docker_executable=None)
    status = sandbox.status()
    assert status["required"] is False
    assert status["available"] is False
    assert status["fallback"] == "restricted_subprocess"

    args = DockerSandbox(docker_executable="docker").build_run_args(
        image="python:3.13-slim",
        argv=["python", "--version"],
        workspace_path=tmp_path,
    )
    assert args[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in args
    assert "--network" in args
    assert "none" in args
    assert any(str(tmp_path) in item and "readonly" in item for item in args)
    assert args[-2:] == ["python", "--version"]


def test_docker_sandbox_execute_is_optional_and_captures_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("local_control_center.security_policy.sandbox.shutil.which", lambda _name: None)
    missing = DockerSandbox().execute(
        image="python:3.13-slim",
        argv=["python", "--version"],
        workspace_path=tmp_path,
    )
    assert missing["executed"] is False
    assert missing["blocked"] is True
    assert "not available" in missing["reason"]

    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "Python 3.13\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return Completed()

    monkeypatch.setattr("local_control_center.security_policy.sandbox.subprocess.run", fake_run)
    result = DockerSandbox(docker_executable="docker").execute(
        image="python:3.13-slim",
        argv=["python", "--version"],
        workspace_path=tmp_path,
        timeout_seconds=10,
    )

    assert result["executed"] is True
    assert result["blocked"] is False
    assert result["returnCode"] == 0
    assert result["stdout"] == "Python 3.13\n"
    assert calls and calls[0][:3] == ["docker", "run", "--rm"]


def test_sandbox_status_endpoint_reports_docker_and_restricted_modes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    response = client.get("/api/v1/sandbox/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["docker"]["required"] is False
    assert payload["restrictedSubprocess"]["available"] is True
    assert payload["restrictedSubprocess"]["shell"] is False


def test_workspace_allocation_enforces_single_owner_and_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Workspace", path=tmp_path / "project", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-1", "agentId": "implementer"},
        headers=headers,
    )
    assert created.status_code == 201
    workspace = created.json()["workspace"]
    assert workspace["status"] == "ready"
    assert workspace["ownerAgentId"] == "implementer"
    assert Path(workspace["path"]).exists()

    duplicate = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-1", "agentId": "qa_reviewer"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    archived = client.post(
        f"/api/v1/workspaces/{workspace['id']}/archive", json={"reason": "done"}, headers=headers
    )
    assert archived.status_code == 202
    assert archived.json()["workspace"]["status"] == "archived"


def test_workspace_archive_creates_evidence_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Workspace Evidence", path=tmp_path / "project-evidence", template_id="other"
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-evidence", "agentId": "implementer"},
        headers=headers,
    )
    workspace = created.json()["workspace"]
    workspace_path = Path(workspace["path"])
    (workspace_path / "src").mkdir()
    (workspace_path / "src" / "change.py").write_text("print('captured')\n", encoding="utf-8")

    archived = client.post(
        f"/api/v1/workspaces/{workspace['id']}/archive",
        json={"reason": "capture diff evidence"},
        headers=headers,
    )
    assert archived.status_code == 202
    evidence = archived.json()["evidencePackage"]
    assert evidence["taskId"] == "story-evidence"
    assert evidence["qaVerdict"] == "evidence_collected"
    snapshot = next(ref for ref in evidence["diffRefs"] if ref["kind"] == "workspace_snapshot")
    assert any(item["path"] == "src/change.py" for item in snapshot["files"])

    overview = client.get("/api/v1/overview").json()
    assert any(package["id"] == evidence["id"] for package in overview["evidencePackages"])


def test_git_worktree_archive_captures_git_diff_before_cleanup(tmp_path: Path, monkeypatch) -> None:
    if not git_available():
        pytest.skip("git CLI is not available")

    repo_path = tmp_path / "git-project"
    repo_path.mkdir()
    assert run_git(["init"], cwd=repo_path).returncode == 0
    (repo_path / "README.md").write_text("initial\n", encoding="utf-8")
    assert run_git(["-C", str(repo_path), "add", "."]).returncode == 0
    commit = run_git(
        [
            "-C",
            str(repo_path),
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            "initial",
        ]
    )
    assert commit.returncode == 0, commit.stderr

    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Git Workspace Evidence", path=repo_path, template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/workspaces",
        json={
            "projectId": project["id"],
            "taskId": "story-git-evidence",
            "agentId": "implementer",
            "isolationType": "git_worktree",
        },
        headers=headers,
    )
    assert created.status_code == 201
    workspace = created.json()["workspace"]
    assert workspace["isolationType"] == "git_worktree"
    workspace_path = Path(workspace["path"])
    (workspace_path / "README.md").write_text("initial\nchanged\n", encoding="utf-8")
    (workspace_path / "new-module.py").write_text("print('new')\n", encoding="utf-8")

    archived = client.post(
        f"/api/v1/workspaces/{workspace['id']}/archive",
        json={"reason": "capture git diff"},
        headers=headers,
    )
    assert archived.status_code == 202
    evidence = archived.json()["evidencePackage"]
    git_diff = next(ref for ref in evidence["diffRefs"] if ref["kind"] == "git_diff")
    assert git_diff["state"] == "captured"
    assert "README.md" in git_diff["nameOnly"]
    assert any(item["path"] == "new-module.py" and item["status"] == "??" for item in git_diff["status"])
    assert not workspace_path.exists()


def test_git_worktree_archive_promotes_large_patch_to_artifact(tmp_path: Path, monkeypatch) -> None:
    if not git_available():
        pytest.skip("git CLI is not available")

    repo_path = tmp_path / "large-git-project"
    repo_path.mkdir()
    assert run_git(["init"], cwd=repo_path).returncode == 0
    (repo_path / "big.txt").write_text("initial\n", encoding="utf-8")
    assert run_git(["-C", str(repo_path), "add", "."]).returncode == 0
    commit = run_git(
        [
            "-C",
            str(repo_path),
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            "initial",
        ]
    )
    assert commit.returncode == 0, commit.stderr

    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Large Patch Evidence", path=repo_path, template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/workspaces",
        json={
            "projectId": project["id"],
            "taskId": "story-large-patch",
            "agentId": "implementer",
            "isolationType": "git_worktree",
        },
        headers=headers,
    )
    workspace = created.json()["workspace"]
    workspace_path = Path(workspace["path"])
    (workspace_path / "big.txt").write_text(
        "\n".join(f"line {index}" for index in range(2000)), encoding="utf-8"
    )

    archived = client.post(
        f"/api/v1/workspaces/{workspace['id']}/archive",
        json={"reason": "capture large patch artifact"},
        headers=headers,
    )
    assert archived.status_code == 202
    evidence = archived.json()["evidencePackage"]
    git_diff = next(ref for ref in evidence["diffRefs"] if ref["kind"] == "git_diff")
    assert "patch" not in git_diff
    assert git_diff["patchArtifactId"].startswith("artifact-")
    assert git_diff["patchSizeBytes"] > 12000

    detail = client.get(f"/api/v1/evidence/{evidence['id']}")
    assert detail.status_code == 200
    artifact = detail.json()["artifacts"][0]
    assert artifact["id"] == git_diff["patchArtifactId"]
    assert artifact["kind"] == "git_patch"
    assert artifact["hash"]
    assert Path(artifact["path"]).exists()


def test_agent_run_without_executable_adapter_does_not_record_synthetic_model_cost(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Agent", path=tmp_path / "agent", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "implementer",
            "name": "Implementer",
            "role": "implementer",
            "runtimeType": "manual",
            "modelPolicyId": "implementation_default",
            "allowedTools": ["policy.evaluate", "evidence.create"],
            "qualityGates": ["structured_output"],
        },
        headers=headers,
    )
    client.post(
        "/api/v1/model-gateway/role-policies",
        json={
            "id": "implementation_default",
            "role": "implementer",
            "routingProfileId": "balanced_best_value",
            "preferred": [{"provider": "openai_compatible", "model": "configured_model"}],
            "fallback": [],
            "maxCostPerTaskUsd": 1.0,
            "maxTokensPerRun": 4000,
            "allowRemote": True,
            "allowLocal": False,
            "allowCli": False,
            "allowApi": True,
        },
        headers=headers,
    )

    run = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "implementer",
            "taskId": "story-2",
            "input": {"goal": "produce a structured patch plan"},
        },
        headers=headers,
    )
    assert run.status_code == 202
    agent_run = run.json()["agentRun"]
    assert agent_run["status"] == "failed"
    assert agent_run["output"]["verdict"] == "blocked"
    assert "no executable adapter" in agent_run["output"]["summary"]

    overview = client.get("/api/v1/overview").json()
    assert not any(call["agentRunId"] == agent_run["id"] for call in overview["agentToolCalls"])
    assert not any(call["agentRunId"] == agent_run["id"] for call in overview["modelCalls"])
    assert not any(item["metadata"].get("agentRunId") == agent_run["id"] for item in overview["costUsage"])


def test_technical_review_agent_runs_require_evidence_package_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Review", path=tmp_path / "review", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    profile_response = client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "technical_lead",
            "name": "Technical Lead",
            "role": "technical_lead",
            "runtimeType": "manual",
            "permissionProfile": "plan",
            "allowedTools": [],
        },
        headers=headers,
    )
    assert profile_response.status_code == 201

    missing_evidence = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "technical_lead",
            "taskId": "technical_review:story-1",
            "input": {"stage": "technical_review"},
        },
        headers=headers,
    )
    assert missing_evidence.status_code == 202
    missing_run = missing_evidence.json()["agentRun"]
    assert missing_run["status"] == "failed"
    assert missing_run["output"]["verdict"] == "blocked"
    assert missing_run["output"]["evidence_refs"] == []
    assert "evidence package" in missing_run["output"]["summary"].lower()

    evidence = store.evidence.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        agent_id="qa_reviewer",
        task_id="story-1",
        test_plan="Review evidence",
        acceptance_checklist=["tests passed"],
        test_results=[{"command": "uv run pytest", "status": "passed"}],
        qa_verdict="passed",
    )

    reviewed = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "technical_lead",
            "taskId": "technical_review:story-1",
            "input": {"stage": "technical_review", "evidenceRefs": [evidence["id"]]},
        },
        headers=headers,
    )
    assert reviewed.status_code == 202
    reviewed_run = reviewed.json()["agentRun"]
    assert reviewed_run["status"] == "failed"
    assert reviewed_run["output"]["verdict"] == "blocked"
    assert "no executable adapter" in reviewed_run["output"]["summary"]


def test_workspace_allocation_accepts_devcontainer_metadata_without_docker_requirement(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Devcontainer", path=tmp_path / "devcontainer", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    response = client.post(
        "/api/v1/workspaces",
        json={
            "projectId": project["id"],
            "taskId": "story-devcontainer",
            "agentId": "implementer",
            "reason": "Prepare metadata only",
            "isolationType": "directory",
            "devcontainer": {
                "enabled": True,
                "templateId": "python-node",
                "image": "mcr.microsoft.com/devcontainers/python:3.12",
                "features": ["node"],
            },
        },
        headers=headers,
    )

    assert response.status_code == 201
    workspace = response.json()["workspace"]
    assert workspace["isolationType"] == "directory"
    assert workspace["metadata"]["devcontainer"] == {
        "enabled": True,
        "templateId": "python-node",
        "image": "mcr.microsoft.com/devcontainers/python:3.12",
        "features": ["node"],
        "status": "metadata_only",
    }
    assert Path(workspace["path"]).exists()


def test_model_gateway_plans_allowed_model_call_without_recording_cost_usage(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Gateway", path=tmp_path / "gateway", template_id="other"
        )
        agents = AgentsRepository(connection)
        agents.upsert_model_policy(
            {
                "id": "implementation_default",
                "name": "Implementation Default",
                "preferred": [{"provider": "openrouter", "model": "oss-model"}],
                "fallback": [{"provider": "ollama", "model": "llama3"}],
                "maxCostUsd": 1.0,
                "maxTokens": 120000,
                "allowRemote": True,
                "allowLocal": True,
            }
        )

        result = ModelGateway(connection).prepare_model_call(
            project_id=project["id"],
            model_policy_id="implementation_default",
            estimated_cost_usd=0.02,
            prompt_tokens=100,
            completion_tokens=50,
            metadata={"request": "safe"},
        )

        assert result["status"] == "planned"
        assert result["provider"] == "openrouter"
        assert result["model"] == "oss-model"
        assert result["modelCall"]["status"] == "planned"
        assert result["modelCall"]["promptTokens"] is None
        assert result["modelCall"]["completionTokens"] is None
        assert result["modelCall"]["costUsd"] is None
        assert agents.list_cost_usage() == []


def test_model_gateway_blocks_budget_overrun_and_redacts_secret_metadata(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Gateway Budget",
            path=tmp_path / "gateway-budget",
            template_id="other",
        )
        AgentsRepository(connection).upsert_model_policy(
            {
                "id": "tiny_budget",
                "name": "Tiny Budget",
                "preferred": [{"provider": "openrouter", "model": "oss-model"}],
                "fallback": [],
                "maxCostUsd": 0.01,
                "maxTokens": 4000,
                "allowRemote": True,
                "allowLocal": False,
            }
        )

        result = ModelGateway(connection).prepare_model_call(
            project_id=project["id"],
            model_policy_id="tiny_budget",
            estimated_cost_usd=0.02,
            prompt_tokens=100,
            completion_tokens=50,
            metadata={"authorization": "Bearer sk-test-secret", "nested": {"token": "secret-value"}},
        )

        assert result["status"] == "blocked_budget"
        assert result["modelCall"]["status"] == "blocked_budget"
        serialized = str(result["modelCall"]["metadata"])
        assert "sk-test-secret" not in serialized
        assert "secret-value" not in serialized
        assert "[redacted]" in serialized


def test_skills_sync_reads_versionable_local_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    skill_dir = tmp_path / "skills" / "backend-api-contract"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "name: backend-api-contract",
                "description: Validate backend API contracts.",
                "license: MIT",
                "compatibility: AIDO",
                "inputs: [openapi]",
                "outputs: [contract-report]",
                "tools: [pytest]",
                "risk_level: low",
                "instructions: Run API contract checks.",
            ]
        ),
        encoding="utf-8",
    )
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    synced = client.post(
        "/api/v1/skills/sync", json={"skillsPath": str(tmp_path / "skills")}, headers=headers
    )
    assert synced.status_code == 202
    assert synced.json()["synced"] == 1

    listed = client.get("/api/v1/skills")
    assert listed.status_code == 200
    assert listed.json()["skills"][0]["name"] == "backend-api-contract"


def test_agent_run_blocks_unknown_requested_skill_before_tool_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Unknown Skill", path=tmp_path / "unknown-skill", template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="unknown-skill",
        agent_id="skill_agent",
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "skill_agent",
            "name": "Skill Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedSkills": ["*"],
            "allowedTools": ["shell"],
        },
        headers=headers,
    )

    response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "skill_agent",
            "taskId": "unknown-skill",
            "input": {
                "skillIds": ["skill-does-not-exist"],
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "argv": [sys.executable, "--version"],
                        "workspaceId": workspace["id"],
                        "workspacePath": str(workspace["path"]),
                        "path": str(workspace["path"]),
                        "execute": True,
                    }
                ],
            },
        },
        headers=headers,
    )

    assert response.status_code == 202
    agent_run = response.json()["agentRun"]
    assert agent_run["status"] == "failed"
    assert agent_run["output"]["verdict"] == "blocked"
    assert "Skill is not cataloged" in agent_run["output"]["summary"]
    overview = client.get("/api/v1/overview").json()
    assert [call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"]] == []


def test_agent_run_blocks_skill_not_allowed_by_profile_before_tool_execution(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    skill_dir = tmp_path / "skills" / "backend-api-contract"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "name: backend-api-contract",
                "description: Validate backend API contracts.",
                "license: MIT",
                "compatibility: AIDO",
                "inputs: [openapi]",
                "outputs: [contract-report]",
                "tools: [pytest]",
                "risk_level: low",
                "instructions: Run API contract checks.",
            ]
        ),
        encoding="utf-8",
    )
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Denied Skill", path=tmp_path / "denied-skill", template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="denied-skill",
        agent_id="skill_agent",
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    synced = client.post(
        "/api/v1/skills/sync", json={"skillsPath": str(tmp_path / "skills")}, headers=headers
    )
    skill = synced.json()["skills"][0]
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "skill_agent",
            "name": "Skill Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedSkills": [],
            "allowedTools": ["shell"],
        },
        headers=headers,
    )

    response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "skill_agent",
            "taskId": "denied-skill",
            "input": {
                "skillIds": [skill["id"]],
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "argv": [sys.executable, "--version"],
                        "workspaceId": workspace["id"],
                        "workspacePath": str(workspace["path"]),
                        "path": str(workspace["path"]),
                        "execute": True,
                    }
                ],
            },
        },
        headers=headers,
    )

    assert response.status_code == 202
    agent_run = response.json()["agentRun"]
    assert agent_run["status"] == "failed"
    assert agent_run["output"]["verdict"] == "blocked"
    assert "not allowed by the agent profile" in agent_run["output"]["summary"]
    overview = client.get("/api/v1/overview").json()
    assert [call for call in overview["agentToolCalls"] if call["agentRunId"] == agent_run["id"]] == []


def test_agent_run_records_allowed_skill_version_in_execution_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    skill_dir = tmp_path / "skills" / "backend-api-contract"
    skill_dir.mkdir(parents=True)
    skill_content = "\n".join(
        [
            "name: backend-api-contract",
            "description: Validate backend API contracts.",
            "license: MIT",
            "compatibility: AIDO",
            "inputs: [openapi]",
            "outputs: [contract-report]",
            "tools: [pytest]",
            "risk_level: low",
            "instructions: Run API contract checks.",
        ]
    )
    (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Allowed Skill", path=tmp_path / "allowed-skill", template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="allowed-skill",
        agent_id="skill_agent",
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    synced = client.post(
        "/api/v1/skills/sync", json={"skillsPath": str(tmp_path / "skills")}, headers=headers
    )
    skill = synced.json()["skills"][0]
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "skill_agent",
            "name": "Skill Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedSkills": [skill["id"]],
            "allowedTools": ["shell"],
        },
        headers=headers,
    )

    response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "skill_agent",
            "taskId": "allowed-skill",
            "input": {
                "skillIds": [skill["id"]],
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "argv": [sys.executable, "--version"],
                        "workspaceId": workspace["id"],
                        "workspacePath": str(workspace["path"]),
                        "path": str(workspace["path"]),
                        "execute": True,
                    }
                ],
            },
        },
        headers=headers,
    )

    assert response.status_code == 202
    agent_run = response.json()["agentRun"]
    assert agent_run["status"] == "completed"
    assert agent_run["output"]["skills"][0]["id"] == skill["id"]
    assert (
        agent_run["output"]["skills"][0]["instructionsHash"]
        == hashlib.sha256(skill_content.encode("utf-8")).hexdigest()
    )
    evidence = client.get("/api/v1/evidence").json()["evidencePackages"]
    package = next(item for item in evidence if item["id"] == agent_run["output"]["evidence_refs"][0])
    assert package["hashes"][f"skill:{skill['id']}"] == agent_run["output"]["skills"][0]["instructionsHash"]
    assert package["artifacts"][0]["kind"] == "skill_instruction"
    assert package["artifacts"][0]["skillId"] == skill["id"]
    binding = store.connection.execute(
        "SELECT * FROM skill_bindings WHERE skill_id = ? AND agent_profile_id = ?",
        (skill["id"], "skill_agent"),
    ).fetchone()
    assert binding["status"] == "resolved"


def test_qa_cannot_pass_without_real_command_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="QA", path=tmp_path / "qa", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    rejected = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-no-evidence",
            "testPlan": "Run tests",
            "evidenceSource": "qa_passed_by_command",
            "qaVerdict": "passed",
        },
        headers=headers,
    )
    assert rejected.status_code == 422

    accepted = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-with-evidence",
            "testPlan": "Run tests",
            "qaVerdict": "passed",
            **real_qa_evidence_fields(),
        },
        headers=headers,
    )
    assert accepted.status_code == 201
    assert accepted.json()["evidencePackage"]["qaVerdict"] == "passed"


def test_create_evidence_promotes_large_logs_and_screenshots_to_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Artifact Evidence", path=tmp_path / "artifact-evidence", template_id="other"
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    large_log = "\n".join(f"log line {index}" for index in range(2000))
    screenshot_bytes = b"\x89PNG\r\n\x1a\n" + (b"0" * 4096)

    created = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-artifacts",
            "testPlan": "Capture logs and screenshots",
            "evidenceSource": "evidence_collected",
            "qaVerdict": "evidence_collected",
            "logs": [{"name": "pytest.log", "content": large_log}],
            "screenshotRefs": [
                {
                    "name": "failure.png",
                    "mimeType": "image/png",
                    "contentBase64": base64.b64encode(screenshot_bytes).decode("ascii"),
                }
            ],
        },
        headers=headers,
    )
    assert created.status_code == 201
    evidence = created.json()["evidencePackage"]
    assert evidence["logs"][0]["logArtifactId"].startswith("artifact-")
    assert "content" not in evidence["logs"][0]
    assert evidence["screenshotRefs"][0]["screenshotArtifactId"].startswith("artifact-")
    assert "contentBase64" not in evidence["screenshotRefs"][0]

    detail = client.get(f"/api/v1/evidence/{evidence['id']}")
    assert detail.status_code == 200
    artifacts = detail.json()["artifacts"]
    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"execution_log", "screenshot"} <= kinds
    for artifact in artifacts:
        assert artifact["hash"]
        assert Path(artifact["path"]).exists()


def test_artifact_download_requires_token_and_returns_owned_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Artifact Download", path=tmp_path / "artifact-download", template_id="other"
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    large_log = "\n".join(f"download line {index}" for index in range(2000))

    created = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-download",
            "testPlan": "Download artifact",
            "evidenceSource": "evidence_collected",
            "qaVerdict": "evidence_collected",
            "testResults": [{"command": "manual", "status": "passed"}],
            "logs": [{"name": "download.log", "content": large_log}],
        },
        headers=headers,
    )
    evidence = created.json()["evidencePackage"]
    artifact_id = evidence["logs"][0]["logArtifactId"]

    unauthenticated = client.get(f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact_id}")
    assert unauthenticated.status_code == 403

    downloaded = client.get(f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact_id}", headers=headers)
    assert downloaded.status_code == 200
    assert "download line 1999" in downloaded.text
    assert downloaded.headers["x-aido-artifact-id"] == artifact_id


def test_artifact_download_rejects_paths_outside_artifact_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Artifact Traversal", path=tmp_path / "artifact-traversal", template_id="other"
    )
    evidence = EvidenceRepository(store.connection).create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        agent_id="qa_reviewer",
        task_id="story-path-traversal",
        test_plan="Reject artifact path traversal",
        test_results=[{"command": "manual", "status": "passed"}],
        evidence_source="evidence_collected",
        qa_verdict="evidence_collected",
    )
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be readable through artifact endpoint", encoding="utf-8")
    artifact = EvidenceRepository(store.connection).create_artifact(
        project_id=project["id"],
        evidence_package_id=evidence["id"],
        kind="execution_log",
        path=str(outside),
        content_hash=hashlib.sha256(outside.read_bytes()).hexdigest(),
        metadata={"name": "outside-secret.txt"},
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    response = client.get(f"/api/v1/evidence/{evidence['id']}/artifacts/{artifact['id']}", headers=headers)
    assert response.status_code == 403


def test_artifact_cleanup_dry_run_reports_orphans_without_deleting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    artifact_root = tmp_path / ".tmp" / "evidence-artifacts"
    artifact_root.mkdir(parents=True)
    orphan = artifact_root / "orphan.log"
    orphan.write_text("orphaned log", encoding="utf-8")

    response = client.post("/api/v1/evidence/artifacts/cleanup", json={"dryRun": True}, headers=headers)

    assert response.status_code == 202
    payload = response.json()
    assert payload["dryRun"] is True
    assert payload["deletedFiles"] == []
    assert any(item["path"] == str(orphan.resolve(strict=False)) for item in payload["orphanFiles"])
    assert orphan.exists()


def test_artifact_cleanup_deletes_orphans_but_keeps_referenced_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Artifact Cleanup", path=tmp_path / "artifact-cleanup", template_id="other"
    )
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)
    large_log = "\n".join(f"cleanup line {index}" for index in range(2000))
    created = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-cleanup",
            "testPlan": "Retain referenced artifacts",
            "evidenceSource": "evidence_collected",
            "qaVerdict": "evidence_collected",
            "testResults": [{"command": "manual", "status": "passed"}],
            "logs": [{"name": "cleanup.log", "content": large_log}],
        },
        headers=headers,
    )
    evidence = created.json()["evidencePackage"]
    artifact = client.get(f"/api/v1/evidence/{evidence['id']}").json()["artifacts"][0]
    referenced = Path(artifact["path"])
    artifact_root = tmp_path / ".tmp" / "evidence-artifacts"
    orphan = artifact_root / "orphan-to-delete.log"
    orphan.write_text("delete me", encoding="utf-8")

    response = client.post("/api/v1/evidence/artifacts/cleanup", json={"dryRun": False}, headers=headers)

    assert response.status_code == 202
    payload = response.json()
    assert payload["dryRun"] is False
    assert any(item["path"] == str(orphan.resolve(strict=False)) for item in payload["deletedFiles"])
    assert not orphan.exists()
    assert referenced.exists()
    assert payload["keptReferencedFiles"] == 1


def test_artifact_cleanup_requires_local_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    response = client.post("/api/v1/evidence/artifacts/cleanup", json={"dryRun": True})

    assert response.status_code == 403


def test_artifact_retention_plan_surfaces_expired_referenced_artifacts_as_governance_risk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Retention", path=tmp_path / "retention", template_id="other")
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)
    repo = EvidenceRepository(store.connection)
    evidence = repo.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        agent_id="qa_reviewer",
        task_id="story-retention",
        test_plan="Keep evidence auditable",
        test_results=[{"command": "manual", "status": "passed"}],
        evidence_source="evidence_collected",
        qa_verdict="evidence_collected",
    )
    artifact_path = tmp_path / ".tmp" / "evidence-artifacts" / "expired.log"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("expired but referenced", encoding="utf-8")
    artifact = repo.create_artifact(
        project_id=project["id"],
        evidence_package_id=evidence["id"],
        kind="execution_log",
        path=str(artifact_path),
        content_hash="hash",
        metadata={"expiresAt": "2000-01-01T00:00:00.000Z", "retentionPolicy": "short"},
    )

    response = client.post(
        "/api/v1/evidence/artifacts/retention",
        json={"now": "2026-05-19T00:00:00.000Z", "dryRun": True},
        headers=headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["dryRun"] is True
    assert body["expiredArtifacts"][0]["id"] == artifact["id"]
    assert artifact_path.exists()

    governance = client.get("/api/v1/governance").json()
    risks = governance["risks"]
    assert any("Expired evidence artifacts" in risk["title"] for risk in risks)


def test_optional_runtime_integrations_and_mcp_registration_are_explicit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    headers = auth_headers(client)

    overview = client.get("/api/v1/integrations")
    assert overview.status_code == 200
    body = overview.json()
    assert body["optionalAdapters"]["openhands"]["required"] is False
    assert body["optionalAdapters"]["sweAgent"]["required"] is False

    denied = client.post(
        "/api/v1/integrations/mcp/register",
        json={"id": "local-docs", "command": "python -m docs_mcp", "transport": "stdio"},
    )
    assert denied.status_code == 403

    registered = client.post(
        "/api/v1/integrations/mcp/register",
        json={"id": "local-docs", "command": "python -m docs_mcp", "transport": "stdio"},
        headers=headers,
    )
    assert registered.status_code == 201
    server = registered.json()["mcpServer"]
    assert server["id"] == "local-docs"
    assert server["status"] == "registered"

    listed = client.get("/api/v1/integrations").json()
    assert any(item["id"] == "local-docs" for item in listed["mcpServers"])
