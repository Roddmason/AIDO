"""El fallo real de un runtime se clasifica en una causa accionable, no en un returncode opaco.

Bug observado en vivo (2026-07-22/23): cuando el CLI de Claude tenía el OAuth vencido o Codex
tenía la cuota agotada, el cliente solo veía "runtime process exited with return code 1". El
stderr real —que sí trae la causa y el remedio— se descartaba. Peor: `codex exec` termina con
**returncode 0** aunque imprima "You've hit your usage limit", así que el returncode no sirve
para detectar el fallo.

Las firmas de este test son transcripciones literales de las salidas reales de los CLIs.

@author Rodrigo Mason
"""

from __future__ import annotations

from local_control_center.agents.runtime_failure_classifier import classify_runtime_failure

# Salida literal de `codex exec` con la cuota agotada (returncode 0).
CODEX_QUOTA_STDERR = (
    "warning: Exceeded skills context budget of 2%.\n"
    "ERROR: You've hit your usage limit. Upgrade to Plus to continue using Codex "
    "(https://chatgpt.com/explore/plus), or try again at Aug 14th, 2026 8:52 AM.\n"
)
# Salida literal de `claude -p` con el token OAuth vencido.
CLAUDE_EXPIRED_STDERR = (
    "Failed to authenticate. API Error: 401 OAuth access token has expired. Re-authenticate to continue\n"
)
CLAUDE_INVALID_STDERR = "Failed to authenticate. API Error: 401 Invalid authentication credentials\n"


def test_quota_exhausted_is_detected_even_when_return_code_is_zero() -> None:
    """Codex sale con rc=0 pese al error de cuota: clasificar por salida, nunca por returncode."""
    failure = classify_runtime_failure(
        runtime_id="codex_cli", return_code=0, stdout="", stderr=CODEX_QUOTA_STDERR
    )

    assert failure is not None
    assert failure.cause == "quota_exhausted"
    # El cliente necesita saber cuándo vuelve a estar disponible.
    assert failure.retry_after == "Aug 14th, 2026 8:52 AM"
    assert failure.runtime_id == "codex_cli"


def test_expired_oauth_is_auth_expired_not_generic_failure() -> None:
    failure = classify_runtime_failure(
        runtime_id="claude_code_cli", return_code=1, stdout="", stderr=CLAUDE_EXPIRED_STDERR
    )

    assert failure is not None
    assert failure.cause == "auth_expired"


def test_invalid_credentials_is_also_auth_expired() -> None:
    """Un 401 de credenciales inválidas se remedia igual que el token vencido: re-login."""
    failure = classify_runtime_failure(
        runtime_id="claude_code_cli", return_code=1, stdout="", stderr=CLAUDE_INVALID_STDERR
    )

    assert failure is not None
    assert failure.cause == "auth_expired"


def test_not_logged_in_is_auth_missing() -> None:
    failure = classify_runtime_failure(
        runtime_id="codex_cli",
        return_code=1,
        stdout="",
        stderr="Not logged in. Run `codex login` to authenticate.",
    )

    assert failure is not None
    assert failure.cause == "auth_missing"


def test_unreachable_provider_is_classified() -> None:
    failure = classify_runtime_failure(
        runtime_id="ollama",
        return_code=1,
        stdout="",
        stderr="error: connection refused connecting to 127.0.0.1:11434",
    )

    assert failure is not None
    assert failure.cause == "provider_unreachable"


def test_missing_model_is_classified() -> None:
    failure = classify_runtime_failure(
        runtime_id="ollama", return_code=1, stdout="", stderr='error: model "qwen9:99b" not found'
    )

    assert failure is not None
    assert failure.cause == "model_not_found"


def test_unrecognized_failure_still_reports_unknown_with_evidence() -> None:
    """Un fallo no catalogado no puede perderse: se reporta como unknown conservando la evidencia."""
    failure = classify_runtime_failure(
        runtime_id="claude_code_cli", return_code=1, stdout="", stderr="Segmentation fault (core dumped)"
    )

    assert failure is not None
    assert failure.cause == "unknown"
    assert "Segmentation fault" in failure.evidence


def test_successful_run_is_not_a_failure() -> None:
    """rc=0 sin firmas de error no es fallo: no inventar problemas donde no los hay."""
    assert (
        classify_runtime_failure(
            runtime_id="claude_code_cli", return_code=0, stdout='{"ok": true}', stderr=""
        )
        is None
    )


def test_evidence_is_redacted_and_bounded() -> None:
    """La evidencia se persiste y se muestra al cliente: sin secretos y acotada."""
    noisy = "Authorization: Bearer sk-ant-super-secret-value\n" + ("x" * 5000)
    failure = classify_runtime_failure(runtime_id="claude_code_cli", return_code=1, stdout="", stderr=noisy)

    assert failure is not None
    assert "sk-ant-super-secret-value" not in failure.evidence
    assert len(failure.evidence) <= 1000
