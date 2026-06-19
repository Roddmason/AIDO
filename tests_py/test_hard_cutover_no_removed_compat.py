from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def make_client(tmp_path: Path) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=store)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]
    return store, client, {"X-Local-Control-Token": token}


def test_removed_backend_and_frontend_contracts_are_absent() -> None:
    removed_package = "leg" + "acy_compat"
    removed_state_route = "/api/" + "state"
    removed_workspace_key = "workspace" + "State"
    removed_workspace_file = "team-" + "workspace.json"

    assert not (ROOT / "local_control_center" / removed_package).exists()
    assert not (ROOT / "local-control-center" / ("build" + ".mjs")).exists()
    assert not (ROOT / "local-control-center" / "web" / ("dashboard-client" + ".jsx")).exists()
    assert not (ROOT / "local-control-center" / "web" / ("platform-client" + ".jsx")).exists()

    active_sources = "\n".join(
        read(str(path.relative_to(ROOT)))
        for path in [
            *Path(ROOT / "local_control_center").rglob("*.py"),
            *Path(ROOT / "local-control-center" / "web").rglob("*.*"),
            *Path(ROOT / "tests_py").rglob("*.py"),
            *Path(ROOT / "tests_web").rglob("*.*"),
        ]
        if path.is_file()
    )
    assert removed_state_route not in active_sources
    assert removed_workspace_key not in active_sources
    assert removed_package not in active_sources
    assert removed_workspace_file not in active_sources


def test_frontend_is_vite_typescript_and_dependency_surface_is_pruned() -> None:
    package = json.loads(read("package.json"))
    dependencies = package["dependencies"]
    dev_dependencies = package["devDependencies"]

    assert (ROOT / "local-control-center" / "web" / "src" / "main.tsx").exists()
    assert (ROOT / "local-control-center" / "web" / "vite.config.ts").exists()
    assert package["scripts"]["build:web"] == "vite build --config local-control-center/web/vite.config.ts"
    assert package["scripts"]["build:control-center"] == "corepack pnpm@10.24.0 run build:web"

    assert "@gsap/react" not in dependencies
    assert "gsap" not in dependencies
    assert "vite" in dev_dependencies
    assert "@vitejs/plugin-react" in dev_dependencies
    obsolete_dependencies = [
        "@inkjs/" + "ui",
        "ink",
        "ink-text-" + "input",
        "sil" + "eo",
        "sql" + ".js",
        "source-" + "map",
        "react-devtools-" + "core",
        "es" + "build",
    ]
    for obsolete in obsolete_dependencies:
        assert obsolete not in dependencies
        assert obsolete not in dev_dependencies

    assert not list((ROOT / "local-control-center" / "web").rglob("*.jsx"))


def test_v1_sessions_chats_pipelines_replace_workspace_state(tmp_path: Path) -> None:
    _store, client, headers = make_client(tmp_path)
    project_id = client.get("/api/v1/projects").json()["projects"][0]["id"]

    session_response = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"projectId": project_id, "name": "Cutover session"},
    )
    assert session_response.status_code == 201
    session = session_response.json()["session"]

    chat_response = client.post(
        "/api/v1/chats",
        headers=headers,
        json={"projectId": project_id, "sessionId": session["id"], "prompt": "Plan the hard cutover"},
    )
    assert chat_response.status_code == 201
    chat = chat_response.json()["chat"]

    pipeline_response = client.post(
        "/api/v1/pipelines",
        headers=headers,
        json={
            "projectId": project_id,
            "sessionId": session["id"],
            "chatId": chat["id"],
            "title": "Cutover pipeline",
        },
    )
    assert pipeline_response.status_code == 201

    overview = client.get("/api/v1/overview").json()
    removed_workspace_key = "workspace" + "State"
    assert removed_workspace_key not in overview
    assert any(item["id"] == session["id"] for item in overview["sessions"])
    assert any(item["id"] == chat["id"] for item in overview["chats"])
    assert any(item["id"] == pipeline_response.json()["pipeline"]["id"] for item in overview["pipelines"])
    removed_state_route = "/api/" + "state"
    assert client.get(removed_state_route).status_code == 404


def test_removed_compatibility_routes_are_not_mounted(tmp_path: Path) -> None:
    _store, _client, _headers = make_client(tmp_path)
    app = _client.app
    mounted_paths = {getattr(route, "path", "") for route in app.routes}
    removed_routes = {
        "/api/" + "state",
        "/api/v1/model-gateway/route/" + "execute-mock",
        "/api/v1/model-providers",
        "/api/v1/model-policies",
    }

    assert mounted_paths.isdisjoint(removed_routes)
    assert not any(("legacy" in path.lower() or "compat" in path.lower()) for path in mounted_paths)


def test_frontend_source_does_not_call_removed_legacy_endpoints() -> None:
    removed_literals = (
        "/api/" + "state",
        "/api/v1/model-gateway/route/" + "execute-mock",
        "/api/v1/model-providers",
        "/api/v1/model-policies",
        "list_model_providers_api_v1_model_providers_get",
        "list_model_policies_api_v1_model_policies_get",
        "upsert_model_policy_api_v1_model_policies_post",
        "createModelPolicy",
        "route_" + "execute_mock",
        "workspace" + "State",
    )
    offenders: list[str] = []
    for path in (ROOT / "local-control-center" / "web" / "src").rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        if any(literal in source for literal in removed_literals):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_hybrid_runtime_catalog_exposes_ollama_and_cli_api_modes(tmp_path: Path) -> None:
    _store, client, headers = make_client(tmp_path)
    assert client.get("/api/v1/model-providers").status_code == 404
    assert client.get("/api/v1/model-policies").status_code == 404
    providers = client.get("/api/v1/runtime/providers").json()["providers"]
    provider_ids = {provider["id"] for provider in providers}
    assert {
        "ollama",
        "openai_compatible",
        "openrouter",
        "openai_api",
        "codex_cli",
        "claude_code_cli",
        "manual",
    } <= provider_ids
    manual_provider = next(provider for provider in providers if provider["id"] == "manual")
    assert manual_provider["available"] is False

    runtime_status = client.get("/api/v1/runtime/providers").json()
    assert runtime_status["ollama"]["provider"] == "ollama"
    assert runtime_status["ollama"]["available"] in {True, False}
    assert set(runtime_status["runtimeModes"]) >= {"api", "cli", "ollama", "hybrid", "manual"}
    assert "internal_mock" not in runtime_status["runtimeModes"]

    profile = client.post(
        "/api/v1/agent-profiles",
        headers=headers,
        json={
            "id": "hybrid-implementer",
            "name": "Hybrid Implementer",
            "role": "implementer",
            "runtimeMode": "hybrid",
            "modelPolicyId": "implementation_default",
            "permissionProfile": "dev_safe",
        },
    )
    assert profile.status_code == 201
    assert profile.json()["agentProfile"]["runtimeMode"] == "hybrid"
