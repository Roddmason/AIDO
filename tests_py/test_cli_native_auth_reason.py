"""Tests del motivo que acompaña a una auth nativa no vigente.

Una validación exitosa que venció no prueba que falte login: repetir su mensaje ("la sesión está
iniciada") como causa del bloqueo es una contradicción que deja al operador sin saber qué hacer.
Ese caso pide revalidación, no credenciales nuevas.

@author Rodrigo Mason
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from local_control_center.agents.runtime_status import (
    CLI_NATIVE_AUTH_REVALIDATION_TTL_SECONDS,
    _cli_provider_status,
)

LOGGED_IN_PROBE = "Codex CLI native session is logged in."
LOGGED_OUT_PROBE = "Codex CLI reports no active session."


def _iso(seconds_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _status(runtime_account: dict | None) -> dict:
    return _cli_provider_status(
        account={"providerId": "codex_cli", "displayName": "Codex CLI", "providerType": "cli"},
        runtime_installation={"enabled": True, "detectedVersion": "codex-cli 0.149.0"},
        runtime_account=runtime_account,
        detection={
            "status": "installed",
            "version": "codex-cli 0.149.0",
            "executable": "codex",
            "message": "",
        },
        capabilities=["chat", "code_edit"],
        policy_decision={"allowed": True},
    )


def _account(*, health_status: str, validated_seconds_ago: float | None, probe: str) -> dict:
    return {
        "enabled": True,
        "healthStatus": health_status,
        "lastValidationAt": None if validated_seconds_ago is None else _iso(validated_seconds_ago),
        "metadata": {"lastNativeAuthProbe": probe},
    }


def test_expired_validation_does_not_report_the_stale_success_message() -> None:
    """La contradicción: sesión iniciada segun el sondeo viejo, pero bloqueado por auth."""
    status = _status(
        _account(
            health_status="healthy",
            validated_seconds_ago=CLI_NATIVE_AUTH_REVALIDATION_TTL_SECONDS * 20,
            probe=LOGGED_IN_PROBE,
        )
    )

    assert status["authenticated"] is False
    assert LOGGED_IN_PROBE not in status["reason"], (
        "un sondeo exitoso vencido no puede presentarse como la causa del bloqueo"
    )
    assert "revalidat" in status["reason"].lower()


def test_never_validated_still_reports_the_probe_message() -> None:
    """Cuando el sondeo dijo que no hay sesión, ese mensaje sigue siendo la explicación correcta."""
    status = _status(
        _account(health_status="unauthenticated", validated_seconds_ago=None, probe=LOGGED_OUT_PROBE)
    )

    assert status["authenticated"] is False
    assert status["reason"] == LOGGED_OUT_PROBE


def test_account_without_any_probe_falls_back_to_the_generic_reason() -> None:
    """Sin evidencia de ningún tipo se explica que la auth nativa nunca se validó."""
    status = _status(_account(health_status="unknown", validated_seconds_ago=None, probe=""))

    assert status["authenticated"] is False
    assert "has not been validated" in status["reason"]


def test_fresh_validation_authenticates_and_explains_success() -> None:
    """Con validación vigente el runtime cuenta como autenticado."""
    status = _status(_account(health_status="healthy", validated_seconds_ago=5, probe=LOGGED_IN_PROBE))

    assert status["authenticated"] is True
    assert "authenticated" in status["reason"].lower()


def test_expired_validation_asks_for_revalidation_not_for_login() -> None:
    """Una validación vencida no es falta de login: blocker propio y sin comando de sign-in."""
    status = _status(
        _account(
            health_status="healthy",
            validated_seconds_ago=CLI_NATIVE_AUTH_REVALIDATION_TTL_SECONDS * 20,
            probe=LOGGED_IN_PROBE,
        )
    )

    assert status["blockerType"] == "runtime_validation_expired"
    assert status["loginCommand"] == "", "pedir login a un operador con sesión iniciada es la contradicción"


def test_missing_login_keeps_auth_blocker_and_login_command() -> None:
    """Cuando el sondeo dijo que no hay sesión, sí corresponde pedir login con su comando."""
    status = _status(
        _account(health_status="unauthenticated", validated_seconds_ago=None, probe=LOGGED_OUT_PROBE)
    )

    assert status["blockerType"] == "runtime_auth_missing"
    assert status["loginCommand"]
