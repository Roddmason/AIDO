from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.runtime_status import RuntimeStatusService
from tests_py.test_aido_real_runtime_slice import create_client, create_git_project

ISSUE_TO_PR_STEP_NAMES = [
    "developer_agent",
    "qa_validation",
    "security_review",
    "architecture_review",
    "devops_validation",
    "evidence_aggregation",
    "approval",
    "branch_promotion",
    "pr_creation",
]


def controlled_developer_runtime_status() -> list[dict[str, Any]]:
    return [
        {
            "id": "codex_cli",
            "kind": "cli",
            "displayName": "Controlled DeveloperAgent runtime",
            "configured": True,
            "available": True,
            "executable": True,
            "requiresApproval": False,
            "reason": "Controlled DeveloperAgent runtime command is available for this test.",
            "version": "test",
            "detectedCommand": sys.executable,
            "developerAgentArgv": [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('issue_to_pr_real.txt').write_text('real issue_to_pr implementation\\n', encoding='utf-8')"
                ),
            ],
            "healthCheckedAt": None,
            "capabilities": ["code_edit", "chat"],
            "safety": {
                "workspaceBound": True,
                "shell": False,
                "structuredArgv": True,
                "network": "none",
            },
        }
    ]


def test_issue_to_pr_executes_real_agents_and_blocks_without_architect_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="Issue To PR Project")
    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        lambda self: controlled_developer_runtime_status(),
    )

    response = client.post(
        "/api/v1/workflows/issue-to-pr",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Implement issue to PR",
            "issueText": "Create a real file change and collect all gate evidence.",
            "preferredRuntime": "codex_cli",
            "qaCommands": [[sys.executable, "--version"]],
            "maxReworkAttempts": 1,
            "createPullRequest": False,
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    run_id = body["workflowRun"]["id"]
    assert body["workflow"]["kind"] == "issue_to_pr"
    assert body["status"] == "blocked"
    assert body["dag"]["nodes"] == ISSUE_TO_PR_STEP_NAMES
    assert body["dag"]["edges"] == [
        ["developer_agent", "qa_validation"],
        ["qa_validation", "security_review"],
        ["security_review", "architecture_review"],
        ["architecture_review", "devops_validation"],
        ["devops_validation", "evidence_aggregation"],
        ["evidence_aggregation", "approval"],
        ["approval", "branch_promotion"],
        ["branch_promotion", "pr_creation"],
    ]
    assert body["rework"]["maxAttempts"] == 1
    assert body["completion"]["allGatesPassed"] is False
    assert body["completion"]["blockedGate"] == "architecture_review"
    assert body["runtime"]["available"] is False
    assert body["runtime"]["executable"] is False
    assert body["runtime"]["reason"] == "Blocked gate: architecture_review."

    step_names = [step["name"] for step in body["workflowSteps"]]
    assert step_names == ISSUE_TO_PR_STEP_NAMES
    gate_names = [gate["name"] for gate in body["gateResults"]]
    assert gate_names == [
        "DeveloperAgent",
        "QAAgent",
        "SecurityAgent",
        "ArchitectAgent",
        "DevOpsAgent",
    ]

    evidence_packages = store.evidence.list_evidence_for_workflow_runs([run_id])
    evidence_agent_ids = {package["agentId"] for package in evidence_packages}
    assert {
        "developer_agent",
        "qa_agent",
        "security_agent",
        "architect_agent",
        "devops_agent",
        "issue_to_pr_aggregator",
    } <= evidence_agent_ids
    for package in evidence_packages:
        assert package["workflowRunId"] == run_id
        assert package["workflowStepId"]
        assert package["agentId"]
        assert package["jobId"]
        assert package["workspaceId"]

    approval_actions = [
        action
        for job in store.jobs.list_jobs_for_workflow_runs([run_id])
        for action in store.jobs.list_action_requests(job["id"])
        if action["actionType"] == "workflow.issue_to_pr.approve_issue_to_pr"
    ]
    assert approval_actions == []

    event_types = {
        row["type"]
        for row in store.connection.execute(
            "SELECT type FROM workflow_events WHERE workflow_run_id = ?",
            (run_id,),
        ).fetchall()
    }
    assert "workflow.issue_to_pr.dag.started" in event_types
    assert "workflow.issue_to_pr.gate.blocked" in event_types
    assert "workflow.issue_to_pr.evidence_aggregated" in event_types

    overview = client.get("/api/v1/overview", headers=headers).json()
    timeline_events = [event for event in overview["workflowEvents"] if event["workflowRunId"] == run_id]
    assert {event["type"] for event in timeline_events} >= {
        "workflow.issue_to_pr.dag.started",
        "workflow.issue_to_pr.gate.blocked",
        "workflow.issue_to_pr.evidence_aggregated",
    }
