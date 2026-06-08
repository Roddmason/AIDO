import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.agents_runtime import GatedAgentsPlanner
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.memory_retrieval.index import RetrievalIndex
from local_control_center.memory_retrieval.repository import MemoryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.sandbox import WindowsSandbox
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema
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
            claimed_ids = [job_id for job_id in pool.map(lambda i: claim_once(f"worker-{i}"), range(8)) if job_id]

        assert len(claimed_ids) == len(set(claimed_ids))
        assert set(claimed_ids).issubset({job["id"] for job in queued} | {sensitive["id"]})

        recovered = jobs.requeue_expired_jobs(now_iso="2999-01-01T00:00:00.000Z")
        assert len(recovered) == len(claimed_ids)
        assert all(job["status"] == "queued" for job in jobs.list_jobs() if job["id"] in claimed_ids)


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
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )
    assert reindex.status_code == 202
    search = client.post("/api/v1/retrieval/search", json={"query": "leases approvals", "limit": 3})
    assert search.status_code == 200
    assert search.json()["results"][0]["memoryItem"]["content"].startswith("Python FastAPI workers")


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
        raise AssertionError("overview must be composed by control_plane repositories, not ControlPlaneFixture.get_overview")

    store.get_overview = fail_if_facade_is_used  # type: ignore[method-assign]

    overview = client.get("/api/v1/overview")
    events = client.get("/api/v1/events")

    assert overview.status_code == 200
    assert any(job["id"] for job in overview.json()["jobs"])
    assert events.status_code == 200
    assert "event: snapshot" in events.text


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

    paths = ["/api/v1/overview", "/api/v1/sessions", "/api/v1/projects", "/api/v1/retrieval/status"] * 4
    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(request, paths))

    assert statuses == [200] * len(paths)


def test_fastapi_can_bootstrap_with_control_center_runtime_without_store_facade(tmp_path: Path, monkeypatch) -> None:
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
        json={"projectId": created.json()["project"]["id"], "editor": "vscode", "workspaceRoot": str(tmp_path)},
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
    assert any(item["id"] == prompt.json()["promptTemplate"]["id"] for item in listed_prompts.json()["promptTemplates"])


def test_fastapi_covers_v1_sessions_chats_and_pipelines(tmp_path: Path, monkeypatch) -> None:
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

    session_response = client.post("/api/v1/sessions", json={"projectId": project["id"], "name": "Python Session"}, headers=headers)
    assert session_response.status_code == 201
    session = session_response.json()["session"]
    assert session["name"] == "Python Session"

    chat = client.post(
        "/api/v1/chats",
        json={"projectId": project["id"], "sessionId": session["id"], "prompt": "Route this prompt"},
        headers=headers,
    )
    assert chat.status_code == 201
    chat_id = chat.json()["chat"]["id"]

    pipeline = client.post(
        "/api/v1/pipelines",
        json={"projectId": project["id"], "sessionId": session["id"], "chatId": chat_id, "title": "Python route"},
        headers=headers,
    )
    assert pipeline.status_code == 201

    overview = client.get("/api/v1/overview").json()
    assert any(item["id"] == session["id"] for item in overview["sessions"])
    assert any(item["id"] == chat_id for item in overview["chats"])
    assert any(item["id"] == pipeline.json()["pipeline"]["id"] for item in overview["pipelines"])


def test_retrieval_status_reports_faiss_or_explicit_degraded_fallback(tmp_path: Path, monkeypatch) -> None:
    from local_control_center.memory_retrieval import index as retrieval_index

    monkeypatch.setattr(retrieval_index, "faiss", None)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
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


def test_retrieval_index_uses_sqlite_metadata_and_is_rebuildable(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Retrieval",
            path=tmp_path / "retrieval",
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

        index = RetrievalIndex(memory=memory, index_dir=tmp_path / "index")
        summary = index.rebuild()
        assert summary["indexed"] == 1
        results = index.search("rebuildable memory metadata", limit=1)
        assert results[0]["memoryItem"]["id"] == item["id"]
        assert (tmp_path / "index" / "manifest.json").exists()


def test_sqlite_schema_contains_python_control_plane_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(name="Agents", path=tmp_path / "agents", template_id="other")
        jobs = JobsRepository(connection)
        job = jobs.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": "plan"})["job"]

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
        job = jobs.create_job(project_id=project["id"], kind="prompt.optimize", payload={"prompt": "plan"})["job"]

        planner = GatedAgentsPlanner(jobs=jobs)
        monkeypatch.setattr(planner, "available", lambda: True)

        result = planner.propose_action(project_id=project["id"], job_id=job["id"], prompt="plan next step")

        assert result.enabled is True
        assert result.action_request_id
        action = jobs.get_action_request(result.action_request_id)
        assert action["actionType"] == "agent.proposed_action"
        assert action["status"] == "pending"
