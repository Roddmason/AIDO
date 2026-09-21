"""Durable model validation from real executions, bound to non-secret configuration.

Catalog discovery, health checks and performance priors never constitute model validation.
Only identifiers, configuration hashes, timestamps and structured outcomes are retained.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from .credentials import CredentialResolver

VALIDATION_TTL_SECONDS = 24 * 60 * 60
AUTHENTICATION_COOLDOWN_SECONDS = 300


def provider_authentication_failure(connection, provider_id, fingerprint):
    """Apply account failures even when its other models retain successful receipts."""
    row = connection.execute(
        """SELECT success,http_status,started_at FROM model_execution_health
           WHERE provider_id=? AND configuration_fingerprint=?
           AND (success=1 OR http_status IN (401,403))
           ORDER BY started_at DESC,id DESC LIMIT 1""",
        (provider_id, fingerprint),
    ).fetchone()
    if row is None or row["success"]:
        return None
    try:
        age = (
            datetime.now(UTC) - datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
        ).total_seconds()
    except (ValueError, TypeError):
        return None
    return row["http_status"] if 0 <= age < AUTHENTICATION_COOLDOWN_SECONDS else None


def provider_configuration_fingerprint(connection: sqlite3.Connection, provider_id: str) -> str:
    """Hash connection identity and CLI configuration without resolving credential values."""
    row = connection.execute(
        """SELECT provider_id, provider_type, api_format, provider_family, deployment_mode,
                  api_family, adapter_profile, base_url, credential_ref, enabled
           FROM provider_accounts WHERE provider_id = ? OR id = ?""",
        (provider_id, provider_id),
    ).fetchone()
    if row is None:
        return ""
    provider = dict(row)
    # Legacy seeds and their persisted canonical forms describe the same transport.
    resolver = CredentialResolver()
    provider["credential_ref"] = resolver.normalize_ref_for_storage(provider["credential_ref"])
    provider["base_url"] = str(provider["base_url"] or "").strip().rstrip("/")
    configuration = {"provider": provider}
    if row["provider_type"] == "cli":
        installation = connection.execute(
            """SELECT executable_path, detected_version, enabled FROM runtime_installations
               WHERE runtime_id = ?""",
            (row["provider_id"],),
        ).fetchone()
        accounts = connection.execute(
            """SELECT id, auth_mode, credential_store_kind, credential_ref, enabled, is_default
               FROM runtime_accounts WHERE runtime_id = ? ORDER BY id""",
            (row["provider_id"],),
        ).fetchall()
        configuration["installation"] = dict(installation) if installation else None
        configuration["accounts"] = [
            {**dict(account), "credential_ref": resolver.normalize_ref_for_storage(account["credential_ref"])}
            for account in accounts
        ]
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_model_execution(
    connection: sqlite3.Connection,
    provider_id: str,
    model: str,
    success: bool,
    source: str,
    http_status: int | None = None,
    configuration_fingerprint: str | None = None,
    started_at: str | None = None,
) -> None:
    """Append an executed attempt using the configuration and start time captured before invocation."""
    if source not in {"tool_broker", "test_prompt", "model_gateway"}:
        raise ValueError("Unknown model execution evidence source")
    fingerprint = (
        provider_configuration_fingerprint(connection, provider_id)
        if configuration_fingerprint is None
        else configuration_fingerprint
    )
    if not fingerprint or not model.strip():
        return
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    started = datetime.fromisoformat((started_at or now).replace("Z", "+00:00"))
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    connection.execute(
        """INSERT INTO model_execution_health
           (provider_id, model, configuration_fingerprint, success, source, http_status, started_at, observed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            provider_id,
            model,
            fingerprint,
            int(success),
            source,
            http_status,
            started.astimezone(UTC).isoformat(timespec="microseconds"),
            now,
        ),
    )


def model_validation_rejection(connection: sqlite3.Connection, provider_id: str, model: str) -> str | None:
    """Reject absent, changed, failed or expired evidence; late older successes cannot rehabilitate."""
    catalog = connection.execute(
        "SELECT enabled FROM model_catalog WHERE provider_id = ? AND model = ?", (provider_id, model)
    ).fetchone()
    if catalog is not None and not catalog["enabled"]:
        return "model_disabled"
    fingerprint = provider_configuration_fingerprint(connection, provider_id)
    if not fingerprint:
        return "model_validation_configuration_required"
    if provider_authentication_failure(connection, provider_id, fingerprint):
        return "provider_authentication_cooldown"
    row = connection.execute(
        """SELECT configuration_fingerprint, success, started_at FROM model_execution_health
           WHERE provider_id = ? AND model = ? ORDER BY started_at DESC, id DESC LIMIT 1""",
        (provider_id, model),
    ).fetchone()
    if row is None:
        return "model_validation_required"
    if row["configuration_fingerprint"] != fingerprint:
        return "model_validation_configuration_changed"
    if not row["success"]:
        return "model_validation_failed"
    try:
        started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - started).total_seconds()
    except (TypeError, ValueError):
        return "model_validation_expired"
    return None if 0 <= age < VALIDATION_TTL_SECONDS else "model_validation_expired"
