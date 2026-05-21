from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def _bool(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, int) else bool(value)


def credential_status(row: sqlite3.Row) -> str:
    ref = str(row["credential_ref"] or "")
    return CredentialResolver().status(ref)


def row_to_provider_account(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "displayName": row["display_name"],
        "providerType": row["provider_type"],
        "apiFormat": row["api_format"],
        "baseUrl": row["base_url"],
        "credentialRef": row["credential_ref"],
        "credentialStatus": credential_status(row),
        "enabled": _bool(row["enabled"]),
        "quotaMode": row["quota_mode"],
        "healthStatus": row["health_status"],
        "lastHealthCheckAt": row["last_health_check_at"],
        "lastError": redact_secrets(row["last_error"] or ""),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_model_catalog(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "displayName": row["display_name"],
        "modelFamily": row["model_family"],
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


class ProviderAccountStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_provider_accounts(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM provider_accounts ORDER BY provider_id ASC").fetchall()
        return [row_to_provider_account(row) for row in rows]

    def get_provider_account(self, provider_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM provider_accounts WHERE provider_id = ? OR id = ?", (provider_id, provider_id)).fetchone()
        if not row:
            raise KeyError(f"Provider account not found: {provider_id}")
        return row_to_provider_account(row)

    def upsert_provider_account(self, body: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(body["providerId"])
        resolver = CredentialResolver()
        credential_ref = resolver.normalize_ref_for_storage(str(body.get("credentialRef", "") or ""))
        resolver.validate_ref(credential_ref)
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO provider_accounts
                (id, provider_id, display_name, provider_type, api_format, base_url, credential_ref,
                 enabled, quota_mode, health_status, last_health_check_at, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                display_name = excluded.display_name,
                provider_type = excluded.provider_type,
                api_format = excluded.api_format,
                base_url = excluded.base_url,
                credential_ref = excluded.credential_ref,
                enabled = excluded.enabled,
                quota_mode = excluded.quota_mode,
                health_status = excluded.health_status,
                last_health_check_at = excluded.last_health_check_at,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                body.get("id") or provider_id,
                provider_id,
                body.get("displayName") or provider_id,
                body.get("providerType", "api"),
                body.get("apiFormat", "openai_compatible"),
                body.get("baseUrl", ""),
                credential_ref,
                1 if body.get("enabled", False) else 0,
                body.get("quotaMode", "none"),
                body.get("healthStatus", "unknown"),
                body.get("lastHealthCheckAt"),
                redact_secrets(body.get("lastError", "")),
                now,
                now,
            ),
        )
        return self.get_provider_account(provider_id)

    def patch_provider_account(self, provider_id: str, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_provider_account(provider_id)
        merged = {**existing, **body, "providerId": existing["providerId"]}
        return self.upsert_provider_account(merged)

    def list_models(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        if provider_id:
            rows = self.connection.execute(
                "SELECT * FROM model_catalog WHERE provider_id = ? ORDER BY provider_id ASC, model ASC",
                (provider_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM model_catalog ORDER BY provider_id ASC, model ASC").fetchall()
        return [row_to_model_catalog(row) for row in rows]

    def get_model(self, model_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM model_catalog WHERE id = ?", (model_id,)).fetchone()
        if not row:
            raise KeyError(f"Model not found: {model_id}")
        return row_to_model_catalog(row)

    def upsert_model(self, body: dict[str, Any]) -> dict[str, Any]:
        model_id = str(body.get("id") or f"{body['providerId']}:{body['model']}")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO model_catalog
                (id, provider_id, model, display_name, model_family, context_window, max_output_tokens,
                 supports_tools, supports_json, supports_streaming, supports_vision, supports_embeddings,
                 supports_rerank, supports_reasoning, supports_thinking, effort_levels_json,
                 input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
                 reasoning_price_per_mtok, free_tier, free_tier_notes, enabled, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, model) DO UPDATE SET
                display_name = excluded.display_name,
                model_family = excluded.model_family,
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
        row = self.connection.execute("SELECT * FROM model_catalog WHERE provider_id = ? AND model = ?", (body["providerId"], body["model"])).fetchone()
        return row_to_model_catalog(row)

    def patch_model(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_model(model_id)
        merged = {**existing, **body, "id": existing["id"], "providerId": existing["providerId"], "model": existing["model"]}
        return self.upsert_model(merged)

    def record_health_check(self, *, provider_id: str, status: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        check_id = f"provider-health-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO provider_health_checks (id, provider_id, status, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (check_id, provider_id, status, json_dumps(redact_secrets(payload)), now),
        )
        self.patch_provider_account(provider_id, {"healthStatus": payload.get("healthStatus", status), "lastHealthCheckAt": now, "lastError": payload.get("lastError", "")})
        return {"id": check_id, "providerId": provider_id, "status": status, "payload": redact_secrets(payload), "createdAt": now}
