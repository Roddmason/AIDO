from __future__ import annotations

from pathlib import Path

from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

PRODUCT_DISCOVERY_TABLES = {
    "initiatives",
    "discovery_sessions",
    "conversation_messages",
    "clarification_questions",
    "clarification_answers",
    "product_briefs",
    "product_brief_versions",
    "assumptions",
    "product_decisions",
}


def test_product_discovery_schema_adds_all_entities_as_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 16 in migrations
    assert tables >= PRODUCT_DISCOVERY_TABLES


def test_product_discovery_schema_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        phase16_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 16"
        ).fetchone()["total"]

    assert phase16_rows == 1


def test_discovery_entities_are_project_scoped_versionable_and_traceable(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        repo = ProductDiscoveryRepository(connection)

        project = projects.create_project(name="Discovery", path=tmp_path / "discovery", template_id="other")
        other_project = projects.create_project(name="Other", path=tmp_path / "other", template_id="other")
        project_id = project["id"]

        # Initiative is project-scoped and versionable.
        initiative = repo.create_initiative(
            {"projectId": project_id, "title": "Self-serve onboarding", "summary": "Reduce time-to-value."}
        )
        assert initiative["projectId"] == project_id
        assert initiative["version"] == 1
        bumped = repo.update_initiative(initiative["id"], {"status": "exploring"})
        assert bumped["version"] == 2
        assert bumped["status"] == "exploring"

        # A second project's initiative must not leak into the first project's listing.
        repo.create_initiative({"projectId": other_project["id"], "title": "Unrelated"})
        scoped = repo.list_initiatives(project_id)
        assert {item["id"] for item in scoped} == {initiative["id"]}

        # Discovery session traces back to the initiative.
        session = repo.create_discovery_session(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "title": "Kickoff",
                "objective": "Map the onboarding journey.",
                "facilitator": "product_owner",
            }
        )
        assert session["initiativeId"] == initiative["id"]
        assert repo.list_discovery_sessions(initiative_id=initiative["id"])[0]["id"] == session["id"]

        # Conversation messages are an ordered, immutable log.
        first = repo.append_conversation_message(
            {
                "projectId": project_id,
                "sessionId": session["id"],
                "initiativeId": initiative["id"],
                "role": "facilitator",
                "author": "product_owner",
                "content": "What blocks activation today?",
            }
        )
        second = repo.append_conversation_message(
            {
                "projectId": project_id,
                "sessionId": session["id"],
                "initiativeId": initiative["id"],
                "role": "stakeholder",
                "author": "growth_lead",
                "content": "Manual config takes too long.",
            }
        )
        assert (first["sequence"], second["sequence"]) == (1, 2)
        ordered = repo.list_conversation_messages(session["id"])
        assert [message["id"] for message in ordered] == [first["id"], second["id"]]

        # Clarification question and answer trace to the initiative and to each other.
        question = repo.create_clarification_question(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "sessionId": session["id"],
                "question": "Which segment activates fastest?",
                "askedBy": "product_owner",
            }
        )
        assert question["sequence"] == 1
        answer = repo.create_clarification_answer(
            {
                "projectId": project_id,
                "questionId": question["id"],
                "initiativeId": initiative["id"],
                "answer": "Self-serve SMB accounts.",
                "answeredBy": "growth_lead",
            }
        )
        assert repo.list_clarification_answers(question["id"])[0]["id"] == answer["id"]
        revised = repo.create_clarification_answer(
            {
                "projectId": project_id,
                "questionId": question["id"],
                "initiativeId": initiative["id"],
                "answer": "Self-serve SMB accounts with billing connected.",
                "answeredBy": "growth_lead",
                "supersedesId": answer["id"],
            }
        )
        assert revised["supersedesId"] == answer["id"]

        # Product brief keeps an explicit version history.
        brief = repo.upsert_product_brief(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "title": "Onboarding brief",
                "summary": "Cut activation friction.",
                "problemStatement": "Users stall at manual setup.",
                "goals": ["Reduce setup steps", "Increase day-1 activation"],
                "targetUsers": ["SMB admins"],
                "successMetrics": ["activation_rate"],
                "scope": "Guided setup wizard.",
                "outOfScope": "Enterprise SSO.",
                "changeSummary": "Initial draft.",
                "authoredBy": "product_owner",
            }
        )
        assert brief["version"] == 1
        updated_brief = repo.upsert_product_brief(
            {
                "briefId": brief["id"],
                "title": "Onboarding brief",
                "summary": "Cut activation friction with a wizard.",
                "problemStatement": "Users stall at manual setup.",
                "goals": ["Reduce setup steps", "Increase day-1 activation", "Add progress tracking"],
                "targetUsers": ["SMB admins"],
                "successMetrics": ["activation_rate", "time_to_value"],
                "scope": "Guided setup wizard with checklist.",
                "outOfScope": "Enterprise SSO.",
                "changeSummary": "Added progress tracking goal.",
                "authoredBy": "product_owner",
            }
        )
        assert updated_brief["version"] == 2
        assert updated_brief["projectId"] == project_id
        assert updated_brief["initiativeId"] == initiative["id"]
        history = repo.list_product_brief_versions(brief["id"])
        assert [snapshot["version"] for snapshot in history] == [2, 1]
        assert history[0]["changeSummary"] == "Added progress tracking goal."

        # Assumption traces to its originating question.
        assumption = repo.create_assumption(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "briefId": brief["id"],
                "sourceQuestionId": question["id"],
                "statement": "SMB admins prefer guided setup.",
                "confidence": "medium",
            }
        )
        assert assumption["sourceQuestionId"] == question["id"]
        validated = repo.update_assumption(
            assumption["id"], {"status": "validated", "validation": "Confirmed in 5 interviews."}
        )
        assert validated["status"] == "validated"

        # Product decision links the assumptions and questions that justify it and is versionable.
        decision = repo.create_product_decision(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "briefId": brief["id"],
                "title": "Ship guided setup wizard",
                "status": "accepted",
                "context": "Activation stalls at manual config.",
                "decision": "Build a guided wizard for SMB onboarding.",
                "rationale": "Highest-leverage activation lever.",
                "consequences": ["Wizard maintenance cost"],
                "linkedAssumptionIds": [assumption["id"]],
                "linkedQuestionIds": [question["id"]],
                "decidedBy": "product_owner",
            }
        )
        assert decision["version"] == 1
        assert decision["linkedAssumptionIds"] == [assumption["id"]]
        assert decision["linkedQuestionIds"] == [question["id"]]
        superseding = repo.update_product_decision(decision["id"], {"status": "superseded"})
        assert superseding["version"] == 2

        # Every persisted entity stays scoped to its project.
        assert repo.list_assumptions(project_id) and repo.list_assumptions(project_id)[0]["projectId"] == (
            project_id
        )
        assert repo.list_product_decisions(other_project["id"]) == []
        assert repo.list_product_briefs(other_project["id"]) == []
