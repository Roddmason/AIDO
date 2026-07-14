from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.app import create_app
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


def _create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(db_path))
    runtime = ControlPlaneFixture(cwd=tmp_path, db_path=db_path)
    runtime.init()
    return TestClient(create_app(runtime=runtime, static_dir=None))


def _auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_phase52_backfills_legacy_nvidia_account_and_model_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        provider_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(provider_accounts)").fetchall()
        }
        model_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(model_catalog)").fetchall()
        }
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 52"
        ).fetchone()[0]
        legacy_row = connection.execute(
            "SELECT id, provider_id FROM provider_accounts WHERE provider_id = 'nvidia_nim'"
        ).fetchone()
        store = ProviderAccountStore(connection)
        account = store.get_provider_account("nvidia_nim")
        model = store.get_model("nvidia_nim:auto_best_available")

        store.patch_provider_account(
            "nvidia_nim",
            {
                "providerFamily": "nvidia_custom",
                "deploymentMode": "self_hosted_enterprise",
                "apiFamily": "rerank",
                "termsMode": "accepted",
                "pricingMode": "configured",
            },
        )
        initialize_platform_schema(connection)
        migration_count_after_rerun = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 52"
        ).fetchone()[0]
        customized_account = store.get_provider_account("nvidia_nim")

    assert {
        "provider_family",
        "deployment_mode",
        "api_family",
        "terms_mode",
        "pricing_mode",
    } <= provider_columns
    assert {"api_family", "supports_image_generation", "supports_image_editing"} <= model_columns
    assert migration_count == 1
    assert migration_count_after_rerun == 1
    assert legacy_row["id"] == "nvidia_nim"
    assert legacy_row["provider_id"] == "nvidia_nim"
    assert account["providerFamily"] == "nvidia_nim"
    assert account["deploymentMode"] == "hosted_trial"
    assert account["apiFamily"] == "chat_completions"
    assert account["termsMode"] == "evaluation"
    assert account["pricingMode"] == "unknown"
    assert model["apiFamily"] == "chat_completions"
    assert model["supportsImageGeneration"] is False
    assert model["supportsImageEditing"] is False
    assert customized_account["providerFamily"] == "nvidia_custom"
    assert customized_account["deploymentMode"] == "self_hosted_enterprise"
    assert customized_account["apiFamily"] == "rerank"
    assert customized_account["termsMode"] == "accepted"
    assert customized_account["pricingMode"] == "configured"


def test_legacy_provider_upsert_preserves_existing_endpoint_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)

    legacy_nvidia = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={"providerId": "nvidia_nim", "displayName": "NVIDIA legacy payload"},
    )
    assert legacy_nvidia.status_code == 201, legacy_nvidia.text
    account = legacy_nvidia.json()["provider"]
    assert account["providerFamily"] == "nvidia_nim"
    assert account["deploymentMode"] == "hosted_trial"
    assert account["apiFamily"] == "chat_completions"
    assert account["termsMode"] == "evaluation"
    assert account["pricingMode"] == "unknown"

    configured = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-partner-legacy",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "partner_paid",
            "apiFamily": "embeddings",
            "termsMode": "accepted",
            "pricingMode": "configured",
        },
    )
    assert configured.status_code == 201, configured.text

    legacy_partner = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={"providerId": "nvidia-partner-legacy", "displayName": "Partner legacy payload"},
    )
    assert legacy_partner.status_code == 201, legacy_partner.text
    partner_account = legacy_partner.json()["provider"]
    assert partner_account["providerFamily"] == "nvidia_nim"
    assert partner_account["deploymentMode"] == "partner_paid"
    assert partner_account["apiFamily"] == "embeddings"
    assert partner_account["termsMode"] == "accepted"
    assert partner_account["pricingMode"] == "configured"

    explicit_defaults = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-partner-legacy",
            "providerFamily": "nvidia-partner-legacy",
            "deploymentMode": "custom",
            "apiFamily": "chat_completions",
            "termsMode": "unspecified",
            "pricingMode": "unknown",
        },
    )
    assert explicit_defaults.status_code == 201, explicit_defaults.text
    defaulted_account = explicit_defaults.json()["provider"]
    assert defaulted_account["providerFamily"] == "nvidia-partner-legacy"
    assert defaulted_account["deploymentMode"] == "custom"
    assert defaulted_account["apiFamily"] == "chat_completions"
    assert defaulted_account["termsMode"] == "unspecified"
    assert defaulted_account["pricingMode"] == "unknown"


def test_endpoint_accounts_coexist_and_keep_health_server_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    accounts = (
        {
            "providerId": "nvidia-hosted-team-a",
            "displayName": "NVIDIA Hosted Team A",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "apiFamily": "chat_completions",
            "termsMode": "evaluation",
            "pricingMode": "unknown",
            "baseUrl": "https://team-a.example.invalid/v1",
            "credentialRef": "env:NVIDIA_HOSTED_TEAM_A_API_KEY",
            "metadata": {"tenant": "team-a"},
            "healthStatus": "healthy",
            "lastError": "client health must be ignored",
        },
        {
            "providerId": "nvidia-partner-prod",
            "displayName": "NVIDIA Partner Production",
            "providerType": "api",
            "apiFormat": "openai_compatible",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "partner_paid",
            "apiFamily": "chat_completions",
            "termsMode": "accepted",
            "pricingMode": "configured",
            "baseUrl": "https://partner.example.invalid/v1",
            "credentialRef": "env:NVIDIA_PARTNER_PROD_API_KEY",
            "metadata": {"tenant": "partner-prod"},
        },
    )

    for expected in accounts:
        response = client.post("/api/v1/model-gateway/providers", headers=headers, json=expected)
        assert response.status_code == 201, response.text
        account = response.json()["provider"]
        for field in (
            "providerFamily",
            "deploymentMode",
            "apiFamily",
            "termsMode",
            "pricingMode",
            "baseUrl",
            "credentialRef",
            "metadata",
        ):
            assert account[field] == expected[field]
        assert account["healthStatus"] == "unknown"
        assert account["lastHealthCheckAt"] is None
        assert account["lastError"] == ""

    rejected = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={"providerId": "NVIDIA team a", "providerFamily": "nvidia_nim"},
    )
    assert rejected.status_code == 422

    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        store = ProviderAccountStore(connection)
        store.record_health_check(
            provider_id="nvidia-hosted-team-a",
            status="available",
            payload={"healthStatus": "healthy"},
        )
        store.record_health_check(
            provider_id="nvidia-partner-prod",
            status="degraded",
            payload={"healthStatus": "degraded", "message": "partner endpoint unavailable"},
        )

    hosted = client.get("/api/v1/model-gateway/providers/nvidia-hosted-team-a")
    partner = client.get("/api/v1/model-gateway/providers/nvidia-partner-prod")
    listed = client.get("/api/v1/model-gateway/providers")
    assert hosted.status_code == partner.status_code == listed.status_code == 200
    assert hosted.json()["provider"]["healthStatus"] == "healthy"
    assert partner.json()["provider"]["healthStatus"] == "degraded"
    listed_by_id = {item["providerId"]: item for item in listed.json()["providers"]}
    assert listed_by_id["nvidia-hosted-team-a"]["baseUrl"] == accounts[0]["baseUrl"]
    assert listed_by_id["nvidia-hosted-team-a"]["credentialRef"] == accounts[0]["credentialRef"]
    assert listed_by_id["nvidia-hosted-team-a"]["metadata"] == accounts[0]["metadata"]
    assert listed_by_id["nvidia-partner-prod"]["baseUrl"] == accounts[1]["baseUrl"]
    assert listed_by_id["nvidia-partner-prod"]["credentialRef"] == accounts[1]["credentialRef"]
    assert listed_by_id["nvidia-partner-prod"]["metadata"] == accounts[1]["metadata"]

    patched = client.patch(
        "/api/v1/model-gateway/providers/nvidia-hosted-team-a",
        headers=headers,
        json={"apiFamily": "embeddings", "healthStatus": "healthy"},
    )
    assert patched.status_code == 200, patched.text
    patched_account = patched.json()["provider"]
    assert patched_account["apiFamily"] == "embeddings"
    assert patched_account["healthStatus"] == "unknown"
    assert patched_account["lastHealthCheckAt"] is None


def test_model_catalog_capabilities_remain_endpoint_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    shared_model = "nvidia/qwen-image"

    invalid_provider = client.post(
        "/api/v1/model-gateway/models",
        headers=headers,
        json={"providerId": "NVIDIA team a", "model": shared_model},
    )
    assert invalid_provider.status_code == 422

    hosted = client.post(
        "/api/v1/model-gateway/models",
        headers=headers,
        json={
            "providerId": "nvidia-hosted-team-a",
            "model": shared_model,
            "apiFamily": "image_generation",
            "supportsImageGeneration": True,
            "supportsImageEditing": False,
        },
    )
    assert hosted.status_code == 201, hosted.text
    hosted_model = hosted.json()["model"]
    assert hosted_model["apiFamily"] == "image_generation"
    assert hosted_model["supportsImageGeneration"] is True
    assert hosted_model["supportsImageEditing"] is False

    patched = client.patch(
        f"/api/v1/model-gateway/models/{hosted_model['id']}",
        headers=headers,
        json={
            "apiFamily": "image_editing",
            "supportsImageGeneration": False,
            "supportsImageEditing": True,
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["model"]["id"] == hosted_model["id"]
    assert patched.json()["model"]["apiFamily"] == "image_editing"
    assert patched.json()["model"]["supportsImageGeneration"] is False
    assert patched.json()["model"]["supportsImageEditing"] is True

    partner = client.post(
        "/api/v1/model-gateway/models",
        headers=headers,
        json={
            "providerId": "nvidia-partner-prod",
            "model": shared_model,
            "apiFamily": "image_generation",
            "supportsImageGeneration": True,
            "supportsImageEditing": False,
        },
    )
    assert partner.status_code == 201, partner.text
    assert partner.json()["model"]["id"] != hosted_model["id"]

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        rows = connection.execute(
            "SELECT provider_id, model FROM model_catalog WHERE model = ? ORDER BY provider_id",
            (shared_model,),
        ).fetchall()

    assert [(row["provider_id"], row["model"]) for row in rows] == [
        ("nvidia-hosted-team-a", shared_model),
        ("nvidia-partner-prod", shared_model),
    ]


def test_catalog_preset_creates_distinct_nvidia_instances_with_explicit_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_HOSTED_TEAM_A_API_KEY", "hosted-team-a-test-key")
    monkeypatch.setenv("NVIDIA_PARTNER_PROD_API_KEY", "partner-prod-test-key")
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)

    catalog = client.get("/api/v1/providers/catalog")
    assert catalog.status_code == 200
    nvidia_preset = next(
        item for item in catalog.json()["providers"] if item["id"] == "nvidia_nim"
    )
    assert nvidia_preset["providerFamily"] == "nvidia_nim"
    assert nvidia_preset["deploymentMode"] == "hosted_trial"
    assert nvidia_preset["apiFamily"] == "chat_completions"
    assert nvidia_preset["termsMode"] == "evaluation"
    assert nvidia_preset["pricingMode"] == "unknown"

    hosted = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": "nvidia-hosted-team-a",
            "displayName": "NVIDIA Hosted Team A",
            "credentialRef": "env:NVIDIA_HOSTED_TEAM_A_API_KEY",
            "enabled": True,
        },
    )
    partner = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": "nvidia-partner-prod",
            "displayName": "NVIDIA Partner Production",
            "deploymentMode": "partner_paid",
            "apiFamily": "embeddings",
            "termsMode": "accepted",
            "pricingMode": "configured",
            "baseUrl": "https://partner.example.invalid/v1",
            "credentialRef": "env:NVIDIA_PARTNER_PROD_API_KEY",
            "enabled": True,
        },
    )

    assert hosted.status_code == 201, hosted.text
    assert partner.status_code == 201, partner.text
    hosted_account = hosted.json()["provider"]
    partner_account = partner.json()["provider"]
    assert hosted_account["providerId"] == "nvidia-hosted-team-a"
    assert hosted_account["providerFamily"] == "nvidia_nim"
    assert hosted_account["deploymentMode"] == "hosted_trial"
    assert hosted_account["termsMode"] == "evaluation"
    assert hosted_account["baseUrl"] == "https://integrate.api.nvidia.com/v1"
    assert hosted_account["metadata"]["providerCatalogId"] == "nvidia_nim"
    assert partner_account["providerId"] == "nvidia-partner-prod"
    assert partner_account["providerFamily"] == "nvidia_nim"
    assert partner_account["deploymentMode"] == "partner_paid"
    assert partner_account["apiFamily"] == "embeddings"
    assert partner_account["termsMode"] == "accepted"
    assert partner_account["pricingMode"] == "configured"
    assert partner_account["metadata"]["providerCatalogId"] == "nvidia_nim"


@pytest.mark.parametrize(
    "deployment_mode",
    ["self_hosted_development", "self_hosted_enterprise", "partner_paid", "custom"],
)
def test_non_hosted_catalog_deployments_require_an_explicit_base_url(
    deployment_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_NON_HOSTED_API_KEY", "non-hosted-test-key")
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    instance_id = f"nvidia-{deployment_mode.replace('_', '-')}"

    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": instance_id,
            "deploymentMode": deployment_mode,
            "credentialRef": "env:NVIDIA_NON_HOSTED_API_KEY",
        },
    )

    assert response.status_code == 422, response.text
    assert "baseUrl" in response.json()["detail"]
    assert client.get(f"/api/v1/model-gateway/providers/{instance_id}").status_code == 404


@pytest.mark.parametrize(
    ("override", "instance_id"),
    [
        ({"termsMode": "accepted"}, "nvidia-hosted-invalid-terms"),
        ({"pricingMode": "configured"}, "nvidia-hosted-invalid-pricing"),
    ],
)
def test_hosted_trial_rejects_non_evaluation_commercial_semantics(
    override: dict[str, str],
    instance_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)

    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": instance_id,
            "credentialRef": "env:NVIDIA_HOSTED_INVARIANT_API_KEY",
            **override,
        },
    )

    assert response.status_code == 422, response.text
    assert client.get(f"/api/v1/model-gateway/providers/{instance_id}").status_code == 404


def test_generic_provider_api_cannot_bypass_hosted_trial_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)

    created = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "nvidia-hosted-generic-invalid",
            "providerFamily": "nvidia_nim",
            "deploymentMode": "hosted_trial",
            "termsMode": "accepted",
            "pricingMode": "unknown",
        },
    )
    patched = client.patch(
        "/api/v1/model-gateway/providers/nvidia_nim",
        headers=headers,
        json={"pricingMode": "configured"},
    )

    assert created.status_code == 400, created.text
    assert patched.status_code == 400, patched.text
    assert client.get(
        "/api/v1/model-gateway/providers/nvidia-hosted-generic-invalid"
    ).status_code == 404
    canonical = client.get("/api/v1/model-gateway/providers/nvidia_nim").json()["provider"]
    assert canonical["termsMode"] == "evaluation"
    assert canonical["pricingMode"] == "unknown"


def test_provider_account_apis_reject_url_userinfo_without_persisting_or_echoing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    secret = "userinfo-secret-should-never-persist"
    unsafe_url = f"https://operator:{secret}@provider.example.invalid/v1"

    generic_post = client.post(
        "/api/v1/model-gateway/providers",
        headers=headers,
        json={
            "providerId": "userinfo-generic-post",
            "providerFamily": "openai_compatible",
            "baseUrl": unsafe_url,
        },
    )
    generic_patch = client.patch(
        "/api/v1/model-gateway/providers/openai_compatible",
        headers=headers,
        json={"baseUrl": unsafe_url},
    )
    catalog_post = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": "userinfo-catalog-post",
            "baseUrl": unsafe_url,
            "credentialRef": "env:NVIDIA_USERINFO_API_KEY",
        },
    )

    assert generic_post.status_code == 400, generic_post.text
    assert generic_patch.status_code == 400, generic_patch.text
    assert catalog_post.status_code == 422, catalog_post.text
    for response in (generic_post, generic_patch, catalog_post):
        assert secret not in response.text

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        persisted = connection.execute(
            "SELECT COUNT(*) FROM provider_accounts WHERE instr(base_url, ?) > 0",
            (secret,),
        ).fetchone()[0]
        created_ids = connection.execute(
            """
            SELECT provider_id
            FROM provider_accounts
            WHERE provider_id IN ('userinfo-generic-post', 'userinfo-catalog-post')
            """
        ).fetchall()

    assert persisted == 0
    assert created_ids == []


def test_explicit_catalog_instance_collision_returns_409_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_COLLISION_API_KEY", "collision-test-key")
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    original_payload = {
        "providerId": "nvidia_nim",
        "instanceId": "nvidia-collision-test",
        "displayName": "Original NVIDIA account",
        "baseUrl": "https://original.example.invalid/v1",
        "credentialRef": "env:NVIDIA_COLLISION_API_KEY",
    }

    created = client.post(
        "/api/v1/provider-accounts/from-catalog", headers=headers, json=original_payload
    )
    collided = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            **original_payload,
            "displayName": "Overwritten NVIDIA account",
            "baseUrl": "https://overwritten.example.invalid/v1",
        },
    )
    persisted = client.get("/api/v1/model-gateway/providers/nvidia-collision-test")

    assert created.status_code == 201, created.text
    assert collided.status_code == 409, collided.text
    assert "PATCH" in collided.json()["detail"]
    assert persisted.status_code == 200
    assert persisted.json()["provider"]["displayName"] == "Original NVIDIA account"
    assert persisted.json()["provider"]["baseUrl"] == "https://original.example.invalid/v1"


def test_catalog_request_without_instance_id_preserves_legacy_canonical_upsert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_LEGACY_API_KEY", "legacy-test-key")
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    payload = {
        "providerId": "nvidia_nim",
        "displayName": "First NVIDIA canonical name",
        "credentialRef": "env:NVIDIA_LEGACY_API_KEY",
    }

    first = client.post("/api/v1/provider-accounts/from-catalog", headers=headers, json=payload)
    updated = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={**payload, "displayName": "Updated NVIDIA canonical name"},
    )

    assert first.status_code == 201, first.text
    assert updated.status_code == 201, updated.text
    assert first.json()["provider"]["providerId"] == "nvidia_nim"
    assert updated.json()["provider"]["providerId"] == "nvidia_nim"
    assert updated.json()["provider"]["displayName"] == "Updated NVIDIA canonical name"
    assert updated.json()["provider"]["metadata"]["providerCatalogId"] == "nvidia_nim"


def test_noncanonical_account_sync_resolves_nvidia_preset_and_stays_endpoint_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_SYNC_API_KEY", "sync-test-key")
    client = _create_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    instance_id = "nvidia-sync-team-a"
    created = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "nvidia_nim",
            "instanceId": instance_id,
            "baseUrl": "https://sync.example.invalid/v1",
            "credentialRef": "env:NVIDIA_SYNC_API_KEY",
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["provider"]["metadata"]["providerCatalogId"] == "nvidia_nim"

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        runtime_repo = RuntimeConfigRepository(connection)
        runtime_repo.set_runtime_setting("runtime.remote.enabled", True)
        runtime_repo.set_runtime_setting("runtime.nvidia.enabled", True)

    resolved_provider_ids: list[str] = []

    class RecordingProvider:
        def list_models(self) -> list[object]:
            class DiscoveredModel:
                def model_dump(self, *, by_alias: bool) -> dict[str, object]:
                    assert by_alias is True
                    return {"model": "nvidia/test-model", "displayName": "NVIDIA test model"}

            return [DiscoveredModel()]

    def recording_provider_instance(provider_id: str, *, connection: object) -> RecordingProvider:
        assert connection is not None
        resolved_provider_ids.append(provider_id)
        return RecordingProvider()

    monkeypatch.setattr(
        "local_control_center.agents.provider_catalog_api.provider_instance",
        recording_provider_instance,
    )

    synced = client.post(
        f"/api/v1/provider-accounts/{instance_id}/sync-models",
        headers=headers,
    )

    assert synced.status_code == 200, synced.text
    assert resolved_provider_ids == [instance_id]
    assert [model["providerId"] for model in synced.json()["models"]] == [instance_id]
    assert [model["model"] for model in synced.json()["models"]] == ["nvidia/test-model"]
