"""Atomic admission, settlement, and provider rate-limit evidence.

Configured policy remains in ``provider_limits``. Runtime counters live in normalized
window rows and are reserved through short SQLite ``BEGIN IMMEDIATE`` transactions.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from local_control_center.shared.serialization import json_dumps

DEFAULT_LEASE_TTL_SECONDS = 300
MAX_LEASE_TTL_SECONDS = 86_400
MAX_RETRY_AFTER_SECONDS = 86_400
RATE_LIMIT_HEADER_ALLOWLIST = frozenset(
    {
        "ratelimit-limit",
        "ratelimit-remaining",
        "ratelimit-reset",
        "x-ratelimit-limit-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    }
)


@dataclass(frozen=True)
class QuotaRequest:
    """Capacity an execution branch must reserve before external transport."""

    provider_id: str
    model: str
    reserved_tokens: int
    estimated_cost_usd: float | None = None
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    execution_id: str | None = None
    branch_id: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be empty")
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.reserved_tokens < 0:
            raise ValueError("reserved_tokens must be non-negative")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be non-negative")
        if not 1 <= self.lease_ttl_seconds <= MAX_LEASE_TTL_SECONDS:
            raise ValueError(f"lease_ttl_seconds must be between 1 and {MAX_LEASE_TTL_SECONDS}")


@dataclass(frozen=True)
class QuotaLease:
    """Persisted admission lease returned to the provider execution boundary."""

    id: str
    limit_id: str | None
    provider_id: str
    model: str
    state: str
    expires_at: str
    guarded: bool


class QuotaAdmissionDenied(RuntimeError):
    """Raised before transport when configured capacity cannot be reserved."""

    def __init__(
        self,
        *,
        reason: str,
        limit_id: str,
        cooldown_until: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.limit_id = limit_id
        self.cooldown_until = cooldown_until


class QuotaStateConflict(RuntimeError):
    """Raised when a lease is settled through an invalid state transition."""


@dataclass(frozen=True)
class QuotaResult:
    """Read-only quota preview used by routing and status APIs."""

    allowed: bool
    reason: str
    cooldown_until: str | None = None
    limit_id: str | None = None
    quota_pressure: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialize the stable camelCase routing preview contract."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "cooldownUntil": self.cooldown_until,
            "limitId": self.limit_id,
            "quotaPressure": self.quota_pressure,
        }


@dataclass(frozen=True)
class _WindowBounds:
    kind: str
    start: str
    end: str


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("QuotaManager clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _bounded_retry_after(
    raw_value: str | None,
    *,
    now: datetime,
    fallback_seconds: int,
) -> tuple[int, str]:
    value = (raw_value or "").strip()
    if value.isdecimal():
        return min(int(value), MAX_RETRY_AFTER_SECONDS), "delta_seconds"
    if value:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            delta = max(0, ceil((parsed.astimezone(UTC) - now).total_seconds()))
            return min(delta, MAX_RETRY_AFTER_SECONDS), "http_date"
        except (TypeError, ValueError, OverflowError):
            pass
    return min(max(fallback_seconds, 0), MAX_RETRY_AFTER_SECONDS), "configured_fallback"


def _safe_error_class(value: str | None) -> str:
    candidate = (value or "").strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", candidate):
        return candidate
    return "provider_rate_limit"


def _safe_rate_limit_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, raw_value in (headers or {}).items():
        key = str(name).strip().lower()
        value = str(raw_value).strip()
        if key in RATE_LIMIT_HEADER_ALLOWLIST and re.fullmatch(r"[0-9.,: +\-]{1,64}", value):
            normalized[key] = value
    return normalized


@contextmanager
def _atomic(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Use BEGIN IMMEDIATE, nesting through a unique savepoint when required."""
    if connection.in_transaction:
        savepoint = f"quota_{uuid.uuid4().hex}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield connection
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _window_bounds(now: datetime, timezone_name: str) -> tuple[_WindowBounds, ...]:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown IANA timezone: {timezone_name}") from error
    local_now = now.astimezone(timezone)
    minute_start = local_now.replace(second=0, microsecond=0)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    def bounds(kind: str, start: datetime, end: datetime) -> _WindowBounds:
        return _WindowBounds(
            kind=kind,
            start=start.astimezone(UTC).isoformat(),
            end=end.astimezone(UTC).isoformat(),
        )

    return (
        bounds("minute", minute_start, minute_start + timedelta(minutes=1)),
        bounds("day", day_start, day_start + timedelta(days=1)),
        bounds("month", month_start, month_end),
    )


class QuotaManager:
    """Atomically reserves and reconciles endpoint/model capacity."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.connection = connection
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    def _resolve_policy(self, provider_id: str, model: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT *
            FROM provider_limits
            WHERE provider_id = ? AND model IN (?, '*')
            ORDER BY CASE WHEN model = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (provider_id, model, model),
        ).fetchone()

    def _window_usage(
        self,
        *,
        limit_id: str,
        bounds: tuple[_WindowBounds, ...],
    ) -> dict[str, dict[str, int | float]]:
        usage: dict[str, dict[str, int | float]] = {}
        for window in bounds:
            row = self.connection.execute(
                """
                SELECT committed_requests, reserved_requests, committed_tokens,
                       unverified_tokens, reserved_tokens, known_cost_usd,
                       unverified_cost_usd, reserved_cost_usd
                FROM provider_limit_windows
                WHERE limit_id = ? AND window_kind = ? AND window_start = ?
                """,
                (limit_id, window.kind, window.start),
            ).fetchone()
            usage[window.kind] = {
                "requests": int(row["committed_requests"] + row["reserved_requests"]) if row else 0,
                "tokens": int(row["committed_tokens"] + row["unverified_tokens"] + row["reserved_tokens"])
                if row
                else 0,
                "cost": float(row["known_cost_usd"] + row["unverified_cost_usd"] + row["reserved_cost_usd"])
                if row
                else 0.0,
            }
        return usage

    def _active_count(self, limit_id: str, now: datetime) -> int:
        return int(
            self.connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM provider_execution_leases
                WHERE limit_id = ? AND state IN ('active', 'dispatched') AND expires_at > ?
                """,
                (limit_id, now.isoformat()),
            ).fetchone()["total"]
        )

    def _evaluate(
        self,
        *,
        policy: sqlite3.Row | None,
        now: datetime,
        request_tokens: int,
        estimated_cost_usd: float | None,
    ) -> QuotaResult:
        if policy is None:
            return QuotaResult(allowed=True, reason="no_limit")
        limit_id = str(policy["id"])
        if not bool(policy["enabled"]):
            return QuotaResult(allowed=True, reason="limit_disabled", limit_id=limit_id)
        cooldown = _parse_utc(policy["cooldown_until"])
        if cooldown and cooldown > now:
            return QuotaResult(
                allowed=False,
                reason="provider_in_cooldown",
                cooldown_until=policy["cooldown_until"],
                limit_id=limit_id,
                quota_pressure=1.0,
            )
        if policy["max_concurrency"] is not None and self._active_count(limit_id, now) >= int(
            policy["max_concurrency"]
        ):
            return QuotaResult(
                allowed=False,
                reason="blocked_concurrency",
                limit_id=limit_id,
                quota_pressure=1.0,
            )
        has_cost_guard = (
            policy["max_cost_per_request_usd"] is not None or policy["monthly_budget_usd"] is not None
        )
        if (
            estimated_cost_usd is None
            and has_cost_guard
            and str(policy["unknown_limit_strategy"]).lower() != "allow"
        ):
            return QuotaResult(
                allowed=False,
                reason="unknown_cost_blocked",
                limit_id=limit_id,
                quota_pressure=1.0,
            )
        if (
            estimated_cost_usd is not None
            and policy["max_cost_per_request_usd"] is not None
            and estimated_cost_usd > float(policy["max_cost_per_request_usd"])
        ):
            return QuotaResult(
                allowed=False,
                reason="max_cost_per_request_exceeded",
                limit_id=limit_id,
                quota_pressure=1.0,
            )
        bounds = _window_bounds(now, str(policy["window_timezone"]))
        usage = self._window_usage(limit_id=limit_id, bounds=bounds)
        minute, day, month = usage["minute"], usage["day"], usage["month"]
        checks = (
            (policy["rpm"], int(minute["requests"]) + 1, "rpm_limit_exceeded"),
            (policy["tpm"], int(minute["tokens"]) + request_tokens, "tpm_limit_exceeded"),
            (policy["daily_requests"], int(day["requests"]) + 1, "daily_request_limit_exceeded"),
            (policy["daily_tokens"], int(day["tokens"]) + request_tokens, "daily_token_limit_exceeded"),
            (
                policy["monthly_requests"],
                int(month["requests"]) + 1,
                "monthly_request_limit_exceeded",
            ),
            (
                policy["monthly_tokens"],
                int(month["tokens"]) + request_tokens,
                "monthly_token_limit_exceeded",
            ),
        )
        for configured, projected, reason in checks:
            if configured is not None and projected > int(configured):
                return QuotaResult(
                    allowed=False,
                    reason=reason,
                    limit_id=limit_id,
                    quota_pressure=1.0,
                )
        if (
            estimated_cost_usd is not None
            and policy["monthly_budget_usd"] is not None
            and float(month["cost"]) + estimated_cost_usd > float(policy["monthly_budget_usd"])
        ):
            return QuotaResult(
                allowed=False,
                reason="monthly_budget_exceeded",
                limit_id=limit_id,
                quota_pressure=1.0,
            )
        pressure = 0.0
        if policy["tpm"]:
            pressure = max(
                pressure,
                min((int(minute["tokens"]) + request_tokens) / int(policy["tpm"]), 1.0),
            )
        if policy["rpm"]:
            pressure = max(
                pressure,
                min((int(minute["requests"]) + 1) / int(policy["rpm"]), 1.0),
            )
        return QuotaResult(
            allowed=True,
            reason="within_limit",
            limit_id=limit_id,
            quota_pressure=pressure,
        )

    def _expire_stale(self, now: datetime) -> None:
        stale = self.connection.execute(
            """
            SELECT id, state
            FROM provider_execution_leases
            WHERE state IN ('active', 'dispatched') AND expires_at <= ?
            """,
            (now.isoformat(),),
        ).fetchall()
        for row in stale:
            self._release_reservations(
                str(row["id"]),
                source_state=str(row["state"]),
                terminal_state="expired",
                reason="lease_expired",
                now=now,
            )

    def _insert_lease(
        self,
        request: QuotaRequest,
        *,
        policy: sqlite3.Row | None,
        now: datetime,
        guarded: bool,
    ) -> QuotaLease:
        lease_id = f"quota-lease-{uuid.uuid4()}"
        expires_at = now + timedelta(seconds=request.lease_ttl_seconds)
        limit_id = str(policy["id"]) if policy is not None else None
        self.connection.execute(
            """
            INSERT INTO provider_execution_leases
                (id, limit_id, provider_id, model, state, reserved_tokens,
                 reserved_cost_usd, actual_tokens, actual_cost_usd, usage_known,
                 cost_known, expires_at, execution_id, branch_id, dispatched_at,
                 released_at, release_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?,
                    NULL, NULL, NULL, ?, ?)
            """,
            (
                lease_id,
                limit_id,
                request.provider_id,
                request.model,
                request.reserved_tokens,
                request.estimated_cost_usd,
                expires_at.isoformat(),
                request.execution_id,
                request.branch_id,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        if guarded and policy is not None:
            for window in _window_bounds(now, str(policy["window_timezone"])):
                window_id = f"{limit_id}:{window.kind}:{window.start}"
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO provider_limit_windows
                        (id, limit_id, window_kind, window_start, window_end,
                         committed_requests, reserved_requests, committed_tokens,
                         unverified_tokens, reserved_tokens, known_cost_usd,
                         unverified_cost_usd, reserved_cost_usd, unknown_usage_count,
                         unknown_cost_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
                    """,
                    (
                        window_id,
                        limit_id,
                        window.kind,
                        window.start,
                        window.end,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self.connection.execute(
                    """
                    UPDATE provider_limit_windows
                    SET reserved_requests = reserved_requests + 1,
                        reserved_tokens = reserved_tokens + ?,
                        reserved_cost_usd = reserved_cost_usd + ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.reserved_tokens,
                        request.estimated_cost_usd or 0.0,
                        now.isoformat(),
                        window_id,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO provider_execution_lease_windows
                        (lease_id, window_id, reserved_requests, reserved_tokens,
                         reserved_cost_usd)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (
                        lease_id,
                        window_id,
                        request.reserved_tokens,
                        request.estimated_cost_usd or 0.0,
                    ),
                )
        return QuotaLease(
            id=lease_id,
            limit_id=limit_id,
            provider_id=request.provider_id,
            model=request.model,
            state="active",
            expires_at=expires_at.isoformat(),
            guarded=guarded,
        )

    def acquire(self, request: QuotaRequest) -> QuotaLease:
        """Atomically validate every guard and reserve capacity before transport."""
        with _atomic(self.connection):
            now = self._now()
            self._expire_stale(now)
            policy = self._resolve_policy(request.provider_id, request.model)
            guarded = policy is not None and bool(policy["enabled"])
            result = self._evaluate(
                policy=policy,
                now=now,
                request_tokens=request.reserved_tokens,
                estimated_cost_usd=request.estimated_cost_usd,
            )
            if not result.allowed:
                raise QuotaAdmissionDenied(
                    reason=result.reason,
                    limit_id=str(result.limit_id),
                    cooldown_until=result.cooldown_until,
                )
            return self._insert_lease(
                request,
                policy=policy,
                now=now,
                guarded=guarded,
            )

    def _lease_row(self, lease_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM provider_execution_leases WHERE id = ?",
            (lease_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Quota lease not found: {lease_id}")
        return row

    def _lease_record(self, row: sqlite3.Row) -> QuotaLease:
        mapped = self.connection.execute(
            "SELECT 1 FROM provider_execution_lease_windows WHERE lease_id = ? LIMIT 1",
            (row["id"],),
        ).fetchone()
        return QuotaLease(
            id=str(row["id"]),
            limit_id=str(row["limit_id"]) if row["limit_id"] is not None else None,
            provider_id=str(row["provider_id"]),
            model=str(row["model"]),
            state=str(row["state"]),
            expires_at=str(row["expires_at"]),
            guarded=mapped is not None,
        )

    def _lease_mappings(self, lease_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT window_id, reserved_requests, reserved_tokens, reserved_cost_usd
            FROM provider_execution_lease_windows
            WHERE lease_id = ?
            """,
            (lease_id,),
        ).fetchall()

    def _commit_reserved_requests(
        self,
        mappings: list[sqlite3.Row],
        *,
        now: datetime,
    ) -> None:
        for mapping in mappings:
            self.connection.execute(
                """
                UPDATE provider_limit_windows
                SET reserved_requests = MAX(0, reserved_requests - ?),
                    committed_requests = committed_requests + ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    mapping["reserved_requests"],
                    mapping["reserved_requests"],
                    now.isoformat(),
                    mapping["window_id"],
                ),
            )

    def _release_reservations(
        self,
        lease_id: str,
        *,
        source_state: str,
        terminal_state: str,
        reason: str,
        now: datetime,
    ) -> None:
        mappings = self._lease_mappings(lease_id)
        for mapping in mappings:
            if source_state == "dispatched":
                self.connection.execute(
                    """
                    UPDATE provider_limit_windows
                    SET reserved_tokens = MAX(0, reserved_tokens - ?),
                        unverified_tokens = unverified_tokens + ?,
                        reserved_cost_usd = MAX(0, reserved_cost_usd - ?),
                        unverified_cost_usd = unverified_cost_usd + ?,
                        unknown_usage_count = unknown_usage_count + 1,
                        unknown_cost_count = unknown_cost_count + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        mapping["reserved_tokens"],
                        mapping["reserved_tokens"],
                        mapping["reserved_cost_usd"],
                        mapping["reserved_cost_usd"],
                        now.isoformat(),
                        mapping["window_id"],
                    ),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE provider_limit_windows
                    SET reserved_requests = MAX(0, reserved_requests - ?),
                        reserved_tokens = MAX(0, reserved_tokens - ?),
                        reserved_cost_usd = MAX(0, reserved_cost_usd - ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        mapping["reserved_requests"],
                        mapping["reserved_tokens"],
                        mapping["reserved_cost_usd"],
                        now.isoformat(),
                        mapping["window_id"],
                    ),
                )
        self.connection.execute(
            """
            UPDATE provider_execution_leases
            SET state = ?, usage_known = CASE WHEN ? = 'dispatched' THEN 0 ELSE usage_known END,
                cost_known = CASE WHEN ? = 'dispatched' THEN 0 ELSE cost_known END,
                released_at = ?, release_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                terminal_state,
                source_state,
                source_state,
                now.isoformat(),
                reason,
                now.isoformat(),
                lease_id,
            ),
        )

    def mark_dispatched(self, lease_id: str) -> QuotaLease:
        """Mark the exact point before transport and count the provider attempt once."""
        with _atomic(self.connection):
            now = self._now()
            row = self._lease_row(lease_id)
            state = str(row["state"])
            if state == "dispatched":
                return self._lease_record(row)
            if state != "active":
                raise QuotaStateConflict(f"Cannot dispatch lease in state {state}")
            self._commit_reserved_requests(self._lease_mappings(lease_id), now=now)
            self.connection.execute(
                """
                UPDATE provider_execution_leases
                SET state = 'dispatched', dispatched_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), now.isoformat(), lease_id),
            )
            return self._lease_record(self._lease_row(lease_id))

    def commit(
        self,
        lease_id: str,
        *,
        actual_tokens: int | None,
        actual_cost_usd: float | None,
    ) -> QuotaLease:
        """Settle a successful call, preserving unknown provider usage as unverified."""
        if actual_tokens is not None and actual_tokens < 0:
            raise ValueError("actual_tokens must be non-negative")
        if actual_cost_usd is not None and actual_cost_usd < 0:
            raise ValueError("actual_cost_usd must be non-negative")
        with _atomic(self.connection):
            now = self._now()
            row = self._lease_row(lease_id)
            state = str(row["state"])
            if state == "committed":
                if row["actual_tokens"] == actual_tokens and row["actual_cost_usd"] == actual_cost_usd:
                    return self._lease_record(row)
                raise QuotaStateConflict("Lease already committed with different settlement")
            if state not in {"active", "dispatched"}:
                raise QuotaStateConflict(f"Cannot commit lease in state {state}")
            mappings = self._lease_mappings(lease_id)
            if state == "active":
                self._commit_reserved_requests(mappings, now=now)
            for mapping in mappings:
                self.connection.execute(
                    """
                    UPDATE provider_limit_windows
                    SET reserved_tokens = MAX(0, reserved_tokens - ?),
                        committed_tokens = committed_tokens + ?,
                        unverified_tokens = unverified_tokens + ?,
                        reserved_cost_usd = MAX(0, reserved_cost_usd - ?),
                        known_cost_usd = known_cost_usd + ?,
                        unverified_cost_usd = unverified_cost_usd + ?,
                        unknown_usage_count = unknown_usage_count + ?,
                        unknown_cost_count = unknown_cost_count + ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        mapping["reserved_tokens"],
                        actual_tokens or 0,
                        mapping["reserved_tokens"] if actual_tokens is None else 0,
                        mapping["reserved_cost_usd"],
                        actual_cost_usd or 0.0,
                        mapping["reserved_cost_usd"] if actual_cost_usd is None else 0.0,
                        1 if actual_tokens is None else 0,
                        1 if actual_cost_usd is None else 0,
                        now.isoformat(),
                        mapping["window_id"],
                    ),
                )
            self.connection.execute(
                """
                UPDATE provider_execution_leases
                SET state = 'committed', actual_tokens = ?, actual_cost_usd = ?,
                    usage_known = ?, cost_known = ?, released_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    actual_tokens,
                    actual_cost_usd,
                    0 if actual_tokens is None else 1,
                    0 if actual_cost_usd is None else 1,
                    now.isoformat(),
                    now.isoformat(),
                    lease_id,
                ),
            )
            return self._lease_record(self._lease_row(lease_id))

    def release(self, lease_id: str, *, reason: str) -> QuotaLease:
        """Release a failed/cancelled call; post-dispatch unknown use stays conservative."""
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("release reason must not be empty")
        with _atomic(self.connection):
            now = self._now()
            row = self._lease_row(lease_id)
            state = str(row["state"])
            if state == "released":
                if row["release_reason"] == normalized_reason:
                    return self._lease_record(row)
                raise QuotaStateConflict("Lease already released with a different reason")
            if state == "expired":
                return self._lease_record(row)
            if state == "committed":
                raise QuotaStateConflict("Cannot release lease in state committed")
            self._release_reservations(
                lease_id,
                source_state=state,
                terminal_state="released",
                reason=normalized_reason,
                now=now,
            )
            return self._lease_record(self._lease_row(lease_id))

    def check(self, *, provider_id: str, model: str, request_tokens: int) -> QuotaResult:
        """Read-only preview; it never creates a lease or advances counters."""
        if request_tokens < 0:
            raise ValueError("request_tokens must be non-negative")
        now = self._now()
        return self._evaluate(
            policy=self._resolve_policy(provider_id, model),
            now=now,
            request_tokens=request_tokens,
            estimated_cost_usd=None,
        )

    def status(
        self,
        *,
        provider_id: str,
        model: str,
        request_tokens: int = 0,
        estimated_cost_usd: float | None = None,
    ) -> dict[str, Any]:
        """Return effective policy and counters without reserving or expiring leases."""
        if request_tokens < 0:
            raise ValueError("request_tokens must be non-negative")
        if estimated_cost_usd is not None and estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be non-negative")
        now = self._now()
        policy = self._resolve_policy(provider_id, model)
        result = self._evaluate(
            policy=policy,
            now=now,
            request_tokens=request_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )
        windows: list[dict[str, Any]] = []
        active_leases = 0
        if policy is not None:
            limit_id = str(policy["id"])
            active_leases = self._active_count(limit_id, now)
            for bounds in _window_bounds(now, str(policy["window_timezone"])):
                row = self.connection.execute(
                    """
                    SELECT committed_requests, reserved_requests, committed_tokens,
                           unverified_tokens, reserved_tokens, known_cost_usd,
                           unverified_cost_usd, reserved_cost_usd
                    FROM provider_limit_windows
                    WHERE limit_id = ? AND window_kind = ? AND window_start = ?
                    """,
                    (limit_id, bounds.kind, bounds.start),
                ).fetchone()
                windows.append(
                    {
                        "kind": bounds.kind,
                        "startsAt": bounds.start,
                        "resetsAt": bounds.end,
                        "committedRequests": int(row["committed_requests"]) if row else 0,
                        "reservedRequests": int(row["reserved_requests"]) if row else 0,
                        "committedTokens": int(row["committed_tokens"]) if row else 0,
                        "unverifiedTokens": int(row["unverified_tokens"]) if row else 0,
                        "reservedTokens": int(row["reserved_tokens"]) if row else 0,
                        "knownCostUsd": float(row["known_cost_usd"]) if row else 0.0,
                        "unverifiedCostUsd": float(row["unverified_cost_usd"]) if row else 0.0,
                        "reservedCostUsd": float(row["reserved_cost_usd"]) if row else 0.0,
                    }
                )
        return {
            "providerId": provider_id,
            "model": model,
            "effectiveLimitId": result.limit_id,
            "guarded": policy is not None and bool(policy["enabled"]),
            "enabled": bool(policy["enabled"]) if policy is not None else False,
            "allowed": result.allowed,
            "reason": result.reason,
            "cooldownUntil": result.cooldown_until,
            "quotaPressure": result.quota_pressure,
            "activeLeases": active_leases,
            "windows": windows,
        }

    def providers_in_cooldown(self) -> set[str]:
        """Reúne los providers cuya cuota está agotada ahora mismo, según el cooldown persistido.

        Lo escribe :meth:`record_rate_limit` cuando el proveedor responde 429 o declara crédito
        agotado. Ante cualquier problema de lectura devuelve vacío: esta señal es auxiliar y su
        fallo no puede dejar a la instalación sin runtimes.

        La vigencia se compara como fecha y no como texto en SQL: ``utc_now`` emite milisegundos
        con sufijo ``Z`` mientras que el cooldown se guarda con ``isoformat`` (microsegundos y
        offset ``+00:00``), así que comparar cadenas ataría el resultado a que ninguno de los dos
        formatos cambie nunca.
        """
        try:
            rows = self.connection.execute(
                """
                SELECT provider_id, cooldown_until FROM provider_limits
                WHERE enabled = 1 AND cooldown_until IS NOT NULL
                """
            ).fetchall()
        except sqlite3.Error:
            return set()
        now = self._now()
        return {
            str(row["provider_id"])
            for row in rows
            if (cooldown := _parse_utc(row["cooldown_until"])) is not None and cooldown > now
        }

    def record_rate_limit(
        self,
        *,
        provider_id: str,
        model: str,
        retry_after_seconds: int | None = None,
        retry_after: str | None = None,
        headers: Mapping[str, str] | None = None,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        """Record bounded 429 evidence without replacing configured operator limits."""
        normalized_model = model or "*"
        with _atomic(self.connection):
            now = self._now()
            effective = self._resolve_policy(provider_id, normalized_model)
            fallback_seconds = (
                int(effective["fallback_retry_after_seconds"]) if effective is not None else 300
            )
            header_retry_after = next(
                (
                    str(value)
                    for name, value in (headers or {}).items()
                    if str(name).strip().lower() == "retry-after"
                ),
                None,
            )
            raw_retry_after = retry_after if retry_after is not None else header_retry_after
            if raw_retry_after is not None:
                bounded_seconds, source = _bounded_retry_after(
                    raw_retry_after,
                    now=now,
                    fallback_seconds=fallback_seconds,
                )
            elif retry_after_seconds is not None:
                bounded_seconds = min(
                    max(int(retry_after_seconds), 0),
                    MAX_RETRY_AFTER_SECONDS,
                )
                source = "legacy_seconds"
            else:
                bounded_seconds, source = _bounded_retry_after(
                    None,
                    now=now,
                    fallback_seconds=fallback_seconds,
                )
            cooldown = now + timedelta(seconds=bounded_seconds)
            timestamp = now.isoformat()
            limit_id = f"{provider_id}:{normalized_model}"
            exact = self.connection.execute(
                "SELECT * FROM provider_limits WHERE provider_id = ? AND model = ?",
                (provider_id, normalized_model),
            ).fetchone()
            if exact is None:
                inherited = effective
                self.connection.execute(
                    """
                    INSERT INTO provider_limits
                        (id, provider_id, model, rpm, tpm, daily_requests, daily_tokens,
                         monthly_requests, monthly_tokens, monthly_budget_usd,
                         current_window_json, cooldown_until, last_429_at,
                         last_limit_error_at, unknown_limit_strategy, max_concurrency,
                         window_timezone, enabled, fallback_retry_after_seconds,
                         max_cost_per_request_usd, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        limit_id,
                        provider_id,
                        normalized_model,
                        inherited["rpm"] if inherited is not None else None,
                        inherited["tpm"] if inherited is not None else None,
                        inherited["daily_requests"] if inherited is not None else None,
                        inherited["daily_tokens"] if inherited is not None else None,
                        inherited["monthly_requests"] if inherited is not None else None,
                        inherited["monthly_tokens"] if inherited is not None else None,
                        inherited["monthly_budget_usd"] if inherited is not None else None,
                        cooldown.isoformat(),
                        timestamp,
                        timestamp,
                        inherited["unknown_limit_strategy"] if inherited is not None else "conservative",
                        inherited["max_concurrency"] if inherited is not None else None,
                        inherited["window_timezone"] if inherited is not None else "UTC",
                        inherited["enabled"] if inherited is not None else 1,
                        inherited["fallback_retry_after_seconds"] if inherited is not None else 300,
                        inherited["max_cost_per_request_usd"] if inherited is not None else None,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                limit_id = str(exact["id"])
                self.connection.execute(
                    """
                    UPDATE provider_limits
                    SET cooldown_until = ?, last_429_at = ?, last_limit_error_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (cooldown.isoformat(), timestamp, timestamp, timestamp, limit_id),
                )
            safe_headers = _safe_rate_limit_headers(headers)
            metadata: dict[str, Any] = {
                "statusCode": 429,
                "retryAfterSource": source,
                "retryAfterSeconds": bounded_seconds,
                "errorClass": _safe_error_class(error_class),
            }
            if safe_headers:
                metadata["rateLimitHeaders"] = safe_headers
            observation_id = f"provider-limit-observation-{uuid.uuid4()}"
            self.connection.execute(
                """
                INSERT INTO provider_limit_observations
                    (id, limit_id, provider_id, model, kind, observed_at,
                     retry_after_at, metadata_json)
                VALUES (?, ?, ?, ?, 'rate_limited', ?, ?, ?)
                """,
                (
                    observation_id,
                    limit_id,
                    provider_id,
                    normalized_model,
                    timestamp,
                    cooldown.isoformat(),
                    json_dumps(metadata),
                ),
            )
            return {
                "id": observation_id,
                "providerId": provider_id,
                "model": normalized_model,
                "kind": "rate_limited",
                "observedAt": timestamp,
                "retryAfterAt": cooldown.isoformat(),
                "retryAfterSeconds": bounded_seconds,
            }
