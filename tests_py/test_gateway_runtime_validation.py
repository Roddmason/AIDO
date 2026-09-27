"""Validación de gateways de auto-ruteo (OmniRoute): elección del modelo, estado por modelo y allowlist.

Un gateway anuncia modelos upstream sin cuenta del operador. Estas pruebas fijan que la validación no
depende del primero alfabético (prueba candidatos acotados y registra cada intento), que la falla de un
modelo no oculta el éxito de otro (candidatos, gate del equipo y reanudación), que el sync preselecciona
solo la allowlist curada y que el flujo completo sync → validar deja al gateway elegible para el equipo.

@author Rodrigo Mason
"""

from __future__ import annotations

import io
import json
import os
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import local_control_center.executions.inputs as execution_inputs
from local_control_center.agents import gateway_model_allowlist
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team import probe
from local_control_center.runtime_team.candidates import RuntimeTeamCandidatesService
from local_control_center.runtime_team.configuration import assess_runtime_team
from local_control_center.runtime_team.global_team import describe_global_team
from local_control_center.runtime_team.probe import MAX_AUTO_VALIDATION_ATTEMPTS, RuntimeValidationService
from local_control_center.runtime_team.validation import (
    RUNTIME_TEAM_FRESHNESS_SECONDS,
    account_validation_state,
)
from local_control_center.shared.db import open_sqlite_connection
from tests_py.test_provider_setup_catalog import auth_headers, create_client, enable_remote_provider

UNAVAILABLE_UPSTREAM = "cc/claude-x"
ALLOWLISTED = ("oc/big-pickle", "oc/deepseek-v4-flash-free")


class _MemoryKeyring:
    """Backend en memoria para las entradas seguras de operaciones encoladas (sin keyring del host)."""

    values: dict[str, str] = {}

    def write(self, key: str, value: str) -> None:
        self.values[key] = value

    def read(self, key: str) -> str | None:
        return self.values.get(key)

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture(autouse=True)
def _memory_operation_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution_inputs, "KeyringBackend", _MemoryKeyring)
    monkeypatch.setattr(
        BlockerRemediationService, "resume_after_runtime_validation", lambda self, provider_id: []
    )


def _db():
    return closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])))


def _http_400(model: str) -> HTTPError:
    body = json.dumps({"error": {"message": f"No active credentials for provider: {model.split('/')[0]}"}})
    return HTTPError("http://gateway/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body.encode()))


def _fake_provider(provider_id: str, failing: set[str], calls: list[str]):
    def chat_completion(request):
        calls.append(request.model)
        if request.model in failing:
            raise _http_400(request.model)
        return ModelResponse(providerId=provider_id, model=request.model, content="ok", usage=UsageRecord())

    return SimpleNamespace(chat_completion=chat_completion)


def _deepseek_with_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, models: list[str]):
    monkeypatch.setenv("AIDO_GATEWAY_TEST_KEY", "unit-test-gateway-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        "deepseek",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_GATEWAY_TEST_KEY",
    )
    with _db() as connection, connection:
        store = ProviderAccountStore(connection)
        for existing in store.list_models("deepseek"):
            store.upsert_model({**existing, "enabled": False})
        for model in models:
            store.upsert_model({"providerId": "deepseek", "model": model, "enabled": True})
    return client, headers


def _evidence(connection, provider_id: str) -> list[tuple[str, bool]]:
    rows = connection.execute(
        "SELECT model, success FROM model_execution_health WHERE provider_id = ? ORDER BY id",
        (provider_id,),
    ).fetchall()
    return [(str(row["model"]), bool(row["success"])) for row in rows]


def test_auto_validation_skips_a_failing_first_model_and_records_every_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin modelo pedido, un 400 del primer alfabético no deja al runtime ``failed``: prueba el siguiente."""
    _deepseek_with_models(tmp_path, monkeypatch, ["a/unavailable", "b/works", "c/unused"])
    calls: list[str] = []
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _fake_provider(provider_id, {"a/unavailable"}, calls),
    )
    with _db() as connection, connection:
        result = RuntimeValidationService(connection).validate("deepseek")
        evidence = _evidence(connection, "deepseek")
    assert (result["status"], result["model"]) == ("validated", "b/works")
    assert calls == ["a/unavailable", "b/works"]
    assert [(item["model"], item["status"]) for item in result["attempts"]] == [
        ("a/unavailable", "failed"),
        ("b/works", "validated"),
    ]
    # La causa del gateway viaja en la evidencia del intento fallido, no solo "HTTP Error 400".
    assert "No active credentials for provider: a" in result["attempts"][0]["evidence"]
    assert evidence == [("a/unavailable", False), ("b/works", True)]


def test_auto_validation_prefers_the_last_validated_model_then_role_policy_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deepseek_with_models(tmp_path, monkeypatch, ["a/first", "b/policy", "c/validated", "d/other"])
    with _db() as connection, connection:
        record_model_execution(connection, "deepseek", "c/validated", True, "test_prompt")
        policy = RoutingProfileStore(connection).list_role_policies()[0]
        RoutingProfileStore(connection).patch_role_policy(
            policy["id"],
            {
                "preferred": [
                    {"provider": "deepseek", "model": "*"},
                    {"provider": "deepseek", "model": "b/policy"},
                ]
            },
        )
        service = RuntimeValidationService(connection)
        account = service.accounts.get_provider_account("deepseek")
        ordered = service._models_to_validate(account, requested=None)
    assert ordered == ["c/validated", "b/policy", "a/first"]
    assert len(ordered) == MAX_AUTO_VALIDATION_ATTEMPTS


def test_auto_validation_is_capped_and_reports_the_last_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = ["a/x", "b/x", "c/x", "d/x"]
    _deepseek_with_models(tmp_path, monkeypatch, models)
    calls: list[str] = []
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _fake_provider(provider_id, set(models), calls),
    )
    with _db() as connection, connection:
        result = RuntimeValidationService(connection).validate("deepseek")
    assert result["status"] == "failed"
    assert calls == models[:MAX_AUTO_VALIDATION_ATTEMPTS]
    assert [item["status"] for item in result["attempts"]] == ["failed"] * MAX_AUTO_VALIDATION_ATTEMPTS


def test_a_requested_model_is_probed_alone_without_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = _deepseek_with_models(tmp_path, monkeypatch, ["a/unavailable", "b/works"])
    calls: list[str] = []
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _fake_provider(provider_id, {"a/unavailable"}, calls),
    )
    response = client.post(
        "/api/v1/model-gateway/providers/deepseek/validate-runtime",
        json={"model": "a/unavailable"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert (validation["status"], validation["model"], validation["attempts"]) == (
        "failed",
        "a/unavailable",
        None,
    )
    assert calls == ["a/unavailable"]


def test_a_failure_on_one_model_does_not_mask_another_models_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El runtime vale por cualquier modelo habilitado validado: candidatos, gate del equipo y reanudación."""
    _deepseek_with_models(tmp_path, monkeypatch, ["a/good", "b/bad"])
    with _db() as connection, connection:
        record_model_execution(connection, "deepseek", "a/good", True, "test_prompt")
        later = (datetime.now(UTC) + timedelta(seconds=1)).isoformat(timespec="microseconds")
        record_model_execution(connection, "deepseek", "b/bad", False, "test_prompt", started_at=later)
        state = account_validation_state(
            connection, "deepseek", max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
        )
        team = {
            "allowedRuntimes": ["deepseek"],
            "roleRuntimes": {"product_owner": "deepseek", "developer": "deepseek"},
        }
        readiness = assess_runtime_team(connection, team, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS)
        candidates = RuntimeTeamCandidatesService(connection).list_candidates(project_id=None, selected=None)
        stale = BlockerRemediationService(connection)._stale_validation_targets({"runtimeIds": ["deepseek"]})
    assert (state.status, state.model) == ("validated", "a/good")
    assert readiness.stale_runtimes == ()
    deepseek = next(item for item in candidates["candidates"] if item["providerId"] == "deepseek")
    assert deepseek["validation"]["status"] == "validated"
    assert stale == []


def test_evidence_of_a_disabled_model_does_not_validate_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deepseek_with_models(tmp_path, monkeypatch, ["b/enabled"])
    with _db() as connection, connection:
        record_model_execution(connection, "deepseek", "a/disabled", True, "test_prompt")
        state = account_validation_state(
            connection, "deepseek", max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
        )
    assert state.status == "never"


class _OmniRouteHandler(BaseHTTPRequestHandler):
    """OmniRoute falso: anuncia un upstream sin cuenta (rechaza chat con 400) y dos modelos curados."""

    announced: tuple[str, ...] = (UNAVAILABLE_UPSTREAM, *ALLOWLISTED, "auto/free")

    def log_message(self, *_args: object) -> None:
        return

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.endswith("/models"):
            self._send(200, {"data": [{"id": model} for model in self.announced]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        model = str(body.get("model"))
        if model == UNAVAILABLE_UPSTREAM:
            self._send(400, {"error": {"message": "No active credentials for provider: cc"}})
            return
        self._send(
            200,
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


@contextmanager
def _omniroute_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _OmniRouteHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


def _create_omniroute(client, headers, base_url: str) -> None:
    with _db() as connection, connection:
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
    created = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "omniroute",
            "baseUrl": base_url,
            "enabled": True,
            "metadata": {"endpointKind": "remote", "gateway": "omniroute"},
        },
    )
    assert created.status_code == 201, created.text


def _omniroute_rows(client) -> dict[str, dict[str, object]]:
    models = client.get("/api/v1/model-gateway/models").json()["models"]
    return {str(row["model"]): row for row in models if row["providerId"] == "omniroute"}


def test_omniroute_sync_preselects_only_the_curated_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _omniroute_server() as base_url:
        _create_omniroute(client, headers, base_url)
        synced = client.post("/api/v1/provider-accounts/omniroute/sync-models", headers=headers)
        assert synced.status_code == 200, synced.text
        # El operador habilita a mano un upstream fuera de la lista: un resync respeta su elección.
        patched = client.patch(
            f"/api/v1/model-gateway/models/omniroute:{UNAVAILABLE_UPSTREAM}",
            headers=headers,
            json={"enabled": True},
        )
        assert patched.status_code == 200, patched.text
        resynced = client.post("/api/v1/provider-accounts/omniroute/sync-models", headers=headers)
        assert resynced.status_code == 200, resynced.text
    rows = _omniroute_rows(client)
    assert {model: bool(row["enabled"]) for model, row in rows.items()} == {
        UNAVAILABLE_UPSTREAM: True,
        ALLOWLISTED[0]: True,
        ALLOWLISTED[1]: True,
    }
    first_sync = {row["model"]: row["enabled"] for row in synced.json()["models"]}
    assert first_sync == {UNAVAILABLE_UPSTREAM: False, ALLOWLISTED[0]: True, ALLOWLISTED[1]: True}
    # Los metadatos curados de la allowlist llegan a la fila (el gateway no los informa).
    assert rows["oc/deepseek-v4-flash-free"]["supportsReasoning"] is True
    assert rows["oc/deepseek-v4-flash-free"]["contextWindow"] == 131072


def test_omniroute_sync_without_a_readable_allowlist_enables_what_the_gateway_announces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        gateway_model_allowlist.GATEWAY_MODEL_ALLOWLIST_FILES, "omniroute", tmp_path / "missing.json"
    )
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _omniroute_server() as base_url:
        _create_omniroute(client, headers, base_url)
        synced = client.post("/api/v1/provider-accounts/omniroute/sync-models", headers=headers)
    assert synced.status_code == 200, synced.text
    assert all(row["enabled"] for row in synced.json()["models"])


def test_parse_model_allowlist_rejects_documents_without_models() -> None:
    with pytest.raises(ValueError):
        gateway_model_allowlist.parse_model_allowlist({"models": []})
    with pytest.raises(ValueError):
        gateway_model_allowlist.parse_model_allowlist({"models": [{"notes": "sin modelo"}]})
    assert gateway_model_allowlist.parse_model_allowlist({"models": [{"model": " oc/x "}]}) == [
        {"model": "oc/x"}
    ]


def test_omniroute_end_to_end_sync_validate_and_join_the_global_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync → validar sin modelo → candidato validado y roles del equipo global, aun con un upstream roto."""
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _omniroute_server() as base_url:
        _create_omniroute(client, headers, base_url)
        health = client.post("/api/v1/model-gateway/providers/omniroute/health-check", headers=headers)
        assert health.status_code == 200, health.text
        assert (
            client.post("/api/v1/provider-accounts/omniroute/sync-models", headers=headers).status_code == 200
        )
        # Peor caso del reporte: el upstream sin cuenta, primero alfabético, queda habilitado.
        client.patch(
            f"/api/v1/model-gateway/models/omniroute:{UNAVAILABLE_UPSTREAM}",
            headers=headers,
            json={"enabled": True},
        )
        validated = client.post(
            "/api/v1/model-gateway/providers/omniroute/validate-runtime", headers=headers, json={}
        )
        # Una revalidación posterior arranca por el modelo ya validado y no vuelve a tocar el roto.
        revalidated = client.post(
            "/api/v1/model-gateway/providers/omniroute/validate-runtime", headers=headers, json={}
        )
    assert validated.status_code == 200, validated.text
    validation = validated.json()["validation"]
    assert validation["status"] == "validated"
    assert validation["model"] == ALLOWLISTED[0]
    attempts = [(item["model"], item["status"]) for item in validation["attempts"]]
    assert attempts == [(UNAVAILABLE_UPSTREAM, "failed"), (ALLOWLISTED[0], "validated")]
    assert "No active credentials for provider: cc" in validation["attempts"][0]["evidence"]
    assert [item["model"] for item in revalidated.json()["validation"]["attempts"]] == [ALLOWLISTED[0]]

    with _db() as connection:
        team = describe_global_team(connection, project_id=None)
    omniroute = next(item for item in team["candidates"] if item["providerId"] == "omniroute")
    assert omniroute["validation"]["status"] == "validated"
    assert {"product_owner", "developer"} <= set(omniroute["eligibleRoles"])
    effective = {role["role"]: role["effective"] for role in team["roles"]}
    assert "omniroute" in effective["product_owner"]
    assert "omniroute" in effective["developer"]
    with _db() as connection:
        readiness = assess_runtime_team(
            connection,
            {
                "allowedRuntimes": ["omniroute"],
                "roleRuntimes": {"product_owner": "omniroute", "developer": "omniroute"},
            },
            max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
            only_assigned=True,
        )
    assert readiness.ready, readiness.reason()
