"""Policy tests use deterministic capacity; browser E2E keeps real host sampling."""

from contextlib import closing

import pytest

from local_control_center.agents import runtime_readiness
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now
from tests_web.fixtures.lifecycle_runtime import synthetic_runtime_scope

ENDPOINT = "http://127.0.0.1:18991"


@pytest.fixture
def owned_scope(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    folder = scratch / "aido-lifecycle-owned"
    folder.mkdir(parents=True)
    monkeypatch.setenv("AIDO_QUALITY_SCRATCH", str(scratch))
    monkeypatch.setenv("AIDO_QUALITY_RETAINED", str(tmp_path / "evidence"))
    return folder / "platform.sqlite"


def readiness(connection, endpoint=ENDPOINT):
    return runtime_readiness.apply_effective_readiness(
        connection,
        {
            "id": "ollama_remote",
            "kind": "api",
            "configured": True,
            "authenticated": True,
            "installed": True,
            "executable": True,
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        },
        {"providerId": "ollama_remote", "baseUrl": endpoint},
        {"allowed": True, "policy": {"global": {"remoteEnabled": True}, "project": {"remoteEnabled": True}}},
    )


def test_synthetic_endpoint_uses_funded_light_budget_without_changing_real_local_policy(owned_scope):
    with closing(open_sqlite_connection(owned_scope)) as connection:
        initialize_platform_schema(connection)
        ResourceRepository(connection).record_sample(
            ResourceSnapshot.test_snapshot(available_memory_bytes=24 * 1024**3)
        )
        assert "aggregate_memory_budget" in readiness(connection)["blockingReasons"]
        with synthetic_runtime_scope(ENDPOINT, owned_scope):
            assert readiness(connection)["resourceAdmissible"] is True
            assert readiness(connection, "http://127.0.0.1:18992")["resourceAdmissible"] is False
        assert readiness(connection)["resourceAdmissible"] is False


def test_synthetic_scope_still_rejects_real_resource_pressure(owned_scope):
    with closing(open_sqlite_connection(owned_scope)) as connection:
        initialize_platform_schema(connection)
        ResourceRepository(connection).record_sample(
            ResourceSnapshot.test_snapshot(available_memory_bytes=17 * 1024**3)
        )
        with synthetic_runtime_scope(ENDPOINT, owned_scope):
            result = readiness(connection)
            assert not result["resourceAdmissible"] and not result["executable"]
            assert "aggregate_memory_budget" in result["blockingReasons"]
        assert not ResourceRepository(connection).active_leases()


def test_dispatcher_restores_exact_scope_without_bypassing_supervision(owned_scope, monkeypatch):
    from local_control_center.agents import ai_execution
    from local_control_center.executions import dispatcher, workloads

    observed = []
    monkeypatch.setattr(dispatcher, "run_supervised_capture", lambda argv, **kw: observed.append((argv, kw)))
    account = {"providerId": "ollama_remote", "baseUrl": ENDPOINT}
    with synthetic_runtime_scope(ENDPOINT, owned_scope):
        assert ai_execution.provider_workload_class(account) == "remote_llm_light"
        assert workloads.provider_workload_class(account) == "remote_llm_light"
        dispatcher.run_supervised_capture(
            [
                "python",
                "-m",
                "local_control_center.executions.runner",
                "--db",
                str(owned_scope),
                "--execution-id",
                "job-one",
                "--owner-id",
                "worker-one",
                "--fencing-token",
                "7",
            ],
            workload_class="agent_cli",
            timeout_seconds=900,
        )
    argv, options = observed[0]
    assert argv[1:3] == ["-m", "tests_web.fixtures.lifecycle_runtime"]
    assert argv[argv.index("--endpoint") + 1] == ENDPOINT
    assert argv[argv.index("--db") + 1] == str(owned_scope)
    assert argv[-2:] == ["--fencing-token", "7"]
    assert options == {"workload_class": "agent_cli", "timeout_seconds": 900}
    assert ai_execution.provider_workload_class(account) == "local_gpu_model"


def test_synthetic_call_profiles_fit_moderate_outer_cpu_without_changing_real_defaults(owned_scope):
    from local_control_center.host_resources.profiles import workload_profile

    before = workload_profile("agent_cli").model_dump()
    with synthetic_runtime_scope(ENDPOINT, owned_scope):
        assert workload_profile("agent_cli").cpu_limit_percent <= 30
        assert workload_profile("remote_llm_light").memory_limit_bytes == 2 * 1024**3
        assert workload_profile("local_gpu_model").cpu_limit_percent == 50
    assert workload_profile("agent_cli").model_dump() == before


def test_synthetic_scope_rejects_database_outside_invocation(owned_scope, tmp_path):
    with (
        pytest.raises(ValueError, match="owned lifecycle"),
        synthetic_runtime_scope(ENDPOINT, tmp_path / "operational.sqlite"),
    ):
        pytest.fail("Must reject before changing policy")


def test_product_classifier_does_not_accept_client_or_environment_test_flags(monkeypatch):
    monkeypatch.setenv("AIDO_SYNTHETIC_RUNTIME", "true")
    assert (
        runtime_readiness.provider_workload_class(
            {
                "providerId": "ollama_remote",
                "baseUrl": ENDPOINT,
                "isTest": True,
                "workloadClass": "remote_llm_light",
            }
        )
        == "local_gpu_model"
    )


def test_native_fixture_budget_funds_all_parts_and_restores_real_runtime_defaults(tmp_path, monkeypatch):
    from local_control_center.host_resources.profiles import workload_profile
    from local_control_center.process_supervision.launcher_session import SESSION_BUDGET
    from local_control_center.quality.paths import QualityPaths
    from tests_py.fixtures.capture_runtime import capture_resource_scope

    paths = QualityPaths.create(tmp_path / "scratch", tmp_path / "retained", [])
    for key, value in paths.environment({}).items():
        monkeypatch.setenv(key, value)
    db = paths.scratch / "isolated.sqlite"
    with capture_resource_scope(db):
        assert workload_profile("agent_cli").memory_limit_bytes == 2 * 1024**3
        assert workload_profile("capture_session").memory_limit_bytes == 12 * 1024**3
        assert sum(p["memoryBytes"] for p in SESSION_BUDGET["parts"].values()) == 12 * 1024**3
        assert workload_profile("local_gpu_model").memory_limit_bytes == 16 * 1024**3
    assert workload_profile("agent_cli").memory_limit_bytes == 8 * 1024**3
    assert SESSION_BUDGET["memoryBytes"] == 18 * 1024**3


@pytest.mark.parametrize("condition", ["expired", "unauthorized"])
def test_reduced_capture_budget_still_requires_capacity_and_authority(tmp_path, monkeypatch, condition):
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
    from local_control_center.process_supervision.service import ExecutionCancelled, ProcessSupervisorService
    from local_control_center.quality.paths import QualityPaths
    from local_control_center.settings.repository import SettingsRepository
    from tests_py.fixtures.capture_runtime import capture_resource_scope

    paths = QualityPaths.create(tmp_path / "scratch", tmp_path / "retained", [])
    for key, value in paths.environment({}).items():
        monkeypatch.setenv(key, value)
    db = paths.scratch / "isolated.sqlite"
    with capture_resource_scope(db), closing(open_sqlite_connection(db)) as connection:
        initialize_platform_schema(connection)
        SettingsRepository(connection).set_value("resources.minFreeMemoryGiB", "general", None, 12)
        governor = HostResourceGovernor(connection)
        request = ResourceAdmissionRequest(
            execution_id="test-only", owner_id="owner", workload_class="capture_session"
        )
        rejected = governor.admit(
            request, snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=23 * 1024**3)
        )
        assert rejected.lease is None and rejected.reason_code == "aggregate_memory_budget"
        lease = governor.admit(
            request, snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=24 * 1024**3)
        ).lease
        assert lease.memory_limit_bytes == 12 * 1024**3
        context = ProcessExecutionContext(
            db_path=db,
            execution_id="test-only",
            resource_lease_id=lease.id,
            worker_id="not-the-owner" if condition == "unauthorized" else None,
            fencing_token=999 if condition == "unauthorized" else None,
        )
        if condition == "expired":
            connection.execute(
                "UPDATE resource_leases SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (lease.id,)
            )
        with execution_scope(context), pytest.raises(ExecutionCancelled):
            ProcessSupervisorService().start(
                argv=["must-not-spawn"], cwd=paths.scratch, workload_class="agent_cli"
            )
        assert connection.execute("SELECT COUNT(*) FROM managed_processes").fetchone()[0] == 0
        governor.release(lease.id, reason="test-complete-no-spawn")
        assert not ResourceRepository(connection).active_leases()


def test_capture_scope_rejects_unowned_database(tmp_path, monkeypatch):
    from local_control_center.quality.paths import QualityPaths
    from tests_py.fixtures.capture_runtime import capture_resource_scope

    paths = QualityPaths.create(tmp_path / "scratch", tmp_path / "retained", [])
    for key, value in paths.environment({}).items():
        monkeypatch.setenv(key, value)
    with (
        pytest.raises(ValueError, match="owned isolated"),
        capture_resource_scope(tmp_path / "original.sqlite"),
    ):
        pytest.fail("Must reject before altering test budgets")
