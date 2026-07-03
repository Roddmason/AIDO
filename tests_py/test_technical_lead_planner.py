from __future__ import annotations

from typing import Any

from local_control_center.backlog.technical_lead_planner import TechnicalLeadPlanner


def _available_team() -> list[dict[str, Any]]:
    return [
        {
            "agentId": "agent-fe",
            "role": "frontend_engineer",
            "allowedTools": ["shell", "workspace_patch", "evidence.create"],
            "runtimePreference": ["cli"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary"]},
            "reviewerPolicy": {"reviewerRole": "technical_lead"},
        },
        {
            "agentId": "agent-be",
            "role": "backend_engineer",
            "allowedTools": ["shell", "workspace_patch", "evidence.create"],
            "runtimePreference": ["cli"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary"]},
            "reviewerPolicy": {"reviewerRole": "technical_lead"},
        },
        {
            "agentId": "agent-qa",
            "role": "qa_engineer",
            "allowedTools": ["shell", "evidence.create"],
            "runtimePreference": ["cli", "api"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary", "evidenceRefs"]},
            "reviewerPolicy": {"reviewerRole": "technical_lead"},
        },
        {
            "agentId": "agent-sec",
            "role": "security_engineer",
            "allowedTools": ["shell", "policy.evaluate", "evidence.create"],
            "runtimePreference": ["cli", "api"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary", "findings"]},
            "reviewerPolicy": {"reviewerRole": "technical_lead"},
        },
        {
            "agentId": "agent-pentest",
            "role": "pentester",
            "allowedTools": ["shell", "policy.evaluate", "evidence.create"],
            "runtimePreference": ["cli", "api"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary", "findings"]},
            "reviewerPolicy": {"reviewerRole": "security_engineer"},
        },
        {
            "agentId": "agent-devops",
            "role": "devops_engineer",
            "allowedTools": ["shell", "evidence.create"],
            "runtimePreference": ["cli"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary", "rollbackPlan"]},
            "reviewerPolicy": {"reviewerRole": "technical_lead"},
        },
    ]


def _base_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "productBrief": {"id": "brief-1", "title": "Checkout", "scope": "Checkout UI and API"},
        "userStories": [
            {
                "id": "user-story-checkout",
                "title": "Guest checkout",
                "asA": "shopper",
                "iWant": "to complete checkout through the web UI and backend API",
                "soThat": "I can buy faster",
                "priority": "high",
            }
        ],
        "acceptanceCriteria": [
            {
                "id": "ac-ui",
                "storyId": "user-story-checkout",
                "criterion": "The shopper can submit checkout from the frontend.",
            },
            {
                "id": "ac-api",
                "storyId": "user-story-checkout",
                "criterion": "The backend API validates and stores the order.",
            },
        ],
        "projectAssessment": {
            "summary": {"stack": ["python", "react"], "hasTests": True},
            "changedFiles": ["local-control-center/web/src/features/checkout/Checkout.tsx"],
        },
        "intentClassification": {
            "intents": ["feature"],
            "risk": "medium",
            "requiredRoles": ["frontend_engineer", "backend_engineer"],
            "requiredGates": ["unit_tests"],
            "suggestedBranchName": "codex/checkout",
        },
        "risk": "medium",
        "availableTeam": _available_team(),
    }
    payload.update(overrides)
    return payload


def test_frontend_backend_story_generates_frontend_backend_and_qa_tasks() -> None:
    result = TechnicalLeadPlanner().plan(_base_payload())

    tasks = result["agent_tasks"]
    roles = {task["role"] for task in tasks}
    assert roles == {"frontend_engineer", "backend_engineer", "qa_engineer"}
    assert {task["storyId"] for task in tasks} == {"user-story-checkout"}
    assert all("role" not in task["scope"].get("userStory", {}) for task in tasks)
    assert all(
        {
            "id",
            "storyId",
            "role",
            "title",
            "goal",
            "scope",
            "filesLikely",
            "acceptanceRefs",
            "outputSchema",
            "requiredTools",
            "runtimePreference",
            "reviewerRole",
            "risk",
        }
        <= task.keys()
        for task in tasks
    )
    assert {ref for task in tasks for ref in task["acceptanceRefs"]} == {"ac-ui", "ac-api"}

    qa_task = next(task for task in tasks if task["role"] == "qa_engineer")
    implementation_task_ids = {task["id"] for task in tasks if task["role"] != "qa_engineer"}
    qa_dependencies = {
        dep["dependsOnTaskId"]
        for dep in result["task_dependencies"]
        if dep["taskId"] == qa_task["id"]
    }
    assert qa_dependencies == implementation_task_ids
    assert result["branch_worktree_plan"]["branchName"] == "codex/checkout"
    assert result["branch_worktree_plan"]["strategy"] == "worktree_per_task"


def test_security_risk_activates_security_and_pentester_tasks() -> None:
    result = TechnicalLeadPlanner().plan(
        _base_payload(
            risk={"level": "critical", "items": ["token handling", "payment PII"]},
            intentClassification={
                "intents": ["security", "feature"],
                "risk": "critical",
                "requiredRoles": ["backend_engineer", "security_engineer", "pentester"],
                "requiredGates": ["security_scan", "pentest"],
                "suggestedBranchName": "codex/payment-security",
            },
        )
    )

    roles = {task["role"] for task in result["agent_tasks"]}
    assert {"security_engineer", "pentester"} <= roles
    security_ids = {gate["id"] for gate in result["quality_gates"]}
    assert {"security_scan", "threat_model", "pentest"} <= security_ids
    assert any(handoff["fromRole"] == "security_engineer" and handoff["toRole"] == "pentester" for handoff in result["assignment_handoffs"])


def test_devops_task_is_only_created_for_build_deploy_or_runtime_changes() -> None:
    without_runtime_change = TechnicalLeadPlanner().plan(_base_payload())
    assert "devops_engineer" not in {task["role"] for task in without_runtime_change["agent_tasks"]}

    with_runtime_change = TechnicalLeadPlanner().plan(
        _base_payload(
            productBrief={"id": "brief-2", "title": "Runtime deploy", "scope": "Update worker runtime"},
            projectAssessment={
                "summary": {"stack": ["python"], "hasTests": True},
                "changedFiles": ["local-control-center/scripts/start_control_center.py"],
            },
            intentClassification={
                "intents": ["feature"],
                "risk": "medium",
                "requiredRoles": ["backend_engineer"],
                "requiredGates": ["unit_tests"],
                "suggestedBranchName": "codex/runtime-change",
            },
        )
    )

    roles = {task["role"] for task in with_runtime_change["agent_tasks"]}
    assert "devops_engineer" in roles
    assert any(gate["id"] == "deploy_dry_run" for gate in with_runtime_change["quality_gates"])
