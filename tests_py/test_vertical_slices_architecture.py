from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_store_facade_is_removed_from_product_and_tests() -> None:
    product_store = ROOT / "local_control_center" / "store.py"
    test_harness = ROOT / "tests_py" / "platform_store.py"

    assert not product_store.exists()
    assert not test_harness.exists()

    forbidden_imports = (
        "local_control_center.store",
        "tests_py.platform_store",
        "from .store",
        "import .store",
    )
    for base in (ROOT / "local_control_center", ROOT / "tests_py"):
        for path in base.rglob("*.py"):
            if path == Path(__file__):
                continue
            source = path.read_text(encoding="utf-8")
            assert not any(token in source for token in forbidden_imports), str(path)


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

    root_worker = read("local_control_center/worker.py")
    assert "from .jobs_approvals.worker import" in root_worker


def test_jobs_approvals_commands_do_not_depend_on_store_facade() -> None:
    commands_source = read("local_control_center/jobs_approvals/commands.py")
    api_source = read("local_control_center/jobs_approvals/api.py")
    worker_source = read("local_control_center/jobs_approvals/worker.py")
    agents_runtime_source = read("local_control_center/agents_runtime.py")

    assert "ControlPlaneFixture" not in commands_source
    assert "store." not in commands_source
    assert "JobsRepository" in commands_source
    assert "EventBus" in commands_source
    assert "commands.list_jobs(platform)" not in api_source
    assert "ControlPlaneFixture" not in worker_source
    assert "JobsRepository" in worker_source
    assert "ControlPlaneFixture" not in agents_runtime_source
    assert "JobsRepository" in agents_runtime_source


def test_active_python_backend_does_not_import_removed_node_backend() -> None:
    active_paths = [
        ROOT / "local_control_center",
        ROOT / "local-control-center" / "web",
        ROOT / "local-control-center" / "scripts",
    ]
    forbidden = ("Legacy/Node.js", "local-control-center/lib", "main.mjs", "app.mjs")
    for base in active_paths:
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ps1", ".ts", ".tsx"}:
                source = path.read_text(encoding="utf-8")
                assert not any(token in source for token in forbidden), str(path)


def test_removed_dashboard_routes_do_not_exist_in_active_backend() -> None:
    root_api = read("local_control_center/api.py")
    removed_state_route = '"/api/' + 'state"'
    removed_package = "leg" + "acy_compat"

    assert removed_state_route not in root_api
    assert ("def " + "leg" + "acy_") not in root_api
    assert not (ROOT / "local_control_center" / removed_package).exists()


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

    retrieval_compat = read("local_control_center/retrieval.py")
    assert "from .memory_retrieval.index import" in retrieval_compat


def test_memory_retrieval_commands_and_index_do_not_depend_on_store_facade() -> None:
    commands_source = read("local_control_center/memory_retrieval/commands.py")
    index_source = read("local_control_center/memory_retrieval/index.py")
    api_source = read("local_control_center/memory_retrieval/api.py")

    assert "ControlPlaneFixture" not in commands_source
    assert "ControlPlaneFixture" not in index_source
    assert "store." not in commands_source
    assert "self.store" not in index_source
    assert "MemoryRepository" in commands_source
    assert "MemoryRepository" in index_source
    assert "commands.list_memory(platform)" not in api_source


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
        "projects",
        "jobs_approvals",
        "memory_retrieval",
        "workspaces_projects",
        "sessions_chats",
        "pipelines",
        "prompts",
        "runtime_integrations",
        "integrations",
        "security_policy",
        "shared",
    ]

    for package in expected_packages:
        package_dir = ROOT / "local_control_center" / package
        assert package_dir.is_dir(), package
        assert (package_dir / "__init__.py").exists(), package


def test_overview_read_model_is_not_embedded_in_store_facade() -> None:
    overview_source = ROOT / "local_control_center" / "control_plane" / "overview.py"
    removed_workspace_key = '"workspace' + 'State"'
    overview_text = overview_source.read_text(encoding="utf-8")

    assert overview_source.exists()
    assert "def build_overview" in overview_text
    assert removed_workspace_key not in overview_text
    assert '"architectureDecisions"' in overview_text


def test_projects_slice_owns_project_catalog_sql() -> None:
    project_repository = ROOT / "local_control_center" / "projects" / "repository.py"
    project_api = ROOT / "local_control_center" / "projects" / "api.py"
    project_commands = ROOT / "local_control_center" / "projects" / "commands.py"
    assert project_repository.exists()
    assert project_api.exists()
    assert project_commands.exists()

    root_api = read("local_control_center/api.py")
    root_project_routes = [
        '"/api/v1/project-templates"',
        '"/api/v1/projects"',
        '"/api/v1/providers"',
        '"/api/v1/teams"',
        '"/api/v1/agents"',
    ]
    for route in root_project_routes:
        assert route not in root_api

    api_source = project_api.read_text(encoding="utf-8")
    assert "SELECT " not in api_source
    assert "INSERT " not in api_source
    assert "UPDATE " not in api_source
    assert "DELETE " not in api_source

    commands_source = project_commands.read_text(encoding="utf-8")
    assert "ControlPlaneFixture" not in commands_source
    assert "store." not in commands_source
    assert "ProjectsRepository" in commands_source
    assert "EventBus" in commands_source


def test_integrations_slice_owns_ide_connection_routes_and_sql() -> None:
    integrations_api = ROOT / "local_control_center" / "integrations" / "api.py"
    integrations_repository = ROOT / "local_control_center" / "integrations" / "repository.py"

    assert integrations_api.exists()
    assert integrations_repository.exists()

    root_api = read("local_control_center/api.py")
    assert '"/api/v1/ide-connections"' not in root_api
    assert '"/api/v1/open-design"' not in root_api

    api_source = integrations_api.read_text(encoding="utf-8")
    assert "SELECT " not in api_source
    assert "INSERT " not in api_source
    assert "UPDATE " not in api_source
    assert "DELETE " not in api_source


def test_prompts_slice_owns_prompt_routes_and_versioning_sql() -> None:
    prompts_api = ROOT / "local_control_center" / "prompts" / "api.py"
    prompts_repository = ROOT / "local_control_center" / "prompts" / "repository.py"

    assert prompts_api.exists()
    assert prompts_repository.exists()

    root_api = read("local_control_center/api.py")
    assert '"/api/v1/prompts"' not in root_api

    api_source = prompts_api.read_text(encoding="utf-8")
    assert "SELECT " not in api_source
    assert "INSERT " not in api_source
    assert "UPDATE " not in api_source
    assert "DELETE " not in api_source


def test_shared_migrations_owns_schema_bootstrap() -> None:
    migrations = ROOT / "local_control_center" / "shared" / "migrations.py"
    assert migrations.exists()

    runtime_source = read("local_control_center/control_plane/runtime.py")
    migrations_source = migrations.read_text(encoding="utf-8")

    assert "from local_control_center.shared.migrations import initialize_platform_schema" in runtime_source
    assert "initialize_platform_schema(self.connection)" in runtime_source

    schema_tokens = [
        "CREATE TABLE IF NOT EXISTS",
        "ALTER TABLE",
        "INSERT OR IGNORE INTO schema_migrations",
        "PRAGMA table_info",
    ]
    for token in schema_tokens:
        assert token not in runtime_source
        assert token in migrations_source


def test_shared_db_and_serialization_own_infrastructure_utilities() -> None:
    shared_db = ROOT / "local_control_center" / "shared" / "db.py"
    shared_time = ROOT / "local_control_center" / "shared" / "time.py"
    shared_serialization = ROOT / "local_control_center" / "shared" / "serialization.py"
    assert shared_db.exists()
    assert shared_time.exists()
    assert shared_serialization.exists()

    runtime_source = read("local_control_center/control_plane/runtime.py")
    assert "from local_control_center.shared.db import open_sqlite_connection" in runtime_source
    assert "open_sqlite_connection(self.db_path)" in runtime_source
    assert "sqlite3.connect(" not in runtime_source
    assert "PRAGMA journal_mode = WAL" not in runtime_source

    forbidden_store_utility_imports = (
        "from local_control_center.store import utc_now",
        "from local_control_center.store import json_dumps",
        "from local_control_center.store import json_loads",
        "from local_control_center.store import stable_hash",
        "from .store import utc_now",
        "from .store import json_dumps",
        "from .store import json_loads",
        "from .store import stable_hash",
    )
    for path in (ROOT / "local_control_center").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden_store_utility_imports), str(path)

    for path in (ROOT / "local_control_center").rglob("repository.py"):
        source = path.read_text(encoding="utf-8")
        assert "store import" not in source, str(path)
        for local_utility in (
            "def utc_now(",
            "def add_millis(",
            "def json_dumps(",
            "def json_loads(",
            "def stable_hash(",
        ):
            assert local_utility not in source, str(path)

    skills_source = read("local_control_center/agents/skills.py")
    assert "store import" not in skills_source


def test_slice_apis_do_not_use_store_facade_for_events_or_project_lookup() -> None:
    forbidden = ("platform.record_event", "platform.record_audit", "platform.get_project")
    for path in (ROOT / "local_control_center").rglob("api.py"):
        if path == ROOT / "local_control_center" / "api.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), str(path)


def test_shared_event_bus_tests_use_direct_repository_setup() -> None:
    event_bus_tests = read("tests_py/test_shared_event_bus.py")

    assert "ControlPlaneFixture" not in event_bus_tests


def test_active_runtime_does_not_import_store_facade() -> None:
    root_api = read("local_control_center/api.py")
    cli_source = read("local_control_center/cli.py")
    runtime_source = read("local_control_center/control_plane/runtime.py")

    assert not (ROOT / "local_control_center" / "store.py").exists()
    assert "store:" not in root_api
    assert "runtime or store" not in root_api
    assert "ControlPlaneFixture" not in root_api
    assert "ControlPlaneFixture" not in cli_source
    assert "ControlPlaneFixture" not in runtime_source
    assert "ControlCenterRuntime" in root_api
    assert "ControlCenterRuntime" in cli_source
