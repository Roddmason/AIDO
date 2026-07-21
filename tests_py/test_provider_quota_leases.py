"""Deterministic contracts for atomic provider quota leases."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from json import loads
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.capabilities import ProviderHttpResponse
from local_control_center.agents.providers.nvidia_nim import (
    NvidiaNimCapabilityError,
    NvidiaNimProvider,
)
from local_control_center.agents.quota_manager import (
    QuotaAdmissionDenied,
    QuotaManager,
    QuotaRequest,
    QuotaStateConflict,
)
from local_control_center.app import create_app
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current


def _create_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database_path = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(database_path))
    runtime = ControlPlaneFixture(cwd=tmp_path, db_path=database_path)
    runtime.init()
    return TestClient(create_app(runtime=runtime, static_dir=None))


def _auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_phase54_migrates_from_phase53_reenters_without_overwriting_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_control_center.shared import migrations

    database_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database_path) as connection:
        monkeypatch.setattr(migrations, "init_phase54_schema", lambda _connection: None, raising=False)
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_limits
            SET rpm = 7, current_window_json = '{"legacy":"preserved"}'
            WHERE id = 'nvidia_nim:*'
            """
        )
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version = 53").fetchone()
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version = 54").fetchone() is None

        monkeypatch.undo()
        migrations.init_phase54_schema(connection)
        migrations.init_phase54_schema(connection)

        columns = {row["name"] for row in connection.execute("PRAGMA table_info(provider_limits)")}
        tables = {
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        preserved = connection.execute(
            "SELECT rpm, current_window_json FROM provider_limits WHERE id = 'nvidia_nim:*'"
        ).fetchone()
        migration_count = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 54"
        ).fetchone()["total"]

    assert {
        "max_concurrency",
        "window_timezone",
        "enabled",
        "fallback_retry_after_seconds",
        "max_cost_per_request_usd",
    } <= columns
    assert {
        "provider_limit_windows",
        "provider_execution_leases",
        "provider_execution_lease_windows",
        "provider_limit_observations",
    } <= tables
    assert preserved["rpm"] == 7
    assert preserved["current_window_json"] == '{"legacy":"preserved"}'
    assert migration_count == 1


def _open_raw_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def test_specific_disabled_policy_is_unguarded_instead_of_falling_back_to_wildcard(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    now = "2026-07-14T12:00:00+00:00"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE provider_limits SET max_concurrency = 1 WHERE id = 'nvidia_nim:*'")
        connection.execute(
            """
            INSERT INTO provider_limits
                (id, provider_id, model, rpm, tpm, daily_requests, daily_tokens,
                 monthly_requests, monthly_tokens, monthly_budget_usd,
                 current_window_json, cooldown_until, last_429_at,
                 last_limit_error_at, unknown_limit_strategy, max_concurrency,
                 window_timezone, enabled, fallback_retry_after_seconds,
                 max_cost_per_request_usd, created_at, updated_at)
            VALUES ('nvidia_nim:specific', 'nvidia_nim', 'specific', NULL, NULL,
                    NULL, NULL, NULL, NULL, NULL, '{}', NULL, NULL, NULL,
                    'conservative', 1, 'UTC', 0, 300, NULL, ?, ?)
            """,
            (now, now),
        )
        manager = QuotaManager(connection, clock=clock)

        first = manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="specific", reserved_tokens=10))
        second = manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="specific", reserved_tokens=10))
        wildcard = manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="other", reserved_tokens=10))
        with pytest.raises(QuotaAdmissionDenied, match="blocked_concurrency"):
            manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="other", reserved_tokens=10))

    assert first.limit_id == "nvidia_nim:specific"
    assert first.guarded is False
    assert second.guarded is False
    assert wildcard.limit_id == "nvidia_nim:*"
    assert wildcard.guarded is True


def test_atomic_concurrency_admits_exactly_configured_capacity_across_connections(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_limits
            SET max_concurrency = 1, rpm = 10, tpm = 1000
            WHERE id = 'nvidia_nim:*'
            """
        )

    barrier = Barrier(2)

    def acquire_once(branch_id: str) -> str:
        with open_sqlite_connection(database_path) as connection:
            manager = QuotaManager(connection, clock=clock)
            barrier.wait()
            try:
                return manager.acquire(
                    QuotaRequest(
                        provider_id="nvidia_nim",
                        model="parallel-model",
                        reserved_tokens=100,
                        branch_id=branch_id,
                    )
                ).id
            except QuotaAdmissionDenied as error:
                return error.reason

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire_once, ("branch-a", "branch-b")))

    with open_sqlite_connection(database_path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) AS total FROM provider_execution_leases WHERE state = 'active'"
        ).fetchone()["total"]
        minute = connection.execute(
            """
            SELECT reserved_requests, reserved_tokens
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*' AND window_kind = 'minute'
            """
        ).fetchone()

    assert sum(result == "blocked_concurrency" for result in results) == 1
    assert active == 1
    assert dict(minute) == {"reserved_requests": 1, "reserved_tokens": 100}


@pytest.mark.parametrize(
    ("policy_update", "first_tokens", "second_tokens", "expected_reason"),
    [
        ({"rpm": 1}, 10, 10, "rpm_limit_exceeded"),
        ({"tpm": 100}, 60, 41, "tpm_limit_exceeded"),
        ({"daily_requests": 1}, 10, 10, "daily_request_limit_exceeded"),
        ({"daily_tokens": 100}, 60, 41, "daily_token_limit_exceeded"),
        ({"monthly_requests": 1}, 10, 10, "monthly_request_limit_exceeded"),
        ({"monthly_tokens": 100}, 60, 41, "monthly_token_limit_exceeded"),
    ],
)
def test_request_and_token_guards_deny_without_adding_a_second_reservation(
    tmp_path: Path,
    policy_update: dict[str, int],
    first_tokens: int,
    second_tokens: int,
    expected_reason: str,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    column, configured = next(iter(policy_update.items()))
    assert column in {
        "rpm",
        "tpm",
        "daily_requests",
        "daily_tokens",
        "monthly_requests",
        "monthly_tokens",
    }
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            f"UPDATE provider_limits SET {column} = ? WHERE id = 'nvidia_nim:*'",
            (configured,),
        )
        manager = QuotaManager(connection, clock=clock)
        manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="guarded", reserved_tokens=first_tokens))

        with pytest.raises(QuotaAdmissionDenied) as captured:
            manager.acquire(
                QuotaRequest(
                    provider_id="nvidia_nim",
                    model="guarded",
                    reserved_tokens=second_tokens,
                )
            )

        lease_count = connection.execute(
            "SELECT COUNT(*) AS total FROM provider_execution_leases"
        ).fetchone()["total"]
        reservations = connection.execute(
            """
            SELECT reserved_requests, reserved_tokens
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            """
        ).fetchall()

    assert captured.value.reason == expected_reason
    assert lease_count == 1
    assert all(row["reserved_requests"] == 1 for row in reservations)
    assert all(row["reserved_tokens"] == first_tokens for row in reservations)


def test_per_request_monthly_cost_cooldown_and_unknown_cost_strategy(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_limits
            SET max_cost_per_request_usd = 0.70,
                monthly_budget_usd = 1.00,
                unknown_limit_strategy = 'conservative'
            WHERE id = 'nvidia_nim:*'
            """
        )
        manager = QuotaManager(connection, clock=clock)

        with pytest.raises(QuotaAdmissionDenied) as unknown:
            manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="paid", reserved_tokens=10))
        with pytest.raises(QuotaAdmissionDenied) as per_request:
            manager.acquire(
                QuotaRequest(
                    provider_id="nvidia_nim",
                    model="paid",
                    reserved_tokens=10,
                    estimated_cost_usd=0.71,
                )
            )

        connection.execute(
            "UPDATE provider_limits SET unknown_limit_strategy = 'allow' WHERE id = 'nvidia_nim:*'"
        )
        allowed_unknown = manager.acquire(
            QuotaRequest(provider_id="nvidia_nim", model="paid", reserved_tokens=10)
        )
        first_paid = manager.acquire(
            QuotaRequest(
                provider_id="nvidia_nim",
                model="paid",
                reserved_tokens=10,
                estimated_cost_usd=0.60,
            )
        )
        with pytest.raises(QuotaAdmissionDenied) as monthly:
            manager.acquire(
                QuotaRequest(
                    provider_id="nvidia_nim",
                    model="paid",
                    reserved_tokens=10,
                    estimated_cost_usd=0.41,
                )
            )

        connection.execute(
            """
            UPDATE provider_limits
            SET cooldown_until = '2026-07-14T12:05:00+00:00'
            WHERE id = 'nvidia_nim:*'
            """
        )
        with pytest.raises(QuotaAdmissionDenied) as cooldown:
            manager.acquire(
                QuotaRequest(
                    provider_id="nvidia_nim",
                    model="paid",
                    reserved_tokens=10,
                    estimated_cost_usd=0.01,
                )
            )

    assert unknown.value.reason == "unknown_cost_blocked"
    assert per_request.value.reason == "max_cost_per_request_exceeded"
    assert monthly.value.reason == "monthly_budget_exceeded"
    assert cooldown.value.reason == "provider_in_cooldown"
    assert allowed_unknown.guarded is True
    assert first_paid.guarded is True


def test_minute_day_and_month_windows_reset_on_iana_timezone_boundaries(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 1, 3, 59, 59, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_limits
            SET rpm = 1, daily_requests = 1, monthly_requests = 1,
                window_timezone = 'America/Santiago'
            WHERE id = 'nvidia_nim:*'
            """
        )
        manager = QuotaManager(connection, clock=clock)
        manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="boundary", reserved_tokens=10))
        with pytest.raises(QuotaAdmissionDenied, match="rpm_limit_exceeded"):
            manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="boundary", reserved_tokens=10))

        clock.current = datetime(2026, 7, 1, 4, 0, 0, tzinfo=UTC)
        after_local_midnight = manager.acquire(
            QuotaRequest(provider_id="nvidia_nim", model="boundary", reserved_tokens=10)
        )
        starts = connection.execute(
            """
            SELECT window_kind, window_start
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            ORDER BY window_kind, window_start
            """
        ).fetchall()

    assert after_local_midnight.state == "active"
    assert len([row for row in starts if row["window_kind"] == "minute"]) == 2
    assert len([row for row in starts if row["window_kind"] == "day"]) == 2
    assert len([row for row in starts if row["window_kind"] == "month"]) == 2


def test_commit_known_usage_converts_reservations_once_and_rejects_release(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        manager = QuotaManager(connection, clock=clock)
        lease = manager.acquire(
            QuotaRequest(
                provider_id="nvidia_nim",
                model="known",
                reserved_tokens=100,
                estimated_cost_usd=0.50,
            )
        )

        dispatched = manager.mark_dispatched(lease.id)
        committed = manager.commit(lease.id, actual_tokens=80, actual_cost_usd=0.40)
        repeated = manager.commit(lease.id, actual_tokens=80, actual_cost_usd=0.40)
        with pytest.raises(QuotaStateConflict, match="committed"):
            manager.release(lease.id, reason="too_late")

        windows = connection.execute(
            """
            SELECT committed_requests, reserved_requests, committed_tokens,
                   unverified_tokens, reserved_tokens, known_cost_usd,
                   unverified_cost_usd, reserved_cost_usd,
                   unknown_usage_count, unknown_cost_count
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            """
        ).fetchall()

    assert dispatched.state == "dispatched"
    assert committed.state == "committed"
    assert repeated == committed
    assert all(row["committed_requests"] == 1 for row in windows)
    assert all(row["reserved_requests"] == 0 for row in windows)
    assert all(row["committed_tokens"] == 80 for row in windows)
    assert all(row["unverified_tokens"] == 0 for row in windows)
    assert all(row["reserved_tokens"] == 0 for row in windows)
    assert all(row["known_cost_usd"] == pytest.approx(0.40) for row in windows)
    assert all(row["unverified_cost_usd"] == 0 for row in windows)
    assert all(row["reserved_cost_usd"] == 0 for row in windows)


def test_commit_unknown_usage_preserves_unverified_reservation_instead_of_zero(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        manager = QuotaManager(connection, clock=clock)
        lease = manager.acquire(
            QuotaRequest(
                provider_id="nvidia_nim",
                model="unknown",
                reserved_tokens=120,
                estimated_cost_usd=0.75,
            )
        )

        committed = manager.commit(lease.id, actual_tokens=None, actual_cost_usd=None)
        stored = connection.execute(
            """
            SELECT actual_tokens, actual_cost_usd, usage_known, cost_known
            FROM provider_execution_leases WHERE id = ?
            """,
            (lease.id,),
        ).fetchone()
        windows = connection.execute(
            """
            SELECT committed_requests, committed_tokens, unverified_tokens,
                   reserved_tokens, known_cost_usd, unverified_cost_usd,
                   reserved_cost_usd, unknown_usage_count, unknown_cost_count
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            """
        ).fetchall()

    assert committed.state == "committed"
    assert stored["actual_tokens"] is None
    assert stored["actual_cost_usd"] is None
    assert stored["usage_known"] == 0
    assert stored["cost_known"] == 0
    assert all(row["committed_requests"] == 1 for row in windows)
    assert all(row["committed_tokens"] == 0 for row in windows)
    assert all(row["unverified_tokens"] == 120 for row in windows)
    assert all(row["reserved_tokens"] == 0 for row in windows)
    assert all(row["known_cost_usd"] == 0 for row in windows)
    assert all(row["unverified_cost_usd"] == pytest.approx(0.75) for row in windows)
    assert all(row["reserved_cost_usd"] == 0 for row in windows)
    assert all(row["unknown_usage_count"] == 1 for row in windows)
    assert all(row["unknown_cost_count"] == 1 for row in windows)


def test_release_before_dispatch_restores_capacity_idempotently(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE provider_limits SET max_concurrency = 1 WHERE id = 'nvidia_nim:*'")
        manager = QuotaManager(connection, clock=clock)
        lease = manager.acquire(QuotaRequest(provider_id="nvidia_nim", model="release", reserved_tokens=50))

        released = manager.release(lease.id, reason="cancelled_before_transport")
        repeated = manager.release(lease.id, reason="cancelled_before_transport")
        replacement = manager.acquire(
            QuotaRequest(provider_id="nvidia_nim", model="release", reserved_tokens=50)
        )
        with pytest.raises(QuotaStateConflict, match="released"):
            manager.commit(lease.id, actual_tokens=10, actual_cost_usd=0)

        windows = connection.execute(
            """
            SELECT committed_requests, reserved_requests, committed_tokens,
                   unverified_tokens, reserved_tokens
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            """
        ).fetchall()

    assert released.state == "released"
    assert repeated == released
    assert replacement.state == "active"
    assert all(row["committed_requests"] == 0 for row in windows)
    assert all(row["reserved_requests"] == 1 for row in windows)
    assert all(row["committed_tokens"] == 0 for row in windows)
    assert all(row["unverified_tokens"] == 0 for row in windows)
    assert all(row["reserved_tokens"] == 50 for row in windows)


def test_release_after_dispatch_keeps_attempt_and_unknown_settlement(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        manager = QuotaManager(connection, clock=clock)
        lease = manager.acquire(
            QuotaRequest(
                provider_id="nvidia_nim",
                model="transport-failed",
                reserved_tokens=70,
                estimated_cost_usd=0.30,
            )
        )

        manager.mark_dispatched(lease.id)
        released = manager.release(lease.id, reason="provider_transport_failed")
        windows = connection.execute(
            """
            SELECT committed_requests, reserved_requests, committed_tokens,
                   unverified_tokens, reserved_tokens, unverified_cost_usd,
                   reserved_cost_usd, unknown_usage_count, unknown_cost_count
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            """
        ).fetchall()

    assert released.state == "released"
    assert all(row["committed_requests"] == 1 for row in windows)
    assert all(row["reserved_requests"] == 0 for row in windows)
    assert all(row["committed_tokens"] == 0 for row in windows)
    assert all(row["unverified_tokens"] == 70 for row in windows)
    assert all(row["reserved_tokens"] == 0 for row in windows)
    assert all(row["unverified_cost_usd"] == pytest.approx(0.30) for row in windows)
    assert all(row["reserved_cost_usd"] == 0 for row in windows)
    assert all(row["unknown_usage_count"] == 1 for row in windows)
    assert all(row["unknown_cost_count"] == 1 for row in windows)


def test_stale_lease_expiry_releases_exact_mapped_reservations_once(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute("UPDATE provider_limits SET max_concurrency = 1 WHERE id = 'nvidia_nim:*'")
        manager = QuotaManager(connection, clock=clock)
        stale = manager.acquire(
            QuotaRequest(
                provider_id="nvidia_nim",
                model="expiring",
                reserved_tokens=90,
                lease_ttl_seconds=1,
            )
        )
        clock.current = datetime(2026, 7, 14, 12, 0, 2, tzinfo=UTC)

        replacement = manager.acquire(
            QuotaRequest(provider_id="nvidia_nim", model="expiring", reserved_tokens=40)
        )
        stale_row = connection.execute(
            "SELECT state FROM provider_execution_leases WHERE id = ?", (stale.id,)
        ).fetchone()
        windows = connection.execute(
            """
            SELECT reserved_requests, reserved_tokens
            FROM provider_limit_windows
            WHERE limit_id = 'nvidia_nim:*'
            """
        ).fetchall()

    assert stale_row["state"] == "expired"
    assert replacement.state == "active"
    assert all(row["reserved_requests"] == 1 for row in windows)
    assert all(row["reserved_tokens"] == 40 for row in windows)


def test_rate_limit_observations_parse_bounded_retry_after_without_overwriting_policy(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "platform.sqlite"
    clock = MutableClock(datetime(2026, 7, 14, 12, tzinfo=UTC))
    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            UPDATE provider_limits
            SET rpm = 7, tpm = 900, fallback_retry_after_seconds = 45
            WHERE id = 'nvidia_nim:*'
            """
        )
        manager = QuotaManager(connection, clock=clock)

        delta = manager.record_rate_limit(
            provider_id="nvidia_nim",
            model="observed-model",
            retry_after="120",
            headers={"X-RateLimit-Remaining-Requests": "0"},
            error_class="HttpRateLimitError",
        )
        http_date = manager.record_rate_limit(
            provider_id="nvidia_nim",
            model="observed-model",
            retry_after="Tue, 14 Jul 2026 12:03:00 GMT",
        )
        bounded = manager.record_rate_limit(
            provider_id="nvidia_nim",
            model="observed-model",
            retry_after="999999999",
        )
        fallback = manager.record_rate_limit(
            provider_id="nvidia_nim",
            model="observed-model",
            retry_after="Bearer secret-invalid-value",
            error_class="Bearer secret-invalid-value",
        )

        wildcard = connection.execute(
            "SELECT rpm, tpm FROM provider_limits WHERE id = 'nvidia_nim:*'"
        ).fetchone()
        specific = connection.execute(
            """
            SELECT rpm, tpm, cooldown_until
            FROM provider_limits
            WHERE provider_id = 'nvidia_nim' AND model = 'observed-model'
            """
        ).fetchone()
        observations = [
            loads(row["metadata_json"])
            for row in connection.execute(
                """
                SELECT metadata_json
                FROM provider_limit_observations
                WHERE provider_id = 'nvidia_nim' AND model = 'observed-model'
                ORDER BY observed_at, rowid
                """
            ).fetchall()
        ]

    assert delta["retryAfterSeconds"] == 120
    assert http_date["retryAfterSeconds"] == 180
    assert bounded["retryAfterSeconds"] == 86_400
    assert fallback["retryAfterSeconds"] == 45
    assert dict(wildcard) == {"rpm": 7, "tpm": 900}
    assert specific["rpm"] == 7
    assert specific["tpm"] == 900
    assert specific["cooldown_until"] == "2026-07-14T12:00:45+00:00"
    assert [item["retryAfterSource"] for item in observations] == [
        "delta_seconds",
        "http_date",
        "delta_seconds",
        "configured_fallback",
    ]
    assert observations[0]["rateLimitHeaders"] == {"x-ratelimit-remaining-requests": "0"}
    assert observations[-1]["errorClass"] == "provider_rate_limit"
    assert "secret" not in str(observations).lower()


def test_nvidia_transport_records_provider_retry_after_before_raising(tmp_path: Path) -> None:
    database_path = tmp_path / "platform.sqlite"

    def rate_limited_transport(_request: object) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            statusCode=429,
            headers={
                "retry-after": "17",
                "x-ratelimit-remaining-requests": "0",
                "authorization": "Bearer must-not-persist",
            },
            jsonBody={"detail": "Bearer must-not-persist"},
        )

    with open_sqlite_connection(database_path) as connection:
        initialize_platform_schema(connection)
        provider = NvidiaNimProvider(
            provider_id="nvidia-local-rate-limit",
            connection=connection,
            base_url="http://127.0.0.1:8123/v1",
            credential_ref="",
            deployment_mode="self_hosted_development",
            api_family="chat_completions",
            adapter_profile="nvidia_openai_chat",
            terms_mode="accepted",
            pricing_mode="free",
            transport=rate_limited_transport,
        )
        with pytest.raises(NvidiaNimCapabilityError, match="provider_rate_limited"):
            provider.chat_completion(
                ModelRequest(
                    model="qwen/test",
                    messages=[{"role": "user", "content": "hello"}],
                    maxTokens=8,
                )
            )
        observation = connection.execute(
            """
            SELECT retry_after_at, metadata_json
            FROM provider_limit_observations
            WHERE provider_id = 'nvidia-local-rate-limit' AND model = 'qwen/test'
            """
        ).fetchone()
        persisted = str(
            connection.execute(
                """
                SELECT metadata_json FROM provider_limit_observations
                WHERE provider_id = 'nvidia-local-rate-limit'
                """
            ).fetchone()["metadata_json"]
        )

    metadata = loads(observation["metadata_json"])
    assert metadata["retryAfterSeconds"] == 17
    assert metadata["retryAfterSource"] == "delta_seconds"
    assert metadata["rateLimitHeaders"] == {
        "x-ratelimit-remaining-requests": "0",
    }
    assert "must-not-persist" not in persisted


def test_provider_limit_api_create_update_validation_and_read_only_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_api_client(tmp_path, monkeypatch)
    headers = _auth_headers(client)
    base_payload = {
        "providerId": "nvidia_nim",
        "model": "minimaxai/minimax-m3",
        "rpm": 2,
        "tpm": 200,
        "dailyRequests": 10,
        "dailyTokens": 1_000,
        "monthlyRequests": 100,
        "monthlyTokens": 10_000,
        "monthlyBudgetUsd": 5.0,
        "maxCostPerRequestUsd": 0.5,
        "maxConcurrency": 2,
        "windowTimezone": "America/Santiago",
        "enabled": True,
        "fallbackRetryAfterSeconds": 60,
        "unknownLimitStrategy": "block",
    }

    invalid_numeric = client.post(
        "/api/v1/model-gateway/provider-limits",
        headers=headers,
        json={**base_payload, "rpm": -1},
    )
    boolean_numeric = client.post(
        "/api/v1/model-gateway/provider-limits",
        headers=headers,
        json={**base_payload, "rpm": True},
    )
    invalid_timezone = client.post(
        "/api/v1/model-gateway/provider-limits",
        headers=headers,
        json={**base_payload, "windowTimezone": "Mars/Olympus_Mons"},
    )
    client_owned_counter = client.post(
        "/api/v1/model-gateway/provider-limits",
        headers=headers,
        json={**base_payload, "currentWindow": {"dailyRequestsUsed": 999}},
    )
    created = client.post(
        "/api/v1/model-gateway/provider-limits",
        headers=headers,
        json=base_payload,
    )
    duplicate = client.post(
        "/api/v1/model-gateway/provider-limits",
        headers=headers,
        json=base_payload,
    )
    limit_id = created.json()["providerLimit"]["id"]
    patched = client.patch(
        f"/api/v1/model-gateway/provider-limits/{limit_id}",
        headers=headers,
        json={"maxConcurrency": 3, "unknownLimitStrategy": "allow"},
    )
    invalid_null = client.patch(
        f"/api/v1/model-gateway/provider-limits/{limit_id}",
        headers=headers,
        json={"windowTimezone": None},
    )

    database_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(database_path) as connection:
        clock = MutableClock(datetime.now(UTC))
        QuotaManager(connection, clock=clock).acquire(
            QuotaRequest(
                provider_id="nvidia_nim",
                model="minimaxai/minimax-m3",
                reserved_tokens=50,
                estimated_cost_usd=0.10,
            )
        )
        before = connection.execute(
            """
            SELECT id, committed_requests, reserved_requests, committed_tokens,
                   unverified_tokens, reserved_tokens, known_cost_usd,
                   unverified_cost_usd, reserved_cost_usd
            FROM provider_limit_windows ORDER BY id
            """
        ).fetchall()

    status = client.get(
        "/api/v1/model-gateway/provider-limits/status",
        params={
            "providerId": "nvidia_nim",
            "model": "minimaxai/minimax-m3",
            "requestTokens": 160,
            "estimatedCostUsd": 0.1,
        },
    )
    with open_sqlite_connection(database_path) as connection:
        after = connection.execute(
            """
            SELECT id, committed_requests, reserved_requests, committed_tokens,
                   unverified_tokens, reserved_tokens, known_cost_usd,
                   unverified_cost_usd, reserved_cost_usd
            FROM provider_limit_windows ORDER BY id
            """
        ).fetchall()

    assert invalid_numeric.status_code == 422
    assert boolean_numeric.status_code == 422
    assert invalid_timezone.status_code == 422
    assert client_owned_counter.status_code == 422
    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert patched.status_code == 200
    assert patched.json()["providerLimit"]["maxConcurrency"] == 3
    assert patched.json()["providerLimit"]["unknownLimitStrategy"] == "allow"
    assert invalid_null.status_code == 422
    assert status.status_code == 200
    assert status.json()["status"]["effectiveLimitId"] == limit_id
    assert status.json()["status"]["reason"] == "tpm_limit_exceeded"
    assert status.json()["status"]["activeLeases"] == 1
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
