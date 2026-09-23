from __future__ import annotations

import pytest

from local_control_center.product_loop.intent_classifier import IntentClassificationInput, IntentClassifier


def classify(
    prompt: str,
    *,
    changed_files: list[str] | None = None,
    git_state: dict | None = None,
    project_assessment: dict | None = None,
    user_mode: str = "aido_decide",
):
    return IntentClassifier().classify(
        IntentClassificationInput(
            prompt=prompt,
            project_assessment=project_assessment or _runtime_available_assessment(),
            changed_files=changed_files or [],
            git_state=git_state or {"status": "completed", "dirty": False, "currentBranch": "dev"},
            user_mode=user_mode,
        )
    )


def _runtime_available_assessment() -> dict:
    return {
        "summary": {"stack": ["React + Vite", "Python FastAPI"], "hasTests": True},
        "runtimeStatus": {
            "providers": [
                {
                    "id": "codex_cli",
                    "executable": True,
                    "available": True,
                    "canEditWorkspace": True,
                }
            ]
        },
    }


def _runtime_unavailable_assessment() -> dict:
    return {
        "summary": {"stack": ["React + Vite", "Python FastAPI"], "hasTests": True},
        "runtimeStatus": {
            "providers": [
                {
                    "id": "codex_cli",
                    "executable": False,
                    "available": False,
                    "canEditWorkspace": False,
                    "reason": "Codex CLI is not executable.",
                }
            ]
        },
    }


def test_refactor_prompt_infers_migration_refactor_frontend_and_backend() -> None:
    decision = classify(
        "Refactor the legacy auth flow, migrate the session API, and update the React settings page.",
        changed_files=[
            "local_control_center/api.py",
            "local-control-center/web/src/features/settings/SettingsPage.tsx",
            "local_control_center/shared/migrations.py",
        ],
    )

    assert {"migration", "refactor"} <= set(decision.intents)
    assert {"frontend_engineer", "backend_engineer"} <= set(decision.required_roles)
    assert decision.plan_mode == "plan"
    assert decision.confidence >= 0.75


def test_bug_prompt_infers_bugfix_and_qa_gate() -> None:
    decision = classify(
        "Fix the bug where checkout validation crashes and add regression coverage.",
        changed_files=["tests_py/test_checkout.py", "local_control_center/checkout/api.py"],
    )

    assert "bugfix" in decision.intents
    assert "qa_reviewer" in decision.required_roles
    assert "regression_tests" in decision.required_gates


def test_pentest_prompt_infers_security_and_pentester() -> None:
    decision = classify("Run a pentest against the auth endpoints and harden the token flow.")

    assert "security" in decision.intents
    assert "pentester" in decision.required_roles
    assert "security_review" in decision.required_gates
    assert decision.risk == "critical"


def test_no_runtime_blocks_plan_mode() -> None:
    decision = classify(
        "Implement the onboarding dashboard.",
        project_assessment=_runtime_unavailable_assessment(),
    )

    assert decision.plan_mode == "blocked"
    assert "runtime_configuration" in decision.required_gates
    assert decision.questions


def test_low_confidence_prompt_asks_before_execution() -> None:
    decision = classify("help")

    assert decision.plan_mode == "ask"
    assert decision.confidence < 0.55
    assert decision.questions


def test_spanish_migration_and_architecture_request_requires_planning() -> None:
    decision = classify(
        "Migra a java 25, lleva el front a react compatible y mejora la lógica y flujos "
        "con patron de arquitectura y patrones de diseño claros, alta performance del sistema"
    )

    assert {"migration", "architecture"} <= set(decision.intents)
    assert decision.risk == "high"
    assert decision.plan_mode == "plan"
    assert {"migration_review", "architecture_review"} <= set(decision.required_gates)
    assert decision.questions == []


def test_migration_keyword_does_not_match_spanish_substrings() -> None:
    decision = classify("Revisa el comportamiento de las aves migratorias")

    assert "migration" not in decision.intents


ORIGINAL_VALIDATION_PROMPT = (
    "Investiga en modo solo lectura: ¿qué versión de Python exige pyproject.toml de este repo? "
    "Responde en una línea y no modifiques archivos."
)
CHANGE_PROMPTS = (
    "Analiza por qué el login falla al expirar la sesión y corrige el bug",
    "Compara la versión actual con la anterior y corrige el bug de sesión",
    "Corrige el bug de memoria sin modificar la API pública",
    "Fix the crash on startup; compare with the previous release",
)
RESEARCH_PROMPTS = (
    "Investiga qué tests cubren el login",
    "Research which feature flags exist",
    "Investiga por qué el build de refactor está lento, solo analiza, no modifiques nada",
    "Analiza el bug de memoria en modo solo lectura, no lo corrijas",
    "Evaluate migration options for Postgres 17 and cite sources",
    ORIGINAL_VALIDATION_PROMPT,
)


@pytest.mark.parametrize("prompt", CHANGE_PROMPTS)
def test_change_requests_are_never_research_only(prompt: str) -> None:
    assert classify(prompt).research_only is False


@pytest.mark.parametrize("prompt", RESEARCH_PROMPTS)
def test_read_only_questions_are_research_only(prompt: str) -> None:
    decision = classify(prompt)

    assert decision.research_only is True
    assert "research" in decision.intents
    assert decision.plan_mode not in {"ask", "blocked"}


def test_spanish_research_vocabulary_matches_with_and_without_accents() -> None:
    accented = classify("Evalúa y compara las opciones de caché")
    unaccented = classify("Evalua y compara las opciones de cache")

    assert accented.scores == unaccented.scores
    assert accented.scores["research"] == 2


def test_classification_exposes_scores_and_research_flag() -> None:
    decision = classify("Research which feature flags exist")

    assert decision.scores["research"] == 1
    assert decision.scores["feature"] == 1
    assert decision.to_dict()["researchOnly"] is True
    assert decision.to_dict()["scores"]["research"] == 1


def test_classifier_questions_carry_i18n_keys_aligned_with_the_english_text() -> None:
    ask = classify("help")
    blocked = classify("Add a dashboard", project_assessment=_runtime_unavailable_assessment())

    assert ask.question_keys == ["app.threads.intake.question.outcome"]
    assert ask.questions == ["What outcome should AIDO optimize for: diagnosis, implementation, or research?"]
    assert ask.to_dict()["questionKeys"] == ask.question_keys
    assert blocked.question_keys[0] == "app.threads.intake.question.runtime"
    assert len(blocked.question_keys) == len(blocked.questions)
