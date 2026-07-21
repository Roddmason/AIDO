import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.agents_runtime import GatedAgentsPlanner
from local_control_center.app import create_app
from local_control_center.control_plane.overview import (
    OVERVIEW_AGENT_RUN_LIMIT,
    OVERVIEW_AGENT_TOOL_CALL_LIMIT,
    OVERVIEW_AUDIT_EVENT_LIMIT,
    OVERVIEW_COST_USAGE_LIMIT,
    OVERVIEW_EVENT_LIMIT,
    OVERVIEW_PERMISSION_DECISION_LIMIT,
)
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.memory_retrieval.index import RetrievalIndex
from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.worker import ConcurrentWorker
from tests_py.control_plane_fixture import ControlPlaneFixture


def test_jobs_have_atomic_leases_recovery_and_granular_action_approvals(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        jobs = JobsRepository(connection)
        project = ProjectsRepository(connection).create_project(
            name="Jobs",
            path=tmp_path / "project",
            template_id="other",
        )

        sensitive = jobs.create_job(
            project_id=project["id"],
            kind="pipeline.start",
            payload={"pipelineId": "pipeline-1"},
        )["job"]
        assert sensitive["status"] == "approval_required"
        approvals = jobs.list_action_requests(job_id=sensitive["id"])
        assert approvals[0]["status"] == "pending"

        jobs.approve_job(sensitive["id"], reason="job approved")
        assert jobs.get_job(sensitive["id"])["status"] == "approval_required"
        approved_action = jobs.approve_action(sensitive["id"], approvals[0]["id"], reason="command approved")
        assert approved_action["actionRequest"]["status"] == "approved"
        assert jobs.get_job(sensitive["id"])["status"] == "queued"

        queued = [
            jobs.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": str(index)})[
                "job"
            ]
            for index in range(6)
        ]

        def claim_once(worker_id: str):
            local_connection = open_sqlite_connection(db_path)
            try:
                claimed = JobsRepository(local_connection).claim_next_job(worker_id=worker_id, lease_ms=10)
                return claimed["job"]["id"] if claimed else None
            finally:
                local_connection.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed_ids = [
                job_id for job_id in pool.map(lambda i: claim_once(f"worker-{i}"), range(8)) if job_id
            ]

        assert len(claimed_ids) == len(set(claimed_ids))
        assert set(claimed_ids).issubset({job["id"] for job in queued} | {sensitive["id"]})

        recovered = jobs.requeue_expired_jobs(now_iso="2999-01-01T00:00:00.000Z")
        assert len(recovered) == len(claimed_ids)
        assert all(job["status"] == "queued" for job in jobs.list_jobs() if job["id"] in claimed_ids)


def test_schema_initialization_keeps_credentials_view_safe_for_concurrent_workers(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)

    barrier = threading.Barrier(8)

    def initialize_from_worker() -> str:
        barrier.wait(timeout=5)
        local_connection = open_sqlite_connection(db_path)
        try:
            initialize_platform_schema(local_connection)
            return local_connection.execute("SELECT credentialRef FROM credentials LIMIT 1").description[0][0]
        finally:
            local_connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        columns = list(pool.map(lambda _index: initialize_from_worker(), range(8)))

    migration_source = Path("local_control_center/shared/migrations.py").read_text(encoding="utf-8")
    phase30_source = migration_source.split("def init_phase30_schema", 1)[1].split(
        "def init_phase31_schema",
        1,
    )[0]

    assert columns == ["credentialRef"] * 8
    assert "DROP VIEW IF EXISTS credentials" not in phase30_source
    assert "CREATE VIEW IF NOT EXISTS credentials AS" in phase30_source


def test_fastapi_contracts_jobs_approvals_sse_and_retrieval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="API", path=tmp_path / "api", template_id="other")
    store.memory.create_memory_item(
        project_id=project["id"],
        scope="project",
        scope_id=project["id"],
        kind="note",
        content="Python FastAPI workers use SQLite leases and granular approvals",
        source_ref="test",
    )
    app = create_app(runtime=store, static_dir=None)
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
        json={"reason": "job approval"},
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
        json={"projectId": project["id"]},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert reindex.status_code == 202
    assert reindex.json()["index"]["status"] == "configuration_required"
    search = client.post(
        "/api/v1/retrieval/search",
        json={"projectId": project["id"], "query": "leases approvals", "limit": 3},
    )
    assert search.status_code == 200
    assert search.json()["status"] == "configuration_required"
    assert search.json()["results"] == []


def test_require_write_rejects_near_miss_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Token", path=tmp_path / "token", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    token = client.get("/api/v1/security/handshake").json()["token"]
    near_miss = token[:-1] + ("A" if token[-1] != "A" else "B")
    body = {"projectId": project["id"], "kind": "prompt.optimize"}

    denied = client.post(
        "/api/v1/jobs",
        json=body,
        headers={"X-Local-Control-Token": near_miss, "Origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 403

    accepted = client.post(
        "/api/v1/jobs",
        json=body,
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert accepted.status_code == 202


def test_static_routes_resolve_relative_static_dir_at_app_creation(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app-root"
    static_dir = app_root / "dist"
    other_cwd = tmp_path / "other"
    static_dir.mkdir(parents=True)
    other_cwd.mkdir()
    (static_dir / "index.html").write_text("<html><body>control shell</body></html>", encoding="utf-8")

    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()

    monkeypatch.chdir(app_root)
    app = create_app(runtime=store, static_dir=Path("dist"))
    monkeypatch.chdir(other_cwd)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "control shell" in response.text


def test_sse_snapshot_does_not_race_shared_store_connection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Concurrent", path=tmp_path / "concurrent", template_id="other")
    store.jobs.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "hello"})
    app = create_app(runtime=store, static_dir=None)
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
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="SSE", path=tmp_path / "sse", template_id="other")
    store.jobs.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "hello"})
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    def fail_if_shared_store_is_used():
        raise AssertionError("SSE endpoint must not snapshot through the shared request store")

    store.get_overview = fail_if_shared_store_is_used  # type: ignore[method-assign]

    response = client.get("/api/v1/events")

    assert response.status_code == 200
    assert "event: snapshot" in response.text
    assert "chat.route" in response.text


def test_overview_routes_do_not_use_store_read_model_facade(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Overview", path=tmp_path / "overview", template_id="other")
    store.jobs.create_job(project_id=project["id"], kind="chat.route", payload={"prompt": "hello"})
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    def fail_if_facade_is_used():
        raise AssertionError(
            "overview must be composed by control_plane repositories, not ControlPlaneFixture.get_overview"
        )

    store.get_overview = fail_if_facade_is_used  # type: ignore[method-assign]

    overview = client.get("/api/v1/overview")
    events = client.get("/api/v1/events")

    assert overview.status_code == 200
    assert any(job["id"] for job in overview.json()["jobs"])
    assert events.status_code == 200
    assert "event: snapshot" in events.text


def test_overview_returns_bounded_recent_event_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Overview Events",
        path=tmp_path / "overview-events",
        template_id="other",
    )
    for index in range(OVERVIEW_EVENT_LIMIT + 25):
        store.events.record_event(
            project_id=project["id"],
            event_type="telemetry.http.request",
            payload={"sequence": index},
        )
    for index in range(OVERVIEW_AUDIT_EVENT_LIMIT + 10):
        store.events.record_audit(
            project_id=project["id"],
            action="audit.test",
            target=f"target-{index}",
            payload={"sequence": index},
        )
    assert len(store.events.list_events(project_id=project["id"])) == OVERVIEW_EVENT_LIMIT + 25
    assert len(store.events.list_audit_events(project_id=project["id"])) >= OVERVIEW_AUDIT_EVENT_LIMIT + 10

    response = TestClient(create_app(runtime=store, static_dir=None)).get("/api/v1/overview")

    assert response.status_code == 200
    overview = response.json()
    assert len(overview["events"]) == OVERVIEW_EVENT_LIMIT
    assert len(overview["auditEvents"]) == OVERVIEW_AUDIT_EVENT_LIMIT
    event_sequences = [
        item["payload"].get("sequence") for item in overview["events"] if "sequence" in item["payload"]
    ]
    assert max(event_sequences) == OVERVIEW_EVENT_LIMIT + 24
    audit_sequences = [
        item["payload"].get("sequence") for item in overview["auditEvents"] if item["action"] == "audit.test"
    ]
    assert max(audit_sequences) == OVERVIEW_AUDIT_EVENT_LIMIT + 9


def test_overview_bounds_heavy_history_collections(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(
        name="Overview History",
        path=tmp_path / "overview-history",
        template_id="other",
    )
    for index in range(OVERVIEW_PERMISSION_DECISION_LIMIT + 25):
        store.security.record_decision(
            project_id=project["id"],
            workspace_id=None,
            agent_id=None,
            role=None,
            tool="shell",
            command=f"echo {index}",
            path=None,
            decision="allow",
            risk_level="low",
            reason=f"sequence-{index}",
            payload={"sequence": index},
        )
    profile = store.agents.upsert_agent_profile(
        {
            "id": "overview_history_agent",
            "name": "Overview History Agent",
            "role": "developer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    run = None
    for index in range(OVERVIEW_AGENT_RUN_LIMIT + 10):
        run = store.agents.create_agent_run(
            project_id=project["id"],
            agent_profile_id=profile["id"],
            task_id=f"overview-history-{index}",
            input_payload={},
            output_payload={},
        )
    assert run is not None
    for index in range(OVERVIEW_AGENT_TOOL_CALL_LIMIT + 15):
        store.agents.record_agent_tool_call(
            agent_run_id=run["id"],
            tool_name="shell",
            status="completed",
            payload={"sequence": index},
        )
    for _index in range(OVERVIEW_COST_USAGE_LIMIT + 10):
        store.agents.record_model_call(
            project_id=project["id"],
            provider="test-provider",
            model="test-model",
            status="completed",
            cost_usd=0.01,
        )

    assert len(store.security.list_decisions()) >= OVERVIEW_PERMISSION_DECISION_LIMIT + 25
    assert len(store.agents.list_agent_runs()) == OVERVIEW_AGENT_RUN_LIMIT + 10
    assert len(store.agents.list_agent_tool_calls()) == OVERVIEW_AGENT_TOOL_CALL_LIMIT + 15
    assert len(store.agents.list_cost_usage()) == OVERVIEW_COST_USAGE_LIMIT + 10

    response = TestClient(create_app(runtime=store, static_dir=None)).get("/api/v1/overview")

    assert response.status_code == 200
    overview = response.json()
    assert len(overview["permissionDecisions"]) == OVERVIEW_PERMISSION_DECISION_LIMIT
    assert len(overview["agentToolCalls"]) == OVERVIEW_AGENT_TOOL_CALL_LIMIT
    assert len(overview["agentRuns"]) == OVERVIEW_AGENT_RUN_LIMIT
    assert len(overview["costUsage"]) == OVERVIEW_COST_USAGE_LIMIT


def test_phase57_creates_created_at_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    expected_indexes = {
        "idx_agent_runs_created_at",
        "idx_agent_tool_calls_created_at",
        "idx_model_calls_created_at",
        "idx_cost_usage_created_at",
        "idx_events_type_created_at",
    }
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        names = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        assert expected_indexes <= names
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version = 57").fetchone()


def test_startup_lifespan_prunes_stale_http_request_telemetry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    store.connection.execute(
        "INSERT INTO events (id, job_id, project_id, type, payload, created_at)"
        " VALUES ('event-old-http', NULL, NULL, 'telemetry.http.request', '{}',"
        " '2020-01-01T00:00:00.000Z')"
    )
    store.connection.execute(
        "INSERT INTO events (id, job_id, project_id, type, payload, created_at)"
        " VALUES ('event-old-domain', NULL, NULL, 'job.created', '{}', '2020-01-01T00:00:00.000Z')"
    )

    with TestClient(create_app(runtime=store, static_dir=None)):
        pass

    survivors = {
        row["id"]
        for row in store.connection.execute(
            "SELECT id FROM events WHERE id IN ('event-old-http', 'event-old-domain')"
        ).fetchall()
    }
    assert survivors == {"event-old-domain"}


def test_api_requests_serialize_shared_store_access(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))

    class GuardedStore(ControlPlaneFixture):
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
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    def request(path: str) -> int:
        return client.get(path).status_code

    paths = [
        "/api/v1/overview",
        "/api/v1/legacy/sessions",
        "/api/v1/projects",
        "/api/v1/retrieval/status",
    ] * 4
    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(request, paths))

    assert statuses == [200] * len(paths)


def test_fastapi_can_bootstrap_with_control_center_runtime_without_store_facade(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    client = TestClient(app)

    health = client.get("/healthz")
    projects = client.get("/api/v1/projects")
    overview = client.get("/api/v1/overview")

    assert health.status_code == 200
    assert projects.status_code == 200
    assert any(project["path"] == str(tmp_path) for project in projects.json()["projects"])
    assert overview.status_code == 200
    runtime.close()


def test_fastapi_covers_platform_v1_catalog_routes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
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
        json={
            "projectId": created.json()["project"]["id"],
            "editor": "vscode",
            "workspaceRoot": str(tmp_path),
        },
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert ide.status_code == 201
    assert ide.json()["ideConnection"]["editor"] == "vscode"

    prompt = client.post(
        "/api/v1/prompts",
        json={
            "projectId": created.json()["project"]["id"],
            "name": "Implementation prompt",
            "body": "Implement with tests.",
            "mode": "manual",
            "appliesTo": {"role": "implementer"},
        },
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert prompt.status_code == 201
    assert prompt.json()["promptTemplate"]["version"] == 1

    revised = client.post(
        "/api/v1/prompts",
        json={
            "id": prompt.json()["promptTemplate"]["id"],
            "projectId": created.json()["project"]["id"],
            "name": "Implementation prompt",
            "body": "Implement with tests and evidence.",
            "mode": "manual",
            "appliesTo": {"role": "implementer"},
        },
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert revised.status_code == 201
    assert revised.json()["promptTemplate"]["version"] == 2

    listed_prompts = client.get("/api/v1/prompts")
    assert listed_prompts.status_code == 200
    assert any(
        item["id"] == prompt.json()["promptTemplate"]["id"]
        for item in listed_prompts.json()["promptTemplates"]
    )


def test_fastapi_covers_threads_and_legacy_read_only_cutover(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="V1", path=tmp_path / "v1", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]
    headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}

    removed_state_path = "/api/" + "state"
    assert client.get(removed_state_path).status_code == 404

    assert (
        client.post(
            "/api/v1/sessions",
            json={"projectId": project["id"], "name": "Python Session"},
            headers=headers,
        ).status_code
        == 404
    )
    assert client.get("/api/v1/legacy/sessions").status_code == 200

    thread_response = client.post(
        "/api/v1/threads",
        json={
            "projectId": project["id"],
            "ownerType": "workspace",
            "ownerId": project["id"],
            "title": "Python route thread",
        },
        headers=headers,
    )
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["thread"]["id"]

    message = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        json={"content": "Route this prompt"},
        headers=headers,
    )
    assert message.status_code == 200

    overview = client.get("/api/v1/overview").json()
    assert "sessions" not in overview
    assert "chats" not in overview
    assert "pipelines" not in overview
    assert any(item["id"] == thread_id for item in overview["threads"])


def test_retrieval_status_reports_configuration_required_without_real_embeddings(
    tmp_path: Path, monkeypatch
) -> None:
    from local_control_center.memory_retrieval import index as retrieval_index

    monkeypatch.setattr(retrieval_index, "faiss", None)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    status = TestClient(app).get("/api/v1/retrieval/status")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "configuration_required"
    assert body["available"] is False
    assert body["backend"] == "unavailable"
    assert body["reason"]


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


def test_validate_dashboard_host_accepts_loopback() -> None:
    from local_control_center import cli

    assert cli.validate_dashboard_host("127.0.0.1") == "127.0.0.1"
    assert cli.validate_dashboard_host("localhost") == "localhost"
    assert cli.validate_dashboard_host("::1") == "::1"


def test_validate_dashboard_host_rejects_wildcard() -> None:
    import pytest

    from local_control_center import cli

    with pytest.raises(SystemExit, match="loopback"):
        cli.validate_dashboard_host("0.0.0.0")
    with pytest.raises(SystemExit, match="loopback"):
        cli.validate_dashboard_host("192.168.1.10")


def test_validate_dashboard_host_env_override() -> None:
    from local_control_center import cli

    assert cli.validate_dashboard_host("0.0.0.0", allow_external=True) == "0.0.0.0"


def test_faiss_cpu_is_optional_not_required_for_python_environment() -> None:
    import tomllib

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    assert all(not dependency.startswith("faiss-cpu") for dependency in dependencies)
    assert any(dependency.startswith("faiss-cpu") for dependency in optional_dependencies["faiss"])


def test_worker_records_runs_events_and_rejects_unapproved_actions(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        jobs = JobsRepository(connection)
        project = ProjectsRepository(connection).create_project(
            name="Worker",
            path=tmp_path / "worker",
            template_id="other",
        )
        blocked = jobs.create_job(
            project_id=project["id"],
            kind="pipeline.start",
            payload={"pipelineId": "blocked"},
        )["job"]
        queued = jobs.create_job(
            project_id=project["id"],
            kind="prompt.optimize",
            payload={"prompt": "improve"},
        )["job"]

        worker = ConcurrentWorker(db_path=db_path)
        result = worker.run_once(worker_id="worker-a")

        assert result["job"]["id"] == queued["id"]
        assert result["job"]["status"] == "failed"
        assert jobs.get_job(blocked["id"])["status"] == "approval_required"
        run = jobs.list_job_runs(job_id=queued["id"])[-1]
        assert run["status"] == "failed"
        assert run["metadata"]["status"] == "configuration_required"
        assert "No real job executor" in run["summary"]
        assert any(event["type"] == "job.failed" for event in EventBus(connection).list_events())


def test_worker_fails_unknown_job_kind_instead_of_simulating_success(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        jobs = JobsRepository(connection)
        project = ProjectsRepository(connection).create_project(
            name="Unsupported Worker",
            path=tmp_path / "unsupported-worker",
            template_id="other",
        )
        queued = jobs.create_job(
            project_id=project["id"],
            kind="unknown.synthetic.kind",
            payload={"value": "must not complete"},
        )["job"]

        result = ConcurrentWorker(db_path=db_path).run_once(worker_id="worker-unsupported")
        run = jobs.list_job_runs(job_id=queued["id"])[-1]

    assert result["job"]["id"] == queued["id"]
    assert result["job"]["status"] == "failed"
    assert run["status"] == "failed"
    assert run["metadata"]["status"] == "unsupported_job_kind"
    assert "Unsupported job kind" in run["summary"]


def test_worker_executes_thread_product_loop_job_and_updates_thread(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    captured: dict[str, str] = {}

    def fake_run_user_message(self, **kwargs):
        captured["thread_id"] = kwargs["thread_id"]
        captured["project_id"] = kwargs["project_id"]
        return {
            "status": "awaiting_approval",
            "reason": "Evidence-backed Product Loop result awaits operator approval.",
            "loop": {"id": "product-loop-controlled", "state": "awaiting_approval"},
            "evidencePackage": {"id": "evidence-controlled"},
        }

    monkeypatch.setattr(
        "local_control_center.product_loop.coordinator.ProductLoopCoordinator.run_user_message",
        fake_run_user_message,
    )

    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Thread Worker",
            path=tmp_path / "thread-worker",
            template_id="other",
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Thread worker",
        )
        job = JobsRepository(connection).create_job(
            project_id=project["id"],
            kind="thread.product_loop.run",
            payload={
                "threadId": thread["id"],
                "message": "Implement the thread console.",
                "title": "Thread console",
                "root": str(tmp_path),
            },
        )["job"]

    result = ConcurrentWorker(db_path=db_path).run_once(worker_id="worker-thread")

    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        repo = ThreadsRepository(connection)
        refreshed = repo.get_thread(thread["id"])
        events = repo.list_events(thread["id"])
        messages = repo.list_messages(thread["id"])

    assert result["job"]["id"] == job["id"]
    assert result["job"]["status"] == "completed"
    assert captured == {"thread_id": thread["id"], "project_id": project["id"]}
    assert refreshed["status"] == "awaiting_approval"
    assert "worker_claimed" in [event["type"] for event in events]
    assert "approval_required" in [event["type"] for event in events]
    assert messages[-1]["kind"] == "aido_lead"


def test_worker_passes_plan_only_thread_job_and_marks_plan_ready(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    captured: dict[str, object] = {}

    def fake_run_user_message(self, **kwargs):
        captured["plan_only"] = kwargs["run_metadata"]["planOnly"]
        captured["thread_id"] = kwargs["thread_id"]
        return {
            "status": "plan_ready",
            "reason": "Product plan is ready without runtime execution.",
            "loop": {"id": "product-loop-plan-only", "state": "backlog_ready"},
            "evidencePackage": {"id": "evidence-plan-only"},
        }

    monkeypatch.setattr(
        "local_control_center.product_loop.coordinator.ProductLoopCoordinator.run_user_message",
        fake_run_user_message,
    )

    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Plan Worker",
            path=tmp_path / "plan-worker",
            template_id="other",
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-1",
            title="Plan worker",
        )
        job = JobsRepository(connection).create_job(
            project_id=project["id"],
            kind="thread.product_loop.run",
            payload={
                "threadId": thread["id"],
                "message": "Plan the thread console.",
                "title": "Plan thread console",
                "root": str(tmp_path),
                "planOnly": True,
            },
        )["job"]

    result = ConcurrentWorker(db_path=db_path).run_once(worker_id="worker-plan")

    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        repo = ThreadsRepository(connection)
        refreshed = repo.get_thread(thread["id"])
        events = repo.list_events(thread["id"])
        messages = repo.list_messages(thread["id"])

    assert result["job"]["id"] == job["id"]
    assert result["job"]["status"] == "completed"
    assert captured == {"plan_only": True, "thread_id": thread["id"]}
    assert refreshed["status"] == "open"
    assert "plan_ready" in [event["type"] for event in events]
    assert "blocked" not in [event["type"] for event in events]
    assert messages[-1]["kind"] == "aido_lead"


def test_retrieval_index_uses_persisted_real_embeddings_per_project_and_is_rebuildable(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Retrieval",
            path=tmp_path / "retrieval",
            template_id="other",
        )
        other_project = ProjectsRepository(connection).create_project(
            name="Other Retrieval",
            path=tmp_path / "other-retrieval",
            template_id="other",
        )
        memory = MemoryRepository(connection)
        item = memory.create_memory_item(
            project_id=project["id"],
            scope="project",
            scope_id=project["id"],
            kind="note",
            content="FAISS retrieval should be rebuildable from SQLite memory metadata",
            source_ref="test",
        )
        other_item = memory.create_memory_item(
            project_id=other_project["id"],
            scope="project",
            scope_id=other_project["id"],
            kind="note",
            content="Other project memory must not leak into this project search",
            source_ref="test",
        )
        expired_item = memory.create_memory_item(
            project_id=project["id"],
            scope="project",
            scope_id=project["id"],
            kind="note",
            content="Expired memory must not be indexed",
            source_ref="test",
            expires_at="2000-01-01T00:00:00.000Z",
        )
        deleted_item = memory.create_memory_item(
            project_id=project["id"],
            scope="project",
            scope_id=project["id"],
            kind="note",
            content="Deleted memory must not be indexed",
            source_ref="test",
        )
        memory.delete_memory_item(deleted_item["id"], reason="unit test retention policy")

        memory.upsert_memory_embedding(
            memory_item_id=item["id"],
            provider="openai_api",
            model="text-embedding-3-small",
            embedding=[1.0, 0.0, 0.0],
        )
        memory.upsert_memory_embedding(
            memory_item_id=other_item["id"],
            provider="openai_api",
            model="text-embedding-3-small",
            embedding=[1.0, 0.0, 0.0],
        )
        memory.upsert_memory_embedding(
            memory_item_id=expired_item["id"],
            provider="openai_api",
            model="text-embedding-3-small",
            embedding=[1.0, 0.0, 0.0],
        )
        memory.upsert_memory_embedding(
            memory_item_id=deleted_item["id"],
            provider="openai_api",
            model="text-embedding-3-small",
            embedding=[1.0, 0.0, 0.0],
        )

        index = RetrievalIndex(memory=memory, index_dir=tmp_path / "index")
        summary = index.rebuild(project_id=project["id"])
        assert summary["status"] == "available"
        assert summary["indexed"] == 1
        assert summary["ids"] == [item["id"]]
        results = index.search_embedding(project_id=project["id"], embedding=[1.0, 0.0, 0.0], limit=5)
        assert results[0]["memoryItem"]["id"] == item["id"]
        assert all(result["memoryItem"]["projectId"] == project["id"] for result in results)
        assert index.manifest_path(project["id"]).exists()

        visible_items = memory.list_memory_items(project_id=project["id"])
        assert [visible["id"] for visible in visible_items] == [item["id"]]
        all_items = memory.list_memory_items(project_id=project["id"], include_inactive=True)
        assert {inactive["id"] for inactive in all_items} == {
            item["id"],
            expired_item["id"],
            deleted_item["id"],
        }


def test_retrieval_reindex_without_persisted_real_embeddings_is_configuration_required(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="No Embeddings",
            path=tmp_path / "no-embeddings",
            template_id="other",
        )
        memory = MemoryRepository(connection)
        memory.create_memory_item(
            project_id=project["id"],
            scope="project",
            scope_id=project["id"],
            kind="note",
            content="This must not receive a synthetic local embedding",
            source_ref="test",
        )

        index = RetrievalIndex(memory=memory, index_dir=tmp_path / "index")
        summary = index.rebuild(project_id=project["id"])

    assert summary["status"] == "configuration_required"
    assert summary["indexed"] == 0
    assert summary["ids"] == []


def test_retrieval_reindex_ignores_non_api_embedding_providers(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Manual Embeddings",
            path=tmp_path / "manual-embeddings",
            template_id="other",
        )
        memory = MemoryRepository(connection)
        item = memory.create_memory_item(
            project_id=project["id"],
            scope="project",
            scope_id=project["id"],
            kind="note",
            content="Manual operator metadata is not a real embedding provider.",
            source_ref="test",
        )
        memory.upsert_memory_embedding(
            memory_item_id=item["id"],
            provider="manual",
            model="manual_selection",
            embedding=[1.0, 0.0, 0.0],
        )

        index = RetrievalIndex(memory=memory, index_dir=tmp_path / "index")
        summary = index.rebuild(project_id=project["id"])

    assert summary["status"] == "configuration_required"
    assert summary["indexed"] == 0
    assert summary["ids"] == []


def test_sqlite_schema_contains_python_control_plane_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    assert {"jobs", "job_runs", "events", "audit_events", "action_requests", "memory_embeddings"} <= tables


def test_agents_planner_is_gated_when_sdk_or_key_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Agents", path=tmp_path / "agents", template_id="other"
        )
        jobs = JobsRepository(connection)
        job = jobs.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": "plan"})[
            "job"
        ]

        planner = GatedAgentsPlanner(jobs=jobs)
        result = planner.propose_action(project_id=project["id"], job_id=job["id"], prompt="plan")

        assert result.enabled is False
        assert "disabled" in result.summary


def test_agents_planner_records_proposals_through_jobs_repository(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Agents Proposal",
            path=tmp_path / "agents-proposal",
            template_id="other",
        )
        jobs = JobsRepository(connection)
        job = jobs.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": "plan"})[
            "job"
        ]

        planner = GatedAgentsPlanner(jobs=jobs)
        monkeypatch.setattr(planner, "available", lambda: True)

        result = planner.propose_action(project_id=project["id"], job_id=job["id"], prompt="plan next step")

        assert result.enabled is True
        assert result.action_request_id
        action = jobs.get_action_request(result.action_request_id)
        assert action["actionType"] == "agent.proposed_action"
        assert action["status"] == "pending"
