"""Validación de un gateway alcanzable con 1000+ modelos habilitados, la mayoría de upstreams sin cuenta.

Reproduce el reporte del operador (OmniRoute respondía, pero la validación fallaba y el runtime no se
podía asignar): un gateway OpenAI-compatible falso en un ``http.server`` local anuncia 1200 modelos y
responde como OmniRoute a los upstreams sin cuenta (400/401/404/429/503 JSON, 200 con objeto ``error`` o
``choices`` vacío, 504 "upstream timed out") y con formas válidas no estándar (SSE aunque se pida
``stream: false``, solo ``reasoning_content``, ``content`` en partes). Fija que la validación elige
candidatos repartidos, que una respuesta válida no estándar cuenta como éxito, que cada intento explica
su falla con el estado HTTP y el cuerpo, que nunca excede su presupuesto de tiempo y que una credencial
que falla antes de la red dice qué referencia revisó y por qué.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import local_control_center.executions.inputs as execution_inputs
from local_control_center.agents.credentials import CredentialResolution
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team import probe
from local_control_center.runtime_team.candidates import RuntimeTeamCandidatesService
from local_control_center.runtime_team.probe import MAX_AUTO_VALIDATION_ATTEMPTS, RuntimeValidationService
from local_control_center.shared.db import open_sqlite_connection
from tests_py.test_gateway_runtime_validation import _MemoryKeyring
from tests_py.test_provider_setup_catalog import auth_headers, create_client, enable_remote_provider

#: Upstreams sin cuenta: estado HTTP y cuerpo JSON típicos de OmniRoute.
FAILING_UPSTREAMS: dict[str, tuple[int, dict[str, object]]] = {
    "anthropic": (400, {"error": {"message": "No active credentials for provider: anthropic"}}),
    "bedrock": (401, {"error": {"message": "Invalid API key for upstream bedrock", "code": 401}}),
    "cohere": (404, {"error": {"message": "Model not available on this gateway"}}),
    "deepinfra": (429, {"error": {"message": "Upstream quota exceeded, retry later"}}),
}
MODELS_PER_UPSTREAM = 300
SUCCESS_MODEL = "zz-free/working-model"


def _message(content: object, **extra: object) -> dict[str, object]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": "upstream-resolved-name",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, **extra},
                "finish_reason": "stop",
            }
        ],
    }


class _LargeGatewayHandler(BaseHTTPRequestHandler):
    """Gateway falso: el comportamiento de ``/chat/completions`` depende del upstream (prefijo) del modelo."""

    requests: list[str] = []

    def log_message(self, *_args: object) -> None:
        return

    def _send(self, status: int, payload: object, *, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        self._send(200, {"data": [{"id": model} for model in _catalog()]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        model = str(body.get("model"))
        type(self).requests.append(model)
        upstream = model.split("/", 1)[0]
        if upstream in FAILING_UPSTREAMS:
            status, payload = FAILING_UPSTREAMS[upstream]
            self._send(status, payload)
        elif upstream == "unavailable":
            self._send(503, {"error": {"message": "All upstream accounts are cooling down"}})
        elif upstream == "gatewaytimeout":
            self._send(504, {"error": {"message": "upstream request timed out after 2000ms"}})
        elif upstream == "slow":
            time.sleep(1.5)
            self._send(200, _message("ok"))
        elif upstream == "error200":
            self._send(200, {"error": {"message": "Provider returned error: no account", "type": "upstream"}})
        elif upstream == "nochoices":
            self._send(200, {"id": "x", "object": "chat.completion", "choices": []})
        elif upstream == "sse":
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "o"}}]},
                {"choices": [{"index": 0, "delta": {"content": "k"}, "finish_reason": "stop"}]},
            ]
            stream = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            self._send(200, stream.encode("utf-8"), content_type="text/event-stream")
        elif upstream == "reasoning":
            self._send(200, _message(None, reasoning_content="The user wants the word ok."))
        elif upstream == "parts":
            self._send(200, _message([{"type": "text", "text": "ok"}]))
        else:
            self._send(200, _message("ok"))


def _catalog() -> list[str]:
    models = [
        f"{upstream}/model-{index:03d}"
        for upstream in FAILING_UPSTREAMS
        for index in range(MODELS_PER_UPSTREAM)
    ]
    return [*models, SUCCESS_MODEL]


@contextmanager
def _gateway() -> Iterator[str]:
    _LargeGatewayHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LargeGatewayHandler)
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


def _omniroute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_url: str, models: list[str]):
    """Cuenta OmniRoute con ``models`` habilitados (todos, como el operador que importó el catálogo)."""
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
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
    with _db() as connection, connection:
        store = ProviderAccountStore(connection)
        for model in models:
            store.upsert_model({"providerId": "omniroute", "model": model, "enabled": True})
    return client, headers


def _validate(client, headers, body: dict[str, object] | None = None) -> dict[str, object]:
    response = client.post(
        "/api/v1/model-gateway/providers/omniroute/validate-runtime", headers=headers, json=body or {}
    )
    assert response.status_code == 200, response.text
    return response.json()["validation"]


def test_a_reachable_gateway_with_1200_models_validates_through_a_spread_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los 300 primeros alfabéticos son del mismo upstream sin cuenta: la muestra salta de upstream.

    Antes se probaban los 3 primeros alfabéticos (todos ``anthropic/``, 400) y el gateway quedaba
    ``failed`` aunque respondía; ahora cada upstream aporta un candidato y el que tiene cuenta valida.
    """
    with _gateway() as base_url:
        client, headers = _omniroute(tmp_path, monkeypatch, base_url, _catalog())
        started = time.monotonic()
        validation = _validate(client, headers)
        elapsed = time.monotonic() - started
        requested = list(_LargeGatewayHandler.requests)
    assert validation["status"] == "validated", validation
    assert validation["model"] == SUCCESS_MODEL
    attempts = validation["attempts"]
    assert len(attempts) == MAX_AUTO_VALIDATION_ATTEMPTS == len(requested)
    assert [item["httpStatus"] for item in attempts] == [400, 401, 404, 429, None]
    assert [item["model"].split("/")[0] for item in attempts] == [*FAILING_UPSTREAMS, "zz-free"]
    # Cada intento fallido explica su causa con el cuerpo del gateway, no solo el código.
    assert "No active credentials for provider: anthropic" in attempts[0]["evidence"]
    assert "Model not available on this gateway" in attempts[2]["evidence"]
    assert elapsed < 30

    with _db() as connection:
        candidates = RuntimeTeamCandidatesService(connection).list_candidates(project_id=None, selected=None)
    omniroute = next(item for item in candidates["candidates"] if item["providerId"] == "omniroute")
    assert omniroute["validation"]["status"] == "validated"
    assert {"product_owner", "developer"} <= set(omniroute["eligibleRoles"])
    assert "omniroute" in candidates["suggestedRoleRuntimes"].values()


def test_the_operator_default_model_is_tried_first_and_recent_failures_go_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _gateway() as base_url:
        client, headers = _omniroute(tmp_path, monkeypatch, base_url, _catalog())
        with _db() as connection, connection:
            LocalModelSettingsRepository(connection).upsert(
                "omniroute", SUCCESS_MODEL, actor="operator", is_default=True
            )
        validation = _validate(client, headers)
    assert [item["model"] for item in validation["attempts"]] == [SUCCESS_MODEL]

    with _db() as connection, connection:
        LocalModelSettingsRepository(connection).upsert(
            "omniroute", SUCCESS_MODEL, actor="operator", is_default=False
        )
        record_model_execution(connection, "omniroute", "anthropic/model-000", False, "test_prompt")
        service = RuntimeValidationService(connection)
        account = service.accounts.get_provider_account("omniroute")
        ordered = service._models_to_validate(account, requested=None)
    # El validado va primero; el que falló recién sale de la muestra (pasa al final) y entra otro.
    assert ordered == [
        SUCCESS_MODEL,
        "bedrock/model-000",
        "cohere/model-000",
        "deepinfra/model-000",
        "anthropic/model-001",
    ]


@pytest.mark.parametrize(
    "model",
    ["sse/streamed-anyway", "reasoning/thinking-only", "parts/content-parts", "plain/upstream-renames-model"],
)
def test_non_standard_but_valid_completions_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """SSE sin pedirlo, solo razonamiento, contenido en partes y un ``model`` distinto en la respuesta."""
    with _gateway() as base_url:
        client, headers = _omniroute(tmp_path, monkeypatch, base_url, [model])
        validation = _validate(client, headers, {"model": model})
    assert (validation["status"], validation["model"]) == ("validated", model), validation


@pytest.mark.parametrize(
    ("model", "expected_status", "expected_evidence"),
    [
        ("error200/no-account", None, "HTTP 200 with an error object: Provider returned error: no account"),
        ("nochoices/empty", None, "HTTP 200 without choices"),
        ("unavailable/cooling", 503, "All upstream accounts are cooling down"),
        ("bedrock/model-000", 401, "Invalid API key for upstream bedrock"),
    ],
)
def test_gateway_rejections_fail_with_the_status_and_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    expected_status: int | None,
    expected_evidence: str,
) -> None:
    with _gateway() as base_url:
        client, headers = _omniroute(tmp_path, monkeypatch, base_url, [model])
        validation = _validate(client, headers, {"model": model})
    assert validation["status"] == "failed"
    assert validation["httpStatus"] == expected_status
    assert expected_evidence in validation["evidence"]
    with _db() as connection:
        candidates = RuntimeTeamCandidatesService(connection).list_candidates(project_id=None, selected=None)
    omniroute = next(item for item in candidates["candidates"] if item["providerId"] == "omniroute")
    # El selector muestra por qué no se puede elegir: falla registrada con su estado HTTP.
    assert omniroute["validation"]["status"] == "failed"
    assert omniroute["validation"]["httpStatus"] == expected_status


def test_a_gateway_timeout_answer_does_not_stop_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un 504 "upstream timed out" viene de un gateway alcanzable: no es ``provider_unreachable`` del endpoint."""
    with _gateway() as base_url:
        client, headers = _omniroute(tmp_path, monkeypatch, base_url, ["gatewaytimeout/x", "ok/y"])
        validation = _validate(client, headers)
    assert validation["status"] == "validated"
    assert [(item["model"], item["httpStatus"]) for item in validation["attempts"]] == [
        ("gatewaytimeout/x", 504),
        ("ok/y", None),
    ]


def test_validation_never_exceeds_its_time_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstreams colgados: cada intento se corta en su tope y el conjunto respeta el presupuesto total."""
    monkeypatch.setattr(probe, "VALIDATION_TIME_BUDGET_SECONDS", 2.5)
    monkeypatch.setattr(probe, "VALIDATION_ATTEMPT_TIMEOUT_SECONDS", 0.8)
    monkeypatch.setattr(probe, "_MIN_ATTEMPT_SECONDS", 0.5)
    slow = [f"slow/model-{index}" for index in range(8)]
    with _gateway() as base_url:
        client, headers = _omniroute(tmp_path, monkeypatch, base_url, slow)
        started = time.monotonic()
        validation = _validate(client, headers)
        elapsed = time.monotonic() - started
    assert validation["status"] == "failed"
    # Un timeout de lectura es de ese upstream: sigue probando mientras quede presupuesto.
    assert 2 <= len(validation["attempts"]) <= 4
    assert elapsed < 2.5 + 1.5


def test_a_missing_env_credential_says_which_ref_and_why_without_calling_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El caso NVIDIA NIM "credential_invalid 0 ms": falla antes de la red y ahora dice qué revisar."""
    monkeypatch.delenv("AIDO_TEST_NIM_MISSING_KEY", raising=False)
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        "nvidia_nim",
        base_url="https://example.invalid/v1",
        credential_ref="env:AIDO_TEST_NIM_MISSING_KEY",
    )
    monkeypatch.setattr(probe, "provider_instance", lambda *args, **kwargs: pytest.fail("must not call"))
    validation = client.post(
        "/api/v1/model-gateway/providers/nvidia_nim/validate-runtime", headers=headers, json={}
    ).json()["validation"]
    assert (validation["status"], validation["reason"]) == ("failed", "credential_missing")
    assert "env:AIDO_TEST_NIM_MISSING_KEY is not set in AIDO's environment" in validation["evidence"]
    assert validation["httpStatus"] is None


def test_credential_failure_explains_unsupported_and_invalid_refs() -> None:
    account = {"providerId": "nvidia_nim", "providerType": "api", "credentialRef": "plain-secret-value"}
    cause, message = probe.credential_failure(account, "Credential ref is invalid.")
    assert cause == "credential_invalid"
    assert "is not a valid reference" in message
    cause, message = probe.credential_failure(
        {"providerId": "nvidia_nim", "providerType": "api", "credentialRef": ""},
        "Credential ref is required.",
    )
    assert cause == "credential_ref_required"
    assert "has no credential ref configured" in message


class _KeyringWithoutSecret:
    """Resolver falso: la ref de llavero es válida sin leerla, pero al leerla no hay secreto."""

    def resolve(self, ref, *, fetch=True):
        if not fetch:
            return CredentialResolution(ref=ref, status="unverified", source="keyring")
        return CredentialResolution(
            ref=ref, status="missing", source="keyring", message="Keyring credential is missing"
        )


def test_a_keyring_ref_without_a_stored_secret_is_explained_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probe, "CredentialResolver", _KeyringWithoutSecret)
    cause, message = probe.stored_secret_missing({"credentialRef": "keyring:aido/nvidia"})
    assert cause == "credential_missing"
    assert (
        message == "Credential ref keyring:aido/nvidia has no stored secret (Keyring credential is missing)."
    )
    assert probe.stored_secret_missing({"credentialRef": ""}) is None
