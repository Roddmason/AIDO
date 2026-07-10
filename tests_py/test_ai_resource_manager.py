from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now


def open_initialized_connection(tmp_path: Path):
    connection = open_sqlite_connection(tmp_path / "platform.sqlite")
    initialize_platform_schema(connection)
    return connection


def register_model(
    manager: AIResourceManager,
    *,
    provider_id: str,
    model: str,
    runtime: str,
    locality: str,
    input_price_per_mtok: float | None,
    output_price_per_mtok: float | None,
    quality_score: float,
    success_rate: float,
    rework_rate: float = 0.0,
    context_window: int = 128000,
    max_output_tokens: int = 4096,
    capabilities: list[str] | None = None,
    privacy_level: str = "remote_allowed",
) -> dict:
    return manager.upsert_model_performance(
        {
            "providerId": provider_id,
            "model": model,
            "runtime": runtime,
            "capabilities": capabilities or ["chat", "code", "review"],
            "contextWindow": context_window,
            "maxOutputTokens": max_output_tokens,
            "inputPricePerMtok": input_price_per_mtok,
            "outputPricePerMtok": output_price_per_mtok,
            "observedLatencyMs": 900 if locality == "local" else 1400,
            "observedSuccessRate": success_rate,
            "reworkRate": rework_rate,
            "qualityScore": quality_score,
            "locality": locality,
            "privacyLevel": privacy_level,
            "evidence": [{"id": f"seed-{provider_id}-{model}", "kind": "manual_seed"}],
        }
    )


def configure_remote_provider_for_selection(
    connection,
    *,
    provider_id: str,
    model: str,
) -> None:
    store = ProviderAccountStore(connection)
    runtime = RuntimeConfigRepository(connection)
    now = utc_now()
    runtime.set_runtime_setting("runtime.remote.enabled", True)
    if provider_id == "nvidia_nim":
        runtime.set_runtime_setting("runtime.nvidia.enabled", True)
    store.upsert_provider_account(
        {
            "providerId": provider_id,
            "displayName": provider_id,
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "baseUrl": f"https://{provider_id}.test/v1",
            "credentialRef": "env:AIDO_TEST_API_KEY",
            "enabled": True,
            "healthStatus": "healthy",
            "lastHealthCheckAt": now,
        }
    )
    store.upsert_model(
        {
            "providerId": provider_id,
            "model": model,
            "displayName": model,
            "modelFamily": "test",
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "supportsTools": True,
            "supportsJson": True,
            "supportsStreaming": True,
            "supportsVision": False,
            "supportsEmbeddings": False,
            "supportsRerank": False,
            "supportsReasoning": False,
            "supportsThinking": False,
            "effortLevels": [],
            "inputPricePerMtok": 0.0,
            "outputPricePerMtok": 0.0,
            "freeTier": True,
            "freeTierNotes": "test provider",
            "enabled": True,
            "source": "test",
        }
    )
    runtime.upsert_installation(
        {
            "runtimeId": provider_id,
            "kind": "api",
            "enabled": True,
            "capabilities": ["chat"],
            "preferredRoles": ["product_owner", "developer", "analyst"],
            "healthStatus": "healthy",
            "lastHealthCheckAt": now,
        }
    )
    connection.execute(
        """
        INSERT INTO runtime_capabilities (id, runtime, capability, enabled, metadata, created_at, updated_at)
        VALUES (?, ?, 'chat', 1, '{}', ?, ?)
        ON CONFLICT(runtime, capability) DO UPDATE SET enabled = 1, updated_at = excluded.updated_at
        """,
        (f"{provider_id}:chat", provider_id, now, now),
    )


def test_phase44_schema_adds_ai_resource_manager_tables(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        cost_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(ai_cost_observations)").fetchall()
        }

    assert 44 in migrations
    assert {
        "ai_model_performance",
        "ai_routing_decisions",
        "ai_cost_observations",
        "ai_prompt_profiles",
        "ai_context_summaries",
    } <= tables
    assert {"estimated_cost_usd", "actual_cost_usd", "token_status", "usage_source"} <= cost_columns


def test_low_risk_prefers_cheap_local_without_reviewer(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ollama",
            model="qwen2.5-coder",
            runtime="local",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.72,
            success_rate=0.82,
        )
        register_model(
            manager,
            provider_id="openai",
            model="gpt-strong",
            runtime="api",
            locality="remote",
            input_price_per_mtok=5.0,
            output_price_per_mtok=15.0,
            quality_score=0.96,
            success_rate=0.98,
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="small_refactor",
                risk_level="low",
                routing_policy="economy",
                context_tokens_estimate=4000,
                required_capabilities=["code"],
                budget_remaining_usd=0.05,
            )
        )

    assert decision["selected"]["providerId"] == "ollama"
    assert decision["selected"]["locality"] == "local"
    assert decision["costTier"] == "cheap"
    assert decision["reviewerSelection"] is None
    assert decision["multiModelQuorum"] is False
    assert decision["approvalRequired"] is False
    assert decision["policyResult"]["mode"] == "economy"


def test_high_risk_prefers_strong_model_and_requires_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ollama",
            model="qwen2.5-coder",
            runtime="local",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.70,
            success_rate=0.80,
        )
        register_model(
            manager,
            provider_id="openai",
            model="gpt-strong",
            runtime="api",
            locality="remote",
            input_price_per_mtok=5.0,
            output_price_per_mtok=15.0,
            quality_score=0.97,
            success_rate=0.98,
        )
        register_model(
            manager,
            provider_id="anthropic",
            model="claude-reviewer",
            runtime="api",
            locality="remote",
            input_price_per_mtok=3.0,
            output_price_per_mtok=12.0,
            quality_score=0.94,
            success_rate=0.96,
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="openai",
            model="gpt-strong",
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="anthropic",
            model="claude-reviewer",
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="security_sensitive_change",
                risk_level="high",
                context_tokens_estimate=12000,
                required_capabilities=["code", "review"],
                budget_remaining_usd=2.0,
            )
        )

    assert decision["selected"]["providerId"] == "openai"
    assert decision["costTier"] == "premium"
    assert decision["reviewerSelection"]["providerId"] == "anthropic"
    assert decision["reviewerSelection"]["model"] == "claude-reviewer"
    assert decision["scoreBreakdown"]["qualityScore"] > decision["scoreBreakdown"]["costEfficiencyScore"]
    assert decision["policyResult"]["opaqueMlUsed"] is False
    assert decision["policyResult"]["scoring"] == "deterministic_explainable"


def test_score_breakdown_exposes_all_required_routing_factors(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ollama",
            model="qwen2.5-coder",
            runtime="local",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.72,
            success_rate=0.82,
            rework_rate=0.08,
            capabilities=["chat", "code"],
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="small_refactor",
                risk_level="low",
                routing_policy="economy",
                context_tokens_estimate=4000,
                required_capabilities=["code"],
                privacy_level="local_private",
                budget_remaining_usd=0.05,
            )
        )

    assert {
        "capabilityMatchScore",
        "costKnownScore",
        "latencyScore",
        "historicalSuccessScore",
        "reworkRateScore",
        "contextSizeScore",
        "privacyLocalityScore",
        "taskRiskScore",
    } <= set(decision["scoreBreakdown"])
    assert decision["scoreBreakdown"]["capabilityMatchScore"] == pytest.approx(1.0)
    assert decision["scoreBreakdown"]["costKnownScore"] == pytest.approx(1.0)
    assert decision["scoreBreakdown"]["privacyLocalityScore"] == pytest.approx(1.0)


def test_economy_policy_rejects_unknown_remote_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="unknown_remote",
            model="opaque-price",
            runtime="api",
            locality="remote",
            input_price_per_mtok=None,
            output_price_per_mtok=None,
            quality_score=0.90,
            success_rate=0.93,
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="unknown_remote",
            model="opaque-price",
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="analysis",
                risk_level="low",
                routing_policy="economy",
                context_tokens_estimate=8000,
                required_capabilities=["chat"],
            )
        )

    assert decision["selected"] is None
    assert decision["approvalRequired"] is False
    assert decision["policyResult"]["mode"] == "economy"
    assert decision["policyResult"]["unknownCostPolicy"]["action"] == "reject"
    assert decision["rejected"][0]["reason"] == "unknown_remote_cost_rejected_by_policy"


@pytest.mark.parametrize("routing_policy", ["economy", "balanced", "critical", "maximum"])
def test_named_routing_policies_are_accepted_and_reported(
    tmp_path: Path,
    routing_policy: str,
) -> None:
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ollama",
            model="qwen2.5-coder",
            runtime="local",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.80,
            success_rate=0.90,
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="implementation",
                risk_level="medium",
                routing_policy=routing_policy,
                context_tokens_estimate=6000,
                required_capabilities=["code"],
            )
        )

    assert decision["selected"]["providerId"] == "ollama"
    assert decision["policyResult"]["mode"] == routing_policy


def test_unknown_remote_cost_requires_approval_unless_policy_allows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="unknown_remote",
            model="opaque-price",
            runtime="api",
            locality="remote",
            input_price_per_mtok=None,
            output_price_per_mtok=None,
            quality_score=0.90,
            success_rate=0.93,
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="unknown_remote",
            model="opaque-price",
        )

        approval_decision = manager.select_resource(
            AIResourceRequest(
                task_type="analysis",
                risk_level="medium",
                context_tokens_estimate=8000,
                required_capabilities=["chat"],
            )
        )
        policy_decision = manager.select_resource(
            AIResourceRequest(
                task_type="analysis",
                risk_level="medium",
                context_tokens_estimate=8000,
                required_capabilities=["chat"],
                allow_unknown_cost=True,
                require_approval_for_unknown_cost=False,
            )
        )

    assert approval_decision["selected"]["providerId"] == "unknown_remote"
    assert approval_decision["approvalRequired"] is True
    assert approval_decision["policyResult"]["unknownCostPolicy"]["action"] == "require_approval"
    assert policy_decision["approvalRequired"] is False
    assert policy_decision["policyResult"]["unknownCostPolicy"]["action"] == "allow"


PREMIUM_CONTEXT_TOKENS = 100_000


def _premium_request(**overrides) -> AIResourceRequest:
    return AIResourceRequest(
        task_type="analysis",
        risk_level="medium",
        context_tokens_estimate=PREMIUM_CONTEXT_TOKENS,
        required_capabilities=["chat"],
        **overrides,
    )


def _register_priced_remote(manager: AIResourceManager, connection) -> None:
    """Un remoto con precio CONOCIDO y caro: $30/Mtok * 100k tokens = $3.00 estimados."""
    register_model(
        manager,
        provider_id="premium_remote",
        model="expensive-frontier",
        runtime="api",
        locality="remote",
        input_price_per_mtok=30.0,
        output_price_per_mtok=0.0,
        quality_score=0.95,
        success_rate=0.95,
    )
    configure_remote_provider_for_selection(
        connection, provider_id="premium_remote", model="expensive-frontier"
    )


def test_premium_selection_requires_approval_when_policy_sets_a_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un costo CONOCIDO por encima del umbral del rol exige aprobación; por debajo, no."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_priced_remote(manager, connection)

        gated = manager.select_resource(_premium_request(require_approval_over_usd=1.00))
        allowed = manager.select_resource(_premium_request(require_approval_over_usd=10.00))

    assert gated["selected"]["model"] == "expensive-frontier"
    assert gated["estimatedCostUsd"] == pytest.approx(3.0)
    assert gated["approvalRequired"] is True
    premium = gated["policyResult"]["premiumApproval"]
    assert premium["required"] is True
    assert premium["reason"] == "premium_cost_over_policy_threshold"
    assert premium["thresholdUsd"] == pytest.approx(1.00)
    assert premium["costTier"] == "premium"
    assert "Approval is required before execution." in gated["decisionReason"]

    assert allowed["approvalRequired"] is False
    assert allowed["policyResult"]["premiumApproval"]["reason"] == "cost_within_policy_threshold"


def test_premium_gate_is_inert_without_a_policy_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin umbral en la política, un costo conocido nunca fuerza aprobación (regresión de db4cd54b)."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_priced_remote(manager, connection)

        no_threshold = manager.select_resource(_premium_request())
        zero_threshold = manager.select_resource(_premium_request(require_approval_over_usd=0.0))

    for decision in (no_threshold, zero_threshold):
        assert decision["approvalRequired"] is False
        assert decision["policyResult"]["premiumApproval"]["required"] is False
        assert decision["policyResult"]["premiumApproval"]["reason"] == "no_premium_threshold_in_policy"
        assert decision["policyResult"]["premiumApproval"]["thresholdUsd"] is None


def test_unknown_cost_is_never_treated_as_cheap_enough_to_pass_the_premium_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un costo desconocido NO es 0: no pasa el umbral por barato, lo escala la política de desconocido."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="unknown_remote",
            model="opaque-price",
            runtime="api",
            locality="remote",
            input_price_per_mtok=None,
            output_price_per_mtok=None,
            quality_score=0.90,
            success_rate=0.93,
        )
        configure_remote_provider_for_selection(
            connection, provider_id="unknown_remote", model="opaque-price"
        )

        decision = manager.select_resource(_premium_request(require_approval_over_usd=1000.00))

    assert decision["estimatedCostUsd"] is None
    assert decision["costTier"] == "unknown"
    premium = decision["policyResult"]["premiumApproval"]
    assert premium["estimatedCostUsd"] is None
    assert premium["required"] is False
    assert premium["reason"] == "cost_unknown_deferred_to_unknown_cost_policy"
    # El umbral gigante haría pasar a un costo coaccionado a 0; la aprobación sigue siendo obligatoria.
    assert decision["approvalRequired"] is True
    assert decision["policyResult"]["unknownCostPolicy"]["action"] == "require_approval"


def test_local_model_under_threshold_never_trips_the_premium_gate(
    tmp_path: Path,
) -> None:
    """El modo Force local es la salida barata: un modelo local sin costo no exige aprobación."""
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ollama_local",
            model="llama-local",
            runtime="ollama",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.72,
            success_rate=0.80,
            privacy_level="local_private",
        )

        decision = manager.select_resource(
            _premium_request(privacy_level="local_private", require_approval_over_usd=0.01)
        )

    assert decision["selected"]["providerId"] == "ollama_local"
    assert decision["localVsRemote"] == "local"
    assert decision["costTier"] == "cheap"
    assert decision["approvalRequired"] is False
    assert decision["policyResult"]["premiumApproval"]["required"] is False


def test_remote_api_provider_must_be_executable_before_selection(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="nvidia_nim",
            model="nvidia/nemotron-coder",
            runtime="api",
            locality="remote",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.96,
            success_rate=0.98,
            capabilities=["chat"],
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                risk_level="medium",
                context_tokens_estimate=4000,
                required_capabilities=["chat"],
                privacy_level="remote_allowed",
            )
        )

    assert decision["selected"] is None
    assert decision["decisionReason"] == "No AI resource satisfied policy and capability filters."
    assert decision["rejected"]
    assert decision["rejected"][0]["providerId"] == "nvidia_nim"
    assert "credential" in decision["rejected"][0]["reason"].lower()


def test_observed_failure_with_evidence_lowers_model_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="openai",
            model="gpt-primary",
            runtime="api",
            locality="remote",
            input_price_per_mtok=2.0,
            output_price_per_mtok=8.0,
            quality_score=0.95,
            success_rate=0.97,
        )
        register_model(
            manager,
            provider_id="anthropic",
            model="claude-alternative",
            runtime="api",
            locality="remote",
            input_price_per_mtok=2.5,
            output_price_per_mtok=9.0,
            quality_score=0.91,
            success_rate=0.92,
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="openai",
            model="gpt-primary",
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="anthropic",
            model="claude-alternative",
        )

        before = manager.select_resource(
            AIResourceRequest(
                task_type="implementation",
                risk_level="medium",
                context_tokens_estimate=10000,
                required_capabilities=["code"],
                budget_remaining_usd=1.0,
            )
        )
        manager.record_outcome(
            provider_id="openai",
            model="gpt-primary",
            success=False,
            rework=True,
            quality_score=0.30,
            latency_ms=2200,
            evidence_ref="evidence-package-123",
        )
        after = manager.select_resource(
            AIResourceRequest(
                task_type="implementation",
                risk_level="medium",
                context_tokens_estimate=10000,
                required_capabilities=["code"],
                budget_remaining_usd=1.0,
            )
        )

    assert before["selected"]["model"] == "gpt-primary"
    assert after["selected"]["model"] == "claude-alternative"
    failed_candidate = next(item for item in after["candidates"] if item["model"] == "gpt-primary")
    assert failed_candidate["scoreBreakdown"]["observedSuccessScore"] < 0.70
    assert failed_candidate["scoreBreakdown"]["reworkPenalty"] > 0


def test_cost_observation_without_provider_usage_keeps_tokens_unknown(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        observation = manager.record_cost_observation(
            provider_id="openai",
            model="gpt-unknown-usage",
            runtime="api",
            estimated_cost_usd=0.0123,
            actual_cost_usd=None,
            provider_usage=None,
            evidence_ref="usage-call-456",
        )
        row = connection.execute(
            "SELECT * FROM ai_cost_observations WHERE id = ?", (observation["id"],)
        ).fetchone()

    assert observation["usageSource"] == "unknown"
    assert observation["tokenStatus"] == "unknown"
    assert observation["inputTokens"] is None
    assert observation["outputTokens"] is None
    assert observation["totalTokens"] is None
    assert observation["estimatedCostUsd"] == pytest.approx(0.0123)
    assert observation["actualCostUsd"] is None
    assert row["input_tokens"] is None
    assert row["actual_cost_usd"] is None


def test_actual_cost_observations_adjust_future_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="openai",
            model="gpt-premium",
            runtime="api",
            locality="remote",
            input_price_per_mtok=0.01,
            output_price_per_mtok=0.01,
            quality_score=0.96,
            success_rate=0.96,
        )
        register_model(
            manager,
            provider_id="anthropic",
            model="claude-efficient",
            runtime="api",
            locality="remote",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.90,
            success_rate=0.92,
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="openai",
            model="gpt-premium",
        )
        configure_remote_provider_for_selection(
            connection,
            provider_id="anthropic",
            model="claude-efficient",
        )

        request = AIResourceRequest(
            task_type="implementation",
            risk_level="medium",
            context_tokens_estimate=10000,
            required_capabilities=["code"],
            budget_remaining_usd=0.05,
        )
        before = manager.select_resource(request)
        manager.record_cost_observation(
            provider_id="openai",
            model="gpt-premium",
            runtime="api",
            estimated_cost_usd=before["estimatedCostUsd"],
            actual_cost_usd=0.05,
            provider_usage={"input_tokens": 10000, "output_tokens": 1000},
            evidence_ref="usage-evidence-789",
        )
        after = manager.select_resource(request)
        premium_candidate = next(item for item in after["candidates"] if item["model"] == "gpt-premium")
        performance_row = connection.execute(
            """
            SELECT total_observed_tokens
            FROM ai_model_performance
            WHERE provider_id = 'openai' AND model = 'gpt-premium' AND runtime = 'api'
            """
        ).fetchone()

    assert before["selected"]["model"] == "gpt-premium"
    assert after["selected"]["model"] == "claude-efficient"
    assert premium_candidate["estimatedCostUsd"] == pytest.approx(0.05)
    assert premium_candidate["costEstimateSource"] == "observed_actual_usage"
    assert premium_candidate["scoreBreakdown"]["costEfficiencyScore"] == 0.0
    assert performance_row["total_observed_tokens"] == 11000


def test_model_router_uses_ai_resource_manager_when_profiles_exist(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ollama",
            model="qwen2.5-coder",
            runtime="local",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.72,
            success_rate=0.82,
        )
        register_model(
            manager,
            provider_id="openai",
            model="gpt-strong",
            runtime="api",
            locality="remote",
            input_price_per_mtok=5.0,
            output_price_per_mtok=15.0,
            quality_score=0.96,
            success_rate=0.98,
        )

        result = ModelRouter(connection).preview(
            RoutingRequest(
                taskType="small_refactor",
                riskLevel="low",
                contextTokensEstimate=4000,
                requiresCodeEdit=True,
                budgetRemainingUsd=0.05,
            ),
            record=True,
        )
        ai_decision_count = connection.execute("SELECT COUNT(*) FROM ai_routing_decisions").fetchone()[0]
        gateway_decision = connection.execute(
            "SELECT * FROM routing_decisions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    assert result["selected"] == {
        "provider": "ollama",
        "model": "qwen2.5-coder",
        "runtime": "local",
        "effort": None,
    }
    assert result["policyResult"]["source"] == "ai_resource_manager"
    assert result["policyResult"]["opaqueMlUsed"] is False
    assert ai_decision_count == 1
    assert gateway_decision["selected_provider"] == "ollama"


def test_model_router_ai_path_still_honors_the_role_premium_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con perfiles AI, el veredicto de aprobacion lo da el AIResourceManager: el umbral del rol debe llegarle.

    De lo contrario una seleccion cara pasaria sin aprobacion por el path AI mientras el path clasico si la exige.
    """
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_priced_remote(manager, connection)
        RoutingProfileStore(connection).patch_role_policy(
            "developer", {"requiresApprovalOverUsd": 0.50, "allowCli": False}
        )

        result = ModelRouter(connection).preview(
            RoutingRequest(
                role="developer",
                taskType="analysis",
                contextTokensEstimate=PREMIUM_CONTEXT_TOKENS,
            ),
            record=False,
        )

    assert result["policyResult"]["source"] == "ai_resource_manager"
    assert result["selected"]["model"] == "expensive-frontier"
    assert result["estimatedCostUsd"] == pytest.approx(3.0)
    assert result["policyResult"]["requiresApproval"] is True
