from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.product_owner_agent import _execution_result_from_tool_call
from local_control_center.agents.product_owner_agent_contract import (
    PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS,
    product_owner_agent_contract,
    product_owner_agent_readiness,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.app import create_app
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_product_owner_output_contract_describes_nested_fields_enforced_by_validator() -> None:
    output_schema = product_owner_agent_contract()["outputSchema"]

    brief_schema = output_schema["properties"]["productBriefPatch"]
    assert brief_schema["required"] == ["title"]
    assert {
        "title",
        "summary",
        "problemStatement",
        "goals",
        "targetUsers",
        "successMetrics",
        "scope",
        "outOfScope",
    } <= brief_schema["properties"].keys()

    question_schema = output_schema["properties"]["questions"]["items"]
    assert set(question_schema["required"]) == {
        "category",
        "question",
        "whyItMatters",
        "blocking",
        "options",
        "recommendation",
        "defaultDecision",
        "confidence",
    }
    assert question_schema["properties"]["options"]["minItems"] == 2

    decision_schema = output_schema["properties"]["decisions"]["items"]
    assert set(decision_schema["properties"]["reversibility"]["enum"]) == {
        "reversible",
        "recoverable",
        "irreversible",
    }

    story_schema = output_schema["properties"]["userStories"]["items"]
    assert set(story_schema["required"]) == {
        "epicTitle",
        "title",
        "asA",
        "iWant",
        "soThat",
        "acceptanceCriteria",
    }
    assert story_schema["properties"]["acceptanceCriteria"]["minItems"] == 1


def test_failed_runtime_tool_call_reports_execution_cause_and_stderr_artifact() -> None:
    result = _execution_result_from_tool_call(
        {
            "id": "agent-tool-call-timeout",
            "status": "failed",
            "payload": {
                "decisionReason": "ProductOwnerAgent CLI runtime execution is allowed inside the workspace.",
                "executionResult": {
                    "returnCode": None,
                    "timedOut": True,
                    "blocked": False,
                    "stdoutArtifactId": "artifact-stdout",
                    "stderrArtifactId": "artifact-stderr",
                },
            },
        }
    )

    assert result["reason"] == "ProductOwnerAgent runtime execution timed out."
    assert result["stderrArtifactId"] == "artifact-stderr"


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def create_project_and_workspace(
    store: ControlPlaneFixture, tmp_path: Path, *, task_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    project_path = tmp_path / task_id
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text("# Product owner test project\n", encoding="utf-8")
    project = store.create_project(name=f"PO {task_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="product_owner_agent",
        reason="product owner agent test workspace",
        isolation_type="directory",
    )
    return project, workspace


def executable_model_runtime_status(runtime_id: str = "openai_compatible") -> list[dict[str, Any]]:
    return [
        {
            "id": runtime_id,
            "providerFamily": runtime_id,
            "kind": "gateway" if runtime_id == "openrouter" else "api",
            "displayName": f"Controlled {runtime_id} runtime",
            "detected": True,
            "configured": True,
            "available": True,
            "executable": True,
            "requiresApproval": False,
            "reason": "Controlled provider is executable.",
            "capabilities": ["chat"],
            "requiredConfiguration": ["baseUrl", "apiKey", "model"],
            "safety": {
                "workspaceBound": False,
                "shell": False,
                "structuredArgv": True,
                "network": "remote_calls_disabled_by_default",
            },
        }
    ]


def executable_openai_runtime_status() -> list[dict[str, Any]]:
    return executable_model_runtime_status("openai_compatible")


class ControlledProductOwnerProviderHandler(BaseHTTPRequestHandler):
    response_content = "{}"
    response_queue: list[str] = []
    chat_post_count = 0

    def _next_content(self) -> str:
        cls = type(self)
        cls.chat_post_count += 1
        if cls.response_queue:
            return cls.response_queue.pop(0)
        return cls.response_content

    def do_GET(self) -> None:
        if self.path != "/models":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {"data": [{"id": "controlled-product-owner-model", "display_name": "Controlled PO Model"}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/messages":
            self.rfile.read(int(self.headers.get("Content-Length") or "0"))
            payload = {
                "content": [{"type": "text", "text": self._next_content()}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        payload = {
            "choices": [{"message": {"content": self._next_content()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def start_controlled_provider(
    content: str, *, contents: list[str] | None = None
) -> tuple[ThreadingHTTPServer, str]:
    ControlledProductOwnerProviderHandler.response_content = content
    ControlledProductOwnerProviderHandler.response_queue = list(contents or [])
    ControlledProductOwnerProviderHandler.chat_post_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledProductOwnerProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def impact_question(*, category: str, text: str, blocking: bool, confidence: str) -> dict[str, Any]:
    return {
        "category": category,
        "question": text,
        "whyItMatters": f"It changes the {category} plan and estimates.",
        "blocking": blocking,
        "options": ["Option A", "Option B"],
        "recommendation": "Option A",
        "defaultDecision": "Option A",
        "confidence": confidence,
    }


def discovery_questions() -> list[dict[str, Any]]:
    # Six candidates so the engine groups five per turn and defers the lowest-impact one (the ux/high one).
    return [
        impact_question(
            category="compliance",
            text="Which jurisdictions must we support?",
            blocking=True,
            confidence="low",
        ),
        impact_question(
            category="data", text="Which records must be retained?", blocking=True, confidence="medium"
        ),
        impact_question(
            category="integration", text="Which payment APIs are in scope?", blocking=False, confidence="low"
        ),
        impact_question(
            category="scope", text="What is in scope for v1?", blocking=False, confidence="medium"
        ),
        impact_question(
            category="users", text="Who is the primary persona?", blocking=False, confidence="medium"
        ),
        impact_question(
            category="ux", text="Which onboarding tone do we want?", blocking=False, confidence="high"
        ),
    ]


def autonomous_blocking_decision() -> dict[str, Any]:
    return {
        "title": "Choose the onboarding layout",
        "question": "Wizard or checklist?",
        "status": "open",
        "rationale": "Both are reversible UI choices with a clear default.",
        "options": ["Wizard", "Checklist"],
        "recommendation": "Wizard",
        "reversibility": "reversible",
        "confidence": "high",
    }


def product_owner_output(*, blocking: bool) -> dict[str, Any]:
    brief = {
        "title": "Self-serve onboarding",
        "summary": "Reduce time-to-value for new SMB users.",
        "problemStatement": "New users stall at manual setup.",
        "goals": ["Cut setup steps", "Raise day-1 activation"],
        "targetUsers": ["SMB admins"],
        "successMetrics": ["activation_rate"],
        "scope": "Guided setup wizard.",
        "outOfScope": "Enterprise SSO.",
    }
    user_stories = [
        {
            "epicTitle": "Guided onboarding",
            "title": "Guided account setup",
            "asA": "new SMB admin",
            "iWant": "to complete setup through a guided wizard",
            "soThat": "I reach first value without manual configuration",
            "businessValue": "high",
            "acceptanceCriteria": [
                "Setup completes without manual config",
                "Progress is shown at each step",
            ],
        }
    ]
    return {
        "status": "brief_ready",
        "summary": "A guided onboarding brief and backlog are ready for approval.",
        "confidence": "high",
        "completeness": {"score": 90, "missing": [], "rationale": "Brief covers the core."},
        "questions": discovery_questions(),
        "assumptions": [
            {"statement": "Users already have email accounts.", "confidence": "high", "validation": "survey"}
        ],
        "decisions": (
            [
                {
                    "title": "Choose the payment provider",
                    "question": "Stripe or a local processor?",
                    "status": "open",
                    "rationale": "It changes scope and compliance.",
                    "options": ["Stripe", "Local processor"],
                    "recommendation": "Stripe",
                    "reversibility": "irreversible",
                    "confidence": "medium",
                }
            ]
            if blocking
            else []
        ),
        "blockingDecisions": (
            [
                {
                    "title": "Choose the payment provider",
                    "question": "Stripe or a local processor?",
                    "status": "open",
                    "rationale": "It changes scope and compliance.",
                }
            ]
            if blocking
            else []
        ),
        "productBriefPatch": brief,
        "brief": brief,
        "epics": [
            {
                "title": "Guided onboarding",
                "description": "A wizard that walks users through setup.",
                "stories": user_stories,
            }
        ],
        "userStories": user_stories,
        "risks": [
            {
                "severity": "medium",
                "description": "Activation improvements depend on accurate funnel instrumentation.",
                "mitigation": "Confirm event tracking before implementation planning.",
            }
        ],
        "recommendedNextAction": "Approve the product brief before generating the backlog.",
    }


def incomplete_product_owner_output() -> dict[str, Any]:
    brief = {
        "title": "Payments workspace",
        "summary": "A payments idea needs buyer, compliance and scope choices before backlog.",
        "problemStatement": "",
        "goals": [],
        "targetUsers": [],
        "successMetrics": [],
        "scope": "",
        "outOfScope": "",
    }
    questions = [
        impact_question(
            category="users",
            text="Who is the primary buyer or operator persona?",
            blocking=True,
            confidence="low",
        ),
        impact_question(
            category="compliance",
            text="Which payment jurisdictions must launch first?",
            blocking=True,
            confidence="low",
        ),
    ]
    return {
        "status": "questions_required",
        "summary": "The idea is too underspecified to produce a buildable backlog.",
        "confidence": "low",
        "completeness": {"score": 25, "missing": ["targetUsers", "scope"]},
        "questions": questions,
        "assumptions": [
            {
                "statement": "The product handles payment data.",
                "confidence": "medium",
                "validation": "Stated in the idea.",
            }
        ],
        "decisions": [],
        "blockingDecisions": [],
        "productBriefPatch": brief,
        "brief": brief,
        "epics": [],
        "userStories": [],
        "risks": [
            {
                "severity": "high",
                "description": "Payment scope is unclear.",
                "mitigation": "Answer the compliance and persona questions first.",
            }
        ],
        "recommendedNextAction": "Answer the blocking product questions.",
    }


def scope_is_clear_product_owner_output() -> dict[str, Any]:
    output = product_owner_output(blocking=False)
    output["status"] = "scope_is_clear"
    output["summary"] = "The direct technical order has enough existing-project context."
    output["questions"] = []
    output["decisions"] = []
    output["blockingDecisions"] = []
    output["epics"] = []
    output["userStories"] = []
    output["productBriefPatch"] = {
        **output["productBriefPatch"],
        "title": "Direct technical task scope",
        "summary": "Implement the bounded technical change in the existing project.",
        "scope": "Fix the requested technical behavior without adjacent refactors.",
        "outOfScope": "New product capabilities.",
    }
    output["brief"] = output["productBriefPatch"]
    output["recommendedNextAction"] = "Review the mini brief/task scope before implementation."
    output["taskScope"] = {
        "type": "direct_technical_order",
        "goal": "Apply the bounded technical change.",
        "constraints": ["Use existing architecture", "Preserve current flow"],
    }
    return output


def product_owner_request(project: dict[str, Any], workspace: dict[str, Any]) -> dict[str, Any]:
    return {
        "projectId": project["id"],
        "workspaceId": workspace["id"],
        "taskId": "discovery-intake",
        "idea": "Let SMB users onboard themselves without manual setup.",
        "workflowContext": {"title": "Product discovery intake"},
        "preferredRuntime": "openai_compatible",
    }


def attach_persisted_resource_decision(
    connection: Any,
    body: dict[str, Any],
    *,
    runtime_id: str,
    model: str,
    runtime_kind: str,
) -> dict[str, Any]:
    workflow_context = body.get("workflowContext") if isinstance(body.get("workflowContext"), dict) else {}
    workflow_run_id = str(workflow_context.get("workflowRunId") or f"product-loop-{uuid.uuid4()}")
    body["workflowContext"] = {**workflow_context, "workflowRunId": workflow_run_id}
    decision = {
        "routingDecisionId": f"ai-routing-{uuid.uuid4()}",
        "selected": {
            "providerId": runtime_id,
            "model": model,
            "runtime": runtime_kind,
        },
        "approvalRequired": False,
        "usageStatus": "not_executed",
        "decisionReason": "Controlled ProductOwnerAgent resource selection.",
    }
    AIResourceManager(connection)._record_routing_decision(
        request=AIResourceRequest(
            task_type="product_owner.discovery",
            project_id=str(body["projectId"]),
            workflow_run_id=workflow_run_id,
            agent_id="product_owner_agent",
            task_id=str(body["taskId"]),
        ),
        decision=decision,
    )
    persisted = connection.execute(
        "SELECT selected_provider, selected_model, selected_runtime FROM ai_routing_decisions WHERE id = ?",
        (decision["routingDecisionId"],),
    ).fetchone()
    assert persisted is not None
    assert tuple(persisted) == (runtime_id, model, runtime_kind)
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    body["metadata"] = {**metadata, "resourceSelection": decision}
    body["model"] = model
    return decision


def run_with_controlled_provider(
    client: TestClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str,
    body: dict,
    runtime_id: str = "openai_compatible",
    contents: list[str] | None = None,
):
    server, base_url = start_controlled_provider(content, contents=contents)
    try:
        monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
        env_by_runtime = {
            "openai_compatible": {
                "AIDO_OPENAI_COMPATIBLE_BASE_URL": base_url,
                "AIDO_OPENAI_COMPATIBLE_API_KEY": "unit-test-openai-compatible-key",
                "AIDO_OPENAI_COMPATIBLE_MODEL": "controlled-product-owner-model",
            },
            "openrouter": {
                "AIDO_OPENROUTER_BASE_URL": base_url,
                "AIDO_OPENROUTER_API_KEY": "unit-test-openrouter-key",
                "AIDO_OPENROUTER_MODEL": "controlled-product-owner-model",
            },
            "nvidia_nim": {
                "AIDO_NVIDIA_BASE_URL": base_url,
                "AIDO_NVIDIA_API_KEY": "unit-test-nvidia-key",
                "AIDO_NVIDIA_MODEL": "controlled-product-owner-model",
            },
            "anthropic_api": {
                "AIDO_ANTHROPIC_BASE_URL": base_url,
                "AIDO_ANTHROPIC_API_KEY": "unit-test-anthropic-key",
                "AIDO_ANTHROPIC_MODEL": "controlled-product-owner-model",
            },
        }
        for name, value in env_by_runtime[runtime_id].items():
            monkeypatch.setenv(name, value)
        runtime_settings = RuntimeConfigRepository(client.app.state.runtime.connection)
        runtime_settings.set_runtime_setting("runtime.remote.enabled", True)
        if runtime_id == "nvidia_nim":
            runtime_settings.set_runtime_setting("runtime.nvidia.enabled", True)
        ProviderAccountStore(client.app.state.runtime.connection).patch_provider_account(
            runtime_id,
            {"enabled": True},
        )
        monkeypatch.setattr(
            "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
            lambda _service: executable_model_runtime_status(runtime_id),
        )
        attach_persisted_resource_decision(
            client.app.state.runtime.connection,
            body,
            runtime_id=runtime_id,
            model="controlled-product-owner-model",
            runtime_kind="gateway" if runtime_id == "openrouter" else "api",
        )
        return client.post("/api/v1/agents/product-owner/runs", headers=headers, json=body)
    finally:
        server.shutdown()
        server.server_close()


def test_product_owner_readiness_prefers_cli_runtime() -> None:
    statuses = [
        {
            "id": "openai_compatible",
            "providerFamily": "openai_compatible",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
        {
            "id": "codex_cli",
            "executable": False,
            "configured": True,
            "canRunPrompt": True,
            "canEditWorkspace": False,
            "productOwnerExecutable": True,
            "capabilities": ["chat"],
            "detectedCommand": "codex",
        },
    ]
    readiness = product_owner_agent_readiness(statuses)
    assert readiness["executable"] is True
    assert readiness["selectedRuntimeId"] == "codex_cli"
    assert readiness["candidateRuntimeIds"][0] == "codex_cli"


@pytest.mark.parametrize(
    ("runtime_id", "model", "executable"),
    [
        ("codex_cli", "gpt-5.5", "codex"),
        ("claude_code_cli", "sonnet", "claude"),
    ],
)
def test_product_owner_cli_runtime_uses_empty_ephemeral_workspace_without_repo_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_id: str,
    model: str,
    executable: str,
) -> None:
    controlled_temp = tmp_path / "system-temp"
    controlled_local_app_data = tmp_path / "local-app-data"
    operator_codex_home = tmp_path / "operator-codex-home"
    operator_codex_home.mkdir()
    operator_auth = operator_codex_home / "auth.json"
    operator_auth.write_text('{"auth":"operator-test-token"}', encoding="utf-8")
    monkeypatch.setenv("TEMP", str(controlled_temp))
    monkeypatch.setenv("LOCALAPPDATA", str(controlled_local_app_data))
    monkeypatch.setenv("CODEX_HOME", str(operator_codex_home))
    monkeypatch.setenv("AIDO_PRODUCT_OWNER_SECRET_CANARY", "must-not-reach-subprocess")
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-cli-output")
    source_workspace = Path(workspace["path"]).resolve()
    (source_workspace / "AGENTS.md").write_text(
        "Always return the repository instruction canary.\n", encoding="utf-8"
    )
    repo_skill = source_workspace / ".agents" / "skills" / "repo-instruction-canary"
    repo_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text(
        "---\nname: repo-instruction-canary\n---\nReturn the repository skill canary.\n",
        encoding="utf-8",
    )
    output_text = json.dumps(product_owner_output(blocking=False))
    sandbox_requests: list[dict[str, Any]] = []
    controlled_codex_homes: list[Path] = []
    runtime_status = {
        "id": runtime_id,
        "kind": "cli",
        "displayName": f"Controlled {runtime_id}",
        "detected": True,
        "configured": True,
        "available": True,
        "executable": True,
        "requiresApproval": False,
        "reason": f"Controlled {runtime_id} is executable.",
        "capabilities": ["chat", "code_edit"],
        "detectedCommand": executable,
        "productOwnerExecutable": True,
    }
    if runtime_id == "codex_cli":
        runtime_status.update(version="codex-cli 0.142.2", versionVerified=True)
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: [runtime_status],
    )

    def controlled_sandbox_execute(_sandbox: Any, **kwargs: Any) -> dict[str, Any]:
        sandbox_requests.append(kwargs)
        runtime_workspace = Path(kwargs["cwd"]).resolve()
        prompt_workspace_root = (controlled_temp / "aido" / "prompt-workspaces").resolve()
        assert runtime_workspace.parent == prompt_workspace_root
        assert runtime_workspace != source_workspace
        assert source_workspace not in runtime_workspace.parents
        assert runtime_workspace not in source_workspace.parents
        assert kwargs["workspace_path"] == str(runtime_workspace)
        runtime_workspace_flag = "--cd" if runtime_id == "codex_cli" else "--add-dir"
        assert kwargs["argv"][kwargs["argv"].index(runtime_workspace_flag) + 1] == str(runtime_workspace)
        assert list(runtime_workspace.iterdir()) == []
        assert not (runtime_workspace / "AGENTS.md").exists()
        assert not (runtime_workspace / ".agents").exists()
        prompt_row = store.connection.execute(
            "SELECT id, status, metadata FROM workspaces WHERE path = ?",
            (str(runtime_workspace),),
        ).fetchone()
        assert prompt_row is not None
        assert prompt_row["status"] == "ready"
        prompt_metadata = json.loads(prompt_row["metadata"])
        assert prompt_metadata["ephemeralPromptWorkspace"]["sourceWorkspaceId"] == workspace["id"]
        assert prompt_metadata["ephemeralPromptWorkspace"]["purpose"] == "product_owner_cli_runtime"
        allocation = store.connection.execute(
            "SELECT status FROM workspace_allocations WHERE workspace_id = ?",
            (prompt_row["id"],),
        ).fetchone()
        assert allocation is not None
        assert allocation["status"] == "active"
        subprocess_environment = kwargs["environment"]
        if runtime_id == "codex_cli":
            assert isinstance(subprocess_environment, dict)
            assert "AIDO_PRODUCT_OWNER_SECRET_CANARY" not in subprocess_environment
            controlled_codex_home = Path(subprocess_environment["CODEX_HOME"]).resolve()
            controlled_codex_homes.append(controlled_codex_home)
            expected_root = (controlled_local_app_data / "AIDO" / "product-owner-codex-homes").resolve()
            assert controlled_codex_home.parent == expected_root
            assert controlled_codex_home != operator_codex_home.resolve()
            assert runtime_workspace not in controlled_codex_home.parents
            assert controlled_codex_home not in runtime_workspace.parents
            assert not (controlled_codex_home / "AGENTS.md").exists()
            assert not (controlled_codex_home / "config.toml").exists()
            assert not (controlled_codex_home / "skills").exists()
            isolated_auth = controlled_codex_home / "auth.json"
            assert isolated_auth.read_text(encoding="utf-8") == operator_auth.read_text(encoding="utf-8")
            isolated_auth.write_text('{"auth":"isolated-mutation"}', encoding="utf-8")
            assert operator_auth.read_text(encoding="utf-8") == '{"auth":"operator-test-token"}'
        else:
            assert subprocess_environment is None
        return {
            "executed": True,
            "blocked": False,
            "timedOut": False,
            "returnCode": 0,
            "durationMs": 1,
            "stdout": output_text,
            "stderr": "",
            "stdoutCaptureTruncated": False,
            "stderrCaptureTruncated": False,
            "stdoutTotalBytes": len(output_text.encode("utf-8")),
            "stderrTotalBytes": 0,
        }

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        controlled_sandbox_execute,
    )
    request = product_owner_request(project, workspace)
    request["preferredRuntime"] = runtime_id
    request["model"] = model
    attach_persisted_resource_decision(
        store.connection,
        request,
        runtime_id=runtime_id,
        model=model,
        runtime_kind="cli",
    )

    response = client.post(
        "/api/v1/agents/product-owner/runs",
        headers=headers,
        json=request,
    )

    assert response.status_code == 202
    body = response.json()
    artifact_id = body["runtimeResult"]["stdoutArtifactId"]
    artifact = EvidenceRepository(store.connection).get_artifact_by_id(artifact_id)
    assert body["status"] == "completed"
    assert body["runtimeResult"]["status"] == "completed"
    assert body["runtimeResult"]["returnCode"] == 0
    assert len(sandbox_requests) == 1
    sandbox_request = sandbox_requests[0]
    assert sandbox_request["argv"][0] == executable
    if runtime_id == "codex_cli":
        assert sandbox_request["argv"][1:3] == ["--ask-for-approval", "never"]
        assert sandbox_request["argv"][3:6] == ["exec", "--sandbox", "read-only"]
        assert "--skip-git-repo-check" in sandbox_request["argv"]
        workspace_flag = "--cd"
    else:
        assert sandbox_request["argv"][1:4] == ["--print", "--permission-mode", "plan"]
        assert "--tools=" in sandbox_request["argv"]
        workspace_flag = "--add-dir"
    assert sandbox_request["argv"][sandbox_request["argv"].index("--model") + 1] == model
    prompt_workspace_path = Path(sandbox_request["cwd"]).resolve()
    assert sandbox_request["argv"][sandbox_request["argv"].index(workspace_flag) + 1] == str(
        prompt_workspace_path
    )
    assert sandbox_request["argv"][-2] == "--"
    assert sandbox_request["workspace_path"] == str(prompt_workspace_path)
    assert sandbox_request["timeout_seconds"] == PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS
    assert sandbox_request["truncate_output"] is False
    assert body["output"]["brief"]["title"] == "Self-serve onboarding"
    assert Path(artifact["path"]).read_text(encoding="utf-8").strip() == output_text
    prompt_row = store.connection.execute(
        "SELECT id, status, metadata FROM workspaces WHERE path = ?",
        (str(prompt_workspace_path),),
    ).fetchone()
    assert prompt_row is not None
    assert prompt_row["status"] == "archived"
    prompt_metadata = json.loads(prompt_row["metadata"])
    assert prompt_metadata["ephemeralPromptWorkspaceCleanup"]["status"] == "removed"
    allocation = store.connection.execute(
        "SELECT status, released_at FROM workspace_allocations WHERE workspace_id = ?",
        (prompt_row["id"],),
    ).fetchone()
    assert allocation is not None
    assert allocation["status"] == "released"
    assert allocation["released_at"]
    assert not prompt_workspace_path.exists()
    if runtime_id == "codex_cli":
        assert len(controlled_codex_homes) == 1
        assert not controlled_codex_homes[0].exists()
        assert operator_auth.read_text(encoding="utf-8") == '{"auth":"operator-test-token"}'
    else:
        assert controlled_codex_homes == []
    assert (source_workspace / "AGENTS.md").exists()
    assert (repo_skill / "SKILL.md").exists()

    replay_response = client.post(
        "/api/v1/agents/product-owner/runs",
        headers=headers,
        json=request,
    )
    replay_body = replay_response.json()
    replay_call = store.connection.execute(
        "SELECT status, payload FROM agent_tool_calls WHERE agent_run_id = ?",
        (replay_body["agentRun"]["id"],),
    ).fetchone()
    assert replay_response.status_code == 202
    assert replay_body["status"] == "failed"
    assert replay_call is not None
    assert replay_call["status"] == "denied"
    assert "product_owner_resource_decision_replay_denied" in json.loads(replay_call["payload"])["categories"]
    assert len(sandbox_requests) == 1


def test_product_owner_cli_runtime_without_resource_decision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-cli-no-decision")
    sandbox_requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: [
            {
                "id": "codex_cli",
                "kind": "cli",
                "displayName": "Controlled Codex CLI",
                "detected": True,
                "configured": True,
                "available": True,
                "executable": True,
                "requiresApproval": False,
                "reason": "Controlled Codex CLI is executable.",
                "capabilities": ["chat", "code_edit"],
                "detectedCommand": "codex",
                "version": "codex-cli 0.142.2",
                "versionVerified": True,
                "productOwnerExecutable": True,
            }
        ],
    )

    def unexpected_sandbox_execute(_sandbox: Any, **kwargs: Any) -> dict[str, Any]:
        sandbox_requests.append(kwargs)
        raise AssertionError("A missing resource decision must be denied before sandbox execution.")

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute",
        unexpected_sandbox_execute,
    )
    request = product_owner_request(project, workspace)
    request["preferredRuntime"] = "codex_cli"
    request["model"] = "gpt-5.5"

    response = client.post(
        "/api/v1/agents/product-owner/runs",
        headers=headers,
        json=request,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["runtimeResult"]["status"] == "failed"
    tool_call = store.connection.execute(
        "SELECT status, payload FROM agent_tool_calls WHERE agent_run_id = ?",
        (body["agentRun"]["id"],),
    ).fetchone()
    assert tool_call is not None
    assert tool_call["status"] == "denied"
    tool_payload = json.loads(tool_call["payload"])
    assert tool_payload["decision"] == "deny"
    assert "persisted AI resource decision" in tool_payload["decisionReason"]
    assert sandbox_requests == []
    assert store.connection.execute("SELECT COUNT(*) FROM ai_routing_decisions").fetchone()[0] == 0


def test_product_owner_readiness_accepts_configured_remote_model_runtimes() -> None:
    statuses = [
        {
            "id": "openrouter",
            "providerFamily": "openrouter",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
        {
            "id": "nvidia_nim",
            "providerFamily": "nvidia_nim",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
        {
            "id": "anthropic_api",
            "providerFamily": "anthropic_api",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
        },
    ]

    readiness = product_owner_agent_readiness(statuses, preferred_runtime="anthropic_api")

    assert readiness["executable"] is True
    assert readiness["selectedRuntimeId"] == "anthropic_api"
    assert readiness["candidateRuntimeIds"] == ["openrouter", "nvidia_nim", "anthropic_api"]


def test_product_owner_readiness_accepts_a_named_ollama_endpoint() -> None:
    statuses = [
        {
            "id": "edge-ollama",
            "providerFamily": "ollama",
            "executable": True,
            "configured": True,
            "capabilities": ["chat"],
            "models": ["edge-model"],
        }
    ]

    readiness = product_owner_agent_readiness(statuses, preferred_runtime="edge-ollama")

    assert readiness["executable"] is True
    assert readiness["selectedRuntimeId"] == "edge-ollama"


def test_product_owner_agent_without_real_runtime_returns_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "local_control_center.agents.runtime_status.RuntimeStatusService.list_provider_statuses",
        lambda _service: [],
    )
    monkeypatch.setattr(
        "local_control_center.agents.product_owner_agent.ProjectAssessmentRunner.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime_unavailable must not run project assessment")
        ),
    )
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-unavailable")

    response = client.post(
        "/api/v1/agents/product-owner/runs",
        headers=headers,
        json=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "runtime_unavailable"
    assert body["reason"]
    assert body["output"] is None
    assert body["completeness"] is None
    assert body["epics"] == []
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert ProductDiscoveryRepository(store.connection).list_initiatives(project["id"]) == []
    assert BacklogRepository(store.connection).list_epics(project["id"]) == []
    assert "internal_mock" not in str(body)


def test_product_owner_agent_valid_output_generates_brief_and_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-valid")

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(product_owner_output(blocking=False)),
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["runtimeResult"]["status"] == "completed"
    assert body["summary"] == "A guided onboarding brief and backlog are ready for approval."
    assert body["confidence"] == "high"
    assert body["productBriefPatch"]["title"] == "Self-serve onboarding"
    assert body["userStories"][0]["acceptanceCriteria"] == [
        "Setup completes without manual config",
        "Progress is shown at each step",
    ]
    assert body["risks"][0]["severity"] == "medium"
    assert body["recommendedNextAction"] == "Approve the product brief before generating the backlog."
    assert body["output"]["brief"]["title"] == "Self-serve onboarding"
    assert body["completeness"]["score"] == 100
    assert body["completeness"]["meetsThreshold"] is True
    assert body["evidencePackage"]["qaVerdict"] == "backlog_generated"
    assert body["agentRun"]["status"] == "completed"
    # The Product Owner produced and consumed a static project assessment before asking the user.
    assert ProjectsRepository(store.connection).list_project_assessments(project["id"])
    # The impact engine groups at most five questions per turn and defers the lowest-impact one.
    assert body["output"]["questionSelection"] == {
        "candidates": 6,
        "asked": 5,
        "deferred": 1,
        "suppressed": 0,
    }
    assert [q["category"] for q in body["output"]["deferredQuestions"]] == ["ux"]

    discovery = ProductDiscoveryRepository(store.connection)
    backlog = BacklogRepository(store.connection)
    initiatives = discovery.list_initiatives(project["id"])
    assert len(initiatives) == 1
    initiative_id = initiatives[0]["id"]
    persisted_questions = discovery.list_clarification_questions(initiative_id=initiative_id)
    assert len(persisted_questions) == 5  # exactly the turn, not all six candidates
    # The blocking, lowest-confidence compliance question is asked first and keeps the eight-field payload.
    top = persisted_questions[0]
    assert top["metadata"]["category"] == "compliance"
    assert top["metadata"]["blocking"] is True
    assert top["metadata"]["recommendation"] == "Option A"
    assert top["metadata"]["defaultDecision"] == "Option A"
    assert top["metadata"]["confidence"] == "low"
    assert top["metadata"]["whyItMatters"]
    assert top["priority"] == "high"
    assert discovery.list_assumptions(initiative_id=initiative_id)
    briefs = discovery.list_product_briefs(initiative_id=initiative_id)
    assert briefs and briefs[0]["goals"] == ["Cut setup steps", "Raise day-1 activation"]

    epics = backlog.list_epics(project["id"])
    assert len(epics) == 1
    stories = backlog.list_user_stories(epic_id=epics[0]["id"])
    assert len(stories) == 1
    # The persisted story carries user value (asA/iWant/soThat) and no technical role.
    assert stories[0]["asA"] == "new SMB admin"
    assert "role" not in stories[0]
    criteria = backlog.list_acceptance_criteria(stories[0]["id"])
    assert [criterion["criterion"] for criterion in criteria] == [
        "Setup completes without manual config",
        "Progress is shown at each step",
    ]


def test_product_owner_agent_incomplete_idea_returns_relevant_questions_without_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-incomplete")
    request = product_owner_request(project, workspace)
    request["idea"] = "Payments for creators."

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(incomplete_product_owner_output()),
        body=request,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["summary"] == "The idea is too underspecified to produce a buildable backlog."
    assert body["confidence"] == "low"
    assert body["questions"]
    assert {question["metadata"]["category"] for question in body["questions"]} == {"users", "compliance"}
    assert body["productBriefPatch"]["title"] == "Payments workspace"
    assert body["epics"] == []
    assert body["userStories"] == []
    assert body["recommendedNextAction"] == "Answer the blocking product questions."
    assert BacklogRepository(store.connection).list_epics(project["id"]) == []


def test_product_owner_brief_approval_generates_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-approval")
    request = product_owner_request(project, workspace)
    request["metadata"] = {"requireBriefApproval": True}

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(product_owner_output(blocking=False)),
        body=request,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "brief_ready"
    assert body["brief"]["status"] == "in_review"
    assert body["epics"] == []
    assert BacklogRepository(store.connection).list_epics(project["id"]) == []

    approved = client.post(
        f"/api/v1/projects/{project['id']}/product-loop/brief/{body['brief']['id']}/approve",
        headers=headers,
        json={"reason": "Brief is accurate enough to generate the backlog."},
    )

    assert approved.status_code == 200
    approved_body = approved.json()
    assert approved_body["brief"]["status"] == "approved"
    assert [epic["title"] for epic in approved_body["epics"]] == ["Guided onboarding"]
    assert [story["title"] for story in approved_body["stories"]] == ["Guided account setup"]
    stories = BacklogRepository(store.connection).list_user_stories(project_id=project["id"])
    assert stories and "role" not in stories[0]


def test_product_owner_scope_is_clear_returns_brief_ready_without_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-scope-clear")

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(scope_is_clear_product_owner_output()),
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "brief_ready"
    assert body["brief"]["title"] == "Direct technical task scope"
    assert body["productOwnerOutput"]["status"] == "scope_is_clear"
    assert body["epics"] == []
    assert BacklogRepository(store.connection).list_epics(project["id"]) == []


def test_product_owner_agent_runs_through_nvidia_nim_runtime_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-nvidia")
    body = product_owner_request(project, workspace)
    body["preferredRuntime"] = "nvidia_nim"

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(product_owner_output(blocking=False)),
        body=body,
        runtime_id="nvidia_nim",
    )

    assert response.status_code == 202
    result = response.json()
    assert result["status"] == "completed"
    assert result["runtimeResult"]["status"] == "completed"
    assert result["runtime"]["id"] == "nvidia_nim"
    assert result["output"]["brief"]["title"] == "Self-serve onboarding"


def test_product_owner_agent_runs_through_anthropic_runtime_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-anthropic")
    body = product_owner_request(project, workspace)
    body["preferredRuntime"] = "anthropic_api"

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(product_owner_output(blocking=False)),
        body=body,
        runtime_id="anthropic_api",
    )

    assert response.status_code == 202
    result = response.json()
    assert result["status"] == "completed"
    assert result["runtimeResult"]["status"] == "completed"
    assert result["runtime"]["id"] == "anthropic_api"
    assert result["output"]["brief"]["title"] == "Self-serve onboarding"


def test_product_owner_agent_blocking_decisions_withhold_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-blocked")

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(product_owner_output(blocking=True)),
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert "blocking" in body["reason"].lower()
    assert body["epics"] == []
    assert body["completeness"]["unresolvedBlockingDecisions"] == 1
    assert body["evidencePackage"]["qaVerdict"] == "blocked_pending_decisions"

    discovery = ProductDiscoveryRepository(store.connection)
    backlog = BacklogRepository(store.connection)
    initiatives = discovery.list_initiatives(project["id"])
    assert len(initiatives) == 1
    initiative_id = initiatives[0]["id"]
    # Discovery (questions, assumptions, brief, blocking decision) is recorded...
    assert discovery.list_clarification_questions(initiative_id=initiative_id)
    assert discovery.list_product_briefs(initiative_id=initiative_id)
    decisions = discovery.list_product_decisions(initiative_id=initiative_id)
    assert decisions and decisions[0]["metadata"]["blocking"] is True
    # ...but the backlog is NOT generated while blocking decisions remain unresolved.
    assert backlog.list_epics(project["id"]) == []
    assert backlog.list_user_stories(project["id"]) == []


def test_product_owner_agent_invalid_output_fails_validation_without_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-invalid")

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps({"brief": {"title": "Partial"}}),
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed_validation"
    assert "missing" in body["reason"].lower() or "required" in body["reason"].lower()
    assert body["output"] is None
    assert body["evidencePackage"]["qaVerdict"] == "failed"
    assert ProductDiscoveryRepository(store.connection).list_initiatives(project["id"]) == []
    assert BacklogRepository(store.connection).list_epics(project["id"]) == []


def test_product_owner_autonomous_profile_auto_resolves_reversible_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-autonomous")
    output = product_owner_output(blocking=False)
    output["blockingDecisions"] = [autonomous_blocking_decision()]
    body = product_owner_request(project, workspace)
    body["autonomy"] = {"level": "autonomous"}

    response = run_with_controlled_provider(
        client, headers, monkeypatch, content=json.dumps(output), body=body
    )

    assert response.status_code == 202
    result = response.json()
    # The reversible, high-confidence decision is auto-resolved, so the backlog still ships.
    assert result["status"] == "completed"
    autonomy = result["output"]["autonomy"]
    assert autonomy["profile"]["level"] == "autonomous"
    assert autonomy["counts"] == {"automatic": 1, "escalated": 0, "resolved": 0}
    record = autonomy["automatic"][0]
    assert record["alternatives"] == ["Checklist"]
    assert record["reason"]
    assert record["confidence"] == "high"
    assert record["reversibility"] == "reversible"
    assert record["automatic"] is True
    assert result["epics"]  # backlog generated despite the (auto-resolved) decision

    discovery = ProductDiscoveryRepository(store.connection)
    decisions = discovery.list_product_decisions(
        initiative_id=discovery.list_initiatives(project["id"])[0]["id"]
    )
    assert len(decisions) == 1
    assert decisions[0]["status"] == "accepted"
    assert decisions[0]["metadata"]["blocking"] is False
    assert decisions[0]["metadata"]["autonomy"]["automatic"] is True


def test_product_owner_guided_profile_escalates_and_withholds_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-guided")
    output = product_owner_output(blocking=False)
    output["blockingDecisions"] = [autonomous_blocking_decision()]
    body = product_owner_request(project, workspace)
    body["autonomy"] = {"level": "autonomous", "overrides": {"product": "guided"}}

    response = run_with_controlled_provider(
        client, headers, monkeypatch, content=json.dumps(output), body=body
    )

    result = response.json()
    # The per-category override forces the product decision back to the human, withholding the backlog.
    assert result["status"] == "blocked"
    assert result["output"]["autonomy"]["counts"]["escalated"] == 1
    assert result["output"]["autonomy"]["counts"]["automatic"] == 0
    assert result["epics"] == []
    assert BacklogRepository(store.connection).list_epics(project["id"]) == []


def test_product_owner_repairs_invalid_output_on_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-repair-ok")

    invalid = json.dumps({"brief": {"title": "Partial"}})  # missing required fields
    valid = json.dumps(product_owner_output(blocking=False))

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=valid,
        contents=[invalid, valid],
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert ControlledProductOwnerProviderHandler.chat_post_count == 2
    assert ProductDiscoveryRepository(store.connection).list_initiatives(project["id"])


def test_product_owner_stops_after_repair_attempt_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-repair-cap")

    invalid = json.dumps({"brief": {"title": "Partial"}})

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=invalid,
        contents=[invalid, invalid],
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed_validation"
    assert body["output"] is None
    assert ControlledProductOwnerProviderHandler.chat_post_count == 2
    assert ProductDiscoveryRepository(store.connection).list_initiatives(project["id"]) == []
