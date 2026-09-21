from __future__ import annotations

from fastapi.testclient import TestClient


def test_safe_read_api_and_configuration_rollback(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "faiss", None)
    monkeypatch.delenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", raising=False)
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
            status = client.get("/api/v1/decision-engine")
            assert status.status_code == 200
            assert status.json()["mode"] == "shadow"
            assert status.json()["enabled"] is True
            assert status.json()["status"] == "unavailable"
            assert "api_key_reference" not in status.text
            assert "TYPESAFE_API_KEY" not in status.text
            assert client.get("/api/v1/decision-engine/decisions").json()["items"] == []
            report = client.get("/api/v1/decision-engine/report").json()
            assert report["metrics"]["agreement_rate"] is None
            assert report["metrics"]["decision_count"] == 0
            assert client.get("/api/v1/decision-engine/decisions?limit=1001").status_code == 422
            assert client.post("/api/v1/decision-engine/decide", json={}).status_code in {404, 405}
            token = runtime.get_handshake()["token"]
            headers = {"X-Local-Control-Token": token}
            assert (
                client.put(
                    "/api/v1/settings/decision_engine.enabled",
                    json={"scope": "general", "value": True},
                    headers=headers,
                ).status_code
                == 204
            )
            assert client.get("/api/v1/decision-engine").json()["enabled"] is True
            assert (
                client.put(
                    "/api/v1/settings/decision_engine.enabled",
                    json={"scope": "general", "value": False},
                    headers=headers,
                ).status_code
                == 204
            )
            assert client.get("/api/v1/decision-engine").json()["enabled"] is False
    finally:
        runtime.close()


def test_http_intake_records_three_shadow_receipts_after_commit(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "faiss", None)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setattr("local_control_center.decision_engine.service.real_jev_calls_enabled", lambda: True)
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.decision_engine.models import DecisionResult
    from local_control_center.projects.repository import ProjectsRepository
    from local_control_center.settings.repository import SettingsRepository
    from local_control_center.threads.repository import ThreadsRepository

    class IntakeProvider:
        async def decide(self, request):
            selected = request.candidates[-1].id
            return DecisionResult(
                selected=selected,
                ranking=(selected, *sorted(item.id for item in request.candidates if item.id != selected)),
                probabilities={item.id: float(item.id == selected) for item in request.candidates},
                confidence=1.0,
                margin=1.0,
                engine="typesafe",
                provider="jev",
                model="jev-1.13.0",
                version="1.13.0",
                decision_type=request.decision_type,
                reason_code="provider_selected",
            )

    monkeypatch.setattr(
        "local_control_center.decision_engine.service.JevDecisionProvider", lambda config: IntakeProvider()
    )
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
            project = ProjectsRepository(runtime.connection).create_project(
                name="shadow-http",
                path=tmp_path / "workspace",
                template_id="other",
                create_directory=True,
                source="runtime",
            )
            thread = ThreadsRepository(runtime.connection).create_thread(
                project_id=project["id"], owner_type="workspace", owner_id="workspace-1", title="Shadow HTTP"
            )
            SettingsRepository(runtime.connection).set_value("decision_engine.enabled", "general", None, True)
            headers = {"X-Local-Control-Token": runtime.get_handshake()["token"]}
            result = client.post(
                f"/api/v1/threads/{thread['id']}/messages",
                headers=headers,
                json={
                    "content": "Add a dashboard endpoint",
                    "projectAssessment": {
                        "aidoCapabilities": {
                            "runtimes": [{"id": "codex", "executable": True, "canEditWorkspace": True}]
                        }
                    },
                },
            )
            assert result.status_code == 200, result.text
            receipts = client.get("/api/v1/decision-engine/decisions").json()["items"]
            assert {item["decisionType"] for item in receipts} == {
                "workflow_classification",
                "agent_role_selection",
                "escalation_decision",
            }
            assert all(item["status"] == "completed" for item in receipts)
            assert any(item["jevRecommendation"] is not None for item in receipts)
            assert all(item["reasonCode"] != "transaction_active" for item in receipts)
            assert result.json()["run"]["status"] == "queued"
    finally:
        runtime.close()


def test_skipped_provider_does_not_report_healthy(tmp_path, monkeypatch):
    import asyncio
    import sys

    monkeypatch.setitem(sys.modules, "faiss", None)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-status-key")
    monkeypatch.setattr("local_control_center.decision_engine.api.real_jev_calls_enabled", lambda: True)
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.decision_engine.config import resolve_config
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from local_control_center.settings.repository import SettingsRepository
    from tests_py.test_decision_engine import FakeJev, request_for

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
            SettingsRepository(runtime.connection).set_value("decision_engine.enabled", "general", None, True)
            request = request_for(effective_decision=None)
            asyncio.run(
                ShadowDecisionEngine(
                    runtime.connection, config=resolve_config(runtime.connection, None), provider=FakeJev()
                ).observe(request)
            )
            assert client.get("/api/v1/decision-engine").json()["status"] == "unknown"
    finally:
        runtime.close()
