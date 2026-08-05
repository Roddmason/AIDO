from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now


def open_initialized_connection(tmp_path: Path):
    connection = open_sqlite_connection(tmp_path / "platform.sqlite")
    initialize_platform_schema(connection)
    connection.execute("UPDATE model_catalog SET enabled = 0")
    return connection


def enable_catalog_provider(connection, provider_id: str) -> None:
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = ?",
        (provider_id,),
    )


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


def advertise_executable_runtimes(
    monkeypatch: pytest.MonkeyPatch,
    *provider_ids: str,
    kind: str = "local",
) -> None:
    statuses = [
        {
            "id": provider_id,
            "kind": kind,
            "configured": True,
            "available": True,
            "executable": True,
            "capabilities": ["chat", "code_edit", "issue_to_patch", "review"],
            "reason": "Controlled executable runtime.",
        }
        for provider_id in provider_ids
    ]
    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        lambda _service, *, project_id=None: statuses,
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
            row["name"] for row in connection.execute("PRAGMA table_info(ai_cost_observations)").fetchall()
        }
        performance_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(ai_model_performance)").fetchall()
        }
        review_capabilities = {
            (row["runtime"], row["capability"])
            for row in connection.execute(
                """
                SELECT runtime, capability
                FROM runtime_capabilities
                WHERE runtime IN ('codex_cli', 'claude_code_cli')
                  AND capability = 'review'
                  AND enabled = 1
                """
            ).fetchall()
        }
        product_owner_policy = RoutingProfileStore(connection).get_role_policy("product_owner")

    assert 44 in migrations
    assert 51 in migrations
    assert {
        "ai_model_performance",
        "ai_routing_decisions",
        "ai_cost_observations",
        "ai_prompt_profiles",
        "ai_context_summaries",
    } <= tables
    assert {"estimated_cost_usd", "actual_cost_usd", "token_status", "usage_source"} <= cost_columns
    assert "profile_source" in performance_columns
    assert review_capabilities == {
        ("codex_cli", "review"),
        ("claude_code_cli", "review"),
    }
    assert product_owner_policy["allowCli"] is True


def test_phase51_preserves_explicit_product_owner_cli_denial(tmp_path: Path) -> None:
    with open_initialized_connection(tmp_path) as connection:
        policies = RoutingProfileStore(connection)
        policies.patch_role_policy("product_owner", {"allowCli": False})

        initialize_platform_schema(connection)

        assert policies.get_role_policy("product_owner")["allowCli"] is False


def test_catalogued_executable_cli_is_selectable_without_performance_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch"],
                "reason": "Controlled executable CLI runtime.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", executable_cli_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        assert connection.execute("SELECT COUNT(*) FROM ai_model_performance").fetchone()[0] == 0

        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )
        performance_rows = connection.execute("SELECT COUNT(*) FROM ai_model_performance").fetchone()[0]

    assert decision["selected"]["providerId"] == "codex_cli"
    assert decision["selected"]["runtime"] == "cli"
    assert decision["selected"]["qualityScore"] is None
    assert decision["selected"]["observedSuccessRate"] is None
    assert decision["selected"]["reworkRate"] is None
    assert decision["selected"]["performanceStatus"] == "unobserved_prior"
    assert decision["candidates"]
    assert decision["policyResult"]["candidateInventory"] == ("model_catalog_with_performance_overlay")
    assert performance_rows == 0


def test_provider_preference_breaks_equal_scores_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertise_executable_runtimes(
        monkeypatch,
        "codex_cli",
        "claude_code_cli",
        kind="cli",
    )

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        enable_catalog_provider(connection, "claude_code_cli")
        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allowed_provider_ids=["codex_cli", "claude_code_cli"],
                preferred_provider_ids=["codex_cli", "claude_code_cli"],
                allow_unknown_cost=True,
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "codex_cli"
    assert decision["policyResult"]["providerPreferenceOrder"] == [
        "codex_cli",
        "claude_code_cli",
    ]
    assert decision["policyResult"]["selectionOrder"] == (
        "preferred_resource_rank_asc_score_desc_provider_preference_asc_identity_asc"
    )


@pytest.mark.parametrize(
    ("runtime_capabilities", "required_capability"),
    [
        (["code_edit"], "chat"),
        (["chat"], "review"),
        (["code_edit"], "review"),
        (["chat"], "tools"),
    ],
)
def test_catalogued_runtime_does_not_invent_unadvertised_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_capabilities: list[str],
    required_capability: str,
) -> None:
    def limited_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": runtime_capabilities,
                "reason": "Controlled limited CLI runtime.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", limited_cli_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="capability_contract",
                required_capabilities=[required_capability],
                allow_unknown_cost=True,
            ),
            record=False,
        )

    assert decision["selected"] is None
    assert any(
        item["providerId"] == "codex_cli" and item["reason"] == f"missing_capabilities:{required_capability}"
        for item in decision["rejected"]
    )


def test_remote_ollama_endpoint_cannot_satisfy_local_private_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def remote_ollama_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "ollama_remote",
                "kind": "local",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat"],
                "reason": "Controlled remote Ollama endpoint.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", remote_ollama_statuses)

    with open_initialized_connection(tmp_path) as connection:
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "ollama_remote",
                "displayName": "Remote Ollama",
                "providerType": "local",
                "apiFormat": "ollama",
                "baseUrl": "https://ollama.example.test",
                "enabled": True,
                "metadata": {"endpointKind": "remote"},
            }
        )
        store.upsert_model(
            {
                "providerId": "ollama_remote",
                "model": "qwen-remote",
                "displayName": "Qwen Remote",
                "contextWindow": 32768,
                "maxOutputTokens": 4096,
                "inputPricePerMtok": 0.0,
                "outputPricePerMtok": 0.0,
                "enabled": True,
                "source": "test",
            }
        )

        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="private_analysis",
                required_capabilities=["chat"],
                privacy_level="local_private",
            ),
            record=False,
        )

    assert decision["selected"] is None
    rejected = next(item for item in decision["rejected"] if item["providerId"] == "ollama_remote")
    assert rejected["reason"] == "privacy_blocks_remote"


def test_local_ollama_selection_rejects_stale_catalog_model_not_reported_by_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def local_ollama_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "ollama",
                "kind": "local",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat"],
                "models": ["qwen3:14b"],
                "reason": "Controlled local Ollama endpoint.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", local_ollama_statuses)

    with open_initialized_connection(tmp_path) as connection:
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "ollama",
                "displayName": "Local Ollama",
                "providerType": "local",
                "apiFormat": "ollama",
                "baseUrl": "http://127.0.0.1:11434",
                "enabled": True,
                "metadata": {"endpointKind": "local"},
            }
        )
        for model in ("local_default", "qwen3:14b"):
            store.upsert_model(
                {
                    "providerId": "ollama",
                    "model": model,
                    "displayName": model,
                    "modelFamily": "ollama",
                    "enabled": True,
                    "source": "test",
                }
            )

        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                privacy_level="local_private",
                allowed_provider_ids=["ollama"],
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "ollama"
    assert decision["selected"]["model"] == "qwen3:14b"
    stale = next(item for item in decision["rejected"] if item["model"] == "local_default")
    assert stale["reason"].startswith("runtime_model_not_available:")


def test_legacy_performance_profile_does_not_hide_catalogued_executable_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mixed_runtime_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch"],
                "reason": "Controlled executable CLI runtime.",
            },
            {
                "id": "nvidia_nim",
                "kind": "api",
                "configured": False,
                "available": False,
                "executable": False,
                "capabilities": ["chat"],
                "reason": "Controlled unavailable API runtime.",
            },
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", mixed_runtime_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="nvidia_nim",
            model="legacy-profile",
            runtime="api",
            locality="remote",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.95,
            success_rate=0.95,
            capabilities=["chat"],
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "codex_cli"
    assert decision["policyResult"]["candidateInventory"] == ("model_catalog_with_performance_overlay")
    assert any(item["providerId"] == "nvidia_nim" for item in decision["rejected"])


def test_resource_request_rejects_provider_outside_agent_runtime_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": provider_id,
                "kind": "cli" if provider_id == "codex_cli" else "api",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit"] if provider_id == "codex_cli" else ["chat"],
                "reason": "Controlled executable runtime.",
            }
            for provider_id in ("codex_cli", "unsupported_chat")
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", executable_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "unsupported_chat",
                "displayName": "Unsupported chat runtime",
                "providerType": "api",
                "baseUrl": "https://unsupported.example.test/v1",
                "enabled": True,
            }
        )
        store.upsert_model(
            {
                "providerId": "unsupported_chat",
                "model": "free-chat",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 32768,
                "inputPricePerMtok": 0.0,
                "outputPricePerMtok": 0.0,
                "enabled": True,
                "source": "test",
            }
        )

        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allowed_provider_ids=["codex_cli"],
                allow_unknown_cost=True,
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "codex_cli"
    rogue = next(item for item in decision["rejected"] if item["providerId"] == "unsupported_chat")
    assert rogue["reason"] == "provider_not_allowed_for_agent"


def test_first_catalogued_runtime_outcome_materializes_observed_performance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_calls = 0

    def executable_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        nonlocal status_calls
        status_calls += 1
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch"],
                "reason": "Controlled executable CLI runtime.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", executable_cli_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        manager = AIResourceManager(connection)
        decision = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )
        manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )
        selected = decision["selected"]

        observed = manager.record_outcome(
            provider_id=selected["providerId"],
            model=selected["model"],
            runtime=selected["runtime"],
            success=True,
            rework=False,
            quality_score=0.9,
            latency_ms=750,
            evidence_ref="evidence-product-owner-first-outcome",
        )
        stored_count = connection.execute("SELECT COUNT(*) FROM ai_model_performance").fetchone()[0]

    assert stored_count == 1
    assert observed["observedSuccessRate"] == 1.0
    assert observed["reworkRate"] == 0.0
    assert observed["qualityScore"] == 0.9
    assert observed["profileSource"] == "model_catalog"
    assert status_calls == 1
    assert {item["kind"] for item in observed["evidence"]} == {
        "model_catalog_baseline",
        "outcome",
    }


def test_first_failed_catalogued_runtime_outcome_preserves_zero_success_rate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch"],
                "reason": "Controlled executable CLI runtime.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", executable_cli_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        manager = AIResourceManager(connection)
        selected = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )["selected"]

        observed = manager.record_outcome(
            provider_id=selected["providerId"],
            model=selected["model"],
            runtime=selected["runtime"],
            success=False,
            rework=True,
            quality_score=0.1,
            latency_ms=900,
            evidence_ref="evidence-product-owner-first-failed-outcome",
        )
        rescored = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )
        failed_candidate = next(
            item
            for item in rescored["candidates"]
            if item["providerId"] == selected["providerId"] and item["model"] == selected["model"]
        )

    assert observed["observedSuccessRate"] == 0.0
    assert observed["reworkRate"] == 1.0
    assert observed["qualityScore"] == 0.1
    assert failed_candidate["performanceStatus"] == "observed"
    assert failed_candidate["scoreBreakdown"]["observedSuccessScore"] == 0.0
    assert failed_candidate["scoreBreakdown"]["reworkRateScore"] == 0.0


def test_low_risk_prefers_cheap_local_without_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertise_executable_runtimes(monkeypatch, "ollama")
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
    assert decision["selected"]["performanceStatus"] == "configured_prior"
    assert decision["selected"]["performanceSampleCount"] == 0
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


def test_score_breakdown_exposes_all_required_routing_factors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertise_executable_runtimes(monkeypatch, "ollama")
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
    monkeypatch: pytest.MonkeyPatch,
    routing_policy: str,
) -> None:
    advertise_executable_runtimes(monkeypatch, "ollama")
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El modo Force local es la salida barata: un modelo local sin costo no exige aprobación."""
    advertise_executable_runtimes(monkeypatch, "ollama_local")
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
    assert decision["decisionReason"] == (
        "No AI resource satisfied policy and capability filters. "
        "1 candidate rejected: 1 runtime_not_executable."
    )
    assert decision["rejected"]
    assert decision["rejected"][0]["providerId"] == "nvidia_nim"
    assert "credential" in decision["rejected"][0]["reason"].lower()


def test_no_selection_reason_aggregates_rejection_categories(tmp_path: Path) -> None:
    """El motivo de bloqueo debe resumir los descartes por categoría, no ocultarlos en rejected."""
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        for provider_id, model in (("remote_a", "remote-model-a"), ("remote_b", "remote-model-b")):
            register_model(
                manager,
                provider_id=provider_id,
                model=model,
                runtime="api",
                locality="remote",
                input_price_per_mtok=0.5,
                output_price_per_mtok=1.5,
                quality_score=0.9,
                success_rate=0.9,
            )
        register_model(
            manager,
            provider_id="ollama_local",
            model="llama-local",
            runtime="ollama",
            locality="local",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.7,
            success_rate=0.8,
            capabilities=["chat"],
            privacy_level="local_private",
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="implementation",
                required_capabilities=["code"],
                privacy_level="local_private",
            ),
            record=False,
        )

    assert decision["selected"] is None
    assert decision["decisionReason"] == (
        "No AI resource satisfied policy and capability filters. "
        "3 candidates rejected: 2 privacy_blocks_remote, 1 missing_capabilities."
    )


@pytest.mark.parametrize(
    ("runtime", "locality"),
    [("cli", "remote"), ("codex_cli", "remote"), ("local", "local")],
)
def test_explicit_profile_must_exist_in_runtime_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime: str,
    locality: str,
) -> None:
    monkeypatch.setattr(
        RuntimeStatusService,
        "list_provider_statuses",
        lambda _service, *, project_id=None: [],
    )

    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="ghost_cli",
            model="ghost-model",
            runtime=runtime,
            locality=locality,
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.99,
            success_rate=0.99,
            capabilities=["chat", "code", "review"],
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="implementation",
                required_capabilities=["code"],
            ),
            record=False,
        )

    assert decision["selected"] is None
    assert decision["rejected"] == [
        {
            "providerId": "ghost_cli",
            "model": "ghost-model",
            "runtime": runtime,
            "profileSource": "explicit",
            "reason": ("runtime_not_executable: Provider ghost_cli is not configured in runtime status."),
        }
    ]


def test_role_execution_policy_blocks_cli_even_when_runtime_is_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertise_executable_runtimes(monkeypatch, "codex_cli", kind="cli")

    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="codex_cli",
            model="gpt-test",
            runtime="cli",
            locality="remote",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.90,
            success_rate=0.90,
            capabilities=["chat", "code"],
        )

        decision = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_cli=False,
            ),
            record=False,
        )

    assert decision["selected"] is None
    assert decision["rejected"][0]["reason"] == "role_blocks_cli"


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


def _register_selectable_remote(
    manager: AIResourceManager,
    connection,
    *,
    provider_id: str,
    model: str,
    quality_score: float,
    success_rate: float,
) -> None:
    """Un candidato remoto ejecutable con precio conocido, listo para pasar todos los gates."""
    register_model(
        manager,
        provider_id=provider_id,
        model=model,
        runtime="api",
        locality="remote",
        input_price_per_mtok=1.0,
        output_price_per_mtok=3.0,
        quality_score=quality_score,
        success_rate=success_rate,
    )
    configure_remote_provider_for_selection(connection, provider_id=provider_id, model=model)


PREFERRED_SELECTION_ORDER = "preferred_resource_rank_asc_score_desc_provider_preference_asc_identity_asc"


def _selection_request(**overrides) -> AIResourceRequest:
    return AIResourceRequest(
        task_type="implementation",
        risk_level="medium",
        context_tokens_estimate=8000,
        required_capabilities=["chat"],
        budget_remaining_usd=1.0,
        **overrides,
    )


def test_preferred_model_entry_outranks_higher_scored_sibling_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión de producción: preferred=[meta/llama-3.1-8b-instruct] elegía 01-ai/yi-large."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_selectable_remote(
            manager,
            connection,
            provider_id="nim_gateway",
            model="01-ai/yi-large",
            quality_score=0.97,
            success_rate=0.98,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="nim_gateway",
            model="meta/llama-3.1-8b-instruct",
            quality_score=0.78,
            success_rate=0.84,
        )

        baseline = manager.select_resource(_selection_request(), record=False)
        preferred = manager.select_resource(
            _selection_request(
                preferred_resources=[{"provider": "nim_gateway", "model": "meta/llama-3.1-8b-instruct"}],
            ),
            record=False,
        )

    assert baseline["selected"]["model"] == "01-ai/yi-large"
    assert preferred["selected"]["model"] == "meta/llama-3.1-8b-instruct"
    assert preferred["policyResult"]["preferredResourceOrder"] == [
        {"provider": "nim_gateway", "model": "meta/llama-3.1-8b-instruct"}
    ]
    assert preferred["policyResult"]["selectionOrder"] == PREFERRED_SELECTION_ORDER
    # El candidato con mejor score sigue auditable como candidato: preferido no significa filtro.
    assert any(item["model"] == "01-ai/yi-large" for item in preferred["candidates"])


@pytest.mark.parametrize("model_wildcard", ["", "auto", "*", None])
def test_preferred_provider_wildcard_outranks_higher_scored_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_wildcard: str | None,
) -> None:
    """Regresión de producción: preferred=[anthropic claude-sonnet] elegía nvidia auto_best_available."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_selectable_remote(
            manager,
            connection,
            provider_id="nim_gateway",
            model="auto_best_available",
            quality_score=0.97,
            success_rate=0.98,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="anthropic_gateway",
            model="claude-sonnet",
            quality_score=0.82,
            success_rate=0.88,
        )

        baseline = manager.select_resource(_selection_request(), record=False)
        preferred = manager.select_resource(
            _selection_request(
                preferred_resources=[{"provider": "anthropic_gateway", "model": model_wildcard}],
            ),
            record=False,
        )

    assert baseline["selected"]["model"] == "auto_best_available"
    assert preferred["selected"]["providerId"] == "anthropic_gateway"
    assert preferred["selected"]["model"] == "claude-sonnet"


def test_preferred_list_order_sets_priority_between_matching_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_selectable_remote(
            manager,
            connection,
            provider_id="provider_alpha",
            model="model-alpha",
            quality_score=0.78,
            success_rate=0.84,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="provider_beta",
            model="model-beta",
            quality_score=0.97,
            success_rate=0.98,
        )

        alpha_first = manager.select_resource(
            _selection_request(
                preferred_resources=[
                    {"provider": "provider_alpha", "model": "model-alpha"},
                    {"provider": "provider_beta", "model": "model-beta"},
                ],
            ),
            record=False,
        )
        beta_first = manager.select_resource(
            _selection_request(
                preferred_resources=[
                    {"provider": "provider_beta", "model": "model-beta"},
                    {"provider": "provider_alpha", "model": "model-alpha"},
                ],
            ),
            record=False,
        )

    assert alpha_first["selected"]["providerId"] == "provider_alpha"
    assert beta_first["selected"]["providerId"] == "provider_beta"


def test_specific_model_preference_outranks_earlier_provider_wildcard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión de producción (votacionenlinea 2026-07-22): el comodín provider-level del
    ``runtime_order`` persistido eclipsaba la preferencia específica del rol solo por ir
    primero en la lista. Un match de modelo específico debe rankear antes que un comodín
    provider-level aunque el comodín esté antes en la lista y su candidato tenga mejor score.
    """
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        # El comodín apunta a este provider y su candidato tiene el score MÁS ALTO: no debe
        # ganar por eso, el match específico manda.
        _register_selectable_remote(
            manager,
            connection,
            provider_id="persisted_gateway",
            model="premium-a",
            quality_score=0.97,
            success_rate=0.98,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="role_gateway",
            model="qwen3:14b",
            quality_score=0.78,
            success_rate=0.84,
        )

        decision = manager.select_resource(
            _selection_request(
                preferred_resources=[
                    # Comodín del runtime_order persistido, PRIMERO en la lista.
                    {"provider": "persisted_gateway", "model": ""},
                    # Preferencia específica del rol, DESPUÉS.
                    {"provider": "role_gateway", "model": "qwen3:14b"},
                ],
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "role_gateway"
    assert decision["selected"]["model"] == "qwen3:14b"
    # Ambos candidatos son ejecutables: el comodín no ganó por rechazo del otro, sino por rank.
    assert {item["providerId"] for item in decision["candidates"]} == {
        "persisted_gateway",
        "role_gateway",
    }


def test_preferred_entry_does_not_resurrect_rejected_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un preferido que no pasa los gates no revive: el resto se ordena por score actual."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        # ghost_gateway queda sin cuenta/instalación: runtime_not_executable en los gates.
        register_model(
            manager,
            provider_id="ghost_gateway",
            model="ghost-model",
            runtime="api",
            locality="remote",
            input_price_per_mtok=1.0,
            output_price_per_mtok=3.0,
            quality_score=0.99,
            success_rate=0.99,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="provider_alpha",
            model="model-alpha",
            quality_score=0.78,
            success_rate=0.84,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="provider_beta",
            model="model-beta",
            quality_score=0.97,
            success_rate=0.98,
        )

        decision = manager.select_resource(
            _selection_request(
                preferred_resources=[{"provider": "ghost_gateway", "model": "ghost-model"}],
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "provider_beta"
    ghost = next(item for item in decision["rejected"] if item["providerId"] == "ghost_gateway")
    assert ghost["reason"].startswith("runtime_not_executable:")


def test_model_router_ai_path_ranks_role_preferred_model_before_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El path AI del router debe llevar preferred_json/fallback_json del rol al selector, en orden."""
    monkeypatch.setenv("AIDO_TEST_API_KEY", "test-key")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_selectable_remote(
            manager,
            connection,
            provider_id="anthropic_gateway",
            model="claude-sonnet",
            quality_score=0.82,
            success_rate=0.88,
        )
        _register_selectable_remote(
            manager,
            connection,
            provider_id="nim_gateway",
            model="auto_best_available",
            quality_score=0.97,
            success_rate=0.98,
        )
        RoutingProfileStore(connection).patch_role_policy(
            "developer",
            {
                "preferred": [{"provider": "anthropic_gateway", "model": "claude-sonnet"}],
                "fallback": [{"provider": "nim_gateway", "model": ""}],
                "escalation": [],
            },
        )

        result = ModelRouter(connection).preview(
            RoutingRequest(
                role="developer",
                taskType="implementation",
                contextTokensEstimate=8000,
            ),
            record=False,
        )

    assert result["policyResult"]["source"] == "ai_resource_manager"
    assert result["selected"]["provider"] == "anthropic_gateway"
    assert result["selected"]["model"] == "claude-sonnet"
    assert result["policyResult"]["preferredResourceOrder"] == [
        {"provider": "anthropic_gateway", "model": "claude-sonnet"},
        {"provider": "nim_gateway", "model": ""},
    ]


def test_model_router_uses_ai_resource_manager_when_profiles_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertise_executable_runtimes(monkeypatch, "ollama")
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


def test_catalog_outcome_does_not_bypass_role_policy_on_the_ai_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable_cli_statuses(
        _service: RuntimeStatusService,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        del project_id
        return [
            {
                "id": "codex_cli",
                "kind": "cli",
                "configured": True,
                "available": True,
                "executable": True,
                "capabilities": ["chat", "code_edit", "issue_to_patch", "review"],
                "reason": "Controlled executable CLI runtime.",
            }
        ]

    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", executable_cli_statuses)

    with open_initialized_connection(tmp_path) as connection:
        enable_catalog_provider(connection, "codex_cli")
        policies = RoutingProfileStore(connection)
        policies.patch_role_policy("product_owner", {"allowCli": False})
        role_policy = policies.get_role_policy("product_owner")
        assert role_policy["allowCli"] is False

        manager = AIResourceManager(connection)
        selected = manager.select_resource(
            AIResourceRequest(
                task_type="product_owner.discovery",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )["selected"]
        manager.record_outcome(
            provider_id=selected["providerId"],
            model=selected["model"],
            runtime=selected["runtime"],
            success=True,
            rework=False,
            quality_score=0.9,
            latency_ms=700,
            evidence_ref="evidence-catalog-outcome-role-policy",
        )

        routed = ModelRouter(connection).preview(
            RoutingRequest(
                role="product_owner",
                taskType="product_owner.discovery",
                contextTokensEstimate=2000,
            ),
            record=False,
        )

    assert routed["selected"] is None
    # El perfil 'model_catalog' del outcome ahora activa la vía AIResourceManager (mismo motor
    # que el product loop); la propiedad protegida se mantiene: la policy del rol sigue
    # rechazando el CLI también por esa vía.
    assert routed["policyResult"].get("source") == "ai_resource_manager"


def test_model_router_ai_path_applies_mutable_role_execution_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertise_executable_runtimes(monkeypatch, "codex_cli", kind="cli")

    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        register_model(
            manager,
            provider_id="codex_cli",
            model="gpt-test",
            runtime="cli",
            locality="remote",
            input_price_per_mtok=0.0,
            output_price_per_mtok=0.0,
            quality_score=0.90,
            success_rate=0.90,
            capabilities=["chat"],
        )
        RoutingProfileStore(connection).patch_role_policy(
            "product_owner",
            {"allowCli": False},
        )

        result = ModelRouter(connection).preview(
            RoutingRequest(
                role="product_owner",
                taskType="product_owner.discovery",
            ),
            record=False,
        )

    assert result["selected"] is None
    assert result["rejected"][0]["reason"] == "role_blocks_cli"
    assert result["policyResult"]["roleExecutionPolicy"]["allowCli"] is False


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


def test_preferred_rank_treats_auto_best_available_as_wildcard() -> None:
    """`auto_best_available` (modelo sembrado por migraciones) rankea tier-1 como los demás comodines."""
    preferred = AIResourceManager._normalized_preferred_resources(
        [{"provider": "openai_gateway", "model": "auto_best_available"}]
    )
    candidate = {"providerId": "openai_gateway", "model": "gpt-real-model"}
    assert AIResourceManager._preferred_resource_rank(candidate, preferred) == (1, 0)


def test_preferred_rank_specific_model_still_beats_wildcard() -> None:
    """Un pin específico (tier 0) sigue ganando al comodín provider-level (tier 1)."""
    preferred = AIResourceManager._normalized_preferred_resources(
        [
            {"provider": "openai_gateway", "model": "auto_best_available"},
            {"provider": "openai_gateway", "model": "gpt-pinned"},
        ]
    )
    pinned = {"providerId": "openai_gateway", "model": "gpt-pinned"}
    other = {"providerId": "openai_gateway", "model": "gpt-other"}
    assert AIResourceManager._preferred_resource_rank(pinned, preferred) == (0, 1)
    assert AIResourceManager._preferred_resource_rank(other, preferred) == (1, 0)
    assert AIResourceManager._preferred_resource_rank(
        pinned, preferred
    ) < AIResourceManager._preferred_resource_rank(other, preferred)


def test_preview_recognizes_model_catalog_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Los perfiles materializados desde el catálogo ('model_catalog') activan la vía AIResourceManager.

    Regresión: el guard exigía profile_source='explicit', que ningún flujo productivo escribe,
    así que el preview de una instalación real siempre usaba el scorer clásico (un motor
    distinto al que ejecuta el product loop).
    """
    advertise_executable_runtimes(monkeypatch, "anthropic_gateway")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_selectable_remote(
            manager,
            connection,
            provider_id="anthropic_gateway",
            model="claude-sonnet",
            quality_score=0.9,
            success_rate=0.95,
        )
        connection.execute("UPDATE ai_model_performance SET profile_source = 'model_catalog'")

        result = ModelRouter(connection).preview(
            RoutingRequest(
                role="developer",
                taskType="implementation",
                contextTokensEstimate=8000,
            ),
            record=False,
        )

    assert result["policyResult"]["source"] == "ai_resource_manager"


def test_preview_falls_back_to_classic_scorer_when_the_manager_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una excepción del manager degrada al scorer clásico en vez de propagar un 500."""
    advertise_executable_runtimes(monkeypatch, "anthropic_gateway")
    with open_initialized_connection(tmp_path) as connection:
        manager = AIResourceManager(connection)
        _register_selectable_remote(
            manager,
            connection,
            provider_id="anthropic_gateway",
            model="claude-sonnet",
            quality_score=0.9,
            success_rate=0.95,
        )
        connection.execute("UPDATE ai_model_performance SET profile_source = 'model_catalog'")

        def _boom(self, request, *, record=True):
            raise RuntimeError("manager contract broke")

        monkeypatch.setattr(AIResourceManager, "select_resource", _boom)
        result = ModelRouter(connection).preview(
            RoutingRequest(
                role="developer",
                taskType="implementation",
                contextTokensEstimate=8000,
            ),
            record=False,
        )

    assert result is not None
    assert (result.get("policyResult") or {}).get("source") != "ai_resource_manager"
