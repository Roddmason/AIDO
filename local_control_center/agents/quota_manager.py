from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from local_control_center.shared.time import utc_now


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    reason: str
    cooldown_until: str | None = None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class QuotaManager:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def check(self, *, provider_id: str, model: str, request_tokens: int) -> QuotaResult:
        row = self.connection.execute(
            "SELECT * FROM provider_limits WHERE provider_id = ? AND (model = ? OR model = '*') ORDER BY model DESC LIMIT 1",
            (provider_id, model),
        ).fetchone()
        if not row:
            return QuotaResult(allowed=True, reason="no_limit")
        cooldown = _parse_utc(row["cooldown_until"])
        if cooldown and cooldown > datetime.now(timezone.utc):
            return QuotaResult(allowed=False, reason="provider_in_cooldown", cooldown_until=row["cooldown_until"])
        if row["tpm"] is not None and request_tokens > int(row["tpm"]):
            return QuotaResult(allowed=False, reason="request_exceeds_tpm")
        return QuotaResult(allowed=True, reason="within_limit")

    def record_rate_limit(self, *, provider_id: str, model: str, retry_after_seconds: int = 300) -> None:
        now = utc_now()
        cooldown = (datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)).isoformat()
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
