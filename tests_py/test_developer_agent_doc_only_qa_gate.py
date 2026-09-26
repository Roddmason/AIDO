"""Tests E2E: el DeveloperAgent aplica el gate de QA proporcional para historias de documentacion.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.qa_doc_gate import DOC_ONLY_SKIP_REASON
from local_control_center.app import create_app
from local_control_center.security_policy.git_command_runner import git_available, run_git
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.execution_client import CompletedExecutionClient as TestClient

pytestmark = [
    pytest.mark.usefixtures("controlled_domain_host"),
    pytest.mark.skipif(not git_available(), reason="git CLI is not available"),
]


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


@pytest.fixture
def create_client():
    with ExitStack() as _owned_fixture_resources:

        def create_owned(
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
            monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
            monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
            monkeypatch.delenv("AIDO_ENABLE_CLI_RUNTIMES", raising=False)
            store = _owned_fixture_resources.enter_context(
                closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
            )
            store.init()
            app = create_app(runtime=store, static_dir=None)
            client = _owned_fixture_resources.enter_context(TestClient(app))
            return store, client, auth_headers(client)

        yield create_owned


def create_git_project_with_test_script(
    store: ControlPlaneFixture, tmp_path: Path, name: str = "Doc Only Gate Project"
) -> dict[str, Any]:
    """Repo base con un `package.json` real, para que el discovery normal encuentre un comando."""
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# Doc only gate project\n", encoding="utf-8")
    (project_path / "package.json").write_text(
        '{"private":true,"scripts":{"test":"node -e \\"process.exit(0)\\""}}\n',
        encoding="utf-8",
        newline="\n",
    )
    assert run_git(["add", "README.md", "package.json"], cwd=project_path).returncode == 0
    commit = run_git(
        ["-c", "user.name=AIDO Tests", "-c", "user.email=aido@example.test", "commit", "-m", "init"],
        cwd=project_path,
    )
    assert commit.returncode == 0, commit.stderr
    return store.create_project(name=name, path=project_path, template_id="other")


def controlled_doc_only_runtime_status(*, filename: str, content: str) -> list[dict[str, Any]]:
    script = (
        "from pathlib import Path; "
        f"Path({filename!r}).write_text({content!r}, encoding='utf-8', newline='\\n'); "
        'print(\'{"summary":"doc only change"}\')'
    )
    return [
        {
            "id": "codex_cli",
            "kind": "cli",
            "displayName": "Controlled developer runtime",
            "configured": True,
            "available": True,
            "executable": True,
            "requiresApproval": False,
            "reason": "Controlled runtime command is available.",
            "version": "test",
            "detectedCommand": sys.executable,
            "developerAgentArgv": [sys.executable, "-c", script],
            "healthCheckedAt": None,
            "capabilities": ["code_edit"],
            "safety": {
                "workspaceBound": True,
                "shell": False,
                "structuredArgv": True,
                "network": "none",
            },
        }
    ]


def test_developer_agent_doc_only_change_reduces_qa_to_diff_check_and_skips_the_rest(
    create_client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project_with_test_script(store, tmp_path)
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="developer-agent-doc-only",
        agent_id="developer_agent",
        reason="developer agent doc only workspace",
        isolation_type="git_worktree",
    )
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_doc_only_runtime_status(
            filename="docs.md", content="# doc only change\n"
        ),
    )

    response = client.post(
        "/api/v1/agents/developer/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "developer-agent-doc-only",
            "instruction": "Update docs.md.",
            "preferredRuntime": "codex_cli",
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert "docs.md" in body["diffSummary"]["changedFiles"]
    # Sin comandos de codigo mezclados en qaResults, un cambio solo-doc con diff-check limpio
    # completa igual que cualquier historia con QA 100% passed: ya no queda "blocked".
    assert body["status"] == "completed"
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert len(body["qaResults"]) == 1
    assert body["qaResults"][0]["argv"] == ["git", "diff", "--check"]
    assert body["qaResults"][0]["status"] == "passed"

    not_applicable = body["runtimeResult"]["notApplicableCommands"]
    assert not_applicable, "el comando de test descubierto debe quedar visible como no aplicable"
    assert all(result["reason"] == DOC_ONLY_SKIP_REASON for result in not_applicable)
    assert all(result["executed"] is False for result in not_applicable)
    assert all(result["status"] == "not_applicable" for result in not_applicable)


def test_developer_agent_code_change_keeps_the_full_qa_plan(
    create_client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project_with_test_script(store, tmp_path)
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="developer-agent-code-change",
        agent_id="developer_agent",
        reason="developer agent code change workspace",
        isolation_type="git_worktree",
    )
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: controlled_doc_only_runtime_status(
            filename="app.py", content="print('not just docs')\n"
        ),
    )

    response = client.post(
        "/api/v1/agents/developer/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "developer-agent-code-change",
            "instruction": "Add app.py.",
            "preferredRuntime": "codex_cli",
            "requireApproval": False,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert "app.py" in body["diffSummary"]["changedFiles"]
    statuses = [result["status"] for result in body["qaResults"]]
    assert "skipped_with_reason" not in statuses
    assert any(result["argv"] != ["git", "diff", "--check"] for result in body["qaResults"])
    assert not body["runtimeResult"].get("notApplicableCommands")
