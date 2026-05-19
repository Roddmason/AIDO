from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.agents.model_gateway import ModelGateway
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared import telemetry
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_http_requests_emit_redacted_telemetry_with_correlation_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    fixture = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    fixture.init()
    client = TestClient(create_app(runtime=fixture, static_dir=None))

    response = client.get(
        "/api/v1/overview",
        headers={"X-Correlation-ID": "corr-http-test", "X-Local-Control-Token": "must-not-leak"},
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr-http-test"
    event = next(item for item in fixture.events.list_events() if item["type"] == "telemetry.http.request")
    assert event["payload"]["correlationId"] == "corr-http-test"
    assert event["payload"]["method"] == "GET"
    assert event["payload"]["path"] == "/api/v1/overview"
    assert event["payload"]["statusCode"] == 200
    assert event["payload"]["durationMs"] >= 0
    assert "must-not-leak" not in str(event["payload"])


def test_policy_tool_and_model_operations_emit_trace_events(tmp_path: Path) -> None:
    fixture = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    fixture.init()
    project = fixture.create_project(name="Telemetry", path=tmp_path / "telemetry", template_id="other")
    agents = AgentsRepository(fixture.connection)
    profile = agents.upsert_agent_profile(
        {
            "id": "telemetry-agent",
            "name": "Telemetry Agent",
            "role": "implementer",
            "runtimeType": "internal_mock",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    model_policy = agents.upsert_model_policy(
        {
            "id": "telemetry_policy",
            "name": "Telemetry Policy",
            "preferred": [{"provider": "internal_mock", "model": "mock"}],
            "fallback": [],
            "maxCostUsd": 1.0,
            "allowRemote": False,
            "allowLocal": True,
        }
    )
    agent_run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id="telemetry-task",
        input_payload={"goal": "trace"},
        output_payload={"verdict": "approved_with_risks"},
    )

    decision = SecurityPolicyRepository(fixture.connection).record_decision(
        project_id=project["id"],
        workspace_id=None,
        agent_id=profile["id"],
        role=profile["role"],
        tool="shell",
        command="pytest",
        path=str(tmp_path),
        decision="allow",
        risk_level="low",
        reason="Allowed test command.",
        payload={"source": "unit-test"},
    )
    tool_result = ToolBroker(fixture.connection).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=agent_run["id"],
        agent_profile=profile,
        tool_call={"tool": "shell", "command": "pytest", "path": str(tmp_path), "execute": False},
    )
    model_result = ModelGateway(fixture.connection).prepare_model_call(
        project_id=project["id"],
        model_policy_id=model_policy["id"],
        estimated_cost_usd=0.01,
        prompt_tokens=10,
        completion_tokens=5,
        agent_run_id=agent_run["id"],
        metadata={"authorization": "Bearer secret-token"},
    )

    events = fixture.events.list_events(project_id=project["id"])
    agent_event = next(item for item in events if item["type"] == "agent.run.completed")
    policy_event = next(
        item
        for item in events
        if item["type"] == "telemetry.policy.decision"
        and item["payload"]["permissionDecisionId"] == decision["id"]
    )
    tool_event = next(item for item in events if item["type"] == "telemetry.tool.call")
    model_event = next(item for item in events if item["type"] == "telemetry.model.call")

    assert agent_event["payload"]["agentRunId"] == agent_run["id"]
    assert agent_event["payload"]["agentProfileId"] == profile["id"]
    assert policy_event["payload"]["permissionDecisionId"] == decision["id"]
    assert policy_event["payload"]["decision"] == "allow"
    assert tool_event["payload"]["toolCallId"] == tool_result["toolCall"]["id"]
    assert tool_event["payload"]["agentRunId"] == agent_run["id"]
    assert model_event["payload"]["modelCallId"] == model_result["modelCall"]["id"]
    assert model_event["payload"]["status"] == "prepared"
    assert "secret-token" not in str(model_event["payload"])


def test_external_telemetry_exporter_is_optional_and_receives_redacted_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AIDO_OTEL_EXPORTER", raising=False)
    fixture = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    fixture.init()
    client = TestClient(create_app(runtime=fixture, static_dir=None))

    telemetry.configure_external_telemetry_from_env()
    status = client.get("/api/v1/telemetry/status").json()["externalExporter"]

    assert status["enabled"] is False
    assert status["mode"] == "none"

    class FakeExporter:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        def export_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    fake = FakeExporter()
    telemetry.set_external_exporter_for_tests(fake, mode="test_exporter")
    fake_secret_value = "sk-" + "testsecret1234567890"
    try:
        event = telemetry.record_telemetry_event(
            fixture.connection,
            event_type="telemetry.model.call",
            payload={
                "provider": "openai_compatible",
                "apiKey": fake_secret_value,
                "authorization": "Bearer secret-token",
                "status": "prepared",
            },
            project_id="project-telemetry",
            correlation_id="corr-exporter",
        )
    finally:
        telemetry.clear_external_exporter_for_tests()

    assert fake.events
    exported = fake.events[0]
    assert exported["id"] == event["id"]
    assert exported["type"] == "telemetry.model.call"
    assert exported["projectId"] == "project-telemetry"
    payload = exported["payload"]
    assert isinstance(payload, dict)
    assert payload["apiKey"] == "[redacted]"
    assert payload["authorization"] == "[redacted]"
    assert "testsecret" not in str(exported)
