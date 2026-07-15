from __future__ import annotations

import json
import sqlite3

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.team_bootstrap import bootstrap_base_team_if_needed
from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.pricing_catalog import PricingCatalog
from local_control_center.agents.product_owner_agent_contract import product_owner_agent_readiness
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_catalog import (
    GEMINI_MODEL_MANIFEST,
    enrich_catalog_model,
    provider_catalog_entry,
)
from local_control_center.shared import migrations
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _enable_gemini(connection: sqlite3.Connection, *, pricing_mode: str = "free") -> None:
    connection.execute(
        """
        UPDATE provider_accounts
        SET enabled = 1, pricing_mode = ?, health_status = 'healthy',
            last_health_check_at = '2026-07-14T12:00:00+00:00',
            metadata_json = ?
        WHERE provider_id = 'gemini'
        """,
        (
            pricing_mode,
            json.dumps({"freeTierDeclaredByOperator": pricing_mode == "free"}),
        ),
    )


def test_phase56_seeds_gemini_without_inventing_a_one_million_token_quota(
    tmp_path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        account = connection.execute(
            "SELECT pricing_mode, quota_mode FROM provider_accounts WHERE provider_id = 'gemini'"
        ).fetchone()
        model = connection.execute(
            """
            SELECT context_window, max_output_tokens, free_tier
            FROM model_catalog
            WHERE provider_id = 'gemini' AND model = 'gemini-3.5-flash'
            """
        ).fetchone()
        limit = connection.execute(
            """
            SELECT rpm, tpm, daily_requests, daily_tokens, monthly_tokens, window_timezone
            FROM provider_limits WHERE id = 'gemini:*'
            """
        ).fetchone()
        product_owner = connection.execute(
            """
            SELECT routing_profile_id, max_cost_per_task_usd, allow_unknown_cost
            FROM role_model_policies WHERE id = 'product_owner'
            """
        ).fetchone()
        installation = connection.execute(
            "SELECT kind, enabled FROM runtime_installations WHERE runtime_id = 'gemini'"
        ).fetchone()

    assert dict(account) == {"pricing_mode": "unknown", "quota_mode": "provider_reported"}
    assert dict(model) == {
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "free_tier": 1,
    }
    assert dict(limit) == {
        "rpm": None,
        "tpm": None,
        "daily_requests": None,
        "daily_tokens": None,
        "monthly_tokens": None,
        "window_timezone": "America/Los_Angeles",
    }
    assert dict(product_owner) == {
        "routing_profile_id": "free_tier",
        "max_cost_per_task_usd": 0.0,
        "allow_unknown_cost": 0,
    }
    assert dict(installation) == {"kind": "api", "enabled": 1}


def test_phase56_preserves_custom_product_owner_policy(tmp_path, monkeypatch) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE role_model_policies
            SET routing_profile_id = 'max_performance', preferred_json = '[{"provider":"custom"}]'
            WHERE id = 'product_owner'
            """
        )
        original(connection)
        original(connection)
        policy = connection.execute(
            """
            SELECT routing_profile_id, preferred_json
            FROM role_model_policies WHERE id = 'product_owner'
            """
        ).fetchone()
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 56"
        ).fetchone()[0]

    assert dict(policy) == {
        "routing_profile_id": "max_performance",
        "preferred_json": '[{"provider":"custom"}]',
    }
    assert migration_count == 1


def test_phase56_preserves_partial_product_owner_override(tmp_path, monkeypatch) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            "UPDATE role_model_policies SET max_tokens_per_run = 64000 WHERE id = 'product_owner'"
        )

        original(connection)
        policy = connection.execute(
            """
            SELECT routing_profile_id, max_tokens_per_run
            FROM role_model_policies WHERE id = 'product_owner'
            """
        ).fetchone()

    assert dict(policy) == {
        "routing_profile_id": "balanced_best_value",
        "max_tokens_per_run": 64000,
    }


def test_phase56_upgrades_untouched_persisted_profile_candidates(tmp_path, monkeypatch) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        bootstrap_base_team_if_needed(connection)
        legacy_policy = {
            "providerCandidates": ["nvidia_nim", "ollama", "claude_code_cli"],
            "requiredCapabilities": ["chat"],
            "selection": "first_executable",
            "fallback": "blocked_with_reason",
        }
        connection.execute(
            """
            UPDATE agent_profiles
            SET allowed_providers = '["nvidia_nim"]', allowed_runtimes = '["api"]',
                default_runtime_policy = ?, updated_at = created_at
            WHERE id = 'base-product-owner'
            """,
            (json.dumps(legacy_policy),),
        )

        original(connection)
        profile = connection.execute(
            """
            SELECT allowed_providers, allowed_runtimes, default_runtime_policy
            FROM agent_profiles WHERE id = 'base-product-owner'
            """
        ).fetchone()

    assert json.loads(profile["allowed_providers"]) == ["*"]
    assert json.loads(profile["allowed_runtimes"]) == ["*"]
    assert "gemini" in json.loads(profile["default_runtime_policy"])["providerCandidates"]


def test_phase56_preserves_operator_model_override_and_is_reentrant(tmp_path, monkeypatch) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "gemini",
                "model": "gemini-3.5-flash",
                "contextWindow": 12345,
                "enabled": False,
                "source": "manual_seed",
            }
        )
        connection.execute(
            """
            UPDATE model_catalog
            SET enabled = 0, context_window = 12345, updated_at = '2099-01-01T00:00:00Z'
            WHERE provider_id = 'gemini' AND model = 'gemini-3.5-flash'
            """
        )

        original(connection)
        model = connection.execute(
            """
            SELECT enabled, context_window FROM model_catalog
            WHERE provider_id = 'gemini' AND model = 'gemini-3.5-flash'
            """
        ).fetchone()
        changes_after_first_run = connection.total_changes
        original(connection)
        changes_after_second_run = connection.total_changes

    assert dict(model) == {"enabled": 0, "context_window": 12345}
    assert changes_after_second_run == changes_after_first_run


def test_phase56_enriches_stale_unmodified_provider_sync(tmp_path, monkeypatch) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.upsert_model(
            {
                "providerId": "gemini",
                "model": "gemini-3.5-flash",
                "enabled": True,
                "source": "provider_account_sync:gemini",
            }
        )
        connection.execute(
            """
            UPDATE model_catalog SET updated_at = '2026-07-13T00:00:00Z'
            WHERE provider_id = 'gemini' AND model = 'gemini-3.5-flash'
            """
        )

        original(connection)
        model = connection.execute(
            """
            SELECT context_window, free_tier FROM model_catalog
            WHERE provider_id = 'gemini' AND model = 'gemini-3.5-flash'
            """
        ).fetchone()

    assert dict(model) == {"context_window": 1_048_576, "free_tier": 1}


def test_phase56_demotes_unattested_legacy_free_account_with_invalid_metadata(
    tmp_path, monkeypatch
) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_accounts
            SET pricing_mode = 'free', metadata_json = 'not-json'
            WHERE provider_id = 'gemini'
            """
        )

        original(connection)
        account = connection.execute(
            "SELECT pricing_mode FROM provider_accounts WHERE provider_id = 'gemini'"
        ).fetchone()

    assert account["pricing_mode"] == "unknown"


def test_phase56_does_not_point_product_owner_at_conflicting_routing_profile(
    tmp_path, monkeypatch
) -> None:
    original = migrations.init_phase56_schema
    monkeypatch.setattr(migrations, "init_phase56_schema", lambda _connection: None)
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            INSERT INTO routing_profiles
                (id, name, mode, objective, rules_json, enabled, created_at, updated_at)
            VALUES (
                'custom-free', 'AIDO strict free tier', 'balanced_best_value',
                'operator profile', '{}', 1,
                '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z'
            )
            """
        )

        original(connection)
        policy = connection.execute(
            "SELECT routing_profile_id FROM role_model_policies WHERE id = 'product_owner'"
        ).fetchone()
        strict_profile = connection.execute(
            "SELECT 1 FROM routing_profiles WHERE id = 'free_tier'"
        ).fetchone()

    assert policy["routing_profile_id"] == "balanced_best_value"
    assert strict_profile is None


def test_pricing_requires_an_explicit_free_account(tmp_path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _enable_gemini(connection, pricing_mode="free")
        catalog = PricingCatalog(connection)
        free = catalog.estimate(
            provider_id="gemini",
            model="gemini-3.5-flash",
            input_tokens=1_000,
            output_tokens=500,
        )
        connection.execute(
            "UPDATE provider_accounts SET pricing_mode = 'configured' WHERE provider_id = 'gemini'"
        )
        paid = catalog.estimate(
            provider_id="gemini",
            model="gemini-3.5-flash",
            input_tokens=1_000,
            output_tokens=500,
        )

    assert free["estimatedCostUsd"] == 0.0
    assert free["freeTier"] is True
    assert paid["estimatedCostUsd"] == pytest.approx(0.006)
    assert paid["freeTier"] is False
    assert paid["freeTierEligible"] is True


def test_free_tier_router_selects_confirmed_gemini_and_fails_closed_for_paid_or_sensitive(
    tmp_path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _enable_gemini(connection, pricing_mode="free")
        router = ModelRouter(connection)
        selected = router.preview(
            RoutingRequest(
                role="product_owner",
                taskType="planning",
                mode="free_tier",
                contextTokensEstimate=12_000,
            ),
            record=False,
        )
        sensitive = router.preview(
            RoutingRequest(
                role="product_owner",
                taskType="planning",
                mode="free_tier",
                privacyLevel="confidential",
            ),
            record=False,
        )
        unclassified = router.preview(
            RoutingRequest(
                role="product_owner",
                taskType="planning",
                mode="free_tier",
                privacyLevel="pii",
            ),
            record=False,
        )
        connection.execute(
            "UPDATE provider_accounts SET pricing_mode = 'configured' WHERE provider_id = 'gemini'"
        )
        paid = router.preview(
            RoutingRequest(role="product_owner", taskType="planning", mode="free_tier"),
            record=False,
        )

    assert selected["selected"] == {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "runtime": "api",
        "effort": None,
    }
    assert selected["estimatedCostUsd"] == 0.0
    assert selected["policyResult"]["freeTierOnly"] is True
    assert sensitive["selected"] is None
    assert unclassified["selected"] is None
    assert any(
        item["provider"] == "gemini" and item["reason"] == "free_tier_sensitive_data_blocked"
        for item in sensitive["rejected"]
    )
    assert paid["selected"] is None
    assert any(
        item["provider"] == "gemini" and item["reason"] == "free_tier_account_not_confirmed"
        for item in paid["rejected"]
    )


def test_ai_resource_manager_enforces_operator_declared_free_account(tmp_path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _enable_gemini(connection, pricing_mode="free")
        manager = AIResourceManager(connection)
        request = AIResourceRequest(
            task_type="product_owner.discovery",
            required_capabilities=["chat"],
            free_tier_only=True,
            context_tokens_estimate=2_000,
        )
        model = {
            "providerId": "gemini",
            "model": "gemini-3.5-flash",
            "runtime": "api",
            "locality": "remote",
            "capabilities": ["chat"],
            "contextWindow": 1_048_576,
            "maxOutputTokens": 65_536,
            "freeTier": True,
            "inputPricePerMtok": 1.5,
            "outputPricePerMtok": 9.0,
            "reasoningPricePerMtok": 9.0,
        }

        assert manager._hard_reject_reason(model, request) is None
        assert manager._free_tier_reject_reason(model, request) is None
        assert manager._estimate_cost(model, request)["estimatedCostUsd"] == 0.0
        connection.execute(
            "UPDATE provider_accounts SET pricing_mode = 'configured' WHERE provider_id = 'gemini'"
        )
        rejection = manager._free_tier_reject_reason(model, request)

    assert rejection == "free_tier_account_not_confirmed"


def test_product_owner_readiness_accepts_and_prioritizes_free_gemini() -> None:
    readiness = product_owner_agent_readiness(
        [
            {
                "id": "openai_compatible",
                "providerFamily": "openai_compatible",
                "executable": True,
                "capabilities": ["chat"],
                "pricingMode": "configured",
            },
            {
                "id": "gemini-team",
                "providerFamily": "gemini",
                "executable": True,
                "capabilities": ["chat"],
                "pricingMode": "free",
                "freeTierDeclaredByOperator": True,
            },
        ]
    )

    assert readiness["executable"] is True
    assert readiness["selectedRuntimeId"] == "gemini-team"
    assert readiness["candidateRuntimeIds"][0] == "gemini-team"


def test_gemini_manifest_enriches_stable_models_but_not_unknown_ids() -> None:
    entry = provider_catalog_entry("gemini")
    assert entry is not None
    stable = enrich_catalog_model(entry, {"model": "gemini-3.5-flash"})
    unknown = enrich_catalog_model(entry, {"model": "gemini-preview-unknown"})

    assert stable["contextWindow"] == 1_048_576
    assert stable["freeTier"] is True
    assert stable["inputPricePerMtok"] == GEMINI_MODEL_MANIFEST["gemini-3.5-flash"]["inputPricePerMtok"]
    assert unknown == {"model": "gemini-preview-unknown"}


def test_gemini_free_account_requires_operator_attestation(tmp_path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        account = store.get_provider_account("gemini")

        with pytest.raises(ValueError, match="freeTierDeclaredByOperator"):
            store.upsert_provider_account({**account, "pricingMode": "free", "metadata": {}})

        saved = store.upsert_provider_account(
            {
                **account,
                "pricingMode": "free",
                "metadata": {"freeTierDeclaredByOperator": True},
            }
        )

    assert saved["pricingMode"] == "free"
    assert saved["metadata"]["freeTierDeclaredByOperator"] is True


def test_product_owner_policy_is_free_tier_even_when_request_mode_is_omitted(tmp_path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _enable_gemini(connection, pricing_mode="configured")

        result = ModelRouter(connection).preview(
            RoutingRequest(role="product_owner", taskType="planning"),
            record=False,
        )

    assert result["selected"] is None
    assert result["policyResult"]["freeTierOnly"] is True
    assert any(
        item["provider"] == "gemini" and item["reason"] == "free_tier_account_not_confirmed"
        for item in result["rejected"]
    )
