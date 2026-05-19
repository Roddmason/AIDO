import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.agents_runtime import GatedAgentsPlanner
from local_control_center.retrieval import RetrievalIndex
from local_control_center.sandbox import WindowsSandbox
from local_control_center.store import PlatformStore
from local_control_center.worker import ConcurrentWorker


def write_legacy_workspace(cwd: Path) -> None:
    claude_dir = cwd / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "team-workspace.json").write_text(
        json.dumps(
            {
                "version": 3,
                "activeTeamId": "team-1",
                "activeSessionId": "session-1",
                "activeChatId": "chat-1",
                "activePipelineId": "pipeline-1",
                "teams": [
                    {
                        "id": "team-1",
                        "name": "Python Migration Team",
                        "workspaceScope": {"cwd": str(cwd), "isGitRepo": False},
                        "members": [
                            {
                                "id": "agent-1",
                                "name": "Builder",
                                "role": "Implementation",
                                "kind": "agent",
                                "capabilities": ["code"],
                                "runtimePreferences": [
                                    {"provider": "codex", "model": "gpt-5", "priority": 0}
                                ],
                                "permissions": {"canEditWorkspace": True},
                            }
                        ],
                    }
                ],
                "sessions": [
                    {
                        "id": "session-1",
                        "name": "Main",
                        "teamId": "team-1",
                        "activeChatId": "chat-1",
                        "activePipelineId": "pipeline-1",
                    }
                ],
                "chats": [
                    {
                        "id": "chat-1",
                        "teamId": "team-1",
                        "sessionId": "session-1",
                        "title": "Python backend",
                        "runs": [{"id": "run-1", "stage": "qa", "output": "RESULT: OK"}],
                    }
                ],
                "pipelines": [
                    {
                        "id": "pipeline-1",
                        "sessionId": "session-1",
                        "teamId": "team-1",
                        "chatId": "chat-1",
                        "status": "blocked",
                        "modules": [
                            {
                                "id": "module-1",
                                "name": "Core",
                                "stages": {
                                    "analyze": {
                                        "id": "stage-1",
                                        "status": "completed",
                                        "gate": "ok",
                                    }
                                },
                                "corrections": [
                                    {
                                        "id": "correction-1",
                                        "stages": {
                                            "analyze": {
                                                "id": "c-stage-1",
                                                "status": "completed",
                                            }
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "memoryByTeamId": {
                    "team-1": {
                        "summary": "SQLite remains canonical after Python migration",
                        "notes": ["Preserve active pointers"],
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_imports_legacy_workspace_and_keeps_sqlite_canonical(tmp_path: Path) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    write_legacy_workspace(cwd)
    db_path = tmp_path / "platform.sqlite"

    store = PlatformStore(cwd=cwd, db_path=db_path)
    store.init()
    store.import_legacy_workspace(cwd)

    assert db_path.read_bytes()[:16] == b"SQLite format 3\0"
    imported = store.load_workspace_state(cwd)
    assert imported["state"]["activeSessionId"] == "session-1"
    assert imported["state"]["activeChatId"] == "chat-1"
    assert imported["state"]["activePipelineId"] == "pipeline-1"
    assert imported["state"]["pipelines"][0]["modules"][0]["corrections"][0]["id"] == "correction-1"
    assert store.list_memory_items()[0]["content"] == "SQLite remains canonical after Python migration"

    legacy_path = cwd / ".claude" / "team-workspace.json"
    legacy_path.write_text(json.dumps({"activeChatId": "json-should-not-win"}), encoding="utf-8")
    state = imported["state"] | {"activeChatId": "chat-2"}
    store.save_workspace_state(cwd, state, source="test")

    reloaded = store.load_workspace_state(cwd)
    assert reloaded["state"]["activeChatId"] == "chat-2"


def test_jobs_have_atomic_leases_recovery_and_granular_action_approvals(tmp_path: Path) -> None:
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Jobs", path=tmp_path / "project", template_id="other")

    sensitive = store.create_job(
        project_id=project["id"],
        kind="pipeline.start",
        payload={"pipelineId": "pipeline-1"},
    )["job"]
    assert sensitive["status"] == "approval_required"
    approvals = store.list_action_requests(job_id=sensitive["id"])
    assert approvals[0]["status"] == "pending"

    store.approve_job(sensitive["id"], reason="job approved")
    assert store.get_job(sensitive["id"])["status"] == "approval_required"
    approved_action = store.approve_action(sensitive["id"], approvals[0]["id"], reason="command approved")
    assert approved_action["actionRequest"]["status"] == "approved"
    assert store.get_job(sensitive["id"])["status"] == "queued"

    queued = [
        store.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": str(index)})[
            "job"
        ]
        for index in range(6)
    ]

    def claim_once(worker_id: str):
        local_store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
        local_store.init()
        claimed = local_store.claim_next_job(worker_id=worker_id, lease_ms=10)
        local_store.close()
        return claimed["job"]["id"] if claimed else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed_ids = [job_id for job_id in pool.map(lambda i: claim_once(f"worker-{i}"), range(8)) if job_id]

    assert len(claimed_ids) == len(set(claimed_ids))
    assert set(claimed_ids).issubset({job["id"] for job in queued} | {sensitive["id"]})

    recovered = store.requeue_expired_jobs(now_iso="2999-01-01T00:00:00.000Z")
    assert len(recovered) == len(claimed_ids)
    assert all(job["status"] == "queued" for job in store.list_jobs() if job["id"] in claimed_ids)


def test_fastapi_contracts_jobs_approvals_sse_and_retrieval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="API", path=tmp_path / "api", template_id="other")
    store.create_memory_item(
        project_id=project["id"],
        scope="project",
        scope_id=project["id"],
        kind="note",
        content="Python FastAPI workers use SQLite leases and granular approvals",
        source_ref="test",
    )
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)

    overview_response = client.get("/api/v1/overview")
    assert overview_response.status_code == 200
    assert overview_response.json()["security"]["loopbackOnly"] is True

    denied = client.post("/api/v1/jobs", json={"projectId": project["id"], "kind": "prompt.optimize"})
    assert denied.status_code == 403

    token = client.get("/api/v1/security/handshake").json()["token"]
    created = client.post(
        "/api/v1/jobs",
        json={"projectId": project["id"], "kind": "pipeline.start", "payload": {"pipelineId": "p1"}},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["job"]["status"] == "approval_required"

    approvals = client.get("/api/v1/approvals").json()["actionRequests"]
    assert approvals[0]["jobId"] == body["job"]["id"]

    job_approve = client.post(
        f"/api/v1/jobs/{body['job']['id']}/approve",
        json={"reason": "legacy approval"},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert job_approve.json()["job"]["status"] == "approval_required"

    action_approve = client.post(
        f"/api/v1/jobs/{body['job']['id']}/actions/{approvals[0]['id']}/approve",
        json={"reason": "specific action approval"},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert action_approve.status_code == 202
    assert action_approve.json()["job"]["status"] == "queued"

    events = client.get("/api/v1/events")
    assert events.status_code == 200
    assert "event: snapshot" in events.text

    reindex = client.post(
        "/api/v1/retrieval/reindex",
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert reindex.status_code == 202
    search = client.post("/api/v1/retrieval/search", json={"query": "leases approvals", "limit": 3})
    assert search.status_code == 200
    assert search.json()["results"][0]["memoryItem"]["content"].startswith("Python FastAPI workers")


def test_sse_snapshot_does_not_race_shared_store_connection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Concurrent", path=tmp_path / "concurrent", template_id="other")
    store.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "hello"})
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)

    def request(path: str) -> tuple[int, str]:
        response = client.get(path)
        return response.status_code, response.text

    paths = ["/api/v1/overview", "/api/v1/events"] * 8
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(request, paths))

    assert all(status == 200 for status, _ in responses)
    assert any("event: snapshot" in body for _, body in responses)


def test_sse_snapshot_uses_isolated_store_connection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="SSE", path=tmp_path / "sse", template_id="other")
    store.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "hello"})
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)

    def fail_if_shared_store_is_used():
        raise AssertionError("SSE endpoint must not snapshot through the shared request store")

    store.get_overview = fail_if_shared_store_is_used  # type: ignore[method-assign]

    response = client.get("/api/v1/events")

    assert response.status_code == 200
    assert "event: snapshot" in response.text
    assert "chat.route" in response.text


def test_api_requests_serialize_shared_store_access(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))

    class GuardedStore(PlatformStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._active_store_call = 0
            self._active_store_lock = threading.Lock()

        def ensure_runtime_project(self):
            with self._active_store_lock:
                if self._active_store_call:
                    raise AssertionError("shared store accessed concurrently")
                self._active_store_call += 1
            try:
                time.sleep(0.02)
                return super().ensure_runtime_project()
            finally:
                with self._active_store_lock:
                    self._active_store_call -= 1

    store = GuardedStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)

    def request(path: str) -> int:
        return client.get(path).status_code

    paths = ["/api/v1/overview", "/api/state", "/api/v1/projects", "/api/v1/retrieval/status"] * 4
    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(request, paths))

    assert statuses == [200] * len(paths)


def test_fastapi_covers_platform_v1_catalog_routes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]

    routes = {
        "/api/v1/project-templates": "projectTemplates",
        "/api/v1/projects": "projects",
        "/api/v1/providers": "providers",
        "/api/v1/teams": "teams",
        "/api/v1/agents": "agents",
        "/api/v1/open-design": "status",
    }
    for path, expected_key in routes.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert expected_key in response.json(), path

    created = client.post(
        "/api/v1/projects",
        json={"name": "Created", "path": str(tmp_path / "created"), "templateId": "python-fastapi"},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert created.status_code == 201
    assert created.json()["project"]["templateId"] == "python-fastapi"

    ide = client.post(
        "/api/v1/ide-connections",
        json={"projectId": created.json()["project"]["id"], "editor": "vscode", "workspaceRoot": str(tmp_path)},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert ide.status_code == 201
    assert ide.json()["ideConnection"]["editor"] == "vscode"


def test_fastapi_covers_legacy_dashboard_routes_with_real_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Legacy", path=tmp_path / "legacy", template_id="other")
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]
    headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}

    state = client.get("/api/state").json()
    assert "state" in state
    assert state["dashboard"]["backend"] == "python"

    assert client.post("/api/sessions", json={"name": "Rejected"}).status_code == 403

    workspace_select = client.post("/api/workspaces/select", json={"path": project["path"]}, headers=headers)
    assert workspace_select.status_code == 202
    assert workspace_select.json()["state"]["activeWorkspacePath"] == project["path"]

    for action in ("collapse", "expand", "pin", "unpin"):
        response = client.post(f"/api/workspaces/{project['id']}/{action}", json={}, headers=headers)
        assert response.status_code == 200
        assert "snapshot" in response.json()

    session_response = client.post("/api/sessions", json={"name": "Python Session"}, headers=headers)
    assert session_response.status_code == 201
    session = session_response.json()["activeSession"]
    assert session["name"] == "Python Session"

    assert client.post("/api/sessions/select", json={"sessionId": session["id"]}, headers=headers).status_code == 200
    assert client.post(f"/api/sessions/{session['id']}/pin", json={}, headers=headers).status_code == 200
    assert client.post(f"/api/sessions/{session['id']}/unpin", json={}, headers=headers).status_code == 200
    patched = client.patch(f"/api/sessions/{session['id']}", json={"name": "Renamed"}, headers=headers)
    assert patched.status_code == 200
    assert patched.json()["activeSession"]["name"] == "Renamed"
    cloned = client.post(f"/api/sessions/{session['id']}/clone", json={"name": "Clone"}, headers=headers)
    assert cloned.status_code == 201
    assert cloned.json()["activeSession"]["name"] == "Clone"

    config = client.patch("/api/config", json={"pipelinePolicy": {"mode": "manual"}}, headers=headers)
    assert config.status_code == 202
    assert config.json()["state"]["configCatalog"]["pipelinePolicy"]["mode"] == "manual"

    chat = client.post("/api/chats/send", json={"prompt": "Route this prompt", "mode": "auto"}, headers=headers)
    assert chat.status_code == 202
    chat_id = chat.json()["activeChat"]["id"]
    assert client.get(f"/api/chats/{chat_id}").status_code == 200

    intake = client.post("/api/idea/intake", json={"idea": "Build a Python parity route"}, headers=headers)
    assert intake.status_code == 202
    pipeline_id = intake.json()["activePipeline"]["id"]
    assert client.get(f"/api/pipelines/{pipeline_id}").status_code == 200

    for suffix in ("start", "retry", "archive"):
        assert client.post(f"/api/pipelines/{pipeline_id}/{suffix}", json={}, headers=headers).status_code == 202
    for suffix in ("stages/retry", "stages/assign", "stages/override"):
        assert client.post(f"/api/pipelines/{pipeline_id}/{suffix}", json={"stageName": "analyze"}, headers=headers).status_code == 202

    assert client.post("/api/extensions/marketplaces", json={"target": "claude", "source": "local"}, headers=headers).status_code == 202
    assert client.post("/api/extensions/plugins/install", json={"pluginRef": "local/plugin"}, headers=headers).status_code == 202
    assert client.post("/api/extensions/plugins/sync-skills", json={"pluginKey": "local"}, headers=headers).status_code == 202
    assert client.post("/api/extensions/skills/install", json={"sourcePath": str(tmp_path)}, headers=headers).status_code == 202
    assert client.post("/api/git/checkout", json={"branch": "feature/test"}, headers=headers).status_code == 202
    assert client.delete(f"/api/sessions/{session['id']}", headers=headers).status_code == 200


def test_retrieval_status_reports_faiss_or_explicit_degraded_fallback(tmp_path: Path, monkeypatch) -> None:
    from local_control_center.memory_retrieval import index as retrieval_index

    monkeypatch.setattr(retrieval_index, "faiss", None)
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(store=store, static_dir=None)
    status = TestClient(app).get("/api/v1/retrieval/status")
    assert status.status_code == 200
    body = status.json()
    assert body["backend"] == "numpy"
    assert body["degraded"] is (body["backend"] != "faiss")


def test_package_manager_is_pnpm_only() -> None:
    package_json = json.loads(Path("package.json").read_text(encoding="utf-8"))
    assert package_json["packageManager"].startswith("pnpm@")
    scripts = package_json["scripts"]
    assert all(not command.strip().startswith("npm ") for command in scripts.values())
    start_script = Path("local-control-center/scripts/start-control-center.ps1").read_text(encoding="utf-8")
    assert re.search(r"(?<!p)npm\s+run", start_script) is None


def test_cli_configures_windows_selector_event_loop_policy() -> None:
    from local_control_center import cli

    assert cli.configure_windows_event_loop_policy(platform_name="nt") is True
    assert Path("pnpm-lock.yaml").exists()
    assert not Path("package-lock.json").exists()


def test_faiss_cpu_is_optional_not_required_for_python_environment() -> None:
    import tomllib

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    assert all(not dependency.startswith("faiss-cpu") for dependency in dependencies)
    assert any(dependency.startswith("faiss-cpu") for dependency in optional_dependencies["faiss"])


def test_worker_records_runs_events_and_rejects_unapproved_actions(tmp_path: Path) -> None:
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Worker", path=tmp_path / "worker", template_id="other")
    blocked = store.create_job(
        project_id=project["id"],
        kind="pipeline.start",
        payload={"pipelineId": "blocked"},
    )["job"]
    queued = store.create_job(
        project_id=project["id"],
        kind="prompt.optimize",
        payload={"prompt": "improve"},
    )["job"]

    worker = ConcurrentWorker(store_factory=lambda: PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
    result = worker.run_once(worker_id="worker-a")

    assert result["job"]["id"] == queued["id"]
    assert result["job"]["status"] == "completed"
    assert store.get_job(blocked["id"])["status"] == "approval_required"
    assert store.list_job_runs(job_id=queued["id"])[-1]["status"] == "completed"
    assert any(event["type"] == "job.completed" for event in store.list_events())


def test_retrieval_index_uses_sqlite_metadata_and_is_rebuildable(tmp_path: Path) -> None:
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Retrieval", path=tmp_path / "retrieval", template_id="other")
    item = store.create_memory_item(
        project_id=project["id"],
        scope="project",
        scope_id=project["id"],
        kind="note",
        content="FAISS retrieval should be rebuildable from SQLite memory metadata",
        source_ref="test",
    )

    index = RetrievalIndex(store=store, index_dir=tmp_path / "index")
    summary = index.rebuild()
    assert summary["indexed"] == 1
    results = index.search("rebuildable memory metadata", limit=1)
    assert results[0]["memoryItem"]["id"] == item["id"]
    assert (tmp_path / "index" / "manifest.json").exists()


def test_sqlite_schema_contains_python_control_plane_tables(tmp_path: Path) -> None:
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    with sqlite3.connect(tmp_path / "platform.sqlite") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    assert {"jobs", "job_runs", "events", "audit_events", "action_requests", "memory_embeddings"} <= tables


def test_sandbox_denies_dangerous_subprocess_without_docker(tmp_path: Path, monkeypatch) -> None:
    sandbox = WindowsSandbox(workspace=tmp_path)
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)

    denied = sandbox.assess(["git", "push"])
    allowed = sandbox.assess(["python", "--version"])

    assert denied.allowed is False
    assert denied.mode == "restricted-subprocess"
    assert allowed.allowed is True


def test_agents_planner_is_gated_when_sdk_or_key_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Agents", path=tmp_path / "agents", template_id="other")
    job = store.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": "plan"})["job"]

    planner = GatedAgentsPlanner(store=store)
    result = planner.propose_action(project_id=project["id"], job_id=job["id"], prompt="plan")

    assert result.enabled is False
    assert "disabled" in result.summary
