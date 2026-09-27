"""Validación modelo por modelo de un gateway (``models.validate_all_models``) contra un gateway falso.

El operador pidió: con múltiples modelos, probar uno por uno; permitir los que responden, descartar los
que fallan, y no descartar el proveedor si tiene otros operativos. Un ``http.server`` local responde como
OmniRoute por prefijo de upstream (400/401/404 definitivos, 429/503 transitorios, lentos, ok). Fija que el
proveedor queda validado con fallas parciales, que los fallidos quedan deshabilitados con su causa (sin
borrarse), que los transitorios no se descartan, que un resync no revive un descartado, que re-habilitar y
volver a probar funcionan, y que la corrida respeta cancelación, presupuesto, switch y cuota.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import local_control_center.executions.inputs as execution_inputs
from local_control_center.agents.model_execution_health import (
    model_validation_rejection,
    provider_authentication_failure,
    provider_configuration_fingerprint,
    record_model_execution,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team import catalog_validation
from local_control_center.runtime_team.candidates import RuntimeTeamCandidatesService
from local_control_center.runtime_team.catalog_validation import (
    CatalogValidationLimits,
    CatalogValidationService,
    classify_outcome,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import CURRENT_SCHEMA_VERSION, init_phase85_schema
from tests_py.test_gateway_runtime_validation import _MemoryKeyring
from tests_py.test_provider_setup_catalog import auth_headers, create_client

PROVIDER = "omniroute"
#: Respuesta por upstream (prefijo del modelo): estado HTTP y cuerpo.
UPSTREAMS: dict[str, tuple[int, dict[str, object]]] = {
    "anthropic": (400, {"error": {"message": "No active credentials for provider: anthropic"}}),
    "bedrock": (401, {"error": {"message": "Invalid API key for upstream bedrock", "code": 401}}),
    "cohere": (404, {"error": {"message": "Model not available on this gateway"}}),
    "deepinfra": (429, {"error": {"message": "Upstream quota exceeded, retry later"}}),
    "unavailable": (503, {"error": {"message": "All upstream accounts are cooling down"}}),
}
MIXED = [
    "anthropic/a1",
    "anthropic/a2",
    "bedrock/b1",
    "cohere/c1",
    "deepinfra/d1",
    "unavailable/u1",
    "ok/one",
    "ok/two",
    "zz-free/working",
]
DEFINITIVE = ["anthropic/a1", "anthropic/a2", "bedrock/b1", "cohere/c1"]
TRANSIENT = ["deepinfra/d1", "unavailable/u1"]
PASSING = ["ok/one", "ok/two", "zz-free/working"]


def _completion() -> dict[str, object]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": "upstream-resolved",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    }


class _Gateway(BaseHTTPRequestHandler):
    """Gateway falso: ``/models`` lista ``catalog``; ``/chat/completions`` responde según el upstream."""

    catalog: list[str] = []
    requests: list[str] = []
    #: Upstreams que dejaron de fallar (el operador arregló la cuenta): vuelven a responder ok.
    repaired: set[str] = set()
    lock = threading.Lock()

    def log_message(self, *_args: object) -> None:
        return

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        self._send(200, {"data": [{"id": model} for model in type(self).catalog]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        model = str(body.get("model"))
        with type(self).lock:
            type(self).requests.append(model)
        upstream = model.split("/", 1)[0]
        if upstream in type(self).repaired:
            self._send(200, _completion())
        elif upstream in UPSTREAMS:
            status, payload = UPSTREAMS[upstream]
            self._send(status, payload)
        elif upstream == "slow":
            time.sleep(1.5)
            self._send(200, _completion())
        else:
            self._send(200, _completion())


@contextmanager
def _gateway(catalog: list[str]) -> Iterator[str]:
    _Gateway.catalog = list(catalog)
    _Gateway.requests = []
    _Gateway.repaired = set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _memory_operation_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution_inputs, "KeyringBackend", _MemoryKeyring)
    monkeypatch.setattr(
        BlockerRemediationService, "resume_after_runtime_validation", lambda self, provider_id: []
    )


def _db():
    return closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])))


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_url: str, models: list[str]):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _db() as connection, connection:
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.remote.enabled", True)
    created = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": PROVIDER,
            "baseUrl": base_url,
            "enabled": True,
            "metadata": {"endpointKind": "remote", "gateway": "omniroute"},
        },
    )
    assert created.status_code == 201, created.text
    with _db() as connection, connection:
        store = ProviderAccountStore(connection)
        for model in models:
            store.upsert_model({"providerId": PROVIDER, "model": model, "enabled": True})
    return client, headers


def _validate_all(client, headers, body: dict[str, object] | None = None):
    return client.post(
        f"/api/v1/model-gateway/providers/{PROVIDER}/validate-all-models", headers=headers, json=body or {}
    )


def _catalog_rows() -> dict[str, dict[str, object]]:
    with _db() as connection:
        return {str(row["model"]): row for row in ProviderAccountStore(connection).list_models(PROVIDER)}


def _provider_candidate() -> dict[str, object]:
    with _db() as connection:
        candidates = RuntimeTeamCandidatesService(connection).list_candidates(project_id=None, selected=None)
    return next(item for item in candidates["candidates"] if item["providerId"] == PROVIDER)


def test_mixed_results_keep_the_provider_validated_and_discard_only_definitive_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _gateway(MIXED) as base_url:
        client, headers = _setup(tmp_path, monkeypatch, base_url, MIXED)
        response = _validate_all(client, headers)
        assert response.status_code == 200, response.text
        result = response.json()
        requested = sorted(_Gateway.requests)
    assert requested == sorted(MIXED)  # cada modelo habilitado se probó una vez
    run = result["run"]
    assert (run["status"], run["reason"]) == ("completed", "")
    assert (run["total"], run["done"], run["ok"], run["failed"], run["skipped"], run["discarded"]) == (
        9,
        9,
        3,
        4,
        2,
        4,
    )
    by_model = {item["model"]: item for item in result["outcomes"]}
    assert {model for model, item in by_model.items() if item["status"] == "ok"} == set(PASSING)
    assert {model for model, item in by_model.items() if item["status"] == "failed"} == set(DEFINITIVE)
    assert {model for model, item in by_model.items() if item["status"] == "skipped"} == set(TRANSIENT)
    assert by_model["bedrock/b1"]["httpStatus"] == 401
    assert "Invalid API key for upstream bedrock" in by_model["bedrock/b1"]["detail"]
    assert by_model["cohere/c1"]["detail"].startswith("HTTP 404")
    assert all(by_model[model]["discarded"] and not by_model[model]["enabled"] for model in DEFINITIVE)
    assert all(by_model[model]["enabled"] and not by_model[model]["discarded"] for model in TRANSIENT)

    rows = _catalog_rows()
    assert len(rows) == len(MIXED)  # nada se borra
    for model in DEFINITIVE:
        assert (rows[model]["enabled"], rows[model]["disabledReason"]) == (False, "validation_failed")
        assert rows[model]["disabledDetail"]
    for model in [*TRANSIENT, *PASSING]:
        assert (rows[model]["enabled"], rows[model]["disabledReason"]) == (True, None)

    # El proveedor sigue validado y enrutable por sus modelos ok, aunque otros upstreams dieron 401.
    assert result["providerValidation"]["status"] == "validated"
    assert _provider_candidate()["validation"]["status"] == "validated"
    with _db() as connection:
        for model in PASSING:
            assert model_validation_rejection(connection, PROVIDER, model) is None
        assert model_validation_rejection(connection, PROVIDER, "bedrock/b1") == "model_disabled"
        assert model_validation_rejection(connection, PROVIDER, "deepinfra/d1") == "model_validation_failed"

    status = client.get(f"/api/v1/model-gateway/providers/{PROVIDER}/model-validation").json()
    assert status["run"]["runId"] == run["runId"]
    assert status["run"]["status"] == "completed"
    assert status["untested"] == 0
    assert len(status["outcomes"]) == len(MIXED)
    assert status["providerValidation"]["status"] == "validated"


def test_a_resync_does_not_resurrect_discarded_models_and_reenable_or_retest_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _gateway(MIXED) as base_url:
        client, headers = _setup(tmp_path, monkeypatch, base_url, MIXED)
        assert _validate_all(client, headers).status_code == 200
        synced = client.post(f"/api/v1/provider-accounts/{PROVIDER}/sync-models", headers=headers)
        assert synced.status_code == 200, synced.text
        with _db() as connection, connection:
            ProviderAccountStore(connection).upsert_model(
                {"providerId": PROVIDER, "model": "cohere/c1", "enabled": True},
                preserve_operator_enabled=True,
            )
        rows = _catalog_rows()
        for model in DEFINITIVE:
            assert (rows[model]["enabled"], rows[model]["disabledReason"]) == (False, "validation_failed")

        # Re-habilitar a mano retira la marca de descarte.
        reenabled = client.patch(
            f"/api/v1/model-gateway/providers/{PROVIDER}/models",
            headers=headers,
            json={"enabled": True, "models": [rows["cohere/c1"]["id"]]},
        )
        assert reenabled.status_code == 200, reenabled.text
        assert (_catalog_rows()["cohere/c1"]["enabled"], _catalog_rows()["cohere/c1"]["disabledReason"]) == (
            True,
            None,
        )

        # Volver a probar un descartado cuyo upstream ya tiene cuenta lo habilita de nuevo.
        _Gateway.repaired.add("anthropic")
        retest = _validate_all(client, headers, {"models": [rows["anthropic/a1"]["id"]]})
        assert retest.status_code == 200, retest.text
        assert retest.json()["run"]["ok"] == 1
        # Uno que sigue fallando queda descartado con la causa actualizada.
        still = _validate_all(client, headers, {"models": [rows["bedrock/b1"]["id"]]})
        assert still.json()["run"]["failed"] == 1
    rows = _catalog_rows()
    assert (rows["anthropic/a1"]["enabled"], rows["anthropic/a1"]["disabledReason"]) == (True, None)
    assert (rows["anthropic/a2"]["enabled"], rows["anthropic/a2"]["disabledReason"]) == (
        False,
        "validation_failed",
    )
    assert (rows["bedrock/b1"]["enabled"], rows["bedrock/b1"]["disabledReason"]) == (
        False,
        "validation_failed",
    )


def test_when_every_model_fails_nothing_is_discarded_and_the_provider_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Todo falla: el problema es del proveedor (cuenta, clave), no de cada modelo; no se apaga ninguno."""
    models = ["anthropic/a1", "bedrock/b1", "cohere/c1"]
    with _gateway(models) as base_url:
        client, headers = _setup(tmp_path, monkeypatch, base_url, models)
        result = _validate_all(client, headers).json()
    assert (result["run"]["status"], result["run"]["reason"]) == ("completed", "no_model_passed")
    assert (result["run"]["failed"], result["run"]["discarded"]) == (3, 0)
    assert all(row["enabled"] and row["disabledReason"] is None for row in _catalog_rows().values())
    assert result["providerValidation"]["status"] == "failed"


def test_a_large_catalog_is_tested_model_by_model_with_bounded_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1201 modelos: 1200 de upstreams sin cuenta y uno bueno. La corrida los prueba todos."""
    catalog = [
        f"{upstream}/model-{index:03d}"
        for upstream in ("anthropic", "bedrock", "cohere")
        for index in range(400)
    ]
    catalog.append("zz-free/working-model")
    active = {"now": 0, "peak": 0}
    original = catalog_validation.RuntimeValidationService.probe_model
    lock = threading.Lock()

    def counting(self, *args, **kwargs):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        try:
            return original(self, *args, **kwargs)
        finally:
            with lock:
                active["now"] -= 1

    monkeypatch.setattr(catalog_validation.RuntimeValidationService, "probe_model", counting)
    with _gateway(catalog) as base_url:
        client, headers = _setup(tmp_path, monkeypatch, base_url, catalog)
        started = time.monotonic()
        result = _validate_all(client, headers, {"concurrency": 4}).json()
        elapsed = time.monotonic() - started
        requests = len(_Gateway.requests)
    run = result["run"]
    assert (run["total"], run["done"], run["ok"], run["failed"], run["discarded"]) == (
        1201,
        1201,
        1,
        1200,
        1200,
    )
    assert requests == 1201
    assert 1 < active["peak"] <= 4
    assert result["providerValidation"]["status"] == "validated"
    assert _provider_candidate()["validation"]["status"] == "validated"
    assert elapsed < 120


def test_cancellation_stops_the_run_and_keeps_what_was_tested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = [*PASSING, *[f"cohere/c{index}" for index in range(30)]]
    with _gateway(models) as base_url:
        _setup(tmp_path, monkeypatch, base_url, models)
        checks = {"count": 0}

        def cancel_soon() -> bool:
            checks["count"] += 1
            return checks["count"] > 1

        monkeypatch.setattr(catalog_validation, "CONTROL_INTERVAL_SECONDS", 0.05)
        _Gateway.catalog = models
        with _db() as connection:
            result = CatalogValidationService(connection).run(
                PROVIDER,
                limits=CatalogValidationLimits(concurrency=1, model_timeout_seconds=5, budget_seconds=60),
                should_cancel=cancel_soon,
            )
    run = result["run"]
    assert (run["status"], run["reason"]) == ("cancelled", "cancelled")
    assert run["done"] < run["total"] == len(models)
    rows = _catalog_rows()
    tested = {item["model"] for item in result["outcomes"]}
    untouched = [model for model in models if model not in tested]
    assert untouched and all(rows[model]["enabled"] for model in untouched)


def test_the_run_respects_its_total_time_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models = [f"slow/model-{index}" for index in range(12)]
    monkeypatch.setattr(catalog_validation, "MIN_MODEL_SECONDS", 0.5)
    monkeypatch.setattr(catalog_validation, "WORKER_GRACE_SECONDS", 2.0)
    with _gateway(models) as base_url:
        _setup(tmp_path, monkeypatch, base_url, models)
        started = time.monotonic()
        with _db() as connection:
            result = CatalogValidationService(connection).run(
                PROVIDER,
                limits=CatalogValidationLimits(concurrency=2, model_timeout_seconds=5, budget_seconds=3),
            )
        elapsed = time.monotonic() - started
    run = result["run"]
    assert (run["status"], run["reason"]) == ("budget_exhausted", "budget_exhausted")
    assert 0 < run["done"] < run["total"]
    assert elapsed < 3 + 2.5
    # Lo no probado sigue habilitado y "continuar" prueba solo lo pendiente.
    with _db() as connection:
        pending = CatalogValidationService(connection)._target_models(PROVIDER, None, only_untested=True)
    assert len(pending) == run["total"] - run["ok"] - run["failed"]


def test_a_switched_off_or_quota_suspended_provider_is_not_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _gateway(MIXED) as base_url:
        client, headers = _setup(tmp_path, monkeypatch, base_url, MIXED)
        original = QuotaManager.providers_in_cooldown
        monkeypatch.setattr(QuotaManager, "providers_in_cooldown", lambda self: {PROVIDER})
        suspended = _validate_all(client, headers)
        assert suspended.status_code == 409
        assert "provider_quota_suspended" in suspended.json()["detail"]
        monkeypatch.setattr(QuotaManager, "providers_in_cooldown", original)
        client.patch(f"/api/v1/model-gateway/providers/{PROVIDER}", headers=headers, json={"enabled": False})
        disabled = _validate_all(client, headers)
        assert disabled.status_code == 409
        assert "provider_disabled" in disabled.json()["detail"]
        assert _Gateway.requests == []
    assert all(row["enabled"] for row in _catalog_rows().values())


def test_quota_suspension_mid_run_stops_before_testing_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = [f"ok/m{index}" for index in range(20)]
    monkeypatch.setattr(catalog_validation, "CONTROL_INTERVAL_SECONDS", 0.01)
    with _gateway(models) as base_url:
        _setup(tmp_path, monkeypatch, base_url, models)
        calls = {"count": 0}

        def suspended_after_start(self):
            calls["count"] += 1
            return {PROVIDER} if calls["count"] > 1 else set()

        monkeypatch.setattr(QuotaManager, "providers_in_cooldown", suspended_after_start)
        with _db() as connection:
            result = CatalogValidationService(connection).run(
                PROVIDER,
                limits=CatalogValidationLimits(concurrency=1, model_timeout_seconds=5, budget_seconds=60),
            )
    assert (result["run"]["status"], result["run"]["reason"]) == ("aborted", "provider_quota_suspended")
    assert result["run"]["done"] < len(models)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "validated"}, "ok"),
        ({"status": "failed", "httpStatus": 400}, "failed"),
        ({"status": "failed", "httpStatus": 401}, "failed"),
        ({"status": "failed", "httpStatus": 404}, "failed"),
        ({"status": "failed", "httpStatus": 429}, "skipped"),
        ({"status": "failed", "httpStatus": 503}, "skipped"),
        ({"status": "failed", "httpStatus": None, "reason": "model_validation_invalid_response"}, "failed"),
        (
            {"status": "failed", "httpStatus": None, "reason": "provider_unreachable", "evidence": "refused"},
            "skipped",
        ),
        (
            {
                "status": "failed",
                "httpStatus": None,
                "reason": "x",
                "evidence": "The read operation timed out",
            },
            "skipped",
        ),
    ],
)
def test_outcome_classification(result: dict[str, object], expected: str) -> None:
    assert classify_outcome(result) == expected


def test_a_gateway_upstream_401_does_not_cool_down_the_whole_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otro modelo del gateway respondió con la misma configuración: la credencial funciona."""
    with _gateway(["ok/one"]) as base_url:
        _setup(tmp_path, monkeypatch, base_url, ["ok/one", "bedrock/b1"])
    with _db() as connection, connection:
        fingerprint = provider_configuration_fingerprint(connection, PROVIDER)
        earlier = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        record_model_execution(connection, PROVIDER, "ok/one", True, "test_prompt", started_at=earlier)
        record_model_execution(connection, PROVIDER, "bedrock/b1", False, "test_prompt", http_status=401)
        assert provider_authentication_failure(connection, PROVIDER, fingerprint) is None
        assert model_validation_rejection(connection, PROVIDER, "ok/one") is None
        # El mismo modelo que falla tras su propio éxito sí enfría la cuenta.
        record_model_execution(connection, PROVIDER, "ok/one", False, "test_prompt", http_status=401)
        record_model_execution(connection, PROVIDER, "bedrock/b1", False, "test_prompt", http_status=401)
        connection.execute("DELETE FROM model_execution_health WHERE model = 'ok/one' AND success = 1")
        assert provider_authentication_failure(connection, PROVIDER, fingerprint) == 401


def test_phase_85_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_client(tmp_path, monkeypatch)
    with _db() as connection:
        assert CURRENT_SCHEMA_VERSION >= 85
        connection.execute("DELETE FROM schema_migrations WHERE version = 85")
        init_phase85_schema(connection)
        init_phase85_schema(connection)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(model_catalog)")}
        assert {"disabled_reason", "disabled_detail"} <= columns
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"model_validation_outcomes", "model_validation_runs"} <= tables
        assert (
            connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 85").fetchone()[0] == 1
        )
