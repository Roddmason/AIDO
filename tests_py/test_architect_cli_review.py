"""Claude review transport, using only deterministic fixtures and no native model."""

import hashlib
import json
import sys
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.api import validate_architect_agent_run_body
from local_control_center.agents.architect_agent import ArchitectAgentRunner
from local_control_center.agents.architect_agent_contract import (
    architect_agent_contract,
    architect_agent_readiness,
)
from local_control_center.agents.contracts import ArchitectAgentRunRequest
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py import test_architect_agent_real_runtime as architect_fixtures
from tests_py.test_architect_agent_real_runtime import (
    architect_request,
    create_diff_artifact,
    create_project_and_workspace,
)

create_client = architect_fixtures.create_client


def test_review_prompt_schema_declares_fields_enforced_by_domain_validator():
    from local_control_center.agents.architect_agent import RISK_SEVERITIES

    schema = architect_agent_contract()["outputSchema"]
    properties = schema["properties"]
    for field in ("architectureFindings", "risks", "requiredChanges"):
        item = properties[field]["items"]
        assert {"title", "description", "evidenceRefs"} <= set(item["required"])
        assert set(item["properties"]["severity"]["enum"]) == RISK_SEVERITIES
        assert item["properties"]["evidenceRefs"]["minItems"] == 1
    assert "mitigation" in properties["risks"]["items"]["required"]
    recommendation = properties["approvalRecommendation"]
    assert set(recommendation["required"]) == {"decision", "reason", "evidenceRefs"}
    assert properties["evidenceRefs"]["minItems"] == 1


@pytest.mark.parametrize("invalid", ["info_severity", "missing_title", "wrong_recommendation"])
def test_historical_review_shape_errors_remain_rejected(invalid):
    from local_control_center.agents.architect_agent import (
        ArchitectOutputValidationError,
        _validate_architect_output,
    )

    item = {"title": "Finding", "description": "Observed diff", "evidenceRefs": ["diff"]}
    output = {
        "verdict": "changes_required",
        "architectureFindings": [item],
        "risks": [],
        "requiredChanges": [],
        "approvalRecommendation": {"decision": "changes_required", "reason": "Fix", "evidenceRefs": ["diff"]},
        "evidenceRefs": ["diff"],
    }
    if invalid == "info_severity":
        item["severity"] = "info"
    elif invalid == "missing_title":
        item.pop("title")
    else:
        output["approvalRecommendation"] = {
            "recommendation": "Fix",
            "rationale": "Issue",
            "evidenceRefs": ["diff"],
        }
    with pytest.raises(ArchitectOutputValidationError):
        _validate_architect_output(output, allowed_refs={"diff"}, grounding_refs={"diff"})


def test_architect_accepts_explicit_eligible_claude_without_api_fallback():
    runtime = {
        "id": "claude_code_cli",
        "kind": "cli",
        "executable": True,
        "canRunPrompt": True,
        "configured": True,
        "capabilities": ["chat"],
    }
    result = architect_agent_readiness([runtime], preferred_runtime="claude_code_cli")
    assert result["executable"]
    assert result["selectedRuntimeId"] == "claude_code_cli"
    request = ArchitectAgentRunRequest.model_validate(
        {
            "projectId": "project-review",
            "workspaceId": "workspace-review",
            "taskId": "review",
            "diffArtifactId": "artifact-diff",
            "workflowContext": {},
            "preferredRuntime": "claude_code_cli",
        }
    )
    assert validate_architect_agent_run_body(request)["preferredRuntime"] == "claude_code_cli"


class ReviewTransport:
    def evaluate_tool_call(self, **kwargs):
        self.call = kwargs
        return {
            "toolCall": {
                "id": "review-transport",
                "status": "completed",
                "payload": {
                    "executionResult": {"returnCode": 0, "stdoutArtifactId": "review-output"},
                },
            }
        }


def test_architect_cli_is_tool_isolated_plan_only_and_preserves_model(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(name="Review", path=tmp_path / "project")
        workspace = WorkspacesRepository(connection, root=tmp_path).allocate_workspace(
            project_id=project["id"], task_id="review", agent_id="architect_agent", reason="test"
        )
        runner = ArchitectAgentRunner(connection, root=tmp_path)
        runtime = {"id": "claude_code_cli", "kind": "cli", "detectedCommand": str(tmp_path / "claude.exe")}
        profile = runner._ensure_profile(runtime)
        broker = ReviewTransport()
        before = sorted(str(p) for p in Path(workspace["path"]).rglob("*"))
        result = runner._execute_cli_runtime(
            payload={
                "projectId": project["id"],
                "model": "review-model",
                "diffArtifactId": "diff-test",
                "workflowContext": {},
            },
            runtime=runtime,
            workspace=workspace,
            agent_run={"id": "run-review"},
            job={"id": "job-review"},
            profile=profile,
            broker=broker,
            diff_text="diff --git a/task.py b/task.py\n+value = 1",
        )
        call = broker.call["tool_call"]
        assert broker.call["trusted_operation"] == "architect_agent_runtime"
        assert profile["permissionProfile"] == "plan" and profile["allowCli"]
        assert call["argv"][call["argv"].index("--permission-mode") + 1] == "plan"
        assert call["argv"][call["argv"].index("--model") + 1] == "review-model"
        assert all(flag in call["argv"] for flag in ("--safe-mode", "--tools=", "--no-session-persistence"))
        assert call["providerTransportRequired"] is True
        assert call["networkRequired"] is False and call["secretsRequired"] is False
        assert result["outputArtifactId"] == "review-output"
        assert before == sorted(str(p) for p in Path(workspace["path"]).rglob("*"))


@pytest.mark.parametrize("patch", [{"executable": False}, {"canRunPrompt": False}, {"capabilities": []}])
def test_architect_blocks_ineligible_explicit_claude(patch):
    runtime = {
        "id": "claude_code_cli",
        "executable": True,
        "canRunPrompt": True,
        "configured": True,
        "capabilities": ["chat"],
        **patch,
    }
    result = architect_agent_readiness(
        [runtime, {"id": "openai_compatible", "executable": True, "capabilities": ["chat"]}],
        preferred_runtime="claude_code_cli",
    )
    assert not result["executable"]
    assert result["selectedRuntimeId"] == "claude_code_cli"


@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", "completed"),
        ("invalid_json", "failed_validation"),
        ("invented_reference", "failed_validation"),
        ("nonzero", "failed"),
        ("timeout", "failed"),
        ("cancelled", "failed"),
        ("native_fixture", "completed"),
    ],
)
def test_http_review_keeps_native_and_domain_results_separate(
    create_client,
    tmp_path,
    monkeypatch,
    case,
    expected,
    request,
):
    native_fixture = case == "native_fixture"
    if native_fixture:
        request.getfixturevalue("controlled_domain_host")
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="cli-review")
    diff = create_diff_artifact(store, project["id"])
    body = {
        **architect_request(project, workspace, diff),
        "preferredRuntime": "claude_code_cli",
        "model": "review-model",
    }
    runtime = {
        "id": "claude_code_cli",
        "kind": "cli",
        "executable": True,
        "configured": True,
        "available": True,
        "canRunPrompt": True,
        "capabilities": ["chat"],
        "reason": "test fixture",
        "detectedCommand": str(tmp_path / "claude.exe"),
    }
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda *_args, **_kwargs: [runtime],
    )
    refs = ["invented-ref"] if case == "invented_reference" else [diff["id"]]
    output = {
        "verdict": "approved",
        "architectureFindings": [],
        "risks": [],
        "requiredChanges": [],
        "approvalRecommendation": {"decision": "approve", "reason": "Fixture only", "evidenceRefs": refs},
        "evidenceRefs": refs,
    }
    calls = []

    from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox

    original_execute = RestrictedSubprocessSandbox.execute
    native_results = []

    def execute(_self, **kwargs):
        calls.append(kwargs)
        if native_fixture:
            # Only the model workload is replaced; policy, sandbox, supervisor and capture run.
            result = original_execute(
                _self,
                **{
                    **kwargs,
                    "argv": [sys.executable, "-c", "print(" + repr(json.dumps(output)) + ")"],
                },
            )
            native_results.append(result)
            return result
        return {
            "returnCode": 0 if case not in {"nonzero", "timeout", "cancelled"} else 1,
            "stdout": "not JSON" if case == "invalid_json" else json.dumps(output),
            "stderr": "synthetic failure" if case in {"nonzero", "timeout", "cancelled"} else "",
            "timedOut": case == "timeout",
            "cancelled": case == "cancelled",
            "blocked": False,
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute", execute
    )

    def contents():
        return {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(workspace["path"]).rglob("*")
            if p.is_file()
        }

    before = contents()
    response = client.post("/api/v1/agents/architect/runs", headers=headers, json=body)
    assert response.status_code == 202
    result = response.json()
    assert result["status"] == expected, result.get("reason")
    assert len(calls) == 1  # No hidden output repair, retry or fallback.
    assert contents() == before
    assert len(store.governance.list_architecture_decisions(project["id"])) == (expected == "completed")
    if case in {"valid", "invalid_json", "invented_reference", "native_fixture"}:
        artifact = store.evidence.get_artifact_by_id(result["runtimeResult"]["outputArtifactId"])
        assert not Path(artifact["path"]).is_relative_to(Path(workspace["path"]))
    if native_fixture:
        from local_control_center.host_resources.repository import ResourceRepository
        from local_control_center.process_supervision.repository import ManagedProcessRepository

        assert native_results[0]["returnCode"] == 0
        assert native_results[0]["managedProcessId"]
        assert native_results[0]["remainingDescendantCount"] == 0
        assert not ManagedProcessRepository(store.connection).active()
        assert not ResourceRepository(store.connection).active_leases()
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "mutation",
    [
        "untrusted",
        "wrong_profile",
        "wrong_runtime",
        "tools",
        "extra_flag",
        "workspace",
        "network",
        "secrets",
        "missing_transport",
        "no_cli_permission",
    ],
)
def test_readonly_review_boundary_rejects_generic_or_widened_calls(tmp_path, mutation):
    from local_control_center.agents.runtime_registry import PRODUCT_OWNER_CLAUDE_EXTRA_ARGS
    from local_control_center.agents.tool_broker import _architect_cli_boundary

    workspace = str(tmp_path.resolve())
    argv = [
        "claude.exe",
        "--print",
        "--permission-mode",
        "plan",
        "--add-dir",
        workspace,
        "--model",
        "review-model",
        *PRODUCT_OWNER_CLAUDE_EXTRA_ARGS,
        "--",
        "Review fixture",
    ]
    call = {
        "tool": "shell",
        "runtimeId": "claude_code_cli",
        "argv": argv,
        "providerTransportRequired": True,
        "networkRequired": False,
        "secretsRequired": False,
    }
    profile = {
        "id": "architect_agent",
        "permissionProfile": "plan",
        "allowCli": True,
        "allowedRuntimes": ["claude_code_cli"],
    }
    trusted = "architect_agent_runtime"
    if mutation == "untrusted":
        trusted = None
    elif mutation == "wrong_profile":
        profile["id"] = "developer_agent"
    elif mutation == "wrong_runtime":
        call["runtimeId"] = "codex_cli"
    elif mutation == "tools":
        argv[argv.index("--tools=")] = "--tools=Bash"
    elif mutation == "extra_flag":
        argv[1:1] = ["--permission-mode", "acceptEdits"]
    elif mutation == "workspace":
        argv[argv.index("--add-dir") + 1] = str(tmp_path.parent)
    elif mutation == "network":
        call["networkRequired"] = True
    elif mutation == "secrets":
        call["secretsRequired"] = True
    elif mutation == "missing_transport":
        call.pop("providerTransportRequired")
    elif mutation == "no_cli_permission":
        profile["allowCli"] = False
    result = _architect_cli_boundary(
        operation="architect_agent_runtime",
        trusted_operation=trusted,
        profile=profile,
        tool_call=call,
        workspace_path=workspace,
    )
    assert result and result["decision"] == "deny"
