"""Uso de cuota por proveedor: porcentaje, umbral del operador y suspensión automática con failover.

Reporte del operador: Claude CLI estaba por llegar al 100 % del uso semanal y no quiere que AIDO lo use
pasado un umbral (80 % para todos o por proveedor), ni siquiera a mitad de un loop.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_usage import (
    ProviderUsageStore,
    UsageWindow,
    collect_accounting_usage,
    parse_claude_usage,
    parse_codex_usage,
    refresh_provider_usage,
)
from local_control_center.agents.quota_manager import QuotaManager, QuotaRequest
from local_control_center.agents.runtime_failover import FailureClass, classify_runtime_failure
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

NOW = datetime.now(UTC)
LATER = (NOW + timedelta(days=2)).isoformat()
EARLIER = (NOW - timedelta(hours=1)).isoformat()


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as conn:
        initialize_platform_schema(conn)
        ProviderAccountStore(conn).patch_provider_account("claude_code_cli", {"enabled": True})
        ProviderAccountStore(conn).patch_provider_account("codex_cli", {"enabled": True})
        yield conn


def _window(
    provider_id: str, kind: str, used: float | None, resets_at: str | None = LATER, status: str = "ok"
):
    return UsageWindow(provider_id, kind, used, resets_at, "test", status)


def test_claude_usage_windows_are_read_as_percent_with_their_reset() -> None:
    windows = parse_claude_usage(
        {
            "five_hour": {"utilization": 33.0, "resets_at": "2026-04-11T07:00:00.528743+00:00"},
            "seven_day": {"utilization": 91.0, "resets_at": "2026-04-17T00:59:59.951713+00:00"},
            "seven_day_opus": None,
        }
    )
    by_kind = {window.window_kind: window for window in windows}
    assert set(by_kind) == {"five_hour", "seven_day"}
    assert by_kind["seven_day"].used_percent == 91.0
    assert by_kind["seven_day"].resets_at.startswith("2026-04-17T00:59:59")


def test_codex_usage_windows_are_read_from_either_known_shape() -> None:
    wham = parse_codex_usage(
        {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 18000,
                    "reset_at": 1_900_000_000,
                },
                "secondary_window": {
                    "used_percent": 84,
                    "limit_window_seconds": 604800,
                    "reset_at": 1_900_500_000,
                },
            }
        }
    )
    assert [(item.window_kind, item.used_percent) for item in wham] == [
        ("five_hour", 12.0),
        ("seven_day", 84.0),
    ]
    app_server = parse_codex_usage(
        {"rateLimits": {"primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": 1_900_000_000}}}
    )
    assert [(item.window_kind, item.used_percent) for item in app_server] == [("five_hour", 40.0)]


def test_a_provider_over_the_general_threshold_is_suspended_until_its_reset(connection) -> None:
    store = ProviderUsageStore(connection)
    store.record([_window("claude_code_cli", "seven_day", 85.0), _window("codex_cli", "seven_day", 50.0)])
    suspensions = store.suspensions()
    assert set(suspensions) == {"claude_code_cli"}
    assert suspensions["claude_code_cli"]["until"] == LATER
    assert suspensions["claude_code_cli"]["thresholdPercent"] == 80.0
    # El umbral general es configurable; y cada proveedor puede tener el suyo.
    SettingsRepository(connection).set_value("runtime.quota.suspendThresholdPercent", "general", None, 90)
    assert store.suspensions() == {}
    store.set_policy("codex_cli", threshold_percent=40)
    assert set(store.suspensions()) == {"codex_cli"}


def test_a_passed_reset_or_a_manual_resume_lifts_the_suspension(connection) -> None:
    store = ProviderUsageStore(connection)
    store.record([_window("claude_code_cli", "five_hour", 99.0, resets_at=EARLIER)])
    assert store.suspensions() == {}
    store.record([_window("claude_code_cli", "seven_day", 99.0)])
    assert "claude_code_cli" in store.suspensions()
    store.set_policy("claude_code_cli", threshold_percent=None, ignore_until=LATER)
    assert store.suspensions() == {}


def test_an_exhausted_window_suspends_even_without_a_percentage(connection) -> None:
    store = ProviderUsageStore(connection)
    store.record([_window("codex_cli", "seven_day", None, status="rejected")])
    assert "codex_cli" in store.suspensions()


def test_a_suspended_provider_counts_as_exhausted_for_status_and_resume(connection) -> None:
    ProviderUsageStore(connection).record([_window("claude_code_cli", "seven_day", 95.0)])
    manager = QuotaManager(connection)
    assert "claude_code_cli" in manager.providers_in_cooldown()
    assert manager.providers_in_cooldown_detail()["claude_code_cli"] == LATER


def test_a_running_loop_stops_using_a_provider_that_crossed_the_threshold(connection, tmp_path) -> None:
    """A mitad de loop: el siguiente intento falla como cuota y el failover elige otro proveedor."""
    sys.modules.setdefault("faiss", None)
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

    coordinator = ProductLoopCoordinator(connection, root=tmp_path)
    coordinator._raise_if_usage_suspended({"preferredRuntime": "claude_code_cli"})  # sin uso: pasa
    ProviderUsageStore(connection).record([_window("claude_code_cli", "seven_day", 97.0)])
    with pytest.raises(RuntimeError) as raised:
        coordinator._raise_if_usage_suspended({"preferredRuntime": "claude_code_cli"})
    assert classify_runtime_failure(raised.value) is FailureClass.QUOTA
    coordinator._raise_if_usage_suspended({"preferredRuntime": "codex_cli"})


def test_refresh_reads_the_local_cli_logins_and_respects_the_poll_interval(connection, tmp_path) -> None:
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    (claude_home / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "oauth-token"}}), encoding="utf-8"
    )
    requests: list = []

    def fetch(request):
        requests.append(request)
        assert request.get_header("Authorization") == "Bearer oauth-token"
        return {"seven_day": {"utilization": 88.0, "resets_at": LATER}}

    env = {"CLAUDE_CONFIG_DIR": str(claude_home), "CODEX_HOME": str(tmp_path / "no-codex")}
    report = refresh_provider_usage(connection, env=env, fetch=fetch)
    assert report["refreshed"] == ["claude_code_cli"]
    # Codex sin login: queda la causa, sin romper el refresco.
    assert "not readable" in report["errors"]["codex_cli"]
    assert "claude_code_cli" in ProviderUsageStore(connection).suspensions()
    again = refresh_provider_usage(connection, env=env, fetch=fetch)
    assert "claude_code_cli" in again["skipped"] and len(requests) == 1


def test_a_disabled_provider_is_not_polled(connection, tmp_path) -> None:
    ProviderAccountStore(connection).patch_provider_account("claude_code_cli", {"enabled": False})
    report = refresh_provider_usage(connection, env={"CLAUDE_CODE_OAUTH_TOKEN": "t"}, fetch=lambda _r: {})
    assert "claude_code_cli" not in report["refreshed"] and "claude_code_cli" not in report["errors"]


def test_aido_accounting_turns_a_daily_request_cap_into_a_usage_percent(connection) -> None:
    connection.execute(
        """INSERT INTO provider_limits (id, provider_id, model, daily_requests, current_window_json,
               unknown_limit_strategy, max_concurrency, window_timezone, enabled,
               fallback_retry_after_seconds, created_at, updated_at)
           VALUES ('gemini:*', 'gemini', '*', 10, '{}', 'allow', 1, 'UTC', 1, 300, ?, ?)
           ON CONFLICT(id) DO UPDATE SET daily_requests = 10, enabled = 1""",
        (NOW.isoformat(), NOW.isoformat()),
    )
    manager = QuotaManager(connection)
    for _ in range(9):
        lease = manager.acquire(QuotaRequest(provider_id="gemini", model="*", reserved_tokens=1))
        manager.commit(lease.id, actual_tokens=1, actual_cost_usd=0.0)
    usage = {
        (item.provider_id, item.window_kind): item.used_percent
        for item in collect_accounting_usage(connection)
    }
    assert usage.get(("gemini", "daily"), 0) >= 90.0


def test_the_api_exposes_usage_and_lets_the_operator_set_a_threshold_and_resume(
    tmp_path, monkeypatch
) -> None:
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from tests_py.control_plane_fixture import ControlPlaneFixture
    from tests_py.execution_client import CompletedExecutionClient as TestClient

    with ExitStack() as stack:
        store = stack.enter_context(
            closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
        )
        store.init()
        client = stack.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
        headers = {"X-Local-Control-Token": client.get("/api/v1/security/handshake").json()["token"]}
        ProviderUsageStore(store.connection).record([_window("claude_code_cli", "seven_day", 85.0)])
        body = client.get("/api/v1/runtime/provider-usage").json()
        assert body["generalThresholdPercent"] == 80.0
        claude = next(item for item in body["providers"] if item["providerId"] == "claude_code_cli")
        assert claude["suspended"] is True and claude["maxUsedPercent"] == 85.0
        raised = client.put(
            "/api/v1/runtime/provider-usage/claude_code_cli/policy",
            json={"thresholdPercent": 95},
            headers=headers,
        )
        assert raised.status_code == 200, raised.text
        claude = next(item for item in raised.json()["providers"] if item["providerId"] == "claude_code_cli")
        assert claude["suspended"] is False and claude["thresholdSource"] == "provider"
        assert (
            client.put(
                "/api/v1/runtime/provider-usage/claude_code_cli/policy",
                json={"thresholdPercent": 150},
                headers=headers,
            ).status_code
            == 422
        )
        client.put(
            "/api/v1/runtime/provider-usage/claude_code_cli/policy",
            json={"thresholdPercent": None},
            headers=headers,
        )
        resumed = client.post("/api/v1/runtime/provider-usage/claude_code_cli/resume", headers=headers)
        assert resumed.status_code == 200, resumed.text
        claude = next(item for item in resumed.json()["providers"] if item["providerId"] == "claude_code_cli")
        assert claude["suspended"] is False and claude["ignoreUntil"] == LATER
        assert (
            client.post("/api/v1/runtime/provider-usage/codex_cli/resume", headers=headers).status_code == 409
        )
        assert client.post("/api/v1/runtime/provider-usage/claude_code_cli/resume").status_code in {401, 403}
