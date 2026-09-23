"""Frescura de 30 minutos y prueba real de runtimes para el equipo del hilo.

La frescura se lee de la misma evidencia de ejecución que usa la validación de 24 h; la prueba real
reusa el prompt fijo de test-prompt (API/local) y el preflight CLI con la aprobación explícita del
operador. Los CLIs se simulan en el borde del proceso: ``validate_cli_candidate`` y el estado de runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from local_control_center.agents import model_gateway_api
from local_control_center.agents.model_execution_health import (
    VALIDATION_TTL_SECONDS,
    model_validation_rejection,
    record_model_execution,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.agents.providers.factory import ProviderAdapterResolutionError
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_team import probe
from local_control_center.runtime_team.probe import OPERATOR_APPROVAL_AUDIT_ACTION, RuntimeValidationService
from local_control_center.runtime_team.validation import (
    RUNTIME_TEAM_FRESHNESS_SECONDS,
    runtime_validated_within,
    runtime_validation_state,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

API_PROVIDER = "openai_compatible"


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        yield handle


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def _state(connection, provider_id: str, seconds: int = RUNTIME_TEAM_FRESHNESS_SECONDS):
    return runtime_validation_state(connection, provider_id, max_age_seconds=seconds)


class _Provider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def chat_completion(self, request):
        if self.error is not None:
            raise self.error
        return ModelResponse(providerId="ollama", model=request.model, content="ok", usage=UsageRecord())


class _Statuses:
    def __init__(self, connection, **kwargs) -> None:
        self.kwargs = kwargs

    def list_provider_statuses(self, *, project_id=None):
        return [{"id": "codex_cli", "kind": "cli", "detectedCommand": "codex"}]


def test_a_never_validated_runtime_reports_never(connection):
    assert _state(connection, API_PROVIDER).status == "never"
    assert runtime_validated_within(connection, API_PROVIDER) is False


def test_a_recent_success_is_validated_with_latency(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(1))
    state = _state(connection, API_PROVIDER)
    assert state.status == "validated"
    assert state.model == "m"
    assert state.latency_ms is not None and state.latency_ms >= 0
    assert runtime_validated_within(connection, API_PROVIDER) is True


def test_a_success_older_than_thirty_minutes_is_stale_but_inside_the_daily_window(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(31))
    assert (_state(connection, API_PROVIDER).status, _state(connection, API_PROVIDER).reason) == (
        "stale",
        "runtime_validation_expired",
    )
    assert _state(connection, API_PROVIDER, VALIDATION_TTL_SECONDS).status == "validated"


def test_a_configuration_change_after_validation_makes_it_stale(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(1))
    ProviderAccountStore(connection).patch_provider_account(
        API_PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
    )
    state = _state(connection, API_PROVIDER)
    assert (state.status, state.reason) == ("stale", "runtime_validation_configuration_changed")


def test_a_later_failure_invalidates_a_fresh_success(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(2))
    record_model_execution(connection, API_PROVIDER, "m", False, "tool_broker", started_at=_ago(1))
    assert _state(connection, API_PROVIDER).status == "failed"


def test_a_configuration_change_after_a_failure_makes_it_stale(connection):
    record_model_execution(connection, API_PROVIDER, "m", False, "test_prompt", started_at=_ago(1))
    ProviderAccountStore(connection).patch_provider_account(
        API_PROVIDER, {"baseUrl": "https://fixed.example.invalid/v1"}
    )
    state = _state(connection, API_PROVIDER)
    assert (state.status, state.reason) == ("stale", "runtime_validation_configuration_changed")


def test_model_runtime_probe_records_fresh_evidence(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    monkeypatch.setattr(probe, "provider_instance", lambda provider_id, *, connection: _Provider())
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert result["status"] == "validated"
    assert result["model"] == "local_default"
    assert runtime_validated_within(connection, "ollama") is True


def test_a_failed_probe_invalidates_the_previous_validation(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt", started_at=_ago(1))
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _Provider(error=RuntimeError("daemon down")),
    )
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", "runtime_validation_failed")
    assert "daemon down" in result["evidence"]
    assert _state(connection, "ollama").status == "failed"
    assert model_validation_rejection(connection, "ollama", "local_default") == "model_validation_failed"


def test_a_failed_probe_is_recorded_despite_a_concurrent_success_of_another_model(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt", started_at=_ago(1))
    monkeypatch.setattr(probe, "provider_instance", lambda provider_id, *, connection: _Provider())

    def concurrent_success_then_unrecorded_failure(provider_id, model, *, connection, provider):
        record_model_execution(connection, provider_id, "other_model", True, "tool_broker")
        return {"ok": False, "error": "boom", "latencyMs": 5}

    monkeypatch.setattr(
        model_gateway_api, "_run_provider_test_prompt", concurrent_success_then_unrecorded_failure
    )
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert result["status"] == "failed"
    assert model_validation_rejection(connection, "ollama", "local_default") == "model_validation_failed"


def test_a_failed_probe_reports_the_shared_classifier_cause(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _Provider(error=ConnectionRefusedError("connection refused")),
    )
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", "provider_unreachable")
    assert "connection refused" in result["evidence"]


def _break_model(connection, monkeypatch):
    connection.execute("UPDATE model_catalog SET enabled = 0 WHERE provider_id = 'ollama'")


def _break_adapter(connection, monkeypatch):
    def _raise(provider_id, *, connection):
        raise ProviderAdapterResolutionError("adapter unavailable")

    monkeypatch.setattr(probe, "provider_instance", _raise)


def _break_credentials(connection, monkeypatch):
    def _raise(account):
        raise HTTPException(status_code=400, detail="Credential ref env:MISSING is missing.")

    monkeypatch.setattr(model_gateway_api, "_validate_real_discovery_credentials", _raise)


@pytest.mark.parametrize(
    ("breakage", "reason"),
    [
        (_break_model, "model_required"),
        (_break_adapter, "provider_adapter_resolution_failed"),
        (_break_credentials, "credential_invalid"),
    ],
)
def test_every_early_failure_invalidates_a_fresh_success(connection, monkeypatch, breakage, reason):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt", started_at=_ago(1))
    breakage(connection, monkeypatch)
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", reason)
    assert _state(connection, "ollama").status == "failed"


def test_a_cli_without_runtime_status_invalidates_a_fresh_success(connection, tmp_path, monkeypatch):
    project = ProjectsRepository(connection).create_project(
        name="Runtime team", path=tmp_path / "project", template_id="other"
    )
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli' AND model = 'gpt-5.5'"
    )
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt", started_at=_ago(1))

    class _NoStatuses(_Statuses):
        def list_provider_statuses(self, *, project_id=None):
            return []

    monkeypatch.setattr(probe, "RuntimeStatusService", _NoStatuses)
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=project["id"])
    assert (result["status"], result["reason"]) == ("failed", "runtime_status_unavailable")
    assert _state(connection, "codex_cli").status == "failed"


def test_a_failure_without_any_prior_evidence_leaves_the_runtime_never_validated(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    _break_model(connection, monkeypatch)
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert result["status"] == "failed"
    assert _state(connection, "ollama").status == "never"


def test_a_disabled_runtime_is_reported_without_calling_it(connection, monkeypatch):
    monkeypatch.setattr(probe, "provider_instance", lambda *args, **kwargs: pytest.fail("must not call"))
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", "provider_disabled")


def test_manual_and_unknown_runtimes_are_rejected(connection):
    service = RuntimeValidationService(connection)
    with pytest.raises(ValueError):
        service.validate("manual", project_id=None)
    with pytest.raises(KeyError):
        service.validate("does_not_exist", project_id=None)


def test_cli_probe_is_operator_approved_and_audited(connection, tmp_path, monkeypatch):
    project = ProjectsRepository(connection).create_project(
        name="Runtime team", path=tmp_path / "project", template_id="other"
    )
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli' AND model = 'gpt-5.5'"
    )
    captured: dict = {}

    def fake_validate(conn, *, model, runtime_status, request):
        captured.update(model=model, runtime_status=runtime_status, request=request)
        record_model_execution(conn, "codex_cli", model["model"], True, "test_prompt")
        return {"status": "completed", "success": True, "attempted": True, "reason": None}

    monkeypatch.setattr(probe, "RuntimeStatusService", _Statuses)
    monkeypatch.setattr(probe, "validate_cli_candidate", fake_validate)
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=project["id"])

    assert result["status"] == "validated"
    request = captured["request"]
    assert request.allow_unknown_cost is True
    assert request.require_approval_for_unknown_cost is False
    assert request.project_id == project["id"]
    assert request.allowed_provider_ids == ["codex_cli"]
    assert captured["model"] == {"providerId": "codex_cli", "model": "gpt-5.5"}
    audit = connection.execute(
        "SELECT project_id FROM audit_events WHERE action = ? AND target = ?",
        (OPERATOR_APPROVAL_AUDIT_ACTION, "codex_cli"),
    ).fetchone()
    assert audit is not None and audit["project_id"] == project["id"]
    assert runtime_validated_within(connection, "codex_cli") is True


def test_cli_probe_without_a_project_is_deferred(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    monkeypatch.setattr(probe, "validate_cli_candidate", lambda *args, **kwargs: pytest.fail("no project"))
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=None)
    assert (result["status"], result["reason"]) == ("deferred", "project_required_for_cli_validation")


def test_cli_probe_deferred_by_the_preflight_keeps_its_reason(connection, tmp_path, monkeypatch):
    project = ProjectsRepository(connection).create_project(
        name="Runtime team", path=tmp_path / "project", template_id="other"
    )
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli' AND model = 'gpt-5.5'"
    )
    monkeypatch.setattr(probe, "RuntimeStatusService", _Statuses)
    monkeypatch.setattr(
        probe,
        "validate_cli_candidate",
        lambda *args, **kwargs: {
            "status": "deferred",
            "success": False,
            "attempted": False,
            "reason": "preflight_requires_execution_context",
        },
    )
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=project["id"])
    assert (result["status"], result["reason"]) == ("deferred", "preflight_requires_execution_context")
