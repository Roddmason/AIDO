from __future__ import annotations

from pathlib import Path

from local_control_center.agents.impact_question_engine import (
    ImpactQuestionEngine,
    detected_facts_from_assessment,
)
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _question(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "category": "scope",
        "question": "Which JDK version is the target?",
        "whyItMatters": "It changes the migration plan.",
        "blocking": True,
        "options": ["Java 17", "Java 21"],
        "recommendation": "Java 21",
        "defaultDecision": "Java 21",
        "confidence": "medium",
    }
    base.update(overrides)
    return base


def test_initiative_is_reused_across_turns_of_the_same_thread(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        repo = ProductDiscoveryRepository(connection)
        project = projects.create_project(name="Dedup", path=tmp_path / "dedup", template_id="other")
        project_id = project["id"]

        assert repo.find_initiative_by_thread(project_id, "thread-1") is None

        created = repo.create_initiative(
            {
                "projectId": project_id,
                "title": "Spring Boot upgrade",
                "metadata": {"source": "product_loop_coordinator", "threadId": "thread-1"},
            }
        )
        assert repo.find_initiative_by_thread(project_id, "thread-1")["id"] == created["id"]
        # Another thread must not inherit it, or unrelated work would share a backlog.
        assert repo.find_initiative_by_thread(project_id, "thread-2") is None
        # Nor may another project reach it.
        other = projects.create_project(name="Other", path=tmp_path / "other", template_id="other")
        assert repo.find_initiative_by_thread(other["id"], "thread-1") is None


def test_an_answered_question_is_never_asked_again(tmp_path: Path) -> None:
    """Una pregunta respondida es el hecho detectado más fuerte: debe suprimir la repregunta."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        repo = ProductDiscoveryRepository(connection)
        project = projects.create_project(name="Answered", path=tmp_path / "answered", template_id="other")
        initiative = repo.create_initiative({"projectId": project["id"], "title": "Upgrade"})

        asked = repo.create_clarification_question(
            {
                "projectId": project["id"],
                "initiativeId": initiative["id"],
                "question": "Which JDK version is the target?",
                "askedBy": "product_owner",
                "metadata": {"category": "scope"},
            }
        )
        repo.update_clarification_question(asked["id"], {"status": "answered"})

        stored = repo.list_clarification_questions(initiative_id=initiative["id"])
        assert [item["status"] for item in stored] == ["answered"]
        # The open-only view is empty, which is exactly why it cannot be the dedup corpus.
        assert [item for item in stored if item["status"] == "open"] == []

        detected = detected_facts_from_assessment(brief=None, existing_questions=stored)
        result = ImpactQuestionEngine().select([_question()], detected_facts=detected)
        assert result["turn"] == []
        assert result["counts"]["suppressed"] == 1
