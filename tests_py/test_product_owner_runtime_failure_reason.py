"""El motivo que ve el cliente debe nombrar la causa real, no el código de salida.

Bug observado en vivo (2026-07-22/23): el stderr del subprocess queda persistido en
``payload.executionResult['stderr']``, pero ``_execution_result_from_tool_call`` proyectaba solo
un subconjunto de campos y lo descartaba, sintetizando "ProductOwnerAgent runtime process exited
with return code 1". Con ese texto el cliente no puede saber que su sesión de Claude expiró ni
que la cuota de Codex se agotó, y la remediación aguas abajo queda ciega.

@author Rodrigo Mason
"""

from __future__ import annotations

from local_control_center.agents.product_owner_agent import _execution_result_from_tool_call


def _tool_call(*, status: str, return_code: int, stderr: str, stdout: str = "") -> dict:
    return {
        "id": "tool-call-1",
        "status": status,
        "payload": {
            "execution": "real",
            "executionResult": {
                "returnCode": return_code,
                "stderr": stderr,
                "stdout": stdout,
                "timedOut": False,
                "blocked": False,
            },
        },
    }


def test_expired_session_is_named_in_the_reason_instead_of_the_return_code() -> None:
    call = _tool_call(
        status="failed",
        return_code=1,
        stderr="Failed to authenticate. API Error: 401 OAuth access token has expired. Re-authenticate to continue",
    )

    result = _execution_result_from_tool_call(call)

    assert result["failureCause"] == "auth_expired"
    # La evidencia real viaja para que la tarjeta de reparación pueda mostrarla.
    assert "expired" in result["failureEvidence"].lower()
    # El motivo ya no puede ser solo el returncode.
    assert result["reason"] != "ProductOwnerAgent runtime process exited with return code 1."


def test_exhausted_quota_carries_cause_and_when_it_comes_back() -> None:
    """Codex termina con returncode 0 pese al error de cuota: la salida manda, no el código."""
    call = _tool_call(
        status="completed",
        return_code=0,
        stdout="ERROR: You've hit your usage limit. Upgrade to Plus to continue using Codex, "
        "or try again at Aug 14th, 2026 8:52 AM.",
        stderr="",
    )

    result = _execution_result_from_tool_call(call)

    assert result["failureCause"] == "quota_exhausted"
    assert result["failureRetryAfter"] == "Aug 14th, 2026 8:52 AM"


def test_healthy_run_reports_no_failure_cause() -> None:
    call = _tool_call(status="completed", return_code=0, stdout='{"status": "completed"}', stderr="")

    result = _execution_result_from_tool_call(call)

    assert result["status"] == "completed"
    assert result["failureCause"] is None
    assert result["failureEvidence"] == ""


def test_unclassified_failure_keeps_the_return_code_message() -> None:
    """Sin firma conocida no se inventa una causa: se conserva el mensaje histórico."""
    call = _tool_call(status="failed", return_code=1, stderr="")

    result = _execution_result_from_tool_call(call)

    assert result["reason"] == "ProductOwnerAgent runtime process exited with return code 1."


def test_explicit_sandbox_reason_is_not_overwritten() -> None:
    """Cuando el sandbox ya explicó el bloqueo, esa razón manda sobre la clasificación."""
    call = {
        "id": "tool-call-2",
        "status": "failed",
        "payload": {
            "executionResult": {
                "returnCode": None,
                "blocked": True,
                "reason": "Command is not allowlisted.",
                "stderr": "",
            }
        },
    }

    result = _execution_result_from_tool_call(call)

    assert result["reason"] == "Command is not allowlisted."
