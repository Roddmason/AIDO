"""La prueba de runtime es una operación encolada por runtime con su propia clase de carga.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.executions.router import OperationSpec
from local_control_center.executions.workloads import INFERENCE_OPERATIONS, operation_workload
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_team import probe
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_provider_setup_catalog import auth_headers, create_client, enable_remote_provider


def _db():
    return closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])))


def test_validate_runtime_is_an_inference_operation() -> None:
    assert "models.validate_runtime" in INFERENCE_OPERATIONS


def test_each_validation_reserves_the_workload_of_its_own_runtime(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "ollama", {"enabled": True, "baseUrl": "http://127.0.0.1:11434"}
        )
        spec = OperationSpec("models.validate_runtime", "remote_llm_light")
        assert operation_workload(connection, spec, {"provider_id": "codex_cli", "body": {}}) == "agent_cli"
        assert (
            operation_workload(connection, spec, {"provider_id": "ollama", "body": {}}) == "local_model_call"
        )


def test_validate_runtime_probes_a_model_provider_through_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_TEAM_TEST_KEY", "unit-test-team-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        "deepseek",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_TEAM_TEST_KEY",
    )
    with _db() as connection, connection:
        ProviderAccountStore(connection).upsert_model(
            {"providerId": "deepseek", "model": "deepseek-chat", "enabled": True}
        )
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: SimpleNamespace(
            chat_completion=lambda request: ModelResponse(
                providerId="deepseek", model=request.model, content="ok", usage=UsageRecord()
            )
        ),
    )
    response = client.post(
        "/api/v1/model-gateway/providers/deepseek/validate-runtime", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert validation["status"] == "validated"
    assert validation["model"]
    assert "unit-test-team-key" not in response.text


def test_validate_runtime_rejects_unknown_and_manual_runtimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    unknown = client.post("/api/v1/model-gateway/providers/nope/validate-runtime", json={}, headers=headers)
    manual = client.post("/api/v1/model-gateway/providers/manual/validate-runtime", json={}, headers=headers)
    assert unknown.status_code == 404
    assert manual.status_code == 422


def test_cli_validation_without_a_project_is_deferred_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _db() as connection, connection:
        ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    response = client.post(
        "/api/v1/model-gateway/providers/codex_cli/validate-runtime", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert (validation["status"], validation["reason"]) == ("deferred", "project_required_for_cli_validation")


def test_a_validated_probe_resumes_runs_blocked_on_that_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_TEAM_TEST_KEY", "unit-test-team-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        "deepseek",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_TEAM_TEST_KEY",
    )
    with _db() as connection, connection:
        ProviderAccountStore(connection).upsert_model(
            {"providerId": "deepseek", "model": "deepseek-chat", "enabled": True}
        )
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: SimpleNamespace(
            chat_completion=lambda request: ModelResponse(
                providerId="deepseek", model=request.model, content="ok", usage=UsageRecord()
            )
        ),
    )
    resumed: list[str] = []
    monkeypatch.setattr(
        BlockerRemediationService,
        "resume_after_runtime_validation",
        lambda self, provider_id: resumed.append(provider_id) or [],
    )
    response = client.post(
        "/api/v1/model-gateway/providers/deepseek/validate-runtime", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert resumed == ["deepseek"]


def test_a_failed_resume_does_not_turn_a_validated_probe_into_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_TEAM_TEST_KEY", "unit-test-team-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        "deepseek",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_TEAM_TEST_KEY",
    )
    with _db() as connection, connection:
        ProviderAccountStore(connection).upsert_model(
            {"providerId": "deepseek", "model": "deepseek-chat", "enabled": True}
        )
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: SimpleNamespace(
            chat_completion=lambda request: ModelResponse(
                providerId="deepseek", model=request.model, content="ok", usage=UsageRecord()
            )
        ),
    )

    def broken_resume(self, provider_id):
        raise RuntimeError("resume exploded")

    monkeypatch.setattr(BlockerRemediationService, "resume_after_runtime_validation", broken_resume)
    response = client.post(
        "/api/v1/model-gateway/providers/deepseek/validate-runtime", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["status"] == "validated"
