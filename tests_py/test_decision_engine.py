from __future__ import annotations

import asyncio
import json
from contextlib import closing

import pytest


def test_shadow_keeps_codex_and_persists_claude_recommendation(tmp_path):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.models import (
        DecisionCandidate,
        DecisionConstraints,
        DecisionContext,
        DecisionRequest,
        DecisionResult,
    )
    from local_control_center.decision_engine.repository import DecisionRepository
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    class FakeJev:
        async def decide(self, request):
            return DecisionResult(
                selected="claude",
                ranking=("claude", "codex"),
                probabilities={"claude": 0.95, "codex": 0.05},
                confidence=0.9,
                margin=0.9,
                engine="jev",
                provider="jev",
                model="jev-1.13.0",
                version="1.13.0",
                decision_type=request.decision_type,
                reason_code="ranked",
            )

    request = DecisionRequest(
        decision_type="runtime_model_ranking",
        candidates=(DecisionCandidate(id="codex"), DecisionCandidate(id="claude")),
        context=DecisionContext(task_type="bug", risk="medium", task_fingerprint="a" * 64),
        constraints=DecisionConstraints(
            allowed_candidates=frozenset({"codex", "claude"}), deterministic_risk="medium"
        ),
        effective_decision="codex",
    )
    with closing(open_sqlite_connection(tmp_path / "decision.sqlite")) as connection:
        initialize_platform_schema(connection)
        engine = ShadowDecisionEngine(connection, config=DecisionConfig(enabled=True), provider=FakeJev())
        receipt = asyncio.run(engine.observe(request))
        assert receipt["effectiveDecision"] == "codex"
        assert receipt["jevRecommendation"] == "claude"
        assert receipt["mode"] == "shadow"
        assert receipt["margin"] == pytest.approx(0.9)
        assert DecisionRepository(connection).get(receipt["decisionId"]) == receipt


@pytest.mark.parametrize(
    "deterministic,recommended,expected",
    [
        ("R3", "R1", "high"),
        ("low", "critical", "critical"),
        ("medium", None, "medium"),
    ],
)
def test_risk_cannot_decrease(deterministic, recommended, expected):
    from local_control_center.decision_engine.models import effective_risk

    assert effective_risk(deterministic, recommended) == expected


@pytest.fixture
def decision_db(tmp_path):
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    with closing(open_sqlite_connection(tmp_path / "shadow.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def request_for(**changes):
    from local_control_center.decision_engine.models import DecisionRequest

    data = {
        "decision_type": "runtime_model_ranking",
        "candidates": [{"id": "codex"}, {"id": "claude"}],
        "context": {"task_type": "bug", "risk": "medium", "task_fingerprint": "a" * 64},
        "constraints": {"allowed_candidates": {"codex", "claude"}, "deterministic_risk": "medium"},
        "effective_decision": "codex",
    }
    return DecisionRequest(**(data | changes))


class FakeJev:
    def __init__(self, **changes):
        self.changes = changes
        self.calls = 0

    async def decide(self, request):
        from local_control_center.decision_engine.models import DecisionResult

        self.calls += 1
        return DecisionResult(
            **(
                {
                    "selected": "claude",
                    "ranking": ("claude", "codex"),
                    "probabilities": {"claude": 0.95, "codex": 0.05},
                    "confidence": 0.9,
                    "margin": 0.9,
                    "engine": "typesafe",
                    "provider": "jev",
                    "model": "jev-1.13.0",
                    "version": "1.13.0",
                    "decision_type": request.decision_type,
                    "reason_code": "provider_selected",
                }
                | self.changes
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"selected": "shell.execute"},
        {"probabilities": {"claude": 0.95, "evil": 0.05}},
        {"confidence": float("nan")},
        {"probabilities": {"claude": float("inf"), "codex": 0.05}},
        {"probabilities": {"claude": -1.0, "codex": 2.0}},
        {"ranking": ("claude", "claude")},
        {"margin": 0.1},
        {"model": "jev-latest"},
        {"version": "next"},
        {"decision_type": "escalation_decision"},
    ],
)
def test_compromised_provider_never_expands_authority(decision_db, changes):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(enabled=True), provider=FakeJev(**changes)
    )
    receipt = asyncio.run(engine.observe(request_for()))
    assert receipt["effectiveDecision"] == "codex"
    assert receipt["jevRecommendation"] is None
    assert receipt["fallbackUsed"] is True
    assert receipt["providerFailed"] is True
    assert receipt["candidates"] == ["codex", "claude"]
    assert decision_db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    assert decision_db.execute("SELECT COUNT(*) FROM managed_processes").fetchone()[0] == 0


@pytest.mark.parametrize(
    "config_change,reason",
    [
        ({"confidence_threshold": 0.95}, "confidence_below_threshold"),
        ({"margin_threshold": 0.99}, "margin_below_threshold"),
        ({"max_risk": "low"}, "risk_requires_review"),
    ],
)
def test_gate_requires_confidence_margin_and_risk(decision_db, config_change, reason):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(enabled=True, **config_change), provider=FakeJev()
    )
    receipt = asyncio.run(engine.observe(request_for()))
    assert receipt["fallbackUsed"] is True
    assert receipt["fallbackReason"] == reason
    assert receipt["effectiveDecision"] == "codex"
    assert receipt["jevRecommendation"] == "claude"


@pytest.mark.parametrize(
    "config_change", [{"enabled": False}, {"mode": "disabled"}, {"shadow_enabled": False}]
)
def test_rollback_has_no_provider_call_or_new_receipt(decision_db, config_change):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    fake = FakeJev()
    config = DecisionConfig(**({"enabled": True} | config_change))
    assert (
        asyncio.run(ShadowDecisionEngine(decision_db, config=config, provider=fake).observe(request_for()))
        is None
    )
    assert fake.calls == 0
    assert decision_db.execute("SELECT COUNT(*) FROM decision_receipts").fetchone()[0] == 0


def test_timeout_cancels_provider_and_breaker_survives_new_engine(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    cancelled = []

    class SlowProvider:
        async def decide(self, request):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.append(True)

    config = DecisionConfig(enabled=True, timeout_seconds=0.01, circuit_failure_threshold=1)
    receipt = asyncio.run(
        ShadowDecisionEngine(decision_db, config=config, provider=SlowProvider()).observe(request_for())
    )
    assert cancelled == [True]
    assert receipt["fallbackReason"] == "timeout"
    fake = FakeJev()
    receipt = asyncio.run(
        ShadowDecisionEngine(decision_db, config=config, provider=fake).observe(request_for())
    )
    assert receipt["reasonCode"] == "circuit_open"
    assert fake.calls == 0


def test_local_only_never_sends_even_metadata(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    fake = FakeJev()
    request = request_for(
        context={
            "task_type": "bug",
            "risk": "medium",
            "task_fingerprint": "a" * 64,
            "privacy_mode": "local_only",
        }
    )
    receipt = asyncio.run(
        ShadowDecisionEngine(decision_db, config=DecisionConfig(enabled=True), provider=fake).observe(request)
    )
    assert fake.calls == 0
    assert receipt["fallbackReason"] == "privacy_blocked"


def test_outcomes_are_unknown_until_observed_and_not_counterfactual(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.models import DecisionOutcome
    from local_control_center.decision_engine.reporting import decision_report
    from local_control_center.decision_engine.repository import DecisionRepository
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(decision_db, config=DecisionConfig(enabled=True), provider=FakeJev())
    receipt = asyncio.run(engine.observe(request_for()))
    before = decision_report(decision_db)
    assert before["metrics"]["success_rate"] is None
    repo = DecisionRepository(decision_db)
    outcome = DecisionOutcome(evidence_ref="run-1", execution_succeeded=True)
    repo.record_outcome(receipt["decisionId"], outcome)
    repo.record_outcome(receipt["decisionId"], outcome)
    after = decision_report(decision_db)
    assert after["metrics"]["success_rate"] == 1
    assert after["metrics"]["agreement_rate"] == 0
    assert after["metrics"]["test_failure_rate"] is None
    assert after["metrics"]["human_override_rate"] is None
    assert after["comparisons"][0]["outcome"]["tokens"] is None
    assert after["comparisons"][0]["recommendationOutcome"] == "not_observed"
    with pytest.raises(ValueError, match="outcome_already_observed"):
        repo.record_outcome(
            receipt["decisionId"], DecisionOutcome(evidence_ref="run-2", execution_succeeded=False)
        )


def test_total_routing_latency_includes_shadow_and_preserves_unknown_baseline(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.reporting import decision_report
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(decision_db, config=DecisionConfig(enabled=True), provider=FakeJev())
    receipt = asyncio.run(engine.observe(request_for(routing_latency_ms=12.5)))
    assert receipt["totalRoutingLatencyMs"] == pytest.approx(12.5 + receipt["latencyMs"])
    unknown = asyncio.run(engine.observe(request_for()))
    assert unknown["totalRoutingLatencyMs"] is None
    assert decision_report(decision_db)["metrics"]["total_routing_latency_ms"] == [
        receipt["totalRoutingLatencyMs"]
    ]


def test_observer_does_not_call_network_under_transaction(decision_db, monkeypatch):
    from local_control_center.decision_engine.observers import observe_resource_decision
    from local_control_center.settings.repository import SettingsRepository

    SettingsRepository(decision_db).set_value("decision_engine.enabled", "general", None, True)
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    fake = FakeJev()
    monkeypatch.setattr(
        "local_control_center.decision_engine.service.JevDecisionProvider", lambda config: fake
    )
    decision_db.execute("BEGIN IMMEDIATE")
    observe_resource_decision(decision_db, decision={"selected": None, "candidates": []}, project_id=None)
    decision_db.execute("COMMIT")
    assert fake.calls == 0


def test_settings_precedence_rejects_secret_and_advisory(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig, resolve_config
    from local_control_center.settings.registry import descriptor_for, validate_value
    from local_control_center.settings.repository import SettingsRepository

    repo = SettingsRepository(decision_db)
    assert DecisionConfig().active is True
    assert resolve_config(decision_db, None).active is True
    repo.set_value("decision_engine.enabled", "general", None, False)
    assert resolve_config(decision_db, "p2").enabled is False
    repo.set_value("decision_engine.enabled", "general", None, True)
    repo.set_value("decision_engine.enabled", "project", "p1", False)
    assert resolve_config(decision_db, "p1").enabled is False
    assert resolve_config(decision_db, "p2").enabled is True
    for key, value in [
        ("api_key_reference", "raw-secret"),
        ("mode", "advisory"),
        ("endpoint", "https://example.org/?api_key=secret"),
        ("timeout_seconds", float("nan")),
        ("confidence_threshold", True),
    ]:
        with pytest.raises(ValueError):
            validate_value(descriptor_for("decision_engine." + key), value)


def test_jev_default_deadline_uses_existing_five_second_bound():
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.settings.registry import descriptor_for, validate_value

    descriptor = descriptor_for("decision_engine.timeout_seconds")
    assert DecisionConfig().timeout_seconds == descriptor.default == 5.0
    assert (descriptor.minimum, descriptor.maximum) == (0.01, 5)
    for value in (0, 5.01, float("inf")):
        with pytest.raises(ValueError):
            validate_value(descriptor, value)


@pytest.mark.parametrize(
    "general,project,expected",
    [(None, None, 5.0), (1.0, None, 1.0), (0.2, 0.3, 0.3)],
    ids=["default", "manual-general-preserved", "manual-project-wins"],
)
def test_effective_jev_deadline_reaches_service_provider_and_http_client(
    decision_db, monkeypatch, general, project, expected
):
    """Exercise actual service/provider/client with fake transport and credential only."""
    from types import SimpleNamespace

    import httpx

    from local_control_center.decision_engine import providers, service
    from local_control_center.decision_engine.config import resolve_config
    from local_control_center.settings.repository import SettingsRepository

    repo = SettingsRepository(decision_db)
    if general is not None:
        repo.set_value("decision_engine.timeout_seconds", "general", None, general)
    if project is not None:
        repo.set_value("decision_engine.timeout_seconds", "project", "p1", project)
    overrides_before = [
        tuple(row)
        for row in decision_db.execute(
            "SELECT scope,scope_id,value_json,assigned_by FROM settings_value "
            "WHERE key='decision_engine.timeout_seconds' ORDER BY scope"
        )
    ]
    observed = {"deadlines": [], "provider": [], "client": [], "transport": [], "calls": []}
    actual_timeout = asyncio.timeout
    actual_provider = providers.JevDecisionProvider
    actual_client = httpx.AsyncClient

    def deadline(seconds):
        observed["deadlines"].append(seconds)
        return actual_timeout(seconds)

    def response(request):
        observed["calls"].append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": "c1",
                        "probabilities": {"c0": 0.05, "c1": 0.95},
                        "confidence": 0.95,
                    }
                },
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        )

    def transport_factory(*, timeout):
        observed["transport"].append(timeout)
        return httpx.MockTransport(response)

    def provider_factory(config):
        observed["provider"].append(config.timeout_seconds)
        return actual_provider(
            config,
            credential_resolver=SimpleNamespace(
                resolve=lambda _reference: SimpleNamespace(configured=True, value="offline-test-value")
            ),
        )

    def client_factory(**kwargs):
        observed["client"].append(kwargs["timeout"])
        assert isinstance(kwargs["transport"], httpx.MockTransport)
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return actual_client(**kwargs)

    monkeypatch.setattr(providers, "JevAsyncTransport", transport_factory)
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(asyncio, "timeout", deadline)
    monkeypatch.setattr(service, "JevDecisionProvider", provider_factory)
    monkeypatch.setattr(service, "real_jev_calls_enabled", lambda: True)
    engine = service.ShadowDecisionEngine(decision_db, config=resolve_config(decision_db, "p1"))
    receipt = asyncio.run(engine.observe(request_for(project_id="p1")))
    assert receipt["reasonCode"] == "recommendation_usable"
    assert receipt["policy"]["timeoutSeconds"] == expected
    assert observed["deadlines"] == [expected, expected]
    assert observed["provider"] == observed["client"] == observed["transport"] == [expected]
    assert len(observed["calls"]) == 1
    assert overrides_before == [
        tuple(row)
        for row in decision_db.execute(
            "SELECT scope,scope_id,value_json,assigned_by FROM settings_value "
            "WHERE key='decision_engine.timeout_seconds' ORDER BY scope"
        )
    ]


def test_migration_is_idempotent_and_preserves_historical_receipts(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.repository import DecisionRepository
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from local_control_center.shared.migrations import init_phase74_schema, initialize_platform_schema

    receipt = asyncio.run(
        ShadowDecisionEngine(decision_db, config=DecisionConfig(enabled=True), provider=FakeJev()).observe(
            request_for()
        )
    )
    init_phase74_schema(decision_db)
    initialize_platform_schema(decision_db)
    assert DecisionRepository(decision_db).get(receipt["decisionId"]) == receipt


def test_typed_report_and_page_keep_every_key_the_handlers_build(decision_db):
    """Un ``response_model`` descarta en silencio lo que no declara.

    Con receipts y un outcome reales, el modelo tiene que devolver exactamente lo que arman
    ``decision_report`` y la página de receipts: si alguien agrega una métrica sin tocar el modelo,
    este test cae antes de que la clave desaparezca de la API.
    """
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.models import (
        DecisionListResponse,
        DecisionOutcome,
        DecisionReportResponse,
    )
    from local_control_center.decision_engine.reporting import decision_report
    from local_control_center.decision_engine.repository import DecisionRepository
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(decision_db, config=DecisionConfig(enabled=True), provider=FakeJev())
    first = asyncio.run(engine.observe(request_for(routing_latency_ms=12.5)))
    asyncio.run(engine.observe(request_for()))
    repository = DecisionRepository(decision_db)
    repository.record_outcome(
        first["decisionId"], DecisionOutcome(evidence_ref="run-1", execution_succeeded=True)
    )

    report = decision_report(decision_db)
    typed_report = DecisionReportResponse.model_validate(report).model_dump(by_alias=True, mode="json")
    assert typed_report == json.loads(json.dumps(report))

    receipts = repository.list_receipts()
    page = {"items": receipts, "nextAfter": receipts[-1]["sequence"]}
    typed_page = DecisionListResponse.model_validate(page).model_dump(by_alias=True, mode="json")
    assert typed_page == json.loads(json.dumps(page))
