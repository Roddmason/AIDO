from __future__ import annotations

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
