"""Active Jev selection must use validated candidates and never an implicit fallback.

SQLite, filtering, pricing, receipts and revalidation are real. Only runtime status
and the decision-provider boundary are controlled; no inference is executed.

@author Rodrigo Mason
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.decision_engine.models import DecisionResult
from local_control_center.decision_engine.providers import ProviderError
from local_control_center.decision_engine.repository import DecisionRepository
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now

CHEAP_PROVIDER = "openai_compatible"
PREMIUM_PROVIDER = "openrouter"
MODELS = {CHEAP_PROVIDER: "cheap-model", PREMIUM_PROVIDER: "premium-model"}


class ControlledJev:
    """Return a contrary choice, optionally changing live evidence before completion."""

    def __init__(self, connection):
        self.connection = connection
        self.preferred_provider = PREMIUM_PROVIDER
        self.requests = []
        self.transactions = []
        self.on_decide = None
        self.outcome = "valid"

    async def decide(self, request):
        self.requests.append(request)
        self.transactions.append(self.connection.in_transaction)
        if self.on_decide:
            self.on_decide()
        if self.outcome == "timeout":
            await asyncio.Event().wait()
        if self.outcome == "failure":
            raise ProviderError("fixture_provider_unavailable")
        ids = [candidate.id for candidate in request.candidates]
        selected = next(
            (identity for identity in ids if identity.startswith(f"{self.preferred_provider}:")),
            ids[0],
        )
        ranking = (selected, *(identity for identity in ids if identity != selected))
        probabilities = {
            identity: (0.96 if identity == selected else 0.04 / (len(ids) - 1)) for identity in ids
        }
        if len(ids) == 1:
            probabilities[selected] = 1.0
        margin = probabilities[selected] - (probabilities[ranking[1]] if len(ids) > 1 else 0.0)
        return DecisionResult(
            selected="unoffered:model" if self.outcome == "invalid" else selected,
            ranking=ranking,
            probabilities=probabilities,
            confidence=0.2 if self.outcome == "low_confidence" else 0.96,
            margin=margin,
            engine="jev",
            provider="jev",
            model="jev-1.13.0",
            version="1.13.0",
            decision_type=request.decision_type,
            reason_code="fixture_ranked",
        )


@pytest.fixture
def selection_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setattr("local_control_center.decision_engine.service.real_jev_calls_enabled", lambda: True)
    monkeypatch.setenv("AIDO_TEST_API_KEY", "fixture-selection-credential")
    with closing(open_sqlite_connection(tmp_path / "selection.sqlite")) as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE model_catalog SET enabled = 0")
        connection.execute("UPDATE ai_model_performance SET enabled = 0")
        settings = SettingsRepository(connection)
        for key, value in {
            "decision_engine.enabled": True,
            "decision_engine.mode": "runtime_selection",
            "decision_engine.provider": "jev",
            "decision_engine.jev.enabled": True,
            "decision_engine.timeout_seconds": 0.1,
            "runtime.remote.enabled": True,
            "project.runtime.remote.enabled": True,
            "project.routing.forceLocal": False,
        }.items():
            settings.set_value(key, "general", None, value)
        store = ProviderAccountStore(connection)
        statuses = {}
        for provider_id, model in MODELS.items():
            store.upsert_provider_account(
                {
                    "providerId": provider_id,
                    "displayName": provider_id,
                    "providerType": "api",
                    "providerFamily": provider_id,
                    "apiFormat": "openai_compatible",
                    "baseUrl": f"https://{provider_id.replace('_', '-')}.example.invalid/v1",
                    "credentialRef": "env:AIDO_TEST_API_KEY",
                    "enabled": True,
                    "healthStatus": "healthy",
                    "lastHealthCheckAt": utc_now(),
                }
            )
            store.upsert_model(
                {
                    "providerId": provider_id,
                    "model": model,
                    "contextWindow": 128000,
                    "maxOutputTokens": 4096,
                    "inputPricePerMtok": 30.0 if provider_id == PREMIUM_PROVIDER else 0.1,
                    "outputPricePerMtok": 0.0,
                    "enabled": True,
                    "freeTier": False,
                }
            )
            statuses[provider_id] = {
                "id": provider_id,
                "kind": "api",
                "providerFamily": provider_id,
                "configured": True,
                "available": True,
                "executable": True,
                "productOwnerExecutable": True,
                "healthStatus": "healthy",
                "healthCheckedAt": utc_now(),
                "models": [model],
                "capabilities": ["chat"],
            }
            record_model_execution(
                connection, provider_id=provider_id, model=model, success=True, source="test_prompt"
            )
        RoutingProfileStore(connection).upsert_role_policy(
            {
                "role": "developer",
                "preferred": [{"provider": CHEAP_PROVIDER, "model": MODELS[CHEAP_PROVIDER]}],
                "fallback": [{"provider": PREMIUM_PROVIDER, "model": MODELS[PREMIUM_PROVIDER]}],
                "allowApi": True,
                "allowRemote": True,
                "allowLocal": False,
                "allowCli": False,
                "allowUnknownCost": False,
                "freeTierOnly": False,
                "maxCostPerTaskUsd": 10.0,
            }
        )
        monkeypatch.setattr(
            RuntimeStatusService,
            "list_provider_statuses",
            lambda _self, *, project_id=None: list(statuses.values()),
        )
        jev = ControlledJev(connection)
        monkeypatch.setattr(
            "local_control_center.decision_engine.service.JevDecisionProvider", lambda _config: jev
        )
        yield SimpleNamespace(
            connection=connection, store=store, settings=settings, statuses=statuses, jev=jev
        )


def request_for_selection(**overrides):
    """AIDO authorizes both accounts; its stored preference deliberately opposes Jev."""
    values = {
        "task_type": "feature",
        "risk_level": "medium",
        "required_capabilities": ["chat"],
        "context_tokens_estimate": 100000,
        "privacy_level": "remote_allowed",
        "preferred_provider_ids": [CHEAP_PROVIDER],
        "preferred_resources": [{"provider": CHEAP_PROVIDER, "model": MODELS[CHEAP_PROVIDER]}],
        "budget_remaining_usd": 10.0,
        "require_approval_over_usd": 1.0,
    }
    return AIResourceRequest(**{**values, **overrides})


def select(lane, **request_overrides):
    """Only the explicit execution path may consult the controlled Jev provider."""
    return AIResourceManager(lane.connection).select_resource(
        request_for_selection(**request_overrides), record=True, allow_decision_inference=True
    )


def receipts(lane):
    """Read durable decision evidence without invoking the engine again."""
    return DecisionRepository(lane.connection).list_receipts()


@pytest.mark.parametrize("execute", [False, True])
def test_cold_start_prevalidates_only_on_execution_and_filters_before_probe(
    selection_lane, monkeypatch, execute
):
    lane = selection_lane
    lane.connection.execute("DELETE FROM model_execution_health")
    lane.connection.commit()
    calls = []

    def prevalidate(connection, *, models, runtime_statuses, request):
        calls.append(models)
        assert not connection.in_transaction
        assert {model["providerId"] for model in models} == {CHEAP_PROVIDER}
        record_model_execution(
            connection,
            provider_id=CHEAP_PROVIDER,
            model=MODELS[CHEAP_PROVIDER],
            success=True,
            source="test_prompt",
        )
        return {"attempts": 1, "budgetSpentUsd": 0.002, "validated": [{"providerId": CHEAP_PROVIDER}]}

    monkeypatch.setattr("local_control_center.agents.runtime_preflight.prevalidate_candidates", prevalidate)
    result = AIResourceManager(lane.connection).select_resource(
        request_for_selection(allowed_provider_ids=[CHEAP_PROVIDER]),
        record=execute,
        allow_decision_inference=execute,
    )
    assert len(calls) == int(execute)
    if execute:
        assert result["selected"]["providerId"] == CHEAP_PROVIDER
        assert result["policyResult"]["runtimePreflight"]["attempts"] == 1
        assert lane.jev.requests[0].candidates[0].id.startswith(CHEAP_PROVIDER + ":")
    else:
        assert result["selected"] is None


@pytest.mark.parametrize("during_selection", [False, True])
def test_project_role_override_is_respected_and_revalidated(selection_lane, during_selection):
    from local_control_center.agents.repository import AgentsRepository

    lane = selection_lane
    repository = AgentsRepository(lane.connection)
    repository.upsert_agent_profile(
        {
            "id": "base-developer",
            "role": "developer",
            "runtimeMode": "hybrid",
            "allowedProviders": ["*"],
            "allowedRuntimes": ["*"],
            "maxCostPerRun": 10.0,
        }
    )

    def restrict():
        repository.upsert_agent_profile_project_override(
            project_id="project-role-override",
            profile_id="base-developer",
            body={"allowedProviders": [CHEAP_PROVIDER]},
        )
        lane.connection.commit()

    if during_selection:
        lane.jev.on_decide = restrict
    else:
        restrict()
    decision = select(lane, project_id="project-role-override", agent_profile_id="base-developer")
    if during_selection:
        assert decision["selected"] is None
    else:
        assert decision["selected"]["providerId"] == CHEAP_PROVIDER


def test_project_zero_budget_prevents_cold_start_probe(selection_lane, monkeypatch):
    from local_control_center.agents.repository import AgentsRepository

    lane = selection_lane
    repository = AgentsRepository(lane.connection)
    repository.upsert_agent_profile(
        {"id": "base-developer", "role": "developer", "runtimeMode": "hybrid", "maxCostPerRun": 10.0}
    )
    repository.upsert_agent_profile_project_override(
        project_id="restricted",
        profile_id="base-developer",
        body={"requiresApprovalOverUsd": 0, "maxCostPerRun": 0},
    )
    lane.connection.execute("DELETE FROM model_execution_health")
    lane.connection.commit()
    calls = []
    monkeypatch.setattr(
        "local_control_center.agents.runtime_preflight._in_quality_environment", lambda: False
    )
    monkeypatch.setattr(
        "local_control_center.agents.runtime_preflight._execute_probe", lambda *a, **k: calls.append(k)
    )
    result = select(lane, project_id="restricted", agent_profile_id="base-developer")
    assert not calls
    assert result["selected"] is None
    assert result["policyResult"]["runtimePreflight"]["attempts"] == 0


def test_auth_failed_account_is_excluded_even_after_preflight_has_enough_candidates(
    selection_lane, monkeypatch
):
    lane = selection_lane
    record_model_execution(
        lane.connection, PREMIUM_PROVIDER, "sibling", False, "model_gateway", http_status=401
    )
    # The shared selector/broker validator must protect accounts the bounded probe loop did not visit.
    monkeypatch.setattr(
        "local_control_center.agents.runtime_preflight.prevalidate_candidates",
        lambda *a, **k: {"validated": [{"providerId": CHEAP_PROVIDER}], "attempts": 0},
    )
    result = select(lane)
    assert result["selected"]["providerId"] == CHEAP_PROVIDER
    assert any(item.get("reason") == "provider_authentication_cooldown" for item in result["rejected"])


def test_jev_overrides_preferences_and_approval_uses_the_selected_models_cost(selection_lane):
    lane = selection_lane
    decision = select(lane)

    assert decision["selected"]["providerId"] == PREMIUM_PROVIDER
    assert decision["selected"]["model"] == MODELS[PREMIUM_PROVIDER]
    assert decision["estimatedCostUsd"] == pytest.approx(3.0)
    assert decision["approvalRequired"] is True
    assert decision["policyResult"]["premiumApproval"]["required"] is True
    assert len(lane.jev.requests) == 1
    assert lane.jev.transactions == [False]
    assert lane.jev.requests[0].effective_decision is None
    row = lane.connection.execute(
        "SELECT selected_provider, selected_model, approval_required FROM ai_routing_decisions WHERE id = ?",
        (decision["routingDecisionId"],),
    ).fetchone()
    assert tuple(row) == (PREMIUM_PROVIDER, MODELS[PREMIUM_PROVIDER], 1)
    evidence = receipts(lane)
    assert len(evidence) == 1
    assert evidence[0]["mode"] == "runtime_selection"
    assert evidence[0]["sourceDecisionId"] == decision["routingDecisionId"]
    assert evidence[0]["effectiveDecision"].startswith(f"{PREMIUM_PROVIDER}:")
    assert evidence[0]["fallbackUsed"] is False


@pytest.mark.parametrize("mode", ["runtime_selection", "shadow", "disabled"])
@pytest.mark.parametrize("approval_required", [False, True])
def test_decision_reason_names_effective_selector_and_preserves_approval(
    selection_lane, mode, approval_required
):
    lane = selection_lane
    lane.settings.set_value("decision_engine.mode", "general", None, mode)
    decision = select(lane, require_approval_over_usd=0.001 if approval_required else 10.0)

    active = mode == "runtime_selection"
    selected_provider = PREMIUM_PROVIDER if active else CHEAP_PROVIDER
    assert decision["selected"]["providerId"] == selected_provider
    assert decision["approvalRequired"] is approval_required
    reason = decision["decisionReason"]
    assert f"Selected {selected_provider}/{MODELS[selected_provider]} for feature " in reason
    assert ("Approval is required before execution." in reason) is approval_required
    if active:
        assert "Jev" in reason and "AIDO" in reason
        assert "validated" in reason.lower()
        assert "deterministic explainable scoring" not in reason
        assert decision["policyResult"]["scoring"] == "jev_among_validated_candidates"
        assert decision["policyResult"]["decisionEngine"]["reasonCode"] == "recommendation_usable"
    else:
        assert "using deterministic explainable scoring." in reason
        assert "Jev" not in reason
        assert decision["policyResult"]["scoring"] == "deterministic_explainable"
        assert lane.jev.requests == []
    persisted = lane.connection.execute(
        "SELECT decision_reason,approval_required FROM ai_routing_decisions WHERE id=?",
        (decision["routingDecisionId"],),
    ).fetchone()
    assert tuple(persisted) == (reason, int(approval_required))


@pytest.mark.parametrize("during_jev", [False, True])
def test_disabled_catalog_model_is_not_reintroduced_by_performance_profile(selection_lane, during_jev):
    lane = selection_lane
    AIResourceManager(lane.connection).upsert_model_performance(
        {
            "providerId": PREMIUM_PROVIDER,
            "model": MODELS[PREMIUM_PROVIDER],
            "runtime": "api",
            "capabilities": ["chat"],
            "enabled": True,
        }
    )

    def disable():
        model = next(
            item
            for item in lane.store.list_models(provider_id=PREMIUM_PROVIDER)
            if item["model"] == MODELS[PREMIUM_PROVIDER]
        )
        lane.store.patch_model(model["id"], {"enabled": False})

    if during_jev:
        lane.jev.on_decide = disable
    else:
        disable()
    decision = select(lane)
    if during_jev:
        assert decision["selected"] is None
    else:
        assert decision["selected"]["providerId"] == CHEAP_PROVIDER
        assert all(not item.id.startswith(f"{PREMIUM_PROVIDER}:") for item in lane.jev.requests[0].candidates)


@pytest.mark.parametrize("failure", ["unhealthy", "stale_health", "unvalidated", "http_404"])
def test_ineligible_models_are_removed_before_jev_sees_them(selection_lane, failure):
    lane = selection_lane
    if failure == "unhealthy":
        lane.statuses[PREMIUM_PROVIDER]["healthStatus"] = "offline"
    elif failure == "stale_health":
        lane.statuses[PREMIUM_PROVIDER]["healthCheckedAt"] = (
            datetime.now(UTC) - timedelta(days=1)
        ).isoformat()
    elif failure == "unvalidated":
        lane.connection.execute(
            "DELETE FROM model_execution_health WHERE provider_id = ?", (PREMIUM_PROVIDER,)
        )
    else:
        record_model_execution(
            lane.connection,
            provider_id=PREMIUM_PROVIDER,
            model=MODELS[PREMIUM_PROVIDER],
            success=False,
            source="test_prompt",
            http_status=404,
        )

    decision = select(lane)

    assert decision["selected"]["providerId"] == CHEAP_PROVIDER
    assert len(lane.jev.requests) == 1
    assert len(lane.jev.requests[0].candidates) == 1
    assert all(candidate.id.startswith(f"{CHEAP_PROVIDER}:") for candidate in lane.jev.requests[0].candidates)
    assert any(item["providerId"] == PREMIUM_PROVIDER for item in decision["rejected"])


def test_hard_role_block_survives_jev_selection(selection_lane):
    lane = selection_lane
    decision = select(lane, blocked_resources=[{"provider": PREMIUM_PROVIDER, "model": "*"}])

    assert decision["selected"]["providerId"] == CHEAP_PROVIDER
    assert len(lane.jev.requests) == 1
    assert len(lane.jev.requests[0].candidates) == 1
    assert lane.jev.requests[0].candidates[0].id.startswith(f"{CHEAP_PROVIDER}:")


def test_force_local_metadata_never_reaches_jev(selection_lane):
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

    lane = selection_lane
    privacy = ProductLoopCoordinator._resource_privacy_level({"forceLocal": True})
    decision = select(lane, privacy_level=privacy)
    assert decision["selected"] is None
    assert lane.jev.requests == []


def test_unvalidated_catalog_cannot_bootstrap_itself_into_a_selection(selection_lane):
    lane = selection_lane
    lane.connection.execute("DELETE FROM model_execution_health")
    decision = select(lane)

    assert decision["selected"] is None
    assert decision["candidates"] == []
    assert len(decision["rejected"]) == 2
    assert lane.jev.requests == []
    assert decision["decisionReason"]
    evidence = receipts(lane)
    assert len(evidence) == 1
    assert evidence[0]["effectiveDecision"] is None
    assert evidence[0]["fallbackUsed"] is False


def test_no_candidates_reports_automatic_validation_blocker(selection_lane, monkeypatch):
    lane = selection_lane
    lane.connection.execute("DELETE FROM model_execution_health")
    lane.connection.commit()
    monkeypatch.setattr(
        "local_control_center.agents.runtime_preflight.prevalidate_candidates",
        lambda *args, **kwargs: {
            "attempts": 0,
            "validated": [],
            "budgetSpentUsd": 0,
            "deferredReasonCounts": {"preflight_unknown_cost_requires_approval": 2},
        },
    )
    result = select(lane)
    assert result["selected"] is None
    assert "0 attempted" in result["decisionReason"]
    assert "unknown cost requires approval (2)" in result["decisionReason"]
    assert lane.jev.requests == []


@pytest.mark.parametrize("record,allow_inference", [(False, False), (False, True), (True, False)])
def test_preview_cannot_infer_or_promise_a_selected_model(selection_lane, record, allow_inference):
    lane = selection_lane
    decision = AIResourceManager(lane.connection).select_resource(
        request_for_selection(), record=record, allow_decision_inference=allow_inference
    )

    assert decision["selected"] is None
    assert decision["decisionReason"]
    assert len(decision["candidates"]) == 2
    assert decision["policyResult"]["opaqueMlUsed"] is False
    assert lane.jev.requests == []
    assert receipts(lane) == []


@pytest.mark.parametrize("record", [False, True])
def test_router_preview_without_profiles_cannot_use_its_classic_fallback(selection_lane, record):
    lane = selection_lane
    assert not ModelRouter(lane.connection)._has_ai_resource_profiles()
    result = ModelRouter(lane.connection).preview(
        RoutingRequest(role="developer", taskType="feature", contextTokensEstimate=100000),
        record=record,
    )

    assert result["selected"] is None
    assert result["decisionReason"]
    assert lane.jev.requests == []
    assert receipts(lane) == []


def test_router_manager_failure_does_not_recover_through_classic_selection(selection_lane, monkeypatch):
    lane = selection_lane
    manager_calls = []

    def broken_manager(_self, _request, **_kwargs):
        manager_calls.append(True)
        raise RuntimeError("fixture selector failure")

    monkeypatch.setattr(AIResourceManager, "select_resource", broken_manager)
    result = ModelRouter(lane.connection).preview(RoutingRequest(role="developer"), record=False)

    assert manager_calls == [True]
    assert result["selected"] is None
    assert lane.jev.requests == []


@pytest.mark.parametrize("change", ["health", "configuration", "execution_failure", "engine_settings"])
def test_jev_choice_is_rejected_when_live_evidence_changes_during_inference(selection_lane, change):
    lane = selection_lane

    def invalidate():
        if change == "health":
            lane.statuses[PREMIUM_PROVIDER]["healthStatus"] = "offline"
        elif change == "configuration":
            lane.store.patch_provider_account(
                PREMIUM_PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
            )
        elif change == "execution_failure":
            record_model_execution(
                lane.connection,
                provider_id=PREMIUM_PROVIDER,
                model=MODELS[PREMIUM_PROVIDER],
                success=False,
                source="test_prompt",
                http_status=404,
            )
        else:
            lane.settings.set_value("decision_engine.confidence_threshold", "general", None, 0.97)

    lane.jev.on_decide = invalidate
    decision = select(lane)

    assert len(lane.jev.requests) == 1
    assert decision["selected"] is None
    assert decision["decisionReason"]
    evidence = receipts(lane)
    assert len(evidence) == 1
    assert evidence[0]["recommendation"].startswith(f"{PREMIUM_PROVIDER}:")
    assert evidence[0]["effectiveDecision"] is None
    assert evidence[0]["reasonCode"] != "recommendation_usable"
    assert evidence[0]["fallbackUsed"] is False


@pytest.mark.parametrize("new_input_price,expected_cost", [(60.0, 6.0), (120.0, None)])
def test_price_changed_during_jev_is_rechecked_before_approval(
    selection_lane, new_input_price, expected_cost
):
    lane = selection_lane

    def update_price():
        model = next(item for item in lane.store.list_models(PREMIUM_PROVIDER) if item["enabled"])
        lane.store.patch_model(model["id"], {"inputPricePerMtok": new_input_price})

    lane.jev.on_decide = update_price
    decision = select(lane)

    assert len(lane.jev.requests) == 1
    if expected_cost is None:
        assert decision["selected"] is None
        assert receipts(lane)[0]["effectiveDecision"] is None
    else:
        assert decision["selected"]["providerId"] == PREMIUM_PROVIDER
        assert decision["estimatedCostUsd"] == pytest.approx(expected_cost)
        assert decision["approvalRequired"] is True


@pytest.mark.parametrize("outcome", ["invalid", "low_confidence", "failure", "timeout"])
def test_jev_cannot_fall_back_to_the_preferred_model_on_rejection(selection_lane, outcome):
    lane = selection_lane
    lane.jev.outcome = outcome
    decision = select(lane)

    assert len(lane.jev.requests) == 1
    assert decision["selected"] is None
    assert decision["decisionReason"]
    evidence = receipts(lane)
    assert len(evidence) == 1
    assert evidence[0]["effectiveDecision"] is None
    assert evidence[0]["fallbackUsed"] is False
    assert evidence[0]["reasonCode"] != "recommendation_usable"
