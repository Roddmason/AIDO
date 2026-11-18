"""Costo cero y privacidad local solo para inferencia self-hosted verificada."""

from __future__ import annotations

from contextlib import closing

from local_control_center.agents.model_router import ModelRouter, RoutingRequest
from local_control_center.agents.pricing_catalog import PricingCatalog
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now

MODEL = "gemma-4-26b-a4b"
ACCOUNTS = {
    "llama-loopback": ("http://127.0.0.1:1/v1", {}),
    "llama-lan": ("http://192.168.1.50:8082/v1", {}),
    "llama-public": ("http://llama.example.com/v1", {}),
    "llama-marked-remote": ("http://127.0.0.1:1/v1", {"endpointKind": "remote"}),
}


def _seed(connection, *, free_tier: bool) -> ProviderAccountStore:
    store = ProviderAccountStore(connection)
    for provider, (base_url, metadata) in ACCOUNTS.items():
        store.upsert_provider_account(
            {
                "providerId": provider,
                "providerType": "local",
                "providerFamily": "openai_compatible",
                "apiFormat": "openai_compatible",
                "baseUrl": base_url,
                "metadata": metadata,
                "enabled": True,
                "healthStatus": "healthy",
                "lastHealthCheckAt": utc_now(),
            }
        )
        store.set_provider_catalog_id(provider, "llama_cpp")
        store.upsert_model({"providerId": provider, "model": MODEL, "enabled": True, "freeTier": free_tier})
    return store


def test_pricing_treats_only_self_hosted_local_inference_as_free(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "cost.sqlite")) as connection:
        initialize_platform_schema(connection)
        _seed(connection, free_tier=True)
        pricing = PricingCatalog(connection)
        estimates = {
            provider: pricing.estimate(provider_id=provider, model=MODEL, input_tokens=100, output_tokens=10)[
                "estimatedCostUsd"
            ]
            for provider in ACCOUNTS
        }
    assert estimates == {
        "llama-loopback": 0.0,
        "llama-lan": 0.0,
        "llama-public": None,
        "llama-marked-remote": None,
    }


def test_router_free_tier_and_local_private_follow_the_single_locality_source(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "router.sqlite")) as connection:
        initialize_platform_schema(connection)
        store = _seed(connection, free_tier=False)
        router = ModelRouter(connection)
        free_tier = RoutingRequest(mode="free_tier")
        private = RoutingRequest(privacyLevel="local_private")

        def reason(request, provider):
            return router._hard_reject_reason(
                request, {}, store.get_provider_account(provider), store.get_model(f"{provider}:{MODEL}")
            )

        free_tier_reasons = {provider: reason(free_tier, provider) for provider in ACCOUNTS}
        private_reasons = {provider: reason(private, provider) for provider in ACCOUNTS}
    assert free_tier_reasons == {
        "llama-loopback": None,
        "llama-lan": None,
        "llama-public": "free_tier_model_required",
        "llama-marked-remote": "free_tier_model_required",
    }
    assert private_reasons == {
        "llama-loopback": None,
        "llama-lan": "privacy_blocks_remote",
        "llama-public": "privacy_blocks_remote",
        "llama-marked-remote": "privacy_blocks_remote",
    }
