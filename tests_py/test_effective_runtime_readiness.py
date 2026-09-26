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
    assert provider_workload_class({"baseUrl": "http://localhost:11434"}) == "local_model_call"
    assert provider_workload_class({"baseUrl": "http://127.0.0.1:8000"}) == "local_model_call"
    assert provider_workload_class({"providerType": "cli"}) == "agent_cli"
    assert provider_workload_class({"baseUrl": "https://api.example.test"}) == "remote_llm_light"


def _configured_omniroute_proxy():
    from local_control_center.agents.provider_catalog import provider_catalog_entry

    entry = provider_catalog_entry("omniroute")
    return {
        "providerId": "operator-proxy-instance",
        "providerType": entry.provider_type,
        "providerFamily": entry.provider_family,
        "apiFormat": entry.api_format,
        "deploymentMode": entry.deployment_mode,
        "baseUrl": entry.default_base_url,
        "providerCatalogId": entry.id,
        "metadata": {"endpointKind": "remote"},
    }


@pytest.mark.parametrize("deployment_mode", ["self_hosted_development", "self_hosted_enterprise"])
def test_declared_omniroute_proxy_uses_existing_light_budget_without_gpu(deployment_mode):
    from local_control_center.host_resources.profiles import workload_profile

    account = {**_configured_omniroute_proxy(), "deploymentMode": deployment_mode}
    workload = provider_workload_class(account)
    assert workload == "remote_llm_light"
    profile = workload_profile(workload)
    assert profile.memory_limit_bytes == 2 * 1024**3
    assert profile.gpu_required is False


@pytest.mark.parametrize(
    "override",
    [
        {"providerType": "local", "providerFamily": "ollama", "apiFormat": "ollama"},
        {"providerType": "api", "providerFamily": "nvidia_nim"},
        {"providerType": "manual"},
        {"providerFamily": "custom_gpu_server"},
        {"apiFormat": "ollama"},
        {"deploymentMode": "local"},
        {"metadata": {"providerCatalogId": "omniroute", "endpointKind": "local"}},
        {"providerCatalogId": None, "metadata": {"endpointKind": "remote"}},
        {"metadata": {}},
    ],
    ids=[
        "ollama",
        "nim",
        "manual",
        "local-family",
        "local-format",
        "local-deployment",
        "local-upstream",
        "unidentified-proxy",
        "unknown-upstream",
    ],
)
def test_loopback_local_or_unverified_contract_keeps_local_inference_budget(override):
    assert provider_workload_class({**_configured_omniroute_proxy(), **override}) == "local_model_call"


def test_declared_proxy_admits_two_gib_but_keeps_host_and_runtime_policy(tmp_path):
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "readiness.sqlite")
    runtime.init()
    account = _configured_omniroute_proxy()
    try:
        sample = ResourceSnapshot.test_snapshot(available_memory_bytes=18 * 1024**3)
        resources = ResourceRepository(runtime.connection)
        resources.record_sample(sample)
        status = {
            "id": account["providerId"],
            "kind": "gateway",
            "configured": True,
            "installed": True,
            "authenticated": True,
            "executable": True,
            "reason": "Ready",
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        }
        policy = {
            "allowed": True,
            "policy": {"global": {"remoteEnabled": True}, "project": {"remoteEnabled": True}},
        }
        assert apply_effective_readiness(runtime.connection, dict(status), account, policy)["executable"]
        denied = apply_effective_readiness(
            runtime.connection, dict(status), account, {**policy, "allowed": False}
        )
        assert not denied["executable"]
        assert "policy_denied" in denied["blockingReasons"]
        decision = HostResourceGovernor(runtime.connection).admit(
            ResourceAdmissionRequest(
                execution_id="proxy", owner_id="worker", workload_class=provider_workload_class(account)
            ),
            snapshot=sample,
        )
        assert decision.status == "admitted"
        assert decision.lease.memory_limit_bytes == 2 * 1024**3
        assert not decision.lease.gpu_required
        resources.record_sample(ResourceSnapshot.test_snapshot(available_memory_bytes=0))
        blocked = apply_effective_readiness(runtime.connection, dict(status), account, policy)
        assert not blocked["resourceAdmissible"]
        assert "hard_memory_floor" in blocked["blockingReasons"]
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["cli", "api"])
@pytest.mark.parametrize("condition", ["verified", "wrong_owner", "wrong_lease", "outside", "memory", "cpu"])
def test_readiness_counts_current_job_reservation_once(tmp_path, kind, condition):
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "readiness.sqlite")
    runtime.init()
    try:
        # Frontera con request 4 GiB: el job cabe (4 <= 4,25) y job + remota (4,5) no.
        sample = ResourceSnapshot.test_snapshot(available_memory_bytes=int(20.25 * 1024**3))
        lease = (
            HostResourceGovernor(runtime.connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="job-current", owner_id="worker-current", workload_class="agent_cli"
                ),
                snapshot=sample,
            )
            .lease
        )
        assert lease is not None
        if condition == "memory":
            sample = sample.model_copy(update={"available_memory_bytes": 19 * 1024**3})
        if condition == "cpu":
            sample = sample.model_copy(update={"cpu_percent_1s": 90.0})
        ResourceRepository(runtime.connection).record_sample(sample)
        status = {
            "id": "test-runtime",
            "kind": kind,
            "configured": True,
            "installed": True,
            "authenticated": True,
            "executable": True,
            "reason": "Ready",
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        }
        flag = "cliEnabled" if kind == "cli" else "remoteEnabled"
        policy = {"allowed": True, "policy": {"global": {flag: True}, "project": {flag: True}}}
        before = runtime.connection.total_changes
        with execution_scope(
            ProcessExecutionContext(
                db_path=runtime.db_path,
                execution_id="job-current",
                project_id="project",
                connection=runtime.connection,
                in_job_runner=condition != "outside",
                worker_id="other-worker" if condition == "wrong_owner" else "worker-current",
                resource_lease_id="other-lease" if condition == "wrong_lease" else lease.id,
            )
        ):
            result = apply_effective_readiness(
                runtime.connection,
                status,
                {"providerType": kind, "baseUrl": "https://api.example.test"},
                policy,
            )
        assert result["resourceAdmissible"] is (condition == "verified"), result
        assert runtime.connection.total_changes == before
        assert len(ResourceRepository(runtime.connection).active_leases()) == 1
    finally:
        runtime.close()


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
        # Con una causa real (auth) la alerta se conserva; si lo unico que falta es capacidad del
        # host, no hay alerta: es transitorio y no se corrige desde el panel del proveedor. El
        # motivo sigue publicado arriba, asi que no se oculta nada, solo se deja de pedir accion.
        assert result["blockerType"] == ("runtime_auth_missing" if already_blocked else None)
        assert runtime.connection.total_changes == before
        assert not ResourceRepository(runtime.connection).active_leases()
    finally:
        runtime.close()


@pytest.mark.real_host_resources
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


@pytest.mark.parametrize(
    "condition", ["verified", "outside", "expired", "mismatch", "memory", "cpu", "gpu", "local-memory"]
)
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
        if condition in {"memory", "local-memory"}:
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
            local_call = condition in {"gpu", "local-memory"}
            account = {"baseUrl": "http://localhost:11434"} if local_call else {"providerType": "cli"}
            result = apply_effective_readiness(runtime.connection, status, account, policy)
        # A resident-model client re-checks only its own class, exactly as BranchAdmission.reserve does.
        admissible = condition in {"verified", "gpu", "local-memory"}
        assert result["resourceAdmissible"] is admissible
        assert result["executable"] is admissible
        if condition in {"expired", "mismatch"}:
            assert "resource_session_unverified" in result["blockingReasons"]
        assert runtime.connection.total_changes == before
        assert len(ResourceRepository(runtime.connection).active_leases()) == 1
    finally:
        runtime.close()


def _capacity_status() -> dict:
    """Proveedor sano en todo lo que el operador controla: sólo el host está ocupado."""
    return {
        "id": "omniroute",
        "kind": "gateway",
        "configured": True,
        "authenticated": True,
        "installed": True,
        "executable": True,
        "reason": "Ready",
        "healthStatus": "healthy",
        "healthCheckedAt": utc_now(),
    }


def _policy() -> dict:
    return {
        "allowed": True,
        "policy": {"global": {"remoteEnabled": True}, "project": {"remoteEnabled": True}},
    }


def test_a_busy_host_is_not_reported_as_an_ai_that_needs_configuring(tmp_path):
    """Que el host esté ocupado no es un problema del runtime ni se arregla con credenciales.

    Es lo que hacía reconfigurar el mismo proveedor una y otra vez: configurado, autenticado y sano,
    pero la UI lo listaba en "necesitan atención" con un deep-link al panel de credenciales, donde no
    hay nada que corregir. La causa sigue publicada en `reason`/`blockingReasons`; lo que se corta es
    la alerta accionable falsa.
    """
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        gib = 1024**3
        ResourceRepository(runtime.connection).record_sample(
            ResourceSnapshot.test_snapshot(
                cpu_percent_1s=95,
                cpu_percent_30s=95,
                available_memory_bytes=32 * gib,
                disk_free_bytes={"C:": 500 * gib},
            )
        )
        result = apply_effective_readiness(
            runtime.connection, _capacity_status(), {"baseUrl": "https://example.test"}, _policy()
        )

        assert result["executable"] is False
        assert result["effectiveStatus"] == "blocked"
        assert "host_cpu_saturated" in result["blockingReasons"], result["blockingReasons"]
        assert "host_cpu_saturated" in result["reason"], result["reason"]
        assert result["blockerType"] is None, result["blockerType"]
    finally:
        runtime.close()


def test_a_real_runtime_fault_still_raises_the_operator_alert(tmp_path):
    """La contrapartida: con el host ocupado Y una causa real, la alerta se mantiene."""
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        gib = 1024**3
        ResourceRepository(runtime.connection).record_sample(
            ResourceSnapshot.test_snapshot(
                cpu_percent_1s=95,
                cpu_percent_30s=95,
                available_memory_bytes=32 * gib,
                disk_free_bytes={"C:": 500 * gib},
            )
        )
        broken = _capacity_status() | {"authenticated": False}
        result = apply_effective_readiness(
            runtime.connection, broken, {"baseUrl": "https://example.test"}, _policy()
        )

        assert result["blockerType"] == "runtime_auth_missing", result
        assert "authentication_required" in result["blockingReasons"]

    finally:
        runtime.close()


def test_every_governor_wait_code_is_classified_as_host_capacity_or_not(tmp_path):
    """Si el gobernador agrega un motivo nuevo, este set tiene que decidir explícitamente qué es.

    Sin este ancla el set se queda atrás en silencio y un motivo transitorio nuevo vuelve a
    presentarse como "configura esta IA".
    """
    import re
    from pathlib import Path as _Path

    from local_control_center.agents.runtime_readiness import HOST_CAPACITY_BLOCKERS

    source = _Path("local_control_center/host_resources/governor.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'wait\(\s*"([a-z_]+)"', source))

    assert codes, "no se encontró ningún motivo del gobernador; el patrón quedó obsoleto"
    assert codes <= HOST_CAPACITY_BLOCKERS, sorted(codes - HOST_CAPACITY_BLOCKERS)


@pytest.mark.parametrize("condition", ["cli_parent", "gpu_parent", "unreal"])
def test_local_model_call_readiness_borrows_the_current_job_and_keeps_the_unreal_conflict(
    tmp_path, monkeypatch, condition
):
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest
    from local_control_center.process_supervision import session_client
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "local-readiness.sqlite")
    runtime.init()
    monkeypatch.setattr(session_client, "session_identity", lambda _path: None)
    try:
        lease = (
            HostResourceGovernor(runtime.connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="local-job",
                    owner_id="worker",
                    workload_class="local_gpu_model" if condition == "gpu_parent" else "agent_cli",
                ),
                snapshot=ResourceSnapshot.test_snapshot(available_memory_bytes=48 * 1024**3),
            )
            .lease
        )
        assert lease is not None
        ResourceRepository(runtime.connection).record_sample(
            ResourceSnapshot.test_snapshot(
                available_memory_bytes=20 * 1024**3, unreal_editor_running=condition == "unreal"
            )
        )
        status = {
            "id": "llama_cpp",
            "kind": "local",
            "configured": True,
            "authenticated": True,
            "installed": True,
            "executable": True,
            "reason": "Ready",
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        }
        policy = {"allowed": True, "policy": {"global": {"localEnabled": True}, "project": {}}}
        account = {
            "providerId": "llama_cpp",
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "providerCatalogId": "llama_cpp",
            "baseUrl": "http://127.0.0.1:1/v1",
        }
        before = runtime.connection.total_changes
        with execution_scope(
            ProcessExecutionContext(
                db_path=runtime.db_path,
                connection=runtime.connection,
                execution_id="local-job",
                worker_id="worker",
                resource_lease_id=lease.id,
                in_job_runner=True,
            )
        ):
            result = apply_effective_readiness(runtime.connection, status, account, policy)
        assert result["resourceAdmissible"] is (condition != "unreal")
        assert result["executable"] is (condition != "unreal")
        if condition == "unreal":
            assert "unreal_local_gpu_conflict" in result["blockingReasons"]
        assert runtime.connection.total_changes == before
    finally:
        runtime.close()
