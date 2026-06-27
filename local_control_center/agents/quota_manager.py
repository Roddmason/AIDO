"""Enforces provider rate/quota limits and records cooldowns after 429 responses.

Checks a request against `provider_limits` (TPM, daily/monthly request and token
windows, active cooldown) before routing picks a provider, and persists a cooldown
window when the provider returns a rate-limit error. Returns a quota-pressure signal
so the router can prefer less-pressured providers.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now


@dataclass(frozen=True)
class QuotaResult:
    """Outcome of a quota check: allow/deny, the limiting reason, and quota pressure."""

    allowed: bool
    reason: str
    cooldown_until: str | None = None
    limit_id: str | None = None
    quota_pressure: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialize to the camelCase shape consumed by the routing/API layer."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "cooldownUntil": self.cooldown_until,
            "limitId": self.limit_id,
            "quotaPressure": self.quota_pressure,
        }


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class QuotaManager:
    """Evaluates and updates per-provider rate limits stored in `provider_limits`."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def check(self, *, provider_id: str, model: str, request_tokens: int) -> QuotaResult:
        """Decide whether a request fits the provider's limits and current usage window.

        Matches the most specific model limit (falling back to the wildcard row), denies
        on active cooldown or any exceeded TPM/request/token threshold, and otherwise
        allows with a pressure score derived from TPM utilization.
        """
        row = self.connection.execute(
            "SELECT * FROM provider_limits WHERE provider_id = ? AND (model = ? OR model = '*') ORDER BY model DESC LIMIT 1",
            (provider_id, model),
        ).fetchone()
        if not row:
            return QuotaResult(allowed=True, reason="no_limit")
        cooldown = _parse_utc(row["cooldown_until"])
        if cooldown and cooldown > datetime.now(UTC):
            return QuotaResult(
                allowed=False,
                reason="provider_in_cooldown",
                cooldown_until=row["cooldown_until"],
                limit_id=row["id"],
                quota_pressure=1.0,
            )
        if row["tpm"] is not None and request_tokens > int(row["tpm"]):
            return QuotaResult(
                allowed=False, reason="request_exceeds_tpm", limit_id=row["id"], quota_pressure=1.0
            )
        window = json_loads(row["current_window_json"], {})
        for limit_column, used_key, reason in (
            ("daily_requests", "dailyRequestsUsed", "daily_request_limit_exceeded"),
            ("monthly_requests", "monthlyRequestsUsed", "monthly_request_limit_exceeded"),
        ):
            limit = row[limit_column]
            if limit is not None and int(window.get(used_key) or 0) >= int(limit):
                return QuotaResult(allowed=False, reason=reason, limit_id=row["id"], quota_pressure=1.0)
        for limit_column, used_key, reason in (
            ("daily_tokens", "dailyTokensUsed", "daily_token_limit_exceeded"),
            ("monthly_tokens", "monthlyTokensUsed", "monthly_token_limit_exceeded"),
        ):
            limit = row[limit_column]
            used = int(window.get(used_key) or 0)
            if limit is not None and used + request_tokens > int(limit):
                return QuotaResult(allowed=False, reason=reason, limit_id=row["id"], quota_pressure=1.0)
        pressure = 0.0
        if row["tpm"]:
            pressure = max(pressure, min(request_tokens / max(int(row["tpm"]), 1), 1.0))
        return QuotaResult(allowed=True, reason="within_limit", limit_id=row["id"], quota_pressure=pressure)

    def record_rate_limit(self, *, provider_id: str, model: str, retry_after_seconds: int = 300) -> None:
        """Persist a cooldown window for a provider/model after a 429/rate-limit error.

        Upserts the limit row so subsequent `check` calls deny until the cooldown expires;
        only cooldown/error timestamps are written, leaving configured limits untouched.
        """
        now = utc_now()
        cooldown = (datetime.now(UTC) + timedelta(seconds=retry_after_seconds)).isoformat()
        limit_id = f"{provider_id}:{model or '*'}"
        self.connection.execute(
            """
            INSERT INTO provider_limits
                (id, provider_id, model, rpm, tpm, daily_requests, daily_tokens, monthly_requests,
                 monthly_tokens, monthly_budget_usd, current_window_json, cooldown_until, last_429_at,
                 last_limit_error_at, unknown_limit_strategy, created_at, updated_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}', ?, ?, ?, 'conservative', ?, ?)
            ON CONFLICT(provider_id, model) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                last_429_at = excluded.last_429_at,
                last_limit_error_at = excluded.last_limit_error_at,
                updated_at = excluded.updated_at
            """,
            (limit_id, provider_id, model or "*", cooldown, now, now, now, now),
        )
