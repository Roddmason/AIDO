from __future__ import annotations

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
