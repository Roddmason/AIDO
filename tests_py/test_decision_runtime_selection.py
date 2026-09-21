"""Active Jev ranking cannot inherit a default or expand AIDO authority."""

import asyncio
from contextlib import closing

import pytest

from tests_py.test_decision_engine import FakeJev, request_for


@pytest.fixture
def decision_db(tmp_path):
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    with closing(open_sqlite_connection(tmp_path / "selection.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def test_active_selection_has_no_default_and_persists_accepted_choice(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.repository import DecisionRepository
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(mode="runtime_selection"), provider=FakeJev()
    )
    receipt = asyncio.run(
        engine.select_runtime(request_for(effective_decision=None), revalidate=lambda _: None)
    )
    assert receipt["effectiveDecision"] == "claude"
    assert receipt["mode"] == "runtime_selection"
    assert receipt["fallbackUsed"] is False
    assert DecisionRepository(decision_db).get(receipt["decisionId"]) == receipt


@pytest.mark.parametrize("changes", [{"selected": "unapproved"}, {"confidence": 0.1}])
def test_active_selection_fails_closed_without_using_request_default(decision_db, changes):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(mode="runtime_selection"), provider=FakeJev(**changes)
    )
    receipt = asyncio.run(engine.select_runtime(request_for(), revalidate=lambda _: None))
    assert receipt["effectiveDecision"] is None
    assert receipt["fallbackUsed"] is False
    assert receipt["reasonCode"] != "recommendation_usable"


def test_active_selection_revalidates_after_jev_before_persisting_choice(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(mode="runtime_selection"), provider=FakeJev()
    )
    receipt = asyncio.run(
        engine.select_runtime(request_for(), revalidate=lambda _: "model_validation_failed")
    )
    assert receipt["effectiveDecision"] is None
    assert receipt["jevRecommendation"] == "claude"
    assert receipt["reasonCode"] == "model_validation_failed"


def test_active_mode_does_not_run_shadow_or_intake_inference(decision_db):
    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine

    provider = FakeJev()
    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(mode="runtime_selection"), provider=provider
    )
    assert asyncio.run(engine.observe(request_for())) is None
    assert provider.calls == 0


@pytest.mark.parametrize(
    "risk,selected,reason",
    [
        ("high", "claude", "recommendation_usable"),
        ("critical", None, "risk_requires_review"),
    ],
)
def test_default_risk_accepts_high_but_keeps_critical_under_review(decision_db, risk, selected, reason):
    from local_control_center.decision_engine.config import DecisionConfig, resolve_config
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from local_control_center.settings.registry import descriptor_for

    assert DecisionConfig().max_risk == "high"
    assert descriptor_for("decision_engine.max_risk").default == "high"
    config = resolve_config(decision_db, "project-without-override")
    assert config.max_risk == "high"
    assert (
        decision_db.execute(
            "SELECT COUNT(*) FROM settings_value WHERE key='decision_engine.max_risk'"
        ).fetchone()[0]
        == 0
    )
    request = request_for(
        effective_decision=None,
        context={"task_type": "bug", "risk": risk, "task_fingerprint": "b" * 64},
        constraints={"allowed_candidates": {"codex", "claude"}, "deterministic_risk": risk},
    )
    engine = ShadowDecisionEngine(
        decision_db,
        config=config.model_copy(update={"mode": "runtime_selection"}),
        provider=FakeJev(),
    )
    receipt = asyncio.run(engine.select_runtime(request, revalidate=lambda _: None))
    assert receipt["effectiveDecision"] == selected
    assert receipt["reasonCode"] == reason
    assert receipt["policy"]["maxRisk"] == "high"


@pytest.mark.parametrize(
    "general,project,expected",
    [
        ("low", None, "low"),
        ("medium", None, "medium"),
        ("high", "medium", "medium"),
        ("medium", "low", "low"),
        ("low", "medium", "medium"),
    ],
)
def test_manual_general_and_project_risk_limits_keep_precedence(decision_db, general, project, expected):
    from local_control_center.decision_engine.config import resolve_config
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from local_control_center.settings.repository import SettingsRepository

    settings = SettingsRepository(decision_db)
    settings.set_value("decision_engine.max_risk", "general", None, general)
    if project is not None:
        settings.set_value("decision_engine.max_risk", "project", "restricted-project", project)
    config = resolve_config(decision_db, "restricted-project")
    assert config.max_risk == expected
    assert resolve_config(decision_db, "other-project").max_risk == general
    request = request_for(
        effective_decision=None,
        context={"task_type": "bug", "risk": "high", "task_fingerprint": "c" * 64},
        constraints={"allowed_candidates": {"codex", "claude"}, "deterministic_risk": "high"},
    )
    engine = ShadowDecisionEngine(
        decision_db,
        config=config.model_copy(update={"mode": "runtime_selection"}),
        provider=FakeJev(),
    )
    receipt = asyncio.run(engine.select_runtime(request, revalidate=lambda _: None))
    assert receipt["effectiveDecision"] is None
    assert receipt["reasonCode"] == "risk_requires_review"
    assert receipt["policy"]["maxRisk"] == expected
