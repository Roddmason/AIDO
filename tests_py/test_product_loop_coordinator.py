from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import local_control_center.product_loop.coordinator as product_loop_coordinator
from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import (
    DEFAULT_AUTO_REWORK_ROUNDS,
    ProductLoopCoordinator,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import ThreadMemoryService

LOOP_TABLES = {"product_loops", "product_loop_transitions", "product_loop_feedback"}
# The canonical happy path: goal_received → … → delivered (delivery only via awaiting_approval).
HAPPY_PATH = [
    "discovering",
    "brief_ready",
    "architecture_review",
    "backlog_ready",
    "iteration_planning",
    "executing",
    "quality_review",
    "awaiting_approval",
    "delivered",
]


@pytest.fixture(autouse=True)
def _controlled_ollama_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.cached_ollama_status",
        lambda *, base_url=None, credential_ref=None: {
            "provider": "ollama",
            "available": True,
            "models": ["qwen2.5-coder"],
            "reason": "Controlled Ollama daemon.",
        },
    )


def test_named_ollama_resource_maps_to_product_owner_and_developer_runtime(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "team_ollama",
                "displayName": "Team Ollama",
                "providerType": "gateway",
                "apiFormat": "ollama",
                "baseUrl": "https://ollama.example.test",
                "enabled": True,
            }
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        selected = {
            "providerId": "team_ollama",
            "model": "qwen2.5-coder",
            "runtime": "local",
        }

        assert coordinator._product_owner_runtime_id_for_resource_selection(selected) == "team_ollama"
        assert coordinator._developer_runtime_id_for_resource_selection(selected) == "team_ollama"


class _RuntimeUnavailable:
    def __init__(self) -> None:
        self.run_payloads: list[dict[str, Any]] = []

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        return {
            "executable": False,
            "selectedRuntimeId": preferred_runtime or "codex_cli",
            "reason": "No executable runtime is configured.",
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        raise AssertionError("runtime must not execute when readiness is unavailable")


class _ControlledRuntime:
    def __init__(
        self,
        *,
        status: str = "completed",
        provider_usage: dict[str, Any] | None = None,
        actual_cost_usd: float | None = None,
        latency_ms: int | None = None,
        changed_files: list[str] | None = None,
        qa_verdict: str = "passed",
        qa_results: list[dict[str, Any]] | None = None,
        resource_usage: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status_value = status
        self.provider_usage = provider_usage
        self.actual_cost_usd = actual_cost_usd
        self.latency_ms = latency_ms
        self.changed_files = changed_files if changed_files is not None else ["src/app.py"]
        self.qa_verdict = qa_verdict
        self.qa_results = (
            qa_results if qa_results is not None else [{"command": "controlled qa", "status": "passed"}]
        )
        self.resource_usage = resource_usage
        self.status_checks: list[str | None] = []
        self.run_payloads: list[dict[str, Any]] = []

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        self.status_checks.append(preferred_runtime)
        return {
            "executable": True,
            "selectedRuntimeId": preferred_runtime or "controlled_test_runtime",
            "reason": "Controlled test runtime is executable.",
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        result = {
            "status": self.status_value,
            "reason": f"controlled runtime returned {self.status_value}",
            "runtime": {"id": "controlled_test_runtime", "executable": True},
            "runtimeResult": {"status": "completed"},
            "agentRun": {"id": "agent-run-controlled"},
            "job": {"id": "job-controlled"},
            "evidencePackage": {"id": "evidence-controlled", "qaVerdict": self.qa_verdict},
            "qaResults": self.qa_results,
            "diffSummary": {"changedFiles": self.changed_files},
        }
        if self.provider_usage is not None:
            result["usage"] = self.provider_usage
        if self.actual_cost_usd is not None:
            result["actualCostUsd"] = self.actual_cost_usd
        if self.latency_ms is not None:
            result["latencyMs"] = self.latency_ms
        if self.resource_usage is not None:
            result["resourceUsage"] = self.resource_usage
        return result


class _FailingRuntime(_ControlledRuntime):
    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        raise RuntimeError("controlled runtime execution crashed")


class _FailingRuntimeStatus(_ControlledRuntime):
    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        self.status_checks.append(preferred_runtime)
        raise RuntimeError("controlled DeveloperAgent runtime status crashed")


class _GitGate:
    def __init__(
        self,
        *,
        dirty: bool = False,
        gitleaks_status: str = "completed",
        branch: str = "dev",
        remotes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.dirty = dirty
        self.gitleaks_status = gitleaks_status
        self.branch = branch
        self.remotes = remotes or [{"name": "origin", "url": "git@example.invalid:aido/aido.git"}]
        self.status_calls = 0
        self.gitleaks_calls = 0

    def status(self, project_id: str) -> dict[str, Any]:
        self.status_calls += 1
        return {
            "status": "completed",
            "reason": "git status collected",
            "projectId": project_id,
            "dirty": self.dirty,
            "branch": self.branch,
            "changedFiles": ["README.md"] if self.dirty else [],
            "untrackedFiles": ["notes.local.md"] if self.dirty else [],
            "stagedFiles": ["src/staged.py"] if self.dirty else [],
            "remotes": self.remotes,
            "toolCalls": [],
            "policyDecisionIds": [],
        }

    def gitleaks_scan(self, project_id: str) -> dict[str, Any]:
        self.gitleaks_calls += 1
        blocked = self.gitleaks_status != "completed"
        return {
            "status": self.gitleaks_status,
            "reason": "gitleaks blocked delivery" if blocked else "gitleaks passed",
            "projectId": project_id,
            "deliveryBlocked": blocked,
            "gitleaks": {
                "status": "blocked" if blocked else "passed",
                "findingCount": 1 if blocked else 0,
            },
        }


class _FailingGitleaksGate(_GitGate):
    def gitleaks_scan(self, project_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.gitleaks_calls += 1
        raise RuntimeError("controlled gitleaks execution crashed")


class _FailingGitStatusGate(_GitGate):
    def status(self, project_id: str) -> dict[str, Any]:
        self.status_calls += 1
        raise RuntimeError("controlled git status crashed")


class _GitNotInitialized:
    def status(self, project_id: str) -> dict[str, Any]:
        return {
            "status": "configuration_required",
            "reason": "Project path is not a Git repository.",
            "projectId": project_id,
            "dirty": False,
            "changedFiles": [],
            "untrackedFiles": [],
            "stagedFiles": [],
            "toolCalls": [],
            "policyDecisionIds": [],
        }


class _AssessmentRunner:
    def __init__(self) -> None:
        self.project_ids: list[str] = []

    def run(self, project_id: str) -> dict[str, Any]:
        self.project_ids.append(project_id)
        return {
            "status": "completed",
            "reason": "assessment completed",
            "assessment": {"id": "assessment-controlled", "summary": {"languages": ["python"]}},
            "findings": [{"category": "architecture", "title": "Existing Python service"}],
            "artifact": {"id": "artifact-assessment-controlled"},
        }


class _AssessmentBlockedRunner:
    def __init__(self) -> None:
        self.project_ids: list[str] = []

    def run(self, project_id: str) -> dict[str, Any]:
        self.project_ids.append(project_id)
        return {
            "status": "blocked",
            "reason": "Project assessment could not read the existing workspace.",
            "assessment": {},
        }


class _ProductOwnerRunner:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.status_calls = 0
        self.status_checks: list[str | None] = []
        self.run_payloads: list[dict[str, Any]] = []

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        self.status_calls += 1
        self.status_checks.append(preferred_runtime)
        return {
            "executable": True,
            "selectedRuntimeId": preferred_runtime or "controlled_product_owner_runtime",
            "reason": "controlled ProductOwnerAgent runtime is executable",
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        return deepcopy(self.result)


class _FailingProductOwnerRunner(_ProductOwnerRunner):
    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        raise RuntimeError("controlled ProductOwnerAgent runtime crashed")


class _FailingProductOwnerStatusRunner(_ProductOwnerRunner):
    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        self.status_calls += 1
        self.status_checks.append(preferred_runtime)
        raise RuntimeError("controlled ProductOwnerAgent runtime status crashed")


class _TechnicalLeadPlanner:
    def __init__(self, *, generate_tasks: bool = True) -> None:
        self.generate_tasks = generate_tasks
        self.payloads: list[dict[str, Any]] = []

    def generate_agent_tasks(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.payloads.append(payload)
        if not self.generate_tasks:
            return []
        tasks: list[dict[str, Any]] = []
        for story in payload["userStories"]:
            tasks.append(
                {
                    "storyId": story["id"],
                    "title": f"Implement {story['title']}",
                    "description": "TechnicalLead implementation task generated from persisted HU.",
                    "role": "backend_engineer",
                    "category": "implementation",
                    "priority": story["priority"],
                    "estimateHours": 4.0,
                }
            )
        return tasks


class _FailingTechnicalLeadPlanner(_TechnicalLeadPlanner):
    def generate_agent_tasks(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.payloads.append(payload)
        raise RuntimeError("controlled TechnicalLeadPlanner crashed")


class _RoleTaskPlanner:
    def __init__(self, roles: list[str]) -> None:
        self.roles = roles
        self.payloads: list[dict[str, Any]] = []

    def generate_agent_tasks(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.payloads.append(payload)
        story = payload["userStories"][0]
        tasks: list[dict[str, Any]] = []
        for role in self.roles:
            tasks.append(
                {
                    "storyId": story["id"],
                    "title": f"{role} task for {story['title']}",
                    "description": "Role-specific task generated for TeamScheduler v2.",
                    "role": role,
                    "category": "implementation",
                    "priority": story["priority"],
                    "estimateHours": 3.0,
                }
            )
        return tasks


def _brief_patch() -> dict[str, Any]:
    return {
        "title": "Guided onboarding",
        "summary": "Help operators configure onboarding with auditable progress.",
        "problemStatement": "Operators cannot see what remains before onboarding is usable.",
        "goals": ["Show onboarding progress", "Surface missing setup"],
        "targetUsers": ["operations lead"],
        "successMetrics": ["80% of setups finish without support"],
        "scope": "Workbench onboarding guidance",
        "outOfScope": "Billing automation",
    }


def _backlog_payload() -> dict[str, Any]:
    return {
        "epics": [{"title": "Onboarding readiness", "description": "Make onboarding progress visible."}],
        "userStories": [
            {
                "epicTitle": "Onboarding readiness",
                "title": "Readiness checklist",
                "asA": "operations lead",
                "iWant": "to see missing onboarding steps",
                "soThat": "I can finish setup without support",
                "businessValue": "high",
                "acceptanceCriteria": [
                    "Given an incomplete project, when the checklist loads, then missing setup is visible."
                ],
            }
        ],
    }


def _product_owner_result(
    status: str,
    *,
    questions: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    backlog = _backlog_payload()
    output = {
        "status": status,
        "summary": "ProductOwnerAgent controlled output",
        "confidence": "medium",
        "questions": questions or [],
        "assumptions": [],
        "decisions": decisions or [],
        "productBriefPatch": _brief_patch(),
        "epics": backlog["epics"],
        "userStories": backlog["userStories"],
        "risks": [],
        "recommendedNextAction": "Continue with the next explicit product gate.",
    }
    return {
        "status": status,
        "reason": f"controlled product owner returned {status}",
        "summary": output["summary"],
        "confidence": output["confidence"],
        "questions": output["questions"],
        "decisions": output["decisions"],
        "brief": _brief_patch(),
        "productBriefPatch": _brief_patch(),
        "output": output,
        "epics": backlog["epics"],
        "userStories": backlog["userStories"],
        "productOwnerOutput": {"id": "product-owner-output-controlled"},
        "evidencePackage": {"id": "evidence-product-owner-controlled"},
    }


def _backlog_ready_po() -> _ProductOwnerRunner:
    return _ProductOwnerRunner(_product_owner_result("backlog_ready"))


class _SecurityGate:
    """Stub inyectable del SecurityAgentRunner con veredicto controlado y payloads capturados."""

    def __init__(self, verdict: str = "passed", reason: str = "Security controls passed.") -> None:
        self.verdict = verdict
        self.reason = reason
        self.run_payloads: list[dict[str, Any]] = []

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        return {
            "status": self.verdict,
            "verdict": self.verdict,
            "reason": self.reason,
            "agentRun": {"id": "security-run-controlled"},
            "evidencePackage": {"id": "security-evidence-controlled"},
            "findingsArtifact": {"id": "security-findings-controlled"},
            "findings": [],
        }


def _seed_ai_resource(
    connection,
    *,
    provider_id: str = "ollama",
    model: str = "qwen2.5-coder",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    if provider_id == "ollama":
        connection.execute(
            """
            UPDATE provider_accounts
            SET enabled = 1,
                health_status = 'healthy',
                last_health_check_at = ?,
                updated_at = ?
            WHERE provider_id = 'ollama'
            """,
            (utc_now(), utc_now()),
        )
    return AIResourceManager(connection).upsert_model_performance(
        {
            "providerId": provider_id,
            "model": model,
            "runtime": "local",
            "capabilities": capabilities or ["chat", "code", "review", "tools", "reasoning", "json"],
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "inputPricePerMtok": 0.0,
            "outputPricePerMtok": 0.0,
            "observedLatencyMs": 900,
            "observedSuccessRate": 0.86,
            "reworkRate": 0.04,
            "qualityScore": 0.82,
            "locality": "local",
            "privacyLevel": "local_private",
            "evidence": [{"id": "seed-product-loop-resource", "kind": "test_seed"}],
        }
    )


def _seed_remote_api_resource(
    connection,
    *,
    provider_id: str = "nvidia_nim",
    model: str = "nvidia/nemotron-coder",
    capabilities: list[str] | None = None,
    input_price_per_mtok: float | None = None,
    output_price_per_mtok: float | None = None,
) -> dict[str, Any]:
    return AIResourceManager(connection).upsert_model_performance(
        {
            "providerId": provider_id,
            "model": model,
            "runtime": "api",
            "capabilities": capabilities or ["chat", "code", "review", "tools", "reasoning", "json"],
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "inputPricePerMtok": input_price_per_mtok,
            "outputPricePerMtok": output_price_per_mtok,
            "observedLatencyMs": 1400,
            "observedSuccessRate": 0.96,
            "reworkRate": 0.02,
            "qualityScore": 0.95,
            "locality": "remote",
            "privacyLevel": "remote_allowed",
            "evidence": [{"id": "seed-product-loop-nvidia-resource", "kind": "test_seed"}],
        }
    )


def _enable_remote_provider_for_resource_selection(
    connection,
    *,
    provider_id: str = "nvidia_nim",
    model: str = "nvidia/nemotron-coder",
    pricing_mode: str = "unknown",
) -> None:
    os.environ.setdefault("AIDO_PRODUCT_LOOP_TEST_API_KEY", "test-key")
    now = utc_now()
    store = ProviderAccountStore(connection)
    runtime = RuntimeConfigRepository(connection)
    runtime.set_runtime_setting("runtime.remote.enabled", True)
    if provider_id == "nvidia_nim":
        runtime.set_runtime_setting("runtime.nvidia.enabled", True)
    store.upsert_provider_account(
        {
            "providerId": provider_id,
            "displayName": provider_id,
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "deploymentMode": "custom",
            "termsMode": "accepted",
            "pricingMode": pricing_mode,
            "baseUrl": f"https://{provider_id}.test/v1",
            "credentialRef": "env:AIDO_PRODUCT_LOOP_TEST_API_KEY",
            "enabled": True,
            "healthStatus": "healthy",
            "lastHealthCheckAt": now,
        }
    )
    store.upsert_model(
        {
            "providerId": provider_id,
            "model": model,
            "displayName": model,
            "modelFamily": "test",
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "supportsTools": True,
            "supportsJson": True,
            "supportsStreaming": True,
            "supportsVision": False,
            "supportsEmbeddings": False,
            "supportsRerank": False,
            "supportsReasoning": False,
            "supportsThinking": False,
            "effortLevels": [],
            "inputPricePerMtok": 0.0,
            "outputPricePerMtok": 0.0,
            "freeTier": True,
            "freeTierNotes": "test provider",
            "enabled": True,
            "source": "test",
        }
    )
    runtime.upsert_installation(
        {
            "runtimeId": provider_id,
            "kind": "api",
            "enabled": True,
            "capabilities": ["chat"],
            "preferredRoles": ["product_owner", "developer", "analyst"],
            "healthStatus": "healthy",
            "lastHealthCheckAt": now,
        }
    )
    connection.execute(
        """
        INSERT INTO runtime_capabilities (id, runtime, capability, enabled, metadata, created_at, updated_at)
        VALUES (?, ?, 'chat', 1, '{}', ?, ?)
        ON CONFLICT(runtime, capability) DO UPDATE SET enabled = 1, updated_at = excluded.updated_at
        """,
        (f"{provider_id}:chat", provider_id, now, now),
    )


def _scope_is_clear_po() -> _ProductOwnerRunner:
    output = {
        "status": "scope_is_clear",
        "summary": "Direct technical scope is clear for an existing project.",
        "confidence": "high",
        "questions": [],
        "assumptions": [
            {
                "statement": "The requested change is bounded to the current codebase.",
                "confidence": "high",
                "source": "existing_project_assessment",
            }
        ],
        "decisions": [],
        "productBriefPatch": {
            **_brief_patch(),
            "title": "Mini scope for direct technical order",
            "summary": "Apply the requested technical change in the existing project.",
            "scope": "Implement the bounded technical order without expanding product scope.",
            "outOfScope": "Unrequested adjacent refactors.",
        },
        "epics": [],
        "userStories": [],
        "risks": [],
        "recommendedNextAction": "Use this mini brief as the task scope before implementation.",
        "taskScope": {
            "type": "direct_technical_order",
            "goal": "Implement the bounded technical order in the existing project.",
            "constraints": ["Preserve existing flow", "Do not expand scope"],
        },
    }
    return _ProductOwnerRunner(
        {
            "status": "scope_is_clear",
            "reason": "ProductOwnerAgent created a mini brief for a clear direct technical order.",
            "summary": output["summary"],
            "confidence": output["confidence"],
            "brief": output["productBriefPatch"],
            "productBriefPatch": output["productBriefPatch"],
            "output": output,
            "evidencePackage": {"id": "evidence-product-owner-scope-clear"},
        }
    )


def _workspace_project(connection, tmp_path: Path, name: str) -> dict:
    project_path = tmp_path / name
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    return ProjectsRepository(connection).create_project(name=name, path=project_path, template_id="other")


def _project(connection, tmp_path: Path, name: str) -> dict:
    return ProjectsRepository(connection).create_project(name=name, path=tmp_path / name, template_id="other")


def _approval_loop_with_action(
    connection,
    tmp_path: Path,
    name: str,
) -> tuple[dict[str, Any], ProductLoopCoordinator, dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = _project(connection, tmp_path, name)
    coordinator = ProductLoopCoordinator(connection)
    loop = coordinator.start(project_id=project["id"], title="Delivery approval")
    for state in HAPPY_PATH[:-1]:
        loop = coordinator.transition(loop["id"], to_state=state)
    job = JobsRepository(connection).create_job(
        project_id=project["id"],
        kind="product_loop_delivery_approval",
        status="approval_required",
        payload={"loopId": loop["id"]},
    )["job"]
    action = JobsRepository(connection).create_action_request(
        job_id=job["id"],
        project_id=project["id"],
        action_type="product_loop.approve_delivery",
        risk_level="medium",
        command="approve product loop delivery",
        payload={"loopId": loop["id"]},
        reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
    )
    loop = coordinator.repository.update_loop_context(
        loop["id"],
        context={
            **loop["context"],
            "durableRun": {
                **dict(loop["context"].get("durableRun") or {}),
                "approval": {
                    "jobId": job["id"],
                    "actionRequestId": action["id"],
                },
            },
        },
    )
    return project, coordinator, loop, job, action


def _git_workspace_project(connection, tmp_path: Path, name: str) -> dict:
    project_path = tmp_path / name
    project_path.mkdir(parents=True, exist_ok=True)
    init = run_git(["init", "--initial-branch", "main"], cwd=project_path)
    if init.returncode != 0:
        assert run_git(["init"], cwd=project_path).returncode == 0
        assert run_git(["checkout", "-b", "main"], cwd=project_path).returncode == 0
    assert run_git(["config", "user.email", "aido-test@example.invalid"], cwd=project_path).returncode == 0
    assert run_git(["config", "user.name", "AIDO Test"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    assert run_git(["add", "."], cwd=project_path).returncode == 0
    assert run_git(["commit", "-m", "Initial commit"], cwd=project_path).returncode == 0
    return ProjectsRepository(connection).create_project(name=name, path=project_path, template_id="other")


def _remediation_action_types(connection, thread_id: str) -> set[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT blocker_type, action_type
        FROM remediation_actions
        WHERE thread_id = ? AND status = 'pending'
        ORDER BY created_at ASC, rowid ASC
        """,
        (thread_id,),
    ).fetchall()
    return {(row["blocker_type"], row["action_type"]) for row in rows}


def test_product_loop_schema_adds_tables_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase19_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 19"
        ).fetchone()["total"]

    assert tables >= LOOP_TABLES
    assert phase19_rows == 1


def test_product_loop_walks_the_happy_path_to_delivered(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "happy")
        coordinator = ProductLoopCoordinator(connection)

        loop = coordinator.start(project_id=project["id"], title="Onboarding")
        assert loop["state"] == "goal_received"
        assert loop["status"] == "active"
        assert loop["version"] == 1

        for state in HAPPY_PATH:
            loop = coordinator.transition(loop["id"], to_state=state)

        assert loop["state"] == "delivered"
        assert loop["status"] == "delivered"
        assert loop["version"] == 1 + len(HAPPY_PATH)
        resume = coordinator.resume(loop["id"])
        assert resume["resumable"] is False
        assert resume["allowedNextStates"] == []
        assert [t["toState"] for t in coordinator.list_transitions(loop["id"])] == [
            "goal_received",
            *HAPPY_PATH,
        ]


def test_product_loop_is_durable_and_resumes_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"

    # --- AIDO session 1: start the loop (with governance) and advance it, then "shut down". ---
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "durable")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(
            project_id=project_id,
            title="Self-serve",
            context={"idea": "self-serve onboarding"},
            correlation_id="corr-7",
            budget={"agentRuns": 9},
            max_rework_rounds=2,
        )
        coordinator.transition(loop["id"], to_state="discovering", trigger="discovery_started")
        coordinator.transition(loop["id"], to_state="awaiting_user", context_patch={"openQuestions": 3})
        loop_id = loop["id"]

    # --- Restart AIDO: a brand-new connection and coordinator, nothing kept in memory. ---
    with open_sqlite_connection(db_path) as connection:
        coordinator = ProductLoopCoordinator(connection)
        resumed = coordinator.resume(loop_id)
        assert resumed["loop"]["state"] == "awaiting_user"  # recovered straight from the database
        assert resumed["loop"]["version"] == 3
        # Domain context and governance both survive the restart.
        assert resumed["loop"]["context"]["idea"] == "self-serve onboarding"
        assert resumed["loop"]["context"]["openQuestions"] == 3
        assert resumed["loop"]["context"]["fsm"]["correlationId"] == "corr-7"
        assert resumed["loop"]["context"]["fsm"]["policy"]["budget"] == {"agentRuns": 9}
        assert resumed["loop"]["context"]["fsm"]["policy"]["maxReworkRounds"] == 2
        assert resumed["resumable"] is True
        assert "discovering" in resumed["allowedNextStates"]

        # The loop continues exactly where it left off before the restart.
        coordinator.transition(loop_id, to_state="discovering", trigger="user_answered")
        loop = coordinator.transition(loop_id, to_state="brief_ready", trigger="discovery_completed")
        assert loop["state"] == "brief_ready"
        assert [t["toState"] for t in coordinator.list_transitions(loop_id)] == [
            "goal_received",
            "discovering",
            "awaiting_user",
            "discovering",
            "brief_ready",
        ]
        assert coordinator.list_loops(project_id)[0]["id"] == loop_id


def test_run_user_message_blocks_new_loop_when_runtime_is_not_executable(tmp_path: Path) -> None:
    runtime = _RuntimeUnavailable()
    git = _GitGate()
    product_owner = _backlog_ready_po()
    assessment = _AssessmentRunner()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "no-runtime")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=assessment,
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert "runtime" in result["reason"].lower()
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedReason"] == result["reason"]
        durable_thread = result["loop"]["context"]["durableRun"]["thread"]
        assert durable_thread["projectThreadId"].startswith("thread-")
        assert durable_thread["messageId"].startswith("thread-msg-")
        assert connection.execute("SELECT COUNT(*) AS total FROM sessions").fetchone()["total"] == 0
        assert connection.execute("SELECT COUNT(*) AS total FROM chats").fetchone()["total"] == 0
        assert result["loop"]["context"]["durableRun"]["evidencePackageIds"]
        assert runtime.run_payloads == []
        assert [item["toState"] for item in result["transitions"]] == [
            "goal_received",
            "workspace_check",
            "git_check",
            "runtime_check",
            "discovery",
            "planning",
            "backlog_ready",
            "blocked",
        ]
        assert product_owner.run_payloads
        assert technical_lead.payloads
        assert ("runtime_not_executable", "open_settings_section") in actions
        assert ("runtime_not_executable", "validate_runtime") in actions
        assert ("runtime_not_executable", "switch_runtime") in actions
        assert ("runtime_not_executable", "retry_loop") in actions


def test_run_user_message_blocks_when_developer_runtime_status_crashes(tmp_path: Path) -> None:
    runtime = _FailingRuntimeStatus()
    git = _GitGate()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "developer-runtime-status-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding while DeveloperAgent readiness crashes.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "runtime"
        assert durable["runtime"]["status"] == "failed"
        assert durable["runtime"]["executable"] is False
        assert "controlled DeveloperAgent runtime status crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert runtime.status_checks
        assert product_owner.run_payloads
        assert technical_lead.payloads
        assert ("runtime_not_executable", "validate_runtime") in actions
        assert ("runtime_not_executable", "switch_runtime") in actions


def test_run_user_message_blocks_missing_workspace_root_with_workspace_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "missing-workspace-root")
        coordinator = ProductLoopCoordinator(connection)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "workspace_check"
        assert "root is required" in result["reason"]
        assert ("workspace_root_missing", "open_settings_section") in actions
        assert ("workspace_root_missing", "retry_loop") in actions


def test_retry_loop_remediation_queues_real_thread_product_loop_retry(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "retry-queues-real-run")
        coordinator = ProductLoopCoordinator(connection)
        message = "Implement onboarding readiness."
        unapproved_resources = [
            {
                "role": "backend_engineer",
                "providerId": "nvidia_nim",
                "model": "nvidia/nemotron-coder",
                "runtime": "api",
            }
        ]

        result = coordinator.run_user_message(
            project_id=project["id"],
            message=message,
            run_metadata={
                "planOnly": True,
                "teamMode": "critical",
                "risk": "high",
                "privacyLevel": "local_private",
            },
        )

        durable_thread = result["loop"]["context"]["durableRun"]["thread"]
        thread_id = durable_thread["projectThreadId"]
        message_id = durable_thread["messageId"]
        durable = dict(result["loop"]["context"]["durableRun"])
        request_meta = dict(durable.get("requestMeta") or {})
        request_meta["approvedResourceSelections"] = unapproved_resources
        result["loop"] = coordinator.repository.update_loop_context(
            result["loop"]["id"],
            context={
                **result["loop"]["context"],
                "durableRun": {
                    **durable,
                    "requestMeta": request_meta,
                },
            },
        )
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id, result["loop"]["id"]),
        ).fetchone()
        assert action_row is not None

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )

        retry_job = JobsRepository(connection).get_job(execution["execution"]["job"]["id"])
        retried_loop = coordinator.get(result["loop"]["id"])
        events = ThreadsRepository(connection).list_events(thread_id)
        assert execution["execution"]["status"] == "queued"
        assert execution["execution"]["action"] == "retry_loop"
        assert execution["execution"]["retryOfLoopId"] == result["loop"]["id"]
        assert retry_job["kind"] == "thread.product_loop.run"
        assert retry_job["status"] == "queued"
        assert retry_job["payload"]["threadId"] == thread_id
        assert retry_job["payload"]["messageId"] == message_id
        assert retry_job["payload"]["message"] == message
        assert retry_job["payload"]["root"] == str(tmp_path.resolve(strict=False))
        assert retry_job["payload"]["planOnly"] is True
        assert retry_job["payload"]["runMetadata"]["teamMode"] == "critical"
        assert retry_job["payload"]["runMetadata"]["risk"] == "high"
        assert "allowUnknownCost" not in retry_job["payload"]["runMetadata"]
        assert "requireApprovalForUnknownCost" not in retry_job["payload"]["runMetadata"]
        assert retry_job["payload"]["runMetadata"]["privacyLevel"] == "local_private"
        assert retry_job["payload"]["retryOfLoopId"] == result["loop"]["id"]
        assert retry_job["payload"]["runMetadata"]["retryOfLoopId"] == result["loop"]["id"]
        assert retry_job["payload"]["runMetadata"]["retryStage"] == "workspace_check"
        assert retry_job["payload"]["runMetadata"]["retryReason"] == result["reason"]
        assert retry_job["payload"]["runMetadata"]["remediationActionId"] == action_row["id"]
        assert retry_job["payload"]["runMetadata"]["retryQueuedAt"]
        assert retry_job["payload"]["approvedResourceSelections"] == []
        assert retry_job["payload"]["runMetadata"]["approvedResourceSelections"] == []
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] == "queued"
        assert retried_loop["state"] == "cancelled"
        assert retried_loop["context"]["durableRun"]["status"] == "retry_queued"
        assert retried_loop["context"]["durableRun"]["retry"]["jobId"] == retry_job["id"]
        assert execution["remediation"]["status"] == "resolved"
        assert any(
            event["type"] == "run_queued"
            and event["payload"].get("jobId") == retry_job["id"]
            and event["payload"].get("retryOfLoopId") == result["loop"]["id"]
            for event in events
        )


def test_retry_loop_remediation_rolls_back_job_when_loop_supersede_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "retry-atomic-rollback")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
        )
        loop_id = result["loop"]["id"]
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id, loop_id),
        ).fetchone()
        assert action_row is not None
        initial_event_ids = {event["id"] for event in ThreadsRepository(connection).list_events(thread_id)}

        def fail_supersede(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("forced retry supersede failure")

        monkeypatch.setattr(ProductLoopCoordinator, "transition_in_transaction", fail_supersede)

        with pytest.raises(RuntimeError, match="forced retry supersede failure"):
            BlockerRemediationService(connection, root=tmp_path).execute(
                action_row["id"],
                platform=object(),
            )

        retry_jobs = [
            job
            for job in JobsRepository(connection).list_jobs(project["id"])
            if job["kind"] == "thread.product_loop.run"
        ]
        assert retry_jobs == []
        assert coordinator.get(loop_id)["state"] == "blocked"
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] != "queued"
        assert {
            event["id"] for event in ThreadsRepository(connection).list_events(thread_id)
        } == initial_event_ids
        action = BlockerRemediationService(connection, root=tmp_path).repository.get(action_row["id"])
        assert action["status"] == "pending"


def test_retry_loop_remediation_builds_job_from_context_revalidated_under_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "retry-fresh-context")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
        )
        loop_id = result["loop"]["id"]
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id, loop_id),
        ).fetchone()
        assert action_row is not None
        approved_resources = [
            {
                "role": "product_owner",
                "providerId": "ollama",
                "model": "qwen3:14b",
                "runtime": "local",
            }
        ]
        service = BlockerRemediationService(connection, root=tmp_path)
        original_get = service.repository.get
        get_calls = 0

        def inject_approval_before_locked_loop_read(action_id: str) -> dict[str, Any]:
            nonlocal get_calls
            get_calls += 1
            if get_calls == 2:
                current_loop = coordinator.get(loop_id)
                durable = dict(current_loop["context"]["durableRun"])
                coordinator.repository.update_loop_context(
                    loop_id,
                    context={
                        **current_loop["context"],
                        "durableRun": {
                            **durable,
                            "resourceApproval": {
                                "status": "approved",
                                "approvedResourceSelections": approved_resources,
                            },
                        },
                    },
                )
            return original_get(action_id)

        monkeypatch.setattr(service.repository, "get", inject_approval_before_locked_loop_read)

        execution = service.execute(action_row["id"], platform=object())

        retry_job = execution["execution"]["job"]
        assert get_calls >= 2
        assert retry_job["payload"]["approvedResourceSelections"] == approved_resources
        assert retry_job["payload"]["runMetadata"]["approvedResourceSelections"] == approved_resources


def test_retry_loop_remediation_rejects_stale_blocked_stage(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "retry-stale-stage")
        coordinator = ProductLoopCoordinator(connection)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
        )

        durable = dict(result["loop"]["context"]["durableRun"])
        durable_thread = durable["thread"]
        thread_id = durable_thread["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id, result["loop"]["id"]),
        ).fetchone()
        assert action_row is not None
        assert durable["blockedStage"] == "workspace_check"
        coordinator.repository.update_loop_context(
            result["loop"]["id"],
            context={
                **result["loop"]["context"],
                "durableRun": {
                    **durable,
                    "blockedStage": "runtime",
                    "blockedReason": "Runtime became unavailable after workspace recovery.",
                },
            },
        )

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )
        retry_jobs = connection.execute(
            """
            SELECT id
            FROM jobs
            WHERE kind = 'thread.product_loop.run'
              AND json_extract(payload, '$.retryOfLoopId') = ?
            """,
            (result["loop"]["id"],),
        ).fetchall()

        assert execution["execution"]["status"] == "blocked"
        assert execution["execution"]["action"] == "retry_loop"
        assert execution["execution"]["blockedStage"] == "runtime"
        assert execution["execution"]["remediationStage"] == "workspace_check"
        assert "no longer matches" in execution["execution"]["reason"]
        assert execution["remediation"]["status"] == "pending"
        assert retry_jobs == []
        assert coordinator.get(result["loop"]["id"])["state"] == "blocked"


@pytest.mark.parametrize("failure_point", ["thread_status", "thread_event"])
def test_retry_loop_remediation_rolls_back_when_thread_queue_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "retry-status-crash")
        coordinator = ProductLoopCoordinator(connection)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
        )

        durable_thread = result["loop"]["context"]["durableRun"]["thread"]
        thread_id = durable_thread["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id, result["loop"]["id"]),
        ).fetchone()
        assert action_row is not None
        initial_loop = coordinator.get(result["loop"]["id"])
        initial_event_ids = {event["id"] for event in ThreadsRepository(connection).list_events(thread_id)}
        original_set_status = ThreadsRepository.set_status
        original_record_event = ThreadsRepository.record_event

        def crash_queued_thread_status(
            self: ThreadsRepository,
            target_thread_id: str,
            status: str,
        ) -> dict[str, Any]:
            if failure_point == "thread_status" and status == "queued":
                raise RuntimeError("controlled retry thread persistence crashed")
            return original_set_status(self, target_thread_id, status)

        def crash_run_queued_event(
            self: ThreadsRepository,
            *,
            thread_id: str,
            type: str,
            payload: dict[str, Any] | None = None,
            agent_role: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if failure_point == "thread_event" and type == "run_queued":
                raise RuntimeError("controlled retry thread persistence crashed")
            return original_record_event(
                self,
                thread_id=thread_id,
                type=type,
                payload=payload,
                agent_role=agent_role,
                metadata=metadata,
            )

        monkeypatch.setattr(ThreadsRepository, "set_status", crash_queued_thread_status)
        monkeypatch.setattr(ThreadsRepository, "record_event", crash_run_queued_event)

        with pytest.raises(RuntimeError, match="controlled retry thread persistence crashed"):
            BlockerRemediationService(connection, root=tmp_path).execute(
                action_row["id"],
                platform=object(),
            )

        retried_loop = coordinator.get(result["loop"]["id"])
        retry_jobs = [
            job
            for job in JobsRepository(connection).list_jobs(project["id"])
            if job["kind"] == "thread.product_loop.run"
        ]
        assert retry_jobs == []
        assert retried_loop["state"] == "blocked"
        assert retried_loop["version"] == initial_loop["version"]
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] != "queued"
        assert {
            event["id"] for event in ThreadsRepository(connection).list_events(thread_id)
        } == initial_event_ids
        action = BlockerRemediationService(connection, root=tmp_path).repository.get(action_row["id"])
        assert action["status"] == "pending"


def test_continue_plan_only_remediation_queues_real_plan_only_thread_run(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(status="failed")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "plan-only-remediation")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        message = "Implement onboarding readiness."
        approved_resources = [
            {
                "role": "backend_engineer",
                "providerId": "ollama",
                "model": "qwen2.5-coder",
                "runtime": "local",
            }
        ]

        result = coordinator.run_user_message(
            project_id=project["id"],
            message=message,
            run_metadata={
                "teamMode": "critical",
                "risk": "high",
                "privacyLevel": "local_private",
                "userMode": "aido_decide",
                "autonomy": "guided",
                "researchPolicy": {"requireOfficialSources": True},
            },
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable_thread = result["loop"]["context"]["durableRun"]["thread"]
        thread_id = durable_thread["projectThreadId"]
        durable = dict(result["loop"]["context"]["durableRun"])
        request_meta = dict(durable.get("requestMeta") or {})
        request_meta["approvedResourceSelections"] = approved_resources
        result["loop"] = coordinator.repository.update_loop_context(
            result["loop"]["id"],
            context={
                **result["loop"]["context"],
                "durableRun": {
                    **durable,
                    "requestMeta": request_meta,
                    "resourceApproval": {
                        "status": "approved",
                        "approvedResourceSelections": approved_resources,
                    },
                },
            },
        )
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'continue_plan_only'
              AND status = 'pending'
            """,
            (thread_id, result["loop"]["id"]),
        ).fetchone()
        assert action_row is not None

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )

        plan_job = JobsRepository(connection).get_job(execution["execution"]["job"]["id"])
        superseded_loop = coordinator.get(result["loop"]["id"])
        events = ThreadsRepository(connection).list_events(thread_id)
        assert execution["execution"]["status"] == "queued"
        assert execution["execution"]["action"] == "continue_plan_only"
        assert execution["execution"]["planOnlyOfLoopId"] == result["loop"]["id"]
        assert plan_job["kind"] == "thread.product_loop.run"
        assert plan_job["status"] == "queued"
        assert plan_job["payload"]["threadId"] == thread_id
        assert plan_job["payload"]["message"] == message
        assert plan_job["payload"]["planOnly"] is True
        assert plan_job["payload"]["planOnlyOfLoopId"] == result["loop"]["id"]
        assert plan_job["payload"]["runMetadata"]["planOnly"] is True
        assert plan_job["payload"]["runMetadata"]["planOnlyOfLoopId"] == result["loop"]["id"]
        assert plan_job["payload"]["runMetadata"]["planOnlyStage"] == "runtime"
        assert plan_job["payload"]["runMetadata"]["teamMode"] == "critical"
        assert plan_job["payload"]["runMetadata"]["risk"] == "high"
        assert "allowUnknownCost" not in plan_job["payload"]["runMetadata"]
        assert "requireApprovalForUnknownCost" not in plan_job["payload"]["runMetadata"]
        assert plan_job["payload"]["runMetadata"]["privacyLevel"] == "local_private"
        assert plan_job["payload"]["runMetadata"]["userMode"] == "aido_decide"
        assert plan_job["payload"]["runMetadata"]["autonomy"] == "guided"
        assert plan_job["payload"]["runMetadata"]["researchPolicy"] == {"requireOfficialSources": True}
        assert plan_job["payload"]["runMetadata"]["approvedResourceSelections"] == approved_resources
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] == "queued"
        assert superseded_loop["state"] == "cancelled"
        assert superseded_loop["context"]["durableRun"]["status"] == "plan_only_queued"
        assert superseded_loop["context"]["durableRun"]["planOnlyRetry"]["jobId"] == plan_job["id"]
        assert execution["remediation"]["status"] == "resolved"
        assert any(
            event["type"] == "run_queued"
            and event["payload"].get("jobId") == plan_job["id"]
            and event["payload"].get("planOnlyOfLoopId") == result["loop"]["id"]
            for event in events
        )


def test_continue_plan_only_remediation_rejects_stale_blocked_stage(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(status="failed")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "plan-only-stale-stage")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = dict(result["loop"]["context"]["durableRun"])
        thread_id = durable["thread"]["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'continue_plan_only'
              AND status = 'pending'
            """,
            (thread_id, result["loop"]["id"]),
        ).fetchone()
        assert action_row is not None
        assert durable["blockedStage"] == "runtime"
        coordinator.repository.update_loop_context(
            result["loop"]["id"],
            context={
                **result["loop"]["context"],
                "durableRun": {
                    **durable,
                    "blockedStage": "qa",
                    "blockedReason": "QA became the active blocker after runtime recovery.",
                },
            },
        )

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )
        plan_jobs = connection.execute(
            """
            SELECT id
            FROM jobs
            WHERE kind = 'thread.product_loop.run'
              AND json_extract(payload, '$.planOnlyOfLoopId') = ?
            """,
            (result["loop"]["id"],),
        ).fetchall()

        assert execution["execution"]["status"] == "blocked"
        assert execution["execution"]["action"] == "continue_plan_only"
        assert execution["execution"]["blockedStage"] == "qa"
        assert execution["execution"]["remediationStage"] == "runtime"
        assert "no longer matches" in execution["execution"]["reason"]
        assert execution["remediation"]["status"] == "pending"
        assert plan_jobs == []
        assert coordinator.get(result["loop"]["id"])["state"] == "blocked"


def test_continue_plan_only_remediation_keeps_queued_job_when_thread_status_update_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime(status="failed")
    status_calls = {"queued": 0}
    original_set_status = ThreadsRepository.set_status

    def crash_queued_thread_status(
        self: ThreadsRepository,
        thread_id: str,
        status: str,
    ) -> dict[str, Any]:
        if status == "queued":
            status_calls["queued"] += 1
            raise RuntimeError("controlled plan-only thread status crashed")
        return original_set_status(self, thread_id, status)

    monkeypatch.setattr(ThreadsRepository, "set_status", crash_queued_thread_status)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "plan-only-status-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable_thread = result["loop"]["context"]["durableRun"]["thread"]
        thread_id = durable_thread["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND action_type = 'continue_plan_only'
              AND status = 'pending'
            """,
            (thread_id, result["loop"]["id"]),
        ).fetchone()
        assert action_row is not None

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )

        plan_job = JobsRepository(connection).get_job(execution["execution"]["job"]["id"])
        superseded_loop = coordinator.get(result["loop"]["id"])
        assert status_calls["queued"] == 1
        assert execution["execution"]["status"] == "queued"
        assert execution["execution"]["action"] == "continue_plan_only"
        assert execution["execution"]["threadStatusUpdate"]["status"] == "failed"
        assert (
            "controlled plan-only thread status crashed"
            in execution["execution"]["threadStatusUpdate"]["reason"]
        )
        assert plan_job["status"] == "queued"
        assert plan_job["payload"]["planOnly"] is True
        assert superseded_loop["state"] == "cancelled"
        assert superseded_loop["context"]["durableRun"]["status"] == "plan_only_queued"
        assert execution["remediation"]["status"] == "resolved"


def test_product_owner_runtime_block_creates_runtime_remediation_actions(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "po-runtime-blocked")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Define onboarding readiness.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(),
            product_owner_runner=_RuntimeUnavailable(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner_runtime"
        assert durable["productOwner"]["status"] == "runtime_unavailable"
        assert durable["productOwner"]["resourceDecision"]["selected"]["providerId"] == "ollama"
        assert ("runtime_not_executable", "validate_runtime") in actions
        assert ("runtime_not_executable", "switch_runtime") in actions


def test_run_user_message_blocks_when_product_owner_runtime_status_crashes(tmp_path: Path) -> None:
    product_owner = _FailingProductOwnerStatusRunner(_product_owner_result("backlog_ready"))
    runtime = _ControlledRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "po-runtime-status-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Define onboarding while ProductOwnerAgent readiness crashes.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner_runtime"
        assert durable["productOwner"]["status"] == "runtime_unavailable"
        assert durable["productOwner"]["runtimeReadiness"]["status"] == "failed"
        assert durable["productOwner"]["runtimeReadiness"]["executable"] is False
        assert "controlled ProductOwnerAgent runtime status crashed" in result["reason"]
        assert product_owner.status_calls == 1
        assert product_owner.run_payloads == []
        assert runtime.run_payloads == []
        assert ("runtime_not_executable", "validate_runtime") in actions
        assert ("runtime_not_executable", "switch_runtime") in actions


def test_run_user_message_blocks_project_assessment_with_assessment_remediation(tmp_path: Path) -> None:
    product_owner = _backlog_ready_po()
    assessment = _AssessmentBlockedRunner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "assessment-blocked")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness after assessment.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=assessment,
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "project_assessment"
        assert assessment.project_ids == [project["id"]]
        assert product_owner.run_payloads == []
        assert ("project_assessment_failed", "open_settings_section") in actions
        assert ("project_assessment_failed", "retry_loop") in actions


def test_git_not_initialized_block_creates_git_init_remediation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "git-not-initialized")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement on a folder without Git metadata.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitNotInitialized(),
            product_owner_runner=_backlog_ready_po(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        action_rows = connection.execute(
            """
            SELECT action_type, payload_json
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'git_not_initialized'
            ORDER BY created_at ASC, rowid ASC
            """,
            (thread_id,),
        ).fetchall()
        payloads = {row["action_type"]: json.loads(row["payload_json"] or "{}") for row in action_rows}
        assert result["status"] == "blocked"
        assert ("git_not_initialized", "git_init") in actions
        assert ("git_not_initialized", "retry_loop") in actions
        assert payloads["git_init"]["status"] == "configuration_required"
        assert payloads["git_init"]["projectId"] == project["id"]
        assert payloads["git_init"]["dirty"] is False
        assert payloads["git_init"]["changedFiles"] == []
        assert payloads["git_init"]["stagedFiles"] == []
        assert payloads["git_init"]["untrackedFiles"] == []
        assert payloads["git_init"]["dirtyFileCount"] == 0
        assert payloads["git_init"]["defaultBranch"] == "dev"
        assert "not a Git repository" in payloads["git_init"]["reason"]
        assert payloads["retry_loop"]["retryTarget"] == "git_not_initialized"


def test_run_user_message_blocks_when_git_status_crashes(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    git = _FailingGitStatusGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "git-status-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement on a repository whose git status crashes.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        action_rows = connection.execute(
            """
            SELECT action_type, payload_json
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'git_status_failed'
            ORDER BY created_at ASC, rowid ASC
            """,
            (thread_id,),
        ).fetchall()
        payloads = {row["action_type"]: json.loads(row["payload_json"] or "{}") for row in action_rows}
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "git"
        assert durable["git"]["status"] == "failed"
        assert "controlled git status crashed" in result["reason"]
        assert git.status_calls == 1
        assert product_owner.run_payloads == []
        assert runtime.run_payloads == []
        assert ("git_status_failed", "open_settings_section") in actions
        assert ("git_status_failed", "retry_loop") in actions
        assert payloads["open_settings_section"]["section"] == "workspaces"
        assert payloads["open_settings_section"]["status"] == "failed"
        assert payloads["open_settings_section"]["projectId"] == project["id"]
        assert payloads["open_settings_section"]["dirty"] is False
        assert payloads["open_settings_section"]["changedFiles"] == []
        assert payloads["open_settings_section"]["stagedFiles"] == []
        assert payloads["open_settings_section"]["untrackedFiles"] == []
        assert payloads["open_settings_section"]["dirtyFileCount"] == 0
        assert "controlled git status crashed" in payloads["open_settings_section"]["reason"]
        assert payloads["retry_loop"]["retryTarget"] == "git_status_failed"


def test_dirty_git_block_creates_diff_branch_and_patch_remediations(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "dirty-remediation")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement on a dirty repository.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(dirty=True),
            product_owner_runner=_backlog_ready_po(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        remediation_records = BlockerRemediationService(connection, root=tmp_path).repository.list_for_thread(
            thread_id
        )
        view_diff = next(action for action in remediation_records if action["actionType"] == "view_diff")
        create_branch = next(
            action for action in remediation_records if action["actionType"] == "create_branch"
        )
        retry = next(action for action in remediation_records if action["actionType"] == "retry_loop")
        assert result["status"] == "blocked"
        assert ("git_dirty_tree", "view_diff") in actions
        assert ("git_dirty_tree", "create_branch") in actions
        assert ("git_dirty_tree", "save_patch") in actions
        assert ("git_dirty_tree", "retry_loop") in actions
        assert view_diff["payload"]["dirty"] is True
        assert view_diff["payload"]["branch"] == "dev"
        assert view_diff["payload"]["changedFiles"] == ["README.md"]
        assert view_diff["payload"]["stagedFiles"] == ["src/staged.py"]
        assert view_diff["payload"]["untrackedFiles"] == ["notes.local.md"]
        assert view_diff["payload"]["remoteNames"] == ["origin"]
        assert view_diff["payload"]["dirtyFileCount"] == 3
        assert create_branch["payload"]["branchName"] == "codex/remediate-dirty-tree"
        assert retry["payload"]["retryTarget"] == "git_dirty_tree"


def test_block_run_creates_generic_retry_when_remediation_mapping_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash_remediations(
        self: BlockerRemediationService, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled remediation mapping crashed")

    monkeypatch.setattr(
        BlockerRemediationService,
        "create_for_blocked_run",
        crash_remediations,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "dirty-remediation-fallback")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement on a dirty repository.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(dirty=True),
            product_owner_runner=_backlog_ready_po(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "git"
        assert ("git_dirty_tree", "retry_loop") in actions


def test_run_user_message_incomplete_idea_awaits_user_without_developer_execution(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    assessment = _AssessmentRunner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Needs input",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        approved_resources = [
            {
                "role": "product_owner",
                "providerId": "ollama",
                "model": "qwen2.5-coder",
                "runtime": "local",
            }
        ]
        source_loop = coordinator.start(
            project_id=project["id"],
            title="Approved ProductOwner retry source",
            context={
                "durableRun": {
                    "resourceApproval": {
                        "status": "approved",
                        "approvedResourceSelections": approved_resources,
                    }
                }
            },
        )
        retry_action = BlockerRemediationService(connection, root=tmp_path).repository.create_action(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=source_loop["id"],
            stage="resource_manager",
            blocker_type="resource_manager_approval_required",
            title="Retry loop",
            description="Retry after approving ProductOwnerAgent routing.",
            action_type="retry_loop",
            payload={},
        )

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            run_metadata={
                "teamMode": "critical",
                "risk": "high",
                "privacyLevel": "local_private",
                "autonomy": "guided",
                "researchPolicy": {"requireOfficialSources": True},
                "retryOfLoopId": source_loop["id"],
                "remediationActionId": retry_action["id"],
                "approvedResourceSelections": approved_resources,
            },
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=assessment,
            thread_id=thread["id"],
        )

        assert result["status"] == "awaiting_user"
        assert result["loop"]["state"] == "awaiting_user"
        assert result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"] == thread["id"]
        assert connection.execute("SELECT COUNT(*) AS total FROM sessions").fetchone()["total"] == 0
        assert connection.execute("SELECT COUNT(*) AS total FROM chats").fetchone()["total"] == 0
        assert runtime.run_payloads == []
        assert product_owner.run_payloads[0]["assessment"]["assessment"]["id"] == "assessment-controlled"
        questions = ProductDiscoveryRepository(connection).list_clarification_questions(
            project_id=project["id"]
        )
        assert [question["question"] for question in questions] == [
            "Who is the primary operator for this workflow?"
        ]
        thread_decisions = ThreadsRepository(connection).list_decisions(thread["id"])
        assert len(thread_decisions) == 1
        assert thread_decisions[0]["options"] == ["operations lead", "support lead"]
        action_row = connection.execute(
            """
            SELECT *
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'po_needs_input'
              AND action_type = 'answer_question'
            """,
            (thread["id"],),
        ).fetchone()
        assert action_row is not None
        action_payload = json.loads(action_row["payload_json"])
        assert action_payload["decisionId"] == thread_decisions[0]["id"]
        assert action_payload["prompt"] == "Who is the primary operator for this workflow?"
        assert action_payload["options"] == ["operations lead", "support lead"]
        assert action_payload["clarificationQuestionId"] == questions[0]["id"]
        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
            payload={"answer": "operations lead"},
        )
        assert execution["execution"]["status"] == "completed"
        assert execution["remediation"]["status"] == "resolved"
        queued_jobs = [
            job
            for job in JobsRepository(connection).list_jobs(project["id"])
            if job["kind"] == "thread.product_loop.run"
        ]
        assert len(queued_jobs) == 1
        assert queued_jobs[0]["payload"]["runMetadata"]["teamMode"] == "critical"
        assert queued_jobs[0]["payload"]["runMetadata"]["risk"] == "high"
        assert "allowUnknownCost" not in queued_jobs[0]["payload"]["runMetadata"]
        assert "requireApprovalForUnknownCost" not in queued_jobs[0]["payload"]["runMetadata"]
        assert queued_jobs[0]["payload"]["runMetadata"]["privacyLevel"] == "local_private"
        assert queued_jobs[0]["payload"]["runMetadata"]["autonomy"] == "guided"
        assert queued_jobs[0]["payload"]["runMetadata"]["researchPolicy"] == {"requireOfficialSources": True}
        assert queued_jobs[0]["payload"]["runMetadata"]["approvedResourceSelections"] == approved_resources
        assert queued_jobs[0]["payload"]["runMetadata"]["userMode"] == "operations_lead"
        superseded_loop = coordinator.get(result["loop"]["id"])
        assert superseded_loop["state"] == "cancelled"
        assert superseded_loop["context"]["durableRun"]["status"] == "decision_answer_queued"
        assert superseded_loop["context"]["durableRun"]["decisionAnswer"]["jobId"] == queued_jobs[0]["id"]
        resolved_decision = ThreadsRepository(connection).get_decision(thread_decisions[0]["id"])
        assert resolved_decision["status"] == "resolved"
        assert resolved_decision["resolution"] == "operations lead"
        answers = ProductDiscoveryRepository(connection).list_clarification_answers(questions[0]["id"])
        assert answers[0]["answer"] == "operations lead"
        assert (
            ProductDiscoveryRepository(connection).get_clarification_question(questions[0]["id"])["status"]
            == "answered"
        )
        transitions = [item["toState"] for item in result["transitions"]]
        assert transitions[-2:] == ["discovery", "awaiting_user"]
        assert "backlog_ready" not in transitions


@pytest.mark.parametrize("failure_point", ["clarification", "loop_supersede"])
def test_answer_question_remediation_rolls_back_decision_and_job_when_atomic_step_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "answer-atomic-rollback")
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Answer atomic rollback",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )
        loop_id = result["loop"]["id"]
        decision = threads.list_decisions(thread["id"])[0]
        question = ProductDiscoveryRepository(connection).list_clarification_questions(
            project_id=project["id"]
        )[0]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND loop_id = ?
              AND blocker_type = 'po_needs_input'
              AND action_type = 'answer_question'
              AND status = 'pending'
            """,
            (thread["id"], loop_id),
        ).fetchone()
        assert action_row is not None

        def fail_atomic_step(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("forced answer transaction failure")

        if failure_point == "clarification":
            monkeypatch.setattr(
                BlockerRemediationService,
                "_record_clarification_answer",
                fail_atomic_step,
            )
        else:
            monkeypatch.setattr(ProductLoopCoordinator, "transition", fail_atomic_step)
            monkeypatch.setattr(ProductLoopCoordinator, "transition_in_transaction", fail_atomic_step)

        with pytest.raises(RuntimeError, match="forced answer transaction failure"):
            BlockerRemediationService(connection, root=tmp_path).execute(
                action_row["id"],
                platform=object(),
                payload={"answer": "operations lead"},
            )

        queued_jobs = [
            job
            for job in JobsRepository(connection).list_jobs(project["id"])
            if job["kind"] == "thread.product_loop.run"
        ]
        discovery = ProductDiscoveryRepository(connection)
        assert queued_jobs == []
        assert threads.get_decision(decision["id"])["status"] == "pending"
        assert threads.get_thread(thread["id"])["status"] == "waiting_decision"
        assert coordinator.get(loop_id)["state"] == "awaiting_user"
        assert discovery.list_clarification_answers(question["id"]) == []
        assert discovery.get_clarification_question(question["id"])["status"] == "open"
        action = BlockerRemediationService(connection, root=tmp_path).repository.get(action_row["id"])
        assert action["status"] == "pending"


def test_answer_question_remediation_rejects_client_decision_override(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input-decision-override")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Needs input guarded",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        original_decision = ThreadsRepository(connection).list_decisions(thread_id)[0]
        unrelated_request = ThreadsRepository(connection).append_message(
            thread_id=thread_id,
            kind="decision_request",
            author="aido_lead",
            content="Unrelated decision",
            metadata={"source": "test"},
        )
        unrelated_decision = ThreadsRepository(connection).create_decision(
            thread_id=thread_id,
            message_id=unrelated_request["id"],
            title="Unrelated decision",
            prompt="Unrelated decision",
            options=["wrong path", "other path"],
            metadata={"source": "test"},
        )
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'po_needs_input'
              AND action_type = 'answer_question'
            """,
            (thread_id,),
        ).fetchone()

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
            payload={"decisionId": unrelated_decision["id"], "answer": "wrong path"},
        )

        assert execution["execution"]["status"] == "blocked"
        assert "decisionId" in execution["execution"]["reason"]
        assert execution["remediation"]["status"] == "pending"
        assert ThreadsRepository(connection).get_decision(original_decision["id"])["status"] == "pending"
        assert ThreadsRepository(connection).get_decision(unrelated_decision["id"])["status"] == "pending"
        assert coordinator.get(result["loop"]["id"])["state"] == "awaiting_user"


def test_answer_question_remediation_rejects_stale_non_question_block(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input-stale-block")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Needs input stale block",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )
        durable = dict(result["loop"]["context"]["durableRun"])
        thread_id = durable["thread"]["projectThreadId"]
        decision = ThreadsRepository(connection).list_decisions(thread_id)[0]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'po_needs_input'
              AND action_type = 'answer_question'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        coordinator.transition(
            result["loop"]["id"],
            to_state="blocked",
            reason="Runtime became unavailable before the question was answered.",
            actor="test",
            trigger="runtime_blocked_after_question",
            context_patch={
                "durableRun": {
                    **durable,
                    "status": "blocked",
                    "blockedStage": "resource_manager",
                    "blockedReason": "Runtime became unavailable before the question was answered.",
                }
            },
            metadata={"blockedStage": "resource_manager"},
        )

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
            payload={"answer": "operations lead"},
        )

        stale_loop = coordinator.get(result["loop"]["id"])
        assert execution["execution"]["status"] == "blocked"
        assert "awaiting" in execution["execution"]["reason"]
        assert execution["remediation"]["status"] == "pending"
        assert ThreadsRepository(connection).get_decision(decision["id"])["status"] == "pending"
        assert stale_loop["state"] == "blocked"
        assert stale_loop["context"]["durableRun"]["blockedStage"] == "resource_manager"


@pytest.mark.parametrize("active_status", ["queued", "running"])
def test_answer_question_remediation_blocks_when_thread_is_already_active(
    tmp_path: Path,
    active_status: str,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, f"needs-input-{active_status}-thread")
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title=f"Needs input {active_status} thread",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )
        durable = dict(result["loop"]["context"]["durableRun"])
        thread_id = durable["thread"]["projectThreadId"]
        decision = threads.list_decisions(thread_id)[0]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'po_needs_input'
              AND action_type = 'answer_question'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        threads.set_status(thread_id, active_status)

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
            payload={"answer": "operations lead"},
        )

        assert execution["execution"]["status"] == "blocked"
        assert f"already {active_status}" in execution["execution"]["reason"]
        assert execution["remediation"]["status"] == "pending"
        assert threads.get_decision(decision["id"])["status"] == "pending"
        assert coordinator.get(result["loop"]["id"])["state"] == "awaiting_user"


def test_answer_question_remediation_ignores_client_clarification_question_override(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input-question-override")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Needs input guarded question",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )
        discovery = ProductDiscoveryRepository(connection)
        original_question = discovery.list_clarification_questions(project_id=project["id"])[0]
        unrelated_question = discovery.create_clarification_question(
            {
                "projectId": project["id"],
                "initiativeId": original_question["initiativeId"],
                "question": "Unrelated product question",
                "status": "open",
                "priority": "low",
                "askedBy": "test",
                "metadata": {"source": "test"},
            }
        )
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'po_needs_input'
              AND action_type = 'answer_question'
            """,
            (thread_id,),
        ).fetchone()

        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
            payload={
                "answer": "operations lead",
                "clarificationQuestionId": unrelated_question["id"],
            },
        )

        assert execution["execution"]["status"] == "completed"
        assert discovery.get_clarification_question(original_question["id"])["status"] == "answered"
        assert discovery.get_clarification_question(unrelated_question["id"])["status"] == "open"
        assert discovery.list_clarification_answers(original_question["id"])[0]["answer"] == "operations lead"
        assert discovery.list_clarification_answers(unrelated_question["id"]) == []


def test_run_user_message_awaits_user_when_thread_status_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )
    status_calls = {"waiting_decision": 0}
    original_set_status = ThreadsRepository.set_status

    def crash_waiting_decision_status(
        self: ThreadsRepository,
        thread_id: str,
        status: str,
    ) -> dict[str, Any]:
        if status == "waiting_decision":
            status_calls["waiting_decision"] += 1
            raise RuntimeError("controlled thread status persistence crashed")
        return original_set_status(self, thread_id, status)

    monkeypatch.setattr(ThreadsRepository, "set_status", crash_waiting_decision_status)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input-status-crash")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Needs input status crash",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )

        assert status_calls["waiting_decision"] == 1
        assert result["status"] == "awaiting_user"
        assert result["loop"]["state"] == "awaiting_user"
        assert runtime.run_payloads == []
        thread_decisions = ThreadsRepository(connection).list_decisions(thread["id"])
        assert len(thread_decisions) == 1
        actions = _remediation_action_types(connection, thread["id"])
        assert ("po_needs_input", "answer_question") in actions


def test_run_user_message_awaits_user_with_generic_retry_when_remediation_mapping_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            questions=[
                {
                    "id": "target-user",
                    "question": "Who is the primary operator for this workflow?",
                    "category": "users",
                    "whyItMatters": "The backlog depends on the actor.",
                    "blocking": True,
                    "options": ["operations lead", "support lead"],
                    "recommendation": "operations lead",
                    "defaultDecision": "operations lead",
                    "confidence": "medium",
                    "priority": "high",
                }
            ],
        )
    )

    def crash_remediations(
        self: BlockerRemediationService, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled remediation mapping crashed")

    monkeypatch.setattr(
        BlockerRemediationService,
        "create_for_blocked_run",
        crash_remediations,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input-remediation-fallback")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Needs input remediation fallback",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )

        assert result["status"] == "awaiting_user"
        assert result["loop"]["state"] == "awaiting_user"
        assert len(ThreadsRepository(connection).list_decisions(thread["id"])) == 1
        actions = _remediation_action_types(connection, thread["id"])
        assert ("po_needs_input", "retry_loop") in actions


def test_run_user_message_blocks_needs_input_without_actionable_options(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(_product_owner_result("needs_input"))
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "needs-input-without-options")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Build something useful.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
        )

        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "failed_validation"
        assert "needs_input" in result["reason"]
        assert "actionable" in result["reason"]
        assert runtime.run_payloads == []
        assert ThreadsRepository(connection).list_decisions(thread_id) == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_needs_input_with_only_accepted_product_decisions(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "needs_input",
            decisions=[
                {
                    "title": "Choose onboarding path",
                    "question": "Which onboarding path should AIDO use?",
                    "recommendation": "Use guided onboarding",
                    "rationale": "AIDO decide selected the lowest-risk product path.",
                    "consequences": ["Guided setup becomes the default."],
                    "status": "accepted",
                    "blocking": False,
                    "options": ["Use guided onboarding", "Use blank setup"],
                    "confidence": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "aido-decide")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="AIDO decide",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="AIDO decide the onboarding path.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            thread_id=thread["id"],
        )

        durable = result["loop"]["context"]["durableRun"]
        actions = _remediation_action_types(connection, thread["id"])

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "failed_validation"
        assert "pending actionable" in result["reason"]
        assert runtime.run_payloads == []
        assert ThreadsRepository(connection).list_decisions(thread["id"]) == []
        decisions = ProductDiscoveryRepository(connection).list_product_decisions(project_id=project["id"])
        assert len(decisions) == 1
        assert decisions[0]["title"] == "Choose onboarding path"
        assert decisions[0]["decision"] == "Use guided onboarding"
        assert decisions[0]["status"] == "accepted"
        assert decisions[0]["metadata"]["source"] == "product_owner_agent"
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_high_impact_technical_decision_when_research_required(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        _product_owner_result(
            "backlog_ready",
            decisions=[
                {
                    "title": "Database migration strategy",
                    "question": "Which migration strategy should AIDO use?",
                    "recommendation": "Rewrite the migration runner around online DDL.",
                    "rationale": "The technical path changes database operations.",
                    "consequences": ["Database rollout behavior changes."],
                    "status": "accepted",
                    "blocking": False,
                    "options": ["Online DDL", "Offline maintenance window"],
                    "confidence": "high",
                    "category": "technical",
                    "impact": "high",
                }
            ],
        )
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "research-required-decision")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Choose the high-impact database migration strategy and implement it.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            run_metadata={
                "researchPolicy": {
                    "requireForHighImpactTechnicalDecisions": True,
                    "allowWebSearch": True,
                }
            },
        )

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "research"
        assert "ResearchAgent" in result["reason"]
        assert runtime.run_payloads == []
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        jobs = JobsRepository(connection).list_jobs(project["id"])
        research_jobs = [job for job in jobs if job["kind"] == "thread.research.run"]
        assert len(research_jobs) == 1
        assert (
            research_jobs[0]["payload"]["metadata"]["researchPolicy"][
                "requireForHighImpactTechnicalDecisions"
            ]
            is True
        )
        assert ("research_required", "run_worker_once") in actions
        assert ("research_required", "retry_loop") in actions


def test_run_user_message_brief_ready_persists_brief_and_artifacts(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _ProductOwnerRunner(_product_owner_result("brief_ready"))
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "brief-ready")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Prepare a brief for onboarding readiness.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            run_metadata={"autonomy": "guided"},
        )

        assert result["status"] == "brief_ready"
        assert result["loop"]["state"] == "brief_ready"
        assert runtime.run_payloads == []
        briefs = ProductDiscoveryRepository(connection).list_product_briefs(project_id=project["id"])
        assert len(briefs) == 1
        assert briefs[0]["title"] == "Guided onboarding"
        assert result["loop"]["context"]["durableRun"]["briefApproval"]["status"] == "approval_required"
        artifact_names = {
            artifact["metadata"].get("name")
            for artifact in EvidenceRepository(connection).list_all_artifacts()
        }
        assert {"product_owner_output.json", "product_brief.json"} <= artifact_names
        transitions = [item["toState"] for item in result["transitions"]]
        assert transitions[-2:] == ["discovery", "brief_ready"]
        assert "backlog_ready" not in transitions


def test_run_user_message_scope_is_clear_persists_mini_brief_without_developer_execution(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _scope_is_clear_po()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "scope-is-clear")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Fix the existing export timeout without changing user-facing workflow.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            run_metadata={"autonomy": "guided"},
        )

        assert result["status"] == "brief_ready"
        assert result["loop"]["state"] == "brief_ready"
        assert runtime.run_payloads == []
        assert product_owner.run_payloads[0]["assessment"]["assessment"]["id"] == "assessment-controlled"
        briefs = ProductDiscoveryRepository(connection).list_product_briefs(project_id=project["id"])
        assert [brief["title"] for brief in briefs] == ["Mini scope for direct technical order"]
        outputs = ProductDiscoveryRepository(connection).list_product_owner_outputs(project_id=project["id"])
        assert outputs[0]["status"] == "scope_is_clear"
        assert BacklogRepository(connection).list_agent_tasks(project_id=project["id"]) == []
        assert result["loop"]["context"]["durableRun"]["briefApproval"]["status"] == "approval_required"


def test_run_user_message_backlog_ready_persists_backlog_and_generates_agent_tasks(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "backlog-ready")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        assert "backlog_ready" in [item["toState"] for item in result["transitions"]]
        backlog = BacklogRepository(connection)
        epics = backlog.list_epics(project["id"])
        stories = backlog.list_user_stories(project_id=project["id"])
        criteria = backlog.list_acceptance_criteria(stories[0]["id"])
        tasks = backlog.list_agent_tasks(project_id=project["id"])
        assert [epic["title"] for epic in epics] == ["Onboarding readiness"]
        assert [story["title"] for story in stories] == ["Readiness checklist"]
        assert [criterion["criterion"] for criterion in criteria] == [
            "Given an incomplete project, when the checklist loads, then missing setup is visible."
        ]
        assert tasks
        assert tasks[0]["metadata"]["source"] == "technical_lead"
        assert technical_lead.payloads[0]["userStories"][0]["id"] == stories[0]["id"]
        assert runtime.run_payloads[0]["agentTasks"][0]["id"] == tasks[0]["id"]
        artifact_names = {
            artifact["metadata"].get("name")
            for artifact in EvidenceRepository(connection).list_all_artifacts()
        }
        assert {"product_owner_output.json", "product_brief.json", "backlog.json"} <= artifact_names


def test_run_user_message_default_technical_lead_planner_generates_role_tasks(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner_result = _product_owner_result("backlog_ready")
    story = product_owner_result["output"]["userStories"][0]
    story["title"] = "Guest checkout"
    story["iWant"] = "to complete checkout through the web UI and backend API"
    story["acceptanceCriteria"] = [
        "The frontend submits checkout.",
        "The backend API validates and stores the order.",
    ]
    product_owner_result["userStories"] = product_owner_result["output"]["userStories"]
    product_owner = _ProductOwnerRunner(product_owner_result)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "default-tech-lead")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement checkout web UI and backend API.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
        )

        assert result["status"] == "awaiting_approval"
        backlog = BacklogRepository(connection)
        tasks = backlog.list_agent_tasks(project_id=project["id"])
        roles = {task["role"] for task in tasks}
        assert {"frontend_engineer", "backend_engineer", "qa_engineer"} <= roles
        assert "developer" not in roles
        assert all(task["metadata"]["source"] == "technical_lead" for task in tasks)
        assert all(task["metadata"]["technicalLeadTaskId"] for task in tasks)
        qa_task = next(task for task in tasks if task["role"] == "qa_engineer")
        assert len(backlog.list_task_dependencies(task_id=qa_task["id"])) >= 2
        runtime_roles = {task["role"] for task in runtime.run_payloads[0]["agentTasks"]}
        assert {"frontend_engineer", "backend_engineer", "qa_engineer"} <= runtime_roles


def test_run_user_message_generic_feature_scope_still_generates_executable_agent_tasks(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner_result = _product_owner_result("backlog_ready")
    story = product_owner_result["output"]["userStories"][0]
    story["title"] = "Generate lifecycle note file"
    story["iWant"] = "the Product Loop to generate a lifecycle note file under src"
    story["acceptanceCriteria"] = ["The generated lifecycle note file exists under src."]
    product_owner_result["output"]["productBriefPatch"]["scope"] = "One generated file under src."
    product_owner_result["userStories"] = product_owner_result["output"]["userStories"]

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "generic-feature-tech-lead")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Add a generated lifecycle note file under src for the AIDO E2E flow.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_ProductOwnerRunner(product_owner_result),
            assessment_runner=_AssessmentRunner(),
        )

        assert result["status"] == "awaiting_approval"
        tasks = BacklogRepository(connection).list_agent_tasks(project_id=project["id"])
        roles = {task["role"] for task in tasks}
        assert {"backend_engineer", "qa_engineer"} <= roles
        assert "backend" in result["loop"]["context"]["durableRun"]["teamSchedule"]["scope"]
        assert runtime.run_payloads[0]["agentTasks"]


def test_run_user_message_refactor_frontend_backend_creates_targeted_team_assignments(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer", "frontend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "refactor-team")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Refactor backend and frontend navigation flow.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        assert result["status"] == "awaiting_approval"
        assignments = BacklogRepository(connection).list_agent_assignments(project_id=project["id"])
        assignment_roles = {assignment["role"] for assignment in assignments}
        assert {"technical_lead", "backend_engineer", "frontend_engineer", "qa_engineer"} <= assignment_roles
        assert {"mobile_engineer", "data_engineer", "security_engineer", "pentester"}.isdisjoint(
            assignment_roles
        )
        assert all(assignment["handoffId"] for assignment in assignments)
        assert connection.execute(
            "SELECT COUNT(*) AS total FROM agent_handoffs WHERE project_id = ?",
            (project["id"],),
        ).fetchone()["total"] == len(assignments)
        team_schedule = result["loop"]["context"]["durableRun"]["teamSchedule"]
        assert team_schedule["mode"] == "balanced"
        assert "backend_engineer" in team_schedule["summary"]["roles"]
        assert all(
            role["resourceDecision"]["selected"]["providerId"] == "ollama" for role in team_schedule["roles"]
        )
        assert all(
            assignment["metadata"]["resourceDecision"]["selected"]["model"] == "qwen2.5-coder"
            for assignment in assignments
        )
        assert runtime.run_payloads[0]["teamSchedule"]["schedulerVersion"] == 2
        assert (
            runtime.run_payloads[0]["teamSchedule"]["roles"][0]["resourceDecision"]["policyResult"][
                "opaqueMlUsed"
            ]
            is False
        )
        assert runtime.run_payloads[0]["preferredRuntime"] == "ollama"
        assert runtime.run_payloads[0]["model"] == "qwen2.5-coder"
        assert runtime.run_payloads[0]["resourceSelection"]["providerId"] == "ollama"


def test_team_resource_decisions_use_team_mode_as_ai_routing_policy(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        team_schedule, blockers = coordinator._team_schedule_with_resource_decisions(
            project_id="project-team-policy",
            loop_id="loop-team-policy",
            request_meta={},
            team_schedule={
                "mode": "economy",
                "risk": "low",
                "roles": [
                    {
                        "role": "backend_engineer",
                        "kind": "build",
                        "capabilities": ["code_edit"],
                        "budgetUsd": 1.0,
                        "maxTokens": 2048,
                    }
                ],
                "summary": {},
            },
            agent_tasks=[{"id": "task-backend", "role": "backend_engineer"}],
        )

    assert blockers == []
    assert team_schedule["roles"][0]["resourceDecision"]["policyResult"]["mode"] == "economy"


def test_run_user_message_resource_manager_selection_overrides_preferred_runtime(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, provider_id="ollama", model="qwen2.5-coder")
        project = _workspace_project(connection, tmp_path, "resource-over-preferred")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            preferred_runtime="codex_cli",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        assert result["status"] == "awaiting_approval"
        assert runtime.status_checks[-1] == "ollama"
        assert runtime.run_payloads[0]["preferredRuntime"] == "ollama"
        assert runtime.run_payloads[0]["resourceSelection"]["providerId"] == "ollama"


def test_run_user_message_resource_manager_can_drive_nvidia_api_runtime(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
        )
        _enable_remote_provider_for_resource_selection(connection, pricing_mode="free")
        project = _workspace_project(connection, tmp_path, "resource-nvidia-api")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through the selected API runtime.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
            },
        )

        team_schedule = result["loop"]["context"]["durableRun"]["teamSchedule"]
        execution_role = next(role for role in team_schedule["roles"] if role["role"] == "backend_engineer")

        assert result["status"] == "awaiting_approval"
        assert execution_role["resourceDecision"]["selected"]["providerId"] == "nvidia_nim"
        assert execution_role["resourceDecision"]["selected"]["runtime"] == "api"
        assert execution_role["resourceDecision"]["approvalRequired"] is False
        assert runtime.status_checks[-1] == "nvidia_nim"
        assert runtime.run_payloads[0]["preferredRuntime"] == "nvidia_nim"
        assert runtime.run_payloads[0]["model"] == "nvidia/nemotron-coder"
        assert runtime.run_payloads[0]["resourceSelection"]["providerId"] == "nvidia_nim"


def test_run_user_message_ignores_untrusted_unknown_cost_policy_metadata(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE role_model_policies
            SET routing_profile_id = 'balanced_best_value',
                allow_unknown_cost = 0,
                require_approval_for_unknown_cost = 1
            WHERE id = 'product_owner'
            """
        )
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["chat"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "untrusted-unknown-cost-policy")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through a remote ProductOwner runtime.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
                "allowUnknownCost": True,
                "allow_unknown_cost": True,
                "requireApprovalForUnknownCost": False,
                "require_approval_for_unknown_cost": False,
            },
        )

        durable = result["loop"]["context"]["durableRun"]
        request_meta = durable["requestMeta"]
        resource_decision = durable["productOwner"]["resourceDecision"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert "allowUnknownCost" not in request_meta
        assert "allow_unknown_cost" not in request_meta
        assert "requireApprovalForUnknownCost" not in request_meta
        assert "require_approval_for_unknown_cost" not in request_meta
        assert resource_decision["approvalRequired"] is True
        assert resource_decision["policyResult"]["unknownCostPolicy"]["action"] == "require_approval"
        assert product_owner.status_calls == 0
        assert product_owner.run_payloads == []
        assert runtime.run_payloads == []


def test_run_user_message_resource_manager_drives_product_owner_runtime_selection(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
        )
        _enable_remote_provider_for_resource_selection(connection, pricing_mode="free")
        project = _workspace_project(connection, tmp_path, "po-resource-nvidia-api")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness after ProductOwner routing.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
            },
        )

        product_owner_context = result["loop"]["context"]["durableRun"]["productOwner"]

        assert result["status"] == "awaiting_approval"
        assert product_owner.status_checks[-1] == "nvidia_nim"
        assert product_owner.run_payloads[0]["preferredRuntime"] == "nvidia_nim"
        assert product_owner.run_payloads[0]["model"] == "nvidia/nemotron-coder"
        assert product_owner_context["resourceDecision"]["selected"]["providerId"] == "nvidia_nim"
        assert product_owner_context["resourceDecision"]["selected"]["runtime"] == "api"
        assert product_owner_context["resourceDecision"]["approvalRequired"] is False


def test_resource_manager_approval_remediation_unblocks_product_owner_resource_selection(
    tmp_path: Path,
) -> None:
    blocked_runtime = _ControlledRuntime()
    approved_runtime = _ControlledRuntime()
    blocked_product_owner = _backlog_ready_po()
    approved_product_owner = _backlog_ready_po()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["code", "review", "tools", "reasoning", "json"])
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["chat"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "po-resource-approval-required")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        coordinator.routing_profiles.patch_role_policy(
            "product_owner",
            {
                "routingProfileId": "balanced_best_value",
                "allowCli": False,
                "allowLocal": False,
                "allowUnknownCost": False,
                "requireApprovalForUnknownCost": True,
            },
        )

        blocked = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness after approving ProductOwner routing.",
            preferred_runtime="ollama",
            runtime_runner=blocked_runtime,
            git_service=_GitGate(),
            product_owner_runner=blocked_product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert blocked["status"] == "blocked"
        assert blocked["loop"]["context"]["durableRun"]["blockedStage"] == "resource_manager"
        assert blocked_product_owner.status_calls == 0
        assert blocked_product_owner.run_payloads == []
        assert blocked_runtime.run_payloads == []
        assert ("resource_manager_approval_required", "approve_resource_decision") in actions
        assert ("resource_manager_approval_required", "retry_loop") in actions

        approval_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_manager_approval_required'
              AND action_type = 'approve_resource_decision'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        remediation = BlockerRemediationService(connection, root=tmp_path)
        approval_action = remediation.repository.get(approval_row["id"])
        approval_context = approval_action["payload"]["resourceApprovals"][0]
        assert approval_context["approvalRequired"] is True
        assert approval_context["estimatedCostUsd"] is None
        assert approval_context["usageStatus"] == "not_executed"
        assert approval_context["policyResult"]["unknownCostPolicy"]["action"] == "require_approval"

        approval = remediation.execute(
            approval_row["id"],
            platform=object(),
        )
        approved_loop = coordinator.get(blocked["loop"]["id"])
        approvals = approved_loop["context"]["durableRun"]["requestMeta"]["approvedResourceSelections"]

        assert approval["execution"]["status"] == "completed"
        assert any(item["role"] == "product_owner" for item in approvals)
        assert all(item["providerId"] == "nvidia_nim" for item in approvals)
        assert all(item["model"] == "nvidia/nemotron-coder" for item in approvals)
        assert all(item["runtime"] == "api" for item in approvals)
        assert approvals[0]["approvalRequired"] is True
        assert approvals[0]["estimatedCostUsd"] is None
        assert approvals[0]["policyResult"]["unknownCostPolicy"]["action"] == "require_approval"

        retry_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_manager_approval_required'
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        assert retry_row is not None
        retry = BlockerRemediationService(connection, root=tmp_path).execute(
            retry_row["id"],
            platform=object(),
        )
        retry_job = JobsRepository(connection).get_job(retry["execution"]["job"]["id"])
        assert retry_job["payload"]["approvedResourceSelections"] == approvals

        approved = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness after approving ProductOwner routing.",
            preferred_runtime="ollama",
            runtime_runner=approved_runtime,
            git_service=_GitGate(),
            product_owner_runner=approved_product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
                "approvedResourceSelections": approvals,
                "retryOfLoopId": blocked["loop"]["id"],
                "remediationActionId": retry["remediation"]["id"],
            },
        )
        product_owner_context = approved["loop"]["context"]["durableRun"]["productOwner"]
        approved_thread_id = approved["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        followup_actions = _remediation_action_types(connection, approved_thread_id)

        assert approved["status"] == "blocked"
        assert approved["loop"]["context"]["durableRun"]["blockedStage"] == "resource_manager"
        assert "aido_lead" in approved["reason"]
        assert approved_product_owner.status_checks[-1] == "nvidia_nim"
        assert approved_product_owner.run_payloads[0]["preferredRuntime"] == "nvidia_nim"
        assert approved_product_owner.run_payloads[0]["model"] == "nvidia/nemotron-coder"
        assert product_owner_context["resourceDecision"]["approvalRequired"] is True
        assert product_owner_context["resourceDecision"]["approvalSatisfied"] is True
        assert (
            product_owner_context["resourceDecision"]["policyResult"]["approvalOverride"]["approved"] is True
        )
        assert approved_runtime.run_payloads == []
        assert ("resource_manager_approval_required", "approve_resource_decision") in followup_actions
        assert ("resource_manager_approval_required", "retry_loop") in followup_actions


def test_run_user_message_records_product_owner_resource_usage_learning(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner_result = _product_owner_result("backlog_ready")
    product_owner_result["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    product_owner_result["actualCostUsd"] = 0.0061
    product_owner_result["latencyMs"] = 812
    product_owner = _ProductOwnerRunner(product_owner_result)
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
        )
        _enable_remote_provider_for_resource_selection(connection, pricing_mode="free")
        project = _workspace_project(connection, tmp_path, "po-resource-learning")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness and learn ProductOwner usage.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
            },
        )

        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'nvidia_nim'
              AND model = 'nvidia/nemotron-coder'
              AND runtime = 'api'
            ORDER BY created_at
            """
        ).fetchall()

        assert result["status"] == "awaiting_approval"
        assert durable["productOwner"]["resourceLearning"]["status"] == "recorded"
        assert durable["productOwner"]["resourceLearning"]["observations"][0]["role"] == "product_owner"
        assert any(
            row["input_tokens"] == 11
            and row["output_tokens"] == 7
            and row["total_tokens"] == 18
            and row["actual_cost_usd"] == pytest.approx(0.0061)
            and row["token_status"] == "actual"
            and row["usage_source"] == "actual"
            for row in cost_rows
        )


def test_resource_manager_approval_remediation_unblocks_approved_unknown_cost_selection(
    tmp_path: Path,
) -> None:
    blocked_runtime = _ControlledRuntime()
    approved_runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["code", "review"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "resource-approval-required")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        blocked = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through an approved API runtime.",
            preferred_runtime="ollama",
            runtime_runner=blocked_runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert blocked["status"] == "blocked"
        assert blocked["loop"]["context"]["durableRun"]["blockedStage"] == "resource_manager"
        assert blocked_runtime.run_payloads == []
        assert ("resource_manager_approval_required", "approve_resource_decision") in actions
        assert ("resource_manager_approval_required", "retry_loop") in actions

        approval_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_manager_approval_required'
              AND action_type = 'approve_resource_decision'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        approval = BlockerRemediationService(connection, root=tmp_path).execute(
            approval_row["id"],
            platform=object(),
        )
        approved_loop = coordinator.get(blocked["loop"]["id"])
        approvals = approved_loop["context"]["durableRun"]["requestMeta"]["approvedResourceSelections"]

        assert approval["execution"]["status"] == "completed"
        approval_roles = {item["role"] for item in approvals}
        assert "backend_engineer" in approval_roles
        assert all(item["providerId"] == "nvidia_nim" for item in approvals)
        assert all(item["model"] == "nvidia/nemotron-coder" for item in approvals)
        assert all(item["runtime"] == "api" for item in approvals)

        retry_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_manager_approval_required'
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        retry = BlockerRemediationService(connection, root=tmp_path).execute(
            retry_row["id"],
            platform=object(),
        )
        retry_job = JobsRepository(connection).get_job(retry["execution"]["job"]["id"])
        assert retry_job["payload"]["approvedResourceSelections"] == approvals

        approved = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through an approved API runtime.",
            preferred_runtime="ollama",
            runtime_runner=approved_runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
                "approvedResourceSelections": approvals,
                "retryOfLoopId": blocked["loop"]["id"],
                "remediationActionId": retry["remediation"]["id"],
            },
        )
        execution_role = next(
            role
            for role in approved["loop"]["context"]["durableRun"]["teamSchedule"]["roles"]
            if role["role"] == "backend_engineer"
        )

        assert approved["status"] == "awaiting_approval"
        assert execution_role["resourceDecision"]["approvalRequired"] is True
        assert execution_role["resourceDecision"]["approvalSatisfied"] is True
        assert execution_role["resourceDecision"]["policyResult"]["approvalOverride"]["approved"] is True
        assert approved_runtime.run_payloads[0]["preferredRuntime"] == "nvidia_nim"


def test_resource_manager_approval_remediation_rejects_stale_non_resource_manager_block(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["code", "review"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "resource-approval-stale")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        blocked = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through a stale resource approval.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )
        durable = dict(blocked["loop"]["context"]["durableRun"])
        thread_id = durable["thread"]["projectThreadId"]
        approval_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_manager_approval_required'
              AND action_type = 'approve_resource_decision'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        coordinator.repository.update_loop_context(
            blocked["loop"]["id"],
            context={
                **blocked["loop"]["context"],
                "durableRun": {
                    **durable,
                    "blockedStage": "runtime",
                    "blockedReason": "Runtime became unavailable before resource approval.",
                },
            },
        )

        approval = BlockerRemediationService(connection, root=tmp_path).execute(
            approval_row["id"],
            platform=object(),
        )
        stale_loop = coordinator.get(blocked["loop"]["id"])
        request_meta = stale_loop["context"]["durableRun"].get("requestMeta") or {}

        assert approval["execution"]["status"] == "blocked"
        assert approval["execution"]["action"] == "approve_resource_decision"
        assert "resource_manager" in approval["execution"]["reason"]
        assert approval["remediation"]["status"] == "pending"
        assert request_meta.get("approvedResourceSelections") in (None, [])


def test_resource_manager_approval_remediation_ignores_client_resource_override(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["code", "review"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "resource-approval-client-override")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        blocked = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness with a guarded resource approval.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )
        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        approval_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_manager_approval_required'
              AND action_type = 'approve_resource_decision'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()

        approval = BlockerRemediationService(connection, root=tmp_path).execute(
            approval_row["id"],
            platform=object(),
            payload={
                "resourceApprovals": [
                    {
                        "role": "backend_engineer",
                        "providerId": "untrusted_provider",
                        "model": "untrusted/model",
                        "runtime": "api",
                    }
                ]
            },
        )
        approved_loop = coordinator.get(blocked["loop"]["id"])
        approvals = approved_loop["context"]["durableRun"]["requestMeta"]["approvedResourceSelections"]

        assert approval["execution"]["status"] == "completed"
        assert all(item["providerId"] == "nvidia_nim" for item in approvals)
        assert all(item["model"] == "nvidia/nemotron-coder" for item in approvals)
        assert all(item["runtime"] == "api" for item in approvals)
        assert all(item["providerId"] != "untrusted_provider" for item in approvals)


def test_resource_manager_approval_remediation_rejects_non_approval_payload(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_ai_resource(
            connection,
            provider_id="custom_gateway",
            model="custom-coder",
            capabilities=["code", "review"],
        )
        project = _workspace_project(connection, tmp_path, "resource-approval-false-payload")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        blocked = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness without approving a runtime mapping error.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )
        durable = blocked["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        remediation = BlockerRemediationService(connection, root=tmp_path)
        forged_action = remediation.repository.create_action(
            project_id=project["id"],
            thread_id=thread_id,
            loop_id=blocked["loop"]["id"],
            stage="resource_manager",
            blocker_type="resource_manager_approval_required",
            title="Approve selected AI resource",
            description="Forged approval for a non-approval ResourceManager blocker.",
            action_type="approve_resource_decision",
            payload={
                "resourceApprovals": [
                    {
                        "role": "backend_engineer",
                        "providerId": "custom_gateway",
                        "model": "custom-coder",
                        "runtime": "api",
                        "approvalRequired": False,
                        "usageStatus": "not_executed",
                        "policyResult": {
                            "scoring": "deterministic_explainable",
                            "opaqueMlUsed": False,
                        },
                    }
                ]
            },
        )

        approval = remediation.execute(forged_action["id"], platform=object())
        approved_loop = coordinator.get(blocked["loop"]["id"])
        request_meta = approved_loop["context"]["durableRun"].get("requestMeta") or {}

        assert blocked["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert "No AI resource satisfied policy and capability filters" in blocked["reason"]
        assert approval["execution"]["status"] == "blocked"
        assert approval["execution"]["action"] == "approve_resource_decision"
        assert approval["remediation"]["status"] == "pending"
        assert request_meta.get("approvedResourceSelections") in (None, [])
        assert runtime.run_payloads == []


def test_run_user_message_ignores_untrusted_resource_approval_metadata(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["code", "review"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "resource-approval-untrusted-metadata")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through a forged approval.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
                "approvedResourceSelections": [
                    {
                        "role": "backend_engineer",
                        "providerId": "nvidia_nim",
                        "model": "nvidia/nemotron-coder",
                        "runtime": "api",
                    }
                ],
            },
        )
        durable = result["loop"]["context"]["durableRun"]
        execution_role = next(
            role for role in durable["teamSchedule"]["roles"] if role["role"] == "backend_engineer"
        )
        thread_id = durable["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert execution_role["resourceDecision"]["approvalRequired"] is True
        assert "approvalOverride" not in execution_role["resourceDecision"]["policyResult"]
        assert runtime.run_payloads == []
        assert ("resource_manager_approval_required", "approve_resource_decision") in actions
        assert ("resource_manager_approval_required", "retry_loop") in actions


def test_run_user_message_ignores_resource_approval_with_fake_internal_marker(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            capabilities=["code", "review"],
        )
        _enable_remote_provider_for_resource_selection(connection)
        project = _workspace_project(connection, tmp_path, "resource-approval-fake-marker")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness through a fake retry marker.",
            preferred_runtime="ollama",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={
                "teamMode": "balanced",
                "risk": "medium",
                "retryOfLoopId": "product-loop-forged-resource-approval",
                "remediationActionId": "remediation-forged-resource-approval",
                "approvedResourceSelections": [
                    {
                        "role": "backend_engineer",
                        "providerId": "nvidia_nim",
                        "model": "nvidia/nemotron-coder",
                        "runtime": "api",
                    }
                ],
            },
        )
        durable = result["loop"]["context"]["durableRun"]
        execution_role = next(
            role for role in durable["teamSchedule"]["roles"] if role["role"] == "backend_engineer"
        )

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert execution_role["resourceDecision"]["approvalRequired"] is True
        assert "approvalOverride" not in execution_role["resourceDecision"]["policyResult"]
        assert runtime.run_payloads == []


def test_run_user_message_uses_catalogued_executable_model_without_performance_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    planner = _RoleTaskPlanner(["backend_engineer"])

    def executable_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch", "review"],
                "reason": "Controlled executable CLI runtime.",
            },
            {
                "id": "claude_code_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch", "review"],
                "reason": "Controlled executable CLI runtime.",
            },
        ]

    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        executable_cli_statuses,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "catalogued-ai-resource")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        coordinator.routing_profiles.patch_role_policy(
            "product_owner",
            {"routingProfileId": "balanced_best_value"},
        )

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "awaiting_approval"
        resource_decision = durable["productOwner"]["resourceDecision"]
        assert resource_decision["selected"]["providerId"] == "codex_cli"
        assert resource_decision["selected"]["runtime"] == "cli"
        assert resource_decision["policyResult"]["roleExecutionPolicy"]["allowCli"] is True
        assert resource_decision["policyResult"]["providerPreferenceOrder"].index(
            "codex_cli"
        ) < resource_decision["policyResult"]["providerPreferenceOrder"].index("claude_code_cli")
        assert (
            resource_decision["policyResult"]["candidateInventory"]
            == "model_catalog_with_performance_overlay"
        )
        assert product_owner.status_checks[-1] == "codex_cli"
        assert runtime.run_payloads[0]["preferredRuntime"] == "codex_cli"


@pytest.mark.parametrize("policy_state", ["cli_disabled", "missing"])
def test_product_owner_resource_selection_obeys_its_mutable_role_policy_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_state: str,
) -> None:
    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        lambda _service, *, project_id=None: [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch", "review"],
                "reason": "Controlled executable CLI runtime.",
            }
        ],
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE model_catalog SET enabled = 0")
        connection.execute("UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli'")
        project = _workspace_project(connection, tmp_path, f"po-policy-{policy_state}")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        if policy_state == "cli_disabled":
            coordinator.routing_profiles.patch_role_policy(
                "product_owner",
                {"allowCli": False},
            )
        else:
            connection.execute("DELETE FROM role_model_policies WHERE role = 'product_owner'")

        decision, blocker = coordinator._product_owner_resource_selection(
            project_id=project["id"],
            loop_id=f"loop-{policy_state}",
            task_id=f"task-{policy_state}",
            request_meta={"teamMode": "balanced", "risk": "medium"},
        )

    assert decision["selected"] is None
    assert blocker is not None
    assert decision["rejected"]
    assert {item["reason"] for item in decision["rejected"]} == {"role_blocks_cli"}
    expected_policy_id = "product_owner" if policy_state == "cli_disabled" else None
    assert decision["policyResult"]["roleExecutionPolicy"]["rolePolicyId"] == (expected_policy_id)


def test_run_user_message_blocks_when_catalogued_runtime_is_not_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()

    def unavailable_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": False,
                "available": False,
                "executable": False,
                "capabilities": ["chat", "code_edit", "issue_to_patch", "review"],
                "reason": "Controlled unavailable CLI runtime.",
            }
        ]

    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        unavailable_cli_statuses,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "unavailable-catalogued-ai-resource")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_RoleTaskPlanner(["backend_engineer"]),
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert durable["productOwner"]["resourceDecision"]["selected"] is None
        assert runtime.run_payloads == []
        assert ("runtime_not_executable", "open_settings_section") in actions
        assert ("runtime_not_executable", "validate_runtime") in actions
        assert ("runtime_not_executable", "switch_runtime") in actions
        assert ("runtime_not_executable", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_resource_selection_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()

    def crashing_select_resource(
        self: AIResourceManager,
        request: Any,
        *,
        record: bool = False,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled ProductOwner ResourceManager crashed")

    monkeypatch.setattr(AIResourceManager, "select_resource", crashing_select_resource)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "po-resource-manager-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner ResourceManager recovery.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert durable["productOwner"]["status"] == "resource_blocked"
        assert durable["productOwner"]["resourceDecision"]["selected"] is None
        assert "controlled ProductOwner ResourceManager crashed" in result["reason"]
        assert product_owner.status_calls == 0
        assert product_owner.run_payloads == []
        assert runtime.run_payloads == []
        assert ("resource_manager_unconfigured", "open_settings_section") in actions
        assert ("resource_manager_unconfigured", "retry_loop") in actions


def test_resource_manager_block_keeps_planning_context_in_durable_run(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        project = _workspace_project(connection, tmp_path, "resource-block-context")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert durable["backlog"]["artifactId"].startswith("artifact-")
        assert durable["agentTasks"]
        assert durable["teamSchedule"]["schedulerVersion"] == 2
        assert durable["teamSchedule"]["summary"]["resourceDecisionBlockedCount"] > 0
        assert any(role["resourceDecision"]["selected"] is None for role in durable["teamSchedule"]["roles"])
        assert runtime.run_payloads == []


def test_run_user_message_blocks_when_team_assignment_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])

    def crash_create_team_assignments(
        self: ProductLoopCoordinator, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled TeamScheduler assignment persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_create_team_assignments",
        crash_create_team_assignments,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "team-assignment-persistence-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness with TeamScheduler assignment failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "team_scheduler"
        assert durable["teamSchedule"]["schedulerVersion"] == 2
        assert durable["agentTasks"]
        assert durable["agentAssignments"] == []
        assert "controlled TeamScheduler assignment persistence crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("team_scheduler_failed", "open_settings_section") in actions
        assert ("team_scheduler_failed", "retry_loop") in actions


def test_run_user_message_blocks_when_resource_manager_selection_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    original_select_resource = AIResourceManager.select_resource
    call_count = 0

    def crashing_select_resource(
        self: AIResourceManager,
        request: Any,
        *,
        record: bool = False,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return original_select_resource(self, request, record=record)
        raise RuntimeError("controlled AIResourceManager crashed")

    monkeypatch.setattr(AIResourceManager, "select_resource", crashing_select_resource)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "resource-manager-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness with ResourceManager crash recovery.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_manager"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert durable["backlog"]["artifactId"].startswith("artifact-")
        assert durable["agentTasks"]
        assert durable["teamSchedule"]["schedulerVersion"] == 2
        assert "controlled AIResourceManager crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("resource_manager_unconfigured", "open_settings_section") in actions
        assert ("resource_manager_unconfigured", "retry_loop") in actions


def test_run_user_message_blocks_when_resource_manager_candidate_has_no_runtime_truth(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection, capabilities=["chat"])
        _seed_ai_resource(
            connection,
            provider_id="custom_gateway",
            model="custom-coder",
            capabilities=["code", "review"],
        )
        project = _workspace_project(connection, tmp_path, "unmapped-ai-resource")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "resource_manager"
        assert "No AI resource satisfied policy and capability filters" in result["reason"]
        assert runtime.run_payloads == []
        assert ("runtime_not_executable", "open_settings_section") in actions
        assert ("runtime_not_executable", "retry_loop") in actions


def test_run_user_message_plan_only_stops_after_team_schedule_without_developer_runtime(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "plan-only-loop")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Plan onboarding readiness without touching files.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
            run_metadata={"planOnly": True},
        )

        durable = result["loop"]["context"]["durableRun"]
        events = ThreadsRepository(connection).list_events(durable["thread"]["projectThreadId"])
        assert result["status"] == "plan_ready"
        assert result["loop"]["state"] == "backlog_ready"
        assert durable["status"] == "plan_ready"
        assert durable["planOnly"] is True
        assert durable["planOnlyResult"]["productOwnerOutputId"].startswith("product-owner-output-")
        assert durable["agentTasks"]
        assert durable["teamSchedule"]["schedulerVersion"] == 2
        assert all(
            role["resourceDecision"]["selected"]["providerId"] == "ollama"
            for role in durable["teamSchedule"]["roles"]
        )
        assignments = BacklogRepository(connection).list_agent_assignments(project_id=project["id"])
        assert all(
            assignment["metadata"]["resourceDecision"]["selected"]["providerId"] == "ollama"
            for assignment in assignments
        )
        assert product_owner.run_payloads
        assert technical_lead.payloads
        assert runtime.run_payloads == []
        assert result["evidencePackage"]["taskId"] == "product_loop.planning"
        assert result["evidencePackage"]["runtimeHealth"]["status"] == "plan_ready"
        assert any(event["type"] == "plan_ready" for event in events)


def test_run_user_message_security_intent_creates_security_and_pentester_assignments(
    tmp_path: Path,
) -> None:
    runtime = _RuntimeUnavailable()
    planner = _RoleTaskPlanner(["backend_engineer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "security-team")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Review authentication security and try to break the login flow.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
            run_metadata={"teamMode": "balanced", "risk": "medium"},
        )

        assert result["status"] == "blocked"
        assignments = BacklogRepository(connection).list_agent_assignments(project_id=project["id"])
        assignment_roles = {assignment["role"] for assignment in assignments}
        assert {"security_engineer", "pentester"} <= assignment_roles
        assert "mobile_engineer" not in assignment_roles
        assert runtime.run_payloads == []


def test_run_user_message_does_not_execute_developer_without_backlog_tasks(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    technical_lead = _TechnicalLeadPlanner(generate_tasks=False)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "no-agent-tasks")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness without task decomposition.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        assert result["status"] == "blocked"
        assert "agent_tasks" in result["reason"]
        assert runtime.run_payloads == []
        assert technical_lead.payloads
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert ("technical_lead_planning_failed", "open_settings_section") in actions
        assert ("technical_lead_planning_failed", "retry_loop") in actions
        action_rows = connection.execute(
            """
            SELECT action_type, payload_json
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'technical_lead_planning_failed'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchall()
        payloads = {row["action_type"]: json.loads(row["payload_json"] or "{}") for row in action_rows}
        for payload in payloads.values():
            assert payload["backlogArtifactId"]
            assert payload["agentTaskIds"] == []
            assert payload["teamScheduleSummary"]["schedulerVersion"] == 2
            assert payload["teamScheduleSummary"]["roleCount"] >= 1
            assert payload["scheduledRoles"]


def test_run_user_message_blocks_when_technical_lead_planner_crashes(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    technical_lead = _FailingTechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "technical-lead-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with TechnicalLead crash recovery.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "technical_lead"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert durable["backlog"]["artifactId"].startswith("artifact-")
        assert durable["agentTasks"] == []
        assert durable["teamSchedule"]["schedulerVersion"] == 2
        assert "controlled TechnicalLeadPlanner crashed" in result["reason"]
        assert technical_lead.payloads
        assert runtime.run_payloads == []
        assert ("technical_lead_planning_failed", "open_settings_section") in actions
        assert ("technical_lead_planning_failed", "retry_loop") in actions


def test_run_user_message_blocks_when_team_scheduler_crashes_before_technical_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    technical_lead = _TechnicalLeadPlanner()

    def crashing_schedule_team(*, scope: list[str], risk: str, mode: str) -> dict[str, Any]:
        raise RuntimeError("controlled TeamScheduler crashed")

    monkeypatch.setattr(
        "local_control_center.product_loop.coordinator.schedule_team",
        crashing_schedule_team,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "team-scheduler-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness with TeamScheduler crash recovery.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "team_scheduler"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert durable["backlog"]["artifactId"].startswith("artifact-")
        assert durable["agentTasks"] == []
        assert durable["teamSchedule"]["status"] == "failed"
        assert durable["teamSchedule"]["phase"] == "preliminary"
        assert "controlled TeamScheduler crashed" in result["reason"]
        assert technical_lead.payloads == []
        assert runtime.run_payloads == []
        assert ("team_scheduler_failed", "open_settings_section") in actions
        assert ("team_scheduler_failed", "retry_loop") in actions


def test_run_user_message_blocks_when_team_scheduler_crashes_after_technical_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    technical_lead = _TechnicalLeadPlanner()
    original_schedule_team = product_loop_coordinator.schedule_team
    call_count = 0

    def crashing_schedule_team(*, scope: list[str], risk: str, mode: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return original_schedule_team(scope=scope, risk=risk, mode=mode)
        raise RuntimeError("controlled TeamScheduler final schedule crashed")

    monkeypatch.setattr(product_loop_coordinator, "schedule_team", crashing_schedule_team)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "team-scheduler-final-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness with final TeamScheduler crash recovery.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "team_scheduler"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert durable["backlog"]["artifactId"].startswith("artifact-")
        assert durable["agentTasks"]
        assert durable["teamSchedule"]["status"] == "failed"
        assert durable["teamSchedule"]["phase"] == "final"
        assert durable["teamSchedule"]["previousSchedule"]["schedulerVersion"] == 2
        assert "controlled TeamScheduler final schedule crashed" in result["reason"]
        assert technical_lead.payloads
        assert runtime.run_payloads == []
        assert ("team_scheduler_failed", "open_settings_section") in actions
        assert ("team_scheduler_failed", "retry_loop") in actions


def test_run_user_message_blocks_when_team_scheduler_role_has_no_agent_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    original_profile_by_role = ProductLoopCoordinator._profile_by_role

    def missing_backend_profile(self: ProductLoopCoordinator) -> dict[str, dict[str, Any]]:
        profiles = dict(original_profile_by_role(self))
        profiles.pop("backend_engineer", None)
        return profiles

    monkeypatch.setattr(ProductLoopCoordinator, "_profile_by_role", missing_backend_profile)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "team-scheduler-missing-profile")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "team_scheduler"
        assert durable["agentAssignments"] == []
        assert "backend_engineer" in result["reason"]
        assert runtime.run_payloads == []
        assert ("team_scheduler_failed", "open_settings_section") in actions
        assert ("team_scheduler_failed", "retry_loop") in actions


def test_run_user_message_blocks_unscheduled_technical_lead_roles_before_runtime(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner(["backend_engineer", "legacy_developer"])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "unscheduled-role")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "technical_lead"
        assert "legacy_developer" in result["reason"]
        assert runtime.run_payloads == []
    assert ("technical_lead_planning_failed", "open_settings_section") in actions
    assert ("technical_lead_planning_failed", "retry_loop") in actions


def test_run_user_message_blocks_technical_lead_task_without_role_before_persisting_developer_fallback(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    planner = _RoleTaskPlanner([""])
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "missing-technical-lead-role")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=planner,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        persisted_tasks = BacklogRepository(connection).list_agent_tasks(project_id=project["id"])

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "technical_lead"
        assert "missing role" in result["reason"]
        assert persisted_tasks == []
        assert runtime.run_payloads == []
        assert ("technical_lead_planning_failed", "open_settings_section") in actions
        assert ("technical_lead_planning_failed", "retry_loop") in actions


def test_run_user_message_with_controlled_runtime_executes_and_awaits_approval(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _backlog_ready_po()
    assessment = _AssessmentRunner()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "controlled-runtime")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement an auditable onboarding dashboard.",
            preferred_runtime="controlled_test_runtime",
            qa_commands=[["python", "--version"]],
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=assessment,
            technical_lead_runner=technical_lead,
        )

        assert result["status"] == "awaiting_approval"
        assert result["loop"]["state"] == "awaiting_approval"
        assert result["loop"]["context"]["durableRun"]["workspaceId"].startswith("workspace-")
        assert result["loop"]["context"]["durableRun"]["productOwner"]["status"] == "backlog_ready"
        assert result["loop"]["context"]["durableRun"]["agentTasks"]
        assert result["loop"]["context"]["durableRun"]["agentAssignments"]
        assert product_owner.run_payloads[0]["assessment"]["assessment"]["id"] == "assessment-controlled"
        assert technical_lead.payloads
        assert runtime.run_payloads[0]["projectId"] == project["id"]
        assert runtime.run_payloads[0]["workspaceId"].startswith("workspace-")
        assert runtime.run_payloads[0]["instruction"] == "Implement an auditable onboarding dashboard."
        assert runtime.run_payloads[0]["agentTasks"]
        assert git.gitleaks_calls == 1
        assert [item["toState"] for item in result["transitions"]] == [
            "goal_received",
            "workspace_check",
            "git_check",
            "runtime_check",
            "discovery",
            "planning",
            "backlog_ready",
            "branch_ready",
            "executing",
            "qa_running",
            "security_running",
            "review_ready",
            "awaiting_approval",
        ]


def test_run_user_message_keeps_durable_result_when_thread_event_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    event_calls = 0

    def crash_thread_event(self: ThreadsRepository, *args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal event_calls
        event_calls += 1
        raise RuntimeError("controlled thread event persistence crashed")

    monkeypatch.setattr(ThreadsRepository, "record_event", crash_thread_event)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "thread-event-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement an auditable onboarding dashboard with thread event persistence failure.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "awaiting_approval"
        assert result["loop"]["state"] == "awaiting_approval"
        assert durable["approval"]["status"] == "available"
        assert durable["review"]["changedFiles"]
        assert durable["gitleaks"]["status"] == "completed"
        assert event_calls > 0
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1


def test_run_user_message_keeps_durable_result_when_event_bus_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    event_bus_calls = 0

    def crash_event_bus(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal event_bus_calls
        event_bus_calls += 1
        raise RuntimeError("controlled event bus persistence crashed")

    monkeypatch.setattr(product_loop_coordinator.EventBus, "record_event", crash_event_bus)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "event-bus-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement an auditable onboarding dashboard with EventBus persistence failure.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=_SecurityGate(),
        )

        durable = result["loop"]["context"]["durableRun"]
        thread_events = ThreadsRepository(connection).list_events(durable["thread"]["projectThreadId"])

        assert result["status"] == "awaiting_approval"
        assert result["loop"]["state"] == "awaiting_approval"
        assert durable["approval"]["status"] == "available"
        assert durable["review"]["changedFiles"]
        assert durable["gitleaks"]["status"] == "completed"
        assert event_bus_calls > 0
        assert any(event["type"] == "loop_event_failed" for event in thread_events)
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1


def test_run_user_message_blocks_when_delivery_approval_action_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    original_create_action_request = JobsRepository.create_action_request

    def crash_delivery_approval_action(
        self: JobsRepository,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if kwargs.get("action_type") == "product_loop.approve_delivery":
            raise RuntimeError("controlled delivery approval persistence crashed")
        return original_create_action_request(self, *args, **kwargs)

    monkeypatch.setattr(
        JobsRepository,
        "create_action_request",
        crash_delivery_approval_action,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "approval-action-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement approval persistence recovery after successful QA and gitleaks.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert durable["blockedStage"] == "approval"
        assert durable["gitleaks"]["status"] == "completed"
        assert durable["review"]["changedFiles"]
        assert "controlled delivery approval persistence crashed" in result["reason"]
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1
        assert ("approval_unavailable", "view_diff") in actions
        assert ("approval_unavailable", "retry_loop") in actions

        monkeypatch.setattr(JobsRepository, "create_action_request", original_create_action_request)
        retry_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'approval_unavailable'
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        assert retry_row is not None
        initial_loop = coordinator.get(result["loop"]["id"])
        initial_transition_ids = {
            transition["id"] for transition in coordinator.list_transitions(result["loop"]["id"])
        }
        initial_job_ids = {job["id"] for job in JobsRepository(connection).list_jobs(project["id"])}
        initial_action_request_ids = {
            request["id"] for request in JobsRepository(connection).list_action_requests()
        }
        initial_thread = ThreadsRepository(connection).get_thread(thread_id)
        initial_thread_event_ids = {
            event["id"] for event in ThreadsRepository(connection).list_events(thread_id)
        }
        original_record_event = ThreadsRepository.record_event

        def crash_late_approval_event(
            self: ThreadsRepository,
            *,
            thread_id: str,
            type: str,
            payload: dict[str, Any] | None = None,
            agent_role: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if type == "approval_required":
                raise RuntimeError("controlled late approval retry failure")
            return original_record_event(
                self,
                thread_id=thread_id,
                type=type,
                payload=payload,
                agent_role=agent_role,
                metadata=metadata,
            )

        monkeypatch.setattr(ThreadsRepository, "record_event", crash_late_approval_event)
        with pytest.raises(RuntimeError, match="controlled late approval retry failure"):
            BlockerRemediationService(connection, root=tmp_path).execute(
                retry_row["id"],
                platform=object(),
            )

        rolled_back_loop = coordinator.get(result["loop"]["id"])
        assert rolled_back_loop["state"] == initial_loop["state"]
        assert rolled_back_loop["version"] == initial_loop["version"]
        assert {
            transition["id"] for transition in coordinator.list_transitions(result["loop"]["id"])
        } == initial_transition_ids
        assert {job["id"] for job in JobsRepository(connection).list_jobs(project["id"])} == initial_job_ids
        assert {
            request["id"] for request in JobsRepository(connection).list_action_requests()
        } == initial_action_request_ids
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] == initial_thread["status"]
        assert {
            event["id"] for event in ThreadsRepository(connection).list_events(thread_id)
        } == initial_thread_event_ids
        assert (
            BlockerRemediationService(connection, root=tmp_path).repository.get(retry_row["id"])["status"]
            == "pending"
        )

        monkeypatch.setattr(ThreadsRepository, "record_event", original_record_event)
        retry = BlockerRemediationService(connection, root=tmp_path).execute(
            retry_row["id"],
            platform=object(),
        )
        jobs = JobsRepository(connection).list_jobs(project["id"])
        delivery_jobs = [job for job in jobs if job["kind"] == "product_loop_delivery_approval"]
        thread_retry_jobs = [
            job
            for job in jobs
            if job["kind"] == "thread.product_loop.run"
            and (job.get("payload") or {}).get("retryOfLoopId") == result["loop"]["id"]
        ]
        retried_loop = coordinator.get(result["loop"]["id"])

        assert retry["execution"]["status"] == "awaiting_approval"
        assert retry["execution"]["action"] == "retry_loop"
        assert retry["execution"]["approval"]["status"] == "available"
        assert delivery_jobs
        assert thread_retry_jobs == []
        assert retried_loop["state"] == "awaiting_approval"
        assert retried_loop["context"]["durableRun"]["status"] == "awaiting_approval"
        assert retried_loop["context"]["durableRun"]["approval"]["actionRequestId"]
        assert (
            connection.execute(
                """
            SELECT COUNT(*) AS total
            FROM remediation_actions
            WHERE loop_id = ?
              AND blocker_type = 'approval_unavailable'
              AND status = 'pending'
            """,
                (result["loop"]["id"],),
            ).fetchone()["total"]
            == 0
        )


def test_run_user_message_records_resource_learning_when_developer_runtime_crashes(tmp_path: Path) -> None:
    runtime = _FailingRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "developer-runtime-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement runtime crash learning.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchall()
        model_row = connection.execute(
            """
            SELECT *
            FROM ai_model_performance
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchone()

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "runtime"
        assert durable["resourceLearning"]["status"] == "recorded"
        assert durable["resourceLearning"]["observations"][0]["role"] == "backend_engineer"
        assert runtime.run_payloads
        assert ("runtime_output_invalid", "continue_plan_only") in actions
        assert ("runtime_output_invalid", "retry_loop") in actions
        assert any(
            row["token_status"] == "unknown"
            and row["usage_source"] == "unknown"
            and row["input_tokens"] is None
            and row["output_tokens"] is None
            and row["total_tokens"] is None
            and row["actual_cost_usd"] is None
            for row in cost_rows
        )
        assert model_row["observed_success_rate"] < 0.86
        assert model_row["quality_score"] < 0.82


def test_developer_runtime_block_survives_resource_learning_persistence_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _FailingRuntime()

    def crash_resource_learning(
        self: ProductLoopCoordinator,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled developer block resource learning crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_record_resource_learning",
        crash_resource_learning,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "developer-runtime-learning-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement runtime crash learning recovery.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "runtime"
        assert durable["resourceLearning"]["status"] == "persistence_failed"
        assert "controlled runtime execution crashed" in result["reason"]
        assert "controlled developer block resource learning crashed" in durable["resourceLearning"]["reason"]
        assert runtime.run_payloads
        assert ("runtime_output_invalid", "continue_plan_only") in actions
        assert ("runtime_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_non_deliverable_runtime_status_with_runtime_context(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime(status="failed")
    git = _GitGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "runtime-status-context")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement but return a non-deliverable runtime status.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        remediation_records = BlockerRemediationService(connection, root=tmp_path).repository.list_for_thread(
            thread_id
        )
        continue_plan = next(
            action for action in remediation_records if action["actionType"] == "continue_plan_only"
        )
        retry = next(action for action in remediation_records if action["actionType"] == "retry_loop")

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "runtime"
        assert "controlled runtime returned failed" in result["reason"]
        assert runtime.run_payloads
        assert git.gitleaks_calls == 0
        assert ("runtime_output_invalid", "continue_plan_only") in actions
        assert ("runtime_output_invalid", "retry_loop") in actions
        assert continue_plan["payload"]["workspaceId"] == durable["workspaceId"]
        assert continue_plan["payload"]["workspacePath"] == durable["workspacePath"]
        assert continue_plan["payload"]["runtimeStatus"] == "failed"
        assert continue_plan["payload"]["runtimeId"] == "controlled_test_runtime"
        assert continue_plan["payload"]["agentTaskIds"]
        assert "backend_engineer" in continue_plan["payload"]["scheduledRoles"]
        assert continue_plan["payload"]["qaResultCount"] == 1
        assert continue_plan["payload"]["changedFiles"] == ["src/app.py"]
        assert continue_plan["payload"]["teamScheduleSummary"]["roleCount"] >= 1
        assert retry["payload"]["retryTarget"] == "runtime"


def test_run_user_message_blocks_empty_review_diff_with_review_remediation(tmp_path: Path) -> None:
    if not git_available():
        pytest.skip("git CLI is required for git worktree review remediation")
    runtime = _ControlledRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "empty-review-diff")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement review evidence without changing files.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "review"
        assert "without real changed files" in result["reason"]
        assert runtime.run_payloads
        assert ("review_diff_unavailable", "view_diff") in actions
        assert ("review_diff_unavailable", "save_patch") in actions
        assert ("review_diff_unavailable", "retry_loop") in actions


def test_run_user_message_blocks_when_review_diff_capture_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not git_available():
        pytest.skip("git CLI is required for git worktree review remediation")
    runtime = _ControlledRuntime()
    capture_calls = 0

    def crash_capture_git_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal capture_calls
        capture_calls += 1
        raise RuntimeError("controlled diff capture crashed")

    monkeypatch.setattr(product_loop_coordinator, "capture_git_diff", crash_capture_git_diff)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "review-diff-capture-crash")
        git = _GitGate()
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement review evidence when diff capture fails.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "review"
        assert durable["review"]["review"]["state"] == "capture_failed"
        assert "controlled diff capture crashed" in result["reason"]
        assert runtime.run_payloads
        assert capture_calls == 1
        assert git.gitleaks_calls == 0
        assert ("review_diff_unavailable", "view_diff") in actions
        assert ("review_diff_unavailable", "save_patch") in actions
        assert ("review_diff_unavailable", "retry_loop") in actions


def test_run_user_message_blocks_directory_runtime_without_changed_files(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(
        changed_files=[],
        provider_usage={"prompt_tokens": 31, "completion_tokens": 4, "total_tokens": 35},
        actual_cost_usd=0.0029,
        latency_ms=777,
    )
    git = _GitGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "directory-empty-review")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement review evidence in a non-git workspace.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchall()
        model_row = connection.execute(
            """
            SELECT *
            FROM ai_model_performance
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchone()

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "review"
        assert durable["resourceLearning"]["status"] == "recorded"
        assert durable["resourceLearning"]["observations"][0]["role"] == "backend_engineer"
        assert "without changed files evidence" in result["reason"]
        assert runtime.run_payloads
        assert git.gitleaks_calls == 0
        assert ("review_diff_unavailable", "view_diff") in actions
        assert ("review_diff_unavailable", "save_patch") in actions
        assert ("review_diff_unavailable", "retry_loop") in actions
        remediation_records = BlockerRemediationService(connection, root=tmp_path).repository.list_for_thread(
            thread_id
        )
        view_diff = next(action for action in remediation_records if action["actionType"] == "view_diff")
        retry = next(action for action in remediation_records if action["actionType"] == "retry_loop")
        assert view_diff["payload"]["workspaceId"]
        assert view_diff["payload"]["runtimeStatus"] == "completed"
        assert view_diff["payload"]["agentTaskIds"]
        assert "backend_engineer" in view_diff["payload"]["scheduledRoles"]
        assert view_diff["payload"]["qaResultCount"] == 1
        assert view_diff["payload"]["changedFiles"] == []
        assert view_diff["payload"]["teamScheduleSummary"]["roleCount"] >= 1
        assert view_diff["payload"]["teamScheduleSummary"]["resourceDecisionBlockedCount"] == 0
        assert "changed files evidence" in view_diff["payload"]["reason"]
        assert retry["payload"]["retryTarget"] == "review_diff"
        assert any(
            row["input_tokens"] == 31
            and row["output_tokens"] == 4
            and row["total_tokens"] == 35
            and row["actual_cost_usd"] == pytest.approx(0.0029)
            and row["token_status"] == "actual"
            for row in cost_rows
        )
        assert model_row["observed_latency_ms"] == 777
        assert model_row["observed_success_rate"] < 0.86
        assert model_row["quality_score"] < 0.82


def test_run_user_message_records_resource_usage_and_quality_learning(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(
        provider_usage={"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
        actual_cost_usd=0.0042,
        latency_ms=1234,
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "resource-learning")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement resource learning from runtime evidence.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute("SELECT * FROM ai_cost_observations").fetchall()
        model_row = connection.execute(
            """
            SELECT *
            FROM ai_model_performance
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchone()

        assert result["status"] == "awaiting_approval"
        assert durable["resourceLearning"]["status"] == "recorded"
        assert durable["resourceLearning"]["observationCount"] == 1
        assert durable["productOwner"]["resourceLearning"]["observationCount"] == 1
        assert len(cost_rows) == 2
        assert cost_rows
        assert any(
            row["input_tokens"] == 17
            and row["output_tokens"] == 5
            and row["total_tokens"] == 22
            and row["actual_cost_usd"] == pytest.approx(0.0042)
            and row["token_status"] == "actual"
            for row in cost_rows
        )
        assert model_row["observed_latency_ms"] == 1234
        assert "outcome" in model_row["evidence_json"]


def test_run_user_message_records_per_role_resource_usage_quality_learning(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(
        resource_usage=[
            {
                "role": "backend_engineer",
                "providerId": "ollama",
                "model": "qwen2.5-coder",
                "runtime": "local",
                "usage": {"prompt_tokens": 101, "completion_tokens": 17, "total_tokens": 118},
                "actualCostUsd": 0.0101,
                "latencyMs": 901,
                "success": True,
                "rework": False,
                "qualityScore": 0.66,
            },
            {
                "role": "qa_engineer",
                "providerId": "ollama",
                "model": "qwen2.5-coder",
                "runtime": "local",
                "usage": {"prompt_tokens": 41, "completion_tokens": 9, "total_tokens": 50},
                "actualCostUsd": 0.0039,
                "latencyMs": 477,
                "success": True,
                "rework": False,
                "qualityScore": 0.94,
            },
        ]
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-role-resource-learning")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement backend onboarding readiness and record per-role resource learning.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = result["loop"]["context"]["durableRun"]
        observations = {
            observation["role"]: observation for observation in durable["resourceLearning"]["observations"]
        }
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchall()
        model_row = connection.execute(
            """
            SELECT *
            FROM ai_model_performance
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchone()

        assert result["status"] == "awaiting_approval"
        assert durable["resourceLearning"]["status"] == "recorded"
        assert durable["resourceLearning"]["observationCount"] == 2
        assert observations["backend_engineer"]["qualityScore"] == pytest.approx(0.66)
        assert observations["qa_engineer"]["qualityScore"] == pytest.approx(0.94)
        assert observations["backend_engineer"]["success"] is True
        assert observations["qa_engineer"]["rework"] is False
        assert any(
            row["input_tokens"] == 101
            and row["output_tokens"] == 17
            and row["total_tokens"] == 118
            and row["actual_cost_usd"] == pytest.approx(0.0101)
            and row["latency_ms"] == 901
            for row in cost_rows
        )
        assert any(
            row["input_tokens"] == 41
            and row["output_tokens"] == 9
            and row["total_tokens"] == 50
            and row["actual_cost_usd"] == pytest.approx(0.0039)
            and row["latency_ms"] == 477
            for row in cost_rows
        )
        learned_scores = {
            item["qualityScore"]
            for item in json.loads(model_row["evidence_json"])
            if item.get("kind") == "outcome"
        }
        assert 0.66 in learned_scores
        assert 0.94 in learned_scores


def test_run_user_message_blocks_when_resource_learning_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime(
        provider_usage={"prompt_tokens": 19, "completion_tokens": 7, "total_tokens": 26},
        actual_cost_usd=0.0051,
        latency_ms=987,
    )
    git = _GitGate()
    original_record_resource_learning = ProductLoopCoordinator._record_resource_learning

    def crash_resource_learning(
        self: ProductLoopCoordinator,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled resource learning persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_record_resource_learning",
        crash_resource_learning,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "resource-learning-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement resource learning persistence recovery after QA and gitleaks.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert durable["blockedStage"] == "resource_learning"
        assert durable["resourceLearning"]["status"] == "persistence_failed"
        assert durable["review"]["changedFiles"]
        assert durable["gitleaks"]["status"] == "completed"
        assert "controlled resource learning persistence crashed" in result["reason"]
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1
        assert ("resource_learning_failed", "open_settings_section") in actions
        assert ("resource_learning_failed", "retry_loop") in actions

        monkeypatch.setattr(
            ProductLoopCoordinator,
            "_record_resource_learning",
            original_record_resource_learning,
        )
        retry_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'resource_learning_failed'
              AND action_type = 'retry_loop'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()
        assert retry_row is not None
        initial_loop = coordinator.get(result["loop"]["id"])
        initial_transition_ids = {
            transition["id"] for transition in coordinator.list_transitions(result["loop"]["id"])
        }
        initial_job_ids = {job["id"] for job in JobsRepository(connection).list_jobs(project["id"])}
        initial_action_request_ids = {
            request["id"] for request in JobsRepository(connection).list_action_requests()
        }
        initial_thread = ThreadsRepository(connection).get_thread(thread_id)
        initial_thread_event_ids = {
            event["id"] for event in ThreadsRepository(connection).list_events(thread_id)
        }
        initial_cost_observation_ids = {
            row["id"] for row in connection.execute("SELECT id FROM ai_cost_observations").fetchall()
        }
        initial_performance_evidence = {
            row["id"]: row["evidence_json"]
            for row in connection.execute("SELECT id, evidence_json FROM ai_model_performance").fetchall()
        }
        original_record_event = ThreadsRepository.record_event

        def crash_late_resource_learning_event(
            self: ThreadsRepository,
            *,
            thread_id: str,
            type: str,
            payload: dict[str, Any] | None = None,
            agent_role: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if type == "approval_required":
                raise RuntimeError("controlled late resource learning retry failure")
            return original_record_event(
                self,
                thread_id=thread_id,
                type=type,
                payload=payload,
                agent_role=agent_role,
                metadata=metadata,
            )

        monkeypatch.setattr(ThreadsRepository, "record_event", crash_late_resource_learning_event)
        with pytest.raises(RuntimeError, match="controlled late resource learning retry failure"):
            BlockerRemediationService(connection, root=tmp_path).execute(
                retry_row["id"],
                platform=object(),
            )

        rolled_back_loop = coordinator.get(result["loop"]["id"])
        assert rolled_back_loop["state"] == initial_loop["state"]
        assert rolled_back_loop["version"] == initial_loop["version"]
        assert {
            transition["id"] for transition in coordinator.list_transitions(result["loop"]["id"])
        } == initial_transition_ids
        assert {job["id"] for job in JobsRepository(connection).list_jobs(project["id"])} == initial_job_ids
        assert {
            request["id"] for request in JobsRepository(connection).list_action_requests()
        } == initial_action_request_ids
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] == initial_thread["status"]
        assert {
            event["id"] for event in ThreadsRepository(connection).list_events(thread_id)
        } == initial_thread_event_ids
        assert {
            row["id"] for row in connection.execute("SELECT id FROM ai_cost_observations").fetchall()
        } == initial_cost_observation_ids
        assert {
            row["id"]: row["evidence_json"]
            for row in connection.execute("SELECT id, evidence_json FROM ai_model_performance").fetchall()
        } == initial_performance_evidence
        assert (
            BlockerRemediationService(connection, root=tmp_path).repository.get(retry_row["id"])["status"]
            == "pending"
        )

        monkeypatch.setattr(ThreadsRepository, "record_event", original_record_event)
        retry = BlockerRemediationService(connection, root=tmp_path).execute(
            retry_row["id"],
            platform=object(),
        )
        jobs = JobsRepository(connection).list_jobs(project["id"])
        delivery_jobs = [job for job in jobs if job["kind"] == "product_loop_delivery_approval"]
        thread_retry_jobs = [
            job
            for job in jobs
            if job["kind"] == "thread.product_loop.run"
            and (job.get("payload") or {}).get("retryOfLoopId") == result["loop"]["id"]
        ]
        retried_loop = coordinator.get(result["loop"]["id"])

        assert retry["execution"]["status"] == "awaiting_approval"
        assert retry["execution"]["action"] == "retry_loop"
        assert retry["execution"]["resourceLearning"]["status"] == "recorded"
        assert retry["execution"]["resourceLearning"]["observationCount"] > 0
        assert retry["execution"]["approval"]["status"] == "available"
        assert delivery_jobs
        assert thread_retry_jobs == []
        assert retried_loop["state"] == "awaiting_approval"
        assert retried_loop["context"]["durableRun"]["status"] == "awaiting_approval"
        assert retried_loop["context"]["durableRun"]["resourceLearning"]["status"] == "recorded"
        assert retried_loop["context"]["durableRun"]["approval"]["actionRequestId"]
        assert (
            connection.execute(
                """
            SELECT COUNT(*) AS total
            FROM remediation_actions
            WHERE loop_id = ?
              AND blocker_type = 'resource_learning_failed'
              AND status = 'pending'
            """,
                (result["loop"]["id"],),
            ).fetchone()["total"]
            == 0
        )


def test_run_user_message_records_unknown_resource_usage_without_fabricating_tokens(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "resource-learning-unknown")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement resource learning without provider usage.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        cost_rows = connection.execute("SELECT * FROM ai_cost_observations").fetchall()

        assert result["status"] == "awaiting_approval"
        assert cost_rows
        assert all(row["token_status"] == "unknown" for row in cost_rows)
        assert all(row["usage_source"] == "unknown" for row in cost_rows)
        assert all(row["input_tokens"] is None for row in cost_rows)
        assert all(row["output_tokens"] is None for row in cost_rows)
        assert all(row["total_tokens"] is None for row in cost_rows)
        assert all(row["actual_cost_usd"] is None for row in cost_rows)


def test_run_user_message_blocks_invalid_product_owner_output_with_remediation(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        {
            "status": "backlog_ready",
            "reason": "ProductOwnerAgent returned no validated output payload.",
            "usage": {"prompt_tokens": 23, "completion_tokens": 9, "total_tokens": 32},
            "actualCostUsd": 0.0075,
            "latencyMs": 1440,
            "evidencePackage": {"id": "evidence-invalid-product-owner"},
        }
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "invalid-po-output")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchall()
        model_row = connection.execute(
            """
            SELECT *
            FROM ai_model_performance
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchone()
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "failed_validation"
        assert durable["productOwner"]["resourceLearning"]["status"] == "recorded"
        assert durable["productOwner"]["resourceLearning"]["observations"][0]["role"] == "product_owner"
        assert "ProductOwnerAgent output" in result["reason"]
        assert runtime.run_payloads == []
        assert (
            ProductDiscoveryRepository(connection).list_product_owner_outputs(project_id=project["id"]) == []
        )
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions
        assert any(
            row["input_tokens"] == 23
            and row["output_tokens"] == 9
            and row["total_tokens"] == 32
            and row["actual_cost_usd"] == pytest.approx(0.0075)
            and row["token_status"] == "actual"
            for row in cost_rows
        )
        assert model_row["observed_success_rate"] < 0.86
        assert model_row["quality_score"] < 0.82


def test_run_user_message_records_product_owner_resource_learning_when_runtime_crashes(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _FailingProductOwnerRunner(_product_owner_result("backlog_ready"))
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-runtime-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchall()
        model_row = connection.execute(
            """
            SELECT *
            FROM ai_model_performance
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchone()

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "runtime_failed"
        assert durable["productOwner"]["resourceDecision"]["selected"]["providerId"] == "ollama"
        assert durable["productOwner"]["resourceLearning"]["status"] == "recorded"
        assert durable["productOwner"]["resourceLearning"]["observations"][0]["role"] == "product_owner"
        assert "controlled ProductOwnerAgent runtime crashed" in result["reason"]
        assert product_owner.run_payloads
        assert runtime.run_payloads == []
        assert (
            ProductDiscoveryRepository(connection).list_product_owner_outputs(project_id=project["id"]) == []
        )
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions
        assert any(
            row["token_status"] == "unknown"
            and row["usage_source"] == "unknown"
            and row["input_tokens"] is None
            and row["output_tokens"] is None
            and row["total_tokens"] is None
            and row["actual_cost_usd"] is None
            for row in cost_rows
        )
        assert model_row["observed_success_rate"] < 0.86
        assert model_row["quality_score"] < 0.82


def test_product_owner_runtime_block_survives_resource_learning_persistence_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _FailingProductOwnerRunner(_product_owner_result("backlog_ready"))

    def crash_product_owner_resource_learning(
        self: ProductLoopCoordinator,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled ProductOwner block resource learning crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_record_product_owner_resource_learning",
        crash_product_owner_resource_learning,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-runtime-learning-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "runtime_failed"
        assert durable["productOwner"]["resourceLearning"]["status"] == "persistence_failed"
        assert "controlled ProductOwnerAgent runtime crashed" in result["reason"]
        assert (
            "controlled ProductOwner block resource learning crashed"
            in durable["productOwner"]["resourceLearning"]["reason"]
        )
        assert product_owner.run_payloads
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_invalid_product_owner_output_block_survives_resource_learning_persistence_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _ProductOwnerRunner(
        {
            "status": "backlog_ready",
            "reason": "ProductOwnerAgent returned no validated output payload.",
            "evidencePackage": {"id": "evidence-invalid-product-owner"},
        }
    )

    def crash_product_owner_resource_learning(
        self: ProductLoopCoordinator,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled ProductOwner validation resource learning crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_record_product_owner_resource_learning",
        crash_product_owner_resource_learning,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "invalid-po-learning-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "failed_validation"
        assert durable["productOwner"]["resourceLearning"]["status"] == "persistence_failed"
        assert "ProductOwnerAgent output" in result["reason"]
        assert (
            "controlled ProductOwner validation resource learning crashed"
            in durable["productOwner"]["resourceLearning"]["reason"]
        )
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_resource_learning_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()

    def crash_product_owner_resource_learning(
        self: ProductLoopCoordinator,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled ProductOwner resource learning persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_record_product_owner_resource_learning",
        crash_product_owner_resource_learning,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-resource-learning-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner resource learning failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert durable["blockedStage"] == "resource_learning"
        assert durable["resourceLearning"]["status"] == "persistence_failed"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert durable["productOwner"]["productOwnerOutputId"]
        assert "controlled ProductOwner resource learning persistence crashed" in result["reason"]
        assert product_owner.run_payloads
        assert technical_lead.payloads == []
        assert runtime.run_payloads == []
        assert ProductDiscoveryRepository(connection).list_product_owner_outputs(project_id=project["id"])
        assert ("resource_learning_failed", "open_settings_section") in actions
        assert ("resource_learning_failed", "retry_loop") in actions


def test_run_user_message_blocks_empty_backlog_with_product_owner_remediation(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    product_owner_result = _product_owner_result("backlog_ready")
    product_owner_result["output"]["epics"] = []
    product_owner_result["output"]["userStories"] = []
    product_owner_result["epics"] = []
    product_owner_result["userStories"] = []
    product_owner = _ProductOwnerRunner(product_owner_result)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "empty-backlog-output")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "backlog"
        assert "backlog_ready" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_backlog_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()

    def crash_persist_backlog(
        self: ProductLoopCoordinator, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled backlog persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_persist_product_owner_backlog",
        crash_persist_backlog,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "backlog-persistence-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with backlog persistence failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "backlog"
        assert durable["backlog"]["status"] == "persistence_failed"
        assert durable["productOwner"]["status"] == "backlog_ready"
        assert "controlled backlog persistence crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_output_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()

    def crash_persist_output(self: ProductLoopCoordinator, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("controlled ProductOwner output persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_persist_product_owner_output_record",
        crash_persist_output,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-output-persistence-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner output persistence failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "persistence_failed"
        assert durable["productOwner"]["workspaceId"]
        assert "controlled ProductOwner output persistence crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_artifact_write_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    original_write_json_artifact = ProductLoopCoordinator._write_json_artifact

    def crash_product_owner_artifact(
        self: ProductLoopCoordinator, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        if kwargs.get("name") == "product_owner_output.json":
            raise RuntimeError("controlled ProductOwner artifact write crashed")
        return original_write_json_artifact(self, *args, **kwargs)

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_write_json_artifact",
        crash_product_owner_artifact,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-artifact-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner artifact failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "persistence_failed"
        assert "controlled ProductOwner artifact write crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_brief_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()

    def crash_persist_brief(self: ProductLoopCoordinator, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("controlled ProductOwner brief persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_persist_product_brief",
        crash_persist_brief,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-brief-persistence-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner brief persistence failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "persistence_failed"
        assert durable["productOwner"]["workspaceId"]
        assert "controlled ProductOwner brief persistence crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_question_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()

    def crash_persist_questions(
        self: ProductLoopCoordinator, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled ProductOwner question persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_persist_clarification_questions",
        crash_persist_questions,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-question-persistence-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner question persistence failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "persistence_failed"
        assert durable["productOwner"]["productOwnerOutputId"]
        assert "controlled ProductOwner question persistence crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_blocks_when_product_owner_decision_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()

    def crash_persist_decisions(
        self: ProductLoopCoordinator, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled ProductOwner decision persistence crashed")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_persist_product_decisions",
        crash_persist_decisions,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "product-owner-decision-persistence-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement onboarding readiness with ProductOwner decision persistence failure.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "product_owner"
        assert durable["productOwner"]["status"] == "persistence_failed"
        assert durable["productOwner"]["productOwnerOutputId"]
        assert "controlled ProductOwner decision persistence crashed" in result["reason"]
        assert runtime.run_payloads == []
        assert ("product_owner_output_invalid", "open_settings_section") in actions
        assert ("product_owner_output_invalid", "retry_loop") in actions


def test_run_user_message_records_thread_events_when_thread_id_is_provided(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "thread-events")
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Thread events",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement an auditable onboarding dashboard.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
            thread_id=thread["id"],
        )

        assert result["status"] == "awaiting_approval"
        events = ThreadsRepository(connection).list_events(thread["id"])
        event_types = [event["type"] for event in events]
        assert "workspace_check" in event_types
        assert "runtime_selected" in event_types
        assert "product_owner_completed" in event_types
        assert "agent_tasks_ready" in event_types
        assert "agent_running" in event_types
        assert "qa_running" in event_types
        assert "security_running" in event_types
        assert "approval_required" in event_types
        assert events[-1]["payload"]["loopId"] == result["loop"]["id"]


def test_run_user_message_blocks_existing_functionality_before_runtime_execution(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "existing-functionality")
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.set_status(existing["id"], "resolved")
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Improve the workspace dashboard filters and add a performance pass.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "functionality_memory"
        assert (
            result["loop"]["context"]["durableRun"]["existingFunctionality"]["sourceThreadId"]
            == existing["id"]
        )
        assert runtime.run_payloads == []

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        thread = repo.get_thread(thread_id)
        assert thread["status"] == "waiting_decision"
        decisions = repo.list_decisions(thread_id)
        assert decisions[0]["options"] == [
            "continue_existing",
            "improve_existing",
            "performance_pass",
            "create_new_anyway",
        ]
        actions = _remediation_action_types(connection, thread_id)
        assert ("functionality_memory_decision_required", "answer_question") in actions
        action_row = connection.execute(
            """
            SELECT id, payload_json
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'functionality_memory_decision_required'
              AND action_type = 'answer_question'
            """,
            (thread_id,),
        ).fetchone()
        action_payload = json.loads(action_row["payload_json"])
        assert action_payload["decisionId"] == decisions[0]["id"]
        assert action_payload["prompt"].startswith("Existing functionality detected:")
        assert action_payload["options"] == [
            "continue_existing",
            "improve_existing",
            "performance_pass",
            "create_new_anyway",
        ]
        assert (
            action_payload["details"]["functionalityId"]
            == result["loop"]["context"]["durableRun"]["existingFunctionality"]["id"]
        )
        execution = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
            payload={"answer": "improve_existing"},
        )
        assert execution["execution"]["status"] == "completed"
        assert execution["remediation"]["status"] == "resolved"
        assert repo.list_decisions(thread_id)[0]["status"] == "resolved"


def test_run_user_message_nested_functionality_decision_skips_existing_functionality_gate(
    tmp_path: Path,
) -> None:
    runtime = _ControlledRuntime()
    product_owner = _backlog_ready_po()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "existing-functionality-resolved")
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.set_status(existing["id"], "resolved")
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Improve the workspace dashboard filters and add a performance pass.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            run_metadata={
                "decision": {
                    "userMode": "improve_existing",
                    "resolution": "improve_existing",
                }
            },
        )

        assert result["loop"]["context"]["durableRun"].get("blockedStage") != "functionality_memory"
        assert product_owner.run_payloads


def test_run_user_message_blocks_existing_functionality_when_thread_event_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "existing-functionality-event-crash")
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.set_status(existing["id"], "resolved")
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])

        event_calls = {"functionality_detected": 0}
        original_record_event = ThreadsRepository.record_event

        def crash_functionality_event(self: ThreadsRepository, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("type") == "functionality_detected":
                event_calls["functionality_detected"] += 1
                raise RuntimeError("controlled functionality event persistence crashed")
            return original_record_event(self, *args, **kwargs)

        monkeypatch.setattr(ThreadsRepository, "record_event", crash_functionality_event)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Improve the workspace dashboard filters and add a performance pass.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        assert event_calls["functionality_detected"] == 1
        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "functionality_memory"
        assert (
            result["loop"]["context"]["durableRun"]["existingFunctionality"]["sourceThreadId"]
            == existing["id"]
        )
        assert runtime.run_payloads == []

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        thread = repo.get_thread(thread_id)
        assert thread["status"] == "waiting_decision"
        decisions = repo.list_decisions(thread_id)
        assert decisions[0]["options"] == [
            "continue_existing",
            "improve_existing",
            "performance_pass",
            "create_new_anyway",
        ]
        actions = _remediation_action_types(connection, thread_id)
        assert ("functionality_memory_decision_required", "answer_question") in actions


def test_run_user_message_blocks_existing_functionality_when_thread_status_persistence_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "existing-functionality-status-crash")
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.set_status(existing["id"], "resolved")
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])

        status_calls = {"waiting_decision": 0}
        original_set_status = ThreadsRepository.set_status

        def crash_waiting_decision_status(
            self: ThreadsRepository,
            thread_id: str,
            status: str,
        ) -> dict[str, Any]:
            if status == "waiting_decision":
                status_calls["waiting_decision"] += 1
                raise RuntimeError("controlled thread status persistence crashed")
            return original_set_status(self, thread_id, status)

        monkeypatch.setattr(ThreadsRepository, "set_status", crash_waiting_decision_status)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Improve the workspace dashboard filters and add a performance pass.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        assert status_calls["waiting_decision"] == 1
        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "functionality_memory"
        assert runtime.run_payloads == []

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        decisions = repo.list_decisions(thread_id)
        assert decisions[0]["options"] == [
            "continue_existing",
            "improve_existing",
            "performance_pass",
            "create_new_anyway",
        ]
        actions = _remediation_action_types(connection, thread_id)
        assert ("functionality_memory_decision_required", "answer_question") in actions


def test_run_user_message_blocks_existing_functionality_with_generic_retry_when_remediation_mapping_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _ControlledRuntime()

    def crash_remediations(
        self: BlockerRemediationService, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        raise RuntimeError("controlled remediation mapping crashed")

    monkeypatch.setattr(
        BlockerRemediationService,
        "create_for_blocked_run",
        crash_remediations,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "existing-functionality-remediation-fallback")
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="Workspace dashboard filters",
            summary="Delivered filters for active workspace dashboard.",
        )
        repo.set_status(existing["id"], "resolved")
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Improve the workspace dashboard filters and add a performance pass.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "functionality_memory"
        assert runtime.run_payloads == []

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        assert len(repo.list_decisions(thread_id)) == 1
        actions = _remediation_action_types(connection, thread_id)
        assert ("functionality_memory_decision_required", "retry_loop") in actions


def test_run_user_message_blocks_dirty_git_before_runtime_execution(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate(dirty=True)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "dirty")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement on a dirty repository.",
            runtime_runner=runtime,
            git_service=git,
        )

        assert result["status"] == "blocked"
        assert "dirty" in result["reason"].lower()
        assert result["loop"]["state"] == "blocked"
        assert runtime.run_payloads == []
        assert [item["toState"] for item in result["transitions"]] == [
            "goal_received",
            "workspace_check",
            "git_check",
            "blocked",
        ]


def test_run_user_message_blocks_when_gitleaks_fails(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate(gitleaks_status="blocked")
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "gitleaks")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement with a secret leak.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        assert result["status"] == "blocked"
        assert "gitleaks" in result["reason"].lower()
        assert result["loop"]["state"] == "blocked"
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1
        assert result["loop"]["context"]["durableRun"]["gitleaks"]["deliveryBlocked"] is True
        assert ("gitleaks_failed", "run_gitleaks") in actions
        assert ("gitleaks_failed", "retry_loop") in actions
        remediation_records = BlockerRemediationService(connection, root=tmp_path).repository.list_for_thread(
            thread_id
        )
        run_gitleaks = next(
            action for action in remediation_records if action["actionType"] == "run_gitleaks"
        )
        retry = next(action for action in remediation_records if action["actionType"] == "retry_loop")
        assert (
            run_gitleaks["payload"]["workspaceId"] == result["loop"]["context"]["durableRun"]["workspaceId"]
        )
        assert (
            run_gitleaks["payload"]["workspacePath"]
            == result["loop"]["context"]["durableRun"]["workspacePath"]
        )
        assert run_gitleaks["payload"]["runtimeStatus"] == "completed"
        assert "backend_engineer" in run_gitleaks["payload"]["scheduledRoles"]
        assert run_gitleaks["payload"]["changedFiles"] == ["src/app.py"]
        assert run_gitleaks["payload"]["gitleaksStatus"] == "blocked"
        assert run_gitleaks["payload"]["gitleaksFindingCount"] == 1
        assert run_gitleaks["payload"]["qaResultCount"] == 1
        assert run_gitleaks["payload"]["teamScheduleSummary"]["roleCount"] >= 1
        assert retry["payload"]["retryTarget"] == "gitleaks"


def test_run_gitleaks_remediation_recovers_blocked_delivery_without_full_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime(
        provider_usage={"prompt_tokens": 23, "completion_tokens": 11, "total_tokens": 34},
        actual_cost_usd=0.0062,
        latency_ms=1100,
    )
    git = _GitGate(gitleaks_status="blocked")
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    captured_scan: dict[str, Any] = {}

    def passing_gitleaks(
        self: GitWorkspaceService,
        project_id: str,
        *,
        workspace_id: str | None = None,
        workspace_path: str | Path | None = None,
    ) -> dict[str, Any]:
        captured_scan.update(
            {
                "projectId": project_id,
                "workspaceId": workspace_id,
                "workspacePath": str(workspace_path or ""),
            }
        )
        return {
            "status": "completed",
            "reason": "gitleaks passed after removing detected secrets",
            "projectId": project_id,
            "workspaceId": workspace_id,
            "deliveryBlocked": False,
            "gitleaks": {
                "status": "completed",
                "findingCount": 0,
            },
            "toolCalls": [{"id": "tool-gitleaks-remediation", "status": "completed"}],
            "policyDecisionIds": ["policy-gitleaks-remediation"],
        }

    monkeypatch.setattr(GitWorkspaceService, "gitleaks_scan", passing_gitleaks)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "gitleaks-remediation")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement with a secret leak, then recover after cleanup.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )
        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'gitleaks_failed'
              AND action_type = 'run_gitleaks'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()

        retry = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )
        jobs = JobsRepository(connection).list_jobs(project["id"])
        delivery_jobs = [job for job in jobs if job["kind"] == "product_loop_delivery_approval"]
        thread_retry_jobs = [
            job
            for job in jobs
            if job["kind"] == "thread.product_loop.run"
            and (job.get("payload") or {}).get("retryOfLoopId") == result["loop"]["id"]
        ]
        recovered_loop = coordinator.get(result["loop"]["id"])
        recovered_durable = recovered_loop["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "gitleaks"
        assert retry["execution"]["status"] == "awaiting_approval"
        assert retry["execution"]["action"] == "run_gitleaks"
        assert retry["execution"]["resourceLearning"]["status"] == "recorded"
        assert retry["execution"]["resourceLearning"]["observationCount"] > 0
        assert retry["execution"]["approval"]["status"] == "available"
        assert retry["remediation"]["status"] == "resolved"
        assert delivery_jobs
        assert thread_retry_jobs == []
        assert captured_scan["workspaceId"] == durable["workspaceId"]
        assert captured_scan["workspacePath"] == durable["workspacePath"]
        assert recovered_loop["state"] == "awaiting_approval"
        assert recovered_durable["status"] == "awaiting_approval"
        assert recovered_durable["gitleaks"]["status"] == "completed"
        assert recovered_durable["resourceLearning"]["status"] == "recorded"
        assert recovered_durable["approval"]["actionRequestId"]
        assert retry["execution"]["evidencePackage"]["id"] in recovered_durable["evidencePackageIds"]
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] == "awaiting_approval"


def test_run_gitleaks_remediation_reports_blocked_when_scanner_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime()
    git = _GitGate(gitleaks_status="blocked")

    def crashing_gitleaks(
        self: GitWorkspaceService,
        project_id: str,
        *,
        workspace_id: str | None = None,
        workspace_path: str | Path | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled remediation gitleaks crashed")

    monkeypatch.setattr(GitWorkspaceService, "gitleaks_scan", crashing_gitleaks)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "gitleaks-remediation-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement with a secret leak, then scanner crashes during repair.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )
        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        action_row = connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'gitleaks_failed'
              AND action_type = 'run_gitleaks'
              AND status = 'pending'
            """,
            (thread_id,),
        ).fetchone()

        retry = BlockerRemediationService(connection, root=tmp_path).execute(
            action_row["id"],
            platform=object(),
        )
        still_blocked = coordinator.get(result["loop"]["id"])

        assert retry["execution"]["status"] == "blocked"
        assert retry["execution"]["action"] == "run_gitleaks"
        assert "controlled remediation gitleaks crashed" in retry["execution"]["reason"]
        assert retry["remediation"]["status"] == "pending"
        assert still_blocked["state"] == "blocked"
        assert still_blocked["context"]["durableRun"]["blockedStage"] == "gitleaks"


def test_run_user_message_blocks_when_gitleaks_execution_crashes(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    git = _FailingGitleaksGate()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "gitleaks-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement with a crashing gitleaks gate.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "gitleaks"
        assert durable["gitleaks"]["status"] == "failed"
        assert "controlled gitleaks execution crashed" in result["reason"]
        assert runtime.run_payloads
        assert git.gitleaks_calls == 1
        assert ("gitleaks_failed", "run_gitleaks") in actions
        assert ("gitleaks_failed", "retry_loop") in actions


def test_run_user_message_opens_rework_when_qa_fails(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(status="qa_failed")
    git = _GitGate()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "qa-fail")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement but QA fails.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )

        assert result["status"] == "reworking"
        assert result["loop"]["state"] == "reworking"
        assert result["loop"]["context"]["durableRun"]["rework"]["source"] == "qa"
        assert result["loop"]["context"]["durableRun"]["rework"]["reason"] == result["reason"]
        assert "reworking" in [item["toState"] for item in result["transitions"]]


def test_qa_failed_blocks_when_resource_learning_persistence_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime(
        status="qa_failed",
        provider_usage={"prompt_tokens": 29, "completion_tokens": 8, "total_tokens": 37},
        actual_cost_usd=0.0037,
    )
    git = _GitGate()

    def crash_resource_learning(
        self: ProductLoopCoordinator,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("controlled QA resource learning crash")

    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_record_resource_learning",
        crash_resource_learning,
    )

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "qa-fail-learning-crash")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement with failing QA and failing resource learning persistence.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)

        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "resource_learning"
        assert durable["resourceLearning"]["status"] == "persistence_failed"
        assert durable["rework"]["source"] == "qa"
        assert durable["rework"]["runtimeStatus"] == "qa_failed"
        assert "controlled QA resource learning crash" in result["reason"]
        assert git.gitleaks_calls == 0
        assert ("resource_learning_failed", "open_settings_section") in actions
        assert ("resource_learning_failed", "retry_loop") in actions


def test_run_user_message_blocks_evidence_ready_without_passing_qa(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(
        status="evidence_ready",
        qa_verdict="blocked",
        qa_results=[],
        provider_usage={"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
        actual_cost_usd=0.0017,
        latency_ms=654,
    )
    git = _GitGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "qa-evidence-blocked")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement evidence-ready output without a real QA pass.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        actions = _remediation_action_types(connection, thread_id)
        durable = result["loop"]["context"]["durableRun"]
        cost_rows = connection.execute(
            """
            SELECT *
            FROM ai_cost_observations
            WHERE provider_id = 'ollama'
              AND model = 'qwen2.5-coder'
              AND runtime = 'local'
            """
        ).fetchall()
        assert result["status"] == "blocked"
        assert durable["blockedStage"] == "qa"
        assert durable["resourceLearning"]["status"] == "recorded"
        assert durable["resourceLearning"]["observations"][0]["role"] == "backend_engineer"
        assert "QA results are required" in result["reason"]
        assert git.gitleaks_calls == 0
        assert ("qa_failed", "continue_plan_only") in actions
        assert ("qa_failed", "retry_loop") in actions
        remediation_records = BlockerRemediationService(connection, root=tmp_path).repository.list_for_thread(
            thread_id
        )
        continue_plan = next(
            action for action in remediation_records if action["actionType"] == "continue_plan_only"
        )
        retry = next(action for action in remediation_records if action["actionType"] == "retry_loop")
        assert continue_plan["payload"]["workspaceId"] == durable["workspaceId"]
        assert continue_plan["payload"]["workspacePath"] == durable["workspacePath"]
        assert continue_plan["payload"]["runtimeStatus"] == "evidence_ready"
        assert continue_plan["payload"]["qaVerdict"] == "blocked"
        assert continue_plan["payload"]["qaResultCount"] == 0
        assert continue_plan["payload"]["nonPassingQaResultCount"] == 0
        assert "backend_engineer" in continue_plan["payload"]["scheduledRoles"]
        assert continue_plan["payload"]["changedFiles"] == ["src/app.py"]
        assert continue_plan["payload"]["teamScheduleSummary"]["roleCount"] >= 1
        assert retry["payload"]["retryTarget"] == "qa"
        assert any(
            row["input_tokens"] == 13
            and row["output_tokens"] == 5
            and row["total_tokens"] == 18
            and row["actual_cost_usd"] == pytest.approx(0.0017)
            and row["token_status"] == "actual"
            for row in cost_rows
        )


def test_run_user_message_recovers_persisted_state_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    runtime = _ControlledRuntime()
    git = _GitGate()
    product_owner = _backlog_ready_po()
    technical_lead = _TechnicalLeadPlanner()
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "restart")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        result = coordinator.run_user_message(
            project_id=project_id,
            message="Implement and persist state.",
            runtime_runner=runtime,
            git_service=git,
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=technical_lead,
        )
        loop_id = result["loop"]["id"]
        expected_context = result["loop"]["context"]["durableRun"]

    with open_sqlite_connection(db_path) as connection:
        resumed = ProductLoopCoordinator(connection, root=tmp_path).resume(loop_id)

    assert resumed["loop"]["state"] == "awaiting_approval"
    assert resumed["loop"]["context"]["durableRun"]["thread"] == expected_context["thread"]
    assert resumed["loop"]["context"]["durableRun"]["workspaceId"] == expected_context["workspaceId"]
    assert resumed["loop"]["context"]["durableRun"]["productOwner"]["status"] == "backlog_ready"
    assert resumed["loop"]["context"]["durableRun"]["agentTasks"]
    assert resumed["loop"]["context"]["durableRun"]["evidencePackageIds"]
    assert resumed["transitions"][-1]["toState"] == "awaiting_approval"


def test_invalid_and_unknown_transitions_are_rejected(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "invalid")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.transition(loop["id"], to_state="delivered")
        with pytest.raises(ProductLoopTransitionError, match="Unknown product loop state"):
            coordinator.transition(loop["id"], to_state="not_a_state")
        # The loop never moved off its initial state after the rejected transitions.
        assert coordinator.get(loop["id"])["state"] == "goal_received"
        assert coordinator.get(loop["id"])["version"] == 1


def test_optimistic_version_guard_blocks_stale_writes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "version")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        with pytest.raises(ProductLoopTransitionError, match="version mismatch"):
            coordinator.transition(loop["id"], to_state="discovering", expected_version=99)
        moved = coordinator.transition(loop["id"], to_state="discovering", expected_version=1)
        assert moved["version"] == 2


def test_block_unblock_and_cancel_paths(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "block")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")
        coordinator.transition(loop["id"], to_state="discovering")

        blocked = coordinator.block(loop["id"], reason="Missing repository access")
        assert blocked["state"] == "blocked"
        assert blocked["status"] == "blocked"
        assert blocked["previousState"] == "discovering"

        # A blocked loop can be cancelled outright (terminal), without unblocking first.
        cancelled = coordinator.cancel(loop["id"], reason="Deprioritised")
        assert cancelled["state"] == "cancelled"
        assert cancelled["status"] == "cancelled"
        assert coordinator.resume(loop["id"])["resumable"] is False
        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.transition(loop["id"], to_state="discovering")


def test_unblock_resumes_the_pre_block_state(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "unblock")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")
        coordinator.transition(loop["id"], to_state="discovering")
        coordinator.block(loop["id"], reason="paused")

        resumed = coordinator.unblock(loop["id"])
        assert resumed["state"] == "discovering"
        assert resumed["status"] == "active"


def test_cancel_is_reachable_from_an_active_state_and_is_terminal(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "cancel")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        cancelled = coordinator.cancel(loop["id"], reason="Out of scope", actor="operator")
        assert cancelled["state"] == "cancelled"
        # A delivered/cancelled loop is terminal.
        loop2 = coordinator.start(project_id=project["id"], title="Y")
        for state in HAPPY_PATH:
            loop2 = coordinator.transition(loop2["id"], to_state=state)
        with pytest.raises(ProductLoopTransitionError, match="Invalid product loop transition"):
            coordinator.cancel(loop2["id"], reason="too late")


def test_correlation_id_is_persisted_on_loop_and_transitions(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "correlation")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", correlation_id="loop-corr")

        coordinator.transition(loop["id"], to_state="discovering", correlation_id="event-1")
        coordinator.transition(loop["id"], to_state="brief_ready")  # falls back to the loop correlation id

        transitions = coordinator.list_transitions(loop["id"])
        assert transitions[0]["metadata"]["correlationId"] == "loop-corr"  # initial transition
        assert transitions[1]["metadata"]["correlationId"] == "event-1"  # explicit per-event id
        assert transitions[2]["metadata"]["correlationId"] == "loop-corr"  # inherited from the loop


def test_budget_consumption_is_metered_off_the_fsm_and_stops_the_loop(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "budget")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", budget={"agentRuns": 2})

        coordinator.record_usage(loop["id"], {"agentRuns": 1})
        metered = coordinator.record_usage(loop["id"], {"agentRuns": 1})

        # Metering does NOT advance the FSM version nor add transitions (the log stays state-only).
        assert metered["version"] == 1
        assert metered["context"]["fsm"]["usage"]["consumed"] == {"agentRuns": 2}
        assert [t["toState"] for t in coordinator.list_transitions(loop["id"])] == ["goal_received"]

        fired = coordinator.evaluate_stop_conditions(loop["id"])
        assert any(item["condition"] == "budget_exhausted" for item in fired)

        result = coordinator.enforce_stop_conditions(loop["id"])
        assert result["loop"]["state"] == "blocked"
        assert any(item["condition"] == "budget_exhausted" for item in result["fired"])
        assert "budget_exhausted" in result["loop"]["context"]["fsm"]["usage"]["stoppedReason"]


def test_usage_optimistic_guard_rejects_stale_metering(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "usageseq")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", budget={"costUsd": 10})

        coordinator.record_usage(loop["id"], {"costUsd": 1}, expected_usage_seq=0)
        with pytest.raises(ProductLoopTransitionError, match="usage version mismatch"):
            coordinator.record_usage(loop["id"], {"costUsd": 1}, expected_usage_seq=0)


def test_state_timeout_stops_the_loop_when_the_deadline_passes(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "timeout")
        coordinator = ProductLoopCoordinator(connection)
        start = "2026-01-01T00:00:00.000Z"
        loop = coordinator.start(
            project_id=project["id"], title="X", timeouts={"goal_received": 60}, now=start
        )
        assert loop["context"]["fsm"]["usage"]["stateDeadline"] == "2026-01-01T00:01:00.000Z"

        # Before the deadline: nothing fires.
        assert coordinator.evaluate_stop_conditions(loop["id"], now="2026-01-01T00:00:30.000Z") == []
        # After the deadline: the state timeout fires and enforcing blocks the loop.
        result = coordinator.enforce_stop_conditions(loop["id"], now="2026-01-01T00:02:00.000Z")
        assert result["loop"]["state"] == "blocked"
        assert any(item["condition"] == "state_timeout" for item in result["fired"])


def test_loop_deadline_stops_the_loop(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "deadline")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(
            project_id=project["id"],
            title="X",
            deadline="2026-01-01T00:00:00.000Z",
            now="2025-12-31T23:00:00.000Z",
        )
        result = coordinator.enforce_stop_conditions(loop["id"], now="2026-01-02T00:00:00.000Z")
        assert result["loop"]["state"] == "blocked"
        assert any(item["condition"] == "deadline_exceeded" for item in result["fired"])


def test_maximum_rework_rounds_are_enforced(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "rework")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X", max_rework_rounds=1)
        loop_id = loop["id"]
        # Walk to the first review.
        for state in [
            "discovering",
            "brief_ready",
            "architecture_review",
            "backlog_ready",
            "iteration_planning",
            "executing",
            "quality_review",
        ]:
            coordinator.transition(loop_id, to_state=state)

        reworked = coordinator.transition(loop_id, to_state="reworking")  # round 1 (allowed)
        assert reworked["context"]["fsm"]["usage"]["reworkRounds"] == 1
        assert any(
            item["condition"] == "max_rework_reached"
            for item in coordinator.evaluate_stop_conditions(loop_id)
        )

        # A second rework round would exceed the maximum and is rejected.
        coordinator.transition(loop_id, to_state="executing")
        coordinator.transition(loop_id, to_state="quality_review")
        with pytest.raises(ProductLoopStopConditionError, match="Maximum rework rounds"):
            coordinator.transition(loop_id, to_state="reworking")


def test_enforce_stop_conditions_is_idempotent_when_nothing_fires(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "noop")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="X")

        result = coordinator.enforce_stop_conditions(loop["id"])
        assert result["fired"] == []
        assert result["loop"]["state"] == "goal_received"
        assert result["loop"]["version"] == 1  # no transition recorded


def test_feedback_actions_are_classified_applied_and_traceable(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "feedback")
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        backlog = coordinator.backlog
        discovery = coordinator.discovery

        epic = backlog.create_epic({"projectId": project_id, "title": "Checkout"})
        story = backlog.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Guest checkout",
                "acceptanceCriteria": ["Guest checkout validates payment fields."],
            }
        )
        task = backlog.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "Fix checkout validation",
                "role": "backend_engineer",
                "status": "completed",
            }
        )
        initiative = discovery.create_initiative(
            {"projectId": project_id, "title": "Checkout initiative", "summary": "Improve checkout."}
        )
        decision = discovery.create_product_decision(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "title": "Use synchronous payment capture",
                "status": "accepted",
                "context": "Initial architecture tradeoff.",
                "decision": "Capture synchronously.",
                "rationale": "Simpler rollout.",
                "decidedBy": "architect_agent",
            }
        )

        approval_loop = coordinator.start(project_id=project_id, title="Approval")
        for state in HAPPY_PATH[:-1]:
            approval_loop = coordinator.transition(approval_loop["id"], to_state=state)
        accepted = coordinator.apply_feedback(
            approval_loop["id"],
            action="accept",
            feedback="QA evidence accepted.",
            actor="operator",
            target_type="brief",
            target_id=approval_loop["id"],
        )
        assert accepted["feedback"]["classification"] == "brief_revision"
        assert accepted["loop"]["state"] == "delivered"
        assert accepted["feedback"]["effects"][0]["type"] == "transition"

        rework_loop = coordinator.start(project_id=project_id, title="Rework")
        for state in HAPPY_PATH[:-1]:
            rework_loop = coordinator.transition(rework_loop["id"], to_state=state)
        reworked = coordinator.apply_feedback(
            rework_loop["id"],
            action="request_changes",
            feedback="Validation needs another pass.",
            actor="operator",
            target_type="task",
            target_id=task["id"],
        )
        assert reworked["feedback"]["classification"] == "rework_task"
        assert reworked["loop"]["state"] == "reworking"
        task_after_rework = backlog.get_agent_task(task["id"])
        assert task_after_rework["status"] == "todo"
        assert task_after_rework["metadata"]["feedbackIds"] == [reworked["feedback"]["id"]]

        new_story = coordinator.apply_feedback(
            rework_loop["id"],
            action="change_scope",
            feedback="Add guest email receipt as follow-up scope.",
            actor="operator",
            target_type="epic",
            target_id=epic["id"],
            payload={
                "story": {
                    "title": "Guest email receipt",
                    "asA": "shopper",
                    "acceptanceCriteria": ["The shopper receives an email receipt after checkout."],
                }
            },
        )
        assert new_story["feedback"]["classification"] == "new_story"
        assert new_story["feedback"]["effects"][0]["type"] == "create_user_story"
        assert (
            backlog.get_user_story(new_story["feedback"]["effects"][0]["id"])["metadata"]["feedbackId"]
            == new_story["feedback"]["id"]
        )

        new_epic = coordinator.apply_feedback(
            rework_loop["id"],
            action="change_scope",
            feedback="Add a post-purchase automation epic.",
            actor="operator",
            payload={"epic": {"title": "Post-purchase automation"}},
        )
        assert new_epic["feedback"]["classification"] == "new_epic"
        assert new_epic["feedback"]["effects"][0]["type"] == "create_epic"

        revised_priority = coordinator.apply_feedback(
            rework_loop["id"],
            action="reprioritize",
            feedback="Escalate the checkout story.",
            actor="operator",
            target_type="story",
            target_id=story["id"],
            payload={"priority": "high"},
        )
        assert revised_priority["feedback"]["classification"] == "brief_revision"
        assert backlog.get_user_story(story["id"])["priority"] == "high"

        rejected_decision = coordinator.apply_feedback(
            rework_loop["id"],
            action="reject_decision",
            feedback="Synchronous capture is too risky.",
            actor="operator",
            target_type="decision",
            target_id=decision["id"],
        )
        assert rejected_decision["feedback"]["classification"] == "architecture_revision"
        assert discovery.get_product_decision(decision["id"])["status"] == "rejected"

        reopened = coordinator.apply_feedback(
            rework_loop["id"],
            action="reopen_story",
            feedback="Story needs another implementation pass.",
            actor="operator",
            target_type="story",
            target_id=story["id"],
        )
        assert reopened["feedback"]["classification"] == "rework_task"
        assert backlog.get_user_story(story["id"])["status"] == "reopened"

        pause_loop = coordinator.start(project_id=project_id, title="Pause")
        paused = coordinator.apply_feedback(
            pause_loop["id"],
            action="pause_loop",
            feedback="Pause while the brief is clarified.",
            actor="operator",
            target_type="brief",
            target_id=pause_loop["id"],
        )
        assert paused["feedback"]["classification"] == "brief_revision"
        assert paused["loop"]["state"] == "blocked"

        cancel_loop = coordinator.start(project_id=project_id, title="Cancel")
        cancelled = coordinator.apply_feedback(
            cancel_loop["id"],
            action="cancel_loop",
            feedback="Cancel after architecture decision rejection.",
            actor="operator",
            target_type="decision",
            target_id=decision["id"],
        )
        assert cancelled["feedback"]["classification"] == "architecture_revision"
        assert cancelled["loop"]["state"] == "cancelled"

        feedback_records = coordinator.list_feedback(project_id=project_id)
        assert {item["action"] for item in feedback_records} >= {
            "accept",
            "request_changes",
            "change_scope",
            "reprioritize",
            "reject_decision",
            "reopen_story",
            "pause_loop",
            "cancel_loop",
        }
        transition = coordinator.list_transitions(rework_loop["id"])[-1]
        assert transition["metadata"]["feedbackId"] == reworked["feedback"]["id"]
        assert reworked["feedback"]["effects"][0]["transitionId"] == transition["id"]


def test_feedback_rejects_unknown_actions_and_untraceable_targets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "feedback-invalid")
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="Invalid")

        with pytest.raises(ProductLoopTransitionError, match="Unknown feedback action"):
            coordinator.apply_feedback(loop["id"], action="defer", feedback="not supported")

        with pytest.raises(ProductLoopTransitionError, match="requires a traceable target"):
            coordinator.apply_feedback(loop["id"], action="request_changes", feedback="missing target")


def test_accept_feedback_resolves_pending_delivery_approval_action(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, coordinator, approval_loop, _job, action = _approval_loop_with_action(
            connection, tmp_path, "delivery-approval"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Delivery approval thread",
        )
        ThreadsRepository(connection).set_status(thread["id"], "awaiting_approval")
        approval_loop = coordinator.repository.update_loop_context(
            approval_loop["id"],
            context={
                **approval_loop["context"],
                "durableRun": {
                    **dict(approval_loop["context"].get("durableRun") or {}),
                    "thread": {"projectThreadId": thread["id"]},
                },
            },
        )

        accepted = coordinator.apply_feedback(
            approval_loop["id"],
            action="accept",
            feedback="QA, diff and gitleaks evidence accepted.",
            actor="operator",
        )

        assert accepted["loop"]["state"] == "delivered"
        assert JobsRepository(connection).get_action_request(action["id"])["status"] == "approved"
        assert JobsRepository(connection).get_action_request(action["id"])["decidedBy"] == "operator"
        assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "resolved"


def test_request_changes_feedback_denies_pending_delivery_approval_action(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, coordinator, approval_loop, _job, action = _approval_loop_with_action(
            connection, tmp_path, "delivery-rework"
        )
        epic = coordinator.backlog.create_epic({"projectId": project["id"], "title": "Checkout"})
        story = coordinator.backlog.create_user_story(
            {
                "projectId": project["id"],
                "epicId": epic["id"],
                "title": "Guest checkout",
                "acceptanceCriteria": ["Guest checkout validates payment fields."],
            }
        )
        task = coordinator.backlog.create_agent_task(
            {
                "projectId": project["id"],
                "storyId": story["id"],
                "title": "Fix checkout validation",
                "role": "backend_engineer",
                "status": "completed",
            }
        )

        reworked = coordinator.apply_feedback(
            approval_loop["id"],
            action="request_changes",
            feedback="Validation needs another pass.",
            actor="operator",
            target_type="task",
            target_id=task["id"],
        )

        assert reworked["loop"]["state"] == "reworking"
        assert JobsRepository(connection).get_action_request(action["id"])["status"] == "denied"
        assert JobsRepository(connection).get_action_request(action["id"])["decidedBy"] == "operator"


def test_request_changes_feedback_reopens_delivery_thread_for_continuation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, coordinator, approval_loop, _job, action = _approval_loop_with_action(
            connection, tmp_path, "delivery-thread-rework"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Delivery rework thread",
        )
        ThreadsRepository(connection).set_status(thread["id"], "awaiting_approval")
        approval_loop = coordinator.repository.update_loop_context(
            approval_loop["id"],
            context={
                **approval_loop["context"],
                "durableRun": {
                    **dict(approval_loop["context"].get("durableRun") or {}),
                    "thread": {"projectThreadId": thread["id"]},
                },
            },
        )

        reworked = coordinator.apply_feedback(
            approval_loop["id"],
            action="request_changes",
            feedback="Evidence needs a targeted rework decision.",
            actor="operator",
            target_type="loop",
            target_id=approval_loop["id"],
        )

        assert reworked["loop"]["state"] == "awaiting_feedback"
        assert JobsRepository(connection).get_action_request(action["id"])["status"] == "denied"
        assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "open"


def test_continue_feedback_does_not_queue_unapproved_resource_metadata(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "delivery-continue-unapproved-resource")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        message = "Continue delivery without trusting loose resource metadata."
        unapproved_resources = [
            {
                "role": "backend_engineer",
                "providerId": "nvidia_nim",
                "model": "nvidia/nemotron-coder",
                "runtime": "api",
            }
        ]
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Delivery continue unapproved resource thread",
        )
        source_message = ThreadsRepository(connection).append_message(
            thread_id=thread["id"],
            kind="user",
            author="operator",
            content=message,
        )
        loop = coordinator.start(
            project_id=project["id"],
            title="Delivery continue unapproved resource",
            context={
                "durableRun": {
                    "thread": {"projectThreadId": thread["id"], "messageId": source_message["id"]},
                    "message": message,
                    "requestMeta": {
                        "messageId": source_message["id"],
                        "planOnly": True,
                        "approvedResourceSelections": unapproved_resources,
                    },
                }
            },
        )

        _updated_loop, effect = coordinator._queue_delivery_feedback_continuation(
            loop=loop,
            feedback_id="product-loop-feedback-unapproved-resource",
            feedback="Continue with the requested rework.",
            actor="operator",
        )

        continuation_job = JobsRepository(connection).get_job(effect["jobId"])
        assert continuation_job["payload"]["approvedResourceSelections"] == []
        assert continuation_job["payload"]["runMetadata"]["approvedResourceSelections"] == []


def test_continue_feedback_queues_real_product_loop_continuation(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, initial_coordinator, approval_loop, _job, action = _approval_loop_with_action(
            connection, tmp_path, "delivery-continue"
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        message = "Implement the requested delivery rework."
        approved_resources = [
            {
                "role": "backend_engineer",
                "providerId": "nvidia_nim",
                "model": "nvidia/nemotron-coder",
                "runtime": "api",
            }
        ]
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Delivery continue thread",
        )
        source_message = ThreadsRepository(connection).append_message(
            thread_id=thread["id"],
            kind="user",
            author="operator",
            content=message,
        )
        ThreadsRepository(connection).set_status(thread["id"], "awaiting_approval")
        approval_loop = initial_coordinator.repository.update_loop_context(
            approval_loop["id"],
            context={
                **approval_loop["context"],
                "durableRun": {
                    **dict(approval_loop["context"].get("durableRun") or {}),
                    "approval": {"jobId": _job["id"], "actionRequestId": action["id"]},
                    "thread": {"projectThreadId": thread["id"], "messageId": source_message["id"]},
                    "message": message,
                    "requestMeta": {
                        "messageId": source_message["id"],
                        "decision": {"planMode": "execute"},
                        "teamPlan": {"roles": [{"role": "backend_engineer"}]},
                        "planOnly": True,
                        "teamMode": "critical",
                        "risk": "high",
                        "approvedResourceSelections": approved_resources,
                    },
                    "resourceApproval": {
                        "status": "approved",
                        "approvedResourceSelections": approved_resources,
                    },
                },
            },
        )
        reworked = coordinator.apply_feedback(
            approval_loop["id"],
            action="request_changes",
            feedback="Evidence needs a targeted rework decision.",
            actor="operator",
            target_type="loop",
            target_id=approval_loop["id"],
        )

        continued = coordinator.apply_feedback(
            reworked["loop"]["id"],
            action="continue",
            feedback="Continue with the requested rework.",
            actor="operator",
            target_type="loop",
            target_id=reworked["loop"]["id"],
        )

        assert continued["feedback"]["action"] == "continue"
        assert continued["feedback"]["classification"] == "rework_task"
        assert continued["loop"]["state"] == "reworking"
        assert continued["feedback"]["effects"][0]["type"] == "transition"
        assert continued["feedback"]["effects"][0]["toState"] == "reworking"
        assert continued["feedback"]["effects"][1]["type"] == "queue_continuation"
        continuation_job = JobsRepository(connection).get_job(continued["feedback"]["effects"][1]["jobId"])
        events = ThreadsRepository(connection).list_events(thread["id"])
        assert JobsRepository(connection).get_action_request(action["id"])["status"] == "denied"
        assert continuation_job["kind"] == "thread.product_loop.run"
        assert continuation_job["status"] == "queued"
        assert continuation_job["payload"]["threadId"] == thread["id"]
        assert continuation_job["payload"]["messageId"] == source_message["id"]
        assert continuation_job["payload"]["message"] == message
        assert continuation_job["payload"]["root"] == str(tmp_path.resolve(strict=False))
        assert continuation_job["payload"]["continueOfLoopId"] == reworked["loop"]["id"]
        assert continuation_job["payload"]["feedbackId"] == continued["feedback"]["id"]
        assert continuation_job["payload"]["runMetadata"]["continueOfLoopId"] == reworked["loop"]["id"]
        assert continuation_job["payload"]["runMetadata"]["feedbackId"] == continued["feedback"]["id"]
        assert (
            continuation_job["payload"]["runMetadata"]["continueReason"]
            == "Continue with the requested rework."
        )
        assert continuation_job["payload"]["runMetadata"]["teamMode"] == "critical"
        assert continuation_job["payload"]["runMetadata"]["risk"] == "high"
        assert continuation_job["payload"]["runMetadata"]["planOnly"] is True
        assert continuation_job["payload"]["runMetadata"]["approvedResourceSelections"] == approved_resources
        assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "queued"
        assert continued["loop"]["context"]["durableRun"]["status"] == "continuation_queued"
        assert continued["loop"]["context"]["durableRun"]["continuation"]["jobId"] == continuation_job["id"]
        assert any(
            event["type"] == "run_queued"
            and event["payload"].get("jobId") == continuation_job["id"]
            and event["payload"].get("continueOfLoopId") == reworked["loop"]["id"]
            for event in events
        )


def _functionality_blocked_run(connection, tmp_path: Path, monkeypatch, name: str) -> dict[str, Any]:
    project = _workspace_project(connection, tmp_path, name)
    monkeypatch.setattr(
        ProductLoopCoordinator,
        "_existing_functionality_match",
        lambda self, *, project_id, message: {
            "id": "functionality-existing-1",
            "name": "Actualizar Spring Boot",
            "sourceThreadId": "thread-source-1",
            "score": 0.7,
            "reason": "Lexical overlap in test",
        },
    )
    coordinator = ProductLoopCoordinator(connection, root=tmp_path)
    return coordinator.run_user_message(
        project_id=project["id"],
        message="Refactoriza y actualiza Spring Boot",
        run_metadata={},
    )


def test_blocked_thread_without_stored_actions_gets_backfilled_remediations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce el hilo bloqueado huérfano: quedó 'blocked' antes de que existiera la creación de
    remediaciones, así que /remediations devolvía [] y el operador no tenía ninguna salida."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        result = _functionality_blocked_run(connection, tmp_path, monkeypatch, "backfill-orphan")
        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        # Estado legacy real: hilo 'blocked' y cero filas de remediación persistidas.
        ThreadsRepository(connection).set_status(thread_id, "blocked")
        connection.execute("DELETE FROM remediation_actions WHERE thread_id = ?", (thread_id,))

        actions = BlockerRemediationService(connection, root=tmp_path).list_for_thread(thread_id=thread_id)

        action_types = {action["actionType"] for action in actions}
        assert "retry_loop" in action_types
        retry = next(action for action in actions if action["actionType"] == "retry_loop")
        assert retry["loopId"] == result["loop"]["id"]
        assert retry["status"] == "pending"


def test_retry_loop_functionality_block_requires_resolved_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        result = _functionality_blocked_run(connection, tmp_path, monkeypatch, "retry-pending-decision")
        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        connection.execute("DELETE FROM remediation_actions WHERE thread_id = ?", (thread_id,))
        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.list_for_thread(thread_id=thread_id)
        retry = next(action for action in actions if action["actionType"] == "retry_loop")

        execution = service.execute(retry["id"], platform=object())

        assert execution["execution"]["status"] == "blocked"
        assert "decision" in execution["execution"]["reason"].lower()


def test_retry_loop_after_resolved_functionality_decision_carries_user_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El retry de un loop bloqueado por funcionalidad existente debe llevar la elección ya resuelta
    del usuario; sin ella, el rerun volvería a bloquearse en el mismo gate para siempre."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        result = _functionality_blocked_run(connection, tmp_path, monkeypatch, "retry-resolved-decision")
        durable = result["loop"]["context"]["durableRun"]
        thread_id = durable["thread"]["projectThreadId"]
        decision_id = durable["decisionId"]
        ThreadsRepository(connection).resolve_decision(
            thread_id=thread_id,
            decision_id=decision_id,
            resolution="continue_existing",
            decided_by="user",
        )
        connection.execute("DELETE FROM remediation_actions WHERE thread_id = ?", (thread_id,))
        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.list_for_thread(thread_id=thread_id)
        retry = next(action for action in actions if action["actionType"] == "retry_loop")

        execution = service.execute(retry["id"], platform=object())

        assert execution["execution"]["status"] == "queued"
        retry_job = JobsRepository(connection).get_job(execution["execution"]["job"]["id"])
        assert retry_job["payload"]["runMetadata"]["functionalityDecision"] == "continue_existing"
        assert ThreadsRepository(connection).get_thread(thread_id)["status"] == "queued"


def test_discovery_payload_seeds_goal_statement_setting(tmp_path: Path) -> None:
    from local_control_center.settings.repository import SettingsRepository

    product_owner = _backlog_ready_po()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "goal-statement")
        SettingsRepository(connection).set_value(
            "project.goal.statement",
            "project",
            project["id"],
            "Construir un panel de onboarding self-serve de principio a fin.",
        )
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard.",
            runtime_runner=_RuntimeUnavailable(),
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        assert product_owner.run_payloads
        assert (
            product_owner.run_payloads[0]["goalStatement"]
            == "Construir un panel de onboarding self-serve de principio a fin."
        )


def test_discovery_payload_omits_goal_statement_when_unset(tmp_path: Path) -> None:
    product_owner = _backlog_ready_po()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "goal-statement-unset")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard.",
            runtime_runner=_RuntimeUnavailable(),
            git_service=_GitGate(),
            product_owner_runner=product_owner,
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

        assert product_owner.run_payloads
        assert "goalStatement" not in product_owner.run_payloads[0]


def test_security_agent_receives_diff_artifact_and_story_specs(tmp_path: Path) -> None:
    security = _SecurityGate()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "security-gate-payload")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard with the security gate.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=security,
        )

        assert result["status"] == "awaiting_approval"
        assert security.run_payloads
        payload = security.run_payloads[0]
        assert payload["workflowStepId"] == "security_review"
        assert payload["taskId"].endswith(".security")
        assert payload["workflowRunId"] == result["loop"]["id"]
        assert "diffArtifactId" in payload
        assert "Readiness checklist" in str(payload.get("storySpecs") or "")


def test_security_agent_verdict_blocked_blocks_delivery(tmp_path: Path) -> None:
    security = _SecurityGate(verdict="blocked", reason="Critical security finding blocks completion.")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "security-gate-blocked")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard with a blocking security finding.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=_ControlledRuntime(),
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=security,
        )

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert "Critical security finding" in result["reason"]
        durable = result["loop"]["context"]["durableRun"]
        assert durable["blockedStage"] == "security_agent"


class _FlakyQARuntime(_ControlledRuntime):
    """Runtime controlado que falla QA un numero fijo de intentos y luego completa."""

    def __init__(self, failures: int = 1) -> None:
        super().__init__(status="qa_failed")
        self._remaining_failures = failures

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.status_value = "qa_failed" if self._remaining_failures > 0 else "completed"
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
        return super().run(payload)


def test_qa_failure_reworks_automatically_and_recovers(tmp_path: Path) -> None:
    runtime = _FlakyQARuntime(failures=1)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "auto-rework-recovers")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard with one flaky QA round.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=_SecurityGate(),
        )

        assert result["status"] == "awaiting_approval"
        assert result["loop"]["state"] == "awaiting_approval"
        assert len(runtime.run_payloads) == 2
        retry_payload = runtime.run_payloads[1]
        assert retry_payload["reworkRound"] == 1
        assert "[QA rework feedback - round 1]" in retry_payload["instruction"]
        assert retry_payload["taskId"].endswith(":r1")
        assert result["loop"]["context"]["fsm"]["usage"]["reworkRounds"] == 1
        states = [item["toState"] for item in result["transitions"]]
        rework_index = states.index("reworking")
        assert "executing" in states[rework_index:]
        triggers = [item.get("trigger") for item in result["transitions"]]
        assert "auto_rework" in triggers


def test_qa_failure_exhausts_auto_rework_and_stops_in_reworking(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(status="qa_failed")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "auto-rework-exhausted")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard while QA always fails.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=_SecurityGate(),
        )

        assert result["status"] == "reworking"
        assert result["loop"]["state"] == "reworking"
        assert len(runtime.run_payloads) == 1 + DEFAULT_AUTO_REWORK_ROUNDS
        assert result["loop"]["context"]["fsm"]["usage"]["reworkRounds"] == 1 + DEFAULT_AUTO_REWORK_ROUNDS


def test_operator_max_rework_policy_overrides_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ControlledRuntime(status="qa_failed")
    original_start = ProductLoopCoordinator.start

    def start_with_policy(self: ProductLoopCoordinator, **kwargs: Any) -> dict[str, Any]:
        return original_start(self, **{**kwargs, "max_rework_rounds": 1})

    monkeypatch.setattr(ProductLoopCoordinator, "start", start_with_policy)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "auto-rework-policy")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard under a strict rework policy.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=_SecurityGate(),
        )

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "qa_rework"
        assert "Maximum rework rounds (1)" in result["reason"]
        assert len(runtime.run_payloads) == 2


class _TransportFlakyRuntime(_ControlledRuntime):
    """Falla el primer run con un error de transporte y responde bien en el reintento."""

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.run_payloads:
            self.run_payloads.append(payload)
            raise ConnectionError("connection refused by provider")
        return super().run(payload)


class _SemanticFailingRuntime(_ControlledRuntime):
    """Falla siempre con una violacion de contrato, que jamas debe reintentarse en otro proveedor."""

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.run_payloads.append(payload)
        raise ValueError("DeveloperAgent model output must be a JSON object.")


def test_a_transport_failure_is_retried_on_another_runtime(tmp_path: Path) -> None:
    runtime = _TransportFlakyRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        # A second, free remote provider. With one provider there is nowhere to fail over to and
        # refusing to retry would be correct, so a real alternative is what makes this a regression.
        _seed_remote_api_resource(
            connection,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
        )
        _enable_remote_provider_for_resource_selection(connection, pricing_mode="free")
        project = _workspace_project(connection, tmp_path, "developer-transport-failover")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        coordinator.run_user_message(
            project_id=project["id"],
            message="Implement transport failover.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

    # The crashed attempt plus at least one retry: the loop no longer dies on a flaky transport.
    assert len(runtime.run_payloads) >= 2


def test_a_contract_violation_is_never_retried_on_another_runtime(tmp_path: Path) -> None:
    """Reintentar un fallo semantico en otro proveedor repite el mismo error y lo paga."""
    runtime = _SemanticFailingRuntime()
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "developer-semantic-no-failover")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        coordinator.run_user_message(
            project_id=project["id"],
            message="Never fail over a contract violation.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

    assert len(runtime.run_payloads) == 1
