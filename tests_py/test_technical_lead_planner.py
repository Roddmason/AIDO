from __future__ import annotations

from typing import Any

from local_control_center.backlog.technical_lead_planner import TechnicalLeadPlanner


def _available_team() -> list[dict[str, Any]]:
    return [
        {
            "agentId": "agent-tl",
            "role": "technical_lead",
            "allowedTools": ["shell", "policy.evaluate", "evidence.create"],
            "runtimePreference": ["cli", "api"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary", "handoffs"]},
            "reviewerPolicy": {"reviewerRole": "product_owner"},
        },
        {
            "agentId": "agent-architect",
            "role": "architect",
            "allowedTools": ["shell", "policy.evaluate", "evidence.create"],
            "runtimePreference": ["cli", "api"],
            "outputSchema": {"type": "object", "required": ["artifactId", "summary", "architectureDecision"]},
            "reviewerPolicy": {"reviewerRole": "technical_lead"},
        },
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
            "qualityGates",
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


def test_java_react_migration_generates_role_tasks_handoffs_and_security_review() -> None:
    result = TechnicalLeadPlanner().plan(
        _base_payload(
            productBrief={
                "id": "brief-migration",
                "title": "Migrar portal Java a React",
                "scope": "Migrate Java backend endpoints and React UI for an exposed authenticated portal.",
            },
            userStories=[
                {
                    "id": "story-java-react-migration",
                    "title": "Migrate account portal from Java views to React",
                    "asA": "authenticated customer",
                    "iWant": "to use the migrated React account portal backed by Java APIs",
                    "soThat": "the portal remains functional during modernization",
                    "priority": "high",
                    "requiredRoles": ["backend", "frontend", "backend_engineer"],
                }
            ],
            acceptanceCriteria=[
                {
                    "id": "ac-java-api",
                    "storyId": "story-java-react-migration",
                    "criterion": "The Java API preserves the existing account contract.",
                },
                {
                    "id": "ac-react-ui",
                    "storyId": "story-java-react-migration",
                    "criterion": "The React UI supports the same account workflow.",
                },
                {
                    "id": "ac-authz",
                    "storyId": "story-java-react-migration",
                    "criterion": "Authorization and session boundaries are preserved for the exposed portal.",
                },
            ],
            projectAssessment={
                "summary": {"stack": ["java", "react"], "hasTests": True},
                "changedFiles": [
                    "backend/src/main/java/com/aido/account/AccountController.java",
                    "frontend/src/features/account/AccountPortal.tsx",
                ],
            },
            intentClassification={
                "intents": ["migration", "feature"],
                "risk": "high",
                "requiredGates": ["migration_check"],
                "suggestedBranchName": "codex/java-react-migration",
            },
            risk={"level": "high", "items": ["exposed authenticated portal migration"]},
        )
    )

    tasks = result["agent_tasks"]
    roles = {task["role"] for task in tasks}
    assert {
        "technical_lead",
        "backend_engineer",
        "frontend_engineer",
        "qa_engineer",
        "security_engineer",
    } <= roles
    assert len(tasks) == len({(task["storyId"], task["role"]) for task in tasks})
    assert {task["storyId"] for task in tasks} == {"story-java-react-migration"}
    assert all(task["qualityGates"] for task in tasks)

    handoff_edges = {(handoff["fromRole"], handoff["toRole"]) for handoff in result["assignment_handoffs"]}
    assert ("product_owner", "technical_lead") in handoff_edges
    assert ("technical_lead", "backend_engineer") in handoff_edges
    assert ("technical_lead", "frontend_engineer") in handoff_edges
    assert ("backend_engineer", "qa_engineer") in handoff_edges
    assert ("frontend_engineer", "qa_engineer") in handoff_edges
    assert ("qa_engineer", "technical_lead") in handoff_edges
    assert ("security_engineer", "technical_lead") in handoff_edges


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


def test_pentest_is_created_only_for_high_risk_or_exposed_surface() -> None:
    security_only = TechnicalLeadPlanner().plan(
        _base_payload(
            productBrief={"id": "brief-token", "title": "Rotate tokens", "scope": "Internal credential rotation"},
            userStories=[
                {
                    "id": "story-token-rotation",
                    "title": "Rotate service tokens",
                    "asA": "operator",
                    "iWant": "internal service tokens rotated",
                    "soThat": "credentials stay fresh",
                    "priority": "medium",
                }
            ],
            acceptanceCriteria=[
                {
                    "id": "ac-token",
                    "storyId": "story-token-rotation",
                    "criterion": "Old internal tokens are revoked after replacement.",
                }
            ],
            intentClassification={
                "intents": ["security"],
                "risk": "medium",
                "requiredRoles": ["backend_engineer", "security_engineer"],
                "suggestedBranchName": "codex/token-rotation",
            },
            risk="medium",
        )
    )
    security_only_roles = {task["role"] for task in security_only["agent_tasks"]}
    assert "security_engineer" in security_only_roles
    assert "pentester" not in security_only_roles

    exposed_surface = TechnicalLeadPlanner().plan(
        _base_payload(
            productBrief={
                "id": "brief-webhook",
                "title": "Public webhook",
                "scope": "Expose a public webhook endpoint with signed requests.",
            },
            userStories=[
                {
                    "id": "story-public-webhook",
                    "title": "Receive signed public webhooks",
                    "asA": "partner system",
                    "iWant": "to call a public webhook",
                    "soThat": "external events are processed",
                    "priority": "high",
                }
            ],
            acceptanceCriteria=[
                {
                    "id": "ac-webhook",
                    "storyId": "story-public-webhook",
                    "criterion": "The exposed endpoint rejects unsigned external requests.",
                }
            ],
            intentClassification={
                "intents": ["feature"],
                "risk": "medium",
                "requiredRoles": ["backend_engineer"],
                "suggestedBranchName": "codex/public-webhook",
            },
            risk="medium",
        )
    )
    assert "pentester" in {task["role"] for task in exposed_surface["agent_tasks"]}


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


def test_user_story_is_not_duplicated_by_agent_aliases() -> None:
    result = TechnicalLeadPlanner().plan(
        _base_payload(
            userStories=[
                {
                    "id": "story-aliased-roles",
                    "title": "Update account API",
                    "asA": "operator",
                    "iWant": "the account API updated",
                    "soThat": "operations remain supported",
                    "requiredRoles": ["backend", "backend_engineer", "backend_developer", "qa"],
                }
            ],
            acceptanceCriteria=[
                {
                    "id": "ac-account-api",
                    "storyId": "story-aliased-roles",
                    "criterion": "The backend account API returns the updated response.",
                }
            ],
        )
    )

    task_keys = [(task["storyId"], task["role"]) for task in result["agent_tasks"]]
    assert len(task_keys) == len(set(task_keys))
    assert {task["storyId"] for task in result["agent_tasks"]} == {"story-aliased-roles"}
