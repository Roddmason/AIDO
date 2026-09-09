from __future__ import annotations

import pytest

from local_control_center.agents.runtime_readiness import apply_effective_readiness, provider_workload_class
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.shared.time import utc_now


def test_readiness_distinguishes_enabled_auth_policy_and_host_without_writes(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        status = {
            "id": "test-api",
            "kind": "api",
            "configured": True,
            "authenticated": False,
            "installed": True,
            "executable": False,
            "reason": "authentication_required",
            "healthStatus": "offline",
        }
        policy = {
            "allowed": True,
            "policy": {"global": {"remoteEnabled": True}, "project": {"remoteEnabled": True}},
        }
        before = runtime.connection.total_changes
        result = apply_effective_readiness(
            runtime.connection, dict(status), {"baseUrl": "https://example.test"}, policy
        )
        assert result["globallyEnabled"] and result["projectEnabled"]
        assert not result["executable"] and not result["resourceAdmissible"]
        assert "authentication_required" in result["blockingReasons"]
        assert "resource_snapshot_required" in result["blockingReasons"]
        assert runtime.connection.total_changes == before
        ResourceRepository(runtime.connection).record_sample(ResourceSnapshot.test_snapshot())
        status.update(
            authenticated=True,
            executable=True,
            healthStatus="healthy",
            healthCheckedAt=utc_now(),
            reason="Ready",
        )
        before = runtime.connection.total_changes
        result = apply_effective_readiness(
            runtime.connection, dict(status), {"baseUrl": "https://example.test"}, policy
        )
        assert result["executable"] and result["resourceAdmissible"]
        assert result["blockingReasons"] == []
        assert runtime.connection.total_changes == before
        status["healthCheckedAt"] = "2000-01-01T00:00:00Z"
        result = apply_effective_readiness(
            runtime.connection, dict(status), {"baseUrl": "https://example.test"}, policy
        )
        assert not result["healthy"] and not result["executable"]
        assert "health_check_required" in result["blockingReasons"]
    finally:
        runtime.close()


def test_local_inference_is_not_classified_as_a_remote_light_call():
    assert provider_workload_class({"baseUrl": "http://localhost:11434"}) == "local_gpu_model"
    assert provider_workload_class({"baseUrl": "http://127.0.0.1:8000"}) == "local_gpu_model"
    assert provider_workload_class({"providerType": "cli"}) == "agent_cli"
    assert provider_workload_class({"baseUrl": "https://api.example.test"}) == "remote_llm_light"


@pytest.mark.parametrize("already_blocked", [False, True])
def test_resource_rejection_explains_effective_state_without_hiding_prior_blocker(tmp_path, already_blocked):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "readiness.sqlite")
    runtime.init()
    try:
        ResourceRepository(runtime.connection).record_sample(
            ResourceSnapshot.test_snapshot(cpu_percent_1s=90.0)
        )
        status = {
            "id": "codex_cli",
            "kind": "cli",
            "configured": True,
            "installed": True,
            "authenticated": not already_blocked,
            "executable": not already_blocked,
            "reason": "authentication_required" if already_blocked else "CLI is executable.",
            "blockerType": "runtime_auth_missing" if already_blocked else None,
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        }
        before = runtime.connection.total_changes
        result = apply_effective_readiness(
            runtime.connection,
            status,
            {"providerType": "cli"},
            {
                "allowed": True,
                "policy": {"global": {"cliEnabled": True}, "project": {"cliEnabled": True}},
            },
        )
        assert not result["executable"] and not result["canRunPrompt"]
        assert "host_cpu_saturated" in result["blockingReasons"]
        assert result["reason"] == ("authentication_required" if already_blocked else "host_cpu_saturated")
        assert result["blockerType"] == (
            "runtime_auth_missing" if already_blocked else "runtime_not_executable"
        )
        assert runtime.connection.total_changes == before
        assert not ResourceRepository(runtime.connection).active_leases()
    finally:
        runtime.close()


def test_disk_probe_accepts_a_database_file_on_windows(tmp_path):
    from local_control_center.host_resources.probes import HostResourceProbe

    database = tmp_path / "database.sqlite"
    database.touch()
    sample = HostResourceProbe(relevant_paths=[database]).sample(cpu_interval_seconds=0)
    assert sample.disk_free_bytes
    assert min(sample.disk_free_bytes.values()) > 0


def test_readonly_ollama_status_never_starts_a_network_probe(monkeypatch):
    from local_control_center.agents import runtime_status

    def unexpected_probe(**kwargs):
        raise AssertionError("GET readiness must not contact a provider")

    monkeypatch.setattr(runtime_status, "cached_ollama_status", unexpected_probe)
    status = runtime_status._ollama_provider_status(
        {"providerId": "ollama", "enabled": True, "baseUrl": "http://localhost:11434"},
        {"enabled": True},
        ["chat"],
        {"allowed": True},
        allow_probes=False,
    )
    assert not status["executable"]
    assert status["healthCheckedAt"] is None


@pytest.mark.parametrize("condition", ["verified", "outside", "expired", "mismatch", "memory", "cpu", "gpu"])
def test_readiness_counts_verified_capture_session_once(tmp_path, monkeypatch, condition):
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest
    from local_control_center.process_supervision import session_client
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "readiness.sqlite")
    runtime.init()
    try:
        sample = ResourceSnapshot.test_snapshot(available_memory_bytes=38 * 1024**3)
        lease = (
            HostResourceGovernor(runtime.connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="capture-root", owner_id="capture-root", workload_class="capture_session"
                ),
                snapshot=sample,
            )
            .lease
        )
        assert lease is not None
        if condition == "memory":
            sample = sample.model_copy(update={"available_memory_bytes": 33 * 1024**3})
        if condition == "cpu":
            sample = sample.model_copy(update={"cpu_percent_1s": 90.0})
        ResourceRepository(runtime.connection).record_sample(sample)

        def native_identity(_db):
            if condition == "expired":
                raise PermissionError("Launcher reservation is unavailable")
            return None if condition == "outside" else ({"aggregateId": "capture-root"}, lease)

        monkeypatch.setattr(session_client, "session_identity", native_identity)
        status = {
            "id": "codex_cli",
            "kind": "cli",
            "configured": True,
            "authenticated": True,
            "installed": True,
            "executable": True,
            "reason": "Ready",
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        }
        policy = {
            "allowed": True,
            "policy": {"global": {"cliEnabled": True}, "project": {"cliEnabled": True}},
        }
        before = runtime.connection.total_changes
        with execution_scope(
            ProcessExecutionContext(
                db_path=runtime.db_path,
                execution_id="po-operation",
                resource_lease_id="unrelated-lease" if condition == "mismatch" else lease.id,
                aggregate_managed_process_id="capture-root",
                in_job_runner=True,
            )
        ):
            account = {"baseUrl": "http://localhost:11434"} if condition == "gpu" else {"providerType": "cli"}
            result = apply_effective_readiness(runtime.connection, status, account, policy)
        assert result["resourceAdmissible"] is (condition == "verified")
        assert result["executable"] is (condition == "verified")
        if condition in {"expired", "mismatch"}:
            assert "resource_session_unverified" in result["blockingReasons"]
        assert runtime.connection.total_changes == before
        assert len(ResourceRepository(runtime.connection).active_leases()) == 1
    finally:
        runtime.close()
