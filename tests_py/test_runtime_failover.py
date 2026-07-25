from __future__ import annotations

from urllib.error import HTTPError

import pytest

from local_control_center.agents.runtime_failover import (
    FailureClass,
    classify_runtime_failure,
    exclusion_for,
    is_affordable_candidate,
    looks_like_quota_exhaustion,
    should_failover,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionError("connection refused"), FailureClass.TRANSPORT),
        (TimeoutError("timed out"), FailureClass.TRANSPORT),
        (RuntimeError("provider returned 503 Service Unavailable"), FailureClass.TRANSPORT),
        (RuntimeError("429 Too Many Requests"), FailureClass.QUOTA),
        (RuntimeError("insufficient_quota: billing exhausted"), FailureClass.QUOTA),
        (RuntimeError("RESOURCE_EXHAUSTED"), FailureClass.QUOTA),
        (ValueError("DeveloperAgent model output must be a JSON object."), FailureClass.SEMANTIC),
        (ValueError("questions[0].defaultDecision must be one of options."), FailureClass.SEMANTIC),
        (TypeError("bad type"), FailureClass.SEMANTIC),
        (RuntimeError("something odd happened"), FailureClass.UNKNOWN),
    ],
)
def test_classify_runtime_failure(error: BaseException, expected: FailureClass) -> None:
    assert classify_runtime_failure(error) is expected


def test_a_429_http_error_is_quota_even_though_it_is_also_an_oserror() -> None:
    """HTTPError hereda de OSError; sin precedencia de cuota se clasificaria como transporte."""
    error = HTTPError(url="http://x", code=429, msg="Too Many Requests", hdrs=None, fp=None)

    assert classify_runtime_failure(error) is FailureClass.QUOTA


def test_only_transport_and_quota_justify_spending_on_another_provider() -> None:
    assert should_failover(FailureClass.TRANSPORT) is True
    assert should_failover(FailureClass.QUOTA) is True
    # Reintentar un fallo de contrato en otro proveedor solo repite el mismo error, pagandolo.
    assert should_failover(FailureClass.SEMANTIC) is False
    assert should_failover(FailureClass.UNKNOWN) is False


def test_quota_excludes_the_whole_account_and_transport_only_the_model() -> None:
    assert exclusion_for(FailureClass.QUOTA, provider_id="gemini", model="flash") == {
        "provider": "gemini",
        "model": "*",
    }
    assert exclusion_for(FailureClass.TRANSPORT, provider_id="gemini", model="flash") == {
        "provider": "gemini",
        "model": "flash",
    }


def test_without_a_role_cost_cap_only_free_candidates_are_accepted() -> None:
    free, reason = is_affordable_candidate(
        {"estimatedCostUsd": 0.0, "costTier": "local"}, requires_approval_over_usd=None
    )
    assert (free, reason) == (True, "free")

    paid, reason = is_affordable_candidate(
        {"estimatedCostUsd": 0.02, "costTier": "paid"}, requires_approval_over_usd=None
    )
    assert paid is False
    assert reason == "no_cost_cap_in_role_policy"


def test_a_cap_admits_what_fits_under_it_and_refuses_the_rest() -> None:
    under, _ = is_affordable_candidate(
        {"estimatedCostUsd": 0.4, "costTier": "paid"}, requires_approval_over_usd=0.5
    )
    over, reason = is_affordable_candidate(
        {"estimatedCostUsd": 0.9, "costTier": "paid"}, requires_approval_over_usd=0.5
    )

    assert under is True
    assert over is False
    assert reason == "over_role_cost_cap"


@pytest.mark.parametrize(
    "output",
    [
        "You've hit your usage limit. Try again later.",
        "Claude AI usage limit reached|1751000000",
        "Error: usage limit exceeded for your plan",
        "You have exceeded your current quota",
        "stream error: 429 Too Many Requests",
    ],
)
def test_a_cli_that_ran_out_of_quota_is_recognized_by_its_own_wording(output: str) -> None:
    """Un CLI sin cuota no emite 429: escribe 'usage limit' en texto plano y sale con rc=1."""
    assert looks_like_quota_exhaustion(output) is True


@pytest.mark.parametrize(
    "output",
    [
        "SyntaxError: invalid syntax",
        "error: pathspec 'main' did not match any file(s) known to git",
        "AssertionError: expected 2 but got 3",
        "",
    ],
)
def test_an_ordinary_failure_is_never_read_as_exhausted_quota(output: str) -> None:
    """Bloquear un runtime sano por un fallo de codigo dejaria la instalacion sin candidatos."""
    assert looks_like_quota_exhaustion(output) is False


def test_the_cli_wording_also_classifies_a_raised_failure_as_quota() -> None:
    """El clasificador de excepciones y el de texto comparten los mismos marcadores."""
    assert classify_runtime_failure(RuntimeError("You've hit your usage limit")) is FailureClass.QUOTA


def test_an_unpriced_candidate_is_never_taken_automatically() -> None:
    """Saltar de gratis a un precio que nadie puede acotar no es una decision automatizable."""
    accepted, reason = is_affordable_candidate(
        {"estimatedCostUsd": 0.0, "costTier": "unknown", "costEstimateSource": "unknown_price"},
        requires_approval_over_usd=10.0,
    )

    assert accepted is False
    assert reason == "unknown_price"
