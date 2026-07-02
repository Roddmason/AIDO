from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.shared.migrations import initialize_platform_schema
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


def test_legacy_sessions_chats_pipelines_are_read_only_and_migrated_to_threads(
    tmp_path: Path,
) -> None:
    store, client, headers = make_client(tmp_path)
    project_id = client.get("/api/v1/projects").json()["projects"][0]["id"]

    session = store.sessions_chats.create_session(project_id=project_id, name="Cutover session")
    chat = store.sessions_chats.create_chat(
        project_id=project_id,
        session_id=session["id"],
        prompt="Plan the hard cutover",
        title="Cutover chat",
    )
    pipeline = store.pipelines.create_pipeline(
        project_id=project_id,
        session_id=session["id"],
        chat_id=chat["id"],
        title="Cutover pipeline",
    )
    initialize_platform_schema(store.connection)

    assert client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"projectId": project_id, "name": "blocked"},
    ).status_code == 404
    assert client.post(
        "/api/v1/chats",
        headers=headers,
        json={"projectId": project_id, "prompt": "blocked"},
    ).status_code == 404
    assert client.post(
        "/api/v1/pipelines",
        headers=headers,
        json={"projectId": project_id, "title": "blocked"},
    ).status_code == 404
    assert client.post(
        "/api/v1/legacy/sessions",
        headers=headers,
        json={"projectId": project_id, "name": "blocked"},
    ).status_code == 405

    overview = client.get("/api/v1/overview").json()
    removed_workspace_key = "workspace" + "State"
    assert removed_workspace_key not in overview
    assert "sessions" not in overview
    assert "chats" not in overview
    assert "pipelines" not in overview

    legacy_sessions = client.get("/api/v1/legacy/sessions").json()["sessions"]
    legacy_chats = client.get("/api/v1/legacy/chats").json()["chats"]
    legacy_pipelines = client.get("/api/v1/legacy/pipelines").json()["pipelines"]
    assert next(item for item in legacy_sessions if item["id"] == session["id"])["metadata"][
        "legacyReadOnly"
    ]
    assert next(item for item in legacy_chats if item["id"] == chat["id"])["metadata"]["legacy"][
        "migratedToThreadId"
    ].startswith("thread-legacy-")
    assert next(item for item in legacy_pipelines if item["id"] == pipeline["id"])["metadata"][
        "legacy"
    ]["migratedToMessageId"].startswith("thread-msg-legacy-")

    threads = client.get(f"/api/v1/threads?projectId={project_id}").json()["threads"]
    migrated_thread = next(item for item in threads if item["id"] == f"thread-legacy-{session['id']}")
    detail = client.get(f"/api/v1/threads/{migrated_thread['id']}").json()
    assert [message["content"] for message in detail["messages"]] == [
        "Plan the hard cutover",
        "Legacy pipeline imported: Cutover pipeline (queued)",
    ]
    removed_state_route = "/api/" + "state"
    assert client.get(removed_state_route).status_code == 404


def test_new_thread_messages_do_not_write_sessions_chats_or_pipelines(tmp_path: Path) -> None:
    store, client, headers = make_client(tmp_path)
    project_id = client.get("/api/v1/projects").json()["projects"][0]["id"]

    created = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project_id,
            "ownerType": "workspace",
            "ownerId": project_id,
            "title": "No legacy writes",
        },
    )
    assert created.status_code == 201
    thread_id = created.json()["thread"]["id"]

    posted = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"content": "Use thread messages only."},
    )
    assert posted.status_code == 200
    assert store.connection.execute("SELECT COUNT(*) AS total FROM sessions").fetchone()["total"] == 0
    assert store.connection.execute("SELECT COUNT(*) AS total FROM chats").fetchone()["total"] == 0
    assert store.connection.execute("SELECT COUNT(*) AS total FROM pipelines").fetchone()["total"] == 0
    assert (
        store.connection.execute("SELECT COUNT(*) AS total FROM thread_messages").fetchone()["total"]
        >= 1
    )


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
    assert {"/api/v1/legacy/sessions", "/api/v1/legacy/chats", "/api/v1/legacy/pipelines"} <= mounted_paths
    non_cutover_legacy = {
        path
        for path in mounted_paths
        if ("legacy" in path.lower() or "compat" in path.lower())
        and path
        not in {"/api/v1/legacy/sessions", "/api/v1/legacy/chats", "/api/v1/legacy/pipelines"}
    }
    assert non_cutover_legacy == set()


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
