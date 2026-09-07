"""Regresión: el overview debe serializar los kinds/roles que el backend persiste de verdad.

Cubre la deriva de esquema que devolvía HTTP 500 (`ResponseValidationError`) cuando la base
contenía artefactos `project_assessment`/`product_owner_manifest` o un perfil con rol `assessor`:
valores que los subsistemas de assessment y product-owner escriben pero que el `response_model`
`OverviewResponse` no listaba en sus `Literal`. Si el contrato y lo persistido vuelven a divergir,
este test falla con el mismo 500 que veía el usuario.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.projects.repository import ProjectsRepository


def _client(tmp_path: Path, *, raise_server_exceptions: bool = True):
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_overview_serializes_assessment_and_product_owner_artifacts(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="Drift", path=tmp_path / "drift", template_id="other"
        )
        evidence = EvidenceRepository(runtime.connection)
        for kind in ("project_assessment", "product_owner_manifest"):
            evidence.create_artifact(
                project_id=project["id"],
                evidence_package_id=None,
                kind=kind,
                path=str(tmp_path / f"{kind}.json"),
            )
        AgentsRepository(runtime.connection).upsert_agent_profile(
            {
                "id": "assessor-profile",
                "name": "AssessorProfile",
                "role": "assessor",
                "runtimeMode": "manual",
                "permissionProfile": "plan",
            }
        )

        response = client.get("/api/v1/overview")

        assert response.status_code == 200, response.text
        payload = response.json()
        artifact_kinds = {artifact["kind"] for artifact in payload["artifacts"]}
        assert {"project_assessment", "product_owner_manifest"} <= artifact_kinds
        roles = {profile["role"] for profile in payload["agentProfiles"]}
        assert "assessor" in roles
    finally:
        runtime.close()


def test_unhandled_error_is_logged_and_returns_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Un error no controlado debe registrar la traza (seguimiento) y responder JSON estable,
    # nunca el texto plano "Internal Server Error" que el cliente no podía parsear.
    import local_control_center.api as api_module
    from local_control_center.shared.diagnostics import configure_diagnostics

    def _boom(**_kwargs):
        raise RuntimeError("forced failure for observability test")

    monkeypatch.setattr(api_module, "build_overview_from_connection", _boom)
    sink = configure_diagnostics(tmp_path / "diagnostics")
    runtime, client = _client(tmp_path, raise_server_exceptions=False)
    try:
        response = client.get("/api/v1/overview", headers={"X-Correlation-ID": "corr-forced-error"})

        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["error"] == "RuntimeError"
        assert "detail" in body
    finally:
        runtime.close()
        sink.close()
    events = [
        json.loads(line)
        for path in sink.root.glob("diag-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    errors = [event for event in events if event["event"] == "http.error"]
    assert len(errors) == 1
    error = errors[0]
    assert error["level"] == "ERROR"
    assert error["requestId"] == response.headers["X-Correlation-ID"] == "corr-forced-error"
    assert error["exceptionChain"][0]["type"] == "RuntimeError"
    assert error["exceptionChain"][0]["message"] == "forced failure for observability test"
    assert error["exceptionChain"][0]["frames"]
