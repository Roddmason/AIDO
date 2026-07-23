"""El probe de auth nativa degrada a unauthenticated cuando la credencial local está vencida.

Bug verificado en vivo (2026-07-22): `claude auth status` reporta ``loggedIn: true`` aunque el
token OAuth de ``~/.claude/.credentials.json`` ya venció; la única señal honesta era ejecutar
``claude -p`` o leer ``expiresAt``. Un indicador proactivo montado sobre el status de hoy mostraría
"verde-falso". El runtime debe cruzar el veredicto del probe con el vencimiento local del archivo de
credenciales y degradar a ``unauthenticated`` SOLO ante evidencia concluyente de vencimiento,
preservando el invariante "unknown/ilegible nunca degrada".

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.cli_runtimes import base as base_module
from local_control_center.agents.cli_runtimes.base import CliRuntime, RuntimeRequest
from local_control_center.agents.cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from local_control_center.agents.runtime_status import (
    CLI_NATIVE_AUTH_REVALIDATION_TTL_SECONDS,
    _native_auth_validation_is_fresh,
)
from local_control_center.shared.time import iso_after_seconds, utc_now

_LOGGED_IN_PROBE = {"returnCode": 0, "stdout": json.dumps({"loggedIn": True, "authMethod": "claude.ai"})}


def _write_credentials(config_dir: Path, expires_at_ms: int | None) -> None:
    oauth: dict[str, Any] = {"accessToken": "sk-ant-oauth-xxx", "refreshToken": "sk-ant-refresh-xxx"}
    if expires_at_ms is not None:
        oauth["expiresAt"] = expires_at_ms
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")


@pytest.fixture
def claude_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ClaudeCodeCliRuntime:
    runtime = ClaudeCodeCliRuntime(executable="claude")
    # El binario "existe" y el probe reporta logged-in; el vencimiento local decide.
    monkeypatch.setattr(runtime, "_which", lambda: "claude")
    monkeypatch.setattr(
        base_module.subprocess_sandbox,
        "run_auth_status_check",
        lambda **_: dict(_LOGGED_IN_PROBE),
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    return runtime


def test_expired_token_degrades_logged_in_probe_to_unauthenticated(
    claude_runtime: ClaudeCodeCliRuntime, tmp_path: Path
) -> None:
    _write_credentials(tmp_path / ".claude", expires_at_ms=int(time.time() * 1000) - 60_000)

    status = claude_runtime.validate_native_auth()

    assert status.status == "unauthenticated", status
    assert "login" in status.message.lower()


def test_valid_future_token_stays_authenticated(claude_runtime: ClaudeCodeCliRuntime, tmp_path: Path) -> None:
    _write_credentials(tmp_path / ".claude", expires_at_ms=int(time.time() * 1000) + 3_600_000)

    status = claude_runtime.validate_native_auth()

    assert status.status == "authenticated", status


def test_missing_credentials_file_never_degrades_the_probe(
    claude_runtime: ClaudeCodeCliRuntime,
) -> None:
    # Sin archivo de credenciales no hay evidencia de vencimiento: se respeta el probe.
    status = claude_runtime.validate_native_auth()

    assert status.status == "authenticated", status


def test_credentials_without_expiry_field_never_degrades(
    claude_runtime: ClaudeCodeCliRuntime, tmp_path: Path
) -> None:
    _write_credentials(tmp_path / ".claude", expires_at_ms=None)

    status = claude_runtime.validate_native_auth()

    assert status.status == "authenticated", status


def test_unreadable_credentials_file_never_degrades(
    claude_runtime: ClaudeCodeCliRuntime, tmp_path: Path
) -> None:
    config_dir = tmp_path / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".credentials.json").write_text("{ this is not json", encoding="utf-8")

    status = claude_runtime.validate_native_auth()

    assert status.status == "authenticated", status


def test_probe_that_could_not_run_stays_unknown_regardless_of_local_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Un probe bloqueado/timeout es 'unknown' y el seam de vencimiento NO se consulta
    # (solo cruza un veredicto 'authenticated'), preservando el invariante.
    runtime = ClaudeCodeCliRuntime(executable="claude")
    monkeypatch.setattr(runtime, "_which", lambda: "claude")
    monkeypatch.setattr(
        base_module.subprocess_sandbox,
        "run_auth_status_check",
        lambda **_: {"blocked": True, "returnCode": None, "reason": "sandbox blocked probe"},
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    _write_credentials(tmp_path / ".claude", expires_at_ms=int(time.time() * 1000) - 60_000)

    status = runtime.validate_native_auth()

    assert status.status == "unknown", status


def test_base_runtime_without_local_check_returns_probe_verdict_unchanged() -> None:
    # El hook base es no-op: un runtime sin verdad local devuelve el veredicto del probe intacto.
    class _StubRuntime(CliRuntime):
        runtime_id = "stub_cli"
        display_name = "Stub CLI"
        auth_status_argv = ("whoami",)

        def build_command(self, request: RuntimeRequest) -> list[str]:  # pragma: no cover
            return [self.executable]

    stub = _StubRuntime(executable="stub")
    assert (
        stub._local_auth_invalidation(  # type: ignore[attr-defined]
            base_module.RuntimeAuthStatus(runtime="stub_cli", status="authenticated")
        )
        is None
    )


def test_recent_validation_is_fresh_and_skips_reprobe() -> None:
    # Una validación de hace instantes está dentro del TTL: no se re-sondea.
    assert _native_auth_validation_is_fresh(utc_now()) is True


def test_validation_older_than_ttl_is_stale_and_triggers_reprobe() -> None:
    stale = iso_after_seconds(utc_now(), -(CLI_NATIVE_AUTH_REVALIDATION_TTL_SECONDS + 60))
    assert _native_auth_validation_is_fresh(stale) is False


def test_missing_or_unparseable_validation_is_treated_as_stale() -> None:
    assert _native_auth_validation_is_fresh(None) is False
    assert _native_auth_validation_is_fresh("") is False
    assert _native_auth_validation_is_fresh("not-a-timestamp") is False
