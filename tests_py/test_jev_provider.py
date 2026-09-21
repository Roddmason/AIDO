"""Contratos offline del proveedor Jev: privacidad, transporte y respuestas estrictas."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing

import httpx
import pytest

from local_control_center.decision_engine.config import DecisionConfig
from local_control_center.decision_engine.models import (
    DecisionCandidate,
    DecisionConstraints,
    DecisionContext,
    DecisionRequest,
)
from local_control_center.decision_engine.providers import (
    DeterministicDecisionProvider,
    JevDecisionProvider,
    ProviderError,
)
from local_control_center.decision_engine.transport import JevAsyncTransport
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope


def test_dns_timeout_cancels_resolver_without_executor_or_orphan(monkeypatch):
    cancelled = []

    async def resolve(host, timeout):
        assert host == "api.typesafe.ai"
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", lambda *args, **kwargs: pytest.fail("System DNS called"))
        monkeypatch.setattr(loop, "run_in_executor", lambda *args, **kwargs: pytest.fail("Executor called"))
        transport = JevAsyncTransport(
            resolver=resolve, transport=httpx.MockTransport(lambda _: pytest.fail("HTTP called"))
        )
        provider = JevDecisionProvider(_config(), transport=transport)
        with pytest.raises(ProviderError, match=r"^timeout$"):
            await provider.decide(_request())
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    asyncio.run(run())
    assert cancelled == [True]


def test_real_async_dns_cancellation_closes_udp_without_system_dns(monkeypatch):
    import dns.asyncresolver

    closed = []
    resolver_class = dns.asyncresolver.Resolver

    def resolver_factory():
        resolver = resolver_class(configure=False)
        resolver.nameservers = ["8.8.8.8"]
        return resolver

    class Datagram:
        def sendto(self, data, destination):
            assert destination[0] == "8.8.8.8"

        def close(self):
            closed.append(True)

    async def endpoint(protocol_factory, local_addr=None, **kwargs):
        protocol = protocol_factory()
        transport = Datagram()
        protocol.connection_made(transport)
        return transport, protocol

    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "create_datagram_endpoint", endpoint)
        monkeypatch.setattr(loop, "getaddrinfo", lambda *args, **kwargs: pytest.fail("System DNS called"))
        monkeypatch.setattr(loop, "run_in_executor", lambda *args, **kwargs: pytest.fail("Executor called"))
        with pytest.raises(ProviderError, match=r"^timeout$"):
            await JevDecisionProvider(_config()).decide(_request())
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    monkeypatch.setattr(dns.asyncresolver, "Resolver", resolver_factory)
    asyncio.run(run())
    assert closed == [True]


def test_default_ip_transport_requires_certificate_verification_and_disables_proxy(monkeypatch):
    captured = []

    async def resolve(host, timeout):
        return "8.8.8.8"

    def transport_factory(**kwargs):
        captured.append(kwargs)
        return httpx.MockTransport(lambda _: httpx.Response(200, json=_response()))

    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport_factory)
    transport = JevAsyncTransport(resolver=resolve)
    asyncio.run(JevDecisionProvider(_config(), transport=transport).decide(_request()))
    assert captured == [{"verify": True, "trust_env": False, "retries": 0}]


def test_dns_ip_transport_retains_host_and_certificate_hostname(monkeypatch):
    captured = []

    async def resolve(host, timeout):
        return "8.8.8.8"

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_response())

    transport = JevAsyncTransport(resolver=resolve, transport=httpx.MockTransport(handler))
    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    result = asyncio.run(JevDecisionProvider(_config(), transport=transport).decide(_request()))
    assert result.selected == "private-model-a"
    assert captured[0].url.host == "8.8.8.8"
    assert captured[0].headers["Host"] == "api.typesafe.ai"
    assert captured[0].extensions["sni_hostname"] == "api.typesafe.ai"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "224.0.0.1", "hostname.invalid"]
)
def test_dns_nonpublic_results_never_reach_http(monkeypatch, address):
    async def resolve(host, timeout):
        return address

    transport = JevAsyncTransport(
        resolver=resolve, transport=httpx.MockTransport(lambda _: pytest.fail("HTTP called"))
    )
    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    with pytest.raises(ProviderError, match=r"^transport_error$"):
        asyncio.run(JevDecisionProvider(_config(), transport=transport).decide(_request()))


def _request(**overrides):
    values = {
        "decision_type": "runtime_model_ranking",
        "candidates": (
            DecisionCandidate(id="private-model-a", metadata={"quality_score": 0.9}),
            DecisionCandidate(id="private-model-b", metadata={"quality_score": 0.5}),
        ),
        "context": DecisionContext(
            task_type="feature", risk="medium", privacy_mode="metadata_only", task_fingerprint="a" * 64
        ),
        "constraints": DecisionConstraints(
            allowed_candidates=frozenset({"private-model-a", "private-model-b"}), deterministic_risk="medium"
        ),
        "effective_decision": "private-model-b",
        "project_id": "private-project",
        "job_id": "private-job",
    }
    values.update(overrides)
    return DecisionRequest(**values)


def _config(**overrides):
    values = {"api_key_reference": "env:AIDO_JEV_TEST_KEY", "timeout_seconds": 0.05}
    values.update(overrides)
    return DecisionConfig(**values)


def _response():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "c0",
                "probabilities": {"c0": 0.88, "c1": 0.12},
                "confidence": 0.81,
            }
        },
        "usage": {"input_tokens": 318, "output_tokens": 34},
    }


def _provider(monkeypatch, handler, **config):
    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    return JevDecisionProvider(_config(**config), transport=httpx.MockTransport(handler))


def test_jev_preserves_confidence_and_maps_opaque_choices(monkeypatch):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_response())

    result = asyncio.run(_provider(monkeypatch, handler).decide(_request()))
    assert result.selected == "private-model-a"
    assert result.ranking == ("private-model-a", "private-model-b")
    assert result.probabilities == {"private-model-a": 0.88, "private-model-b": 0.12}
    assert result.confidence == 0.81
    assert result.margin == pytest.approx(0.76)
    assert result.input_tokens == 318
    assert result.output_tokens == 34
    assert result.model == "jev-1.13.0"
    assert result.version == "1.13.0"
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer offline-test-value"
    body = json.loads(request.content)
    assert set(body) == {"state", "model", "questions"}
    assert set(body["questions"]["decision"]["criteria"]) == {"c0", "c1"}
    assert "private-" not in request.content.decode()


def test_metadata_allowlist_excludes_free_text_and_invalid_values(monkeypatch):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    request = _request(
        candidates=(
            DecisionCandidate(
                id="private-model-a",
                metadata={
                    "prompt": "private-prompt",
                    "provider": "private-provider",
                    "quality_score": "private-score",
                    "free_tier": True,
                    "context_window": float("nan"),
                    "nested": {"token": "private-secret"},
                },
            ),
            DecisionCandidate(id="private-model-b", metadata={}),
        )
    )
    asyncio.run(_provider(monkeypatch, handler).decide(request))
    payload = json.dumps(captured[0])
    assert "private-" not in payload
    assert "NaN" not in payload
    assert "free_tier" in payload


def test_known_role_and_family_metadata_remains_useful(monkeypatch):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    request = _request(
        candidates=(
            DecisionCandidate(id="private-model-a", metadata={"family": "codex", "role": "backend_engineer"}),
            DecisionCandidate(id="private-model-b", metadata={"family": "gemini", "role": "private-role"}),
        )
    )
    asyncio.run(_provider(monkeypatch, handler).decide(request))
    criteria = captured[0]["questions"]["decision"]["criteria"]
    assert criteria["c0"] == {"family": "codex", "role": "backend_engineer"}
    assert criteria["c1"] == {"family": "gemini"}


@pytest.mark.parametrize("value", ["private\nheader", "private token", "privaté", "x" * 4097])
def test_invalid_credential_header_never_reaches_transport(monkeypatch, value):
    monkeypatch.setenv("AIDO_JEV_TEST_KEY", value)
    provider = JevDecisionProvider(
        _config(), transport=httpx.MockTransport(lambda _: pytest.fail("Network called"))
    )
    with pytest.raises(ProviderError, match=r"^credential_unavailable$"):
        asyncio.run(provider.decide(_request()))


@pytest.mark.parametrize("privacy_mode", ["local_only"])
def test_local_only_never_resolves_credentials_or_calls_network(privacy_mode):
    class Resolver:
        def resolve(self, *args, **kwargs):
            pytest.fail("Credentials must not be resolved")

    provider = JevDecisionProvider(_config(), credential_resolver=Resolver())
    request = _request(
        context=DecisionContext(
            task_type="feature", risk="medium", privacy_mode=privacy_mode, task_fingerprint="a" * 64
        )
    )
    with pytest.raises(ProviderError, match=r"^privacy_blocked$"):
        asyncio.run(provider.decide(request))


@pytest.mark.parametrize(
    "path,value,code",
    [
        (("model",), "jev", "model_mismatch"),
        (("answers", "decision", "type"), "score", "invalid_response"),
        (("answers", "decision", "choice"), "unknown", "invalid_choice"),
        (("answers", "decision", "choice"), "c1", "invalid_choice"),
        (("answers", "decision", "confidence"), True, "invalid_response"),
        (("answers", "decision", "confidence"), "0.8", "invalid_response"),
        (("answers", "decision", "confidence"), float("nan"), "invalid_response"),
        (("answers", "decision", "confidence"), 1.1, "invalid_response"),
        (("answers", "decision", "probabilities"), {"c0": 0.8}, "invalid_distribution"),
        (("answers", "decision", "probabilities"), {"c0": 0.8, "x": 0.2}, "invalid_distribution"),
        (("answers", "decision", "probabilities"), {"c0": 0.8, "c1": 0.8}, "invalid_distribution"),
        (("answers", "decision", "probabilities"), {"c0": True, "c1": 0}, "invalid_response"),
        (("answers", "decision", "probabilities"), {"c0": float("inf"), "c1": 0}, "invalid_response"),
        (("answers", "decision", "probabilities"), {"c0": -0.1, "c1": 1.1}, "invalid_response"),
        (("usage", "input_tokens"), True, "invalid_response"),
        (("usage", "input_tokens"), -1, "invalid_response"),
    ],
)
def test_jev_rejects_invalid_response_values(monkeypatch, path, value, code):
    payload = _response()
    destination = payload
    for key in path[:-1]:
        destination = destination[key]
    destination[path[-1]] = value
    raw = json.dumps(payload).encode()
    provider = _provider(monkeypatch, lambda _: httpx.Response(200, content=raw))
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.decide(_request()))
    assert error.value.code == code
    assert str(error.value) == code


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(unexpected="sensitive"),
        lambda body: body["answers"].update(unexpected=body["answers"]["decision"]),
        lambda body: body["answers"]["decision"].update(unexpected=True),
        lambda body: body["usage"].update(unexpected=True),
        lambda body: body.pop("model"),
        lambda body: body.pop("usage"),
        lambda body: body["answers"]["decision"].pop("confidence"),
    ],
)
def test_jev_requires_exact_response_schema(monkeypatch, mutate):
    body = _response()
    mutate(body)
    provider = _provider(monkeypatch, lambda _: httpx.Response(200, json=body))
    with pytest.raises(ProviderError, match=r"^invalid_response$"):
        asyncio.run(provider.decide(_request()))


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json-private-secret",
        b"[]",
        b'{"model":"first","model":"second"}',
        b'{"model":"jev-1.13.0","answers":{"decision":{"type":"choice","choice":"c0",'
        b'"probabilities":{"c0":0.9,"c0":0.8,"c1":0.2},"confidence":0.81}},'
        b'"usage":{"input_tokens":1,"output_tokens":1}}',
    ],
)
def test_jev_rejects_invalid_json_and_duplicate_keys(monkeypatch, raw):
    provider = _provider(monkeypatch, lambda _: httpx.Response(200, content=raw))
    with pytest.raises(ProviderError, match=r"^invalid_response$"):
        asyncio.run(provider.decide(_request()))


@pytest.mark.parametrize("status", [301, 401, 429, 500, 529])
def test_http_errors_have_no_retry_redirect_or_body_leak(monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="private-server-error", headers={"Location": "https://evil.invalid"}
        )

    with pytest.raises(ProviderError, match=r"^http_error$"):
        asyncio.run(_provider(monkeypatch, handler).decide(_request()))
    assert len(calls) == 1


def test_total_timeout_cancels_transport_and_closes_client(monkeypatch):
    class SlowTransport(httpx.AsyncBaseTransport):
        cancelled = False
        closed = False

        async def handle_async_request(self, request):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

        async def aclose(self):
            self.closed = True

    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    transport = SlowTransport()
    with pytest.raises(ProviderError, match=r"^timeout$"):
        asyncio.run(JevDecisionProvider(_config(), transport=transport).decide(_request()))
    assert transport.cancelled
    assert transport.closed


def test_external_cancellation_is_not_hidden_as_fallback(monkeypatch):
    closed = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise asyncio.CancelledError

        async def aclose(self):
            closed.append(True)

    monkeypatch.setenv("AIDO_JEV_TEST_KEY", "offline-test-value")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(JevDecisionProvider(_config(), transport=Transport()).decide(_request()))
    assert closed == [True]


def test_no_network_inside_sqlite_transaction(monkeypatch, tmp_path):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE example (id INTEGER)")
        connection.execute("INSERT INTO example VALUES (1)")
        with (
            execution_scope(ProcessExecutionContext(db_path=tmp_path / "db", connection=connection)),
            pytest.raises(ProviderError, match=r"^external_boundary$"),
        ):
            asyncio.run(_provider(monkeypatch, lambda _: pytest.fail("Network called")).decide(_request()))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.typesafe.ai/v1/systemone",
        "https://evil.invalid/v1/systemone",
        "https://api.typesafe.ai/v1/systemone?api_key=private",
        "https://user:private@api.typesafe.ai/v1/systemone",
    ],
)
def test_provider_defends_endpoint_even_with_unvalidated_config(endpoint):
    provider = JevDecisionProvider(_config().model_copy(update={"endpoint": endpoint}))
    with pytest.raises(ProviderError, match=r"^invalid_endpoint$"):
        asyncio.run(provider.decide(_request()))


@pytest.mark.parametrize(
    "reference", ["keyring:service/account", "vault:mount/path#field", "private-raw-key"]
)
def test_provider_rejects_blocking_or_raw_credential_sources(reference):
    class Resolver:
        def resolve(self, *args, **kwargs):
            pytest.fail("Blocking or raw credentials must not be resolved")

    config = _config().model_copy(update={"api_key_reference": reference})
    provider = JevDecisionProvider(config, credential_resolver=Resolver())
    with pytest.raises(ProviderError, match=r"^credential_unavailable$"):
        asyncio.run(provider.decide(_request()))


def test_encoded_response_is_rejected_before_decompression(monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Encoded response must not be read")
            yield b""

    provider = _provider(
        monkeypatch, lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Stream())
    )
    with pytest.raises(ProviderError, match=r"^invalid_response$"):
        asyncio.run(provider.decide(_request()))


def test_tied_argmax_ranking_starts_with_the_selected_candidate(monkeypatch):
    body = _response()
    body["answers"]["decision"].update(choice="c1", probabilities={"c0": 0.5, "c1": 0.5}, confidence=0.0)
    result = asyncio.run(_provider(monkeypatch, lambda _: httpx.Response(200, json=body)).decide(_request()))
    assert result.selected == result.ranking[0] == "private-model-b"
    assert result.margin == 0.0


def test_oversized_response_is_rejected(monkeypatch):
    provider = _provider(monkeypatch, lambda _: httpx.Response(200, content=b" " * 70_000))
    with pytest.raises(ProviderError, match=r"^response_too_large$"):
        asyncio.run(provider.decide(_request()))


def test_missing_credential_never_calls_transport(monkeypatch):
    monkeypatch.delenv("AIDO_JEV_TEST_KEY", raising=False)
    provider = JevDecisionProvider(
        _config(), transport=httpx.MockTransport(lambda _: pytest.fail("Network called"))
    )
    with pytest.raises(ProviderError, match=r"^credential_unavailable$"):
        asyncio.run(provider.decide(_request()))


def test_transport_errors_do_not_expose_original_message(monkeypatch):
    def handler(_):
        raise httpx.ConnectError("private-token-and-url")

    with pytest.raises(ProviderError, match=r"^transport_error$") as error:
        asyncio.run(_provider(monkeypatch, handler).decide(_request()))
    assert error.value.__suppress_context__


def test_deterministic_provider_preserves_existing_decision_without_probabilities():
    result = asyncio.run(DeterministicDecisionProvider().decide(_request()))
    assert result.selected == "private-model-b"
    assert result.probabilities == {}
    assert result.confidence is None
    assert result.margin is None


def test_deterministic_provider_abstains_without_existing_decision():
    result = asyncio.run(DeterministicDecisionProvider().decide(_request(effective_decision=None)))
    assert result.selected is None
    assert result.ranking == ()
    assert result.confidence is None
