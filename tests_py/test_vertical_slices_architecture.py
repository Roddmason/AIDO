from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_jobs_approvals_slice_owns_http_worker_and_sql() -> None:
    jobs_api = ROOT / "local_control_center" / "jobs_approvals" / "api.py"
    jobs_repository = ROOT / "local_control_center" / "jobs_approvals" / "repository.py"
    jobs_worker = ROOT / "local_control_center" / "jobs_approvals" / "worker.py"

    assert jobs_api.exists()
    assert jobs_repository.exists()
    assert jobs_worker.exists()

    root_api = read("local_control_center/api.py")
    assert '@app.get("/api/v1/jobs")' not in root_api
    assert '@app.post("/api/v1/jobs"' not in root_api
    assert '@app.get("/api/v1/approvals")' not in root_api
    assert '"/api/v1/jobs/{job_id}/actions/{action_id}/approve"' not in root_api

    api_source = jobs_api.read_text(encoding="utf-8")
    assert "SELECT " not in api_source
    assert "INSERT " not in api_source
    assert "UPDATE " not in api_source
    assert "DELETE " not in api_source

    store_source = read("local_control_center/store.py")
    operational_jobs_sql = [
        "INSERT INTO jobs",
        "SELECT * FROM jobs",
        "UPDATE jobs",
        "INSERT INTO job_runs",
        "UPDATE job_runs",
        "INSERT INTO action_requests",
        "UPDATE action_requests",
        "SELECT * FROM action_requests",
        "INSERT INTO events",
        "INSERT INTO audit_events",
    ]
    for sql in operational_jobs_sql:
        assert sql not in store_source

    root_worker = read("local_control_center/worker.py")
    assert "from .jobs_approvals.worker import" in root_worker


def test_active_python_backend_does_not_import_legacy_node_backend() -> None:
    active_paths = [
        ROOT / "local_control_center",
        ROOT / "local-control-center" / "web",
        ROOT / "local-control-center" / "scripts",
    ]
    forbidden = ("Legacy/Node.js", "local-control-center/lib", "main.mjs", "app.mjs")
    for base in active_paths:
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ps1", ".jsx", ".js", ".mjs"}:
                source = path.read_text(encoding="utf-8")
                assert not any(token in source for token in forbidden), str(path)


def test_legacy_dashboard_routes_are_isolated_in_legacy_compat_slice() -> None:
    root_api = read("local_control_center/api.py")
    legacy_api = ROOT / "local_control_center" / "legacy_compat" / "api.py"

    assert legacy_api.exists()
    assert '"/api/state"' not in root_api
    assert "def legacy_" not in root_api
    assert '"/api/state"' in legacy_api.read_text(encoding="utf-8")


def test_memory_retrieval_slice_owns_http_index_and_memory_sql() -> None:
    memory_api = ROOT / "local_control_center" / "memory_retrieval" / "api.py"
    memory_repository = ROOT / "local_control_center" / "memory_retrieval" / "repository.py"
    memory_index = ROOT / "local_control_center" / "memory_retrieval" / "index.py"

    assert memory_api.exists()
    assert memory_repository.exists()
    assert memory_index.exists()

    root_api = read("local_control_center/api.py")
    assert '@app.get("/api/v1/memory")' not in root_api
    assert '@app.post("/api/v1/memory"' not in root_api
    assert '"/api/v1/retrieval/status"' not in root_api
    assert '"/api/v1/retrieval/reindex"' not in root_api
    assert '"/api/v1/retrieval/search"' not in root_api

    api_source = memory_api.read_text(encoding="utf-8")
    assert "SELECT " not in api_source
    assert "INSERT " not in api_source
    assert "UPDATE " not in api_source
    assert "DELETE " not in api_source

    store_source = read("local_control_center/store.py")
    memory_sql = [
        "INSERT INTO memory_items",
        "SELECT * FROM memory_items",
        "INSERT INTO memory_embeddings",
    ]
    for sql in memory_sql:
        assert sql not in store_source

    retrieval_compat = read("local_control_center/retrieval.py")
    assert "from .memory_retrieval.index import" in retrieval_compat


def test_fastapi_composition_entrypoint_is_named_app() -> None:
    app_entrypoint = ROOT / "local_control_center" / "app.py"

    assert app_entrypoint.exists()
    assert "create_app" in app_entrypoint.read_text(encoding="utf-8")

    cli_source = read("local_control_center/cli.py")
    assert "from .app import create_app" in cli_source

    python_tests = read("tests_py/test_python_control_center.py")
    assert "from local_control_center.api import create_app" not in python_tests


def test_screaming_architecture_domain_packages_are_explicit() -> None:
    expected_packages = [
        "control_plane",
        "jobs_approvals",
        "memory_retrieval",
        "workspaces_projects",
        "sessions_chats",
        "pipelines",
        "runtime_integrations",
        "security_policy",
        "legacy_compat",
        "shared",
    ]

    for package in expected_packages:
        package_dir = ROOT / "local_control_center" / package
        assert package_dir.is_dir(), package
        assert (package_dir / "__init__.py").exists(), package


def test_overview_read_model_is_not_embedded_in_platform_store() -> None:
    store_source = read("local_control_center/store.py")
    overview_source = ROOT / "local_control_center" / "control_plane" / "overview.py"

    assert overview_source.exists()
    assert "def build_overview" in overview_source.read_text(encoding="utf-8")
    assert '"workspaceState"' not in store_source
    assert '"architectureDecisions"' not in store_source
