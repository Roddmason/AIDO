"""llama.cpp (llama-server) entra al catálogo como runtime local OpenAI-compatible.

Familia ``openai_compatible`` para quedar en ``MODEL_RUNTIME_TOOLS`` (PO y Developer por patch sin
adaptadores nuevos) y siembra de ``code_edit``/``code_review`` como OmniRoute, o los roles de build
y review la descartan por capacidades faltantes.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.shared.db import open_sqlite_connection
from tests_py.test_provider_setup_catalog import auth_headers, catalog_by_id, create_client

LLAMA_CPP_BASE_URL = "http://127.0.0.1:8082/v1"


def test_catalog_exposes_llama_cpp_as_a_local_openai_compatible_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = catalog_by_id(create_client(tmp_path, monkeypatch))["llama_cpp"]
    assert entry["providerType"] == "local"
    assert entry["apiFormat"] == "openai_compatible"
    assert entry["providerFamily"] == "openai_compatible"
    assert entry["defaultBaseUrl"] == LLAMA_CPP_BASE_URL
    assert entry["credentialKind"] == "optional_bearer_token"
    assert entry["requiredFields"] == []


def test_from_catalog_llama_cpp_seeds_build_and_review_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=auth_headers(client),
        json={"providerId": "llama_cpp", "enabled": True},
    )
    assert response.status_code == 201, response.text
    provider = response.json()["provider"]
    assert provider["providerId"] == "llama_cpp"
    assert provider["baseUrl"] == LLAMA_CPP_BASE_URL
    with (
        closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))) as connection,
        connection,
    ):
        rows = connection.execute(
            "SELECT capability FROM runtime_capabilities WHERE runtime = 'llama_cpp' AND enabled = 1"
        ).fetchall()
        instance = provider_instance("llama_cpp", connection=connection)
    assert {str(row["capability"]) for row in rows} == {"chat", "code_edit", "code_review"}
    assert isinstance(instance, OpenAICompatibleProvider)
    assert instance.base_url == LLAMA_CPP_BASE_URL


def test_runtime_status_service_includes_llama_cpp_with_local_provider_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RuntimeStatusService debe incluir llama_cpp en list_provider_statuses() con tipo 'local'."""
    client = create_client(tmp_path, monkeypatch)
    # Registrar llama_cpp desde el catálogo
    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=auth_headers(client),
        json={"providerId": "llama_cpp", "enabled": True},
    )
    assert response.status_code == 201, response.text

    # Verificar que RuntimeStatusService incluye llama_cpp
    with closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))) as connection:
        service = RuntimeStatusService(connection)
        statuses = service.list_provider_statuses(project_id=None)

    llama_cpp_status = next((s for s in statuses if s["id"] == "llama_cpp"), None)
    assert llama_cpp_status is not None, "llama_cpp debe estar en list_provider_statuses()"
    assert llama_cpp_status["kind"] == "local", (
        f"llama_cpp debe tener kind='local', pero es {llama_cpp_status['kind']}"
    )
    # Si está configurado pero no se ha hecho health check explícito, el status puede variar.
    # Lo importante es que NO sea rechazado como "Provider type is not executable by the local control plane."
    assert llama_cpp_status["reason"] != "Provider type is not executable by the local control plane.", (
        f"llama_cpp no debe caer en la rama else de provider type. reason={llama_cpp_status['reason']}"
    )


def test_llama_cpp_from_catalog_is_healthy_executable_and_admissible_inside_a_cli_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
    from local_control_center.host_resources.repository import ResourceRepository
    from local_control_center.process_supervision import session_client
    from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
    from tests_py.fakes.local_llm_servers import running_llama_router

    monkeypatch.setattr(session_client, "session_identity", lambda _path: None)
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    database = Path(os.environ["LOCAL_CONTROL_CENTER_DB"])
    with running_llama_router() as (root, _router):
        created = client.post(
            "/api/v1/provider-accounts/from-catalog",
            headers=headers,
            json={"providerId": "llama_cpp", "baseUrl": f"{root}/v1", "enabled": True},
        )
        assert created.status_code == 201, created.text
        health = client.post("/api/v1/model-gateway/providers/llama_cpp/health-check", headers=headers)
        assert health.json()["health"]["healthStatus"] == "healthy", health.text
        synced = client.post("/api/v1/provider-accounts/llama_cpp/sync-models", headers=headers)
        assert synced.status_code == 200, synced.text
    with closing(open_sqlite_connection(database)) as connection:
        governor = HostResourceGovernor(connection)
        lease = governor.admit(
            ResourceAdmissionRequest(execution_id="po-job", owner_id="worker", workload_class="agent_cli"),
            snapshot=ResourceSnapshot.test_snapshot(),
        ).lease
        assert lease is not None
        ResourceRepository(connection).record_sample(
            ResourceSnapshot.test_snapshot(available_memory_bytes=20 * 1024**3)
        )
        try:
            with execution_scope(
                ProcessExecutionContext(
                    db_path=database,
                    connection=connection,
                    execution_id="po-job",
                    worker_id="worker",
                    resource_lease_id=lease.id,
                    in_job_runner=True,
                )
            ):
                statuses = RuntimeStatusService(connection, probe_runtime_ids=set()).list_provider_statuses()
        finally:
            governor.release(lease.id, reason="test cleanup")
    status = next(item for item in statuses if item["id"] == "llama_cpp")
    assert status["healthy"] is True
    assert status["resourceAdmissible"] is True, status["blockingReasons"]
    assert status["executable"] is True, status["blockingReasons"]
    assert status["localModelRuntime"] is True
    assert "gemma-4-26b-a4b" in status["models"]
