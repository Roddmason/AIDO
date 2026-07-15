"""Persists provider accounts, the model catalog, pricing snapshots, and health checks.

Backs the Model Gateway's provider configuration: who the providers are, which models
they expose (capabilities and prices), point-in-time pricing snapshots, and the latest
health-check result. Credential references are validated/normalized via the resolver and
never stored raw; error text and metadata are redacted before persistence.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any
from urllib.parse import urlparse

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def validate_provider_base_url(
    value: str | None,
    *,
    provider_family: str = "",
    deployment_mode: str = "custom",
    api_family: str = "chat_completions",
    adapter_profile: str = "auto",
) -> str:
    """Validate a safe absolute endpoint root before it reaches SQLite."""
    candidate = str(value or "").strip()
    if not candidate:
        return candidate
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("baseUrl must be an absolute http(s) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("baseUrl must not contain URL userinfo.")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("baseUrl must not include params, query or fragment.")
    if (
        provider_family == "nvidia_nim"
        and not deployment_mode.startswith("self_hosted")
        and parsed.scheme != "https"
    ):
        raise ValueError("baseUrl must use https for hosted or partner NVIDIA NIM endpoints.")
    if provider_family == "nvidia_nim":
        path = parsed.path.rstrip("/")
        if api_family in {"chat_completions", "embeddings"} and not path.endswith("/v1"):
            raise ValueError("baseUrl must end in /v1 for NVIDIA chat or embeddings.")
        if api_family == "rerank":
            if deployment_mode.startswith("self_hosted"):
                if not path.endswith("/v1"):
                    raise ValueError("baseUrl must end in /v1 for self-hosted NVIDIA rerank.")
            else:
                marker = "/v1/retrieval/"
                route = path.split(marker, 1)[1] if marker in path else ""
                if len([segment for segment in route.split("/") if segment]) != 2:
                    raise ValueError(
                        "baseUrl must be a model-specific /v1/retrieval/{publisher}/{model} root "
                        "for hosted NVIDIA rerank."
                    )
        if api_family in {"image_generation", "image_editing"}:
            if deployment_mode.startswith("self_hosted"):
                if not path.endswith("/v1"):
                    raise ValueError("baseUrl must end in /v1 for self-hosted NVIDIA visual NIMs.")
            elif adapter_profile == "nvidia_hosted_prompt_image_generation":
                marker = "/v1/genai/"
                route = path.removeprefix(marker) if path.startswith(marker) else ""
                if len([segment for segment in route.split("/") if segment]) != 2:
                    raise ValueError(
                        "baseUrl must be a model-specific /v1/genai/{publisher}/{model} root "
                        "for hosted NVIDIA image generation."
                    )
    return candidate.rstrip("/")

PROVIDER_HEALTH_RESET_FIELDS = {
    "providerType",
    "provider_type",
    "apiFormat",
    "api_format",
    "providerFamily",
    "provider_family",
    "deploymentMode",
    "deployment_mode",
    "apiFamily",
    "api_family",
    "adapterProfile",
    "adapter_profile",
    "baseUrl",
    "base_url",
    "credentialRef",
    "credential_ref",
}

PROVIDER_ACCOUNT_COLUMNS = """
    id, provider_id, display_name, provider_type, api_format, provider_family,
    deployment_mode, api_family, adapter_profile, terms_mode, pricing_mode, base_url, credential_ref,
    enabled, quota_mode, health_status, last_health_check_at, last_error, metadata_json,
    created_at, updated_at
"""
MODEL_CATALOG_COLUMNS = """
    id, provider_id, model, display_name, model_family, api_family, context_window,
    max_output_tokens, supports_tools, supports_json, supports_streaming, supports_vision,
    supports_embeddings, supports_rerank, supports_reasoning, supports_thinking,
    supports_image_generation, supports_image_editing, effort_levels_json,
    input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
    reasoning_price_per_mtok, free_tier, free_tier_notes, enabled, source, created_at, updated_at
"""
PRICING_SNAPSHOT_COLUMNS = """
    id, provider_id, model, input_price_per_mtok, cached_input_price_per_mtok,
    output_price_per_mtok, reasoning_price_per_mtok, free_tier, source_ref, effective_at,
    metadata_json, apply_to_catalog, created_at
"""


def _bool(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, int) else bool(value)


def _value_or_existing(
    body: dict[str, Any], existing: dict[str, Any] | None, field: str, default: Any
) -> Any:
    """Use an explicitly supplied value, otherwise preserve the stored value or create default."""
    if field in body and body[field] is not None:
        return body[field]
    if existing is not None and field in existing:
        return existing[field]
    return default


def credential_status(row: sqlite3.Row) -> str:
    """Resolve the configured/missing status of a provider row's credential reference."""
    ref = str(row["credential_ref"] or "")
    return CredentialResolver().status(ref)


def row_to_provider_account(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `provider_accounts` row to its camelCase dict, redacting error/metadata fields."""
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "displayName": row["display_name"],
        "providerType": row["provider_type"],
        "apiFormat": row["api_format"],
        "providerFamily": row["provider_family"],
        "deploymentMode": row["deployment_mode"],
        "apiFamily": row["api_family"],
        "adapterProfile": row["adapter_profile"],
        "termsMode": row["terms_mode"],
        "pricingMode": row["pricing_mode"],
        "baseUrl": row["base_url"],
        "credentialRef": row["credential_ref"],
        "credentialStatus": credential_status(row),
        "enabled": _bool(row["enabled"]),
        "quotaMode": row["quota_mode"],
        "healthStatus": row["health_status"],
        "lastHealthCheckAt": row["last_health_check_at"],
        "lastError": redact_secrets(row["last_error"] or ""),
        "metadata": redact_secrets(json_loads(row["metadata_json"], {}))
        if "metadata_json" in row.keys()
        else {},
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def provider_account_is_declared_free(account: dict[str, Any]) -> bool:
    """Return whether zero-cost billing was explicitly and safely declared for an account."""
    if str(account.get("pricingMode") or "unknown") != "free":
        return False
    if str(account.get("providerFamily") or account.get("providerId") or "") != "gemini":
        return True
    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    return metadata.get("freeTierDeclaredByOperator") is True


def row_to_model_catalog(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `model_catalog` row to its camelCase dict (capability flags and prices decoded)."""
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "displayName": row["display_name"],
        "modelFamily": row["model_family"],
        "apiFamily": row["api_family"],
        "contextWindow": row["context_window"],
        "maxOutputTokens": row["max_output_tokens"],
        "supportsTools": _bool(row["supports_tools"]),
        "supportsJson": _bool(row["supports_json"]),
        "supportsStreaming": _bool(row["supports_streaming"]),
        "supportsVision": _bool(row["supports_vision"]),
        "supportsEmbeddings": _bool(row["supports_embeddings"]),
        "supportsRerank": _bool(row["supports_rerank"]),
        "supportsReasoning": _bool(row["supports_reasoning"]),
        "supportsThinking": _bool(row["supports_thinking"]),
        "supportsImageGeneration": _bool(row["supports_image_generation"]),
        "supportsImageEditing": _bool(row["supports_image_editing"]),
        "effortLevels": json_loads(row["effort_levels_json"], []),
        "inputPricePerMtok": row["input_price_per_mtok"],
        "cachedInputPricePerMtok": row["cached_input_price_per_mtok"],
        "outputPricePerMtok": row["output_price_per_mtok"],
        "reasoningPricePerMtok": row["reasoning_price_per_mtok"],
        "freeTier": _bool(row["free_tier"]),
        "freeTierNotes": row["free_tier_notes"],
        "enabled": _bool(row["enabled"]),
        "source": row["source"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_pricing_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `pricing_snapshots` row to its camelCase dict, redacting source ref and metadata."""
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "inputPricePerMtok": row["input_price_per_mtok"],
        "cachedInputPricePerMtok": row["cached_input_price_per_mtok"],
        "outputPricePerMtok": row["output_price_per_mtok"],
        "reasoningPricePerMtok": row["reasoning_price_per_mtok"],
        "freeTier": _bool(row["free_tier"]),
        "sourceRef": redact_secrets(row["source_ref"]),
        "effectiveAt": row["effective_at"],
        "metadata": redact_secrets(json_loads(row["metadata_json"], {})),
        "applyToCatalog": _bool(row["apply_to_catalog"]),
        "createdAt": row["created_at"],
    }


class ProviderAccountStore:
    """SQLite store for provider accounts, model catalog, pricing snapshots, and health checks."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_provider_accounts(self) -> list[dict[str, Any]]:
        """List all provider accounts ordered by provider id."""
        rows = self.connection.execute(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts ORDER BY provider_id ASC"
        ).fetchall()
        return [row_to_provider_account(row) for row in rows]

    def get_provider_account(self, provider_id: str) -> dict[str, Any]:
        """Fetch a provider account by provider id or row id.

        Raises:
            KeyError: if no matching account exists.
        """
        row = self.connection.execute(
            f"""
            SELECT {PROVIDER_ACCOUNT_COLUMNS}
            FROM provider_accounts
            WHERE provider_id = ? OR id = ?
            """,
            (provider_id, provider_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Provider account not found: {provider_id}")
        return row_to_provider_account(row)

    def upsert_provider_account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a provider account, validating/normalizing its credential reference.

        Metadata and error text are redacted before storage.
        """
        provider_id = str(body["providerId"])
        try:
            existing = self.get_provider_account(provider_id)
        except KeyError:
            existing = None
        resolver = CredentialResolver()
        credential_ref = resolver.normalize_ref_for_storage(str(body.get("credentialRef", "") or ""))
        resolver.validate_ref(credential_ref)
        provider_family = _value_or_existing(body, existing, "providerFamily", provider_id)
        deployment_mode = _value_or_existing(body, existing, "deploymentMode", "custom")
        api_family = _value_or_existing(body, existing, "apiFamily", "chat_completions")
        adapter_profile = _value_or_existing(body, existing, "adapterProfile", "auto")
        terms_mode = _value_or_existing(body, existing, "termsMode", "unspecified")
        pricing_mode = _value_or_existing(body, existing, "pricingMode", "unknown")
        provider_type = _value_or_existing(body, existing, "providerType", "api")
        metadata = redact_secrets(body.get("metadata") or {})
        base_url = validate_provider_base_url(
            body.get("baseUrl"),
            provider_family=provider_family,
            deployment_mode=deployment_mode,
            api_family=api_family,
            adapter_profile=adapter_profile,
        )
        if provider_family == "nvidia_nim" and provider_type not in {"api", "gateway"}:
            raise ValueError("NVIDIA NIM providerType must be api or gateway.")
        if (
            provider_family == "nvidia_nim"
            and deployment_mode == "hosted_trial"
            and (terms_mode != "evaluation" or pricing_mode != "unknown")
        ):
            raise ValueError(
                "NVIDIA NIM hosted_trial requires termsMode evaluation and pricingMode unknown."
            )
        if (
            provider_family == "gemini"
            and pricing_mode == "free"
            and metadata.get("freeTierDeclaredByOperator") is not True
        ):
            raise ValueError(
                "Gemini pricingMode free requires metadata.freeTierDeclaredByOperator=true."
            )
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO provider_accounts
                (id, provider_id, display_name, provider_type, api_format, provider_family,
                 deployment_mode, api_family, adapter_profile, terms_mode, pricing_mode, base_url, credential_ref,
                 enabled, quota_mode, health_status, last_health_check_at, last_error, metadata_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                display_name = excluded.display_name,
                provider_type = excluded.provider_type,
                api_format = excluded.api_format,
                provider_family = excluded.provider_family,
                deployment_mode = excluded.deployment_mode,
                api_family = excluded.api_family,
                adapter_profile = excluded.adapter_profile,
                terms_mode = excluded.terms_mode,
                pricing_mode = excluded.pricing_mode,
                base_url = excluded.base_url,
                credential_ref = excluded.credential_ref,
                enabled = excluded.enabled,
                quota_mode = excluded.quota_mode,
                health_status = excluded.health_status,
                last_health_check_at = excluded.last_health_check_at,
                last_error = excluded.last_error,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                body.get("id") or provider_id,
                provider_id,
                body.get("displayName") or provider_id,
                provider_type,
                body.get("apiFormat", "openai_compatible"),
                provider_family,
                deployment_mode,
                api_family,
                adapter_profile,
                terms_mode,
                pricing_mode,
                base_url,
                credential_ref,
                1 if body.get("enabled", False) else 0,
                body.get("quotaMode", "none"),
                body.get("healthStatus", "unknown"),
                body.get("lastHealthCheckAt"),
                redact_secrets(body.get("lastError", "")),
                json_dumps(metadata),
                now,
                now,
            ),
        )
        return self.get_provider_account(provider_id)

    def patch_provider_account(self, provider_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Apply a partial update to a provider account, resetting health when connectivity changes."""
        existing = self.get_provider_account(provider_id)
        merged = {**existing, **body, "providerId": existing["providerId"]}
        if any(
            field in body and body[field] != existing.get(field) for field in PROVIDER_HEALTH_RESET_FIELDS
        ):
            merged["healthStatus"] = "unknown"
            merged["lastHealthCheckAt"] = None
            merged["lastError"] = ""
        return self.upsert_provider_account(merged)

    def list_models(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        """List catalog models, optionally filtered to one provider, ordered by provider then model."""
        if provider_id:
            rows = self.connection.execute(
                f"""
                SELECT {MODEL_CATALOG_COLUMNS}
                FROM model_catalog
                WHERE provider_id = ?
                ORDER BY provider_id ASC, model ASC
                """,
                (provider_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                f"SELECT {MODEL_CATALOG_COLUMNS} FROM model_catalog ORDER BY provider_id ASC, model ASC"
            ).fetchall()
        return [row_to_model_catalog(row) for row in rows]

    def get_model(self, model_id: str) -> dict[str, Any]:
        """Fetch one catalog model by id.

        Raises:
            KeyError: if no model has the given id.
        """
        row = self.connection.execute(
            f"SELECT {MODEL_CATALOG_COLUMNS} FROM model_catalog WHERE id = ?", (model_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Model not found: {model_id}")
        return row_to_model_catalog(row)

    def upsert_model(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a catalog model keyed by (providerId, model) and return the stored row."""
        model_id = str(body.get("id") or f"{body['providerId']}:{body['model']}")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO model_catalog
                (id, provider_id, model, display_name, model_family, api_family, context_window,
                 max_output_tokens, supports_tools, supports_json, supports_streaming, supports_vision,
                 supports_embeddings, supports_rerank, supports_reasoning, supports_thinking,
                 supports_image_generation, supports_image_editing, effort_levels_json,
                 input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
                 reasoning_price_per_mtok, free_tier, free_tier_notes, enabled, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, model) DO UPDATE SET
                display_name = excluded.display_name,
                model_family = excluded.model_family,
                api_family = excluded.api_family,
                context_window = excluded.context_window,
                max_output_tokens = excluded.max_output_tokens,
                supports_tools = excluded.supports_tools,
                supports_json = excluded.supports_json,
                supports_streaming = excluded.supports_streaming,
                supports_vision = excluded.supports_vision,
                supports_embeddings = excluded.supports_embeddings,
                supports_rerank = excluded.supports_rerank,
                supports_reasoning = excluded.supports_reasoning,
                supports_thinking = excluded.supports_thinking,
                supports_image_generation = excluded.supports_image_generation,
                supports_image_editing = excluded.supports_image_editing,
                effort_levels_json = excluded.effort_levels_json,
                input_price_per_mtok = excluded.input_price_per_mtok,
                cached_input_price_per_mtok = excluded.cached_input_price_per_mtok,
                output_price_per_mtok = excluded.output_price_per_mtok,
                reasoning_price_per_mtok = excluded.reasoning_price_per_mtok,
                free_tier = excluded.free_tier,
                free_tier_notes = excluded.free_tier_notes,
                enabled = excluded.enabled,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                model_id,
                body["providerId"],
                body["model"],
                body.get("displayName") or body["model"],
                body.get("modelFamily", ""),
                body.get("apiFamily", "chat_completions"),
                int(body.get("contextWindow") or 0),
                int(body.get("maxOutputTokens") or 0),
                1 if body.get("supportsTools", False) else 0,
                1 if body.get("supportsJson", False) else 0,
                1 if body.get("supportsStreaming", False) else 0,
                1 if body.get("supportsVision", False) else 0,
                1 if body.get("supportsEmbeddings", False) else 0,
                1 if body.get("supportsRerank", False) else 0,
                1 if body.get("supportsReasoning", False) else 0,
                1 if body.get("supportsThinking", False) else 0,
                1 if body.get("supportsImageGeneration", False) else 0,
                1 if body.get("supportsImageEditing", False) else 0,
                json_dumps(body.get("effortLevels") or []),
                body.get("inputPricePerMtok"),
                body.get("cachedInputPricePerMtok"),
                body.get("outputPricePerMtok"),
                body.get("reasoningPricePerMtok"),
                1 if body.get("freeTier", False) else 0,
                body.get("freeTierNotes", ""),
                1 if body.get("enabled", True) else 0,
                body.get("source", "manual"),
                now,
                now,
            ),
        )
        row = self.connection.execute(
            f"SELECT {MODEL_CATALOG_COLUMNS} FROM model_catalog WHERE provider_id = ? AND model = ?",
            (body["providerId"], body["model"]),
        ).fetchone()
        return row_to_model_catalog(row)

    def patch_model(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Apply a partial update to a catalog model, preserving its identity keys."""
        existing = self.get_model(model_id)
        merged = {
            **existing,
            **body,
            "id": existing["id"],
            "providerId": existing["providerId"],
            "model": existing["model"],
            "source": "operator_override",
        }
        return self.upsert_model(merged)

    def list_pricing_snapshots(
        self,
        *,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """List pricing snapshots, optionally filtered by provider and/or model, newest first."""
        clauses: list[str] = []
        params: list[Any] = []
        if provider_id:
            clauses.append("provider_id = ?")
            params.append(provider_id)
        if model:
            clauses.append("model = ?")
            params.append(model)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT {PRICING_SNAPSHOT_COLUMNS} FROM pricing_snapshots {where} ORDER BY created_at DESC",
            tuple(params),
        ).fetchall()
        return [row_to_pricing_snapshot(row) for row in rows]

    def create_pricing_snapshot(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert a pricing snapshot and, when `applyToCatalog`, propagate its prices to the model.

        Source ref and metadata are redacted before storage; the snapshot insert and the
        optional catalog upsert share the caller's transaction.
        """
        provider_id = str(body["providerId"])
        model = str(body["model"])
        snapshot_id = str(body.get("id") or f"pricing-snapshot-{uuid.uuid4()}")
        source_ref = str(redact_secrets(str(body.get("sourceRef") or "manual")))[:240]
        metadata = redact_secrets(body.get("metadata") or {})
        apply_to_catalog = bool(body.get("applyToCatalog", False))
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO pricing_snapshots
                (id, provider_id, model, input_price_per_mtok, cached_input_price_per_mtok,
                 output_price_per_mtok, reasoning_price_per_mtok, free_tier, source_ref,
                 effective_at, metadata_json, apply_to_catalog, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                provider_id,
                model,
                body.get("inputPricePerMtok"),
                body.get("cachedInputPricePerMtok"),
                body.get("outputPricePerMtok"),
                body.get("reasoningPricePerMtok"),
                1 if body.get("freeTier", False) else 0,
                source_ref,
                body.get("effectiveAt"),
                json_dumps(metadata),
                1 if apply_to_catalog else 0,
                now,
            ),
        )
        snapshot = self.get_pricing_snapshot(snapshot_id)
        if apply_to_catalog:
            try:
                existing = self.get_model(f"{provider_id}:{model}")
            except KeyError:
                existing = {
                    "providerId": provider_id,
                    "model": model,
                    "displayName": model,
                    "enabled": True,
                }
            self.upsert_model(
                {
                    **existing,
                    "providerId": provider_id,
                    "model": model,
                    "inputPricePerMtok": snapshot["inputPricePerMtok"],
                    "cachedInputPricePerMtok": snapshot["cachedInputPricePerMtok"],
                    "outputPricePerMtok": snapshot["outputPricePerMtok"],
                    "reasoningPricePerMtok": snapshot["reasoningPricePerMtok"],
                    "freeTier": snapshot["freeTier"],
                    "source": f"pricing_snapshot:{snapshot_id}",
                }
            )
        return snapshot

    def get_pricing_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Fetch one pricing snapshot by id.

        Raises:
            KeyError: if no snapshot has the given id.
        """
        row = self.connection.execute(
            f"SELECT {PRICING_SNAPSHOT_COLUMNS} FROM pricing_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Pricing snapshot not found: {snapshot_id}")
        return row_to_pricing_snapshot(row)

    def record_health_check(
        self, *, provider_id: str, status: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a provider health-check result and update the account's health summary.

        Writes a `provider_health_checks` row (payload redacted) and patches the account's
        health status, timestamp, and last error within the caller's transaction.
        """
        now = utc_now()
        check_id = f"provider-health-{uuid.uuid4()}"
        health_status = str(payload.get("healthStatus") or status)
        last_error = str(payload.get("lastError") or "")
        if not last_error and health_status != "healthy":
            last_error = str(payload.get("message") or payload.get("status") or "")
        last_error = str(redact_secrets(last_error))
        self.connection.execute(
            """
            INSERT INTO provider_health_checks (id, provider_id, status, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (check_id, provider_id, status, json_dumps(redact_secrets(payload)), now),
        )
        self.patch_provider_account(
            provider_id,
            {"healthStatus": health_status, "lastHealthCheckAt": now, "lastError": last_error},
        )
        return {
            "id": check_id,
            "providerId": provider_id,
            "status": status,
            "payload": redact_secrets(payload),
            "createdAt": now,
        }
